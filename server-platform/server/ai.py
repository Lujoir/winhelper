# -*- coding: utf-8 -*-
"""观枢终端平台服务端 · AI 智能分析（算力平台 OpenAI 兼容接口）。

- 调用：settings `llm.url` / `llm.api_key`（加密存储，ADR-012）/ `llm.model`；
  标准库 urllib，POST /v1/chat/completions；超时 + 1 次重试。
- 上下文聚合（隐私边界见 ADR-017）：仅运维诊断数据——资产摘要、指标统计、
  瓶颈记录、事件、上传文件名列表；不含任何凭据/配置/终端任意文件内容。
- FAULT_PATTERNS：内置常见终端故障模式知识片段，随 prompt 注入。
"""
import json
import time
import urllib.error
import urllib.request

# ============================================================
# 隐私边界公共前缀（2026-09-20，对齐 ai-agent/soul/SOUL.md 准则五）
# ------------------------------------------------------------
# 背景：此前 6 套 prompt **各自硬编码**隐私声明，口径不一（"上下文仅含"/
# "日志包仅含"/"证据仅含"/"证据仅含登记类"），改一处不会影响其余，且无校验
# 手段。现抽为公共常量，由 build_system_prompt 统一注入 —— 口径一致、可集中
# 校验、合规调整只改一处。
# 配置侧对应：ai-agent/agents/{center,client}/*.agent.md §7 隐私边界。
# ============================================================

PRIVACY_BOUNDARY = (
    "隐私边界（不可逾越）：输入仅含终端运维诊断数据，不涉及使用人个人文件"
    "内容、浏览记录、聊天记录与任何凭据；不得在输出中复述或推断这类信息。"
)


def build_system_prompt(role_prompt, extra=""):
    """统一 system prompt 拼装：角色 prompt + 可选知识片段 + 隐私边界公共前缀。

    **所有 AI 引擎必须经此拼装**（而非各自拼接），以保证：
    ① 隐私边界口径一致且无一遗漏（此前 6 套各自硬编码、措辞不一）；
    ② 后续合规调整只改一处，不会漏改某个引擎。
    各角色 prompt 常量本身**不再自带**隐私边界行（避免重复与口径分叉）。
    """
    parts = [role_prompt]
    if extra:
        parts.append(extra)
    parts.append(PRIVACY_BOUNDARY)
    return "\n\n".join(parts)


FAULT_PATTERNS = """常见终端故障模式（知识库片段，供分析参考）：
1. CPU 持续 >85%：多为后台进程失控/病毒挖矿/编译任务；结合进程名与时段判断。
2. 内存可用 <10%：内存泄漏、浏览器多标签、大文件缓存；观察 swap 使用率是否同步上升。
3. 磁盘空间 >80%：日志堆积、临时文件、更新缓存；关注增长速率而非绝对值。
4. swap 使用率 >50%：内存压力已外溢，性能将显著下降。
5. 磁盘 busy_percent 持续 >80：I/O 瓶颈，即使空间充足也会卡顿。
6. 心跳频繁掉线：网络不稳/驱动/休眠唤醒问题；结合在线率与事件时间线。
7. 异常事件（crash/service）：查看事件 category 与时间聚集性，判断单次或持续。"""

# ⚠️ 使用前必须经 build_system_prompt() 拼装（隐私边界由其统一注入，勿在此重复）
# 场景口径：医院（与客户端 prompt 及项目实际场景一致；此前误写「企业」）
SYSTEM_PROMPT = """你是医院终端运维专家（观枢终端平台 EyeTerm 的智能分析引擎）。
输入是某台受管终端的运维诊断上下文（资产、性能指标统计、瓶颈记录、事件、上传文件清单）。
请输出：
【故障原因分析】按可能性排序，引用数据依据；
【处理意见】分「立即处理」「建议观察」两档，给出可执行步骤；
【风险提示】如需更多数据，说明需要哪类信息。
约束：只基于给出的数据分析，不要编造数据；中文输出；简洁专业。"""


def build_context(store, terminal_id, hours=6):
    """聚合运维诊断上下文（结构化文本，截断到 max_chars）。"""
    now = int(time.time())
    since = now - hours * 3600
    t = store.get_terminal(terminal_id)
    if not t:
        return None
    points = store.query_metrics(terminal_id, since)
    cpus = [p["cpu_percent"] for p in points if p.get("cpu_percent") is not None]
    mems = [p["mem_available_percent"] for p in points
            if p.get("mem_available_percent") is not None]
    swaps = [p["swap_percent"] for p in points if p.get("swap_percent") is not None]

    def stat(name, values, fmt="%.1f"):
        if not values:
            return "%s: 无数据" % name
        return "%s: 均值=%s 峰值=%s 最低=%s 样本=%d" % (
            name, fmt % (sum(values) / len(values)), fmt % max(values),
            fmt % min(values), len(values))

    disks = points[-1]["disks"] if points else []
    disk_line = "; ".join(
        "%s %.1f%%" % (d.get("mount", "?"), float(d.get("percent") or 0))
        for d in disks) or "无数据"
    bottlenecks = store.list_bottlenecks(limit=15, terminal_id=terminal_id,
                                         since_ts=since)
    events = store.list_events(limit=15, terminal_id=terminal_id, since_ts=since)
    uploads = [u["filename"] for u in store.list_uploads(limit=10,
                                                         terminal_id=terminal_id)]
    lines = [
        "【终端】%s（%s）%s | 客户端 %s | IP %s" % (
            t["hostname"] or t["terminal_id"], t["terminal_type"],
            t["os_info"], t["client_version"], t["ip"]),
        "【硬件】CPU %s x%s | 内存 %s MB | 磁盘 %s GB | GPU %s" % (
            t.get("cpu_model") or "?", t.get("cpu_cores") or "?",
            t.get("mem_total_mb") or "?", t.get("disk_total_gb") or "?",
            t.get("gpu_info") or "?"),
        "【近 %d 小时指标】" % hours,
        "  " + stat("CPU", cpus),
        "  " + stat("内存可用率", mems),
        "  " + stat("SWAP", swaps),
        "  磁盘(最近): " + disk_line,
        "【瓶颈记录】%d 条" % len(bottlenecks),
    ]
    for b in bottlenecks:
        lines.append("  %s %s %s=%.1f 阈值=%.1f [%s]" % (
            time.strftime("%m-%d %H:%M", time.localtime(b["ts"])),
            b["kind"], b["metric_key"], float(b["value"] or 0),
            float(b["threshold"] or 0), b["level"]))
    lines.append("【事件】%d 条" % len(events))
    for e in events:
        lines.append("  %s [%s] %s: %s" % (
            time.strftime("%m-%d %H:%M", time.localtime(e["ts"])),
            e["level"], e["category"], e["message"][:120]))
    lines.append("【近期上传文件】%s" % (", ".join(uploads) or "无"))
    return "\n".join(lines)[:6000]


