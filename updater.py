# -*- coding: utf-8 -*-
"""EyeTerm 自动更新引擎（三大改造③，2026-09-17；ADR-012）。

链路：心跳响应携带 latest_version（server-platform 并行交付）→ 版本比对
（三段数字 semver）→ 新版本时拉取更新清单 → 下载安装包到
%PROGRAMDATA%\\EyeTerm\\update\\（sha256 校验、失败重试 3 次）→ 状态落档
（UI 经 bridge /api/app/update-status 轮询出提示条）→「立即更新」启动
updater 子进程（--et-updater：等待主进程退出 → 运行安装包 /SILENT
/SUPPRESSMSGBOXES /NORESTART → 安装器自启新客户端）→ 主进程自行退出。

清单端点形状（server-platform 定稿前按此实现，联调时对齐——TBC-002）：
    GET {server}/api/v1/client/update-manifest?version=<当前版本>
    → {"ok":true,"manifest":{"latest_version","download_url","sha256"}}（sha256 空=跳过校验）
状态文件 update_state.json：{status: idle|pending|downloading|ready|failed, ...}
旧版本安装包保留在 update 目录（覆盖升级由安装器自然处理；预置配置随文件名继承）。
"""

import ctypes
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import urllib.request

UPDATE_DIR_NAME = os.path.join("EyeTerm", "update")
_MAX_DL_BYTES = 512 * 1024 * 1024
_RETRY = 3
_INSTALL_TIMEOUT = 1800


def update_dir():
    base = os.environ.get("PROGRAMDATA") or os.environ.get("LOCALAPPDATA") \
        or os.environ.get("TEMP") or os.getcwd()
    d = os.path.join(base, UPDATE_DIR_NAME)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def _state_path():
    return os.path.join(update_dir(), "update_state.json")


def load_state():
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            st = json.load(f)
        if isinstance(st, dict):
            return st
    except Exception:
        pass
    return {"status": "idle"}


def _save_state(**kw):
    st = load_state()
    st.update(kw)
    st["ts"] = int(time.time())
    tmp = _state_path() + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, _state_path())
    except OSError:
        pass
    return st


def version_tuple(v):
    """'4.1.0' → (4,1,0)；修饰后缀剥除；空/垃圾返回 ()（视为不可比）。"""
    s = str(v or "").strip()
    if not s:
        return ()
    try:
        parts = s.split(".")
        return tuple(int(re.sub(r"\D.*$", "", p) or 0) for p in parts[:3])
    except Exception:
        return ()


def is_newer(latest, current):
    a, b = version_tuple(latest), version_tuple(current)
    if not a or not b:
        return False
    return a > b


def fetch_manifest(server_url, token, current_version, timeout=8):
    """拉取更新清单（GET /api/v1/client/update-manifest，X-ETP-Token）；
    未配置/网络失败/端点未就绪 → None（TBC-002：形状联调后对齐）。"""
    base = (server_url or "").rstrip("/")
    if not base:
        return None
    url = base + "/api/v1/client/update-manifest?version=" + \
        urllib.request.quote(str(current_version or ""))
    try:
        req = urllib.request.Request(url)
        req.add_header("X-ETP-Token", token or "")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    m = (data or {}).get("manifest") if isinstance(data, dict) else None
    if isinstance(m, dict) and m.get("download_url") and m.get("latest_version"):
        return m
    return None


def _sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url, dest, sha256="", progress=None):
    """下载安装包（重试 3 次 + sha256 校验）。全部失败抛 RuntimeError。"""
    last_err = None
    for attempt in range(1, _RETRY + 1):
        try:
            tmp = dest + ".part"
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "EyeTerm-updater")
            with urllib.request.urlopen(req, timeout=30) as resp, \
                    open(tmp, "wb") as f:
                total = 0
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _MAX_DL_BYTES:
                        raise RuntimeError("download size limit exceeded")
                    f.write(chunk)
                    if progress:
                        try:
                            progress(total)
                        except Exception:
                            pass
            if total == 0:
                raise RuntimeError("empty download")
            if sha256:
                got = _sha256_of(tmp)
                if got.lower() != str(sha256).lower():
                    os.remove(tmp)
                    raise RuntimeError("sha256 mismatch")
            os.replace(tmp, dest)
            return dest
        except Exception as e:
            last_err = e
            try:
                if os.path.exists(dest + ".part"):
                    os.remove(dest + ".part")
            except OSError:
                pass
            if attempt < _RETRY:
                time.sleep(2 * attempt)
    raise RuntimeError("download failed after %d attempts: %s"
                       % (_RETRY, last_err))


def check_and_fetch(current_version, server_url, token):
    """心跳侧入口：比对 → 下载就绪置 ready。全程后台可调用（异常不外抛）。"""
    try:
        m = fetch_manifest(server_url, token, current_version)
        if not m:
            return load_state()
        latest = str(m.get("latest_version"))
        if not is_newer(latest, current_version):
            return load_state()
        st = load_state()
        if st.get("status") == "ready" and st.get("version") == latest:
            return st   # 已就绪，不重复下载
        _save_state(status="downloading", version=latest, error=None)
        url = m.get("download_url")
        fname = "EyeTerm_Setup_x64_%s.exe" % latest
        dest = os.path.join(update_dir(), fname)
        try:
            download(url, dest, sha256=str(m.get("sha256") or ""))
            _save_state(status="ready", version=latest, file=dest,
                        error=None)
        except RuntimeError as e:
            _save_state(status="failed", version=latest, error=str(e)[:200])
    except Exception as e:
        _save_state(status="failed", error=str(e)[:200])
    return load_state()


def check_async(current_version, server_url, token):
    threading.Thread(target=check_and_fetch,
                     args=(current_version, server_url, token),
                     daemon=True, name="eyeterm-update").start()


# ----------------------------------------------------------------------
# updater 子进程（--et-updater）：等待主进程退出 → 静默安装 → 自启
# ----------------------------------------------------------------------
_MAIN_PID_NAME = "main.pid"


def write_main_pid():
    try:
        with open(os.path.join(update_dir(), _MAIN_PID_NAME), "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass


def _pid_alive(pid):
    try:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        h = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False   # 打不开（已退出或权限）按退出处理
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return bool(ok) and code.value == STILL_ACTIVE
    except Exception:
        return False


def run_updater(timeout_sec=_INSTALL_TIMEOUT):
    """updater 子进程主循环：等主进程退 → /SILENT 安装 → 退出（安装器
    postinstall 自启新客户端）。文件缺失/主进程迟迟不退 → 静默退出。"""
    d = update_dir()
    try:
        with open(os.path.join(d, _MAIN_PID_NAME), "r") as f:
            main_pid = int(f.read().strip())
    except Exception:
        return 2
    st = load_state()
    installer = st.get("file") or ""
    if not installer or not os.path.exists(installer):
        return 3
    deadline = time.time() + 120   # 等主进程退出最多 2 分钟
    while time.time() < deadline:
        if not _pid_alive(main_pid):
            break
        time.sleep(0.5)
    else:
        return 4   # 主进程未退出（放弃，不动安装包）
    time.sleep(1.0)   # 让文件句柄完全释放
    try:
        rc = subprocess.call(
            [installer, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
            timeout=timeout_sec)
    except Exception:
        return 5
    _save_state(status="idle")
    return 0 if rc == 0 else 6
