# -*- coding: utf-8 -*-
"""本地冒烟：auth_upgrade 与 api.py 集成链路（临时脚本，用后即删）。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "server-platform", "server")))
import auth_upgrade as auth  # noqa: E402

DB = os.path.join(tempfile.gettempdir(), "_etp_auth_smoke.db")
for f in (DB, DB + "-wal", DB + "-shm"):
    if os.path.exists(f):
        os.remove(f)
auth.DB_PATH = DB

pwd = "Smoke@Test123"

conn = auth.get_conn()
conn.executescript(
    "CREATE TABLE console_users (id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " username TEXT UNIQUE NOT NULL, password TEXT NOT NULL);")
conn.commit()

# 走真实幂等迁移，建全 5 张支撑表 + 策略默认值（与生产流程一致）
import migrate_login_upgrade as mig  # noqa: E402
assert mig.migrate(DB, "console_users", dry_run=False) == 0, "migrate failed"

conn = auth.get_conn()
conn.execute("INSERT INTO console_users (username, password) VALUES ('admin', ?)",
             (auth.hash_password(pwd),))
conn.commit()

# 1. 正确口令登录
r = auth.authenticate("admin", pwd, "127.0.0.1", "smoke-agent/1.0")
assert r.ok, "login failed: %s %s" % (r.reason, r.message)
assert r.token
print("1. login OK must_change=%s" % r.must_change_password)

# 2. 会话解析
sess = auth.resolve_session(r.token, "127.0.0.1", "smoke-agent/1.0")
assert sess is not None, "session resolve failed"
print("2. resolve_session OK user=%s sid=%s" % (sess["username"], sess["id"]))

# 3. 错误口令拒绝
r2 = auth.authenticate("admin", "wrong-pass", "127.0.0.2", "smoke-agent/1.0")
assert not r2.ok
print("3. wrong password rejected (%s) http=%s" % (r2.reason, r2.http_status))

# 4. 改密（吊销其它会话、保留当前）
r3 = auth.change_password(sess["user_id"], pwd, "NewPwd#2026x", "NewPwd#2026x",
                          "127.0.0.1", "smoke-agent/1.0",
                          current_session_id=sess["id"])
assert r3.ok, "change_password failed: %s %s" % (r3.reason, r3.message)
print("4. change_password OK:", r3.message)

# 5. 新口令登录
r4 = auth.authenticate("admin", "NewPwd#2026x", "127.0.0.1", "smoke-agent/1.0")
assert r4.ok
print("5. login with new password OK")

# 6. 连续 5 次失败触发账号锁定（423）
for i in range(5):
    auth.authenticate("admin", "bad-%d" % i, "127.0.0.3", "smoke-agent/1.0")
r5 = auth.authenticate("admin", "NewPwd#2026x", "127.0.0.3", "smoke-agent/1.0")
assert not r5.ok and r5.http_status in (423, 429), \
    "expect lock/limit, got ok=%s http=%s" % (r5.ok, r5.http_status)
print("6. after 5 failures locked: reason=%s http=%s retry_after=%s"
      % (r5.reason, r5.http_status, r5.retry_after))

print("LOCAL SMOKE ALL PASS")
