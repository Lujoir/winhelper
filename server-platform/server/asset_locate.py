# -*- coding: utf-8 -*-
"""观枢终端平台服务端 · 首页「资产定位」检索聚合管线（/api/v1/console/home/asset-locate）。

归属：asset-mgmt-dev（检索聚合管线 + 编排端点，首页「资产定位」卡数据供给方）。
api.py 单点转发接入（home 分支之前精确匹配该路径），不进 api_home.py
（home-console-dev 辖区），避免跨辖区并行改动。

管线四阶段（每阶段独立 try/except，单阶段失败不拖垮整响应）：
1. extract —— 自由文本鲁棒提取标识符：IPv4（含 /掩码、:端口 尾巴容错）、
   MAC（冒号/连字符/裸 12 位/点分 4 段，归一 12 位小写）、计算机名
   （WIN-/DESKTOP- 前缀、连字符段、字母数字混合三种模式）、工号
   （独立 token 纯数字 4-11 位）；裸 12 位纯数字等不可判候选进
   ambiguous（透明展示、不参与检索）。
2. search —— 三源并行聚合：中心平台 assets（terminals，ip/hostname/mac）
   → 未命中不阻断，继续火绒 hr_clients（mac/ip/computer_name，含
   hr_client_assets 登记信息与 hr_terminal_map 关联）+ 画方 nad 准入
   （macs[].ips / mac / 登记名，复用 nad_client 现行匹配模式）。
3. aggregate —— MAC 主键聚类合并（无 MAC 回落 IP / hostname），输出
   asset_profile 融合视图（计算机名/登记信息/准入信息/时间线/IP 交叉）。
4. inference —— 编排 AI 推断（经 ai-analysis 内部函数 infer_asset_locate，
   LLM 调用走其模型链；未就绪/失败均降级为 available=false，确定数据
   照常返回）。inference 块独立于确定数据，绝不混排。

铁律：无任何命中如实返回 not_found（不虚构）；数据源异常以 status 透出。
"""

import re
import time

MAX_TEXT = 2000

# ----------------------------------------------------------------------
# extract：标识符提取
# ----------------------------------------------------------------------

# 标准 IPv4（4 段；前后不得再邻接数字/点，防截断误吞）
_IPV4_RE = re.compile(r"(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})(?![\d.])")
# 5 段及以上点分数字（疑似 IP 但超段，进 ambiguous）
_OVERSEG_RE = re.compile(r"(?<![\d.])((?:\d{1,3}\.){4,}\d{1,3})(?![\d.])")

# MAC 四形态（col/hyph 需前后不邻接 hex 与分隔符，防子串误吞；bare 12 位
# 前后不邻接 [\w:.\-]，防吞进更长串）
_MAC_COL_RE = re.compile(r"(?<![0-9A-Fa-f:])((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})(?![0-9A-Fa-f:])")
_MAC_HYPH_RE = re.compile(r"(?<![0-9A-Fa-f\-])((?:[0-9A-Fa-f]{2}-){5}[0-9A-Fa-f]{2})(?![0-9A-Fa-f\-])")
_MAC_DOT4_RE = re.compile(r"(?<![0-9A-Fa-f.])((?:[0-9A-Fa-f]{4}\.){2}[0-9A-Fa-f]{4})(?![0-9A-Fa-f.])")
_MAC_BARE_RE = re.compile(r"(?<![\w:.\-])([0-9A-Fa-f]{12})(?![\w:.\-])")

# 工号：独立 token 纯数字 5-11 位（前后不邻接字母/数字/:.，防吞主机名
# 段、端口尾巴与版本号碎片）；4 位纯数字仅当带「工号/编号/职工号」
# 前缀时认工号（防年份/短数字误报）
_EMPID_RE = re.compile(r"(?<![\dA-Za-z:.])(\d{5,11})(?![\dA-Za-z])")
_EMPID_PREFIX_RE = re.compile(
    r"(?:工号|编号|职工号)\s*:?\s*(\d{4})(?![\dA-Za-z])")

# 计算机名 token：字母数字连字符，2-31 位
_HOST_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]{1,30}")

