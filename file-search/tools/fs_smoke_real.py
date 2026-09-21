# -*- coding: utf-8 -*-
"""
fs_smoke_real.py — P1 真数据实测（路线 C 自研索引器，须管理员会话运行）
=======================================================================
v2（2026-09-15）：**默认复用现有索引库**（162 万条物化是真重活，不再删除重建）；
增量测试目标卷从 usn_state 现有游标卷里选（不依赖 C 盘 partial）；轮询带 sleep+90s
硬超时（graceful FAIL，绝不热轮询打满 CPU）。--fresh 才强制重建全量索引。

运行（管理员 PowerShell）：
  cd file-search
  python tools\\fs_smoke_real.py           # 复用现有库（增量+检索验证，分钟内）
  python tools\\fs_smoke_real.py --fresh   # 删库重建全量索引（20+ 分钟，慎用）
"""

import ctypes
import json
import os
import shutil
import sys
import tempfile
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # 提权控制台 GBK 下 JSON 不被截断
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

if os.name != "nt" or not ctypes.windll.shell32.IsUserAnAdmin():
    print("需要管理员权限运行（读 MFT/USN 为 Windows 安全边界）。")
    sys.exit(2)

import fs_indexer            # noqa: E402
import search_service as ss  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  | " + str(detail)[:170]))


