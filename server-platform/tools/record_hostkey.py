#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""采集 SSH 主机密钥指纹（安全改造 R1 · H3 配套）。

只做密钥交换（**不需要账号密码**），打印 SHA256 指纹；人工核对后可用 `--write`
写入 `deploy/known_hosts`（一行 `主机:端口  指纹`），供 `ssh_hostkey.harden()`
在校验时读取。

用法：
    python tools/record_hostkey.py --host 172.17.5.215 --port 21232
    python tools/record_hostkey.py --host 172.17.5.215 --port 21232 --write
"""
import argparse
import hashlib
import os
import socket
import sys

import paramiko

HERE = os.path.dirname(os.path.abspath(__file__))
KNOWN_HOSTS = os.path.normpath(os.path.join(HERE, "..", "deploy", "known_hosts"))


def grab(host, port, timeout=10):
    """仅凭密钥交换取得主机公钥（不做认证）。"""
    sock = socket.create_connection((host, port), timeout=timeout)
    transport = None
    try:
        transport = paramiko.Transport(sock)
        transport.start_client(timeout=timeout)
        key = transport.get_remote_server_key()
        # SHA256 over 公钥 wire-format（与 server/ssh_hostkey.key_fingerprint 同口径）
        return key.get_name(), hashlib.sha256(key.asbytes()).hexdigest()
    finally:
        for obj in (transport, sock):
            try:
                obj.close()
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser(description="采集 SSH 主机密钥指纹")
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=22)
    ap.add_argument("--write", action="store_true",
                    help="写入 deploy/known_hosts（默认仅打印）")
    args = ap.parse_args()

    keytype, fp = grab(args.host, args.port)
    print("host    : %s:%d" % (args.host, args.port))
    print("keytype : %s" % keytype)
    print("sha256  : %s" % fp)

    if not args.write:
        print("\n（与管理员核对无误后，加 --write 写入 %s）" % KNOWN_HOSTS)
        return 0

    entry = "%s:%d  %s" % (args.host, args.port, fp)
    lines = []
    if os.path.isfile(KNOWN_HOSTS):
        with open(KNOWN_HOSTS, encoding="utf-8") as fh:
            # 丢弃旧注释行，避免重复写入时注释叠加
            lines = [l.rstrip("\n") for l in fh
                     if l.strip() and not l.startswith("#")]
    prefix = "%s:%d" % (args.host, args.port)
    lines = [l for l in lines if not l.split()[0] == prefix] if lines else []
    lines.append(entry)
    os.makedirs(os.path.dirname(KNOWN_HOSTS), exist_ok=True)
    with open(KNOWN_HOSTS, "w", encoding="utf-8") as fh:
        fh.write("# SSH 主机密钥指纹（安全改造 R1/H3）：主机:端口  指纹\n")
        fh.write("\n".join(lines) + "\n")
    print("\n已写入 %s（建议随仓库提交，供团队共用）" % KNOWN_HOSTS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