# 整串兜底检索上限（无任何标识符时的非空短文本整体作为检索候选，
# 用于 nad 登记名/火绒名称类非 NETBIOS 命名）
_FREETEXT_MAX = 64

# 火绒登记信息白名单投影（键名由火绒控制台配置，无固定 API 字段名；
# 首键为控制台截图实证口径，次键为官方文档示例名——与控制台
# hrAssetsRows 白名单同口径）
REG_WHITELIST = (
    ("楼层", ("楼层",)),
    ("具体位置", ("具体位置", "位置")),
    ("工号", ("工号",)),
    ("姓名", ("姓名",)),
    ("使用科室", ("使用科室", "部门")),
)


def _mac_norm(mac):
    """MAC 归一：去 :- . 分隔 + 小写，12 位；非法返回 None。"""
    norm = "".join(ch for ch in str(mac or "")
                   if ch not in ":-. ").lower()
    return norm if len(norm) == 12 else None


def _extract(text):
    """自由文本 → 标识符集合。返回 extract 响应块。"""
    identifiers = []
    ambiguous = []
    counts = {"ip": 0, "mac": 0, "hostname": 0, "empid": 0}

    def consume(spans):
        """把已消费区间抹为空白，防止后续提取吞碎片。"""
        masked = list(text)
        for start, end in spans:
            for i in range(start, min(end, len(masked))):
                masked[i] = " "
        return "".join(masked)

    seen_spans = []
    seen_vals = set()

    def add_ident(kind, value, raw, span):
        if value in seen_vals:
            seen_spans.append(span)
            return
        seen_vals.add(value)
        identifiers.append({"type": kind, "value": value, "raw": raw})
        counts[kind] += 1
        seen_spans.append(span)

    # 1) 疑似但超段的点分数字（先记 ambiguous，并消费防碎片）
    for m in _OVERSEG_RE.finditer(text):
        ambiguous.append({"suspect_type": "ip",
                          "value": m.group(1),
                          "reason": "点分超过 4 段，非标准 IPv4"})
        seen_spans.append(m.span(1))

    # 2) IPv4
    for m in _IPV4_RE.finditer(text):
        ip = m.group(1)
        try:
            octets = [int(p) for p in ip.split(".")]
        except ValueError:
            continue
        if any(o > 255 for o in octets):
            ambiguous.append({"suspect_type": "ip", "value": ip,
                              "reason": "存在大于 255 的段，非法 IPv4"})
            seen_spans.append(m.span(1))
            continue
        add_ident("ip", ip, ip, m.span(1))

    # 3) MAC（四形态；bare 纯 12 位数字不认 MAC —— 防误吞工号/单号）
    for m in _MAC_COL_RE.finditer(text):
        add_ident("mac", _mac_norm(m.group(1)), m.group(1), m.span(1))
    for m in _MAC_HYPH_RE.finditer(text):
        add_ident("mac", _mac_norm(m.group(1)), m.group(1), m.span(1))
    for m in _MAC_DOT4_RE.finditer(text):
        add_ident("mac", _mac_norm(m.group(1)), m.group(1), m.span(1))
    for m in _MAC_BARE_RE.finditer(text):
        token = m.group(1)
        span = m.span(1)
        if token.isdigit():
            ambiguous.append({"suspect_type": "mac", "value": token,
                              "reason": "12 位纯数字，不满足 MAC 含十六进制"
                                        "字母特征，不参与检索"})
            seen_spans.append(span)
            continue
        norm = _mac_norm(token)
        if norm:
            add_ident("mac", norm, token, span)

    masked = consume(seen_spans)

    # 4) 计算机名（token 化：含连字符、或字母数字混合；纯数字/纯字母
    #    无连字符不算，防把普通词/版本号当主机名）
    for m in _HOST_TOKEN_RE.finditer(masked):
        token = m.group(0)
        if len(token) < 3 or len(token) > 31:
            continue
        if not re.search(r"[A-Za-z]", token):
            continue                       # 纯数字（归工号/忽略）
        has_hyph = "-" in token
        mixed = bool(re.search(r"[A-Za-z]", token)) and \
            bool(re.search(r"\d", token))
        if not (has_hyph or mixed):
            continue                       # 纯字母单词，防误报
        add_ident("hostname", token, token, m.span())

    # 5) 工号（5-11 位独立纯数字；4 位仅认「工号/编号」前缀形态；
    #    12 位纯数字已在 MAC 步骤入 ambiguous）
    for m in _EMPID_RE.finditer(masked):
        add_ident("empid", m.group(1), m.group(1), m.span())
    for m in _EMPID_PREFIX_RE.finditer(masked):
        add_ident("empid", m.group(1), m.group(1), m.span(1))

    # 6) 整串兜底：无任何标识符、无 ambiguous 项、无空白、2-64 位的
    #    文本整体作为一个 hostname 候选（如实检索，命中仅 low；
    #    用于 nad 登记名/火绒名称类非 NETBIOS 命名）。含空白或存在
    #    ambiguous 项的输入不兜底（防句子/误报整串检索）。
    value = text.strip()
    if not identifiers and not ambiguous and value \
            and not any(ch.isspace() for ch in value) \
            and 2 <= len(value) <= _FREETEXT_MAX:
        identifiers.append({"type": "hostname", "value": value,
                            "raw": value})
        counts["hostname"] = 1

    return {"identifiers": identifiers, "counts": counts,
            "ambiguous": ambiguous, "empty": not identifiers}


