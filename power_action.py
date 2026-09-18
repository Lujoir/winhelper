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
import re
import socket
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


# ---------- wol_relay（跨网段中继主通道·终端本地发魔术包；2026-09-18 main 定稿）----------
# WoL 中继语义：终端与目标机同网段时由中心指派本终端代发魔术包（纯 UDP
# 广播、零依赖零提权）；仅中心发起（白名单+审计），无本地 UI 入口。
# UDP 无确认——回执注明「已发送」，唤醒结果取决于目标机电源状态与同网段可达性。

def normalize_mac(mac):
    """MAC 归一：AA:BB:CC:DD:EE:FF / AA-BB-CC-DD-EE-FF / aabbccddeeff
    → 大写 12 hex；非法拒绝。"""
    s = re.sub(r"[:\-\s.]", "", str(mac or "")).upper()
    if not re.fullmatch(r"[0-9A-F]{12}", s):
        raise ValueError("MAC 格式非法: %r" % (mac,))
    return s


def build_magic_packet(mac):
    """魔术包：6×0xFF + 16×目标 MAC（6 字节）= 102 字节（AMD 帕洛阿尔托标准）。"""
    return b"\xff" * 6 + bytes.fromhex(normalize_mac(mac)) * 16


def validate_broadcast(addr):
    """广播地址合法性：严格 IPv4 点分四段、每段 0-255（拒绝 inet_aton 简写形态）。"""
    s = str(addr or "").strip()
    if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", s):
        raise ValueError("广播地址非法: %r" % (addr,))
    if any(int(x) > 255 for x in s.split(".")):
        raise ValueError("广播地址段超界: %r" % (addr,))
    return s


def _default_wol_sender(packet, broadcast, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(3)
        s.sendto(packet, (broadcast, port))
    finally:
        s.close()


def handle_wol_relay(args, sender=None):
    """UPL wol_relay：终端向同网段广播地址发送目标机魔术包。

    args: {mac:string(归一格式不限), broadcast:string(IPv4), port?:uint(缺省 9)}
    返回 (ok, data)；UDP fire-and-forget，发送成功即回执。"""
    args = args if isinstance(args, dict) else {}
    mac_raw = args.get("mac")
    port_raw = args.get("port", 9)
    try:
        mac = normalize_mac(mac_raw)
        packet = build_magic_packet(mac)
        bcast = validate_broadcast(args.get("broadcast"))
        port = int(port_raw)
        if not (1 <= port <= 65535):
            raise ValueError("端口超出范围（1-65535）: %r" % (port_raw,))
    except (TypeError, ValueError) as e:
        log("wol_relay 参数校验拒绝: %s | args=%r" % (e, args), "WARN")
        return False, {"ok": False, "error": "参数校验失败: %s" % e}
    send = sender or _default_wol_sender
    log("wol_relay 中心下发: mac=%s broadcast=%s port=%d（审计）"
        % (mac, bcast, port), "WARN")
    try:
        send(packet, bcast, port)
    except Exception as e:
        log("wol_relay 发送失败: %s" % e, "WARN")
        return False, {"ok": False, "mac": mac, "broadcast": bcast,
                       "port": port, "error": str(e)[:160]}
    log("wol_relay 魔术包已发送（102B → %s:%d）" % (bcast, port))
    return True, {"ok": True, "mac": mac, "broadcast": bcast, "port": port,
                  "note": "魔术包已发送至 %s:%d；唤醒结果取决于目标机电源"
                          "状态与同网段可达性" % (bcast, port)}
