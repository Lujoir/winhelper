#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第三方数据源 mock 冒烟（自包含隔离环境，零凭据；ADR-043）。

脚本内 spawn 本地服务（同 smoke_client_release 口径：临时 config +
auth 空库自举 + 白名单引导 + 端口预检）。handler 层覆盖：
聚合降级（火绒无绑定 / 画方未配置）→ 404/400 → operator 403 →
isolate 密码重校验（错密码 401+审计 / 正确密码 501+审计意图）→
isolated 桩 → 页面 200。

环境变量：ETP_TP_PORT（默认 18293）。
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "server"))

ADMIN_PWD = "TpSmoke#2026x"
TOKEN = "tp-smoke-token"
PORT = int(os.environ.get("ETP_TP_PORT", "18293"))
API_BASE = "http://127.0.0.1:%d" % PORT

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" | " + detail) if detail and not cond else ""))


def http(method, path, token=None, payload=None, terminal=None):
    h = {}
    if token:
        h["X-ETP-Console-Token"] = token
    if terminal:
        h["X-ETP-Token"] = terminal
    if payload is not None:
        h["Content-Type"] = "application/json"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    else:
        body = None
    req = urllib.request.Request(API_BASE + path, data=body, headers=h,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
            try:
                return resp.status, json.loads(data.decode("utf-8"))
            except ValueError:
                return resp.status, data
    except urllib.error.HTTPError as e:
        data = e.read()
        try:
            return e.code, json.loads(data.decode("utf-8"))
        except ValueError:
            return e.code, data


def wait_health(timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            st, r = http("GET", "/api/v1/health")
            if st == 200 and r.get("ok"):
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def bootstrap_auth(auth_db_path):
    """空库自举：最小 auth 表 + admin（与 smoke_client_release 同款）。"""
    import auth_upgrade as auth
    auth.DB_PATH = auth_db_path
    auth._local = threading.local()
    conn = auth.get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS console_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
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
        idle_expires_at INTEGER NOT NULL,
        absolute_expires_at INTEGER NOT NULL, client_ip TEXT, ua_hash TEXT,
        revoked_at INTEGER, revoke_reason TEXT,
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
        "INSERT OR IGNORE INTO console_users(username, password,"
        " password_algo, password_updated_at, role, status)"
        " VALUES('admin', ?, 'pbkdf2_sha256', strftime('%s','now'),"
        " 'admin', 'active')", (hashed,))
    conn.commit()
    auth.invalidate_policy_cache()


def main():
    import socket
    probe = socket.socket()
    probe.settimeout(1)
    busy = probe.connect_ex(("127.0.0.1", PORT)) == 0
    probe.close()
    if busy:
        print("FATAL: port %d already busy (orphan service? kill it first)"
              % PORT)
        return 2
    tmp = tempfile.mkdtemp(prefix="etp_tp_smoke_")
    data_dir = os.path.join(tmp, "data")
    os.makedirs(data_dir, exist_ok=True)
    cfg_path = os.path.join(tmp, "config.json")
    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump({"port": PORT, "terminal_token": TOKEN,
                   "data_dir": data_dir, "session_ttl_hours": 8}, fh)
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
            return 2
        conn = sqlite3.connect(os.path.join(data_dir, "eyeterm.db"),
                               timeout=10)
        conn.execute("INSERT OR IGNORE INTO whitelist(cidr, note, enabled,"
                     " created_at) VALUES('127.0.0.1/32', 'tp-smoke', 1, 0)")
        conn.commit()
        conn.close()
        run(data_dir)
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
                print(fh.read()[-3500:])
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n=== thirdparty smoke: pass %d / fail %d ==="
          % (len(PASSED), len(FAILED)))
    if FAILED:
        print("failed: %s" % ", ".join(FAILED))
        return 1
    print("ALL SMOKE TESTS PASSED")
    return 0


