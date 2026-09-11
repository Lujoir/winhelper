# -*- coding: utf-8 -*-
import os, paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(os.environ["ETP_SSH_HOST"], port=int(os.environ["ETP_SSH_PORT"]),
            username=os.environ["ETP_SSH_USER"], password=os.environ["ETP_SSH_PASS"], timeout=20)
chk = ("import sqlite3, json\n"
       "c = sqlite3.connect('data/eyeterm.db')\n"
       "rows = c.execute('SELECT terminal_id, last_seen, asset_detail IS NULL,"
       " length(asset_detail), length(hwinfo_json) FROM terminals').fetchall()\n"
       "print(json.dumps(rows))\n")
sftp = ssh.open_sftp()
with sftp.open("/tmp/etp_chk.py", "w") as f:
    f.write(chk)
sftp.close()
def run(cmd):
    _, out, err = ssh.exec_command(cmd, timeout=30)
    print("$", cmd[:70])
    print(out.read().decode("utf-8", "replace")[:600])
    e = err.read().decode("utf-8", "replace")[:200]
    if e: print("[err]", e)
run("cd /data/terminal-platform; ./venv/bin/python /tmp/etp_chk.py")
run("rm -f /tmp/etp_chk.py")
run("journalctl -u terminal-platform --since '22:25' --no-pager | grep -iE 'register' | tail -6")
ssh.close()
