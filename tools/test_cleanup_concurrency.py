#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""安全改造 R1 · H9 单测：并发清理请求显式化。

原实现：已有同类清理任务时**静默复用**旧 job 并忽略本次 categories，却把新的
categories 回显给调用方（前端误以为已生效）。修复后：如实返回
`code=cleanup_busy` + 正在运行的分类 + 本次请求分类。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import disk_cleanup as dc  # noqa: E402

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                          (" | " + detail) if detail and not cond else ""))


def main():
    key = sorted(dc.CATEGORY_DEFS)[0]

    # 1) 互斥量被占用（已有清理在执行）→ 明确 busy 并透出运行中分类
    dc._cleanup_mutex.acquire()
    dc._cleanup_current["categories"] = ["oldcat"]
    try:
        r = dc.handle_disk_cleanup({"categories": key})
    finally:
        dc._cleanup_mutex.release()
        dc._cleanup_current["categories"] = []
    check("互斥占用 → cleanup_busy",
          r.get("success") is False and r.get("code") == "cleanup_busy", str(r))
    check("透出正在运行的分类", r.get("running_categories") == ["oldcat"], str(r))
    check("透出本次请求分类", r.get("requested_categories") == [key], str(r))

    # 2) 任务复用场景（_start_task 返回 reused=True）→ 不静默、返回 busy
    orig = dc._start_task
    dc._start_task = lambda kind, runner, params: ("job-x", True)
    try:
        r2 = dc.handle_disk_cleanup({"categories": key})
    finally:
        dc._start_task = orig
    check("任务复用 → cleanup_busy 且 reused=True",
          r2.get("success") is False and r2.get("code") == "cleanup_busy"
          and r2.get("reused") is True, str(r2))
    check("复用时不返回 success/categories 组合（消除误导回显）",
          "categories" not in r2, str(r2))

    # 3) 非法分类仍被前置拒绝
    r3 = dc.handle_disk_cleanup({"categories": "__nope__"})
    check("非法分类被拒",
          r3.get("success") is False
          and "非法清理项" in (r3.get("error") or ""), str(r3))

    # 4) 正常路径（新 job）→ success + categories
    dc._start_task = lambda kind, runner, params: ("job-y", False)
    try:
        r4 = dc.handle_disk_cleanup({"categories": key})
    finally:
        dc._start_task = orig
    check("正常路径返回 success + categories",
          r4.get("success") is True and r4.get("categories") == [key], str(r4))

    print("\n=== cleanup concurrency tests: pass %d / fail %d ==="
          % (len(PASSED), len(FAILED)))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
