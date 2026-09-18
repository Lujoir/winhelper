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

import hashlib
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time

import urllib.error
import urllib.request

# GUI（无控制台）程序中调用控制台子进程（ping/route/iperf3）必须隐藏窗口（ADR-013）
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

CLIENT_VERSION = "4.1.5"
TERMINAL_TYPE = "windows"
DEFAULT_HEARTBEAT_INTERVAL = 30          # 秒（ADR 可调）
_BACKOFF_CAP = 2                         # 失败退避倍数上限（30s*2=60s；4.1.3 main 批准 ADR-004 变更：联调期快速重连）
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
           "terminal_id": "", "heartbeat_interval": DEFAULT_HEARTBEAT_INTERVAL,
           "server_ca_fingerprint": ""}   # HTTPS 专项（2026-09-11）：CA 指纹下发值，旧配置缺字段兼容（空=跳过比对）
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
    "effective_interval": None,   # 实际生效心跳间隔（服务端下发覆盖后写入）
    "executed": set(),      # 命令 id 幂等集合（服务端单次下发不重发，内存集合足够）
}
_loop_thread = {"obj": None}
_stop = threading.Event()
_wakeup = threading.Event()   # 手动注册/配置变更提前唤醒心跳拍


def _effective_heartbeat_interval(cfg=None):
    """实际生效心跳间隔：服务端下发覆盖值 > 本地配置 > 默认（4.1.4 观察修正：
    此前自报一律读本地配置，实际心跳按服务端覆盖值跑，自报 30/实际 60
    双值来源不一致——统一取生效值，供 pc_diag/asset/status 远程核验）。"""
    with _lock:
        eff = _state.get("effective_interval")
    try:
        eff = int(eff) if eff else 0
    except (TypeError, ValueError):
        eff = 0
    if eff >= 5:
        return eff
    if not isinstance(cfg, dict):
        cfg = load_config()
    try:
        v = int(cfg.get("heartbeat_interval") or 0)
    except (TypeError, ValueError):
        v = 0
    return v if v >= 5 else DEFAULT_HEARTBEAT_INTERVAL


def _ulog(msg):
    """uplink 状态迁移日志（4.1.3 缺陷 F）：写 power-control 同目录 ul_*.log，
    供 pc_diag 取数（此前 uplink 零日志=排障黑盒）。失败静默不影响心跳。"""
    try:
        d = os.path.join(_data_dir(), "power-control", "logs")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, time.strftime("ul_%Y%m%d.log"))
        with open(p, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


_UL_KEYS = ("state", "last_error", "registered", "enabled", "started",
            "terminal_id")   # 噪声键（last_hb_ts/last_ok）不入日志


def _set_state(**kw):
    changed = []
    with _lock:
        for k, v in kw.items():
            old = _state.get(k)
            _state[k] = v
            if k in _UL_KEYS and old != v:
                changed.append("%s: %s -> %s" % (k, old, v))
    if changed:
        _ulog("state " + "; ".join(changed))


# ============================================================
# HTTPS 传输层（2026-09-11 专项：与 net_service 同构复用——自建 CA +
# TLSv1.2+ + 指纹双层校验；CA 资产共用 assets/platform_ca.pem；
# 真实链路待服务端就绪联调，本层单测与本地自签冒烟覆盖）
# ============================================================

UPLINK_CA_ENV = "NETDOCTOR_CA_PATH"   # CA 定位环境变量（与 net_service 共用，单测/冒烟注入优先）


def _uplink_ca_path():
    """内置 CA pem 定位：环境变量（单测注入）→ PyInstaller _MEIPASS 资源 → 脚本目录 assets。
    全部缺失返回空串（https 调用将 fail-closed 报 ca_missing，http 不受影响）。"""
    env = os.environ.get(UPLINK_CA_ENV)
    if env and os.path.isfile(env):
        return env
    cands = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        cands.append(os.path.join(base, "assets", "platform_ca.pem"))
        cands.append(os.path.join(base, "platform_ca.pem"))
    cands.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "assets", "platform_ca.pem"))
    for c in cands:
        if os.path.isfile(c):
            return c
    return ""


