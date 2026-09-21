#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""终端删除 mock 冒烟（标准库）：删除 API/未注册 404/级联/审计留痕。"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

API_BASE = os.environ.get("ETP_API_BASE", "http://127.0.0.1:18090").rstrip("/")
TOKEN = os.environ.get("ETP_TERMINAL_TOKEN", "dev-token")
PWD = os.environ.get("ETP_CONSOLE_PASSWORD", "dev-console")

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" | " + detail) if detail and not cond else ""))


def http(method, path, token=None, payload=None, terminal=None):
    h = {}
    if token:
        h["X-ETP-Console-Token"] = token
    if terminal:
        h["X-ETP-Token"] = terminal
    body = None
    if payload is not None:
        h["Content-Type"] = "application/json"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(API_BASE + path, data=body, headers=h,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except ValueError:
            return e.code, {}


def main():
    tid = "WIN-DEL-" + str(int(time.time()))
    print("=== terminal delete smoke ===")

    st, r = http("POST", "/api/v1/terminals/register", terminal=TOKEN,
                 payload={"terminal_id": tid, "terminal_type": "windows",
                          "hostname": "DEL-TEST",
                          "os_info": "Windows 10", "client_version": "t"})
    check("register", st == 200 and r.get("ok"))
    st, r = http("POST", "/api/v1/terminals/%s/metrics" % tid, terminal=TOKEN,
                 payload={"ts": int(time.time()), "cpu_percent": 11.1,
                          "mem_available_percent": 60.0})
    check("metrics uploaded", st == 200)

    st, r = http("POST", "/api/v1/console/login", payload={
        "username": "admin", "password": PWD})
    check("admin login", st == 200 and r.get("ok"))
    admin = r["token"]

    # 保留模式删除（purge=0）→ 注册移除、terminal 详情 404
    st, r = http("DELETE", "/api/v1/console/terminals/" + tid
                 + "?purge=0", token=admin)
    check("delete keep-history", st == 200 and r.get("ok")
          and r.get("purged") is False)
    st, r = http("GET", "/api/v1/console/terminals/" + tid, token=admin)
    check("terminal gone (404)", st == 404)
    st, r = http("POST", "/api/v1/terminals/%s/heartbeat" % tid,
                 terminal=TOKEN, payload={})
    check("heartbeat after delete fails", st in (401, 404))

    # 重新注册 → 级联删除（purge=1，此前 metrics 保留被清）
    st, r = http("POST", "/api/v1/terminals/register", terminal=TOKEN,
                 payload={"terminal_id": tid, "terminal_type": "windows",
                          "hostname": "DEL-TEST2",
                          "os_info": "Windows 10", "client_version": "t"})
    check("re-register", st == 200 and r.get("ok"))
    st, r = http("DELETE", "/api/v1/console/terminals/" + tid
                 + "?purge=1", token=admin)
    check("delete purge", st == 200 and r.get("ok")
          and r.get("purged") is True)
    st, r = http("GET", "/api/v1/console/terminals/" + tid, token=admin)
    check("terminal gone again", st == 404)

    # 未注册终端删除 → 404
    st, r = http("DELETE", "/api/v1/console/terminals/NO-SUCH-DEL",
                 token=admin)
    check("delete unknown 404", st == 404)

    # 审计留痕（console_audit_log 落库验证）
    import sqlite3
    db = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "data", "console_auth.db")
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT target FROM console_audit_log"
        " WHERE event_type='terminal.delete' ORDER BY id DESC LIMIT 5"
    ).fetchall()
    conn.close()
    check("audit trail recorded (db)",
          any(tid in (r[0] or "") for r in rows), str(rows))

    print("\n=== smoke summary ===")
    print("PASS: %d  FAIL: %d" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("failed: %s" % ", ".join(FAILED))
        sys.exit(1)
    print("ALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