# ----------------------------------------------------------------------
# search：三源检索（platform / huorong / nad）
# ----------------------------------------------------------------------

def _ids_of(extract_block):
    """extract 块 → 检索键集合。"""
    ips, macs, hosts, empids = set(), set(), set(), set()
    for it in extract_block["identifiers"]:
        v = (it.get("value") or "").strip()
        if not v:
            continue
        if it["type"] == "ip":
            ips.add(v)
        elif it["type"] == "mac":
            norm = _mac_norm(v)
            if norm:
                macs.add(norm)
        elif it["type"] == "hostname":
            hosts.add(v.strip().lower())
        elif it["type"] == "empid":
            empids.add(v)
    return {"ips": ips, "macs": macs, "hosts": hosts, "empids": empids}


def _search_platform(store, ids, hb, now):
    """中心平台资产（terminals）。返回 (hits, status)。"""
    hits = []
    groups = {}
    try:
        groups = dict((g["id"], g["name"]) for g in store.asset_group_list())
    except Exception:
        groups = {}
    for t in store.list_terminals():
        asset = None
        keys = set()
        ip = (t.get("ip") or "").strip()
        host = (t.get("hostname") or "").strip().lower()
        if ip and ip in ids["ips"]:
            keys.add("ip")
        if host and host in ids["hosts"]:
            keys.add("hostname")
        macs = set()
        try:
            asset = store.get_terminal_asset(t["terminal_id"]) or {}
        except Exception:
            asset = {}
        for nic in (asset.get("network") or []):
            if isinstance(nic, dict):
                norm = _mac_norm(nic.get("mac"))
                if norm and norm in ids["macs"]:
                    macs.add(norm)
                    keys.add("mac")
        if not keys:
            continue
        gid = t.get("group_id")
        hits.append({
            "terminal_id": t["terminal_id"],
            "hostname": t.get("hostname") or "",
            "ip": ip,
            "os_info": t.get("os_info") or "",
            "online": bool((now - (t.get("last_seen") or 0)) < hb),
            "last_seen": t.get("last_seen") or 0,
            "macs": sorted(macs),
            "group": ({"id": gid, "name": groups.get(gid)}
                      if gid else None),
            "hit_keys": sorted(keys),
        })
    return hits, "ok"


