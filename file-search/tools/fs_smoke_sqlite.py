# -*- coding: utf-8 -*-
"""
fs_smoke_sqlite.py — search_service sqlite 引擎冒烟（P1 门禁，无管理员可跑）
===========================================================================
用 FS_INDEX_DB 临时库手工插数（模拟 fs_indexer 产物）→ handle_fs_query/status 全契约断言。
运行：python tools/fs_smoke_sqlite.py
"""

import os
import sqlite3
import sys
import tempfile
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  | " + str(detail)[:140]))


def build_fixture(tmp):
    import fs_indexer
    conn = sqlite3.connect(tmp)
    conn.executescript(fs_indexer._SCHEMA)
    rows = [
        ("C", 5, None, "", "C:\\", 1, None, None),
        ("C", 100, 5, "数据中心火灾演练", "C:\\数据中心火灾演练\\", 1, None, None),
        ("C", 101, 100, "火灾预案.pdf", "C:\\数据中心火灾演练\\火灾预案.pdf", 0, 2048, 1727000000),
        ("C", 102, 100, "report_2026.pdf", "C:\\数据中心火灾演练\\report_2026.pdf", 0, 1048576, 1727000100),
        ("C", 103, 100, "note.docx", "C:\\数据中心火灾演练\\note.docx", 0, 999, 1727000200),
        ("C", 104, 100, "archive.zip", "C:\\数据中心火灾演练\\archive.zip", 0, 4096, 1727000300),
        ("C", 200, 5, "logs", "C:\\logs\\", 1, None, None),
        ("C", 201, 200, "build_report.log", "C:\\logs\\build_report.log", 0, 512, 1727000400),
        ("D", 5, None, "", "D:\\", 1, None, None),
        ("D", 300, 5, "data.txt", "D:\\data.txt", 0, 128, 1727000500),
    ]
    conn.executemany("INSERT OR REPLACE INTO files(volume,frn,parent_frn,name,path,is_dir,size,mtime) "
                     "VALUES(?,?,?,?,?,?,?,?)", rows)
    conn.execute("INSERT INTO usn_state(volume,journal_id,next_usn) VALUES('C',1,100)")
    conn.execute("INSERT INTO usn_state(volume,journal_id,next_usn) VALUES('D',1,200)")
    conn.commit()
    conn.close()


