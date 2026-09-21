#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""asset_group_sync_huorong 单测（临时库，直连 store）。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "server"))
import store as store_mod   # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" | " + detail) if detail and not cond else ""))


def main():
    tmp = os.path.join(tempfile.gettempdir(), "asset_sync_test.db")
    if os.path.exists(tmp):
        os.remove(tmp)
    s = store_mod.Store(tmp)

    # 注册两台终端（挂载验证用）
    s.register_terminal("T-A", "windows", "PC-A", "Windows 11", "t", "10.0.0.1")
    s.register_terminal("T-B", "windows", "PC-B", "Windows 10", "t", "10.0.0.2")

    # 火绒组：1 防护组（根级） / 2 门诊（根级） / 3 门诊子组（父=2）
    groups = [
        {"id": 1, "name": "防护组", "parent": 0},
        {"id": 2, "name": "门诊", "parent": 0},
        {"id": 3, "name": "门诊子组", "parent": 2},
    ]
    st = s.asset_group_sync_huorong(groups, {1: ["T-A"]})

    check("root ensured", st["root"] > 0)
    check("created 3", st["created"] == 3, str(st))
    rows = {r["name"]: r for r in s.asset_group_list()}
    check("root name", "全部资产" in rows)
    check("huorong groups synced", "防护组" in rows and "门诊" in rows
          and "门诊子组" in rows)
    g3 = rows["门诊子组"]
    g2 = rows["门诊"]
    check("hierarchy parent linked", g3["parent_id"] == g2["id"])
    check("source huorong tagged", g2.get("source") == "huorong")
    term_rows = [dict(x) for x in s.list_terminals()]
    check("mounted matched terminal",
          any(t["terminal_id"] == "T-A"
              and t["group_id"] == rows["防护组"]["id"] for t in term_rows),
          str(term_rows))

    # 幂等：再跑一次不重复建
    st2 = s.asset_group_sync_huorong(groups, {1: ["T-A"]})
    check("idempotent re-sync", st2["created"] == 0 and st2["updated"] == 3,
          str(st2))
    check("no duplicate after re-sync",
          len([r for r in s.asset_group_list()
               if r["name"] == "防护组"]) == 1)

    # 改名 + 删除组 3 → 软删；组 2 改名跟随
    groups2 = [
        {"id": 1, "name": "防护组", "parent": 0},
        {"id": 2, "name": "门诊部", "parent": 0},
    ]
    st3 = s.asset_group_sync_huorong(groups2, {})
    check("rename followed", st3["updated"] >= 1)
    check("soft deleted missing group", st3["soft_deleted"] == 1)
    rows2 = {r["name"]: r for r in s.asset_group_list()}
    check("renamed visible", "门诊部" in rows2 and "门诊" not in rows2)
    check("soft-deleted hidden", "门诊子组" not in rows2)

    # 删除保护：huorong 组手动删拒绝
    r = s.asset_group_delete(rows2["门诊部"]["id"])
    check("huorong group delete rejected", r == "synced", str(r))
    root_id = st["root"]
    check("root delete rejected",
          s.asset_group_delete(root_id) == "root")

    # manual 组不受同步影响
    s.asset_group_create("手工组", None)
    st4 = s.asset_group_sync_huorong(groups2, {})
    check("manual group untouched",
          any(r["name"] == "手工组" for r in s.asset_group_list()))

    print("\n=== asset sync test summary ===")
    print("PASS: %d  FAIL: %d" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("failed: %s" % ", ".join(FAILED))
        sys.exit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
