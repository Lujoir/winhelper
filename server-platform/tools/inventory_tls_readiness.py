#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段 0 · 生产只读盘点（H2 关停 legacy HTTP 18090 的前置阻断项）。

只读性质：仅执行查询 / 统计 / 打包备份，**不修改**任何服务端配置、服务状态或防火墙规则。

凭据（不落盘到仓库、不回显）：
    环境变量：ETP_SSH_HOST / ETP_SSH_PORT / ETP_SSH_USER / ETP_SSH_PASS
    或文件：tools/.prod_ssh.json {"host","port","user","password"}（已 gitignore）
可选：
    ETP_SSH_HOSTKEY   服务器主机密钥指纹（SHA256 hex）。提供则强制校验（RejectPolicy）
    ETP_APP_DIR       应用根目录，默认 /data/terminal-platform
    ETP_SAMPLE_SEC    防火墙计数采样间隔秒，默认 60（决定判定"明文 vs TLS 流量占比"的精度）
    ETP_SKIP_BACKUP   置 1 跳过备份步骤

采集项：
    A 端口监听（18090 明文 / 18443 TLS 终端 / 443 TLS 管理）
    B terminals 表（terminal_id / client_version / ip / last_seen）→ 版本分布与在线数
    C settings 表关键键（敏感值脱敏，仅显示长度与掩码）
    D config.json 关键键（legacy_http/tls/port 等，敏感值不回显）
    E 防火墙包计数两次采样（明文 18090 vs TLS 18443+443）→ 明文流量残留占比
    F systemd 特权端口能力与防火墙放行（443 需 CAP_NET_BIND_SERVICE，commit 431166c）
    G 备份（config.json + data/ 打包，落在远端 backups/，只读复制）

输出：控制台摘要 + 本地 JSON（不含任何敏感明文）

用法：
    $env:ETP_SSH_HOST="..."; ...; python tools/inventory_tls_readiness.py
"""
import base64
import json
import os
import sys
import time

try:
    import paramiko
except ImportError:
    raise SystemExit("missing dependency: paramiko")

APP_DIR = os.environ.get("ETP_APP_DIR", "/data/terminal-platform")
SAMPLE_SEC = int(os.environ.get("ETP_SAMPLE_SEC", "60"))
SKIP_BACKUP = os.environ.get("ETP_SKIP_BACKUP") == "1"
OUT_JSON = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "_s0_inventory_out.json")

# settings 表需读取的键（敏感键只取是否已设置/长度，不识别明文）
SETTINGS_KEYS = [
    "storage.root_dir", "smb.mount_cmd", "iperf.server_ip", "iperf.path",
    "llm.url", "llm.model", "switch.default_username",
]
SENSITIVE_KEYS = ["llm.api_key", "switch.default_password", "ftp.password",
                  "terminal_token", "console_password"]


def die(msg):
    sys.stderr.write("ERROR: %s\n" % msg)
    sys.exit(2)


CRED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         ".prod_ssh.json")


def _load_creds():
    """凭据来源：环境变量优先；其次 tools/.prod_ssh.json（gitignored，勿入库）。"""
    host = os.environ.get("ETP_SSH_HOST")
    port = os.environ.get("ETP_SSH_PORT")
    user = os.environ.get("ETP_SSH_USER")
    pwd = os.environ.get("ETP_SSH_PASS")
    if not (host and user and pwd) and os.path.isfile(CRED_FILE):
        try:
            with open(CRED_FILE, encoding="utf-8") as fh:
                c = json.load(fh)
        except Exception as exc:
            die("凭据文件 %s 解析失败：%s" % (CRED_FILE, exc))
        host = host or c.get("host")
        port = port or (str(c["port"]) if c.get("port") else None)
        user = user or c.get("user")
        pwd = pwd or c.get("password")
    return host, port, user, pwd


def connect():
    host, port, user, pwd = _load_creds()
    port = int(port or 22)
    if not (host and user and pwd):
        die("缺少凭据：设置 ETP_SSH_HOST/ETP_SSH_PORT/ETP_SSH_USER/ETP_SSH_PASS，"
            "或创建 tools/.prod_ssh.json（已 gitignore）")
    cli = paramiko.SSHClient()
    fp = (os.environ.get("ETP_SSH_HOSTKEY") or "").strip()
    if fp:
        cli.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        # H3 改造前过渡：显式告警，不在无提示下降级
        sys.stderr.write("WARN: ETP_SSH_HOSTKEY 未提供，本次未校验主机密钥"
                         "（H3 改造后此路径将被移除）\n")
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(host, port=port, username=user, password=pwd, timeout=20,
                banner_timeout=30, auth_timeout=20,
                look_for_keys=False, allow_agent=False)
    if fp:
        got = cli.get_transport().get_remote_server_key().get_fingerprint()
        got_hex = got.hex() if hasattr(got, "hex") else str(got)
        if fp.replace(":", "").lower() != got_hex.lower():
            cli.close()
            die("host key mismatch: expected=%s got=%s" % (fp, got_hex))
    return cli


def run(cli, cmd, timeout=60, quiet=False):
    _in, out, err = cli.exec_command(cmd, timeout=timeout)
    o = out.read().decode("utf-8", "replace")
    e = err.read().decode("utf-8", "replace")
    rc = out.channel.recv_exit_status()
    if rc != 0 and not quiet:
        sys.stderr.write("  [warn] rc=%d cmd=%.70s err=%.160s\n" % (rc, cmd, e.strip()))
    return rc, o, e


def py(cli, code, timeout=60):
    """经 base64 管道以应用 venv python 执行片段，避免引号转义问题。"""
    b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
    pybin = "%s/venv/bin/python" % APP_DIR
    cmd = "echo %s | base64 -d | %s -" % (b64, pybin)
    rc, o, e = run(cli, cmd, timeout=timeout)
    return rc, o, e


def main():
    host = _load_creds()[0]
    cli = connect()
    rep = {"host": host, "app_dir": APP_DIR, "ts": int(time.time())}

    print("== [A] 端口监听 ==")
    _, o, _ = run(cli, "ss -lntp 2>/dev/null | grep -E ':(18090|18443|443)\\b' || true")
    rep["listen"] = o.strip().splitlines()
    for ln in rep["listen"]:
        print("  " + ln.strip())

    print("== [B] terminals 表（版本 / 在线）==")
    code = """
