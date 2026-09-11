# -*- coding: utf-8 -*-
"""SMB 挂载收尾：读 settings storage.root_dir → cifs 挂载 → fstab 持久化 → 读写验证。"""
import os

import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(os.environ["ETP_SSH_HOST"], port=int(os.environ["ETP_SSH_PORT"]),
            username=os.environ["ETP_SSH_USER"],
            password=os.environ["ETP_SSH_PASS"], timeout=20)

code = (
    "import sqlite3, subprocess\n"
    "c = sqlite3.connect('/data/terminal-platform/data/eyeterm.db')\n"
    "row = c.execute(\"SELECT value FROM settings WHERE key='storage.root_dir'\").fetchone()\n"
    "print('storage.root_dir:', row[0] if row else None)\n"
    "root = row[0] if row else '/data/terminal-platform/storage'\n"
    "subprocess.run(['mkdir', '-p', root], check=True)\n"
    "r = subprocess.run('mountpoint -q ' + root + ' && echo ALREADY || echo NOT_MOUNTED',\n"
    "                   shell=True, capture_output=True, text=True)\n"
    "print('mountpoint check:', r.stdout.strip())\n"
    "if 'NOT_MOUNTED' in r.stdout:\n"
    "    cmd = ('mount -t cifs //172.17.200.101/EyeTerm ' + root +\n"
    "           \" -o username=eyeterm,password='L5VGRrt-9-bQ',vers=3.0,\"\n"
    "           'uid=eyeterm,gid=eyeterm,iocharset=utf8')\n"
    "    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)\n"
    "    print('mount rc:', r.returncode, r.stderr.strip()[:200])\n"
    "r = subprocess.run('mountpoint -q ' + root + ' && echo MOUNTED || echo FAIL',\n"
    "                   shell=True, capture_output=True, text=True)\n"
    "print('after mount:', r.stdout.strip())\n"
    "if 'MOUNTED' in r.stdout:\n"
    "    fstab = ('//172.17.200.101/EyeTerm ' + root +\n"
    "             ' cifs username=eyeterm,password=L5VGRrt-9-bQ,vers=3.0,'\n"
    "             'uid=eyeterm,gid=eyeterm,iocharset=utf8,_netdev 0 0\\n')\n"
    "    with open('/etc/fstab') as f:\n"
    "        cur = f.read()\n"
    "    if '172.17.200.101/EyeTerm' not in cur:\n"
    "        with open('/etc/fstab', 'a') as f:\n"
    "            f.write(fstab)\n"
    "        print('fstab appended')\n"
    "    else:\n"
    "        print('fstab already has entry')\n"
    "    probe = root + '/.eyeterm_mount_probe'\n"
    "    open(probe, 'w').write('ok')\n"
    "    print('write probe:', open(probe).read())\n"
    "    os_remove = subprocess.run(['rm', '-f', probe])\n"
    "    print('SMB READY')\n")
sftp = ssh.open_sftp()
with sftp.open("/tmp/_smbfin.py", "w") as fh:
    fh.write(code)
sftp.close()
_, out, err = ssh.exec_command("python3 /tmp/_smbfin.py; rm -f /tmp/_smbfin.py",
                               timeout=60)
print(out.read().decode("utf-8", "replace"))
e = err.read().decode("utf-8", "replace").strip()
if e:
    print("[stderr]", e)
ssh.close()