def _search_huorong(store, ids):
    """火绒镜像（hr_clients + 登记 + 关联）。返回 (hits, status)。"""
    try:
        rows = store.hr_locate_clients(ips=ids["ips"], macs_norm=ids["macs"],
                                       hosts_lower=ids["hosts"])
        maps = {}
        try:
            maps = dict((m["hr_client_id"], m) for m in store.hr_map_list())
        except Exception:
            maps = {}
        hits = []
        for r in rows:
            link = maps.get(r["client_id"])
            try:
                reg = store.hr_assets_get(r["client_id"])
            except Exception:
                reg = None
            hits.append({
                "client_id": r["client_id"],
                "name": r.get("name") or "",
                "computer_name": r.get("computer_name") or "",
                "ip": r.get("ip") or "",
                "mac": _mac_norm(r.get("mac")) or "",
                "online": bool(r.get("online")),
                "os": r.get("os") or "",
                "version": r.get("version") or "",
                "group_name": r.get("group_name") or "",
                "last_seen": r.get("last_seen") or 0,
                "first_seen": r.get("first_seen") or 0,
                "last_off": r.get("last_off") or 0,
                "this_on": r.get("this_on") or 0,
                "registration": reg or None,
                "linked_terminal_id": (link.get("terminal_id")
                                       if link else None),
                "link_match_type": (link.get("match_type") or "none"
                                    if link else "none"),
                "hit_keys": r.get("hit_keys") or [],
            })
        return hits, "ok"
    except Exception as exc:
        return [], "error:%s" % repr(exc)[:80]


def _nad_block_shape(block):
    """nad_evidence_full 块 → 响应形状（准入信息投影）。"""
    if not isinstance(block, dict):
        return None
    macs, ips = [], []
    for entry in block.get("macs") or []:
        norm = _mac_norm(entry.get("mac"))
        if norm and norm not in macs:
            macs.append(norm)
        for ip in entry.get("ips") or []:
            if ip and ip not in ips:
                ips.append(ip)
    access = []
    for entry in block.get("macs") or []:
        for p in entry.get("macports") or []:
            access.append({"nasname": p.get("nasname"),
                           "nasif": p.get("nasif"),
                           "manip": p.get("manip")})
    return {
        "oid": block.get("oid"),
        "terminal_name": block.get("name") or "",
        "alias": block.get("ou") or "",
        "owner_name": block.get("owner_name") or "",
        "owner_uuid": block.get("owner_uuid") or "",
        "online": bool(block.get("online")),
        "block": bool(block.get("block")),
        "reginfo_stat": block.get("reginfo_stat"),
        "reginfo": block.get("reginfo"),
        "onlts": block.get("onlts"),
        "current_ips": ips,
        "macs": macs,
        "access_points": access,
        "hit_keys": [],
    }


def _search_nad(store, ids, budget_sec=8.0):
    """画方准入检索（**限时执行**）。

    背景（2026-09-19 实测定位）：画方侧有 1588 台终端，需翻 2 页；单请求超时
    上限 10s → 最坏 20s。叠加其它环节后逼近控制台前端 25s 兜底预算，用户实际
    看到「定位超时」，连平台/火绒的确定数据都拿不到。

    画方是**三源之一且属增益项**（缺它仍能定位，照常返回其余源），故此处加
    **硬预算**：超时即放弃并如实回 status=timeout_budget，绝不拖垮整条聚合链。
    配合 nad_client 侧 NAD_HTTP_TIMEOUT 收紧到 4s，最坏 8s 收口。
    """
    import threading
    box = {}

    def _work():
        try:
            box["r"] = _search_nad_raw(store, ids)
        except Exception as exc:          # 兜底：绝不让线程异常丢失
            box["e"] = exc

    th = threading.Thread(target=_work, daemon=True, name="al-nad")
    th.start()
    th.join(budget_sec)
    if th.is_alive():
        return [], "timeout_budget"
    if "e" in box:
        return [], "error:%s" % repr(box["e"])[:80]
    return box.get("r") or ([], "unknown")


