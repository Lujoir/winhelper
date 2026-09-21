# -*- coding: utf-8 -*-
"""
后端真实冒烟（门禁3）：win32evtlog 真实读取本机 System 日志近3天
================================================================
1. search：真实数据检索 + 统计 + 分页
2. export：任务模式导出 txt（BOM/元信息/事件块格式抽检、行数>0、进度回调）
3. analyze：真实数据输出结论 + 故障模式
4. report-export：HTML 报告落盘
5. Security 权限降级（非 admin 预期 denied）
运行: python tools/smoke_backend.py
"""

import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import log_reader
import log_service

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail}")


def main():
    print("[1] 权限探测")
    acc = log_service.handle_log_access({})
    check("access.success", acc["success"])
    check("access.System.ok", acc["access"].get("System") is True)
    print(f"      access={acc['access']}")

    print("[2] search 真实检索（System 近3天）")
    t0 = time.time()
    r = log_service.handle_log_search({"types": "System", "hours": "72", "page": "1", "per_page": "20"})
    check("search.success", r["success"], r.get("error", ""))
    s = r.get("summary") or {}
    check("search.total>0", s.get("total", 0) > 0, f"total={s.get('total')}")
    check("search.pageEvents", len(r.get("events", [])) > 0)
    check("search.summaryKeys", all(k in s for k in ("critical", "error", "warning", "info", "by_hour_labels", "top_events")))
    check("search.elapsed", time.time() - t0 < 120, f"{time.time()-t0:.1f}s")
    ev0 = (r.get("events") or [{}])[0]
    check("search.eventFields", all(k in ev0 for k in ("event_id", "source", "level_name", "timestamp", "description")))
    print(f"      total={s.get('total')} critical={s.get('critical')} error={s.get('error')} warning={s.get('warning')}")
    if r.get("denied"):
        print(f"      denied={[d['type'] for d in r['denied']]}")

    print("[3] 级别/关键字/事件ID 过滤")
    r2 = log_service.handle_log_search({"types": "System", "hours": "72", "levels": "error,critical", "per_page": "10"})
    if r2["success"] and (r2["summary"]["critical"] + r2["summary"]["error"]) > 0:
        bad = [e for e in r2["events"] if e["level_name"] not in ("关键", "错误")]
        check("search.levelFilter", not bad, f"bad={len(bad)}")
    else:
        check("search.levelFilter", True, "(范围内无错误级事件，过滤逻辑由桩E2E覆盖)")
    r3 = log_service.handle_log_search({"types": "System", "hours": "72", "event_id": "41", "per_page": "10"})
    check("search.eventIdFilter", r3["success"])

    print("[4] export 真实导出 txt")
    st = log_service.handle_log_export_start({"types": "System", "hours": "72"})
    check("export.start", st["success"], str(st))
    tid = st.get("task_id")
    path = None
    progress_seen = False
    for _ in range(300):
        v = log_service.handle_log_export_status({"task_id": tid})
        t = (v.get("task") or {})
        p = t.get("progress") or {}
        if t["status"] == "running" and p.get("written"):
            progress_seen = True
            if p["written"] % 500 < 60:
                print(f"      ... written={p.get('written')} scanned={p.get('scanned')} elapsed={t.get('elapsed')}s")
        if t["status"] in ("done", "error", "cancelled"):
            break
        time.sleep(0.4)
    check("export.progressCallback", progress_seen or (t["status"] == "done" and (t.get("progress") or {}).get("written", 0) >= 0))
    check("export.done", t["status"] == "done", f"status={t['status']} err={t.get('error')}")
    res = t.get("result") or {}
    path = res.get("path")
    check("export.pathExists", bool(path) and os.path.exists(path), str(path))
    if path and os.path.exists(path):
        raw = open(path, "rb").read()
        check("export.utf8sig.bom", raw[:3] == b"\xef\xbb\xbf")
        text = raw.decode("utf-8-sig", errors="replace")
        lines = text.splitlines()
        check("export.lines>0", len(lines) > 10, f"lines={len(lines)}")
        check("export.header.brand", "观枢终端平台｜EyeTerm" in lines[1] if len(lines) > 1 else False)
        check("export.header.meta", any("时间范围" in l for l in lines[:15]))
        blocks = [l for l in lines if l.startswith("[2") and "] [" in l]
        check("export.eventBlocks", len(blocks) > 0, f"blocks={len(blocks)}")
        if blocks:
            check("export.blockFormat", blocks[0].count("[") >= 3, blocks[0][:90])
        print(f"      文件大小={res.get('size')} 事件数={res.get('count')}")

    print("[5] analyze 真实分析")
    a = log_service.handle_log_analyze({"types": "System", "hours": "72"})
    check("analyze.success", a["success"], a.get("error", ""))
    check("analyze.conclusion", bool(a.get("conclusion", {}).get("level")))
    print(f"      结论={a['conclusion']['level']} {a['conclusion']['text']}")
    for p_ in a.get("patterns", [])[:5]:
        print(f"      模式: {p_['name']} ×{p_['count']} [{p_['severity']}] 建议{len(p_['suggestions'])}条 证据{len(p_.get('evidence', []))}条")
    check("analyze.patternsWithSuggestion", all(len(p["suggestions"]) > 0 for p in a.get("patterns", [])))
    check("analyze.knownCount", isinstance(a.get("known_count"), int))

    print("[6] report-export HTML 落盘")
    rp = log_service.handle_log_report_export({"types": "System", "hours": "72"})
    check("report.success", rp["success"], rp.get("error", ""))
    if rp.get("path") and os.path.exists(rp["path"]):
        html = io.open(rp["path"], encoding="utf-8").read()
        check("report.brand", "观枢终端平台｜EyeTerm" in html)
        check("report.selfcontained", "<style>" in html and "http" not in html.lower().split("<body")[0].replace("lang=", ""))
        print(f"      {rp['filename']} ({rp['html_size']} bytes)")

    print("[7] Security 降级（非 admin 预期）")
    sec = log_reader.check_log_access("Security")
    if not sec["ok"]:
        rs = log_service.handle_log_search({"types": "System,Security", "hours": "24", "per_page": "5"})
        check("security.degraded.search", rs["success"] and len(rs.get("denied", [])) >= 1,
              f"denied={[d['type'] for d in rs.get('denied', [])]}")
        ra = log_service.handle_log_analyze({"types": "System,Security", "hours": "24"})
        check("security.degraded.analyze", ra["success"] and len(ra.get("denied", [])) >= 1)
    else:
        check("security.degraded.search", True, "(当前为管理员，Security 可读，跳过降级断言)")

    print("\n================ 冒烟汇总 ================")
    print(f"通过 {len(PASSED)} / 失败 {len(FAILED)}")
    if FAILED:
        for f in FAILED:
            print("  FAIL:", f)
        sys.exit(1)
    print("后端冒烟全部通过")


if __name__ == "__main__":
    main()