def _builtin_ca_fingerprint(ca_path=None):
    """内置 CA 证书 SHA256 指纹（小写连续 hex）；不可得返回空串。"""
    path = ca_path or _uplink_ca_path()
    if not path:
        return ""
    try:
        with open(path, "rb") as f:
            der = ssl.PEM_cert_to_DER_cert(f.read().decode("ascii"))
        return hashlib.sha256(der).hexdigest()
    except Exception:
        return ""


def _uplink_ssl_context(cfg):
    """https 平台调用的 SSL 上下文：TLSv1.2+ + 内置 CA 验签（CERT_REQUIRED）。
    指纹双层校验第一层：内置 CA 计算指纹 vs uplink_config.server_ca_fingerprint（下发值
    非空时强制比对，不一致拒绝连接）。IP 自签场景 check_hostname=False，身份由
    CA 链 + 指纹双层保证。返回 (context, err)；err 非空时调用方拒绝连接。"""
    fp_cfg = str(cfg.get("server_ca_fingerprint") or "").strip().lower().replace(":", "")
    ca_path = _uplink_ca_path()
    if not ca_path:
        return None, "ca_missing"
    if fp_cfg:
        fp_local = _builtin_ca_fingerprint(ca_path)
        if not fp_local:
            return None, "ca_error: 内置 CA 不可解析"
        if fp_local != fp_cfg:
            return None, "ca_fingerprint_mismatch"
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_verify_locations(ca_path)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx, ""
    except Exception as e:
        return None, "ca_error: %s" % e


# ============================================================
# HTTP（纯标准库 urllib；https:// 走上节 SSL context，http:// 过渡兼容直连）
# ============================================================


def _post(path, payload, timeout=15):
    """POST JSON 到服务端。返回 (http_status:int, body:dict)。
    网络异常 → (-1, {"error": ...})；HTTP 4xx/5xx → 解析 error 字段。
    https:// 时构造 SSL context（TLSv1.2+ + 内置 CA 验签 + 指纹双层校验第一层），
    校验失败 fail-closed 拒绝连接；心跳行为（周期/重试语义）不受影响。
    红线：任何异常/日志不得携带 token。"""
    cfg = load_config()
    server = (cfg.get("server_url") or "").rstrip("/")
    if not server:
        return -1, {"error": "uplink_server_missing"}
    ctx = None
    if server.startswith("https://"):
        ctx, err = _uplink_ssl_context(cfg)
        if err:
            return -1, {"error": err}
    url = server + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-ETP-Token", cfg.get("token") or "")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
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


def _hwinfo_from_asset(asset):
    """从完整资产明细提取注册摘要（服务端 terminals 表列），CIM 失败时兜底。"""
    cpu = asset.get("cpu") or {}
    mem = asset.get("memory") or {}
    try:
        disk_total = sum(d.get("size") or 0 for d in (asset.get("disks") or []))
    except Exception:
        disk_total = 0
    gpus = [g.get("name") for g in (asset.get("gpu") or []) if g.get("name")]
    return {
        "cpu_model": cpu.get("name") or None,
        "cpu_cores": cpu.get("logical") or cpu.get("cores"),
        "mem_total_mb": (round(mem["total"] / 1048576.0) if mem.get("total") else None),
        "disk_total_gb": (round(disk_total / 1073741824.0, 1) if disk_total else None),
        "gpu_info": (" + ".join(gpus) if gpus else None),
        "os_arch": _os_arch(),
    }


def _hwinfo_fallback(asset):
    """CIM 采集失败时的基础兜底（2026-09-08：首次注册资产空白修复）。"""
    if not asset.get("cpu", {}).get("name"):
        try:
            import platform
            p = platform.processor()
            asset.setdefault("cpu", {})["name"] = p or platform.machine() or None
        except Exception:
            pass
    if not asset.get("memory", {}).get("total"):
        try:
            import psutil
            asset.setdefault("memory", {})["total"] = psutil.virtual_memory().total
        except Exception:
            pass
    return asset


