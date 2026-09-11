# -*- coding: utf-8 -*-
"""SMB 协议版本协商探测：循环 vers 尝试挂载，成功即做 fstab 持久化 + 读写验证。"""
import os

import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(os.environ["ETP_SSH_HOST"], port=int(os.environ["ETP_SSH_PORT"]),
            username=os.environ["ETP_SSH_USER"],
            password=os.environ["ETP_SSH_PASS"], timeout=20)

code = r'''
import sqlite3, subprocess
ROOT = "/data/terminal-platform/storage"
c = sqlite3.connect('/data/terminal-platform/data/eyeterm.db')
row = c.execute("SELECT value FROM settings WHERE key='storage.root_dir'").fetchone()
print('storage.root_dir:', row[0] if row else None)
subprocess.run(['mkdir', '-p', ROOT], check=True)

def is_mounted():
    return subprocess.run('mountpoint -q ' + ROOT + ' && echo Y || echo N',
                          shell=True, capture_output=True, text=True).stdout.strip() == 'Y'

if is_mounted():
    print('ALREADY MOUNTED')
else:
    ok = None
    for v in ('3.1.1', '3.02', '3.0', '2.1', '2.0'):
        cmd = ('mount -t cifs //172.17.200.101/EyeTerm ' + ROOT +
               " -o username=eyeterm,password='L5VGRrt-9-bQ',vers=" + v +
               ',uid=eyeterm,gid=eyeterm,iocharset=utf8')
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        mounted = is_mounted()
        print('vers=%s rc=%s mounted=%s %s' % (v, r.returncode, mounted,
                                               r.stderr.strip()[:120]))
        if mounted:
            ok = v
            break
    if not ok:
        print('SMB ALL VERSIONS FAILED')
        raise SystemExit(1)
    print('OK vers=' + ok)

fstab = ('//172.17.200.101/EyeTerm ' + ROOT +
         ' cifs username=eyeterm,password=L5VGRrt-9-bQ,vers=' + ok +
         ',uid=eyeterm,gid=eyeterm,iocharset=utf8,_netdev 0 0\n')
with open('/etc/fstab') as f:
    cur = f.read()
if '172.17.200.101/EyeTerm' not in cur:
    with open('/etc/fstab', 'a') as f:
        f.write(fstab)
    print('fstab appended')
else:
    print('fstab already has entry')

probe = ROOT + '/.eyeterm_mount_probe'
with open(probe, 'w') as f:
    f.write('ok')
print('write probe:', open(probe).read())
subprocess.run(['rm', '-f', probe])
print('SMB READY')
'''
sftp = ssh.open_sftp()
with sftp.open("/tmp/_smbvers.py", "w") as fh:
    fh.write(code)
sftp.close()
_, out, err = ssh.exec_command("python3 /tmp/_smbvers.py; rm -f /tmp/_smbvers.py",
                               timeout=120)
print(out.read().decode("utf-8", "replace"))
e = err.read().decode("utf-8", "replace").strip()
if e:
    print("[stderr]", e[:400])
ssh.close()
