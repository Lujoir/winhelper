# -*- coding: utf-8 -*-
"""观枢终端平台服务端 · WoL 定时唤醒（第二段产品化，ADR-044）。

组成：
- 魔术包/校验纯函数（与服务端 systemd timer 脚本、终端侧 wol_relay 同构，
  服务端先行校验做双保险——终端 handle_wol_relay 仍会再拒一次）；
- 同网段中继选举（目标机 asset 自报 IP 优先、连接源兜底，ADR-043 IP 口径）；
- 直发适用性判定（2026-09-18 对照实验定案：三层设备默认禁转 directed
  broadcast——服务器单播 3/3 达、定向广播 0/3 达——跨网段直发必无效）；
- 调度状态机 wol_tick：到期触发按 method + 直发适用性起步——同网段
  direct（服务器直发）→ 观察窗未上线 → relay（同网段在线受控终端代发
  wol_relay）→ 观察窗 → 结论落档；跨网段/method=relay 跳过直发直接中继。

与终端契约（power-control power_action.py，2026-09-18 定稿）：
  wol_relay args {mac, broadcast, port?}；回执 ok=true 仅代表 UDP 已发送，
  唤醒确认统一以目标机 last_seen 恢复为准（二次确认环节）。
"""
import json
import random
import re
import socket
import time
import traceback

from power_control import task_due_today, time_due_with_grace

DEFAULT_PORT = 9
DIRECT_PORTS = (9, 7)            # 直发双端口（与 systemd timer 一致）
GLOBAL_BROADCAST = "255.255.255.255"
WAKE_WAIT_SEC = 240              # 直发后的唤醒观察窗
RELAY_STEP_SEC_DEFAULT = 120     # 单台中继观察窗（settings 可调）
RELAY_WINDOW_SEC_DEFAULT = 1500  # 整轮全局超窗（25 分钟：与 30 分钟补触窗对齐，
                                 # 2026-09-19 调整。修复「当日只跑一次」标记后，
                                 # 重试力度不再依赖重复触发，故窗口覆盖整个补触窗）
MAX_INFLIGHT_DEFAULT = 3         # 同时在途唤醒链上限（防风暴）
TICK_SEC = 20                    # 调度线程节拍
MAX_BROADCASTS = 4               # 单次直发广播地址上限（防御异常多网卡）

_MAC_RE = re.compile(r"[^0-9A-Fa-f]")


def normalize_mac(mac):
    """MAC 归一（与终端侧同构）：分隔符剔除 → 大写 12 hex；非法拒绝。"""
    s = _MAC_RE.sub("", str(mac or "")).upper()
    if not re.fullmatch(r"[0-9A-F]{12}", s):
        raise ValueError("MAC 格式非法")
    return s


def build_magic_packet(mac):
    """魔术包：6×0xFF + 16×MAC（AMD 帕洛阿尔托标准，102 字节）。"""
    return b"\xff" * 6 + bytes.fromhex(normalize_mac(mac)) * 16


def validate_broadcast(addr):
    """严格 IPv4 点分四段、每段 0-255（拒绝 inet_aton 简写形态）。"""
    s = str(addr or "").strip()
    if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", s):
        raise ValueError("广播地址非法")
    if any(int(x) > 255 for x in s.split(".")):
        raise ValueError("广播地址段超界")
    return s


def derive_broadcast(ip):
    """IPv4 → /24 定向广播地址；非法/非点分形态返回空串。"""
    s = str(ip or "").strip()
    if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", s):
        return ""
    parts = s.split(".")
    if any(int(x) > 255 for x in parts):
        return ""
    return "%s.%s.%s.255" % (parts[0], parts[1], parts[2])


def same_subnet(a, b):
    """两个 IPv4 是否同 /24（广播域近似判定；无 VLAN 库，仅做网段比对）。"""
    ba, bb = derive_broadcast(a), derive_broadcast(b)
    return bool(ba) and ba == bb


