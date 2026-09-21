# -*- coding: utf-8 -*-
"""ADR-027 联调复测核对（一次性脚本，凭据环境变量，用后删除）。

用法：set ETP_ANALYSIS_ID=<id> && python tools/_verify_retest.py
验收口径（team-lead 确认版）：
  1. logs_stats 各类 suspect_mojibake=false
  2. system_log kept_items>0 且事件时间覆盖今早重启时段
  3. response 无虚构引用（输出原文供人工复核，脚本辅助比对事件引用）
"""
import json
import os
import sys
import urllib.error
import urllib.request
import urllib.parse

BASE = os.environ.get("ETP_API_BASE", "http://172.17.5.215:18090")
ADMIN_PWD = os.environ["ETP_ADMIN_PWD"]
AID = int(os.environ.get("ETP_ANALYSIS_ID", "0"))
TODAY_PREFIX = os.environ.get("ETP_RETEST_DATE", "")  # 如 2026/9/10（可选）


def req(method, path, payload=None, headers=None, timeout=60):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode() or "{}")
        except ValueError:
            return exc.code, {}


def main():
    if not AID:
        print("missing env ETP_ANALYSIS_ID")
        return 1
    c, j = req("POST", "/api/v1/console/login",
               {"username": "admin", "password": ADMIN_PWD})
    if c != 200:
        print("login failed: %s" % c)
        return 1
    h = {"X-ETP-Console-Token": j["token"]}
    c, j = req("GET", "/api/v1/console/ai/analyses/%d" % AID, headers=h)
    if c != 200:
        print("analysis %d not found: %s" % (AID, c))
        return 1
    a = j.get("analysis") or {}
    ctx = a.get("context") or {}
    logs = ctx.get("logs") or {}
    stats = ctx.get("logs_stats") or {}
    print("=== analysis #%s | trigger=%s | model=%s | status=%s ==="
          % (AID, a.get("trigger"), a.get("model"), a.get("status")))
    print("issue: %s" % a.get("issue"))
    print("")
    verdicts = []

    # 验收1：suspect_mojibake 全 false
    for k in sorted(logs):
        st = stats.get(k) or {}
        suspects = st.get("suspect_mojibake")
        verdicts.append(("PASS" if suspects is False else "CHECK",
                         "mojibake", "%s suspect_mojibake=%s" % (k, suspects)))
        print("  [%s] %s: suspect_mojibake=%s truncated=%s items=%s/%s "
              "chars=%s/%s" % (
                  "PASS" if suspects is False else "CHECK", k,
                  suspects, st.get("truncated"),
                  st.get("kept_items"), st.get("original_items"),
                  st.get("evidence_chars"), st.get("original_chars")))
    print("")

    # 验收2：system_log 条数与时间覆盖（首末事件时间戳）
    sl_raw = logs.get("system_log") or ""
    sl_events = None
    try:
        parsed = json.loads(sl_raw) if isinstance(sl_raw, str) else sl_raw
        if isinstance(parsed, dict):
            sl_events = None
            for v in parsed.values():
                if isinstance(v, list):
                    sl_events = v
                    break
        elif isinstance(parsed, list):
            sl_events = parsed
    except ValueError:
        print("  [FAIL] system_log 存证不可 json.loads（存证损坏）")
        verdicts.append(("FAIL", "evidence-json", "system_log 不可解析"))
    if sl_events is not None:
        times = [e.get("timestamp") or e.get("time") or ""
                 for e in sl_events if isinstance(e, dict)]
        times = [t for t in times if t]
        kept = len(sl_events)
        print("  [PASS] system_log kept_items=%d" % kept)
        verdicts.append(("PASS" if kept else "CHECK", "kept_items",
                         "system_log kept=%d" % kept))
        if times:
            print("  事件时间范围: %s ~ %s（共 %d 条）"
                  % (times[0], times[-1], len(times)))
            if TODAY_PREFIX:
                today = [t for t in times if t.startswith(TODAY_PREFIX)]
                ok = bool(today)
                print("  [%s] 覆盖 %s 时段事件 %d 条"
                      % ("PASS" if ok else "CHECK", TODAY_PREFIX, len(today)))
                verdicts.append(("PASS" if ok else "CHECK", "time-coverage",
                                 "%s 时段事件 %d 条" % (TODAY_PREFIX, len(today))))
    print("")

    # 验收3：response 原文输出供人工复核虚构引用
    print("--- response_text（人工复核引用真实性）---")
    print(a.get("response_text") or "(空)")
    print("---")
    bad = [v for v in verdicts if v[0] == "FAIL"]
    print("\n=== 自动核对: %d 项通过 / %d 项待人工 / %d 失败 ==="
          % (len([v for v in verdicts if v[0] == "PASS"]),
             len([v for v in verdicts if v[0] == "CHECK"]), len(bad)))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
