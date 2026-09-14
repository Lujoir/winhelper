# -*- coding: utf-8 -*-
"""
file_search.py — EyeTerm「文件检索」服务层
===========================================

架构参照 net_service（即时返回 + ROUTES dict + 后台编排）：
  1. Everything HTTP Server 客户端（127.0.0.1:5700 /?search=x&json=1）
  2. eyeterm 独立实例启动编排（-instance eyeterm + 数据目录 ini/database，失败给明确指引）
  3. 第三方实例 HTTP 探测复用

零新增 pip 依赖；一切子进程 CREATE_NO_WINDOW。
"""

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

FS_HTTP_BASE = "http://127.0.0.1:5700"
FS_MAX_RESULTS = 200
FS_START_TIMEOUT = 25.0   # 拉起后探测窗口（秒）

# exe 定位链：环境变量 → 配置文件（%LOCALAPPDATA%/winhelper/filesearch_config.json）
# → 常见便携路径探测
_FS_COMMON_DIRS = [
    r"F:\Program Files\Everything-1.4.1.969.x64",
    r"C:\Program Files\Everything",
    r"C:\Program Files\Everything 1.4.1.969.x64",
    r"C:\Program Files (x86)\Everything",
    r"D:\Program Files\Everything-1.4.1.969.x64",
]


def _config_dir():
    base = os.environ.get("FILESEARCH_CONFIG_DIR") or os.environ.get("LOCALAPPDATA") \
        or os.environ.get("TEMP") or os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(base, "" if os.environ.get("FILESEARCH_CONFIG_DIR") else "winhelper")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = os.environ.get("TEMP") or os.getcwd()
    return d


def _config_path():
    return os.path.join(_config_dir(), "filesearch_config.json")


def _load_config():
    try:
        with open(_config_path(), "r", encoding="utf-8") as f:
            saved = json.load(f)
        return saved if isinstance(saved, dict) else {}
    except Exception:
        return {}


def _save_config(cfg):
    try:
        with open(_config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def _data_dir():
    d = os.path.join(_config_dir(), "filesearch")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = _config_dir()
    return d


# ============================================================
# Everything exe 定位链
# ============================================================

def _env_exe():
    p = os.environ.get("EYETERM_EVERYTHING_EXE")
    return p if p and os.path.isfile(p) else ""


def _config_exe():
    p = str(_load_config().get("everything_path") or "")
    return p if p and os.path.isfile(p) else ""


def _probe_common_exe():
    for d in _FS_COMMON_DIRS:
        p = os.path.join(d, "Everything.exe")
        if os.path.isfile(p):
            return p
    return ""


def _registry_exe():
    """{app} 探测：installer 登记的 HKLM/HKCU Software\\EyeTerm\\EverythingPath（定位链第三级，
    2026-09-14 随安装包服务模式落地新增）。"""
    try:
        import winreg
    except Exception:
        return ""
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, r"Software\EyeTerm") as k:
                p = str(winreg.QueryValueEx(k, "EverythingPath")[0] or "").strip()
                if p and os.path.isfile(p):
                    return p
        except Exception:
            continue
    return ""


def locate_everything_exe():
    """返回 Everything.exe 绝对路径或空串（定位链：env → 配置 → 注册表{app} → 常见路径）。"""
    return _env_exe() or _config_exe() or _registry_exe() or _probe_common_exe()


# ============================================================
# HTTP 探测 / 查询
# ============================================================

def _fs_get(query, timeout=6):
    """Everything HTTP API GET；返回 (status, obj)。连接失败返回 (-1, {"error": ...})。"""
    url = FS_HTTP_BASE + "/?search=" + urllib.parse.quote(query) \
        + "&json=1&count=%d&path_column=1&size_column=1&date_modified_column=1" % FS_MAX_RESULTS
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {}
    except Exception as e:
        return -1, {"error": str(e) or type(e).__name__}


def _norm_results(obj):
    """防御性解析 Everything JSON（1.4 形状：totalResults/results[{name,path,size,
    date_modified}]）；字段缺失容错。"""
    if not isinstance(obj, dict):
        return []
    out = []
    for r in (obj.get("results") or [])[:FS_MAX_RESULTS]:
        if not isinstance(r, dict):
            continue
        name = str(r.get("name") or "")
        path = str(r.get("path") or "")
        size = r.get("size")
        try:
            size = int(size) if size is not None else None
        except (TypeError, ValueError):
            size = None
        dm = r.get("date_modified")
        try:
            dm = float(dm) if dm is not None else None
        except (TypeError, ValueError):
            dm = None
        out.append({"name": name, "path": path, "size": size, "date_modified": dm})
    return out


def http_available():
    """Everything HTTP Server 探测（轻量 health 式查询）。"""
    code, obj = _fs_get("", timeout=2)
    return code == 200 and isinstance(obj, dict)


# ============================================================
# eyeterm 独立实例启动编排
# ============================================================

_FS_INI_LINES = [
    "[Everything]",
    "http_server_enabled=1",
    "http_server_bindings=127.0.0.1",
    "http_server_port=5700",
    "http_server_download=0",
    "http_server_username=",
    "http_server_password=",
]


