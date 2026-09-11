# -*- coding: utf-8 -*-
import io
p = "server-platform/server/store.py"
s = io.open(p, encoding="utf-8").read()
old = ('                    "INSERT INTO terminals(terminal_id, terminal_type, hostname, os_info,"'
       '\n                    " client_version, ip, first_seen, last_seen) VALUES(?,?,?,?,?,?,?,?)",'
       '\n                    (terminal_id, terminal_type, hostname, os_info, client_version,'
       '\n                     ip, now, now))')
new = ('                    "INSERT INTO terminals(terminal_id, terminal_type, hostname, os_info,"'
       '\n                    " client_version, ip, first_seen, last_seen, cpu_model, cpu_cores,"'
       '\n                    " mem_total_mb, disk_total_gb, gpu_info, os_arch, hwinfo_json,"'
       '\n                    " asset_detail) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",'
       '\n                    (terminal_id, terminal_type, hostname, os_info, client_version,'
       '\n                     ip, now, now, str(hw.get("cpu_model") or ""),'
       '\n                     _as_int(hw.get("cpu_cores")), _as_int(hw.get("mem_total_mb")),'
       '\n                     _as_float(hw.get("disk_total_gb")),'
       '\n                     str(hw.get("gpu_info") or ""), str(hw.get("os_arch") or ""),'
       '\n                     json.dumps(hw, ensure_ascii=False), asset_json))')
assert old in s, "m6"
s = s.replace(old, new, 1)
anchor = "    def touch_terminal(self, terminal_id, now=None):"
assert anchor in s, "m7"
add = ('    def get_terminal_asset(self, terminal_id):\n'
       '        """资产明细（register 上报的结构化 JSON，无则回退 hwinfo_json）。"""\n'
       '        cur = self._conn.cursor()\n'
       '        cur.execute("SELECT asset_detail, hwinfo_json FROM terminals"\n'
       '                    " WHERE terminal_id=?", (terminal_id,))\n'
       '        row = cur.fetchone()\n'
       '        cur.close()\n'
       '        if not row:\n'
       '            return None\n'
       '        asset = _loads(row.get("asset_detail"), None)\n'
       '        if asset is not None:\n'
       '            return asset\n'
       '        hw = _loads(row.get("hwinfo_json"), None)\n'
       '        return hw if isinstance(hw, dict) else None\n\n')
s = s.replace(anchor, add + anchor, 1)
io.open(p, "w", encoding="utf-8").write(s)
print("STORE_A2_OK")
