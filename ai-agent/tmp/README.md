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

## legacy/ — 存量临时文件归档（2026-09-20）

主仓根与子仓根的历史遗留临时产物已集中归置于此：

```
tmp/legacy/main/            主仓根原有 _* 临时文件（59 个）
tmp/legacy/server-platform/ 子仓 server-platform 根原有 _* 临时文件（94 个）
```

**背景**：项目根长期堆积一次性探针脚本、构建日志、调试输出（`_*.txt` / `_*.py` /
`_*.bat` / `_*.ps1`）—— 主仓根 64 个、子仓根 98 个。它们既污染仓库根目录（把真实
工程文件淹没在噪音里），也让人无法判断归属与是否还有用。按 ADR-005「只有 `tmp/`
允许自动清理、主仓根禁止散落临时文件」执行归置。

**保留说明**：
- 根目录仍留 `_*.png`（VLAN 拓扑 / 界面截图等）—— 属交付证据，**未归置**
- **已跟踪（git tracked）的 `_*.py` 脚本已移回原位**，不纳入归置

**教训（本次实操踩到，记录下来避免重犯）**：
批量移动前**必须先区分「已跟踪 / 未跟踪」**。我起初用 `Get-ChildItem -Filter "_*"`
无差别移动，误移了 5 个 git 跟踪文件（`_diag_ai.py` / `_notify_dev.py` /
`_restore_llm_key.py` / `_cr_publish46.py` / `_tp_wol_optimize.py`），导致工作区
出现删除记录，需逐个恢复。
**正确做法**：先用 `git ls-files` + `git -C <sub> ls-files` 取已跟踪集合，
再从待移动集合中剔除；移动后必须跑 `git status --porcelain | Select-String "^ D"`
验证无误删。
