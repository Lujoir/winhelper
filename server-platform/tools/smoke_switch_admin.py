#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""交换机管理生产冒烟（ADR-026，一次性脚本，凭据走环境变量）。"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("ETP_API_BASE", "http://172.17.5.215:18090")
ADMIN_PWD = os.environ["ETP_ADMIN_PWD"]
TERM_TOKEN = os.environ["ETP_TERMINAL_TOKEN"]
REAL_TID = os.environ.get("ETP_REAL_TERMINAL", "WIN-Jun-office-PC")
DEF_PWD = "3@Ww18_Bu9xn"

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
        with urllib.request.urlopen(r, timeout=30) as resp:
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

    c, j = req("POST", "/api/v1/console/login",
               {"username": "admin", "password": ADMIN_PWD})
    h = {"X-ETP-Console-Token": j.get("token")}
    c1("admin login", c == 200)

    # 1. 默认凭据（部署预置 reader）
    c, j = req("GET", "/api/v1/console/sysadmin/switch-default", headers=h)
    d = j.get("switch_default") or {}
    c1("默认凭据 username=reader 且 password_set=True",
       c == 200 and d.get("username") == "reader"
       and d.get("password_set") is True, str(j)[:150])
    c1("默认凭据响应无明文", DEF_PWD not in json.dumps(j))

    # 2. PUT 留空保持
    c, j = req("PUT", "/api/v1/console/sysadmin/switch-default",
               {"username": "reader", "password": ""}, headers=h)
    c1("默认凭据留空保持（changed 不含 password）",
       c == 200 and "password" not in (j.get("changed") or []), str(j)[:120])
    c, j = req("GET", "/api/v1/console/sysadmin/switch-default", headers=h)
    c1("留空后 password_set 仍 True",
       (j.get("switch_default") or {}).get("password_set") is True)

    # 3. 台账 CRUD 往返
    c, j = req("POST", "/api/v1/console/sysadmin/switches",
               {"name": "SMOKE-SW", "ip": "172.17.254.249", "ssh_port": 22,
                "username": "reader", "password": "SwSmoke#001",
                "brand": "H3C"}, headers=h)
    sid = j.get("id")
    c1("创建交换机 200", c == 200 and sid, str(j)[:120])
    c, j = req("GET", "/api/v1/console/sysadmin/switches", headers=h)
    row = [s for s in j.get("switches", []) if s.get("id") == sid]
    c1("列表含新条目且密码恒 ****",
       bool(row) and row[0].get("password") == "****"
       and "SwSmoke#001" not in json.dumps(j))
    c, j = req("PUT", "/api/v1/console/sysadmin/switches/%d" % sid,
               {"name": "SMOKE-SW-EDIT", "password": ""}, headers=h)
    c1("编辑（密码留空保持）200", c == 200)
    c, j = req("GET", "/api/v1/console/sysadmin/switches", headers=h)
    row = [s for s in j.get("switches", []) if s.get("id") == sid]
    c1("编辑后名称更新且密码仍 ****",
       row and row[0].get("name") == "SMOKE-SW-EDIT"
       and row[0].get("password") == "****")
    c, j = req("DELETE", "/api/v1/console/sysadmin/switches/%d" % sid,
               headers=h)
    c1("删除 200", c == 200)
    c, j = req("DELETE", "/api/v1/console/sysadmin/switches/%d" % sid,
               headers=h)
    c1("重复删除 404", c == 404)

    # 4. 密文校验（库中不含明文）
    c, j = req("GET", "/api/v1/console/sysadmin/switches", headers=h)
    c1("全列表无任何明文密码残留", "SwSmoke#001" not in json.dumps(j))

    # 5. 审计
    c, j = req("POST", "/api/v1/terminals/%s/heartbeat" % REAL_TID, {},
               headers={"X-ETP-Token": TERM_TOKEN})
    c1("real terminal heartbeat 200", c == 200)

    print("\n=== smoke switch admin: pass %d / fail %d ===" % (ok, fail))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
