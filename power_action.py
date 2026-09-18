"""中心发起的重启/关机（UPL power_action / power_action_abort，2026-09-18 main 定稿）。

管控语义（用户点名需求 + 参考脚本 定时关机_V1.0.2.bat）：
- **仅中心可发起**：本地 UI（自动开关机页等）不提供任何立即关机/重启入口；
  命令面仅 uplink 白名单两命令，本地执行无需提权（OS 命令）。
- power_action：args {action:"shutdown"|"restart", delay_sec:uint, force:bool}
  → shutdown /s|/r /f /t delay_sec；force 缺省 True 对齐参考脚本 -f。
- 终端侧仅倒计时知会（弹窗），**不提供本地取消**——管控语义中心意图优先；
  撤销走中心 power_action_abort（shutdown /a，对齐参考脚本取消功能）。
- 防误操作约束：delay_sec 上限 3600；执行前后写本地审计日志（服务端回执
  记录 cid 审计，双存档）。
- 高危红线（power-control 技术红线）：命令串仅由受控白名单参数构造；
  子进程 CREATE_NO_WINDOW；单测全 mock，禁真实 shutdown。

ADR-006 附注⑤（power-control/docs/DECISIONS.md）。
"""

import ctypes
import subprocess
import threading

CREATE_NO_WINDOW = 0x08000000
MAX_DELAY_SEC = 3600
DEFAULT_DELAY_SEC = 60
ACTIONS = {"shutdown": "/s", "restart": "/r"}
_ERROR_NO_SHUTDOWN_IN_PROGRESS = 1116

_USER32 = ctypes.WinDLL("user32", use_last_error=True)
_MB_FLAGS = 0x00000040 | 0x00010000 | 0x00040000   # MB_ICONWARNING|MB_SETFOREGROUND|MB_TOPMOST
_ACTION_CN = {"shutdown": "关机", "restart": "重启"}


def log(msg, level="INFO"):
    """复用引擎日志（日期分文件、可追溯）；import 失败降级静默（测试环境）。"""
    try:
        from power_control import log as _log
        _log(msg, level)
    except Exception:
        pass


def _default_runner(argv, timeout=20):
    """执行受控 shutdown 命令（仅提权链外的 OS 命令，无需管理员）。"""
    p = subprocess.run(argv, capture_output=True, timeout=timeout,
                       creationflags=CREATE_NO_WINDOW)
    return (p.returncode,
            (p.stdout or b"").decode("utf-8", "replace"),
            (p.stderr or b"").decode("utf-8", "replace"))


def build_power_action_argv(action, delay_sec, force):
    """构造 shutdown 命令参数表（纯函数；白名单 action + 数字化 delay）。"""
    flag = ACTIONS.get(action)
    if flag is None:
        raise ValueError("不支持的动作: %r（仅 shutdown/restart）" % (action,))
    d = int(delay_sec)   # 非整数形态（含 "abc"/"10.5"）在此拒绝
    if d < 0 or d > MAX_DELAY_SEC:
        raise ValueError("延迟超出范围（0-%d 秒）: %s" % (MAX_DELAY_SEC, d))
    argv = ["shutdown", flag, "/t", str(d)]
    if force:
        argv.append("/f")
    return argv


def _notify_user(action, delay_sec, force):
    """倒计时知会弹窗（独立线程，非阻塞命令回执；纯知会无确认语义）。"""
    def _show():
        try:
            txt = ("中心已发起%s，将在 %d 秒后执行。\n\n"
                   % (_ACTION_CN.get(action, action), delay_sec))
            if not force:
                txt += "系统将等待正在运行的应用程序自行关闭。\n"
            txt += "如需撤销请由管理平台操作。"
            _USER32.MessageBoxW(None, txt, "观枢终端平台｜EyeTerm", _MB_FLAGS)
        except Exception:
            pass
    t = threading.Thread(target=_show, daemon=True)
    t.start()
    return t


def handle_power_action(args, runner=None, notify=None):
    """UPL power_action：中心发起关机/重启。

    args: {action:"shutdown"|"restart", delay_sec?:uint(≤3600，缺省 60),
           force?:bool(缺省 True)}
    返回 (ok, data)；data 含 action/delay_sec/force/rc/note（审计字段回执）。"""
    args = args if isinstance(args, dict) else {}
    action = str(args.get("action") or "").strip()
    delay_sec = args.get("delay_sec", DEFAULT_DELAY_SEC)
    force = args.get("force", True)
    if not isinstance(force, bool):
        force = str(force).lower() in ("true", "1", "yes")
    try:
        argv = build_power_action_argv(action, delay_sec, force)
        delay_sec = int(delay_sec)
    except (TypeError, ValueError) as e:
        log("power_action 参数校验拒绝: %s | args=%r" % (e, args), "WARN")
        return False, {"ok": False, "action": action or None,
                       "error": "参数校验失败: %s" % e}
    run = runner or _default_runner
    log("power_action 中心下发: action=%s delay=%ds force=%s（审计）"
        % (action, delay_sec, force), "WARN")
    try:
        rc, out, err = run(argv)
    except Exception as e:
        log("power_action 执行异常: %s" % e, "WARN")
        return False, {"ok": False, "action": action, "delay_sec": delay_sec,
                       "force": force, "error": str(e)[:160]}
    if rc != 0:
        note = (err or out or "").strip()[:160] or "exit=%d" % rc
        log("power_action 执行失败 rc=%s: %s" % (rc, note), "WARN")
        return False, {"ok": False, "action": action, "delay_sec": delay_sec,
                       "force": force, "rc": rc, "error": note}
    (notify or _notify_user)(action, delay_sec, force)
    log("power_action 已受理: %s %ds 后执行（知会已弹出）" % (action, delay_sec))
    return True, {"ok": True, "action": action, "delay_sec": delay_sec,
                  "force": force, "rc": 0,
                  "note": "%d 秒后%s%s" % (delay_sec,
                                           _ACTION_CN.get(action, action),
                                           "（强制）" if force else "")}


def handle_power_action_abort(args, runner=None):
    """UPL power_action_abort：中心撤销 pending 关机/重启（shutdown /a）。

    无 pending 关机时 rc=1116，如实回执不算异常。"""
    run = runner or _default_runner
    log("power_action_abort 中心撤销请求（审计）", "WARN")
    try:
        rc, out, err = run(["shutdown", "/a"])
    except Exception as e:
        return False, {"ok": False, "error": str(e)[:160]}
    if rc == 0:
        log("power_action_abort 已撤销待执行的关机/重启")
        return True, {"ok": True, "rc": 0,
                      "note": "已撤销待执行的关机/重启"}
    if rc == _ERROR_NO_SHUTDOWN_IN_PROGRESS:
        log("power_action_abort 无 pending 关机（rc=1116）")
        return False, {"ok": False, "rc": rc,
                       "error": "当前没有待执行的关机/重启"}
    note = (err or out or "").strip()[:160] or "exit=%d" % rc
    log("power_action_abort 失败 rc=%s: %s" % (rc, note), "WARN")
    return False, {"ok": False, "rc": rc, "error": note}
