# -*- coding: utf-8 -*-
"""fstab 统一修正：挂载点对齐 settings storage.root_dir + vers=2.0，重挂验证。"""
import os

import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(os.environ["ETP_SSH_HOST"], port=int(os.environ["ETP_SSH_PORT"]),
            username=os.environ["ETP_SSH_USER"],
            password=os.environ["ETP_SSH_PASS"], timeout=20)

code = r'''
import subprocess, os
ROOT = "/data/terminal-platform/storage"
cred = "/etc/eyeterm/smb.cred"
print("cred file exists:", os.path.exists(cred),
      "mode:", oct(os.path.getmode(cred) if hasattr(os, "getmode") else 0)
      if False else (oct(os.stat(cred).st_mode & 0o777) if os.path.exists(cred) else "-"))

# 1. 卸载手动挂载点
subprocess.run(["umount", ROOT], capture_output=True)

# 2. 重写 fstab 中 EyeTerm 行（对齐挂载点 + vers=2.0 + credentials 文件）
with open("/etc/fstab") as f:
    lines = f.readlines()
new = ("//172.17.200.101/EyeTerm " + ROOT +
       " cifs credentials=" + cred +
       ",vers=2.0,uid=991,gid=1000,file_mode=0664,dir_mode=0775,"
       "iocharset=utf8,_netdev,nofail 0 0\n")
out, changed = [], False
for ln in lines:
    if "172.17.200.101/EyeTerm" in ln:
        out.append(new)
        changed = True
    else:
        out.append(ln)
if not changed:
    out.append(new)
with open("/etc/fstab", "w") as f:
    f.writelines(out)
print("fstab rewritten:", changed)

# 3. mount -a 重挂 + 验证
r = subprocess.run("mount -a 2>&1; mountpoint -q " + ROOT +
                   " && echo MOUNTED || echo FAIL",
                   shell=True, capture_output=True, text=True)
print("mount -a:", r.stdout.strip()[:200])

# 4. 读写探针（eyeterm 身份可达性）
probe = ROOT + "/.eyeterm_mount_probe"
with open(probe, "w") as f:
    f.write("ok")
print("write probe:", open(probe).read())
subprocess.run(["rm", "-f", probe])

# 5. 清理旧挂载点目录（空则删）
r = subprocess.run("rmdir /mnt/eyeterm-store 2>/dev/null && echo OLD_DIR_REMOVED "
                   "|| echo OLD_DIR_KEEP", shell=True, capture_output=True, text=True)
print(r.stdout.strip())
print("SMB FINAL READY")
'''
sftp = ssh.open_sftp()
with sftp.open("/tmp/_smbfix.py", "w") as fh:
    fh.write(code)
sftp.close()
_, out, err = ssh.exec_command("python3 /tmp/_smbfix.py; rm -f /tmp/_smbfix.py",
                               timeout=60)
print(out.read().decode("utf-8", "replace"))
e = err.read().decode("utf-8", "replace").strip()
if e:
    print("[stderr]", e[:300])
ssh.close()
