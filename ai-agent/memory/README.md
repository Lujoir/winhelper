# memory/ — 模块记忆区

**一模块一目录，物理隔离。** 目录名 = `module_id`，与 `.codebuddy/agents/` 里的
业务负责人对应（如 `log-inspector`、`perf-analyzer`、`asset-locate`）。

## 目录形态

```
memory/<module_id>/
  MEMORY.md      长期记忆（人工可读；只增不改，修正=追加新条目）
  SESSION.jsonl  会话增量（逐行追加，崩溃安全、可回放）
  STATE.json     当前状态（覆盖写；进度指针 + 最近执行摘要）
  archive/       轮转归档段（只读、可检索、不参与日常读取）
```

## 防串扰三道闸（缺一不可）

| 闸 | 做法 |
|---|---|
| **物理隔离** | 一个模块一个目录；**禁止**跨目录直接 `open()` 别人的记忆 |
| **访问收口** | 一切读写走 `MemoryScope(module_id)`，越界访问直接抛异常 |
| **命名空间** | 条目带 `module` 字段；跨模块引用须在条目里显式写 `depends_on: [<module>]` |

跨模块读取的**唯一合法方式**：在条目中声明 `depends_on`，由底座校验目标模块
确实存在且该条目允许被引用。**顺手读隔壁**在代码层面就做不到。

## MEMORY.md 条目格式（写入前必须符合，否则校验拒绝）

```markdown
# <module_id> 长期记忆

<!-- 条目由 Skill 校验后写入；人工可读、可手工追加（同样需符合 schema） -->

- [M-0001] 172.17.90.215 的 C 盘长期维持 90% 以上占用，主因是安装包缓存
  - module: disk-cleaner
  - source: task#20260919-3, metrics 表 11 天时间线
  - ts: 2026-09-19T23:10:00+08:00
  - ttl: 永久
```

**规则**：

1. **只增不改** —— 结论被推翻时追加 `[M-0002] 修正 M-0001：...`，不抹掉历史
   （需要能回答"我当时为什么这么判断"）
2. **必须带 `source`** —— 说不出依据的信息不进记忆
3. **单条 ≤ 4KB** —— 超过多半是误把原始日志塞进来（原始数据有各自的归档处）
4. **`ttl` 到期**由 `tools/cleanup.py` 处理

## STATE.json 形态

```json
{
  "module": "log-inspector",
  "updated_ts": 1789830000,
  "progress": {"last_task_id": "20260919-3", "stage": "done"},
  "last_summary": "完成 3 台终端日志诊断，2 台定位到磁盘满"
}
```

状态可覆盖写——它只是进度指针，丢了能从 `SESSION.jsonl` 重建。

## 新建模块记忆目录

```bash
mkdir -p ai-agent/memory/<module_id>/archive
# 初始化 MEMORY.md（含 schema 头）与 STATE.json（空壳）
```

并在 `ai-agent/docs/DECISIONS.md` 记一条：新增了哪个模块的记忆域。