def _asset_detail():
    """完整结构化资产明细（schema 2，2026-09-16 P1 顺带：新增 machine 块——
    厂商/型号/系列复用 power-control 引擎采集，消除控制台对 power_snapshots
    的跨模块依赖；schema 1 为 os/cpu/memory/disks/gpu/network/temps，
    服务端与控制台对缺失块兜底）。注册时随 payload 上报，服务端存
    terminals.asset_detail 供资产清单明细查看。"""
    detail = {"schema": 2, "ts": int(time.time())}
    try:
        from power_control import collect_machine
        detail["machine"] = collect_machine()
    except Exception:
        detail["machine"] = {}
    try:
        from perf_service import handle_perf_hwinfo
        hw = (handle_perf_hwinfo({}).get("hwinfo") or {})
    except Exception:
        hw = {}
    if hw:
        detail["os"] = hw.get("os") or {}
        detail["hostname"] = hw.get("hostname")
        detail["cpu"] = hw.get("cpu") or {}
        detail["memory"] = hw.get("memory") or {}
        detail["disks"] = hw.get("disks") or []
        detail["gpu"] = hw.get("gpu") or []
    try:
        from home_service import handle_home_network
        nr = handle_home_network()
        if nr.get("success"):
            detail["network"] = nr.get("adapters") or []
        else:
            detail["network"] = []
    except Exception:
        detail["network"] = []
    try:
        from perf_service import _temps_cache
        detail["temps"] = {"cpu": _temps_cache.get("cpu_temp"),
                           "gpu": _temps_cache.get("gpu_temp")}
    except Exception:
        detail["temps"] = {}
    return detail


def _register_payload(cfg):
    tid = _terminal_id(cfg)
    try:
        hostname = socket.gethostname() or None
    except Exception:
        hostname = None
    asset = _hwinfo_fallback(_asset_detail())
    # 心跳间隔生效值（4.1.4：服务端下发覆盖优先，自报=实际生效口径）
    asset["heartbeat_interval"] = _effective_heartbeat_interval(cfg)
    payload = {
        "terminal_id": tid,
        "terminal_type": TERMINAL_TYPE,
        "hostname": hostname or asset.get("hostname"),
        "client_version": CLIENT_VERSION,
        "hwinfo": _hwinfo_from_asset(asset),
        "asset": asset,
    }
    os_info = (asset.get("os") or {}).get("text")
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


# ---------- pc_apply_policy（自动开关机平台下发执行面，ADR-038 侧协议；main 定稿） ----------


@command_handler("pc_apply_policy")
def _cmd_pc_apply_policy(args):
    """args={policy_id, op:"apply", boot?:{enabled,mode daily|weekly|single|disabled,
    time "HH:MM", weekdays?:[7x0/1 周一~周日], date?}, shutdown?:{enabled,mode,time,date}}
    （协议 main 定稿，server-platform 同款）。执行链复用 power-control P1a/P1b
    引擎（写前快照→写入→回读校验；schtasks EyeTermAutoShutdown）。无人值守提权
    （UAC）限制如实回执（power-control ADR-009），成功后落 policy_state 供
    「自动开关机」页展示当前生效策略。"""
    from power_control import apply_policy   # 延迟导入（引擎零第三方依赖）
    try:
        data = apply_policy(args or {})
    except Exception as e:
        return False, {"policy_id": (args or {}).get("policy_id"),
                       "op": "apply", "ok": False,
                       "steps": {}, "error": str(e)[:200]}
    return bool(data.get("ok")), data


# ---------- power_action / power_action_abort（中心发起重启/关机；2026-09-18 main 定稿）----------