def direct_send(mac, broadcasts, ports=DIRECT_PORTS, timeout=2.0):
    """服务器直发魔术包（UDP 广播 fire-and-forget）。返回已发送目标列表。"""
    packet = build_magic_packet(mac)
    sent = []
    for b in broadcasts:
        try:
            b = validate_broadcast(b)
        except ValueError:
            continue
        for port in ports:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.settimeout(timeout)
                s.sendto(packet, (b, int(port)))
                sent.append("%s:%d" % (b, port))
            except OSError:
                pass
            finally:
                s.close()
    return sent


# ----------------------------------------------------------------------
# 目标统一解析（ADR-047 批 B）：平台终端 / 火绒终端 / 画方终端
#   标识约定：'hr:<client_id>' | 'nad:<oid>' | 其余 = 平台 terminal_id
#   第三方终端只能作**唤醒目标**——它们没有平台客户端，既不能当中继
#   代发（elect_relays 仍只从平台终端选举），也不能接收关机指令。
# ----------------------------------------------------------------------

HR_PREFIX = "hr:"
NAD_PREFIX = "nad:"


def parse_target_id(tid):
    """目标标识 → (source, native_id)。"""
    s = str(tid or "")
    if s.startswith(HR_PREFIX):
        return "huorong", s[len(HR_PREFIX):]
    if s.startswith(NAD_PREFIX):
        return "nad", s[len(NAD_PREFIX):]
    return "platform", s


def encode_target_id(source, native_id):
    """（source, native_id）→ 平台统一目标标识。"""
    if source == "huorong":
        return HR_PREFIX + str(native_id)
    if source == "nad":
        return NAD_PREFIX + str(native_id)
    return str(native_id)


def resolve_target(store, tid, now=None):
    """统一目标画像；未知目标返回 None。

    - platform：terminals 表 + asset_detail.network（既有口径，_term_row 透出）
    - huorong ：hr_clients 镜像（mac / ip / connect_ip / online）
    - nad     ：nad_terminals 镜像（mac / ips / online）

    online 对第三方源是**镜像快照**语义（同步时刻的状态），精度受同步
    间隔限制，故另附 fresh_ts（快照时间）供唤醒确认判定时效性。"""
    source, nid = parse_target_id(tid)
    if not nid:
        return None
    if source == "huorong":
        row = store.hr_client_get(nid)
        if not row:
            return None
        ips = [x for x in (str(row.get("ip") or "").strip(),
                           str(row.get("connect_ip") or "").strip()) if x]
        return {"source": source, "native_id": nid,
                "label": row.get("computer_name") or row.get("name") or nid,
                "mac": str(row.get("mac") or "").strip(),
                "ips": ips, "online": bool(row.get("online")),
                "fresh_ts": int(row.get("last_seen") or 0),
                "_term_row": None}
    if source == "nad":
        row = store.nad_terminal_get(nid)
        if not row:
            return None
        return {"source": source, "native_id": nid,
                "label": row.get("name") or nid,
                "mac": str(row.get("mac") or "").strip(),
                "ips": [str(x) for x in (row.get("ips") or []) if x],
                "online": bool(row.get("online")),
                "fresh_ts": int(row.get("synced_ts") or 0),
                "_term_row": None}
    term = store.get_terminal(nid)
    if not term:
        return None
    return {"source": "platform", "native_id": nid,
            "label": term.get("hostname") or nid,
            "mac": "", "ips": [], "online": False, "fresh_ts": 0,
            "_term_row": term}


# ----------------------------------------------------------------------
# IP 收集与中继选举（ADR-043 口径：asset 自报优先，连接源兜底）
# ----------------------------------------------------------------------

def terminal_candidate_ips(store, tid, term_row=None):
    """终端候选 IP 集合：asset network[] 全部自报 IPv4 + 连接源 IP。

    第三方源（hr:/nad:）改从各自镜像取 IP —— 它们的 IP 不在平台 asset 里。"""
    source, _nid = parse_target_id(tid)
    if source != "platform":
        tgt = resolve_target(store, tid)
        out, seen = [], set()
        for ip in ((tgt or {}).get("ips") or []):
            s = str(ip or "").strip()
            if s and s not in seen and derive_broadcast(s):
                seen.add(s)
                out.append(s)
        return out
    ips = []
    try:
        asset = store.get_terminal_asset(tid) or {}
        for nic in (asset.get("network") or []):
            if isinstance(nic, dict) and nic.get("ip"):
                ips.append(str(nic["ip"]).strip())
    except Exception:
        pass
    try:
        row = term_row if term_row is not None else store.get_terminal(tid)
        src = str((row or {}).get("ip") or "").strip()
        if src:
            ips.append(src)
    except Exception:
        pass
    out, seen = [], set()
    for ip in ips:
        if ip not in seen and derive_broadcast(ip):
            seen.add(ip)
            out.append(ip)
    return out


