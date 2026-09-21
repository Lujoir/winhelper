# -*- coding: utf-8 -*-
"""观枢终端平台服务端 · 终端「自动开关机」：电源策略快照存档与查询（P0）。

对应终端侧引擎：主仓 power-control/power_control.py（只读快照上报）。
实现裁定（ADR-001~003，power-control/docs/DECISIONS.md）：
- 终端上报：POST /api/v1/terminals/{tid}/powercontrol/snapshot
  （X-ETP-Token + 白名单准入），body 为快照 JSON（schema=1）；
- 存档为时间线（每次上报一行），控制台按 terminal_id 查最新/历史；
- 服务端不做策略判定，仅校验最小形状（dict + collected_ts）后原样存档，
  解析展示由消费方负责（P0 快照生产在终端侧）。
"""
import datetime
import json
import re
import sqlite3
import threading
import time

_MAX_SNAPSHOT_BYTES = 256 * 1024   # 快照体积上限（防御异常载荷）
_MAX_CONTENT_TEXT = 256            # 列表摘要字段截断

_SCHEMA = """
CREATE TABLE IF NOT EXISTS power_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  terminal_id TEXT NOT NULL,
  collected_ts INTEGER NOT NULL DEFAULT 0,
  schema_ver INTEGER NOT NULL DEFAULT 1,
  capability TEXT NOT NULL DEFAULT '',
  vendor_line TEXT NOT NULL DEFAULT '',
  snapshot TEXT NOT NULL DEFAULT '{}',
  created_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_power_snapshots_term
  ON power_snapshots(terminal_id, collected_ts DESC, id DESC);

CREATE TABLE IF NOT EXISTS pc_policy_dispatch (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  policy_id TEXT NOT NULL UNIQUE,
  operator TEXT NOT NULL DEFAULT '',
  payload TEXT NOT NULL DEFAULT '{}',
  created_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pc_policy_targets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dispatch_id INTEGER NOT NULL,
  terminal_id TEXT NOT NULL,
  command_id INTEGER,
  status TEXT NOT NULL DEFAULT 'pending',
  error_detail TEXT,
  result_json TEXT,
  updated_ts INTEGER NOT NULL,
  created_ts INTEGER NOT NULL,
  UNIQUE(dispatch_id, terminal_id)
);
CREATE INDEX IF NOT EXISTS idx_pcpt_dispatch
  ON pc_policy_targets(dispatch_id);
CREATE INDEX IF NOT EXISTS idx_pcpt_terminal
  ON pc_policy_targets(terminal_id);

CREATE TABLE IF NOT EXISTS pc_manual_config (
  terminal_id TEXT PRIMARY KEY,
  boot_json TEXT NOT NULL DEFAULT '',
  shutdown_json TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  operator TEXT NOT NULL DEFAULT '',
  updated_ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS pc_diag_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  terminal_id TEXT NOT NULL,
  command_id INTEGER,
  status TEXT NOT NULL DEFAULT 'pending',
  result_json TEXT,
  error TEXT,
  operator TEXT NOT NULL DEFAULT '',
  created_ts INTEGER NOT NULL, updated_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pc_diag_term
  ON pc_diag_records(terminal_id, id DESC);

CREATE TABLE IF NOT EXISTS wol_schedules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  terminal_id TEXT NOT NULL,
  mac TEXT NOT NULL DEFAULT '',
  name TEXT NOT NULL DEFAULT '',
  time_hhmm TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  method TEXT NOT NULL DEFAULT 'auto',
  run_state TEXT NOT NULL DEFAULT '',
  direct_ts INTEGER,
  relay_ts INTEGER,
  relay_cid INTEGER,
  relay_tid TEXT NOT NULL DEFAULT '',
  last_run_date TEXT NOT NULL DEFAULT '',
  last_result TEXT NOT NULL DEFAULT '',
  operator TEXT NOT NULL DEFAULT '',
  created_ts INTEGER NOT NULL, updated_ts INTEGER NOT NULL,
  UNIQUE(terminal_id, time_hhmm)
);

CREATE TABLE IF NOT EXISTS wol_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  schedule_id INTEGER,
  terminal_id TEXT NOT NULL,
  phase TEXT NOT NULL DEFAULT '',
  relay_terminal_id TEXT,
  ok INTEGER NOT NULL DEFAULT 0,
  detail TEXT NOT NULL DEFAULT '',
  created_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wol_attempts_sched
  ON wol_attempts(schedule_id, id DESC);

-- 开关机任务化（ADR-046）：任务为中心统一模型
-- kind=boot：平台调度（wol 引擎展开执行）；kind=shutdown：声明配置模板集
--（终端本地 schtasks 执行，中心仅声明+下发+回读比对，不承载到期调度）
CREATE TABLE IF NOT EXISTS power_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,                      -- boot | shutdown
  name TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'platform', -- platform | huorong | nad
  origin TEXT NOT NULL DEFAULT 'platform', -- platform | client_personal
  target_type TEXT NOT NULL DEFAULT 'terminals',  -- group | terminals
  group_id INTEGER,
  target_json TEXT NOT NULL DEFAULT '[]',
  repeat TEXT NOT NULL DEFAULT 'daily',    -- boot: daily|workday|holiday|weekly|once
                                           -- shutdown: daily|weekly|once（本地执行）
  weekdays TEXT NOT NULL DEFAULT '',       -- weekly: 7 位 0/1（周一..周日）
  once_date TEXT NOT NULL DEFAULT '',      -- once: YYYY-MM-DD（未来日期）
  time_hhmm TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  method TEXT NOT NULL DEFAULT 'auto',     -- boot: auto|direct|relay
  last_run_date TEXT NOT NULL DEFAULT '',
  last_result TEXT NOT NULL DEFAULT '',
  operator TEXT NOT NULL DEFAULT '',
  created_ts INTEGER NOT NULL, updated_ts INTEGER NOT NULL,
  UNIQUE(kind, name)
);

CREATE TABLE IF NOT EXISTS holidays (
  date TEXT PRIMARY KEY,                   -- YYYY-MM-DD
  type TEXT NOT NULL,                      -- holiday（休）| workday（调休上班）
  name TEXT NOT NULL DEFAULT ''
);

-- 终端关机配置上报（架构修正：本地执行为准，连接时+变更时各上报一次；
-- 中心按版本去重存档；超期未上报标注「配置状态陈旧」）
CREATE TABLE IF NOT EXISTS pc_shutdown_config (
  terminal_id TEXT PRIMARY KEY,
  config_json TEXT NOT NULL DEFAULT '{}',
  version TEXT NOT NULL DEFAULT '',
  reported_ts INTEGER NOT NULL
);
"""


class PowerControlError(Exception):
    """电源管控业务错误（message 为简体中文，可直接作为 API error 文案）。"""

    def __init__(self, message, http_status=400):
        Exception.__init__(self, message)
        self.message = message
        self.http_status = http_status


