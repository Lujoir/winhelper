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


def resource_path(rel: str) -> str:
    """兼容 PyInstaller 打包后的资源路径 (sys._MEIPASS)"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def fatal(msg: str) -> None:
    """无降级手段时的原生弹窗提示"""
    ctypes.windll.user32.MessageBoxW(0, msg, "Windows 系统故障分析系统", 0x10)


def main() -> None:
    try:
        import webview
        from bridge import ApiBridge
    except ImportError as e:
        fatal(f"桌面组件缺失：{e}\n\n请安装 pywebview（pip install pywebview）后重试。")
        return

    index_path = resource_path(os.path.join("web", "index.html"))
    if not os.path.exists(index_path):
        fatal(f"前端资源缺失：{index_path}\n\n请重新安装本程序。")
        return

    webview.create_window(
        "Windows 系统故障分析系统",
        index_path,
        js_api=ApiBridge(),
        width=1380,
        height=880,
        min_size=(1100, 700),
        background_color="#0f1117",
    )
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