def elect_relays(store, target_tid, hb_timeout=180, now=None):
    """同网段在线受控终端选举（排除目标机自身），按 last_seen 新→旧。

    判定：候选终端任一候选 IP 与目标机任一候选 IP 同 /24 即入列；
    在线口径：last_seen 距今 < hb_timeout。返回 [{terminal_id, ip, last_seen}]。"""
    now = now if now is not None else int(time.time())
    target_ips = set(terminal_candidate_ips(store, target_tid))
    if not target_ips:
        return []
    target_b = {derive_broadcast(ip) for ip in target_ips}
    out = []
    try:
        rows = store.list_terminals()
    except Exception:
        return []
    for r in rows:
        tid = r["terminal_id"]
        if tid == target_tid:
            continue
        last_seen = int(r.get("last_seen") or 0)
        if not last_seen or (now - last_seen) >= hb_timeout:
            continue
        for ip in terminal_candidate_ips(store, tid, term_row=r):
            if derive_broadcast(ip) in target_b:
                out.append({"terminal_id": tid, "ip": ip,
                            "last_seen": last_seen})
                break
    out.sort(key=lambda x: -x["last_seen"])
    return out


def target_broadcasts(store, tid, term_row=None):
    """目标机广播地址列表：候选 IP 的 /24 定向广播（去重）+ 全网广播兜底。"""
    outs, seen = [], set()
    for ip in terminal_candidate_ips(store, tid, term_row=term_row):
        b = derive_broadcast(ip)
        if b and b not in seen:
            seen.add(b)
            outs.append(b)
    outs.append(GLOBAL_BROADCAST)
    return outs[:MAX_BROADCASTS]


# ----------------------------------------------------------------------
# 直发适用性判定（2026-09-18 对照实验：跨网段 directed broadcast 被三层
# 设备过滤——单播 3/3 达、定向广播 0/3 达——跨网段直发必无效）
# ----------------------------------------------------------------------