class PowerControlStore(object):
    """电源快照存储（自持连接 + 线程锁，desktop_policy 同款形态）。"""

    def __init__(self, db_path):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False,
                                     timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self):
        """幂等迁移（PRAGMA 预检，ADR-042 教训：并发 DDL 不用 try-ALTER）。"""
        cur = self._conn.execute("PRAGMA table_info(pc_policy_dispatch)")
        cols = set(r["name"] for r in cur.fetchall())
        cur.close()
        if "note" not in cols:
            self._conn.execute(
                "ALTER TABLE pc_policy_dispatch ADD COLUMN"
                " note TEXT NOT NULL DEFAULT ''")
        # WoL 双路线增强（ADR-044 增补）：随机多候选轮替状态列
        cur = self._conn.execute("PRAGMA table_info(wol_schedules)")
        cols = set(r["name"] for r in cur.fetchall())
        cur.close()
        if "relay_round_ts" not in cols:
            self._conn.execute(
                "ALTER TABLE wol_schedules ADD COLUMN relay_round_ts INTEGER")
        if "relay_queue" not in cols:
            self._conn.execute(
                "ALTER TABLE wol_schedules ADD COLUMN"
                " relay_queue TEXT NOT NULL DEFAULT ''")
        if "relay_tried" not in cols:
            self._conn.execute(
                "ALTER TABLE wol_schedules ADD COLUMN"
                " relay_tried TEXT NOT NULL DEFAULT ''")
        # 任务化（ADR-046）：调度行挂任务关联
        if "task_id" not in cols:
            self._conn.execute(
                "ALTER TABLE wol_schedules ADD COLUMN task_id INTEGER")

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 手动维护登记（ADR-040 增补：平台侧台账，不下发终端）
    # ------------------------------------------------------------------

    @staticmethod
    def _manual_out(row):
        d = dict(row)
        try:
            d["boot"] = json.loads(d.get("boot_json") or "{}")
        except ValueError:
            d["boot"] = {}
        try:
            d["shutdown"] = json.loads(d.get("shutdown_json") or "{}")
        except ValueError:
            d["shutdown"] = {}
        return d

    def manual_get(self, terminal_id):
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM pc_manual_config WHERE terminal_id=?",
                    (terminal_id,))
        row = cur.fetchone()
        cur.close()
        return self._manual_out(row) if row else None

    def manual_upsert(self, terminal_id, boot, shutdown, note, operator,
                      now=None):
        """登记/更新手动开关机配置（boot/shutdown 为已校验完整 dict 或
        None/空对象；与下发 payload 同构，JSON 存档）。"""
        now = now if now is not None else int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM pc_manual_config WHERE terminal_id=?",
                        (terminal_id,))
            cur.execute(
                "INSERT INTO pc_manual_config(terminal_id, boot_json,"
                " shutdown_json, note, operator, updated_ts)"
                " VALUES(?,?,?,?,?,?)",
                (terminal_id,
                 json.dumps(boot or {}, ensure_ascii=False),
                 json.dumps(shutdown or {}, ensure_ascii=False),
                 str(note or ""), str(operator or ""), now))
            self._conn.commit()
            cur.close()
        return self.manual_get(terminal_id)

    def manual_delete(self, terminal_id):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM pc_manual_config WHERE terminal_id=?",
                        (terminal_id,))
            self._conn.commit()
            deleted = cur.rowcount > 0
            cur.close()
        return deleted

    def manual_list(self, limit=200):
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM pc_manual_config"
                    " ORDER BY updated_ts DESC LIMIT ?", (int(limit),))
        rows = [self._manual_out(r) for r in cur.fetchall()]
        cur.close()
        return rows

    # ------------------------------------------------------------------
    # pc_diag 只读诊断（ADR-040 增补联调通道：发起→存档→查看）
    # ------------------------------------------------------------------

    def diag_create(self, terminal_id, command_id, operator, now=None):
        now = now if now is not None else int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO pc_diag_records(terminal_id, command_id,"
                " status, operator, created_ts, updated_ts)"
                " VALUES(?,?, 'pending', ?, ?, ?)",
                (terminal_id, int(command_id), str(operator or ""), now, now))
            did = cur.lastrowid
            self._conn.commit()
            cur.close()
        return did

    @staticmethod
    def _diag_out(row):
        d = dict(row)
        try:
            d["result"] = json.loads(d.get("result_json") or "null")
        except ValueError:
            d["result"] = None
        return d

    def diag_get(self, diag_id):
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM pc_diag_records WHERE id=?", (int(diag_id),))
        row = cur.fetchone()
        cur.close()
        return self._diag_out(row) if row else None

    def diag_by_command(self, terminal_id, command_id):
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM pc_diag_records WHERE terminal_id=?"
                    " AND command_id=? ORDER BY id DESC LIMIT 1",
                    (terminal_id, int(command_id)))
        row = cur.fetchone()
        cur.close()
        return self._diag_out(row) if row else None

    def diag_complete(self, terminal_id, command_id, ok, result):
        """命令回执 → 存档原始 JSON（result_json），状态 completed/failed。"""
        now = int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE pc_diag_records SET status=?, result_json=?,"
                " error=?, updated_ts=? WHERE terminal_id=? AND command_id=?",
                ("completed" if ok else "failed",
                 json.dumps(result, ensure_ascii=False)[:_MAX_SNAPSHOT_BYTES],
                 None if ok else str((result or {}).get("error") or "")[:256],
                 now, terminal_id, int(command_id)))
            n = cur.rowcount
            self._conn.commit()
            cur.close()
        return n > 0

    def diag_list(self, terminal_id=None, limit=50):
        limit = max(1, min(int(limit or 50), 200))
        cur = self._conn.cursor()
        if terminal_id:
            cur.execute("SELECT * FROM pc_diag_records WHERE terminal_id=?"
                        " ORDER BY id DESC LIMIT ?",
                        (terminal_id, limit))
        else:
            cur.execute("SELECT * FROM pc_diag_records"
                        " ORDER BY id DESC LIMIT ?", (limit,))
        rows = [self._diag_out(r) for r in cur.fetchall()]
        cur.close()
        return rows

    # ------------------------------------------------------------------
    # 上报与查询
    # ------------------------------------------------------------------
    def save_snapshot(self, terminal_id, snapshot):
        """存档一条快照（时间线追加）；返回摘要 dict。"""
        if not isinstance(snapshot, dict):
            raise PowerControlError("快照格式无效（需 JSON 对象）")
        raw = json.dumps(snapshot, ensure_ascii=False)
        if len(raw.encode("utf-8")) > _MAX_SNAPSHOT_BYTES:
            raise PowerControlError("快照体积超出上限")
        try:
            collected_ts = int(snapshot.get("collected_ts") or 0)
        except (TypeError, ValueError):
            collected_ts = 0
        machine = snapshot.get("machine") or {}
        capability = str(machine.get("capability") or "")[:_MAX_CONTENT_TEXT]
        vendor_line = str(machine.get("vendor_line") or "")[:_MAX_CONTENT_TEXT]
        now = int(time.time())
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO power_snapshots(terminal_id, collected_ts,"
                " schema_ver, capability, vendor_line, snapshot, created_ts)"
                " VALUES(?,?,?,?,?,?,?)",
                (str(terminal_id), collected_ts,
                 int(snapshot.get("schema") or 1),
                 capability, vendor_line, raw, now))
            sid = cur.lastrowid
            self._conn.commit()
        return self._out(self.get_snapshot(sid))

    def get_snapshot(self, sid):
        row = self._conn.execute(
            "SELECT * FROM power_snapshots WHERE id=?", (sid,)).fetchone()
        return dict(row) if row else None

    def latest_snapshot(self, terminal_id):
        rows = self._conn.execute(
            "SELECT * FROM power_snapshots WHERE terminal_id=?"
            " ORDER BY collected_ts DESC, id DESC LIMIT 1",
            (str(terminal_id),)).fetchall()
        return self._out(dict(rows[0])) if rows else None

    def history_snapshots(self, terminal_id, limit=50, since=0):
        limit = max(1, min(int(limit or 50), 500))
        rows = self._conn.execute(
            "SELECT * FROM power_snapshots WHERE terminal_id=?"
            " AND created_ts>=?"
            " ORDER BY collected_ts DESC, id DESC LIMIT ?",
            (str(terminal_id), int(since or 0), limit)).fetchall()
        return [self._out(dict(r)) for r in rows]

    @staticmethod
    def _out(row):
        d = dict(row)
        try:
            d["snapshot"] = json.loads(d.get("snapshot") or "{}")
        except ValueError:
            d["snapshot"] = {}
        return d

    # ------------------------------------------------------------------
    # 定时开关机策略下发（批次 + per-terminal 状态机，ADR-040）
    # ------------------------------------------------------------------

    def create_dispatch(self, policy_id, payload, targets, operator,
                        note="", now=None):
        """建批次：targets=[terminal_id,...]，各行初始 pending。返回批次 id。"""
        now = now if now is not None else int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO pc_policy_dispatch(policy_id, operator,"
                " payload, note, created_ts) VALUES(?,?,?,?,?)",
                (str(policy_id), str(operator or ""),
                 json.dumps(payload or {}, ensure_ascii=False),
                 str(note or ""), now))
            did = cur.lastrowid
            for tid in targets:
                cur.execute(
                    "INSERT INTO pc_policy_targets(dispatch_id, terminal_id,"
                    " status, updated_ts, created_ts) VALUES(?,?,?,?,?)",
                    (did, str(tid), "pending", now, now))
            self._conn.commit()
            cur.close()
        return did

    def bind_command(self, dispatch_id, terminal_id, command_id, offline):
        """命令已入队：在线 → pending（待拉取）；离线 → offline_queued（上线补投）。"""
        status = "offline_queued" if offline else "pending"
        with self._lock:
            self._conn.execute(
                "UPDATE pc_policy_targets SET command_id=?, status=?,"
                " updated_ts=? WHERE dispatch_id=? AND terminal_id=?",
                (int(command_id), status, int(time.time()),
                 int(dispatch_id), str(terminal_id)))
            self._conn.commit()

    def on_command_result(self, terminal_id, command_id, ok, result):
        """命令回执 → 更新批次行：success / rejected（不支持/非法参数）/ failed。"""
        result = result or {}
        cap = str(result.get("capability") or "")
        if ok:
            status = "success"
            err = None
        elif cap == "not_supported" or bool(result.get("rejected")):
            status = "rejected"
            err = "终端不支持自动控制"
        else:
            status = "failed"
            steps = result.get("steps") or {}
            errs = []
            for name, st in steps.items():
                if isinstance(st, dict) and not st.get("ok"):
                    errs.append("%s: %s" % (name, st.get("error") or "失败"))
            err = "; ".join(errs) or str(result.get("error") or "执行失败")
        with self._lock:
            self._conn.execute(
                "UPDATE pc_policy_targets SET status=?, error_detail=?,"
                " result_json=?, updated_ts=? WHERE command_id=?"
                " AND terminal_id=?",
                (status, err,
                 json.dumps(result, ensure_ascii=False), int(time.time()),
                 int(command_id), str(terminal_id)))
            self._conn.commit()
        return status

    def dispatch_list(self, limit=50):
        """批次列表（附各终态计数）。"""
        limit = max(1, min(int(limit or 50), 200))
        rows = self._conn.execute(
            "SELECT d.id, d.policy_id, d.operator, d.payload, d.note,"
            " d.created_ts,"
            " COUNT(t.id) AS total,"
            " SUM(t.status='success') AS success,"
            " SUM(t.status='failed') AS failed,"
            " SUM(t.status='rejected') AS rejected,"
            " SUM(t.status IN ('pending','offline_queued')) AS queued"
            " FROM pc_policy_dispatch d LEFT JOIN pc_policy_targets t"
            " ON t.dispatch_id=d.id"
            " GROUP BY d.id ORDER BY d.id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d.get("payload") or "{}")
            except ValueError:
                d["payload"] = {}
            out.append(d)
        return out

    def dispatch_get(self, dispatch_id, store=None):
        """批次详情（targets 各台状态；关联命令已超时且未终态 → expired 展示态）。"""
        row = self._conn.execute(
            "SELECT * FROM pc_policy_dispatch WHERE id=?",
            (int(dispatch_id),)).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["payload"] = json.loads(d.get("payload") or "{}")
        except ValueError:
            d["payload"] = {}
        targets = [dict(r) for r in self._conn.execute(
            "SELECT id, terminal_id, command_id, status, error_detail,"
            " result_json, updated_ts, created_ts FROM pc_policy_targets"
            " WHERE dispatch_id=? ORDER BY terminal_id",
            (int(dispatch_id),)).fetchall()]
        for t in targets:
            try:
                t["result"] = json.loads(t.get("result_json") or "null")
            except ValueError:
                t["result"] = None
            t.pop("result_json", None)
            if t["status"] in ("pending", "offline_queued") and store \
                    and t["command_id"]:
                cmd = store.get_command(t["terminal_id"], t["command_id"])
                if cmd and cmd.get("status") == "timeout":
                    t["status"] = "expired"
        d["targets"] = targets
        return d

    # ------------------------------------------------------------------
    # WoL 定时唤醒（第二段产品化，ADR-044：调度状态机见 wol.py）
    # ------------------------------------------------------------------

    def wol_schedule_create(self, terminal_id, mac, time_hhmm, name="",
                            method="auto", operator="", now=None):
        now = now if now is not None else int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO wol_schedules(terminal_id, mac, name,"
                " time_hhmm, enabled, method, operator, created_ts,"
                " updated_ts) VALUES(?,?,?,?,1,?,?,?,?)",
                (str(terminal_id), str(mac or ""), str(name or ""),
                 str(time_hhmm), str(method or "auto"), str(operator or ""),
                 now, now))
            sid = cur.lastrowid
            self._conn.commit()
            cur.close()
        return sid

    def wol_schedule_list(self):
        rows = self._conn.execute(
            "SELECT * FROM wol_schedules ORDER BY time_hhmm, id").fetchall()
        return [dict(r) for r in rows]

    def wol_schedule_get(self, sid):
        row = self._conn.execute(
            "SELECT * FROM wol_schedules WHERE id=?", (int(sid),)).fetchone()
        return dict(row) if row else None

    def wol_schedule_update(self, sid, fields):
        """白名单字段更新（enable 切换/改名/改时间/改方式），返回受影响行。"""
        allowed = {"name", "time_hhmm", "enabled", "method", "mac"}
        sets, args = [], []
        for k, v in (fields or {}).items():
            if k not in allowed:
                continue
            sets.append("%s=?" % k)
            args.append(int(v) if k == "enabled" else str(v or ""))
        if not sets:
            return False
        sets.append("updated_ts=?")
        args.append(int(time.time()))
        args.append(int(sid))
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("UPDATE wol_schedules SET %s WHERE id=?"
                        % ", ".join(sets), args)
            n = cur.rowcount
            self._conn.commit()
            cur.close()
        return n > 0

    def wol_schedule_mark(self, sid, **fields):
        """调度器内部状态推进（run_state/时间戳/结果），白名单同上 + 运行态列。"""
        allowed = {"name", "time_hhmm", "enabled", "method", "mac",
                   "run_state", "direct_ts", "relay_ts", "relay_cid",
                   "relay_tid", "last_run_date", "last_result",
                   "relay_round_ts", "relay_queue", "relay_tried"}
        sets, args = [], []
        for k, v in fields.items():
            if k not in allowed or v is None:
                continue
            sets.append("%s=?" % k)
            args.append(v if isinstance(v, int) else str(v))
        if not sets:
            return False
        sets.append("updated_ts=?")
        args.append(int(time.time()))
        args.append(int(sid))
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("UPDATE wol_schedules SET %s WHERE id=?"
                        % ", ".join(sets), args)
            n = cur.rowcount
            self._conn.commit()
            cur.close()
        return n > 0

    def wol_schedule_delete(self, sid):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM wol_schedules WHERE id=?", (int(sid),))
            n = cur.rowcount
            self._conn.commit()
            cur.close()
        return n > 0

    def wol_attempt_add(self, schedule_id, terminal_id, phase, relay_tid,
                        ok, detail, now=None):
        now = now if now is not None else int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO wol_attempts(schedule_id, terminal_id, phase,"
                " relay_terminal_id, ok, detail, created_ts)"
                " VALUES(?,?,?,?,?,?,?)",
                (int(schedule_id) if schedule_id else None,
                 str(terminal_id), str(phase or ""),
                 str(relay_tid) if relay_tid else None,
                 1 if ok else 0, str(detail or "")[:400], now))
            self._conn.commit()
            cur.close()

    def wol_attempts_list(self, schedule_id=None, limit=50):
        limit = max(1, min(int(limit or 50), 200))
        cur = self._conn.cursor()
        if schedule_id:
            cur.execute("SELECT * FROM wol_attempts WHERE schedule_id=?"
                        " ORDER BY id DESC LIMIT ?",
                        (int(schedule_id), limit))
        else:
            cur.execute("SELECT * FROM wol_attempts"
                        " ORDER BY id DESC LIMIT ?", (limit,))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return rows

    # ------------------------------------------------------------------
    # 开关机任务化（ADR-046）：任务 CRUD / 目标展开 / 节假日 / 执行历史
    # ------------------------------------------------------------------

    @staticmethod
    def _task_out(row):
        d = dict(row)
        try:
            d["targets"] = json.loads(d.get("target_json") or "[]")
        except ValueError:
            d["targets"] = []
        d.pop("target_json", None)
        return d

    def task_list(self, kind=None):
        sql = "SELECT * FROM power_tasks"
        args = []
        if kind in ("boot", "shutdown"):
            sql += " WHERE kind=?"
            args.append(kind)
        sql += " ORDER BY kind, time_hhmm, id"
        return [self._task_out(r) for r in
                self._conn.execute(sql, args).fetchall()]

    def task_get(self, task_id):
        row = self._conn.execute(
            "SELECT * FROM power_tasks WHERE id=?", (int(task_id),)).fetchone()
        return self._task_out(row) if row else None

    def task_create(self, fields):
        now = int(time.time())
        f = dict(fields or {})
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO power_tasks(kind, name, source, origin,"
                " target_type, group_id, target_json, repeat, weekdays,"
                " once_date, time_hhmm, enabled, method,"
                " operator, created_ts, updated_ts)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (str(f.get("kind")), str(f.get("name") or ""),
                 str(f.get("source") or "platform"),
                 str(f.get("origin") or "platform"),
                 str(f.get("target_type") or "terminals"),
                 int(f["group_id"]) if f.get("group_id") else None,
                 json.dumps(f.get("targets") or [], ensure_ascii=False),
                 str(f.get("repeat") or "daily"),
                 str(f.get("weekdays") or ""),
                 str(f.get("once_date") or ""),
                 str(f.get("time_hhmm")),
                 1 if f.get("enabled", 1) else 0,
                 str(f.get("method") or "auto"),
                 str(f.get("operator") or ""), now, now))
            tid = cur.lastrowid
            self._conn.commit()
            cur.close()
        return self.task_get(tid)

    def task_update(self, task_id, fields):
        allowed = {"name", "source", "repeat", "weekdays", "once_date",
                   "time_hhmm", "enabled", "method",
                   "last_run_date", "last_result"}
        sets, args = [], []
        for k, v in (fields or {}).items():
            if k not in allowed or v is None:
                continue
            sets.append("%s=?" % k)
            if k == "enabled":
                args.append(1 if v else 0)
            else:
                args.append(str(v))
        if not sets:
            return False
        sets.append("updated_ts=?")
        args.extend([int(time.time()), int(task_id)])
        with self._lock:
            self._conn.execute(
                "UPDATE power_tasks SET %s WHERE id=?" % ",".join(sets),
                args)
            self._conn.commit()
        return True

    def task_set_targets(self, task_id, target_type, group_id, targets):
        with self._lock:
            self._conn.execute(
                "UPDATE power_tasks SET target_type=?, group_id=?,"
                " target_json=?, updated_ts=? WHERE id=?",
                (str(target_type),
                 int(group_id) if group_id else None,
                 json.dumps(targets or [], ensure_ascii=False),
                 int(time.time()), int(task_id)))
            self._conn.commit()
        return True

    def task_delete(self, task_id):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM power_tasks WHERE id=?", (int(task_id),))
            n = cur.rowcount
            if n:
                cur.execute("DELETE FROM wol_schedules WHERE task_id=?",
                            (int(task_id),))
            self._conn.commit()
            cur.close()
        return n > 0

    def task_set_enabled(self, task_id, enabled, now=None):
        """启停任务并同步其 wol_schedules 行（双保险，引擎层再复核）。"""
        now = now if now is not None else int(time.time())
        with self._lock:
            self._conn.execute(
                "UPDATE power_tasks SET enabled=?, updated_ts=? WHERE id=?",
                (1 if enabled else 0, now, int(task_id)))
            self._conn.execute(
                "UPDATE wol_schedules SET enabled=?, updated_ts=?"
                " WHERE task_id=?",
                (1 if enabled else 0, now, int(task_id)))
            self._conn.commit()

    def task_gen_name(self, kind, time_hhmm):
        """空名自动生成（UNIQUE(kind,name) 防撞）。"""
        base = "%s任务 %s" % ("开机" if kind == "boot" else "关机",
                              str(time_hhmm))
        name, i = base, 2
        while True:
            dup = self._conn.execute(
                "SELECT 1 FROM power_tasks WHERE kind=? AND name=?",
                (str(kind), name)).fetchone()
            if not dup:
                return name
            name = "%s(%d)" % (base, i)
            i += 1

    def task_count_personal(self, terminal_id):
        """某终端的个性化开机任务数（origin=client_personal 且目标含本终端）。"""
        n = 0
        for t in self.task_list(kind="boot"):
            if str(t.get("origin") or "platform") != "client_personal":
                continue
            if terminal_id in [str(x) for x in (t.get("targets") or [])]:
                n += 1
        return n

    def boot_tasks_for_terminal(self, store, terminal_id):
        """终端命中启用中 boot 任务（组展开与执行引擎同源
        expand_platform_targets，展示与执行一致）。按下次触发升序。
        返回 hits（ADR-047 增补：日历降级可选例外层，无 fallback 态）。"""
        today = time.strftime("%Y-%m-%d")
        hmap = self.holidays_map(today[:4])
        hits = []
        for t in self.task_list(kind="boot"):
            if not t.get("enabled"):
                continue
            tids, _n = self.expand_platform_targets(store, t)
            if terminal_id not in tids:
                continue
            item = dict(t)
            nts = next_trigger_ts(t, today, hmap)
            item["next_ts"] = nts
            item["next_trigger"] = time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(nts)) if nts else ""
            hits.append(item)
        hits.sort(key=lambda x: (x["next_ts"] is None, x["next_ts"] or 0))
        return hits

    def expand_platform_targets(self, store, task):
        """目标展开（触发时动态）：按 task.source 分派到三类资产源。

        - platform：组=组内直绑平台终端（root=全部平台终端）；指定=平台终端
        - huorong ：组=平台侧「安全分组」（关联火绒组的 asset_group）
                    → 组内火绒终端；指定=hr:<client_id>；root=全部火绒终端
        - nad     ：指定=nad:<oid>（用户口径：同步后「检索选择」，组未开放）

        第三方源只展开**可唤醒**终端（有 MAC 且至少一个可派生广播的 IP），
        不满足者剔除并在 note 中如实计数——WoL 硬约束（无 MAC 组不出魔术包）。
        返回 (tids, note)；tids 为平台统一目标标识（第三方带 hr:/nad: 前缀），
        note 供执行留痕。"""
        task = task or {}
        source = str(task.get("source") or "platform")
        if source == "huorong":
            return self._expand_huorong(store, task)
        if source == "nad":
            return self._expand_nad(store, task)
        return self._expand_platform(store, task)

    def _expand_platform(self, store, task):
        """平台源展开（原语义不变）：组=组内直绑终端（root=全部平台终端）、
        指定终端=引用过滤已注销。"""
        task = task or {}
        tids, note = [], ""
        ttype = str(task.get("target_type") or "terminals")
        if ttype == "group":
            gid = task.get("group_id")
            rows = store.list_terminals()
            gname = "组#%s" % gid
            is_root = False
            if gid:
                for g in store.asset_group_list():
                    if int(g["id"]) == int(gid):
                        gname = g["name"] or gname
                        is_root = str(g["source"] or "") == "root"
                        break
            if is_root:
                tids = [r["terminal_id"] for r in rows]
                note = "根组（全部平台终端）展开 %d 台" % len(tids)
            else:
                tids = [r["terminal_id"] for r in rows
                        if r.get("group_id") is not None
                        and int(r["group_id"]) == int(gid or 0)]
                note = "资产组「%s」展开 %d 台" % (gname, len(tids))
        else:
            want = [str(x) for x in (task.get("targets") or [])]
            tids = [x for x in want if store.get_terminal(x)]
            dropped = len(want) - len(tids)
            note = "指定终端 %d 台" % len(tids)
            if dropped:
                note += "（%d 台已注销自动剔除）" % dropped
        return self._dedup(tids), note

    def _expand_huorong(self, store, task):
        """火绒源展开：组=安全分组（火绒组）内终端；指定=hr:<client_id>。

        仅保留可唤醒终端（MAC + IP 齐备），其余剔除并如实计数。"""
        task = task or {}
        ttype = str(task.get("target_type") or "terminals")
        dropped, note = 0, ""
        if ttype == "group":
            gid = task.get("group_id")
            hr_gid, gname, is_root = None, "组#%s" % gid, False
            for g in store.asset_group_list():
                if int(g["id"]) == int(gid or 0):
                    gname = g["name"] or gname
                    hr_gid = g.get("huorong_group_id")
                    is_root = str(g.get("source") or "") == "root"
                    break
            rows = store.hr_clients_wol_targets(None if is_root else hr_gid)
            tids = []
            for r in rows:
                if not r.get("wol_capable"):
                    dropped += 1
                    continue
                tids.append("hr:" + str(r["client_id"]))
            label = ("火绒根组（全部火绒终端）" if is_root
                     else "安全分组「%s」" % gname)
            note = "%s展开 %d 台" % (label, len(tids))
        else:
            want = [str(x) for x in (task.get("targets") or [])]
            tids = []
            for x in want:
                cid = x.split(":", 1)[1] if x.startswith("hr:") else x
                row = store.hr_client_get(cid)
                if not row:
                    dropped += 1
                    continue
                if not (str(row.get("mac") or "").strip()
                        and str(row.get("ip") or row.get("connect_ip")
                                or "").strip()):
                    dropped += 1
                    continue
                tids.append("hr:" + str(row["client_id"]))
            note = "指定火绒终端 %d 台" % len(tids)
        if dropped:
            note += "（%d 台缺 MAC/网段或已消失，已剔除）" % dropped
        return self._dedup(tids), note

    def _expand_nad(self, store, task):
        """画方源展开：仅「指定终端」（nad:<oid>）——用户口径为同步后检索选择；
        组模式未开放（NAD 侧无稳定组 id）。仅保留可唤醒终端。"""
        task = task or {}
        want = [str(x) for x in (task.get("targets") or [])]
        tids, dropped = [], 0
        for x in want:
            oid = x.split(":", 1)[1] if x.startswith("nad:") else x
            row = store.nad_terminal_get(oid)
            if not row or not row.get("wol_capable"):
                dropped += 1
                continue
            tids.append("nad:" + str(row["oid"]))
        note = "指定画方准入终端 %d 台" % len(tids)
        if dropped:
            note += "（%d 台缺 MAC/网段或已消失，已剔除）" % dropped
        return self._dedup(tids), note

    @staticmethod
    def _dedup(seq):
        """保序去重（目标展开统一收口）。"""
        seen, out = set(), []
        for x in seq or []:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    def wol_expand_for_task(self, task, store):
        """boot 任务目标 → wol_schedules 行同步（diff 保留同名同刻运行态，
        迁移时序红线：不重建既有行，引擎无缝接续）。
        返回 {kept, added, removed, conflicts}。"""
        task = task or {}
        tids, _note = self.expand_platform_targets(store, task)
        time_hhmm = str(task.get("time_hhmm") or "")
        method = str(task.get("method") or "auto")
        want = set(tids)
        rows = [r for r in self.wol_schedule_list()
                if r.get("task_id") == task.get("id")]
        old_keys = {(r["terminal_id"], r["time_hhmm"]): r for r in rows}
        conflicts, added, kept = [], 0, 0
        now = int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            for (tid, thhmm), r in old_keys.items():
                if thhmm != time_hhmm or tid not in want:
                    cur.execute("DELETE FROM wol_schedules WHERE id=?",
                                (r["id"],))
            for tid in tids:
                if (tid, time_hhmm) in old_keys:
                    kept += 1
                    continue
                dup = cur.execute(
                    "SELECT id FROM wol_schedules WHERE terminal_id=?"
                    " AND time_hhmm=?", (tid, time_hhmm)).fetchone()
                if dup:
                    conflicts.append({"terminal_id": tid,
                                      "reason": "该终端同一时间已有唤醒计划"})
                    continue
                cur.execute(
                    "INSERT INTO wol_schedules(terminal_id, mac, name,"
                    " time_hhmm, enabled, method, task_id, operator,"
                    " created_ts, updated_ts)"
                    " VALUES(?,?,?,?,1,?,?,?,?,?)",
                    (tid, "", task.get("name") or "", time_hhmm, method,
                     int(task.get("id") or 0), task.get("operator") or "",
                     now, now))
                added += 1
            self._conn.commit()
            cur.close()
        return {"kept": kept, "added": added,
                "removed": len(old_keys) - kept, "conflicts": conflicts}

    def migrate_legacy_schedules(self):
        """存量 wol_schedules（task_id 空）→ power_tasks 迁移（幂等增量）。

        部署时序红线：只建任务并回填 task_id，不删不重建调度行，
        wol_tick 对 daily 任务判定恒真，行为无缝接续（明早 07:30 不空窗）。"""
        rows = [dict(r) for r in self._conn.execute(
            "SELECT * FROM wol_schedules WHERE task_id IS NULL").fetchall()]
        if not rows:
            return {"migrated": 0}
        now = int(time.time())
        migrated = 0
        for r in rows:
            tid = str(r["terminal_id"])
            t = str(r.get("time_hhmm") or "")
            name = str(r.get("name") or "").strip() or \
                ("定时唤醒 %s·%s" % (t, tid))
            try:
                with self._lock:
                    cur = self._conn.cursor()
                    dup = cur.execute(
                        "SELECT 1 FROM power_tasks WHERE kind='boot'"
                        " AND name=?", (name,)).fetchone()
                    if dup:
                        name = "%s·%s" % (name, tid)
                    cur.execute(
                        "INSERT INTO power_tasks(kind, name, source,"
                        " target_type, target_json, repeat, time_hhmm,"
                        " enabled, method, operator, created_ts, updated_ts)"
                        " VALUES('boot',?,'platform','terminals',?,"
                        " 'daily',?,?,?,?,?,?)",
                        (name, json.dumps([tid]), t,
                         1 if r.get("enabled") else 0,
                         str(r.get("method") or "auto"),
                         str(r.get("operator") or ""),
                         int(r.get("created_ts") or now), now))
                    new_id = cur.lastrowid
                    cur.execute("UPDATE wol_schedules SET task_id=?"
                                " WHERE id=?", (new_id, int(r["id"])))
                    self._conn.commit()
                    cur.close()
                migrated += 1
            except sqlite3.IntegrityError:
                continue
        return {"migrated": migrated}

    # ------------------------------------------------------------------
    # 节假日日历（ADR-046）
    # ------------------------------------------------------------------

    def holidays_map(self, year):
        """某年 {date: type} 映射（判定用，每次节拍一次轻查询）。"""
        out = {}
        for r in self._conn.execute(
                "SELECT date, type FROM holidays WHERE date LIKE ?",
                ("%s-%%" % int(year),)).fetchall():
            out[r["date"]] = r["type"]
        return out

    def holiday_list(self, year=None):
        if year:
            rows = self._conn.execute(
                "SELECT * FROM holidays WHERE date LIKE ? ORDER BY date",
                ("%s-%%" % int(year),)).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM holidays ORDER BY date").fetchall()
        return [dict(r) for r in rows]

    def holiday_upsert(self, date, htype, name=""):
        with self._lock:
            self._conn.execute(
                "INSERT INTO holidays(date, type, name) VALUES(?,?,?)"
                " ON CONFLICT(date) DO UPDATE SET type=excluded.type,"
                " name=excluded.name",
                (str(date), str(htype), str(name or "")))
            self._conn.commit()

    def holiday_delete(self, date):
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM holidays WHERE date=?", (str(date),))
            self._conn.commit()
        return cur.rowcount > 0

    def holiday_import(self, items):
        """批量导入（≤500 条/次，逐条校验，非法条目计数返回）。"""
        ok_n, invalid = 0, 0
        with self._lock:
            for it in (items or [])[:_HOLIDAY_IMPORT_MAX]:
                try:
                    d = str((it or {}).get("date") or "")
                    t = str((it or {}).get("type") or "")
                    if not _DATE_RE.match(d) \
                            or t not in ("holiday", "workday"):
                        invalid += 1
                        continue
                    self._conn.execute(
                        "INSERT INTO holidays(date, type, name) VALUES(?,?,?)"
                        " ON CONFLICT(date) DO UPDATE SET type=excluded.type,"
                        " name=excluded.name",
                        (d, t, str((it or {}).get("name") or "")))
                    ok_n += 1
                except Exception:
                    invalid += 1
            self._conn.commit()
        return {"imported": ok_n, "invalid": invalid}

    def holiday_status(self, today=None):
        """当年例外标记统计（可选层：无标记时纯星期语义，用户定案）。"""
        today = today or time.strftime("%Y-%m-%d")
        year = today[:4]
        rows = self._conn.execute(
            "SELECT type FROM holidays WHERE date LIKE ?",
            (year + "-%",)).fetchall()
        hol = sum(1 for r in rows if r["type"] == "holiday")
        work = sum(1 for r in rows if r["type"] == "workday")
        return {"year": int(year), "holiday_count": hol,
                "workday_count": work}

    # ------------------------------------------------------------------
    # 终端关机配置上报（架构修正：本地执行为准；版本去重 + 陈旧标注）
    # ------------------------------------------------------------------

    def shutdown_config_save(self, terminal_id, config, version, now=None):
        """上报入库。同版本重复上报幂等（仅刷新时间戳）；新版本覆盖。"""
        now = now if now is not None else int(time.time())
        version = str(version or "")
        with self._lock:
            row = self._conn.execute(
                "SELECT version FROM pc_shutdown_config WHERE terminal_id=?",
                (str(terminal_id),)).fetchone()
            if row and version and str(row["version"] or "") == version:
                self._conn.execute(
                    "UPDATE pc_shutdown_config SET reported_ts=?"
                    " WHERE terminal_id=?", (now, str(terminal_id)))
                self._conn.commit()
                return {"updated": False, "duplicate": True}
            self._conn.execute(
                "INSERT INTO pc_shutdown_config(terminal_id, config_json,"
                " version, reported_ts) VALUES(?,?,?,?)"
                " ON CONFLICT(terminal_id) DO UPDATE SET"
                " config_json=excluded.config_json,"
                " version=excluded.version,"
                " reported_ts=excluded.reported_ts",
                (str(terminal_id),
                 json.dumps(config or {}, ensure_ascii=False),
                 version, now))
            self._conn.commit()
        return {"updated": True, "duplicate": False}

    @staticmethod
    def _shutdown_config_out(row):
        d = dict(row)
        try:
            d["config"] = json.loads(d.get("config_json") or "{}")
        except ValueError:
            d["config"] = {}
        d.pop("config_json", None)
        return d

    def shutdown_config_get(self, terminal_id):
        row = self._conn.execute(
            "SELECT * FROM pc_shutdown_config WHERE terminal_id=?",
            (str(terminal_id),)).fetchone()
        return self._shutdown_config_out(row) if row else None

    def shutdown_config_list(self):
        rows = self._conn.execute(
            "SELECT * FROM pc_shutdown_config").fetchall()
        return [self._shutdown_config_out(r) for r in rows]

    def shutdown_drift_for_task(self, task, store,
                                stale_sec=None, now=None):
        """任务声明 vs 各目标上报态比对：consistent / drift / not_reported，
        外加 stale（超期未上报）标注。声明形状与 pc_apply_policy
        shutdown-set 同构（比对归一见 _cfg_equal）。"""
        now = now if now is not None else int(time.time())
        stale_sec = stale_sec or SHUTDOWN_STALE_SEC
        tids, note = self.expand_platform_targets(store, task)
        declared = task_shutdown_config(task)
        out = []
        for tid in tids:
            rep = self.shutdown_config_get(tid)
            rc = dict(rep["config"]) if rep else None
            state = "not_reported" if rc is None else \
                ("consistent" if _cfg_equal(declared, rc) else "drift")
            out.append({
                "terminal_id": tid, "state": state,
                "stale": bool(rep and
                              (now - int(rep.get("reported_ts") or 0))
                              > stale_sec),
                "reported": rc,
                "reported_ts": (rep or {}).get("reported_ts"),
                "version": (rep or {}).get("version") or ""})
        return {"declared": declared, "note": note, "targets": out}


