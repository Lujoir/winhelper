#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
后台管理控制台 登录改造 —— 幂等数据库迁移器

依赖：仅 Python 标准库（sqlite3 / argparse / hashlib / os / shutil / time / sys）
契合既定架构决策 ADR-002「零第三方依赖」。

为什么需要这个脚本而不是直接跑 .sql：
    SQLite 的 ALTER TABLE ADD COLUMN 不支持 IF NOT EXISTS，纯 SQL 脚本重复执行会
    在第二次报 "duplicate column name" 而中断，导致后半段 DDL 未执行、库处于半改造
    状态。本脚本先用 PRAGMA table_info 读取现有列，只补缺失的列，可安全重复执行。

用法：
    # 1) 先演练，不落盘，只打印将要执行的动作
    python migrate_login_upgrade.py --db /path/to/console.db --dry-run

    # 2) 正式执行（自动先备份数据库文件）
    python migrate_login_upgrade.py --db /path/to/console.db

    # 3) 账号表名不是默认的 console_users 时
    python migrate_login_upgrade.py --db ./console.db --users-table admin_user

    # 4) 迁移后核对状态
    python migrate_login_upgrade.py --db ./console.db --check-only

注意：
    · 本脚本**不会**把现有明文口令批量转成哈希。明文转哈希采用「登录时自动平滑迁移」
      策略（见 auth_upgrade.py 的 verify_and_upgrade），因为批量转换需要明文，而
      我们希望改造后明文在库中的存续时间尽可能短、且不引入一次性全量重置口令的运维成本。
      若你希望强制一刀切，用 --force-reset 给所有账号置 password_must_change=1，
      下次登录即强制改密（但这会打断所有在用账号，需提前通知）。