@command_handler("power_action")
def _cmd_power_action(args):
    """args={action:"shutdown"|"restart", delay_sec?:uint(≤3600，缺省 60),
    force?:bool(缺省 True 对齐参考脚本 -f)}。

    管控语义：仅中心可发起（本地 UI 无任何立即关机/重启入口）；终端侧仅
    倒计时知会弹窗、不提供本地取消；撤销走 power_action_abort；delay 上限
    3600；执行前后写本地审计日志（服务端回执 cid 审计双存档）。
    高危红线：命令串仅由受控白名单参数构造（power_action.py）。"""
    import power_action   # 延迟导入（零第三方依赖）
    return power_action.handle_power_action(args or {})


@command_handler("power_action_abort")
def _cmd_power_action_abort(args):
    """中心撤销 pending 关机/重启（shutdown /a）；无 pending 如实回执。"""
    import power_action   # 延迟导入
    return power_action.handle_power_action_abort(args or {})


# ---------- pc_diag（自动开关机只读诊断，ADR-006 定案工具；2026-09-17）----------


@command_handler("pc_diag")
def _cmd_pc_diag(args):
    """只读诊断：SaveBiosSetting 类存在性 / PasswordState / RTC 读回 /
    最近一次 BIOS apply 完整结果（attempts 含写入 rv 与 __commit__ note）/
    日志尾 200 行 / client_version。纯只读零写入（power-control ADR-006 定案）。"""
    from power_control import collect_diag   # 延迟导入
    try:
        data = collect_diag()
        data["client_version"] = CLIENT_VERSION
        # 生效间隔在锁外取值（helper 内部取 _lock，Lock 不可重入，锁内调用=死锁）
        eff_interval = _effective_heartbeat_interval()
        with _lock:
            data["uplink"] = {
                "state": _state.get("state"),
                "registered": _state.get("registered"),
                "last_error": _state.get("last_error"),
                "last_hb_ts": _state.get("last_hb_ts"),
                "heartbeat_interval": eff_interval,
            }
        return True, data
    except Exception as e:
        return False, {"error": str(e)[:200]}


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
        # 4.1.3 缺陷 B：401=token 失效或终端已被服务端删除 → registered 复位，
        # 下轮循环重走 register（原实现 registered 永不复位 → 心跳 401 卡死）
        with _lock:
            _state["registered"] = False
        _set_state(state="error", last_ok=False, last_error="auth_failed_check_token")
        return False, resp
    if code == 404:
        # 缺陷 B 同源：终端不存在（服务端丢库）→ 重注册
        with _lock:
            _state["registered"] = False
        _set_state(state="error", last_ok=False,
                   last_error="heartbeat_404_terminal_gone")
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
        _set_state(effective_interval=delay)

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
                    _set_state(effective_interval=srv_interval)
            except Exception:
                pass
            # 自动更新（三大改造③，ADR-012）：心跳响应 latest_version →
            # 版本比对 + 清单下载（后台线程；状态经 /api/app/update-status 出提示条）。
            # 4.1.3 候选修复（缺陷 C）：下载失败终态（failed）后原实现不再重触发
            # ——现改为 failed 状态即重新武装（5 分钟节流防风暴）。
            try:
                lv = hb_resp.get("latest_version")
                retry_needed = False
                try:
                    import updater as _upd
                    if _upd.load_state().get("status") == "failed":
                        retry_needed = (time.time() -
                                        float(_upd.load_state().get("ts") or 0)
                                        ) >= 300
                except Exception:
                    pass
                if lv and (retry_needed or
                           lv != _state.get("_last_seen_version")):
                    with _lock:
                        _state["_last_seen_version"] = str(lv)
                    import updater as _upd
                    _upd.check_async(CLIENT_VERSION,
                                     cfg.get("server_url") or "",
                                     cfg.get("token") or "")
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
            "version": CLIENT_VERSION,
            "executed_count": len(st.get("executed") or ()),
            "iperf3_available": _iperf3_available(),
            "heartbeat_interval": _effective_heartbeat_interval(cfg),
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