# --------------------------------------------------------------------------
# 任务化模块级常量与纯函数（ADR-046；API/调度器/单测共用）
# --------------------------------------------------------------------------

TRIGGER_GRACE_MIN = 30        # 逾期补触窗口（分钟）：修复「精确分钟匹配 +
                              # 在途上限 → 超额行当天漏跑」存量缺陷
_TASK_MAX_TARGETS = 200
_HOLIDAY_IMPORT_MAX = 500
PERSONAL_TASK_LIMIT = 5       # 终端个性化开机任务上限（每终端，fail-closed）
SHUTDOWN_STALE_SEC = 7 * 86400  # 关机配置上报超期阈值（超过标注「陈旧」）

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HHMM_RE = re.compile(r"^\d{2}:\d{2}$")


def task_shutdown_config(task):
    """power_tasks 行 → pc_apply_policy shutdown 配置形状（声明模板，
    与终端本地执行引擎契约同构）。"""
    task = task or {}
    rep = str(task.get("repeat") or "daily")
    cfg = {"enabled": bool(task.get("enabled")), "mode": "daily"}
    if rep == "weekly":
        cfg["mode"] = "weekly"
        days = str(task.get("weekdays") or "")
        cfg["weekdays"] = [days[i:i + 1] == "1" for i in range(7)]
    elif rep == "once":
        cfg["mode"] = "single"
        cfg["date"] = str(task.get("once_date") or "")
    cfg["time"] = str(task.get("time_hhmm") or "")
    return cfg


