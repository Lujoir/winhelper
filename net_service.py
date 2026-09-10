# -*- coding: utf-8 -*-
"""
net_service.py — 网络排障服务层（框架无关）
================================================
观枢终端平台｜EyeTerm · net-doctor 子系统服务端引擎。

五个功能引擎：
  1. 配置核查   ipconfig /all 权威解析 → DHCP / DNS(基线比对) / 网关 逐网卡核查（离线可用）
  2. IP 冲突    活动网卡 IP+MAC → 平台 ipconflict 端点 → conflict_suspect 时自动 AI 分析
  3. 连通性测试 配置化节点表逐节点 ping / nslookup / w32tm(stripchart)，JSONL 落盘记录
  4. 路由追踪   tracert 逐跳解析 + 平台 route-nodes 知识库 CIDR 区域标注
  5. 网络压测   多档包长 ping 中心 + 平台 iperf-server + 本地捆绑 iperf3.exe → 综合总结 + HTML 报告

后台任务：disk_cleanup._start_task 同款模式（立即返回 task_id + 前端轮询 + Event 取消）。

铁律（ADR-007）：
  - GUI 无控制台程序一切子进程 CREATE_NO_WINDOW
  - 子进程输出三级兜底解码（utf-8-sig/utf-8/gbk），PowerShell 强制 UTF8 输出
  - 路径禁硬编码盘符（LOCALAPPDATA → TEMP 逐级 fallback）
  - 不 import uplink/home_service（ADR-002，复制语义自包含）
  - token 仅进请求头，异常/日志/回显永不携带
依赖：纯标准库（不新增第三方包）。
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from urllib.parse import urlsplit

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ============================================================
# 通用工具
# ============================================================


def _decode_output(b):
    """子进程输出三级兜底解码：utf-8-sig → utf-8 → gbk → replace。
    PowerShell 已强制 UTF8 输出；cmd 工具（ping/tracert/nslookup/w32tm）中文系统
    为 GBK/OEM，故必须多编码兜底。"""
    if b is None:
        return ""
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return b.decode(enc)
        except Exception:
            continue
    return b.decode("utf-8", errors="replace")


def _run(cmd, timeout=30):
    """执行子进程（隐藏窗口），返回 (returncode, stdout+stderr 文本)。"""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=_NO_WINDOW)
        return r.returncode, _decode_output(r.stdout) + _decode_output(r.stderr)
    except subprocess.TimeoutExpired:
        return -2, "timeout"
    except FileNotFoundError:
        return -3, "tool_not_found: %s" % (cmd[0] if cmd else "?")
    except Exception as e:
        return -4, str(e)[:160]


def _run_ps(script, timeout=30):
    """PowerShell 执行（强制 UTF8 输出 + NoProfile 防 Profil 拖慢/污染）。"""
    full = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " + script
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", full],
                timeout=timeout)


# ============================================================
# 数据目录 / 本地配置（app_config.json · uplink_config.json）
# ============================================================


def _data_dir():
    """数据目录：NETDOCTOR_CONFIG_DIR 环境变量优先（E2E/冒烟隔离），
    默认 %LOCALAPPDATA%/winhelper（与 uplink/perf 配置同目录）。禁硬编码盘符。"""
    base = os.environ.get("NETDOCTOR_CONFIG_DIR") or os.environ.get("LOCALAPPDATA") \
        or os.environ.get("TEMP") or os.environ.get("SystemDrive", "C:") + os.sep
    d = os.path.join(base, "" if os.environ.get("NETDOCTOR_CONFIG_DIR") else "winhelper")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = os.environ.get("TEMP") or os.getcwd()
    return d


def _records_dir():
    d = os.path.join(_data_dir(), "netdoctor_records")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = _data_dir()
    return d


# ---- 连通性节点默认表（需求规格固定值；生产可经 app_config.json 覆盖/清空） ----

DEFAULT_NODES = [
    {"key": "gateway",    "name": "终端区：本终端网关",             "method": "ping",     "target": ""},
    {"key": "core",       "name": "核心交换机",                     "method": "ping",     "target": "172.17.254.1"},
    {"key": "datacenter", "name": "数据中心区：数据中心汇聚交换机", "method": "ping",     "target": "172.17.254.2"},
    {"key": "dmz",        "name": "DMZ区：DMZ汇聚交换机",           "method": "ping",     "target": "172.17.254.9"},
    {"key": "dns",        "name": "内网DNS",                        "method": "nslookup", "target": "172.17.1.109", "probe": "baidu.com"},
    {"key": "ntp",        "name": "温州总院",                       "method": "ntp",      "target": "ntp.eye.ac.cn"},
    {"key": "internet",   "name": "互联网",                         "method": "ping",     "target": "baidu.com"},
    {"key": "center",     "name": "中心服务器",                     "method": "ping",     "target": ""},
]

_DEFAULT_STRESS_SIZES = [64, 256, 1024, 4096]


def _load_app_config():
    """读取 app_config.json 的 netdoctor 段（缺键回退默认值，读端容错：
    perf_service 的保存为整文件覆盖式，netdoctor.* 键可能被剥离——见 ADR-004）。"""
    cfg = {"expected_dns": [], "nodes": DEFAULT_NODES, "ai_personal": {}}
    path = os.path.join(_data_dir(), "app_config.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        nd = saved.get("netdoctor") if isinstance(saved, dict) else None
        if isinstance(nd, dict):
            dns = nd.get("expected_dns")
            if isinstance(dns, list):
                cfg["expected_dns"] = [str(x).strip() for x in dns if str(x).strip()]
            nodes = nd.get("nodes")
            if isinstance(nodes, list) and nodes:
                clean = []
                for n in nodes:
                    if isinstance(n, dict) and (n.get("key") or n.get("name")):
                        clean.append({
                            "key": str(n.get("key") or uuid.uuid4().hex[:8]),
                            "name": str(n.get("name") or n.get("key")),
                            "method": str(n.get("method") or "ping").lower(),
                            "target": str(n.get("target") or ""),
                            "probe": str(n.get("probe") or "") or None,
                        })
                if clean:
                    cfg["nodes"] = clean
            ap = nd.get("ai_personal")
            if isinstance(ap, dict):
                cfg["ai_personal"] = {
                    "api_url": str(ap.get("api_url") or "").strip(),
                    "api_key": str(ap.get("api_key") or "").strip(),
                    "model": str(ap.get("model") or "").strip(),
                }
    except Exception:
        pass
    return cfg


# ---- uplink 配置（与 uplink.py 共读同一文件；token 永不回显） ----


def _uplink_config():
    cfg = {"enabled": False, "server_url": "", "token": "", "terminal_id": ""}
    path = os.path.join(_data_dir(), "uplink_config.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            cfg.update({k: saved.get(k, cfg[k]) for k in cfg})
    except Exception:
        pass
    return cfg


def _terminal_id(cfg=None):
    cfg = cfg or _uplink_config()
    tid = (cfg.get("terminal_id") or "").strip()
    if tid:
        return tid
    try:
        host = socket.gethostname() or os.environ.get("COMPUTERNAME") or "UNKNOWN"
    except Exception:
        host = os.environ.get("COMPUTERNAME") or "UNKNOWN"
    return "WIN-" + re.sub(r"[^A-Za-z0-9_.\-]", "_", host).strip("_")[:40]


def _center_host():
    """中心服务器 IP（uplink server_url 的 host；未配置返回 None）。"""
    url = (_uplink_config().get("server_url") or "").strip()
    if not url:
        return None
    try:
        return urlsplit(url).hostname
    except Exception:
        return None


# ============================================================
# 平台 HTTP（X-ETP-Token；纯标准库 urllib；异常不带 token）
# ============================================================


def _platform_get(path, timeout=12):
    cfg = _uplink_config()
    server = (cfg.get("server_url") or "").rstrip("/")
    if not server or not cfg.get("token"):
        return -1, {"error": "uplink_not_configured"}
    url = server + path
    req = urllib.request.Request(url, method="GET")
    req.add_header("X-ETP-Token", cfg.get("token") or "")
    return _platform_req(req, timeout)


def _platform_post(path, payload, timeout=15):
    cfg = _uplink_config()
    server = (cfg.get("server_url") or "").rstrip("/")
    if not server or not cfg.get("token"):
        return -1, {"error": "uplink_not_configured"}
    url = server + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-ETP-Token", cfg.get("token") or "")
    return _platform_req(req, timeout)


def _platform_req(req, timeout):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            try:
                return resp.status, json.loads(body.decode("utf-8"))
            except Exception:
                return resp.status, {}
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {}
    except Exception as e:
        return -1, {"error": str(e) or type(e).__name__}


def uplink_configured():
    cfg = _uplink_config()
    return bool(cfg.get("enabled") and cfg.get("server_url") and cfg.get("token"))


# ============================================================
# 网络探测原语（ping / 网关 / nslookup / w32tm —— 全部语言无关解析）
# ============================================================


def _default_gateway():
    """route print -4 解析默认网关（0.0.0.0/0.0.0.0 活动路由，metric 最小优先）。
    语言无关（不解析表头）。语义复制自 uplink._default_gateway（ADR-002）。"""
    rc, out = _run(["route", "print", "-4"], timeout=10)
    best = None
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) < 5 or parts[0] != "0.0.0.0" or parts[1] != "0.0.0.0":
            continue
        gw, iface, metric = parts[2], parts[3], parts[4]
        if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", gw) \
                or not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", iface):
            continue  # 接口列不是 IP → 持久路由段行
        try:
            m = int(metric)
        except ValueError:
            continue
        if best is None or m < best[1]:
            best = (gw, m)
    return best[0] if best else None


_RE_LATENCY = re.compile(r"(?:time|时间|平均)\s*[=<]\s*(\d+)\s*ms", re.IGNORECASE)


def _ping_once(host, timeout_ms=2000):
    rc, out = _run(["ping", "-n", "1", "-w", str(timeout_ms), host], timeout=timeout_ms / 1000.0 + 3)
    m = _RE_LATENCY.search(out)
    if m:
        return int(m.group(1)), out
    return None, out


def _ping_summary(host, count=4, timeout_ms=2000):
    """ping -n <count>：解析丢包率与最短/最长/平均延迟（中英文输出均兼容）。
    返回 {ok, loss_pct, avg_ms, max_ms, min_ms, replies, error}。"""
    rc, out = _run(["ping", "-n", str(count), "-w", str(timeout_ms), host],
                   timeout=count * (timeout_ms / 1000.0) + 6)
    res = {"ok": False, "loss_pct": 100.0, "avg_ms": None, "max_ms": None,
           "min_ms": None, "replies": [], "error": None}

    # 逐回复延迟（time=1ms / 时间<1ms）
    for m in re.finditer(r"(?:time|时间)\s*[=<]\s*(\d+)\s*ms", out, re.IGNORECASE):
        res["replies"].append(int(m.group(1)))

    # 统计行：已发送 = 4，已接收 = 4，丢失 = 0 (0% 丢失) | Sent = 4, Received = 4, Lost = 0 (0% loss)
    m_stat = re.search(
        r"(?:已发送|Sent)\s*=\s*(\d+).{0,40}?(?:已接收|Received)\s*=\s*(\d+).{0,40}?(?:丢失|Lost)\s*=\s*(\d+)\s*[（(]\s*(\d+(?:\.\d+)?)\s*%",
        out, re.IGNORECASE | re.DOTALL)
    if m_stat:
        sent, recv, lost, loss = int(m_stat.group(1)), int(m_stat.group(2)), int(m_stat.group(3)), float(m_stat.group(4))
        res["loss_pct"] = round(loss, 1)
        res["ok"] = recv > 0
    else:
        res["ok"] = len(res["replies"]) > 0
        if res["ok"]:
            res["loss_pct"] = round(100.0 * (count - len(res["replies"])) / float(count), 1)

    # 摘要行：最短 = 0ms，最长 = 1ms，平均 = 0ms | Minimum = 0ms, Maximum = 1ms, Average = 0ms
    m_avg = re.search(
        r"(?:最短|Minimum)\s*=\s*(\d+)\s*ms\s*[,，]\s*(?:最长|Maximum)\s*=\s*(\d+)\s*ms\s*[,，]\s*(?:平均|Average)\s*=\s*(\d+)\s*ms",
        out, re.IGNORECASE)
    replies = res["replies"]
    if replies:
        res["min_ms"] = min(replies)
        res["max_ms"] = max(replies)
        res["avg_ms"] = round(sum(replies) / float(len(replies)), 1)
    if m_avg:
        res["min_ms"] = int(m_avg.group(1))
        res["max_ms"] = int(m_avg.group(2))
        res["avg_ms"] = int(m_avg.group(3))
    if not res["ok"]:
        res["error"] = "timeout_or_unreachable"
    return res


_RE_IP = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
# IPv6 token：至少含一个冒号的十六进制段组，容许 %zone 后缀（fe80::1%12）
_RE_IPV6 = re.compile(r"\b([0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{0,4}){2,7}%?\d*)\b")


def _nslookup_probe(server, domain):
    """nslookup <domain> <server>：验证指定 DNS 服务器的解析能力。
    ok = 拿到至少一个非服务器地址的应答。"""
    rc, out = _run(["nslookup", domain, server], timeout=12)
    res = {"ok": False, "answers": [], "server_addr": None, "error": None}
    addrs = _RE_IP.findall(out)
    low = out.lower()
    timed_out = ("timed out" in low) or ("请求超时" in out) or ("default servers are not available" in low)
    # 第一个 Address 通常是 DNS 服务器自身；其后为应答
    if addrs:
        res["server_addr"] = addrs[0]
        answers = [a for a in addrs[1:]]
        # 部分输出服务器地址会重复出现在名称段，去掉与 server 相同者
        answers = [a for a in answers if a != addrs[0]]
        res["answers"] = answers
        res["ok"] = bool(answers) and not timed_out
    if not res["ok"]:
        res["error"] = "dns_timeout" if timed_out else ("dns_nxdomain_or_fail" if rc == 0 else "nslookup_failed")
    return res


# w32tm /stripchart 样本行两种实测格式：
#   中文系统： 10:37:19, +01.0620337s   （HH:mm:ss, ±SS.sssssss，秒可两位数；error 行同头但无样本）
#   英文系统： 17:00:01 d:+00.0012s o:-00.0123s  [*  |]
_NTP_OFFSET_WARN_S = 0.5        # |offset| 均值阈值（秒）：>0.5s 判「偏差过大 · 需校时」
_RE_STRIP_COMMA = re.compile(r"^\s*\d{1,2}:\d{2}:\d{2},\s*([+-]\d+(?:\.\d+)?)s", re.MULTILINE)
_RE_STRIP_O = re.compile(r"o:\s*([+-]?\d+(?:\.\d+)?)s")
_RE_STRIP_ERR = re.compile(r"error\s*:\s*0x[0-9A-Fa-f]+")


def _parse_stripchart(out):
    """w32tm 输出 → (offset 样本秒值列表, error 行数)。中英文头/空行/error 行容错跳过。
    优先匹配逗号格式（中文系统实测），无样本时兜底 o: 格式。"""
    vals = [float(m.group(1)) for m in _RE_STRIP_COMMA.finditer(out)]
    if not vals:
        vals = [float(m.group(1)) for m in _RE_STRIP_O.finditer(out)]
    errs = len(_RE_STRIP_ERR.findall(out))
    return vals, errs


def _ntp_result(vals, errs, out=""):
    """样本值（秒）→ 三态判定结果（纯函数，可单测）。
    |均值|≤0.5s → ok（正常）；>0.5s → warn（偏差过大 · 需校时）；无样本 → err+错误码。"""
    res = {"ok": False, "status": "err", "offset_ms": None, "max_abs_ms": None,
           "samples": len(vals), "err_samples": errs, "detail": "", "error": None}
    if vals:
        avg = sum(vals) / len(vals)
        max_abs = max(abs(v) for v in vals)
        res["ok"] = True
        res["offset_ms"] = round(avg * 1000.0, 1)
        res["max_abs_ms"] = round(max_abs * 1000.0, 1)
        if abs(avg) > _NTP_OFFSET_WARN_S:
            res["status"] = "warn"
            res["detail"] = "偏差 %+.1fms · 需校时（%d 样本）" % (res["offset_ms"], len(vals))
        else:
            res["status"] = "ok"
            res["detail"] = "偏移 %+.1fms（%d 样本）" % (res["offset_ms"], len(vals))
        return res
    low = (out or "").lower()
    if "timeout" in low or "无法访问" in out or "超时" in out or errs:
        res["error"] = "ntp_timeout"
    else:
        res["error"] = "ntp_no_samples"
    res["detail"] = res["error"]
    return res


def _ntp_probe(host, samples=5):
    """w32tm /stripchart /computer:<host> /dataonly /samples:<n>：真实执行 + 判定。"""
    rc, out = _run(["w32tm", "/stripchart", "/computer:" + host, "/dataonly",
                    "/samples:" + str(samples)], timeout=samples * 5 + 20)
    vals, errs = _parse_stripchart(out)
    return _ntp_result(vals, errs, out)


# ============================================================
# 功能一：配置核查（ipconfig /all 权威解析，离线可用）
# ============================================================

_ADAPTER_HEADER = re.compile(r"^(\S.*)适配器\s+(.+?):\s*$|^(Ethernet|Wireless LAN|PPP|Tunnel|Loopback) adapter\s+(.+?):\s*$", re.IGNORECASE)
_RE_FIELD_IPS = _RE_IP


def _field_value(line):
    """提取字段值：'描述. . . . : Intel(R)...' → 'Intel(R)...'（冒号后内容）"""
    idx = line.find(":")
    if idx < 0:
        return ""
    return line[idx + 1:].strip()


def _collect_adapters():
    """ipconfig /all 解析 → 网卡列表。解析失败的行容错跳过。
    返回 [{name, desc, mac, dhcp, dhcp_server, ipv4[], gateway[], dns[], media_down}]"""
    rc, out = _run_ps("ipconfig /all", timeout=25)
    if rc != 0 or not out.strip():
        return None, "ipconfig_failed_rc%s" % rc
    adapters = []
    cur = None

    def _flush():
        if cur and (cur["ipv4"] or cur["desc"]):
            adapters.append(cur)

    for raw in out.splitlines():
        line = raw.rstrip()
        m = _ADAPTER_HEADER.match(line.strip()) if line and not line[0].isspace() else None
        if m:
            _flush()
            # 中式：组1=类型 组2=名称；英式：组3=类型 组4=名称
            title = (m.group(2) or m.group(4) or "").strip()
            cur = {"name": title, "desc": "", "mac": "", "dhcp": None, "dhcp_server": "",
                   "ipv4": [], "subnet": [], "ipv6": [], "gateway": [], "dns": [],
                   "lease_obtained": "", "lease_expires": "", "wins": [],
                   "media_down": False,
                   "kind": (m.group(1) or m.group(3) or "").strip()}
            continue
        if cur is None:
            continue
        if not line.strip():
            continue
        low = line.lower()
        if ("媒体状态" in line or "media state" in low) and ("媒体已断开" in line or "media disconnected" in low):
            cur["media_down"] = True
        elif "描述" in line or "description" in low:
            v = _field_value(line)
            if v:
                cur["desc"] = v
        elif "物理地址" in line or "physical address" in low:
            v = _field_value(line)
            if v and v.upper() != "FF-FF-FF-FF-FF-FF":
                cur["mac"] = v.upper().replace("-", ":")
        elif "dhcp 已启用" in line or "dhcp enabled" in low:
            v = _field_value(line)
            cur["dhcp"] = v in ("是", "Yes", "yes")
        elif "dhcp 服务器" in line or "dhcp server" in low:
            v = _field_value(line)
            if v:
                cur["dhcp_server"] = v
        elif "默认网关" in line or "default gateway" in low:
            v = _field_value(line)
            if v:
                cur["gateway"].extend(_RE_IP.findall(v))
        elif "dns 服务器" in line or "dns servers" in low:
            v = _field_value(line)
            if v:
                cur["dns"].extend(_RE_IP.findall(v))
        elif "ipv4 地址" in line or ("ipv4 address" in low and "(" not in line.split(":")[0]):
            v = _field_value(line)
            found = _RE_IP.findall(v)
            if found:
                cur["ipv4"].extend(found)
        elif "子网掩码" in line or "subnet mask" in low:
            v = _field_value(line)
            if v:
                cur["subnet"].extend(_RE_IP.findall(v))
        elif "ipv6" in low:
            v = _field_value(line)
            found = _RE_IPV6.findall(v)
            if found:
                cur["ipv6"].extend(found)
        elif "获得租约" in line or "lease obtained" in low:
            v = _field_value(line)
            if v:
                cur["lease_obtained"] = v
        elif "租约过期" in line or "lease expires" in low:
            v = _field_value(line)
            if v:
                cur["lease_expires"] = v
        elif "wins" in low:
            v = _field_value(line)
            if v:
                cur["wins"].extend(_RE_IP.findall(v))
        elif line[0].isspace() and line.strip() and (cur["dns"] or cur["gateway"]):
            # DNS/网关多行续行（缩进的裸 IP 行）
            v = line.strip()
            found = _RE_IP.findall(v)
            if found and "." in v and "ms" not in v and len(v) <= 60:
                if cur["dns"]:
                    cur["dns"].extend(found)
                elif cur["gateway"]:
                    cur["gateway"].extend(found)
    _flush()
    return adapters, None


def _fetch_link_speeds():
    """链路速率映射 {网卡名小写: "1 Gbps" 格式化字符串}。
    优先复用 home_service 现成采集（同进程 + 60s 缓存，不重复起 PowerShell，
    团队指令 2026-09-09）；独立运行环境（net-doctor 冒烟等无 home_service 场景）
    回退轻量 Get-NetAdapter 查询（CREATE_NO_WINDOW + UTF8 铁律）。"""
    try:
        from home_service import handle_home_network
        r = handle_home_network()
        if r.get("success") and r.get("adapters"):
            return {str(a.get("name") or "").strip().lower(): (a.get("speed") or "")
                    for a in r["adapters"]}
    except Exception:
        pass
    speeds = {}
    rc, out = _run_ps("Get-NetAdapter | ForEach-Object { $_.Name + '|' + [string]$_.LinkSpeed }",
                      timeout=15)
    if rc == 0:
        for ln in out.splitlines():
            if "|" not in ln:
                continue
            nm, _, sp = ln.partition("|")
            if nm.strip() and sp.strip() and sp.strip() != "--":
                speeds[nm.strip().lower()] = sp.strip()
    return speeds


def _dns_verdict(dns_list, expected):
    """DNS 基线比对。expected 为空 → 仅提示未配置基线（unknown）。"""
    if not expected:
        return {"status": "unknown", "reason": "未配置基线（app_config.netdoctor.expected_dns 为空），仅展示实测值"}
    got = [d for d in dns_list if d]
    exp = [str(d).strip() for d in expected if str(d).strip()]
    if not got:
        return {"status": "err", "reason": "未配置任何 DNS 服务器，域名将无法解析"}
    missing = [d for d in exp if d not in got]
    extra = [d for d in got if d not in exp]
    if not missing and not extra:
        return {"status": "ok", "reason": "与基线一致"}
    if missing and extra:
        return {"status": "warn", "reason": "与基线不一致：缺少 %s，多出 %s" % (",".join(missing), ",".join(extra))}
    if missing:
        return {"status": "warn", "reason": "缺少基线 DNS：%s" % ",".join(missing)}
    return {"status": "warn", "reason": "多出非基线 DNS：%s" % ",".join(extra)}


def run_config_check_result():
    """配置核查引擎（同步执行，供后台任务调用）。"""
    adapters, err = _collect_adapters()
    if adapters is None:
        return {"success": False, "error": err or "ipconfig 解析失败"}
    speeds = _fetch_link_speeds()   # 按网卡名合并链路速率（无数据 → None）
    for a in adapters:
        a["speed"] = speeds.get((a["name"] or "").strip().lower()) or None
    out_adapters = []
    for a in adapters:
        is_tunnel = (a.get("kind") or "").lower() in ("tunnel", "隧道")
        active = bool(a["ipv4"]) and not a["media_down"]
        checks = []
        if a["media_down"]:
            checks.append({"item": "链路状态", "status": "muted", "reason": "媒体已断开（未连接）"})
        else:
            # DHCP 核查
            if a["dhcp"] is True:
                checks.append({"item": "DHCP", "status": "ok", "reason": "自动获取" +
                               (("，服务器 " + a["dhcp_server"]) if a["dhcp_server"] else "")})
            elif a["dhcp"] is False:
                checks.append({"item": "DHCP", "status": "warn",
                               "reason": "手动静态配置——如非运维指派地址，建议改回自动获取"})
            else:
                checks.append({"item": "DHCP", "status": "unknown", "reason": "未能解析 DHCP 状态"})
            # DNS 核查
            dv = _dns_verdict(a["dns"], _load_app_config()["expected_dns"])
            checks.append({"item": "DNS", "status": dv["status"], "reason": dv["reason"],
                           "servers": a["dns"]})
            # 网关核查
            if a["gateway"]:
                checks.append({"item": "网关", "status": "ok", "reason": "，".join(a["gateway"])})
            elif a["ipv4"]:
                checks.append({"item": "网关", "status": "warn", "reason": "无默认网关（纯二层/隔离网段或链路异常）"})
            else:
                checks.append({"item": "网关", "status": "muted", "reason": "未配置 IPv4"})
        out_adapters.append({
            "name": a["name"], "desc": a["desc"], "mac": a["mac"], "kind": a["kind"],
            "speed": a.get("speed"),
            "ipv4": a["ipv4"], "subnet": a["subnet"], "ipv6": a["ipv6"],
            "gateway": a["gateway"], "dns": a["dns"],
            "dhcp": a["dhcp"], "dhcp_server": a["dhcp_server"],
            "lease_obtained": a["lease_obtained"], "lease_expires": a["lease_expires"],
            "wins": a["wins"],
            "active": active, "tunnel": is_tunnel,
            "media_down": a["media_down"], "checks": checks,
        })
    # 总体结论：只统计非隧道、有 IPv4 的活动网卡
    actives = [x for x in out_adapters if x["active"] and not x["tunnel"]]
    bad = [x for x in actives if any(c["status"] == "err" for c in x["checks"])]
    warn = [x for x in actives if any(c["status"] == "warn" for c in x["checks"])]
    if bad:
        overall = {"status": "err", "text": "发现异常配置（%d 张网卡）" % len(bad)}
    elif warn:
        overall = {"status": "warn", "text": "存在需关注项（%d 张网卡）" % len(warn)}
    elif actives:
        overall = {"status": "ok", "text": "全部核查项正常"}
    else:
        overall = {"status": "warn", "text": "未发现活动网卡（无 IPv4）"}
    return {"success": True, "adapters": out_adapters, "overall": overall,
            "ts": int(time.time())}


# ============================================================
# 功能二：IP 冲突检测（需已连接中心）
# ============================================================


def run_ipconflict_result(cancel=None):
    """IP 冲突引擎：活动网卡 IP+MAC → 平台 ipconflict → 疑似时自动 AI 分析。"""
    adapters, err = _collect_adapters()
    candidates = []
    if adapters:
        for a in adapters:
            if a["ipv4"] and a["mac"] and not a["media_down"] and \
                    (a.get("kind") or "").lower() not in ("tunnel", "隧道"):
                candidates.append(a)
    if not candidates:
        return {"success": False, "error": err or "未找到带 IPv4+MAC 的活动网卡"}
    a = candidates[0]
    ip, mac = a["ipv4"][0], a["mac"]
    tid = _terminal_id()
    result = {"success": True, "ip": ip, "mac": mac, "adapter": a["name"],
              "connected": uplink_configured(), "verdict": None, "ai": None, "error": None}
    if not result["connected"]:
        result["error"] = "not_connected"
        return result
    code, resp = _platform_post("/api/v1/terminals/%s/netdoctor/ipconflict" % tid,
                                {"ip": ip, "mac": mac})
    if not (200 <= code < 300):
        result["error"] = "platform_http_%s: %s" % (code, (resp or {}).get("error", ""))
        return result
    result["verdict"] = resp.get("verdict") or resp
    if isinstance(result["verdict"], dict) and result["verdict"].get("conflict_suspect"):
        evidence = result["verdict"].get("evidence") or []
        issue = ("IP冲突疑似：终端 %s 网卡 %s IP=%s MAC=%s；平台证据：%s"
                 % (tid, a["name"], ip, mac, "; ".join(str(e)[:120] for e in evidence[:6]) or "无明细"))
        ai_code, ai_resp = _platform_post("/api/v1/ai/analyze",
                                          {"terminal_id": tid, "issue_description": issue},
                                          timeout=45)
        if 200 <= ai_code < 300:
            result["ai"] = {"ok": True,
                            "analysis": (ai_resp.get("analysis") or ai_resp.get("content")
                                         or ai_resp.get("result") or ""),
                            "model": ai_resp.get("model")}
        else:
            result["ai"] = {"ok": False, "error": "ai_http_%s: %s" % (ai_code, (ai_resp or {}).get("error", ""))}
    return result


# ============================================================
# 功能三：网络连通性测试（配置化节点 + JSONL 记录）
# ============================================================


def _record_jsonl(rec):
    """追加一条连通性记录到 netdoctor_records/ping_YYYYMMDD.jsonl。失败静默。"""
    try:
        day = datetime.now().strftime("%Y%m%d")
        path = os.path.join(_records_dir(), "ping_%s.jsonl" % day)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _resolve_node_target(node):
    """target 为空时动态解析：gateway=本地默认网关；center=uplink server host。
    返回 (target_ip, note)。"""
    t = (node.get("target") or "").strip()
    if t:
        return t, None
    if node.get("key") == "gateway":
        gw = _default_gateway()
        if gw:
            return gw, None
        return None, "gateway_not_found"
    if node.get("key") == "center":
        host = _center_host()
        if host:
            return host, None
        return None, "center_not_connected"
    return None, "target_not_configured"


def run_ping_suite(task, params):
    """连通性全量检测：逐节点探测 + JSONL 落盘 + 进度更新（可取消）。"""
    nodes = _load_app_config()["nodes"]
    center_connected = uplink_configured()
    results = []
    total = len(nodes)
    for i, node in enumerate(nodes):
        if task and task["_cancel"].is_set():
            break
        key = node.get("key") or ""
        method = node.get("method") or "ping"
        name = node.get("name") or key
        target, note = _resolve_node_target(node)
        entry = {"key": key, "name": name, "method": method,
                 "target": target or (node.get("target") or "--"),
                 "ok": False, "status": "err", "detail": "",
                 "loss_pct": None, "avg_ms": None, "max_ms": None}
        if note == "center_not_connected":
            entry.update({"status": "muted", "detail": "未连接中心"})
            results.append(entry)
            _set_progress(task, done=i + 1, total=total, current=name, results=results)
            continue
        if not target:
            entry["detail"] = note or "目标未配置"
            results.append(entry)
            _set_progress(task, done=i + 1, total=total, current=name, results=results)
            continue
        if method == "nslookup":
            probe_domain = node.get("probe") or "baidu.com"
            r = _nslookup_probe(target, probe_domain)
            entry["detail"] = ("解析 %s → %s" % (probe_domain, ", ".join(r["answers"][:3]))
                               if r["ok"] else (r.get("error") or "解析失败"))
            entry["ok"] = r["ok"]
            entry["status"] = "ok" if r["ok"] else "err"
            jsonl = {"ts": int(time.time()), "key": key, "target": target, "ok": r["ok"],
                     "warn": False,
                     "loss_pct": 0.0 if r["ok"] else 100.0,
                     "avg_ms": None, "max_ms": None}
        elif method == "ntp":
            r = _ntp_probe(target)
            entry["detail"] = r.get("detail") or (r.get("error") or "NTP 探测失败")
            # ok 语义 = 探测通道成功（拿到 ≥1 有效样本）；偏差超阈值为 warn 质量告警，
            # 不改 ok（2026-09-09 用户实测语义缺陷修复：需关注 ≠ 失败）
            entry["ok"] = r["ok"]
            entry["status"] = r["status"]          # ok | warn(偏差过大·需校时) | err
            entry["warn"] = r["status"] == "warn"
            jsonl = {"ts": int(time.time()), "key": key, "target": target, "ok": r["ok"],
                     "warn": (r["status"] == "warn"),
                     "loss_pct": 0.0 if r["ok"] else 100.0,
                     "avg_ms": (abs(r["offset_ms"]) if r["offset_ms"] is not None else None),
                     "max_ms": r["max_abs_ms"]}
        else:
            r = _ping_summary(target, count=4)
            entry["ok"] = r["ok"]
            entry["loss_pct"] = r["loss_pct"]
            entry["avg_ms"] = r["avg_ms"]
            entry["max_ms"] = r["max_ms"]
            if r["ok"]:
                entry["status"] = "ok"
                entry["detail"] = "平均 %sms / 丢包 %s%%" % (r["avg_ms"], r["loss_pct"])
            else:
                entry["status"] = "err"
                entry["detail"] = "超时或不可达"
            jsonl = {"ts": int(time.time()), "key": key, "target": target, "ok": r["ok"],
                     "warn": False,
                     "loss_pct": r["loss_pct"], "avg_ms": r["avg_ms"], "max_ms": r["max_ms"]}
        _record_jsonl(jsonl)
        results.append(entry)
        _set_progress(task, done=i + 1, total=total, current=name, results=results)
    ok_cnt = sum(1 for x in results if x["ok"])
    muted = sum(1 for x in results if x["status"] == "muted")
    summary = {"total": len(results), "ok": ok_cnt, "err": len(results) - ok_cnt - muted,
               "muted": muted}
    return {"success": True, "results": results, "summary": summary,
            "center_connected": center_connected, "ts": int(time.time())}


def ping_history(limit=200, hours=None, start_ts=None, end_ts=None):
    """最近连通性记录摘要：读取近 7 天 JSONL，返回最近 limit 条 + 按 key 聚合。
    2026-09-10：AI 诊断时间范围过滤——hours（小时数）或 start_ts/end_ts（epoch 秒）。"""
    recs = []
    d = _records_dir()
    try:
        files = sorted([f for f in os.listdir(d) if re.match(r"ping_\d{8}\.jsonl$", f)],
                       reverse=True)[:7]
        for fn in files:
            try:
                with open(os.path.join(d, fn), "r", encoding="utf-8") as f:
                    for ln in f:
                        ln = ln.strip()
                        if not ln:
                            continue
                        try:
                            recs.append(json.loads(ln))
                        except Exception:
                            continue
            except Exception:
                continue
    except Exception:
        pass
    recs.sort(key=lambda x: x.get("ts") or 0)
    if hours is not None or start_ts is not None or end_ts is not None:
        lo = start_ts
        if lo is None and hours is not None:
            lo = time.time() - hours * 3600.0
        hi = end_ts
        recs = [r for r in recs
                if (lo is None or (r.get("ts") or 0) >= lo)
                and (hi is None or (r.get("ts") or 0) <= hi)]
    agg = {}
    for r in recs[-limit:]:
        k = r.get("key") or "?"
        a = agg.setdefault(k, {"key": k, "n": 0, "ok_n": 0, "warn_n": 0,
                               "avg_ms_sum": 0.0, "avg_ms_n": 0, "max_ms": None})
        a["n"] += 1
        if r.get("ok"):
            a["ok_n"] += 1
        if r.get("warn"):
            a["warn_n"] += 1
        if isinstance(r.get("avg_ms"), (int, float)):
            a["avg_ms_sum"] += r["avg_ms"]
            a["avg_ms_n"] += 1
        if isinstance(r.get("max_ms"), (int, float)):
            a["max_ms"] = r["max_ms"] if a["max_ms"] is None else max(a["max_ms"], r["max_ms"])
    for a in agg.values():
        a["ok_rate"] = round(100.0 * a["ok_n"] / a["n"], 1) if a["n"] else None
        a["avg_ms"] = round(a["avg_ms_sum"] / a["avg_ms_n"], 1) if a["avg_ms_n"] else None
    return {"success": True, "recent": recs[-limit:],
            "agg": sorted(agg.values(), key=lambda x: x["key"])}


# ============================================================
# 功能四：路由追踪（tracert + route-nodes 知识库标注）
# ============================================================

_TRACERT_HOP = re.compile(r"^\s*(\d{1,2})\s+(.*)$")
_RE_LAT_TOK = re.compile(r"(<\s*1\s*m?s|\d+\s*m?s|<\s*1\s*毫秒|\d+\s*毫秒|\*)", re.IGNORECASE)


def _parse_tracert(out):
    """tracert 输出 → 逐跳 [{hop, delays[], ip, host, timeout}]。解析失败行跳过。"""
    hops = []
    for raw in out.splitlines():
        m = _TRACERT_HOP.match(raw)
        if not m:
            continue
        hop_no = int(m.group(1))
        if hop_no < 1 or hop_no > 30:
            continue
        rest = m.group(2)
        delays = _RE_LAT_TOK.findall(rest)
        if not delays:
            continue  # 跟踪标题/统计行等
        tail = _RE_LAT_TOK.sub(" ", rest, count=len(delays)).strip()
        ip, host = None, None
        mh = re.match(r"(\S+)\s+\[([\d.]+)\]", tail)
        if mh:
            host, ip = mh.group(1), mh.group(2)
        else:
            mi = _RE_IP.search(tail)
            if mi:
                ip = mi.group(1)
        all_timeout = all("*" in d for d in delays)
        hops.append({"hop": hop_no,
                     "delays": [d.replace(" ", "") for d in delays[:3]],
                     "ip": ip, "host": host, "timeout": all_timeout})
    return hops


def fetch_route_nodes():
    """平台路由知识库。返回 (nodes, err)。"""
    if not uplink_configured():
        return [], "not_connected"
    tid = _terminal_id()
    code, resp = _platform_get("/api/v1/terminals/%s/netdoctor/route-nodes" % tid)
    if 200 <= code < 300 and isinstance(resp, dict):
        nodes = resp.get("nodes") or []
        return [n for n in nodes if isinstance(n, dict)], None
    return [], "http_%s" % code


def _match_zone(ip, kb_nodes):
    """按 IP 前缀/CIDR 匹配知识库区域（ipaddress 支持 CIDR）。未匹配返回 None。"""
    if not ip:
        return None
    try:
        addr = __import__("ipaddress").ip_address(ip)
    except Exception:
        return None
    for n in kb_nodes:
        match = str(n.get("match") or "").strip()
        if not match:
            continue
        try:
            net = __import__("ipaddress").ip_network(match, strict=False)
            if addr in net:
                return {"zone": n.get("zone") or "—", "desc": n.get("desc") or ""}
        except Exception:
            # 容错：知识库条目非合法 CIDR/IP 时按纯文本前缀比对
            if ip.startswith(match):
                return {"zone": n.get("zone") or "—", "desc": n.get("desc") or ""}
    return None


def run_tracert_result(task, target, max_hops=15):
    """路由追踪引擎：tracert -w 500 -h 15 <target> + 知识库区域标注。"""
    target = (target or "").strip()
    if not target or not re.match(r"^[\w.\-]+$", target):
        return {"success": False, "error": "目标须为 IP 或域名（不含路径/空格）"}
    kb_nodes, kb_err = fetch_route_nodes()
    _set_progress(task, stage="tracing", current=target, kb=len(kb_nodes))
    rc, out = _run(["tracert", "-w", "500", "-h", str(max_hops), target], timeout=120)
    hops = _parse_tracert(out)
    if not hops:
        return {"success": False, "error": "tracert 无有效输出（rc=%s）" % rc}
    for h in hops:
        m = _match_zone(h.get("ip"), kb_nodes)
        h["zone"] = m["zone"] if m else None
        h["zone_desc"] = (m["desc"] if m else "") or None
    resolved_ok = hops[-1].get("ip") is not None
    return {"success": True, "target": target, "hops": hops,
            "kb_count": len(kb_nodes), "kb_error": kb_err,
            "reached": resolved_ok, "ts": int(time.time())}


# ============================================================
# 功能五：网络压测（多档包长 ping + iperf3）+ HTML 报告
# ============================================================

# iperf3 资源解析：环境变量（NETDOCTOR_IPERF_EXE → UPLINK_IPERF_EXE 兼容）
# → 打包 _MEIPASS/libs/iperf3 → 开发态项目 libs/iperf3（语义复制自 uplink，ADR-002）


def _iperf3_env_exe():
    for var in ("NETDOCTOR_IPERF_EXE", "UPLINK_IPERF_EXE"):
        p = os.environ.get(var)
        if p and os.path.exists(p):
            return p
    return None


def _iperf3_bundle_dir():
    env = _iperf3_env_exe()
    if env:
        return os.path.dirname(os.path.abspath(env))
    base = getattr(sys, "_MEIPASS", None)
    here = os.path.dirname(os.path.abspath(__file__))
    cands = []
    if base:
        cands.append(os.path.join(base, "libs", "iperf3"))
    cands.append(os.path.join(here, "libs", "iperf3"))
    cands.append(os.path.join(here, "..", "perf-analyzer", "libs", "iperf3"))
    for d in cands:
        try:
            if os.path.exists(os.path.join(os.path.abspath(d), "iperf3.exe")):
                return os.path.abspath(d)
        except Exception:
            continue
    return None


def _iperf3_available():
    return _iperf3_bundle_dir() is not None


def _run_iperf3(server, port, duration, mode, udp_mbps):
    """本地捆绑 iperf3 执行（独立 staging，finally 必删）。返回 (summary_dict, err)。"""
    bundle = _iperf3_bundle_dir()
    if not bundle:
        return None, "iperf3_not_bundled"
    staging = tempfile.mkdtemp(prefix="winhelper_nd_iperf3_")
    try:
        for fn in os.listdir(bundle):
            s = os.path.join(bundle, fn)
            if os.path.isfile(s):
                try:
                    shutil.copy2(s, os.path.join(staging, fn))
                except Exception:
                    pass
        exe = os.path.join(staging, "iperf3.exe")
        if not os.path.exists(exe):
            return None, "iperf3_stage_copy_failed"
        cmd = [exe, "-c", server, "-p", str(port), "-t", str(duration), "-J"]
        if mode == "udp":
            cmd += ["-u", "-b", "%sM" % (udp_mbps or 100)]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=duration + 30,
                               creationflags=_NO_WINDOW)
        except subprocess.TimeoutExpired:
            return None, "iperf3_timeout"
        if r.returncode != 0:
            return None, "iperf3_exit_%s: %s" % (r.returncode,
                                                 _decode_output(r.stderr or r.stdout).strip()[:120] or "no_output")
        try:
            j = json.loads(_decode_output(r.stdout))
        except Exception:
            return None, "iperf3_json_parse_failed"
        if not isinstance(j, dict) or j.get("error"):
            return None, "iperf3_error: %s" % str((j or {}).get("error"))[:120]
        end = j.get("end") or {}
        intervals = j.get("intervals") or []
        if mode == "udp":
            s = end.get("sum") or {}
            bps, jitter, lost = s.get("bits_per_second"), s.get("jitter_ms"), s.get("lost_percent")
            if bps is None and jitter is None and lost is None:
                return None, "iperf3_udp_summary_missing"
            return {"mode": "udp",
                    "mbits_sec": round(bps / 1e6, 1) if bps is not None else None,
                    "jitter_ms": round(jitter, 3) if jitter is not None else None,
                    "lost_percent": round(lost, 2) if lost is not None else None}, None
        # TCP：intervals 逐秒实测带宽 → max/min/avg（ADR-006 口径）
        vals = []
        for it in intervals:
            s = (it or {}).get("sum") or {}
            if isinstance(s.get("bits_per_second"), (int, float)):
                vals.append(s["bits_per_second"] / 1e6)
        s_recv = end.get("sum_received") or end.get("sum_sent") or end.get("sum") or {}
        agg_bps = s_recv.get("bits_per_second")
        if not vals and agg_bps is None:
            return None, "iperf3_tcp_summary_missing"
        tcp = {"mode": "tcp", "mbits_sec": round(agg_bps / 1e6, 1) if agg_bps is not None else None,
               "retransmits": s_recv.get("retransmits")}
        if vals:
            tcp["interval_max_mbits"] = round(max(vals), 1)
            tcp["interval_min_mbits"] = round(min(vals), 1)
            tcp["interval_avg_mbits"] = round(sum(vals) / len(vals), 1)
        return tcp, None
    except Exception as e:
        return None, str(e)[:160]
    finally:
        try:
            shutil.rmtree(staging, ignore_errors=True)
        except Exception:
            pass


_STRESS_VERDICT = [
    ("优良", "ok"), ("可用", "warn"), ("拥塞", "warn"), ("异常", "err"),
]


def _stress_verdict(ping_rows, iperf):
    """综合结论：按丢包与延迟阈值判定（优良/可用/拥塞/异常）。"""
    if not ping_rows and not iperf:
        return {"text": "异常", "cls": "err", "reason": "无有效数据"}
    ok_any = any(r["ok"] for r in ping_rows)
    if not ok_any and not any(x.get("ok") for x in iperf):
        return {"text": "异常", "cls": "err", "reason": "全部探测失败（中心不可达或链路中断）"}
    worst_loss = max([r["loss_pct"] or 0 for r in ping_rows if r["ok"]] or [0])
    worst_avg = max([r["avg_ms"] or 0 for r in ping_rows if r["ok"]] or [0])
    if worst_loss >= 5:
        return {"text": "异常", "cls": "err", "reason": "最高丢包 %.1f%%（≥5%%）" % worst_loss}
    if worst_loss > 0 or worst_avg > 50:
        return {"text": "拥塞", "cls": "warn",
                "reason": "丢包 %.1f%% / 平均延迟 %sms 偏高" % (worst_loss, worst_avg)}
    if worst_avg > 10:
        return {"text": "可用", "cls": "warn", "reason": "平均延迟 %sms（10~50ms）" % worst_avg}
    return {"text": "优良", "cls": "ok", "reason": "零丢包，平均延迟 %sms" % worst_avg}


def run_stress(task, params):
    """压测引擎：①各档包长 ping 中心（每档 ping -n 20 -l size）②iperf3 TCP+UDP 轮次
    （平台 iperf-server 起服务端 → 本地 iperf3 客户端 → iperf-result 回传）。"""
    if not uplink_configured():
        return {"success": False, "error": "not_connected"}
    center = _center_host()
    if not center:
        return {"success": False, "error": "center_host_missing"}
    try:
        duration = max(5, min(120, int(float(params.get("duration_sec") or 10))))
    except (TypeError, ValueError):
        duration = 10
    try:
        udp_mbps = max(1, min(1000, int(float(params.get("udp_mbps") or 100))))
    except (TypeError, ValueError):
        udp_mbps = 100
    sizes = _DEFAULT_STRESS_SIZES
    raw_sizes = str(params.get("sizes") or "").strip()
    if raw_sizes:
        try:
            cand = [max(16, min(65000, int(s))) for s in raw_sizes.split(",") if s.strip()]
            if cand:
                sizes = cand
        except (TypeError, ValueError):
            pass
    if not _iperf3_available():
        return {"success": False, "error": "iperf3_not_bundled"}

    result = {"success": True, "center": center, "sizes": sizes,
              "udp_mbps": udp_mbps, "iperf_duration": duration,
              "ping": [], "iperf": [], "ts": int(time.time())}
    tid = _terminal_id()

    # ① 多档包长 ping
    for i, size in enumerate(sizes):
        if task and task["_cancel"].is_set():
            result["cancelled"] = True
            break
        _set_progress(task, stage="ping", done=i, total=len(sizes) + 2,
                      current="ping -l %s" % size)
        rc, out = _run(["ping", "-n", "20", "-l", str(size), center],
                       timeout=20 * 2 + 6)
        row = {"size": size, "ok": False, "loss_pct": 100.0, "avg_ms": None,
               "max_ms": None, "min_ms": None}
        replies = [int(m.group(1)) for m in
                   re.finditer(r"(?:time|时间)\s*[=<]\s*(\d+)\s*ms", out, re.IGNORECASE)]
        m_stat = re.search(
            r"(?:已接收|Received)\s*=\s*(\d+).{0,40}?(?:丢失|Lost)\s*=\s*(\d+)\s*[（(]\s*(\d+(?:\.\d+)?)\s*%",
            out, re.IGNORECASE | re.DOTALL)
        if m_stat:
            recv, loss = int(m_stat.group(1)), float(m_stat.group(3))
            row["ok"] = recv > 0
            row["loss_pct"] = round(loss, 1)
        elif replies:
            row["ok"] = True
            row["loss_pct"] = 0.0
        if replies:
            row["min_ms"] = min(replies)
            row["max_ms"] = max(replies)
            row["avg_ms"] = round(sum(replies) / len(replies), 1)
        m_avg = re.search(r"(?:平均|Average)\s*=\s*(\d+)\s*ms", out, re.IGNORECASE)
        if m_avg and replies:
            row["avg_ms"] = int(m_avg.group(1))
        result["ping"].append(row)
    _record_jsonl({"ts": int(time.time()), "key": "stress_ping", "target": center,
                   "ok": any(r["ok"] for r in result["ping"]),
                   "loss_pct": max([r["loss_pct"] for r in result["ping"]] or [100.0]),
                   "avg_ms": None, "max_ms": None})

    # ② iperf3 TCP + UDP
    for mode in ("tcp", "udp"):
        if task and task["_cancel"].is_set():
            result["cancelled"] = True
            break
        _set_progress(task, stage="iperf-" + mode,
                      done=len(result["ping"]) + (1 if mode == "udp" else 0),
                      total=len(sizes) + 2, current="iperf3 " + mode.upper())
        code, resp = _platform_post("/api/v1/terminals/%s/netdoctor/iperf-server" % tid,
                                    {"mode": mode, "duration_sec": duration})
        if not (200 <= code < 300) or not (resp or {}).get("port"):
            result["iperf"].append({"mode": mode, "ok": False,
                                    "error": "iperf_server_http_%s: %s" % (code, (resp or {}).get("error", ""))})
            continue
        port = int(resp["port"])
        summary, err = _run_iperf3(center, port, duration, mode, udp_mbps)
        if summary is None:
            result["iperf"].append({"mode": mode, "ok": False, "error": err})
            _platform_post("/api/v1/terminals/%s/netdoctor/iperf-result" % tid,
                           {"task_id": resp.get("task_id"), "ok": False, "data": {"error": err}})
            continue
        if mode == "tcp":
            text = "tcp %.1f Mbits/sec" % (summary.get("mbits_sec") or 0)
        else:
            text = "udp %.1f Mbits/sec jitter %sms loss %s%%" % (
                summary.get("mbits_sec") or 0, summary.get("jitter_ms"),
                summary.get("lost_percent"))
        entry = {"mode": mode, "ok": True, "port": port, "duration_sec": duration,
                 "summary": text, "result": summary}
        result["iperf"].append(entry)
        _platform_post("/api/v1/terminals/%s/netdoctor/iperf-result" % tid,
                       {"task_id": resp.get("task_id"), "ok": True,
                        "data": {"summary": text, "result": summary}})

    result["verdict"] = _stress_verdict(result["ping"], result["iperf"])
    return result


# ---- 压测 HTML 报告（单文件自包含深色风格） ----

_STRESS_CSS = """
body{margin:0;background:#10131b;color:#e8ebf2;font:14px/1.7 "Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:960px;margin:0 auto;padding:28px 20px 48px}
header{border-bottom:1px solid #262b3a;padding-bottom:14px;margin-bottom:20px}
.brand{font-size:13px;color:#8a93a5;letter-spacing:1px}
h1{font-size:21px;margin:6px 0 2px}
h2{font-size:15px;color:#4fc3f7;margin:26px 0 10px;border-left:3px solid #4fc3f7;padding-left:9px}
.meta{font-size:12px;color:#8a93a5}
.badge{display:inline-block;border:1px solid;border-radius:20px;padding:2px 12px;font-size:13px}
.b-ok{color:#66bb6a;border-color:rgba(102,187,106,.5);background:rgba(102,187,106,.1)}
.b-warn{color:#ffb74d;border-color:rgba(255,183,77,.5);background:rgba(255,183,77,.1)}
.b-err{color:#ef5350;border-color:rgba(239,83,80,.5);background:rgba(239,83,80,.1)}
table{width:100%;border-collapse:collapse;margin:6px 0 12px;font-size:13px}
th,td{border:1px solid #262b3a;padding:6px 10px;text-align:left}
th{background:#171a23;color:#8a93a5;font-weight:600}
td.num{font-variant-numeric:tabular-nums}
.kv{display:grid;grid-template-columns:150px 1fr;gap:4px 12px;font-size:13px;margin:6px 0}
.kv .k{color:#8a93a5}
footer{margin-top:34px;font-size:11px;color:#5a6274;border-top:1px solid #262b3a;padding-top:12px}
"""


def stress_report_html(r):
    """压测结果 dict → 单文件自包含深色 HTML（头部「观枢终端平台｜EyeTerm」）。"""
    def esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    v = r.get("verdict") or {}
    ping_rows = ""
    for p in r.get("ping") or []:
        cls = "b-ok" if p["ok"] else "b-err"
        ping_rows += ("<tr><td class='num'>%s B</td><td><span class='badge %s'>%s</span></td>"
                      "<td class='num'>%s</td><td class='num'>%s</td><td class='num'>%s</td>"
                      "<td class='num'>%s%%</td></tr>"
                      % (p["size"], cls, "通" if p["ok"] else "失败",
                         p["avg_ms"] if p["avg_ms"] is not None else "--",
                         p["min_ms"] if p["min_ms"] is not None else "--",
                         p["max_ms"] if p["max_ms"] is not None else "--",
                         p["loss_pct"] if p["loss_pct"] is not None else "--"))
    iperf_rows = ""
    for x in r.get("iperf") or []:
        if x.get("ok"):
            sm = x["result"]
            if x["mode"] == "tcp":
                detail = ("聚合 %s Mbits/sec（intervals 最大 %s / 最小 %s / 平均 %s），重传 %s"
                          % (sm.get("mbits_sec"), sm.get("interval_max_mbits", "--"),
                             sm.get("interval_min_mbits", "--"), sm.get("interval_avg_mbits", "--"),
                             sm.get("retransmits", "--")))
            else:
                detail = ("带宽 %s Mbits/sec，抖动 %s ms，丢包 %s%%"
                          % (sm.get("mbits_sec"), sm.get("jitter_ms"), sm.get("lost_percent")))
            iperf_rows += ("<tr><td>%s</td><td><span class='badge b-ok'>完成</span></td>"
                           "<td class='num'>%ss</td><td>%s</td></tr>"
                           % (x["mode"].upper(), x.get("duration_sec"), esc(detail)))
        else:
            iperf_rows += ("<tr><td>%s</td><td><span class='badge b-err'>失败</span></td>"
                           "<td class='num'>--</td><td>%s</td></tr>"
                           % (x["mode"].upper(), esc(x.get("error") or "--")))
    vcls = {"ok": "b-ok", "warn": "b-warn", "err": "b-err"}.get(v.get("cls"), "b-warn")
    dt = datetime.fromtimestamp(r.get("ts") or time.time()).strftime("%Y-%m-%d %H:%M:%S")
    return ("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            "<title>NetStress 压测报告</title><style>%s</style></head><body><div class='wrap'>"
            "<header><div class='brand'>观枢终端平台｜EyeTerm · 网络监测配置</div>"
            "<h1>网络压测报告</h1><div class='meta'>目标 %s ｜ 生成时间 %s</div></header>"
            "<h2>综合结论</h2><div><span class='badge %s'>%s</span>"
            "<span style='margin-left:10px;font-size:13px'>%s</span></div>"
            "<div class='kv' style='margin-top:14px'>"
            "<span class='k'>iperf3 轮次时长</span><span>%s 秒 / 轮</span>"
            "<span class='k'>UDP 码率</span><span>%s Mbps</span>"
            "<span class='k'>包长档位</span><span>%s</span></div>"
            "<h2>① 中心服务器多档包长 ping（每档 20 次）</h2>"
            "<table><tr><th>包长</th><th>结果</th><th>平均 ms</th><th>最小 ms</th><th>最大 ms</th><th>丢包</th></tr>%s</table>"
            "<h2>② iperf3 带宽压测</h2>"
            "<table><tr><th>模式</th><th>状态</th><th>时长</th><th>结果</th></tr>%s</table>"
            "<footer>观枢终端平台｜EyeTerm · net-doctor 网络压测 · 本报告为单文件自包含 HTML，可离线查看</footer>"
            "</div></body></html>"
            % (_STRESS_CSS, esc(r.get("center") or "--"), dt, vcls, esc(v.get("text") or "--"),
               esc(v.get("reason") or ""), r.get("iperf_duration"), r.get("udp_mbps"),
               esc(",".join(str(s) for s in (r.get("sizes") or []))),
               ping_rows or "<tr><td colspan='6'>无数据</td></tr>",
               iperf_rows or "<tr><td colspan='4'>无数据</td></tr>"))


def export_stress_report(result):
    """结果 dict → netdoctor_records/NetStress_YYYYMMDD_HHMM.html，返回路径。"""
    dt = datetime.now().strftime("%Y%m%d_%H%M")
    path = os.path.join(_records_dir(), "NetStress_%s.html" % dt)
    with open(path, "w", encoding="utf-8") as f:
        f.write(stress_report_html(result))
    return path


# ============================================================
# 后台任务管理（disk_cleanup._start_task 同款模式，ADR-003）
# ============================================================

_tasks = {}
_tlock = threading.Lock()


def _set_progress(task, **kw):
    """线程内更新进度 dict（容错：任务可能已被视图丢弃）。"""
    if task is None:
        return
    try:
        task["progress"].update(kw)
    except Exception:
        pass


def _start_task(kind, runner, params):
    # 同类任务进行中则复用（幂等）
    with _tlock:
        for t in _tasks.values():
            if t["kind"] == kind and t["status"] == "running":
                return t["id"], True
        task_id = uuid.uuid4().hex[:12]
        task = {"id": task_id, "kind": kind, "status": "running",
                "progress": {}, "result": None, "error": None,
                "started": time.time(), "_cancel": threading.Event()}
        _tasks[task_id] = task

    def _worker():
        try:
            res = runner(task, params)
            if task["status"] == "running":
                task["status"] = "done"
                task["result"] = res
        except Exception as e:
            task["status"] = "error"
            task["error"] = str(e)[:200]

    threading.Thread(target=_worker, daemon=True,
                     name="netdoctor-" + kind).start()
    # 简单清理：仅保留最近 20 个任务
    with _tlock:
        if len(_tasks) > 20:
            for k in sorted(_tasks, key=lambda x: _tasks[x]["started"])[:-20]:
                _tasks.pop(k, None)
    return task_id, False


def _task_view(task_id):
    with _tlock:
        task = _tasks.get(task_id or "")
        if not task:
            return None
        return {"id": task["id"], "kind": task["kind"], "status": task["status"],
                "progress": task["progress"], "result": task["result"],
                "error": task["error"], "elapsed": round(time.time() - task["started"], 1)}


def _cancel_task(task_id):
    with _tlock:
        task = _tasks.get(task_id or "")
        if task and task["status"] == "running":
            task["_cancel"].set()
            return True
    return False


# ============================================================
# handle_net_* 桥接约定（供 bridge.py 挂载 /api/netdoctor/*）
# ============================================================


_NODE_METHODS = ("ping", "nslookup", "ntp")
_DYNAMIC_KEYS = ("gateway", "center")     # 目标动态解析（本地网关 / uplink server host）


def _sanitize_nodes(raw):
    """节点表校验：method 白名单 / 目标仅 IP·域名字符 / 动态键强制 target=""。"""
    if not isinstance(raw, list):
        return None
    nodes = []
    for n in raw:
        if not isinstance(n, dict):
            return None
        key = str(n.get("key") or "").strip() or uuid.uuid4().hex[:8]
        name = str(n.get("name") or "").strip()
        method = str(n.get("method") or "ping").strip().lower()
        target = str(n.get("target") or "").strip()
        probe = str(n.get("probe") or "").strip()
        if not name or method not in _NODE_METHODS:
            return None
        if target and not re.match(r"^[\w.\-]+$", target):
            return None
        if key in _DYNAMIC_KEYS:
            target = ""                      # 网关/中心服务器：动态获取，不接受静态目标
        node = {"key": key, "name": name[:60], "method": method, "target": target}
        if probe and method == "nslookup":
            node["probe"] = probe[:60]
        nodes.append(node)
    return nodes


def _save_netdoctor_section(nd):
    """merge 原子写：仅替换 app_config.json 的 netdoctor 键，其余模块键（perf.* 等）
    原样保留（与 perf_service merge 语义对齐，ADR-018）。"""
    path = os.path.join(_data_dir(), "app_config.json")
    data = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            data = saved
    except Exception:
        data = {}
    data["netdoctor"] = nd
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def handle_net_config(params=None):
    """读配置（无参）/ 写配置（nodes_json / expected_dns_json / ai_personal_json / reset=1）。
    写入为 merge 原子写，保存后连通性检测即时生效（每次检测读取最新配置，无缓存）。
    ai_personal：个人版 LLM 配置；api_key 留空 = 不修改（与 uplink token 同款语义），
    读取端只回传 has_key 不回显明文。"""
    params = params or {}
    raw_nodes = params.get("nodes_json")
    raw_dns = params.get("expected_dns_json")
    raw_ai = params.get("ai_personal_json")
    reset = str(params.get("reset") or "").strip().lower() in ("1", "true", "yes")
    if raw_nodes is not None or raw_dns is not None or raw_ai is not None or reset:
        cur = _load_app_config()
        nd = {}
        if reset:
            # 恢复出厂：不写 nodes 键（读取端回退内置默认表），基线清空；
            # 个人版凭据属用户配置，随恢复保留
            nd["expected_dns"] = []
            nd["ai_personal"] = cur.get("ai_personal") or {}
        else:
            nd["nodes"] = cur["nodes"]
            nd["expected_dns"] = cur["expected_dns"]
            nd["ai_personal"] = cur.get("ai_personal") or {}
        if raw_dns is not None:
            try:
                dns = json.loads(raw_dns)
            except Exception:
                return {"success": False, "error": "expected_dns_json 解析失败"}
            if not isinstance(dns, list):
                return {"success": False, "error": "expected_dns_json 须为字符串数组"}
            nd["expected_dns"] = [str(x).strip() for x in dns if str(x).strip()][:16]
        if raw_nodes is not None:
            try:
                nodes = _sanitize_nodes(json.loads(raw_nodes))
            except Exception:
                return {"success": False, "error": "nodes_json 解析失败"}
            if nodes is None:
                return {"success": False,
                        "error": "节点表校验失败（名称必填；方式限 ping/nslookup/ntp；目标仅 IP/域名）"}
            nd["nodes"] = nodes
        if raw_ai is not None:
            try:
                ai = json.loads(raw_ai)
            except Exception:
                return {"success": False, "error": "ai_personal_json 解析失败"}
            if not isinstance(ai, dict):
                return {"success": False, "error": "ai_personal_json 须为对象"}
            cur_ai = cur.get("ai_personal") or {}
            key_in = str(ai.get("api_key") or "").strip()
            nd["ai_personal"] = {
                "api_url": str(ai.get("api_url") or "").strip()[:300],
                "api_key": key_in or str(cur_ai.get("api_key") or ""),
                "model": str(ai.get("model") or "").strip()[:120],
            }
        try:
            _save_netdoctor_section(nd)
        except Exception as e:
            return {"success": False, "error": "save_failed: %s" % e}
    cfg = _load_app_config()
    u = _uplink_config()
    ap = cfg.get("ai_personal") or {}
    return {"success": True,
            "nodes": cfg["nodes"], "expected_dns": cfg["expected_dns"],
            "ai_personal": {"api_url": ap.get("api_url") or "", "model": ap.get("model") or "",
                            "has_key": bool(ap.get("api_key"))},
            "uplink": {"enabled": bool(u.get("enabled")),
                       "server_url": u.get("server_url") or "",
                       "terminal_id": _terminal_id(u),
                       "configured": uplink_configured()},
            "iperf3_available": _iperf3_available()}


def handle_net_config_check(params=None):
    """启动配置核查任务。"""
    task_id, reused = _start_task("confcheck",
                                  lambda t, p: run_config_check_result(), {})
    return {"success": True, "task_id": task_id, "reused": reused}


def handle_net_ipconflict(params=None):
    """启动 IP 冲突检测任务。"""
    if not uplink_configured():
        return {"success": False, "error": "not_connected"}
    task_id, reused = _start_task("ipconflict",
                                  lambda t, p: run_ipconflict_result(t), {})
    return {"success": True, "task_id": task_id, "reused": reused}


def handle_net_ping_start(params=None):
    """启动连通性全量检测任务。"""
    task_id, reused = _start_task("ping", run_ping_suite, {})
    return {"success": True, "task_id": task_id, "reused": reused}


def handle_net_ping_history(params=None):
    p = params or {}
    try:
        limit = max(10, min(500, int(p.get("limit") or 100)))
    except (TypeError, ValueError):
        limit = 100
    hours = None
    try:
        if p.get("hours") not in (None, ""):
            hours = max(0.1, min(8760.0, float(p.get("hours"))))
    except (TypeError, ValueError):
        hours = None
    start_ts = end_ts = None
    try:
        if p.get("start_ts") not in (None, ""):
            start_ts = float(p.get("start_ts"))
        if p.get("end_ts") not in (None, ""):
            end_ts = float(p.get("end_ts"))
    except (TypeError, ValueError):
        start_ts = end_ts = None
    return ping_history(limit, hours=hours, start_ts=start_ts, end_ts=end_ts)


def handle_net_tracert_start(params):
    """启动路由追踪任务。params: target"""
    target = (params.get("target") or "").strip() if params else ""
    if not target:
        return {"success": False, "error": "缺少 target（IP 或域名）"}
    task_id, reused = _start_task("tracert", run_tracert_result, target)
    return {"success": True, "task_id": task_id, "reused": reused}


def handle_net_stress_start(params):
    """启动网络压测任务。params: duration_sec / sizes / udp_mbps"""
    if not uplink_configured():
        return {"success": False, "error": "not_connected"}
    p = {"duration_sec": params.get("duration_sec") if params else None,
         "sizes": params.get("sizes") if params else None,
         "udp_mbps": params.get("udp_mbps") if params else None}
    task_id, reused = _start_task("stress", run_stress, p)
    return {"success": True, "task_id": task_id, "reused": reused}


def handle_net_task_status(params):
    """通用任务轮询。"""
    view = _task_view((params or {}).get("task_id", ""))
    if view is None:
        return {"success": False, "error": "任务不存在或已过期"}
    return {"success": True, "task": view}


def handle_net_task_cancel(params):
    ok = _cancel_task((params or {}).get("task_id", ""))
    return {"success": True, "cancelled": ok}


def handle_net_stress_export(params):
    """导出压测 HTML 报告（需先有完成的压测任务）。params: task_id"""
    view = _task_view((params or {}).get("task_id", ""))
    if not view or view["status"] != "done" or not isinstance(view.get("result"), dict):
        return {"success": False, "error": "压测任务不存在或未完成"}
    try:
        path = export_stress_report(view["result"])
    except Exception as e:
        return {"success": False, "error": "export_failed: %s" % e}
    return {"success": True, "path": path}


def handle_net_ai_diagnose(params=None, body=None):
    """AI 智能诊断双模式入口（2026-09-10 第九项优化）：
    - mode=enterprise（默认）：转发代理 → 平台 /ai/diagnose（token 仅后端注入），
      未连中心 → not_connected
    - mode=personal：本机直连第三方 OpenAI 兼容 API（/v1/chat/completions），
      不依赖中心；未配置 → not_configured 引导设置
    响应结构对齐：{success, response_text, model, duration_ms}
    （个人版无 analysis_id；失败 {success:false, error}）"""
    b = body if isinstance(body, dict) else {}
    mode = str(b.get("mode") or "enterprise").strip().lower()
    if mode == "personal":
        return _ai_diagnose_personal(b)
    return _ai_diagnose_enterprise(b)


def _ai_diagnose_enterprise(body):
    """企业版：转发代理（第六模块 2026-09-09 原逻辑）。
    token 仅在服务端注入；issue 截 2000 字，logs 每类 ≤32KB 双保险；
    同步等待 ≤120s（服务端模型链），本端 urllib 超时 125s 保护。"""
    if not uplink_configured():
        return {"success": False, "error": "not_connected"}
    if not isinstance(body, dict):
        return {"success": False, "error": "body_invalid"}
    issue = str(body.get("issue") or "").strip()[:2000]
    if not issue:
        return {"success": False, "error": "issue_empty"}
    logs = body.get("logs") if isinstance(body.get("logs"), dict) else {}
    clean = {}
    for k, v in logs.items():
        if isinstance(k, str) and re.match(r"^[a-z_]{1,32}$", k):
            clean[k] = v
    payload = {"issue": issue, "logs": clean}
    tid = _terminal_id()
    code, resp = _platform_post("/api/v1/terminals/%s/ai/diagnose" % tid, payload, timeout=125)
    if not (200 <= code < 300) or not (resp or {}).get("ok"):
        # 非 200/ok=false 的失败诊断服务端同样落库——透传 analysis_id 供 UI 引导追溯
        # （server-platform 对接要点 2026-09-09：失败诊断可在控制台 AI 分析页查看）
        return {"success": False,
                "error": "ai_diagnose_http_%s: %s" % (code, (resp or {}).get("error", "")),
                "analysis_id": (resp or {}).get("analysis_id")}
    return {"success": True,
            "analysis_id": resp.get("analysis_id"),
            "response_text": resp.get("response_text"),
            "model": resp.get("model"),
            "duration_ms": resp.get("duration_ms")}


# 个人版三段结构提示词（与 server-platform/server/ai.py DIAG_SYSTEM_PROMPT 语义对齐，
# 单份沉淀防漂移；企业版 prompt 在中心侧，本常量供个人版直调使用。
# 2026-09-10 证据可信性硬约束：analysis_id=16 实证模型虚构 WHEA-Logger 17 等「日志依据」——
# 服务端同款加固由 server-platform-dev 并行处理。）
_ND_AI_PROMPT_SYSTEM = (
    "你是医院网络运维诊断专家。基于终端上报的问题概述与日志数据，输出中文诊断结论，"
    "必须且只能按以下三段结构输出：\n"
    "【故障原因分析】按可能性排序，引用日志依据（注明来自哪一类日志）；\n"
    "【处理意见】分「立即处理」「建议观察」两档，给出可执行步骤；\n"
    "【风险提示】数据缺失或需要补充采集的部分。\n"
    "证据可信性硬约束（必须遵守，优先级高于其它风格要求）：\n"
    "1. 只允许引用随请求提供的日志中实际出现的事件 ID/来源/时间戳，"
    "严禁编造、推测或凭常识填充任何「日志依据」；\n"
    "2. 引用事件必须附带日志原文中的时间戳；\n"
    "3. 某类日志未提供/为空/被截断时，必须显式声明该维度证据不足，"
    "不得用其它维度推断或常识替代。\n"
    "约束：只基于给出的日志数据分析，不要编造；中文输出；简洁专业。\n"
    "隐私边界：日志包仅含运维诊断数据，不涉及用户个人文件内容与任何凭据。"
)


def _nd_ai_logs_text(logs):
    """logs dict → 分节文本（与中心 build_diagnose_context 语义对齐）"""
    if not isinstance(logs, dict) or not logs:
        return "（无日志数据）"
    parts = []
    for k in sorted(logs.keys()):
        v = logs[k]
        if not isinstance(v, str):
            try:
                v = json.dumps(v, ensure_ascii=False)
            except Exception:
                v = str(v)
        parts.append("== %s ==\n%s" % (k, str(v)[:32768]))
    return "\n\n".join(parts)


def _personal_chat_url(url):
    """API 地址自适配：完整 /v1/chat/completions 原样；以 /v1 结尾补 /chat/completions；
    其余按 base 处理补 /v1/chat/completions"""
    u = (url or "").strip().rstrip("/")
    if u.endswith("/chat/completions"):
        return u
    if u.endswith("/v1"):
        return u + "/chat/completions"
    return u + "/v1/chat/completions"


def _personal_models_url(url):
    """连通性测试地址自适配（GET /models）"""
    u = (url or "").strip().rstrip("/")
    if u.endswith("/chat/completions"):
        u = u[: -len("/chat/completions")]
    if u.endswith("/models"):
        return u
    if u.endswith("/v1"):
        return u + "/models"
    return u + "/v1/models"


def _ai_personal_config():
    ap = _load_app_config().get("ai_personal") or {}
    api_url = str(ap.get("api_url") or "").strip()
    api_key = str(ap.get("api_key") or "").strip()
    model = str(ap.get("model") or "").strip()
    if not api_url or not model:
        return None
    return {"api_url": api_url, "api_key": api_key, "model": model}


def _ai_diagnose_personal(body):
    """个人版：本机直连第三方 OpenAI 兼容 API（chat/completions，120s 超时）。
    错误信息永不携带 api_key。"""
    conf = _ai_personal_config()
    if not conf:
        return {"success": False, "error": "not_configured"}
    issue = str(body.get("issue") or "").strip()[:2000]
    if not issue:
        return {"success": False, "error": "issue_empty"}
    logs = body.get("logs") if isinstance(body.get("logs"), dict) else {}
    clean = {}
    for k, v in logs.items():
        if isinstance(k, str) and re.match(r"^[a-z_]{1,32}$", k):
            clean[k] = v
    payload = {
        "model": conf["model"],
        "messages": [
            {"role": "system", "content": _ND_AI_PROMPT_SYSTEM},
            {"role": "user",
             "content": "问题概述：%s\n\n诊断日志数据：\n%s" % (issue, _nd_ai_logs_text(clean))},
        ],
        "temperature": 0.3,
        "stream": False,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(_personal_chat_url(conf["api_url"]), data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    if conf["api_key"]:
        req.add_header("Authorization", "Bearer " + conf["api_key"])
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        return {"success": False, "error": "personal_http_%s: %s" % (e.code, detail)}
    except Exception as e:
        return {"success": False, "error": "personal_request_failed: %s" % e}
    duration_ms = int((time.time() - t0) * 1000)
    try:
        content = str(resp["choices"][0]["message"]["content"] or "")
    except Exception:
        return {"success": False, "error": "personal_bad_response"}
    if not content.strip():
        return {"success": False, "error": "personal_empty_response"}
    return {"success": True, "response_text": content,
            "model": conf["model"], "duration_ms": duration_ms}


def handle_net_ai_personal_test(params=None):
    """个人版连通性测试（轻量 GET /models，15s 超时；鉴权被拒/不可达如实标注）。
    2026-09-10 语义修复（用户实锤误判 401）：api_key 留空 = 回退已保存配置的 Key
    （对齐保存端「留空不修改」语义）；used_key: provided（表单现值）/ saved（已保存）/ none。
    used_key=none 时不发请求，直接提示先配置。错误信息永不携带 api_key。"""
    p = params or {}
    api_url = (p.get("api_url") or "").strip()
    key_in = (p.get("api_key") or "").strip()
    if not api_url:
        return {"success": False, "error": "api_url_required"}
    saved_key = str((_load_app_config().get("ai_personal") or {}).get("api_key") or "").strip()
    if key_in:
        api_key, used_key = key_in, "provided"
    elif saved_key:
        api_key, used_key = saved_key, "saved"
    else:
        return {"success": True, "ok": False, "used_key": "none",
                "hint": "未配置 API Key，请先填写并保存（设置 → AI 诊断（个人版））"}
    req = urllib.request.Request(_personal_models_url(api_url), method="GET")
    if api_key:
        req.add_header("Authorization", "Bearer " + api_key)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            code = r.getcode()
    except urllib.error.HTTPError as e:
        hint = ("Key 被拒（HTTP 401）——请核对 Key 是否完整/有效" if e.code == 401
                else "服务可达但请求被拒（HTTP %s，请检查 Key / 地址）" % e.code)
        return {"success": True, "ok": False, "http_code": e.code, "used_key": used_key, "hint": hint}
    except Exception as e:
        return {"success": True, "ok": False, "http_code": None, "used_key": used_key,
                "hint": "连接失败：%s" % e}
    if 200 <= code < 300:
        return {"success": True, "ok": True, "http_code": code, "used_key": used_key,
                "hint": "服务可达（HTTP %s）" % code}
    return {"success": True, "ok": False, "http_code": code, "used_key": used_key,
            "hint": "HTTP %s" % code}


NET_ROUTES = {
    "/api/netdoctor/config": handle_net_config,
    "/api/netdoctor/config-check": handle_net_config_check,
    "/api/netdoctor/ipconflict": handle_net_ipconflict,
    "/api/netdoctor/ping-start": handle_net_ping_start,
    "/api/netdoctor/ping-history": handle_net_ping_history,
    "/api/netdoctor/tracert-start": handle_net_tracert_start,
    "/api/netdoctor/stress-start": handle_net_stress_start,
    "/api/netdoctor/stress-export": handle_net_stress_export,
    "/api/netdoctor/task-status": handle_net_task_status,
    "/api/netdoctor/task-cancel": handle_net_task_cancel,
    "/api/netdoctor/ai-diagnose": handle_net_ai_diagnose,
    "/api/netdoctor/ai-personal-test": handle_net_ai_personal_test,
}


if __name__ == "__main__":
    # 冒烟入口：python net_service.py [ping|conf|gateway]
    import pprint
    arg = sys.argv[1] if len(sys.argv) > 1 else "conf"
    if arg == "ping":
        pprint.pprint(_ping_summary("127.0.0.1", count=2))
    elif arg == "gateway":
        print("default gateway:", _default_gateway())
    else:
        r = run_config_check_result()
        pprint.pprint(r.get("overall"))
        for a in (r.get("adapters") or [])[:6]:
            print(a["name"], a["ipv4"], a["dhcp"], [c["status"] for c in a["checks"]])
