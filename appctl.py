# -*- coding: utf-8 -*-
"""EyeTerm 客户端控制（三大改造②③桥接面，2026-09-17）。

- 开机自启：HKCU Run「EyeTerm」键读写（安装器 [Tasks] autostart 同一键）；
  exe 态写 sys.executable；python 态写 pythonw + 脚本（开发环境可写但仅调试用）。
- 更新状态/应用：updater 状态透出 + 「立即更新」拉起 updater 子进程
  （--et-updater；主进程随后自行退出）。
"""

import os
import subprocess
import sys
import winreg

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "EyeTerm"


def _launch_target():
    if getattr(sys, "frozen", False):
        return {"file": sys.executable, "params": ""}
    # python 态（开发）：pythonw + 脚本（无控制台）
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    py = pyw if os.path.exists(pyw) else sys.executable
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "desktop.py")
    return {"file": py, "params": '"%s"' % script}


def get_autostart():
    """HKCU Run 是否存在 EyeTerm 启动项。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, _VALUE_NAME)
            return {"success": True, "enabled": bool(str(val).strip())}
    except FileNotFoundError:
        return {"success": True, "enabled": False}
    except OSError as e:
        return {"success": False, "error": str(e)}


def set_autostart(enabled):
    """写入/删除 HKCU Run 启动项（与安装器 [Tasks] 同一键）。
    CreateKeyEx 幂等创建（Run 键正常恒存在，防御性创建保证任意环境可写）。"""
    try:
        if enabled:
            t = _launch_target()
            data = t["params"] and ('"%s" %s' % (t["file"], t["params"])) \
                or '"%s"' % t["file"]
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                                    winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, _VALUE_NAME, 0, winreg.REG_SZ, data)
        else:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                                    winreg.KEY_SET_VALUE) as k:
                    winreg.DeleteValue(k, _VALUE_NAME)
            except FileNotFoundError:
                pass
        return {"success": True, "enabled": bool(enabled)}
    except OSError as e:
        return {"success": False, "error": str(e)}


def update_status():
    """更新引擎状态透出（updater.load_state 状态机）。"""
    try:
        import updater
        st = updater.load_state()
        return {"success": True, "update": {
            "status": st.get("status") or "idle",
            "version": st.get("version"),
            "error": st.get("error"),
        }}
    except Exception as e:
        return {"success": False, "error": str(e)}


def update_apply():
    """「立即更新」：拉起 updater 子进程（--et-updater），由其等待本进程
    退出后静默安装并自启新客户端；调用方（前端提示条）随即触发窗口退出。"""
    try:
        import updater
        st = updater.load_state()
        if st.get("status") != "ready" or not st.get("file"):
            return {"success": False, "error": "尚无可安装的更新包"}
        updater.write_main_pid()
        exe = sys.executable
        args = [exe]
        if not getattr(sys, "frozen", False):
            args += [os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "desktop.py")]
        args += ["--et-updater"]
        subprocess.Popen(
            args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True)
        return {"success": True, "exiting": True}
    except Exception as e:
        return {"success": False, "error": str(e)}
