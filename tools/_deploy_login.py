# -*- coding: utf-8 -*-
"""登录改造部署：备份 → 上传 → 建库 → 迁移 → 重启 → health。

凭据走 ETP_SSH_* 环境变量；管理员口令走 ETP_ADMIN_PWD（仅本地用于算 PBKDF2
哈希，服务器端命令行只出现哈希串，不出现明文）。
"""
import os
import sys
import time

import paramiko

sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "server-platform", "server")))
import auth_upgrade as auth  # noqa: E402

ROOT = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".."))

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(os.environ["ETP_SSH_HOST"], port=int(os.environ["ETP_SSH_PORT"]),
            username=os.environ["ETP_SSH_USER"],
            password=os.environ["ETP_SSH_PASS"], timeout=20)


def run(cmd, timeout=180):
    _, out, err = ssh.exec_command(cmd, timeout=timeout)
    o = out.read().decode("utf-8", "replace").strip()
    e = err.read().decode("utf-8", "replace").strip()
    rc = out.channel.recv_exit_status()
    print("$", cmd[:100])
    if o:
        print(o)
    if e:
        print("[stderr]", e)
    print("rc=%d" % rc)
    return rc, o


# 1. 备份现有文件
ts = time.strftime("%Y%m%d_%H%M%S")
run("cd /data/terminal-platform/app/server && "
    "cp -a api.py api.py.bak.%s && cp -a app.py app.py.bak.%s 2>/dev/null; "
    "cd /data/terminal-platform/app/console && "
    "cp -a index.html index.html.bak.%s 2>/dev/null; echo BACKUP_OK" % (ts, ts, ts))

# 2. 上传
sftp = ssh.open_sftp()
base = "/data/terminal-platform/app"
for local, remote in [
    ("server-platform/server/api.py", base + "/server/api.py"),
    ("server-platform/server/app.py", base + "/server/app.py"),
    ("server-platform/server/auth_upgrade.py", base + "/server/auth_upgrade.py"),
    ("server-platform/server/migrate_login_upgrade.py",
     base + "/server/migrate_login_upgrade.py"),
    ("server-platform/console/index.html", base + "/console/index.html"),
]:
    sftp.put(os.path.join(ROOT, local), remote)
    print("UP", remote)
sftp.close()

# 3. 建鉴别库（幂等）：上传一次性脚本，口令经 stdin 传入（不进命令行、不落盘）
auth_db = "/data/terminal-platform/data/console_auth.db"
svc_user = run("ps -o user= -p $(systemctl show -p MainPID --value terminal-platform)")[1]
print("service user:", svc_user)
sftp = ssh.open_sftp()
sftp.put(os.path.join(ROOT, "tools", "_mk_admin.py"), "/tmp/_mk_admin.py")
sftp.close()
chan = ssh.get_transport().open_session()
chan.exec_command("python3 /tmp/_mk_admin.py; rm -f /tmp/_mk_admin.py")
chan.sendall((os.environ["ETP_ADMIN_PWD"] + "\n").encode("utf-8"))
chan.shutdown_write()
out_data, err_data = "", ""
while True:
    if chan.recv_ready():
        out_data += chan.recv(4096).decode("utf-8", "replace")
    if chan.recv_stderr_ready():
        err_data += chan.recv_stderr(4096).decode("utf-8", "replace")
    if chan.exit_status_ready() and not chan.recv_ready() \
            and not chan.recv_stderr_ready():
        break
rc = chan.recv_exit_status()
print("$ python3 /tmp/_mk_admin.py  (password via stdin)")
print(out_data.strip())
if err_data.strip():
    print("[stderr]", err_data.strip())
print("rc=%d" % rc)
if rc != 0 or "ADMIN_SET" not in out_data:
    raise SystemExit("admin setup failed")

# 4. 幂等迁移（自动备份 + 全支撑表 + 策略默认值）+ 状态核对
run("PYTHONIOENCODING=utf-8 python3 %s/migrate_login_upgrade.py --db %s --no-backup"
    % (base + "/server", auth_db))
run("PYTHONIOENCODING=utf-8 python3 %s/migrate_login_upgrade.py --db %s --check-only"
    % (base + "/server", auth_db))

# 5. 权限：归属服务用户，等保最小权限 600
run("chown %s:%s %s && chmod 600 %s && ls -l %s"
    % (svc_user, svc_user, auth_db, auth_db, auth_db))

# 6. 重启 + health
run("systemctl restart terminal-platform")
run("sleep 2; systemctl is-active terminal-platform")
run("curl -s -m 5 http://127.0.0.1:18090/api/v1/health")
ssh.close()
print("DEPLOY DONE")
