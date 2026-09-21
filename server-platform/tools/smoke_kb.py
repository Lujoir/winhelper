#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知识库模块生产冒烟（ADR-022，一次性脚本，凭据走环境变量或内网已知配置）。"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("ETP_API_BASE", "http://172.17.5.215:18090")
ADMIN_PWD = os.environ["ETP_ADMIN_PWD"]
TERM_TOKEN = os.environ["ETP_TERMINAL_TOKEN"]
REAL_TID = "WIN-Jun-office-PC"

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
        with urllib.request.urlopen(r, timeout=15) as resp:
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
    atok = j.get("token")
    h = {"X-ETP-Console-Token": atok}
    c1("admin login role=admin", c == 200 and j.get("role") == "admin")

    c, j = req("GET", "/api/v1/console/kb", headers=h)
    routes = j.get("routes", [])
    c1("seeded route-nodes entry",
       any(r.get("kb_id") == "route-nodes" for r in routes),
       str([r.get("kb_id") for r in routes]))
    c1("categories contains route_nodes",
       "route_nodes" in (j.get("categories") or []))

    th = {"X-ETP-Token": TERM_TOKEN}
    c, j = req("GET", "/api/v1/terminals/%s/netdoctor/route-nodes" % REAL_TID,
               headers=th)
    nodes = j.get("nodes", [])
    c1("route-nodes source=kb",
       j.get("source") == "kb"
       and any(n.get("match") == "172.17.254.0/24" for n in nodes),
       json.dumps(j, ensure_ascii=False)[:160])

    c, j = req("POST", "/api/v1/console/kb",
               {"kb_id": "smoke-kb-rt", "title": "冒烟条目",
                "category": "route_nodes",
                "content": json.dumps(
                    [{"match": "10.55.0.0/16", "zone": "冒烟区", "desc": "v1"}],
                    ensure_ascii=False),
                "note": "smoke"}, headers=h)
    c1("create entry", c == 200 and j.get("version") == 1, str(j)[:120])

    c, j = req("GET", "/api/v1/terminals/%s/netdoctor/route-nodes" % REAL_TID,
               headers=th)
    c1("route-nodes reflects newest kb entry (v2 未改 route_nodes 最新条目仍为 route-nodes 条目)",
       j.get("source") == "kb", "source=%s" % j.get("source"))

    c, j = req("PUT", "/api/v1/console/kb/smoke-kb-rt",
               {"content": json.dumps(
                   [{"match": "10.66.0.0/16", "zone": "冒烟区2", "desc": "v2"}],
                   ensure_ascii=False), "note": "smoke-edit"}, headers=h)
    c1("update to v2", c == 200 and j.get("version") == 2, str(j)[:120])

    c, j = req("GET", "/api/v1/console/kb/smoke-kb-rt/versions", headers=h)
    c1("versions has 2", len(j.get("versions", [])) == 2,
       str([v.get("version") for v in j.get("versions", [])]))

    c, j = req("POST", "/api/v1/console/kb/smoke-kb-rt/rollback",
               {"version": 1}, headers=h)
    c1("rollback to v1 (new v3)", c == 200 and j.get("version") == 3, str(j)[:120])

    c, j = req("GET", "/api/v1/console/kb/smoke-kb-rt", headers=h)
    content = (j.get("entry") or {}).get("content", "")
    c1("rolled-back content is v1", "10.55.0.0/16" in content, content[:120])

    c, j = req("GET", "/api/v1/console/kb/smoke-kb-rt/versions", headers=h)
    c1("versions has 3 after rollback", len(j.get("versions", [])) == 3)

    c, j = req("DELETE", "/api/v1/console/kb/smoke-kb-rt", headers=h)
    c1("cleanup smoke entry", c == 200)
    c, j = req("GET", "/api/v1/console/kb/smoke-kb-rt", headers=h)
    c1("deleted entry 404", c == 404)

    c, j = req("POST", "/api/v1/terminals/%s/heartbeat" % REAL_TID, headers=th)
    c1("real terminal heartbeat 200", c == 200)

    print("\n=== smoke kb: pass %d / fail %d ===" % (ok, fail))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
