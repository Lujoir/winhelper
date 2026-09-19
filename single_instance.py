"""winhelper 单实例约束（4.1.6，用户点名需求，2026-09-18 立项）。

防重复 + 可接管（--replace 语义），规格 main 定稿两点：
1. **防重复**：全启动入口（手动双击/开机自启/托盘/更新重启/安装后自启）
   过同一把命名互斥锁（Global\\EyeTerm.SingleInstance）——已运行时再启动
   不起新进程，只把现有窗口还原置前；
2. **可接管**：新实例带 --replace 启动 → 向旧实例发替换事件（命名 Event）
   → 旧实例走既有优雅退出链（托盘退出同款：删图标 + updater pid 锚点 +
   destroy，状态落盘不丢）→ 新实例轮询拿锁无缝交接；无旧实例时 replace
   幂等（直接拿锁正常启动）。

多开成因排查结论（2026-09-18 实查）：HKCU Run 键与启动文件夹均无 EyeTerm
条目、无重复注册——多开实锤为「无锁 + 关窗收纳托盘进程常驻」叠开（用户
双击/更新自启与托盘存活实例并发）。本模块加锁根治，注册表无需修。

技术要点（零第三方依赖，Win32 走 ctypes）：
- Mutex：CreateMutexW + GetLastError()==ERROR_ALREADY_EXISTS（同进程/跨进程
  同语义，单测可同进程复刻双实例）；句柄随进程退出自动释放。
- Event：auto-reset 命名事件 Global\\EyeTerm.ReplaceRequest，旧实例后台
  线程 WaitForSingleObject 消费；新实例 SetEvent 请求。
- 置前：FindWindowW 按主窗口标题定位 → ShowWindow(SW_RESTORE) →
  SetForegroundWindow（前台锁失败静默——主目标是不叠开，置前尽力而为）。
- 全部 Win32 调用失败均静默降级：单实例锁失败时不阻塞启动主流程以外的
  行为（enter() 的返回值由 desktop.py 决定退出与否）。
"""

import ctypes
import sys
import threading
import time

_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
_USER32 = ctypes.WinDLL("user32", use_last_error=True)

# 64 位 HANDLE 签名修正（restype 缺省 int 会截断句柄）+ lasterror 快照
# （kernel32.GetLastError 经 ctypes 调用链会被内部调用覆盖，必须用
# ctypes.get_last_error() 读取 ctypes 保存的最近一次外部调用错误码）。
_KERNEL32.CreateMutexW.restype = ctypes.c_void_p
_KERNEL32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                   ctypes.c_wchar_p]
_KERNEL32.CreateEventW.restype = ctypes.c_void_p
_KERNEL32.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_wchar_p]
_KERNEL32.WaitForSingleObject.restype = ctypes.c_uint32
_KERNEL32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
_KERNEL32.SetEvent.argtypes = [ctypes.c_void_p]
_KERNEL32.SetEvent.restype = ctypes.c_int
_KERNEL32.CloseHandle.argtypes = [ctypes.c_void_p]
_KERNEL32.CloseHandle.restype = ctypes.c_int
_USER32.FindWindowW.restype = ctypes.c_void_p
_USER32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
_USER32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
_USER32.SetForegroundWindow.argtypes = [ctypes.c_void_p]

ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9
INFINITE = 0xFFFFFFFF
WAIT_OBJECT_0 = 0
SYNCHRONIZE = 0x00100000
EVENT_MODIFY_STATE = 0x0002

MUTEX_NAME = "Global\\EyeTerm.SingleInstance"
REPLACE_EVENT_NAME = "Global\\EyeTerm.ReplaceRequest"
MAIN_WINDOW_TITLE = "观枢终端平台｜EyeTerm"
REPLACE_WAIT_TIMEOUT = 30.0     # 旧实例优雅退出上限（秒）
REPLACE_POLL_INTERVAL = 0.4     # 拿锁轮询步进（秒）

_handle = {"mutex": None}       # 进程生命周期内持有的 mutex 句柄


# 活实例隔离（4.1.7 单测实况：本机 winhelper 4.1.6+ 常驻持有正式锁名，
# 同机跑单测 try_acquire 必失败）——测试专用替换锁/事件名，生产不调用。
_mutex_name = MUTEX_NAME
_event_name = REPLACE_EVENT_NAME


