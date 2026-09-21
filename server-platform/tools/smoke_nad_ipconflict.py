#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""IP 冲突检测生产冒烟（ADR-028 改造：只读模式，测试数据禁触生产库）。

历史版本曾向生产库写入冒烟上报（同 IP 假 MAC 场景），污染
ipconflict_reports（AA-BB-CC-DD-EE-01 / 00-11-22-33-44-55 假证据进入
用户可见判定）——ADR-028 定型：一切冒烟/E2E 必须用隔离 config/db。

本脚本现在只做生产只读验证：
  R1. admin 登录 + 终端详情可达（真实终端在线）
  R2. ipconflict 路由负向：缺参 400（不写库）
  R3. 未知子路由 404（路由表未被误改）
  R4. 心跳 200
写路径全场景判定已由 tools/test_ipconflict.py 在隔离临时库覆盖。
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("ETP_API_BASE", "http://172.17.5.215:18090")
ADMIN_PWD = os.environ["ETP_ADMIN_PWD"]
TERM_TOKEN = os.environ["ETP_TERMINAL_TOKEN"]
REAL_TID = os.environ.get("ETP_REAL_TERMINAL", "WIN-Jun-office-PC")

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" | " + detail) if detail else ""))


def req(method, path, payload=None, headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        try:
            return exc.code, json.loads(body or "{}")
        except ValueError:
            return exc.code, {}


def main():
    ok = fail = 0

    def c1(name, cond, detail=""):
        nonlocal ok, fail
        ok, fail = ok + (1 if cond else 0), fail + (0 if cond else 1)
        check(name, cond, detail)

    th = {"X-ETP-Token": TERM_TOKEN}

    # R1. 登录 + 真实终端在线（只读）
    c, j = req("POST", "/api/v1/console/login",
               {"username": "admin", "password": ADMIN_PWD})
    h = {"X-ETP-Console-Token": j.get("token")} if c == 200 else {}
    c1("admin login", c == 200)
    c, j = req("GET", "/api/v1/console/terminals/%s" % REAL_TID, headers=h)
    c1("真实终端详情可达（只读）", c == 200 and (j.get("terminal") or {}).get("ip"))

    # R2. ipconflict 路由负向：缺参 400（服务端不写库）
    c, j = req("POST", "/api/v1/terminals/%s/netdoctor/ipconflict" % REAL_TID,
               {"mac": "00-00-00-00-00-00"}, headers=th)
    c1("ipconflict 缺 ip 400", c == 400)
    c, j = req("POST", "/api/v1/terminals/%s/netdoctor/ipconflict" % REAL_TID,
               {"ip": "10.255.255.254"}, headers=th)
    c1("ipconflict 缺 mac 400", c == 400)

    # R3. 未知子路由 404
    c, j = req("POST", "/api/v1/terminals/%s/netdoctor/not-a-route" % REAL_TID,
               {}, headers=th)
    c1("未知子路由 404", c == 404)

    # R4. 心跳
    c, j = req("POST", "/api/v1/terminals/%s/heartbeat" % REAL_TID, {},
               headers=th)
    c1("real terminal heartbeat 200", c == 200)

    print("\n=== smoke nad ipconflict (readonly): pass %d / fail %d ==="
          % (ok, fail))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
