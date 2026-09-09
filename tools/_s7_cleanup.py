# -*- coding: utf-8 -*-
"""S7 剩余信息保护：config.json 移除明文 console_password + VACUUM 两库 +
备份介质明文口令清理。凭据走 ETP_SSH_* 环境变量。用法：
    python _s7_cleanup.py check    # 只检查不改动
    python _s7_cleanup.py apply    # 执行清理
"""
import os
import sys

import paramiko

MODE = sys.argv[1] if len(sys.argv) > 1 else "check"

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


# ---- 1. config.json 键检查（只列键名，不回显值） ----
run("python3 -c 'import json;c=json.load(open(\"/data/terminal-platform/"
    "config.json\"));print(\"keys:\", sorted(c.keys()));"
    "print(\"has_console_password:\", \"console_password\" in c)'")

# ---- 2. 明文口令残留精确扫描：按口令值 + JSON 配置形态（代码引用不算残留） ----
pwd_val = os.environ.get("ETP_ADMIN_PWD", "")
rc, hits_val = run("grep -rlF '%s' /data/terminal-platform/ 2>/dev/null | head -20"
                   % pwd_val)
files_with_value = [h for h in hits_val.splitlines() if h.strip()]
print("含明文口令值的文件数:", len(files_with_value))
for f in files_with_value:
    print("  VALUE-LEAK:", f)
rc, hits_json = run("grep -rl 'console_password.*:' /data/terminal-platform/ "
                    "2>/dev/null | grep -E '\\.(json|json\\.[^.]+)$' | head -20")
files_cfg = [h for h in hits_json.splitlines() if h.strip()]
print("含 console_password 配置键的 JSON 文件:", files_cfg or "无")

if MODE == "apply":
    # ---- 3. config.json 移除 console_password（600 权限保持） ----
    run("python3 -c 'import json,os;p=\"/data/terminal-platform/config.json\";"
        "c=json.load(open(p));v=c.pop(\"console_password\",None);"
        "json.dump(c,open(p,\"w\"),ensure_ascii=False,indent=2);"
        "os.chmod(p,0o600);print(\"console_password removed:\",v is not None)'")

    # ---- 4. 清理含明文口令值的文件（代码文件除外，仅配置/快照类） ----
    for f in files_with_value:
        if f.endswith((".py", ".pyc", ".sql", ".md")):
            print("KEEP(代码/文档引用):", f)
            continue
        run("rm -f '%s' && echo REMOVED %s" % (f, f))
    for f in files_cfg:
        if f == "/data/terminal-platform/config.json":
            continue
        run("python3 -c 'import json;p=\"%s\";c=json.load(open(p));"
            "c.pop(\"console_password\",None);"
            "json.dump(c,open(p,\"w\"),ensure_ascii=False,indent=2);"
            "print(\"cleaned\",p)'" % f)

    # ---- 5. VACUUM 两库（停机窗口秒级）+ WAL 清除 ----
    run("systemctl stop terminal-platform")
    run("python3 -c 'import sqlite3;"
        "for p in [\"/data/terminal-platform/data/eyeterm.db\","
        "\"/data/terminal-platform/data/console_auth.db\"];"
        "conn=sqlite3.connect(p);conn.execute(\"VACUUM\");conn.close();"
        "print(\"VACUUM OK\", p)'")
    run("rm -f /data/terminal-platform/data/eyeterm.db-wal "
        "/data/terminal-platform/data/eyeterm.db-shm "
        "/data/terminal-platform/data/console_auth.db-wal "
        "/data/terminal-platform/data/console_auth.db-shm "
        "2>/dev/null; echo WAL_CLEANED")
    run("systemctl start terminal-platform; sleep 9; "
        "systemctl is-active terminal-platform")
    run("curl -s -m 5 http://127.0.0.1:18090/api/v1/health")

    # ---- 6. 复扫确认（按口令值） ----
    rc, left = run("grep -rlF '%s' /data/terminal-platform/ 2>/dev/null | "
                   "grep -vE '\\.(py|pyc|sql|md)$' | head -10; echo SCAN_DONE"
                   % pwd_val)
    print("剩余明文口令值文件:", [x for x in left.splitlines()
                          if x.strip() and x != "SCAN_DONE"] or "无")

ssh.close()
print("S7 %s DONE" % MODE.upper())
