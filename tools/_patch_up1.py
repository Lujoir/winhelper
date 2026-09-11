# -*- coding: utf-8 -*-
"""一次性补丁 1：uplink.py 资产同步增强。
1) _hwinfo_asset 重构为 _hwinfo_from_asset(asset)，CIM 失败时 psutil/platform 兜底；
2) 新增 _asset_detail()（schema 1：os/cpu/memory/disks/gpu/network/temps）；
3) _register_payload 携带完整 asset，单次 hwinfo 采集复用。
执行后自删。"""
import io

p = "uplink.py"
s = io.open(p, encoding="utf-8").read()

start = s.index("def _hwinfo_asset():")
end = s.index("def _metrics_payload(")
new_block = '''def _hwinfo_from_asset(asset):
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
    """完整结构化资产明细（schema 1）：os/cpu/memory/disks/gpu/network/temps。
    注册时随 payload 上报，服务端存 terminals.asset_detail 供资产清单明细查看。"""
    detail = {"schema": 1, "ts": int(time.time())}
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


'''
s = s[:start] + new_block + s[end:]
io.open(p, "w", encoding="utf-8").write(s)
print("UPLINK_ASSET_PATCHED")
