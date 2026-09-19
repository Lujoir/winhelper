#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI Agent 架构系统 · 一键清理。

治理对象（**只在这三处动手**，其余一律不碰）：
  1. tmp/                 临时中间产物 —— 按 TTL 清扫（默认 7 天）
  2. memory/*/SESSION.jsonl  会话增量 —— 超阈值轮转到 archive/（默认 5MB）
  3. memory/*/archive/    归档段 —— 超保留期 purge（默认 90 天）

**永不触碰**：soul/SOUL.md、skills/**、memory/*/MEMORY.md、memory/*/STATE.json
（这些是声明与当前状态，误删会丢人格/规范/进度）

安全设计：
  · **默认 dry-run** —— 只看会做什么，不动手；真删需显式 `--apply`
  · 所有删除前打印相对路径与大小；`--apply` 时输出汇总
  · 路径经 resolve() 后校验必须位于 ai-agent/ 之下（防符号链接逃逸）

用法：
    python ai-agent/tools/cleanup.py                      # 预览（默认）
    python ai-agent/tools/cleanup.py --apply              # 实际执行
    python ai-agent/tools/cleanup.py --scope log-inspector
    python ai-agent/tools/cleanup.py --tmp-ttl-days 3 --archive-days 60
    python ai-agent/tools/cleanup.py --include-active     # 连 active 会话段一起轮转
"""
import argparse
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # ai-agent/
MEMORY = ROOT / "memory"
TMP = ROOT / "tmp"

MB = 1024 * 1024
DAY = 86400

# 绝不触碰的文件名：声明与说明文件（清理只针对数据/临时产物，不碰约定文件）
PROTECTED_NAMES = {"MEMORY.md", "STATE.json", "README.md", ".gitkeep"}


def _human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0


def _under_root(p):
    """路径必须落在 ai-agent/ 之下（防符号链接/相对路径逃逸）。"""
    try:
        p.resolve().relative_to(ROOT)
        return True
    except ValueError:
        return False


def _scan_tmp(ttl_days, now):
    """tmp/ 下超过 TTL 的文件与空目录。返回 [(action, path, size)]。"""
    out = []
    if not TMP.is_dir():
        return out
    cutoff = now - ttl_days * DAY
    for p in sorted(TMP.rglob("*")):
        if not _under_root(p) or p.name in PROTECTED_NAMES:
            continue
        try:
            if p.is_file():
                if p.stat().st_mtime < cutoff:
                    out.append(("删除临时文件", p, p.stat().st_size))
            elif p.is_dir() and not any(p.iterdir()):
                out.append(("删除空目录", p, 0))
        except OSError:
            continue
    return out


def _scan_rotate(modules, max_bytes, include_active):
    """SESSION.jsonl 超阈值 → 轮转。返回 [(action, path, size, dest)]。"""
    out = []
    if not MEMORY.is_dir():
        return out
    for mod in sorted(MEMORY.iterdir()):
        if not mod.is_dir():
            continue
        if modules and mod.name not in modules:
            continue
        sess = mod / "SESSION.jsonl"
        if not sess.is_file():
            continue
        try:
            size = sess.stat().st_size
        except OSError:
            continue
        if size <= max_bytes:
            continue
        # active 段默认不轮转（正在被写）；需 --include-active 显式开启。
        # 轮转策略：整段移到 archive（保留全部历史，不截断丢失）。
        if not include_active:
            continue
        stamp = time.strftime("%Y%m%d_%H%M%S")
        dest = mod / "archive" / ("SESSION-%s.jsonl" % stamp)
        out.append(("轮转会话段", sess, size, dest))
    return out


def _scan_purge(modules, keep_days, now):
    """archive/ 下超过保留期的归档段。返回 [(action, path, size)]。"""
    out = []
    if not MEMORY.is_dir():
        return out
    cutoff = now - keep_days * DAY
    for mod in sorted(MEMORY.iterdir()):
        if not mod.is_dir():
            continue
        if modules and mod.name not in modules:
            continue
        arc = mod / "archive"
        if not arc.is_dir():
            continue
        for p in sorted(arc.iterdir()):
            if not _under_root(p) or not p.is_file() \
                    or p.name in PROTECTED_NAMES:
                continue
            try:
                if p.stat().st_mtime < cutoff:
                    out.append(("清理归档段", p, p.stat().st_size))
            except OSError:
                continue
    return out


def main():
    ap = argparse.ArgumentParser(description="AI Agent 架构系统一键清理")
    ap.add_argument("--apply", action="store_true",
                    help="实际执行（默认仅预览 dry-run）")
    ap.add_argument("--scope", default="",
                    help="只处理指定模块（memory 子目录名）；空=全部")
    ap.add_argument("--tmp-ttl-days", type=float, default=7.0,
                    help="tmp/ 文件保留天数（默认 7）")
    ap.add_argument("--max-session-mb", type=float, default=5.0,
                    help="SESSION.jsonl 轮转阈值 MB（默认 5）")
    ap.add_argument("--archive-days", type=float, default=90.0,
                    help="归档段保留天数（默认 90）")
    ap.add_argument("--include-active", action="store_true",
                    help="连 active 会话段一起轮转（默认跳过，正在被写）")
    args = ap.parse_args()

    if not ROOT.is_dir():
        print("找不到 ai-agent 目录：%s" % ROOT)
        return 2
    modules = set()
    if args.scope:
        modules = {s.strip() for s in args.scope.split(",") if s.strip()}

    now = time.time()
    plans = []
    plans += _scan_tmp(args.tmp_ttl_days, now)
    plans += _scan_rotate(modules, args.max_session_mb * MB,
                          args.include_active)
    plans += _scan_purge(modules, args.archive_days, now)

    mode = "执行" if args.apply else "预览（dry-run，未改动任何文件）"
    print("=== AI Agent 清理 · %s ===" % mode)
    print("范围: %s | tmp TTL %.0f天 | 段轮转 %.1fMB | 归档保留 %.0f天"
          % (args.scope or "全部模块", args.tmp_ttl_days,
             args.max_session_mb, args.archive_days))
    if not plans:
        print("\n无需清理：没有超期临时文件、超阈值会话段或超期归档。")
        return 0

    print("")
    freed = 0
    rotated = 0
    touched = []
    for item in plans:
        if len(item) == 4:
            action, path, size, dest = item
            rel = path.relative_to(ROOT)
            print("  [%s] %s → %s（%s）"
                  % (action, rel, dest.relative_to(ROOT), _human(size)))
            if args.apply:
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.replace(str(path), str(dest))     # 同盘原子移动，不丢数据
                rotated += 1
            touched.append(path)
        else:
            action, path, size = item
            rel = path.relative_to(ROOT)
            print("  [%s] %s（%s）" % (action, rel, _human(size)))
            if args.apply:
                try:
                    if path.is_dir():
                        shutil.rmtree(str(path), ignore_errors=True)
                    else:
                        path.unlink()
                    freed += size
                except OSError as exc:
                    print("      ! 失败：%s" % exc)
            touched.append(path)

    print("\n合计 %d 项；%s" % (len(plans),
          ("已删除 %s，轮转 %d 个会话段" % (_human(freed), rotated))
          if args.apply else "以上为预览。加 --apply 执行。"))
    if not args.apply:
        print("提示：默认 dry-run 是刻意的——清理属不可逆操作，请先看清范围。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
