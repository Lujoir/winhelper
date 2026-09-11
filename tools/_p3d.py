# -*- coding: utf-8 -*-
import io
p = "server-platform/server/api.py"
s = io.open(p, encoding="utf-8").read()
old = ('                "first_seen": t["first_seen"],\n'
       '                "last_seen": t["last_seen"],\n'
       '                "online": (now - t["last_seen"]) < hb_timeout,\n'
       '            },')
assert old in s, "a2"
new = ('                "first_seen": t["first_seen"],\n'
       '                "last_seen": t["last_seen"],\n'
       '                "cpu_model": t.get("cpu_model"),\n'
       '                "cpu_cores": t.get("cpu_cores"),\n'
       '                "mem_total_mb": t.get("mem_total_mb"),\n'
       '                "disk_total_gb": t.get("disk_total_gb"),\n'
       '                "gpu_info": t.get("gpu_info"),\n'
       '                "os_arch": t.get("os_arch"),\n'
       '                "online": (now - t["last_seen"]) < hb_timeout,\n'
       '            },\n'
       '            "asset": store.get_terminal_asset(tid),')
s = s.replace(old, new, 1)
io.open(p, "w", encoding="utf-8").write(s)
print("API_A2_PATCHED")
