#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""安全改造 R1 · H8 单测：Store 并发安全。

背景：原实现只有部分写路径持锁，读路径裸用共享单连接（check_same_thread=False），
并发下出现 `database is locked` / 游标错乱。修复为「RLock + 方法统一串行化」。
本测以多线程并发读写（写注册/指标、读终端/上传名）压测，断言：
  1. 无任何异常（尤其 database is locked）
  2. 写入数据可完整读回（一致性）
  3. 并发结束后连接仍可用
"""
import os
import sys
import tempfile
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))

import store as store_mod  # noqa: E402

PASSED = []
FAILED = []
ERRORS = []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                          (" | " + detail) if detail and not cond else ""))


def main():
    tmp = tempfile.mkdtemp(prefix="store_cc_")
    db = os.path.join(tmp, "cc.db")
    st = store_mod.Store(db)

    n_threads, n_ops = 8, 30

    def writer(tid):
        try:
            for i in range(n_ops):
                st.register_terminal("W%d" % tid, "windows", "host%d" % tid,
                                     "Windows 11", "4.1.8", "127.0.0.%d" % (tid + 1))
                st.insert_metric("W%d" % tid, int(time.time()),
                                 {"cpu": {"percent": float(i)},
                                  "mem": {"used_percent": 30.0}})
        except Exception as exc:
            ERRORS.append("writer%d: %r\n%s" % (tid, exc, traceback.format_exc()))

    def reader(tid):
        try:
            for _ in range(n_ops):
                st.get_terminal("W%d" % (tid % n_threads))
                st.known_upload_names()
        except Exception as exc:
            ERRORS.append("reader%d: %r\n%s" % (tid, exc, traceback.format_exc()))

    threads = [threading.Thread(target=writer, args=(i,))
               for i in range(n_threads)]
    threads += [threading.Thread(target=reader, args=(i,))
                for i in range(n_threads)]
    started = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - started

    check("并发读写无异常（含 database is locked）", not ERRORS,
          (ERRORS[0][:400] if ERRORS else ""))
    locked_hits = [e for e in ERRORS if "locked" in e.lower()]
    check("无 database is locked", not locked_hits)

    # 一致性：8 个终端全部登记成功且可读回
    missing = [t for t in ("W%d" % i for i in range(n_threads))
               if st.get_terminal(t) is None]
    check("并发写入的终端全部可读回", not missing, str(missing))

    # 连接仍可用（并发结束后不处于损坏状态）
    try:
        st.register_terminal("AFTER", "windows", "h", "win", "4.1.8", "127.0.0.9")
        usable = st.get_terminal("AFTER") is not None
    except Exception as exc:
        usable = False
        print("    收尾写入异常：%r" % exc)
    check("并发后连接仍可用", usable)

    print("\n=== store concurrency tests: pass %d / fail %d（耗时 %.1fs）==="
          % (len(PASSED), len(FAILED), elapsed))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
