# -*- coding: utf-8 -*-
"""asset_groups 存储层本地测试（临时脚本，用后即删）。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "server-platform", "server")))
from store import Store  # noqa: E402

DB = os.path.join(tempfile.gettempdir(), "_etp_ag_test.db")
for f in (DB, DB + "-wal", DB + "-shm"):
    if os.path.exists(f):
        os.remove(f)

s = Store(DB)
n = [0, 0]


def check(name, cond, extra=""):
    n[0 if cond else 1] += 1
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))


# 终端两条
s.register_terminal("T1", "Windows", "host-1", "Win10", "1.0", "127.0.0.1")
s.register_terminal("T2", "Windows", "host-2", "Win11", "1.0", "127.0.0.2")

# 多级嵌套创建
g1 = s.asset_group_create("杭州院区")
g11 = s.asset_group_create("临床医疗科室", g1)
g111 = s.asset_group_create("外派之江", g11)
g2 = s.asset_group_create("外部公司")
check("create multi-level", (g1, g11, g111, g2) == (1, 2, 3, 4))

# 非法创建
try:
    s.asset_group_create("x", 999)
    check("bad parent rejected", False)
except ValueError:
    check("bad parent rejected", True)
try:
    s.asset_group_create("  ")
    check("empty name rejected", False)
except ValueError:
    check("empty name rejected", True)

# 重命名
check("rename", s.asset_group_rename(g11, "临床科室"))
check("rename missing", not s.asset_group_rename(999, "x"))

# 绑定 / 改绑 / 解绑
s.set_terminal_group("T1", g111)
s.set_terminal_group("T2", g2)
rows = {r["terminal_id"]: r for r in s.list_terminals()}
check("bind T1->g111 T2->g2",
      rows["T1"]["group_id"] == g111 and rows["T2"]["group_id"] == g2)
s.set_terminal_group("T1", g2)          # 改绑
check("rebind T1->g2", {r["terminal_id"]: r for r in s.list_terminals()}
      ["T1"]["group_id"] == g2)
s.set_terminal_group("T2", None)        # 解绑
check("unbind T2", {r["terminal_id"]: r for r in s.list_terminals()}
      ["T2"]["group_id"] is None)
try:
    s.set_terminal_group("T1", 999)
    check("bind bad group rejected", False)
except ValueError:
    check("bind bad group rejected", True)
try:
    s.set_terminal_group("NOPE", None)
    check("bind bad terminal rejected", False)
except ValueError:
    check("bind bad terminal rejected", True)

# 计数一致性
lst = {g["id"]: g for g in s.asset_group_list()}
check("terminal_count consistent",
      lst[g2]["terminal_count"] == 1 and lst[g111]["terminal_count"] == 0
      and lst[g1]["terminal_count"] == 0)

# 删除保护：有子组拒绝
check("delete with children blocked",
      s.asset_group_delete(g11) == "has_children")
check("delete missing", s.asset_group_delete(999) == "missing")
# 从叶子删起：g111 有 T1（现绑 g2？T1 已改绑 g2）→ g111 空组可删
check("delete leaf ok", s.asset_group_delete(g111) == "ok")
# 删除 g2（有 T1 绑定）→ 终端自动解绑
check("delete with terminals ok", s.asset_group_delete(g2) == "ok")
check("terminals auto unbound after delete",
      all(r["group_id"] is None for r in s.list_terminals()))
check("group gone", s.asset_group_delete(g2) == "missing")
s.close()
os.remove(DB)

# ---- 场景2：存量库迁移（旧 terminals 无 group_id / 无 asset_groups）----
DB2 = os.path.join(tempfile.gettempdir(), "_etp_ag_legacy.db")
for f in (DB2, DB2 + "-wal", DB2 + "-shm"):
    if os.path.exists(f):
        os.remove(f)
import sqlite3
legacy = sqlite3.connect(DB2)
legacy.executescript(
    "CREATE TABLE terminals (id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " terminal_id TEXT UNIQUE NOT NULL, terminal_type TEXT, hostname TEXT,"
    " os_info TEXT, client_version TEXT, ip TEXT, first_seen INTEGER,"
    " last_seen INTEGER, cpu_model TEXT DEFAULT '', gpu_info TEXT DEFAULT '');"
    "INSERT INTO terminals(terminal_id,hostname,last_seen) VALUES('OLD1','legacy',0);")
legacy.commit()
legacy.close()
s2 = Store(DB2)   # 构造时补列 + 建索引 + 建 asset_groups
g9 = s2.asset_group_create("存量组")
s2.set_terminal_group("OLD1", g9)
check("legacy migrate + bind",
      {r["terminal_id"]: r for r in s2.list_terminals()}["OLD1"]["group_id"] == g9)
s2.close()
os.remove(DB2)
print("RESULT: %d pass, %d fail" % (n[0], n[1]))