def _cfg_equal(a, b):
    """声明 vs 上报配置归一比对（容忍键序/布尔或 0-1 weekday 表示）。"""
    if not isinstance(b, dict):
        return False
    a = a or {}
    if bool(a.get("enabled")) != bool((b or {}).get("enabled")):
        return False
    if str(a.get("mode") or "") != str((b or {}).get("mode") or ""):
        return False
    if str(a.get("time") or "") != str((b or {}).get("time") or ""):
        return False
    aw, bw = a.get("weekdays"), (b or {}).get("weekdays")
    if aw is not None or bw is not None:
        def _norm_w(v):
            if isinstance(v, str) and len(v) == 7:
                return [c == "1" for c in v]
            if isinstance(v, (list, tuple)):
                return [bool(x) for x in v]
            return None
        nw, nb = _norm_w(aw), _norm_w(bw)
        if nw is None or nb is None or nw != nb:
            return False
    ad, bd = a.get("date"), (b or {}).get("date")
    if ad or bd:
        if str(ad or "") != str(bd or ""):
            return False
    return True


def time_due_with_grace(time_hhmm, hhmm_now, grace_min=TRIGGER_GRACE_MIN):
    """到期判定（定宽 HH:MM）：目标时刻 ≤ 当前时刻且逾期 ≤ grace 分钟。
    精确匹配（diff=0）天然兼容既有行为。"""
    if not _HHMM_RE.match(str(time_hhmm or "")) \
            or not _HHMM_RE.match(str(hhmm_now or "")):
        return False
    t1 = int(time_hhmm[:2]) * 60 + int(time_hhmm[3:5])
    t2 = int(hhmm_now[:2]) * 60 + int(hhmm_now[3:5])
    return 0 <= (t2 - t1) <= grace_min


