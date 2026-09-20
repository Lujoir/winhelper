---
agent_id: "center-routetrace"
feature: "路由追踪研判"
env: center
module: "中心控制台 · 路由追踪智能研判（终端 hops + 路由知识库 → LLM）"
owner: ai-analysis-dev
service_target: operator
version: "1.0"
status: active
code_ref: "server-platform/server/ai.py:ROUTETRACE_SYSTEM_PROMPT（调用点 api.py:run_ai_analysis kind=routetrace）"
const_ref: "ROUTETRACE_SYSTEM_PROMPT"
---

# 路由追踪研判 · Agent 配置

> **源文件**：`ai-agent/agents/center/routetrace.agent.md` —— 维护在此
> **发布副本**：`server-platform/server/agents/routetrace.agent.md`

## 1. 身份（派生自 Soul）

| 项 | 值 |
|---|---|
| 灵魂来源 | `ai-agent/soul/SOUL.md` |
| 角色一句话 | **路由研判引擎**：读终端上传的 hops 链路 + 路由知识库，判断路径是否异常 |
| 立场 | 站在**运维人员**一侧做网络路径分析 |

**身份声明**：

```
你是医院网络运维专家（观枢终端平台 EyeTerm 的路由追踪研判引擎）。
```

> ✅ **场景口径已统一**（2026-09-20）："企业"→"医院"；隐私边界改由
> `ai.build_system_prompt()` 统一追加。

## 2. 服务对象与表达（准则四）

| 项 | 值 |
|---|---|
| 主要对象 | operator（运维人员） |
| 语言要求 | 技术语言 + **按跳数序号**引用 |
| 输出粒度 | 路径研判 + 异常识别 + 结论三段 |

**表达示例**（本配置采用运维版）：

- 采用：`第 7 跳出现 RTT 突增至 180ms（hops[6]，10:41:22），疑为跨网段瓶颈`
- 不采用：`路上可能有点堵`

## 3. 职责与边界

**做**：

- 判断路径是否异常、异常在哪一跳
- 引用路由知识库（`route_nodes`）说明节点归属

**不做**：

- **禁止按 IP 段推测区域归属**（prompt 硬约束）
- 不臆造 hops 中不存在的跳数或节点名

## 4. 输入契约

| 项 | 要求 |
|---|---|
| 必填 | `route_ctx` 需含 `hops`（hops 非法/空 → 聚合返回 None → **回退一般性分析**） |
| 选填 | 知识库 `route_nodes`（sources 标注 `kb` / `empty`）、`terminal` 上下文 |
| 限幅 | 三源预算：`hops` **16KB** / `route_nodes` **8KB** / `terminal` **4KB**，总 **32KB** |
| 预算分配 | `_allocate_budgets` 优先级贪心 |
| 拒绝规则 | hops 缺失 → 回退 `SYSTEM_PROMPT` 一般性分析（**不是报错**） |

> ⚠️ **已知契约不一致（未修）**：终端本地转发 BRG-055 的 payload 附加键是 `data`，
> 而 `api.py:279-281` 读 `context` → **routetrace 聚合的 route_ctx 恒定拿不到数据**。
> 详见接口台账 SRV-055 更新记录。此项已记为待修欠账。

## 5. 输出契约

| 项 | 要求 |
|---|---|
| 结构 | 路径研判 + 异常识别 + 结论 三段 |
| 必含 | 引用须附**跳数序号**；缺失显式声明 |
| 禁含 | 按 IP 段推测区域归属；编造跳数/节点；用户个人文件内容 |
| 校验点 | `context_record={kind,target,hops_count,sources,sections,stats}` 落库 |

## 6. 预算与降级（准则二 / ADR-006）

| 项 | 值 |
|---|---|
| 单次超时 | 复用 `run_ai_analysis` 链（现 **10s / max_retries=0**） |
| 最坏耗时 | 10 × 2 = **20s** ✅ |
| 场景预算 | 控制台前端 **25s** |
| 失败降级 | 全链失败 → 502（失败记录仍落库） |

> ✅ **2026-09-19 已随 analyze 一并收紧（ADR-009）**：原链为 `60s / max_retries=1`，
> 真实最坏 **240s**。现最坏 20s，在 25s 场景预算内。

## 7. 隐私边界（准则五）

| 项 | 值 |
|---|---|
| 数据来源 | 终端上传 hops（`terminal_upload`）+ 中心路由知识库 |
| 是否离机 | hops 数据**离机**（终端 → 中心）；知识库在中心 |
| 禁含 | 用户个人文件内容、凭据 |
| 声明方式 | ⚠️ 已有声明（`隐私边界：证据仅含运维诊断数据…`），但硬编码在本 prompt 字面量里 → 待抽为公共常量<br>**2026-09-20 更正**：原文记"当前无声明"**有误** |

## 8. 记忆与上下文（模块 1 / 3）

| 项 | 值 |
|---|---|
| 记忆目录 | ❌ 暂未接入 |
| 落库内容 | `ai_records`：`context_record`（hops_count + sections + stats） |
| 上下文留存 | ⚠️ 无 TTL |
| 清理策略 | ❌ 未接入 |

## 9. 触发与优先级（模块 2）

| 项 | 值 |
|---|---|
| 触发类型 | 交互式（运维发起）/ 事件式（终端 `路由追踪分析` 前缀触发） |
| 优先级 | **P0** |
| 推理留痕 | ⚠️ 输入侧存证完善，推理过程无留痕 |
| 回退三层 | ①模型链降级 ✅　②**阶段降级 ✅**（hops 非法 → 回退一般性分析）　③任务降级 ❌ |

## 10. 整合契约

| 项 | 值 |
|---|---|
| 代码位置 | `server-platform/server/api.py:run_ai_analysis(kind="routetrace")` |
| 对应常量 | `server-platform/server/ai.py:ROUTETRACE_SYSTEM_PROMPT` |
| 加载方式 | 现：常量硬编码；目标：从本配置读身份段 + 共用隐私前缀 |
| 一致性校验 | `const_ref` 存在性校验 |
| **待迁移项** | ①**修复 `data`/`context` 键名不一致**（当前聚合恒失败，**下一步即做**）；②~~超时统一收紧~~ ✅ **已完成**（ADR-009）；③~~身份"企业"→"医院"~~ ✅ **已完成**；④~~注入隐私前缀~~ ✅ **已完成** |

## 11. 变更记录

| 日期 | 版本 | 变更 | 变更人 |
|---|---|---|---|
| 2026-09-19 | 1.0 | 初版；记录 data/context 键名不一致欠账 | ai-agent-arch-dev |
