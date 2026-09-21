#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""开关机任务化（ADR-046）本地单测（临时库，零凭据，零网络）。

覆盖：
- 纯函数：task_due_today 全 repeat 分支 + 星期默认与例外覆盖 / time_due_with_grace
  触发窗 / next_trigger_ts / task_shutdown_config 声明形状 / _cfg_equal 归一
  / validate_task_payload 校验矩阵
- 存储层：任务 CRUD 与名称防撞 / 组·终端目标展开（root 全量/直绑/注销剔除）
  / wol_expand diff（kept/added/removed/冲突）/ 存量迁移幂等 / 终端命中解析
  （直选/组命中/无命中，与执行引擎同源）/ 个性化计数 / 关机配置上报
  （版本去重/幂等）/ 回读比对（一致/漂移/未上报/陈旧）/ 节假日导入
- 路由层：tasks CRUD 400 矩阵 / shutdown 下发（离线排队）/ drift /
  终端口 boot-tasks GET·POST·PUT·DELETE（403 归属锁定）/ holidays 组

用法：python tools/test_power_tasks.py
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.normpath(os.path.join(HERE, "..", "server"))
sys.path.insert(0, SERVER)

import auth_upgrade as auth            # noqa: E402
import power_control as pcmod          # noqa: E402
from store import Store                # noqa: E402

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                          (" | " + detail) if detail and not cond else ""))


AUTH_BOOTSTRAP = """
CREATE TABLE console_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at INTEGER NOT NULL,
    occurred_ms INTEGER, event_type TEXT NOT NULL, result TEXT NOT NULL,
    username TEXT, user_id INTEGER, client_ip TEXT, user_agent TEXT,
    session_id INTEGER, target TEXT, reason TEXT, detail TEXT);
"""

NOW = int(time.time())
TODAY = time.strftime("%Y-%m-%d")