def _search_nad_raw(store, ids):
    """画方准入（复用 nad_client 现行匹配模式 + 登记名兜底检索）。
    返回 (hits, status)；status=not_configured 表示数据源未接入。"""
    import nad_client
    try:
        terms, reason = nad_client.nad_terminals_cached(store)
    except Exception as exc:
        return [], "error:%s" % repr(exc)[:80]
    if terms is None:
        return [], reason or "not_configured"
    hits, seen = [], set()

    def try_append(blocks, key):
        for b in blocks or []:
            shaped = _nad_block_shape(b)
            if not shaped or shaped["oid"] in seen:
                continue
            shaped["hit_keys"].append(key)
            seen.add(shaped["oid"])
            hits.append(shaped)

    try:
        for ip in sorted(ids["ips"]):
            blocks, _ = nad_client.nad_find_by_ip(store, ip, full=True)
            try_append(blocks, "ip")
        for mac in sorted(ids["macs"]):
            blocks, _ = nad_client.nad_find_by_mac(store, mac, full=True)
            try_append(blocks, "mac")
        # 登记名兜底（精确或包含；仅在前两键零命中时补检索）
        if not hits and ids["hosts"]:
            for host in sorted(ids["hosts"]):
                for term in terms:
                    if not isinstance(term, dict):
                        continue
                    name = str(term.get("name") or "").strip().lower()
                    if name and (name == host or host in name):
                        try:
                            try_append(
                                [nad_client.nad_evidence_full(term)],
                                "hostname")
                        except Exception:
                            pass
    except Exception as exc:
        return [], "error:%s" % repr(exc)[:80]
    return hits, "ok"


# ----------------------------------------------------------------------
# aggregate：聚类合并与融合视图
# ----------------------------------------------------------------------

_SCORE_ORDER = {"high": 0, "medium": 1, "low": 2}


def _cluster_identity(cluster):
    """簇内身份（mac/hostname/current_ip 汇总）。"""
    macs, hosts, ips = set(), set(), set()
    for p in cluster["platform"]:
        macs.update(p["macs"])
        if p["hostname"]:
            hosts.add(p["hostname"].strip().lower())
        if p["ip"]:
            ips.add(p["ip"])
    for h in cluster["huorong"]:
        if h["mac"]:
            macs.add(h["mac"])
        if h["computer_name"]:
            hosts.add(h["computer_name"].strip().lower())
        if h["ip"]:
            ips.add(h["ip"])
    for n in cluster["nad"]:
        macs.update(n["macs"])
        if n["terminal_name"]:
            hosts.add(n["terminal_name"].strip().lower())
        ips.update(n["current_ips"])
    return {"macs": macs, "hosts": hosts, "ips": ips}


def _attach(cluster_ids, ids, keys):
    """判断候选是否可挂靠到簇（mac/ip/hostname 任一键交集）。"""
    if ids["macs"] & cluster_ids["macs"]:
        keys.add("mac")
    if ids["ips"] & cluster_ids["ips"]:
        keys.add("ip")
    if ids["hosts"] & cluster_ids["hosts"]:
        keys.add("hostname")
    return bool(keys)


def _aggregate(platform_hits, huorong_hits, nad_hits):
    """MAC 主键聚类；返回 matches 响应块列表（已排序）。"""
    clusters = []
    for p in platform_hits:
        clusters.append({"platform": [p], "huorong": [], "nad": [],
                         "keys": set(p["hit_keys"])})
    for h in huorong_hits:
        hids = {"macs": {h["mac"]} if h["mac"] else set(),
                "ips": set(x for x in (h["ip"],) if x),
                "hosts": {h["computer_name"].strip().lower()
                          if h["computer_name"] else ""}
                - {""}}
        attached = False
        for cluster, cids in [(c, _cluster_identity(c)) for c in clusters]:
            if cluster["huorong"]:
                continue   # 每簇每源最多一员：同键多候选歧义各自成簇
            keys = set()
            if _attach(cids, hids, keys):
                if h["linked_terminal_id"] and any(
                        p["terminal_id"] == h["linked_terminal_id"]
                        for p in cluster["platform"]):
                    keys.add("link")
                cluster["huorong"].append(h)
                cluster["keys"] |= keys
                attached = True
                break
        if not attached:
            clusters.append({"platform": [], "huorong": [h], "nad": [],
                             "keys": set(h["hit_keys"])})
    for n in nad_hits:
        nids = {"macs": set(n["macs"]), "ips": set(n["current_ips"]),
                "hosts": {n["terminal_name"].strip().lower()
                          if n["terminal_name"] else ""} - {""}}
        attached = False
        for cluster in clusters:
            if cluster["nad"]:
                continue   # 每簇每源最多一员：同键多候选歧义各自成簇
            keys = set()
            if _attach(_cluster_identity(cluster), nids, keys):
                cluster["nad"].append(n)
                cluster["keys"] |= keys
                attached = True
                break
        if not attached:
            clusters.append({"platform": [], "huorong": [], "nad": [n],
                             "keys": set(n["hit_keys"])})

    matches = []
    for c in clusters:
        if "mac" in c["keys"]:
            score = "high"
        elif "ip" in c["keys"] or "link" in c["keys"]:
            score = "medium"
        else:
            score = "low"
        ids = _cluster_identity(c)
        cur_ip = _current_ip(c)
        matches.append({
            "score": score,
            "keys": sorted(c["keys"]),
            "identity": {
                "mac": sorted(ids["macs"])[0] if ids["macs"] else None,
                "hostname": sorted(ids["hosts"])[0] if ids["hosts"] else None,
                "current_ip": cur_ip,
            },
            "platform": c["platform"][0] if c["platform"] else None,
            "huorong": c["huorong"][0] if c["huorong"] else None,
            "nad": c["nad"][0] if c["nad"] else None,
        })
    matches.sort(key=lambda m: (_SCORE_ORDER[m["score"]],
                                -len(m["keys"]),
                                -(m["platform"] or {}).get("last_seen", 0)
                                if m["platform"] else 0))
    return matches


