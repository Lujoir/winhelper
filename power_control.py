# -*- coding: utf-8 -*-
"""power_control · 终端「自动开关机」快照引擎（观枢终端平台｜EyeTerm）。

P0 只读：机型识别 / BIOS 企业线（Lenovo_BiosSetting）探测与解析 /
Windows 唤醒定时器 / 关机类计划任务 / 快速启动状态。
零写操作、零 shutdown 调用（决策 ADR-001）；测试夹具禁真实关机命令串（ADR-018 惯例）。

决策 docs/DECISIONS.md（ADR-001~004）；规格 docs/POWER_CONTROL_SPEC.md（主仓）。
同步契约：本文件 → 主应用根 power_control.py（bridge 路由 /api/powercontrol/*）。
"""
import csv
import ctypes
import io
import json
import os
import re
import subprocess
import sys
import threading
import time

APP_DIR_NAME = "power-control"
CREATE_NO_WINDOW = 0x08000000
REPORT_PATH = "/api/v1/terminals/{tid}/powercontrol/snapshot"
REPORT_THROTTLE_SEC = 12 * 3600

# RTC 项映射：归一化（小写去空白）BIOS 项名 → 语义键。
# 真实基准 = ThinkCentre M720t 实测（ADR-005）：Wake Up on Alarm / Alarm
# Time(HH:MM:SS) / Alarm Date(MM/DD/YYYY) / Alarm Day of Week / 逐日开关；
# 旧机型候选（Automatic Power On Control 等）保留作兼容，不虚构语义。
_RTC_ITEM_KEYS = {
    "wakeuponalarm": "alarm",
    "wakeuponrtcalarm": "alarm",
    "automaticpoweronrtcalarm": "alarm",
    "automaticpoweroncontrol": "master",
    "alarmtime(hh:mm:ss)": "time",
    "alarmtime": "time",
    "userdefinedalarmtime(hh:mm:ss)": "user_time",
    "userdefinedalarmtime": "user_time",
    "alarmdate(mm/dd/yyyy)": "date",
    "alarmdate": "date",
    "alarmdayofweek": "day",
    "afterpowerloss": "after_power_loss",
    "wakeonlan": "wake_on_lan",
}
_RTC_WEEKDAYS = {"monday", "tuesday", "wednesday", "thursday", "friday",
                 "saturday", "sunday"}
_RTC_DAY_ORDER = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                  "Saturday", "Sunday")

# 关机类任务匹配（对 schtasks 动作/任务名）；测试夹具用 stub 占位非真实命令串
_SHUTDOWN_ACTION_RE = re.compile(r"(?i)shutdown|psshutdown")
_SHUTDOWN_NAME_RE = re.compile(r"(?i)shutdown|关机")

# 唤醒定时器双格式（ADR-005 实测 zh-CN Win10 格式 + 经典 ID 格式）：
#   格式 A（M720t 实测）：[SERVICE] <path> (svc) 设置的计时器在 18:48:18 过期(位于 2026/9/16 上)。
#                        原因: Windows 将执行“NT TASK\...”计划的任务，该任务请求唤醒计算机。
#   格式 B（旧文档口径）：计时器 ID [0]: 2026-09-17T07:30:00 [AUTHOR: ...] [DESCRIPTION: ...]
#                        所有者: ...
_WAKE_SVC_RE = re.compile(r"^\[(SERVICE|PROCESS|DEVICE)\]\s*(.+)$", re.I)
_WAKE_SVC_OWNER_SPLIT_RE = re.compile(r"设置的计时器|set\s+a?\s*timer", re.I)
_WAKE_SVC_TIME_RE = re.compile(r"(?:在|at)\s*(\d{1,2}:\d{2}:\d{2})\s*(?:过期|expir)", re.I)
_WAKE_SVC_DATE_ZH_RE = re.compile(r"位于\s*(\d{4}/\d{1,2}/\d{1,2})")
_WAKE_SVC_DATE_EN_RE = re.compile(r"on\s+(\d{1,2}/\d{1,2}/\d{4})")
_WAKE_REASON_RE = re.compile(r"^(?:原因|Reason)\s*[:：]\s*(.*)$")
_WAKE_HEAD_RE = re.compile(r"(?:计时器|Timer)\s*ID\s*\[(\d+)\]\s*:\s*(.*)", re.I)
_WAKE_ISO_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")
_WAKE_AUTHOR_RE = re.compile(r"\[AUTHOR:\s*([^\]]*)\]", re.I)
_WAKE_DESC_RE = re.compile(r"\[DESCRIPTION:\s*([^\]]*)\]", re.I)
_WAKE_OWNER_RE = re.compile(r"(?:所有者|Owner)\s*:\s*(.*)", re.I)
_WAKE_DENY_RE = re.compile(r"拒绝访问|access is denied|administrator|管理员", re.I)

_log_lock = threading.Lock()
_log_dir = None


# ======================================================================
# 日志（日期分文件）与数据目录
# ======================================================================
def data_dir():
    base = os.path.join(os.environ.get("LOCALAPPDATA",
                                       os.path.expanduser("~")),
                        "winhelper", APP_DIR_NAME)
    os.makedirs(base, exist_ok=True)
    return base


def set_log_dir(path):
    global _log_dir
    _log_dir = path
    os.makedirs(path, exist_ok=True)


def log(msg, level="INFO"):
    with _log_lock:
        d = _log_dir or os.path.join(data_dir(), "logs")
        try:
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, "pc_%s.log" % time.strftime("%Y%m%d"))
            with open(path, "a", encoding="utf-8") as f:
                f.write("%s [%s] %s\n"
                        % (time.strftime("%Y-%m-%d %H:%M:%S"), level, msg))
        except OSError:
            pass


# ======================================================================
# 子进程执行：CREATE_NO_WINDOW + 超时 + 重试 3 次 + 三级解码
# ======================================================================
def _decode(raw):
    if isinstance(raw, str):
        return raw
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def _default_runner(args, timeout):
    p = subprocess.Popen(args, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE,
                         creationflags=CREATE_NO_WINDOW)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            p.kill()
            p.communicate(timeout=5)
        except Exception:
            pass
        raise RuntimeError("timeout after %ss" % timeout)
    return p.returncode, _decode(out), _decode(err)


def _run_cmd(args, timeout=30, retries=3, runner=None):
    """执行子进程：最多尝试 retries 次（指数退避 0.5/1/2s）。
    返回 (rc, stdout, stderr)；超时按失败计。"""
    run = runner or _default_runner
    delays = (0.5, 1.0, 2.0)
    last_rc, last_out, last_err = 1, "", ""
    for attempt in range(max(1, retries)):
        try:
            rc, out, err = run(args, timeout)
        except Exception as e:
            rc, out, err = 1, "", str(e)
        if rc == 0:
            if attempt:
                log("命令第 %d 次尝试成功: %s" % (attempt + 1, args[0]))
            return rc, out, err
        last_rc, last_out, last_err = rc, out, err
        log("命令失败(rc=%s) 第 %d 次: %s | %s"
            % (rc, attempt + 1, args[0], (err or out or "")[:200]), "WARN")
        if attempt < max(1, retries) - 1:
            time.sleep(delays[min(attempt, len(delays) - 1)])
    return last_rc, last_out, last_err


_PS_PREFIX = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "


def _ps(script):
    return ["powershell", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-Command", _PS_PREFIX + script]


def _ps_json(text):
    """解析 PowerShell ConvertTo-Json 输出（单对象/数组/空 均容错）。"""
    t = (text or "").strip()
    if not t:
        return None
    try:
        data = json.loads(t)
    except ValueError:
        return None
    return data


# ======================================================================
# ① 机型识别（Win32_ComputerSystem）
# ======================================================================
def collect_machine(runner=None, timeout=30):
    rc, out, err = _run_cmd(_ps(
        "ConvertTo-Json -Compress -InputObject "
        "@(Get-CimInstance -ClassName Win32_ComputerSystem "
        "-Property Name,Manufacturer,Model,SystemFamily)"),
        runner=runner, timeout=timeout)
    if rc != 0:
        raise RuntimeError((err or out or "查询失败").strip()[:200])
    data = _ps_json(out)
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        raise RuntimeError("机型信息返回格式异常")
    return {
        "hostname": str(data.get("Name") or ""),
        "manufacturer": str(data.get("Manufacturer") or "").strip(),
        "model": str(data.get("Model") or "").strip(),
        "system_family": str(data.get("SystemFamily") or "").strip(),
    }


# ======================================================================
# ② BIOS 企业线探测（root\wmi Lenovo_BiosSetting）与解析
# ======================================================================
def _ps_bios_write_iface_probe():
    return ("$old = Get-CimClass -Namespace root/wmi -ClassName "
            "Lenovo_SetBiosSetting -ErrorAction SilentlyContinue; "
            "if ($old) { Write-Output 'PCIF=old' } else { "
            "$inst = Get-CimInstance -Namespace root/wmi -ClassName "
            "Lenovo_BiosSetting -ErrorAction SilentlyContinue | "
            "Select-Object -First 1; "
            "if ($inst -and $inst.CimClass -and "
            "$inst.CimClass.CimClassMethods['SetBiosSetting']) "
            "{ Write-Output 'PCIF=new' } else { Write-Output 'PCIF=none' } }")


def probe_write_iface(runner=None, timeout=25):
    """写接口形态只读探测（ADR-006 附注③，不做任何 Set 调用）：
    old=老接口 Lenovo_SetBiosSetting 类存在；
    new=新一代接口（Lenovo_BiosSetting 实例自带 SetBiosSetting 方法）；
    none=两者皆无（如实上报不可写）；unknown=探测失败。"""
    rc, out, err = _run_cmd(_ps(_ps_bios_write_iface_probe()),
                            runner=runner, timeout=timeout)
    m = re.search(r"PCIF=(old|new|none)", out or "")
    if rc != 0 or not m:
        return "unknown"
    return m.group(1)


def probe_lenovo_bios(runner=None, timeout=30):
    """探测 root/wmi Lenovo_BiosSetting：类不存在 → class_found=False；
    存在 → 全量读取 CurrentSetting（"ItemName,Value" 列表）。
    同时探测写接口形态（write_iface：old/new/none/unknown，ADR-006 附注③）。
    只读探测，不做任何 Set 调用。"""
    rc, out, err = _run_cmd(_ps(
        "ConvertTo-Json -Compress -InputObject "
        "@(Get-CimClass -Namespace root/wmi -ClassName Lenovo_BiosSetting "
        "-ErrorAction SilentlyContinue)"), runner=runner, timeout=timeout)
    if rc != 0:
        raise RuntimeError((err or out or "探测失败").strip()[:200])
    classes = _ps_json(out)
    if not classes:
        return {"class_found": False, "items_raw": [], "write_iface": "none"}
    write_iface = None
    try:
        write_iface = probe_write_iface(runner=runner, timeout=timeout)
    except Exception:
        write_iface = "unknown"
    rc, out, err = _run_cmd(_ps(
        "ConvertTo-Json -Compress -InputObject "
        "@(Get-CimInstance -Namespace root/wmi -ClassName Lenovo_BiosSetting "
        "-ErrorAction SilentlyContinue | ForEach-Object { $_.CurrentSetting })"),
        runner=runner, timeout=timeout)
    if rc != 0:
        return {"class_found": True, "items_raw": [],
                "write_iface": write_iface or "unknown",
                "error": (err or out or "读取失败").strip()[:200]}
    data = _ps_json(out)
    if data is None:
        items = []
    elif isinstance(data, list):
        items = data
    else:
        items = [data]
    return {"class_found": True, "items_raw": items,
            "write_iface": write_iface or "unknown"}


def _norm_item(name):
    return re.sub(r"\s+", "", str(name or "")).lower()


_STATUS_RE = re.compile(r"\[Status:([^\]]*)\]", re.I)
_OPT_RE = re.compile(r"\[Optional:([^\]]*)\]", re.I)


def parse_current_setting_entry(s):
    """解析单条 CurrentSetting（纯函数，M720t 实测两形态，ADR-005）。

    形态 1：`Item,Value;[Optional:v1,v2,...]`（可再带 [Status:ShowOnly]）
    形态 2：`Item,[V][Status:ShowOnly]`（值带方括号）
    返回 {"item","value","optional","status","note"}；空值/无逗号 → None。"""
    if s is None:
        return None
    s = str(s).strip()
    if not s or "," not in s:
        return None
    item, rest = s.split(",", 1)
    item = item.strip()
    rest = rest.strip()
    if not item:
        return None
    status = optional = note = None
    m = _STATUS_RE.search(rest)
    if m:
        status = (m.group(1).strip() or None)
        rest = (rest[:m.start()] + rest[m.end():]).strip()
    m = _OPT_RE.search(rest)
    if m:
        optional = [x.strip() for x in m.group(1).split(",") if x.strip()]
        rest = (rest[:m.start()] + rest[m.end():]).strip()
    value = rest
    if ";" in value:
        left, tail = value.split(";", 1)
        tail = tail.strip()
        if tail.startswith("[") and "]" in tail:
            note = tail
        value = left.strip()
    if len(value) >= 2 and value.startswith("[") and value.endswith("]"):
        value = value[1:-1].strip()
    return {"item": item, "value": value, "optional": optional or [],
            "status": status, "note": note}


def _rtc_decorate(rtc):
    """由原始 RTC 值推导 alarm_on / cycle_text / summary（纯函数）。
    语义只按实测可选值推导，不虚构（ADR-005）。"""
    alarm = rtc.get("alarm")
    if alarm is None and rtc.get("master") is not None:
        alarm = ("Disabled" if rtc["master"] in ("Disabled", "Disable")
                 else "Enabled")
        rtc["alarm"] = alarm
    on = bool(alarm) and alarm not in ("Disabled", "Disable")
    rtc["alarm_on"] = on
    t = rtc.get("time") or rtc.get("user_time") or ""
    if not on:
        rtc["cycle_text"] = None
        rtc["summary"] = "已关闭"
        return
    if alarm == "Daily Event":
        cycle = "每天"
    elif alarm == "Weekly Event":
        cycle = "每周"
    elif alarm == "Single Event":
        cycle = "单次"
    elif alarm == "User Defined":
        cycle = "自定义（按星期）"
    else:
        cycle = None
    rtc["cycle_text"] = cycle
    day = rtc.get("day") or ""
    date = rtc.get("date") or ""
    weekdays = rtc.get("weekdays") or {}
    days_on = [d for d in _RTC_DAY_ORDER
               if weekdays.get(d) in ("Enabled", "Enable")]
    if cycle == "每天":
        rtc["summary"] = ("每天 " + t).strip() if t else "每天"
    elif cycle == "每周":
        rtc["summary"] = ("每周" + day + " " + t).strip() if (day or t) \
            else "每周"
    elif cycle == "单次":
        rtc["summary"] = ("单次 " + date + " " + t).strip() if (date or t) \
            else "单次"
    elif cycle and cycle.startswith("自定义"):
        if days_on:
            rtc["summary"] = ("指定星期（%s）%s"
                              % ("、".join(days_on), (" " + t) if t else ""))
        else:
            rtc["summary"] = ("自定义，星期未启用 " + t).strip() if t \
                else "自定义，星期未启用"
    else:
        rtc["summary"] = (" ".join(x for x in (alarm, day, date, t) if x)
                          or "已启用")


def parse_current_setting(entries):
    """Lenovo CurrentSetting 全量解析（纯函数，mock 单测覆盖）。

    输入：CurrentSetting 字符串列表（含 None/空串——255 项全量枚举中有
    CurrentSetting 为空的项，须容错跳过）。
    输出：{"items": [{item,value,optional,status,note}],
           "rtc": {语义键: 原值, weekdays: {...}, alarm_on, cycle_text,
                   summary},
           "rtc_present": bool}
    未知项原样保留在 items；RTC 项映射见 _RTC_ITEM_KEYS。"""
    items, rtc, weekdays = [], {}, {}
    for e in entries or []:
        parsed = parse_current_setting_entry(e)
        if parsed is None:
            continue
        items.append(parsed)
        norm = _norm_item(parsed["item"])
        if norm in _RTC_WEEKDAYS and parsed["value"]:
            weekdays[parsed["item"].strip()] = parsed["value"]
            continue
        key = _RTC_ITEM_KEYS.get(norm)
        if key and parsed["value"] and key not in rtc:
            rtc[key] = parsed["value"]
    rtc_present = bool(rtc or weekdays)
    if rtc_present:
        if weekdays:
            rtc["weekdays"] = weekdays
        _rtc_decorate(rtc)
    return {"items": items, "rtc": (rtc if rtc_present else {}),
            "rtc_present": rtc_present}


# ======================================================================
# ③ Windows 唤醒定时器（powercfg /waketimers）
# ======================================================================
def parse_waketimers(text):
    """解析 powercfg /waketimers 输出（纯函数，双格式容错）。

    格式 A（zh-CN Win10 实测）：[SERVICE] <path> (svc) 设置的计时器在
    18:48:18 过期(位于 2026/9/16 上)。 + 下一行 原因: ...
    格式 B（经典口径）：计时器 ID [0]: ... [AUTHOR: ...] + 所有者: ..."""
    items = []
    cur = None
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = _WAKE_SVC_RE.match(line)
        if m:
            body = m.group(2).strip()
            item = {"type": m.group(1).upper(), "owner": "", "wake_time": "",
                    "reason": "", "author": "", "description": ""}
            om = _WAKE_SVC_OWNER_SPLIT_RE.split(body, maxsplit=1)
            item["owner"] = om[0].strip() if om and om[0].strip() else body
            tm = _WAKE_SVC_TIME_RE.search(body)
            if tm:
                dm = (_WAKE_SVC_DATE_ZH_RE.search(body)
                      or _WAKE_SVC_DATE_EN_RE.search(body))
                item["wake_time"] = ((dm.group(1) + " " if dm else "")
                                     + tm.group(1))
            cur = item
            items.append(item)
            continue
        rm = _WAKE_REASON_RE.match(line)
        if rm:
            if cur is not None:
                cur["reason"] = rm.group(1).strip()
            continue
        m = _WAKE_HEAD_RE.search(line)
        if m:
            rest = m.group(2) or ""
            item = {"type": None, "owner": "", "wake_time": "",
                    "reason": "", "author": "", "description": ""}
            iso = _WAKE_ISO_RE.search(rest)
            if iso:
                item["wake_time"] = iso.group(1)
            am = _WAKE_AUTHOR_RE.search(rest)
            if am:
                item["author"] = am.group(1).strip()
            dm = _WAKE_DESC_RE.search(rest)
            if dm:
                item["description"] = dm.group(1).strip()
            cur = item
            items.append(item)
            continue
        if cur is not None:
            om = _WAKE_OWNER_RE.match(line)
            if om and not cur.get("owner"):
                cur["owner"] = om.group(1).strip()
    return items


def collect_wake_timers(runner=None, timeout=20):
    rc, out, err = _run_cmd(["powercfg", "/waketimers"], runner=runner,
                            timeout=timeout)
    text = (out or "") + "\n" + (err or "")
    if rc != 0:
        if _WAKE_DENY_RE.search(text):
            return {"ok": True, "need_admin": True, "count": 0, "items": [],
                    "error": "需管理员权限查看唤醒定时器（本机只读，未提权）"}
        raise RuntimeError((err or out or "查询失败").strip()[:200])
    items = parse_waketimers(out)
    return {"ok": True, "need_admin": False, "count": len(items),
            "items": items, "raw": out.strip()[:4000]}


# ======================================================================
# ④ 关机类计划任务（schtasks /query /fo csv /v）
# ======================================================================
def parse_schtasks_csv(text):
    """解析 schtasks /v CSV（表头名模糊定位列，中英双语；纯函数）。

    返回 (header_found, rows_dict_list)。"""
    rows = [r for r in csv.reader(io.StringIO(text or "")) if r]
    if not rows:
        return False, []
    header = rows[0]
    low = [(h or "").strip().lower() for h in header]

    def _find(names, default):
        for n in names:
            for i, h in enumerate(low):
                if n in h:
                    return i
        return default

    i_name = _find(("任务名", "taskname"), 1)
    i_act = _find(("要运行的任务", "task to run"), 8)
    i_next = _find(("下次运行时间", "next run"), 2)
    i_stat = _find(("状态", "status"), 3)
    i_type = _find(("计划类型", "schedule type"), 18)

    def _cell(row, idx):
        return row[idx].strip() if idx < len(row) else ""

    out = []
    for row in rows[1:]:
        if not row or not _cell(row, i_name):
            continue
        out.append({
            "name": _cell(row, i_name).lstrip("\\"),
            "next_run": _cell(row, i_next),
            "status": _cell(row, i_stat),
            "action": _cell(row, i_act),
            "schedule_type": _cell(row, i_type),
        })
    return True, out


def filter_shutdown_tasks(rows):
    """筛选关机类任务：动作命中 shutdown/psshutdown，或任务名命中
    shutdown/关机（纯函数）。"""
    out = []
    for r in rows:
        if _SHUTDOWN_ACTION_RE.search(r.get("action") or "") or \
                _SHUTDOWN_NAME_RE.search(r.get("name") or ""):
            out.append(r)
    return out


def collect_shutdown_tasks(runner=None, timeout=60):
    rc, out, err = _run_cmd(["schtasks", "/query", "/fo", "csv", "/v"],
                            runner=runner, timeout=timeout)
    if rc != 0:
        raise RuntimeError((err or out or "枚举失败").strip()[:200])
    _, rows = parse_schtasks_csv(out)
    items = filter_shutdown_tasks(rows)
    return {"ok": True, "count": len(items), "items": items}


# ======================================================================
# ⑤ 快速启动（注册表 HiberbootEnabled + powercfg /a 交叉）
# ======================================================================
def _read_hiberboot():
    """读 HKLM HiberbootEnabled；键不存在返回 (False, None)。只读。"""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Power")
        try:
            val, _ = winreg.QueryValueEx(key, "HiberbootEnabled")
            return True, int(val)
        finally:
            key.Close()
    except FileNotFoundError:
        return False, None
    except OSError as e:
        log("读取快速启动注册表失败: %s" % e, "WARN")
        return False, None


def parse_powercfg_available(text):
    """powercfg /a 判定快速启动是否在「可用」段（中英双语，纯函数）。"""
    t = text or ""
    markers = ("以下睡眠状态在此系统上不可用", "以下睡眠状态不可用",
               "not available on this system")
    cut = len(t)
    for m in markers:
        i = t.lower().find(m.lower())
        if i >= 0:
            cut = min(cut, i)
    available_text = t[:cut].lower()
    for term in ("快速启动", "fast startup"):
        if term in available_text:
            return True
    return False


def collect_fast_startup(runner=None, timeout=20):
    rc, out, err = _run_cmd(["powercfg", "/a"], runner=runner, timeout=timeout)
    available = None
    if rc == 0:
        available = parse_powercfg_available(out)
    registry_present, hiberboot = _read_hiberboot()
    if hiberboot == 1:
        enabled = True
    elif hiberboot == 0:
        enabled = False
    else:
        enabled = bool(available) if available is not None else None
    if available is None:
        note = "未能通过系统电源能力输出确认（%s）" % ((err or out or "unknown")
                                             .strip()[:80])
    else:
        note = ("系统支持快速启动" if available else
                "系统电源能力输出中未见快速启动（可能未开启休眠）")
    return {"registry_present": registry_present,
            "hiberboot_enabled": hiberboot,
            "available": available, "enabled": enabled, "note": note}


# ======================================================================
# 分线判定与快照组装
# ======================================================================
def classify_vendor_line(manufacturer, class_found):
    """（纯函数）厂商 + Lenovo_BiosSetting 类存在性 → 分线与能力。"""
    mfr = str(manufacturer or "").strip()
    if "LENOVO" in mfr.upper() or "联想" in mfr:
        if class_found:
            return ("lenovo_enterprise", "联想（企业线接口可用）",
                    "enterprise_configurable", "企业线可配置")
        return ("lenovo_consumer", "联想（未提供企业线接口）",
                "not_supported", "不支持远程配置")
    return ("other", mfr or "未知厂商", "not_supported",
            "不支持远程配置")


def collect_snapshot(runner=None, probe_bios=True):
    """五段只读采集汇总；任一段失败不阻断其它段（ADR-004）。"""
    errors = []
    snapshot = {"schema": 1,
                "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "collected_ts": int(time.time())}

    # ① 机型
    machine = {}
    try:
        machine = collect_machine(runner=runner)
    except Exception as e:
        errors.append("机型信息: %s" % e)
        machine = {"hostname": "", "manufacturer": "", "model": "",
                   "system_family": ""}
    snapshot["machine"] = machine

    # ② BIOS 企业线
    bios = {"remote_configurable": False, "wmi_class_found": False,
            "write_iface": None,
            "reason": "", "items": [], "rtc": {}}
    try:
        if probe_bios:
            probe = probe_lenovo_bios(runner=runner)
            bios["wmi_class_found"] = probe["class_found"]
            if probe.get("write_iface"):
                bios["write_iface"] = probe["write_iface"]
            if probe.get("error"):
                errors.append("BIOS 项读取: %s" % probe["error"])
            if probe["class_found"]:
                parsed = parse_current_setting(probe.get("items_raw"))
                bios["items"] = parsed["items"]
                bios["rtc"] = parsed["rtc"]
        else:
            bios["wmi_class_found"] = False
    except Exception as e:
        errors.append("BIOS 探测: %s" % e)
    line_key, line_text, cap, cap_text = classify_vendor_line(
        machine.get("manufacturer"), bios.get("wmi_class_found"))
    machine["vendor_line"] = line_key
    machine["vendor_line_text"] = line_text
    machine["capability"] = cap
    machine["capability_text"] = cap_text
    if cap == "enterprise_configurable":
        bios["remote_configurable"] = True
        wi = bios.get("write_iface")
        if wi == "new":
            bios["reason"] = ("检测到企业线 BIOS 配置接口（新一代实例级），"
                              "可读取自动开机项")
        elif wi == "old":
            bios["reason"] = "检测到企业线 BIOS 配置接口，可读取自动开机项"
        else:
            bios["reason"] = ("检测到企业线 BIOS 配置接口，可读取自动开机项"
                              "（远程写入通道待确认）")
    else:
        bios["reason"] = ("本机 BIOS 未提供企业线远程配置接口，"
                          "如需定时开机请在开机时进入 BIOS 菜单人工设置"
                          if line_key == "lenovo_consumer" else
                          "该厂商未适配远程 BIOS 配置，"
                          "如需定时开机请在开机时进入 BIOS 菜单人工设置")
    snapshot["bios"] = bios

    # ③ 唤醒定时器
    try:
        snapshot["wake_timers"] = collect_wake_timers(runner=runner)
    except Exception as e:
        errors.append("唤醒定时器: %s" % e)
        snapshot["wake_timers"] = {"ok": False, "need_admin": False,
                                   "count": 0, "items": [], "error": str(e)}

    # ④ 关机类计划任务
    try:
        snapshot["shutdown_tasks"] = collect_shutdown_tasks(runner=runner)
    except Exception as e:
        errors.append("关机计划任务: %s" % e)
        snapshot["shutdown_tasks"] = {"ok": False, "count": 0, "items": [],
                                      "error": str(e)}

    # ⑤ 快速启动
    try:
        snapshot["fast_startup"] = collect_fast_startup(runner=runner)
    except Exception as e:
        errors.append("快速启动: %s" % e)
        snapshot["fast_startup"] = {"registry_present": False,
                                    "hiberboot_enabled": None,
                                    "available": None, "enabled": None,
                                    "note": str(e)}

    snapshot["errors"] = errors
    return snapshot


# ======================================================================
# P1a · BIOS 定时开机写入闭环（企业线，ADR-006/007）
#   用户配置 → 目标项构造 → 写前原值快照（本地+平台双存档）→ 提权写入
#   → 回读校验（读回≠目标即 failed，绝不静默成功）→ 一键还原
# ======================================================================

BIOS_RTC_ITEM_NAMES = frozenset({
    "Wake Up on Alarm", "Alarm Time(HH:MM:SS)", "Alarm Date(MM/DD/YYYY)",
    "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
    "Saturday",
})
BIOS_WRITE_MODES = ("daily", "workday", "single", "off")


def _norm_bios_value(v):
    """值归一比较：剥方括号/空白、忽略大小写（读取形态 [08:00:00] 与
    写入形态 08:00:00 等价）。"""
    return re.sub(r"[\[\]\s]", "", str(v or "")).lower()


def _validate_hhmm(s):
    """'HH:MM' → 'HH:MM:SS'（秒补 00）；非法抛 ValueError。"""
    m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", str(s or "").strip())
    if not m:
        raise ValueError("时刻格式应为 HH:MM")
    h, mi, se = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if not (0 <= h < 24 and 0 <= mi < 60 and 0 <= se < 60):
        raise ValueError("时刻数值超出范围")
    return "%02d:%02d:%02d" % (h, mi, se)


def _validate_mmddyyyy(s):
    """'MM/DD/YYYY' 或 'YYYY-MM-DD' → 'MM/DD/YYYY'；非法抛 ValueError。"""
    t = str(s or "").strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", t)
    if m:
        t = "%s/%s/%s" % (m.group(2), m.group(3), m.group(1))
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", t)
    if not m:
        raise ValueError("日期格式应为 MM/DD/YYYY")
    mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        time.strptime("%04d-%02d-%02d" % (y, mo, d), "%Y-%m-%d")
    except ValueError:
        raise ValueError("日期不存在")
    return "%02d/%02d/%04d" % (mo, d, y)


_WD_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday")   # 与协议 weekdays[0..6]（周一~周日）对齐
_WD_CN = {"Monday": "周一", "Tuesday": "周二", "Wednesday": "周三",
          "Thursday": "周四", "Friday": "周五", "Saturday": "周六",
          "Sunday": "周日"}


def build_bios_targets(config):
    """用户配置 → (targets[(item,value)], summary_text)；纯函数，mock 单测覆盖。

    项名/值域以 M720t 实测（ADR-005）：Wake Up on Alarm ∈
    Single Event/Daily Event/Weekly Event/User Defined/Disabled；
    周期日期 Alarm Date(MM/DD/YYYY)；时刻 Alarm Time(HH:MM:SS)；
    weekly 模式 weekdays 为 7 元素 0/1（周一~周日，协议契约），
    workday 为 weekly+周一~五 的快捷写法。"""
    config = dict(config or {})
    mode = str(config.get("mode") or "").strip()
    if mode not in BIOS_WRITE_MODES + ("weekly",):
        raise ValueError("未知的定时开机周期")
    if mode == "off":
        return [("Wake Up on Alarm", "Disabled")], "停用定时开机"
    t = _validate_hhmm(config.get("time"))
    if mode == "daily":
        return ([("Wake Up on Alarm", "Daily Event"),
                 ("Alarm Time(HH:MM:SS)", t)], "每天 %s" % t)
    if mode in ("workday", "weekly"):
        if mode == "workday":
            weekdays = [1, 1, 1, 1, 1, 0, 0]
        else:
            weekdays = list(config.get("weekdays") or [])
        if len(weekdays) != 7 or any(x not in (0, 1, True, False)
                                     for x in weekdays):
            raise ValueError("每周开关应为 7 位 0/1（周一至周日）")
        names = [_WD_NAMES[i] for i in range(7) if weekdays[i]]
        if not names:
            raise ValueError("每周周期未选择任何一天")
        targets = [("Wake Up on Alarm", "Weekly Event")]
        for day in _WD_NAMES:
            targets.append((day, "Enabled" if day in names else "Disabled"))
        cn = "、".join(_WD_CN[n] for n in names)
        return targets, "每周（%s）%s" % (cn, t)
    d = _validate_mmddyyyy(config.get("date"))
    return ([("Wake Up on Alarm", "Single Event"),
             ("Alarm Date(MM/DD/YYYY)", d),
             ("Alarm Time(HH:MM:SS)", t)],
            "单次 %s %s" % (d, t))


# --- 写入命令构造（仅提权 worker / 真机验证脚本执行；单测 mock runner）---
def _ps_bios_write(item, value):
    """BIOS 单条写入：老接口优先 → 新一代实例级接口回退（ADR-006 附注③）。

    老接口 Lenovo_SetBiosSetting.SetBiosSetting("Item,Value")：类存在且
    rv=0 直接成功；类不存在（异常）或 rv!=0 → 枚举 root/wmi
    Lenovo_BiosSetting 实例，按 CurrentSetting 前缀匹配目标项，调用该
    实例自带的 SetBiosSetting 方法（新一代接口，无需 SaveBiosSetting
    提交，落盘与否由调用方回读校验兜底）。
    输出 PCSET=<rv|NA> / PCVIA=<old|new> / 可选 PCERR=<异常摘要>——
    rv=null（命令从未执行成功）不再静默，err 透传真实异常消息。
    项名/值均来自受控集合，仍做单引号防御；老/新两处调用内联同一
    字面量（单测 mock 按 request = '...' 提取）。"""
    req = ("%s,%s" % (item, value)).replace("'", "''")
    pref = item.replace("'", "''")
    return (
        "$via = 'old'; $errtxt = ''; $rv = $null; "
        "try { $r = Invoke-CimMethod -Namespace 'root/wmi' "
        "-ClassName 'Lenovo_SetBiosSetting' -MethodName 'SetBiosSetting' "
        "-Arguments @{ request = '%(req)s' } -ErrorAction Stop; "
        "$rv = $r.ReturnValue } "
        "catch { $errtxt = $_.Exception.Message } "
        "if ($null -eq $rv -or $rv -ne 0) { "
        "$via = 'new'; "
        "$inst = Get-CimInstance -Namespace root/wmi "
        "-ClassName Lenovo_BiosSetting -ErrorAction SilentlyContinue | "
        "Where-Object { $_.CurrentSetting -and "
        "$_.CurrentSetting.StartsWith('%(pref)s,') } | "
        "Select-Object -First 1; "
        "if ($inst) { "
        "try { $r2 = Invoke-CimMethod -InputObject $inst "
        "-MethodName 'SetBiosSetting' -Arguments @{ request = '%(req)s' } "
        "-ErrorAction Stop; $rv = $r2.ReturnValue } "
        "catch { $errtxt = $_.Exception.Message; $rv = $null } } "
        "else { $errtxt = ($errtxt + ' no-instance').Trim(); "
        "$rv = $null } } "
        "$errtxt = ($errtxt -replace '\\r?\\n', ' '); "
        "Write-Output ('PCSET=' + $(if ($null -ne $rv) { $rv } "
        "else { 'NA' })); "
        "Write-Output ('PCVIA=' + $via); "
        "if ($errtxt) { Write-Output ('PCERR=' + $errtxt) }"
    ) % {"req": req, "pref": pref}


def _ps_bios_read():
    return ("ConvertTo-Json -Compress -InputObject "
            "@(Get-CimInstance -Namespace root/wmi -ClassName Lenovo_BiosSetting "
            "-ErrorAction SilentlyContinue | ForEach-Object { $_.CurrentSetting })")


def _parse_set_rv(out):
    """PCSET=<rv|NA>；NA = 老/新接口均未成功执行（命令级失败，非 BIOS 拒绝）。"""
    m = re.search(r"PCSET=(-?\d+|NA)", out or "")
    if not m or m.group(1) == "NA":
        return None
    return int(m.group(1))


def _parse_set_via(out):
    """实际生效的写入接口形态：old=老接口；new=新一代实例级接口。"""
    m = re.search(r"PCVIA=(old|new)", out or "")
    return m.group(1) if m else None


def _parse_set_err(out):
    """PCERR= 异常摘要（invalid class / 无实例 / 实例方法异常），截断 160 字符。"""
    m = re.search(r"PCERR=(.*)", out or "", re.S)
    return m.group(1).strip()[:160] if m else None


def read_rtc_map(runner=None, timeout=40):
    """读全量 CurrentSetting → {item: value}（仅 RTC 项；回读/快照共用）。"""
    rc, out, err = _run_cmd(_ps(_ps_bios_read()), runner=runner, timeout=timeout)
    if rc != 0:
        raise RuntimeError((err or out or "读取失败").strip()[:200])
    data = _ps_json(out)
    entries = (data if isinstance(data, list)
               else ([] if data is None else [data]))
    items = {}
    for e in entries:
        d = parse_current_setting_entry(e)
        if d and d["item"] in BIOS_RTC_ITEM_NAMES:
            items[d["item"]] = d["value"]
    return items


def _ps_bios_commit():
    return ("try { $r = Invoke-CimMethod -Namespace root/wmi "
            "-ClassName Lenovo_SaveBiosSetting -MethodName SaveBiosSetting "
            "-ErrorAction Stop; Write-Output ('PCSAVE=' + $r.ReturnValue) } "
            "catch { Write-Output 'PCSAVE=NA' }")


def _bios_commit(runner=None, timeout=40):
    """写入后显式提交（ADR-006 附注：M720t 实机症状「rv=0 接受但不落盘、
    读回旧值」最高概率为部分联想固件需 Lenovo_SaveBiosSetting.SaveBiosSetting()
    提交 pending 写入）。

    探测与提交合一：类不存在 → PCSAVE=NA → 跳过（向后兼容）；提交成功
    PCSAVE=0；提交失败 rv 非 0 如实返回。返回 (committed:bool, note:str)。"""
    rc, out, err = _run_cmd(_ps(_ps_bios_commit()), runner=runner,
                            timeout=timeout)
    m = re.search(r"PCSAVE=(-?\d+|NA)", (out or "").strip())
    if not m:
        return False, "commit probe failed (rc=%s out=%s)" % (
            rc, (out or "").strip()[:60])
    v = m.group(1)
    if v == "NA":
        return False, "no-save-class"
    if v == "0":
        return True, "committed"
    return False, "commit rv=%s" % v


def bios_apply_targets(targets, runner=None, timeout=40):
    """逐项写入 + 显式提交 + 回读校验（两轮值格式尝试，ADR-007）。

    每轮写入后调用 Lenovo_SaveBiosSetting.SaveBiosSetting() 提交 pending
    （类不存在自动跳过，向后兼容，ADR-006 附注）。
    第一轮无括号值；回读不匹配的项第二轮带括号值（读取形态 [V]）。
    读回≠目标即计入 failed，整体 ok=False——绝不静默成功。
    真实执行仅发生在提权 worker / 真机验证脚本；单测传 mock runner。"""
    attempts = []
    for item, want in targets:
        rc, out, err = _run_cmd(_ps(_ps_bios_write(item, want)),
                                runner=runner, timeout=timeout)
        attempts.append({"item": item, "sent": want, "format": "plain",
                         "rc": rc, "rv": _parse_set_rv(out),
                         "via": _parse_set_via(out),
                         "err": _parse_set_err(out)
                                or (err or "").strip()[:160] or None})
    committed, note = _bios_commit(runner=runner, timeout=timeout)
    attempts.append({"item": "__commit__", "sent": "Lenovo_SaveBiosSetting",
                     "format": "commit", "rc": 0, "rv": note})
    try:
        readback = read_rtc_map(runner=runner, timeout=timeout)
    except Exception as e:
        return {"ok": False, "attempts": attempts, "failed": [], "error":
                "回读失败: %s" % e}
    bad = [(i, w) for i, w in targets
           if _norm_bios_value(readback.get(i)) != _norm_bios_value(w)]
    for item, want in bad:
        rc, out, err = _run_cmd(_ps(_ps_bios_write(item, "[%s]" % want)),
                                runner=runner, timeout=timeout)
        attempts.append({"item": item, "sent": "[%s]" % want,
                         "format": "bracket", "rc": rc, "rv": _parse_set_rv(out),
                         "via": _parse_set_via(out),
                         "err": _parse_set_err(out)
                                or (err or "").strip()[:160] or None})
    if bad:
        committed2, note2 = _bios_commit(runner=runner, timeout=timeout)
        attempts.append({"item": "__commit__",
                         "sent": "Lenovo_SaveBiosSetting",
                         "format": "commit", "rc": 0, "rv": note2})
        try:
            readback = read_rtc_map(runner=runner, timeout=timeout)
        except Exception as e:
            return {"ok": False, "attempts": attempts, "failed": [], "error":
                    "回读失败: %s" % e}
    failed = [{"item": i, "want": w, "got": readback.get(i)}
              for i, w in targets
              if _norm_bios_value(readback.get(i)) != _norm_bios_value(w)]
    return {"ok": not failed, "attempts": attempts, "failed": failed,
            "readback": readback}


# --- 原值快照（写前强制；本地 JSON 存档，平台侧以快照上报双存档）---
def _bios_backup_path():
    return os.path.join(data_dir(), "bios_backup.json")


def save_bios_backup(items, machine=None):
    payload = {"schema": 1, "kind": "bios_rtc_backup",
               "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "saved_ts": int(time.time()),
               "machine": dict(machine or {}),
               "items": dict(items), "restored": False}
    tmp = _bios_backup_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _bios_backup_path())
    log("BIOS 原值快照已本地存档（%d 项）" % len(items))
    return payload


def load_bios_backup():
    try:
        with open(_bios_backup_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ======================================================================
# P1b · 定时关机计划任务（两线路通用；schtasks + XML，SYSTEM 运行）
# ======================================================================

PC_SHUTDOWN_TASK = "EyeTermAutoShutdown"
PC_SHUTDOWN_ACTION = "shutdown"
PC_SHUTDOWN_ARGS = "/s /t 60 /d p:0:0"          # 产品路径（SPEC §4）
PC_SHUTDOWN_ARGS_VERIFY = "/s /t 90 /d p:0:0"   # 真机验证路径（创建即停用）

_SHUTDOWN_TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>EyeTerm</Author>
    <Description>由观枢终端平台（EyeTerm）管理的定时关机任务</Description>
  </RegistrationInfo>
  <Triggers>{trigger}</Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>{cmd}</Command>
      <Arguments>{args}</Arguments>
    </Exec>
  </Actions>
  <Principals>
    <Principal id="Author">
      <UserId>S-1-5-18</UserId>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>
    <Enabled>{enabled}</Enabled>
  </Settings>
</Task>
"""

_WEEKDAY_BITS = {"Monday": 1, "Tuesday": 2, "Wednesday": 4, "Thursday": 8,
                 "Friday": 16, "Saturday": 32, "Sunday": 64}


def _xml_days_from_names(names):
    return "".join("<%s/>" % n for n in names)


def build_shutdown_task_xml(mode, time_hhmm, date=None,
                            enabled=True, args=PC_SHUTDOWN_ARGS,
                            now=None):
    """定时关机任务 XML（纯函数；单测覆盖三种周期）。

    mode ∈ daily/workday/single（与 BIOS 侧周期语义对齐）；
    StartBoundary 取当天日期 + 用户时刻（一次性触发用 TimeTrigger，
    周期触发 StartBoundary 仅作首次基准）。"""
    t = _validate_hhmm(time_hhmm)
    now = now or time.localtime()
    day0 = time.strftime("%Y-%m-%dT", now)
    start = "%s%s:00" % (day0, t)
    if mode == "daily":
        trigger = ("<CalendarTrigger><StartBoundary>%s</StartBoundary>"
                   "<Enabled>true</Enabled><ScheduleByDay>"
                   "<DaysInterval>1</DaysInterval></ScheduleByDay>"
                   "</CalendarTrigger>" % start)
    elif mode == "workday":
        names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        trigger = ("<CalendarTrigger><StartBoundary>%s</StartBoundary>"
                   "<Enabled>true</Enabled><ScheduleByWeek>"
                   "<Weekdays>%s</Weekdays></ScheduleByWeek>"
                   "</CalendarTrigger>" % (start, _xml_days_from_names(names)))
    elif mode == "single":
        d = _validate_mmddyyyy(date)
        mo, dd, yy = d.split("/")
        start = "%s-%s-%sT%s:00" % (yy, mo, dd, t)
        trigger = ("<TimeTrigger><StartBoundary>%s</StartBoundary>"
                   "<Enabled>true</Enabled></TimeTrigger>" % start)
    else:
        raise ValueError("未知的定时关机周期")
    return _SHUTDOWN_TASK_XML.format(trigger=trigger, cmd=PC_SHUTDOWN_ACTION,
                                     args=args,
                                     enabled="true" if enabled else "false")


# ======================================================================
# P1 提权 worker（单操作子进程；最小职责，见 ADR-008）
#   exe 态：desktop.py 入口拦截；python 态：power_control.__main__ 拦截
# ======================================================================

ELEVATED_FLAG = "--pc-elevated-worker"


def _ps_schtasks_set(spec):
    """schtasks /Create /XML（XML 由主进程生成随 spec 传入）。"""
    return (["$r = schtasks /Create /F /TN '{tn}' /XML '{xml}'; "
             "Write-Output ('PCST=' + $LASTEXITCODE)"
             .format(tn=PC_SHUTDOWN_TASK, xml=spec["xml_file"])])


def _ps_schtasks_toggle(enable):
    flag = "/ENABLE" if enable else "/DISABLE"
    return ["$r = schtasks /Change /TN '{tn}' {f}; "
            "Write-Output ('PCST=' + $LASTEXITCODE)"
            .format(tn=PC_SHUTDOWN_TASK, f=flag)]


def _ps_schtasks_delete():
    return ["$r = schtasks /Delete /TN '{tn}' /F; "
            "Write-Output ('PCST=' + $LASTEXITCODE)".format(
                tn=PC_SHUTDOWN_TASK)]


def _schtasks_elevated_op(op, spec):
    """worker 内执行 schtasks 单操作（提权上下文）。"""
    if op == "schtasks_set":
        cmd = _ps_schtasks_set(spec)
    elif op == "schtasks_toggle":
        cmd = _ps_schtasks_toggle(bool(spec.get("enable")))
    elif op == "schtasks_delete":
        cmd = _ps_schtasks_delete()
    else:
        return {"ok": False, "error": "unknown op"}
    rc, out, err = _run_cmd(_ps(cmd[0]), timeout=60)
    code = None
    m = re.search(r"PCST=(-?\d+)", out or "")
    if m:
        code = int(m.group(1))
    ok = (rc == 0 and code == 0)
    return {"ok": ok, "rc": rc, "code": code,
            "detail": (out or err or "").strip()[:200]}


def elevated_worker_entry(args):
    """提权子进程入口：args = [op, opfile]。

    只执行单个操作（BIOS 写/还原 或 schtasks 单操作），结果 JSON 写
    opfile + ".result" 后退出——禁止在提权会话内做任何其它事（ADR-008）。"""
    res = None
    try:
        op, opfile = str(args[0]), str(args[1])
        with open(opfile, "r", encoding="utf-8") as f:
            spec = json.load(f)
        if op in ("bios_apply", "bios_restore"):
            targets = [tuple(x) for x in spec.get("targets", [])]
            res = bios_apply_targets(targets)
        elif op in ("schtasks_set", "schtasks_toggle", "schtasks_delete"):
            res = _schtasks_elevated_op(op, spec)
        else:
            res = {"ok": False, "error": "unknown op"}
    except Exception as e:
        res = {"ok": False, "error": str(e)}
    try:
        rf = str(args[1]) + ".result"
        with open(rf + ".tmp", "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False)
        os.replace(rf + ".tmp", rf)   # 原子写：父进程 0.5s 轮询不读半截
    except OSError:
        pass
    log("提权 worker 完成: op=%s ok=%s" % (args[0], res.get("ok")))
    return 0 if res.get("ok") else 1


def _self_launch_params(extra):
    """runas 参数串：PyInstaller 态（sys.frozen）拉起自身 exe；
    python 态拉起引擎脚本 __main__。ShellExecuteW 的 file 恒为
    sys.executable，故 exe 态 params 不含脚本路径。"""
    if getattr(sys, "frozen", False):
        script = None
    else:
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "power_control.py")
    parts = []
    if script:
        parts.append('"%s"' % script)
    parts.extend(extra)
    return " ".join(parts)


def run_elevated(op, spec, wait_timeout=240):
    """父进程编排：写 opfile → runas 提权子进程 → 轮询 .result。

    UAC 取消（ShellExecuteW 返回 ≤32）与超时均如实返回，不静默成功。"""
    opfile = os.path.join(data_dir(), "_op_%d_%d.json"
                          % (os.getpid(), int(time.time() * 1000) % 10 ** 9))
    try:
        with open(opfile, "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False)
    except OSError as e:
        return {"ok": False, "error": "操作文件写入失败: %s" % e}
    result_file = opfile + ".result"
    try:
        os.remove(result_file)
    except OSError:
        pass
    params = _self_launch_params([ELEVATED_FLAG, op, '"%s"' % opfile])
    log("拉起提权子进程: op=%s" % op)
    ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable,
                                              params, None, 0)  # SW_HIDE
    if int(ret) <= 32:
        return {"ok": False, "cancelled": True,
                "error": "提权未确认或被拒绝（UAC 已取消）"}
    deadline = time.time() + wait_timeout
    while time.time() < deadline:
        if os.path.exists(result_file):
            try:
                with open(result_file, "r", encoding="utf-8") as f:
                    res = json.load(f)
                return res
            except (OSError, ValueError) as e:
                return {"ok": False, "error": "结果读取失败: %s" % e}
        time.sleep(0.5)
    return {"ok": False, "error": "提权操作等待超时（%ds）" % wait_timeout}


if __name__ == "__main__":  # python 态提权子进程入口（exe 态由 desktop.py 拦截）
    if len(sys.argv) > 1 and sys.argv[1] == ELEVATED_FLAG:
        sys.exit(elevated_worker_entry(sys.argv[2:]))


# ======================================================================
# 平台上报（语义复制 desktop_policy PlatformTransport；不动 uplink.py）
# ======================================================================
def _uplink_tid_inproc():
    try:
        import uplink
        r = uplink.handle_uplink_status(None) or {}
        u = r.get("uplink") or {}
        return (str(u.get("terminal_id") or "").strip() or None)
    except Exception:
        return None


def _hostname_tid():
    try:
        import socket
        host = socket.gethostname() or os.environ.get("COMPUTERNAME") \
            or "UNKNOWN"
    except Exception:
        host = os.environ.get("COMPUTERNAME") or "UNKNOWN"
    return "WIN-" + re.sub(r"[^A-Za-z0-9_.\-]", "_", host).strip("_")[:40]


class PlatformReporter(object):
    """平台直连上报（X-ETP-Token；terminal_id 三级解析同 ADR-006 口径）。"""

    def __init__(self, config_path=None):
        self.cfg_path = config_path or os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "winhelper", "uplink_config.json")

    def _cfg(self):
        base = token = tid = ""
        try:
            with open(self.cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            base = str(cfg.get("server_url") or "")
            token = str(cfg.get("token") or "")
            tid = str(cfg.get("terminal_id") or "").strip()
        except (OSError, ValueError):
            pass
        if not tid:
            tid = _uplink_tid_inproc() or _hostname_tid()
        return base, token, tid

    def _request(self, method, path, body=None, timeout=30):
        import urllib.request
        base, token, tid = self._cfg()
        if not base or not token or not tid:
            raise RuntimeError("not_registered")
        url = base.rstrip("/") + path.format(tid=tid)
        data = None
        headers = {"X-ETP-Token": token}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        return json.loads(_decode(raw))

    def report_snapshot(self, snapshot):
        return self._request("POST", REPORT_PATH, body=snapshot)


_last_report_lock = threading.Lock()


def _last_report_path():
    return os.path.join(data_dir(), "last_report.json")


def _should_auto_report(now=None):
    now = now if now is not None else time.time()
    with _last_report_lock:
        try:
            with open(_last_report_path(), "r", encoding="utf-8") as f:
                ts = int(json.load(f).get("ts") or 0)
            return (now - ts) >= REPORT_THROTTLE_SEC
        except (OSError, ValueError):
            return True


def _mark_reported(now=None):
    now = now if now is not None else time.time()
    with _last_report_lock:
        try:
            with open(_last_report_path(), "w", encoding="utf-8") as f:
                json.dump({"ts": int(now)}, f)
        except OSError:
            pass


def _auto_report_async(snapshot):
    """打开菜单自动上报（12h 节流）；后台线程，结果状态落档供 UI 展示
    （修复「平台存档：—」：自动上报成功/失败此前不回传界面）。"""
    if not _should_auto_report():
        return False

    def _job():
        try:
            PlatformReporter().report_snapshot(snapshot)
            _mark_reported()
            _save_report_state(True)
            log("快照已自动上报平台")
        except Exception as e:
            _save_report_state(False, str(e))
            log("自动上报失败: %s" % e, "WARN")

    threading.Thread(target=_job, daemon=True).start()
    return True


# ======================================================================
# 桥接处理器（bridge.py ROUTES 直连）
# ======================================================================
def handle_pc_snapshot(params=None):
    """采集本机电源策略快照（只读）。触发 12h 节流的后台自动上报；
    返回体携带平台存档状态（auto_report + report_state）供 UI 展示。"""
    try:
        snapshot = collect_snapshot()
        auto = "throttled"
        try:
            auto = "triggered" if _auto_report_async(snapshot) else "throttled"
        except Exception as e:
            log("自动上报调度失败: %s" % e, "WARN")
        return {"success": True, "snapshot": snapshot,
                "auto_report": auto, "report_state": _load_report_state(),
                "active_policy": load_policy_state()}
    except Exception as e:
        log("快照采集失败: %s" % e, "ERROR")
        return {"success": False, "error": str(e)}


def handle_pc_report(params=None, data=None):
    """手动上报：采集并立即推送平台（同步）。

    data={"human_set": true} 时标记消费线「已在 BIOS 人工设置」登记后上报。"""
    try:
        snapshot = collect_snapshot()
    except Exception as e:
        log("上报前采集失败: %s" % e, "ERROR")
        return {"success": False, "error": "采集失败: %s" % e}
    if isinstance(data, dict) and data.get("human_set"):
        bios = snapshot.setdefault("bios", {})
        bios["human_set_flag"] = True
        bios["human_set_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        resp = PlatformReporter().report_snapshot(snapshot)
        _mark_reported()
        _save_report_state(True)
        log("快照已手动上报平台: %s" % json.dumps(resp, ensure_ascii=False)[:200])
        return {"success": True, "reported": True, "server": resp}
    except RuntimeError as e:
        if str(e) == "not_registered":
            _save_report_state(False, "not_registered")
            return {"success": False, "error": "尚未接入中心平台，请先在主页完成平台接入"}
        _save_report_state(False, str(e))
        return {"success": False, "error": str(e)}
    except Exception as e:
        _save_report_state(False, str(e))
        log("手动上报失败: %s" % e, "WARN")
        return {"success": False, "error": "上报失败（平台不可达或未接入）"}


# ======================================================================
# P1 · 异步任务模式（提权操作耗时含 UAC 等待，超 apiFetch 15s 上限；
#       desktop-policy dpRunTask 同款形态：POST 回 task_id + 轮询）
# ======================================================================
_PC_TASKS = {}
_PC_TASKS_LOCK = threading.Lock()
_PC_TASK_TTL = 1800        # 任务记录保留 30 分钟
_PC_TASK_SEQ = [0]


def _report_state_path():
    return os.path.join(data_dir(), "report_state.json")


def _save_report_state(ok, error=None):
    try:
        with open(_report_state_path(), "w", encoding="utf-8") as f:
            json.dump({"last_ok": bool(ok), "last_error": error,
                       "last_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "ts": int(time.time())}, f, ensure_ascii=False)
    except OSError:
        pass


def _load_report_state():
    try:
        with open(_report_state_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _pc_task_create(kind):
    with _PC_TASKS_LOCK:
        now = time.time()
        for k in [k for k, v in _PC_TASKS.items()
                  if now - v["started_ts"] > _PC_TASK_TTL]:
            _PC_TASKS.pop(k, None)
        _PC_TASK_SEQ[0] += 1
        tid = "PCT-%06d" % _PC_TASK_SEQ[0]
        _PC_TASKS[tid] = {"task_id": tid, "kind": kind, "status": "running",
                          "started_ts": int(now), "result": None}
        return tid


def _pc_task_finish(tid, result):
    with _PC_TASKS_LOCK:
        t = _PC_TASKS.get(tid)
        if t:
            t["status"] = "done" if result.get("ok") else "error"
            t["result"] = result


def _pc_task_get(tid):
    with _PC_TASKS_LOCK:
        t = _PC_TASKS.get(str(tid or ""))
        return dict(t) if t else None


def _pc_spawn_task(kind, job):
    tid = _pc_task_create(kind)

    def _runner():
        try:
            res = job()
        except Exception as e:
            log("任务 %s(%s) 异常: %s" % (tid, kind, e), "ERROR")
            res = {"ok": False, "error": str(e)}
        _pc_task_finish(tid, res if isinstance(res, dict) else {"ok": False})

    threading.Thread(target=_runner, daemon=True).start()
    return tid


# --- 任务体：BIOS 应用 / 还原 / 关机任务管理（真实写入仅在此路径） ---
_LAST_APPLY = {"data": None, "kind": None, "ts": 0}


def _record_apply(kind, res):
    """最近一次 BIOS apply/restore 结果运行态缓存（pc_diag 采集源）。"""
    _LAST_APPLY["data"] = res
    _LAST_APPLY["kind"] = kind
    _LAST_APPLY["ts"] = int(time.time())


def _ps_probe_save_class():
    return ("$c = Get-CimClass -Namespace root/wmi "
            "-ClassName Lenovo_SaveBiosSetting -ErrorAction SilentlyContinue; "
            "Write-Output ('PCSC=' + [bool]$c)")


def _ps_probe_pwd_state():
    return ("try { $p = Get-CimInstance -Namespace root/wmi "
            "-ClassName Lenovo_BiosPasswordSettings -ErrorAction Stop | "
            "Select-Object -First 1; Write-Output ('PCPWD=' + $p.PasswordState) } "
            "catch { Write-Output 'PCPWD=NA' }")


def collect_diag():
    """pc_diag 只读诊断采集（ADR-006 定案工具；纯只读零写入，各段失败不阻断）。

    返回：{schema, hostname, ts, save_class, password_state, rtc_readback,
    last_apply, log_tail[200 行]}；client_version 由 uplink 回执层补齐。"""
    data = {"schema": 1,
            "hostname": None, "ts": int(time.time()),
            "save_class": None, "password_state": None,
            "rtc_readback": {}, "last_apply": None, "log_tail": []}
    try:
        import socket
        data["hostname"] = socket.gethostname()
    except Exception:
        pass
    try:
        rc, out, _ = _run_cmd(_ps(_ps_probe_save_class()))
        m = re.search(r"PCSC=(True|False)", out or "")
        data["save_class"] = (m.group(1) == "True") if m else None
    except Exception:
        pass
    try:
        data["write_iface"] = probe_write_iface()
    except Exception:
        pass
    try:
        rc, out, _ = _run_cmd(_ps(_ps_probe_pwd_state()))
        m = re.search(r"PCPWD=(-?\d+|NA)", out or "")
        data["password_state"] = m.group(1) if m else None
    except Exception:
        pass
    try:
        data["rtc_readback"] = read_rtc_map()
    except Exception as e:
        data["rtc_readback"] = {"error": str(e)[:120]}
    if _LAST_APPLY.get("data"):
        data["last_apply"] = {"kind": _LAST_APPLY.get("kind"),
                              "ts": _LAST_APPLY.get("ts"),
                              "result": _LAST_APPLY.get("data")}
    try:
        data["log_tail"] = _log_tail(200)
    except Exception:
        pass
    return data


def _log_tail(lines):
    """当日+前一日 pc_*.log 尾部 lines 行（诊断用，读失败返回空）。"""
    d = os.path.join(data_dir(), "logs")
    if not os.path.isdir(d):
        return []
    days = [time.strftime("pc_%Y%m%d.log", time.localtime(time.time() - 86400 * i))
            for i in range(2)]
    buf = []
    for name in days:
        p = os.path.join(d, name)
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                buf.extend(f.read().splitlines())
        except OSError:
            continue
    return buf[-lines:]


def _job_bios_apply(config):
    targets, summary = build_bios_targets(config)
    probe = probe_lenovo_bios()
    if not probe.get("class_found"):
        return {"ok": False, "rejected": "capability",
                "error": "本机不支持远程配置定时开机（可在开机自检时进入 BIOS 人工设置）"}
    if probe.get("write_iface") == "none":
        return {"ok": False, "rejected": "capability",
                "error": "本机 BIOS 无远程写入通道（老/新接口均缺失），"
                         "请开机自检时进入 BIOS 人工设置"}
    try:
        machine = collect_machine()
        items = read_rtc_map()
    except Exception as e:
        return {"ok": False, "error": "写入前读取失败: %s" % e}
    save_bios_backup(items, machine=machine)
    try:
        PlatformReporter().report_snapshot(collect_snapshot())  # 平台双存档
    except Exception as e:
        log("备份的平台存档失败（不阻断）: %s" % e, "WARN")
    res = run_elevated("bios_apply",
                       {"targets": [[i, v] for i, v in targets]})
    # P1a 首轮实机迭代：失败回执步骤化（用户反馈问题④——此前 failed 明细
    # 不含 error 键，UI 只能显示笼统「操作未成功」）
    if not res.get("ok") and not res.get("error"):
        failed = res.get("failed") or []
        if failed:
            parts = ["「%s」写入未生效：读回为「%s」（目标「%s」）"
                     % (f.get("item"), f.get("got"), f.get("want"))
                     for f in failed]
            res["error"] = "回读校验不符：" + "；".join(parts)
        else:
            res["error"] = "写入未生效（详见日志的尝试明细）"
    res["summary"] = summary
    if res.get("ok"):
        log("BIOS 定时开机已应用: %s" % summary)
    else:
        log("BIOS 应用失败: %s | attempts=%s"
            % (res.get("error"),
               json.dumps(res.get("attempts"), ensure_ascii=False)[:400]))
    _record_apply("bios_apply", res)
    return res


def _job_bios_restore():
    bak = load_bios_backup()
    if not bak or not bak.get("items"):
        return {"ok": False, "error": "尚无已保存的原值快照，无法还原"}
    probe = probe_lenovo_bios()
    if not probe.get("class_found"):
        return {"ok": False, "rejected": "capability",
                "error": "本机不支持远程配置定时开机"}
    targets = list(bak["items"].items())
    res = run_elevated("bios_restore",
                       {"targets": [[i, v] for i, v in targets]})
    if res.get("ok"):
        bak["restored"] = True
        bak["restored_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(_bios_backup_path() + ".tmp", "w",
                      encoding="utf-8") as f:
                json.dump(bak, f, ensure_ascii=False, indent=1)
            os.replace(_bios_backup_path() + ".tmp", _bios_backup_path())
        except OSError:
            pass
        log("BIOS 配置已还原为初始值（%d 项）" % len(targets))
    _record_apply("bios_restore", res)
    return res


def _job_shutdown_set(config, wait_timeout=240):
    mode = str(config.get("mode") or "").strip()
    if mode not in ("daily", "workday", "single"):
        return {"ok": False, "error": "未知的定时关机周期"}
    t = _validate_hhmm(config.get("time"))
    date = config.get("date")
    if mode == "single":
        date = _validate_mmddyyyy(date)
    xml = build_shutdown_task_xml(mode, t, date=date)
    xml_file = os.path.join(data_dir(), "_schtasks_%d_%d.xml"
                            % (os.getpid(), int(time.time() * 1000) % 10 ** 9))
    try:
        with open(xml_file, "w", encoding="utf-16") as f:
            f.write(xml)
    except OSError as e:
        return {"ok": False, "error": "任务文件写入失败: %s" % e}
    try:
        res = run_elevated("schtasks_set", {"xml_file": xml_file},
                           wait_timeout=wait_timeout)
    finally:
        try:
            os.remove(xml_file)
        except OSError:
            pass
    res["task_name"] = PC_SHUTDOWN_TASK
    if mode == "single":
        res["summary"] = "单次 %s %s" % (date, t)
    else:
        res["summary"] = "%s %s" % ("每天" if mode == "daily" else "工作日", t)
    if res.get("ok"):
        log("定时关机任务已应用: %s" % res["summary"])
    return res


def _job_shutdown_remove():
    res = run_elevated("schtasks_delete", {})
    res["task_name"] = PC_SHUTDOWN_TASK
    if res.get("ok"):
        log("定时关机任务已删除")
    return res


def _job_shutdown_toggle(enable):
    res = run_elevated("schtasks_toggle", {"enable": bool(enable)})
    res["task_name"] = PC_SHUTDOWN_TASK
    if res.get("ok"):
        log("定时关机任务已%s" % ("启用" if enable else "停用"))
    return res


def handle_pc_shutdown_toggle(params=None, data=None):
    """停用/启用定时关机计划任务（异步任务；提权执行）。"""
    enable = bool((data or {}).get("enable"))
    tid = _pc_spawn_task("shutdown_toggle",
                         lambda: _job_shutdown_toggle(enable))
    return {"success": True, "task_id": tid}


# --- 桥接处理器（body 双参；bridge.call 对 /api/powercontrol/ 特判透传）---
def handle_pc_bios_apply(params=None, data=None):
    """应用定时开机配置（异步任务；提权执行，UAC 确认制）。"""
    try:
        targets, summary = build_bios_targets(data)
    except ValueError as e:
        return {"success": False, "error": str(e)}
    tid = _pc_spawn_task("bios_apply", lambda: _job_bios_apply(dict(data)))
    log("定时开机配置任务已受理: %s（%d 项）" % (summary, len(targets)))
    return {"success": True, "task_id": tid, "summary": summary}


def handle_pc_bios_restore(params=None, data=None):
    """一键还原 BIOS 定时开机初始值（异步任务；以最近一次备份为准）。"""
    bak = load_bios_backup()
    if not bak or not bak.get("items"):
        return {"success": False, "error": "尚无已保存的原值快照，无法还原"}
    tid = _pc_spawn_task("bios_restore", _job_bios_restore)
    return {"success": True, "task_id": tid,
            "summary": "还原为 %s 保存的初始值" % bak.get("saved_at", "此前")}


def handle_pc_shutdown_set(params=None, data=None):
    """创建/更新定时关机计划任务（异步任务；提权执行）。"""
    data = dict(data or {})
    mode = str(data.get("mode") or "").strip()
    if mode not in ("daily", "workday", "single"):
        return {"success": False, "error": "未知的定时关机周期"}
    try:
        _validate_hhmm(data.get("time"))
        if mode == "single":
            _validate_mmddyyyy(data.get("date"))
    except ValueError as e:
        return {"success": False, "error": str(e)}
    tid = _pc_spawn_task("shutdown_set", lambda: _job_shutdown_set(data))
    return {"success": True, "task_id": tid}


def handle_pc_shutdown_remove(params=None, data=None):
    """删除定时关机计划任务（异步任务；提权执行）。"""
    tid = _pc_spawn_task("shutdown_remove", _job_shutdown_remove)
    return {"success": True, "task_id": tid}


def handle_pc_task_status(params=None, data=None):
    """任务进度轮询：?task_id=PCT-xxxxxx。"""
    tid = (params or {}).get("task_id")
    t = _pc_task_get(tid)
    if not t:
        return {"success": False, "error": "任务不存在"}
    return {"success": True, "task": {"task_id": t["task_id"],
                                      "kind": t["kind"],
                                      "status": t["status"],
                                      "result": t["result"]}}


# ======================================================================
# P1 · 平台下发策略执行器（pc_apply_policy 命令体，ADR-009；
#       协议 main 定稿：boot/shutdown 可只出现其一）
# ======================================================================

def _policy_state_path():
    return os.path.join(data_dir(), "policy_state.json")


def _save_policy_state(policy_id, summary):
    try:
        with open(_policy_state_path(), "w", encoding="utf-8") as f:
            json.dump({"policy_id": policy_id, "summary": summary,
                       "applied_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "ts": int(time.time())}, f, ensure_ascii=False)
    except OSError:
        pass


def load_policy_state():
    try:
        with open(_policy_state_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _policy_boot_to_config(boot):
    """协议 boot 块 → build_bios_targets 配置。"""
    mode = str(boot.get("mode") or "disabled")
    if not boot.get("enabled", True) or mode == "disabled":
        return {"mode": "off"}
    if mode == "daily":
        return {"mode": "daily", "time": boot.get("time")}
    if mode == "weekly":
        return {"mode": "weekly", "weekdays": boot.get("weekdays"),
                "time": boot.get("time")}
    if mode == "single":
        return {"mode": "single", "time": boot.get("time"),
                "date": boot.get("date")}
    raise ValueError("未知的开机周期: %s" % mode)


def apply_policy(args):
    """平台下发策略执行（同步闭环；无人值守提权限制如实回执，ADR-009）。

    回执：{policy_id, op, ok, steps:{bios?, shutdown_task?}, capability}；
    steps 仅包含协议中出现的块；capability ∈ enterprise/not_supported/None。"""
    args = dict(args or {})
    policy_id = str(args.get("policy_id") or "")[:64]
    steps = {}
    capability = None
    ok_all = True
    summaries = []

    boot = args.get("boot")
    if isinstance(boot, dict):
        try:
            capability = ("enterprise" if probe_lenovo_bios().get(
                "class_found") else "not_supported")
        except Exception:
            capability = "not_supported"
        if capability != "enterprise":
            steps["bios"] = {
                "ok": False,
                "error": "capability_not_supported（本机不支持远程配置，"
                         "请开机自检时进入 BIOS 人工设置）"}
            ok_all = False
        else:
            try:
                cfg = _policy_boot_to_config(boot)
                targets, summary = build_bios_targets(cfg)
                items = read_rtc_map()
                machine = collect_machine()
                save_bios_backup(items, machine=machine)
                try:
                    PlatformReporter().report_snapshot(collect_snapshot())
                except Exception as e:
                    log("策略备份平台存档失败（不阻断）: %s" % e, "WARN")
                res = bios_apply_targets(targets)
                if res.get("ok"):
                    steps["bios"] = {"ok": True, "summary": summary}
                    summaries.append("定时开机 " + summary)
                else:
                    steps["bios"] = {
                        "ok": False,
                        "error": "apply_failed: %s" % json.dumps(
                            res.get("failed"), ensure_ascii=False)[:200]}
                    ok_all = False
            except ValueError as e:
                steps["bios"] = {"ok": False, "error": str(e)}
                ok_all = False
            except Exception as e:
                steps["bios"] = {"ok": False, "error": str(e)[:200]}
                ok_all = False

    sd = args.get("shutdown")
    if isinstance(sd, dict):
        enabled = bool(sd.get("enabled")) \
            and str(sd.get("mode") or "disabled") != "disabled"
        if enabled:
            cfg = {"mode": sd.get("mode"), "time": sd.get("time"),
                   "date": sd.get("date")}
            res = _job_shutdown_set(cfg, wait_timeout=30)
            res.pop("task_name", None)
            if res.get("ok"):
                steps["shutdown_task"] = {
                    "ok": True, "task_name": PC_SHUTDOWN_TASK,
                    "summary": res.get("summary")}
                summaries.append("定时关机 " + str(res.get("summary")))
            else:
                steps["shutdown_task"] = {
                    "ok": False, "task_name": PC_SHUTDOWN_TASK,
                    "error": res.get("error") or "apply_failed"}
                ok_all = False
        else:
            res = _job_shutdown_remove()
            steps["shutdown_task"] = {
                "ok": bool(res.get("ok")), "task_name": PC_SHUTDOWN_TASK,
                "error": None if res.get("ok")
                else (res.get("error") or "remove_failed")}
            if not res.get("ok"):
                ok_all = False
            else:
                summaries.append("定时关机任务已移除")

    if summaries:
        _save_policy_state(policy_id, "；".join(summaries))
    return {"policy_id": policy_id, "op": str(args.get("op") or "apply"),
            "ok": ok_all, "steps": steps, "capability": capability}
