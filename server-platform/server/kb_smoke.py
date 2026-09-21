# -*- coding: utf-8 -*-
import os, sys, tempfile
sys.path.insert(0, chr(115)+chr(101)+chr(114)+chr(118)+chr(101)+chr(114)+chr(45)+chr(112)+chr(108)+chr(97)+chr(116)+chr(102)+chr(111)+chr(114)+chr(109)+chr(47)+chr(115)+chr(101)+chr(114)+chr(118)+chr(101)+chr(114))
import kb_store
db = os.path.join(tempfile.gettempdir(), 'kb_smoke_test.db')
if os.path.exists(db): os.remove(db)
kb = kb_store.get_kb(db)
PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name)
r = kb.create('KB-NET-001', 'wangluo', 'route-guide', 'step1', "admin", 'init')
check("create_ok", r == ('KB-NET-001', 1))
check("dup_409", kb.create('KB-NET-001', 'wangluo', "x", "y", "admin", '')[0] is None)
kb.update('KB-NET-001', 'route-guide-v2', 'wangluo', 'v2-content', 'ops1', 'v2')
kb.update('KB-NET-001', None, None, 'v3-content', 'ops2', 'v3')
vs = kb.versions('KB-NET-001')
check("versions_3", [v["version"] for v in vs] == [3, 2, 1])
for i in range(4, 9):
    kb.update('KB-NET-001', 't' + str(i), None, 'c' + str(i), 'ops', 'n' + str(i))
vs = kb.versions('KB-NET-001')
check("versions_capped_5", len(vs) == 5 and vs[0][VK] == 8 and vs[-1][VK] == 4)
g = kb.get('KB-NET-001')
check("get_latest", g["content"] == 'c8' and g["version"] == 8 and g["author"] == 'ops')
rb = kb.rollback('KB-NET-001', 4, "admin")
g2 = kb.get('KB-NET-001')
check("rollback_ok", rb == 9 and g2[CK] == 'c4' and g2[VK] == 9)
h = kb.history('KB-NET-001')
acts = [x["action"] for x in h]
check("history_ok", acts[0] == UPD and RB_IN in acts and CRT in acts)
rt = kb.route_table()
check("route_ok", len(rt) == 1 and rt[0]["kb_id"] == 'KB-NET-001' and rt[0]["category"] == 'wangluo')
os.remove(db)
print("RESULT: " + str(len(PASS)) + "/" + str(len(PASS) + len(FAIL)))
sys.exit(1 if FAIL else 0)