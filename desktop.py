"""
C/S 桌面模式入口 — 双击即用
============================
pywebview 原生窗口 (Edge WebView2) + JS桥接直连本地分析引擎。

特性:
  - 无需安装 Python / 无需配置环境 (打包为 exe 后)
  - 无 HTTP 服务 / 无端口占用 / 无防火墙弹窗
  - 离线可用 (前端资源全部本地化)
"""

import os
import sys
import ctypes
import socketserver

# power-control P1：提权子进程拦截（单操作 worker，UAC runas 拉起；最小职责
# 见 power-control/docs/DECISIONS.md ADR-008）——必须在任何 UI/服务初始化之前
# 执行并退出；非提权调用零开销。
if len(sys.argv) > 1 and sys.argv[1] == "--pc-elevated-worker":
    from power_control import elevated_worker_entry
    sys.exit(elevated_worker_entry(sys.argv[2:]))

# 自动更新 updater 子进程拦截（三大改造③，ADR-012）：等待主进程退出 →
# /SILENT 安装 → 安装器 postinstall 自启新客户端。独立进程，不加载 UI。
if len(sys.argv) > 1 and sys.argv[1] == "--et-updater":
    import updater as _upd
    sys.exit(_upd.run_updater())

# 4.1.7 文件检索索引器常驻 worker 拦截（--fs-indexer-worker，A/B 部署方案共同
# 前置）：SYSTEM 计划任务 / 安装器 / 客户端引导注册拉起。不加载 UI，不进单实例
# 锁（worker 与 GUI 生命周期独立，锁是 GUI 单实例语义）。
if len(sys.argv) > 1 and sys.argv[1] == "--fs-indexer-worker":
    from fs_indexer import run_worker
    run_worker()
    sys.exit(0)

# pywebview 6.x 本地资源改走内置 HTTP 服务（wsgiref/TCPServer，默认 backlog=5）：
# 首帧并发加载多个静态资源时会随机丢弃请求（症状：某模块 js 整文件未执行、该页功能全断、
# reload 后自愈——disk-cleaner ADR-016 活体取证定案）。在服务实例化前扩大队列根治。
socketserver.TCPServer.request_queue_size = 128


def resource_path(rel: str) -> str:
    """兼容 PyInstaller 打包后的资源路径 (sys._MEIPASS)"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def fatal(msg: str) -> None:
    """无降级手段时的原生弹窗提示"""
    ctypes.windll.user32.MessageBoxW(0, msg, "观枢终端平台｜EyeTerm", 0x10)


# ---- 4.1.7 开机自启静默化（用户点名需求）----
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "EyeTerm"
AUTOSTART_FLAG = "--autostart"


def is_autostart_launch(argv=None):
    """启动旗标判定：--autostart（开机自启静默）/ 无旗标（手动/更新正常显示）。"""
    return AUTOSTART_FLAG in (sys.argv if argv is None else argv)


def ensure_run_key_autostart():
    """旧自启 Run 键幂等重写为带 --autostart 旗标（升级终端首次运行后自动
    完成，下个开机周期生效，无需重装）。

    仅刷新已存在的 EyeTerm 键（安装器 autostart 任务未勾选 = 用户意图，
    不无中生有）；已含旗标不动；失败静默不阻塞启动。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0,
                            winreg.KEY_READ | winreg.KEY_WRITE) as k:
            try:
                val, _ = winreg.QueryValueEx(k, RUN_VALUE_NAME)
            except FileNotFoundError:
                return False
            cmd = str(val).strip()
            if AUTOSTART_FLAG in cmd:
                return False
            exe = cmd.strip('"').rstrip()
            new_val = '"%s" %s' % (exe, AUTOSTART_FLAG)
            winreg.SetValueEx(k, RUN_VALUE_NAME, 0, winreg.REG_SZ, new_val)
            return True
    except Exception:
        return False