def _current_ip(cluster):
    """当前在用 IP 口径：nad 在线 IP > 火绒 IP > 平台 IP。"""
    for n in cluster["nad"]:
        if n["current_ips"]:
            return n["current_ips"][0]
    for h in cluster["huorong"]:
        if h["ip"]:
            return h["ip"]
    for p in cluster["platform"]:
        if p["ip"]:
            return p["ip"]
    return None


def _reg_projection(registration):
    """火绒登记信息白名单投影（值缺失的键不虚构，返回实际命中的键）。"""
    if not isinstance(registration, dict):
        return None
    out = {}
    for label, names in REG_WHITELIST:
        for name in names:
            v = registration.get(name)
            if v is not None and str(v).strip():
                out[label] = str(v).strip()
                break
    return out or None


def _build_profile(cluster, matches):
    """最佳命中融合视图（asset_profile）。"""
    p = cluster["platform"][0] if cluster["platform"] else None
    h = cluster["huorong"][0] if cluster["huorong"] else None
    n = cluster["nad"][0] if cluster["nad"] else None
    ids = _cluster_identity(cluster)

    computer_name = ""
    if p and p["hostname"]:
        computer_name = p["hostname"]
    elif h and h["computer_name"]:
        computer_name = h["computer_name"]
    elif n and n["terminal_name"]:
        computer_name = n["terminal_name"]

    sources = [s for s, v in (("platform", p), ("huorong", h), ("nad", n))
               if v]
    reg = _reg_projection(h.get("registration")) if h else None

    latest = None
    for source, ts, kind in (
            ("platform", p["last_seen"] if p else 0, "last_seen"),
            ("huorong", h["last_seen"] if h else 0, "last_seen"),
            ("huorong", h["this_on"] if h else 0, "this_on"),
            ("nad", n["onlts"] if n else 0, "onlts")):
        if ts and (latest is None or ts > latest["ts"]):
            latest = {"ts": ts, "source": source, "kind": kind}

    # IP 交叉校验：多源当前 IP 互检（单源不谈一致 → null）
    ip_pairs = []
    if p and p["ip"]:
        ip_pairs.append(("platform", p["ip"]))
    if h and h["ip"]:
        ip_pairs.append(("huorong", h["ip"]))
    if n and n["current_ips"]:
        ip_pairs.append(("nad", n["current_ips"][0]))
    distinct = set(ip for _, ip in ip_pairs)
    if len(ip_pairs) >= 2 and len(distinct) > 1:
        ip_consistent = False
        notes = ["、".join("%s=%s" % (s, ip) for s, ip in ip_pairs)
                 + " 各源 IP 不一致，请核实终端当前接入"]
    elif len(ip_pairs) >= 2:
        ip_consistent = True
        notes = []
    else:
        ip_consistent = None
        notes = []

    return {
        "computer_name": computer_name,
        "source_platform": "+".join(sources) if sources else "",
        "identity": {
            "mac": sorted(ids["macs"])[0] if ids["macs"] else None,
            "hostname": sorted(ids["hosts"])[0] if ids["hosts"] else None,
            "current_ip": _current_ip(cluster),
        },
        "registration": reg,
        "admission": ({"terminal_name": n["terminal_name"],
                       "alias": n["alias"],
                       "owner": n["owner_name"],
                       "owner_uuid": n["owner_uuid"],
                       "current_ip": (n["current_ips"][0]
                                      if n["current_ips"] else ""),
                       "access_location": (n["access_points"][0]["nasname"]
                                           if n["access_points"] else "")}
                      if n else None),
        "timeline": {
            "platform_last_seen": p["last_seen"] if p else 0,
            "huorong_last_seen": h["last_seen"] if h else 0,
            "huorong_this_on": h["this_on"] if h else 0,
            "nad_onlts": n["onlts"] if n else 0,
            "latest": latest,
        },
        "cross_check": {"ip_consistent": ip_consistent, "notes": notes},
    }


