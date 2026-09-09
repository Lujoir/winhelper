# -*- coding: utf-8 -*-
"""确认 console_auth.db 审计链（临时脚本）。"""
import os

import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(os.environ["ETP_SSH_HOST"], port=int(os.environ["ETP_SSH_PORT"]),
            username=os.environ["ETP_SSH_USER"],
            password=os.environ["ETP_SSH_PASS"], timeout=20)
code = ("import sqlite3\n"
        "c = sqlite3.connect('/data/terminal-platform/data/console_auth.db')\n"
        "print('audit rows:', c.execute('SELECT COUNT(*) FROM "
        "console_audit_log').fetchone()[0])\n"
        "for r in c.execute('SELECT event_type, result, reason, client_ip "
        "FROM console_audit_log ORDER BY id DESC LIMIT 10'):\n"
        "    print(r)\n")
sftp = ssh.open_sftp()
with sftp.open("/tmp/_audit.py", "w") as fh:
    fh.write(code)
sftp.close()
_, out, err = ssh.exec_command("python3 /tmp/_audit.py; rm -f /tmp/_audit.py",
                               timeout=30)
print(out.read().decode("utf-8", "replace"))
print(err.read().decode("utf-8", "replace"))
ssh.close()