def task_due_today(task, date_str, holidays_map=None):
    """任务当日是否应触发（repeat 判定）。holidays_map=该年 {date: type}
    可选例外层：type=workday 把周末标成班、type=holiday 把工作日标成休
    （ADR-047 增补，用户定案）；无标记时纯星期语义（workday=周一~五 /
    holiday=周末），零外部数据依赖。date_str: YYYY-MM-DD。"""
    task = task or {}
    rep = str(task.get("repeat") or "daily")
    try:
        d = datetime.date(int(date_str[:4]), int(date_str[5:7]),
                          int(date_str[8:10]))
        wd = d.weekday()          # 0=周一
    except (ValueError, IndexError):
        return False
    mark = (holidays_map or {}).get(date_str)
    if rep == "daily":
        return True
    if rep == "weekly":
        days = str(task.get("weekdays") or "")
        return len(days) == 7 and days[wd] == "1"
    if rep == "once":
        return str(task.get("once_date") or "") == date_str
    if rep == "workday":
        if mark == "workday":       # 调休上班
            return True
        if mark == "holiday":       # 法定节假日
            return False
        return wd <= 4              # 日历缺失回退：周一~五
    if rep == "holiday":
        if mark == "holiday":
            return True
        if mark == "workday":       # 调休上班日不算休
            return False
        return wd >= 5              # 日历缺失回退：周末
    return False


