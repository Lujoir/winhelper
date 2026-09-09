# -*- coding: utf-8 -*-
"""恢复 config.json（S7 事故修复）：按 deploy.py 首次部署模板重建，
console_password 不再写入（登录改造后鉴权走 PBKDF2 auth 库）。
凭据走 ETP_SSH_* 环境变量。"""
import json
import os

import paramiko

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


CFG_PATH = "/data/terminal-platform/config.json"
cfg = {
    "port": 18090,
    "terminal_token": "fb77d34a28e82cdba4e887a04add4b6d3809c36f1166504b",
    "session_ttl_hours": 8,
    "heartbeat_timeout_sec": 180,
    "report_interval": 60,
    "retention_days": {"metrics": 30, "events": 90, "bottlenecks": 90},
    "bottleneck_dedup_min": 10,
    "data_dir": "/data/terminal-platform/data",
}
sftp = ssh.open_sftp()
with sftp.open(CFG_PATH + ".restore", "w") as fh:
    fh.write(json.dumps(cfg, indent=2, ensure_ascii=False))
sftp.close()
run("mv %s.restore %s && chown eyeterm:eyeterm %s && chmod 600 %s && "
    "ls -l %s" % (CFG_PATH, CFG_PATH, CFG_PATH, CFG_PATH, CFG_PATH))

# VACUUM 两库（停机状态）—— 修正上一轮的语法问题
vac = ("import sqlite3\n"
       "for p in ('/data/terminal-platform/data/eyeterm.db',"
       "'/data/terminal-platform/data/console_auth.db'):\n"
       "    c = sqlite3.connect(p)\n"
       "    c.execute('VACUUM')\n"
       "    c.close()\n"
       "    print('VACUUM OK', p)\n")
sftp = ssh.open_sftp()
with sftp.open("/tmp/_vacuum.py", "w") as fh:
    fh.write(vac)
sftp.close()
run("python3 /tmp/_vacuum.py; rm -f /tmp/_vacuum.py")
run("rm -f /data/terminal-platform/data/*.db-wal /data/terminal-platform/data/*.db-shm; "
    "echo WAL_CLEANED; ls -la /data/terminal-platform/data/")

# 清理 DEV 误建脏目录
run("rm -rf /data/terminal-platform/app/data && echo DIRTY_DIR_REMOVED")

# 启动 + 验证
run("systemctl start terminal-platform; sleep 9; "
    "systemctl is-active terminal-platform")
run("journalctl -u terminal-platform --since '-30s' --no-pager | grep -E 'config|data dir|auth db|listening'")
run("curl -s -m 5 http://127.0.0.1:18090/api/v1/health")
ssh.close()
print("RESTORE DONE")
