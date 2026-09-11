# -*- coding: utf-8 -*-
import io
p = "server-platform/server/store.py"
s = io.open(p, encoding="utf-8").read()
old = '                ("hwinfo_json", "TEXT NOT NULL DEFAULT \'{}\'")):'
new = '                ("hwinfo_json", "TEXT NOT NULL DEFAULT \'{}\'"),\n                ("asset_detail", "TEXT")):'
assert old in s, "m1"
s = s.replace(old, new, 1)
old = "client_version, ip, hwinfo=None, now=None):"
new = "client_version, ip, hwinfo=None, now=None, asset=None):"
assert old in s, "m2"
s = s.replace(old, new, 1)
old = "            hw = hwinfo or {}\n"
new = "            hw = hwinfo or {}\n            asset_json = json.dumps(asset, ensure_ascii=False) if asset else None\n"
assert old in s, "m3"
s = s.replace(old, new, 1)
old = '                    " hwinfo_json=? WHERE terminal_id=?",'
new = '                    " hwinfo_json=?, asset_detail=? WHERE terminal_id=?",'
assert old in s, "m4"
s = s.replace(old, new, 1)
old = '                     json.dumps(hw, ensure_ascii=False), terminal_id))'
new = '                     json.dumps(hw, ensure_ascii=False), asset_json, terminal_id))'
assert old in s, "m5"
s = s.replace(old, new, 1)
io.open(p, "w", encoding="utf-8").write(s)
print("STORE_A1_OK")
