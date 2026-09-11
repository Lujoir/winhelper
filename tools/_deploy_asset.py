# -*- coding: utf-8 -*-
"""部署：store.py/api.py/console 上传 + 重启 + 冒烟。"""
import os
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(os.environ["ETP_SSH_HOST"], port=int(os.environ["ETP_SSH_PORT"]),
            username=os.environ["ETP_SSH_USER"], password=os.environ["ETP_SSH_PASS"],
            timeout=20)
sftp = ssh.open_sftp()
base = "/data/terminal-platform/app"
for local, remote in [
    ("server-platform/server/store.py", base + "/server/store.py"),
    ("server-platform/server/api.py", base + "/server/api.py"),
    ("server-platform/console/index.html", base + "/console/index.html"),
]:
    sftp.put(local, remote)
    print("UP", remote)
sftp.close()
for cmd in ["systemctl restart terminal-platform",
            "sleep 2; systemctl is-active terminal-platform",
            "curl -s http://127.0.0.1:18090/api/v1/health"]:
    _, out, err = ssh.exec_command(cmd, timeout=30)
    print("$", cmd[:60], "->", out.read().decode().strip()[:120])
ssh.close()
