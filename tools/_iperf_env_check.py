# -*- coding: utf-8 -*-
"""iperf 联调环境检查（服务器侧）。"""
import os

import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(os.environ["ETP_SSH_HOST"], port=int(os.environ["ETP_SSH_PORT"]),
            username=os.environ["ETP_SSH_USER"],
            password=os.environ["ETP_SSH_PASS"], timeout=20)

code = (
    "import shutil, sqlite3, subprocess\n"
    "p = shutil.which('iperf3')\n"
    "print('iperf3 path:', p)\n"
    "if p:\n"
    "    print(subprocess.run([p, '--version'], capture_output=True, text=True)\n"
    "          .stdout.splitlines()[0])\n"
    "c = sqlite3.connect('/data/terminal-platform/data/eyeterm.db')\n"
    "print('settings iperf*:', list(c.execute(\n"
    "    \"SELECT key, value FROM settings WHERE key LIKE 'iperf%'\")))\n"
    "print('settings all keys:', [r[0] for r in c.execute('SELECT key FROM settings')])\n"
    "print('firewalld 18200-18299:', subprocess.run(\n"
    "    'firewall-cmd --list-ports', shell=True, capture_output=True,\n"
    "    text=True).stdout.strip())\n")
sftp = ssh.open_sftp()
with sftp.open("/tmp/_ipchk.py", "w") as fh:
    fh.write(code)
sftp.close()
_, out, err = ssh.exec_command("python3 /tmp/_ipchk.py; rm -f /tmp/_ipchk.py",
                               timeout=30)
print(out.read().decode("utf-8", "replace"))
e = err.read().decode("utf-8", "replace").strip()
if e:
    print("[stderr]", e)
ssh.close()