# ----------------------------------------------------------------------
# inference：AI 推断编排（经 ai-analysis 内部函数，失败降级）
# ----------------------------------------------------------------------

def _get_infer_fn():
    """取 ai-analysis-dev 提供的内部推断函数（未落盘返回 None → not_ready）。
    契约：infer_asset_locate(ctx, raw_text, identifiers, payload)
          → {"available": bool, "model": str, "blocks": [...], "error": str}"""
    try:
        from ai_analysis import infer_asset_locate
        return infer_asset_locate
    except Exception:
        return None


def _run_inference(ctx, raw_text, extract_block, profile, matches, sources):
    """编排 AI 推断；任何失败降级为 available=false，不阻塞确定数据。"""
    if not extract_block["identifiers"]:
        return _infer_off("skipped_no_identifiers", "未提取到可用标识符")
    if not matches:
        return _infer_off("skipped_not_found", "三源未命中，无推断对象")
    fn = _get_infer_fn()
    if fn is None:
        return _infer_off("not_ready", "AI 推断模块未就绪")
    payload = {
        "asset_profile": profile,
        "matches": matches[:3],
        "sources": dict((k, v.get("status")) for k, v in sources.items()),
    }
    try:
        result = fn(ctx, raw_text, extract_block["identifiers"], payload)
    except Exception as exc:
        return _infer_off("unavailable", "AI 推断调用失败：%s"
                          % repr(exc)[:80])
    if not isinstance(result, dict):
        return _infer_off("unavailable", "AI 推断返回形状异常")
    blocks = result.get("blocks")
    if result.get("available") and isinstance(blocks, list) and blocks:
        return {"available": True, "status": "ok",
                "model": result.get("model") or "",
                "blocks": blocks,
                "disclaimer": "AI 推测内容，非登记数据，仅供定位参考",
                "error": None}
    return _infer_off("unavailable", result.get("error") or "AI 推断不可用")


def _infer_off(status, error):
    return {"available": False, "status": status, "model": None,
            "blocks": [], "disclaimer": "AI 推测内容，非登记数据，"
            "仅供定位参考", "error": error}


# ----------------------------------------------------------------------
# 端点入口
# ----------------------------------------------------------------------

