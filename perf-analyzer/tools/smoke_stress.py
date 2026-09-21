# -*- coding: utf-8 -*-
"""
压测模块冒烟（真实短时单项，时长缩短但不改服务端硬上限）
- disk 4s / cpu 4s / mem 5s（验证安全底线与释放）/ gpu 检测 3s（无独显即 skipped）
- hwinfo 全链路（PowerShell CIM + 降级）
用法：python tools/smoke_stress.py
"""

import io
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import perf_service as P  # noqa: E402


def _mk_task(mode, key, secs):
    return {
        "id": "smoke_%s" % mode, "mode": mode, "plan": [(key, secs)],
        "total_seconds": secs, "status": "running", "started_at": time.time(),
        "finished_at": None, "current_stage": None, "stage_detail": None,
        "_stage_started": None, "result": {"stages": {}}, "error": None,
        "need_webgl": False, "_cancel": threading.Event(),
        "_status_gaps": 0, "_last_status_ts": time.time(),
        "params": {"disk": {"drive": ""}},
    }


def _run(name, mode, key, secs):
    print("---- %s (%ss) ----" % (name, secs))
    task = _mk_task(mode, key, secs)
    P._stress_worker(task)
    st = task["result"]["stages"].get(key) or {}
    print("status:", task["status"], "| stage status:", st.get("status"))
    print(json.dumps(st, ensure_ascii=False, indent=1, default=str)[:1500])
    assert task["status"] == "done", "worker 状态异常"
    assert st.get("status") in ("done", "skipped"), "阶段状态异常: %s" % st
    if key == "disk":
        assert st.get("tmp_cleaned") is True, "磁盘临时文件未清理"
        assert "conclusion" in st, "磁盘缺结论"
    if key == "cpu":
        assert "conclusion" in st and st.get("workers", 0) >= 1
    if key == "mem":
        assert "conclusion" in st
        after = st.get("percent_after_release")
        before = st.get("before_percent")
        assert after is not None and after <= before + 5, "内存释放后未回落（%s -> %s）" % (before, after)
    print("[PASS] %s" % name)
    return st


def main():
    print("== hwinfo ==")
    r = P.handle_perf_hwinfo({})
    assert r.get("success"), r
    h = r["hwinfo"]
    print(json.dumps(h, ensure_ascii=False, indent=1, default=str)[:2000])
    assert h.get("source") in ("cim", "fallback")
    assert h["cpu"].get("name"), "CPU 名缺失"

    _run("磁盘读写上限", "disk", "disk", 4)
    _run("CPU 稳定性", "cpu", "cpu", 4)
    _run("内存稳定性（安全底线验证）", "mem", "mem", 5)
    _run("GPU 稳定性", "gpu", "gpu", 3)

    print("== cancel 验证 ==")
    task = _mk_task("disk", "disk", 25)
    th = threading.Thread(target=P._stress_worker, args=(task,), daemon=True)
    th.start()
    time.sleep(3)
    task["_cancel"].set()
    th.join(timeout=10)
    assert not th.is_alive(), "cancel 后 worker 未退出"
    st = task["result"]["stages"].get("disk") or {}
    assert st.get("tmp_cleaned") is True, "cancel 后临时文件未清理"
    print("[PASS] cancel 立即停负载并清理")

    print("== record-latest ==")
    # stress 分支：内存无记录时磁盘回退（worker 收尾已落盘 stress_*.json）
    rl = P.handle_perf_record_latest({"kind": "stress"})
    assert rl.get("success") is True and rl.get("found") is True, "stress 最新记录应可取: %r" % rl
    assert rl.get("kind") == "stress" and rl.get("record_id"), rl
    rep = rl.get("report") or {}
    assert rep.get("stress_id") == rl.get("record_id"), "载荷 stress_id 与顶层不一致"
    assert (rep.get("result") or {}).get("overall"), "压测载荷缺 overall"
    print("[PASS] record-latest kind=stress（磁盘回退）-> %s (%s)" % (rl["record_id"], rep.get("status")))
    # 内存优先：构造已完成任务塞入内存表，应直接命中
    memtask = _mk_task("cpu", "cpu", 1)
    memtask["status"] = "done"
    memtask["finished_at"] = time.time()
    memtask["result"] = {"stages": {}, "overall": {"level": "ok", "text": "内存桩", "detail": []}}
    with P._stress_lock:
        P._stress_tasks[memtask["id"]] = memtask
    rl2 = P.handle_perf_record_latest({"kind": "stress"})
    assert rl2.get("found") is True and rl2.get("record_id") == memtask["id"], "内存优先未命中"
    with P._stress_lock:
        P._stress_tasks.pop(memtask["id"], None)
    print("[PASS] record-latest kind=stress（内存优先）")
    # analysis 分支：短记录后取最新，record_id 应匹配
    rs = P.handle_perf_record_start({"interval": "1"})
    time.sleep(2.2)
    stop = P.handle_perf_record_stop({"record_id": rs["record_id"]})
    assert stop.get("success"), stop
    la = P.handle_perf_record_latest({"kind": "analysis"})
    assert la.get("success") and la.get("found") and la.get("kind") == "analysis", la
    assert la.get("record_id") == rs["record_id"], la
    assert (la.get("report") or {}).get("assessment"), "分析报告缺 assessment"
    print("[PASS] record-latest kind=analysis -> %s" % la["record_id"])
    # 非法 kind 明确报错
    bad = P.handle_perf_record_latest({"kind": "nope"})
    assert bad.get("success") is False and "kind" in (bad.get("error") or ""), bad
    print("[PASS] record-latest 非法 kind 拒绝")

    print("SMOKE_ALL_PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