# ---------------- 终端 AI 智能诊断（ADR-023，终端推送日志包 → LLM 结论）----------------

DIAG_SYSTEM_PROMPT = """你是医院终端运维专家（观枢终端平台 EyeTerm 的智能诊断引擎）。
终端侧自动采集了运行日志包（硬件信息/系统信息/性能分析/压测结果/系统日志/网络数据，部分类别可能缺失），
并附运维人员或终端自动生成的问题概述。请输出：
【故障原因分析】按可能性排序，引用日志依据（注明来自哪一类日志）；
【处理意见】分「立即处理」「建议观察」两档，给出可执行步骤；
【风险提示】数据缺失或需要补充采集的部分。
证据可信性硬约束（违反即视为分析无效）：
1. 只允许引用随请求日志中实际出现的事件 ID/来源/时间戳/原文内容，
   严禁编造、推测或凭常识填充任何「日志依据」；
2. 引用事件必须附其在日志中的原文时间戳，无法给出时间戳的依据不得引用；
3. 某类日志未提供、为空或被截断时，必须显式声明「该类证据不足」，
   不得用其它来源或常识推断补位。
约束：只基于给出的日志数据分析，不要编造；中文输出；简洁专业。"""

DIAG_LOG_KEYS = ("hwinfo", "os_info", "perf_analysis", "perf_stress",
                 "system_log", "network")
DIAG_KEY_LABELS = {
    "hwinfo": "硬件信息", "os_info": "系统信息", "perf_analysis": "性能分析",
    "perf_stress": "压测结果", "system_log": "系统日志", "network": "网络数据",
}
# 截断预算（ADR-027）：终端 AI 卡对用户承诺「单类超 32KB 自动截断」，
# 注入与存证上限必须与之一致（历史缺陷：4000 字符截断导致证据饥饿，
# LLM 拿不到问题时段日志而虚构依据——analysis_id=16 事故）。
# - DIAG_RAW_LIMIT / DIAG_EVIDENCE_PER_KEY：单类原始与存证上限 32KB（承诺一致）；
# - DIAG_PROMPT_PER_KEY：单类注入上限 16KB；DIAG_PROMPT_TOTAL：全 prompt 日志
#   注入总预算 32KB（防溢出，处于 LLM 上下文安全范围）；
# - 预算分配：按优先级贪心——system_log 恒第一优先，问题概述命中关键词的类
#   提前，快照类（os_info/hwinfo）垫底，保证日志类不被快照类挤占。
DIAG_RAW_LIMIT = 32 * 1024
DIAG_PROMPT_PER_KEY = 16 * 1024
DIAG_PROMPT_TOTAL = 32 * 1024
DIAG_EVIDENCE_PER_KEY = 32 * 1024
DIAG_PRIORITY_DEFAULT = ("system_log", "perf_analysis", "perf_stress",
                         "network", "os_info", "hwinfo")
# 问题概述关键词 → 日志源关联（小写匹配；用于优先级提前，非强过滤）
_KEY_HINT_WORDS = {
    "network": ("网络", "断网", "上网", "ping", "丢包", "延迟", "dns",
                "wifi", "网关", "ip "),
    "perf_analysis": ("卡顿", "很慢", "缓慢", "cpu", "内存", "性能", "占用"),
    "perf_stress": ("压测", "带宽", "测速"),
    "system_log": ("重启", "蓝屏", "死机", "崩溃", "日志", "错误", "报错",
                   "异常", "开机", "关机", "黑屏"),
    "os_info": ("系统", "版本", "更新", "补丁"),
    "hwinfo": ("硬件", "温度", "磁盘", "硬盘", "显卡", "风扇"),
}
# json.loads 哨兵：区分「解析失败」与「解析结果恰为 None」
_JSON_UNPARSED = object()
_TRUNC_MARK = "…[truncated]"