def _local_ip_for(ip):
    """UDP connect 探测到目标 ip 的本机源 IP（不发包）。失败返回空串。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((str(ip), 9))
            return str(s.getsockname()[0] or "")
        finally:
            s.close()
    except (OSError, ValueError):
        return ""


def direct_applicable(store, tid, term_row=None):
    """直发是否可能有效：目标机任一候选 IP 与服务器在该目标方向的本机
    源 IP 同 /24（同广播域）。

    - 目标机无已知 IP → 保守返回 True（保持既有直发尝试，仅存机会）；
    - method=direct 显式指定时由调用方绕过本判定（用户意志优先）。"""
    for ip in terminal_candidate_ips(store, tid, term_row=term_row):
        local = _local_ip_for(ip)
        if local and same_subnet(local, ip):
            return True
    return False


# ----------------------------------------------------------------------
# 调度状态机（线程节拍内调用，单次 tick 快速返回，不阻塞）
# ----------------------------------------------------------------------

def _fmt_ts(v):
    try:
        return time.strftime("%m-%d %H:%M:%S", time.localtime(int(v)))
    except (TypeError, ValueError):
        return "-"


def _ctx_setting(ctx, key, default):
    """读运行时设置（ctx.settings 为 SettingsStore 或同签名 dict）。"""
    try:
        s = getattr(ctx, "settings", None)
        if s is None:
            return default
        v = s.get(key, default)
        return default if v in (None, "") else v
    except Exception:
        return default


def _wake_mode(ctx):
    """唤醒模式：relay（默认，同网段中继）| nad（画方准入唤醒，未接入时
    调度器如实回退 relay 链，不许哑等）。"""
    mode = str(_ctx_setting(ctx, "wol.wake_mode", "relay") or "relay")
    return mode if mode in ("relay", "nad") else "relay"


def wake_via_nad(ctx, store, tid):
    """方案一（画方准入唤醒）适配器——stub 级预埋，ADR-043 降级位模式。

    预期契约（画方接口交付后按规格接通）：
        POST {nad.wol_api_url}
        Headers: Authorization: Bearer {nad.wol_api_key}
        Body:    [{"ip": "<目标机 VLAN 内 IP>", "mac": "<12hex>"}, ...]
        语义：由画方准入在其已部署接口 IP 的目标 VLAN 内发唤醒广播，
        返回逐条成功/失败（形状以画方规格为准，接通时对齐）。

    现状：未接通。返回 (ok, detail)：
      - url 未配置 → (False, "画方准入唤醒接口未接入，已回退中继模式")
      - url 已配置 → (False, "画方接口适配器未接通（规格待交付），"
                             "已回退中继模式")
    两种情况调度器/手动唤醒均须如实留痕后照常走 relay 链，不许哑等。"""
    url = str(_ctx_setting(ctx, "nad.wol_api_url", "") or "")
    if not url:
        return False, "画方准入唤醒接口未接入，已回退中继模式"
    return False, "画方接口适配器未接通（规格待交付），已回退中继模式"


def _target_mac(store, sched):
    mac = str(sched.get("mac") or "")
    if mac:
        return mac
    tid = str(sched.get("terminal_id") or "")
    _source, _nid = parse_target_id(tid)
    try:
        if _source != "platform":
            # 第三方源 MAC 只存在于各自镜像（火绒 hr_clients.mac /
            # 画方 nad_terminals.mac）——WoL 必需项，缺失即落「MAC 未知」
            return str((resolve_target(store, tid) or {}).get("mac") or "")
        asset = store.get_terminal_asset(tid) or {}
        for nic in (asset.get("network") or []):
            if isinstance(nic, dict) and nic.get("mac"):
                return str(nic["mac"])
    except Exception:
        pass
    return ""


# 公开别名：控制台「批量开机」端点（api.py batch-wake）与调度链共用同一
# 取 MAC 口径（平台终端资产自报 → 火绒/画方镜像），避免两处实现漂移。
target_mac = _target_mac


def _fire_direct(ctx, pc, sched, now, today):
    store = ctx.store
    tid = sched["terminal_id"]
    try:
        mac = normalize_mac(_target_mac(store, sched))
    except ValueError:
        pc.wol_schedule_mark(sched["id"], run_state="failed",
                             last_result="目标机 MAC 未知或非法，请编辑补录",
                             last_run_date=today, updated_ts=now)
        pc.wol_attempt_add(sched["id"], tid, "direct", None, 0,
                           "no_mac")
        return
    bcasts = target_broadcasts(store, tid)
    sent = direct_send(mac, bcasts)
    pc.wol_attempt_add(sched["id"], tid, "direct", None, bool(sent),
                       "sent=%s" % (",".join(sent) if sent else "none"))
    pc.wol_schedule_mark(sched["id"], run_state="running",
                         direct_ts=now, last_run_date=today,
                         last_result=("直发已执行（%s），观察 %d 秒"
                                      % (",".join(sent) if sent else "无可达广播",
                                         WAKE_WAIT_SEC)),
                         updated_ts=now)


def _pinned_tid(ctx):
    """专用代理兜底终端 tid（全局设置 wol.pinned_relay，如 Jun-office-PC）。"""
    return str(_ctx_setting(ctx, "wol.pinned_relay", "") or "").strip()


def _pinned_online(store, tid, hb_timeout, now):
    row = store.get_terminal(tid)
    if not row:
        return False
    ls = int(row.get("last_seen") or 0)
    return bool(ls and (now - ls) < hb_timeout)


def _fire_relay_round(ctx, pc, sched, now, skip_note="直发未唤醒，"):
    """中继轮替首轮：同网段在线候选集乱序（随机起点）入队，弹第一台代发。

    - 目标机 MAC 未知 / 候选集为空且无可用 pinned → 如实 failed；
    - 候选集为空但 pinned 在线 → 专用代理兜底单独成队。"""
    store = ctx.store
    tid = sched["terminal_id"]
    mac = _target_mac(store, sched)
    if not mac:
        pc.wol_attempt_add(sched["id"], tid, "relay", None, 0,
                           "目标机 MAC 未知")
        pc.wol_schedule_mark(sched["id"], run_state="failed",
                             last_result=skip_note + "目标机 MAC 未知"
                                                     "，本轮结束",
                             updated_ts=now)
        return
    relays = elect_relays(store, tid, hb_timeout=int(
        ctx.config.get("heartbeat_timeout_sec", 180)), now=now)
    random.shuffle(relays)                      # 随机多候选（随机起点）
    queue = [r["terminal_id"] for r in relays]
    if not queue:
        pinned = _pinned_tid(ctx)
        hb = int(ctx.config.get("heartbeat_timeout_sec", 180))
        if pinned and pinned != tid \
                and _pinned_online(store, pinned, hb, now):
            queue = [pinned]                    # 专用代理兜底
        else:
            pc.wol_attempt_add(sched["id"], tid, "relay", None, 0,
                               "同网段无在线中继终端"
                               + ("，pinned 不可用" if pinned else ""))
            pc.wol_schedule_mark(sched["id"], run_state="failed",
                                 last_result=skip_note
                                             + "同网段无在线中继终端"
                                             "，本轮结束",
                                 updated_ts=now)
            return
    first = queue.pop(0)
    bcast = target_broadcasts(store, tid)[0]
    args = {"mac": normalize_mac(mac), "broadcast": bcast,
            "port": DEFAULT_PORT}
    cid = store.enqueue_command(first, "wol_relay", args,
                                timeout_sec=120, source="powercontrol")
    pc.wol_attempt_add(sched["id"], tid, "relay", first, 1,
                       "cid=%s bcast=%s round#1" % (cid, bcast))
    pc.wol_schedule_mark(sched["id"], run_state="relay_sent",
                         relay_ts=now, relay_round_ts=now, relay_cid=cid,
                         relay_tid=first,
                         relay_queue=json.dumps(queue),
                         relay_tried=json.dumps([first]),
                         last_result="%s已指派同网段终端 %s 代发"
                                     "（命令 #%s），继续观察"
                                     % (skip_note, first, cid),
                         updated_ts=now)


def _rotate_relay(ctx, pc, sched, now):
    """轮替：队列弹下一台代发；队列空时尝试 pinned 兜底；无可用即 give_up。"""
    store = ctx.store
    tid = sched["terminal_id"]
    try:
        queue = list(json.loads(sched.get("relay_queue") or "[]"))
    except ValueError:
        queue = []
    try:
        tried = list(json.loads(sched.get("relay_tried") or "[]"))
    except ValueError:
        tried = []
    mac = _target_mac(store, sched)
    nxt, src = None, ""
    if queue:
        nxt, src = queue.pop(0), "候选轮替"
    else:
        pinned = _pinned_tid(ctx)
        hb = int(ctx.config.get("heartbeat_timeout_sec", 180))
        if pinned and pinned != tid and pinned not in tried \
                and _pinned_online(store, pinned, hb, now):
            nxt, src = pinned, "专用代理兜底"
    if not nxt:
        _final_giveup(ctx, pc, sched, now, note="候选耗尽")
        return
    bcast = target_broadcasts(store, tid)[0]
    args = {"mac": normalize_mac(mac or ""), "broadcast": bcast,
            "port": DEFAULT_PORT}
    cid = store.enqueue_command(nxt, "wol_relay", args,
                                timeout_sec=120, source="powercontrol")
    tried.append(nxt)
    pc.wol_attempt_add(sched["id"], tid, "relay", nxt, 1,
                       "cid=%s bcast=%s %s(#%d)"
                       % (cid, bcast, src, len(tried)))
    pc.wol_schedule_mark(sched["id"], relay_ts=now, relay_cid=cid,
                         relay_tid=nxt,
                         relay_queue=json.dumps(queue),
                         relay_tried=json.dumps(tried),
                         last_result="%s已换 %s 代发（命令 #%s），继续观察"
                                     % (src, nxt, cid),
                         updated_ts=now)


def _final_giveup(ctx, pc, sched, now, note=""):
    """中继轮替结束仍未上线 → 结论落档（附中继命令回执摘要）。"""
    store = ctx.store
    detail = "无回执"
    cid = sched.get("relay_cid")
    relay_tid = sched.get("relay_tid")
    if cid and relay_tid:
        cmd = store.get_command(relay_tid, int(cid))
        if cmd:
            detail = "命令状态=%s" % cmd.get("status")
            res = cmd.get("result_json")
            if isinstance(res, str):
                detail += " result=%s" % res[:160]
            elif isinstance(res, dict):
                detail += " ok=%s" % res.get("ok")
    pc.wol_attempt_add(sched["id"], sched["terminal_id"], "giveup", None, 0,
                       detail)
    pre = ("直发+中继均未唤醒" if sched.get("direct_ts")
           else "中继未唤醒")
    pc.wol_schedule_mark(sched["id"], run_state="failed",
                         last_result="%s（%s%s），本轮结束"
                                     % (pre, detail,
                                        ("；" + note) if note else ""),
                         updated_ts=now)


def wol_tick(ctx):
    """调度一个节拍：触发到期任务 / 确认唤醒 / 中继轮替 / 落档结论。

    唤醒模式（wol.wake_mode）：relay=中继主路线；nad=画方准入唤醒
    （未接入时如实留痕并回退 relay 链，不许哑等）。"""
    store = ctx.store
    pc = ctx.pc
    if pc is None:
        return
    now = int(time.time())
    today = time.strftime("%Y-%m-%d")
    hhmm = time.strftime("%H:%M")

    def _int_setting(key, dflt):
        try:
            return int(_ctx_setting(ctx, key, dflt))
        except (TypeError, ValueError):
            return dflt

    step_sec = max(30, _int_setting("wol.relay_step_sec",
                                    RELAY_STEP_SEC_DEFAULT))
    window_sec = max(step_sec, _int_setting("wol.relay_window_sec",
                                            RELAY_WINDOW_SEC_DEFAULT))
    inflight_cap = max(1, _int_setting("wol.max_inflight",
                                       MAX_INFLIGHT_DEFAULT))
    scheds = pc.wol_schedule_list()
    inflight = sum(1 for s in scheds
                   if (s.get("run_state") or "") in ("running", "relay_sent"))
    hmap = None
    for sched in scheds:
        if not sched.get("enabled"):
            continue
        # 任务化联动（ADR-046）：行挂任务时按任务复核启停与当日重复模式
        #（disabled 双保险：task_set_enabled 已同步行 enabled，此处再复核）
        if sched.get("task_id"):
            task = pc.task_get(int(sched["task_id"]))
            if task is None or not task.get("enabled"):
                continue
            if hmap is None:
                hmap = pc.holidays_map(today[:4])
            if not task_due_today(task, today, hmap):
                continue
        tid = sched["terminal_id"]
        tgt = resolve_target(store, tid)
        if tgt is None:
            # 目标已不在册（平台终端注销 / 镜像中已消失）→ 静默跳过
            continue
        term = tgt.get("_term_row") or {}
        state = sched.get("run_state") or ""
        base_ts = (sched.get("relay_ts") if state == "relay_sent"
                   else sched.get("direct_ts")) or 0
        # 1) 唤醒确认：本轮任意阶段后目标机恢复在线即成功
        #    平台源 = 心跳 last_seen；第三方源 = 外部平台镜像的在线态
        #    （第三方无平台心跳，只能取同步来的在线状态近似判定，且要求
        #     快照时间 ≥ 本轮起点——避免拿陈旧快照误判成功）。
        round_base = (sched.get("direct_ts")
                      or sched.get("relay_round_ts")
                      or sched.get("relay_ts")) or 0
        if tgt["source"] == "platform":
            last_seen = int(term.get("last_seen") or 0)
            confirmed = bool(last_seen and last_seen >= round_base)
            confirm_detail = "last_seen=%s" % _fmt_ts(last_seen)
        else:
            fresh = int(tgt.get("fresh_ts") or 0)
            confirmed = bool(tgt.get("online") and fresh >= round_base)
            confirm_detail = "%s在线（镜像快照 %s）" % (
                tgt["source"], _fmt_ts(fresh) if fresh else "未知")
        if state in ("running", "relay_sent") and confirmed:
            pc.wol_attempt_add(sched["id"], tid, "confirm", None, 1,
                               confirm_detail)
            pc.wol_schedule_mark(sched["id"], run_state="done",
                                 last_result="目标机已上线（%s）"
                                             % confirm_detail,
                                 updated_ts=now)
            continue
        # 2) 到期触发（逾期 ≤30min 补触，修复「精确分钟匹配+在途上限 →
        #    超额行错过触发分钟当天漏跑」缺陷；当天未跑且非进行中；
        #    在途上限内才新开链，补触窗内逐节拍自动续跑）
        if time_due_with_grace(sched.get("time_hhmm"), hhmm) \
                and sched.get("last_run_date") != today \
                and state not in ("running", "relay_sent"):
            if inflight >= inflight_cap:
                continue
            # 当日触发即落「已跑」标记（2026-09-19 缺陷修复）。
            # 原实现只在 _fire_direct 写 last_run_date，跨网段走中继的任务
            # 该字段恒为空 → 触发条件 `last_run_date != today` 恒真 →
            # 成功（done）后下一节拍又被重开链，把 done 覆盖回 relay_sent，
            # 循环至补触窗结束：实测同一任务反复重发 29 个魔术包，且界面
            # "最近结果"被刷成最后一次确认时刻（真实开机时间被覆盖，用户
            # 据此误判「07:30 任务没有实现」——事实上 07:30:19 已触发、
            # 目标机 07:31:16 就已上线）。
            # 在途链（确认/升级/轮替/落档）由 state 保护，不受本标记影响。
            pc.wol_schedule_mark(sched["id"], last_run_date=today,
                                 updated_ts=now)
            mode = _wake_mode(ctx)
            if mode == "nad":
                ok_nad, detail = wake_via_nad(ctx, store, tid)
                pc.wol_attempt_add(sched["id"], tid, "nad", None,
                                   1 if ok_nad else 0, detail)
                _fire_relay_round(ctx, pc, sched, now,
                                  skip_note="nad 模式回退中继：")
                inflight += 1
            else:
                method = str(sched.get("method") or "auto")
                if method == "relay":
                    _fire_relay_round(ctx, pc, sched, now,
                                      skip_note="已指定中继方式，跳过直发：")
                    inflight += 1
                elif method == "auto" \
                        and not direct_applicable(store, tid, term_row=term):
                    _fire_relay_round(ctx, pc, sched, now,
                                      skip_note="跨网段目标，跳过直发：")
                    inflight += 1
                else:
                    _fire_direct(ctx, pc, sched, now, today)
                    inflight += 1
            continue
        # 3) 直发观察窗耗尽 → 升级中继（轮替首轮）
        if state == "running" and now - base_ts >= WAKE_WAIT_SEC:
            _fire_relay_round(ctx, pc, sched, now)
            continue
        # 4) 中继轮替：单台观察窗耗尽 → 换下一台（队列/pinned 兜底）；
        #    全局超窗 → 结论落档
        if state == "relay_sent" and now - base_ts >= step_sec:
            round_ts = int(sched.get("relay_round_ts")
                           or sched.get("relay_ts") or now)
            if now - round_ts >= window_sec:
                _final_giveup(ctx, pc, sched, now,
                              note="全局观察窗（%ds）耗尽" % window_sec)
            else:
                _rotate_relay(ctx, pc, sched, now)


def wol_loop(ctx, stop_event, log=None):
    """守护线程循环（app.py 装配，wol_enabled 配置关闭时空转退出）。"""
    while not stop_event.wait(TICK_SEC):
        try:
            wol_tick(ctx)
        except Exception:
            if log:
                try:
                    log("wol tick error:\n%s" % traceback.format_exc())
                except Exception:
                    pass
