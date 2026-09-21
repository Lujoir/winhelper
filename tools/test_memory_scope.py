#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MemoryScope（P1-3 记忆访问收口）本地单测。

重点验证**防串扰第二道闸是否真的关得住** ——
如果越界不抛异常而是静默返回空，隔离就名存实亡，
调用方还会以为"该模块没记忆"从而得出错误结论。

零凭据零外联：全程临时目录，测试后自清理。
"""
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import memory_scope                                     # noqa: E402
from memory_scope import MemoryScope, MemoryScopeViolation   # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" | " + detail) if detail else ""))


def expect_violation(name, fn):
    try:
        fn()
    except MemoryScopeViolation as exc:
        check(name, True, str(exc)[:60])
        return
    except Exception as exc:                            # noqa: BLE001
        check(name, False, "抛了非预期异常: %r" % exc)
        return
    check(name, False, "未抛异常（收口失效）")


def main():
    tmp = tempfile.mkdtemp(prefix="memtest_")
    root = os.path.join(tmp, "memory")
    try:
        print("=== 一、边界防护（收口层的存在理由）===")
        expect_violation("非法 module_id（路径分隔符）",
                         lambda: MemoryScope(root, "net/doctor"))
        expect_violation("非法 module_id（..）",
                         lambda: MemoryScope(root, "../escape"))
        expect_violation("非法 module_id（空）",
                         lambda: MemoryScope(root, ""))
        expect_violation("空 memory_root",
                         lambda: MemoryScope("", "netdoctor"))

        a = MemoryScope(root, "netdoctor")
        b = MemoryScope(root, "diskcleaner")

        expect_violation("文件名含分隔符",
                         lambda: a.path("sub/x.json"))
        expect_violation("文件名 ..",
                         lambda: a.path(".."))
        expect_violation("文件名含反斜杠",
                         lambda: a.path("..\\other\\MEMORY.md"))
        expect_violation("非法子目录（不在白名单）",
                         lambda: a.path("x.jsonl", subdir="evil"))
        expect_violation("子目录名含 ../（借 subdir 逃逸）",
                         lambda: a.path("x.jsonl", subdir="../netdoctor"))
        check("archive 白名单子目录可用",
              a.path("x.jsonl", subdir="archive").name == "x.jsonl"
              and "netdoctor" in a.path("x.jsonl", subdir="archive").as_posix())

        check("两个模块目录互相独立",
              a.dir != b.dir and a.dir != b.dir,
              "%s | %s" % (a.dir.name, b.dir.name))
        check("模块目录都在根之下",
              root.replace("\\", "/") in a.dir.as_posix()
              and root.replace("\\", "/") in b.dir.as_posix())

        print("=== 二、SESSION 追加与读取 ===")
        a.append_session({"ts": 100, "summary": "第一次诊断"})
        a.append_session({"ts": 200, "summary": "第二次诊断"})
        recs = a.read_session()
        check("追加两条可读回", len(recs) == 2, str(len(recs)))
        check("按 ts 倒序", recs[0]["ts"] == 200 and recs[1]["ts"] == 100)

        b.append_session({"ts": 300, "summary": "磁盘清理"})
        check("模块间互不可见（第①道闸）",
              len(a.read_session()) == 2 and len(b.read_session()) == 1,
              "a=%d b=%d" % (len(a.read_session()), len(b.read_session())))

        print("=== 三、强制打标（第③道闸：命名空间）===")
        rec = a.append_session({"ts": 400, "summary": "带来源标记"})
        check("写入记录自带 module",
              rec.get("module") == "netdoctor", str(rec.get("module")))
        spoofed = a.append_session({"ts": 500, "module": "other", "summary": "x"})
        check("调用方无法篡改 module（防伪造来源）",
              spoofed.get("module") == "netdoctor", str(spoofed.get("module")))
        check("读取回来的记录也带 module",
              all(r.get("module") == "netdoctor" for r in a.read_session()))

        print("=== 四、坏行容错 ===")
        p = a.path(MemoryScope.SESSION_NAME)
        with open(p, "a", encoding="utf-8") as f:
            f.write("这不是 JSON\n")
            f.write("\n")
            f.write('{"ts": 600, "summary": "坏行后的好行"}\n')
        recs = a.read_session()
        check("坏行跳过、好行保留",
              any(r.get("ts") == 600 for r in recs), "共 %d 条" % len(recs))

        print("=== 五、轮转归档 ===")
        a2 = MemoryScope(root, "rotate_test")
        for i in range(1, 11):
            a2.append_session({"ts": i, "summary": "第 %d 条" % i})
        kept, archived = a2.rotate(keep=4)
        check("保留 keep 条、其余归档",
              kept == 4 and archived == 6, "kept=%d archived=%d" % (kept, archived))
        check("主文件只剩最新 4 条", len(a2.read_session()) == 4)
        check("最新 4 条是 ts 最大的",
              [r["ts"] for r in a2.read_session()] == [10, 9, 8, 7],
              str([r["ts"] for r in a2.read_session()]))
        arch_dir = a2.dir / "archive"
        arch_files = list(arch_dir.glob("SESSION-*.jsonl")) if arch_dir.exists() else []
        check("归档文件已生成", len(arch_files) == 1, str([f.name for f in arch_files]))
        if arch_files:
            with open(arch_files[0], "r", encoding="utf-8") as f:
                n = len([l for l in f if l.strip()])
            check("归档包含 6 条（数据未丢）", n == 6, "归档 %d 条" % n)
        check("未超阈值时不轮转",
              a2.rotate(keep=100)[1] == 0)

        print("=== 六、STATE 原子写 ===")
        check("初始状态为空 dict", a.read_state() == {})
        a.write_state({"cursor": 42, "note": "断点"})
        check("写入后可读回", a.read_state().get("cursor") == 42)
        a.write_state({"cursor": 43})
        check("覆盖写生效", a.read_state().get("cursor") == 43)
        expect_violation("非 dict 状态被拦",
                         lambda: a.write_state(["not", "a", "dict"]))
        check("非法写入后旧值仍在（不留空文件，ADR-007）",
              a.read_state().get("cursor") == 43, str(a.read_state()))

        print("=== 七、导出接口 ===")
        check("memory_root_for 返回 memory 子目录",
              memory_scope.memory_root_for("C:/app").name == "memory")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n---- 结果：通过 %d，失败 %d ----" % (len(PASSED), len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  FAILED: %s" % f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
