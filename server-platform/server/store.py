# -*- coding: utf-8 -*-
"""观枢终端平台服务端 · SQLite 存储层。

表：terminals / metrics / events / bottlenecks（ADR-003）。
线程模型：ThreadingHTTPServer 多线程访问，统一用一把锁序列化写；
WAL 模式允许读并发。保留策略：metrics/events/bottlenecks 按 config 天数清理。
"""
import functools
import json
import secrets
import sqlite3
import threading
import time
import types

_SCHEMA = """
CREATE TABLE IF NOT EXISTS terminals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    terminal_id TEXT UNIQUE NOT NULL,
    terminal_type TEXT NOT NULL DEFAULT 'windows',
    hostname TEXT NOT NULL DEFAULT '',
    os_info TEXT NOT NULL DEFAULT '',
    client_version TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL DEFAULT '',
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    terminal_id TEXT NOT NULL,
    ts INTEGER NOT NULL,
    cpu_percent REAL,
    mem_percent REAL,
    mem_available_percent REAL,
    swap_percent REAL,
    disks_json TEXT NOT NULL DEFAULT '[]',
    volumes_json TEXT NOT NULL DEFAULT '[]',
    raw_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_metrics_tid_ts ON metrics(terminal_id, ts);
CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(ts);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    terminal_id TEXT NOT NULL,
    ts INTEGER NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    category TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_tid_ts ON events(terminal_id, ts);
CREATE TABLE IF NOT EXISTS bottlenecks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    terminal_id TEXT NOT NULL,
    ts INTEGER NOT NULL,
    kind TEXT NOT NULL,
    metric_key TEXT NOT NULL DEFAULT '',
    value REAL,
    threshold REAL,
    level TEXT NOT NULL DEFAULT 'warn',
    detail_json TEXT NOT NULL DEFAULT '{}',
    acknowledged INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_bottlenecks_ts ON bottlenecks(ts);
CREATE INDEX IF NOT EXISTS idx_bottlenecks_tid_ts ON bottlenecks(terminal_id, ts);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    masked INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS whitelist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cidr TEXT NOT NULL UNIQUE,
    note TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS vlan_kb (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cidr TEXT NOT NULL UNIQUE,
    vlan_id TEXT NOT NULL DEFAULT '',
    zone_desc TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    updated_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    ip TEXT NOT NULL DEFAULT '',
    path TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
CREATE TABLE IF NOT EXISTS upload_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    terminal_id TEXT NOT NULL DEFAULT '',
    filename TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL DEFAULT '',
    path TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'ftp',
    ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_upload_ts ON upload_files(ts);
CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    terminal_id TEXT NOT NULL,
    command TEXT NOT NULL,
    args_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    timeout_sec INTEGER NOT NULL DEFAULT 120,
    source TEXT NOT NULL DEFAULT 'console',
    created_ts INTEGER NOT NULL,
    sent_ts INTEGER,
    done_ts INTEGER,
    result_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_commands_tid ON commands(terminal_id, status);
CREATE TABLE IF NOT EXISTS iperf_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT UNIQUE NOT NULL,
    terminal_id TEXT NOT NULL,
    test_type TEXT NOT NULL,
    port INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    created_ts INTEGER NOT NULL,
    finished_ts INTEGER,
    result_json TEXT NOT NULL DEFAULT '{}',
    log_text TEXT NOT NULL DEFAULT '',
    command_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_iperf_ts ON iperf_tasks(created_ts);
CREATE TABLE IF NOT EXISTS ai_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    terminal_id TEXT NOT NULL,
    ts INTEGER NOT NULL,
    trigger TEXT NOT NULL DEFAULT 'console',
    issue TEXT NOT NULL DEFAULT '',
    context_json TEXT NOT NULL DEFAULT '{}',
    response_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ok',
    error TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ai_ts ON ai_analyses(ts);
CREATE TABLE IF NOT EXISTS asset_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    parent_id INTEGER,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_asset_groups_parent ON asset_groups(parent_id);
CREATE TABLE IF NOT EXISTS ipconflict_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    terminal_id TEXT NOT NULL,
    ip TEXT NOT NULL,
    mac TEXT NOT NULL,
    created_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ipconflict_ip ON ipconflict_reports(ip, created_ts);
CREATE TABLE IF NOT EXISTS ipconflict_deep_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL UNIQUE,
    terminal_id TEXT NOT NULL,
    ip TEXT NOT NULL,
    mac TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    steps_json TEXT NOT NULL DEFAULT '[]',
    verdict_json TEXT,
    error TEXT NOT NULL DEFAULT '',
    created_ts INTEGER NOT NULL, updated_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS third_party_apis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, base_url TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT 'GET',
    params_json TEXT NOT NULL DEFAULT '{}',
    headers_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    note TEXT NOT NULL DEFAULT '',
    created_ts INTEGER NOT NULL, updated_ts INTEGER);
CREATE TABLE IF NOT EXISTS terminal_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT NOT NULL, label TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_ts INTEGER NOT NULL, updated_ts INTEGER, last_used_ts INTEGER);
CREATE UNIQUE INDEX IF NOT EXISTS idx_terminal_tokens_token ON terminal_tokens(token);
CREATE TABLE IF NOT EXISTS switches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, ip TEXT NOT NULL,
    ssh_port INTEGER NOT NULL DEFAULT 22,
    username TEXT NOT NULL DEFAULT '',
    password_enc TEXT NOT NULL DEFAULT '',
    brand TEXT NOT NULL DEFAULT '',
    created_ts INTEGER NOT NULL, updated_ts INTEGER);
CREATE TABLE IF NOT EXISTS hr_groups (
    group_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    parent INTEGER,
    total INTEGER NOT NULL DEFAULT 0,
    online INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS hr_clients (
    client_id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    computer_name TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL DEFAULT '',
    connect_ip TEXT NOT NULL DEFAULT '',
    mac TEXT NOT NULL DEFAULT '',
    group_id INTEGER,
    native_group_id INTEGER,
    online INTEGER NOT NULL DEFAULT 0,
    os TEXT NOT NULL DEFAULT '',
    version TEXT NOT NULL DEFAULT '',
    last_seen INTEGER,
    first_seen INTEGER,
    last_off INTEGER,
    this_on INTEGER,
    updated_at INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_hr_clients_group ON hr_clients(group_id);
CREATE INDEX IF NOT EXISTS idx_hr_clients_online ON hr_clients(online);
CREATE TABLE IF NOT EXISTS hr_sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at INTEGER NOT NULL,
    finished_at INTEGER NOT NULL,
    ok INTEGER NOT NULL,
    trigger TEXT NOT NULL DEFAULT '',
    groups_n INTEGER NOT NULL DEFAULT 0,
    clients_n INTEGER NOT NULL DEFAULT 0,
    matched_n INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS hr_terminal_map (
    hr_client_id TEXT PRIMARY KEY,
    terminal_id TEXT NOT NULL,
    match_type TEXT NOT NULL DEFAULT 'mac',
    created_ts INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_hr_map_terminal
    ON hr_terminal_map(terminal_id);
CREATE TABLE IF NOT EXISTS hr_link_ignore (
    hr_client_id TEXT NOT NULL,
    terminal_id TEXT NOT NULL,
    created_ts INTEGER NOT NULL,
    PRIMARY KEY(hr_client_id, terminal_id)
);
CREATE TABLE IF NOT EXISTS hr_group_override (
    hr_client_id TEXT PRIMARY KEY,
    group_id INTEGER NOT NULL,
    created_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS hr_virus_stat (
    client_id TEXT PRIMARY KEY,
    total INTEGER NOT NULL DEFAULT 0,
    success INTEGER NOT NULL DEFAULT 0,
    fail INTEGER NOT NULL DEFAULT 0,
    ignored INTEGER NOT NULL DEFAULT 0,
    trusted INTEGER NOT NULL DEFAULT 0,
    snapshot_ts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS hr_client_assets (
    client_id TEXT PRIMARY KEY,
    assets_json TEXT NOT NULL DEFAULT '{}',
    updated_at INTEGER NOT NULL DEFAULT 0
);

-- 画方准入（NAD）终端镜像（ADR-047 批 B）
-- NAD 平台无本地库、终端标识为 oid；本表为同步快照，支撑：
--   ① 开关机任务「画方准入」资产源的组/终端选取
--   ② WoL 目标解析（MAC 与 IP 是发魔术包与派生广播地址的必需项）
CREATE TABLE IF NOT EXISTS nad_terminals (
    oid TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    mac TEXT NOT NULL DEFAULT '',
    ips_json TEXT NOT NULL DEFAULT '[]',
    group_path TEXT NOT NULL DEFAULT '',
    online INTEGER NOT NULL DEFAULT 0,
    owner TEXT NOT NULL DEFAULT '',
    synced_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nad_terminals_group
  ON nad_terminals(group_path);
"""


def _row_to_dict(cursor, row):
    return dict((cursor.description[i][0], row[i]) for i in range(len(row)))


