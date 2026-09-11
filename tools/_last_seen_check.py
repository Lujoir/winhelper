# -*- coding: utf-8 -*-
"""终端最后心跳与服务端日志排查。"""
import os

import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(os.environ["ETP_SSH_HOST"], port=int(os.environ["ETP_SSH_PORT"]),
            username=os.environ["ETP_SSH_USER"],
            password=os.environ["ETP_SSH_PASS"], timeout=20)

code = (
    "import sqlite3, time\n"
    "c = sqlite3.connect('/data/terminal-platform/data/eyeterm.db')\n"
    "now = int(time.time())\n"
    "print('server now:', now)\n"
    "for r in c.execute('SELECT terminal_id, last_seen, client_version, ip,'\n"
    "                    ' last_seen - ? AS ago FROM terminals', (now,)):\n"
    "    print(dict(zip(('terminal_id','last_seen','client_version','ip','ago_sec'), r)))\n")
sftp = ssh.open_sftp()
with sftp.open("/tmp/_lschk.py", "w") as fh:
    fh.write(code)
sftp.close()
_, out, err = ssh.exec_command(
    "python3 /tmp/_lschk.py; rm -f /tmp/_lschk.py; "
    "echo ---LOG---; journalctl -u terminal-platform --since '12:55' --no-pager "
    "| grep -E 'WIN-Jun|heartbeat|register' | tail -6", timeout=30)
print(out.read().decode("utf-8", "replace"))
e = err.read().decode("utf-8", "replace").strip()
if e:
    print("[stderr]", e[:300])
ssh.close()
