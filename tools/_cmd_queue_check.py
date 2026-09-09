# -*- coding: utf-8 -*-
"""命令队列状态排查（commands 表 id 15+）。"""
import os

import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(os.environ["ETP_SSH_HOST"], port=int(os.environ["ETP_SSH_PORT"]),
            username=os.environ["ETP_SSH_USER"],
            password=os.environ["ETP_SSH_PASS"], timeout=20)

code = (
    "import sqlite3, json\n"
    "c = sqlite3.connect('/data/terminal-platform/data/eyeterm.db')\n"
    "c.row_factory = sqlite3.Row\n"
    "for r in c.execute('SELECT id, terminal_id, command, status, created_ts, '\n"
    "                   'done_ts, sent_ts, timeout_sec, substr(args_json,1,120) AS args, '\n"
    "                   'substr(result_json,1,160) AS result '\n"
    "                   'FROM commands WHERE id >= 15 ORDER BY id'):\n"
    "    print(dict(r))\n"
    "print('--- iperf tasks ---')\n"
    "for r in c.execute('SELECT task_id, test_type, status, port, created_ts, '\n"
    "                   'substr(result_json,1,120) AS result, substr(log_text,1,120) AS log '\n"
    "                   'FROM iperf_tasks ORDER BY id DESC LIMIT 6'):\n"
    "    print(dict(r))\n")
sftp = ssh.open_sftp()
with sftp.open("/tmp/_qchk.py", "w") as fh:
    fh.write(code)
sftp.close()
_, out, err = ssh.exec_command("python3 /tmp/_qchk.py; rm -f /tmp/_qchk.py",
                               timeout=30)
print(out.read().decode("utf-8", "replace"))
e = err.read().decode("utf-8", "replace").strip()
if e:
    print("[stderr]", e)
ssh.close()
