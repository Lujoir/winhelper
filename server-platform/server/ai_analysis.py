# -*- coding: utf-8 -*-
"""观枢终端平台服务端 · AI 分析对外契约层（ai-analysis-dev）。

asset-locate 管线（asset_locate.py）经 `from ai_analysis import
infer_asset_locate` try-import 本模块；未就绪/异常由管线侧降级
available=false，互不阻塞。

契约（与 asset-mgmt-dev 对齐，2026-09-18）：
infer_asset_locate(ctx, raw_text, identifiers, payload)
  → {"available": bool, "model": str,
     "blocks": [{"title", "text", "evidence": [依据条目]}],
     "error": str|None}
payload = {"asset_profile": 融合视图, "matches": 前 3 命中,
           "sources": {源名: 状态串},
           "hints": {"vlan_kb": {cidr: zone_desc}}（可选，仅权威库
           实际条目时空库不注入；直通 ai.py 消费，不改内容）}

核心推断与模型链在 server/ai.py（ADR-046）：本模块只做
payload → 推断 profile 的映射与结果形状适配。
隐私边界：raw_text 不进 LLM prompt——检索标识已由管线结构化
提取（identifiers），原文不做透传，最小化数据面。"""
import ai

_FIELD_LABELS = {"floor": "楼层", "room": "房间", "department": "科室",
                 "user": "使用人"}
_CONF_LABELS = {"high": "高", "medium": "中", "low": "低"}


def _payload_to_profile(payload, identifiers):
    """管线 payload → ai.run_asset_locate_inference 的 profile 形状（全键容缺）。"""
    payload = payload if isinstance(payload, dict) else {}
    p = payload.get("asset_profile")
    p = p if isinstance(p, dict) else {}
    prof = {}

    identity = p.get("identity")
    identity = identity if isinstance(identity, dict) else {}
    terminal = {}
    for key in ("hostname", "ip", "mac"):
        v = identity.get(key)
        if v:
            terminal[key] = str(v)
    computer_name = str(p.get("computer_name") or "").strip()
    if computer_name:
        terminal["hostname"] = computer_name
    if terminal:
        prof["terminal"] = terminal

    sources = []
    reg = p.get("registration")
    if isinstance(reg, dict) and reg:
        sources.append({"source": "huorong", "source_label": "火绒登记信息",
                        "matched": True, "fields": dict(reg)})
    admission = p.get("admission")
    if isinstance(admission, dict) and admission:
        fields = {}
        for key, label in (("terminal_name", "登记名"), ("alias", "别名"),
                           ("owner", "使用人")):
            v = str(admission.get(key) or "").strip()
            if v:
                fields[label] = v
        if fields:
            sources.append({"source": "nad", "source_label": "画方准入登记",
                            "matched": True, "fields": fields})
    matches = payload.get("matches")
    m0 = matches[0] if isinstance(matches, list) and matches else None
    plat = m0.get("platform") if isinstance(m0, dict) else None
    if isinstance(plat, dict):
        group = plat.get("group")
        if isinstance(group, dict) and group.get("name"):
            sources.append({"source": "platform",
                            "source_label": "中心平台资产", "matched": True,
                            "fields": {"资产组": str(group["name"])}})
    ids = {}
    for it in identifiers or []:
        if isinstance(it, dict) and it.get("type") and it.get("value"):
            ids.setdefault(str(it["type"]), str(it["value"]))
    if ids:
        sources.append({"source": "query", "source_label": "检索标识",
                        "matched": True, "fields": ids})
    src_state = payload.get("sources")
    if isinstance(src_state, dict) and src_state:
        sources.append({"source": "sources_state",
                        "source_label": "数据源状态", "matched": None,
                        "fields": dict((str(k), str(v))
                                       for k, v in src_state.items())})
    if sources:
        prof["sources"] = sources

    # hints 直通（管线仅在有权威数据时注入该键：vlan_kb{cidr:zone_desc} /
    # name_pattern_peers[]；空库/无命中不出现——消费端语义见 ai.py ADR-046）
    hints = payload.get("hints")
    if isinstance(hints, dict) and hints:
        prof["hints"] = hints
    return prof


def _terminal_id_of(payload):
    """最佳命中的平台终端 ID（供 ai_analyses 落库；无平台命中 → None）。"""
    payload = payload if isinstance(payload, dict) else {}
    matches = payload.get("matches")
    m0 = matches[0] if isinstance(matches, list) and matches else None
    plat = m0.get("platform") if isinstance(m0, dict) else None
    tid = plat.get("terminal_id") if isinstance(plat, dict) else None
    return str(tid) if tid else None


def _to_contract(result):
    """推断结果 → 管线契约形状。

    blocks 供 UI 渲染（有推断值的字段每字段一块，含中文置信度与依据透传）；
    全部字段证据不足 → 单块「推断结论：证据不足，无法推断」（如实可见）；
    unavailable → available=false + error。附加键 inferences/dropped 供
    管线侧选用（形状演进而预留）。"""
    if not isinstance(result, dict) or result.get("status") != "ok":
        return {"available": False, "model": (result or {}).get("model") or "",
                "blocks": [],
                "error": (result or {}).get("unavailable_reason") or "unknown"}
    inferences = result.get("inferences") or []
    blocks = []
    for inf in inferences:
        if not inf.get("value"):
            continue
        conf = _CONF_LABELS.get(inf.get("confidence"),
                                str(inf.get("confidence") or ""))
        blocks.append({"title": _FIELD_LABELS.get(inf.get("field"),
                                                  str(inf.get("field"))),
                       "text": "%s（置信度：%s）" % (inf["value"], conf),
                       "evidence": list(inf.get("evidence") or [])})
    if not blocks:
        blocks = [{"title": "推断结论", "text": "证据不足，无法推断",
                   "evidence": []}]
    return {"available": True, "model": result.get("model") or "",
            "blocks": blocks, "error": None, "inferences": inferences,
            "dropped": int(result.get("dropped") or 0)}


def infer_asset_locate(ctx, raw_text, identifiers, payload):
    """资产定位 AI 推断（asset-locate 管线契约入口，ADR-046）。

    raw_text 仅收口不透传（隐私最小化，见模块 docstring）；
    任何内部异常兜底 available=False（管线照常返回确定数据）。"""
    try:
        profile = _payload_to_profile(payload, identifiers)
        result = ai.run_asset_locate_inference(
            ctx, profile, terminal_id=_terminal_id_of(payload))
        return _to_contract(result)
    except Exception as exc:
        return {"available": False, "model": "", "blocks": [],
                "error": "AI 推断异常:%s" % repr(exc)[:120]}
