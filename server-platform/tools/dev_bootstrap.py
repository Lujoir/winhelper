#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""本地开发引导：初始化本地 data/console_auth.db 的 admin 账号（仅开发用）。

口令从环境变量 ETP_ADMIN_PWD 读取（须满足复杂度策略），不落任何文件。
本地服务默认读 server/../data/console_auth.db（api.py 顶部设定）。

用法：
    $env:ETP_ADMIN_PWD = "<本地开发口令>"
    python tools/dev_bootstrap.py
"""
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.normpath(os.path.join(HERE, "..", "server"))
sys.path.insert(0, SERVER)

import auth_upgrade as auth  # noqa: E402


def main():
    pwd = os.environ.get("ETP_ADMIN_PWD")
    if not pwd:
        raise SystemExit("missing env: ETP_ADMIN_PWD")
    auth.DB_PATH = os.path.normpath(
        os.path.join(SERVER, "..", "data", "console_auth.db"))
    os.makedirs(os.path.dirname(auth.DB_PATH), exist_ok=True)

    conn = auth.get_conn()
    # 幂等建表（与 migrate_login_upgrade 同构，最小集）
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS console_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        password_algo TEXT NOT NULL DEFAULT 'plain',
        password_updated_at INTEGER,
        password_must_change INTEGER NOT NULL DEFAULT 0,
        failed_attempts INTEGER NOT NULL DEFAULT 0,
        first_failed_at INTEGER, locked_until INTEGER,
        lock_count INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'active',
        last_login_at INTEGER, last_login_ip TEXT,
        last_failed_at INTEGER, last_failed_ip TEXT,
        role TEXT NOT NULL DEFAULT 'operator');
    CREATE TABLE IF NOT EXISTS console_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, token_hash TEXT NOT NULL UNIQUE,
        user_id INTEGER NOT NULL, username TEXT NOT NULL,
        created_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL,
        idle_expires_at INTEGER NOT NULL, absolute_expires_at INTEGER NOT NULL,
        client_ip TEXT, ua_hash TEXT, revoked_at INTEGER, revoke_reason TEXT,
        mfa_passed INTEGER NOT NULL DEFAULT 0,
        must_change_password INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES console_users(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS console_password_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        password_hash TEXT NOT NULL, changed_at INTEGER NOT NULL, changed_by TEXT);
    CREATE TABLE IF NOT EXISTS console_login_throttle (
        id INTEGER PRIMARY KEY AUTOINCREMENT, scope_type TEXT NOT NULL,
        scope_key TEXT NOT NULL, window_start INTEGER NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, blocked_until INTEGER,
        updated_at INTEGER NOT NULL, UNIQUE(scope_type, scope_key));
    CREATE TABLE IF NOT EXISTS console_audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at INTEGER NOT NULL,
        occurred_ms INTEGER, event_type TEXT NOT NULL, result TEXT NOT NULL,
        username TEXT, user_id INTEGER, client_ip TEXT, user_agent TEXT,
        session_id INTEGER, target TEXT, reason TEXT, detail TEXT);
    CREATE TABLE IF NOT EXISTS console_security_policy (
        key TEXT PRIMARY KEY, value TEXT NOT NULL,
        value_type TEXT NOT NULL DEFAULT 'int', description TEXT,
        updated_at INTEGER, updated_by TEXT);
    """)
    conn.commit()

    row = conn.execute(
        "SELECT id FROM console_users WHERE username='admin'").fetchone()
    if row:
        print("admin already exists (id=%s), skip" % row["id"])
        conn.close()
        return 0
    ok, why = auth.check_password_policy(pwd, "admin")
    if not ok:
        raise SystemExit("password policy: %s" % why)
    h = auth.hash_password(pwd)
    conn.execute(
        "INSERT INTO console_users(username, password, password_algo, "
        "password_updated_at, password_must_change, role, status) "
        "VALUES('admin', ?, 'pbkdf2_sha256', ?, 0, 'admin', 'active')",
        (h, auth.now()))
    conn.commit()
    conn.close()
    print("admin created (role=admin) at %s" % auth.DB_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
