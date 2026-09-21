#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""VLAN 知识库 console 管理端点单测（临时库，零凭据，零网络）。

覆盖（ADR-044 收尾验证门禁）：
- GET 列表（空态 / 数据态，只读不写审计）
- POST 单条 upsert：cidr 归一 strict=False、非法 cidr 400、按 cidr 幂等覆盖
- POST import 三分支 400：items 非数组 / len>500 / 元素非对象（错误信息含原因）
- import 混合批次：非法 cidr 记入 invalid 不阻断、added/updated 统计、imported_ts ISO
- import 幂等：同种子二次导入 added=0 updated=N invalid=[]
- DELETE / 404
- 写操作审计（sysadmin.vlan_kb，action 区分 upsert/import/delete）

用法：python tools/test_vlan_kb.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.normpath(os.path.join(HERE, "..", "server"))
sys.path.insert(0, SERVER)

import auth_upgrade as auth           # noqa: E402
from store import Store               # noqa: E402

PASSED = []
FAILED = []

SESS = {"username": "admin", "user_id": 1}

# auth 库最小引导：vlan-kb 路由仅写 console_audit_log
BOOTSTRAP = """
CREATE TABLE console_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at INTEGER NOT NULL,
    occurred_ms INTEGER, event_type TEXT NOT NULL, result TEXT NOT NULL,
    username TEXT, user_id INTEGER, client_ip TEXT, user_agent TEXT,
    session_id INTEGER, target TEXT, reason TEXT, detail TEXT);
"""


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                          (" | " + detail) if detail and not cond else ""))


def main():
    tmp = tempfile.mkdtemp(prefix="etp_vlan_kb_test_")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n=== 结果：通过 %d / 失败 %d ===" % (len(PASSED), len(FAILED)))
    return 0 if not FAILED else 1