def _instance_paths(exe_path):
    """eyeterm 实例 ini/database 置于 EyeTerm 数据目录（不写用户 Everything 目录）。"""
    d = _data_dir()
    return os.path.join(d, "Everything-eyeterm.ini"), os.path.join(d, "Everything-eyeterm.db")


def _write_instance_ini(ini_path):
    try:
        with open(ini_path, "w", encoding="utf-16") as f:
            f.write("\r\n".join(_FS_INI_LINES))
        return True
    except Exception:
        return False


def start_everything():
    """拉起 eyeterm 独立实例；返回 {success, error?, hint?, pid?}。
    权限不足（读 MFT 需管理员）时 HTTP 可能不起——如实返回指引，不吞错。"""
    exe = locate_everything_exe()
    if not exe:
        return {"success": False, "error": "everything_not_found",
                "hint": "未找到 Everything.exe：请在设置中配置路径，或安装 Everything 1.4 x64"}
    ini_path, db_path = _instance_paths(exe)
    if not _write_instance_ini(ini_path):
        return {"success": False, "error": "ini_write_failed",
                "hint": "实例配置写入失败（数据目录不可写）"}
    try:
        proc = subprocess.Popen(
            [exe, "-instance", "eyeterm", "-config", ini_path, "-database", db_path,
             "-startup"],
            creationflags=_NO_WINDOW, cwd=os.path.dirname(exe))
    except Exception as e:
        return {"success": False, "error": "start_failed: %s" % e,
                "hint": "启动失败，请以管理员运行 EyeTerm 或手动启动 Everything 并开启 HTTP 服务器"}
    deadline = time.time() + FS_START_TIMEOUT
    while time.time() < deadline:
        if http_available():
            return {"success": True, "pid": proc.pid, "reused": False}
        if proc.poll() is not None:
            return {"success": False, "error": "exited_early",
                    "hint": "Everything 启动后立即退出（检查实例冲突或权限）"}
        time.sleep(0.5)
    return {"success": False, "error": "http_not_listening",
            "hint": "Everything 已启动但 HTTP 服务器未就绪：读 MFT 需管理员权限，"
                    "请以管理员运行 EyeTerm 后重试，或在 Everything 选项中手动开启 HTTP 服务器（127.0.0.1:5700）"}


def ensure_running():
    """查询前保障：HTTP 可用→复用；不可用→拉起 eyeterm 实例。返回 {available, ...}"""
    if http_available():
        return {"available": True, "reused": True}
    r = start_everything()
    return {"available": bool(r.get("success")), "detail": r}


# ============================================================
# ROUTES handlers（即时返回）
# ============================================================

def handle_fs_query(params=None):
    """文件检索：params {q, count?}。未运行时自动拉起（eyeterm 实例），失败给指引。"""
    q = str((params or {}).get("q") or "").strip()
    if not q:
        return {"success": False, "error": "empty_query"}
    st = ensure_running()
    if not st.get("available"):
        d = st.get("detail") or {}
        return {"success": False, "error": d.get("error") or "everything_unavailable",
                "hint": d.get("hint") or "Everything HTTP 服务器不可用"}
    count = 50
    try:
        count = max(10, min(FS_MAX_RESULTS, int((params or {}).get("count") or 50)))
    except (TypeError, ValueError):
        count = 50
    code, obj = _fs_get(q, timeout=6)
    if code != 200 or not isinstance(obj, dict):
        return {"success": False, "error": "everything_http_%s" % code,
                "hint": "Everything 查询失败，请检查其 HTTP 服务器状态"}
    results = _norm_results(obj)
    return {"success": True, "q": q, "total": obj.get("totalResults"),
            "count": len(results), "results": results}


def handle_fs_status(params=None):
    """状态：HTTP 探测 + exe 定位结果（设置/指引依据）。"""
    exe = locate_everything_exe()
    return {"success": True, "http_available": http_available(), "exe_found": bool(exe),
            "exe_path": exe or "", "config_path": _config_path()}


def handle_fs_save_path(params=None):
    """保存 Everything.exe 路径到配置（设置页/指引使用）。"""
    p = str((params or {}).get("path") or "").strip()
    if not p or not os.path.isfile(p):
        return {"success": False, "error": "invalid_path"}
    cfg = _load_config()
    cfg["everything_path"] = p
    return {"success": _save_config(cfg)}


def handle_fs_open_location(params=None):
    """打开位置：explorer /select（与 /api/disk/open-location 同模式）。"""
    path = str((params or {}).get("path") or "").strip()
    if not path or not os.path.exists(path):
        return {"success": False, "error": "path_not_found"}
    try:
        subprocess.Popen(["explorer", "/select,", path], creationflags=_NO_WINDOW)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


FS_ROUTES = {
    "/api/filesearch/query": handle_fs_query,
    "/api/filesearch/status": handle_fs_status,
    "/api/filesearch/save-path": handle_fs_save_path,
    "/api/filesearch/open-location": handle_fs_open_location,
}


if __name__ == "__main__":
    print(json.dumps(handle_fs_status({}), ensure_ascii=False, indent=2))
