# -*- coding: utf-8 -*-
"""隔离冒烟环境初始化（临时脚本，不入仓库）：console_auth.db 建表 + admin 明文账号
（auth 登录时 verify_and_upgrade 自动平滑转哈希）。"""
import os
import sqlite3
import sys
import threading

DATA_DIR = sys.argv[1]
USERS = "console_users"

os.makedirs(DATA_DIR, exist_ok=True)
db_path = os.path.join(DATA_DIR, "console_auth.db")

import auth_upgrade as auth  # noqa: E402
auth.DB_PATH = db_path
auth._local = threading.local()

c = auth.get_conn()
c.executescript("""
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
    must_change_password INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS console_password_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    password_hash TEXT NOT NULL, changed_at INTEGER NOT NULL, changed_by TEXT);
CREATE TABLE IF NOT EXISTS console_login_throttle (
    id INTEGER PRIMARY KEY AUTOINCREMENT, scope_type TEXT NOT NULL,
    scope_key TEXT NOT NULL, window_start INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, blocked_until INTEGER,
    updated_at INTEGER NOT NULL, UNIQUE(scope_type, scope_key));
CREATE TABLE IF NOT EXISTS console_audit_log (
    occurred_at INTEGER NOT NULL, occurred_ms INTEGER, event_type TEXT NOT NULL,
    result TEXT NOT NULL, username TEXT, user_id INTEGER, client_ip TEXT,
    user_agent TEXT, session_id INTEGER, target TEXT, reason TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS console_security_policy (
    key TEXT PRIMARY KEY, value TEXT NOT NULL,
    value_type TEXT NOT NULL DEFAULT 'int', description TEXT,
    updated_at INTEGER, updated_by TEXT);
INSERT OR REPLACE INTO console_security_policy(key,value)
    VALUES('pbkdf2_iterations','20000');
INSERT OR IGNORE INTO console_users(username,password,role)
    VALUES('admin','dev-console','admin');
""")
c.commit()
row = c.execute("SELECT id, username, role FROM console_users").fetchall()
print("auth db ready:", db_path)
print("users:", row)
