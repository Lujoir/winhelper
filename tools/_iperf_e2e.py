# -*- coding: utf-8 -*-
"""iperf 打流端到端联调：launch → 终端心跳拉命令执行 → 结果回传 → done。"""
import json
import os
import sys
import time

import requests

API = "http://172.17.5.215:18090/api/v1"
PWD = os.environ["ETP_ADMIN_PWD"]
TEST_TYPE = sys.argv[1] if len(sys.argv) > 1 else "bandwidth_tcp"
DURATION = int(sys.argv[2]) if len(sys.argv) > 2 else 5
WAIT_MAX = int(sys.argv[3]) if len(sys.argv) > 3 else 180

r = requests.post(API + "/console/login",
                  json={"username": "admin", "password": PWD}, timeout=10)
assert r.status_code == 200, r.text
H = {"X-ETP-Console-Token": r.json()["token"]}

tids = [t["terminal_id"] for t in requests.get(
    API + "/console/terminals", headers=H, timeout=10).json()["terminals"]]
print("terminals:", tids)
tid = "WIN-Jun-office-PC"
assert tid in tids, "terminal offline"

r = requests.post(API + "/console/nettest/launch",
                  json={"terminal_id": tid, "test_type": TEST_TYPE,
                        "duration_sec": DURATION}, headers=H, timeout=10)
print("launch:", r.status_code, r.text[:200])
assert r.status_code == 200, r.text
task_id = r.json()["task_id"]

print("polling task %s (max %ss) ..." % (task_id, WAIT_MAX))
start = time.time()
last = None
while time.time() - start < WAIT_MAX:
    time.sleep(6)
    tasks = requests.get(API + "/console/nettest/tasks?limit=10",
                         headers=H, timeout=10).json()["tasks"]
    row = next((t for t in tasks if t["task_id"] == task_id), None)
    if row is None:
        print("  task vanished!")
        sys.exit(1)
    cur = (row["status"], json.dumps(row.get("result") or {}, ensure_ascii=False)[:220])
    if cur != last:
        print("  %6ds status=%s result=%s"
              % (time.time() - start, row["status"], cur[1]))
        last = cur
    if row["status"] != "running":
        print("FINAL: %s" % row["status"])
        print("LOG:", (row.get("log") or "")[:500])
        sys.exit(0 if row["status"] == "done" else 2)
print("TIMEOUT still running")
sys.exit(3)
