# -*- coding: utf-8 -*-
"""IP 冲突深度检测引擎（ADR-029）。

用户网工级业务流程编排（只读 display 命令白名单，逐命令证据链存档）：
  1. resolve  区域定位：知识库 route_nodes（[{match,zone,desc,gw_ip?}]）按
              业务 IP 最长前缀匹配 → 网关设备 IP（gw_ip 缺失 → skipped，
              提示知识库维护「网段→网关设备」映射）
  2. arp      网关 ARP 检索：display arp | include <ip> → 0 条=empty（证据
              不足：IP 不在该网关）；1 条校验 MAC 匹配（match/mismatch，
              mismatch=网关层冲突信号）；≥2 条不同 MAC=multi（冲突实锤）
  3. nad      准入资产关联：IP+MAC → 准入登记证据 + macports.manip（接入
              交换机管理 IP，ADR-025/Phase A 实测结构）
  4. macaddr  接入交换机 MAC 表：display mac-address | include <四位段> →
              多端口在线=漂移/环路信号；管理网不可达/认证失败 → failed
              降级标注（不阻断整体）
  5. conclude 结论合成：confirmed（冲突确认）/ suspect（疑似）/ normal（正常）
              / insufficient_evidence（证据不足）

依赖：paramiko==3.5.1（ADR-029：仅本模块使用；凭据零回显；SSH 凭据来自
switches 台账逐台覆盖或 settings 全局缺省，SecretsBox 解密）。
"""
import ipaddress
import re
import socket
import sys
import time

try:
    import paramiko
    _PARAMIKO_ERR = ""
except ImportError as _exc:  # 防御：未安装时引擎整体降级 not_installed
    paramiko = None
    _PARAMIKO_ERR = str(_exc)

import ssh_hostkey  # noqa: E402  H3：SSH 主机密钥校验（server/ 同目录模块）

# 只读命令白名单（模板；ip 已过 ipaddress 校验、mac 已归一为十六进制段，
# 不存在用户注入面）
CMD_SCREEN = "screen-length disable"
CMD_ARP = "display arp | include {ip}"
CMD_MACADDR = "display mac-address | include {mac}"
CMD_VERSION = "display version"
_OUTPUT_TAIL = 1500          # 逐步证据链中原始输出保留长度
_CONN_TIMEOUT = 8
_BANNER_TIMEOUT = 10
_CMD_WAIT = 2.5

_MAC_TOKEN = re.compile(
    r"([0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4})"
    r"|((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})"
    r"|((?:[0-9A-Fa-f]{2}-){5}[0-9A-Fa-f]{2})")
_MAC_STRIP = (":", "-", ".", " ")


def mac_key(mac):
    """MAC 归一比对键（与 store._mac_key 同规则）：去分隔符+小写。"""
    out = str(mac or "")
    for ch in _MAC_STRIP:
        out = out.replace(ch, "")
    return out.lower()


def mac_h3c(mac):
    """任意格式 → 华三/华为四位段（F4F1-9E3C-69E4）；非法返回原文。"""
    key = mac_key(mac)
    if len(key) != 12 or not all(c in "0123456789abcdef" for c in key):
        return str(mac or "")
    return "-".join(key[i:i + 4].upper() for i in (0, 4, 8))


def valid_ip(text):
    """合法 IPv4/IPv6 校验（引擎入口参数安全闸）。"""
    try:
        ipaddress.ip_address(str(text or "").strip())
        return True
    except ValueError:
        return False