def run(data_dir):
    print("=== third-party mock smoke (port %d) ===" % PORT)

    # 准备：注册终端（带自报 asset.network IP/MAC；连接源 IP 模拟 NAT 场景）
    st, r = http("POST", "/api/v1/terminals/register", terminal=TOKEN,
                 payload={"terminal_id": "WIN-TP-SMOKE",
                          "terminal_type": "windows", "hostname": "TP-SMOKE",
                          "os_info": "Windows 11", "client_version": "test",
                          "ip": "172.17.90.33",
                          "asset": {"network": [
                              {"name": "以太网", "ip": "10.20.30.40",
                               "mac": "AA-BB-CC-DD-EE-10"}]}})
    check("register terminal", st == 200 and r.get("ok"))
    st, r = http("POST", "/api/v1/console/login",
                 payload={"username": "admin", "password": ADMIN_PWD})
    check("admin login", st == 200 and r.get("ok"))
    admin = r.get("token")
    st, r = http("POST", "/api/v1/console/sysadmin/users", token=admin,
                 payload={"username": "operator", "password": "TpSmoke#2026o",
                          "role": "operator"})
    op_id = r.get("id")
    st, r = http("POST", "/api/v1/console/login",
                 payload={"username": "operator", "password": "TpSmoke#2026o"})
    if r.get("must_change_password"):
        http("POST", "/api/v1/console/password", token=r.get("token"),
             payload={"old_password": "TpSmoke#2026o",
                      "new_password": "TpSmoke#2026z",
                      "confirm_password": "TpSmoke#2026z"})
        st, r = http("POST", "/api/v1/console/login",
                     payload={"username": "operator",
                              "password": "TpSmoke#2026z"})
    operator = r.get("token")
    check("operator login", st == 200 and bool(operator))

    # 1. 聚合端点：降级路径（火绒无绑定 / 画方未配置 / 骨架标记）
    st, r = http("GET", "/api/v1/console/thirdparty/WIN-TP-SMOKE",
                 token=admin)
    check("聚合端点 200", st == 200 and r.get("ok"))
    hr = r.get("huorong") or {}
    check("火绒块未关联降级", hr.get("linked") is False)
    nad = r.get("nad") or {}
    check("画方块未配置降级", nad.get("available") is False
          and nad.get("reason") == "not_configured"
          and nad.get("matched") is False and nad.get("evidence") == [])
    # IP 口径：查询键取自报 IP（asset.network），连接源仅兜底并透出双值
    q = nad.get("query") or {}
    check("nad 查询键自报 IP 优先", q.get("ip") == "10.20.30.40"
          and q.get("asset_ip") == "10.20.30.40"
          and q.get("source_ip") == "172.17.90.33", str(q))
    check("骨架标记 online_log/isolate", (r.get("online_log") or {})
          .get("available") is False and (r.get("isolate") or {})
          .get("available") is False)
    check("终端摘要回显", (r.get("terminal") or {}).get("hostname")
          == "TP-SMOKE")

    # 2. 404 / 400 矩阵
    st, r = http("GET", "/api/v1/console/thirdparty/NO-SUCH-TERMINAL",
                 token=admin)
    check("聚合端点未知终端 404", st == 404)
    st, r = http("GET",
                 "/api/v1/console/thirdparty/WIN-TP-SMOKE/online-log",
                 token=admin)
    check("online-log 缺参 400", st == 400)
    st, r = http("GET",
                 "/api/v1/console/thirdparty/WIN-TP-SMOKE/online-log"
                 "?ip=10.20.30.40&start=2026-09-10&end=2026-09-17",
                 token=admin)
    check("online-log 骨架降级", st == 200 and r.get("available") is False
          and "尚未接入" in str(r.get("reason")))
    check("online-log 空窗口时间线降级",
          (r.get("platform_timeline") or {}).get("available") is False)
    st, r = http("GET", "/api/v1/console/thirdparty/isolated", token=admin)
    check("isolated 骨架降级", st == 200 and r.get("available") is False
          and r.get("terminals") == [])

    # 平台通信时间线：终端上报一次指标后应出现数据点
    st, r = http("POST", "/api/v1/terminals/WIN-TP-SMOKE/metrics",
                 terminal=TOKEN,
                 payload={"cpu": {"percent": 33.3},
                          "mem": {"used_percent": 44.4}})
    check("metrics 上报", st == 200 and r.get("ok"))
    st, r = http("GET",
                 "/api/v1/console/thirdparty/WIN-TP-SMOKE/online-log"
                 "?ip=10.20.30.40", token=admin)
    tl = r.get("platform_timeline") or {}
    check("时间线出现数据点", tl.get("available") is True
          and (tl.get("total") or 0) >= 1 and len(tl.get("points") or []) >= 1,
          str(tl)[:160])
    p0 = (tl.get("points") or [{}])[0]
    check("时间线点含 cpu/mem/gap", p0.get("cpu") == 33.3
          and p0.get("mem") == 44.4 and "gap" in p0 and "ts" in p0,
          str(p0))

    # 3. isolate 权限与密码重校验
    st, r = http("POST", "/api/v1/console/thirdparty/WIN-TP-SMOKE/isolate",
                 token=operator,
                 payload={"action": "block", "password": "x" * 12})
    check("operator isolate 403", st == 403)
    st, r = http("POST", "/api/v1/console/thirdparty/WIN-TP-SMOKE/isolate",
                 token=admin, payload={"action": "freeze"})
    check("非法 action 400", st == 400)
    st, r = http("POST", "/api/v1/console/thirdparty/WIN-TP-SMOKE/isolate",
                 token=admin, payload={"action": "block"})
    check("缺密码 400", st == 400)
    st, r = http("POST", "/api/v1/console/thirdparty/WIN-TP-SMOKE/isolate",
                 token=admin, payload={"action": "block",
                                       "password": "wrong-pwd-123"})
    check("错误密码 403（401 保留给会话语义）", st == 403, str(r)[:100])
    st, r = http("POST", "/api/v1/console/thirdparty/WIN-TP-SMOKE/isolate",
                 token=admin, payload={"action": "block",
                                       "password": ADMIN_PWD})
    check("正确密码 → 画方待接入 501", st == 501, str(r)[:120])
    st, r = http("POST", "/api/v1/console/thirdparty/NO-SUCH/isolate",
                 token=admin, payload={"action": "block",
                                       "password": ADMIN_PWD})
    check("isolate 未知终端 404", st == 404)

    # 4. 审计直查（隔离动作留痕：失败与意图）
    st, r = http("GET", "/api/v1/console/audit?limit=50", token=admin)
    acts = [a.get("action") for a in (r.get("audit") or [])]
    check("审计直查：密码失败留痕", "tp_isolate_auth_fail" in acts,
          str(acts))
    check("审计直查：操作意图留痕", "tp_isolate_attempt" in acts)

    # 5. 页面 200
    st, _ = http("GET", "/")
    check("控制台页面 200", st == 200)


if __name__ == "__main__":
    sys.exit(main())