def _use_names(mutex=None, event=None):
    """替换锁/事件名（测试隔离用）；传 None 恢复正式名。"""
    global _mutex_name, _event_name
    _mutex_name = mutex or MUTEX_NAME
    _event_name = event or REPLACE_EVENT_NAME


def _create_mutex():
    """尝试创建命名互斥锁。返回 (acquired:bool, handle)；
    已存在持有者 → acquired=False（句柄已关闭，不占名）。"""
    h = _KERNEL32.CreateMutexW(None, True, _mutex_name)
    last = ctypes.get_last_error()   # use_last_error=True 配套快照
    if not h:
        return False, None
    if last == ERROR_ALREADY_EXISTS:
        _KERNEL32.CloseHandle(h)
        return False, None
    return True, h


def try_acquire():
    """尝试获取单实例锁；成功后句柄驻留进程生命周期（退出自动释放）。"""
    acquired, h = _create_mutex()
    if acquired:
        _handle["mutex"] = h
    return acquired


def _find_main_window():
    """按主窗口标题定位现有实例窗口（含托盘隐藏态；找不到返回 0）。"""
    return _USER32.FindWindowW(None, MAIN_WINDOW_TITLE) or 0


def focus_existing():
    """还原并置前现有实例窗口（尽力而为：前台锁失败/窗口缺失均静默）。"""
    try:
        hwnd = _find_main_window()
        if not hwnd:
            return False
        _USER32.ShowWindow(hwnd, SW_RESTORE)
        _USER32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def _create_or_open_replace_event():
    """打开/创建替换请求事件（auto-reset）。首个创建者为旧实例；后来者
    CreateEventW 同名返回既有句柄（语义等同 OpenEvent）。"""
    return _KERNEL32.CreateEventW(None, False, False, _event_name)


def request_replace():
    """向旧实例发出替换请求（SetEvent）；无旧实例时无人消费，无害。"""
    try:
        h = _create_or_open_replace_event()
        if not h:
            return False
        ok = bool(_KERNEL32.SetEvent(h))
        _KERNEL32.CloseHandle(h)
        return ok
    except Exception:
        return False


def listen_replace(callback):
    """旧实例侧：后台线程等待替换请求，收到即回调（daemon，不阻塞退出）。"""
    def _watch():
        try:
            h = _create_or_open_replace_event()
            if not h:
                return
            try:
                while True:
                    w = _KERNEL32.WaitForSingleObject(h, INFINITE)
                    if w != WAIT_OBJECT_0:
                        return
                    callback()
                    # auto-reset 已复位；继续等待下一次替换请求（本实例
                    # 若未退出说明优雅退出失败，仍可被再次请求）
            finally:
                _KERNEL32.CloseHandle(h)
        except Exception:
            return
    t = threading.Thread(target=_watch, daemon=True,
                         name="eyeterm-replace-watch")
    t.start()
    return t


def enter(replace=False, focus_on_exists=True,
          wait_timeout=REPLACE_WAIT_TIMEOUT,
          poll_interval=REPLACE_POLL_INTERVAL, _sleep=time.sleep):
    """启动入口统一闸门（desktop.py 在 UI/服务初始化之前调用）。

    replace=False：拿锁成功 → True；已被持有 → 置前现有窗口（focus_on_exists
    =False 时静默不置前，开机自启撞托盘常驻实例不得抢焦点，4.1.7）→ False
    （调用方应立即退出，不产生新进程）。
    replace=True：先请求旧实例优雅退出 → 轮询拿锁（上限 wait_timeout 秒）
    → 拿到 True；旧实例未退出超时 → False（如实失败，不叠开）；
    无旧实例 → 立即拿到（幂等）。"""
    if not replace:
        if try_acquire():
            return True
        if focus_on_exists:
            focus_existing()
        return False
    request_replace()
    deadline = time.time() + max(0.0, float(wait_timeout))
    while True:
        if try_acquire():
            return True
        if time.time() >= deadline:
            return False
        _sleep(poll_interval)


# ---------------------------------------------------------------- 单测辅助
def _self_test_helpers():
    """暴露内部常量/句柄容器供单测断言（生产代码不使用）。"""
    return {"mutex_name": MUTEX_NAME, "event_name": REPLACE_EVENT_NAME,
            "handle": _handle}


if __name__ == "__main__":   # 手工验证：本进程持锁后二次尝试应失败
    ok = try_acquire()
    again = try_acquire()
    print("first=%s second=%s" % (ok, not again))
    sys.exit(0 if ok and not again else 1)