def handle(ctx, method, path, query, headers=None, body=None,
           sess=None, client_ip=None):
    """入口：POST /api/v1/console/home/asset-locate。返回 (status, bytes, ctype)。

    admin-only（403 带审计，与 sysadmin 路由组同口径）；正文 {"text": str}。"""
    from api import ApiError, _json_response, _parse_json_body
    import auth_upgrade as auth  # 项目内鉴权模块名（api.py 同款别名）

    if sess is None or not auth.require_admin(sess):
        auth.audit(auth.Ev.ACCESS_DENIED, "blocked",
                   username=(sess["username"] if sess is not None else None),
                   user_id=(sess["user_id"] if sess is not None else None),
                   client_ip=client_ip, target=path, reason="admin_required")
        raise ApiError(403, "需要管理员权限")

    if method != "POST":
        raise ApiError(404, "not found")
    data = _parse_json_body(body) or {}
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ApiError(400, "text required")
    text = text.strip()
    if len(text) > MAX_TEXT:
        raise ApiError(400, "text too long (max %d)" % MAX_TEXT)

    started = time.time()
    now = int(started)
    hb = _hb_timeout(ctx)

    # 阶段 1：extract（失败 → 空提取 + not_found，透明报错）
    try:
        extract_block = _extract(text)
    except Exception as exc:
        extract_block = {"identifiers": [], "counts": {}, "ambiguous": [],
                         "empty": True, "error": "extract error:%s"
                         % repr(exc)[:80]}
    ids = _ids_of(extract_block)

    # 阶段 2：search（三源各自隔离）
    sources = {}
    platform_hits = []
    try:
        platform_hits, st = _search_platform(ctx.store, ids, hb, now)
        sources["platform"] = {"status": st, "hits": len(platform_hits)}
    except Exception as exc:
        sources["platform"] = {"status": "error:%s" % repr(exc)[:80],
                               "hits": 0}
    try:
        huorong_hits, st = _search_huorong(ctx.store, ids)
        sources["huorong"] = {"status": st, "hits": len(huorong_hits)}
    except Exception as exc:
        huorong_hits = []
        sources["huorong"] = {"status": "error:%s" % repr(exc)[:80],
                              "hits": 0}
    try:
        _t_nad = time.time()
        nad_hits, st = _search_nad(ctx.store, ids)
        sources["nad"] = {"status": st, "hits": len(nad_hits),
                          "elapsed_ms": int((time.time() - _t_nad) * 1000)}
    except Exception as exc:
        nad_hits = []
        sources["nad"] = {"status": "error:%s" % repr(exc)[:80], "hits": 0}

    # 阶段 3：aggregate
    matches = _aggregate(platform_hits, huorong_hits, nad_hits)
    not_found = not matches
    profile = _build_profile_from_match(matches[0]) if matches else None

    # 阶段 4：inference（编排；未命中/无标识符跳过）
    inference = _run_inference(ctx, text, extract_block, profile, matches,
                               sources)

    _elapsed_ms = int((time.time() - started) * 1000)
    # 分源耗时日志（2026-09-19 增补）：资产定位曾因画方检索（1588 台需翻 2 页）
    # 拖过控制台前端 25s 预算而报「定位超时」。此日志让"慢在哪个源"可观测，
    # 并附画方内存缓存写入时点，便于判断缓存是否命中（age 越小越新鲜）。
    try:
        import nad_client as _nad
        _nad_ts = int(_nad.nad_cache_ts() or 0)
    except Exception:
        _nad_ts = 0
    print("[asset-locate] %dms len=%d sources=%s nad_cache_age=%s"
          % (_elapsed_ms, len(text),
             {k: (v.get("status"), v.get("hits"), v.get("elapsed_ms"))
              for k, v in sources.items()},
             ("%ds" % (int(time.time()) - _nad_ts)) if _nad_ts else "none"))

    return _json_response(200, {
        "ok": True,
        "ts": now,
        "query": {"text_length": len(text),
                  "elapsed_ms": _elapsed_ms},
        "not_found": not_found,
        "extract": extract_block,
        "search": {"sources": sources},
        "matches": matches,
        "asset_profile": profile,
        "inference": inference,
    })


def _hb_timeout(ctx):
    try:
        return int(ctx.config.get("heartbeat_timeout_sec", 180))
    except (TypeError, ValueError):
        return 180


def _build_profile_from_match(match):
    """matches[0] → asset_profile（与 _build_profile 同形状，复用簇结构）。"""
    cluster = {"platform": [match["platform"]] if match["platform"] else [],
               "huorong": [match["huorong"]] if match["huorong"] else [],
               "nad": [match["nad"]] if match["nad"] else []}
    return _build_profile(cluster, [match])
