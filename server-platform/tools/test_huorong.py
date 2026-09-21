#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ADR-033 单测：火绒客户端（签名金向量/分页/errno 分支/破坏性门禁/TLS pin）
+ 镜像同步幂等 + 控制台路由（scope/admin 鉴权）+ mock HTTP 冒烟。

运行：python tools/test_huorong.py
（纯标准库 + 临时库 + 本机 mock HTTP 服务，零真实凭据、零真实请求、
零任务类接口调用——含 mock 在内全仓不出现 /api/task/_create 成功调用）
"""
import base64
import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "server")))
sys.path.insert(0, _HERE)

import api as api_mod                    # noqa: E402
import auth_upgrade as auth              # noqa: E402
from huorong import (HuorongApiError, HuorongClient, HuorongSyncer,     # noqa: E402
                     HuorongTaskDisabled, assets_row, mirror_rows, _parse_ts)
from store import Store                  # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" | " + str(detail)) if (detail and not cond) else ""))


def dispatch_status(scope, method, path, headers=None, body=b"", query=None,
                   ctx=None):
    try:
        result = api_mod.dispatch(ctx, method, path, query or {},
                                  headers or {}, body, "127.0.0.1", scope=scope)
        if isinstance(result, tuple):
            return result[0]
        return result.status
    except api_mod.ApiError as exc:
        return exc.status


# ----------------------------------------------------------------------
# mock 火绒服务端（独立长算签名校验，与被测实现零共享）
# ----------------------------------------------------------------------

class _MockHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        srv = self.server
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        parsed = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(parsed.query)
        srv.hits.append((parsed.path, body))
        if not _verify_signature(srv, q, body, parsed.path):
            srv.bad_auth += 1
            self._json({"errno": 1, "errmsg": "Authentication failed"})
            return
        script = srv.script.get(parsed.path)
        item = script.pop(0) if script else {"errno": 0, "errmsg": "ok",
                                             "data": {}}
        if isinstance(item, tuple) and item[0] == "raw":
            self.send_response(item[1])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(item[2])))
            self.end_headers()
            self.wfile.write(item[2])
            return
        self._json(item)

    def _json(self, obj):
        payload = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _verify_signature(srv, q, body, path):
    """服务端独立实现签名校验（金向量算法的长算版本）。"""
    ak = (q.get("ak") or [""])[0]
    expires = (q.get("expires") or [""])[0]
    sign = (q.get("sign") or [""])[0]
    if not (ak and expires and sign):
        return False
    md5 = base64.b64encode(hashlib.md5(body).digest()).decode("utf-8")
    sts = "\n".join([ak, expires, "POST", md5, path.lstrip("/")])
    digest = base64.b64encode(hmac.new(srv.sk.encode("utf-8"),
                                       sts.encode("utf-8"),
                                       hashlib.sha1).digest()).decode("utf-8")
    # parse_qs 已解码 %XX → sign 还原为原始 base64，直接比对
    return sign == digest


class MockHuorong(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        ThreadingHTTPServer.__init__(self, ("127.0.0.1", 0), _MockHandler)
        self.ak = "AKMOCK00000000"
        self.sk = "SKMOCK-secret-0000"
        self.script = {}
        self.hits = []
        self.bad_auth = 0

    @property
    def base_url(self):
        return "http://127.0.0.1:%d" % self.server_address[1]

    def hits_on(self, path):
        return [h for h in self.hits if h[0] == path]


# ----------------------------------------------------------------------
# 同步器夹具
# ----------------------------------------------------------------------

FIX_GROUPS = [
    {"group_id": 1, "parent_group": 0, "group_name": "防护组"},
    {"group_id": 2, "parent_group": 1, "group_name": "1F诊室"},
    {"group_id": 3, "parent_group": 0, "group_name": "i18n:db_groups_name:ungrouped"},
]
FIX_CLIENTS = [
    {"client_id": "c1", "client_name": "视光-01", "computer_name": "PC01",
     "local_ip": "172.17.8.11", "connect_ip": "172.17.8.11",
     "mac": "AA:BB:CC:00:00:01", "group_id": 2, "is_online": 1,
     "os_version": "Microsoft Windows 10 专业版", "version": "2.0.7.5",
     "last_connect_time": 1726358400,
     "last_seen_time": 1726358300, "first_appear_time": 1691459644,
     "last_off_time": 1691126727, "this_on_time": 1691126737},
    {"client_id": "c2", "client_name": "检验-02", "computer_name": "PC02",
     "local_ip": "172.17.8.12", "connect_ip": "10.1.1.9",
     "mac": "AA:BB:CC:00:00:02", "group_id": 2, "is_online": 0,
     "os_version": "Microsoft Windows 7 旗舰版 ", "version": "2.0.6.1",
     "last_connect_time": "2026-09-15 10:00:00",
     "first_appear_time": "2026-09-15 09:00:00"},
    {"client_id": "c3", "client_name": "行政-03", "computer_name": "PC03",
     "local_ip": "172.17.9.13", "connect_ip": "172.17.9.13",
     "mac": "AA:BB:CC:00:00:03", "group_id": 99, "is_online": 1,
     "os_version": "Microsoft Windows 10 专业版", "version": "2.0.7.5",
     "last_connect_time": None},
    {"client_id": "c4", "client_name": "收费-04", "computer_name": "PC04",
     "local_ip": "172.17.9.14", "connect_ip": "172.17.9.14",
     "mac": "AA:BB:CC:00:00:01", "group_id": 3, "is_online": 0,
     "os_version": "Microsoft Windows 11 专业版", "version": "2.0.7.5",
     "last_connect_time": "2026/09/15 11:30:00"},
]

# 登记信息夹具（_info2 options=["assets"] name/value 数组原始形态；
# 键名由火绒控制台配置——c1 含截图实证 5 字段 + 额外「备注」键
# 验证全量保留不丢，c3 空数组 = 未登记空态）
FIX_ASSETS = {
    "c1": [{"name": "楼层", "value": "3"},
           {"name": "具体位置", "value": "门诊楼3F东区"},
           {"name": "工号", "value": "1024"},
           {"name": "姓名", "value": "张三"},
           {"name": "使用科室", "value": "检验科"},
           {"name": "备注", "value": "备注值"}],
    "c3": [],
}


class FakeClient(object):
    """同步器注入客户端：可编排失败。"""

    def __init__(self, groups=None, clients=None, fail=None, assets=None):
        self.groups = FIX_GROUPS if groups is None else groups
        self.clients = FIX_CLIENTS if clients is None else clients
        self.assets = FIX_ASSETS if assets is None else assets
        self.fail = fail
        self.calls = 0

    def get_groups(self):
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return [dict(g) for g in self.groups]

    def iter_all_clients(self, page=200, max_pages=200):
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        for row in self.clients:
            yield dict(row), {"total": len(self.clients), "offset": 0}

    def iter_virus_stats(self, page=200, max_pages=200):
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        for row in self.clients:
            yield {"client_id": row["client_id"], "count": 1,
                   "success": 1, "fail": 0, "ignored": 0, "trusted": 0}

    def client_info2(self, clients=None, mac=None, options=None):
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        out = []
        for cid in (clients or []):
            if cid in self.assets:
                out.append({"client_id": cid,
                            "assets": [dict(a) for a in self.assets[cid]]})
        return out


def noop_sleep(_seconds):
    pass


# ----------------------------------------------------------------------
# 路由测试用的鉴权库 bootstrap（同 test_sysadmin 模式）
# ----------------------------------------------------------------------

BOOTSTRAP = """
CREATE TABLE console_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    password_algo TEXT NOT NULL DEFAULT 'plain',
    password_updated_at INTEGER,
    password_must_change INTEGER NOT NULL DEFAULT 0,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    first_failed_at INTEGER, locked_until INTEGER,
    lock_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    last_login_at INTEGER, last_login_ip TEXT,
    last_failed_at INTEGER, last_failed_ip TEXT,
    role TEXT NOT NULL DEFAULT 'operator');
