# -*- coding: utf-8 -*-
"""config 重建后最终验证：PBKDF2 登录、业务库数据、终端心跳（token 正确性）。"""
import os
import time

import requests

API = "http://172.17.5.215:18090/api/v1"
PWD = os.environ["ETP_ADMIN_PWD"]

n = [0, 0]


def check(name, cond, extra=""):
    n[0 if cond else 1] += 1
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))


r = requests.post(API + "/console/login",
                  json={"username": "admin", "password": PWD}, timeout=10)
check("login(PBKDF2)", r.status_code == 200 and r.json().get("ok"),
      "http=%s" % r.status_code)
tok = r.json().get("token")

r = requests.get(API + "/console/terminals",
                 headers={"X-ETP-Console-Token": tok or ""}, timeout=10)
ts = r.json().get("terminals", []) if r.ok else []
check("terminals data intact", r.status_code == 200 and len(ts) >= 1,
      "count=%s first=%s" % (len(ts), ts[0].get("terminal_id") if ts else "-"))

print("waiting up to 70s for terminal heartbeat ...")
ok_hb = False
deadline = time.time() + 70
while time.time() < deadline:
    time.sleep(10)
    r = requests.get(API + "/console/terminals",
                     headers={"X-ETP-Console-Token": tok or ""}, timeout=10)
    ts = r.json().get("terminals", []) if r.ok else []
    online = [t for t in ts if t.get("online")]
    if online:
        ok_hb = True
        check("terminal heartbeat online (token OK)", True, online[0].get("terminal_id"))
        break
if not ok_hb:
    check("terminal heartbeat online (token OK)", False, "no online terminal in 70s")

print("RESULT: %d pass, %d fail" % (n[0], n[1]))
