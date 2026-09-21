# Skill 契约总览（单一源）

本目录是**契约声明的单一源**；校验实现位于 `server-platform/server/skills.py`，
测试位于 `server-platform/tools/test_skills.py`。

> **为什么声明与实现分开**：声明是给人看的（评审契约是否合理），实现是给机器执行的
> （校验是否严格）。两者分离才能"契约先评审、实现再跟上"；若混在一处，
> 改契约等于改代码，评审就没了着力点。

## 为什么需要这一层（而不是各引擎自己判断）

审计（`docs/AUDIT.md` 模块 4）结论：项目**有实质能力，但未收口为机制** ——
`truncate_text`（结构感知裁剪）、`_detect_mojibake`（乱码探测）、预算分配
都已存在，但各引擎**各自处理**，导致：

- 输入：无统一 schema，各 `build_*_context` 自行容错
- 输出：LLM 返回文本**直接落库**，无结构校验
- 污染：`_detect_mojibake` **只用于存证标记**，未用于写入把关
- 丢弃：无回执 —— 静默丢弃会让"结果变少"无法归因

## 契约表

| Skill | 输入键 | 输出形态 | 必需段落 |
|---|---|---|---|
| `analyze` | `terminal_id`（必填）、`issue` | 文本 | 故障原因分析 / 处理意见 / 风险提示 |
| `diagnose` | `hwinfo` `os_info` `perf_analysis` `perf_stress` `system_log` `network`（均可选） | 文本 | 故障原因分析 / 处理意见 / 风险提示 |
| `ipconflict` | `conflict_reports` `deep_task` `admission` `sources` | 文本 | 冲突判定 / 冲突画像 / 处理意见 / 风险提示 |
| `routetrace` | `hops` `route_nodes` `terminal` | 文本 | 路径研判 / 异常识别 / 结论 |
| `asset-locate` | `sources` `terminal` `hints` | **严格 JSON** | 见 `asset-locate/SKILL.md` |

## 三层校验

**① 输入契约** —— 键合法性 / 必填存在 / 值类型可用
> 未知键**不报错但要回执**：调用方常常以为传进去了，实际引擎没读。

**② 输出契约** —— 必需段落齐全 / 非空 / 长度下限
> **容错原则（关键）**：LLM 输出天然有变体（`【X】` / `**X**` / `X：` / `X`），
> 校验只验"该说的说了没有"，**不验逐字复现模板**。否则会把正常输出全判死 ——
> 那比没有校验层更糟。

**③ 污染检测** —— 三类
| 类型 | 判据 | 危害 |
|---|---|---|
| `mojibake` | 复用 `ai._detect_mojibake` | 编码损坏内容进库，后续无法区分"模型说的"与"乱码" |
| `privacy_violation` | 输出含聊天记录/浏览历史/个人文件内容等词 | **违反 SOUL 准则五**：本机个人内容不得进入输出 |
| `offtopic` | 输出含"作为一个AI""请咨询专业人士"等 | 答非所问，浪费用户等待 |

## asset-locate 的额外契约（最严格的一个）

它输出 JSON，所以能验的比文本引擎多：

- `field` ∈ `{floor, room, department, user}`，**四个必须都有**（没有也要显式 `null`）
- `confidence` ∈ `{high, medium, low, none}`
- **`confidence=none` 却给了 `value`** → 违约（对应 prompt 里"证据不足必须给 null"）
- **有 `value` 却无 `evidence`** → 违约（对应"严禁凭常识填值"）

后两条把 prompt 里的**文字约束变成了可校验的代码约束** —— 这是 Skill 层的核心价值：
把"模型承诺遵守"升级为"系统强制检查"。

## 当前状态

- ✅ 契约表 + 校验实现 + 27 项测试全部通过
- ⏳ **尚未接入调用链** —— 引擎仍直接落库。接入需配套回归验证
  （会改变生产失败判定行为），故单独作为一步

接入点（后续）：
- `api.py::run_ai_analysis` —— `analyze` / `ipconflict` / `routetrace`
- `api.py::run_ai_diagnose` —— `diagnose`
- `api.py::run_asset_locate_inference` —— `asset-locate`