class SwitchSession(object):
    """Comware V7 SSH shell 会话（paramiko 3.5.1，只读 display 白名单）。"""

    def __init__(self, host, username, password, port=22):
        self.host = host
        self.port = int(port or 22)
        self.username = username
        self._password = password      # 零回显：仅内存持有，禁止进日志/证据
        self.client = None
        self.channel = None
        self.banner = ""

    def connect(self):
        if paramiko is None:
            raise RuntimeError("paramiko not installed")
        self.client = paramiko.SSHClient()
        # H3：主机密钥校验。交换机侧指纹库为 R2 项（多设备 + reader 尚未开户），
        # 本模块采用「登记了指纹则强校验；未登记则**显式告警**后放行」——
        # 原实现在任何情况下都静默 AutoAdd，连告警都没有。
        mode, expected_fp = ssh_hostkey.harden(self.client, self.host, self.port)
        if mode == "missing":
            sys.stderr.write(
                "WARN: deep_engine 未登记交换机主机指纹（%s:%s），本次未校验"
                "主机密钥；建议用 tools/record_hostkey.py 采集并登记\n"
                % (self.host, self.port))
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            mode = "insecure"
        self.client.connect(
            self.host, port=self.port, username=self.username,
            password=self._password, timeout=_CONN_TIMEOUT,
            banner_timeout=_BANNER_TIMEOUT, auth_timeout=_CONN_TIMEOUT,
            look_for_keys=False, allow_agent=False)
        if mode == "pinned":
            ssh_hostkey.verify(self.client, expected_fp)
        self.banner = self.client.get_transport().remote_version or ""
        self.channel = self.client.invoke_shell(width=220, height=1000)
        time.sleep(1.2)
        self._drain()
        return self.banner

    def _drain(self):
        out = b""
        try:
            while self.channel.recv_ready():
                out += self.channel.recv(65535)
                time.sleep(0.2)
        except Exception:
            pass
        return out.decode("utf-8", "replace")

    def send(self, cmd, wait=_CMD_WAIT):
        """发送单条命令并收集输出（命令进证据链，输出截断存档）。"""
        self.channel.send(cmd + "\n")
        time.sleep(wait)
        return self._drain()

    def run_readonly(self, commands):
        """按白名单顺序执行并返回 [(cmd, output)]（调用方负责解析）。"""
        results = []
        for cmd in commands:
            results.append((cmd, self.send(cmd)))
        return results

    def close(self):
        for closer in (self.channel, self.client):
            try:
                if closer is not None:
                    closer.close()
            except Exception:
                pass


# ----------------------------- 输出解析器 ------------------------------