def _to_text(value):
    """日志值文本化：str 原样；dict/list 压缩 JSON；其余 str()。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _json_dumps(obj):
    """紧凑序列化（ensure_ascii=False 省预算，中文不转义）。"""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _try_parse_json(text):
    """尝试把文本解析为 JSON；失败返回哨兵（不抛异常）。"""
    if not isinstance(text, str) or len(text) > DIAG_RAW_LIMIT * 4:
        return _JSON_UNPARSED
    try:
        return json.loads(text)
    except ValueError:
        return _JSON_UNPARSED


def _clip_json_list(items, budget):
    """列表按「完整条目」粒度保留头部前缀（二分找最大前缀），返回 (obj, truncated)。"""
    if budget < 2 or not items:
        return ([], True) if items else (items, False)
    lo, hi, best = 1, len(items), []
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = items[:mid]
        if len(_json_dumps(cand)) <= budget:
            best, lo = cand, mid + 1
        else:
            hi = mid - 1
    if best:  # 至少完整保留 1 条
        return best, len(best) < len(items)
    # 连 1 条都放不下：对第一条做值级裁剪（列表结构保持，条目内容截断）
    clipped, _ = _clip_json_value(items[0], max(budget - 2, 1))
    return [clipped], True


def _clip_json_dict(d, budget):
    """字典裁剪：小值键优先完整保留，大值键用剩余预算做键内结构裁剪，
    放不下的尾部键按「完整键」粒度丢弃。返回裁剪后 dict。"""
    out = {}
    out_len = 2  # "{}"
    for k, v in sorted(d.items(), key=lambda kv: len(_json_dumps(kv[1]))):
        k_len = len(_json_dumps(k)) + 1          # "k":
        v_len = len(_json_dumps(v))
        if out_len + k_len + v_len + 1 <= budget:  # +1 逗号
            out[k] = v
            out_len += k_len + v_len + 1
            continue
        rest = budget - out_len - k_len - 1 - 1   # 值裁剪预算（预留逗号+括号）
        if rest > 64:
            clipped, _ = _clip_json_value(v, rest)
            v_len = len(_json_dumps(clipped))
            if out_len + k_len + v_len + 1 <= budget:
                out[k] = clipped
                out_len += k_len + v_len + 1
    return out


def _clip_json_value(value, budget):
    """把 JSON 值裁剪到序列化长度 ≤ budget；返回 (clipped_obj, truncated)。

    结构感知：list 按完整条目、dict 按完整键粒度裁剪；仅字符串标量才字符截断
    （截断发生在对象层，序列化转义由 json.dumps 统一完成——绝不撕裂
    已序列化 JSON 文本，裁剪结果恒可通过 json.loads 还原）。"""
    if budget <= 0:
        return None, True
    try:
        text = _json_dumps(value)
    except (TypeError, ValueError):  # 不可序列化兜底：转字符串按文本截断
        t = str(value)
        if len(t) <= budget:
            return t, False
        return t[:max(budget - len(_TRUNC_MARK), 1)] + _TRUNC_MARK, True
    if len(text) <= budget:
        return value, False
    if isinstance(value, list):
        clipped, truncated = _clip_json_list(value, budget)
        return clipped, truncated
    if isinstance(value, dict):
        clipped = _clip_json_dict(value, budget)
        return clipped, (len(clipped) < len(value)
                         or len(_json_dumps(clipped)) < len(text))
    if isinstance(value, str):  # 超长字符串标量：对象层字符截断
        return value[:max(budget - len(_TRUNC_MARK), 1)] + _TRUNC_MARK, True
    return value, True  # 数值/布尔超限（理论边界）：原样保留


def truncate_text(value, limit):
    """结构感知截断（ADR-027）：

    - JSON 值（dict/list）或 JSON 文本：按「完整事件/完整条目」粒度裁剪到预算内
      再压缩序列化，结果恒可通过 json.loads 校验（截断与否由 logs_stats 记录，
      不往 JSON 里拼文本尾注，避免破坏可解析性）；
    - 非 JSON 文本：保留头部字符截断，尾部标注 …[truncated]；
    - limit 内原样返回。"""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        clipped, _ = _clip_json_value(value, limit)
        return _json_dumps(clipped)
    text = value if isinstance(value, str) else str(value)
    if len(text) <= limit:
        return text
    parsed = _try_parse_json(text)
    if parsed is not _JSON_UNPARSED:
        clipped, _ = _clip_json_value(parsed, limit)
        return _json_dumps(clipped)
    return text[:limit] + "\n" + _TRUNC_MARK


def _count_items(value):
    """事件条数统计口径：顶层 list → len；dict 中首个 list 值 → len；否则 None。"""
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        for v in value.values():
            if isinstance(v, list):
                return len(v)
    return None


def _diag_issue_priority(issue):
    """日志源注入优先级：system_log 恒第一；问题概述命中关键词的类提前
    （问题相关源优先）；其余保持默认序（快照类垫底）。"""
    text = (issue or "").lower()
    hit = [k for k, words in _KEY_HINT_WORDS.items()
           if any(w in text for w in words)]
    order = ["system_log"] + hit
    order += [k for k in DIAG_PRIORITY_DEFAULT if k not in order]
    seen, out = set(), []
    for k in order:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _allocate_diag_budgets(needs, priority):
    """总预算内按优先级贪心分配：高优先类先拿满 min(需求, 单类上限)，
    剩余给低优先类（保证日志类不被快照类挤占）。返回 {key: budget}。"""
    budgets, remaining = {}, DIAG_PROMPT_TOTAL
    for key in priority:
        if key not in needs:
            continue
        give = min(min(needs[key], DIAG_PROMPT_PER_KEY), remaining)
        budgets[key] = give
        remaining = max(remaining - give, 0)
    return budgets


# 疑似乱码探测（ADR-027 加项2）：analysis_id=16 取证定性——乱码为终端采集层
# 代码页错位（GBK↔UTF-8 双向均见：字段级 UI 输入的 issue 正常、子进程采集的
# os_info/system_log 乱码），服务端解码链 strict UTF-8 无错位空间。此处仅做
# 存证侧标记（不修复、不定性），供联调复测直接验证乱码消除。
_MOJIBAKE_PAIRS = (   # 方向A：UTF-8 字节被 GBK 误解码的高频双字产物（正常中文低频）
    "鍚庣", "鏂囦", "绔嬪", "绯荤", "纭洴", "娴嬭", "璇锋", "绠＄",
    "鏈嶅", "鑾峰", "璁板", "瀹㈡", "浠诲", "绋嬪", "鍛戒", "鍝嶅",
)
# 方向B：GBK 字节被 UTF-8 误解码——CJK 语境混入成片西里尔/亚美尼亚/希伯来/
# 阿拉伯字母（analysis_id=16 实测样式「רҵ」即此方向）
_MOJIBAKE_RANGES = (("\u0400", "\u04ff"), ("\u0530", "\u058f"),
                    ("\u0590", "\u05ff"), ("\u0600", "\u06ff"))
_MOJIBAKE_FOREIGN_MIN = 3


def _detect_mojibake(text):
    """疑似乱码探测：U+FFFD / UTF-8→GBK 双字特征 / CJK 语境混入成片
    西里尔·亚美尼亚·希伯来·阿拉伯字母（≥3）→ True。"""
    if not text:
        return False
    if "\ufffd" in text:
        return True
    if any(pair in text for pair in _MOJIBAKE_PAIRS):
        return True
    foreign = sum(1 for ch in text
                  if any(lo <= ch <= hi for lo, hi in _MOJIBAKE_RANGES))
    return foreign >= _MOJIBAKE_FOREIGN_MIN


def _diag_evidence_stats(value):
    """单类日志 32KB 存证裁剪统计（如实记录，供控制台展示）。"""
    text = _to_text(value)
    stats = {"original_chars": len(text), "evidence_chars": 0,
             "original_items": None, "kept_items": None, "truncated": False,
             "suspect_mojibake": _detect_mojibake(text)}
    parsed = value if isinstance(value, (dict, list)) else _try_parse_json(text)
    stats["original_items"] = _count_items(
        None if parsed is _JSON_UNPARSED else parsed)
    if parsed is _JSON_UNPARSED:  # 非 JSON 文本：字符截断
        clipped_text = truncate_text(value, DIAG_EVIDENCE_PER_KEY)
        stats["evidence_chars"] = len(clipped_text)
        stats["truncated"] = len(text) > DIAG_EVIDENCE_PER_KEY
        return stats
    clipped, truncated = _clip_json_value(parsed, DIAG_EVIDENCE_PER_KEY)
    stats["evidence_chars"] = len(_json_dumps(clipped))
    stats["kept_items"] = _count_items(clipped)
    stats["truncated"] = bool(truncated)
    return stats


def build_diagnose_context(issue, logs):
    """构建终端诊断 prompt（user 内容文本）、存证 evidence 与统计 stats。

    logs 六类键均可选（终端采集容错缺省），值可为对象或字符串；
    返回 (prompt_text, evidence_dict, stats_dict)：
    - prompt：单类注入 ≤16KB 且日志总量 ≤32KB（按优先级分配，system_log 与
      问题相关源优先）；因预算未注入的类别在 prompt 中如实声明（防 LLM
      把「未注入」误判为「未采集」而虚构）；
    - evidence：每类 ≤32KB 结构感知裁剪（JSON 类可再 json.loads）；
    - stats：每类 {original_chars, evidence_chars, original_items,
      kept_items, truncated}，落库 context_json.logs_stats 供控制台展示。"""
    logs = logs if isinstance(logs, dict) else {}
    present = [k for k in DIAG_LOG_KEYS if logs.get(k) is not None]
    priority = _diag_issue_priority(issue)
    budgets = _allocate_diag_budgets(
        {k: len(_to_text(logs[k])) for k in present}, priority)
    ordered = [k for k in priority if k in present]  # 分节顺序=优先级序
    sections, evidence, stats = [], {}, {}
    for key in ordered:
        evidence[key] = truncate_text(logs[key], DIAG_EVIDENCE_PER_KEY)
        stats[key] = _diag_evidence_stats(logs[key])
        budget = budgets.get(key, 0)
        if budget > 0:
            sections.append("【%s】\n%s" % (DIAG_KEY_LABELS[key],
                                            truncate_text(logs[key], budget)))
    skipped = [DIAG_KEY_LABELS[k] for k in ordered
               if budgets.get(k, 0) <= 0]
    if not sections:
        sections.append("（终端未提供日志数据，请基于问题概述给出一般性建议）")
    elif skipped:
        sections.append("（注：以下日志源因总预算限制未随本请求注入：%s；"
                        "如需分析请说明，将按类别重新拉取）" % "、".join(skipped))
    head = "【问题概述】\n%s" % (issue or "（未填写）")
    return head + "\n\n" + "\n\n".join(sections), evidence, stats


def llm_chat(url, api_key, model, messages, timeout=60, max_retries=1):
    """OpenAI 兼容 chat/completions 调用。返回 dict(ok, content, error, model, can_fallback)。

    can_fallback=True 表示失败原因允许切换备选模型（超时/连接/5xx/429/模型不可用）；
    4xx 鉴权/参数类错误 can_fallback=False（切模型无意义）。"""
    if not url:
        return {"ok": False, "error": "llm.url not configured", "can_fallback": False}
    if not api_key:
        return {"ok": False, "error": "llm.api_key not configured", "can_fallback": False}
    if not model:
        return {"ok": False, "error": "llm.model not configured", "can_fallback": False}
    base = url.rstrip("/")
    endpoint = base + ("/v1/chat/completions"
                       if not base.endswith("/v1/chat/completions") else "")
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 0.3,
    }).encode("utf-8")
    last_error = None
    can_fallback = True
    for attempt in range(max_retries + 1):
        req = urllib.request.Request(endpoint, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer " + api_key)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = ""
            choices = data.get("choices") or []
            if choices:
                content = ((choices[0].get("message") or {}).get("content") or "")
            return {"ok": True, "content": content,
                    "model": data.get("model", model)}
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")[:300]
            except OSError:
                pass
            last_error = "HTTP %d: %s" % (exc.code, body)
            if exc.code < 500 and exc.code != 429:
                can_fallback = False  # 鉴权/参数类错误：切换模型无意义
                break  # 4xx 除 429 外不重试
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last_error = str(exc)
        time.sleep(1)
    return {"ok": False, "error": last_error or "unknown error",
            "can_fallback": can_fallback}


def llm_chat_chain(url, api_key, models, messages, timeout=60, max_retries=1):
    """模型链调用：主模型 → 备选模型（优先级降级）。

    切换条件：超时/连接失败/5xx/429/模型不可用（can_fallback=True 的失败）；
    4xx 鉴权/参数类错误不切换直接返回。返回最后一次结果（含 tried_models 实际尝试链）。"""
    models = [m for m in (models or []) if m]
    if not models:
        return {"ok": False, "error": "llm.model not configured",
                "can_fallback": False, "tried_models": []}
    result = {"ok": False, "error": "unknown error", "can_fallback": False}
    tried = []
    for model in models:
        result = llm_chat(url, api_key, model, messages,
                          timeout=timeout, max_retries=max_retries)
        result["model"] = result.get("model") or model
        tried.append(model)
        if result.get("ok") or not result.get("can_fallback"):
            break
    result["tried_models"] = tried
    return result


# ------------- IP 冲突智能分析（ADR-030：平台聚合四源 → LLM）-------------

IPCONFLICT_SYSTEM_PROMPT = """你是医院终端运维专家（观枢终端平台 EyeTerm 的 IP 冲突智能分析引擎）。
输入是某终端疑似 IP 冲突时平台聚合的多方证据（终端冲突上报记录、深度检测编排结果、
画方准入资产、数据源状态位，部分来源可能缺失）。请输出：
【冲突判定】按证据强度给出结论倾向（确认冲突/疑似冲突/无冲突/证据不足），说明依据；
【冲突画像】涉事终端/IP/MAC 的归属、网卡变更史、接入位置（仅限证据中实际出现的
   字段——NAD 准入登记/深度检测结果；证据未覆盖的归属一律声明「该类证据不足」，
   禁止按 IP 网段、地址段或常识推测任何区域/VLAN/网段归属）；
