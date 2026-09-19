# 本地 AI Agent 架构系统

EyeTerm 项目内**所有 AI 能力**共用的底座。目标：让每个 AI 模块都有**记忆、会推理、
可治理、守边界、有自我**。

> 负责人：`ai-agent-arch-dev`（见 `.codebuddy/agents/ai-agent-arch-dev.md`）
> 设计取舍记录：`docs/DECISIONS.md`

## 目录规范

```
ai-agent/
  README.md                 本文件：总览 + 接入指引
  soul/
    SOUL.md                 灵魂文件（只读、版本化；会话启动即注入）
    archive/                历史版本（变更时旧版移入）
  memory/<module_id>/       各模块记忆（一模块一目录，物理隔离）
    MEMORY.md               长期记忆（人工可读；只增不改，修正=追加）
    SESSION.jsonl           会话增量（逐行追加，崩溃安全、可回放）
    STATE.json              当前状态（覆盖写；进度指针 + 最近执行摘要）
    archive/                轮转归档段（只读、可检索）
  skills/<skill_id>/        固化 Skill（记忆的**唯一写入通道**）
    SKILL.md                声明：accepts 什么 / 产出什么 / 边界在哪
    input.schema.json       输入结构约束
    output.schema.json      输出结构约束
    validate.py             校验器（输入拒绝 + 输出把关 + 污染检测）
  templates/
    AGENT.template.md       AI 功能 Agent 配置模板（**唯一源**，实例从它派生）
    archive/                模板历史版本（模板变更前必须先归档原版）
  agents/
    center/<id>.agent.md    中心服务侧 AI 功能配置（**源**）
    client/<id>.agent.md    Windows 客户端侧 AI 功能配置（**源**）
  tmp/                      临时中间产物（**唯一允许自动清理**的目录，带 TTL）
  tools/
    cleanup.py              一键清理（默认 dry-run）
    publish_agents.py       发布配置：源 → 功能模块（幂等）
    verify_agent_configs.py 校验门禁：字段/枚举/段落/身份/隐私/一致性/常量（7 项）
  docs/
    DECISIONS.md            架构决策（ADR）
    AUDIT.md                AI 能力合规审计（含问题清单与欠账）
```

## 服务对象与隐私边界（接入必读）

本系统服务**两类对象**，接入时**必须**明确你的模块面向谁：

| 对象 | 在哪 | 典型模块 |
|---|---|---|
| 桌面运维人员 | 中心控制台（多终端） | 终端监控、资产管理、策略下发、发布管理 |
| **终端使用人** | **客户端本机（单机）** | 磁盘清理、应用数据迁移、性能分析、网络排障、日志诊断、文件搜索 |

**隐私线划在「是否离开本机」**（详见 `soul/SOUL.md` 准则五）：

- 使用人**个人内容**（聊天记录 / 文档 / 照片 / 浏览历史）→ **只在本机参与判断，不落中心、不外传**
- 上报中心的**允许项**：健康指标与结果（"C 盘释放 30GB"）
- 上报中心的**禁止项**：个人文件清单、文件名、聊天内容、清理了哪些私人文件

**接入检查**：你的 Skill 产出中**每个字段**都要能回答"这是**结果**还是**内容**"。
判不准 → 按"内容"处理（不上报）。

**面向使用人的模块**默认「只分析、只建议」，破坏性动作需强确认，优先"移动 / 迁移"而非"删除"。

## 分层与优先级

```
Soul    我是谁、坚守什么、长期要把什么做成   → 约束行为取向（不可被自动改写）
Skill   这类任务怎么做、什么算合法产出       → 约束过程与产物（受 Soul 约束）
Memory  我做过什么、知道什么、上次结论       → 提供事实与经验（受前两者过滤）
```

**冲突优先级：Soul > Skill > Memory**。记忆记错了不能改变人格；Skill 判错了不能逾越准则。

## 记忆防串扰的三道闸

1. **物理隔离** —— 一模块一目录，禁止跨目录直接 `open()`
2. **访问收口** —— 读写一律经 `MemoryScope(module_id)`，越界抛异常
3. **命名空间标注** —— 条目带 `module` 字段；跨模块引用必须显式声明 `depends_on`

## 每个 AI 功能都要有 Agent 配置（强制）