def parse_arp(output):
    """display arp | include <ip> 输出 → [{ip, mac, rest}]。

    Comware 行样例：172.17.90.215  F4f1-9e3c-69e4  90  GE1/0/48  D  18
    宽松解析：逐行提取 IPv4 + MAC token（含表头行自动跳过）。"""
    rows = []
    for line in (output or "").splitlines():
        ip_tok = re.search(
            r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", line)
        mac_tok = _MAC_TOKEN.search(line)
        if not ip_tok or not mac_tok:
            continue
        rows.append({"ip": ip_tok.group(1),
                     "mac": mac_tok.group(0),
                     "rest": line.strip()[:200]})
    return rows


def parse_mac_address(output):
    """display mac-address | include <mac> 输出 → [{mac, vlan, port, rest}]。

    Comware 行样例：F4f1-9e3c-69e4  90  Learned  GigabitEthernet1/0/48  Y"""
    rows = []
    for line in (output or "").splitlines():
        mac_tok = _MAC_TOKEN.search(line)
        if not mac_tok:
            continue
        port = ""
        port_tok = re.search(
            r"([A-Za-z]+\d+(?:/\d+)+(?:[:.]\d+)?)", line)
        if port_tok:
            port = port_tok.group(1)
        vlan_tok = re.search(r"\b(\d{1,4})\b", line[mac_tok.end():])
        rows.append({"mac": mac_tok.group(0),
                     "vlan": vlan_tok.group(1) if vlan_tok else "",
                     "port": port,
                     "rest": line.strip()[:200]})
    return rows


def distinct_macs(rows):
    """按归一键去重后的 MAC 列表。"""
    seen, out = set(), []
    for r in rows:
        key = mac_key(r.get("mac"))
        if key and key not in seen:
            seen.add(key)
            out.append(r)
    return out


# ----------------------------- 凭据解析 -------------------------------

def resolve_credentials(store, settings, device_ip):
    """按设备 IP 查台账（switches）凭据，缺失回退全局缺省（ADR-026 覆盖链）。

    台账密文经 SettingsStore.decrypt 代理（SecretsBox）解密到内存；
    返回 (username, password, source)；source ∈ switch_entry/default；
    凭据零回显（不进任何证据/日志）。"""
    try:
        entry = store.switch_find_by_ip(device_ip) if store else None
        if entry:
            user = str(entry.get("username") or "")
            enc = str(entry.get("password_enc") or "")
            if user and enc and settings is not None:
                try:
                    pwd = settings.decrypt(enc)
                except Exception:
                    pwd = ""
                if pwd:
                    return user, pwd, "switch_entry"
    except Exception:
        pass
    if settings is None:
        return "", "", "default"
    try:
        user = settings.get("switch.default_username") or ""
        pwd = settings.get("switch.default_password") or ""
    except Exception:
        user, pwd = "", ""
    return user, pwd, "default"


# ----------------------------- 编排主流程 -----------------------------

def _resolve_gw(kb_nodes, ip):
    """知识库 route_nodes 最长前缀匹配 → (zone, gw_ip|None, desc)。"""
    best = None
    ipobj = ipaddress.ip_address(ip)
    for node in kb_nodes or []:
        match = str(node.get("match") or "").strip()
        try:
            net = ipaddress.ip_network(match, strict=False)
        except ValueError:
            continue
        if ipobj.version == net.version and ipobj in net:
            plen = net.prefixlen
            if best is None or plen > best[0]:
                best = (plen, node)
    if not best:
        return None, None, None
    node = best[1]
    gw = str(node.get("gw_ip") or "").strip() or None
    return (str(node.get("zone") or ""), gw, str(node.get("desc") or ""))


def run_deep_check(store, kb_nodes, settings, terminal_id, ip, mac,
                   on_step=None, task_id=""):
    """执行深度检测编排，返回 (steps, verdict)。

    任一步骤失败不阻断整体（failed/skipped 如实标注）；
    SSH 凭据零回显（证据链只含命令与输出摘要）。"""
    steps = []

    def emit(step):
        steps.append(step)
        if on_step:
            try:
                on_step(task_id, steps)
            except Exception:
                pass

    def _cmd_entry(cmd, output, ok):
        return {"cmd": cmd, "ok": ok,
                "output_tail": (output or "")[-_OUTPUT_TAIL:]}

    my_key = mac_key(mac)

    # ---- step1 resolve ----
    zone, gw_ip, desc = _resolve_gw(kb_nodes, ip)
    emit({"step": "resolve", "name": "区域定位",
          "status": "done" if gw_ip else "skipped",
          "target": gw_ip or "-", "zone": zone, "note": desc or "",
          "commands": [],
          "evidence": [] if gw_ip else
          ["知识库 route_nodes 未提供该网段 gw_ip（需维护「网段→网关设备」映射）"]})

    # ---- step2 arp ----
    arp_rows, arp_status, arp_note = [], "skipped", ""
    if gw_ip:
        user, pwd, src = resolve_credentials(store, settings, gw_ip)
        if not valid_ip(ip):
            arp_status = "failed"
            arp_note = "非法 IP 参数"
        elif not (user and pwd):
            arp_status = "failed"
            arp_note = "网关设备无可用凭据（台账/缺省均未配置）"
        else:
            sess = SwitchSession(gw_ip, user, pwd)
            try:
                banner = sess.connect()
                outs = sess.run_readonly([
                    CMD_SCREEN,
                    CMD_ARP.format(ip=ip),
                ])
                cmd_entries = [_cmd_entry(c, o, True) for c, o in outs]
                arp_rows = distinct_macs(parse_arp(
                    dict(outs).get(CMD_ARP.format(ip=ip), "")))
                if not arp_rows:
                    arp_status = "empty"
                    arp_note = "网关 ARP 无该 IP 记录（可能不在线或不属于该网关）"
                elif len(arp_rows) >= 2:
                    arp_status = "multi"
                    arp_note = "网关 ARP 同 IP 多 MAC（冲突实锤信号）"
                else:
                    hit = mac_key(arp_rows[0].get("mac")) == my_key
                    arp_status = "match" if hit else "mismatch"
                    arp_note = ("ARP MAC 与上报一致" if hit else
                                "ARP MAC 与上报不一致（网关层冲突信号）")
                emit({"step": "arp", "name": "网关 ARP 检索",
                      "status": arp_status, "target": gw_ip,
                      "note": arp_note, "commands": cmd_entries,
                      "evidence": [r["rest"] for r in arp_rows],
                      "banner": banner})
            except Exception as exc:
                note = str(exc)
                if "Authentication" in note:
                    note = "认证失败：请核对交换机 reader 凭据"
                elif "timed out" in note or "refused" in note \
                        or "unreachable" in note:
                    note = "网络不可达/超时：%s" % note
                arp_status = "failed"   # 同步变量：verdict.sources 如实
                arp_note = note
                emit({"step": "arp", "name": "网关 ARP 检索",
                      "status": "failed", "target": gw_ip, "note": note,
                      "commands": [], "evidence": []})
            finally:
                sess.close()
    else:
        emit({"step": "arp", "name": "网关 ARP 检索", "status": "skipped",
              "target": "-", "note": "无网关设备（区域定位未提供）",
              "commands": [], "evidence": []})

    # ---- step3 nad ----
    manip, nas_if, nad_status, nad_note, nad_evidence = "", "", "skipped", "", []
    try:
        from nad_client import nad_find_by_ip
        blocks, reason = nad_find_by_ip(store, ip)
        if blocks is None:
            nad_status, nad_note = "skipped", "准入数据源不可用：%s" % reason
        else:
            nad_status = "done"
            nad_evidence = blocks
            target = my_key
            for h in blocks:
                for m in h.get("macs") or []:
                    if mac_key(m.get("mac")) == target:
                        for p in m.get("macports") or []:
                            manip = str(p.get("manip") or manip)
                            nas_if = str(p.get("nasif") or nas_if)
            nad_note = ("命中接入交换机 %s（%s）" % (manip, nas_if)
                        if manip else "命中准入登记，无端口记录")
    except Exception as exc:
        nad_status, nad_note = "failed", "准入检索异常：%s" % repr(exc)[:120]
    emit({"step": "nad", "name": "准入资产关联", "status": nad_status,
          "target": manip or "-", "note": nad_note, "commands": [],
          "evidence": nad_evidence})

    # ---- step4 macaddr ----
    mac_rows, mac_status, mac_note = [], "skipped", ""
    emitted_mac = False
    if not manip:
        mac_status = "skipped"
        mac_note = "准入未提供接入交换机（macports 无记录），无法定位登录目标"
    else:
        user, pwd, src = resolve_credentials(store, settings, manip)
        if not (user and pwd):
            mac_status = "failed"
            mac_note = "接入交换机无可用凭据（台账/缺省均未配置）"
        else:
            sess = SwitchSession(manip, user, pwd)
            try:
                banner = sess.connect()
                mac_fmt = mac_h3c(mac)
                outs = sess.run_readonly([
                    CMD_SCREEN,
                    CMD_MACADDR.format(mac=mac_fmt),
                ])
                cmd_entries = [_cmd_entry(c, o, True) for c, o in outs]
                # 注意：不做 MAC 去重——同一 MAC 出现在多端口正是多在线信号
                mac_rows = parse_mac_address(
                    dict(outs).get(CMD_MACADDR.format(mac=mac_fmt), ""))
                ports = {r.get("port") for r in mac_rows if r.get("port")}
                if not mac_rows:
                    mac_status = "empty"
                    mac_note = "该交换机 MAC 表无此 MAC（终端不在其下）"
                elif len(ports) >= 2:
                    mac_status = "multi"
                    mac_note = "同 MAC 多端口同时在线（漂移/环路信号）"
                else:
                    mac_status = "done"
                    mac_note = "单一端口在线（%s）" % (list(ports)[0] if ports
                                                      else "未知端口")
                emit({"step": "macaddr", "name": "接入交换机 MAC 表检索",
                      "status": mac_status, "target": manip,
                      "note": mac_note, "commands": cmd_entries,
                      "evidence": [r["rest"] for r in mac_rows],
                      "banner": banner})
                emitted_mac = True
            except Exception as exc:
                note = str(exc)
                if "Authentication" in note:
                    note = "认证失败：请核对交换机 reader 凭据"
                elif "timed out" in note or "refused" in note \
                        or "unreachable" in note or "No route" in note:
                    note = "管理网不可达/超时：%s" % note
                mac_status = "failed"
            finally:
                sess.close()
    if not emitted_mac:
        emit({"step": "macaddr", "name": "接入交换机 MAC 表检索",
              "status": mac_status, "target": manip or "-",
              "note": mac_note, "commands": [], "evidence": []})

    # ---- conclude ----
    reasons = []
    if arp_status == "multi":
        reasons.append("网关 ARP 同 IP 多 MAC")
    if arp_status == "mismatch":
        reasons.append("网关 ARP MAC 与上报不一致")
    if mac_status == "multi":
        reasons.append("接入交换机同 MAC 多端口在线")
    # 平台内交叉判定（ADR-028）顺带纳入（只读窗口查询，不落库）
    try:
        lk = store.ipconflict_lookup(ip, terminal_id, mac, window_days=7)
        if lk.get("conflict_suspect"):
            reasons.append("平台内同 IP 多终端上报（%s）"
                           % "/".join(lk.get("suspect_reasons") or []))
    except Exception:
        pass

    hard_fail = arp_status in ("failed", "skipped") and \
        mac_status in ("failed", "skipped")
    if arp_status == "multi" or arp_status == "mismatch":
        conclusion = "confirmed"
    elif mac_status == "multi":
        conclusion = "suspect"
    elif hard_fail:
        conclusion = "insufficient_evidence"
    elif reasons:
        conclusion = "suspect"
    elif arp_status == "match" and mac_status in ("done", "empty",
                                                  "skipped"):
        conclusion = "normal"
    elif arp_status == "empty" or mac_status == "empty":
        conclusion = "insufficient_evidence"
    else:
        conclusion = "insufficient_evidence"
    verdict = {
        "ip": ip, "mac": mac, "conclusion": conclusion,
        "reasons": reasons,
        "sources": {
            "gateway_arp": arp_status,
            "admission": nad_status,
            "access_mac": mac_status,
        },
        "checked_at": int(time.time()),
    }
    emit({"step": "conclude", "name": "结论合成", "status": "done",
          "target": "-", "note": conclusion, "commands": [],
          "evidence": reasons})
    return steps, verdict
