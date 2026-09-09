# -*- coding: utf-8 -*-
"""服务器端一次性脚本：设置 admin 哈希口令（口令经 stdin 传入，不落盘不留痕）。
由部署工具经 sftp 上传到 /tmp，执行后即删。"""
import os
import sqlite3
import sys

sys.path.insert(0, "/data/terminal-platform/app/server")
import auth_upgrade as a

AUTH_DB = "/data/terminal-platform/data/console_auth.db"

pwd = sys.stdin.readline().rstrip("\r\n")
assert pwd, "empty password from stdin"

h = a.hash_password(pwd)
ok, _ = a.verify_and_upgrade(h, pwd)
assert ok, "self verify failed"

c = sqlite3.connect(AUTH_DB)
c.execute(
    "CREATE TABLE IF NOT EXISTS console_users "
    "(id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " username TEXT UNIQUE NOT NULL, password TEXT NOT NULL)")
row = c.execute("SELECT id FROM console_users WHERE username='admin'").fetchone()
if row:
    c.execute("UPDATE console_users SET password=?, password_algo=?, "
              "password_updated_at=?, failed_attempts=0, first_failed_at=NULL, "
              "locked_until=NULL WHERE username='admin'",
              (h, a.HASH_SCHEME, a.now()))
else:
    c.execute("INSERT INTO console_users (username,password,password_algo,"
              "password_updated_at) VALUES ('admin',?,?,?)",
              (h, a.HASH_SCHEME, a.now()))
c.commit()
row = c.execute("SELECT length(password), password_algo "
                "FROM console_users WHERE username='admin'").fetchone()
print("ADMIN_SET len=%s algo=%s self_verify=%s" % (row[0], row[1], ok))
c.close()