class FakeStore(object):
    """expand_platform_targets 所需最小终端/资产组视图（与主库同文件）。"""

    def __init__(self, db_path):
        self._conn = sqlite3.connect(db_path, check_same_thread=False,
                                     timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")

    def list_terminals(self):
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM terminals ORDER BY terminal_id").fetchall()]

    def get_terminal(self, tid):
        row = self._conn.execute(
            "SELECT * FROM terminals WHERE terminal_id=?",
            (str(tid),)).fetchone()
        return dict(row) if row else None

    def asset_group_list(self):
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM asset_groups").fetchall()]


def seed_terminals(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript("""
CREATE TABLE IF NOT EXISTS terminals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    terminal_id TEXT UNIQUE NOT NULL,
    terminal_type TEXT NOT NULL DEFAULT 'windows',
    hostname TEXT NOT NULL DEFAULT '',
    os_info TEXT NOT NULL DEFAULT '',
    client_version TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL DEFAULT '',
    group_id INTEGER,
    first_seen INTEGER NOT NULL DEFAULT 0,
    last_seen INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS asset_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, parent_id INTEGER,
    created_at INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'manual',
    huorong_group_id INTEGER,
    deleted INTEGER NOT NULL DEFAULT 0);
INSERT INTO terminals(terminal_id, hostname, ip, last_seen) VALUES
    ('WIN-A', 'pc-a', '172.17.90.10', 100),
    ('WIN-B', 'pc-b', '172.17.90.11', 0),
    ('WIN-C', 'pc-c', '172.17.91.20', 100);
INSERT INTO asset_groups(name, source) VALUES('全部资产', 'root');
INSERT INTO asset_groups(name, source) VALUES('办公区', 'manual');
""")
    conn.commit()
    conn.close()


def run(tmp):
    db_path = os.path.join(tmp, "eyeterm.db")
    seed_terminals(db_path)
    pc = pcmod.PowerControlStore(db_path)
    fs = FakeStore(db_path)

    # ---- [1] task_due_today 纯函数 ----
    print("\n[1] task_due_today（repeat 判定 + 日历回退）")
    check("daily 恒真", pcmod.task_due_today({"repeat": "daily"}, TODAY))
    weekly = {"repeat": "weekly", "weekdays": "1000000"}   # 仅周一
    import datetime as _dt
    d_mon = _dt.date(2026, 9, 14)     # 周一
    d_tue = _dt.date(2026, 9, 15)     # 周二
    check("weekly 命中周一",
          pcmod.task_due_today(weekly, d_mon.strftime("%Y-%m-%d")))
    check("weekly 非命中周二",
          not pcmod.task_due_today(weekly, d_tue.strftime("%Y-%m-%d")))
    once = {"repeat": "once", "once_date": "2026-09-20"}
    check("once 命中", pcmod.task_due_today(once, "2026-09-20"))
    check("once 未命中", not pcmod.task_due_today(once, "2026-09-21"))
    wd = {"repeat": "workday"}
    hm = {"2026-10-01": "holiday", "2026-09-26": "workday"}  # 国庆休/周六班
    check("workday 普通工作日",
          pcmod.task_due_today(wd, "2026-09-15", hm))     # 周二
    check("workday 周末默认休",
          not pcmod.task_due_today(wd, "2026-09-19", hm))  # 周六
    check("workday 调休上班", pcmod.task_due_today(wd, "2026-09-26", hm))
    check("workday 法定假日休",
          not pcmod.task_due_today(wd, "2026-10-01", hm))
    check("workday 默认星期语义（周六为否）",
          not pcmod.task_due_today(wd, "2026-09-19", {}))
    check("workday 默认星期语义（周三为是）",
          pcmod.task_due_today(wd, "2026-09-16", {}))
    hd = {"repeat": "holiday"}
    check("holiday 标记命中", pcmod.task_due_today(hd, "2026-10-01", hm))
    check("holiday 周末默认休", pcmod.task_due_today(hd, "2026-09-19", hm))
    check("holiday 调休上班不算休",
          not pcmod.task_due_today(hd, "2026-09-26", hm))
    check("holiday 工作日非命中",
          not pcmod.task_due_today(hd, "2026-09-15", hm))
    check("holiday 默认星期语义（周三为否）",
          not pcmod.task_due_today(hd, "2026-09-16", {}))

    # ---- [2] time_due_with_grace 触发窗 ----
    print("\n[2] time_due_with_grace（逾期补触）")
    check("精确匹配触发", pcmod.time_due_with_grace("07:30", "07:30"))
    check("逾期 10min 补触", pcmod.time_due_with_grace("07:30", "07:40"))
    check("逾期 30min 边界", pcmod.time_due_with_grace("07:30", "08:00"))
    check("逾期 31min 不触发",
          not pcmod.time_due_with_grace("07:30", "08:01"))
    check("未到时刻不触发", not pcmod.time_due_with_grace("07:30", "07:29"))
    check("跨日不触发", not pcmod.time_due_with_grace("23:59", "00:01"))
    check("非法格式拒绝", not pcmod.time_due_with_grace("7:30", "07:31"))

    # ---- [3] next_trigger_ts ----
    print("\n[3] next_trigger_ts")
    nts = pcmod.next_trigger_ts({"repeat": "daily", "time_hhmm": "23:00"},
                                TODAY)
    check("daily 当晚/次晨返回时间戳", isinstance(nts, int) and nts > 0)
    check("once 未来返回当日",
          pcmod.next_trigger_ts({"repeat": "once", "once_date": "2026-09-25",
                                 "time_hhmm": "08:00"}, TODAY)
          is not None)
    check("once 过期返回 None",
          pcmod.next_trigger_ts({"repeat": "once", "once_date": "2020-01-01",
                                 "time_hhmm": "08:00"}, TODAY) is None)
    nts_wd = pcmod.next_trigger_ts({"repeat": "workday",
                                    "time_hhmm": "08:00"},
                                   "2026-09-30", hm)
    check("workday 跳过法定假日（09-30 周三 → 次周工作日）",
          nts_wd is not None and
          time.strftime("%Y-%m-%d", time.localtime(nts_wd)) != "2026-10-01")

    # ---- [4] 校验矩阵 ----
    print("\n[4] validate_task_payload 校验矩阵")
    ok_boot = {"kind": "boot", "target_type": "terminals",
               "targets": ["WIN-A"], "repeat": "daily", "time": "08:30"}
    norm = pcmod.validate_task_payload(ok_boot)
    check("合法 boot 通过", norm["kind"] == "boot"
          and norm["time_hhmm"] == "08:30" and norm["origin"] == "platform")
    try:
        pcmod.validate_task_payload({"kind": "reboot"})
        check("非法 kind 400", False)
    except pcmod.PowerControlError:
        check("非法 kind 400", True)
    try:
        pcmod.validate_task_payload({"kind": "shutdown", "source": "huorong",
                                     "target_type": "terminals",
                                     "targets": ["WIN-A"], "time": "22:00"})
        check("关机第三方源 400", False)
    except pcmod.PowerControlError:
        check("关机第三方源 400", True)
    try:
        pcmod.validate_task_payload({"kind": "shutdown",
                                     "target_type": "terminals",
                                     "targets": ["WIN-A"],
                                     "repeat": "holiday", "time": "22:00"})
        check("关机节假日模式 400（本地执行无节假日）", False)
    except pcmod.PowerControlError:
        check("关机节假日模式 400（本地执行无节假日）", True)
    try:
        pcmod.validate_task_payload({"kind": "boot",
                                     "target_type": "terminals",
                                     "targets": ["WIN-A"],
                                     "repeat": "weekly", "weekdays": "0000000",
                                     "time": "08:30"})
        check("weekly 全 0 400", False)
    except pcmod.PowerControlError:
        check("weekly 全 0 400", True)
    try:
        pcmod.validate_task_payload({"kind": "boot",
                                     "target_type": "terminals",
                                     "targets": ["WIN-A"], "repeat": "once",
                                     "once_date": "2020-01-01",
                                     "time": "08:30"})
        check("once 过去日期 400", False)
    except pcmod.PowerControlError:
        check("once 过去日期 400", True)
    try:
        pcmod.validate_task_payload({"kind": "boot",
                                     "target_type": "terminals",
                                     "targets": [], "time": "08:30"})
        check("空目标 400", False)
    except pcmod.PowerControlError:
        check("空目标 400", True)
    try:
        pcmod.validate_task_payload({"kind": "boot", "target_type": "group",
                                     "group_id": 0, "time": "08:30"})
        check("缺资产组 400", False)
    except pcmod.PowerControlError:
        check("缺资产组 400", True)
    norm2 = pcmod.validate_task_payload({"kind": "boot", "origin":
                                         "client_personal"},
                                        existing=dict(ok_boot))
    check("existing 增量合并保留原字段",
          norm2["repeat"] == "daily" and norm2["origin"] == "client_personal")

    # ---- [5] 任务 CRUD / 名称防撞 ----
    print("\n[5] 任务 CRUD 与名称防撞")
    t1 = pc.task_create(dict(norm, name="晨间开机", operator="tester"))
    check("创建任务返回 id", t1["id"] > 0)
    auto_name = pc.task_gen_name("boot", "08:30")
    pc.task_create(dict(norm, name=auto_name))
    auto_name2 = pc.task_gen_name("boot", "08:30")
    check("空名自动生成防撞", auto_name2 != auto_name)
    pc.task_update(t1["id"], {"time_hhmm": "07:30"})
    check("更新生效", pc.task_get(t1["id"])["time_hhmm"] == "07:30")

    # ---- [6] 目标展开（root/直绑/注销剔除）----
    print("\n[6] expand_platform_targets")
    g_root = [g for g in fs.asset_group_list()
              if g["source"] == "root"][0]["id"]
    g_office = [g for g in fs.asset_group_list()
                if g["name"] == "办公区"][0]["id"]
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE terminals SET group_id=? WHERE terminal_id='WIN-A'",
                 (g_office,))
    conn.commit()
    conn.close()
    tids, note = pc.expand_platform_targets(
        fs, {"target_type": "group", "group_id": g_root})
    check("root 组展开全部终端", sorted(tids) == ["WIN-A", "WIN-B", "WIN-C"],
          "%s %s" % (tids, note))
    tids, _ = pc.expand_platform_targets(
        fs, {"target_type": "group", "group_id": g_office})
    check("普通组直绑展开", tids == ["WIN-A"], str(tids))
    tids, _ = pc.expand_platform_targets(
        fs, {"target_type": "terminals", "targets": ["WIN-A", "WIN-C",
                                                     "WIN-GONE"]})
    check("指定终端剔除已注销", tids == ["WIN-A", "WIN-C"], str(tids))

    # ---- [7] wol_expand diff ----
    print("\n[7] wol_expand_for_task（diff 保留运行态）")
    task_boot = pc.task_create({"kind": "boot", "name": "组开机",
                                "target_type": "group",
                                "group_id": g_office,
                                "repeat": "daily", "time_hhmm": "07:30",
                                "method": "auto", "operator": "tester"})
    exp = pc.wol_expand_for_task(pc.task_get(task_boot["id"]), fs)
    check("展开新增 1 行", exp["added"] == 1 and exp["kept"] == 0, str(exp))
    rows = [r for r in pc.wol_schedule_list()
            if r.get("task_id") == task_boot["id"]]
    check("调度行 task_id 回填", len(rows) == 1
          and rows[0]["terminal_id"] == "WIN-A")
    exp2 = pc.wol_expand_for_task(pc.task_get(task_boot["id"]), fs)
    check("重展开幂等（kept=1）", exp2["kept"] == 1 and exp2["added"] == 0,
          str(exp2))
    # 冲突：另一任务同刻同终端
    task_boot2 = pc.task_create({"kind": "boot", "name": "组开机2",
                                 "target_type": "group",
                                 "group_id": g_office,
                                 "repeat": "daily", "time_hhmm": "07:30",
                                 "method": "auto", "operator": "tester"})
    exp3 = pc.wol_expand_for_task(pc.task_get(task_boot2["id"]), fs)
    check("同终端同刻冲突如实上报", len(exp3["conflicts"]) == 1, str(exp3))
    # 时间变更 → 旧行删除新行新增
    pc.task_update(task_boot["id"], {"time_hhmm": "08:00"})
    exp4 = pc.wol_expand_for_task(pc.task_get(task_boot["id"]), fs)
    check("时间变更重建行", exp4["added"] == 1 and exp4["removed"] == 1,
          str(exp4))
    # 停用联动
    pc.task_set_enabled(task_boot["id"], False)
    row_after = [r for r in pc.wol_schedule_list()
                 if r.get("task_id") == task_boot["id"]][0]
    check("任务停用同步调度行停用", row_after["enabled"] == 0)

    # ---- [8] 存量迁移幂等（时序红线：无缝接续）----
    print("\n[8] migrate_legacy_schedules")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO wol_schedules(terminal_id, mac, name, time_hhmm,"
        " enabled, method, operator, created_ts, updated_ts)"
        " VALUES('WIN-C', '', 'M720t 晨间唤醒', '07:30', 1, 'auto',"
        " 'admin', 100, 100)")
    conn.commit()
    conn.close()
    mig = pc.migrate_legacy_schedules()
    check("迁移 1 行", mig["migrated"] == 1, str(mig))
    mig_task = [t for t in pc.task_list(kind="boot")
                if t["name"] == "M720t 晨间唤醒"]
    check("迁移任务形状（daily/单目标）", len(mig_task) == 1
          and mig_task[0]["repeat"] == "daily"
          and mig_task[0]["targets"] == ["WIN-C"], str(mig_task))
    row = [r for r in pc.wol_schedule_list()
           if r["terminal_id"] == "WIN-C" and r["time_hhmm"] == "07:30"][0]
    check("调度行只回填不重建（created_ts 保持）", row["task_id"] is not None
          and row["created_ts"] == 100)
    check("迁移后 daily 判定恒真（引擎无缝）",
          pcmod.task_due_today(pc.task_get(mig_task[0]["id"]), TODAY))
    mig2 = pc.migrate_legacy_schedules()
    check("二次迁移幂等", mig2["migrated"] == 0, str(mig2))

    # ---- [9] 终端命中解析（与执行引擎同源）----
    print("\n[9] boot_tasks_for_terminal（直选/组命中/无命中）")
    pc.task_delete(task_boot2["id"])
    pc.task_set_enabled(task_boot["id"], True)   # [7] 停用联动用例后复启用
    hits = pc.boot_tasks_for_terminal(fs, "WIN-A")
    check("组命中 WIN-A", any(h["id"] == task_boot["id"] for h in hits))
    hits_c = pc.boot_tasks_for_terminal(fs, "WIN-C")
    check("迁移任务直选命中 WIN-C",
          any(h["targets"] == ["WIN-C"] for h in hits_c))
    hits_b = pc.boot_tasks_for_terminal(fs, "WIN-B")
    check("无命中为空", not hits_b)
    check("下次触发按序", all(
        (hits[i]["next_ts"] or 1 << 60)
        <= (hits[i + 1]["next_ts"] or 1 << 60)
        for i in range(len(hits) - 1)))

    # ---- [10] 个性化任务 ----
    print("\n[10] 个性化开机任务")
    p1 = pc.task_create({"kind": "boot", "origin": "client_personal",
                         "name": "终端个性化开机", "target_type": "terminals",
                         "targets": ["WIN-A"], "repeat": "daily",
                         "time_hhmm": "06:50", "operator": "client:WIN-A"})
    check("个性化任务创建", p1["origin"] == "client_personal")
    check("个性化计数", pc.task_count_personal("WIN-A") == 1
          and pc.task_count_personal("WIN-B") == 0)

    # ---- [11] 关机配置上报与比对 ----
    print("\n[11] shutdown_config 上报 / drift 比对")
    cfg = {"enabled": True, "mode": "daily", "time": "22:00"}
    r1 = pc.shutdown_config_save("WIN-A", cfg, "v1")
    check("首次上报 updated", r1["updated"] is True)
    r2 = pc.shutdown_config_save("WIN-A", cfg, "v1",
                                 now=NOW + 10)
    check("同版本幂等（仅刷时间戳）", r2["updated"] is False
          and r2["duplicate"] is True)
    pc.shutdown_config_save("WIN-A", {"enabled": True, "mode": "daily",
                                      "time": "23:00"}, "v2")
    got = pc.shutdown_config_get("WIN-A")
    check("新版本覆盖", got["config"]["time"] == "23:00"
          and got["version"] == "v2")
    sd_task = pc.task_create({"kind": "shutdown", "name": "夜间关机",
                              "target_type": "group", "group_id": g_office,
                              "repeat": "daily", "time_hhmm": "23:00",
                              "operator": "tester"})
    drift = pc.shutdown_drift_for_task(pc.task_get(sd_task["id"]), fs,
                                       now=NOW + 20)
    target_a = [x for x in drift["targets"] if x["terminal_id"] == "WIN-A"][0]
    check("一致判定（归一容忍）", target_a["state"] == "consistent",
          str(target_a))
    pc.shutdown_config_save("WIN-B", {"enabled": True, "mode": "daily",
                                      "time": "20:00"}, "v1",
                            now=NOW - 100 * 86400)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE terminals SET group_id=? WHERE terminal_id='WIN-B'",
                 (g_office,))
    conn.commit()
    conn.close()
    drift2 = pc.shutdown_drift_for_task(pc.task_get(sd_task["id"]), fs,
                                        now=NOW + 20)
    tgt_b = [x for x in drift2["targets"]
             if x["terminal_id"] == "WIN-B"][0]
    check("漂移判定", tgt_b["state"] == "drift")
    check("陈旧标注", tgt_b["stale"] is True)
    tgt_c = [x for x in drift2["targets"] if x["terminal_id"] == "WIN-C"]
    check("WIN-C 不在办公组（展开仅 A/B）", not tgt_c, str(drift2))
    decl = pcmod.task_shutdown_config(
        {"repeat": "weekly", "weekdays": "1111100", "time_hhmm": "22:30",
         "enabled": 1})
    check("weekly 声明形状", decl["mode"] == "weekly"
          and decl["weekdays"] == [True, True, True, True, True, False,
                                   False], str(decl))
    check("归一比对：布尔与 0-1 兼容",
          pcmod._cfg_equal(decl, {"mode": "weekly", "time": "22:30",
                                  "enabled": 1,
                                  "weekdays": [1, 1, 1, 1, 1, 0, 0]}))
    check("归一比对：时间不同即漂移",
          not pcmod._cfg_equal(decl, {"mode": "weekly", "time": "23:30",
                                      "enabled": 1,
                                      "weekdays": [1, 1, 1, 1, 1, 0, 0]}))

    # ---- [12] 节假日日历 ----
    print("\n[12] holidays 导入与状态")
    res = pc.holiday_import([
        {"date": "2026-10-01", "type": "holiday", "name": "国庆"},
        {"date": "2026-10-02", "type": "holiday", "name": "国庆"},
        {"date": "2026-09-27", "type": "workday", "name": "调休"},
        {"date": "bad", "type": "holiday"},
        {"date": "2026-10-03", "type": "unknown"}])
    check("导入成功 3 无效 2", res["imported"] == 3 and res["invalid"] == 2,
          str(res))
    st = pc.holiday_status("2026-10-05")
    check("例外标记统计", st["holiday_count"] == 2
          and st["workday_count"] == 1, str(st))
    st_empty = pc.holiday_status("2027-01-01")
    check("空年零计数（默认星期语义不受影响）",
          st_empty["holiday_count"] == 0
          and st_empty["workday_count"] == 0)
    check("删除日历条目", pc.holiday_delete("2026-10-02") is True)
    check("重复删除 404 语义", pc.holiday_delete("2026-10-02") is False)

    # ---- [13] 路由层（console + terminal）----
    print("\n[13] 路由层")
    auth.DB_PATH = os.path.join(tmp, "auth.db")
    auth._local = threading.local()
    aconn = auth.get_conn()
    aconn.executescript(AUTH_BOOTSTRAP)
    aconn.commit()
    from store import Store as _Store
    store = _Store(db_path, config_token="cfg-token-0001")
    import api as api_mod
    ctx = api_mod.ApiContext(store, {"terminal_token": "cfg-token-0001"})
    ctx.pc = pc
    admin_sess = {"user_id": 1, "username": "admin", "role": "admin",
                  "password_must_change": 0}
    auth.resolve_session = lambda token, ip, ua: (
        admin_sess if token == "sess-admin" else None)
    auth.require_admin = lambda sess: bool(
        sess and sess.get("role") == "admin")
    headers = {"x-etp-console-token": "sess-admin"}

    def call(method, path, body=None, q=None, hdrs=None, scope="console"):
        h = dict(hdrs or headers)
        raw = json.dumps(body).encode("utf-8") if body is not None else b""
        try:
            st, payload, _ct = api_mod.dispatch(
                ctx, method, path, q or {}, h, raw, "127.0.0.1",
                scope=scope)
        except api_mod.ApiError as e:
            return e.status, {"error": str(e)}
        try:
            return st, json.loads(payload.decode("utf-8"))
        except ValueError:
            return st, {}

    st, j = call("GET", "/api/v1/console/powercontrol/tasks")
    check("任务列表 200", st == 200 and j.get("ok"))
    st, j = call("POST", "/api/v1/console/powercontrol/tasks",
                 {"kind": "shutdown", "target_type": "terminals",
                  "targets": ["WIN-A"], "repeat": "workday",
                  "time": "22:00"})
    check("关机 workday 400（路由层）", st == 400, str(st))
    st, j = call("POST", "/api/v1/console/powercontrol/tasks",
                 {"kind": "boot", "target_type": "terminals",
                  "targets": ["WIN-A"], "repeat": "daily", "time": "08:00",
                  "name": "路由开机"})
    check("创建任务 200 且 task_id 回填",
          st == 200 and j.get("task", {}).get("id"), str(j)[:200])
    tid_route = j["task"]["id"]
    st, j = call("POST", "/api/v1/console/powercontrol/tasks",
                 {"kind": "boot", "target_type": "terminals",
                  "targets": ["WIN-A"], "repeat": "daily", "time": "08:00",
                  "name": "路由开机"})
    check("同名任务 409", st == 409, str(st))
    st, j = call("POST", "/api/v1/console/powercontrol/tasks",
                 {"kind": "boot", "target_type": "terminals",
                  "targets": ["NOPE"], "repeat": "daily", "time": "08:00"})
    check("未知终端 404", st == 404, str(st))
    st, j = call("GET", "/api/v1/console/powercontrol/tasks/99999")
    check("详情 404", st == 404)
    st, j = call("PUT", "/api/v1/console/powercontrol/tasks/%s" % tid_route,
                 {"enabled": False})
    check("停用 200 且展开行同步", st == 200
          and j.get("task", {}).get("enabled") == 0, str(st))
    st, j = call("POST", "/api/v1/console/powercontrol/tasks",
                 {"kind": "shutdown", "target_type": "terminals",
                  "targets": ["WIN-A"], "repeat": "daily", "time": "22:30",
                  "name": "路由关机"})
    sd_route = j["task"]["id"]
    st, j = call("POST",
                 "/api/v1/console/powercontrol/tasks/%s/dispatch" % sd_route)
    check("关机下发 200（离线排队 1）", st == 200
          and j.get("total") == 1 and j.get("queued_offline") == 1,
          str(j))
    st, j = call("POST",
                 "/api/v1/console/powercontrol/tasks/%s/dispatch"
                 % tid_route)
    check("开机任务下发 400（平台调度无需下发）", st == 400, str(st))
    st, j = call("GET",
                 "/api/v1/console/powercontrol/tasks/%s/drift" % sd_route)
    check("drift 200 漂移判定（声明 22:30 vs 上报 23:00）", st == 200
          and j["drift"]["targets"][0]["state"] == "drift", str(j))
    st, j = call("GET", "/api/v1/console/powercontrol/daily-summary")
    check("daily-summary 200 且 WIN-A 开机命中",
          st == 200 and "WIN-A" in j.get("map", {})
          and j["map"]["WIN-A"].get("boot", {}).get("time"),
          str(j)[:200])
    st, j = call("GET", "/api/v1/console/powercontrol/term-search",
                 q={"q": "pc-a"})
    check("term-search 按主机名命中", st == 200
          and any(x["terminal_id"] == "WIN-A"
                  for x in j.get("terminals", [])), str(j)[:160])
    st, j = call("POST", "/api/v1/console/powercontrol/holidays",
                 {"date": "2026-10-05", "type": "holiday", "name": "假"})
    check("日历单条 200", st == 200)
    st, j = call("POST", "/api/v1/console/powercontrol/holidays",
                 {"date": "bad", "type": "holiday"})
    check("日历非法日期 400", st == 400)
    st, j = call("GET", "/api/v1/console/powercontrol/holiday-status")
    check("holiday-status 200", st == 200
          and "holiday_count" in j["status"]
          and "covered" not in j["status"])
    # operator 权限
    op_sess = {"user_id": 2, "username": "op", "role": "operator",
               "password_must_change": 0}
    auth.resolve_session = lambda token, ip, ua: (
        op_sess if token == "sess-op" else None)
    st, j = call("POST", "/api/v1/console/powercontrol/tasks",
                 {"kind": "boot", "target_type": "terminals",
                  "targets": ["WIN-A"], "time": "08:00"},
                 hdrs={"x-etp-console-token": "sess-op"})
    check("operator 建任务 403", st == 403, str(st))
    st, j = call("GET", "/api/v1/console/powercontrol/tasks",
                 hdrs={"x-etp-console-token": "sess-op"})
    check("operator 读任务 200", st == 200)

    # 终端口 boot-tasks（X-ETP-Token）
    thdrs = {"x-etp-token": "cfg-token-0001"}

    def tcall(method, path, body=None, hdrs=None):
        h = dict(hdrs or thdrs)
        raw = json.dumps(body).encode("utf-8") if body is not None else b""
        try:
            st, payload, _ct = api_mod.dispatch(
                ctx, method, path, {}, h, raw, "127.0.0.1",
                scope="terminal")
        except api_mod.ApiError as e:
            return e.status, {"error": str(e)}
        try:
            return st, json.loads(payload.decode("utf-8"))
        except ValueError:
            return st, {}

    st, j = tcall("GET",
                  "/api/v1/terminals/WIN-A/powercontrol/boot-tasks")
    check("终端命中任务 200（组+直选+个性化）", st == 200
          and len(j.get("tasks", [])) >= 3, str(j)[:200])
    check("按下次触发升序", j["tasks"] == sorted(
        j["tasks"], key=lambda x: (x.get("next_ts") is None,
                                   x.get("next_ts") or 0)))
    st, j = tcall("POST",
                  "/api/v1/terminals/WIN-A/powercontrol/boot-tasks",
                  {"repeat": "daily", "time": "06:40", "name": "终端自建"})
    check("终端个性化创建 200 origin 锁定", st == 200
          and j.get("task", {}).get("origin") == "client_personal", str(j))
    pid = j.get("task_id")
    st, j = tcall("PUT",
                  "/api/v1/terminals/WIN-A/powercontrol/boot-tasks/%s"
                  % pid, {"time": "06:45"})
    check("终端维护自身任务 200", st == 200
          and j.get("task", {}).get("time_hhmm") == "06:45", str(j))
    st, j = tcall("PUT",
                  "/api/v1/terminals/WIN-C/powercontrol/boot-tasks/%s"
                  % pid, {"time": "06:45"})
    check("他终端维护 403", st == 403, str(st))
    st, j = tcall("DELETE",
                  "/api/v1/terminals/WIN-A/powercontrol/boot-tasks/%s"
                  % pid)
    check("终端删除自身任务 200", st == 200)
    for _i in range(pcmod.PERSONAL_TASK_LIMIT):
        tcall("POST", "/api/v1/terminals/WIN-B/powercontrol/boot-tasks",
              {"repeat": "daily", "time": "06:4%d" % (_i % 10)})
    st, j = tcall("POST",
                  "/api/v1/terminals/WIN-B/powercontrol/boot-tasks",
                  {"repeat": "daily", "time": "07:10"})
    check("个性化上限 409", st == 409, str(st))
    st, j = tcall("POST",
                  "/api/v1/terminals/WIN-A/powercontrol/shutdown-config",
                  {"config": {"enabled": True, "mode": "daily",
                              "time": "21:00"}, "version": "r1"})
    check("关机配置上报 200", st == 200 and j.get("updated") is True)
    st, j = tcall("POST",
                  "/api/v1/terminals/WIN-A/powercontrol/shutdown-config",
                  {"config": {"enabled": True, "mode": "daily",
                              "time": "21:00"}, "version": "r1"})
    check("同版本上报幂等 duplicate", st == 200
          and j.get("duplicate") is True)
    st, j = tcall("GET", "/api/v1/terminals/WIN-A/powercontrol/boot-tasks",
                  hdrs={"x-etp-token": "wrong-token"})
    check("终端假 token 401", st == 401, str(st))

    pc.close()

    print("\n=== 结果：通过 %d / 失败 %d ===" % (len(PASSED), len(FAILED)))
    return 0 if not FAILED else 1


def main():
    tmp = tempfile.mkdtemp(prefix="etp_pt_")
    try:
        return run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