import sqlite3, json, time
c = sqlite3.connect('%s/data/eyeterm.db')
c.row_factory = sqlite3.Row
rows = [dict(r) for r in c.execute(
    'SELECT terminal_id, client_version, ip, last_seen FROM terminals'
    ' ORDER BY last_seen DESC')]
now = int(time.time())
for r in rows:
    r['offline_sec'] = now - (r.get('last_seen') or 0)
print(json.dumps(rows))
""" % APP_DIR
    rc, o, e = py(cli, code)
    if rc != 0 or not o.strip():
        rep["terminals"] = {"error": e.strip()[:300] or "empty"}
        print("  [FAIL] 读取 terminals 失败：%s" % (e.strip()[:200]))
    else:
        rows = json.loads(o.strip().splitlines()[-1])
        rep["terminals"] = rows
        online = [r for r in rows if (r.get("offline_sec") or 9e9) <= 300]
        vers = {}
        for r in rows:
            vers[r.get("client_version") or "(空)"] = \
                vers.get(r.get("client_version") or "(空)", 0) + 1
        print("  终端总数 %d，近 5 分钟在线 %d" % (len(rows), len(online)))
        print("  版本分布：%s" % json.dumps(vers, ensure_ascii=False))
        for r in rows[:15]:
            print("    %-24s ver=%-8s ip=%-15s offline=%ss"
                  % (r.get("terminal_id"), r.get("client_version"),
                     r.get("ip"), r.get("offline_sec")))
        if len(rows) > 15:
            print("    ...（其余 %d 台见 JSON）" % (len(rows) - 15))

    print("== [C] settings 表（敏感值脱敏）==")
    # 全量取键值（settings 表体量小，避免 IN 列表引号嵌套破坏 Python 字面量）；
    # 敏感值仅本地记录长度，不写入 JSON
    code = """
import sqlite3, json
c = sqlite3.connect('%s/data/eyeterm.db')
c.row_factory = sqlite3.Row
try:
    rows = [dict(r) for r in c.execute('SELECT key, value FROM settings')]
except Exception as exc:
    print(json.dumps({'error': str(exc)}))
else:
    print(json.dumps(rows))
""" % APP_DIR
    rc, o, e = py(cli, code)
    st = {}
    if o.strip():
        try:
            for item in json.loads(o.strip().splitlines()[-1]):
                st[item["key"]] = item.get("value")
        except Exception:
            pass
    rep["settings"] = {}
    for k in SETTINGS_KEYS:
        v = st.get(k)
        rep["settings"][k] = v
        print("  %-24s = %s" % (k, "(unset)" if v is None else v))
    rep["settings_sensitive"] = {}
    for k in SENSITIVE_KEYS:
        v = st.get(k)
        rep["settings_sensitive"][k] = (
            "unset" if v is None else "set(len=%d)" % len(v))
        print("  %-24s = %s" % (k, rep["settings_sensitive"][k]))

    print("== [D] config.json 关键键 ==")
    code = """
import json
p = '%s/config.json'
try:
    cfg = json.load(open(p))
except Exception as exc:
    print(json.dumps({'error': str(exc)}))