def main():
    import glob
    import search_service as ss

    tmp = os.path.join(tempfile.gettempdir(), "fs_smoke_idx.db")
    for p in glob.glob(tmp + "*"):   # 统一清 %TEMP% 全部残留（含 T8 mock 子库，防撞 UNIQUE/锁）
        try:
            os.remove(p)
        except Exception:
            pass
    os.environ["FS_INDEX_DB"] = tmp
    build_fixture(tmp)

    # 1. 子串模糊 + 大小写不敏感
    r = ss.handle_fs_query({"q": "report"})
    check("query·子串命中 2 条（含路径无关文件名匹配）", r["success"] and r["total"] == 2, r)
    r2 = ss.handle_fs_query({"q": "REPORT"})
    check("query·大小写不敏感", r2["success"] and r2["total"] == 2, r2)

    # 2. 中文关键词 + 目录行（path 归一父目录）
    r = ss.handle_fs_query({"q": "数据中心火灾"})
    check("query·中文命中目录", r["success"] and r["count"] == 1 and r["results"][0]["name"] == "数据中心火灾演练", r)
    check("query·目录行 path 归一父目录", r["results"][0]["path"] == "C:\\", r["results"][0])

    # 3. ext 预筛（多选 OR）
    r = ss.handle_fs_query({"q": "report ext:pdf;docx"})
    check("query·kw+ext 组合过滤", r["success"] and r["count"] == 1 and r["results"][0]["name"] == "report_2026.pdf", r)
    r = ss.handle_fs_query({"q": "ext:pdf"})
    check("query·纯 ext 查询", r["success"] and r["total"] == 2, r)

    # 4. 排序正倒序
    r = ss.handle_fs_query({"q": "ext:pdf", "sort": "size", "ascending": "1"})
    check("sort·size 升序", [x["name"] for x in r["results"]] == ["火灾预案.pdf", "report_2026.pdf"], r["results"])
    r = ss.handle_fs_query({"q": "ext:pdf", "sort": "size", "ascending": "0"})
    check("sort·size 降序翻转", [x["name"] for x in r["results"]] == ["report_2026.pdf", "火灾预案.pdf"], r["results"])
    r = ss.handle_fs_query({"q": "ext:pdf;zip;log", "sort": "mtime", "ascending": "1"})
    check("sort·mtime 升序", [x["date_modified"] for x in r["results"]] ==
          [1727000000, 1727000100, 1727000300, 1727000400], r["results"])
    r = ss.handle_fs_query({"q": "ext:pdf", "sort": "name", "ascending": "0"})
    check("sort·name 降序（默认列可翻）", [x["name"] for x in r["results"]] == ["火灾预案.pdf", "report_2026.pdf"], r["results"])

    # 修改时间区间过滤 + 日期排序别名
    r = ss.handle_fs_query({"q": "report", "date_from": "2024-09-21", "date_to": "2024-09-21"})
    check("filter·日期范围无命中", r["success"] and r["total"] == 0, r)
    r = ss.handle_fs_query({"q": "report", "date_from": "1970-01-01", "date_to": "2099-12-31"})
    check("filter·日期范围全命中", r["success"] and r["total"] == 2, r)
    r = ss.handle_fs_query({"q": "ext:pdf", "sort": "date_modified", "ascending": "1"})
    check("sort·date_modified 别名走 mtime", [x["name"] for x in r["results"]] == ["火灾预案.pdf", "report_2026.pdf"], r["results"])

    # 类型统计端点
    st = ss.handle_fs_stats({})
    check("stats·返回扩展名统计", st["success"] and any(x["ext"] == "pdf" for x in st["exts"]), st)

    # 5. 非法 sort 忽略走默认 + 形状兼容
    r = ss.handle_fs_query({"q": "report", "sort": "hack; DROP TABLE files"})
    check("sort·非法值忽略", r["success"] and r["count"] == 2, r)

    # 6. LIKE 通配转义
    r = ss.handle_fs_query({"q": "100%"})
    check("query·通配符字面匹配（不崩不误配）", r["success"] and r["total"] == 0, r)

    # 7. 上限 200
    conn = sqlite3.connect(tmp)
    bulk = [("C", 1000 + i, 200, "bulk_%03d.txt" % i, "C:\\logs\\bulk_%03d.txt" % i, 0, i, 1727001000 + i)
            for i in range(250)]
    conn.executemany("INSERT OR REPLACE INTO files(volume,frn,parent_frn,name,path,is_dir,size,mtime) "
                     "VALUES(?,?,?,?,?,?,?,?)", bulk)
    conn.commit()
    conn.close()
    r = ss.handle_fs_query({"q": "bulk_"})
    check("limit·默认上限 200", r["success"] and r["count"] == 200 and r["total"] == 250, r["count"])
    r = ss.handle_fs_query({"q": "bulk_", "count": 30})
    check("limit·count 参数生效", r["count"] == 30, r["count"])

    # 8. 空查询
    r = ss.handle_fs_query({"q": ""})
    check("query·空查询拒绝", r["success"] is False and r["error"] == "empty_query", r)

    # 9. 索引未就绪（库不存在）
    os.environ["FS_INDEX_DB"] = os.path.join(tempfile.gettempdir(), "fs_no_such_dir_zz", "index.db")
    r = ss.handle_fs_query({"q": "report"})
    check("query·索引未就绪如实提示（三态 hint：本机未注册 → 未部署指引）",
          r["success"] is False and r["error"] == "indexer_not_running"
          and "未部署" in (r.get("hint") or ""), r)
    st = ss.handle_fs_status({})
    check("status·未就绪形状", st["success"] and st["db_exists"] is False and st["file_count"] == 0, st)
    os.environ["FS_INDEX_DB"] = tmp

    # 10. status 形状
    st = ss.handle_fs_status({})
    check("status·就绪形状（计数+卷）", st["db_exists"] and st["file_count"] == 256
          and st["dir_count"] == 4 and set(st["volumes"]) == {"C:", "D:"}, st)

    # 11. 前端兼容形状（date_modified 秒/size 数值/fsFmtDate 输入）
    r = ss.handle_fs_query({"q": "火灾预案"})
    item = r["results"][0]
    check("形状·results 键兼容前端", set(item.keys()) == {"name", "path", "is_dir", "size", "date_modified"}
          and item["date_modified"] == 1727000000 and item["path"] == "C:\\数据中心火灾演练", item)

    # 12. 真实 build_volume 路径（mock IOCTL 层，2026-09-15 防再犯门禁）——
    #     覆盖 records int 契约 / partial / V3 记录 / 截断回退 / V1 切换五路径
    check_group = build_mock_group(tmp)

    # 13. 服务端单测（2026-09-16 排序/筛选增强）：ext 服务端排序语义 / mtime 区间闭边界与别名 / 组合查询
    import fs_indexer
    t9db = os.path.join(tempfile.gettempdir(), "fs_smoke_t9_%d.db" % os.getpid())
    c9 = sqlite3.connect(t9db)
    c9.executescript(fs_indexer._SCHEMA)
    c9.executemany("INSERT OR REPLACE INTO files(volume,frn,parent_frn,name,path,is_dir,size,mtime) "
                   "VALUES(?,?,?,?,?,?,?,?)", [
        ("C", 5, None, "", "C:\\", 1, None, None),
        ("C", 10, 5, "archive_归档", "C:\\archive_归档\\", 1, None, None),
        ("C", 11, 10, "zebra.txt", "C:\\archive_归档\\zebra.txt", 0, 10, 1727000100),
        ("C", 12, 10, "apple.pdf", "C:\\archive_归档\\apple.pdf", 0, 20, 1727000200),
        ("C", 13, 10, "orange.pdf", "C:\\archive_归档\\orange.pdf", 0, 30, 1727000300),
        ("C", 14, 10, "code.docx", "C:\\archive_归档\\code.docx", 0, 40, 1727000400),
        ("C", 15, 10, "readme", "C:\\archive_归档\\readme", 0, 50, 1727000500),
    ])
    c9.commit()
    c9.close()
    os.environ["FS_INDEX_DB"] = t9db

    def t9_names(r):
        return [x["name"] for x in r["results"]]

    r = ss.handle_fs_query({"q": "e", "sort": "ext", "ascending": "1"})
    check("T9·ext 升序（目录固定排最前+扩展名字典序+同名稳定）",
          r["success"] and t9_names(r) ==
          ["archive_归档", "code.docx", "apple.pdf", "orange.pdf", "readme", "zebra.txt"], t9_names(r))
    r = ss.handle_fs_query({"q": "e", "sort": "ext", "ascending": "0"})
    check("T9·ext 倒序（目录仍排最前不翻转，扩展段倒序）",
          r["success"] and t9_names(r) ==
          ["archive_归档", "zebra.txt", "readme", "apple.pdf", "orange.pdf", "code.docx"], t9_names(r))
    r = ss.handle_fs_query({"q": "ext:pdf", "mtime_from": 1727000300})
    check("T9·mtime_from 闭区间边界（=值命中）", r["success"] and t9_names(r) == ["orange.pdf"], t9_names(r))
    r = ss.handle_fs_query({"q": "ext:pdf", "mtime_to": 1727000200})
    check("T9·mtime_to 闭区间边界", r["success"] and t9_names(r) == ["apple.pdf"], t9_names(r))
    r = ss.handle_fs_query({"q": "ext:pdf", "mtime_from": 1727000200, "mtime_to": 1727000300})
    check("T9·mtime 区间双端命中 2 条", r["success"] and r["total"] == 2
          and t9_names(r) == ["apple.pdf", "orange.pdf"], t9_names(r))
    r2 = ss.handle_fs_query({"q": "ext:pdf", "date_from": 1727000300})
    check("T9·date_from 兼容别名与 mtime_from 等价", r2["success"]
          and t9_names(r2) == t9_names(ss.handle_fs_query({"q": "ext:pdf", "mtime_from": 1727000300})), r2)
    r = ss.handle_fs_query({"q": "an ext:pdf", "mtime_from": 1727000200, "mtime_to": 1727000350})
    check("T9·组合查询（关键词+ext 多值 AND 日期区间）", r["success"] and t9_names(r) == ["orange.pdf"],
          t9_names(r))
    try:
        os.remove(t9db)
    except Exception:
        pass

    # 14. 部署机制单测（4.1.7）：status 三态 / deploy 注册幂等 / 提权取消如实
    import types
    t10db = os.path.join(tempfile.gettempdir(), "fs_smoke_t10_%d.db" % os.getpid())
    c10 = sqlite3.connect(t10db)
    c10.executescript(fs_indexer._SCHEMA)
    c10.commit()
    c10.close()
    _saved_q, _saved_admin, _saved_run = (ss._schtasks_task_registered, ss._is_user_admin,
                                          ss.subprocess)
    try:
        ss._schtasks_task_registered = lambda task_name=None: False
        os.environ["FS_INDEX_DB"] = t10db
        r = ss.handle_fs_status({})
        check("T10·status 未部署态（空库+任务未注册）", r["success"] and r["state"] == "not_deployed"
              and "未部署" in r["hint"], {"state": r.get("state"), "hint": r.get("hint")})
        os.environ["FS_INDEX_DB"] = os.path.join(tempfile.gettempdir(), "fs_no_such_dir_zz", "index.db")
        r = ss.handle_fs_query({"q": "x"})
        check("T10·query 未部署 hint 指引（库不存在场景）", r["success"] is False
              and "未部署" in (r.get("hint") or "") and "重新安装" in (r.get("hint") or ""), r)
        ss._schtasks_task_registered = lambda task_name=None: True
        r = ss.handle_fs_query({"q": "x"})
        check("T10·query 构建中 hint（库不存在+任务已注册）", r["success"] is False
              and "构建中" in (r.get("hint") or ""), r)
        os.environ["FS_INDEX_DB"] = t10db
        c10 = sqlite3.connect(t10db)
        c10.execute("INSERT OR REPLACE INTO files(volume,frn,parent_frn,name,path,is_dir,size,mtime) "
                    "VALUES('C',20,5,'a.txt','C:\\a.txt',0,1,1727000000)")
        c10.commit()
        c10.close()
        ss._schtasks_task_registered = lambda task_name=None: False
        r = ss.handle_fs_status({})
        check("T10·status 就绪态（有数据即 ready，不依赖注册）", r["success"] and r["state"] == "ready"
              and r["hint"] == "", {"state": r.get("state")})

        calls = []

        class _R:
            def __init__(self, rc):
                self.returncode = rc
                self.stdout = b"ok"
                self.stderr = b""

        ss._is_user_admin = lambda: True
        ss.subprocess = types.SimpleNamespace(
            run=lambda cmd, **kw: (calls.append(list(cmd)), _R(0))[1])
        r = ss.handle_fs_indexer_deploy({})
        r2 = ss.handle_fs_indexer_deploy({})
        check("T10·deploy 管理员直执行（Create+Run 两条命令）", r.get("success") is True
              and len(calls) == 4 and calls[0][0] == "schtasks" and "/F" in calls[0]
              and "/TN" in calls[0] and "EyeTermFileIndexer" in calls[0]
              and "/SC" in calls[0] and "ONSTART" in calls[0] and "/RL" in calls[0]
              and calls[1][:2] == ["schtasks", "/Run"], {"calls": calls[:2], "r": r})
        check("T10·deploy 幂等（/F 覆盖+Run 重复触发，二次调用仍成功）",
              r2.get("success") is True, r2)
        ss._is_user_admin = lambda: False

        class _CT:
            class windll:
                class shell32:
                    @staticmethod
                    def ShellExecuteW(*a):
                        return 5   # ≤32 = UAC 取消
        _saved_ct = ss.ctypes
        ss.ctypes = _CT
        r = ss.handle_fs_indexer_deploy({})
        check("T10·非管理员 UAC 取消如实上报（不静默成功）", r.get("success") is False
              and r.get("elevate_cancelled") is True, r)
        ss.ctypes = _saved_ct
    finally:
        ss._schtasks_task_registered, ss._is_user_admin, ss.subprocess = _saved_q, _saved_admin, _saved_run
        try:
            os.remove(t10db)
        except Exception:
            pass
    os.environ["FS_INDEX_DB"] = tmp

    for suffix in ("", "-wal", "-shm"):
        p = tmp + suffix
        if os.path.exists(p):
            os.remove(p)

    total = len(PASS) + len(FAIL)   # gcheck 已计入 PASS/FAIL，check_group 仅作分组计数
    print("\n===== SMOKE RESULT: %d/%d passed =====" % (len(PASS), total))
    if FAIL:
        for f in FAIL:
            print("  -", f)
        sys.exit(1)
    print("FILESEARCH SQLITE SMOKE: PASS")


