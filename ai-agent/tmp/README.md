# tmp/ — 临时中间产物区

**本项目唯一允许自动清理的目录。**

## 规则

1. **一切中间产物落这里** —— 探针脚本输出、构建日志、临时截图、一次性脚本
2. **按 TTL 自动清扫**（默认 7 天），由 `../tools/cleanup.py` 执行
3. **禁止在主仓根创建 `_xxx.txt` / `_xxx.py`** 这类散落文件

## 为什么需要这个区

主仓根实测堆了 **60+ 个** `_xxx.txt` / `_xxx.py`（历史探针、构建日志、一次性脚本）。
问题不在于"文件多"，而在于——**没有约定的临时区，就没有安全的清理边界**：

- 不知道某个 `_xxx.txt` 是谁的、什么时候的、还有没有用 → 不敢删
- 于是只能一直堆着，越堆越不敢动

有了 `tmp/` 这个明确约定，清理就变成了安全的机械操作：**这里的东西都有 TTL**。

## 命名建议

带模块前缀与时间，便于人工判断归属：

```
tmp/log-inspector_probe_20260919_2310.py
tmp/perf-analyzer_e2e_out_20260919.txt
```

## 清理

```bash
python ai-agent/tools/cleanup.py              # 预览（默认 dry-run）
python ai-agent/tools/cleanup.py --apply      # 执行
python ai-agent/tools/cleanup.py --tmp-ttl-days 3
```

> `.gitkeep` 之外的文件不会入库；用完即删是好习惯，别留到下一轮。