def validate_task_payload(data, existing=None):
    """任务载荷校验与归一（API/单测共用）。existing 传入时增量合并
    （PUT 未提供字段保持原值）。返回归一 dict；非法抛 PowerControlError。"""
    if not isinstance(data, dict):
        raise PowerControlError("载荷格式无效")
    data = dict(data)
    if data.get("time") is not None and data.get("time_hhmm") is None:
        data["time_hhmm"] = data["time"]   # 别名归一（PUT 增量合并语义）
    eff = dict(existing or {})
    eff.update({k: v for k, v in data.items() if v is not None})
    kind = str(eff.get("kind") or "")
    if kind not in ("boot", "shutdown"):
        raise PowerControlError("kind 仅支持 boot（开机）/ shutdown（关机）")
    source = str(eff.get("source") or "platform")
    if source not in ("platform", "huorong", "nad"):
        raise PowerControlError("资产源仅支持 platform/huorong/nad")
    if kind == "shutdown" and source != "platform":
        raise PowerControlError("关机任务仅支持本平台资产源（第三方终端"
                                "无客户端，无法接收关机指令）")
    ttype = str(eff.get("target_type") or "terminals")
    if ttype not in ("group", "terminals"):
        raise PowerControlError("target_type 仅支持 group/terminals")
    group_id, targets = None, []
    if ttype == "group":
        try:
            group_id = int(eff.get("group_id") or 0)
        except (TypeError, ValueError):
            raise PowerControlError("group_id 无效")
        if group_id <= 0:
            raise PowerControlError("必须选择资产组")
    else:
        raw = eff.get("targets")
        if not isinstance(raw, list) or not raw:
            raise PowerControlError("targets 必须为非空终端数组")
        seen = set()
        for x in raw:
            x = str(x or "").strip()
            if x and x not in seen:
                seen.add(x)
                targets.append(x)
        if not targets:
            raise PowerControlError("targets 为空")
        if len(targets) > _TASK_MAX_TARGETS:
            raise PowerControlError("单任务目标数上限 %d 台"
                                    % _TASK_MAX_TARGETS)
    rep = str(eff.get("repeat") or "daily")
    if rep not in ("daily", "workday", "holiday", "weekly", "once"):
        raise PowerControlError("repeat 仅支持 daily/workday/holiday/"
                                "weekly/once")
    if kind == "shutdown" and rep not in ("daily", "weekly", "once"):
        raise PowerControlError("关机任务由终端本地执行，仅支持"
                                "每天 / 每周指定日 / 单次")
    weekdays = ""
    if rep == "weekly":
        wd = eff.get("weekdays")
        if isinstance(wd, list):
            wd = "".join("1" if x else "0" for x in wd)
        weekdays = str(wd or "")
        if len(weekdays) != 7 or set(weekdays) - {"0", "1"} \
                or "1" not in weekdays:
            raise PowerControlError("weekdays 必须为 7 位 0/1（周一..周日）"
                                    "且至少勾选一天")
    once_date = ""
    if rep == "once":
        once_date = str(eff.get("once_date") or "")
        if not _DATE_RE.match(once_date):
            raise PowerControlError("once_date 必须为 YYYY-MM-DD")
        if not existing and once_date <= time.strftime("%Y-%m-%d"):
            raise PowerControlError("单次任务日期必须为未来日期")
    t = str(eff.get("time_hhmm") or eff.get("time") or "")
    if not _HHMM_RE.match(t):
        raise PowerControlError("time 必须为 HH:MM")
    method = str(eff.get("method") or "auto")
    if kind == "boot" and method not in ("auto", "direct", "relay"):
        raise PowerControlError("method 仅支持 auto/direct/relay")
    name = str(eff.get("name") or "").strip()
    if len(name) > 60:
        raise PowerControlError("名称最长 60 字")
    origin = str(eff.get("origin") or "platform")
    if origin not in ("platform", "client_personal"):
        raise PowerControlError("origin 仅支持 platform/client_personal")
    if kind == "shutdown" and origin == "client_personal":
        raise PowerControlError("终端个性化任务仅支持开机任务")
    return {
        "kind": kind, "source": source, "origin": origin,
        "target_type": ttype,
        "group_id": group_id, "targets": targets, "repeat": rep,
        "weekdays": weekdays, "once_date": once_date, "time_hhmm": t,
        "enabled": 1 if eff.get("enabled", 1) else 0,
        "method": method if kind == "boot" else "auto",
        "name": name,
    }


