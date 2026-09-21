# -*- coding: utf-8 -*-
"""一次性：生产库 ipconflict_reports 冒烟污染审计与清理（ADR-028）。

用法（凭据环境变量 ETP_SSH_*）：
  python tools/_clean_ipconflict_smoke.py            # dry-run：仅审计打印
  python tools/_clean_ipconflict_smoke.py --apply    # 备份→删除→验证

污染识别规则：
  1) MAC 归一后命中已知冒烟假 MAC 集合（AA-BB-CC-DD-EE-01 /
     00-11-22-33-44-55 / 00-00-00-00-00-01 等）
  2) terminal_id 不在 terminals 表（孤立来源，冒烟/弃用终端残留）
删除前导出全表 JSON 备份至远端 backups 目录（带时间戳）。
"""
import json
import os
import sys
import time

import paramiko

APPLY = "--apply" in sys.argv
KNOWN_FAKE = {"aabbccddee01", "001122334455", "000000000001",
              "aabbccddee02", "aabbccddee03"}

host = os.environ["ETP_SSH_HOST"]
port = int(os.environ.get("ETP_SSH_PORT", "22"))
user = os.environ["ETP_SSH_USER"]
pwd = os.environ["ETP_SSH_PASS"]

REMOTE_PY = r'''
import json, sqlite3, time
conn = sqlite3.connect("/data/terminal-platform/data/eyeterm.db")
conn.row_factory = sqlite3.Row
rows = [dict(r) for r in conn.execute(
    "SELECT id, terminal_id, ip, mac, created_ts FROM ipconflict_reports"
    " ORDER BY id")]
known_tids = {r[0] for r in conn.execute("SELECT terminal_id FROM terminals")}

def mkey(m):
    return "".join(ch for ch in str(m or "") if ch not in ":-. ").lower()

for r in rows:
    r["fake_mac"] = mkey(r["mac"]) in {mkey(x) for x in %(FAKE)s}
    r["orphan_tid"] = r["terminal_id"] not in known_tids
    r["polluted"] = r["fake_mac"] or r["orphan_tid"]
print("ROWS_JSON<<<" + json.dumps(rows, ensure_ascii=False) + ">>>")
''' % {"FAKE": json.dumps(sorted(KNOWN_FAKE))}

ssh = None
try:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, port=port, username=user, password=pwd, timeout=20)

    def run(cmd, timeout=60):
        _, out, err = ssh.exec_command(cmd, timeout=timeout)
        rc = out.channel.recv_exit_status()
        return rc, out.read().decode("utf-8", "replace"), \
            err.read().decode("utf-8", "replace")

    rc, out, err = run(
        "ETP_CONFIG=/data/terminal-platform/config.json "
        "/data/terminal-platform/venv/bin/python - <<'PYEOF'\n"
        + REMOTE_PY + "\nPYEOF")
    if rc != 0:
        print("[FATAL] remote audit failed: %s" % err[:400])
        sys.exit(1)
    blob = out.split("ROWS_JSON<<<")[1].split(">>>")[0]
    rows = json.loads(blob)
    print("=== 审计：ipconflict_reports 共 %d 行 ===" % len(rows))
    polluted = [r for r in rows if r["polluted"]]
    for r in rows:
        tag = (" [FAKE_MAC]" if r["fake_mac"] else "") + \
              (" [ORPHAN_TID]" if r["orphan_tid"] else "")
        mark = "DEL" if r["polluted"] else "   "
        print("  %s #%d tid=%-22s ip=%-15s mac=%-18s ts=%s%s"
              % (mark, r["id"], r["terminal_id"], r["ip"], r["mac"],
                 time.strftime("%m-%d %H:%M", time.localtime(r["created_ts"]))
                 if r["created_ts"] else "?", tag))
    print("\n识别污染行 %d / 总 %d" % (len(polluted), len(rows)))
    if not APPLY:
        print("（dry-run：未做任何变更；加 --apply 执行 备份→删除→验证）")
        sys.exit(0)

    ids = ",".join(str(r["id"]) for r in polluted)
    ts = time.strftime("%Y%m%d_%H%M%S")
    bak = "/data/terminal-platform/backups/ipconflict_cleanup_%s.json" % ts
    vpy = ("/data/terminal-platform/venv/bin/python")
    cmds = [
        # 1) 删除前备份（含被删行的 JSON 清单）
        vpy + " - <<'PYEOF'\n"
        "import json, sqlite3\n"
        "conn = sqlite3.connect('/data/terminal-platform/data/eyeterm.db')\n"
        "conn.row_factory = sqlite3.Row\n"
        "rows = [dict(r) for r in conn.execute(\n"
        "    'SELECT * FROM ipconflict_reports WHERE id IN (%s)')]\n"
        "open('%s', 'w', encoding='utf-8').write(\n"
        "    json.dumps(rows, ensure_ascii=False, indent=1))\n"
        "print('backup rows:', len(rows))\n"
        "PYEOF\n" % (ids or "0", bak),
        # 2) 删除
        vpy + " - <<'PYEOF'\n"
        "import sqlite3\n"
        "conn = sqlite3.connect('/data/terminal-platform/data/eyeterm.db')\n"
        "cur = conn.execute('DELETE FROM ipconflict_reports WHERE id IN (%s)')\n"
        "conn.commit()\n"
        "print('deleted:', cur.rowcount)\n"
        "PYEOF\n" % (ids or "0"),
        # 3) 验证残留
        vpy + " - <<'PYEOF'\n"
        "import sqlite3\n"
        "conn = sqlite3.connect('/data/terminal-platform/data/eyeterm.db')\n"
        "n = conn.execute('SELECT COUNT(*) FROM ipconflict_reports"
        " WHERE id IN (%s)').fetchone()[0]\n"
        "print('remain:', n)\n"
        "PYEOF\n" % (ids or "0"),
    ]
    if not polluted:
        print("无污染行，无需清理")
        sys.exit(0)
    rc, out, err = run(cmds[0])
    print("[backup] %s -> %s" % (bak, out.strip() or err[:200]))
    rc, out, err = run(cmds[1])
    print("[delete] rc=%d %s" % (rc, out.strip() or err[:200]))
    rc, out, err = run(cmds[2])
    print("[verify] 残留=%s（应为 0）" % out.strip())
    print("DONE: 清理 %d 行，备份 %s" % (len(polluted), bak))
finally:
    if ssh:
        ssh.close()