def _as_int(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_float(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _loads(text, default):
    if not text:
        return default
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return default


class Store(object):
    """SQLite 访问封装。所有写操作经内部锁串行化。"""

    def __init__(self, db_path, config_token=None):
        # H8（安全改造 R1）：改为可重入锁 —— 公开方法统一串行化（见文件末尾自动包装），
        # 内部方法嵌套调用依赖 RLock 的重入语义
        self._lock = threading.RLock()
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self._conn.row_factory = _row_to_dict
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        # 存量库迁移：terminals 补资产列（幂等）
        for column, typedef in (
                ("cpu_model", "TEXT NOT NULL DEFAULT ''"),
                ("cpu_cores", "INTEGER"),
                ("mem_total_mb", "INTEGER"),
                ("disk_total_gb", "REAL"),
                ("gpu_info", "TEXT NOT NULL DEFAULT ''"),
                ("os_arch", "TEXT NOT NULL DEFAULT ''"),
                ("hwinfo_json", "TEXT NOT NULL DEFAULT '{}'"),
                ("asset_detail", "TEXT"),
                ("group_id", "INTEGER")):
            try:
                self._conn.execute(
                    "ALTER TABLE terminals ADD COLUMN %s %s" % (column, typedef))
            except sqlite3.OperationalError:
                pass  # 列已存在
        # 绑定索引依赖 group_id 列，必须在迁移 ALTER 之后创建
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_terminals_group ON terminals(group_id)")
        # hr_sync_log 增列（幂等；ADR-033 融合改造追加 matched_n）
        try:
            self._conn.execute(
                "ALTER TABLE hr_sync_log ADD COLUMN matched_n INTEGER"
                " NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # 列已存在
        # hr_clients 增列（幂等）：native_group_id 保存火绒原生分组，
        # group_id 为分组覆盖（hr_group_override）生效值
        try:
            self._conn.execute(
                "ALTER TABLE hr_clients ADD COLUMN native_group_id INTEGER")
        except sqlite3.OperationalError:
            pass  # 列已存在
        # hr_clients 增列（幂等）：上下线/开关机时间（火绒 _list 基础
        # 字段 first_appear_time/last_off_time/this_on_time，ADR-033 增补三）
        for column in ("first_seen", "last_off", "this_on"):
            try:
                self._conn.execute(
                    "ALTER TABLE hr_clients ADD COLUMN %s INTEGER" % column)
            except sqlite3.OperationalError:
                pass  # 列已存在
        self._conn.commit()
        # 向后兼容迁移（ADR-021）：把 config.json 的 terminal_token 收录进
        # terminal_tokens（label='default'，幂等 INSERT OR IGNORE），
        # 存量终端 token 行为完全不变；此后新 token 亦可在控制台登记。
        if config_token:
            self.token_ensure_default(str(config_token))

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # terminals
    # ------------------------------------------------------------------

    def register_terminal(self, terminal_id, terminal_type, hostname, os_info,
                          client_version, ip, hwinfo=None, now=None, asset=None):
        now = now if now is not None else int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT id FROM terminals WHERE terminal_id=?", (terminal_id,))
            row = cur.fetchone()
            hw = hwinfo or {}
            # asset 序列化必须零异常（TBC-001）：None/缺字段/不可序列化一律静默降级 NULL
            asset_json = None
            if asset:
                try:
                    asset_json = json.dumps(asset, ensure_ascii=False)
                except (TypeError, ValueError):
                    asset_json = None
            if row:
                cur.execute(
                    "UPDATE terminals SET terminal_type=?, hostname=?, os_info=?,"
                    " client_version=?, ip=?, last_seen=?, cpu_model=?, cpu_cores=?,"
                    " mem_total_mb=?, disk_total_gb=?, gpu_info=?, os_arch=?,"
                    " hwinfo_json=?, asset_detail=? WHERE terminal_id=?",
                    (terminal_type, hostname, os_info, client_version, ip, now,
                     str(hw.get("cpu_model") or ""), _as_int(hw.get("cpu_cores")),
                     _as_int(hw.get("mem_total_mb")), _as_float(hw.get("disk_total_gb")),
                     str(hw.get("gpu_info") or ""), str(hw.get("os_arch") or ""),
                     json.dumps(hw, ensure_ascii=False), asset_json, terminal_id))
                registered = False
            else:
                cur.execute(
                    "INSERT INTO terminals(terminal_id, terminal_type, hostname, os_info,"
                    " client_version, ip, first_seen, last_seen, cpu_model, cpu_cores,"
                    " mem_total_mb, disk_total_gb, gpu_info, os_arch, hwinfo_json,"
                    " asset_detail) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (terminal_id, terminal_type, hostname, os_info, client_version,
                     ip, now, now, str(hw.get("cpu_model") or ""),
                     _as_int(hw.get("cpu_cores")), _as_int(hw.get("mem_total_mb")),
                     _as_float(hw.get("disk_total_gb")),
                     str(hw.get("gpu_info") or ""), str(hw.get("os_arch") or ""),
                     json.dumps(hw, ensure_ascii=False), asset_json))
                registered = True
            self._conn.commit()
            cur.close()
            return registered

    def get_terminal_asset(self, terminal_id):
        """资产明细（register 上报的结构化 JSON，无则回退 hwinfo_json）。"""
        cur = self._conn.cursor()
        cur.execute("SELECT asset_detail, hwinfo_json FROM terminals"
                    " WHERE terminal_id=?", (terminal_id,))
        row = cur.fetchone()
        cur.close()
        if not row:
            return None
        asset = _loads(row.get("asset_detail"), None)
        if asset is not None:
            return asset
        hw = _loads(row.get("hwinfo_json"), None)
        return hw if isinstance(hw, dict) else None

    def metrics_timeline(self, terminal_id, days=7, limit=240):
        """平台通信时间线（ADR-043 过渡数据源）：窗口内该终端每次指标
        上报/心跳同拍的时间点（ts 升序 + 间隔秒）。

        语义边界：仅代表终端与平台的通信记录（心跳/上报到达时间），
        不代表准入层入网时段——前端必须显著标注。"""
        since = int(time.time()) - int(days) * 86400
        cur = self._conn.cursor()
        cur.execute(
            "SELECT ts, cpu_percent, mem_percent FROM metrics"
            " WHERE terminal_id=? AND ts>=? ORDER BY ts ASC LIMIT ?",
            (terminal_id, since, int(limit)))
        rows = cur.fetchall()
        cur.close()
        pts = []
        prev = None
        for r in rows:
            ts = r["ts"]
            gap = (ts - prev) if prev is not None else None
            prev = ts
            pts.append({"ts": ts,
                        "cpu": (round(r["cpu_percent"], 1)
                                if r["cpu_percent"] is not None else None),
                        "mem": (round(r["mem_percent"], 1)
                                if r["mem_percent"] is not None else None),
                        "gap": gap})
        return {"available": bool(pts), "days": int(days),
                "total": len(pts), "points": pts}

    def touch_terminal(self, terminal_id, now=None):
        """心跳：刷新 last_seen。返回终端是否存在。"""
        now = now if now is not None else int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("UPDATE terminals SET last_seen=? WHERE terminal_id=?",
                        (now, terminal_id))
            self._conn.commit()
            found = cur.rowcount > 0
            cur.close()
            return found

    def delete_terminal(self, terminal_id, purge=False):
        """移除终端注册。purge=True 时级联清除历史数据
        （指标/瓶颈/事件/命令/iperf 任务/电源快照）；
        下发批次回执、火绒镜像、审计记录保留。返回终端是否存在。"""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT id FROM terminals WHERE terminal_id=?",
                        (terminal_id,))
            if not cur.fetchone():
                cur.close()
                return False
            cur.execute("DELETE FROM terminals WHERE terminal_id=?",
                        (terminal_id,))
            if purge:
                for tbl in ("metrics", "bottlenecks", "events", "commands",
                            "iperf_tasks", "power_snapshots"):
                    cur.execute("DELETE FROM %s WHERE terminal_id=?" % tbl,
                                (terminal_id,))
            self._conn.commit()
            cur.close()
        return True

    def get_terminal(self, terminal_id):
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM terminals WHERE terminal_id=?", (terminal_id,))
        row = cur.fetchone()
        cur.close()
        return row

    def list_terminals(self):
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM terminals ORDER BY last_seen DESC")
        rows = cur.fetchall()
        cur.close()
        return rows

    # ------------------------------------------------------------------
    # asset groups（资产组树 + 终端绑定）
    # ------------------------------------------------------------------

    def asset_group_list(self):
        """未软删资产组（附直属终端计数 + 来源/映射列），按 id 排序。"""
        self._asset_group_migrate()
        cur = self._conn.cursor()
        cur.execute(
            "SELECT g.id, g.name, g.parent_id, g.created_at, g.source,"
            " g.huorong_group_id,"
            " (SELECT COUNT(*) FROM terminals t WHERE t.group_id = g.id)"
            "     AS terminal_count"
            " FROM asset_groups g WHERE COALESCE(g.deleted,0)=0"
            " ORDER BY g.id, g.created_at")
        rows = cur.fetchall()
        cur.close()
        return rows

    def _asset_group_migrate(self):
        """资产组表结构幂等迁移：来源/火绒映射/软删三列。

        实现（ADR-042 顺带修复）：先 PRAGMA 检查列存在再 ALTER——
        原实现 try/except OperationalError 依赖"重复列"异常类型，
        但并发 DDL 场景下 sqlite3 会抛 DatabaseError 甚至
        SystemError（C 层 NULL），均不可靠；且每次空跑 ALTER 与同库
        其他连接的读写交织有 schema 冲突面。PRAGMA 检查在实例锁内
        串行执行，列已存在时零 DDL。"""
        with self._lock:
            cur = self._conn.execute("PRAGMA table_info(asset_groups)")
            # row_factory 为 _row_to_dict（dict），按列名取值
            existing = set(r["name"] for r in cur.fetchall())
            cur.close()
            for column, typedef in (("source", "TEXT NOT NULL DEFAULT 'manual'"),
                                    ("huorong_group_id", "INTEGER"),
                                    ("deleted", "INTEGER NOT NULL DEFAULT 0")):
                if column not in existing:
                    self._conn.execute(
                        "ALTER TABLE asset_groups ADD COLUMN %s %s"
                        % (column, typedef))
            self._conn.commit()

    def asset_group_ensure_root(self, name="全部资产"):
        """根节点幂等确保（source=root，parent 空）。返回根 id。"""
        self._asset_group_migrate()
        cur = self._conn.cursor()
        cur.execute("SELECT id FROM asset_groups WHERE source='root'"
                    " AND COALESCE(deleted,0)=0 LIMIT 1")
        row = cur.fetchone()
        cur.close()
        if row:
            return row["id"]
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO asset_groups(name,parent_id,source,created_at)"
                " VALUES(?,NULL,'root',?)", (name, int(time.time())))
            gid = cur.lastrowid
            self._conn.commit()
            cur.close()
        return gid

    def asset_group_sync_huorong(self, groups, matched_map, now=None):
        """火绒分组镜像同步为资产组子树（幂等；ADR-041）。

        groups=[{id,name,parent}]（调用方给火绒原始组全量）。
        规则：按 huorong_group_id 幂等（有→更新名/父；无→父先子后创建；
        库内有映射但本次不含 → 软删 deleted=1，终端挂载保留）；
        同步后按 matched_map {huorong_gid:[terminal_id,...]} 重挂终端。
        返回 {root, created, updated, soft_deleted, mounted}。
        """
        now = now if now is not None else int(time.time())
        root = self.asset_group_ensure_root()
        self._asset_group_migrate()
        by_hgid = {}
        cur = self._conn.cursor()
        cur.execute("SELECT id, huorong_group_id FROM asset_groups"
                    " WHERE huorong_group_id IS NOT NULL")
        for r in cur.fetchall():
            by_hgid[r["huorong_group_id"]] = r["id"]
        cur.close()
        stat = {"root": root, "created": 0, "updated": 0, "soft_deleted": 0,
                "mounted": 0}
        # 父先子后：按 parent 拓扑（火绒 parent=0 视为根下）
        pending = list(groups)
        guard = 0
        while pending and guard <= len(groups) + 2:
            guard += 1
            rest = []
            progressed = False
            for g in pending:
                hpid = g.get("parent") or 0
                parent_gid = root if not hpid else by_hgid.get(hpid)
                if hpid and parent_gid is None:
                    rest.append(g)     # 父尚未建，下一轮
                    continue
                if not hpid:
                    parent_gid = root
                existing = by_hgid.get(g["id"])
                with self._lock:
                    cur = self._conn.cursor()
                    if existing:
                        cur.execute(
                            "UPDATE asset_groups SET name=?, parent_id=?,"
                            " deleted=0 WHERE id=?",
                            (g["name"], parent_gid, existing))
                        stat["updated"] += 1
                    else:
                        cur.execute(
                            "INSERT INTO asset_groups(name,parent_id,source,"
                            "huorong_group_id,created_at)"
                            " VALUES(?,?, 'huorong', ?, ?)",
                            (g["name"], parent_gid, g["id"], now))
                        existing = cur.lastrowid
                        stat["created"] += 1
                    self._conn.commit()
                    cur.close()
                by_hgid[g["id"]] = existing
                progressed = True
            if not progressed:
                break
            pending = rest
        # 软删：库内有映射但本次火绒组不含
        live = {g["id"] for g in groups}
        for hgid, agid in by_hgid.items():
            if hgid in live:
                continue
            with self._lock:
                self._conn.execute(
                    "UPDATE asset_groups SET deleted=1 WHERE id=?",
                    (agid,))
                self._conn.commit()
            stat["soft_deleted"] += 1
        # 终端挂载（matched 映射 → 资产组对应节点）
        with self._lock:
            for hgid, tids in (matched_map or {}).items():
                agid = by_hgid.get(hgid)
                if not agid:
                    continue
                for tid in tids:
                    self._conn.execute(
                        "UPDATE terminals SET group_id=? WHERE terminal_id=?",
                        (agid, str(tid)))
                    stat["mounted"] += 1
            self._conn.commit()
        return stat

    def asset_group_create(self, name, parent_id=None):
        """创建资产组（parent_id 为空 = 根下顶级；层级自由扩展）。"""
        name = (name or "").strip()
        if not name:
            raise ValueError("empty name")
        if len(name) > 60:
            raise ValueError("name too long")
        with self._lock:
            cur = self._conn.cursor()
            if parent_id is not None:
                cur.execute("SELECT id FROM asset_groups WHERE id=?", (parent_id,))
                if cur.fetchone() is None:
                    cur.close()
                    raise ValueError("parent group not found")
            cur.execute("INSERT INTO asset_groups(name,parent_id,created_at)"
                        " VALUES(?,?,?)", (name, parent_id, int(time.time())))
            gid = cur.lastrowid
            self._conn.commit()
            cur.close()
            return gid

    def asset_group_rename(self, group_id, name):
        name = (name or "").strip()
        if not name:
            raise ValueError("empty name")
        if len(name) > 60:
            raise ValueError("name too long")
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("UPDATE asset_groups SET name=? WHERE id=?",
                        (name, group_id))
            changed = cur.rowcount > 0
            self._conn.commit()
            cur.close()
            return changed

    def asset_group_delete(self, group_id):
        """删除组：含子组拒绝（树须从叶子删起）；火绒同步组拒绝手动删
        （下轮同步会重建，须待火绒侧删除后镜像软删）；直属终端自动解绑。
        返回 ok / has_children / missing / synced / root。"""
        self._asset_group_migrate()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT id, source FROM asset_groups WHERE id=?",
                        (group_id,))
            row = cur.fetchone()
            if row is None:
                cur.close()
                return "missing"
            if row["source"] == "root":
                cur.close()
                return "root"
            if row["source"] == "huorong":
                cur.close()
                return "synced"
            cur.execute("SELECT COUNT(*) AS n FROM asset_groups"
                        " WHERE parent_id=? AND COALESCE(deleted,0)=0",
                        (group_id,))
            if (cur.fetchone().get("n") or 0) > 0:
                cur.close()
                return "has_children"
            cur.execute("UPDATE terminals SET group_id=NULL WHERE group_id=?",
                        (group_id,))
            cur.execute("DELETE FROM asset_groups WHERE id=?", (group_id,))
            cur.execute("UPDATE terminals SET group_id=NULL WHERE group_id=?",
                        (group_id,))
            cur.execute("DELETE FROM asset_groups WHERE id=?", (group_id,))
            self._conn.commit()
            cur.close()
            return "ok"

    def set_terminal_group(self, terminal_id, group_id):
        """绑定/改绑/解绑终端到资产组（group_id=None 解绑）。"""
        with self._lock:
            cur = self._conn.cursor()
            if group_id is not None:
                cur.execute("SELECT id FROM asset_groups WHERE id=?", (group_id,))
                if cur.fetchone() is None:
                    cur.close()
                    raise ValueError("group not found")
            cur.execute("SELECT id FROM terminals WHERE terminal_id=?", (terminal_id,))
            if cur.fetchone() is None:
                cur.close()
                raise ValueError("terminal not found")
            cur.execute("UPDATE terminals SET group_id=? WHERE terminal_id=?",
                        (group_id, terminal_id))
            self._conn.commit()
            cur.close()
            return True

    # ------------------------------------------------------------------
    # IP 冲突检测（终端上报 + 中心交叉校验，net-doctor 功能二）
    # ------------------------------------------------------------------

    @staticmethod
    def _mac_key(mac):
        """MAC 归一比对键：去分隔符（:-. 空白）+ 小写（判定与格式无关）。"""
        return "".join(ch for ch in str(mac or "") if ch not in ":-. ").lower()

    def ipconflict_report(self, terminal_id, ip, mac, window_days=7):
        """登记终端上报的 IP/MAC 并交叉校验（ADR-028 判定精化）。

        - 同 IP 近 N 天出现**其它 terminal_id** 的报告 → conflict_suspect=true
          （多终端抢占同 IP；terminal_id 不在 terminals 表的未知来源按保守
          原则同样 suspect，suspect_reasons 区分 multi_terminal/unknown_terminal）；
        - 仅同一 terminal_id 的 MAC 变化（真实网卡变更/虚拟适配器）→ 不
          suspect，记录 nic_history 供人工参考（证据照存）；
        - MAC 比对经 _mac_key 归一（大小写/分隔符无关），避免同网卡因
          格式差异误判为异 MAC；
        - 准入日志与核心交换机状态数据源由 api 层注入（store 无网络依赖），
          verdict.sources 如实标注，不虚构结论。"""
        now = int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("INSERT INTO ipconflict_reports(terminal_id,ip,mac,created_ts)"
                        " VALUES(?,?,?,?)", (terminal_id, ip, mac, now))
            self._conn.commit()
            cur.close()
        foreign, nic_history = self._ipconflict_scan(
            ip, now - window_days * 86400, terminal_id, self._mac_key(mac))
        reasons = sorted({"multi_terminal" if f["known"] else "unknown_terminal"
                          for f in foreign})
        return {
            "ip": ip,
            "mac": mac,
            "checked_at": now,
            "conflict_suspect": bool(foreign),
            "suspect_reasons": reasons,
            "evidence": foreign,
            "nic_history": nic_history,
            "window_days": window_days,
            "sources": {
                "terminal_reports": "ok",
                "admission_log": "not_connected",
                "core_switch_state": "not_connected",
            },
        }

    def ipconflict_lookup(self, ip, terminal_id, mac, window_days=7):
        """只读交叉校验（不落库，ADR-029 深度检测引擎 conclude 步骤复用）。

        判定语义与 ipconflict_report 一致（foreign 终端维度 + MAC 归一），
        返回 {conflict_suspect, suspect_reasons, evidence, nic_history}。"""
        now = int(time.time())
        foreign, nic_history = self._ipconflict_scan(
            ip, now - window_days * 86400, terminal_id, self._mac_key(mac))
        reasons = sorted({"multi_terminal" if f["known"] else "unknown_terminal"
                          for f in foreign})
        return {"ip": ip, "mac": mac, "checked_at": now,
                "conflict_suspect": bool(foreign),
                "suspect_reasons": reasons, "evidence": foreign,
                "nic_history": nic_history, "window_days": window_days}

    # ---------------- 深度检测任务（ADR-029，异步编排） ----------------

    def deep_task_create(self, task_id, terminal_id, ip, mac):
        now = int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("INSERT INTO ipconflict_deep_tasks(task_id,"
                        " terminal_id, ip, mac, status, steps_json,"
                        " created_ts, updated_ts) VALUES(?,?,?,?,?,?,?,?)",
                        (task_id, terminal_id, ip, mac, "running", "[]",
                         now, now))
            self._conn.commit()
            cur.close()
        return task_id

    def deep_task_update(self, task_id, steps=None, verdict=None,
                         status=None, error=None):
        """进度/结论更新（on_step 逐步回写，steps 全量覆盖）。"""
        sets, args = ["updated_ts=?"], [int(time.time())]
        if steps is not None:
            sets.append("steps_json=?")
            args.append(json.dumps(steps, ensure_ascii=False))
        if verdict is not None:
            sets.append("verdict_json=?")
            args.append(json.dumps(verdict, ensure_ascii=False))
        if status is not None:
            sets.append("status=?")
            args.append(status)
        if error is not None:
            sets.append("error=?")
            args.append(error)
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("UPDATE ipconflict_deep_tasks SET %s WHERE task_id=?"
                        % ",".join(sets), args + [task_id])
            self._conn.commit()
            cur.close()

    def deep_task_get(self, task_id):
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM ipconflict_deep_tasks WHERE task_id=?",
                    (str(task_id or ""),))
        row = cur.fetchone()
        cur.close()
        if not row:
            return None
        out = dict(row)
        try:
            out["steps"] = json.loads(out.pop("steps_json") or "[]")
        except ValueError:
            out["steps"] = []
        try:
            out["verdict"] = (json.loads(out.pop("verdict_json"))
                              if out.get("verdict_json") else None)
        except ValueError:
            out["verdict"] = None
        return out

    def deep_task_latest(self, terminal_id):
        """该终端最近一次深度检测任务（ADR-030 AI 聚合数据源）。"""
        cur = self._conn.cursor()
        cur.execute("SELECT task_id FROM ipconflict_deep_tasks"
                    " WHERE terminal_id=? ORDER BY id DESC LIMIT 1",
                    (str(terminal_id or ""),))
        row = cur.fetchone()
        cur.close()
        if not row:
            return None
        tid = row["task_id"] if isinstance(row, dict) else row[0]
        return self.deep_task_get(tid)

    def _ipconflict_scan(self, ip, since, terminal_id, my_key):
        """交叉判定公共扫描（report/lookup 共用）：同 IP 窗口内按
        (terminal_id, 归一 MAC) 归并 → foreign（其它终端）/nic_history。"""
        cur = self._conn.cursor()
        cur.execute("SELECT terminal_id, mac, MAX(created_ts) AS last_ts"
                    " FROM ipconflict_reports WHERE ip=? AND created_ts>=?"
                    " GROUP BY terminal_id, mac", (ip, since))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        # 按 (terminal_id, 归一 MAC) 二次归并：同一网卡以不同格式入库
        # （大小写/分隔符差异）合并为一条，保留最近一次上报的原始格式
        merged = {}
        for r in rows:
            key = (r["terminal_id"], self._mac_key(r["mac"]))
            if key not in merged or (r["last_ts"] or 0) > (merged[key]["last_ts"] or 0):
                merged[key] = r
        foreign, nic_history = [], []
        for r in merged.values():
            if r["terminal_id"] == terminal_id and self._mac_key(r["mac"]) == my_key:
                continue  # 本次上报自身（含异格式同键）
            if r["terminal_id"] == terminal_id:
                nic_history.append({"mac": r["mac"], "last_ts": r["last_ts"]})
            else:
                foreign.append({"terminal_id": r["terminal_id"], "mac": r["mac"],
                                "last_ts": r["last_ts"],
                                "known": self.get_terminal(r["terminal_id"])
                                is not None})
        return foreign, nic_history

    # ------------------------------------------------------------------
    # metrics
    # ------------------------------------------------------------------

    def insert_metric(self, terminal_id, ts, snapshot):
        """插入一条指标快照。返回 (metric_id, cpu_percent, mem_available_percent)。
        mem_available_percent 同时返回，供上层补内存饱和判定冗余信息。"""
        cpu = snapshot.get("cpu") or {}
        mem = snapshot.get("mem") or {}
        swap = snapshot.get("swap") or {}
        cpu_percent = cpu.get("percent")
        mem_percent = mem.get("used_percent")
        mem_avail = mem.get("available_percent")
        if mem_avail is None and mem_percent is not None:
            mem_avail = 100.0 - mem_percent
        swap_percent = swap.get("used_percent")
        disks = snapshot.get("disks") or []
        volumes = snapshot.get("volumes") or []
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO metrics(terminal_id, ts, cpu_percent, mem_percent,"
                " mem_available_percent, swap_percent, disks_json, volumes_json, raw_json)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (terminal_id, ts, cpu_percent, mem_percent, mem_avail, swap_percent,
                 json.dumps(disks, ensure_ascii=False),
                 json.dumps(volumes, ensure_ascii=False),
                 json.dumps(snapshot, ensure_ascii=False)))
            self._conn.commit()
            mid = cur.lastrowid
            cur.close()
        return mid, cpu_percent, mem_avail

    def query_metrics(self, terminal_id, since_ts, until_ts=None):
        until_ts = until_ts if until_ts is not None else int(time.time())
        cur = self._conn.cursor()
        cur.execute(
            "SELECT ts, cpu_percent, mem_percent, mem_available_percent, swap_percent,"
            " disks_json FROM metrics WHERE terminal_id=? AND ts>=? AND ts<=?"
            " ORDER BY ts ASC", (terminal_id, since_ts, until_ts))
        rows = cur.fetchall()
        cur.close()
        out = []
        for r in rows:
            out.append({
                "ts": r["ts"],
                "cpu_percent": r["cpu_percent"],
                "mem_percent": r["mem_percent"],
                "mem_available_percent": r["mem_available_percent"],
                "swap_percent": r["swap_percent"],
                "disks": _loads(r["disks_json"], []),
            })
        return out

    def latest_metrics_all(self, since_ts=0):
        """每台终端最近一条指标（按 MAX(id) 取最新，避免同 ts 重影）。

        仅返回 ts>=since_ts 的记录：陈旧指标不计入「当前负载」口径。
        只读查询（与 list_events / metrics_timeline 同为无锁读路径）。"""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT m.terminal_id, m.ts, m.cpu_percent, m.mem_percent,"
            " m.swap_percent FROM metrics m"
            " JOIN (SELECT terminal_id, MAX(id) AS mid FROM metrics"
            "       GROUP BY terminal_id) t ON m.id = t.mid"
            " WHERE m.ts >= ? ORDER BY m.terminal_id", (int(since_ts),))
        rows = cur.fetchall()
        cur.close()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------

    def insert_event(self, terminal_id, ts, level, category, message, detail):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO events(terminal_id, ts, level, category, message, detail_json)"
                " VALUES(?,?,?,?,?,?)",
                (terminal_id, ts, level, category, message,
                 json.dumps(detail or {}, ensure_ascii=False)))
            self._conn.commit()
            eid = cur.lastrowid
            cur.close()
        return eid

    def list_events(self, limit=100, terminal_id=None, since_ts=None):
        sql = "SELECT id, terminal_id, ts, level, category, message, detail_json" \
              " FROM events WHERE 1=1"
        args = []
        if terminal_id:
            sql += " AND terminal_id=?"
            args.append(terminal_id)
        if since_ts:
            sql += " AND ts>=?"
            args.append(since_ts)
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        args.append(int(limit))
        cur = self._conn.cursor()
        cur.execute(sql, args)
        rows = cur.fetchall()
        cur.close()
        for r in rows:
            r["detail"] = _loads(r.pop("detail_json"), {})
        return rows

    # ------------------------------------------------------------------
    # bottlenecks
    # ------------------------------------------------------------------

    def last_bottleneck_ts(self, terminal_id, kind, metric_key):
        """同终端同规则最近一次记录时间（去重窗口用）。无记录返回 None。"""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT MAX(ts) AS last_ts FROM bottlenecks"
            " WHERE terminal_id=? AND kind=? AND metric_key=?",
            (terminal_id, kind, metric_key))
        row = cur.fetchone()
        cur.close()
        if not row or row["last_ts"] is None:
            return None
        return row["last_ts"]

    def insert_bottleneck(self, terminal_id, ts, kind, metric_key, value,
                          threshold, level, detail):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO bottlenecks(terminal_id, ts, kind, metric_key, value,"
                " threshold, level, detail_json, acknowledged) VALUES(?,?,?,?,?,?,?,?,0)",
                (terminal_id, ts, kind, metric_key, value, threshold, level,
                 json.dumps(detail or {}, ensure_ascii=False)))
            self._conn.commit()
            bid = cur.lastrowid
            cur.close()
        return bid

    def list_bottlenecks(self, limit=100, terminal_id=None, since_ts=None):
        sql = "SELECT id, terminal_id, ts, kind, metric_key, value, threshold," \
              " level, detail_json, acknowledged FROM bottlenecks WHERE 1=1"
        args = []
        if terminal_id:
            sql += " AND terminal_id=?"
            args.append(terminal_id)
        if since_ts:
            sql += " AND ts>=?"
            args.append(since_ts)
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        args.append(int(limit))
        cur = self._conn.cursor()
        cur.execute(sql, args)
        rows = cur.fetchall()
        cur.close()
        for r in rows:
            r["detail"] = _loads(r.pop("detail_json"), {})
            r["acknowledged"] = bool(r["acknowledged"])
        return rows

    def ack_bottleneck(self, bottleneck_id):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("UPDATE bottlenecks SET acknowledged=1 WHERE id=?",
                        (bottleneck_id,))
            self._conn.commit()
            found = cur.rowcount > 0
            cur.close()
            return found

    # ------------------------------------------------------------------
    # settings（key-value 运行时配置；masked=1 表示 value 为密文）
    # ------------------------------------------------------------------

    def settings_get(self, key):
        cur = self._conn.cursor()
        cur.execute("SELECT key, value, masked FROM settings WHERE key=?", (key,))
        row = cur.fetchone()
        cur.close()
        return row

    def settings_set(self, key, value, masked):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO settings(key, value, masked, updated_at) VALUES(?,?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                " masked=excluded.masked, updated_at=excluded.updated_at",
                (key, value, int(masked), int(time.time())))
            self._conn.commit()
            cur.close()

    def settings_delete(self, key):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM settings WHERE key=?", (key,))
            self._conn.commit()
            cur.close()

    def settings_list(self):
        cur = self._conn.cursor()
        cur.execute("SELECT key, value, masked, updated_at FROM settings ORDER BY key")
        rows = [(r["key"], r["value"], r["masked"], r["updated_at"])
                for r in cur.fetchall()]
        cur.close()
        return rows

    # ------------------------------------------------------------------
    # whitelist（终端准入白名单，IP/CIDR）
    # ------------------------------------------------------------------

    def whitelist_add(self, cidr, note, now=None):
        now = now if now is not None else int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(
                    "INSERT INTO whitelist(cidr, note, enabled, created_at)"
                    " VALUES(?,?,1,?)", (cidr, note, now))
                self._conn.commit()
                wid = cur.lastrowid
            except sqlite3.IntegrityError:
                wid = None
            cur.close()
        return wid

    def whitelist_list(self):
        cur = self._conn.cursor()
        cur.execute("SELECT id, cidr, note, enabled, created_at FROM whitelist"
                    " ORDER BY id ASC")
        rows = cur.fetchall()
        cur.close()
        for r in rows:
            r["enabled"] = bool(r["enabled"])
        return rows

    def whitelist_delete(self, entry_id):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM whitelist WHERE id=?", (entry_id,))
            self._conn.commit()
            deleted = cur.rowcount > 0
            cur.close()
        return deleted

    # ------------------------------------------------------------------
    # VLAN 权威知识库（ADR-044 增补：钉钉表格灌入 + 三级检索梯子 L1 数据源）
    # ------------------------------------------------------------------

    def vlan_kb_list(self):
        cur = self._conn.cursor()
        cur.execute("SELECT id, cidr, vlan_id, zone_desc, source,"
                    " updated_ts FROM vlan_kb ORDER BY id ASC")
        rows = cur.fetchall()
        cur.close()
        return [dict(r) for r in rows]

    def vlan_kb_upsert_many(self, items, now=None):
        """批量幂等导入：cidr 唯一键 upsert。返回 {"added": n1, "updated": n2}。"""
        now = now if now is not None else int(time.time())
        added = updated = 0
        with self._lock:
            cur = self._conn.cursor()
            for it in items:
                cidr = str(it.get("cidr") or "").strip()
                if not cidr:
                    continue
                vlan_id = str(it.get("vlan_id") or "").strip()
                zone = str(it.get("zone_desc") or "").strip()
                source = str(it.get("source") or "").strip()
                row = cur.execute("SELECT id FROM vlan_kb WHERE cidr=?",
                                  (cidr,)).fetchone()
                if row:
                    cur.execute("UPDATE vlan_kb SET vlan_id=?, zone_desc=?,"
                                " source=?, updated_ts=? WHERE id=?",
                                (vlan_id, zone, source, now, row["id"]))
                    updated += 1
                else:
                    cur.execute("INSERT INTO vlan_kb(cidr, vlan_id,"
                                " zone_desc, source, updated_ts)"
                                " VALUES(?,?,?,?,?)",
                                (cidr, vlan_id, zone, source, now))
                    added += 1
            self._conn.commit()
            cur.close()
        return {"added": added, "updated": updated}

    def vlan_kb_delete(self, entry_id):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM vlan_kb WHERE id=?", (entry_id,))
            self._conn.commit()
            deleted = cur.rowcount > 0
            cur.close()
        return deleted

    def whitelist_toggle(self, entry_id, enabled):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("UPDATE whitelist SET enabled=? WHERE id=?",
                        (1 if enabled else 0, entry_id))
            self._conn.commit()
            found = cur.rowcount > 0
            cur.close()
        return found

    # ------------------------------------------------------------------
    # audit_log（准入拒绝等安全审计）
    # ------------------------------------------------------------------

    def audit(self, ip, path, action, reason):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO audit_log(ts, ip, path, action, reason)"
                " VALUES(?,?,?,?,?)",
                (int(time.time()), ip or "", path or "", action or "",
                 reason or ""))
            self._conn.commit()
            cur.close()

    def list_audit(self, limit=100):
        cur = self._conn.cursor()
        cur.execute("SELECT id, ts, ip, path, action, reason"
                    " FROM audit_log ORDER BY ts DESC, id DESC LIMIT ?",
                    (int(limit),))
        rows = cur.fetchall()
        cur.close()
        return rows

    # ------------------------------------------------------------------
    # upload_files（日志/文件上传登记）
    # ------------------------------------------------------------------

    def insert_upload(self, terminal_id, filename, size, sha256, path, source, ts):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO upload_files(terminal_id, filename, size, sha256,"
                " path, source, ts) VALUES(?,?,?,?,?,?,?)",
                (terminal_id or "", filename, int(size or 0), sha256 or "",
                 path or "", source, int(ts)))
            self._conn.commit()
            uid = cur.lastrowid
            cur.close()
        return uid

    def list_uploads(self, limit=100, terminal_id=None):
        sql = "SELECT id, terminal_id, filename, size, sha256, path, source, ts" \
              " FROM upload_files WHERE 1=1"
        args = []
        if terminal_id:
            sql += " AND terminal_id=?"
            args.append(terminal_id)
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        args.append(int(limit))
        cur = self._conn.cursor()
        cur.execute(sql, args)
        rows = cur.fetchall()
        cur.close()
        return rows

    def known_upload_names(self):
        cur = self._conn.cursor()
        cur.execute("SELECT filename, size FROM upload_files")
        rows = set((r["filename"], r["size"]) for r in cur.fetchall())
        cur.close()
        return rows

    # ------------------------------------------------------------------
    # commands（心跳命令通道，状态机见 ADR-015）
    # ------------------------------------------------------------------

    def enqueue_command(self, terminal_id, command, args, timeout_sec=120,
                        source="console", now=None):
        now = now if now is not None else int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO commands(terminal_id, command, args_json, status,"
                " timeout_sec, source, created_ts) VALUES(?,?,?,?,?,?,?)",
                (terminal_id, command, json.dumps(args or {}, ensure_ascii=False),
                 "pending", int(timeout_sec), source, now))
            self._conn.commit()
            cid = cur.lastrowid
            cur.close()
        return cid

    def take_pending_commands(self, terminal_id, limit=10, now=None):
        """心跳时取走 pending 命令并置 sent（单次下发语义）。"""
        now = now if now is not None else int(time.time())
        out = []
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT id, command, args_json, timeout_sec FROM commands"
                " WHERE terminal_id=? AND status='pending' ORDER BY id ASC LIMIT ?",
                (terminal_id, int(limit)))
            rows = cur.fetchall()
            for r in rows:
                cur.execute("UPDATE commands SET status='sent', sent_ts=? WHERE id=?",
                            (now, r["id"]))
                out.append({"id": r["id"], "command": r["command"],
                            "args": _loads(r["args_json"], {}),
                            "timeout_sec": r["timeout_sec"]})
            self._conn.commit()
            cur.close()
        return out

    def complete_command(self, terminal_id, command_id, ok, result):
        """终端回执执行结果。仅 sent 状态可完结。"""
        now = int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE commands SET status=?, done_ts=?, result_json=? WHERE"
                " id=? AND terminal_id=? AND status='sent'",
                ("executed" if ok else "failed", now,
                 json.dumps(result or {}, ensure_ascii=False),
                 int(command_id), terminal_id))
            self._conn.commit()
            updated = cur.rowcount > 0
            cur.close()
        return updated

    def expire_commands(self, now=None):
        """惰性超时：sent 超过 timeout_sec 未回执 → timeout；
        pending 超过 timeout_sec+300 无终端领取 → timeout。返回超时条数。"""
        now = now if now is not None else int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE commands SET status='timeout', done_ts=? WHERE"
                " status='sent' AND sent_ts IS NOT NULL"
                " AND sent_ts + timeout_sec < ?", (now, now))
            n1 = cur.rowcount
            cur.execute(
                "UPDATE commands SET status='timeout', done_ts=? WHERE"
                " status='pending' AND created_ts + timeout_sec + 300 < ?",
                (now, now))
            n2 = cur.rowcount
            self._conn.commit()
            cur.close()
        return n1 + n2

    def list_commands(self, terminal_id=None, limit=100):
        sql = "SELECT id, terminal_id, command, args_json, status, timeout_sec,"               " source, created_ts, sent_ts, done_ts, result_json FROM commands"
        args = []
        if terminal_id:
            sql += " WHERE terminal_id=?"
            args.append(terminal_id)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        cur = self._conn.cursor()
        cur.execute(sql, args)
        rows = cur.fetchall()
        cur.close()
        for r in rows:
            r["args"] = _loads(r.pop("args_json"), {})
            r["result"] = _loads(r.pop("result_json"), {})
        return rows

    def get_command(self, terminal_id, command_id):
        cur = self._conn.cursor()
        cur.execute("SELECT id, terminal_id, command, args_json, status,"
                    " timeout_sec, source, created_ts, sent_ts, done_ts,"
                    " result_json FROM commands WHERE id=? AND terminal_id=?",
                    (int(command_id), terminal_id))
        row = cur.fetchone()
        cur.close()
        if row:
            row["args"] = _loads(row.pop("args_json"), {})
            row["result"] = _loads(row.pop("result_json"), {})
        return row

    # ------------------------------------------------------------------
    # iperf_tasks（打流任务）
    # ------------------------------------------------------------------

    def iperf_insert(self, task_id, terminal_id, test_type, port, status,
                     created_ts, finished_ts, result, log_text, command_id=None):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO iperf_tasks(task_id, terminal_id, test_type, port,"
                " status, created_ts, finished_ts, result_json, log_text, command_id)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (task_id, terminal_id, test_type, int(port), status,
                 int(created_ts), finished_ts,
                 json.dumps(result or {}, ensure_ascii=False), log_text,
                 command_id))
            self._conn.commit()
            cur.close()

    def iperf_running_ports(self):
        cur = self._conn.cursor()
        cur.execute("SELECT port FROM iperf_tasks WHERE status='running'")
        rows = cur.fetchall()
        cur.close()
        return rows

    def iperf_get(self, task_id):
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM iperf_tasks WHERE task_id=?", (task_id,))
        row = cur.fetchone()
        cur.close()
        if row:
            row["result"] = _loads(row.pop("result_json"), {})
        return row

    def iperf_finish(self, task_id, status, result, log_tail):
        now = int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE iperf_tasks SET status=?, finished_ts=?, result_json=?,"
                " log_text=substr(log_text || char(10) || ?, -8000)"
                " WHERE task_id=?",
                (status, now, json.dumps(result or {}, ensure_ascii=False),
                 log_tail or "", task_id))
            self._conn.commit()
            cur.close()

    def iperf_set_log(self, task_id, log_text):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("UPDATE iperf_tasks SET log_text=? WHERE task_id=?",
                        (log_text or "", task_id))
            self._conn.commit()
            cur.close()

    def iperf_list(self, limit=50, terminal_id=None):
        sql = "SELECT task_id, terminal_id, test_type, port, status, created_ts," \
              " finished_ts, result_json, log_text FROM iperf_tasks"
        args = []
        if terminal_id:
            sql += " WHERE terminal_id=?"
            args.append(terminal_id)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        cur = self._conn.cursor()
        cur.execute(sql, args)
        rows = cur.fetchall()
        cur.close()
        for r in rows:
            r["result"] = _loads(r.pop("result_json"), {})
        return rows

    # ------------------------------------------------------------------
    # ai_analyses（AI 智能分析记录）
    # ------------------------------------------------------------------

    def ai_insert(self, terminal_id, ts, trigger, issue, context, response_text,
                  status, error, model, duration_ms):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO ai_analyses(terminal_id, ts, trigger, issue,"
                " context_json, response_text, status, error, model, duration_ms)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (terminal_id, int(ts), trigger, issue or "",
                 json.dumps(context or {}, ensure_ascii=False),
                 response_text or "", status, error or "", model or "",
                 int(duration_ms)))
            self._conn.commit()
            aid = cur.lastrowid
            cur.close()
        return aid

    def ai_get(self, analysis_id):
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM ai_analyses WHERE id=?", (int(analysis_id),))
        row = cur.fetchone()
        cur.close()
        if row:
            row["context"] = _loads(row.pop("context_json"), {})
        return row

    def ai_list(self, limit=50, terminal_id=None):
        sql = "SELECT id, terminal_id, ts, trigger, issue, response_text," \
              " status, error, model, duration_ms FROM ai_analyses"
        args = []
        if terminal_id:
            sql += " WHERE terminal_id=?"
            args.append(terminal_id)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        cur = self._conn.cursor()
        cur.execute(sql, args)
        rows = cur.fetchall()
        cur.close()
        return rows

    # ------------------------------------------------------------------
    # terminal_tokens（终端接入 token 多实例维护；config token 兼容迁移）
    # ------------------------------------------------------------------

    def token_ensure_default(self, token):
        now = int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT OR IGNORE INTO terminal_tokens(token, label, status,"
                " created_ts, updated_ts) VALUES(?, 'default', 'active', ?, ?)",
                (token, now, now))
            self._conn.commit()
            cur.close()

    def token_list(self):
        cur = self._conn.cursor()
        cur.execute("SELECT id, token, label, status, created_ts, updated_ts,"
                    " last_used_ts FROM terminal_tokens ORDER BY id ASC")
        rows = cur.fetchall()
        cur.close()
        return rows

    def token_create(self, label):
        """生成并登记新 token（secrets.token_hex(24)，48 位十六进制）。"""
        now = int(time.time())
        token = secrets.token_hex(24)
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO terminal_tokens(token, label, status, created_ts,"
                " updated_ts) VALUES(?,?,'active',?,?)",
                (token, label or "", now, now))
            tid = cur.lastrowid
            self._conn.commit()
            cur.close()
        return {"id": tid, "token": token, "label": label or "",
                "status": "active", "created_ts": now, "updated_ts": now,
                "last_used_ts": None}

    def token_get_active(self, token):
        """按 token 值查启用中的条目（上行鉴权热路径）。"""
        cur = self._conn.cursor()
        cur.execute("SELECT id, last_used_ts FROM terminal_tokens"
                    " WHERE token=? AND status='active'", (token,))
        row = cur.fetchone()
        cur.close()
        return row

    def token_touch_last_used(self, token_id, last_used_ts, now=None):
        """节流更新最近使用时间（60 秒内不重复写，避免高频心跳刷库）。"""
        now = now if now is not None else int(time.time())
        if last_used_ts is not None and now - int(last_used_ts) < 60:
            return False
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("UPDATE terminal_tokens SET last_used_ts=? WHERE id=?",
                        (now, int(token_id)))
            self._conn.commit()
            cur.close()
        return True

    def token_rotate(self, token_id):
        """轮换：生成新 token 替换该条（status 保持）。返回新 token 或 None。"""
        now = int(time.time())
        token = secrets.token_hex(24)
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE terminal_tokens SET token=?, updated_ts=? WHERE id=?",
                (token, now, int(token_id)))
            self._conn.commit()
            found = cur.rowcount > 0
            cur.close()
        return token if found else None

    def token_set_status(self, token_id, status):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE terminal_tokens SET status=?, updated_ts=? WHERE id=?",
                (status, int(time.time()), int(token_id)))
            self._conn.commit()
            found = cur.rowcount > 0
            cur.close()
        return found

    # ------------------------------------------------------------------
    # switches（交换机台账，ADR-026；password_enc 为 SecretsBox 密文）
    # ------------------------------------------------------------------

    def switch_list(self):
        cur = self._conn.cursor()
        cur.execute(
            "SELECT id, name, ip, ssh_port, username, brand, created_ts,"
            " updated_ts, (password_enc IS NOT NULL AND password_enc != '')"
            " AS password_set FROM switches ORDER BY id ASC")
        rows = cur.fetchall()
        cur.close()
        return rows

    def switch_get(self, sid):
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM switches WHERE id=?", (int(sid),))
        row = cur.fetchone()
        cur.close()
        return row

    def switch_find_by_ip(self, ip):
        """按设备 IP 查台账条目（内部用：含 password_enc 密文，零回显）。

        返回 dict(username, password_enc, ssh_port, name) 或 None。"""
        cur = self._conn.cursor()
        cur.execute("SELECT username, password_enc, ssh_port, name"
                    " FROM switches WHERE ip=?", (str(ip or "").strip(),))
        row = cur.fetchone()
        cur.close()
        if not row:
            return None
        return dict(row)

    def switch_create(self, name, ip, ssh_port, username, password_enc,
                      brand):
        now = int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO switches(name, ip, ssh_port, username,"
                " password_enc, brand, created_ts, updated_ts)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (name, ip, int(ssh_port), username, password_enc or None,
                 brand or "", now, now))
            self._conn.commit()
            sid = cur.lastrowid
            cur.close()
        return sid

    def switch_update(self, sid, fields):
        """fields 白名单由 API 层控制；password_enc 键存在时才更新密码。"""
        allowed = {"name", "ip", "ssh_port", "username", "password_enc",
                   "brand"}
        sets, args = [], []
        for key, value in fields.items():
            if key not in allowed:
                continue
            sets.append("%s=?" % key)
            args.append(int(value) if key == "ssh_port" else value)
        if not sets:
            return False
        sets.append("updated_ts=?")
        args.extend([int(time.time()), int(sid)])
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("UPDATE switches SET %s WHERE id=?" % ",".join(sets),
                        args)
            self._conn.commit()
            found = cur.rowcount > 0
            cur.close()
        return found

    def switch_delete(self, sid):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM switches WHERE id=?", (int(sid),))
            self._conn.commit()
            found = cur.rowcount > 0
            cur.close()
        return found

    # ------------------------------------------------------------------
    # third_party_apis（第三方接口登记）
    # ------------------------------------------------------------------

    def third_party_list(self):
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM third_party_apis ORDER BY id ASC")
        rows = cur.fetchall()
        cur.close()
        for r in rows:
            r["enabled"] = bool(r["enabled"])
        return rows

    def third_party_create(self, name, base_url, method, params_json,
                           headers_json, note):
        now = int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO third_party_apis(name, base_url, method,"
                " params_json, headers_json, enabled, note, created_ts,"
                " updated_ts) VALUES(?,?,?,?,?,1,?,?,?)",
                (name, base_url, method, params_json, headers_json, note,
                 now, now))
            tpid = cur.lastrowid
            self._conn.commit()
            cur.close()
        return tpid

    def third_party_get(self, tpid):
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM third_party_apis WHERE id=?", (int(tpid),))
        row = cur.fetchone()
        cur.close()
        if row:
            row["enabled"] = bool(row["enabled"])
        return row

    def third_party_update(self, tpid, fields):
        """按白名单字段更新（列名已在上层校验）。返回条目是否存在。"""
        allowed = ("name", "base_url", "method", "params_json",
                   "headers_json", "note")
        sets = [k for k in fields.keys() if k in allowed]
        if not sets:
            return True  # 无可更新字段视为成功（上层已拦空更新）
        args = [fields[k] for k in sets] + [int(time.time()), int(tpid)]
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE third_party_apis SET " + ", ".join(
                    "%s=?" % k for k in sets) + ", updated_ts=? WHERE id=?",
                args)
            self._conn.commit()
            found = cur.rowcount > 0
            cur.close()
        return found

    def third_party_delete(self, tpid):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM third_party_apis WHERE id=?", (int(tpid),))
            self._conn.commit()
            found = cur.rowcount > 0
            cur.close()
        return found

    def third_party_toggle(self, tpid, enabled):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE third_party_apis SET enabled=?, updated_ts=? WHERE id=?",
                (1 if enabled else 0, int(time.time()), int(tpid)))
            self._conn.commit()
            found = cur.rowcount > 0
            cur.close()
        return found

    # ------------------------------------------------------------------
    # hr_groups / hr_clients / hr_sync_log（火绒终端安全镜像，ADR-033）
    # 控制台只读缓存，绝不透传火绒实时请求；镜像由 huorong.HuorongSyncer
    # 全量整体替换（单事务，天然幂等）
    # ------------------------------------------------------------------

    def hr_replace_snapshot(self, groups, clients):
        """整体替换镜像快照（单事务；火绒侧删除的分组/终端同步消失）。"""
        now = int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM hr_groups")
            cur.execute("DELETE FROM hr_clients")
            cur.executemany(
                "INSERT OR REPLACE INTO hr_groups(group_id, name, parent,"
                " total, online, updated_at) VALUES(?,?,?,?,?,?)",
                [(g["group_id"], g["name"], g["parent"], g.get("total", 0),
                  g.get("online", 0), g.get("updated_at", now))
                 for g in groups])
            cur.executemany(
                "INSERT OR REPLACE INTO hr_clients(client_id, name,"
                " computer_name, ip, connect_ip, mac, group_id,"
                " native_group_id, online, os, version, last_seen,"
                " first_seen, last_off, this_on,"
                " updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(c["client_id"], c["name"], c["computer_name"], c["ip"],
                  c["connect_ip"], c["mac"], c["group_id"],
                  c.get("native_group_id", c["group_id"]), c["online"],
                  c["os"], c["version"], c["last_seen"],
                  c.get("first_seen"), c.get("last_off"), c.get("this_on"),
                  c.get("updated_at", now)) for c in clients])
            self._conn.commit()
            cur.close()

    def hr_assets_replace(self, assets_map, now):
        """登记信息整体替换（单事务；火绒侧未返回登记的终端自然消失）。

        assets_map: {client_id: {登记字段名: 值}}（键名由火绒控制台配置，
        无固定 API 字段名，全量保留原始键，ADR-033 增补三）。"""
        now = int(now or time.time())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM hr_client_assets")
            cur.executemany(
                "INSERT OR REPLACE INTO hr_client_assets(client_id,"
                " assets_json, updated_at) VALUES(?,?,?)",
                [(cid, json.dumps(m, ensure_ascii=False), now)
                 for cid, m in (assets_map or {}).items() if m])
            self._conn.commit()
            cur.close()

    def hr_assets_get(self, client_id):
        """终端登记信息 dict；未登记（无行/空对象）返回 None。"""
        cur = self._conn.cursor()
        cur.execute("SELECT assets_json FROM hr_client_assets WHERE client_id=?",
                    (str(client_id or ""),))
        row = cur.fetchone()
        cur.close()
        if row is None:
            return None
        data = _loads(row["assets_json"], {})
        return data or None

    def hr_client_get(self, client_id):
        """单个火绒终端镜像行（开关机任务「火绒资产源」目标解析用）。

        返回含 mac / ip / connect_ip / group_id / online 的 dict（不存在返回
        None）—— mac 与 ip 是 WoL 目标解析的必需项。"""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT c.client_id, c.name, c.computer_name, c.ip, c.connect_ip,"
            " c.mac, c.group_id, g.name AS group_name, c.online, c.os,"
            " c.version, c.last_seen"
            " FROM hr_clients c LEFT JOIN hr_groups g"
            " ON g.group_id = c.group_id WHERE c.client_id=?",
            (str(client_id or ""),))
        row = cur.fetchone()
        cur.close()
        return row

    def hr_clients_wol_targets(self, hr_group_id=None):
        """火绒终端（WoL 目标候选）：按火绒组过滤，附「可唤醒」判定。

        wol_capable = 有 MAC 且有 IP（WoL 硬约束：缺一不可）。
        hr_group_id=None 表示不限组（火绒源根组语义）。"""
        where, args = [], []
        if hr_group_id is not None:
            where.append("c.group_id=?")
            args.append(int(hr_group_id))
        cond = (" WHERE " + " AND ".join(where)) if where else ""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT c.client_id, c.name, c.computer_name, c.ip, c.connect_ip,"
            " c.mac, c.group_id, g.name AS group_name, c.online"
            " FROM hr_clients c LEFT JOIN hr_groups g ON g.group_id=c.group_id"
            + cond + " ORDER BY c.group_id, c.name", args)
        rows = cur.fetchall()
        cur.close()
        out = []
        for r in rows:
            d = dict(r)
            d["wol_capable"] = bool(str(d.get("mac") or "").strip()
                                    and str(d.get("ip")
                                            or d.get("connect_ip") or "").strip())
            out.append(d)
        return out

    # ------------------------------------------------------------------
    # 画方准入（NAD）终端镜像（ADR-047 批 B）
    # ------------------------------------------------------------------

    def nad_replace_snapshot(self, rows):
        """整体替换 NAD 终端快照（单事务幂等；NAD 侧删除的终端同步消失）。"""
        now = int(time.time())
        clean = [r for r in (rows or []) if (r or {}).get("oid")]
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM nad_terminals")
            cur.executemany(
                "INSERT OR REPLACE INTO nad_terminals(oid, name, mac,"
                " ips_json, group_path, online, owner, synced_ts)"
                " VALUES(?,?,?,?,?,?,?,?)",
                [(str(r.get("oid")), str(r.get("name") or ""),
                  str(r.get("mac") or ""),
                  json.dumps(r.get("ips") or [], ensure_ascii=False),
                  str(r.get("group_path") or ""),
                  1 if r.get("online") else 0,
                  str(r.get("owner") or ""), now) for r in clean])
            self._conn.commit()
            cur.close()
        return len(clean)

    def nad_terminal_get(self, oid):
        """单个 NAD 终端（WoL 目标解析用）；不存在返回 None。"""
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM nad_terminals WHERE oid=?",
                    (str(oid or ""),))
        row = cur.fetchone()
        cur.close()
        if row is None:
            return None
        out = dict(row)
        out["ips"] = _loads(out.pop("ips_json", "[]"), [])
        out["wol_capable"] = bool(out.get("mac") and out.get("ips"))
        return out

    def nad_terminals_page(self, q=None, group_path=None,
                           only_wol_capable=False, page=1, page_size=50):
        """NAD 终端分页（任务向导第 3 步目标选择用）。

        only_wol_capable=True 只返回「有 MAC 且有 IP」的终端——WoL 硬约束：
        无 MAC 组不出魔术包、无 IP 派生不出广播地址，只能禁选并标注原因。"""
        where, args = [], []
        if group_path:
            where.append("group_path=?")
            args.append(str(group_path))
        if only_wol_capable:
            where.append("mac<>'' AND ips_json NOT IN ('[]','')")
        if q:
            like = "%" + str(q).replace("\\", "\\\\").replace("%", "\\%") \
                .replace("_", "\\_") + "%"
            where.append("(name LIKE ? ESCAPE '\\' OR oid LIKE ? ESCAPE '\\'"
                         " OR mac LIKE ? ESCAPE '\\'"
                         " OR ips_json LIKE ? ESCAPE '\\')")
            args.extend([like] * 4)
        cond = (" WHERE " + " AND ".join(where)) if where else ""
        cur = self._conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM nad_terminals" + cond, args)
        total = cur.fetchone()["n"]
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 200))
        cur.execute(
            "SELECT * FROM nad_terminals" + cond
            + " ORDER BY group_path, name LIMIT ? OFFSET ?",
            args + [page_size, (page - 1) * page_size])
        rows = cur.fetchall()
        cur.close()
        items = []
        for r in rows:
            d = dict(r)
            d["ips"] = _loads(d.pop("ips_json", "[]"), [])
            d["wol_capable"] = bool(d.get("mac") and d.get("ips"))
            items.append(d)
        return {"total": total, "page": page, "page_size": page_size,
                "items": items}

    def nad_groups_summary(self):
        """NAD 终端按归属路径聚合（任务向导第 3 步分源树用）。"""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT group_path, COUNT(*) AS total,"
            " SUM(CASE WHEN mac<>'' AND ips_json NOT IN ('[]','')"
            "     THEN 1 ELSE 0 END) AS wol_capable"
            " FROM nad_terminals GROUP BY group_path ORDER BY group_path")
        rows = cur.fetchall()
        cur.close()
        return [dict(r) for r in rows]

    def nad_sync_ts(self):
        """最近一次 NAD 快照同步时间（0=从未同步）。"""
        cur = self._conn.cursor()
        cur.execute("SELECT MAX(synced_ts) AS ts FROM nad_terminals")
        row = cur.fetchone()
        cur.close()
        return int((row or {}).get("ts") or 0)

    def hr_overview(self):
        """概览 KPI：分组数/终端总数/在线数/在线率/Win7 EOL/最近成功同步。"""
        cur = self._conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM hr_groups")
        groups_count = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n, COALESCE(SUM(online),0) AS onl"
                    " FROM hr_clients")
        row = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS n FROM hr_clients"
                    " WHERE os LIKE '%Windows 7%'")
        win7 = cur.fetchone()["n"]
        cur.execute("SELECT MAX(finished_at) AS ts FROM hr_sync_log WHERE ok=1")
        last = cur.fetchone()["ts"]
        cur.close()
        total = row["n"]
        online = row["onl"]
        return {"groups_count": groups_count, "clients_total": total,
                "online": online,
                "online_rate": round(online / float(total), 2) if total else 0.0,
                "win7_eol_count": win7, "last_sync": last or 0}

    def hr_groups_list(self):
        """火绒分组列表（total/online 按**分组覆盖应用后**实时聚合，
        与统一视图/终端列表口径一致；镜像列仅作火绒原生快照）。"""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT g.group_id AS id, g.name, g.parent,"
            " COALESCE(s.total, 0) AS total, COALESCE(s.online, 0) AS online"
            " FROM hr_groups g LEFT JOIN ("
            "   SELECT c.group_id AS gid, COUNT(*) AS total,"
            "          COALESCE(SUM(c.online), 0) AS online"
            "   FROM hr_clients c GROUP BY c.group_id) s"
            " ON s.gid = g.group_id ORDER BY g.group_id ASC")
        rows = cur.fetchall()
        cur.close()
        return rows

    def hr_clients_page(self, group_id=None, online=None, q=None,
                        page=1, page_size=50):
        """分页终端（真实 IP/MAC 不脱敏，控制台为可信管理员界面）。

        q 按 name/computer_name/ip/mac 子串匹配（LIKE 特殊字符转义）。"""
        where, args = [], []
        if group_id is not None:
            where.append("c.group_id=?")
            args.append(int(group_id))
        if online in (0, 1):
            where.append("c.online=?")
            args.append(int(online))
        if q:
            like = "%" + q.replace("\\", "\\\\").replace("%", "\\%") \
                .replace("_", "\\_") + "%"
            where.append("(c.name LIKE ? ESCAPE '\\' OR c.computer_name"
                         " LIKE ? ESCAPE '\\' OR c.ip LIKE ? ESCAPE '\\'"
                         " OR c.mac LIKE ? ESCAPE '\\')")
            args.extend([like, like, like, like])
        cond = (" WHERE " + " AND ".join(where)) if where else ""
        cur = self._conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM hr_clients c" + cond, args)
        total = cur.fetchone()["n"]
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 200))
        cur.execute(
            "SELECT c.client_id, c.name, c.computer_name, c.ip, c.connect_ip,"
            " c.mac, c.group_id, g.name AS group_name, c.online, c.os,"
            " c.version, c.last_seen"
            " FROM hr_clients c LEFT JOIN hr_groups g"
            " ON g.group_id = c.group_id" + cond +
            " ORDER BY c.group_id, c.name, c.client_id LIMIT ? OFFSET ?",
            args + [page_size, (page - 1) * page_size])
        rows = cur.fetchall()
        cur.close()
        return {"total": total, "page": page, "page_size": page_size,
                "clients": rows}

    def hr_sync_log_add(self, started_at, finished_at, ok, trigger,
                        groups_n, clients_n, matched_n, error):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO hr_sync_log(started_at, finished_at, ok, trigger,"
                " groups_n, clients_n, matched_n, error)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (int(started_at), int(finished_at), int(ok), trigger or "",
                 int(groups_n), int(clients_n), int(matched_n), error or ""))
            self._conn.commit()
            cur.close()

    def hr_sync_last(self):
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM hr_sync_log ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        cur.close()
        return row

    # ------------------------------------------------------------------
    # 火绒 ↔ 平台终端关联引擎（资产融合，ADR-033 增补）
    # 匹配键优先级 MAC → IP → 主机名，任一键命中多候选即歧义回落下一键；
    # manual 映射不被自动覆盖且两端不参与自动分配；ignore 对（用户解除过
    # 的配对）自动 pass 永久跳过；分组覆盖同步时镜像写入后再应用。
    # ------------------------------------------------------------------

    @staticmethod
    def _hr_mac_norm(mac):
        """MAC 归一比对键：去 :- . 空白分隔符 + 大写（12 位十六进制）。"""
        return "".join(ch for ch in str(mac or "")
                       if ch not in ":-. ").upper()

    def _hr_platform_index(self):
        """平台终端匹配索引：tid/hostname(小写)/ip/asset 逐网卡 MAC 集合。"""
        platforms = []
        for t in self.list_terminals():
            macs = set()
            asset = self.get_terminal_asset(t["terminal_id"]) or {}
            for nic in (asset.get("network") or []):
                if isinstance(nic, dict):
                    norm = self._hr_mac_norm(nic.get("mac"))
                    if len(norm) == 12:
                        macs.add(norm)
            platforms.append({
                "tid": t["terminal_id"],
                "host": (t.get("hostname") or "").strip().lower(),
                "ip": (t.get("ip") or "").strip(),
                "macs": macs})
        return platforms

    def hr_relink(self, now=None):
        """自动关联 pass（每次 hr 同步成功后执行；幂等）。

        - 火绒同 MAC 多条取 last_connect_time 最新参与 MAC 键
          （旧条目仍可用 IP/主机名键匹配）；
        - 平台侧 asset_detail.network[]（缺则 hwinfo 回退）逐网卡比对；
        - 返回统计 {mac, ip, hostname, manual, ignored, total}。
        """
        now = now if now is not None else int(time.time())
        platforms = self._hr_platform_index()
        cur = self._conn.cursor()
        cur.execute("SELECT client_id, mac, ip, connect_ip,"
                    " computer_name, last_seen FROM hr_clients"
                    " ORDER BY last_seen DESC")
        hr_rows = cur.fetchall()
        cur.execute("SELECT hr_client_id, terminal_id FROM hr_terminal_map"
                    " WHERE match_type='manual'")
        manual_rows = cur.fetchall()
        cur.execute("SELECT hr_client_id, terminal_id FROM hr_link_ignore")
        ignore_rows = cur.fetchall()
        cur.close()
        clients, seen_mac = [], set()
        for r in hr_rows:
            norm = self._hr_mac_norm(r["mac"])
            if len(norm) == 12:
                if norm in seen_mac:
                    norm = ""       # 同 MAC 仅最新参与 MAC 键
                else:
                    seen_mac.add(norm)
            else:
                norm = ""
            ips = []
            for ip in (r["ip"], r["connect_ip"]):
                ip = (ip or "").strip()
                if ip and ip not in ips:
                    ips.append(ip)
            clients.append({"cid": r["client_id"], "mac": norm or None,
                            "ips": ips,
                            "host": (r["computer_name"] or "")
                            .strip().lower()})
        manual = dict((r["hr_client_id"], r["terminal_id"])
                      for r in manual_rows)
        ignore = set((r["hr_client_id"], r["terminal_id"])
                     for r in ignore_rows)
        used_c = set(manual.keys())
        used_t = set(manual.values())
        pairs = []

        # MAC 轮
        for c in clients:
            if c["cid"] in used_c or not c["mac"]:
                continue
            cand = [p for p in platforms
                    if p["tid"] not in used_t and c["mac"] in p["macs"]]
            if len(cand) == 1:
                pairs.append((c["cid"], cand[0]["tid"], "mac"))
                used_c.add(c["cid"])
                used_t.add(cand[0]["tid"])
        # IP 轮（歧义时换下一 IP 再试）
        for c in clients:
            if c["cid"] in used_c:
                continue
            for ip in c["ips"]:
                cand = [p for p in platforms if p["tid"] not in used_t
                        and p["ip"] == ip]
                if len(cand) == 1:
                    pairs.append((c["cid"], cand[0]["tid"], "ip"))
                    used_c.add(c["cid"])
                    used_t.add(cand[0]["tid"])
                    break
        # 主机名轮
        for c in clients:
            if c["cid"] in used_c or not c["host"]:
                continue
            cand = [p for p in platforms if p["tid"] not in used_t
                    and p["host"] and p["host"] == c["host"]]
            if len(cand) == 1:
                pairs.append((c["cid"], cand[0]["tid"], "hostname"))
                used_c.add(c["cid"])
                used_t.add(cand[0]["tid"])

        dropped = sum(1 for x in pairs if (x[0], x[1]) in ignore)
        pairs = [x for x in pairs if (x[0], x[1]) not in ignore]
        stats = {"mac": 0, "ip": 0, "hostname": 0}
        for _, _, mt in pairs:
            stats[mt] += 1
        stats["manual"] = len(manual)
        stats["ignored"] = dropped
        stats["total"] = (stats["mac"] + stats["ip"] + stats["hostname"]
                          + stats["manual"])
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM hr_terminal_map"
                        " WHERE match_type != 'manual'")
            cur.executemany(
                "INSERT OR REPLACE INTO hr_terminal_map(hr_client_id,"
                " terminal_id, match_type, created_ts) VALUES(?,?,?,?)",
                [(cid, tid, mt, now) for cid, tid, mt in pairs])
            self._conn.commit()
            cur.close()
        return stats

    def hr_map_list(self):
        cur = self._conn.cursor()
        cur.execute("SELECT hr_client_id, terminal_id, match_type, created_ts"
                    " FROM hr_terminal_map")
        rows = cur.fetchall()
        cur.close()
        return rows

    def hr_map_manual_set(self, client_id, terminal_id, now=None):
        """manual 指定：目标被其它 manual 占用 → 'conflict'；否则抢占 auto
        并写入 manual，返回 'ok'。"""
        now = now if now is not None else int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT hr_client_id FROM hr_terminal_map"
                        " WHERE terminal_id=? AND hr_client_id != ?"
                        " AND match_type='manual'", (terminal_id, client_id))
            clash = cur.fetchone()
            if clash:
                cur.close()
                return "conflict"
            cur.execute("DELETE FROM hr_terminal_map WHERE hr_client_id=?",
                        (client_id,))
            cur.execute("DELETE FROM hr_terminal_map WHERE terminal_id=?"
                        " AND match_type != 'manual'", (terminal_id,))
            cur.execute("INSERT OR REPLACE INTO hr_terminal_map(hr_client_id,"
                        " terminal_id, match_type, created_ts)"
                        " VALUES(?,?,?,?)",
                        (client_id, terminal_id, "manual", now))
            self._conn.commit()
            cur.close()
        return "ok"

    def hr_map_delete(self, client_id):
        """解除关联。返回被删映射 {terminal_id, match_type} 或 None。"""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT terminal_id, match_type FROM hr_terminal_map"
                        " WHERE hr_client_id=?", (client_id,))
            row = cur.fetchone()
            cur.execute("DELETE FROM hr_terminal_map WHERE hr_client_id=?",
                        (client_id,))
            self._conn.commit()
            cur.close()
        return dict(row) if row else None

    def hr_ignore_add(self, client_id, terminal_id, now=None):
        now = now if now is not None else int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("INSERT OR IGNORE INTO hr_link_ignore(hr_client_id,"
                        " terminal_id, created_ts) VALUES(?,?,?)",
                        (client_id, terminal_id, now))
            self._conn.commit()
            cur.close()

    def hr_ignore_clear(self, client_id, terminal_id):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM hr_link_ignore WHERE hr_client_id=?"
                        " AND terminal_id=?", (client_id, terminal_id))
            self._conn.commit()
            cur.close()

    def hr_override_set(self, client_id, group_id, now=None):
        """分组覆盖：group_id 为 0/None/空 → 删除覆盖（回火绒原生分组）。"""
        now = now if now is not None else int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            if group_id in (None, 0, "", "0"):
                cur.execute("DELETE FROM hr_group_override"
                            " WHERE hr_client_id=?", (client_id,))
            else:
                cur.execute("INSERT OR REPLACE INTO hr_group_override"
                            "(hr_client_id, group_id, created_ts)"
                            " VALUES(?,?,?)",
                            (client_id, int(group_id), now))
            self._conn.commit()
            cur.close()

    def hr_apply_overrides(self):
        """镜像写入后应用分组覆盖（目标组须存在于 hr_groups，悬空跳过）。"""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE hr_clients SET group_id = ("
                "  SELECT o.group_id FROM hr_group_override o"
                "  JOIN hr_groups g ON g.group_id = o.group_id"
                "  WHERE o.hr_client_id = hr_clients.client_id)"
                " WHERE client_id IN (SELECT o.hr_client_id"
                " FROM hr_group_override o JOIN hr_groups g"
                " ON g.group_id = o.group_id)")
            applied = cur.rowcount
            self._conn.commit()
            cur.close()
        return applied

    def hr_groups_with_stats(self):
        """融合组树火绒段：覆盖应用后 total/online + matched 计数。"""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT g.group_id AS id, g.name, g.parent,"
            " COALESCE(s.total, 0) AS total, COALESCE(s.online, 0) AS online,"
            " COALESCE(s.matched, 0) AS matched"
            " FROM hr_groups g LEFT JOIN ("
            "   SELECT c.group_id AS gid, COUNT(*) AS total,"
            "          COALESCE(SUM(c.online), 0) AS online,"
            "          SUM(CASE WHEN m.hr_client_id IS NOT NULL"
            "              THEN 1 ELSE 0 END) AS matched"
            "   FROM hr_clients c LEFT JOIN hr_terminal_map m"
            "   ON m.hr_client_id = c.client_id GROUP BY c.group_id) s"
            " ON s.gid = g.group_id ORDER BY g.group_id ASC")
        rows = cur.fetchall()
        cur.close()
        return rows

    def hr_groups_raw(self):
        """火绒组原始列表 [{id,name,parent}]（镜像同步输入）。"""
        cur = self._conn.cursor()
        cur.execute("SELECT group_id AS id, name, parent FROM hr_groups")
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return rows

    def hr_locate_clients(self, ips=(), macs_norm=(), hosts_lower=()):
        """资产定位检索：火绒镜像候选行（只读；asset_locate 管线消费）。

        macs_norm 为 12 位归一小写 MAC 集合（Python 端归一比对，镜像
        MAC 分隔符形态不定）；hosts_lower 为计算机名精确小写集合；
        ips 同时匹配 ip 与 connect_ip。三键均空返回 []，绝不全表返回。
        返回行含分组名与上下线/开关机时间（hr_clients_page 不透出的
        first_seen/last_off/this_on 由本方法补齐）。"""
        ips = set(str(i or "").strip() for i in ips if str(i or "").strip())
        # 归一口径统一小写 12 位（store 内部 _hr_mac_norm 为大写，此处
        # 统一 lower，与 asset_locate 管线小写键对齐）
        macs_norm = set("".join(ch for ch in str(m or "")
                                if ch not in ":-. ").lower()
                        for m in macs_norm)
        macs_norm = set(m for m in macs_norm if len(m) == 12)
        hosts_lower = set(h for h in hosts_lower if h)
        if not (ips or macs_norm or hosts_lower):
            return []
        cur = self._conn.cursor()
        cur.execute(
            "SELECT c.client_id, c.name, c.computer_name, c.ip, c.connect_ip,"
            " c.mac, c.group_id, g.name AS group_name, c.native_group_id,"
            " c.online, c.os, c.version, c.last_seen, c.first_seen,"
            " c.last_off, c.this_on, c.updated_at"
            " FROM hr_clients c LEFT JOIN hr_groups g"
            " ON g.group_id = c.group_id")
        rows = []
        for r in cur.fetchall():
            hit_keys = set()
            if r["ip"] in ips or r["connect_ip"] in ips:
                hit_keys.add("ip")
            norm = self._hr_mac_norm(r["mac"]).lower()
            if len(norm) == 12 and norm in macs_norm:
                hit_keys.add("mac")
            host = (r["computer_name"] or "").strip().lower()
            if host and host in hosts_lower:
                hit_keys.add("hostname")
            if hit_keys:
                rows.append(dict(r, hit_keys=sorted(hit_keys)))
        cur.close()
        return rows

    def hr_matched_map(self):
        """{huorong_group_id: [terminal_id,...]}（matched 关联映射）。"""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT c.group_id AS gid, m.terminal_id AS tid"
            " FROM hr_terminal_map m"
            " JOIN hr_clients c ON c.client_id = m.hr_client_id")
        out = {}
        for r in cur.fetchall():
            out.setdefault(r["gid"], []).append(r["tid"])
        cur.close()
        return out

    def hr_platform_unlinked(self):
        """未关联任何火绒终端的平台终端（「其他」虚拟组成员）。"""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT t.terminal_id, t.last_seen FROM terminals t"
            " WHERE NOT EXISTS (SELECT 1 FROM hr_terminal_map m"
            " WHERE m.terminal_id = t.terminal_id)")
        rows = cur.fetchall()
        cur.close()
        return rows

    def hr_client_exists(self, client_id):
        cur = self._conn.cursor()
        cur.execute("SELECT 1 FROM hr_clients WHERE client_id=?",
                    (str(client_id or ""),))
        row = cur.fetchone()
        cur.close()
        return row is not None

    def hr_group_exists(self, group_id):
        cur = self._conn.cursor()
        cur.execute("SELECT 1 FROM hr_groups WHERE group_id=?",
                    (int(group_id),))
        row = cur.fetchone()
        cur.close()
        return row is not None

    def hr_override_apply(self, client_id):
        """按 override 表当前值单点应用分组（组不存在则跳过不改）。"""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE hr_clients SET group_id = ("
                "  SELECT o.group_id FROM hr_group_override o"
                "  JOIN hr_groups g ON g.group_id = o.group_id"
                "  WHERE o.hr_client_id = ?) WHERE client_id = ?"
                " AND EXISTS (SELECT 1 FROM hr_group_override o2"
                " JOIN hr_groups g2 ON g2.group_id = o2.group_id"
                " WHERE o2.hr_client_id = ?)",
                (client_id, client_id, client_id))
            changed = cur.rowcount > 0
            self._conn.commit()
            cur.close()
        return changed

    def hr_override_reset_native(self, client_id):
        """解除覆盖：group_id 恢复火绒原生分组（原生组已消失则置 NULL）。"""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT native_group_id FROM hr_clients WHERE client_id=?",
                (client_id,))
            row = cur.fetchone()
            native = row["native_group_id"] if row else None
            if native is not None and not self.hr_group_exists(native):
                native = None
            cur.execute("UPDATE hr_clients SET group_id=? WHERE client_id=?",
                        (native, client_id))
            self._conn.commit()
            cur.close()
        return native

    # ------------------------------------------------------------------
    # 病毒事件统计镜像（官方 /api/clnts/_virus_events 为统计口径：
    # 每终端 count + 处理结果分布，无病毒名/事件时间明细流，ADR-033 增补）
    # ------------------------------------------------------------------

    def hr_virus_replace_snapshot(self, rows, now=None):
        """整体替换病毒统计快照（单事务，幂等）。"""
        now = now if now is not None else int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM hr_virus_stat")
            cur.executemany(
                "INSERT OR REPLACE INTO hr_virus_stat(client_id, total,"
                " success, fail, ignored, trusted, snapshot_ts)"
                " VALUES(?,?,?,?,?,?,?)",
                [(r["client_id"], int(r.get("total") or 0),
                  int(r.get("success") or 0), int(r.get("fail") or 0),
                  int(r.get("ignored") or 0), int(r.get("trusted") or 0),
                  int(r.get("snapshot_ts") or now)) for r in rows])
            self._conn.commit()
            cur.close()

    def hr_virus_get(self, client_id):
        cur = self._conn.cursor()
        cur.execute("SELECT client_id, total, success, fail, ignored,"
                    " trusted, snapshot_ts FROM hr_virus_stat WHERE client_id=?",
                    (str(client_id or ""),))
        row = cur.fetchone()
        cur.close()
        return row

    def _hr_group_path(self, group_id, max_depth=10):
        """火绒组命名路径（父链上溯反序拼「A/B/C」；环/断链防御）。"""
        names = []
        gid = group_id
        seen = set()
        cur = self._conn.cursor()
        while gid is not None and len(names) < max_depth \
                and gid not in seen:
            seen.add(gid)
            cur.execute("SELECT name, parent FROM hr_groups WHERE group_id=?",
                        (gid,))
            row = cur.fetchone()
            if row is None:
                break
            names.append(row["name"] or "")
            gid = row["parent"]
        cur.close()
        names.reverse()
        return "/".join(n for n in names if n) or None

    def hr_context_block(self, terminal_id, now=None):
        """资产弹窗「第三方数据源信息·火绒块」聚合（只读缓存，ADR-033）。

        未关联 → {"linked": False}；关联 → 绑定关系 + 火绒终端字段 +
        组命名路径 + 病毒统计（无快照为 null）。"""
        now = now if now is not None else int(time.time())
        cur = self._conn.cursor()
        cur.execute("SELECT hr_client_id, match_type, created_ts"
                    " FROM hr_terminal_map WHERE terminal_id=?",
                    (str(terminal_id or ""),))
        m = cur.fetchone()
        cur.close()
        if m is None:
            return {"linked": False}
        cid = m["hr_client_id"]
        cur = self._conn.cursor()
        cur.execute(
            "SELECT c.client_id, c.name, c.computer_name, c.ip, c.connect_ip,"
            " c.mac, c.group_id, g.name AS group_name, c.online, c.os,"
            " c.version, c.last_seen, c.first_seen, c.last_off, c.this_on"
            " FROM hr_clients c"
            " LEFT JOIN hr_groups g ON g.group_id = c.group_id"
            " WHERE c.client_id=?", (cid,))
        c = cur.fetchone()
        cur.close()
        if c is None:
            # 映射悬空（镜像波动），下轮 relink 自愈；如实返回未关联
            return {"linked": False}
        virus = self.hr_virus_get(cid)
        return {
            "linked": True,
            "match_type": m["match_type"],
            "bound_ts": m["created_ts"],
            "client": {
                "client_id": c["client_id"],
                "name": c["name"] or "",
                "computer_name": c["computer_name"] or "",
                "ip": c["ip"] or "",
                "connect_ip": c["connect_ip"] or "",
                "mac": c["mac"] or "",
                "online": bool(c["online"]),
                "os": c["os"] or "",
                "hr_version": c["version"] or "",
                "last_seen": c["last_seen"],
                "first_seen": c["first_seen"],
                "last_off": c["last_off"],
                "this_on": c["this_on"],
            },
            "group_id": c["group_id"],
            "group_name": c["group_name"] or "",
            "group_path": self._hr_group_path(c["group_id"]),
            "assets": self.hr_assets_get(cid),
            "virus": ({"total": virus["total"], "success": virus["success"],
                       "fail": virus["fail"], "ignored": virus["ignored"],
                       "trusted": virus["trusted"],
                       "snapshot_ts": virus["snapshot_ts"]}
                      if virus else None),
        }

    def hr_unified_items(self, group_source="huorong", group_id=None,
                         q=None, hb_timeout=180, now=None):
        """统一条目装配（三类 kind；内存过滤排序，分页由调用方切片）。

        kind：matched（映射两端齐全）/ huorong_only / platform_only。
        group_id="other" 仅 huorong 视角有效（装 platform_only）。
        """
        now = now if now is not None else int(time.time())
        cur = self._conn.cursor()
        cur.execute(
            "SELECT c.client_id, c.name, c.computer_name, c.ip, c.connect_ip,"
            " c.mac, c.group_id, g.name AS group_name, c.online, c.os,"
            " c.version, c.last_seen FROM hr_clients c"
            " LEFT JOIN hr_groups g ON g.group_id = c.group_id")
        hr_rows = cur.fetchall()
        cur.execute("SELECT hr_client_id, terminal_id, match_type"
                    " FROM hr_terminal_map")
        map_rows = cur.fetchall()
        cur.execute(
            "SELECT terminal_id, hostname, ip, os_info, client_version,"
            " group_id, last_seen, cpu_model, cpu_cores, mem_total_mb,"
            " disk_total_gb, gpu_info, os_arch FROM terminals")
        pt_rows = cur.fetchall()
        cur.close()
        cmap = dict((r["hr_client_id"], r) for r in map_rows)
        tmap = dict((r["terminal_id"], r) for r in map_rows)
        platform_by_tid = dict((r["terminal_id"], r) for r in pt_rows)

        def psub(r):
            return {"terminal_id": r["terminal_id"],
                    "hostname": r["hostname"] or "",
                    "ip": r["ip"] or "",
                    "online": (now - (r["last_seen"] or 0)) < hb_timeout,
                    "client_version": r["client_version"] or "",
                    "os_info": r["os_info"] or "",
                    "group_id": r["group_id"],
                    "asset": {"cpu_model": r.get("cpu_model") or "",
                              "cpu_cores": r.get("cpu_cores"),
                              "mem_total_mb": r.get("mem_total_mb"),
                              "disk_total_gb": r.get("disk_total_gb"),
                              "gpu_info": r.get("gpu_info") or "",
                              "os_arch": r.get("os_arch") or ""}}

        def hsub(r):
            osv = r["os"] or ""
            return {"client_id": r["client_id"], "name": r["name"] or "",
                    "computer_name": r["computer_name"] or "",
                    "ip": r["ip"] or "", "connect_ip": r["connect_ip"] or "",
                    "mac": r["mac"] or "", "group_id": r["group_id"],
                    "group_name": r["group_name"] or "",
                    "online": bool(r["online"]), "os": osv,
                    "hr_version": r["version"] or "",
                    "last_seen": r["last_seen"],
                    "win7_eol": "windows 7" in osv.lower()}

        items = []
        for r in hr_rows:
            m = cmap.get(r["client_id"])
            p = platform_by_tid.get(m["terminal_id"]) if m else None
            if m and p is not None:
                items.append({"kind": "matched",
                              "match_type": m["match_type"],
                              "platform": psub(p), "huorong": hsub(r)})
            else:
                items.append({"kind": "huorong_only", "match_type": None,
                              "platform": None, "huorong": hsub(r)})
        for r in pt_rows:
            if r["terminal_id"] in tmap:
                continue
            items.append({"kind": "platform_only", "match_type": None,
                          "platform": psub(r), "huorong": None})

        def in_view(it):
            if not group_id:
                return True
            if str(group_id) == "other":
                return group_source == "huorong" \
                    and it["kind"] == "platform_only"
            try:
                gid = int(group_id)
            except (TypeError, ValueError):
                return False
            owners = []
            if it["huorong"] is not None:
                owners.append(("huorong", it["huorong"]["group_id"]))
            if it["platform"] is not None:
                owners.append(("platform", it["platform"]["group_id"]))
            for source, owner_gid in owners:
                if source == group_source and owner_gid == gid:
                    return True
            return False

        qn = (q or "").strip().lower()

        def hit_q(it):
            if not qn:
                return True
            for sub in (it["platform"], it["huorong"]):
                if not sub:
                    continue
                for key in ("terminal_id", "hostname", "ip", "name",
                            "computer_name", "mac", "os_info"):
                    v = sub.get(key)
                    if v and qn in str(v).lower():
                        return True
            return False

        items = [it for it in items if in_view(it) and hit_q(it)]
        kind_rank = {"matched": 0, "huorong_only": 1, "platform_only": 2}

        def sort_key(it):
            h, p = it["huorong"], it["platform"]
            name = ""
            if h and h.get("name"):
                name = h["name"]
            elif p and p.get("hostname"):
                name = p["hostname"]
            elif h:
                name = h.get("computer_name") or ""
            ident = (h or {}).get("client_id") or \
                (p or {}).get("terminal_id") or ""
            return (kind_rank[it["kind"]], name.lower(), str(ident))

        items.sort(key=sort_key)
        return items

    # ------------------------------------------------------------------
    # retention
    # ------------------------------------------------------------------

    def cleanup(self, retention_days):
        """按保留策略清理过期数据。返回 {表: 删除行数}。"""
        now = int(time.time())
        stats = {}
        with self._lock:
            cur = self._conn.cursor()
            for table, days in retention_days.items():
                if not days or days <= 0:
                    continue
                cutoff = now - int(days) * 86400
                cur.execute("DELETE FROM %s WHERE ts < ?" % table, (cutoff,))
                stats[table] = cur.rowcount
            self._conn.commit()
            cur.close()
        return stats


# ---------------------------------------------------------------------------
# H8（安全改造 R1）：并发安全 —— 方法统一串行化
#
# 原实现仅部分写路径持 `self._lock`，读路径直接使用**共享单连接**
# （check_same_thread=False），并发下会出现 `database is locked` 与游标状态错乱
# （ThreadingHTTPServer 每请求一线程，心跳上报 + 控制台查询天然并发）。
#
# 这里在类定义之后，自动为 Store 的**所有普通方法**（含内部方法；排除 dunder）
# 包一层可重入锁：一次覆盖存量与后续新增方法，无需逐方法改动，也不会因内部
# 方法嵌套调用而死锁（RLock）。
# ---------------------------------------------------------------------------
def _serialized(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)
    return wrapper


def _serialize_all(cls):
    for name, attr in list(vars(cls).items()):
        # 仅包装普通函数（跳过 property / staticmethod / classmethod 与 dunder）
        if isinstance(attr, types.FunctionType) and not (
                name.startswith("__") and name.endswith("__")):
            setattr(cls, name, _serialized(attr))
    return cls


Store = _serialize_all(Store)
