# -*- coding: utf-8 -*-
"""304 协商精确验证（HTTPError 捕获）。"""
import os

import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(os.environ["ETP_SSH_HOST"], port=int(os.environ["ETP_SSH_PORT"]),
            username=os.environ["ETP_SSH_USER"],
            password=os.environ["ETP_SSH_PASS"], timeout=20)

code = r'''
import urllib.request, urllib.error, ssl
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
url = "http://127.0.0.1:18090/"
r1 = urllib.request.urlopen(url, timeout=10, context=ctx)
etag = r1.headers.get("ETag")
print("first:", r1.status, "etag:", etag)
req = urllib.request.Request(url, headers={"If-None-Match": etag})
try:
    r2 = urllib.request.urlopen(req, timeout=10, context=ctx)
    print("revalidate status:", r2.status, "(expect 304)")
except urllib.error.HTTPError as e:
    print("revalidate status:", e.code, "->", "304 OK" if e.code == 304 else "MISMATCH")
    print("cached header kept:", e.headers.get("Cache-Control"))
'''
sftp = ssh.open_sftp()
with sftp.open("/tmp/_e2.py", "w") as fh:
    fh.write(code)
sftp.close()
_, out, err = ssh.exec_command("python3 /tmp/_e2.py; rm -f /tmp/_e2.py", timeout=30)
print(out.read().decode("utf-8", "replace"))
e = err.read().decode("utf-8", "replace").strip()
if e:
    print("[stderr]", e[:200])
ssh.close()