else:
    keep = ('port', 'terminal_port', 'console_port', 'tls', 'legacy_http',
            'session_ttl_hours', 'heartbeat_timeout_sec')
    safe = {}
    for k in keep:
        if k in cfg:
            safe[k] = cfg[k]
    if isinstance(safe.get('tls'), dict):
        safe['tls'] = {kk: vv for kk, vv in safe['tls'].items()
                       if kk in ('enabled', 'min_tls_version')}
    safe['_sensitive_present'] = sorted(
        k for k in ('terminal_token', 'console_password') if cfg.get(k))
    print(json.dumps(safe, ensure_ascii=False))
""" % APP_DIR
    rc, o, e = py(cli, code)
    try:
        rep["config"] = json.loads(o.strip().splitlines()[-1]) if o.strip() else {}
    except Exception:
        rep["config"] = {"error": e.strip()[:200]}
    print("  " + json.dumps(rep["config"], ensure_ascii=False))

    print("== [E] 防火墙包计数（间隔 %ds，判定明文残留流量）==" % SAMPLE_SEC)

    def fw_snapshot():
        _, outp, _ = run(
            cli,
            "iptables -L INPUT -v -n 2>/dev/null"
            " | grep -E 'dpt:(18090|18443|443)' || true",
            quiet=True)
        cnt = {}
        for ln in outp.splitlines():
            for p in ("18090", "18443", "443"):
                if "dpt:%s" % p in ln:
                    parts = ln.split()
                    if len(parts) >= 2:
                        cnt[p] = cnt.get(p, 0) + int(parts[0])
        return cnt

    s1 = fw_snapshot()
    print("  t0: %s" % json.dumps(s1))
    time.sleep(SAMPLE_SEC)
    s2 = fw_snapshot()
    print("  t1: %s" % json.dumps(s2))
    delta = {p: s2.get(p, 0) - s1.get(p, 0) for p in set(list(s1) + list(s2))}
    total = sum(delta.values())
    rep["fw_delta"] = {"t0": s1, "t1": s2, "delta": delta,
                       "sample_sec": SAMPLE_SEC}
    print("  增量：%s（合计 %d 包 / %ds）" % (json.dumps(delta), total, SAMPLE_SEC))
    if total > 0:
        plain = delta.get("18090", 0) * 100.0 / total
        rep["fw_delta"]["plaintext_pct"] = round(plain, 2)
        print("  → 明文(18090)占比 %.2f%%；TLS(18443+443)占比 %.2f%%"
              % (plain, 100 - plain))
        if delta.get("18090", 0) > 0:
            print("  → 结论：仍有终端走明文，**不得关闭 18090**（先完成终端灰度）")
        else:
            print("  → 结论：采样窗口内无明文流量（仍需覆盖全部在线终端的心跳周期）")
    else:
        print("  → 警告：iptables 未取到计数（可能使用 firewalld/nftables），"
              "改用 ss 连接态或 journal 采样判读")

    print("== [F] systemd 特权端口能力与防火墙放行 ==")
    _, o, _ = run(cli, "systemctl cat terminal-platform 2>/dev/null"
                       " | grep -E 'AmbientCapabilities|^User=' || true",
                  quiet=True)
    rep["unit_caps"] = [l.strip() for l in o.strip().splitlines() if l.strip()]
    print("  unit: %s" % (rep["unit_caps"] or "(未声明)"))
    if not any("CAP_NET_BIND_SERVICE" in l for l in rep["unit_caps"]):
        print("  → 警告：unit 未声明 CAP_NET_BIND_SERVICE。管理口 443（<1024）"
              "且服务非 root 运行时，重启后会 bind 失败（对应 commit 431166c）")
    _, o, _ = run(cli, "firewall-cmd --list-ports 2>/dev/null"
                       " || iptables -L INPUT -n 2>/dev/null"
                       " | grep -E 'dpt:(18090|18443|443)' || true", quiet=True)
    rep["firewall"] = [l.strip() for l in o.strip().splitlines() if l.strip()]
    print("  firewall: %s" % (rep["firewall"] or "(未取到)"))

    print("== [G] 备份 ==")
    if SKIP_BACKUP:
        print("  已跳过（ETP_SKIP_BACKUP=1）")
    else:
        ts = time.strftime("%Y%m%d_%H%M%S")
        bdir = "%s/backups" % APP_DIR
        tgt = "%s/_s0_pre_r1_%s.tar.gz" % (bdir, ts)
        cmd = ("mkdir -p %s && tar czf %s -C %s config.json data 2>/dev/null; "
               "ls -l %s | awk '{print $5\" \"$9}'" % (bdir, tgt, APP_DIR, tgt))
        rc, o, e = run(cli, cmd, timeout=180)
        rep["backup"] = o.strip() or e.strip()[:200]
        print("  备份：%s" % rep["backup"])
        if rc != 0:
            print("  [FAIL] 备份失败，请人工确认")

    cli.close()
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=2)
    print("\nJSON 已写入：%s（不含敏感明文）" % OUT_JSON)
    return 0


if __name__ == "__main__":
    sys.exit(main())