【处理意见】分「立即处理」「建议观察」两档，给出可执行步骤（如换 IP、查环路、更新准入登记）；
【风险提示】数据缺失或需补充采集的部分。
证据可信性硬约束（违反即视为分析无效）：
1. 只允许引用随请求证据中实际出现的事件 ID/来源/时间戳/MAC/IP/端口，严禁编造、
   推测或凭常识填充任何「日志依据」；
2. 引用证据必须附其在证据中的原文时间戳或记录字段，无法给出依据的不得引用；
3. 某类数据源未提供、为空或被截断时，必须显式声明「该类证据不足」，
   不得用其它来源或常识推断补位。
约束：只基于给出的证据分析，不要编造；中文输出；简洁专业。"""

# 预算（对齐 ADR-027 结构感知设施）：单类注入 ≤16KB、总量 ≤32KB、存证每类 ≤32KB
IPCONFLICT_PER_KEY = DIAG_PROMPT_PER_KEY
IPCONFLICT_TOTAL = DIAG_PROMPT_TOTAL
IPCONFLICT_EVIDENCE_PER_KEY = DIAG_EVIDENCE_PER_KEY
# 优先级：冲突证据 > 深度检测 > 准入资产 > 状态位
IPCONFLICT_PRIORITY = ("conflict_reports", "deep_task", "admission", "sources")
_IPCONFLICT_LABELS = {
    "conflict_reports": "终端冲突上报记录",
    "deep_task": "深度检测编排结果",
    "admission": "准入资产关联",
    "sources": "数据源状态位",
}


def build_ipconflict_context(issue, pkg):
    """聚合证据包 → LLM user 文本 + 存证 + stats（ADR-027 结构感知同款）。

    pkg 各源可缺（None/缺失键=该类证据不足，如实声明）；
    返回 (prompt_text, evidence_dict, stats_dict)。"""
    pkg = pkg if isinstance(pkg, dict) else {}
    needs, texts = {}, {}
    for key in IPCONFLICT_PRIORITY:
        value = pkg.get(key)
        if value is None:
            continue
        texts[key] = _to_text(value)
        needs[key] = len(texts[key])
    budgets = _allocate_diag_budgets(needs, IPCONFLICT_PRIORITY)
    sections, evidence, stats = [], {}, {}
    for key in IPCONFLICT_PRIORITY:
        if key not in texts:
            stats[key] = {"original_chars": 0, "evidence_chars": 0,
                          "truncated": False, "missing": True}
            continue
        raw = texts[key]
        inject = (truncate_text(raw, budgets[key])
                  if budgets.get(key, 0) > 0 else "")
        evidence[key] = truncate_text(raw, IPCONFLICT_EVIDENCE_PER_KEY)
        stats[key] = {"original_chars": len(raw),
                      "evidence_chars": len(evidence[key]),
                      "truncated": budgets.get(key, 0) < len(raw),
                      "missing": False}
        if inject:
            sections.append("【%s】\n%s" % (_IPCONFLICT_LABELS[key], inject))
    skipped = [_IPCONFLICT_LABELS[k] for k in IPCONFLICT_PRIORITY
               if k not in texts]
    if not sections:
        sections.append("（平台未聚合到冲突证据，请基于问题概述给出一般性建议）")
    elif skipped:
        sections.append("（注：以下证据源本次未提供：%s；"
                        "分析时请声明该类证据不足）" % "、".join(skipped))
    head = "【问题概述】\n%s" % (issue or "（未填写）")
    return head + "\n\n" + "\n\n".join(sections), evidence, stats


# ------------- 路由追踪 AI 研判（ADR-031：终端 hops + 知识库 → LLM）-------------

ROUTETRACE_SYSTEM_PROMPT = """你是医院网络运维专家（观枢终端平台 EyeTerm 的路由追踪研判引擎）。
输入是某终端 tracert 路由追踪结果（逐跳 IP/主机名/延迟/超时/区域标注）与平台知识库
路由表（网段→区域映射，部分跳点可能未被知识库覆盖）。请输出：
【路径研判】判定源与目的所属区域；按跳序重建路径经过的区域链；
【异常识别】连续超时跳 / 非预期区域跳（知识库未覆盖的 IP 如实标注「知识库未覆盖」，
不得猜测归属）/ 延迟突增段 / 绕行路径；
【结论】路由是否存在问题（正常 / 疑似 / 需关注），给出依据与建议（如核查某跳设备、
调整路由、确认防火墙策略）。
证据可信性硬约束（违反即视为分析无效）：
1. 只允许引用随请求的 hops 记录与知识库路由表中实际出现的内容（IP/主机名/延迟/
   区域），严禁编造、推测或凭常识填充跳点与区域归属；