"""

import argparse
import os
import shutil
import sqlite3
import sys
import time

# ---------------------------------------------------------------------------
# 需要在账号表上补齐的列： (列名, 类型与约束)
# 顺序即执行顺序；带 NOT NULL 的必须给 DEFAULT，否则 SQLite 对已有行无法增列。
# ---------------------------------------------------------------------------
USER_COLUMNS = [
    ("password_algo",         "TEXT NOT NULL DEFAULT 'plain'"),
    ("password_updated_at",   "INTEGER"),
    ("password_must_change",  "INTEGER NOT NULL DEFAULT 0"),
    ("failed_attempts",       "INTEGER NOT NULL DEFAULT 0"),
    ("first_failed_at",       "INTEGER"),
    ("locked_until",          "INTEGER"),
    ("lock_count",            "INTEGER NOT NULL DEFAULT 0"),
    ("status",                "TEXT NOT NULL DEFAULT 'active'"),
    ("last_login_at",         "INTEGER"),
    ("last_login_ip",         "TEXT"),
    ("last_failed_at",        "INTEGER"),
    ("last_failed_ip",        "TEXT"),
    ("role",                  "TEXT NOT NULL DEFAULT 'operator'"),
]

# ---------------------------------------------------------------------------
# 新建表 DDL（均幂等）。
# {ut} 为账号表名占位符，执行时以 .format(ut=users_table) 替换——
# 外键必须指向实际账号表，故不能写死表名。
# token_hash 不加内联 UNIQUE：已有命名唯一索引 idx_sessions_token 承担唯一性，
# 再加内联约束会让 SQLite 多建一个 sqlite_autoindex，每次会话写入多维护一份索引。
# ---------------------------------------------------------------------------
DDL_SESSIONS = """
CREATE TABLE IF NOT EXISTS console_sessions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash            TEXT    NOT NULL,
    user_id               INTEGER NOT NULL,
    username              TEXT    NOT NULL,
    created_at            INTEGER NOT NULL,
    last_seen_at          INTEGER NOT NULL,
    idle_expires_at       INTEGER NOT NULL,
    absolute_expires_at   INTEGER NOT NULL,
    client_ip             TEXT,
    ua_hash               TEXT,
    revoked_at            INTEGER,
    revoke_reason         TEXT,
    mfa_passed            INTEGER NOT NULL DEFAULT 0,
    must_change_password  INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES {ut}(id) ON DELETE CASCADE
)
"""

DDL_PWD_HISTORY = """
CREATE TABLE IF NOT EXISTS console_password_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    password_hash TEXT    NOT NULL,
    changed_at    INTEGER NOT NULL,
    changed_by    TEXT,
    FOREIGN KEY (user_id) REFERENCES {ut}(id) ON DELETE CASCADE
)
"""

DDL_THROTTLE = """
CREATE TABLE IF NOT EXISTS console_login_throttle (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type    TEXT    NOT NULL,
    scope_key     TEXT    NOT NULL,
    window_start  INTEGER NOT NULL,
    attempts      INTEGER NOT NULL DEFAULT 0,
    blocked_until INTEGER,
    updated_at    INTEGER NOT NULL,
    UNIQUE (scope_type, scope_key)
)
"""

DDL_AUDIT = """
CREATE TABLE IF NOT EXISTS console_audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at INTEGER NOT NULL,
    occurred_ms INTEGER,
    event_type  TEXT    NOT NULL,
    result      TEXT    NOT NULL,
    username    TEXT,
    user_id     INTEGER,
    client_ip   TEXT,
    user_agent  TEXT,
    session_id  INTEGER,
    target      TEXT,
    reason      TEXT,
    detail      TEXT
)
"""

DDL_POLICY = """
CREATE TABLE IF NOT EXISTS console_security_policy (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    value_type  TEXT NOT NULL DEFAULT 'int',
    description TEXT,
    updated_at  INTEGER,
    updated_by  TEXT
)
"""

INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_token    ON console_sessions(token_hash)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_idle_exp        ON console_sessions(idle_expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_abs_exp         ON console_sessions(absolute_expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_user_active     ON console_sessions(user_id, revoked_at)",
    "CREATE INDEX IF NOT EXISTS idx_pwdhist_user             ON console_password_history(user_id, changed_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_throttle_blocked         ON console_login_throttle(blocked_until)",
    "CREATE INDEX IF NOT EXISTS idx_audit_time               ON console_audit_log(occurred_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_type               ON console_audit_log(event_type, occurred_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_user               ON console_audit_log(username, occurred_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_ip                 ON console_audit_log(client_ip, occurred_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_result             ON console_audit_log(result, occurred_at DESC)",
]

# 审计表 append-only 保护触发器
TRIGGERS = [
    ("trg_audit_no_update",
     "CREATE TRIGGER trg_audit_no_update BEFORE UPDATE ON console_audit_log "
     "BEGIN SELECT RAISE(ABORT, 'audit log is append-only: UPDATE denied'); END"),
    ("trg_audit_no_delete",
     "CREATE TRIGGER trg_audit_no_delete BEFORE DELETE ON console_audit_log "
     "BEGIN SELECT RAISE(ABORT, 'audit log is append-only: DELETE denied'); END"),
]

DEFAULT_POLICY = [
    ("pwd_min_length",        "10",     "int",  "口令最小长度"),
    ("pwd_min_classes",       "3",      "int",  "口令需覆盖的字符类别数（大写/小写/数字/符号）"),
    ("pwd_max_age_days",      "90",     "int",  "口令最长使用天数，到期强制改密，0=不限"),
    ("pwd_history_count",     "3",      "int",  "禁止与最近 N 次历史口令重复"),
    ("pbkdf2_iterations",     "320000", "int",  "新口令 PBKDF2-HMAC-SHA256 迭代次数"),
    ("lock_threshold",        "5",      "int",  "连续失败达此次数即锁定账号"),
    ("lock_window_seconds",   "900",    "int",  "失败计数滑动窗口（秒）"),
    ("lock_duration_seconds", "1800",   "int",  "账号锁定时长（秒），0=需管理员手工解锁"),
    ("ip_window_seconds",     "60",     "int",  "IP 限速统计窗口（秒）"),
    ("ip_max_attempts",       "10",     "int",  "单 IP 窗口内最大登录尝试次数"),
    ("ip_block_seconds",      "600",    "int",  "IP 触发限速后的封禁时长（秒）"),
    ("session_idle_seconds",  "1800",   "int",  "会话空闲超时（秒）"),
    ("session_abs_seconds",   "43200",  "int",  "会话绝对最长存活（秒）"),
    ("session_max_per_user",  "5",      "int",  "单账号并发会话上限，超出踢最旧"),
    ("bind_session_ip",       "0",      "bool", "会话 IP 变化是否直接阻断（0=仅告警）"),
    ("bind_session_ua",       "1",      "bool", "会话 User-Agent 变化是否直接阻断"),
    ("audit_retain_days",     "365",    "int",  "审计日志保留天数（等保三级底线 180 天）"),
]


# ===========================================================================
# 工具函数
# ===========================================================================

def log(msg, prefix="  "):
    print(f"{prefix}{msg}", flush=True)


def connect(db_path):
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    # 注意：迁移期间不开 foreign_keys，避免历史脏数据（如指向已删账号的行）阻塞建表
    return conn


def table_exists(conn, name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def existing_columns(conn, table):
    """用 PRAGMA table_info 读现有列名集合 —— 这是实现 ALTER 幂等的关键。"""
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def trigger_exists(conn, name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=?", (name,)
    ).fetchone()
    return row is not None


def backup_db(db_path):
    """迁移前物理备份。WAL 模式下需连 -wal / -shm 一起复制才是一致快照。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = f"{db_path}.bak-{stamp}"
    shutil.copy2(db_path, dst)
    copied = [os.path.basename(dst)]
    for suffix in ("-wal", "-shm"):
        side = db_path + suffix
        if os.path.exists(side):
            shutil.copy2(side, dst + suffix)
            copied.append(os.path.basename(dst + suffix))
    log(f"已备份：{', '.join(copied)}")
    return dst