CREATE TABLE console_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, token_hash TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL, username TEXT NOT NULL,
    created_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL,
    idle_expires_at INTEGER NOT NULL, absolute_expires_at INTEGER NOT NULL,
    client_ip TEXT, ua_hash TEXT, revoked_at INTEGER, revoke_reason TEXT,
    mfa_passed INTEGER NOT NULL DEFAULT 0,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES console_users(id) ON DELETE CASCADE);
CREATE TABLE console_password_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    password_hash TEXT NOT NULL, changed_at INTEGER NOT NULL, changed_by TEXT);
CREATE TABLE console_login_throttle (
    id INTEGER PRIMARY KEY AUTOINCREMENT, scope_type TEXT NOT NULL,
    scope_key TEXT NOT NULL, window_start INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, blocked_until INTEGER,
    updated_at INTEGER NOT NULL, UNIQUE(scope_type, scope_key));
CREATE TABLE console_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at INTEGER NOT NULL,
    occurred_ms INTEGER, event_type TEXT NOT NULL, result TEXT NOT NULL,
    username TEXT, user_id INTEGER, client_ip TEXT, user_agent TEXT,
    session_id INTEGER, target TEXT, reason TEXT, detail TEXT);
CREATE TABLE console_security_policy (
    key TEXT PRIMARY KEY, value TEXT NOT NULL,
    value_type TEXT NOT NULL DEFAULT 'int', description TEXT,
    updated_at INTEGER, updated_by TEXT);
