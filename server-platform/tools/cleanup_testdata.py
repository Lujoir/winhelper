#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""观枢终端平台 · 生产库测试数据清理工具。

删除冒烟测试产生的终端及关联数据（默认匹配 WIN-SMOKE-% / WIN-ADMIT-%），
含 audit_log 全量（测试期间产生）。执行前自动备份远端 db（带时间戳），
执行后 VACUUM 回收空间并输出清理统计。

安全约定：SSH 凭据一律从环境变量读取（ETP_SSH_*），严禁硬编码。
用法：
    python tools/cleanup_testdata.py            # 先统计预览，--yes 才真正删除
    python tools/cleanup_testdata.py --yes
"""
import os
import sys
import time

import paramiko

PATTERNS = ["WIN-SMOKE-%", "WIN-ADMIT-%"]
TABLES = ["metrics", "events", "bottlenecks", "commands", "iperf_tasks",
          "ai_analyses", "terminals"]
DB = "/data/terminal-platform/data/eyeterm.db"


def run(ssh, cmd, check=True):
    _, stdout, stderr = ssh.exec_command(cmd, timeout=60)
    rc = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    if check and rc != 0:
        print("[stderr]", err)
        raise SystemExit("remote command failed: %s" % cmd)
    return out


def build_sql(dry):
    conds = " OR ".join("terminal_id LIKE '%s'" % p for p in PATTERNS)
    lines = [".mode list"]
    if not dry:
        for t in TABLES:
            lines.append("DELETE FROM %s WHERE %s;" % (t, conds))
        lines.append("DELETE FROM audit_log;")
        lines.append("VACUUM;")
    for t in TABLES:
        lines.append("SELECT '%s_cleaned', COUNT(*) FROM %s WHERE %s;"
                     % (t, t, conds))
    lines.append("SELECT 'audit_log_total', COUNT(*) FROM audit_log;")
    return "\n".join(lines) + "\n"


def main():
    apply = "--yes" in sys.argv
    host = os.environ.get("ETP_SSH_HOST")
    port = int(os.environ.get("ETP_SSH_PORT", "22"))
    user = os.environ.get("ETP_SSH_USER")
    password = os.environ.get("ETP_SSH_PASS")
    if not (host and user and password):
        raise SystemExit("missing env: ETP_SSH_HOST/PORT/USER/PASS")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, port=port, username=user, password=password,
                timeout=20, banner_timeout=30, auth_timeout=20)
    sftp = ssh.open_sftp()
    ts = time.strftime("%Y%m%d_%H%M%S")

    backup = "/data/terminal-platform/backups/pre_cleanup_%s/eyeterm.db" % ts
    run(ssh, "mkdir -p %s" % os.path.dirname(backup))
    run(ssh, "cp -a %s %s" % (DB, backup))
    print("backup: %s" % backup)

    sql = build_sql(dry=not apply)
    remote_sql = "/tmp/_cleanup_%s.sql" % ts
    with sftp.open(remote_sql, "w") as fh:
        fh.write(sql)
    if apply:
        run(ssh, "systemctl stop terminal-platform")  # 避免写竞争
    out = run(ssh, "sqlite3 %s < %s" % (DB, remote_sql))
    run(ssh, "rm -f %s" % remote_sql)
    if apply:
        run(ssh, "systemctl start terminal-platform")
    print(out)
    print("mode: %s" % ("APPLIED" if apply else "DRY-RUN (add --yes to apply)"))
    sftp.close()
    ssh.close()


if __name__ == "__main__":
    main()
