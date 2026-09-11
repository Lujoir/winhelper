# -*- coding: utf-8 -*-
"""部署 ETag 协商缓存（api.py/app.py）+ 304 冒烟验证。"""
import os

import paramiko

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
        print("[stderr]", e[:200])
    print("rc=%d" % rc)
    return rc, o


run("cd /data/terminal-platform/app/server && "
    "cp -a api.py api.py.bak.20260910_etag && cp -a app.py app.py.bak.20260910_etag && "
    "echo BACKUP_OK")

sftp = ssh.open_sftp()
for f in ("server/api.py", "server/app.py"):
    sftp.put(r"c:\Users\10604\CodeBuddy\20260522083146\server-platform" + "/" + f,
             "/data/terminal-platform/app/" + f)
    print("UP", f)
sftp.close()

run("systemctl restart terminal-platform")
run("sleep 9; systemctl is-active terminal-platform")
run("curl -s -m 5 http://127.0.0.1:18090/api/v1/health")

# 304 协商缓存冒烟：首次取 ETag → 二次带 If-None-Match 应 304
smoke = r'''
import urllib.request, ssl
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
url = "http://127.0.0.1:18090/"
r1 = urllib.request.urlopen(url, timeout=10, context=ctx)
etag = r1.headers.get("ETag")
print("first:", r1.status, "etag:", etag)
req = urllib.request.Request(url, headers={"If-None-Match": etag})
r2 = urllib.request.urlopen(req, timeout=10, context=ctx)
print("revalidate:", r2.status, "(expect 304)")
'''
sftp = ssh.open_sftp()
with sftp.open("/tmp/_etag_smoke.py", "w") as fh:
    fh.write(smoke)
sftp.close()
run("python3 /tmp/_etag_smoke.py; rm -f /tmp/_etag_smoke.py")
ssh.close()
print("DEPLOY DONE")