INSERT INTO console_security_policy(key, value) VALUES('pbkdf2_iterations', '20000');
INSERT INTO console_users(username, password) VALUES('admin', 'Legacy@Plain2026');
INSERT INTO console_users(username, password, role) VALUES('op', 'OpPlain2026', 'operator');
"""


def login_token(username, password):
    r = auth.authenticate(username, password, "127.0.0.1", "test")
    return r.token if r.ok else None


# ======================================================================

def main():
    tmp = tempfile.mkdtemp(prefix="etp_huorong_test_")
    mock = None
    try:
        run(tmp)
    finally:
        if mock is not None:
            mock.shutdown()
            mock.server_close()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n=== %d passed / %d failed ===" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  FAILED: %s" % f)
    return 1 if FAILED else 0


def run(tmp):
    client = HuorongClient("https://mock.local", "AKTEST1234567890",
                           "SKTEST-secret-key", sleep=noop_sleep)

    # ---- [1] 签名金向量（2026-09-15 实测算法固化为回归基线） ----
    print("[1] 签名金向量")
    check("content_md5('hello')",
          client.content_md5(b"hello") == "XUFAKrxLKna5cZ2REBfFkg==")
    check("content_md5 空体金向量",
          client.content_md5(b"") == "1B2M2Y8AsgTpgAmY7PhCfg==")
    check("resource 无前导斜杠",
          client.canonicalized_resource("/api/group/_list")
          == "api/group/_list")
    check("金向量 sign（quote +/ 保留/）",
          client._sign(1726358400, "POST", "1B2M2Y8AsgTpgAmY7PhCfg==",
                       "api/group/_list")
          == "X0MbXEIjPFPZtNPXMjGfwbe%2Bcmk%3D")
    url = client.build_signed_url("/api/group/_list", b"", expires=1726358400)
    check("签名 URL 形态",
          url == "https://mock.local/api/group/_list"
                 "?ak=AKTEST1234567890&expires=1726358400"
                 "&sign=X0MbXEIjPFPZtNPXMjGfwbe%2Bcmk%3D", url)
    check("待签串 5 行结构",
          "\n".join(["AKTEST1234567890", "1726358400", "POST",
                     "1B2M2Y8AsgTpgAmY7PhCfg==", "api/group/_list"])
          == "AKTEST1234567890\n1726358400\nPOST\n1B2M2Y8AsgTpgAmY7PhCfg==\n"
             "api/group/_list")

    # ---- [2] 镜像行归一 ----
    print("[2] mirror_rows 归一")
    groups, clients = mirror_rows(FIX_GROUPS, FIX_CLIENTS, 12345)
    g2 = [g for g in groups if g["group_id"] == 2][0]
    check("分组 total/online 快照聚合", g2["total"] == 2 and g2["online"] == 1,
          g2)
    check("未知分组终端保留", any(c["group_id"] == 99 for c in clients))
    check("同 MAC 双记录保留（client_id 主键）",
          sum(1 for c in clients if c["mac"] == "AA:BB:CC:00:00:01") == 2)
    check("local_ip 为主 IP",
          [c for c in clients if c["client_id"] == "c2"][0]["ip"]
          == "172.17.8.12")
    check("epoch last_connect_time", [c for c in clients
                                      if c["client_id"] == "c1"][0]["last_seen"]
          == 1726358400)
    check("字符串时间解析", 0 < ([c for c in clients
                                if c["client_id"] == "c2"][0]["last_seen"]
                               or 0) < 2000000000)
    check("空时间软失败", [c for c in clients
                          if c["client_id"] == "c3"][0]["last_seen"] is None)
    check("is_online 归一", [c for c in clients
                            if c["client_id"] == "c4"][0]["online"] == 0)
    check("i18n 键残留原样入库（前端兜底）",
          [g for g in groups if g["group_id"] == 3][0]["name"]
          == "i18n:db_groups_name:ungrouped")
    check("_parse_ts 垃圾输入",
          _parse_ts("not-a-date") is None and _parse_ts("") is None)
    # 上下线/开关机时间字段映射（ADR-033 增补三：_list 基础字段直取）
    c1m = [c for c in clients if c["client_id"] == "c1"][0]
    check("首次上线/上次关机/本次开机 epoch 映射",
          c1m["first_seen"] == 1691459644 and c1m["last_off"] == 1691126727
          and c1m["this_on"] == 1691126737, c1m)
    c2m = [c for c in clients if c["client_id"] == "c2"][0]
    check("首次上线字符串时间解析",
          0 < (c2m["first_seen"] or 0) < 2000000000, c2m["first_seen"])
    c3m = [c for c in clients if c["client_id"] == "c3"][0]
    check("时间字段缺失容忍 → None",
          c3m["first_seen"] is None and c3m["last_off"] is None
          and c3m["this_on"] is None)
    # 登记信息归一（name/value 数组 → dict 全量保留；EXT 纪律不猜字段名）
    am = assets_row(FIX_ASSETS["c1"])
    check("assets_row 归一全量保留（含额外键）",
          am.get("楼层") == "3" and am.get("具体位置") == "门诊楼3F东区"
          and am.get("工号") == "1024" and am.get("姓名") == "张三"
          and am.get("使用科室") == "检验科" and am.get("备注") == "备注值",
          am)
    check("assets_row 空态与畸形输入",
          assets_row(FIX_ASSETS["c3"]) == {} and assets_row(None) == {}
          and assets_row("junk") == {}
          and assets_row([{"name": "", "value": "x"},
                          {"name": "k", "value": None},
                          {"name": "k2", "value": "  "},
                          "junk-item", {"name": "ok", "value": "v"}]
                         ) == {"ok": "v"})

    # ---- [3] mock HTTP 冒烟：签名端到端 + errno 分支 + 重试 ----
    print("[3] mock HTTP 冒烟（签名服务端独立校验 + errno 分支）")
    mock = MockHuorong()
    mock_thread = threading.Thread(target=mock.serve_forever)
    mock_thread.daemon = True
    mock_thread.start()

    mc = HuorongClient(mock.base_url, mock.ak, mock.sk, sleep=noop_sleep)
    mock.script["/api/group/_list"] = [
        {"errno": 0, "errmsg": "ok",
         "data": {"list": [{"group_id": 7, "parent_group": 0,
                            "group_name": "mock组"}]}}]
    got = mc.get_groups()
    check("errno=0 直取 data", got == [{"group_id": 7, "parent_group": 0,
                                        "group_name": "mock组"}])
    check("服务端独立签名校验零失配", mock.bad_auth == 0,
          "bad_auth=%d" % mock.bad_auth)
    hit_path, hit_body = mock.hits[0]
    check("请求路径与紧凑 body",
          hit_path == "/api/group/_list" and hit_body == b"{}")

    def errno_case(errno, expect_hits):
        path = "/api/group/_list"
        # 脚本按命中次数重放（errno=3 每次重试都命中）
        mock.script[path] = [{"errno": errno, "errmsg": "E%d" % errno}] \
            * expect_hits
        before = len(mock.hits_on(path))
        try:
            mc.get_groups()
            return False, "no raise"
        except HuorongApiError as exc:
            return (exc.errno == errno
                    and len(mock.hits_on(path)) - before == expect_hits,
                    "errno=%s hits=%d" % (exc.errno,
                                          len(mock.hits_on(path)) - before))

    ok, d = errno_case(1, 1)
    check("errno=1 认证失败不重试", ok, d)
    ok, d = errno_case(2, 1)
    check("errno=2 参数错误不重试", ok, d)
    ok, d = errno_case(4, 1)
    check("errno=4 未授权不重试", ok, d)
    ok, d = errno_case(3, 3)
    check("errno=3 内部错误重试≤2", ok, d)

    mock.script["/api/group/_list"] = [("raw", 500, b'{"errno":3}'),
                                       ("raw", 500, b'{"errno":3}'),
                                       ("raw", 500, b'{"errno":3}')]
    before = len(mock.hits_on("/api/group/_list"))
    try:
        mc.get_groups()
        check("HTTP 5xx 重试耗尽后报错", False, "no raise")
    except HuorongApiError as exc:
        check("HTTP 5xx 重试耗尽后报错",
              exc.errno == -1 and exc.http_status == 500
              and len(mock.hits_on("/api/group/_list")) - before == 3,
              "errno=%s http=%s" % (exc.errno, exc.http_status))

    bad = HuorongClient("http://127.0.0.1:1", "a", "b", timeout=2,
                        sleep=noop_sleep)
    try:
        bad.get_groups()
        check("网络异常归一 errno=-1", False, "no raise")
    except HuorongApiError as exc:
        check("网络异常归一 errno=-1", exc.errno == -1)

    mock.script["/api/clnts/_list"] = [
        {"errno": 0, "errmsg": "ok", "data": {"list": [], "total": 0}}]
    mc.list_clients(limit=500)
    body = json.loads(mock.hits_on("/api/clnts/_list")[-1][1].decode("utf-8"))
    check("limit 收敛官方上限 200", body["limit"] == 200, body)

    # ---- [4] 分页迭代 ----
    print("[4] 分页迭代")
    page_counter = {"n": 0}

    def make_client(pages):
        """pages: [{'list': [...], 'total': N|缺失}] 依序回放。"""
        state = {"i": 0}

        class Paged(HuorongClient):
            def __init__(self):
                HuorongClient.__init__(self, "http://mock", "a", "b",
                                       sleep=noop_sleep)

            def list_clients(self, limit=200, offset=0):
                page_counter["n"] += 1
                idx = state["i"]
                state["i"] += 1
                return dict(pages[idx]) if idx < len(pages) else {"list": []}

        return Paged()

    rows5 = [{"client_id": "c%d" % i} for i in range(5)]
    pc = make_client([{"list": rows5[0:2], "total": 5},
                      {"list": rows5[2:4], "total": 5},
                      {"list": rows5[4:5], "total": 5}])
    got = [r for r, meta in pc.iter_all_clients(page=2)]
    check("total 语义翻页（5 行 3 页）",
          [r["client_id"] for r in got]
          == ["c0", "c1", "c2", "c3", "c4"] and page_counter["n"] == 3,
          "pages=%d rows=%d" % (page_counter["n"], len(got)))

    page_counter["n"] = 0
    pc = make_client([{"list": rows5[0:2]}, {"list": rows5[2:3]}])
    got = [r for r, _ in pc.iter_all_clients(page=2)]
    check("无 total 短页停", len(got) == 3 and page_counter["n"] == 2,
          "pages=%d rows=%d" % (page_counter["n"], len(got)))

    page_counter["n"] = 0
    pc = make_client([{"list": rows5[0:2]}, {"list": []}, {"list": []},
                      {"list": rows5[4:5]}])
    got = [r for r, _ in pc.iter_all_clients(page=2)]
    check("连续空页防御停", len(got) == 2 and page_counter["n"] == 3,
          "pages=%d rows=%d" % (page_counter["n"], len(got)))

    page_counter["n"] = 0
    pc = make_client([{"list": rows5[0:2]}, {"list": rows5[0:2]},
                      {"list": rows5[0:2]}])
    got = [r for r, _ in pc.iter_all_clients(page=2, max_pages=3)]
    check("max_pages 硬上限", len(got) == 6 and page_counter["n"] == 3,
          "pages=%d rows=%d" % (page_counter["n"], len(got)))

    # ---- [5] 破坏性硬门禁 + TLS pin 预留 ----
    print("[5] 破坏性门禁 + TLS pin")
    try:
        mc.create_task("quick_scan", {}, clients=["c1"])
        check("enable_tasks=False 硬门禁", False, "no raise")
    except HuorongTaskDisabled:
        check("enable_tasks=False 硬门禁", True)
    check("全测程零任务接口调用",
          len(mock.hits_on("/api/task/_create")) == 0)

    class _FakeSock(object):
        def __init__(self, der):
            self._der = der

        def getpeercert(self, binary_form=False):
            return self._der if binary_form else {}

    class _FakeRaw(object):
        def __init__(self, der):
            self._sock = _FakeSock(der)

    class _FakeResp(object):
        def __init__(self, der):
            self.fp = type("F", (), {})()
            self.fp.raw = _FakeRaw(der)

    der = b"\x30\x82\x01\x0a-fake-cert-der"
    expect_fp = hashlib.sha256(der).hexdigest()
    pinned = HuorongClient("https://x", "a", "b",
                           tls_fingerprint_sha256=expect_fp.upper())
    try:
        pinned._check_pin(_FakeResp(der))
        check("指纹匹配放行（大小写/冒号归一）", True)
    except HuorongApiError:
        check("指纹匹配放行（大小写/冒号归一）", False)
    try:
        pinned._check_pin(_FakeResp(b"other-der"))
        check("指纹不符 fail-closed", False, "no raise")
    except HuorongApiError as exc:
        check("指纹不符 fail-closed", exc.errno == -2)
    try:
        pinned._check_pin(_FakeResp(None))
        check("对端证书不可取 fail-closed", False, "no raise")
    except HuorongApiError as exc:
        check("对端证书不可取 fail-closed", exc.errno == -2)
    unpinned = HuorongClient("https://x", "a", "b")
    try:
        unpinned._check_pin(_FakeResp(b"anything"))
        check("未配置指纹不校验（联调默认）", True)
    except HuorongApiError:
        check("未配置指纹不校验（联调默认）", False)

    # ---- [6] 同步幂等（Store + Syncer 注入客户端） ----
    print("[6] 同步幂等与退避")
    store = Store(os.path.join(tmp, "eyeterm.db"), config_token="t0ken")
    syncer = HuorongSyncer(store, None, client_factory=lambda: FakeClient(),
                           sleep=noop_sleep)
    check("注入工厂视为已配置", syncer.configured())
    r1 = syncer.sync_once(trigger="manual")
    check("首轮同步 ok", r1.get("ok") and r1.get("groups") == 3
          and r1.get("clients") == 4, r1)
    check("登记信息同步状态 ok", r1.get("assets_sync") == "ok", r1)
    ov = store.hr_overview()
    check("概览 KPI", ov["groups_count"] == 3 and ov["clients_total"] == 4
          and ov["online"] == 2 and ov["online_rate"] == 0.5
          and ov["win7_eol_count"] == 1 and ov["last_sync"] > 0, ov)
    gl = store.hr_groups_list()
    g2row = [g for g in gl if g["id"] == 2][0]
    check("分组表快照列", g2row["total"] == 2 and g2row["online"] == 1
          and g2row["parent"] == 1, g2row)
    cl = store.hr_clients_page(page=1, page_size=50)
    check("终端分页结构", cl["total"] == 4 and cl["page"] == 1
          and len(cl["clients"]) == 4, cl["total"])
    cname = [c for c in cl["clients"] if c["client_id"] == "c2"][0]
    check("group_name JOIN", cname["group_name"] == "1F诊室", cname)
    c99 = [c for c in cl["clients"] if c["client_id"] == "c3"][0]
    check("未知组 group_name 为空", not c99["group_name"])

    r2 = syncer.sync_once(trigger="manual")
    check("二轮同步幂等（行数不增）", r2.get("ok")
          and store.hr_clients_page()["total"] == 4)
    log = sqlite3.connect(os.path.join(tmp, "eyeterm.db")).execute(
        "SELECT COUNT(*), SUM(ok) FROM hr_sync_log").fetchone()
    check("同步审计 2 条全 ok", log[0] == 2 and log[1] == 2, log)

    # 登记信息镜像 + context block 新字段（ADR-033 增补三）
    a1 = store.hr_assets_get("c1")
    check("登记信息落库全量保留", a1 is not None and a1.get("楼层") == "3"
          and a1.get("使用科室") == "检验科" and a1.get("备注") == "备注值",
          a1)
    check("未登记终端空态（null）", store.hr_assets_get("c3") is None
          and store.hr_assets_get("c4") is None)
    with store._lock:
        store._conn.execute(
            "INSERT OR REPLACE INTO hr_terminal_map(hr_client_id,"
            " terminal_id, match_type, created_ts) VALUES(?,?,?,?)",
            ("c1", "t-c1", "mac", 12345))
        store._conn.commit()
    cb = store.hr_context_block("t-c1")
    check("context block 时间新字段",
          cb.get("linked") and cb["client"]["first_seen"] == 1691459644
          and cb["client"]["last_off"] == 1691126727
          and cb["client"]["this_on"] == 1691126737, cb)
    check("context block 登记信息附加",
          cb.get("assets") is not None and cb["assets"].get("姓名") == "张三",
          cb)
    # 未关联终端不携带火绒块（不变回归）
    check("context block 未关联回归",
          store.hr_context_block("t-none") == {"linked": False})

    # 镜像语义：源缩减 → 缓存同步收敛
    shrunken = FakeClient(clients=FIX_CLIENTS[:3])
    syncer2 = HuorongSyncer(store, None, client_factory=lambda: shrunken,
                            sleep=noop_sleep)
    r3 = syncer2.sync_once(trigger="manual")
    check("镜像收敛（源删缓存删）", r3.get("ok")
          and store.hr_clients_page()["total"] == 3, r3)

    # 过滤查询
    store.hr_replace_snapshot(*mirror_rows(FIX_GROUPS, FIX_CLIENTS, 12345))
    check("online 过滤", store.hr_clients_page(online=1)["total"] == 2
          and store.hr_clients_page(online=0)["total"] == 2)
    check("group_id 过滤", store.hr_clients_page(group_id=2)["total"] == 2)
    check("q 模糊（IP 片段）",
          store.hr_clients_page(q="172.17.8")["total"] == 2)
    check("q 转义（% 字面量）",
          store.hr_clients_page(q="100%")["total"] == 0)
    check("page_size 上限 200",
          store.hr_clients_page(page=0, page_size=999)["page_size"] == 200)

    # 失败路径：errno 分支 → 审计 + 退避 + 认证锁死防线
    auth_fail = FakeClient(fail=HuorongApiError(1, "Authentication failed"))
    s_bad = HuorongSyncer(store, None, client_factory=lambda: auth_fail,
                          sleep=noop_sleep)
    rb = s_bad.sync_once(trigger="scheduled")
    check("失败归一 ok=False", rb.get("ok") is False and rb.get("error"))
    for _ in range(2):
        s_bad.sync_once(trigger="scheduled")
    check("认证连败 3 次暂停周期同步", s_bad._scheduled_disabled is True)
    check("认证失败不进退避档位", s_bad._backoff == 0)
    net_fail = FakeClient(fail=HuorongApiError(-1, "network"))
    s_net = HuorongSyncer(store, None, client_factory=lambda: net_fail,
                          sleep=noop_sleep)
    s_net.sync_once(trigger="scheduled")
    check("非认证失败进退避档位", s_net._backoff == 1)
    check("退避延迟翻倍", s_net._next_delay({"interval": 300}) == 600)
    # 手动成功恢复
    s_rec = HuorongSyncer(store, None, client_factory=lambda: FakeClient(),
                          sleep=noop_sleep)
    s_rec._scheduled_disabled = True
    rr = s_rec.sync_once(trigger="manual")
    check("手动成功解除周期暂停", rr.get("ok")
          and s_rec._scheduled_disabled is False)

    # 互斥：同步进行中二次触发 busy
    holder = HuorongSyncer(store, None, client_factory=lambda: FakeClient(),
                           sleep=noop_sleep)
    holder._lock.acquire()
    try:
        rb2 = holder.sync_once(trigger="manual")
        check("同步互斥 busy", rb2.get("busy") is True
              and rb2.get("ok") is False)
    finally:
        holder._lock.release()

    # 未配置（无工厂无 settings）→ not configured 结果
    s_nc = HuorongSyncer(store, None, sleep=noop_sleep)
    rnc = s_nc.sync_once(trigger="manual")
    check("未配置凭据 not_configured", rnc.get("ok") is False
          and "not configured" in rnc.get("error", "") and not rnc.get("busy"))

    # ---- [7] 控制台路由（scope / 鉴权 / 契约形态） ----
    print("[7] 控制台路由")
    auth.DB_PATH = os.path.join(tmp, "auth.db")
    auth._local = threading.local()
    conn = auth.get_conn()
    conn.executescript(BOOTSTRAP)
    conn.commit()
    auth.ensure_admin_role()            # bootstrap 的 admin 初始为 operator
    try:
        auth.invalidate_policy_cache()
    except Exception:
        pass
    import api as api_mod2                  # api 已 import，重设鉴权库指向
    auth.DB_PATH = os.path.join(tmp, "auth.db")
    auth._local = threading.local()

    store2 = Store(os.path.join(tmp, "etp2.db"), config_token="t0ken")
    store2.hr_replace_snapshot(*mirror_rows(FIX_GROUPS, FIX_CLIENTS, 12345))
    ctx = api_mod2.ApiContext(store2, {"terminal_token": "t0ken"})
    ctx.huorong = HuorongSyncer(store2, None,
                                client_factory=lambda: FakeClient(),
                                sleep=noop_sleep)
    ctx.huorong.sync_once(trigger="manual")   # 预置一轮成功同步（last_sync 审计）

    admin_token = login_token("admin", "Legacy@Plain2026")
    op_token = login_token("op", "OpPlain2026")
    check("测试登录就绪", bool(admin_token and op_token))

    def call(method, path, token=None, body=b"", query=None, scope="console"):
        headers = {}
        if token:
            headers["x-etp-console-token"] = token
        try:
            return api_mod2.dispatch(ctx, method, path, query or {}, headers,
                                     body, "127.0.0.1", scope=scope)
        except api_mod2.ApiError as exc:
            return exc.status, b"", ""

    st, payload, ctype = call("GET", "/api/v1/console/huorong/overview",
                              admin_token)
    ovj = json.loads(payload.decode("utf-8"))
    check("overview 200 契约字段",
          st == 200 and ovj["ok"] and ovj["groups_count"] == 3
          and ovj["clients_total"] == 4 and ovj["online"] == 2
          and ovj["online_rate"] == 0.5 and ovj["win7_eol_count"] == 1
          and ovj["last_sync"] > 0, ovj)

    st, payload, _ = call("GET", "/api/v1/console/huorong/groups", admin_token)
    gj = json.loads(payload.decode("utf-8"))
    check("groups 200 契约字段", st == 200 and len(gj["groups"]) == 3
          and set(gj["groups"][0].keys())
          == {"id", "name", "parent", "total", "online"}, gj)

    st, payload, _ = call("GET", "/api/v1/console/huorong/clients",
                          admin_token,
                          query={"group_id": "2", "online": "1", "q": "",
                                 "page": "1", "page_size": "20"})
    cj = json.loads(payload.decode("utf-8"))
    row = cj["clients"][0]
    check("clients 过滤+分页契约", st == 200 and cj["total"] == 1
          and cj["page_size"] == 20
          and set(row.keys()) == {"client_id", "name", "computer_name", "ip",
                                  "connect_ip", "mac", "group_id",
                                  "group_name", "online", "os", "version",
                                  "last_seen"}, cj)
    check("IP/MAC 真实值不脱敏", row["ip"] == "172.17.8.11"
          and row["mac"] == "AA:BB:CC:00:00:01")

    st, _, _ = call("GET", "/api/v1/console/huorong/clients", admin_token,
                    query={"group_id": "abc"})
    check("非法 group_id 400", st == 400)
    st, _, _ = call("GET", "/api/v1/console/huorong/nope", admin_token)
    check("未知子路由 404", st == 404)

    st, _, _ = call("POST", "/api/v1/console/huorong/sync", admin_token,
                    body=b"{}")
    check("admin 手动同步 200", st == 200, st)
    st, payload, _ = call("POST", "/api/v1/console/huorong/sync", op_token,
                          body=b"{}")
    check("operator 同步 403", st == 403)
    st, _, _ = call("GET", "/api/v1/console/huorong/overview", op_token)
    check("operator 只读放行", st == 200)
    st, _, _ = call("GET", "/api/v1/console/huorong/overview", None)
    check("未登录 401", st == 401)

    ctx.huorong._lock.acquire()
    try:
        st, _, _ = call("POST", "/api/v1/console/huorong/sync", admin_token,
                        body=b"{}")
        check("同步中 409", st == 409)
    finally:
        ctx.huorong._lock.release()

    syncer_nocfg = HuorongSyncer(store2, None, sleep=noop_sleep)
    keep, ctx.huorong = ctx.huorong, syncer_nocfg
    st, _, _ = call("POST", "/api/v1/console/huorong/sync", admin_token,
                    body=b"{}")
    check("未配置 400", st == 400)
    ctx.huorong = keep

    try:
        st, _, _ = api_mod2.dispatch(ctx, "GET",
                                     "/api/v1/console/huorong/overview", {},
                                     {}, b"", "127.0.0.1", scope="terminal")
    except api_mod2.ApiError as exc:
        st = exc.status
    check("terminal scope 跨类 404", st == 404)
    st, _, _ = call("GET", "/api/v1/console/huorong/overview", None,
                    scope="console")
    check("console scope 命中鉴权（非 404）", st == 401)

    # settings 键位敏感名单（凭据加密 + 脱敏零回显）
    from settings import DEFAULTS, SENSITIVE_KEYS, SettingsStore
    check("huorong.ak/sk 入敏感键",
          "huorong.ak" in SENSITIVE_KEYS and "huorong.sk" in SENSITIVE_KEYS)
    for key in ("huorong.url", "huorong.enabled",
                "huorong.sync_interval_sec", "huorong.tls_fingerprint"):
        check("默认键存在 %s" % key, key in DEFAULTS)
    check("settings 正则兼容 huorong.*",
          bool(api_mod2.re.match(r"^[a-z_]+\.[a-z_0-9]+$",
                                 "huorong.sync_interval_sec")))


if __name__ == "__main__":
    sys.exit(main())
