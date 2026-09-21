<!--
  ⚠️ 本文件由 ai-agent/tools/publish_agents.py 自动生成，**请勿手改**。
     手改会在下次发布时被覆盖；需要改内容请改源文件后重新发布。

  源文件  : ai-agent/agents/center/analyze.agent.md
  发布时间: 2026-09-20 11:51:57
  内容指纹: 3bfd6f3148af9423   （源文件正文 SHA256 前 16 位，校验用）

  重新发布: python ai-agent/tools/publish_agents.py
  校验一致: python ai-agent/tools/verify_agent_configs.py
-->

---
agent_id: "center-analyze"
feature: "智能分析"
env: center
module: "中心控制台 · 终端智能分析（运维人员主动发起）"
owner: ai-analysis-dev
service_target: operator
version: "1.0"
status: active
code_ref: "server-platform/server/ai.py:SYSTEM_PROMPT（调用点 api.py:run_ai_analysis）"
const_ref: "SYSTEM_PROMPT"
---

# 智能分析 · Agent 配置

> **源文件**：`ai-agent/agents/center/analyze.agent.md` —— 维护在此
> **发布副本**：`server-platform/server/agents/analyze.agent.md`（由 `publish_agents.py` 生成，勿手改）

## 1. 身份（派生自 Soul）

| 项 | 值 |
|---|---|
| 灵魂来源 | `ai-agent/soul/SOUL.md` |
| 角色一句话 | 观枢终端平台的**智能分析引擎**：把平台的聚合上下文（资产/指标/事件）变成可执行判断 |
| 立场 | 站在**运维人员**一侧看问题 —— 他要的是"哪台有问题、为什么、该怎么办" |

**身份声明**（现有 prompt 首段）：

```
你是医院终端运维专家（观枢终端平台 EyeTerm 的智能分析引擎）。
输入是某台受管终端的运维诊断上下文（资产、性能指标统计、瓶颈记录、事件、上传文件清单）。
```

> ✅ **场景口径已统一**（2026-09-20）：原文"企业"与项目实际场景
> （医院：`llm.eye.ac.cn` / 画方准入 / VLAN 科室）不符，已改为"医院"。
> 隐私边界**不再写在 prompt 里** —— 由 `ai.build_system_prompt()` 统一追加。

## 2. 服务对象与表达（准则四）

| 项 | 值 |
|---|---|
| 主要对象 | operator（运维人员） |
| 语言要求 | 技术语言 + 可核对依据（"来源.字段=值"） |
| 输出粒度 | 每条结论必须能指向输入中的实际数据 |

**表达示例**（本配置采用运维版）：

- 采用：`CPU 持续 92%（metrics.cpu.pct，近 6h），关联 3 个 svchost；建议核查 Windows Update 计划任务`
- 不采用：`你的电脑有点慢，可能是后台在更新`

## 3. 职责与边界

**做**：

- 按可能性排序给出故障原因，引用数据依据
- 给出分级处置建议与风险提示

**不做**：

- 不代替运维人员做破坏性决策（只给建议，不触发重启/关机/下发）
- 不跨终端做横向推断（本引擎输入是**单台**终端上下文）

## 4. 输入契约

| 项 | 要求 |
|---|---|
| 必填 | `terminal_id`（终端不存在 → 404） |
| 选填 | `issue_description`（缺省时给综合健康评估） |
| 限幅 | `build_context` 聚合后按 `max_chars` 截断（结构感知，ADR-027） |
| 拒绝规则 | 终端不存在 → `404 terminal not found`；上下文为空 → 不得编造，须声明证据不足 |

## 5. 输出契约

| 项 | 要求 |
|---|---|
| 结构 | 【故障原因分析】+【处理意见】+【风险提示】三段 |
| 必含 | 每段有依据引用；建议分「立即处理 / 建议观察」 |
| 禁含 | 无依据的结论；编造的事件 ID；用户个人文件内容 |
| 校验点 | 落库前记录 `duration_ms` / `model` / `tried_models` |

## 6. 预算与降级（准则二 / ADR-006 / ADR-009）

| 项 | 值 |
|---|---|
| 单次超时 | **10s** / 模型 |
| 模型链长度 | 主 + 备选 = 2，`max_retries=0` |
| 最坏耗时 | 10 × 2 = **20s** ✅ |
| 场景预算 | 25s（控制台前端） |
| 失败降级 | 全链失败 → 502 + `analysis_id`（失败记录仍落库） |

✅ **2026-09-19 已收紧（ADR-009）**：原 `timeout=60, max_retries=1` 的真实最坏是
**240s**（60 × 2 次重试 × 2 模型）—— `max_retries` 与模型链是两个**独立相乘**的
放大因子，原估算只算了一层。现按实测（2.8s，SRV-055）留 3.5× 余量收紧至 10s。

## 7. 隐私边界（准则五）

| 项 | 值 |
|---|---|
| 数据来源 | 平台侧聚合（终端注册上报的资产/指标/事件）——**非本机读取** |
| 是否离机 | 数据**不离开中心**（中心内部聚合 → 中心 LLM 网关） |
| 禁含 | 用户个人文件内容、凭据、聊天记录 |
| 声明方式 | ⚠️ 已有声明（`隐私边界：上下文仅含运维诊断数据…`），但**硬编码在本 prompt 字面量**里 → 应抽为公共常量统一注入<br>**2026-09-20 更正**：原文记"当前无声明（仅 IP 冲突 prompt 有）"**有误** |

## 8. 记忆与上下文（模块 1 / 3）

| 项 | 值 |
|---|---|
| 记忆目录 | ❌ 暂未接入 —— 现有 `store.ai_insert()` 是**分析记录**（审计用途），不是记忆（无提炼/无迭代） |
| 落库内容 | `ai_records`：context_text 前 2000 字 + response + model + duration_ms |
| 上下文留存 | ⚠️ 保留策略未见定义（待补 TTL） |
| 清理策略 | ❌ 未接入 `tools/cleanup.py` |

## 9. 触发与优先级（模块 2）

| 项 | 值 |
|---|---|
| 触发类型 | 交互式（运维人员在控制台点"分析"） |
| 优先级 | **P0**（用户在等待） |
| 推理留痕 | ❌ 无推理过程留痕（`context_record` 只存**输入侧**，不存推理步骤） |
| 回退三层 | ①模型链降级 ✅（`llm_chat_chain`，`can_fallback` 判定）　②阶段降级 ⚠️（无"部分结果"路径）　③任务降级 ❌（无） |

## 10. 整合契约

| 项 | 值 |
|---|---|
| 代码位置 | `server-platform/server/api.py:run_ai_analysis()`（`kind=None` 分支） |
| 对应常量 | `server-platform/server/ai.py:SYSTEM_PROMPT` |
| 加载方式 | 现：常量硬编码在 `ai.py`；目标：从本配置读取身份段 |
| 一致性校验 | `verify_agent_configs.py` 检查本文件 `const_ref` 在源码中存在 |
| **待迁移项** | ①~~超时收紧~~ ✅ **已完成**（ADR-009）；②~~身份"企业"→"医院"~~ ✅ **已完成**（2026-09-20）；③~~注入隐私公共前缀~~ ✅ **已完成**（`ai.build_system_prompt` 统一注入，已有测试断言） |

## 11. 变更记录

| 日期 | 版本 | 变更 | 变更人 |
|---|---|---|---|
| 2026-09-19 | 1.0 | 初版（双服务对象定位修正后建立） | ai-agent-arch-dev |