2. 引用跳点必须附其在 hops 中的跳数序号，无法给出序号的依据不得引用；
3. 跳点未被知识库覆盖、数据缺失或被截断时，必须显式声明「该跳证据不足/知识库未覆盖」，
   不得用其它来源或常识推断补位；
4. 禁止根据 IP 地址段、相邻跳点或常识推测任何区域/VLAN/网段归属——知识库
   （route_nodes）未覆盖的跳点，一律只标注「知识库未覆盖」，不做任何归属推断
   或建议性猜测；知识库的补充只能来自管理员提供的权威数据。
约束：只基于给出的证据分析，不要编造；中文输出；简洁专业。"""

ROUTETRACE_PRIORITY = ("hops", "route_nodes", "terminal")
# 逐类上限（任务书口径）：hops 16KB / 知识库 8KB / 终端信息 4KB
ROUTETRACE_CAPS = {"hops": 16 * 1024, "route_nodes": 8 * 1024,
                   "terminal": 4 * 1024}
ROUTETRACE_TOTAL = DIAG_PROMPT_TOTAL
ROUTETRACE_LABELS = {"hops": "路由追踪跳点（终端上传）",
                     "route_nodes": "知识库路由表（网段→区域）",
                     "terminal": "终端信息"}


def _allocate_budgets(needs, priority, caps, total):
    """通用预算分配：按优先级贪心，逐类上限 caps[key]（缺失=无上限）。"""
    budgets, remaining = {}, total
    for key in priority:
        if key not in needs:
            continue
        cap = caps.get(key) if caps else None
        give = min(min(needs[key], cap) if cap else needs[key], remaining)
        budgets[key] = give
        remaining = max(remaining - give, 0)
    return budgets


def build_routetrace_context(issue, pkg):
    """路由追踪证据包 → LLM user 文本 + 存证 + stats（ADR-031）。

    pkg: {target, hops(必填，list), route_nodes(kb 全量), terminal(名称/网段)}；
    返回 (prompt_text, evidence_dict, stats_dict)。"""
    pkg = pkg if isinstance(pkg, dict) else {}
    needs, texts = {}, {}
    for key in ROUTETRACE_PRIORITY:
        value = pkg.get(key)
        if value is None:
            continue
        texts[key] = _to_text(value)
        needs[key] = len(texts[key])
    budgets = _allocate_budgets(needs, ROUTETRACE_PRIORITY,
                                ROUTETRACE_CAPS, ROUTETRACE_TOTAL)
    sections, evidence, stats = [], {}, {}
    for key in ROUTETRACE_PRIORITY:
        if key not in texts:
            stats[key] = {"original_chars": 0, "evidence_chars": 0,
                          "truncated": False, "missing": True}
            continue
        raw = texts[key]
        inject = (truncate_text(raw, budgets[key])
                  if budgets.get(key, 0) > 0 else "")
        evidence[key] = truncate_text(raw, ROUTETRACE_TOTAL)
        stats[key] = {"original_chars": len(raw),
                      "evidence_chars": len(evidence[key]),
                      "truncated": budgets.get(key, 0) < len(raw),
                      "missing": False}
        if inject:
            sections.append("【%s】\n%s" % (ROUTETRACE_LABELS[key], inject))
    skipped = [ROUTETRACE_LABELS[k] for k in ROUTETRACE_PRIORITY
               if k not in texts]
    if not sections:
        sections.append("（平台未聚合到路由追踪证据，请基于问题概述给出一般性建议）")
    elif skipped:
        sections.append("（注：以下证据源本次未提供：%s；"
                        "分析时请声明该类证据不足）" % "、".join(skipped))
    head = "【问题概述】\n%s" % (issue or "（未填写）")
    if pkg.get("target"):
        head += "\n【追踪目标】%s" % pkg["target"]
    return head + "\n\n" + "\n\n".join(sections), evidence, stats


# ------------- 资产定位 AI 推断（ADR-046：首页「资产定位」聚合管线环节）-------------

ASSET_LOCATE_SYSTEM_PROMPT = """你是医院终端资产管理助手（观枢终端平台 EyeTerm 的资产定位推断引擎）。
输入是某台终端从多源聚合的登记/标识证据（火绒登记信息、准入登记名、部门路径、终端标识、
可选的同网段终端命名对照与知识库网段映射，部分来源可能缺失）。请对「楼层(floor)/房间(room)/
科室(department)/使用人(user)」四个字段逐一判断，只输出严格 JSON（无其它文本）：
{"inferences":[{"field":"floor|room|department|user","value":"推断值或null","confidence":"high|medium|low|none","evidence":["依据条目"],"note":"value为null时填'证据不足，无法推断'"}]}
证据可信性硬约束（违反即视为输出无效）：
1. 只基于给定数据推断，每条推断的 evidence 必须引用输入中实际出现的字段值（写明「来源.字段=值」），严禁编造、推测或凭常识填充依据；
2. 依据不足的字段 value 必须为 null 并注明「证据不足，无法推断」，不得强行给值；
3. 禁止按 IP 网段/地址段推测楼层、区域、VLAN、科室归属——除非输入明确提供了知识库网段映射或同网段终端命名对照数据；
4. confidence 口径：high=权威登记字段直出（火绒登记/准入登记等）；medium=多源交叉印证；low=命名模式或单源弱信号；none=无推断。
约束：中文输出；只输出 JSON。"""

ASSET_LOCATE_KB = """登记字段参考（知识库片段，键名以输入实际为准，不得假设未出现的键）：
- 火绒登记信息为控制台自定义键值对，常见实名键：楼层/具体位置/工号/姓名/使用科室；
- 准入登记名常见形如「某某的办公电脑」（登记人姓名信号）；部门路径常见键：部门/OU/科室；
- 计算机名命名规律（如楼层编码）仅在输入提供同网段对照数据时可作为 low 置信度依据。"""

ASSET_LOCATE_PRIORITY = ("sources", "terminal", "hints")
# 逐类上限：登记证据 24KB / 终端标识 4KB / 对照线索 4KB，总注入 ≤32KB（ADR-027 同款）
ASSET_LOCATE_CAPS = {"sources": 24 * 1024, "terminal": 4 * 1024,
                     "hints": 4 * 1024}
ASSET_LOCATE_TOTAL = DIAG_PROMPT_TOTAL
ASSET_LOCATE_EVIDENCE_PER_KEY = DIAG_EVIDENCE_PER_KEY
ASSET_LOCATE_LABELS = {"sources": "多源登记证据", "terminal": "终端标识",
                       "hints": "对照线索"}
ASSET_FIELDS = ("floor", "room", "department", "user")
_INSUFFICIENT_NOTE = "证据不足，无法推断"
ASSET_DISCLAIMER = "AI 推测，非权威数据"
_EVIDENCE_MAX_ENTRIES = 6
_EVIDENCE_MAX_CHARS = 200
_ASSET_VALUE_MAX_CHARS = 100
_ASSET_FIELD_ALIAS = {
    "floor": "floor", "楼层": "floor",
    "room": "room", "房间": "room", "位置": "room", "具体位置": "room",
    "department": "department", "科室": "department", "部门": "department",
    "使用科室": "department",
    "user": "user", "使用人": "user", "用户": "user", "姓名": "user",
    "owner": "user",
}
_ASSET_CONF_ALIAS = {
    "high": "high", "高": "high",
    "medium": "medium", "mid": "medium", "中": "medium",
    "low": "low", "低": "low",
    "none": "none", "无": "none",
}
_ASSET_HINT_LABELS = {
    "name_pattern_peers": "同网段在线终端计算机名对照（有据参考）",
    "vlan_kb": "知识库网段映射（有据参考；未提供则禁止按网段推测归属）",
}


def _asset_sources_text(sources):
    """命中源列表 → 可读证据文本（保留原始键名，供 LLM 按「来源.字段=值」引用）。

    兼容 dict 形态（键=source）；matched 容忍 bool/int/str。"""
    if isinstance(sources, dict):
        sources = [dict(v, source=k) if isinstance(v, dict)
                   else {"source": k, "fields": v}
                   for k, v in sources.items()]
    lines = []
    idx = 0
    for s in sources or []:
        if not isinstance(s, dict):
            continue
        idx += 1
        key = str(s.get("source") or "unknown").strip() or "unknown"
        label = str(s.get("source_label") or "").strip() or key
        matched = s.get("matched")
        if matched in (True, 1, "1", "true"):
            mtag = "已命中"
        elif matched in (False, 0, "0", "false"):
            mtag = "未命中"
        else:
            mtag = "命中状态未标注"
        lines.append("◆ 来源%d「%s」（%s，%s）" % (idx, label, key, mtag))
        fields = s.get("fields")
        if isinstance(fields, dict) and fields:
            for k in sorted(fields):
                v = fields[k]
                if v in (None, ""):
                    continue
                lines.append("  %s=%s" % (k, _to_text(v)[:200]))
        else:
            lines.append("  （该源未提供字段）")
    return "\n".join(lines)


def build_asset_locate_context(profile):
    """资产定位证据包 → LLM user 文本 + 存证 + stats（ADR-027 结构感知同款）。

    profile: {terminal(可选), sources(可选，list/dict), hints(可选)}——
    全部键均可缺（缺失在 prompt 尾部如实声明，防 LLM 把「未提供」当「无线索」）。
    返回 (prompt_text, evidence_dict, stats_dict)。"""
    prof = profile if isinstance(profile, dict) else {}
    raws = {}
    terminal = prof.get("terminal")
    if isinstance(terminal, dict) and terminal:
        tlines = []
        for k in ("terminal_id", "hostname", "ip", "mac", "os_info"):
            v = terminal.get(k)
            if v in (None, ""):
                continue
            tlines.append("%s=%s" % (k, _to_text(v)[:200]))
        if tlines:
            raws["terminal"] = "\n".join(tlines)
    sources_text = _asset_sources_text(prof.get("sources"))
    if sources_text:
        raws["sources"] = sources_text
    hints = prof.get("hints")
    if isinstance(hints, dict) and hints:
        hlines = []
        for k in sorted(hints):
            v = hints[k]
            if v in (None, "", [], {}):
                continue
            label = _ASSET_HINT_LABELS.get(k, str(k))
            hlines.append("%s: %s" % (label, _to_text(v)[:400]))
        if hlines:
            raws["hints"] = "\n".join(hlines)
    budgets = _allocate_budgets({k: len(v) for k, v in raws.items()},
                                ASSET_LOCATE_PRIORITY, ASSET_LOCATE_CAPS,
                                ASSET_LOCATE_TOTAL)
    sections, evidence, stats = [], {}, {}
    for key in ASSET_LOCATE_PRIORITY:
        if key not in raws:
            stats[key] = {"original_chars": 0, "evidence_chars": 0,
                          "truncated": False, "missing": True}
            continue
        raw = raws[key]
        inject = (truncate_text(raw, budgets[key])
                  if budgets.get(key, 0) > 0 else "")
        evidence[key] = truncate_text(raw, ASSET_LOCATE_EVIDENCE_PER_KEY)
        stats[key] = {"original_chars": len(raw),
                      "evidence_chars": len(evidence[key]),
                      "truncated": budgets.get(key, 0) < len(raw),
                      "missing": False}
        if inject:
            sections.append("【%s】\n%s" % (ASSET_LOCATE_LABELS[key], inject))
    skipped = [ASSET_LOCATE_LABELS[k] for k in ASSET_LOCATE_PRIORITY
               if k not in raws]
    if not sections:
        sections.append("（未提供任何登记命中证据、终端标识与对照线索）")
    elif skipped:
        sections.append("（注：以下证据源本次未提供：%s；"
                        "涉及字段请声明证据不足）" % "、".join(skipped))
    head = "【任务】只基于以下证据，推断该终端的楼层/房间/科室/使用人。\n\n"
    return head + "\n\n".join(sections), evidence, stats


def _asset_atoms(profile):
    """收集输入数据的引用原子（接地校验依据）：来源标识/字段键/字段值/终端值/线索值。"""
    prof = profile if isinstance(profile, dict) else {}
    atoms = set()
    terminal = prof.get("terminal")
    if isinstance(terminal, dict):
        for k, v in terminal.items():
            if k:
                atoms.add(str(k))
            if v not in (None, ""):
                atoms.add(_to_text(v))
    sources = prof.get("sources")
    if isinstance(sources, dict):
        sources = list(sources.values())
    for s in sources or []:
        if not isinstance(s, dict):
            continue
        for key in ("source", "source_label"):
            v = str(s.get(key) or "").strip()
            if v:
                atoms.add(v)
        fields = s.get("fields")
        if isinstance(fields, dict):
            for k, v in fields.items():
                if k:
                    atoms.add(str(k))
                if v not in (None, ""):
                    atoms.add(_to_text(v))
    hints = prof.get("hints")
    if isinstance(hints, dict):
        for k, v in hints.items():
            if k:
                atoms.add(str(k))
            if isinstance(v, dict):
                for kk, vv in v.items():
                    if kk:
                        atoms.add(str(kk))
                    if vv not in (None, ""):
                        atoms.add(_to_text(vv))
            elif isinstance(v, (list, tuple)):
                for vv in v:
                    if vv not in (None, ""):
                        atoms.add(_to_text(vv))
            elif v not in (None, ""):
                atoms.add(_to_text(v))
    return {a.strip() for a in atoms if len(str(a).strip()) >= 2}


def _asset_evidence_grounded(ev, context_text, atoms):
    """依据接地校验（启发式引用完整性防线，非语义真值判定）：

    - 依据原文是 prompt 片段（直接引用）→ 通过；
    - 依据含任一长度 ≥3 的原子（如来源名/值「1310房」/计算机名）→ 通过；
    - 依据含键值分隔符（=/：/:）时，键部含任一原子（如「楼层」）→ 通过；
    - 其余（凭空叙述、编造编码规则等）→ 不通过（该条依据剔除）。"""
    ev = str(ev or "").strip()
    if not ev:
        return False
    if ev in context_text:
        return True
    if any(a in ev for a in atoms if len(a) >= 3):
        return True
    for sep in ("=", "：", ":"):
        if sep in ev:
            key_part = ev.split(sep, 1)[0]
            return any(a in key_part for a in atoms)
    return False


def _asset_normalize_field(raw):
    for key in (str(raw or "").strip(), str(raw or "").strip().lower()):
        if key in _ASSET_FIELD_ALIAS:
            return _ASSET_FIELD_ALIAS[key]
    return None


def _asset_insufficient(field, note=""):
    return {"field": field, "value": None, "confidence": "none",
            "evidence": [], "note": (note or _INSUFFICIENT_NOTE)[:120]}


def _validate_asset_inferences(raw_list, context_text, atoms):
    """模型输出校验层（ADR-046 编造拒绝）：

    - field/confidence 白名单 + 中文别名归一；不可辨 → 条目剔除；
    - value 为空 → 保留为「证据不足」null 条（如实口径）；
    - evidence 逐条接地校验，无接地依据的条目整体剔除（引用完整性防线）；
    - 同字段重复保留首条；value/evidence 做长度钳制。
    dropped 计数口径：被剔除的条目数 + 被剔除的编造 evidence 条数。
    返回 (kept_entries, dropped_count)。"""
    kept, dropped = [], 0
    for item in raw_list:
        if not isinstance(item, dict):
            dropped += 1
            continue
        field = _asset_normalize_field(item.get("field"))
        if field not in ASSET_FIELDS:
            dropped += 1
            continue
        value = item.get("value")
        note = str(item.get("note") or "").strip()
        if value in (None, ""):
            kept.append(_asset_insufficient(field, note))
            continue
        conf = _ASSET_CONF_ALIAS.get(
            str(item.get("confidence") or "").strip().lower())
        if conf not in ("high", "medium", "low"):
            dropped += 1     # 置信度不可辨 → 条目不可信
            continue
        ev_raw = item.get("evidence")
        ev_list = ev_raw if isinstance(ev_raw, list) else \
            ([ev_raw] if ev_raw else [])
        ev_kept = []
        for e in ev_list:
            e = _to_text(e).strip()[:_EVIDENCE_MAX_CHARS]
            if e and _asset_evidence_grounded(e, context_text, atoms):
                ev_kept.append(e)
            else:
                dropped += 1     # 编造依据条（无接地引用）
            if len(ev_kept) >= _EVIDENCE_MAX_ENTRIES:
                break
        if not ev_kept:
            dropped += 1     # 无可引用依据 → 拒绝（编造防线）
            continue
        kept.append({"field": field, "value": _to_text(value).strip()
                     [:_ASSET_VALUE_MAX_CHARS], "confidence": conf,
                     "evidence": ev_kept, "note": note[:120]})
    seen = set()
    final = []
    for item in kept:
        if item["field"] in seen:
            dropped += 1
            continue
        seen.add(item["field"])
        final.append(item)
    return final, dropped


def _asset_canonical_fill(entries):
    """补齐四字段固定顺序（模型未给的字段补「证据不足」null 条）。"""
    by_field = dict((e["field"], e) for e in entries)
    return [by_field.get(f) or _asset_insufficient(f)
            for f in ASSET_FIELDS]


def _extract_json_object(text):
    """从模型输出提取首个 JSON 对象（容忍代码围栏/前后缀文本）；失败 None。"""
    if not isinstance(text, str):
        return None
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def run_asset_locate_inference(ctx, profile, terminal_id=None):
    """资产定位 AI 推断（ADR-046，内部函数供 asset-locate 管线调用，无 HTTP 路由）。

    输入 profile（与 asset-mgmt-dev 聚合管线对齐）：多源登记证据包（全键可选）。
    输出 inference 块（并入 asset-locate 响应）：
    {status: ok|unavailable, inferences[{field, value, confidence, evidence, note}],
     disclaimer, model, dropped/unavailable_reason, analysis_id, duration_ms}
    - 四字段恒齐全（模型未给或依据不足 → null 条「证据不足，无法推断」）；
    - LLM 全链失败/输出不可解析/校验全灭 → unavailable+原因（管线不因 AI 挂）；
    - 零证据短路：无任何输入原子 → 确定性四字段「证据不足」块，不消耗模型调用；
    - terminal_id 提供时落 ai_analyses（trigger=asset_locate）入 AI 分析历史。"""
    from_api_started = time.time()
    prof = profile if isinstance(profile, dict) else {}
    prompt_text, evidence, stats = build_asset_locate_context(prof)
    atoms = _asset_atoms(prof)
    context_record = {"kind": "asset_locate", "sections": evidence,
                      "stats": stats}
    store = getattr(ctx, "store", None)

    def _persist(status, error, model, response_text):
        if store is None or not terminal_id:
            return None
        try:
            return store.ai_insert(
                terminal_id=terminal_id, ts=int(time.time()),
                trigger="asset_locate", issue="资产定位推断",
                context=context_record, response_text=response_text[:32 * 1024],
                status=status, error=error, model=model,
                duration_ms=int((time.time() - from_api_started) * 1000))
        except Exception:
            return None     # 落库失败不阻断推断结果返回

    def _result(block, status, error="", model="", response_text=""):
        block["analysis_id"] = _persist(status, error, model, response_text)
        block["duration_ms"] = int((time.time() - from_api_started) * 1000)
        return block

    if not atoms:
        # 零证据短路：确定性如实回答（四字段证据不足），不消耗模型调用
        return _result({"status": "ok",
                        "inferences": _asset_canonical_fill([]),
                        "disclaimer": ASSET_DISCLAIMER, "model": "",
                        "dropped": 0, "shortcircuit": "no_evidence"},
                       status="ok", model="")

    messages = [
        {"role": "system",
         "content": build_system_prompt(ASSET_LOCATE_SYSTEM_PROMPT,
                                       ASSET_LOCATE_KB)},
        {"role": "user", "content": prompt_text},
    ]
    # 超时预算（2026-09-19 修复）：AI 推断是资产定位的**增益项**，不是必需项
    # （管线契约：AI 失败降级 available=false，确定数据照常返回）。原 timeout=45
    # 且走「主模型 + 备选模型」链 → 最坏 90 秒，远超控制台前端 25 秒预算，
    # 导致用户连三源确定数据都看不到（实测报「定位超时」）。收紧为 8 秒/模型，
    # 最坏 16 秒，留足余量给 nad 检索（10 秒/请求）。
    result = llm_chat_chain(ctx.settings.get("llm.url"),
                            ctx.settings.get("llm.api_key"),
                            [ctx.settings.get("llm.model"),
                             ctx.settings.get("llm.model_fallback")],
                            messages, timeout=8, max_retries=0)
    if not result.get("ok"):
        return _result({"status": "unavailable", "inferences": [],
                        "disclaimer": ASSET_DISCLAIMER,
                        "model": result.get("model") or "",
                        "unavailable_reason":
                            str(result.get("error") or "unknown")[:200]},
                       status="failed", error=result.get("error") or "",
                       model=result.get("model") or "",
                       response_text="")
    raw_content = result.get("content") or ""
    obj = _extract_json_object(raw_content)
    raw_list = obj.get("inferences") if obj else None
    if not isinstance(raw_list, list) or not raw_list:
        return _result({"status": "unavailable", "inferences": [],
                        "disclaimer": ASSET_DISCLAIMER,
                        "model": result.get("model") or "",
                        "unavailable_reason": "model_output_invalid"},
                       status="failed", error="model_output_invalid",
                       model=result.get("model") or "",
                       response_text=raw_content)
    entries, dropped = _validate_asset_inferences(raw_list, prompt_text, atoms)
    if not entries:
        # 全部条目未通过接地校验：不产出可信度存疑的空块
        return _result({"status": "unavailable", "inferences": [],
                        "disclaimer": ASSET_DISCLAIMER,
                        "model": result.get("model") or "",
                        "unavailable_reason": "model_output_validation_failed",
                        "dropped": dropped},
                       status="failed", error="model_output_validation_failed",
                       model=result.get("model") or "",
                       response_text=raw_content)
    context_record["dropped"] = dropped
    return _result({"status": "ok",
                    "inferences": _asset_canonical_fill(entries),
                    "disclaimer": ASSET_DISCLAIMER,
                    "model": result.get("model") or "",
                    "dropped": dropped},
                   status="ok", model=result.get("model") or "",
                   response_text=raw_content)
