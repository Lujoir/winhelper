# -*- coding: utf-8 -*-
"""AI 能力 Skill 校验层（P1-5）。

定位
----
把散落在各引擎里的"输入处理 + 输出接收"收口为**统一契约校验**。
规范依据：`ai-agent/skills/README.md`（Skill 是记忆的唯一写入通道）。

三条设计原则（来自 SOUL / 审计结论）
----------------------------------
① **Skill 是记忆的唯一写入通道** —— 绕过校验的写入即污染源。故校验放在
   引擎出口（落库前），而不是各调用点各写一遍。
② **所有丢弃必须回传结构化原因**，禁止静默吞掉 —— 静默丢弃会让"分析结果
   变少"无法归因（看起来像模型变差，实际是校验拦了）。
③ **严进宽出**：输入校验可以硬（结构由代码决定，可控）；输出校验要**容忍
   LLM 的自然变体**（全角/半角括号、冒号有无、加粗标记 `**`），只验"该说的
   说了没有"，不验"是否逐字复现模板" —— 否则正常输出会被判死。

三层校验
--------
  ① 输入契约  validate_input()  键是否合法 / 必填是否存在 / 值类型是否可用
  ② 输出契约  validate_output() 必需段落是否齐全 / 是否非空 / 长度下限
  ③ 污染检测  detect_pollution() 乱码（复用 ai._detect_mojibake）/ 隐私越界 / 无关内容

本期范围
--------
**纯新增、尚未接入调用链** —— 先让契约立起来并接受测试，再谈"引擎调用它"。
接入需配套回归验证（会改变生产 prompt 的失败判定行为），不在本期。
"""
from __future__ import annotations

import json
import re

try:                                    # 同目录模块（生产运行时）
    from ai import _detect_mojibake
except ImportError:                     # 独立测试时兜底
    def _detect_mojibake(text):         # type: ignore
        return bool(text) and "\ufffd" in text


# --------------------------------------------------------------------------
# 契约表：每个引擎的输入键与输出段落要求
# 依据各引擎的 SYSTEM_PROMPT 与其 context 构建函数的实际参数
# --------------------------------------------------------------------------
CONTRACTS = {
    "analyze": {
        "format": "text",
        "input_keys": ("terminal_id", "issue"),
        "required_input": ("terminal_id",),
        "output_sections": ("故障原因分析", "处理意见", "风险提示"),
        "min_length": 30,
    },
    "diagnose": {
        "format": "text",
        # 六类日志键，均可选（终端采集容错缺省）
        "input_keys": ("hwinfo", "os_info", "perf_analysis", "perf_stress",
                       "system_log", "network"),
        "required_input": (),
        "output_sections": ("故障原因分析", "处理意见", "风险提示"),
        "min_length": 30,
    },
    "ipconflict": {
        "format": "text",
        "input_keys": ("conflict_reports", "deep_task", "admission", "sources"),
        "required_input": (),
        "output_sections": ("冲突判定", "冲突画像", "处理意见", "风险提示"),
        "min_length": 30,
    },
    "routetrace": {
        "format": "text",
        "input_keys": ("hops", "route_nodes", "terminal"),
        "required_input": (),
        "output_sections": ("路径研判", "异常识别", "结论"),
        "min_length": 30,
    },
    "asset-locate": {
        "format": "json",
        "input_keys": ("sources", "terminal", "hints"),
        "required_input": (),
        # JSON 契约：见 validate_asset_locate_json()
        "output_sections": (),
        "min_length": 10,
    },
}

# 隐私越界词：输出里出现这些，说明模型在推测使用人个人内容（准则五红线）
_PRIVACY_VIOLATION_WORDS = (
    "聊天记录", "微信内容", "QQ内容", "浏览历史", "浏览记录", "个人文件内容",
    "照片内容", "邮件内容", "浏览器历史", "私密文件",
)

# 无关内容特征：输出里出现这些，说明模型答非所问（跑题到别的引擎/领域）
_OFFTOPIC_WORDS = (
    "作为AI", "作为一个AI", "我无法访问", "我没有权限",
    "请咨询专业人士", "建议联系客服",
)


def _section_present(text, name):
    """段落存在性判定（容忍 LLM 的自然变体）。

    容忍以下写法：`【故障原因分析】` / `**故障原因分析**` / `故障原因分析：`
    / `## 故障原因分析` —— 只要求"该标题出现过"，不要求逐字复现模板。
    """
    if name in text:
        return True
    # 去掉常见装饰符后重试
    stripped = text.replace("【", "").replace("】", "") \
                    .replace("*", "").replace("#", "") \
                    .replace(" ", "").replace("\u3000", "")
    return name in stripped


def validate_input(skill_id, payload):
    """① 输入契约校验。

    payload 为 dict（各引擎的 context 入参）。
    返回 (ok, dropped)：dropped 是**结构化丢弃回执**列表，
    每项 {"field", "reason", "detail"} —— 绝不静默丢弃。
    """
    contract = CONTRACTS.get(skill_id)
    if contract is None:
        return False, [{"field": None, "reason": "unknown_skill",
                        "detail": skill_id}]

    dropped = []
    if not isinstance(payload, dict):
        return False, [{"field": None, "reason": "payload_not_dict",
                        "detail": type(payload).__name__}]

    # 未知键：不是错误，但要回执（防"调用方以为传进去了"）
    for key in payload:
        if key not in contract["input_keys"]:
            dropped.append({"field": key, "reason": "unknown_input_key",
                            "detail": "不在契约键集内，未被引擎使用"})

    # 必填缺失
    for key in contract["required_input"]:
        if payload.get(key) in (None, "", []):
            dropped.append({"field": key, "reason": "required_missing",
                            "detail": "契约要求必填但为空"})

    # 值类型可用性：None 表示"该源未提供"（合法），但若是带内容的非法类型要回执
    for key, val in payload.items():
        if key not in contract["input_keys"]:
            continue
        if val is None:
            continue
        if not isinstance(val, (str, dict, list)):
            dropped.append({"field": key, "reason": "unusable_value_type",
                            "detail": type(val).__name__})

    ok = not any(d["reason"] in ("required_missing", "payload_not_dict",
                                 "unknown_skill") for d in dropped)
    return ok, dropped