**每一项 AI 能力都必须在两侧（中心服务 / Windows 客户端）有独立配置文件**，
且遵循「模板 → 实例 → 归档 → 发布」流水线：

```
templates/AGENT.template.md          ← 唯一模板（改模板前先归档旧版到 archive/）
        │  复制实例化
        ▼
agents/<env>/<id>.agent.md           ← 源（唯一维护点）
        │  publish_agents.py（机械发布，勿手改副本）
        ▼
功能模块内副本                        ← 中心: server-platform/server/agents/
                                      客户端: net-doctor/agents/
        │  verify_agent_configs.py（7 项门禁）
        ▼
```

| 步骤 | 命令 / 动作 |
|---|---|
| 建配置 | 复制 `templates/AGENT.template.md` → `agents/<env>/<id>.agent.md`，**11 个段落一个都不能少** |
| 归档模板 | 改模板前先把当前版复制到 `templates/archive/AGENT.template.v<版本>.md` |
| 发布 | `python ai-agent/tools/publish_agents.py`（幂等，会跳过未变项） |
| 校验 | `python ai-agent/tools/verify_agent_configs.py`（**必须全绿**才算完成） |

**现有配置**（7 项 AI 能力）：

| env | 配置 | 对应代码 |
|---|---|---|
| center | `analyze` `diagnose` `ipconflict` `routetrace` `asset-locate` | `server-platform/server/ai.py` |
| client | `netdoctor-ai`（企业版 + 个人版双模式） | `net_service.py` |

## 接入指引（给业务模块）

新模块要把 AI 能力接入底座，按此四步：

1. **建记忆目录**：`memory/<module_id>/`，初始化 `MEMORY.md`（含条目 schema 头）与空 `STATE.json`
2. **定义 Skill**：在 `skills/<skill_id>/` 写 `SKILL.md` + 两个 schema + `validate.py`；
   **声明式**写清 `accepts`（接受什么输入）与产出结构 —— 校验器据此拒绝越界输入
3. **标注触发类型**：在 `SKILL.md` 写明是**交互式 / 事件式 / 定时式**，据此定预算等级
   （P0 交互式必须有硬预算，绝不让用户空手）
4. **只经 Skill 写记忆**：业务代码不得直接 append `MEMORY.md`；所有写入走 Skill 校验通道

## 复用既有能力（不要另起平行链路）

| 能力 | 位置 | 用途 |
|---|---|---|
| 模型调用 + 模型链降级 | `server-platform/server/ai.py: llm_chat / llm_chat_chain` | 主→备选模型，`can_fallback` 判可切换性 |
| 预算分配 | `ai.py: _allocate_diag_budgets` | 总量 32KB 按优先级贪心 |
| 结构感知截断 | `ai.py: truncate_text / _clip_json_*` | 按完整条目裁，不切碎 JSON（ADR-027） |
| 证据存证 | `ai.py: build_diagnose_context → (prompt, evidence, stats)` | prompt 与存证分离 |
| 乱码探测 | `ai.py: _detect_mojibake` | 污染检测第 2 道直接复用 |
| 客户端命令通道 | `uplink.py: COMMAND_HANDLERS` + `store.enqueue_command` | 客户端侧 AI 任务触发与回执 |

## 铁律（血的教训）

1. **AI 是增益项不是必需项** —— 任何 LLM/外部检索都要有硬超时与降级（2026-09-19 资产定位
   因 90s 模型链 + 20s 画方检索撞爆前端 25s 预算，用户连确定数据都看不到）
2. **AI 与确定数据同请求串行** —— AI 慢则整个响应慢（确定数据被一并拖住）。
   服务本身是 `ThreadingHTTPServer`（每请求一线程，不阻塞其他请求），但**同一个请求内**
   确定数据与 AI 是顺序执行的，所以 AI 必须有**独立硬预算**、超时立即降级返回确定数据
3. **失败不覆盖好数据** —— 写缓存/记忆前判断是否有可用旧值
4. **声明与状态分离** —— 人读的（SOUL/SKILL/MEMORY）与机写的（STATE/SESSION/archive）分开
5. **不污染主仓** —— 中间产物落 `ai-agent/tmp/`，禁止在项目根留 `_xxx` 散落文件
6. **丢弃必须有回执** —— 拒绝/丢弃一律返回结构化原因，禁止静默吞掉
