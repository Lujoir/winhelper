#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""安全改造 R1 · H1 单测：应用数据删除授权模型（会话隔离 + TTL + fail-closed）。

修复前的缺陷：删除白名单来自模块级全局集合 `_EXTRA_ALLOWED_ROOTS`，历次扫描
登记的根永久叠加（含全盘模式逐文件登记的父目录），删除接口据此放行 —— 扫描的
副作用变成删除授权，最终可删任意曾被扫到的目录内文件。

本测覆盖修复后的语义（零网络、零真实删除；_start_task 与 _locate_zone 均打桩）：
  1. 未扫描                → 非区域路径一律拒绝，并提示重新扫描（fail-closed）
  2. 扫描本轮根            → 根内文件可删
  3. 再次扫描其他根        → **上一轮根立即失效**（核心：授权不跨会话累积）
  4. TTL 过期              → 不可删
  5. 应用数据区域白名单路径 → 不受扫描会话影响（保证既有盘点删除业务不回退）
  6. 路径不存在            → 直接拒绝
  7. 授权边界              → 前缀紧邻目录不可越界（A 与 A2 不视为同一根）
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))      # disk-cleaner 根

import appdata_scan as ads  # noqa: E402

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                          (" | " + detail) if detail and not cond else ""))


def main():
    tmp = tempfile.mkdtemp(prefix="adgrant_")
    root_a = os.path.join(tmp, "A")
    root_a2 = os.path.join(tmp, "A2")        # 前缀紧邻，用于越界断言
    root_b = os.path.join(tmp, "B")
    for d in (root_a, root_a2, root_b):
        os.makedirs(d)
    fa = os.path.join(root_a, "x.exe")
    fa2 = os.path.join(root_a2, "z.exe")
    fb = os.path.join(root_b, "y.exe")
    for f in (fa, fa2, fb):
        with open(f, "w"):
            pass

    # 打桩：屏蔽真实删除任务（只验证前置校验分支）
    calls = []

    def fake_start_task(kind, runner, params):
        calls.append((kind, params))
        return "job-1", False

    ads._start_task = fake_start_task

    real_locate_zone = ads._locate_zone

    # 1) 未扫描 → 非区域路径拒绝（fail-closed）
    ads._SCAN_GRANT["roots"] = set()
    ads._SCAN_GRANT["expires_at"] = 0.0
    r = ads.handle_appdata_delete({"paths": fa})
    check("未扫描 → 拒绝且提示重新扫描",
          r.get("success") is False and r.get("code") == "scan_expired", str(r))

    # 2) 本轮扫描登记 A → A 内可删
    ads._grant_reset("scan-1")
    ads._grant_add(root_a)
    r = ads.handle_appdata_delete({"paths": fa})
    check("本轮扫描根内文件可删", r.get("success") is True, str(r))

    # 3) 再次扫描登记 B → A 立即失效（核心：不跨会话累积）
    ads._grant_reset("scan-2")
    ads._grant_add(root_b)
    r = ads.handle_appdata_delete({"paths": fa})
    # 核心断言是「拒绝」本身：上一轮登记的根不再放行（授权未跨会话累积）。
    # 错误码取决于授权是否仍活跃——此处 scan-2 活跃但 A 不在其 roots 内，
    # 故走「不在允许范围」分支而非 scan_expired；两条分支均为 fail-closed。
    check("第二次扫描后上一轮根失效（不累积）", r.get("success") is False, str(r))
    r = ads.handle_appdata_delete({"paths": fb})
    check("第二次扫描后本轮根可删", r.get("success") is True, str(r))

    # 4) TTL 过期
    ads._SCAN_GRANT["expires_at"] = time.time() - 1
    r = ads.handle_appdata_delete({"paths": fb})
    check("TTL 过期 → 拒绝",
          r.get("success") is False and r.get("code") == "scan_expired", str(r))

    # 5) 应用数据区域白名单路径不受会话约束（业务不回退）
    ads._locate_zone = lambda p: (("desktop", None) if p == fa2 else (None, None))
    ads._SCAN_GRANT["roots"] = set()
    ads._SCAN_GRANT["expires_at"] = 0.0
    r = ads.handle_appdata_delete({"paths": fa2})
    check("区域白名单路径不依赖扫描会话（业务不回退）",
          r.get("success") is True, str(r))
    ads._locate_zone = real_locate_zone

    # 6) 路径不存在
    ads._grant_reset("scan-3")
    ads._grant_add(root_a)
    r = ads.handle_appdata_delete({"paths": os.path.join(root_a, "nope.exe")})
    check("路径不存在 → 拒绝", r.get("success") is False
          and "不存在" in (r.get("error") or ""), str(r))

    # 7) 前缀紧邻目录不可越界：仅登记 A2 时 A 内的文件不可删
    ads._grant_reset("scan-4")
    ads._grant_add(root_a2)
    r = ads.handle_appdata_delete({"paths": fa})
    check("前缀紧邻目录不视为同一根（无越界）", r.get("success") is False, str(r))
    r = ads.handle_appdata_delete({"paths": fa2})
    check("同根内文件仍可删", r.get("success") is True, str(r))

    # 8) 授权为空集合时不得视为有效
    ads._SCAN_GRANT["roots"] = set()
    ads._SCAN_GRANT["expires_at"] = time.time() + 600
    check("空授权不视为有效（_grant_active=False）", ads._grant_active() is False)

    print("\n=== appdata delete grant tests: pass %d / fail %d ==="
          % (len(PASSED), len(FAILED)))
    if calls:
        print("（已拦截的真实删除任务数：%d，仅用于确认未误触删除）" % len(calls))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
