# -*- coding: utf-8 -*-
"""资产组功能部署：备份 → 上传 store/api/console → 重启 → health。"""
import os
import time

import paramiko

ROOT = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".."))

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(os.environ["ETP_SSH_HOST"], port=int(os.environ["ETP_SSH_PORT"]),
            username=os.environ["ETP_SSH_USER"],
            password=os.environ["ETP_SSH_PASS"], timeout=20)


def run(cmd, timeout=120):
    _, out, err = ssh.exec_command(cmd, timeout=timeout)
    o = out.read().decode("utf-8", "replace").strip()
    e = err.read().decode("utf-8", "replace").strip()
    rc = out.channel.recv_exit_status()
    print("$", cmd[:90])
    if o:
        print(o)
    if e:
        print("[stderr]", e)
    print("rc=%d" % rc)
    return rc, o


ts = time.strftime("%Y%m%d_%H%M%S")
run("cd /data/terminal-platform/app/server && cp -a store.py store.py.bak.%s && "
    "cp -a api.py api.py.bak.%s; cd ../console && cp -a index.html index.html.bak.%s; "
    "echo BACKUP_OK" % (ts, ts, ts))

sftp = ssh.open_sftp()
base = "/data/terminal-platform/app"
for local, remote in [
    ("server-platform/server/store.py", base + "/server/store.py"),
    ("server-platform/server/api.py", base + "/server/api.py"),
    ("server-platform/console/index.html", base + "/console/index.html"),
]:
    sftp.put(os.path.join(ROOT, local), remote)
    print("UP", remote)
sftp.close()

run("systemctl restart terminal-platform")
run("sleep 9; systemctl is-active terminal-platform")
run("curl -s -m 5 http://127.0.0.1:18090/api/v1/health")
run("journalctl -u terminal-platform --since '-25s' --no-pager | grep -E 'listening|ERROR|error|Traceback' | head -5")
ssh.close()
print("DEPLOY DONE")
