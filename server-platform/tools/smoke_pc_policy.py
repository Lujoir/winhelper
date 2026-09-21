#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""desktop-policy 邻座 · 开关机管控 mock 冒烟（标准库）。

覆盖：注册冒烟终端 → 批量下发（boot+shutdown 全参）→ 400 校验矩阵 →
批次列表/详情（targets 状态机）→ 心跳拉命令（pc_apply_policy + policy_id）→
回执三态（success / rejected=not_supported / failed=steps 明细）→ 审计落库。

环境变量：ETP_API_BASE / ETP_TERMINAL_TOKEN / ETP_CONSOLE_PASSWORD
"""
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
    suffix = str(int(time.time()))
    t1 = "WIN-PCP-A-" + suffix
    t2 = "WIN-PCP-B-" + suffix

    print("=== power-control policy dispatch smoke ===")

    st, r = http("POST", "/api/v1/terminals/register", terminal=TOKEN,
                    payload={"terminal_id": t1, "terminal_type": "windows",
                             "hostname": "PCP-A",
                             "os_info": "Windows 11", "client_version": "t"})
    check("register A", st == 200 and r.get("ok"))
    st, r = http("POST", "/api/v1/terminals/register", terminal=TOKEN,
                    payload={"terminal_id": t2, "terminal_type": "windows",
                             "hostname": "PCP-B",
                             "os_info": "Windows 10", "client_version": "t"})
    check("register B", st == 200 and r.get("ok"))
    st, r = http("POST", "/api/v1/terminals/%s/heartbeat" % t1,
                    terminal=TOKEN, payload={})
    check("heartbeat A", st == 200)
    st, r = http("POST", "/api/v1/terminals/%s/heartbeat" % t2,
                    terminal=TOKEN, payload={})
    check("heartbeat B", st == 200)

    st, r = http("POST", "/api/v1/console/login", payload={
        "username": "admin", "password": PWD})
    check("admin login", st == 200 and r.get("ok"))
    admin = r["token"]

    # 1. 批量下发（boot daily + shutdown weekly 全参）
    boot = {"enabled": True, "mode": "daily", "time": "08:30"}
    shutdown = {"enabled": True, "mode": "weekly", "time": "18:30",
                "weekdays": [1, 1, 1, 1, 1, 0, 0]}
    st, r = http("POST",
                    "/api/v1/console/powercontrol/policies/dispatch",
                    token=admin,
                    payload={"terminal_ids": [t1, t2],
                             "boot": boot, "shutdown": shutdown})
    check("dispatch batch", st == 200 and r.get("ok")
          and r.get("total") == 2 and r.get("queued_offline") == 0,
          str(r))
    did = r.get("dispatch_id")
    pid = r.get("policy_id")

    # 2. 400 校验矩阵
    st, r = http("POST",
                    "/api/v1/console/powercontrol/policies/dispatch",
                    token=admin, payload={"terminal_ids": []})
    check("empty terminal_ids rejected", st == 400)
    st, r = http("POST",
                    "/api/v1/console/powercontrol/policies/dispatch",
                    token=admin, payload={"terminal_ids": [t1]})
    check("no boot/shutdown rejected", st == 400)
    st, r = http("POST",
                    "/api/v1/console/powercontrol/policies/dispatch",
                    token=admin,
                    payload={"terminal_ids": [t1],
                             "boot": {"enabled": True, "mode": "bad",
                                      "time": "08:30"}})
    check("bad mode rejected", st == 400)
    st, r = http("POST",
                    "/api/v1/console/powercontrol/policies/dispatch",
                    token=admin,
                    payload={"terminal_ids": [t1],
                             "boot": {"enabled": True, "mode": "daily",
                                      "time": "8:30"}})
    check("bad time rejected", st == 400)
    st, r = http("POST",
                    "/api/v1/console/powercontrol/policies/dispatch",
                    token=admin,
                    payload={"terminal_ids": [t1],
                             "boot": {"enabled": True, "mode": "weekly",
                                      "time": "08:30",
                                      "weekdays": [1, 1, 1]}})
    check("bad weekdays rejected", st == 400)
    st, r = http("POST",
                    "/api/v1/console/powercontrol/policies/dispatch",
                    token=admin,
                    payload={"terminal_ids": ["NO-SUCH-T"],
                             "boot": {"enabled": True, "mode": "daily",
                                      "time": "08:30"}})
    check("unknown terminal 404", st == 404)

    # 3. 批次列表 + 详情（targets pending）
    st, r = http("GET",
                    "/api/v1/console/powercontrol/policies/dispatches"
                    "?limit=10", token=admin)
    check("dispatch list", st == 200 and any(
        d["id"] == did for d in r.get("dispatches", [])))
    st, r = http("GET",
                    "/api/v1/console/powercontrol/policies/dispatches/%d"
                    % did, token=admin)
    d = r.get("dispatch") or {}
    tg = {t["terminal_id"]: t for t in d.get("targets", [])}
    check("dispatch detail targets pending",
          st == 200 and len(tg) == 2
          and tg[t1]["status"] == "pending"
          and tg[t2]["status"] == "pending", str(d)[:200])

    # 4. 心跳拉命令 → pc_apply_policy 下投
    st, r = http("POST", "/api/v1/terminals/%s/heartbeat" % t1,
                    terminal=TOKEN, payload={})
    cmds = r.get("commands") or []
    pc_cmds = [c for c in cmds if c.get("command") == "pc_apply_policy"]
    check("heartbeat carries pc_apply_policy", st == 200 and pc_cmds,
          str(r)[:200])
    check("command args policy_id matches",
          pc_cmds and pc_cmds[0]["args"].get("policy_id") == pid)
    cid1 = pc_cmds[0]["id"] if pc_cmds else None

    # 5. 回执三态
    st, r = http("POST",
                    "/api/v1/terminals/%s/commands/%d/result"
                    % (t1, cid1), terminal=TOKEN,
                    payload={"ok": True, "data": {
                        "policy_id": pid, "op": "apply", "ok": True,
                        "steps": {"bios": {"ok": True},
                                  "shutdown_task": {"ok": True,
                                                    "task_name": "ETP-S1"}},
                        "capability": "enterprise_configurable"}})
    check("receipt success accepted", st == 200)
    st, r = http("GET",
                    "/api/v1/console/powercontrol/policies/dispatches/%d"
                    % did, token=admin)
    tg = {t["terminal_id"]: t for t in
          (r.get("dispatch") or {}).get("targets", [])}
    check("target A success", tg[t1]["status"] == "success", str(tg[t1]))

    # 6. 第二批：rejected（not_supported）
    st, r = http("POST",
                    "/api/v1/console/powercontrol/policies/dispatch",
                    token=admin,
                    payload={"terminal_ids": [t2],
                             "boot": {"enabled": True, "mode": "daily",
                                      "time": "07:00"}})
    did2 = r.get("dispatch_id")
    pid2 = r.get("policy_id")
    st, r = http("POST", "/api/v1/terminals/%s/heartbeat" % t2,
                 terminal=TOKEN, payload={})
    cmds2 = [c for c in (r.get("commands") or [])
             if c.get("command") == "pc_apply_policy"
             and c.get("args", {}).get("policy_id") == pid2]
    cid2 = cmds2[0]["id"] if cmds2 else None
    check("B command delivered", bool(cid2))
    st, r = http("POST",
                    "/api/v1/terminals/%s/commands/%d/result"
                    % (t2, cid2), terminal=TOKEN,
                    payload={"ok": False, "data": {
                        "ok": False, "capability": "not_supported",
                        "error": "消费线不支持"}})
    check("receipt rejected accepted", st == 200)
    st, r = http("GET",
                    "/api/v1/console/powercontrol/policies/dispatches/%d"
                    % did2, token=admin)
    tg2 = {t["terminal_id"]: t for t in
           (r.get("dispatch") or {}).get("targets", [])}
    check("target B rejected", tg2[t2]["status"] == "rejected",
          str(tg2[t2]))

    # 7. 第三批：failed（steps 明细落 error_detail）
    st, r = http("POST",
                    "/api/v1/console/powercontrol/policies/dispatch",
                    token=admin,
                    payload={"terminal_ids": [t1],
                             "shutdown": {"enabled": True, "mode": "daily",
                                          "time": "20:00"}})
    did3 = r.get("dispatch_id")
    pid3 = r.get("policy_id")
    st, r = http("POST", "/api/v1/terminals/%s/heartbeat" % t1,
                 terminal=TOKEN, payload={})
    cmds3 = [c for c in (r.get("commands") or [])
             if c.get("command") == "pc_apply_policy"
             and c.get("args", {}).get("policy_id") == pid3]
    cid3 = cmds3[0]["id"] if cmds3 else None
    st, r = http("POST",
                    "/api/v1/terminals/%s/commands/%d/result"
                    % (t1, cid3), terminal=TOKEN,
                    payload={"ok": False, "data": {
                        "ok": False,
                        "steps": {"bios": {"ok": False,
                                           "error": "BIOS 写入被拒绝"},
                                  "shutdown_task": {"ok": True}}}})
    check("receipt failed accepted", st == 200)
    st, r = http("GET",
                    "/api/v1/console/powercontrol/policies/dispatches/%d"
                    % did3, token=admin)
    tg3 = {t["terminal_id"]: t for t in
           (r.get("dispatch") or {}).get("targets", [])}
    check("target C failed with steps detail",
          tg3[t1]["status"] == "failed"
          and "BIOS 写入被拒绝" in (tg3[t1].get("error_detail") or ""),
          str(tg3[t1]))

    print("\n=== smoke summary ===")
    print("PASS: %d  FAIL: %d" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("failed: %s" % ", ".join(FAILED))
        sys.exit(1)
    print("ALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