def main(autostart=False) -> None:
    try:
        import webview
        from bridge import ApiBridge
        import bridge as _bridge_mod
    except ImportError as e:
        fatal(f"桌面组件缺失：{e}\n\n请安装 pywebview（pip install pywebview）后重试。")
        return

    # 安装包预置注册（三大改造①，ADR-010）：{app}\config_bootstrap.json →
    # uplink 配置（仅当中心未配置时采用）；失败静默，完全回退手动流程。
    try:
        import bootstrap as _bs
        _bs.apply_on_startup()
    except Exception:
        pass

    index_path = resource_path(os.path.join("web", "index.html"))
    if not os.path.exists(index_path):
        fatal(f"前端资源缺失：{index_path}\n\n请重新安装本程序。")
        return

    window = webview.create_window(
        "观枢终端平台｜EyeTerm",
        index_path,
        js_api=ApiBridge(),
        width=1380,
        height=880,
        min_size=(1100, 700),
        background_color="#0f1117",
        hidden=autostart,   # 4.1.7：开机自启静默启动——不弹主窗体，托盘常驻
    )
    # 「以管理员重启」：性能模块通过该钩子销毁窗口退出当前实例（防双实例）
    try:
        _bridge_mod.register_exit_hook(window.destroy)
    except Exception:
        pass

    # 托盘常驻 + 关窗收纳（三大改造②，ADR-011）：关窗 → 隐藏到托盘；
    # 托盘「退出」/exit hook → 删图标后 destroy 真退出（后台线程全 daemon，
    # 主线程返回即净退）。
    tray_holder = {}

    def _tray_show():
        try:
            window.show()
        except Exception:
            pass

    def _tray_exit():
        try:
            t = tray_holder.get("obj")
            if t:
                t.stop()          # 先删托盘图标（防残留）
        except Exception:
            pass
        try:
            import updater as _upd
            _upd.write_main_pid()  # updater 等待主进程退出的 pid 锚点
        except Exception:
            pass
        try:
            window.destroy()       # 后台线程全 daemon，主线程返回即净退
        except Exception:
            pass

    def _on_closing():
        # 关窗 → 收纳托盘（返回 False 阻止默认关闭）；托盘退出走 _tray_exit
        try:
            window.hide()
        except Exception:
            pass
        return False

    try:
        from tray import TrayIcon
        tray = TrayIcon(on_show=_tray_show, on_exit=_tray_exit)
        tray.start()
        tray_holder["obj"] = tray
        # 4.1.7：静默自启一次性气泡知会（每开机至多一条；失败静默）
        if autostart:
            try:
                tray.notify("观枢终端平台｜EyeTerm",
                            "已在后台运行，双击托盘图标打开主界面")
            except Exception:
                pass
    except Exception:
        tray_holder["obj"] = None
    # 4.1.6 可接管：监听新实例的替换请求 → 旧实例走 _tray_exit 同款优雅
    # 退出链（删托盘图标 + updater pid 锚点落盘 + destroy）；失败仍可被
    # 再次请求。事件监听线程 daemon，不阻塞退出。
    try:
        import single_instance as _si2
        _si2.listen_replace(_tray_exit)
    except Exception:
        pass
    try:
        window.events.closing += _on_closing
    except Exception:
        pass
    # 文件检索：Everything eyeterm 实例随启动拉起（后台线程；失败静默，首用时另有自动拉起兜底）
    try:
        import threading as _th
        import file_search as _fs
        _th.Thread(target=_fs.ensure_running, daemon=True).start()
    except Exception:
        pass
    # 桌面管控：锁屏及壁纸引擎随启动常驻（后台线程等待平台接入就绪后开始轮询；
    # 失败静默不崩主进程，引擎内每轮自动重试——BRG-065 修复懒加载缺陷）
    try:
        import desktop_policy as _dp
        _dp.ensure_autostart()
    except Exception:
        pass
    # 不强制指定 gui: pywebview 自动优先 Edge Chromium(WebView2)
    try:
        webview.start()
    except Exception as e:
        fatal(
            "原生窗口启动失败：WebView2 运行时不可用。\n\n"
            f"详情: {e}\n\n"
            "请安装 Microsoft Edge WebView2 Runtime 后重试"
            "（https://developer.microsoft.com/microsoft-edge/webview2/）。"
        )


if __name__ == "__main__":
    # 4.1.6 单实例约束：全启动入口过同一把命名互斥锁——已运行时再启动不起
    # 新进程；手动启动置前现有窗口后退出；--replace（更新安装后自启统一
    # 走此语义）= 通知旧实例优雅退出 → 轮询拿锁无缝交接；无旧实例时幂等。
    # 4.1.7：--autostart（开机自启）+ 已运行 → 静默退出不置前不抢焦点；
    # --autostart 正常启动 → 隐藏主窗体托盘常驻（一次性气泡知会）。
    try:
        ensure_run_key_autostart()   # 旧 Run 键幂等重写（失败静默）
    except Exception:
        pass
    import single_instance as _si
    autostart = is_autostart_launch()
    if not _si.enter(replace="--replace" in sys.argv,
                     focus_on_exists=not autostart):
        sys.exit(0)
    main(autostart=autostart)
