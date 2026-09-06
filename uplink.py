# -*- coding: utf-8 -*-
"""
终端接入服务端（平台联动）— EyeTerm uplink 模块（v4）
=================================================================
职责（独立于 Web 框架，供 bridge.py 以 handle_* 约定挂载）：
  1. 注册：POST /api/v1/terminals/register（携带 hwinfo 资产对象，幂等）
  2. 心跳循环：后台 daemon 线程（默认 30s，失败指数退避），响应携带的
     commands 数组解析 → 处理器映射表分发 → 线程执行（不阻塞心跳）→ 按 id
     幂等 → 回执 POST result
  3. 指标上报：心跳成功拍附带一次 snapshot 映射上报（复用 perf_service 采集，
     不重复采样，30s/拍）
  4. 命令处理器：iperf_client（内置 iperf3）/ net_probe（ping/网关/TCP）/
     collect_logs（v1 hook，ADR-019 结构已冻结待日志引擎接入）/ ai_context
     （AI 暂缓，固定 ok=false reason=ai_disabled）

协议依据：server-platform ADR-015/016/019（心跳命令通道协议 v1 冻结稿）。
安全红线：
  - token 仅存本机配置文件（%LOCALAPPDATA%/winhelper/uplink_config.json），
    状态接口永不回显（仅 has_token 布尔）；禁止日志/异常文本携带 token
  - collect_logs args 携带的 FTP 凭据用完即弃，禁止落盘/打日志/回显（ADR-019）
  - GUI 无控制台程序中一切子进程必须 CREATE_NO_WINDOW（ADR-013）
依赖：网络层纯标准库 urllib；psutil/perf_service 为进程内复用（同应用既有依赖）。
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

# GUI（无控制台）程序中调用控制台子进程（ping/route/iperf3）必须隐藏窗口（ADR-013）
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

CLIENT_VERSION = "4.0.0"
TERMINAL_TYPE = "windows"
DEFAULT_HEARTBEAT_INTERVAL = 30          # 秒（ADR 可调）
_BACKOFF_CAP = 20                        # 失败退避倍数上限（30s*20=600s）
_RESULT_RETRIES = 3                      # 命令回执重试（409 终态不重试）


def _decode_output(b):
    """子进程 bytes 输出解码：utf-8 → gbk → replace 兜底。
    中文系统 ping/route 输出为 GBK；PYTHONUTF8=1 模式下 text=True 会用 utf-8
    解码 GBK 直接 UnicodeDecodeError（e2e 实测踩中），故统一 bytes + 多编码解码。"""
    if b is None:
        return ""
    for enc in ("utf-8", "gbk"):
        try:
            return b.decode(enc)
        except Exception:
            continue
    return b.decode("utf-8", errors="replace")

# ============================================================
# 配置（落盘 %LOCALAPPDATA%/winhelper/uplink_config.json，0600 语义）
# ============================================================


def _data_dir():
    """配置目录：UPLINK_CONFIG_DIR 环境变量优先（测试隔离），默认 LOCALAPPDATA/winhelper。
    禁止硬编码盘符（LOCALAPPDATA → TEMP → SystemDrive 逐级 fallback，与 perf 记录目录一致）。"""
    base = os.environ.get("UPLINK_CONFIG_DIR") or os.environ.get("LOCALAPPDATA") \
        or os.environ.get("TEMP") or os.environ.get("SystemDrive", "C:") + os.sep
    d = os.path.join(base, "" if os.environ.get("UPLINK_CONFIG_DIR") else "winhelper")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = os.environ.get("TEMP") or os.getcwd()
    return d


def config_path():
    return os.path.join(_data_dir(), "uplink_config.json")


def load_config():
    cfg = {"enabled": False, "server_url": "", "token": "",
           "terminal_id": "", "heartbeat_interval": DEFAULT_HEARTBEAT_INTERVAL}
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            cfg.update({k: v for k, v in saved.items() if k in cfg})
    except Exception:
        pass
    return cfg


def save_config(cfg):
    path = config_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    try:
        os.chmod(path, 0o600)  # 0600 语义（Windows 下尽力而为，无 ACL 粒度）
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    os.replace(tmp, path)


# ============================================================
# 运行时状态（线程安全）
# ============================================================

_lock = threading.Lock()
_state = {
    "started": False,       # 循环线程存活（loop 退出时自置 False）
    "enabled": False,
    "state": "disabled",    # disabled | connecting | connected | error
    "terminal_id": None,
    "registered": False,
    "last_hb_ts": 0.0,
    "last_ok": False,
    "last_error": None,
    "executed": set(),      # 命令 id 幂等集合（服务端单次下发不重发，内存集合足够）
}
_loop_thread = {"obj": None}
_stop = threading.Event()
_wakeup = threading.Event()   # 手动注册/配置变更提前唤醒心跳拍


def _set_state(**kw):
    with _lock:
        _state.update(kw)


# ============================================================
# HTTP（纯标准库 urllib）
# ============================================================


def _post(path, payload, timeout=15):
    """POST JSON 到服务端。返回 (http_status:int, body:dict)。
    网络异常 → (-1, {"error": ...})；HTTP 4xx/5xx → 解析 error 字段。
    红线：任何异常/日志不得携带 token。"""
    cfg = load_config()
    server = (cfg.get("server_url") or "").rstrip("/")
    if not server:
        return -1, {"error": "uplink_server_missing"}
    url = server + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-ETP-Token", cfg.get("token") or "")
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


# ============================================================
# 资产信息（复用 perf_service hwinfo/snapshot，进程内调用不重复采集）
# ============================================================


def _default_terminal_id():
    try:
        host = socket.gethostname() or os.environ.get("COMPUTERNAME") or "UNKNOWN"
    except Exception:
        host = os.environ.get("COMPUTERNAME") or "UNKNOWN"
    return "WIN-" + re.sub(r"[^A-Za-z0-9_.\-]", "_", host).strip("_")[:40]


def _terminal_id(cfg):
    tid = (cfg.get("terminal_id") or "").strip()
    return tid or _default_terminal_id()


def _os_arch():
    try:
        import platform
        m = platform.machine() or ""
    except Exception:
        m = ""
    return {"AMD64": "x86_64", "ARM64": "arm64", "x86": "x86"}.get(m, m.lower() or None)


def _hwinfo_asset():
    """注册用资产对象（服务端 terminals 表字段：cpu_model/cpu_cores/mem_total_mb/
    disk_total_gb/gpu_info/os_arch，未知字段服务端忽略，可多带）。"""
    try:
        from perf_service import handle_perf_hwinfo
        hw = (handle_perf_hwinfo({}).get("hwinfo") or {})
    except Exception:
        hw = {}
    cpu = hw.get("cpu") or {}
    mem = hw.get("memory") or {}
    try:
        disk_total = sum(d.get("size") or 0 for d in (hw.get("disks") or []))
    except Exception:
        disk_total = 0
    gpus = [g.get("name") for g in (hw.get("gpu") or []) if g.get("name")]
    os_info = (hw.get("os") or {}).get("text")
    return {
        "cpu_model": cpu.get("name") or None,
        "cpu_cores": cpu.get("logical") or cpu.get("cores"),
        "mem_total_mb": (round(mem["total"] / 1048576.0) if mem.get("total") else None),
        "disk_total_gb": (round(disk_total / 1073741824.0, 1) if disk_total else None),
        "gpu_info": (" + ".join(gpus) if gpus else None),
        "os_arch": _os_arch(),
        "os_info": os_info,  # 附加字段，服务端进 hwinfo_json
    }


def _register_payload(cfg):
    tid = _terminal_id(cfg)
    try:
        hostname = socket.gethostname() or None
    except Exception:
        hostname = None
    payload = {
        "terminal_id": tid,
        "terminal_type": TERMINAL_TYPE,
        "hostname": hostname,
        "client_version": CLIENT_VERSION,
        "hwinfo": _hwinfo_asset(),
    }
    os_info = (payload["hwinfo"] or {}).pop("os_info", None)
    if os_info:
        payload["os_info"] = os_info
    return payload


def _metrics_payload(cfg, tid):
    """心跳同拍指标上报：handle_perf_snapshot 数据映射到平台 schema
    （cpu.percent / mem.used_percent 等 / swap.used_percent / disks[卷容量+busy] /
    volumes）。物理盘 busy 经 hwinfo 卷→盘映射挂到对应卷条目，覆盖服务端
    disk_saturation 的空间与 busy 双判定（ADR-006）。"""
    try:
        from perf_service import handle_perf_snapshot
        snap = handle_perf_snapshot({})
    except Exception:
        return None
    if not snap.get("success"):
        return None
    mem = snap.get("memory") or {}
    total_mb = (mem.get("total") / 1048576.0) if mem.get("total") else None
    used_mb = (mem.get("used") / 1048576.0) if mem.get("used") else None
    avail_pct = (round(100.0 - mem["percent"], 1)
                 if mem.get("percent") is not None else None)

    # 卷 → 物理盘 busy 映射（hwinfo 缓存：PhysicalDrive0 → ["C:\"]）
    try:
        from perf_service import handle_perf_hwinfo
        hw = (handle_perf_hwinfo({}).get("hwinfo") or {})
    except Exception:
        hw = {}
    vol2disk = {}
    for d in (hw.get("disks") or []):
        for v in (d.get("volumes") or []):
            vol2disk[str(v).rstrip("\\").upper()] = d.get("name")
    busy_by_disk = {d.get("name"): d.get("busy_pct") for d in (snap.get("disks") or [])}

    disks, volumes = [], []
    for v in (snap.get("volumes") or []):
        mount = v.get("mount")
        entry = {
            "mount": mount,
            "used_gb": (round(v["used"] / 1073741824.0, 2) if v.get("used") is not None else None),
            "total_gb": (round(v["total"] / 1073741824.0, 2) if v.get("total") is not None else None),
            "percent": v.get("percent"),
        }
        dname = vol2disk.get(str(mount or "").rstrip("\\").upper())
        if dname:
            entry["busy_percent"] = busy_by_disk.get(dname)
        disks.append(entry)
        volumes.append({"mount": mount, "percent": v.get("percent"),
                        "used_gb": entry["used_gb"], "total_gb": entry["total_gb"]})

    return {
        "terminal_id": tid,
        "terminal_type": TERMINAL_TYPE,
        "client_version": CLIENT_VERSION,
        "ts": int(time.time()),
        "cpu": {"percent": (snap.get("cpu") or {}).get("percent")},
        "mem": {"used_percent": mem.get("percent"), "available_percent": avail_pct,
                "used_mb": (round(used_mb, 1) if used_mb else None),
                "total_mb": (round(total_mb, 1) if total_mb else None)},
        "swap": {"used_percent": (snap.get("swap") or {}).get("percent")},
        "disks": disks,
        "volumes": volumes,
    }


# ============================================================
# 命令处理器映射表（按 command 类型注册，新增类型只加条目）
# ============================================================

COMMAND_HANDLERS = {}


def command_handler(name):
    def deco(fn):
        COMMAND_HANDLERS[name] = fn
        return fn
    return deco


# ---------- iperf3 客户端（打包随 exe，执行时解压至 %TEMP% 独立 staging） ----------


def _iperf3_env_exe():
    p = os.environ.get("UPLINK_IPERF_EXE")
    return p if p and os.path.exists(p) else None


def _iperf3_bundle_dir():
    """iperf3 资源目录：环境变量覆盖（E2E 假客户端）→ 打包 _MEIPASS/libs/iperf3 →
    开发态 项目 libs/iperf3。"""
    env = _iperf3_env_exe()
    if env:
        return os.path.dirname(os.path.abspath(env))
    base = getattr(sys, "_MEIPASS", None)
    cands = []
    if base:
        cands.append(os.path.join(base, "libs", "iperf3"))
    cands.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "libs", "iperf3"))
    for d in cands:
        if os.path.exists(os.path.join(d, "iperf3.exe")):
            return d
    return None


def _iperf3_available():
    return _iperf3_env_exe() is not None or _iperf3_bundle_dir() is not None


def _parse_iperf_json(j, mode):
    """iperf3 -J 输出 → 摘要（服务端 smoke 约定 "tcp 940.2 Mbits/sec" 样式）"""
    if not isinstance(j, dict):
        return None, "iperf3_output_invalid"
    if j.get("error"):
        return None, "iperf3_error: %s" % str(j["error"])[:120]
    end = j.get("end") or {}
    if mode == "udp":
        s = end.get("sum") or {}
        bps = s.get("bits_per_second")
        jitter = s.get("jitter_ms")
        loss = s.get("lost_percent")
        if bps is None and jitter is None and loss is None:
            return None, "iperf3_udp_summary_missing"
        return {
            "mode": "udp",
            "mbits_sec": (round(bps / 1e6, 1) if bps is not None else None),
            "jitter_ms": (round(jitter, 3) if jitter is not None else None),
            "lost_percent": (round(loss, 2) if loss is not None else None),
        }, None
    s = end.get("sum_received") or end.get("sum_sent") or end.get("sum") or {}
    bps = s.get("bits_per_second")
    if bps is None:
        return None, "iperf3_tcp_summary_missing"
    return {
        "mode": "tcp",
        "mbits_sec": round(bps / 1e6, 1),
        "retransmits": s.get("retransmits"),
    }, None


@command_handler("iperf_client")
def _cmd_iperf_client(args):
    """args={task_id, server_ip, server_port, duration_sec, mode tcp|udp, reverse}
    回执 data 必带 task_id（ADR-015/016）。执行目录：每次任务独立 staging
    （%TEMP%/winhelper_iperf3_<ts>，并发安全），finally 必删。"""
    task_id = args.get("task_id")
    server_ip = (args.get("server_ip") or "").strip()
    try:
        server_port = int(args.get("server_port") or 5201)
        duration = max(1, min(300, int(args.get("duration_sec") or 10)))
    except (TypeError, ValueError):
        return False, {"task_id": task_id, "error": "iperf_args_invalid"}
    if not server_ip:
        return False, {"task_id": task_id, "error": "iperf_server_ip_missing"}
    mode = (args.get("mode") or "tcp").strip().lower()
    if mode not in ("tcp", "udp"):
        mode = "tcp"

    env_exe = _iperf3_env_exe()
    bundle = _iperf3_bundle_dir()
    if env_exe is None and bundle is None:
        return False, {"task_id": task_id, "error": "iperf3_not_bundled"}

    staging = tempfile.mkdtemp(prefix="winhelper_iperf3_")
    try:
        # 复制资源（exe + 运行时 dll）到独立 staging，避免 _MEIPASS 退出清理与文件占用冲突
        src_dir = (os.path.dirname(os.path.abspath(env_exe)) if env_exe else bundle)
        for fn in os.listdir(src_dir):
            s = os.path.join(src_dir, fn)
            if os.path.isfile(s):
                shutil.copy2(s, os.path.join(staging, fn))
        exe = os.path.join(staging, os.path.basename(env_exe) if env_exe else "iperf3.exe")
        if not os.path.exists(exe):
            return False, {"task_id": task_id, "error": "iperf3_stage_copy_failed"}

        cmd = [exe, "-c", server_ip, "-p", str(server_port), "-t", str(duration), "-J"]
        if mode == "udp":
            cmd.append("-u")
        if args.get("reverse"):
            cmd.append("-R")
        try:
            r = subprocess.run(cmd, capture_output=True,
                               timeout=duration + 30, creationflags=_NO_WINDOW)
        except subprocess.TimeoutExpired:
            return False, {"task_id": task_id, "error": "iperf3_timeout"}
        if r.returncode != 0:
            detail = _decode_output(r.stderr or r.stdout).strip()[:160]
            return False, {"task_id": task_id,
                           "error": "iperf3_exit_%s: %s" % (r.returncode, detail or "no_output")}
        try:
            parsed, err = _parse_iperf_json(json.loads(_decode_output(r.stdout)), mode)
        except Exception:
            parsed, err = None, "iperf3_json_parse_failed"
        if parsed is None:
            return False, {"task_id": task_id, "error": err}
        summary = ("udp %.1f Mbits/sec jitter %sms loss %s%%"
                   % (parsed.get("mbits_sec") or 0, parsed.get("jitter_ms"),
                      parsed.get("lost_percent")) if mode == "udp"
                   else "tcp %.1f Mbits/sec" % (parsed.get("mbits_sec") or 0))
        return True, {"task_id": task_id, "summary": summary,
                      "mode": mode, "server": "%s:%s" % (server_ip, server_port),
                      "duration_sec": duration, "result": parsed}
    except Exception as e:
        return False, {"task_id": task_id, "error": str(e)[:160]}
    finally:
        try:
            shutil.rmtree(staging, ignore_errors=True)
        except Exception:
            pass


# ---------- net_probe（网关解析 + ping/TCP 探测，禁 raw socket） ----------


def _default_gateway():
    """route print -4 解析默认网关：取 0.0.0.0/0.0.0.0 活动路由（第 4 列为接口 IP
    以区分持久路由段），metric 最小者优先。语言无关（表头不解析）。"""
    try:
        r = subprocess.run(["route", "print", "-4"], capture_output=True,
                           timeout=10, creationflags=_NO_WINDOW)
    except Exception:
        return None
    best = None
    for ln in _decode_output(r.stdout).splitlines():
        parts = ln.split()
        if len(parts) < 5 or parts[0] != "0.0.0.0" or parts[1] != "0.0.0.0":
            continue
        gw, iface, metric = parts[2], parts[3], parts[4]
        if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", gw) or not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", iface):
            continue  # 接口列不是 IP → 持久路由段行
        try:
            m = int(metric)
        except ValueError:
            continue
        if best is None or m < best[1]:
            best = (gw, m)
    return best[0] if best else None


def _ping(host, timeout_ms=2000):
    """系统 ping -n 1 -w 2000（禁 raw ICMP socket，需管理员）。
    中文/英文输出均解析：time=/时间=/平均 = x ms；time<1ms。"""
    try:
        r = subprocess.run(["ping", "-n", "1", "-w", str(timeout_ms), host],
                           capture_output=True, timeout=timeout_ms / 1000.0 + 3,
                           creationflags=_NO_WINDOW)
        out = _decode_output(r.stdout) + _decode_output(r.stderr)
        m = re.search(r"(?:time|时间|平均)\s*[=<]\s*(\d+)\s*ms", out, re.IGNORECASE)
        if m:
            return {"host": host, "method": "ping", "ok": True,
                    "latency_ms": int(m.group(1)), "loss_pct": 0.0}
        return {"host": host, "method": "ping", "ok": False, "latency_ms": None,
                "loss_pct": 100.0, "error": "timeout_or_unreachable"}
    except Exception as e:
        return {"host": host, "method": "ping", "ok": False, "latency_ms": None,
                "loss_pct": 100.0, "error": str(e)[:80]}


def _tcp_probe(host, port, timeout=2.0):
    t0 = time.time()
    try:
        s = socket.create_connection((host, int(port)), timeout=timeout)
        try:
            s.close()
        except Exception:
            pass
        return {"host": host, "port": int(port), "method": "tcp", "ok": True,
                "latency_ms": round((time.time() - t0) * 1000.0, 1), "loss_pct": 0.0}
    except Exception as e:
        return {"host": host, "port": int(port), "method": "tcp", "ok": False,
                "latency_ms": None, "loss_pct": 100.0, "error": str(e)[:80]}


@command_handler("net_probe")
def _cmd_net_probe(args):
    """args={task_id, targets:[{host, method ping|tcp, port}]}；
    host=="_gateway" 解析为本地默认网关（ADR-015 约定）。"""
    task_id = args.get("task_id")
    targets = args.get("targets") or []
    if not isinstance(targets, list) or not targets:
        return False, {"task_id": task_id, "error": "net_probe_targets_missing"}
    results = []
    for t in targets:
        if not isinstance(t, dict):
            continue
        host = (t.get("host") or "").strip()
        method = (t.get("method") or "ping").strip().lower()
        if host == "_gateway":
            gw = _default_gateway()
            if not gw:
                results.append({"host": "_gateway", "method": method, "ok": False,
                                "latency_ms": None, "loss_pct": 100.0,
                                "error": "gateway_not_found"})
                continue
            host = gw
        if method == "tcp":
            port = t.get("port") or 80
            results.append(_tcp_probe(host, port))
        else:
            results.append(_ping(host))
    return True, {"task_id": task_id, "results": results,
                  "gateway": _default_gateway()}


# ---------- collect_logs（v1 hook：ADR-019 结构已冻结，日志引擎待接入） ----------


@command_handler("collect_logs")
def _cmd_collect_logs(args):
    """v1 暂不支持执行：日志收集引擎由 log-inspector 子系统接管后替换本 handler。
    红线：args.ftp 凭据用完即弃，禁止落盘/打日志/回显——此处不读取不保留。"""
    return False, {"task_id": args.get("task_id"),
                   "error": "collect_logs_not_implemented_v1"}


# ---------- ai_context（AI 暂缓，固定禁用回执） ----------


@command_handler("ai_context")
def _cmd_ai_context(args):
    return False, {"task_id": args.get("task_id"), "error": "ai_disabled"}


# ============================================================
# 命令分发（幂等 + 不阻塞心跳 + 回执重试）
# ============================================================


def _dispatch(tid, cmd):
    """单命令处理：幂等检查 → 处理器（异常隔离）→ 回执。在独立线程执行。"""
    if not isinstance(cmd, dict):
        return
    cid = str(cmd.get("id") or "")
    ctype = str(cmd.get("command") or "")
    args = cmd.get("args") if isinstance(cmd.get("args"), dict) else {}
    if not cid:
        return
    with _lock:
        if cid in _state["executed"]:
            return  # 幂等：本地已执行（服务端单次下发，双保险防重复回执）
    handler = COMMAND_HANDLERS.get(ctype)
    if handler is None:
        # 未知类型：ok=false（ADR-015；测试载荷用良性探测串，禁攻击样式串 ADR-018）
        ok, data = False, {"error": "unknown_command_type: %s" % (ctype or "empty")}
    else:
        try:
            ok, data = handler(args)
            if not isinstance(data, dict):
                data = {"value": data}
        except Exception as e:
            ok, data = False, {"error": str(e)[:160]}
    _post_result(tid, cid, ok, data)
    with _lock:
        _state["executed"].add(cid)
        if len(_state["executed"]) > 500:
            _state["executed"] = set(list(_state["executed"])[-400:])


def _post_result(tid, cid, ok, data):
    payload = {"ok": bool(ok), "data": data or {}}
    for attempt in range(_RESULT_RETRIES):
        code, _resp = _post("/api/v1/terminals/%s/commands/%s/result" % (tid, cid), payload)
        if 200 <= code < 300:
            return True
        if code == 409:
            return False  # 重复回执/未知命令：服务端已终态
        time.sleep(1.0)
    return False


def _spawn_dispatch(tid, cmd):
    th = threading.Thread(target=_dispatch, args=(tid, cmd),
                          daemon=True, name="perf-uplink-cmd")
    th.start()


# ============================================================
# 注册 / 心跳 / 指标（单拍动作）
# ============================================================


def register_once():
    """立即注册（幂等，重复注册即更新）。返回 (ok, error)。"""
    cfg = load_config()
    tid = _terminal_id(cfg)
    code, resp = _post("/api/v1/terminals/register", _register_payload(cfg))
    ok = 200 <= code < 300 and resp.get("ok") is not False
    _set_state(terminal_id=tid, registered=ok, last_ok=ok,
               last_hb_ts=(time.time() if ok else _state["last_hb_ts"]),
               last_error=(None if ok else "register_http_%s: %s" % (code, (resp or {}).get("error", ""))))
    return ok, (None if ok else _state["last_error"])


def _heartbeat_once():
    """一次心跳：解析 commands → 分发。返回 (ok, resp)（resp 供 loop 读取 interval 覆盖）。"""
    cfg = load_config()
    tid = _terminal_id(cfg)
    code, resp = _post("/api/v1/terminals/%s/heartbeat" % tid, {})
    if code == 401:
        _set_state(state="error", last_ok=False, last_error="auth_failed_check_token")
        return False, resp
    if not (200 <= code < 300):
        _set_state(state="error", last_ok=False,
                   last_error="heartbeat_http_%s: %s" % (code, (resp or {}).get("error", "")))
        return False, resp
    _set_state(state="connected", last_ok=True, last_hb_ts=time.time(), last_error=None)
    for cmd in (resp.get("commands") or []):
        _spawn_dispatch(tid, cmd)
    return True, resp


def _report_metrics(tid):
    payload = _metrics_payload(load_config(), tid)
    if payload is None:
        return False
    code, _resp = _post("/api/v1/terminals/%s/metrics" % tid, payload)
    return 200 <= code < 300


# ============================================================
# 心跳循环（daemon，失败指数退避，配置热更新）
# ============================================================


def _loop():
    backoff = 1
    last_metrics_ts = 0.0
    while True:
        cfg = load_config()
        if not (cfg.get("enabled") and cfg.get("server_url") and cfg.get("token")):
            _set_state(started=False, enabled=False, state="disabled")
            return  # 配置停用 → 线程退出（save 保存后由 start() 重启）
        tid = _terminal_id(cfg)
        _set_state(terminal_id=tid, enabled=True)
        delay = max(1, int(cfg.get("heartbeat_interval") or DEFAULT_HEARTBEAT_INTERVAL))

        if not _state.get("registered"):
            _set_state(state="connecting")
            code, _resp = _post("/api/v1/terminals/register", _register_payload(cfg))
            if 200 <= code < 300:
                _set_state(registered=True)
            else:
                _set_state(state="error", last_ok=False,
                           last_error="register_http_%s: %s" % (code, (_resp or {}).get("error", "")))
                if _stop.wait(delay * min(backoff, _BACKOFF_CAP)):
                    _set_state(started=False, state="disabled")
                    return
                _wakeup.clear()
                backoff = min(backoff * 2, _BACKOFF_CAP)
                continue

        hb_ok, hb_resp = _heartbeat_once()
        if hb_ok:
            backoff = 1
            # 指标上报节流：与心跳同拍（≥ 一个心跳间隔才报一次）
            now = time.time()
            if now - last_metrics_ts >= max(10, delay):
                if _report_metrics(tid):
                    last_metrics_ts = now
            # 服务端 interval 覆盖（ADR-004 heartbeat 响应 interval 字段）
            try:
                srv_interval = int(hb_resp.get("interval") or 0)
                if srv_interval >= 5:
                    delay = srv_interval
            except Exception:
                pass
        else:
            backoff = min(backoff * 2, _BACKOFF_CAP)
        if _stop.is_set():
            _set_state(started=False, state="disabled")
            return
        _wakeup.clear()
        _wakeup.wait(delay * backoff)
        if _stop.is_set():
            _set_state(started=False, state="disabled")
            return


def start():
    """启动心跳循环（幂等：旧循环线程仍存活则仅唤醒一拍）。"""
    th = _loop_thread.get("obj")
    if th is not None and th.is_alive():
        _wakeup.set()
        return
    _stop.clear()
    _wakeup.set()  # 立即执行第一拍
    _set_state(started=True, enabled=True, state="connecting", last_error=None)
    th = threading.Thread(target=_loop, daemon=True, name="perf-uplink")
    _loop_thread["obj"] = th
    th.start()


def stop():
    """停用心跳循环（保留配置，等待旧循环线程退出防竞态双循环）。"""
    _stop.set()
    _wakeup.set()
    th = _loop_thread.get("obj")
    if th is not None:
        th.join(timeout=3)
    _set_state(started=False, enabled=False, state="disabled")
    _stop.clear()  # 旧线程已退出，复位供下次 start


def autostart():
    """进程启动时按配置自动恢复（bridge import 后显式调用，失败静默不崩主进程）。"""
    try:
        cfg = load_config()
        if cfg.get("enabled") and cfg.get("server_url") and cfg.get("token"):
            start()
    except Exception:
        pass


# ============================================================
# handle_* 桥接约定（供 bridge.py 挂载 /api/perf/uplink/*）
# ============================================================


def handle_uplink_status(params=None):
    cfg = load_config()
    with _lock:
        st = dict(_state)
    return {
        "success": True,
        "uplink": {
            "enabled": bool(cfg.get("enabled")),
            "running": bool(st.get("started")),
            "server_url": cfg.get("server_url") or "",
            "terminal_id": st.get("terminal_id") or _terminal_id(cfg),
            "state": st.get("state") or "disabled",
            "registered": bool(st.get("registered")),
            "last_hb_ts": st.get("last_hb_ts") or 0,
            "last_error": st.get("last_error"),
            "has_token": bool(cfg.get("token")),
            "executed_count": len(st.get("executed") or ()),
            "iperf3_available": _iperf3_available(),
            "heartbeat_interval": int(cfg.get("heartbeat_interval") or DEFAULT_HEARTBEAT_INTERVAL),
            "client_version": CLIENT_VERSION,
        },
    }


def handle_uplink_save(params):
    """保存配置并应用启停。token 参数留空 = 不修改（状态接口永不回显 token）。"""
    server = (params.get("server_url") or params.get("server") or "").strip()
    token = (params.get("token") or "").strip()
    enabled_raw = str(params.get("enabled", "")).strip().lower()
    cfg = load_config()
    if server:
        if not (server.startswith("http://") or server.startswith("https://")):
            return {"success": False, "error": "server_url_must_start_with_http"}
        cfg["server_url"] = server.rstrip("/")
    if token:
        cfg["token"] = token
    if enabled_raw in ("1", "true", "on", "yes"):
        cfg["enabled"] = True
    elif enabled_raw in ("0", "false", "off", "no"):
        cfg["enabled"] = False
    if cfg.get("enabled") and (not cfg.get("server_url") or not cfg.get("token")):
        return {"success": False, "error": "missing_server_or_token"}
    try:
        save_config(cfg)
    except Exception as e:
        return {"success": False, "error": "save_failed: %s" % e}
    if cfg.get("enabled"):
        start()
    else:
        stop()
    return handle_uplink_status()


def handle_uplink_register(params=None):
    """手动立即注册：注册 + 一次完整心跳拍（循环未跑时也完成一拍上报）。"""
    cfg = load_config()
    if not cfg.get("server_url") or not cfg.get("token"):
        return {"success": False, "error": "missing_server_or_token"}
    ok, err = register_once()
    if not ok:
        return {"success": False, "error": err or "register_failed"}
    _heartbeat_once()
    with _lock:
        running = _state.get("started")
    if not running:
        # 循环未启用：补一次指标上报，形成完整一拍
        _report_metrics(_terminal_id(cfg))
    return handle_uplink_status()


UPLINK_ROUTES = {
    "/api/perf/uplink/status": handle_uplink_status,
    "/api/perf/uplink/save": handle_uplink_save,
    "/api/perf/uplink/register": handle_uplink_register,
}


if __name__ == "__main__":
    # 冒烟：python uplink.py [--gateway]
    import pprint
    if "--gateway" in sys.argv:
        print("default gateway:", _default_gateway())
        print("ping gateway:", _ping(_default_gateway() or "127.0.0.1"))
    else:
        pprint.pprint(handle_uplink_status())
        print("iperf3 bundled:", _iperf3_available())