# ===========================================================================
# 迁移主流程
# ===========================================================================

def migrate(db_path, users_table, dry_run=False, force_reset=False):
    conn = connect(db_path)
    actions = []          # 记录实际执行/将执行的动作，便于 dry-run 输出与结果汇总

    try:
        # ---- 步骤 0：前置校验 ------------------------------------------------
        print("\n[0/6] 前置校验")
        if not table_exists(conn, users_table):
            tables = [r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            log(f"✗ 找不到账号表 `{users_table}`", prefix="  ")
            log(f"库内现有表：{', '.join(tables) or '（空库）'}")
            log("请用 --users-table 指定正确的账号表名后重试。")
            return 2
        cols = existing_columns(conn, users_table)
        log(f"✓ 账号表 `{users_table}` 存在，现有 {len(cols)} 列")
        if "username" not in cols:
            log("! 警告：未发现 username 列，请确认登录名列名，"
                "auth_upgrade.py 中的 USERNAME_COLUMN 需同步调整")

        # ---- 步骤 1：账号表增列 ----------------------------------------------
        print(f"\n[1/6] 账号表 `{users_table}` 增列（改造点①口令哈希、④失败锁定）")
        added = 0
        for col, decl in USER_COLUMNS:
            if col in cols:
                log(f"· {col:22s} 已存在，跳过")
                continue
            sql = f"ALTER TABLE {users_table} ADD COLUMN {col} {decl}"
            actions.append(sql)
            if not dry_run:
                conn.execute(sql)
            log(f"+ {col:22s} {decl}")
            added += 1
        log(f"本次新增 {added} 列" + ("（演练，未落盘）" if dry_run else ""))

        # 为已有账号回填 password_updated_at，避免口令有效期判定把 NULL 当成远古时间
        # 而导致全员立即被判「口令过期」——这是很容易踩的坑。
        if not dry_run and added > 0:
            now = int(time.time())
            cur = conn.execute(
                f"UPDATE {users_table} SET password_updated_at = ? "
                f"WHERE password_updated_at IS NULL", (now,))
            log(f"回填 password_updated_at 共 {cur.rowcount} 行（防止误判口令过期）")

        # ---- 步骤 2：新建表 --------------------------------------------------
        print("\n[2/6] 新建支撑表")
        for label, ddl in (
            ("console_sessions        会话持久化（改造点②）", DDL_SESSIONS),
            ("console_password_history 口令历史", DDL_PWD_HISTORY),
            ("console_login_throttle   登录限速（改造点③）", DDL_THROTTLE),
            ("console_audit_log        安全审计（改造点③）", DDL_AUDIT),
            ("console_security_policy  安全策略参数", DDL_POLICY),
        ):
            name = label.split()[0]
            if table_exists(conn, name):
                log(f"· {label} —— 已存在，跳过")
            else:
                sql = ddl.format(ut=users_table)
                actions.append(sql.strip())
                if not dry_run:
                    conn.execute(sql)
                log(f"+ {label}")

        # ---- 步骤 3：索引 ----------------------------------------------------
        print("\n[3/6] 建立索引")
        # 末两项随账号表名变化，单独拼接（与 schema_login_upgrade.sql 保持一致：
        # 状态列支撑「僵尸账号」筛选查询，locked_until 支撑锁定账号检索）
        index_sqls = INDEXES + [
            f"CREATE INDEX IF NOT EXISTS idx_users_status "
            f"ON {users_table}(status)",
            f"CREATE INDEX IF NOT EXISTS idx_users_locked_until "
            f"ON {users_table}(locked_until)",
        ]
        for sql in index_sqls:
            if not dry_run:
                conn.execute(sql)
        log(f"✓ 已确保 {len(index_sqls)} 个索引存在（IF NOT EXISTS，天然幂等）")

        # ---- 步骤 4：审计表防篡改触发器 ---------------------------------------
        print("\n[4/6] 审计表 append-only 保护（等保三级 8.1.4.3 c）")
        for name, ddl in TRIGGERS:
            if trigger_exists(conn, name):
                log(f"· {name} 已存在，跳过")
            else:
                if not dry_run:
                    conn.execute(ddl)
                log(f"+ {name}")
        log("说明：审计表在库层面禁止 UPDATE/DELETE，归档裁剪须先 DROP TRIGGER 并留痕")

        # ---- 步骤 5：安全策略默认值 ------------------------------------------
        print("\n[5/6] 写入安全策略默认值（INSERT OR IGNORE，不覆盖已调整过的值）")
        if not dry_run:
            now = int(time.time())
            conn.executemany(
                "INSERT OR IGNORE INTO console_security_policy "
                "(key, value, value_type, description, updated_at, updated_by) "
                "VALUES (?,?,?,?,?,'migration')",
                [(k, v, t, d, now) for k, v, t, d in DEFAULT_POLICY])
        log(f"✓ {len(DEFAULT_POLICY)} 项策略参数已就位")

        # ---- 步骤 6：可选强制改密 --------------------------------------------
        print("\n[6/6] 口令迁移策略")
        if force_reset:
            if not dry_run:
                cur = conn.execute(
                    f"UPDATE {users_table} SET password_must_change = 1 "
                    f"WHERE password_algo = 'plain'")
                log(f"已对 {cur.rowcount} 个明文账号置 password_must_change=1，"
                    f"下次登录强制改密")
            else:
                log("（演练）将对所有明文账号置 password_must_change=1")
        else:
            log("采用「登录时自动平滑迁移」：用户下次成功登录时，")
            log("其明文口令即被 PBKDF2 哈希改写，用户完全无感，无需重置。")
            log("如需一刀切强制改密，加 --force-reset 重跑。")

        if dry_run:
            conn.rollback()
            print(f"\n=== 演练结束，共 {len(actions)} 条变更未执行 ===")
        else:
            conn.commit()
            print("\n=== 迁移完成，已提交 ===")

        report(conn, users_table)
        return 0

    except sqlite3.Error as exc:
        conn.rollback()
        print(f"\n✗ 迁移失败已回滚：{exc}", file=sys.stderr)
        print("  数据库保持改造前状态；如已生成备份文件可直接还原。", file=sys.stderr)
        return 1
    finally:
        conn.close()


def report(conn, users_table):
    """迁移后状态核对，也可通过 --check-only 单独运行。"""
    print("\n--- 状态核对 ---")
    cols = existing_columns(conn, users_table)
    missing = [c for c, _ in USER_COLUMNS if c not in cols]
    log(f"账号表列完整性：{'✓ 全部就位' if not missing else '✗ 缺失 ' + ', '.join(missing)}")

    for t in ("console_sessions", "console_password_history",
              "console_login_throttle", "console_audit_log",
              "console_security_policy"):
        mark = "✓" if table_exists(conn, t) else "✗"
        cnt = ""
        if table_exists(conn, t):
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            cnt = f"（{n} 行）"
        log(f"{mark} {t}{cnt}")

    if "password_algo" in cols:
        rows = conn.execute(
            f"SELECT password_algo, COUNT(*) AS n FROM {users_table} "
            f"GROUP BY password_algo").fetchall()
        log("口令算法分布：" + ", ".join(f"{r['password_algo']}={r['n']}" for r in rows))
        plain = conn.execute(
            f"SELECT COUNT(*) FROM {users_table} WHERE password_algo='plain'"
        ).fetchone()[0]
        if plain:
            log(f"→ 仍有 {plain} 个账号为明文，将在其下次登录时自动迁移。")
            log("  改造上线 30 天后若仍未归零，说明是僵尸账号，应停用清理。")
        else:
            log("→ 明文口令已全部清零，改造点①收尾完成。")

    if table_exists(conn, "console_sessions"):
        now = int(time.time())
        n = conn.execute(
            "SELECT COUNT(*) FROM console_sessions WHERE revoked_at IS NULL "
            "AND idle_expires_at > ? AND absolute_expires_at > ?", (now, now)
        ).fetchone()[0]
        log(f"当前有效会话数：{n}")


def main():
    ap = argparse.ArgumentParser(
        description="后台管理控制台登录改造 —— 幂等数据库迁移器（纯标准库）")
    ap.add_argument("--db", required=True, help="SQLite 数据库文件路径")
    ap.add_argument("--users-table", default="console_users",
                    help="现有账号表名，默认 console_users")
    ap.add_argument("--dry-run", action="store_true",
                    help="只演练打印，不写库")
    ap.add_argument("--no-backup", action="store_true",
                    help="跳过自动备份（不建议）")
    ap.add_argument("--force-reset", action="store_true",
                    help="对所有明文账号置强制改密标记，放弃平滑迁移")
    ap.add_argument("--check-only", action="store_true",
                    help="只做状态核对，不做任何变更")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"✗ 数据库文件不存在：{args.db}", file=sys.stderr)
        return 2

    print("=" * 68)
    print(" 后台管理控制台 登录改造 数据库迁移")
    print(f" 目标库    : {args.db}")
    print(f" 账号表    : {args.users_table}")
    print(f" 模式      : {'状态核对' if args.check_only else ('演练' if args.dry_run else '正式执行')}")
    print(f" 时间      : {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 68)

    if args.check_only:
        conn = connect(args.db)
        try:
            if not table_exists(conn, args.users_table):
                print(f"✗ 账号表 `{args.users_table}` 不存在", file=sys.stderr)
                return 2
            report(conn, args.users_table)
        finally:
            conn.close()
        return 0

    if not args.dry_run and not args.no_backup:
        print("\n[备份]")
        backup_db(args.db)

    return migrate(args.db, args.users_table,
                   dry_run=args.dry_run, force_reset=args.force_reset)


if __name__ == "__main__":
    sys.exit(main())
