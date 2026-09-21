#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""sysadmin 模块本地最小单测（临时库，零凭据，零网络）。

覆盖（ADR-021 验证门禁）：
- 账户防线：用户名唯一(409)、禁删自己(409)、禁自降级/自禁用(409)、
  最后一个 admin 保护(409)、口令复杂度(400)、创建后强制改密
- ensure_admin_role：历史库全 operator 时把 admin 提升为 admin（幂等）
- require_admin：admin 放行 / operator 与停用账号拒绝
- token 兼容：config token 仍有效、新 token 可用、disabled 拒绝、
  rotate 旧失效、last_used_ts 60s 节流
- third-party CRUD + toggle
- check_terminal_token（ApiContext 真实路径）

用法：python tools/test_sysadmin.py
"""
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.normpath(os.path.join(HERE, "..", "server"))
sys.path.insert(0, SERVER)

import auth_upgrade as auth           # noqa: E402
from store import Store               # noqa: E402

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                          (" | " + detail) if detail and not cond else ""))


BOOTSTRAP = """
CREATE TABLE console_users (
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
CREATE TABLE console_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, token_hash TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL, username TEXT NOT NULL,
    created_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL,
    idle_expires_at INTEGER NOT NULL, absolute_expires_at INTEGER NOT NULL,
    client_ip TEXT, ua_hash TEXT, revoked_at INTEGER, revoke_reason TEXT,
    mfa_passed INTEGER NOT NULL DEFAULT 0,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES console_users(id) ON DELETE CASCADE);
CREATE TABLE console_password_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    password_hash TEXT NOT NULL, changed_at INTEGER NOT NULL, changed_by TEXT);
CREATE TABLE console_login_throttle (
    id INTEGER PRIMARY KEY AUTOINCREMENT, scope_type TEXT NOT NULL,
    scope_key TEXT NOT NULL, window_start INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, blocked_until INTEGER,
    updated_at INTEGER NOT NULL, UNIQUE(scope_type, scope_key));
CREATE TABLE console_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at INTEGER NOT NULL,
    occurred_ms INTEGER, event_type TEXT NOT NULL, result TEXT NOT NULL,
    username TEXT, user_id INTEGER, client_ip TEXT, user_agent TEXT,
    session_id INTEGER, target TEXT, reason TEXT, detail TEXT);
CREATE TABLE console_security_policy (
    key TEXT PRIMARY KEY, value TEXT NOT NULL,
    value_type TEXT NOT NULL DEFAULT 'int', description TEXT,
    updated_at INTEGER, updated_by TEXT);
INSERT INTO console_security_policy(key, value) VALUES('pbkdf2_iterations', '20000');
INSERT INTO console_users(username, password) VALUES('admin', 'Legacy@Plain2026');
"""

VALID_PWD = "Sysadmin#2026x"
VALID_PWD2 = "Operator$2026y"


def main():
    tmp = tempfile.mkdtemp(prefix="etp_sysadmin_test_")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n=== 结果：通过 %d / 失败 %d ===" % (len(PASSED), len(FAILED)))
    return 0 if not FAILED else 1


def run(tmp):
    # ---- 环境搭建：临时鉴别库 + 临时业务库 ----
    auth.DB_PATH = os.path.join(tmp, "auth.db")
    auth._local = threading.local()          # 重置线程局部连接
    conn = auth.get_conn()
    conn.executescript(BOOTSTRAP)
    conn.commit()
    auth.invalidate_policy_cache()

    store = Store(os.path.join(tmp, "eyeterm.db"), config_token="cfg-token-0001")
    import api as api_mod                    # 导入后重设（api 会覆盖 auth.DB_PATH）
    auth.DB_PATH = os.path.join(tmp, "auth.db")
    auth._local = threading.local()
    ctx = api_mod.ApiContext(store, {"terminal_token": "cfg-token-0001"})

    # ---- [1] ensure_admin_role 迁移 ----
    print("\n[1] ensure_admin_role（历史库全 operator → admin 提升）")
    r0 = conn.execute("SELECT role FROM console_users WHERE username='admin'").fetchone()
    check("迁移前 admin 角色为 operator", r0["role"] == "operator")
    auth.ensure_admin_role()
    r1 = conn.execute("SELECT role FROM console_users WHERE username='admin'").fetchone()
    check("迁移后 admin 角色为 admin", r1["role"] == "admin")
    n0 = conn.execute("SELECT COUNT(*) AS n FROM console_users WHERE role='admin'").fetchone()["n"]
    auth.ensure_admin_role()
    n1 = conn.execute("SELECT COUNT(*) AS n FROM console_users WHERE role='admin'").fetchone()["n"]
    check("重复执行幂等（admin 数不变）", n0 == 1 and n1 == 1)

    # ---- [2] require_admin ----
    print("\n[2] require_admin")
    admin_row = conn.execute("SELECT id FROM console_users WHERE username='admin'").fetchone()
    admin_id = admin_row["id"]
    check("admin 会话放行", auth.require_admin({"user_id": admin_id}))
    rc = auth.create_user("op1", VALID_PWD, "operator", operator="admin")
    check("创建 operator 成功", rc["ok"] and rc["http_status"] == 200, str(rc))
    op1_id = rc["user_id"]
    check("创建后强制改密置位",
          conn.execute("SELECT password_must_change FROM console_users WHERE id=?",
                       (op1_id,)).fetchone()["password_must_change"] == 1)
    check("operator 会话拒绝", not auth.require_admin({"user_id": op1_id}))
    rc2 = auth.create_user("op2", VALID_PWD2, "operator", operator="admin")
    auth.update_user(rc2["user_id"], status="disabled", operator="admin",
                     current_user_id=admin_id)
    check("停用账号即使角色不符也拒绝",
          not auth.require_admin({"user_id": rc2["user_id"]}))

    # ---- [3] 账户管理防线 ----
    print("\n[3] 账户管理防线")
    rc = auth.create_user("op1", VALID_PWD, "operator", operator="admin")
    check("用户名唯一（409）", not rc["ok"] and rc["http_status"] == 409, str(rc))
    rc = auth.create_user("op3", "123", "operator", operator="admin")
    check("口令复杂度不足（400）", not rc["ok"] and rc["http_status"] == 400, str(rc))
    rc = auth.create_user("bad name!", VALID_PWD, "operator", operator="admin")
    check("用户名非法字符（400）", not rc["ok"] and rc["http_status"] == 400)
    rc = auth.update_user(admin_id, role="operator", operator="op1",
                          current_user_id=admin_id)
    check("禁自降级（409）", not rc["ok"] and rc["http_status"] == 409, str(rc))
    rc = auth.update_user(admin_id, status="disabled", operator="op1",
                          current_user_id=admin_id)
    check("禁停用自己（409）", not rc["ok"] and rc["http_status"] == 409)
    rc = auth.update_user(admin_id, status="disabled", operator="op1",
                          current_user_id=op1_id)
    check("禁停用最后一个 admin（409）", not rc["ok"] and rc["http_status"] == 409)
    rc = auth.update_user(admin_id, role="operator", operator="op1",
                          current_user_id=op1_id)
    check("禁降级最后一个 admin（409）", not rc["ok"] and rc["http_status"] == 409)
    rc = auth.delete_user(admin_id, operator="op1", current_user_id=admin_id)
    check("禁删自己（409）", not rc["ok"] and rc["http_status"] == 409)
    rc = auth.delete_user(admin_id, operator="op1", current_user_id=op1_id)
    check("禁删最后一个 admin（409）", not rc["ok"] and rc["http_status"] == 409)
    rc = auth.delete_user(9999, operator="admin", current_user_id=admin_id)
    check("删除不存在账号（404）", not rc["ok"] and rc["http_status"] == 404)

    # 吊销联动：启用 op2 → 建会话 → 停用 → 会话被吊销（转换吊销 + 状态复核双保险）
    auth.update_user(rc2["user_id"], status="active", operator="admin",
                     current_user_id=admin_id)
    token2, _sid = auth.create_session(
        conn.execute("SELECT * FROM console_users WHERE id=?",
                     (rc2["user_id"],)).fetchone(), "127.0.0.1", "Test/1.0")
    check("op2 会话建立", auth.resolve_session(token2, "127.0.0.1") is not None)
    auth.update_user(rc2["user_id"], status="disabled", operator="admin",
                     current_user_id=admin_id)
    check("停用后存量会话被吊销",
          auth.resolve_session(token2, "127.0.0.1") is None)
    token2b, _sid2b = auth.create_session(
        conn.execute("SELECT * FROM console_users WHERE id=?",
                     (rc2["user_id"],)).fetchone(), "127.0.0.1", "Test/1.0")
    check("停用账号直建会话被状态复核拒绝",
          auth.resolve_session(token2b, "127.0.0.1") is None)

    # 删除联动 + FK 级联
    rc3 = auth.create_user("victim", VALID_PWD, "operator", operator="admin")
    victim_id = rc3["user_id"]
    tok3, _sid3 = auth.create_session(
        conn.execute("SELECT * FROM console_users WHERE id=?",
                     (victim_id,)).fetchone(), "127.0.0.1", "Test/1.0")
    rc = auth.delete_user(victim_id, operator="admin", current_user_id=admin_id)
    check("删除普通账号成功", rc["ok"], str(rc))
    gone = conn.execute("SELECT id FROM console_users WHERE id=?",
                        (victim_id,)).fetchone()
    sess_gone = conn.execute(
        "SELECT id FROM console_sessions WHERE token_hash=?",
        (auth._sha256_hex(tok3),)).fetchone()
    check("删除后会话随 FK 级联清理", gone is None and sess_gone is None)

    # ---- [4] token 兼容（config token / 新 token / disabled / rotate / 节流）----
    print("\n[4] terminal_tokens 兼容与鉴权")
    check("config token 已幂等迁移入表（label=default）",
          store.token_get_active("cfg-token-0001") is not None
          and [t for t in store.token_list()
               if t["token"] == "cfg-token-0001"][0]["label"] == "default")
    store.token_ensure_default("cfg-token-0001")
    check("token_ensure_default 幂等（不产生重复行）",
          len([t for t in store.token_list()
               if t["token"] == "cfg-token-0001"]) == 1)

    check("config token 经 check_terminal_token 放行",
          ctx.check_terminal_token("cfg-token-0001", "127.0.0.1"))
    check("错误 token 拒绝", not ctx.check_terminal_token("no-such-token", "127.0.0.1"))
    check("空 token 拒绝", not ctx.check_terminal_token("", "127.0.0.1"))

    new_tok = store.token_create("unit-test")
    check("新 token 放行（表通道）",
          ctx.check_terminal_token(new_tok["token"], "127.0.0.1"))
    row = [t for t in store.token_list() if t["id"] == new_tok["id"]][0]
    check("首次使用记录 last_used_ts", row["last_used_ts"] is not None)
    ts_now = int(time.time())
    check("60 秒内重复使用不重复写库（节流）",
          not store.token_touch_last_used(new_tok["id"], ts_now, now=ts_now + 10))
    check("超 60 秒后允许更新",
          store.token_touch_last_used(new_tok["id"], ts_now, now=ts_now + 61))

    store.token_set_status(new_tok["id"], "disabled")
    check("disabled token 拒绝（立即失效）",
          not ctx.check_terminal_token(new_tok["token"], "127.0.0.1"))
    store.token_set_status(new_tok["id"], "active")
    check("enable 后恢复放行", ctx.check_terminal_token(new_tok["token"], "127.0.0.1"))
    rotated = store.token_rotate(new_tok["id"])
    check("rotate 返回新值且旧值立即失效",
          rotated and rotated != new_tok["token"]
          and not ctx.check_terminal_token(new_tok["token"], "127.0.0.1")
          and ctx.check_terminal_token(rotated, "127.0.0.1"))
    check("rotate 不存在条目返回 None", store.token_rotate(99999) is None)
    store.token_set_status(new_tok["id"], "disabled")   # 清理：测试 token 停用

    # ---- [5] third-party CRUD + toggle ----
    print("\n[5] third_party_apis CRUD")
    tpid = store.third_party_create(
        "cmdb", "https://cmdb.example.local/api", "POST",
        '{"host": "{host}"}', '{"X-Token": "abc"}', "配置查询")
    rows = store.third_party_list()
    check("创建 + 列表", len(rows) == 1 and rows[0]["name"] == "cmdb"
          and rows[0]["enabled"] is True)
    check("读取单条", store.third_party_get(tpid)["method"] == "POST")
    check("更新字段", store.third_party_update(
        tpid, {"note": "改备注", "method": "GET"})
        and store.third_party_get(tpid)["note"] == "改备注"
        and store.third_party_get(tpid)["method"] == "GET")
    store.third_party_toggle(tpid, False)
    check("停用", store.third_party_get(tpid)["enabled"] is False)
    store.third_party_toggle(tpid, True)
    check("启用", store.third_party_get(tpid)["enabled"] is True)
    check("删除", store.third_party_delete(tpid)
          and not store.third_party_list())
    check("删除/更新不存在条目返回 False",
          not store.third_party_delete(tpid)
          and not store.third_party_update(tpid, {"note": "x"}))

    # ---- [6] 审计落库抽查 ----
    print("\n[6] console_audit_log 关键操作留痕")
    evs = set(r["event_type"] for r in conn.execute(
        "SELECT DISTINCT event_type FROM console_audit_log").fetchall())
    need = {auth.Ev.USER_CREATED, auth.Ev.USER_UPDATED, auth.Ev.USER_DELETED,
            auth.Ev.SESSION_REVOKED}
    check("建户/改户/删户/吊销均有审计（缺 %s）" % (need - evs or "无"),
          need <= evs, str(evs))

    conn.close()
    store.close()


if __name__ == "__main__":
    sys.exit(main())
