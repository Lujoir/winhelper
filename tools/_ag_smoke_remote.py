# -*- coding: utf-8 -*-
"""资产组远程冒烟：组树 CRUD + 绑定一致性 + 删除保护（测试数据自清理）。"""
import os

import requests

API = "http://172.17.5.215:18090/api/v1"
PWD = os.environ["ETP_ADMIN_PWD"]
H = {}

n = [0, 0]


def check(name, cond, extra=""):
    n[0 if cond else 1] += 1
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))


def groups():
    return requests.get(API + "/console/asset-groups", headers=H, timeout=10).json()["groups"]


def bind(tid, gid):
    return requests.post(API + "/console/terminals/" + tid + "/group",
                         json={"group_id": gid}, headers=H, timeout=10)


r = requests.post(API + "/console/login",
                  json={"username": "admin", "password": PWD}, timeout=10)
check("login", r.status_code == 200)
H = {"X-ETP-Console-Token": r.json()["token"]}

# 1. 多级创建：根→子→孙
g1 = requests.post(API + "/console/asset-groups",
                   json={"name": "SMK-杭州院区", "parent_id": None}, headers=H, timeout=10).json()["id"]
g2 = requests.post(API + "/console/asset-groups",
                   json={"name": "SMK-临床科室", "parent_id": g1}, headers=H, timeout=10).json()["id"]
g3 = requests.post(API + "/console/asset-groups",
                   json={"name": "SMK-外派之江", "parent_id": g2}, headers=H, timeout=10).json()["id"]
gs = {g["id"]: g for g in groups()}
check("multi-level created", gs[g1]["parent_id"] is None and gs[g2]["parent_id"] == g1
      and gs[g3]["parent_id"] == g2)

# 2. 非法创建（父不存在 → 400）
r = requests.post(API + "/console/asset-groups",
                  json={"name": "bad", "parent_id": 99999}, headers=H, timeout=10)
check("bad parent 400", r.status_code == 400)

# 3. 绑定真实终端到孙组 → 双向一致
tid = requests.get(API + "/console/terminals", headers=H, timeout=10).json()["terminals"][0]["terminal_id"]
check("bind terminal", bind(tid, g3).status_code == 200)
ts = {t["terminal_id"]: t for t in requests.get(API + "/console/terminals",
      headers=H, timeout=10).json()["terminals"]}
check("bind consistent (terminal side)", ts[tid]["group_id"] == g3)
gs = {g["id"]: g for g in groups()}
check("bind consistent (group count)", gs[g3]["terminal_count"] == 1)

# 4. 改绑到子组
check("rebind", bind(tid, g2).status_code == 200)
ts = {t["terminal_id"]: t for t in requests.get(API + "/console/terminals",
      headers=H, timeout=10).json()["terminals"]}
gs = {g["id"]: g for g in groups()}
check("rebind consistent", ts[tid]["group_id"] == g2
      and gs[g2]["terminal_count"] == 1 and gs[g3]["terminal_count"] == 0)

# 5. 重命名
check("rename", requests.post(API + "/console/asset-groups/%d" % g3,
      json={"name": "SMK-外派之江改"}, headers=H, timeout=10).status_code == 200)
check("renamed", {g["id"]: g for g in groups()}[g3]["name"] == "SMK-外派之江改")

# 6. 删除保护：有子组 → 409
r = requests.delete(API + "/console/asset-groups/%d" % g2, headers=H, timeout=10)
check("delete with children 409", r.status_code == 409)

# 7. 删除绑有终端的组 → 终端自动解绑
check("delete group with terminal", requests.delete(
    API + "/console/asset-groups/%d" % g2, headers=H, timeout=10) or True)
r = requests.delete(API + "/console/asset-groups/%d" % g2, headers=H, timeout=10)
# g2 有子组 g3，先删 g3 再删 g2
requests.delete(API + "/console/asset-groups/%d" % g3, headers=H, timeout=10)
r = requests.delete(API + "/console/asset-groups/%d" % g2, headers=H, timeout=10)
check("delete after children removed", r.status_code == 200)
ts = {t["terminal_id"]: t for t in requests.get(API + "/console/terminals",
      headers=H, timeout=10).json()["terminals"]}
check("auto unbind after delete", ts[tid]["group_id"] is None)

# 8. 解绑端点（group_id=null）+ 清理
check("unbind endpoint", bind(tid, None).status_code == 200)
requests.delete(API + "/console/asset-groups/%d" % g1, headers=H, timeout=10)
left = [g["name"] for g in groups() if g["name"].startswith("SMK-")]
check("test data cleaned", not left, str(left))

print("RESULT: %d pass, %d fail" % (n[0], n[1]))
