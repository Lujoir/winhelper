# -*- coding: utf-8 -*-
"""从服务端探测画方准入平台端口可达性。"""
import os

import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(os.environ["ETP_SSH_HOST"], port=int(os.environ["ETP_SSH_PORT"]),
            username=os.environ["ETP_SSH_USER"],
            password=os.environ["ETP_SSH_PASS"], timeout=20)

code = r'''
import socket
HOST = "172.17.254.250"
for port in (9002, 443, 8443, 9000, 9001, 80):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3)
    try:
        s.connect((HOST, port))
        print("port %d: OPEN" % port)
    except Exception as e:
        print("port %d: %s" % (port, e))
    finally:
        s.close()
'''
sftp = ssh.open_sftp()
with sftp.open("/tmp/_nadprobe.py", "w") as fh:
    fh.write(code)
sftp.close()
_, out, err = ssh.exec_command("python3 /tmp/_nadprobe.py; rm -f /tmp/_nadprobe.py",
                               timeout=60)
print(out.read().decode("utf-8", "replace"))
e = err.read().decode("utf-8", "replace").strip()
if e:
    print("[stderr]", e[:200])
ssh.close()