def next_trigger_ts(task, from_date, holidays_map=None):
    """下次触发时间（epoch，本地时区）；无（once 已过期等）→ None。
    from_date: YYYY-MM-DD（含当天；当天时刻已过则顺延至下一个命中日）。"""
    task = task or {}
    t = str(task.get("time_hhmm") or "00:00")
    try:
        hh, mm = int(t[:2]), int(t[3:5])
    except (ValueError, IndexError):
        return None
    try:
        d0 = datetime.date(int(from_date[:4]), int(from_date[5:7]),
                           int(from_date[8:10]))
    except (ValueError, IndexError):
        return None
    hmap = holidays_map or {}
    nowdt = datetime.datetime.now()
    for i in range(0, 367):
        d = d0 + datetime.timedelta(days=i)
        ds = d.strftime("%Y-%m-%d")
        if not task_due_today(task, ds, hmap):
            continue
        dt = datetime.datetime(d.year, d.month, d.day, hh, mm)
        if i == 0 and dt <= nowdt:
            continue
        return int(dt.timestamp())
    return None


_PC_SINGLETON = None
_PC_LOCK = threading.Lock()


def get_pc(db_path):
    """惰性单例工厂（get_dp 同款形态）。"""
    global _PC_SINGLETON
    with _PC_LOCK:
        if _PC_SINGLETON is None:
            _PC_SINGLETON = PowerControlStore(db_path)
        return _PC_SINGLETON
