# -*- coding: utf-8 -*-
"""power_control · 终端「自动开关机」快照引擎（观枢终端平台｜EyeTerm）。

P0 只读：机型识别 / BIOS 企业线（Lenovo_BiosSetting）探测与解析 /
Windows 唤醒定时器 / 关机类计划任务 / 快速启动状态。
零写操作、零 shutdown 调用（决策 ADR-001）；测试夹具禁真实关机命令串（ADR-018 惯例）。

决策 docs/DECISIONS.md（ADR-001~004）；规格 docs/POWER_CONTROL_SPEC.md（主仓）。
同步契约：本文件 → 主应用根 power_control.py（bridge 路由 /api/powercontrol/*）。
"""
import csv
import io
import json
import os
import re
import subprocess
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
def probe_lenovo_bios(runner=None, timeout=30):
    """探测 root/wmi Lenovo_BiosSetting：类不存在 → class_found=False；
    存在 → 全量读取 CurrentSetting（"ItemName,Value" 列表）。
    只读探测，不做任何 Set 调用。"""
    rc, out, err = _run_cmd(_ps(
        "ConvertTo-Json -Compress -InputObject "
        "@(Get-CimClass -Namespace root/wmi -ClassName Lenovo_BiosSetting "
        "-ErrorAction SilentlyContinue)"), runner=runner, timeout=timeout)
    if rc != 0:
        raise RuntimeError((err or out or "探测失败").strip()[:200])
    classes = _ps_json(out)
    if not classes:
        return {"class_found": False, "items_raw": []}
    rc, out, err = _run_cmd(_ps(
        "ConvertTo-Json -Compress -InputObject "
        "@(Get-CimInstance -Namespace root/wmi -ClassName Lenovo_BiosSetting "
        "-ErrorAction SilentlyContinue | ForEach-Object { $_.CurrentSetting })"),
        runner=runner, timeout=timeout)
    if rc != 0:
        return {"class_found": True, "items_raw": [],
                "error": (err or out or "读取失败").strip()[:200]}
    data = _ps_json(out)
    if data is None:
        items = []
    elif isinstance(data, list):
        items = data
    else:
        items = [data]
    return {"class_found": True, "items_raw": items}


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
            "reason": "", "items": [], "rtc": {}}
    try:
        if probe_bios:
            probe = probe_lenovo_bios(runner=runner)
            bios["wmi_class_found"] = probe["class_found"]
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
        bios["reason"] = "检测到企业线 BIOS 配置接口，可读取自动开机项"
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
    """打开菜单自动上报（12h 节流）；后台线程，失败仅记日志不干扰 UI。"""
    if not _should_auto_report():
        return False

    def _job():
        try:
            PlatformReporter().report_snapshot(snapshot)
            _mark_reported()
            log("快照已自动上报平台")
        except Exception as e:
            log("自动上报失败: %s" % e, "WARN")

    threading.Thread(target=_job, daemon=True).start()
    return True


# ======================================================================
# 桥接处理器（bridge.py ROUTES 直连）
# ======================================================================
def handle_pc_snapshot(params=None):
    """采集本机电源策略快照（只读）。触发 12h 节流的后台自动上报。"""
    try:
        snapshot = collect_snapshot()
        try:
            _auto_report_async(snapshot)
        except Exception as e:
            log("自动上报调度失败: %s" % e, "WARN")
        return {"success": True, "snapshot": snapshot}
    except Exception as e:
        log("快照采集失败: %s" % e, "ERROR")
        return {"success": False, "error": str(e)}


def handle_pc_report(params=None):
    """手动上报：采集并立即推送平台（同步）。"""
    try:
        snapshot = collect_snapshot()
    except Exception as e:
        log("上报前采集失败: %s" % e, "ERROR")
        return {"success": False, "error": "采集失败: %s" % e}
    try:
        resp = PlatformReporter().report_snapshot(snapshot)
        _mark_reported()
        log("快照已手动上报平台: %s" % json.dumps(resp, ensure_ascii=False)[:200])
        return {"success": True, "reported": True, "server": resp}
    except RuntimeError as e:
        if str(e) == "not_registered":
            return {"success": False, "error": "尚未接入中心平台，请先在主页完成平台接入"}
        return {"success": False, "error": str(e)}
    except Exception as e:
        log("手动上报失败: %s" % e, "WARN")
        return {"success": False, "error": "上报失败（平台不可达或未接入）"}