def run(tmp):
    auth.DB_PATH = os.path.join(tmp, "auth.db")
    auth._local = threading.local()
    conn = auth.get_conn()
    conn.executescript(BOOTSTRAP)
    conn.commit()

    store = Store(os.path.join(tmp, "eyeterm.db"), config_token="cfg-token-0001")
    import api as api_mod               # 导入后重设（api 会覆盖 auth.DB_PATH）
    auth.DB_PATH = os.path.join(tmp, "auth.db")
    auth._local = threading.local()
    ctx = api_mod.ApiContext(store, {"terminal_token": "cfg-token-0001"})

    def call(method, tail, body_obj=None):
        """tail：parts[4:]，如 ["vlan-kb"] / ["vlan-kb", "import"]。"""
        body = json.dumps(body_obj).encode("utf-8") \
            if body_obj is not None else b""
        parts = ["api", "v1", "console", "sysadmin"] + tail
        return api_mod._console_sysadmin(ctx, method, parts, {}, body,
                                         SESS, "127.0.0.1")

    def call_err(method, tail, body_obj=None):
        try:
            call(method, tail, body_obj)
        except api_mod.ApiError as exc:
            return exc
        return None

    def jload(resp):
        return json.loads(resp[1].decode("utf-8"))

    # ---- [1] GET 空列表 ----
    print("\n[1] GET 列表（空态）")
    r = jload(call("GET", ["vlan-kb"]))
    check("GET 空列表 items=[]", r.get("ok") is True and r.get("items") == [])
    evts = conn.execute("SELECT COUNT(*) AS n FROM console_audit_log"
                        ).fetchone()["n"]
    check("GET 只读不写审计", evts == 0)

    # ---- [2] POST 单条 upsert：归一 + 幂等覆盖 + 非法 400 ----
    print("\n[2] POST 单条 upsert")
    r = jload(call("POST", ["vlan-kb"],
                   {"cidr": "10.3.4.5/24", "vlan_id": "34",
                    "zone_desc": "测试·归一", "source": "unit"}))
    check("upsert 归一 strict=False（10.3.4.5/24→10.3.4.0/24）",
          r.get("added") == 1 and r.get("updated") == 0)
    items = jload(call("GET", ["vlan-kb"]))["items"]
    check("落库 cidr 已归一", len(items) == 1 and items[0]["cidr"] == "10.3.4.0/24"
          and items[0]["vlan_id"] == "34")
    r = jload(call("POST", ["vlan-kb"],
                   {"cidr": "10.3.4.0/24", "vlan_id": "35",
                    "zone_desc": "测试·覆盖", "source": "unit"}))
    check("同 cidr 二次 upsert 幂等覆盖（added=0 updated=1）",
          r.get("added") == 0 and r.get("updated") == 1)
    items = jload(call("GET", ["vlan-kb"]))["items"]
    check("覆盖后仍 1 行且 vlan_id 更新",
          len(items) == 1 and items[0]["vlan_id"] == "35")
    exc = call_err("POST", ["vlan-kb"], {"cidr": "not-a-cidr", "vlan_id": "1"})
    check("非法 cidr 单条 400", exc is not None and exc.status == 400
          and "cidr" in exc.message)
    exc = call_err("POST", ["vlan-kb"], {"vlan_id": "1"})
    check("缺 cidr 400", exc is not None and exc.status == 400)

    # ---- [3] import 三分支 400 ----
    print("\n[3] import 校验 400 三分支")
    exc = call_err("POST", ["vlan-kb", "import"], {"items": "not-array"})
    check("items 非数组 400（含原因）", exc is not None and exc.status == 400
          and "数组" in exc.message)
    exc = call_err("POST", ["vlan-kb", "import"], {"nope": 1})
    check("缺 items 字段同样 400", exc is not None and exc.status == 400)
    big = [{"cidr": "10.%d.0.0/16" % (i % 250), "vlan_id": str(i)}
           for i in range(501)]
    exc = call_err("POST", ["vlan-kb", "import"], {"items": big})
    check("items 501 条 400（含原因与数量）", exc is not None
          and exc.status == 400 and "500" in exc.message and "501" in exc.message)
    exc = call_err("POST", ["vlan-kb", "import"],
                   {"items": [{"cidr": "10.9.0.0/16"}, "not-an-object"]})
    check("元素非对象 400（含位置原因）", exc is not None and exc.status == 400
          and "items[1]" in exc.message)
    r = jload(call("POST", ["vlan-kb", "import"], {"items": big[:500]}))
    check("恰好 500 条不 400", r.get("ok") is True)

    # ---- [4] import 混合批次：归一 / 非法进 invalid / 统计 ----
    print("\n[4] import 混合批次")
    seed = {"items": [
        {"cidr": "192.168.56.9/24", "vlan_id": "56",
         "zone_desc": "测试·导入归一", "source": "unit-import"},
        {"cidr": "2001:db8:1::1/64", "vlan_id": "v6",
         "zone_desc": "测试·IPv6 归一", "source": "unit-import"},
        {"cidr": "10.0.0.300/24", "vlan_id": "bad",
         "zone_desc": "测试·非法", "source": "unit-import"},
        {"cidr": "", "vlan_id": "empty", "zone_desc": "测试·空", "source": "s"},
    ]}
    r = jload(call("POST", ["vlan-kb", "import"], seed))
    check("added=2（两条合法）", r.get("added") == 2 and r.get("updated") == 0)
    check("非法 cidr 记入 invalid 不阻断（2 条）", len(r.get("invalid", [])) == 2)
    check("invalid 条目含原因", all("reason" in x for x in r["invalid"]))
    check("imported_ts 为 ISO 字符串",
          isinstance(r.get("imported_ts"), str) and "T" in r["imported_ts"])
    items = jload(call("GET", ["vlan-kb"]))["items"]
    cidrs = set(x["cidr"] for x in items)
    check("归一落库（192.168.56.0/24 + 2001:db8:1::/64）",
          "192.168.56.0/24" in cidrs and "2001:db8:1::/64" in cidrs
          and "192.168.56.9/24" not in cidrs)

    # ---- [5] import 幂等：同种子二次 ----
    print("\n[5] import 幂等")
    r2 = jload(call("POST", ["vlan-kb", "import"], seed))
    check("同种子二次导入 added=0 updated=2（invalid 同批一致复现）",
          r2.get("added") == 0 and r2.get("updated") == 2
          and r2.get("invalid") == r.get("invalid"))
    clean_seed = {"items": [x for x in seed["items"]
                            if x["cidr"] not in ("", "10.0.0.300/24")]}
    jload(call("POST", ["vlan-kb", "import"], clean_seed))
    r3 = jload(call("POST", ["vlan-kb", "import"], clean_seed))
    check("干净种子二次导入 added=0 updated=2 invalid=[]",
          r3.get("added") == 0 and r3.get("updated") == 2
          and r3.get("invalid") == [])

    # ---- [6] DELETE ----
    print("\n[6] DELETE")
    items = jload(call("GET", ["vlan-kb"]))["items"]
    victim = [x for x in items if x["cidr"] == "192.168.56.0/24"][0]
    r = jload(call("DELETE", ["vlan-kb", str(victim["id"])]))
    check("删除成功", r.get("ok") is True)
    cidrs = set(x["cidr"] for x in jload(call("GET", ["vlan-kb"]))["items"])
    check("删除后列表不含该条", "192.168.56.0/24" not in cidrs)
    exc = call_err("DELETE", ["vlan-kb", "99999"])
    check("删除不存在 404", exc is not None and exc.status == 404)
    exc = call_err("GET", ["vlan-kb", "99999"])
    check("未知子路径 404", exc is not None and exc.status == 404)

    # ---- [7] 审计 ----
    print("\n[7] 审计留痕")
    rows = conn.execute(
        "SELECT result, reason FROM console_audit_log"
        " WHERE event_type='sysadmin.vlan_kb'").fetchall()
    reasons = set(x["reason"] for x in rows)
    results = set(x["result"] for x in rows)
    check("action 覆盖 upsert/import/delete",
          {"upsert", "import", "delete"} <= reasons, str(reasons))
    check("含 success 与 failed 记录", results == {"success", "failed"},
          str(results))
    check("写操作均有审计（>5 条）", len(rows) > 5)

    conn.close()
    store.close()


if __name__ == "__main__":
    sys.exit(main())