def main():
    fresh = "--fresh" in sys.argv
    if fresh:
        for suffix in ("", "-wal", "-shm"):
            p = fs_indexer.db_path() + suffix
            if os.path.exists(p):
                os.remove(p)
        print("--fresh：已删除现有索引库，将全量重建")

    conn = fs_indexer._connect()
    conn.executescript(fs_indexer._SCHEMA)
    fs_indexer._migrate(conn)

    # 1. 建库（复用或首建）
    st = fs_indexer.status(conn)
    if st.get("file_count", 0) > 0:
        check("复用·现有索引库（%d 文件，不重建）" % st["file_count"], True)
    else:
        t0 = time.time()
        report = fs_indexer.run_once(conn)
        print("run_once report:", json.dumps(report, ensure_ascii=True)[:600])
        check("首建·全部 NTFS 卷成功", all(v.get("ok") for v in report["volumes"]) and report["volumes"], report)
        check("首建·耗时 %.1fs" % (time.time() - t0), True)

    # 2. 游标卷选择（partial 卷跳过——其增量语义未生效）
    rows = conn.execute("SELECT volume, partial FROM usn_state ORDER BY volume").fetchall()
    cursor_vols = [r[0] for r in rows if not r[1]]
    partial_vols = [r[0] + ":" for r in rows if r[1]]
    check("游标卷存在（partial 卷如实列出：%s）" % (",".join(partial_vols) or "无"),
          bool(cursor_vols), {"cursor": cursor_vols, "partial": partial_vols})
    if not cursor_vols:
        print("无可用游标卷（全 partial），增量测试跳过；请先 --fresh 或排查 build 失败卷。")
        total = len(PASS) + len(FAIL)
        print("\n===== REAL SMOKE RESULT: %d/%d passed =====" % (len(PASS), total))
        sys.exit(1)
    # 2.5 健康探测轮（ADR-006 journal 失效自愈）：先消费一轮再选目标卷——
    #     journal 失效卷当轮自动重建（重建成功=健康）；重建失败才 fallback 下一健康卷，
    #     单卷日志失效不再阻塞全链判定。探测轮同时把全库游标追平。
    rep0 = fs_indexer.run_once(conn)
    print("probe run_once report:", json.dumps(rep0, ensure_ascii=True)[:1500])

    def _vol_health(rep, lv):
        """(healthy, note)：以该卷最后一个 consume 条目为准——ok=健康（skipped=partial 不算）；
        无 consume ok 但 journal_reset_rebuild ok=自愈重建成功（健康）；其余=不健康。"""
        consumes = [v for v in rep.get("volumes", [])
                    if v.get("volume") == lv + ":" and v.get("mode") == "consume"]
        if consumes and consumes[-1].get("ok"):
            return True, "consume_ok"
        rebuilt = [v for v in rep.get("volumes", []) if v.get("volume") == lv + ":"
                   and v.get("mode") == "journal_reset_rebuild" and v.get("ok")]
        if rebuilt:
            return True, "journal_reset_rebuilt"
        return False, (consumes and consumes[-1].get("error")) or "no_consume"

    target_vol, health_note, health_seq = None, "", []
    for lv in cursor_vols:
        okh, note_ = _vol_health(rep0, lv)
        health_seq.append("%s=%s" % (lv, note_))
        if okh and target_vol is None:
            target_vol, health_note = lv + ":", note_
    check("增量·目标卷健康（探测：%s）" % " ".join(health_seq), bool(target_vol), health_seq)
    if not target_vol:
        print("无健康游标卷（探测报告见上），增量测试跳过。")
        total = len(PASS) + len(FAIL)
        print("\n===== REAL SMOKE RESULT: %d/%d passed =====" % (len(PASS), total))
        sys.exit(1)

    # 3. 基线检索（真关键词）
    r = ss.handle_fs_query({"q": "windows", "count": 20})
    check("检索·真关键词命中", r["success"] and r["count"] > 0, r.get("count"))

    # 4. 新建文件（目标卷根下固定目录）→ 增量消费 → 轮询命中（sleep+90s 超时）
    sandbox = os.path.join(target_vol + "\\", "EyeTerm_smoke")
    os.makedirs(sandbox, exist_ok=True)
    marker = os.path.join(sandbox, "EyeTerm_smoke_marker.txt")
    with open(marker, "w", encoding="utf-8") as f:
        f.write("fs_smoke_real marker\n")
    rep = fs_indexer.run_once(conn)
    print("run_once report:", json.dumps(rep, ensure_ascii=True)[:2000])
    consume = [v for v in rep.get("volumes", []) if v["volume"] == target_vol and v["mode"] == "consume"]
    check("增量·consume 目标卷执行", bool(consume) and consume[-1].get("ok"), consume)
    hit, elapsed = fs_indexer.wait_until_indexed(conn, "EyeTerm_smoke_marker.txt", timeout=90, poll=1.0)
    check("实时·新建文件入索引（%.1fs，轮询含 sleep）" % elapsed, hit,
          {"hit": hit, "elapsed": elapsed, "volume": target_vol})

    # 5. 契约形状
    r = ss.handle_fs_query({"q": "EyeTerm_smoke_marker.txt"})
    if r.get("results"):
        it = [x for x in r["results"] if x["name"] == "EyeTerm_smoke_marker.txt"][0]
        check("契约·results 键形状（含 is_dir，2026-09-16 展示增强字段）",
              set(it.keys()) == {"name", "path", "is_dir", "size", "date_modified"}, it)
        check("契约·path 为所在目录", it["path"].endswith(sandbox), it["path"])

    # 6. 排序真数据抽查
    r = ss.handle_fs_query({"q": "windows", "sort": "size", "ascending": "0", "count": 50})
    sizes = [x["size"] or 0 for x in r.get("results") or []]
    check("排序·真数据 size 降序", sizes == sorted(sizes, reverse=True), sizes[:10])

    # 7. 删除 → 失效
    os.remove(marker)
    fs_indexer.run_once(conn)
    hit2, _el = fs_indexer.wait_until_indexed(conn, "EyeTerm_smoke_marker.txt", timeout=15, poll=1.0)
    check("实时·删除后索引失效", not hit2, {"hit2": hit2})
    shutil.rmtree(sandbox, ignore_errors=True)

    total = len(PASS) + len(FAIL)
    print("\n===== REAL SMOKE RESULT: %d/%d passed =====" % (len(PASS), total))
    if FAIL:
        for f in FAIL:
            print("  -", f)
        sys.exit(1)
    print("FILESEARCH REAL SMOKE: PASS")


if __name__ == "__main__":
    main()
