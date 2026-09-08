# -*- coding: utf-8 -*-
"""主页 · 本地网络配置采集（观枢终端平台｜EyeTerm）。

设计（2026-09-08，主页菜单需求）：
- 独立模块：不触碰 perf_service/disk_cleanup/log_service 等子项目契约文件；
- 数据源：优先 PowerShell Get-NetAdapter/Get-NetIPConfiguration 一次采集
  （网卡名/描述/状态/MAC/速率/IPv4/掩码/网关/DNS），psutil 降级兜底（无网关/DNS）；
- 缓存 60s：PowerShell 冷启动 1-3s，避免主页频繁刷新重复付出；
- 红线：GUI 无控制台程序子进程必须 CREATE_NO_WINDOW（2026-09-08 循环弹窗教训）。
"""

import json
import subprocess
import threading
import time

try:
    import psutil
except ImportError:
    psutil = None

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_CACHE_TTL = 60.0
_ps_cache = {"ts": 0.0, "data": None}
_cache_lock = threading.Lock()

_PS_SCRIPT = (
    "$ErrorActionPreference='SilentlyContinue';"
    "$out=@();"
    "Get-NetAdapter | Sort-Object -Property Status -Descending | ForEach-Object {"
    " $c=$_ | Get-NetIPConfiguration;"
    " $dns=@();"
    " if ($c.DNSServer) { $dns=@($c.DNSServer | Where-Object { $_ -and $_.AddressFamily -eq 2 } | ForEach-Object { $_.ServerAddress } | Where-Object { $_ }) }"
    " $gw=$null;"
    " if ($c.IPv4DefaultGateway) { $gw=(@($c.IPv4DefaultGateway)[0]).NextHop }"
    " $ipv4=@(); $plen=@();"
    " if ($c.IPv4Address) { $ipv4=@($c.IPv4Address | ForEach-Object IPAddress);"
    "   $plen=@($c.IPv4Address | ForEach-Object PrefixLength) }"
    " $out += [PSCustomObject]@{ name=$_.Name; desc=$_.InterfaceDescription;"
    "  status=$_.Status; mac=$_.MacAddress; speed=[string]$_.LinkSpeed;"
    "  ipv4=$ipv4; plen=$plen; gw=$gw; dns=$dns }"
    "};"
    "$out | ConvertTo-Json -Depth 3"
)


def _collect_powershell():
    """PowerShell 采集：失败返回 None（调用方降级 psutil）。"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_SCRIPT],
            capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
        if r.returncode != 0 or not r.stdout or not r.stdout.strip():
            return None
        data = json.loads(r.stdout)
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return None
        adapters = []
        for a in data:
            if not isinstance(a, dict):
                continue
            adapters.append({
                "name": str(a.get("name") or "--"),
                "desc": str(a.get("desc") or "--"),
                "status": str(a.get("status") or "--"),
                "mac": str(a.get("mac") or "--"),
                "speed": str(a.get("speed") or "--"),
                "ipv4": [str(x) for x in (a.get("ipv4") or [])],
                "plen": [int(x) for x in (a.get("plen") or []) if str(x).strip().lstrip("-").isdigit()],
                "gw": a.get("gw") or None,
                "dns": [str(x) for x in (a.get("dns") or [])
                        if x not in (None, "", "None")],
            })
        return adapters or None
    except Exception:
        return None


def _collect_psutil():
    """psutil 降级：基础网卡信息（IP/掩码/MAC/状态，无网关/DNS）。"""
    if psutil is None:
        return None
    try:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
    except Exception:
        return None
    adapters = []
    for name, ifs in addrs.items():
        st = stats.get(name)
        entry = {
            "name": name, "desc": "--",
            "status": ("Up" if (st and st.isup) else "Down"),
            "mac": "--",
            "speed": (("%.0f Mbps" % st.speed) if (st and st.speed and st.speed > 0) else "--"),
            "ipv4": [], "plen": [], "gw": None, "dns": [],
        }
        for a in ifs:
            fam = str(getattr(a, "family", ""))
            if "AF_INET" in fam:
                entry["ipv4"].append(a.address)
                if getattr(a, "netmask", None):
                    try:
                        plen = sum(bin(int(x)).count("1")
                                   for x in str(a.netmask).split("."))
                        if 0 <= plen <= 32:
                            entry["plen"].append(plen)
                    except Exception:
                        pass
            elif "AF_LINK" in fam and a.address:
                entry["mac"] = a.address
        adapters.append(entry)
    return adapters or None


def handle_home_network(params=None):
    """本地网络配置列表（主页「本地网络配置」卡）。

    返回 {"success", "source": powershell|psutil, "adapters": [...]}。
    """
    now = time.time()
    with _cache_lock:
        if _ps_cache["data"] is not None and now - _ps_cache["ts"] < _CACHE_TTL:
            return {"success": True, "source": "cache",
                    "adapters": _ps_cache["data"]}
    adapters = _collect_powershell()
    source = "powershell"
    if adapters is None:
        adapters = _collect_psutil()
        source = "psutil"
    if adapters is None:
        return {"success": False, "error": "无法获取网络配置（PowerShell 与 psutil 均不可用）"}
    with _cache_lock:
        _ps_cache["data"] = adapters
        _ps_cache["ts"] = now
    return {"success": True, "source": source, "adapters": adapters}
