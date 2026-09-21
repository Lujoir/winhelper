# -*- coding: utf-8 -*-
"""server-platform · 自动开关机快照存档 smoke（power-control P0）。

覆盖：PowerControlStore 存档/最新/历史/上限校验；api 层终端上报分支
（_terminal_api 直调，绕过传输层）与控制台查询组（_console_powercontrol）。
零外部依赖（stdlib + 本仓模块），临时库自清理。
运行：python tools/smoke_power_srv.py
"""
import json
import os
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_HERE, "server"))

import api as api_mod  # noqa: E402
import power_control as pc_mod  # noqa: E402
import store as store_mod  # noqa: E402

_checks = []


def check(name, ok, detail=""):
    _checks.append((name, bool(ok), detail))
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name,
                         ("  <- " + str(detail)[:200]) if detail and not ok
                         else ""))


class _Ctx(object):
    def __init__(self, store, pc):
        self.store = store
        self.pc = pc


def main():
    tmpdir = tempfile.mkdtemp(prefix="pc_smoke_")
    db_main = os.path.join(tmpdir, "main.db")
    db_pc = os.path.join(tmpdir, "pc.db")
    st = store_mod.Store(db_main, config_token="tok-smoke")
    st.register_terminal("WIN-PC-SMOKE", "windows", "PC-SMOKE", "stub os",
                         "0.0.0", "127.0.0.1")
    pc = pc_mod.PowerControlStore(db_pc)
    ctx = _Ctx(st, pc)

    snap = {
        "schema": 1, "collected_at": "2026-09-16 18:00:00",
        "collected_ts": int(time.time()) - 60,
        "machine": {"hostname": "PC-SMOKE", "manufacturer": "LENOVO",
                    "model": "10SWA03ECD",
                    "system_family": "ThinkCentre M720t-D234",
                    "vendor_line": "lenovo_enterprise",
                    "capability": "enterprise_configurable"},
        "bios": {"remote_configurable": True, "wmi_class_found": True,
                 "reason": "ok", "items": [], "rtc": {
                     "alarm": "Daily Event", "alarm_on": True,
                     "time": "08:00:00", "summary": "每天 08:00:00"}},
        "wake_timers": {"ok": True, "need_admin": False, "count": 1,
                        "items": [{"type": "SERVICE", "owner": "stub",
                                   "wake_time": "2026/9/16 18:48:18",
                                   "reason": "", "author": "",
                                   "description": ""}]},
        "shutdown_tasks": {"ok": True, "count": 0, "items": []},
        "fast_startup": {"registry_present": True, "hiberboot_enabled": 1,
                         "available": True, "enabled": True, "note": "ok"},
        "errors": [],
    }

    # ① 终端上报分支（_terminal_api 直调）
    body = json.dumps(snap, ensure_ascii=False).encode("utf-8")
    r = api_mod._terminal_api(ctx, "POST",
                              "/api/v1/terminals/WIN-PC-SMOKE/powercontrol"
                              "/snapshot", {}, {}, body, "127.0.0.1")
    status = r[0] if isinstance(r, tuple) else None
    check("终端上报返回 200", status == 200, r)
    check("上报回执 snapshot_id=1",
          isinstance(r, tuple) and b"snapshot_id" in (r[1] or b""), r)
    # 未注册终端 404
    try:
        api_mod._terminal_api(ctx, "POST",
                              "/api/v1/terminals/WIN-NOPE/powercontrol"
                              "/snapshot", {}, {}, {}, "127.0.0.1")
        check("未注册终端 404", False, "no exception")
    except api_mod.ApiError as e:
        check("未注册终端 404", e.status == 404, e.message)

    # ② 控制台查询：最新
    r = api_mod._console_powercontrol(
        ctx, "GET",
        ["api", "v1", "console", "powercontrol", "terminals",
         "WIN-PC-SMOKE", "snapshots"], {"latest": "1"}, None)
    check("控制台最新查询 200",
          isinstance(r, tuple) and r[0] == 200 and
          b"ThinkCentre" in (r[1] or b""), r)
    # 历史
    r = api_mod._console_powercontrol(
        ctx, "GET",
        ["api", "v1", "console", "powercontrol", "terminals",
         "WIN-PC-SMOKE", "snapshots"], {"limit": "10"}, None)
    check("控制台历史查询 200",
          isinstance(r, tuple) and r[0] == 200 and b"total" in (r[1] or b""),
          r)
    # 未知终端 404
    try:
        api_mod._console_powercontrol(
            ctx, "GET",
            ["api", "v1", "console", "powercontrol", "terminals",
             "WIN-NOPE", "snapshots"], {}, None)
        check("控制台未知终端 404", False, "no exception")
    except api_mod.ApiError as e:
        check("控制台未知终端 404", e.status == 404, e.message)

    # ③ store 语义：时间线 + 上限校验
    pc.save_snapshot("WIN-PC-SMOKE", dict(snap, collected_ts=int(time.time())))
    latest = pc.latest_snapshot("WIN-PC-SMOKE")
    check("最新快照存在", latest is not None)
    check("快照 JSON 回读结构",
          isinstance(latest.get("snapshot"), dict) and
          latest["snapshot"].get("machine", {}).get("model") == "10SWA03ECD")
    hist = pc.history_snapshots("WIN-PC-SMOKE", limit=1)
    check("历史 limit 生效", len(hist) == 1)
    try:
        pc.save_snapshot("WIN-PC-SMOKE", "not-a-dict")
        check("非 dict 快照拒绝", False)
    except pc_mod.PowerControlError:
        check("非 dict 快照拒绝", True)

    n_fail = sum(1 for _, ok, _ in _checks if not ok)
    print("\n== smoke 完成：%d 项，失败 %d ==" % (len(_checks), n_fail))
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
