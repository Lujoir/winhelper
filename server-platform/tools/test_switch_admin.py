#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""交换机管理（ADR-026）本地单测：默认凭据加密存取/留空保持/列表脱敏/CRUD/审计。

零凭据临时库；运行后自清理。"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "server"))

import api as api_mod                                  # noqa: E402
import auth_upgrade as auth                            # noqa: E402
import store as store_mod                              # noqa: E402
import threading                                       # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_sysadmin import BOOTSTRAP                     # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" | " + detail) if detail else ""))


class FakeSettings:
    def __init__(self, d):
        self.d = d

    def get(self, k, default=None):
        return self.d.get(k, default)


def main():
    tmp = tempfile.mkdtemp(prefix="etp_sw_")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n=== switch admin tests: pass %d / fail %d ==="
          % (len(PASSED), len(FAILED)))
    return 0 if not FAILED else 1


def run(tmp):
    auth.DB_PATH = os.path.join(tmp, "auth.db")
    auth._local = threading.local()          # 重置线程局部连接
    conn = auth.get_conn()
    conn.executescript(BOOTSTRAP)
    conn.commit()
    auth.invalidate_policy_cache()
    # 引导 admin 会话（走真实 auth_upgrade，PBKDF2）
    import secretsbox, settings as settings_mod  # noqa: E402
    box = secretsbox.SecretsBox(os.path.join(tmp, "machine.key"))
    db = os.path.join(tmp, "t.db")
    st = store_mod.Store(db, config_token="cfg-token")
    st.register_terminal("T-SW", "windows", "host", "Windows 11",
                         "1.0.0", "127.0.0.1")
    st.settings_set("__probe__", "x", 0)
    settings = settings_mod.SettingsStore(st, box)
    ctx = api_mod.ApiContext(st, {"terminal_token": "cfg-token"},
                             settings=settings)
    c = st._conn.cursor()
    c.execute("INSERT OR IGNORE INTO whitelist(cidr,note,enabled,created_at)"
              " VALUES('127.0.0.1','test',1,0)")
    st._conn.commit()
    c.close()
    # BOOTSTRAP 预置 admin（role=operator）→ 提升 + 重置为已知口令
    conn.execute("UPDATE console_users SET role='admin' WHERE username='admin'")
    conn.commit()
    row = conn.execute(
        "SELECT * FROM console_users WHERE username='admin'").fetchone()
    admin_id = row["id"]
    rr = auth.admin_reset_password(admin_id, "Root#2026xyz", "test",
                                   force_change=False)
    check("admin 口令重置", rr.ok, getattr(rr, "message", ""))
    atok, _sid = auth.create_session(
        auth.get_conn().execute(
            "SELECT * FROM console_users WHERE id=?", (admin_id,)).fetchone(),
        "127.0.0.1", "test")
    ah = {"x-etp-console-token": atok}

    def dispatch(method, path, payload=None, headers=None):
        body = json.dumps(payload).encode() if payload is not None else b""
        try:
            status, raw, _ct = api_mod.dispatch(ctx, method, path, {},
                                                headers or {}, body,
                                                "127.0.0.1")
            return status, json.loads(raw.decode("utf-8"))
        except api_mod.ApiError as exc:   # HTTP 层会将 ApiError 转状态码
            return exc.status, {"ok": False, "error": exc.message}

    # [1] 默认凭据：未配置态
    print("[1] 默认凭据 GET 未配置态")
    code, body = dispatch("GET", "/api/v1/console/sysadmin/switch-default", {},
                          ah)
    d = body.get("switch_default") or {}
    check("未配置返回空 username 与 password_set=False",
          code == 200 and d.get("username") == ""
          and d.get("password_set") is False, str(body))

    # [2] PUT 设置默认凭据 → 密文非明文 → GET 解密回读
    print("[2] 默认凭据 PUT 加密落库")
    code, body = dispatch("PUT", "/api/v1/console/sysadmin/switch-default",
                          {"username": "reader",
                           "password": "3@Ww18_Bu9xn"}, ah)
    check("PUT 200", code == 200 and "username" in body.get("changed", [])
          and "password" in body.get("changed", []), str(body))
    raw = st.settings_get("switch.default_password")
    check("库中为密文（不含明文）", raw is not None
          and "3@Ww18_Bu9xn" not in str(raw["value"]), str(raw["value"])[:40])
    check("明文不落 settings 明文列", raw["value"] != "3@Ww18_Bu9xn")
    code, body = dispatch("GET", "/api/v1/console/sysadmin/switch-default",
                          {}, ah)
    d = body.get("switch_default") or {}
    check("GET 回读 username + password_set=True",
          d.get("username") == "reader" and d.get("password_set") is True)
    check("GET 响应不含明文密码", "3@Ww18_Bu9xn" not in json.dumps(body))

    # [3] PUT 密码留空 = 保持不变
    print("[3] 默认凭据留空保持")
    code, body = dispatch("PUT", "/api/v1/console/sysadmin/switch-default",
                          {"username": "reader2", "password": ""}, ah)
    check("留空 PUT 200 且 changed 不含 password",
          code == 200 and "password" not in body.get("changed", []))
    code, body = dispatch("GET", "/api/v1/console/sysadmin/switch-default",
                          {}, ah)
    check("username 更新 + 密码保持",
          body["switch_default"]["username"] == "reader2"
          and body["switch_default"]["password_set"] is True)
    code, body = dispatch("PUT", "/api/v1/console/sysadmin/switch-default",
                          {"username": "reader", "password": "New$Pwd456"},
                          ah)
    code, body = dispatch("GET", "/api/v1/console/sysadmin/switch-default",
                          {}, ah)
    check("再次设置后 GET 不泄露新明文",
          "New$Pwd456" not in json.dumps(body))

    # [4] switches CRUD
    print("[4] 交换机台账 CRUD")
    code, body = dispatch("POST", "/api/v1/console/sysadmin/switches",
                          {"name": "SW0691-核心", "ip": "172.17.254.1",
                           "ssh_port": 22, "username": "reader",
                           "password": "Sw#Pwd001", "brand": "H3C"}, ah)
    check("创建 200", code == 200 and body.get("id"), str(body))
    sid = body.get("id")
    code, body = dispatch("POST", "/api/v1/console/sysadmin/switches",
                          {"name": "BAD", "ip": "999.1.1.1",
                           "username": "r", "password": "x"}, ah)
    check("非法 IP 400", code == 400, "code=%s" % code)
    code, body = dispatch("POST", "/api/v1/console/sysadmin/switches",
                          {"name": "BAD2", "ip": "172.17.254.2",
                           "username": "r"}, ah)
    check("缺 password 400", code == 400, "code=%s" % code)
    code, body = dispatch("GET", "/api/v1/console/sysadmin/switches", {},
                          ah)
    row = [s for s in body.get("switches", []) if s.get("id") == sid]
    check("列表返回且不含明文密码", code == 200 and row
          and "Sw#Pwd001" not in json.dumps(body))
    check("列表脱敏 password=****", row and row[0].get("password") == "****"
          and "password_set" not in row[0]
          and "password_enc" not in row[0])
    code, body = dispatch("PUT", "/api/v1/console/sysadmin/switches/%d" % sid,
                          {"name": "SW0691-核心改名", "password": ""}, ah)
    check("PUT 留空密码保持 200", code == 200, str(body))
    c = st._conn.cursor()
    c.execute("SELECT password_enc FROM switches WHERE id=?", (sid,))
    enc = c.fetchone()["password_enc"]
    c.close()
    check("密码密文未变且可解密回原值", "Sw#Pwd001" not in enc
          and settings.decrypt(enc) == "Sw#Pwd001")
    code, body = dispatch("PUT", "/api/v1/console/sysadmin/switches/%d" % sid,
                          {"name": "SW0691", "password": "Sw#Pwd002"}, ah)
    c = st._conn.cursor()
    c.execute("SELECT password_enc FROM switches WHERE id=?", (sid,))
    enc2 = c.fetchone()["password_enc"]
    c.close()
    check("PUT 新密码加密更新", enc2 != enc
          and settings.decrypt(enc2) == "Sw#Pwd002")
    code, body = dispatch("PUT", "/api/v1/console/sysadmin/switches/9999",
                          {"name": "x"}, ah)
    check("PUT 不存在 404", code == 404)
    code, body = dispatch("DELETE",
                          "/api/v1/console/sysadmin/switches/%d" % sid, {},
                          ah)
    check("删除 200", code == 200)
    code, body = dispatch("DELETE",
                          "/api/v1/console/sysadmin/switches/%d" % sid, {},
                          ah)
    check("重复删除 404", code == 404)

    # [5] 权限与审计
    print("[5] 权限与审计")
    r_op = auth.create_user("op_sw", "OpUser#2026a", role="operator")
    otok, _ = auth.create_session(
        auth.get_conn().execute(
            "SELECT * FROM console_users WHERE id=?",
            (r_op["user_id"],)).fetchone(), "127.0.0.1", "test")
    code, body = dispatch("GET", "/api/v1/console/sysadmin/switches", {},
                          {"x-etp-console-token": otok})
    check("operator 访问 403", code == 403, "code=%s" % code)
    c = auth.get_conn().cursor()
    c.execute("SELECT COUNT(*) AS n FROM console_audit_log"
              " WHERE event_type IN (?,?,?,?)",
              (auth.Ev.SWITCH_DEFAULT_UPDATED, auth.Ev.SWITCH_CREATED,
               auth.Ev.SWITCH_UPDATED, auth.Ev.SWITCH_DELETED))
    n = c.fetchone()["n"]
    c.close()
    check("审计落库（默认凭据/增/改/删）", n >= 4, "rows=%d" % n)
    c = auth.get_conn().cursor()
    c.execute("SELECT detail FROM console_audit_log"
              " WHERE event_type=? ORDER BY id DESC LIMIT 1",
              (auth.Ev.SWITCH_CREATED,))
    detail = c.fetchone()["detail"]
    c.close()
    check("审计不记明文密码", "Sw#Pwd001" not in str(detail))


if __name__ == "__main__":
    sys.exit(main())