def detect_pollution(text):
    """③ 污染检测：返回结构化原因列表（空列表 = 干净）。"""
    reasons = []
    if not text:
        return reasons

    if _detect_mojibake(text):
        reasons.append({"kind": "mojibake",
                        "detail": "疑似编码损坏（复用 ai._detect_mojibake）"})

    for word in _PRIVACY_VIOLATION_WORDS:
        if word in text:
            reasons.append({"kind": "privacy_violation", "detail": word})
            break

    for word in _OFFTOPIC_WORDS:
        if word in text:
            reasons.append({"kind": "offtopic", "detail": word})
            break

    return reasons


def validate_output(skill_id, content):
    """② 输出契约校验 + 污染检测。

    返回 (ok, issues)：issues 为结构化问题列表，每项
    {"kind", "detail"}；kind ∈ {empty, too_short, missing_section,
    invalid_json, pollution_*}。空列表 = 通过。
    """
    contract = CONTRACTS.get(skill_id)
    issues = []
    if contract is None:
        return False, [{"kind": "unknown_skill", "detail": skill_id}]

    if not content or not content.strip():
        issues.append({"kind": "empty", "detail": "模型返回空内容"})
        return False, issues

    text = content.strip()

    if len(text) < contract["min_length"]:
        issues.append({"kind": "too_short",
                       "detail": "长度 %d < 下限 %d"
                                 % (len(text), contract["min_length"])})

    if contract["format"] == "json":
        parsed, json_issue = parse_asset_locate_json(text)
        if json_issue:
            issues.append(json_issue)
        elif parsed is not None:
            issues.extend(validate_asset_locate_shape(parsed))
    else:
        missing = [s for s in contract["output_sections"]
                   if not _section_present(text, s)]
        if missing:
            issues.append({"kind": "missing_section",
                           "detail": "、".join(missing)})

    for pol in detect_pollution(text):
        issues.append({"kind": "pollution_" + pol["kind"],
                       "detail": pol["detail"]})

    hard = ("empty", "invalid_json", "missing_section")
    ok = not any(i["kind"] in hard or i["kind"].startswith("pollution_")
                 for i in issues)
    return ok, issues


# --------------------------------------------------------------------------
# asset-locate 专用：它输出严格 JSON，需要结构校验（不能像文本引擎那样只验段落）
# --------------------------------------------------------------------------
ASSET_FIELDS = ("floor", "room", "department", "user")
ASSET_CONFIDENCE = ("high", "medium", "low", "none")


def parse_asset_locate_json(text):
    """从模型输出中提取 JSON（容忍 ```json 围栏与前后说明文字）。

    返回 (parsed_or_None, issue_or_None)。
    """
    raw = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", raw, re.S)
    if fence:
        raw = fence.group(1)
    try:
        return json.loads(raw), None
    except (ValueError, TypeError):
        pass
    # 兜底：抓第一个 {...} 平衡片段
    start = raw.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(raw)):
            if raw[i] == "{":
                depth += 1
            elif raw[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[start:i + 1]), None
                    except (ValueError, TypeError):
                        break
    return None, {"kind": "invalid_json",
                  "detail": "未能在输出中解析出合法 JSON"}


def validate_asset_locate_shape(obj):
    """校验 asset-locate 的 JSON 结构。返回 issues 列表。"""
    issues = []
    if not isinstance(obj, dict):
        return [{"kind": "invalid_json", "detail": "顶层不是对象"}]
    items = obj.get("inferences")
    if not isinstance(items, list) or not items:
        return [{"kind": "invalid_json",
                 "detail": "缺少非空 inferences 数组"}]
    seen = set()
    for idx, it in enumerate(items):
        if not isinstance(it, dict):
            issues.append({"kind": "invalid_json",
                           "detail": "inferences[%d] 不是对象" % idx})
            continue
        field = it.get("field")
        if field not in ASSET_FIELDS:
            issues.append({"kind": "invalid_json",
                           "detail": "inferences[%d].field 非法: %r"
                                     % (idx, field)})
            continue
        seen.add(field)
        conf = it.get("confidence")
        if conf not in ASSET_CONFIDENCE:
            issues.append({"kind": "invalid_json",
                           "detail": "inferences[%d].confidence 非法: %r"
                                     % (idx, conf)})
        # 证据可信性硬约束的实现化：low/none 置信度却给了非空值 → 违约
        if conf == "none" and it.get("value") not in (None, ""):
            issues.append({"kind": "contract_violation",
                           "detail": "inferences[%d] confidence=none 但给了 value"
                                     % idx})
        # 非空 value 必须带 evidence（防止"凭常识填值"）
        if it.get("value") not in (None, "") and not it.get("evidence"):
            issues.append({"kind": "contract_violation",
                           "detail": "inferences[%d] 有 value 但无 evidence"
                                     % idx})
    missing = [f for f in ASSET_FIELDS if f not in seen]
    if missing:
        issues.append({"kind": "missing_section",
                       "detail": "未覆盖字段：%s" % "、".join(missing)})
    return issues
