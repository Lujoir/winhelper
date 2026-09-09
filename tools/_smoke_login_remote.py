# -*- coding: utf-8 -*-
"""远程冒烟：登录改造全链路（临时脚本，凭据走环境变量）。

覆盖：登录 → 会话鉴权 → 改密踢端 → 重启会话保持（SQLite 持久）→
      连续失败锁定 423 → 管理员解锁恢复。
"""
import os
import time

import paramiko
import requests

BASE = os.environ.get("ETP_BASE_URL", "http://172.17.5.215:18090")
API = BASE + "/api/v1"
PWD = os.environ["ETP_ADMIN_PWD"]
PWD2 = "Temp#Smoke2026a"

_n = [0, 0]


def check(name, cond, extra=""):
    _n[0 if cond else 1] += 1
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))


def login(pwd):
    return requests.post(API + "/console/login",
                         json={"username": "admin", "password": pwd}, timeout=10)


def get_terminals(token):
    return requests.get(API + "/console/terminals",
                        headers={"X-ETP-Console-Token": token or ""}, timeout=10)


def change_pwd(token, old, new):
    return requests.post(API + "/console/password",
                         json={"old_password": old, "new_password": new,
                               "confirm_password": new},
                         headers={"X-ETP-Console-Token": token or ""}, timeout=10)


# 1. 登录
r = login(PWD)
check("1 login ok", r.status_code == 200 and r.json().get("ok"),
      "http=%s" % r.status_code)
token1 = r.json().get("token")

# 2. 会话鉴权
r = get_terminals(token1)
check("2 terminals with token1", r.status_code == 200, "http=%s" % r.status_code)

# 3. 改密（吊销其它会话、保留当前）
r = change_pwd(token1, PWD, PWD2)
check("3 change password", r.status_code == 200 and r.json().get("ok"),
      r.text[:80])

# 4. 旧口令拒绝
r = login(PWD)
check("4 old password rejected", r.status_code == 401, "http=%s" % r.status_code)

# 5. 新口令登录
r = login(PWD2)
check("5 new password login", r.status_code == 200, "http=%s" % r.status_code)
token2 = r.json().get("token")

# 6. 改回原口令（吊销 S1/token1）
r = change_pwd(token2, PWD2, PWD)
check("6 revert password", r.status_code == 200, r.text[:80])

# 7. 重启服务（SQLite 会话持久化验证）
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(os.environ["ETP_SSH_HOST"], port=int(os.environ["ETP_SSH_PORT"]),
            username=os.environ["ETP_SSH_USER"],
            password=os.environ["ETP_SSH_PASS"], timeout=20)
_, out, _ = ssh.exec_command("systemctl restart terminal-platform; sleep 9; "
                             "systemctl is-active terminal-platform", timeout=60)
print("$ restart ->", out.read().decode().strip())

r = get_terminals(token1)
check("7a token1 revoked (password change kick)", r.status_code == 401,
      "http=%s" % r.status_code)
r = get_terminals(token2)
check("7b token2 survives restart (sqlite session)", r.status_code == 200,
      "http=%s" % r.status_code)

# 8. 等待 IP 限速窗口重置（前面已消耗约 7 次配额）
print("sleep 65s for IP throttle window reset ...")
time.sleep(65)

# 9. 连续 5 次失败 → 账号锁定 423
for i in range(5):
    requests.post(API + "/console/login",
                  json={"username": "admin", "password": "bad%d" % i}, timeout=10)
r = login(PWD)
check("9 locked 423 after 5 failures", r.status_code == 423,
      "http=%s" % r.status_code)

# 10. 管理员解锁 + 修正 password_algo 标记（哈希串本身已是 PBKDF2）
sql = ("UPDATE console_users SET failed_attempts=0, first_failed_at=NULL, "
       "locked_until=NULL WHERE username='admin'; "
       "UPDATE console_users SET password_algo='pbkdf2_sha256' "
       "WHERE password LIKE 'pbkdf2%';")
_, out, err = ssh.exec_command(
    "python3 -c \"import sqlite3;c=sqlite3.connect("
    "'/data/terminal-platform/data/console_auth.db');"
    "c.executescript('''%s''');c.commit();"
    "print('UNLOCKED')\"" % sql, timeout=30)
print("$ unlock ->", out.read().decode().strip(), err.read().decode().strip())
ssh.close()

# 11. 解锁后登录恢复
r = login(PWD)
check("11 login restored after unlock", r.status_code == 200,
      "http=%s" % r.status_code)

print("RESULT: %d pass, %d fail" % (_n[0], _n[1]))
