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


def main() -> None:
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
    except Exception:
        tray_holder["obj"] = None
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
    main()
