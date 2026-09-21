#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""手动维护登记存储层单测（ADR-040 增补）。"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.normpath(os.path.join(HERE, "..", "server"))
sys.path.insert(0, SERVER)

from power_control import PowerControlStore            # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" | " + detail) if detail and not cond else ""))


def main():
    tmp = tempfile.mkdtemp(prefix="pw_manual_")
    pc = PowerControlStore(os.path.join(tmp, "pc.db"))
    print("=== power manual config unit tests ===")

    check("空库 manual_get None", pc.manual_get("T1") is None)
    check("空库 manual_list 空表", pc.manual_list() == [])

    cfg = pc.manual_upsert(
        "T1",
        {"enabled": True, "mode": "daily", "time": "07:50"},
        {}, "BIOS 人工配置", "admin", now=100)
    check("upsert 返回配置", cfg["terminal_id"] == "T1"
          and cfg["boot"].get("mode") == "daily"
          and cfg["boot"].get("time") == "07:50"
          and cfg["shutdown"] == {}
          and cfg["note"] == "BIOS 人工配置"
          and cfg["operator"] == "admin")
    check("manual_get 回读一致", pc.manual_get("T1")["boot"]["enabled"])

    cfg2 = pc.manual_upsert(
        "T1",
        {"enabled": True, "mode": "single", "time": "08:00",
         "date": "2026-09-20"},
        {"enabled": True, "mode": "daily", "time": "18:00"},
        "更新为单次", "operator1", now=200)
    check("覆盖更新全字段", cfg2["boot"]["mode"] == "single"
          and cfg2["boot"]["date"] == "2026-09-20"
          and cfg2["shutdown"].get("enabled") is True
          and cfg2["note"] == "更新为单次"
          and cfg2["operator"] == "operator1")
    lst = pc.manual_list()
    check("manual_list 含更新项", len(lst) == 1 and lst[0]["updated_ts"] == 200)

    check("manual_delete 成功", pc.manual_delete("T1") is True)
    check("重复删除 False", pc.manual_delete("T1") is False)
    check("删除后回读 None", pc.manual_get("T1") is None)

    pc.close()
    print("\n=== power manual unit tests: pass %d / fail %d ==="
          % (len(PASSED), len(FAILED)))
    if FAILED:
        print("failed: %s" % ", ".join(FAILED))
        return 1
    print("ALL UNIT TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
