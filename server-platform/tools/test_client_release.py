#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""客户端版本发布管理（ADR-042）本地单测。

覆盖：定制文件名协议（build/parse round-trip/md58 校验/截断回退语义）+
版本存储（上传覆盖/格式校验）+ current 指针与回滚标记 + manifest + 一次性票据。

零凭据临时库；运行后自清理。"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "server"))

import client_release as cr_mod                        # noqa: E402
from client_release import (ClientReleaseError,        # noqa: E402
                            ClientReleaseStore,
                            build_custom_filename,
                            parse_custom_filename)

PASSED, FAILED = [], []

import re as _re                                      # noqa: E402

_CUSTOM_FMT = _re.compile(
    r"^EyeTerm_Setup_x64_\d{1,3}(?:\.\d{1,3}){1,3}_"
    r"[A-Za-z0-9_-]{1,160}_[0-9a-f]{8}\.exe$")


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" | " + detail) if detail else ""))


def expect_err(name, fn, status=None):
    try:
        fn()
        check(name, False, "no exception raised")
    except ClientReleaseError as e:
        ok = (status is None) or (e.http_status == status)
        check(name, ok, "status=%s msg=%s" % (e.http_status, e.message))


def main():
    tmp = tempfile.mkdtemp(prefix="etp_cr_")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n=== client release tests: pass %d / fail %d ==="
          % (len(PASSED), len(FAILED)))
    return 0 if not FAILED else 1


def _ins_cmd(conn, tid, status, source, result_json=None):
    """直插一条 commands 行（本模块是发布模块的独立临时库，commands 表在
    真实环境由 store.py 的 Store 建；此处只验批次聚合 SQL 的正确性）。"""
    conn.execute(
        "INSERT INTO commands(terminal_id, command, args_json, status,"
        " timeout_sec, source, created_ts, result_json)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (tid, "client_update", "{}", status, 60, source, 1760000000,
         result_json))
    conn.commit()


