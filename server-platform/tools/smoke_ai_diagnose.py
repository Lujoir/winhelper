#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""终端 AI 智能诊断生产冒烟（ADR-023，一次性脚本，凭据走环境变量）。"""
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
        with urllib.request.urlopen(r, timeout=130) as resp:
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

    # 1. 终端诊断主链路（真实终端，最小日志包）
    c, j = req("POST", "/api/v1/terminals/%s/ai/diagnose" % REAL_TID,
               {"issue": "测试诊断：请确认接口链路",
                "logs": {"hwinfo": {"cpu": "Intel test-core",
                                    "mem_total_mb": 16384,
                                    "disks": [{"mount": "C:", "percent": 42}]}}},
               headers=th)
    c1("diagnose 200", c == 200 and j.get("ok") is True, str(j)[:200])
    c1("返回四元组", j.get("analysis_id") and j.get("response_text")
       and j.get("model") and j.get("duration_ms") is not None, str(j)[:200])
    aid = j.get("analysis_id")
    print("     model=%s duration=%sms" % (j.get("model"), j.get("duration_ms")))

    # 2. 控制台历史可见 + trigger 文案数据源
    c, j = req("POST", "/api/v1/console/login",
               {"username": "admin", "password": ADMIN_PWD})
    h = {"X-ETP-Console-Token": j.get("token")}
    c1("admin login", c == 200)
    c, j = req("GET", "/api/v1/console/ai/analyses?limit=10", headers=h)
    row = [a for a in j.get("analyses", []) if a.get("id") == aid]
    c1("AI 历史含本次记录 trigger=terminal_diagnose",
       bool(row) and row[0]["trigger"] == "terminal_diagnose",
       str(row)[:150])

    # 3. 单条详情含日志存证（控制台详情弹窗数据源）+ ADR-027 logs_stats
    c, j = req("GET", "/api/v1/console/ai/analyses/%d" % aid, headers=h)
    ctxj = (j.get("analysis") or {}).get("context") or {}
    c1("详情含 issue 与 hwinfo 日志存证",
       ctxj.get("issue") == "测试诊断：请确认接口链路"
       and "hwinfo" in (ctxj.get("logs") or {}), str(ctxj)[:150])
    hw_stat = (ctxj.get("logs_stats") or {}).get("hwinfo") or {}
    c1("详情含 logs_stats（原始字符/截断标记）",
       isinstance(hw_stat.get("original_chars"), int)
       and hw_stat.get("truncated") is False, str(hw_stat)[:150])

    # 4. 参数校验
    c, j = req("POST", "/api/v1/terminals/%s/ai/diagnose" % REAL_TID,
               {"issue": "", "logs": {}}, headers=th)
    c1("空 issue 400", c == 400)
    c, j = req("POST", "/api/v1/terminals/%s/ai/diagnose" % REAL_TID,
               {"issue": "x", "logs": "bad"}, headers=th)
    c1("logs 非对象 400", c == 400)

    # 5. 现有 /ai/analyze 回归（服务端聚合链路不受影响）
    c, j = req("POST", "/api/v1/ai/analyze",
               {"terminal_id": REAL_TID, "issue_description": "回归测试"},
               headers=th)
    c1("现有 /ai/analyze 回归 200", c == 200 and j.get("ok") is True,
       str(j)[:150])

    # 6. 真实终端心跳
    c, j = req("POST", "/api/v1/terminals/%s/heartbeat" % REAL_TID, {},
               headers=th)
    c1("real terminal heartbeat 200", c == 200)

    print("\n=== smoke ai diagnose: pass %d / fail %d ===" % (ok, fail))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
