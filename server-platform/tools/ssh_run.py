#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SSH 远程命令执行辅助工具。

安全约定：凭据一律从环境变量读取，严禁硬编码、严禁写入任何仓库文件。

环境变量：
    ETP_SSH_HOST   远程主机
    ETP_SSH_PORT   SSH 端口（默认 22）
    ETP_SSH_USER   用户名
    ETP_SSH_PASS   密码

用法：
    python tools/ssh_run.py "cmd1; cmd2; cmd3"
"""
import os
import sys
import time

import paramiko


def _env(name, default=None):
    value = os.environ.get(name, default)
    if value is None:
        raise SystemExit("missing env: %s" % name)
    return value


def connect(retries=3):
    host = _env("ETP_SSH_HOST")
    port = int(_env("ETP_SSH_PORT", "22"))
    user = _env("ETP_SSH_USER")
    password = _env("ETP_SSH_PASS")
    last_exc = None
    for attempt in range(retries):
        try:
            client = paramiko.SSHClient()
            # H3：主机密钥校验（默认 fail-closed；仅 ETP_SSH_INSECURE=1 才放宽）
            sys.path.insert(0, os.path.join(os.path.dirname(
                os.path.abspath(__file__)), "..", "server"))
            import ssh_hostkey
            mode, expected_fp = ssh_hostkey.harden(client, host, port)
            if mode == "missing":
                raise SystemExit(ssh_hostkey.MISSING_HINT)
            client.connect(host, port=port, username=user, password=password,
                           timeout=20, banner_timeout=30, auth_timeout=20)
            if mode == "pinned":
                ssh_hostkey.verify(client, expected_fp)
            return client
        except Exception as exc:  # 重装后 sshd 偶发抖动，做有限重试
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(3)
    raise SystemExit("ssh connect failed after %d retries: %s"
                     % (retries, last_exc))


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: ssh_run.py <remote command>")
    command = sys.argv[1]
    client = connect()
    try:
        stdin, stdout, stderr = client.exec_command(command, timeout=300)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        rc = stdout.channel.recv_exit_status()
        if out:
            sys.stdout.write(out)
        if err:
            sys.stdout.write("[stderr]\n" + err)
        return rc
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