def run(tmp):
    db = os.path.join(tmp, "eyeterm.db")
    store = ClientReleaseStore(db, tmp)

    # ---------- 1. 定制文件名协议 ----------
    fn = build_custom_filename("http://172.17.5.215:18090", "tok123", "4.0.0")
    check("定制名格式前缀",
          _CUSTOM_FMT.match(fn) is not None, fn)
    parts = parse_custom_filename(fn)
    check("round-trip 解出 server", parts["s"] == "http://172.17.5.215:18090")
    check("round-trip 解出 token", parts["t"] == "tok123")
    check("round-trip 解出 version", parts["version"] == "4.0.0")

    # 篡改 cfg 段 → md58 校验失败（cfg64 含 -/_ 字符，必须用协议正则 dissect）
    m0 = cr_mod._CUSTOM_RE.match(fn)
    cfg = m0.group("cfg")
    bad_cfg = cfg[:-1] + ("A" if cfg[-1] != "A" else "B")
    tampered = fn.replace("_" + cfg + "_", "_" + bad_cfg + "_", 1)
    expect_err("篡改 cfg 校验失败", lambda: parse_custom_filename(tampered))

    # 非 base64url 字符 → 解析失败
    expect_err("非法字符解析失败",
               lambda: parse_custom_filename(
                   "EyeTerm_Setup_x64_1.0.0_%%%%%%%%_md58.exe"))
    # 普通文件名 → 格式无效
    expect_err("普通文件名格式无效",
               lambda: parse_custom_filename("setup_4.0.0.exe"))

    # 截断路径：超长不可压缩 token 触发 cfg64 > 160 截断 → 解析失败（回退语义）
    import os as _os
    long_token = _os.urandom(400).hex()      # 800 hex 字符，不可压缩
    long_fn = build_custom_filename("http://172.17.5.215:18090",
                                    long_token, "4.0.0")
    m1 = cr_mod._CUSTOM_RE.match(long_fn)
    cfg_long = m1.group("cfg") if m1 else ""
    check("超长配置触发截断", len(cfg_long) == 160,
          "cfg_len=%d" % len(cfg_long))
    expect_err("截断后解析失败(回退语义)",
               lambda: parse_custom_filename(long_fn))

    # 中文（ensure_ascii=False 原文编码）round-trip
    fn2 = build_custom_filename("http://平台:18090", "tok中文", "4.0.0")
    parts2 = parse_custom_filename(fn2)
    check("中文字段 round-trip",
          parts2["s"] == "http://平台:18090" and parts2["t"] == "tok中文")

    # cfg64 提取与校验（长期分发链接 ?cfg64= 的入参把关，2026-09-19）
    cfg_ok = cr_mod.custom_cfg64(fn)
    check("custom_cfg64 提取正确", cfg_ok == cfg, cfg_ok[:24])
    check("validate_cfg64 接受合法 cfg64", cr_mod.validate_cfg64(cfg_ok) is True)
    check("custom_cfg64 对通用名返回空", cr_mod.custom_cfg64(
        "EyeTerm_Setup_x64_4.1.10_20260919_2150.exe") == "")
    check("validate_cfg64 拒绝空串", cr_mod.validate_cfg64("") is False)
    check("validate_cfg64 拒绝随机串",
          cr_mod.validate_cfg64("not-a-valid-cfg-at-all") is False)
    check("validate_cfg64 拒绝超长串",
          cr_mod.validate_cfg64("A" * 400) is False)
    # 合法 base64+zlib 但 payload 缺 token → 拒绝
    import base64 as _b64
    import zlib as _zlib
    _bad = _b64.urlsafe_b64encode(
        _zlib.compress(b'{"s":"http://x","t":""}')).decode().rstrip("=")
    check("validate_cfg64 拒绝缺 token 的配置",
          cr_mod.validate_cfg64(_bad) is False)

    # ---------- 2. 版本存储 ----------
    check("空库 list", store.list() == [])
    check("无 current 时 manifest 为 None", store.manifest() is None)
    check("无 current 时 current_version 为 None",
          store.current_version() is None)

    r1 = store.upload("windows", "4.0.0", "EyeTerm_Setup_x64_4.0.0.exe",
                      b"PK" + b"x" * 99, "首版")
    check("上传记录字段", r1["version"] == "4.0.0" and r1["size"] == 101
          and r1["note"] == "首版" and len(r1["sha256"]) == 64)

    r1b = store.upload("windows", "4.0.0", "EyeTerm_Setup_x64_4.0.0.exe",
                       b"PK" + b"y" * 99, "覆盖更新")
    check("同版本覆盖更新 sha256/note 且 id 不变",
          r1b["id"] == r1["id"] and r1b["sha256"] != r1["sha256"]
          and r1b["note"] == "覆盖更新")
    row, data = store.read_file(r1["id"])
    check("覆盖后文件内容为新", data == b"PK" + b"y" * 99)

    # 同版本换文件名 → 旧文件清理
    store.upload("windows", "4.0.0", "renamed_setup.exe", b"PKz", "改名")
    old_path = os.path.join(tmp, "storage", "client", "4.0.0",
                            "EyeTerm_Setup_x64_4.0.0.exe")
    check("覆盖换名旧文件清理", not os.path.isfile(old_path))

    expect_err("版本号格式拒绝", lambda: store.upload("windows", "4_0_0", "a.exe", b"x"))
    expect_err("空版本号拒绝", lambda: store.upload("windows", "", "a.exe", b"x"))
    expect_err("空文件名拒绝", lambda: store.upload("windows", "1.0.0", "", b"x"))
    expect_err("路径穿越文件名拒绝",
               lambda: store.upload("windows", "1.0.0", "..evil.exe", b"x"))
    expect_err("空内容拒绝", lambda: store.upload("windows", "1.0.0", "a.exe", b""))
    expect_err("超上限拒绝",
               lambda: store.upload("windows", "1.0.0", "a.exe",
                                    b"x" * (cr_mod._MAX_UPLOAD_BYTES + 1)))

    # ---------- 3. current 指针与回滚标记 ----------
    r2 = store.upload("windows", "4.1.0", "setup_4.1.0.exe", b"NEW", "次版")
    cur = store.set_current(r2["id"])
    check("发布最新版本 rollback_flag=0", cur["rollback_flag"] == 0
          and store.current_version() == "4.1.0")
    back = store.set_current(r1["id"])
    check("指回旧版 rollback_flag=1", back["rollback_flag"] == 1
          and store.current_version() == "4.0.0")
    store.set_current(r2["id"])
    check("再指回最新 rollback_flag=0",
          store.get(r2["id"])["rollback_flag"] == 0)
    expect_err("设置不存在版本 404", lambda: store.set_current(99999),
               status=404)

    # 同 published_at 平局（同秒上传）：指回平局旧 id 不标回滚
    r3 = store.upload("windows", "4.2.0", "s3.exe", b"N3", "平局")
    store.set_current(r2["id"])
    store.set_current(r3["id"])
    check("平局指向 rollback_flag=0", store.get(r3["id"])["rollback_flag"] == 0)

    # ---------- 4. manifest ----------
    m = store.manifest()
    check("manifest 字段齐全", m is not None
          and m["latest_version"] == "4.2.0"
          and m["download_url"] == "/download/client/setup"
          and len(m["sha256"]) == 64 and m["size"] == 2
          and m["release_note"] == "平局")

    # ---------- 5. 一次性下载票据 ----------
    fn3 = build_custom_filename("http://172.17.5.215:18090", "tok", "4.2.0")
    tk = store.issue_ticket(r3["id"], fn3)
    info = store.consume_ticket(tk)
    check("票据消费返回定制名与版本", info["filename"] == fn3
          and info["release_id"] == r3["id"])
    expect_err("票据一次性（二次消费 404）",
               lambda: store.consume_ticket(tk), status=404)
    expect_err("无效票据 404", lambda: store.consume_ticket("nope"),
               status=404)

    tk2 = store.issue_ticket(r3["id"], fn3, ttl=0)
    import time as _time
    _time.sleep(0.05)
    expect_err("过期票据 410", lambda: store.consume_ticket(tk2), status=410)

    # 票据大量签发不膨胀（惰性清理上限）
    for _ in range(300):
        store.issue_ticket(r3["id"], fn3, ttl=0)
    check("过期票据惰性清理", len(store._tickets) <= 300,
          "tickets=%d" % len(store._tickets))

    # ---------- 6. read_file 边界 ----------
    expect_err("读取不存在版本 404", lambda: store.read_file(424242),
               status=404)
    row, data = store.read_file(r3["id"])
    check("read_file 内容一致", data == b"N3" and row["version"] == "4.2.0")

    # ---------- 7. 更新推送批次（2026-09-19）----------
    #   commands/terminals 在真实环境由 store.py 的 Store 建表；此临时库需显式
    #   建表并直插行，只验批次聚合 SQL（跨批次不串台、回执内层解包、倒序列表）。
    store._conn.executescript("""
CREATE TABLE IF NOT EXISTS commands (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  terminal_id TEXT NOT NULL,
  command TEXT NOT NULL,
  args_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending',
  timeout_sec INTEGER NOT NULL DEFAULT 120,
  source TEXT NOT NULL DEFAULT 'console',
  created_ts INTEGER NOT NULL DEFAULT 0,
  sent_ts INTEGER,
  done_ts INTEGER,
  result_json TEXT
);
CREATE TABLE IF NOT EXISTS terminals (
  terminal_id TEXT PRIMARY KEY,
  ip TEXT NOT NULL DEFAULT '',
  client_version TEXT NOT NULL DEFAULT '',
  last_seen INTEGER NOT NULL DEFAULT 0
);
""")
    store._conn.commit()

    check("空库推送批次为空", store.list_batches() == [])
    check("批次不存在返回 None", store.batch_get(999999) is None)

    tids = ["T-A", "T-B", "T-C"]
    bid = store.push_create("silent", "4.2.0", tids,
                            operator="admin", note="批量推送")
    check("push_create 返回批次 id", isinstance(bid, int) and bid > 0)
    b = store.batch_get(bid)
    check("批次字段落库完整",
          b["mode"] == "silent" and b["target_version"] == "4.2.0"
          and b["total"] == 3 and b["operator"] == "admin"
          and b["note"] == "批量推送" and b["targets"] == tids, str(b))
    check("批次初始统计全零", b["stats"]["total"] == 0)
    src = store.push_source(bid)
    check("批次 source 标记格式", src == "client-push:%d" % bid, src)

    other = store.push_create("notify", "4.2.0", ["T-Z"])
    _ins_cmd(store._conn, "T-A", "pending", src)
    _ins_cmd(store._conn, "T-B", "sent", src)
    _ins_cmd(store._conn, "T-Z", "pending", store.push_source(other))
    _ins_cmd(store._conn, "T-A", "pending", "console")     # 非本批次命令
    check("批次统计只认本批次 source",
          store.batch_get(bid)["stats"] == {"pending": 1, "sent": 1,
                                            "executed": 0, "failed": 0,
                                            "timeout": 0, "total": 2},
          str(store.batch_get(bid)["stats"]))
    check("另一批次统计独立",
          store.batch_get(other)["stats"]["pending"] == 1)

    cid_a = store._conn.execute(
        "SELECT id FROM commands WHERE terminal_id='T-A' AND source=?",
        (src,)).fetchone()["id"]
    store._conn.execute(
        "UPDATE commands SET status='executed', result_json=? WHERE id=?",
        (cr_mod.json.dumps({
            "mode": "silent", "status": "ready", "version": "4.2.0",
            "note": "silent_install_scheduled"}, ensure_ascii=False), cid_a))
    store._conn.execute(
        "INSERT INTO terminals(terminal_id, ip, client_version, last_seen)"
        " VALUES('T-A', '10.0.0.9', '4.1.9', 1760000000)")
    store._conn.commit()
    check("回执后 executed=1 / sent=1",
          store.batch_get(bid)["stats"]["executed"] == 1
          and store.batch_get(bid)["stats"]["sent"] == 1)

    items = store.batch_detail(bid)
    check("批次明细逐终端 2 条", len(items) == 2, "n=%d" % len(items))
    hit = [x for x in items if x["terminal_id"] == "T-A"]
    check("明细解包回执内层（ok/version/apply_status/note + 终端版本）",
          bool(hit) and hit[0]["ok"] is True and hit[0]["version"] == "4.2.0"
          and hit[0]["apply_status"] == "ready"
          and hit[0]["note"] == "silent_install_scheduled"
          and hit[0]["client_version"] == "4.1.9"
          and hit[0]["ip"] == "10.0.0.9", str(hit[:1]))

    check("批次列表倒序含两批",
          [x["id"] for x in store.list_batches()] == [other, bid])
    check("删除批次台账命中", store.push_delete(other) is True
          and store.batch_get(other) is None)
    check("删除不存在的批次返回 False", store.push_delete(999999) is False)

    # ---------- 8. 单例形态 ----------
    s1 = cr_mod.get_cr(db, tmp)
    s2 = cr_mod.get_cr(db, tmp)
    check("get_cr 单例", s1 is s2)
    s1.close()


if __name__ == "__main__":
    sys.exit(main())