def build_mock_group(tmp):
    """mock IOCTL 层驱动真实 fs_indexer.build_volume 全路径（不触真实卷句柄）。"""
    import fs_indexer as fi

    def v2rec(frn, parent, name, is_dir=False):
        nb = name.encode("utf-16-le")
        b = (60 + len(nb)).to_bytes(4, "little") + (2).to_bytes(2, "little") + (1).to_bytes(2, "little")
        b += frn.to_bytes(8, "little") + parent.to_bytes(8, "little")
        b += (0).to_bytes(8, "little") + (0).to_bytes(8, "little")
        b += (0).to_bytes(4, "little") * 3
        b += ((0x10 if is_dir else 0x20)).to_bytes(4, "little")
        b += len(nb).to_bytes(2, "little") + (60).to_bytes(2, "little") + nb
        return b

    def v3rec(frn, parent, name):
        nb = name.encode("utf-16-le")
        b = (76 + len(nb)).to_bytes(4, "little") + (3).to_bytes(2, "little") + (0).to_bytes(2, "little")
        b += frn.to_bytes(16, "little") + parent.to_bytes(16, "little")
        b += (0).to_bytes(8, "little") + (0).to_bytes(8, "little")
        b += (0).to_bytes(4, "little") * 3
        b += ((0x20)).to_bytes(4, "little")
        b += len(nb).to_bytes(2, "little") + (76).to_bytes(2, "little") + nb
        return b

    n = 0
    count = [0]

    def run_build(pages, fail_at=None, fail_err=None, v1_pages=None):
        """pages=[(next_frn,[rec_bytes...])]；fail_at=第 N 次 ENUM 调用硬失败（mock err）；
        v1_pages=V1 输入结构（假设 B 切换）专供数据序列（sz>24 区分）。"""
        nonlocal n
        n += 1
        letter = chr(ord("M") + n)   # M/N/O/... 独立卷名
        tmpdb = tmp + (".%s" % letter)
        os.environ["FS_INDEX_DB"] = tmpdb
        conn = fi._connect()
        conn.executescript(fi._SCHEMA)
        fi._migrate(conn)   # 残留旧 schema 库补 partial 列（生产同款迁移语义）
        state = {"enum": 0, "query": 0, "enum_v1": 0}

        def fake_open(letter_):
            return 4242 + state["query"]

        def fake_ioctl(h, code, in_buf, out_size):
            if code == fi.FSCTL_QUERY_USN_JOURNAL:
                state["query"] += 1
                j = fi.USN_JOURNAL_DATA(7, 0, 100, 0, 0x7FFFFFFFFFFFFFFF, 0x100000, 0x20)
                return True, bytes(j)
            if code == fi.FSCTL_ENUM_USN_DATA:
                sz = fi.ctypes.sizeof(in_buf) if in_buf is not None else 0
                if sz <= 24:
                    seq, idx_key = pages, "enum"
                else:
                    seq, idx_key = (v1_pages or pages), "enum_v1"
                i = state[idx_key]
                state[idx_key] = i + 1
                if fail_at is not None and seq is pages and i >= fail_at:
                    fi._last_ioctl_error = fail_err
                    return False, b""
                if i < len(seq):
                    nxt, recs = seq[i]
                    return True, nxt.to_bytes(8, "little") + b"".join(recs)
                fi._last_ioctl_error = fi.ERROR_HANDLE_EOF
                return False, b""
            fi._last_ioctl_error = 1
            return False, b""

        fi._open_volume = fake_open
        fi._device_ioctl = fake_ioctl
        r = fi.build_volume(conn, letter)
        dbfile = fi.db_path()
        files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        has_state = conn.execute("SELECT COUNT(*) FROM usn_state").fetchone()[0]
        names = [x[0] for x in conn.execute("SELECT name FROM files ORDER BY name").fetchall()]
        conn.close()
        return r, files, has_state, names

    grp = [0]

    def gcheck(name, cond, detail=""):
        grp[0] += 1
        (PASS if cond else FAIL).append(name)
        print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  | " + str(detail)[:140]))

    # T1 正常两页 + EOF：records int 契约 + 入库 + 游标落
    r, files, has_state, names = run_build([(9, [v2rec(10, 5, "a.txt"), v2rec(11, 5, "logs", True), v2rec(12, 11, "b.log")]),
                                            (14, [v2rec(13, 5, "c.txt")])])
    gcheck("mock·正常 build records 为 int 且=4（含目录）", isinstance(r.get("records"), int) and r["records"] == 4, r)
    gcheck("mock·正常 build 入库 4（含目录）", files == 4, names)
    gcheck("mock·正常 build 游标已落", has_state == 1, has_state)

    # T2 1392 partial：第二页硬失败 → 已收记录保留 + partial 状态行落（游标 NULL）
    r, files, has_state, names = run_build([(9, [v2rec(10, 5, "a.txt"), v2rec(11, 5, "logs", True)])],
                                           fail_at=1, fail_err=1392)
    gcheck("mock·1392 partial ok=False 且已收入库", r.get("ok") is False and r.get("partial") is True
           and r.get("records") == 2 and files == 2, r)
    gcheck("mock·1392 partial 游标 NULL + partial 标记", has_state == 1, has_state)
    # T2b partial 卷 consume 静默跳过（设计内常态，2026-09-15 派修②）
    conn2 = fi._connect()
    vol_row = conn2.execute("SELECT volume, next_usn, partial FROM usn_state").fetchone()
    gcheck("mock·partial 行形状（游标 NULL+标记 1）", vol_row is not None
           and vol_row[1] is None and vol_row[2] == 1, vol_row)
    rc = fi.consume_volume(conn2, vol_row[0])
    gcheck("mock·partial 卷 consume 静默跳过", rc.get("ok") is True and rc.get("skipped") == "partial", rc)
    conn2.close()

    # T3 V3 记录（128 位 FRN 取低 64）
    r, files, has_state, names = run_build([(9, [v3rec(200, 5, "v3file.dat")])])
    gcheck("mock·V3 记录解析（低 64 位 FRN）", files == 1 and "v3file.dat" in names, names)

    # T4 尾部截断回退：页 1 尾截断（半条）→ 回退 safe_frn 重枚举完整页
    good = v2rec(30, 5, "half-vec.txt")
    half = good[:len(good) // 2]
    pages = [(9, [v2rec(29, 5, "first.txt"), half]), (31, [good, v2rec(31, 5, "tail.txt")])]
    r, files, has_state, names = run_build(pages)
    gcheck("mock·截断回退零丢失", files >= 3 and "half-vec.txt" in names and "tail.txt" in names, names)

    # T5 首轮 0 解析（合法记录但空名被丢弃）→ V1 切换重枚举（假设 B 路径）
    pages = [(9, [v2rec(39, 5, ""), v2rec(40, 5, "after-switch.txt")]), (41, [v2rec(41, 5, "next.txt")])]
    v1_pages = [(9, [v2rec(40, 5, "after-switch.txt")]), (41, [v2rec(41, 5, "next.txt")])]
    r, files, has_state, names = run_build(pages, v1_pages=v1_pages)
    gcheck("mock·V1 切换重枚举后正常入库", files >= 2 and "after-switch.txt" in names,
          {"files": files, "names": names, "switched": r.get("switched_v1")})

    # T6 父链 FRN 高位段号不一致（2026-09-15 铁证回归门禁）：父记录自身 seq=0x36、
    # 子记录保存的父引用 seq=0x01，低 48 位 MFT 记录号相同 → 统一低 48 位键后必须物化成功
    SEQ_A = 0x0036000000000000
    SEQ_B = 0x0001000000000000
    MFT_LOW = 0x10
    pages = [(9, [v2rec(SEQ_A | MFT_LOW, 5, "dir36", True),
                  v2rec(0x0001000000002281, SEQ_B | MFT_LOW, "child.txt")]),
             (0x2282, [])]
    r, files, has_state, names = run_build(pages)
    gcheck("mock·FRN 高位段号不一致物化成功（低 48 位键）",
          files == 2 and "child.txt" in names and r.get("parsed") == 2
          and (r.get("drops") or {}).get("parent_not_found") == 0, r)
    gcheck("mock·parsed/persisted 分离字段", isinstance(r.get("parsed"), int)
           and "drops" in r, {"parsed": r.get("parsed"), "drops": r.get("drops")})

    # T7 wait_until_indexed：命中即返（无 sleep）+ 超时 graceful（含 sleep 不热轮询）
    os.environ["FS_INDEX_DB"] = tmp + ".W7"
    conn7 = fi._connect()
    conn7.executescript(fi._SCHEMA)
    conn7.execute("INSERT OR REPLACE INTO files(volume,frn,parent_frn,name,path,is_dir,size,mtime) "
                  "VALUES('W7',10,5,'a.txt','W7:\\a.txt',0,10,1727000000)")
    conn7.commit()
    t0 = time.time()
    hit, elapsed = fi.wait_until_indexed(conn7, "a.txt", timeout=5, poll=0.2)
    gcheck("mock·wait 命中即返（<1s 无无谓 sleep）", hit is True and (time.time() - t0) < 1.0,
          {"hit": hit, "elapsed": round(elapsed, 2)})
    t0 = time.time()
    miss, elapsed = fi.wait_until_indexed(conn7, "no_such_keyword_zz", timeout=1.5, poll=0.4)
    gcheck("mock·wait 超时 graceful（sleep 生效不热轮询）", miss is False and (time.time() - t0) >= 1.4,
          {"miss": miss, "elapsed": round(elapsed, 2)})
    conn7.close()

    # T8 journal 失效自愈（ADR-006）：journal_reset → 当场重建 → 再消费 单轮闭环
    def run_journal_consume(query_id, state_id=1, query_fail_err=None, read_fail_err=None):
        """独立库预插卷游标(state_id, next_usn=100)+files 一条；mock QUERY/READ。
        返回 (consume结果, usn_state 行数, files 行数)。"""
        nonlocal n
        n += 1
        letter = chr(ord("M") + n)
        tmpdb = tmp + (".%s%d" % (letter, os.getpid()))   # pid 后缀进程级唯一，历史残留零相撞
        os.environ["FS_INDEX_DB"] = tmpdb
        c = fi._connect()
        c.executescript(fi._SCHEMA)
        c.execute("INSERT OR REPLACE INTO files(volume,frn,parent_frn,name,path,is_dir) VALUES(?,?,?,?,?,0)",
                  (letter, 77, 5, "seed.txt", letter + ":\\seed.txt"))
        c.execute("INSERT INTO usn_state(volume,journal_id,next_usn,partial) VALUES(?,?,100,0)",
                  (letter, state_id))
        c.commit()

        def fake_open(_l):
            return 4242

        def fake_ioctl(h, code, in_buf, out_size):
            if code == fi.FSCTL_QUERY_USN_JOURNAL:
                if query_fail_err is not None:
                    fi._last_ioctl_error = query_fail_err
                    return False, b""
                j = fi.USN_JOURNAL_DATA(query_id, 0, 100, 0, 0x7FFFFFFFFFFFFFFF, 0x100000, 0x20)
                return True, bytes(j)
            if code == fi.FSCTL_READ_USN_JOURNAL:
                if read_fail_err is not None:
                    fi._last_ioctl_error = read_fail_err
                    return False, b""
                fi._last_ioctl_error = fi.ERROR_HANDLE_EOF
                return False, b""
            fi._last_ioctl_error = 1
            return False, b""

        fi._open_volume = fake_open
        fi._device_ioctl = fake_ioctl
        rc = fi.consume_volume(c, letter)
        st = c.execute("SELECT COUNT(*) FROM usn_state").fetchone()[0]
        fc = c.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        c.close()
        return rc, st, fc

    rc, st, fc = run_journal_consume(query_id=2, state_id=1)
    gcheck("T8·journal_id 变化→journal_reset 信号", rc.get("ok") is False
           and rc.get("error") == "journal_reset" and rc.get("rebuild") is True, rc)
    gcheck("T8·id_changed 重置清空游标与索引（半新半旧不可留）", st == 0 and fc == 0,
          {"usn_state": st, "files": fc})

    rc, st, fc = run_journal_consume(query_id=1, state_id=1, read_fail_err=1181)
    gcheck("T8·read err=1181（JOURNAL_DELETED）→journal_reset", rc.get("error") == "journal_reset"
           and rc.get("rebuild") is True and rc.get("err") == 1181, rc)
    gcheck("T8·read 失效重置清空", st == 0 and fc == 0, {"usn_state": st, "files": fc})

    rc, st, fc = run_journal_consume(query_id=1, state_id=1, query_fail_err=1180)
    gcheck("T8·query err=1180（JOURNAL_NOT_ACTIVE）→journal_reset", rc.get("error") == "journal_reset"
           and rc.get("rebuild") is True, rc)

    rc, st, fc = run_journal_consume(query_id=1, state_id=1, read_fail_err=50)
    gcheck("T8·非失效码（err=50）不重置、游标保留", rc.get("error") == "read_journal_failed"
           and rc.get("rebuild") is None and st == 1 and fc == 1, (rc, st, fc))

    # T8b run_once 集成：重置→当场重建→再消费（真实 run_once 编排路径）
    n += 1
    letter = chr(ord("M") + n)
    tmpdb = tmp + (".%s%d" % (letter, os.getpid()))   # pid 后缀进程级唯一
    os.environ["FS_INDEX_DB"] = tmpdb
    c = fi._connect()
    c.executescript(fi._SCHEMA)
    c.execute("INSERT OR REPLACE INTO files(volume,frn,parent_frn,name,path,is_dir) VALUES(?,?,?,?,?,0)",
              (letter, 77, 5, "stale.txt", letter + ":\\stale.txt"))
    c.execute("INSERT INTO usn_state(volume,journal_id,next_usn,partial) VALUES(?,?,100,0)", (letter, 1))
    c.commit()
    state8 = {"enum": 0}

    def fake_open8(_l):
        return 4242

    def fake_ioctl_full(h, code, in_buf, out_size):
        if code == fi.FSCTL_QUERY_USN_JOURNAL:
            j = fi.USN_JOURNAL_DATA(2, 0, 100, 0, 0x7FFFFFFFFFFFFFFF, 0x100000, 0x20)
            return True, bytes(j)
        if code == fi.FSCTL_ENUM_USN_DATA:
            i = state8["enum"]
            state8["enum"] += 1
            if i == 0:
                return True, (20).to_bytes(8, "little") + v2rec(20, 5, "rebuilt.txt")
            fi._last_ioctl_error = fi.ERROR_HANDLE_EOF
            return False, b""
        if code == fi.FSCTL_READ_USN_JOURNAL:
            fi._last_ioctl_error = fi.ERROR_HANDLE_EOF
            return False, b""
        fi._last_ioctl_error = 1
        return False, b""

    fi._open_volume = fake_open8
    fi._device_ioctl = fake_ioctl_full
    fi.discover_volumes = lambda: ([letter], [])
    rep = fi.run_once(c)
    modes = [v.get("mode") for v in rep.get("volumes", [])]
    gcheck("T8·run_once 自愈闭环单轮完成（reset→重建→再消费）",
          modes == ["consume", "journal_reset_rebuild", "consume"], modes)
    rb = [v for v in rep["volumes"] if v.get("mode") == "journal_reset_rebuild"]
    gcheck("T8·当场重建成功且入库", bool(rb) and rb[0].get("ok") is True and rb[0].get("records") == 1, rb)
    row = c.execute("SELECT journal_id, next_usn, partial FROM usn_state WHERE volume=?", (letter,)).fetchone()
    gcheck("T8·重建后游标落新 journal_id=2", row is not None and row[0] == 2 and row[1] == 100 and row[2] == 0, row)
    nm = [x[0] for x in c.execute("SELECT name FROM files").fetchall()]
    gcheck("T8·旧索引被清、新索引在位（stale 不残留）", "rebuilt.txt" in nm and "stale.txt" not in nm, nm)
    c.close()

    return grp[0]

    total = len(PASS) + len(FAIL)
    print("\n===== SMOKE RESULT: %d/%d passed =====" % (len(PASS), total))
    if FAIL:
        for f in FAIL:
            print("  -", f)
        sys.exit(1)
    print("FILESEARCH SQLITE SMOKE: PASS")


if __name__ == "__main__":
    main()
