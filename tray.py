# -*- coding: utf-8 -*-
"""EyeTerm 托盘常驻（三大改造②，2026-09-17）。

ctypes Shell_NotifyIconW 手写（零第三方依赖红线，ADR-011）：
- 隐藏消息窗口接收托盘回调（WM_APP）；双击/菜单「打开主界面」→ on_show；
  菜单「退出」→ on_exit（desktop.py 负责先删图标再 destroy 真退出）
- 右键菜单 TrackPopupMenu(TPM_RETURNCMD) 同步取选择（SetForegroundWindow
  前置规避菜单不消失的系统行为）
- 图标：exe 同目录 app.ico（安装器随包）→ 落盘 _MEIPASS/脚本目录 → 系统
  IDI_APPLICATION 兜底
"""

import ctypes
import ctypes.wintypes as wt
import os
import threading

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
WM_APP_TRAY = 0x8000
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
IDI_APPLICATION = 32512
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
MF_STRING = 0x00000000
TPM_RETURNCMD = 0x0100
TPM_RIGHTBUTTON = 0x0002
CS_VREDRAW, CS_HREDRAW = 0x0001, 0x0002
SW_SHOWNORMAL = 1

_id = 1   # 菜单项 id（1=打开主界面 2=退出）


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", wt.HWND),
        ("uID", wt.UINT),
        ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256),
        ("uVersion", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64),
        ("dwInfoFlags", wt.DWORD),
    ]


_user32 = ctypes.windll.user32
_shell32 = ctypes.windll.shell32
_kernel32 = ctypes.windll.kernel32
# Shell_NotifyIconW 属于 shell32（不是 user32——误用会 AttributeError 且被
# 线程异常吞掉造成“假成功”，冒烟实测）；DefWindowProcW 需显式 argtypes
# （WM_RBUTTONUP 的 lparam 超 int32，不声明会 OverflowError）。
_user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
_user32.DefWindowProcW.restype = ctypes.c_longlong
_shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
_shell32.Shell_NotifyIconW.restype = wt.BOOL

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_longlong, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSW(ctypes.Structure):
    """ctypes.wintypes 无 WNDCLASSW（实测 3.12），自定义。"""
    _fields_ = [
        ("style", wt.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


def _icon_from_file(path):
    try:
        if path and os.path.exists(path):
            h = _user32.LoadImageW(None, path, IMAGE_ICON, 0, 0,
                                   LR_LOADFROMFILE)
            if h:
                return h
    except Exception:
        pass
    return _user32.LoadIconW(None, ctypes.c_wchar_p(IDI_APPLICATION))


def _locate_ico():
    cands = []
    if getattr(__import__("sys"), "frozen", False):
        cands.append(os.path.join(os.path.dirname(
            os.path.abspath(__import__("sys").executable)), "app.ico"))
        try:
            cands.append(os.path.join(__import__("sys")._MEIPASS, "app.ico"))
        except Exception:
            pass
    cands.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "app.ico"))
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


class TrayIcon(object):
    """托盘图标；start() 起消息线程（daemon）。on_show/on_exit 在消息线程回调。"""

    def __init__(self, on_show, on_exit, tooltip="观枢终端平台｜EyeTerm"):
        self._on_show = on_show
        self._on_exit = on_exit
        self._tooltip = (tooltip or "")[:127]
        self._nid = None
        self._hwnd = None
        self._hicon = None
        self._thread = None
        self._ready = threading.Event()
        self._removed = threading.Event()
        self._added = threading.Event()
        # ctypes 回调对象必须持有长引用，否则 GC 后消息循环回调 → 进程崩溃
        # （0xC0000409，冒烟实测）——故在实例上保存 WNDPROC 引用。
        self._wndproc_ref = WNDPROC(self._wndproc)

    # ---- 内部 ----
    def _make_nid(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_APP_TRAY
        nid.hIcon = self._hicon
        nid.szTip = self._tooltip
        return nid

    def _register_class(self):
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc_ref
        wc.lpszClassName = "EyeTermTrayWnd"
        wc.hInstance = _kernel32.GetModuleHandleW(None)
        wc.style = CS_VREDRAW | CS_HREDRAW
        atom = _user32.RegisterClassW(ctypes.byref(wc))
        return bool(atom)

    def _create_window(self):
        self._hwnd = _user32.CreateWindowExW(
            0, "EyeTermTrayWnd", "EyeTermTray", 0, 0, 0, 0, 0,
            None, None, _kernel32.GetModuleHandleW(None), None)
        return bool(self._hwnd)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_APP_TRAY:
                ev = lparam & 0xFFFF
                if ev in (WM_LBUTTONDBLCLK,):
                    self._safe_call(self._on_show)
                elif ev == WM_RBUTTONUP:
                    self._popup_menu(hwnd)
                return 0
            if msg == WM_DESTROY:
                return 0
        except Exception:
            pass
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _popup_menu(self, hwnd):
        menu = _user32.CreatePopupMenu()
        if not menu:
            return
        _user32.AppendMenuW(menu, MF_STRING, 1, "打开主界面")
        _user32.AppendMenuW(menu, MF_STRING, 2, "退出")
        _user32.SetForegroundWindow(hwnd)   # 托盘菜单焦点规避（系统行为）
        chosen = _user32.TrackPopupMenu(
            menu, TPM_RETURNCMD | TPM_RIGHTBUTTON,
            _user32.GetSystemMetrics(0) - 1,   # 屏幕右下角
            _user32.GetSystemMetrics(1) - 1, 0, hwnd, None)
        _user32.DestroyMenu(menu)
        if chosen == 1:
            self._safe_call(self._on_show)
        elif chosen == 2:
            self._safe_call(self._on_exit)

    def _safe_call(self, fn):
        try:
            if fn:
                fn()
        except Exception:
            pass

    def _loop(self):
        try:
            if not self._register_class():
                return
            if not self._create_window():
                return
            self._hicon = _icon_from_file(_locate_ico())
            self._nid = self._make_nid()
            if not _shell32.Shell_NotifyIconW(NIM_ADD,
                                              ctypes.byref(self._nid)):
                return
            self._added.set()
            msg = wt.MSG()
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
                if self._removed.is_set():
                    break
        finally:
            self._ready.set()

    # ---- 对外 ----
    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="eyeterm-tray")
        self._thread.start()
        self._ready.wait(timeout=5)
        return self._added.is_set()   # 以 Shell_NotifyIconW(ADD) 真实成功为准

    def stop(self):
        """删除托盘图标并结束消息循环（退出前调用，防图标残留）。"""
        if self._nid and self._hwnd:
            try:
                _shell32.Shell_NotifyIconW(NIM_DELETE,
                                           ctypes.byref(self._nid))
            except Exception:
                pass
        self._removed.set()
        if self._hwnd:
            try:
                _user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass
