#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""客户端版本发布 mock 冒烟（自包含隔离环境，零凭据；ADR-042）。

脚本内 spawn 本地服务（ETP_CONFIG 指临时目录，业务库/存储全隔离，
auth 复用本地开发库与既有 smoke 口径）；跑完自动回收。

链路：无 current 三态（manifest null / 下载 404 / 定制名 400）→ 上传
（raw bytes PUT + 400 矩阵）→ 发布（set-current + 回滚标记）→ manifest
→ 心跳捎带 latest_version → 通用下载（Content-Disposition + 内容一致）
→ 定制名生成（协议格式 + 票据 + token 零泄露）→ 票据下载（一次性）
→ operator 403 矩阵 → manifest 401 → 页面 200。

环境变量：ETP_ADMIN_PWD（本地开发库 admin 口令，默认 dev-console）、
ETP_CR_PORT（默认 18290）。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
ADMIN_PWD = os.environ.get("ETP_ADMIN_PWD", "dev-console")
PORT = int(os.environ.get("ETP_CR_PORT", "18290"))
API_BASE = "http://127.0.0.1:%d" % PORT
TOKEN = "cr-smoke-token"

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" | " + detail) if detail and not cond else ""))


def http(method, path, token=None, payload=None, raw=None, terminal=None):
    h = {}
    if token:
        h["X-ETP-Console-Token"] = token
    if terminal:
        h["X-ETP-Token"] = terminal
    if payload is not None:
        h["Content-Type"] = "application/json"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    elif raw is not None:
        body = raw
    else:
        body = None
    req = urllib.request.Request(API_BASE + path, data=body, headers=h,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
            hdrs = dict(resp.headers)
            try:
                return resp.status, json.loads(data.decode("utf-8")), hdrs
            except ValueError:
                return resp.status, data, hdrs
    except urllib.error.HTTPError as e:
        data = e.read()
        try:
            return e.code, json.loads(data.decode("utf-8")), {}
        except ValueError:
            return e.code, data, {}


_CUSTOM_FMT = re.compile(
    r"^EyeTerm_Setup_x64_\d{1,3}(?:\.\d{1,3}){1,3}_"
    r"[A-Za-z0-9_-]{1,160}_[0-9a-f]{8}\.exe$")


def wait_health(timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            st, r, _ = http("GET", "/api/v1/health")
            if st == 200 and r.get("ok"):
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def bootstrap_auth(auth_db_path):
    """空库自举：最小 5 表 + admin（PBKDF2 低迭代，测试提速）。

    表结构与 auth_upgrade 一致（服务进程 ensure_admin_role 幂等共存）。"""
    sys.path.insert(0, os.path.join(ROOT, "server"))
    import threading                                        # noqa: E402
    import auth_upgrade as auth                             # noqa: E402
    auth.DB_PATH = auth_db_path
    auth._local = threading.local()
    conn = auth.get_conn()
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
        must_change_password INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS console_password_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        password_hash TEXT NOT NULL, changed_at INTEGER NOT NULL,
        changed_by TEXT);
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
    INSERT OR REPLACE INTO console_security_policy(key, value)
        VALUES('pbkdf2_iterations', '20000');
    """)
    hashed = auth.hash_password(ADMIN_PWD, 20000)
    conn.execute(
        "INSERT OR IGNORE INTO console_users(username, password, password_algo,"
        " password_updated_at, role, status)"
        " VALUES('admin', ?, 'pbkdf2_sha256', strftime('%s','now'),"
        " 'admin', 'active')", (hashed,))
    conn.commit()
    auth.invalidate_policy_cache()


def bootstrap_whitelist(db_path):
    """业务库白名单放行本机（服务 Store 启动已建表；空名单 fail-closed）。"""
    import sqlite3
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("INSERT OR IGNORE INTO whitelist(cidr, note, enabled,"
                 " created_at) VALUES('127.0.0.1/32', 'cr-smoke', 1,"
                 " strftime('%s','now'))")
    conn.commit()
    conn.close()


def main():
    # 端口预检：残留孤儿进程会让 health 打到旧实例，造成诡异的表缺失/超时
    import socket
    probe = socket.socket()
    probe.settimeout(1)
    busy = probe.connect_ex(("127.0.0.1", PORT)) == 0
    probe.close()
    if busy:
        print("FATAL: port %d already busy (orphan service? kill it first)"
              % PORT)
        return 2
    tmp = tempfile.mkdtemp(prefix="etp_cr_smoke_")
    data_dir = os.path.join(tmp, "data")
    os.makedirs(data_dir, exist_ok=True)
    cfg = {
        "port": PORT,
        "terminal_token": TOKEN,
        "data_dir": data_dir,
        "session_ttl_hours": 8,
    }
    cfg_path = os.path.join(tmp, "config.json")
    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)
    bootstrap_auth(os.path.join(data_dir, "console_auth.db"))
    env = dict(os.environ)
    env["ETP_CONFIG"] = cfg_path
    srv_log = os.path.join(tmp, "service.log")
    srv_fh = open(srv_log, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "server", "app.py")],
        cwd=ROOT, env=env, stdout=srv_fh, stderr=subprocess.STDOUT)
    try:
        if not wait_health():
            print("FATAL: service not healthy in time")
            sys.exit(2)
        bootstrap_whitelist(os.path.join(data_dir, "eyeterm.db"))
        run(tmp)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        srv_fh.close()
        if FAILED:
            with open(srv_log, encoding="utf-8", errors="replace") as fh:
                print("---- service log tail ----")
                print(fh.read()[-4000:])
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n=== client release smoke: pass %d / fail %d ==="
          % (len(PASSED), len(FAILED)))
    if FAILED:
        print("failed: %s" % ", ".join(FAILED))
        return 1
    print("ALL SMOKE TESTS PASSED")
    return 0


def run(tmp):
    print("=== client-release mock smoke (port %d) ===" % PORT)

    # 0. 准备：注册终端 + admin 登录 + operator 幂等
    st, r, _ = http("POST", "/api/v1/terminals/register", terminal=TOKEN,
                    payload={"terminal_id": "WIN-CR-SMOKE",
                             "terminal_type": "windows",
                             "hostname": "CR-SMOKE",
                             "os_info": "Windows 11",
                             "client_version": "test"})
    check("register terminal", st == 200 and r.get("ok"))

    st, r, _ = http("POST", "/api/v1/console/login",
                    payload={"username": "admin", "password": ADMIN_PWD})
    check("admin login", st == 200 and r.get("ok"), str(r)[:120])
    admin = r.get("token")
    if not admin:
        return

    st, r, _ = http("GET", "/api/v1/console/sysadmin/users", token=admin)
    op_id = None
    for u in (r.get("users") or []):
        if u.get("username") == "operator":
            op_id = u["id"]
    if op_id is None:
        st, r, _ = http("POST", "/api/v1/console/sysadmin/users", token=admin,
                        payload={"username": "operator",
                                 "password": "CrSmoke#2026",
                                 "role": "operator"})
        op_id = r.get("id")
    http("POST", "/api/v1/console/sysadmin/users/%d/reset-password" % op_id,
         token=admin, payload={"new_password": "CrSmoke#2026"})
    st, r, _ = http("POST", "/api/v1/console/login",
                    payload={"username": "operator",
                             "password": "CrSmoke#2026"})
    check("operator login", st == 200 and r.get("ok"))
    operator = r["token"]
    if r.get("must_change_password"):
        st, r, _ = http("POST", "/api/v1/console/password", token=operator,
                        payload={"old_password": "CrSmoke#2026",
                                 "new_password": "CrSmoke#2026x",
                                 "confirm_password": "CrSmoke#2026x"})
        st, r, _ = http("POST", "/api/v1/console/login",
                        payload={"username": "operator",
                                 "password": "CrSmoke#2026x"})
        operator = r.get("token")

    # 1. 无 current 三态
    st, r, _ = http("GET", "/api/v1/client/manifest", terminal=TOKEN)
    check("无 current manifest=null", st == 200 and r.get("manifest") is None)
    st, r, _ = http("GET", "/api/v1/client/update-manifest", terminal=TOKEN)
    check("无 current update-manifest 扁平 null", st == 200
          and r.get("latest_version") is None
          and r.get("download_url") is None
          and r.get("sha256") is None, str(r)[:120])
    st, r, _ = http("GET", "/download/client/setup")
    check("无 current 通用下载 404", st == 404, str(r)[:80])
    st, r, _ = http("POST", "/api/v1/console/client/custom-name", token=admin,
                    payload={"server": "http://127.0.0.1:%d" % PORT})
    check("无 current 定制名 400", st == 400, str(r)[:80])

    # 2. 上传（raw bytes PUT）+ 400 矩阵
    body40 = b"PK\x03\x04" + b"A" * 128
    st, r, _ = http("PUT",
                    "/api/v1/console/client/releases/4.0.0"
                    "?filename=EyeTerm_Setup_x64_4.0.0.exe&note=%E9%A6%96%E7%89%88",
                    token=admin, raw=body40)
    check("上传 4.0.0", st == 200 and r.get("ok")
          and r["release"]["version"] == "4.0.0"
          and r["release"]["size"] == len(body40), str(r)[:120])
    st, r, _ = http("PUT", "/api/v1/console/client/releases/bad_ver"
                    "?filename=a.exe", token=admin, raw=b"x")
    check("坏版本号 400", st == 400)
    st, r, _ = http("PUT", "/api/v1/console/client/releases/1.0.1",
                    token=admin, raw=b"x")
    check("缺 filename 400", st == 400)
    st, r, _ = http("PUT", "/api/v1/console/client/releases/1.0.2"
                    "?filename=a.exe", token=admin)
    check("空 body 400", st == 400)

    # 3. 发布与回滚
    st, r, _ = http("GET", "/api/v1/console/client/releases", token=admin)
    check("列表含 4.0.0 未发布态", st == 200
          and len(r.get("releases") or []) == 1
          and r["releases"][0]["is_current"] is False)
    rid = r["releases"][0]["id"]
    st, r, _ = http("POST",
                    "/api/v1/console/client/releases/%d/set-current" % rid,
                    token=admin)
    check("set-current 200 + is_current", st == 200
          and r["release"]["rollback_flag"] == 0)

    st, r, _ = http("GET", "/api/v1/client/manifest", terminal=TOKEN)
    m = r.get("manifest") or {}
    check("manifest 字段齐全", st == 200 and m.get("latest_version") == "4.0.0"
          and m.get("download_url") == "/download/client/setup"
          and len(m.get("sha256") or "") == 64
          and m.get("size") == len(body40)
          and m.get("release_note") == "首版", str(m)[:160])

    st, r, _ = http("GET",
                    "/api/v1/client/update-manifest?version=3.9.0",
                    terminal=TOKEN)
    check("update-manifest 扁平形状（TBC-002）", st == 200
          and r.get("latest_version") == "4.0.0"
          and r.get("download_url") == "/download/client/setup"
          and len(r.get("sha256") or "") == 64
          and r.get("size") == len(body40)
          and r.get("ok") is True, str(r)[:160])

    st, r, _ = http("POST", "/api/v1/terminals/WIN-CR-SMOKE/heartbeat",
                    terminal=TOKEN, payload={})
    check("心跳捎带 latest_version", st == 200
          and r.get("latest_version") == "4.0.0", str(r)[:120])

    # 4. 通用下载
    st, body, hdrs = http("GET", "/download/client/setup")
    disp = (hdrs.get("Content-Disposition")
            or hdrs.get("content-disposition") or "")
    check("通用下载 200 + 内容一致", st == 200 and body == body40)
    check("通用下载 Content-Disposition 原始文件名",
          "EyeTerm_Setup_x64_4.0.0.exe" in disp, disp)

    # 5. 上传 4.1.0 → 发布 → 回滚到 4.0.0
    body41 = b"PK\x03\x04" + b"B" * 64
    st, r, _ = http("PUT", "/api/v1/console/client/releases/4.1.0"
                    "?filename=setup_4.1.0.exe", token=admin, raw=body41)
    rid41 = r["release"]["id"]
    http("POST", "/api/v1/console/client/releases/%d/set-current" % rid41,
         token=admin)
    st, r, _ = http("POST",
                    "/api/v1/console/client/releases/%d/set-current" % rid,
                    token=admin)
    check("回滚指回旧版 rollback_flag=1", st == 200
          and r["release"]["rollback_flag"] == 1
          and r["release"]["version"] == "4.0.0")
    st, r, _ = http("GET", "/api/v1/client/manifest", terminal=TOKEN)
    check("回滚后 manifest latest=4.0.0",
          (r.get("manifest") or {}).get("latest_version") == "4.0.0")

    # 6. 定制名生成 + 票据
    srv = "http://172.17.5.215:%d" % PORT
    st, r, _ = http("POST", "/api/v1/console/client/custom-name", token=admin,
                    payload={"server": srv})
    fn = r.get("filename") or ""
    check("定制名协议格式", st == 200 and _CUSTOM_FMT.match(fn) is not None,
          fn)
    check("定制名 base64url 不含 token 明文", TOKEN not in fn
          and TOKEN not in str(r.get("download_url") or ""))
    check("download_url 带票据 + 有效期", (r.get("download_url") or "")
          .startswith("/download/client/setup?ticket=")
          and r.get("expires_in") == 600)
    dl = r.get("download_url")
    st, body, hdrs = http("GET", dl)
    disp = (hdrs.get("Content-Disposition")
            or hdrs.get("content-disposition") or "")
    check("票据下载 200 + 定制名响应头", st == 200 and fn in disp
          and body == body40, disp)
    st, _, _ = http("GET", dl)
    check("票据一次性（二用 404）", st == 404)

    # 7. custom-name 400 矩阵
    st, r, _ = http("POST", "/api/v1/console/client/custom-name", token=admin,
                    payload={})
    check("缺 server 400", st == 400)
    st, r, _ = http("POST", "/api/v1/console/client/custom-name", token=admin,
                    payload={"server": "ftp://x"})
    check("非法 server 400", st == 400)

    # 8. operator 403 矩阵 + manifest 401
    st, r, _ = http("GET", "/api/v1/console/client/releases", token=operator)
    check("operator 列表 403", st == 403)
    st, r, _ = http("PUT", "/api/v1/console/client/releases/9.9.9"
                    "?filename=a.exe", token=operator, raw=b"x")
    check("operator 上传 403", st == 403)
    st, r, _ = http("POST", "/api/v1/console/client/custom-name",
                    token=operator, payload={"server": srv})
    check("operator 定制名 403", st == 403)
    st, r, _ = http("GET", "/api/v1/client/manifest", terminal="wrong-tok")
    check("manifest 错 token 401", st == 401)
    st, r, _ = http("GET", "/api/v1/client/update-manifest",
                    terminal="wrong-tok")
    check("update-manifest 错 token 401", st == 401)

    # 9. 页面 200
    st, _, _ = http("GET", "/")
    check("控制台页面 200", st == 200)


if __name__ == "__main__":
    sys.exit(main())
