#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""发布回滚工具（安全改造 R1 · H5）。

背景
----
原部署流程在健康检查失败时仅 `die()` 退出，不恢复备份 —— 线上会停留在坏版本，
只能人工介入。本工具提供可复用的回滚能力，并与 `deploy.py` 的自动回滚共用同一
套恢复语义（server/ + console/ + config.json → 重启 → health 复验）。

用法
----
    python tools/rollback.py list                          # 列出远端可用备份
    python tools/rollback.py rollback pre_20260919_070000 --yes
    python tools/rollback.py verify                        # 当前服务/端口健康

凭据：环境变量 `ETP_SSH_*`，或 `tools/.prod_ssh.json`（gitignored）。
主机密钥：默认校验（`deploy/known_hosts` 或 `ETP_SSH_HOSTKEY`）；
          `ETP_SSH_INSECURE=1` 可显式放宽（不推荐）。
"""
import argparse
import json
import os
import sys

import paramiko

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
import ssh_hostkey  # noqa: E402

APP_DIR = os.environ.get("ETP_APP_DIR", "/data/terminal-platform")
BACKUP_ROOT = "%s/backups" % APP_DIR
CONFIG_PATH = "%s/config.json" % APP_DIR
SERVICE_NAME = "terminal-platform"
CRED_FILE = os.path.join(HERE, ".prod_ssh.json")


def load_creds():
    host = os.environ.get("ETP_SSH_HOST")
    port = os.environ.get("ETP_SSH_PORT")
    user = os.environ.get("ETP_SSH_USER")
    pwd = os.environ.get("ETP_SSH_PASS")
    if not (host and user and pwd) and os.path.isfile(CRED_FILE):
        with open(CRED_FILE, encoding="utf-8") as fh:
            cred = json.load(fh)
        host = host or cred.get("host")
        port = port or (str(cred["port"]) if cred.get("port") else None)
        user = user or cred.get("user")
        pwd = pwd or cred.get("password")
    return host, int(port or 22), user, pwd


def connect():
    host, port, user, pwd = load_creds()
    if not (host and user and pwd):
        raise SystemExit("缺少凭据：请设置 ETP_SSH_* 或创建 tools/.prod_ssh.json")
    cli = paramiko.SSHClient()
    mode, expected = ssh_hostkey.harden(cli, host, port)
    if mode == "missing":
        raise SystemExit(ssh_hostkey.MISSING_HINT)
    cli.connect(host, port=port, username=user, password=pwd, timeout=20,
                banner_timeout=30, auth_timeout=20,
                look_for_keys=False, allow_agent=False)
    if mode == "pinned":
        ssh_hostkey.verify(cli, expected)
    return cli, host


def run(cli, cmd, timeout=180, check=True, quiet=False):
    _in, out, err = cli.exec_command(cmd, timeout=timeout)
    o = out.read().decode("utf-8", "replace")
    e = err.read().decode("utf-8", "replace")
    rc = out.channel.recv_exit_status()
    if rc != 0 and check and not quiet:
        sys.stderr.write("[warn] rc=%d cmd=%.60s err=%.140s\n"
                         % (rc, cmd, e.strip()))
    return rc, o, e


def service_active(cli):
    _rc, out, _e = run(cli, "systemctl is-active %s" % SERVICE_NAME,
                       check=False, quiet=True)
    return out.strip() == "active"


def health_ok(cli, service_port=18090):
    _rc, out, _e = run(
        cli, "curl -s -o /dev/null -w '%%{http_code}' "
             "http://127.0.0.1:%d/api/v1/health" % service_port,
        check=False, quiet=True)
    return out.strip() == "200"


def cmd_list(cli):
    print("远端备份目录（新→旧，最多 20 条）：")
    _rc, out, _e = run(cli, "ls -1dt %s/*/ 2>/dev/null | head -20" % BACKUP_ROOT,
                       check=False, quiet=True)
    dirs = [l.strip().rstrip("/") for l in out.splitlines() if l.strip()]
    if not dirs:
        print("  （无备份目录）")
    for d in dirs:
        _rc, size, _e = run(cli, "du -sh %s 2>/dev/null | cut -f1" % d,
                            check=False, quiet=True)
        print("  %-58s %s" % (d.replace(BACKUP_ROOT + "/", ""), size.strip()))
    _rc, arc, _e = run(cli, "ls -1t %s/*.tar.gz 2>/dev/null | head -5" % BACKUP_ROOT,
                       check=False, quiet=True)
    if arc.strip():
        print("\n归档包（新→旧）：")
        for line in arc.strip().splitlines():
            print("  " + line.strip())
    return 0


def cmd_verify(cli):
    print("服务状态 : %s" % ("active" if service_active(cli) else "NOT active"))
    _rc, out, _e = run(cli, "ss -lntp 2>/dev/null | grep -E ':(18090|18443|443)\\b' || true",
                       check=False, quiet=True)
    print("端口监听 :")
    for line in out.strip().splitlines():
        print("  " + line.split()[3] if len(line.split()) > 3 else "  " + line.strip())
    print("legacy health : %s" % ("200" if health_ok(cli) else "FAIL"))
    return 0


def cmd_rollback(cli, tag, assume_yes):
    target = tag if tag.startswith("/") else "%s/%s" % (BACKUP_ROOT, tag)
    _rc, out, _e = run(cli, "test -d %s/server && echo yes" % target,
                       check=False, quiet=True)
    if out.strip() != "yes":
        print("FAIL: 备份无效（缺少 server/）：%s" % target)
        print("      先用 list 子命令确认可用备份")
        return 2
    if not assume_yes:
        print("将回滚到：%s" % target)
        print("（确认请加 --yes 重跑）")
        return 1

    print("回滚中：%s" % target)
    script = (
        "set -e; "
        "rm -rf {app}/server && cp -a {bak}/server {app}/server; "
        "if [ -d {bak}/console ]; then rm -rf {app}/console && "
        "cp -a {bak}/console {app}/console; fi; "
        "if [ -f {bak}/config.json ]; then cp -a {bak}/config.json {cfg}; fi; "
        "systemctl restart {svc}; sleep 2; systemctl is-active {svc}"
    ).format(app=APP_DIR, bak=target, cfg=CONFIG_PATH, svc=SERVICE_NAME)
    _rc, out, err = run(cli, script, check=False)
    tail = [l for l in out.strip().splitlines() if l.strip()][-1:]
    ok_active = (tail == ["active"])
    print("服务重启 : %s" % ("active" if ok_active else "NOT active"))
    if err.strip():
        print("stderr    : %s" % err.strip()[-200:])

    ok_health = health_ok(cli)
    print("health    : %s" % ("200" if ok_health else "FAIL"))
    if ok_active and ok_health:
        print("PASS: 已回滚到 %s 且服务健康" % target)
        return 0
    print("FAIL: 回滚后仍不健康，请人工介入（备份目录未删除，可再次回滚或手工修复）")
    return 2


def main():
    ap = argparse.ArgumentParser(description="EyeTerm 发布回滚工具")
    sub = ap.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="列出远端可用备份")
    sub.add_parser("verify", help="检查当前服务/端口健康")
    rb = sub.add_parser("rollback", help="回滚到指定备份")
    rb.add_argument("tag", help="备份名（如 pre_20260919_070000）或绝对路径")
    rb.add_argument("--yes", action="store_true", help="确认执行（不加则仅预览）")
    args = ap.parse_args()

    cli, host = connect()
    print("host: %s" % host)
    try:
        if args.action == "list":
            return cmd_list(cli)
        if args.action == "verify":
            return cmd_verify(cli)
        return cmd_rollback(cli, args.tag, args.yes)
    finally:
        cli.close()


if __name__ == "__main__":
    sys.exit(main())
