#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""观枢终端平台服务端 · 部署脚本（本机运行，paramiko SFTP + 远端命令）。

安全约定（红线）：
- SSH 凭据一律从环境变量读取：ETP_SSH_HOST / ETP_SSH_PORT / ETP_SSH_USER / ETP_SSH_PASS
- 凭据严禁写入任何文件/仓库；本脚本不含任何凭据。
- config.json（含 terminal_token / console_password）在远端生成，不回传仓库。

流程：勘察(端口/python39/防火墙) → 备份远端原文件 → 上传 → venv →
      系统用户/目录 → config(生成或保留) → systemd → 防火墙持久放行 → 健康检查

用法：
    # PowerShell
    $env:ETP_SSH_HOST=...; $env:ETP_SSH_PORT=...; $env:ETP_SSH_USER=...;
    $env:ETP_SSH_PASS=...
    python deploy/deploy.py [--port 18090]
"""
import argparse
import os
import posixpath
import shlex
import stat
import sys
import time

import paramiko

# H3（安全改造 R1）：SSH 主机密钥校验模块（与 server/ 共用同一实现）
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "server"))
import ssh_hostkey  # noqa: E402

APP_DIR = "/data/terminal-platform/app"
DATA_DIR = "/data/terminal-platform/data"
VENV_DIR = "/data/terminal-platform/venv"
BACKUP_ROOT = "/data/terminal-platform/backups"
CONFIG_PATH = "/data/terminal-platform/config.json"
SERVICE_NAME = "terminal-platform"
SERVICE_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "terminal-platform.service")
LOCAL_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                           ".."))

SERVICE_USER = "eyeterm"
FTP_USER = "eyetermftp"
FTP_LISTEN_PORT = 18121
FTP_PASV_START = 18122
FTP_PASV_END = 18141


def die(msg):
    print("[FATAL] %s" % msg)
    sys.exit(1)


def run(ssh, cmd, check=True, quiet=False):
    """执行远端命令，返回 (rc, out)。check=True 时失败即终止。"""
    _, stdout, stderr = ssh.exec_command(cmd, timeout=180)
    rc = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    if not quiet:
        print("  $ %s\n  -> rc=%d%s" % (cmd.split("\n")[0][:120], rc,
                                        ("\n  %s" % out[:400]) if out else ""))
    if err and rc != 0 and not quiet:
        print("  [stderr] %s" % err[:400])
    if check and rc != 0:
        die("remote command failed: %s" % cmd)
    return rc, out


def main():
    host = os.environ.get("ETP_SSH_HOST")
    port = int(os.environ.get("ETP_SSH_PORT", "22"))
    user = os.environ.get("ETP_SSH_USER")
    password = os.environ.get("ETP_SSH_PASS")
    if not (host and user and password):
        die("missing env: ETP_SSH_HOST / ETP_SSH_PORT / ETP_SSH_USER / ETP_SSH_PASS")

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18090,
                    help="legacy HTTP 端口（仅首次生成 config 时生效）")
    ap.add_argument("--skip-firewall", action="store_true")
    ap.add_argument("--skip-certs", action="store_true",
                    help="跳过证书生成/上传（ADR-032）")
    ap.add_argument("--rotate-certs", action="store_true",
                    help="强制重新生成服务器证书（CA 不动）")
    ap.add_argument("--san-dns", default="zljtest5.215",
                    help="服务器证书 SAN DNS 名")
    ap.add_argument("--san-ip", default="172.17.5.215",
                    help="服务器证书 SAN IP")
    args = ap.parse_args()

    ts = time.strftime("%Y%m%d_%H%M%S")
    print("=== EyeTerm Server deploy ===")
    print("host=%s port=%s user=%s app=%s" % (host, port, user, APP_DIR))

    # ------------------------------------------------------------------
    # 0. 连接
    # ------------------------------------------------------------------
    ssh = paramiko.SSHClient()
    # H3：主机密钥校验（默认 fail-closed）。指纹来源见 server/ssh_hostkey.py；
    # 采集：python tools/record_hostkey.py --host <IP> --port <PORT> --write
    mode, expected_fp = ssh_hostkey.harden(ssh, host, port)
    if mode == "missing":
        die(ssh_hostkey.MISSING_HINT)
    last_exc = None
    for attempt in range(3):  # 重装后 sshd 偶发抖动，有限重试
        try:
            ssh.connect(host, port=port, username=user, password=password,
                        timeout=20, banner_timeout=30, auth_timeout=20)
            break
        except Exception as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(3)
    else:
        die("ssh connect failed after retries: %s" % last_exc)
    if mode == "pinned":
        try:
            ssh_hostkey.verify(ssh, expected_fp)
        except RuntimeError as exc:
            ssh.close()
            die(str(exc))
    sftp = ssh.open_sftp()
    print("[0] connected (hostkey=%s)" % mode)

    # ------------------------------------------------------------------
    # 1. 勘察：OS / python39 / 端口 / 防火墙 / 磁盘
    # ------------------------------------------------------------------
    print("[1] recon")
    run(ssh, "cat /etc/anolis-release 2>/dev/null || cat /etc/os-release | head -2",
        quiet=True)
    run(ssh, "dnf module list 2>/dev/null | grep -E '^python39' || true", quiet=True)

    rc, out = run(ssh, "ss -tlnp | grep ':%d ' || true" % args.port, quiet=True)
    if out:
        # 自身服务占用属正常（增量部署会 restart），其他进程占用才是真冲突
        rc2, svc = run(ssh, "systemctl is-active %s" % SERVICE_NAME,
                       check=False, quiet=True)
        if svc.strip() == "active":
            print("  port %d held by our own active service, will restart later"
                  % args.port)
        else:
            die("port %d already in use by foreign process:\n%s" % (args.port, out))
    else:
        print("  port %d is free" % args.port)

    rc, fw_active = run(ssh, "systemctl is-active firewalld || true", quiet=True)
    fw_active = (fw_active.strip() == "active")
    print("  firewalld: %s" % ("active" if fw_active else "inactive"))

    rc, df_out = run(ssh, "df -h %s | tail -1" % posixpath.dirname(APP_DIR),
                     quiet=True)
    print("  disk: %s" % df_out)

    # ------------------------------------------------------------------
    # 2. 基础环境：python39 模块流 + venv + 系统用户 + 目录
    # ------------------------------------------------------------------
    print("[2] runtime: python39 module + venv + user")
    # Anolis 8 python39 包的二进制名是 python3.9（非 python39）
    run(ssh, "dnf module enable -y python39 2>/dev/null || true")
    run(ssh, "dnf install -y python39 2>&1 | tail -1")
    run(ssh, "python3.9 --version")
    run(ssh, "dnf install -y vsftpd 2>&1 | tail -1 || true")
    run(ssh, "dnf install -y iperf3 2>&1 | tail -1 || true")
    run(ssh, "iperf3 --version 2>&1 | head -1 || true")
    rc, out = run(ssh, "test -x %s/bin/python && echo yes || echo no" % VENV_DIR,
                  quiet=True)
    if out.strip() != "yes":
        run(ssh, "python3.9 -m venv %s" % VENV_DIR)
        print("  venv created: %s" % VENV_DIR)
    else:
        print("  venv exists: %s" % VENV_DIR)

    # paramiko（ADR-029：深度检测引擎依赖，版本锁定 3.5.1——5.x 移除
    # ssh-rsa/ssh-dss 老算法无法协商 Comware V7；幂等安装，失败不阻断部署）
    rc, out = run(ssh, "%s/bin/pip install 'paramiko==3.5.1' 2>&1 | tail -1"
                  " && %s/bin/python -c \"import paramiko;"
                  " print('  paramiko =', paramiko.__version__)\""
                  % (VENV_DIR, VENV_DIR), check=False)
    if rc != 0:
        print("  WARNING: paramiko install failed (deep engine degraded)")

    rc, _ = run(ssh, "id %s >/dev/null 2>&1 && echo ok || echo missing" % SERVICE_USER,
                quiet=True)
    if _.strip() != "ok":
        run(ssh, "useradd -r -d %s -s /sbin/nologin %s" % ("/data/terminal-platform",
                                                           SERVICE_USER))
        print("  system user created: %s" % SERVICE_USER)

    for d in (APP_DIR, DATA_DIR, BACKUP_ROOT):
        run(ssh, "mkdir -p %s" % d)
    run(ssh, "mkdir -p %s/server %s/console" % (APP_DIR, APP_DIR))

    # ------------------------------------------------------------------
    # 3. 备份远端原文件（带时间戳）
    # ------------------------------------------------------------------
    print("[3] backup existing files")
    rc, out = run(ssh, "ls %s/server 2>/dev/null | head -1 || true" % APP_DIR,
                  quiet=True)
    backup_dir = None
    if out.strip():
        backup_dir = "%s/pre_%s" % (BACKUP_ROOT, ts)
        run(ssh, "cp -a %s/server %s/console %s %s 2>/dev/null || cp -a %s %s"
            % (APP_DIR, APP_DIR, CONFIG_PATH, backup_dir, APP_DIR, backup_dir))
        run(ssh, "ls -la %s | head -5" % backup_dir)
        print("  backup: %s" % backup_dir)
    else:
        print("  first deploy, nothing to backup")

    # ------------------------------------------------------------------
    # 4. 上传文件（SFTP）
    # ------------------------------------------------------------------
    print("[4] upload via SFTP")

    def mkdirs(remote_path):
        parts = remote_path.strip("/").split("/")
        cur = ""
        for p in parts:
            cur += "/" + p
            try:
                sftp.stat(cur)
            except IOError:
                sftp.mkdir(cur)

    def upload(local, remote):
        sftp.put(local, remote)
        print("  %s -> %s" % (os.path.basename(local), remote))

    mkdirs(APP_DIR + "/server")
    mkdirs(APP_DIR + "/console")
    import glob as _glob
    for local_py in sorted(_glob.glob(os.path.join(LOCAL_ROOT, "server", "*.py"))):
        upload(local_py, APP_DIR + "/server/" + os.path.basename(local_py))

    # console 静态资源：**整目录递归上传**（含 home/ 首页模块与 cards/ 卡片）。
    # 2026-09-19 缺陷：原实现只硬编码上传 console/index.html，新增的
    # console/home/** 从未上生产 → /home/home.js 404 → 首页永久「加载中…」、
    # home.css 404 导致卡片无样式、初始化链中断还连带终端监控列表卡死。
    console_root = os.path.join(LOCAL_ROOT, "console")
    _skip_dirs = {"__pycache__", "node_modules", ".git"}
    _skip_exts = (".bak", ".pyc", ".orig", ".rej", ".swp")
    for dirpath, dirnames, filenames in os.walk(console_root):
        dirnames[:] = [d for d in dirnames if d not in _skip_dirs]
        for fn in sorted(filenames):
            if fn.endswith(_skip_exts):
                continue
            local_fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(local_fp, console_root).replace("\\", "/")
            remote_fp = APP_DIR + "/console/" + rel
            mkdirs(posixpath.dirname(remote_fp))
            upload(local_fp, remote_fp)

    upload(SERVICE_SRC, "/etc/systemd/system/%s.service" % SERVICE_NAME)

    # ------------------------------------------------------------------
    # 5. config.json：生成（首次）或保留
    # ------------------------------------------------------------------
    print("[5] config")
    import secrets
    import json
    rc, out = run(ssh, "test -f %s && echo exists || echo missing" % CONFIG_PATH,
                  quiet=True)
    if out.strip() == "missing":
        cfg = {
            "port": args.port,
            "terminal_token": secrets.token_hex(24),
            "console_password": secrets.token_urlsafe(12),
            "session_ttl_hours": 8,
            "heartbeat_timeout_sec": 180,
            "report_interval": 60,
            "retention_days": {"metrics": 30, "events": 90, "bottlenecks": 90},
            "bottleneck_dedup_min": 10,
            "data_dir": DATA_DIR,
        }
        with sftp.open(CONFIG_PATH, "w") as fh:
            fh.write(json.dumps(cfg, indent=2, ensure_ascii=False))
        sftp.chmod(CONFIG_PATH, 0o600)
        print("  NEW config generated: %s" % CONFIG_PATH)
        print("  [敏感信息 · 勿外传]")
        print("  terminal_token   : %s" % cfg["terminal_token"])
        print("  console_password : %s" % cfg["console_password"])
        service_port = args.port
    else:
        with sftp.open(CONFIG_PATH, "r") as fh:
            cfg = json.loads(fh.read().decode("utf-8"))
        print("  existing config kept: %s" % CONFIG_PATH)
        service_port = int(cfg.get("port", args.port))
        if service_port != args.port:
            print("  NOTE: config port=%d (cli --port ignored)" % service_port)

    # ------------------------------------------------------------------
    # 5a. HTTPS 证书（ADR-032）：远端 openssl 生成自建 CA + 服务器证书，
    #     私钥不出服务器；ca.crt 下载到本地 deploy/certs/ 供终端打包。
    # ------------------------------------------------------------------
    print("[5a] https certificates (ADR-032)")
    CERTS_DIR = "%s/certs" % DATA_DIR
    LOCAL_CA = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "certs", "ca.crt")
    ca_fp_hex = ""
    if args.skip_certs:
        print("  SKIPPED (--skip-certs)")
    else:
        rc, out = run(ssh, "test -f %s/server.crt && echo yes || echo no"
                      % CERTS_DIR, quiet=True)
        if out.strip() == "yes" and not args.rotate_certs:
            print("  certs exist, keep (use --rotate-certs to renew)")
        else:
            if out.strip() == "yes":
                run(ssh, "cp -a %s %s/certs_backup_%s" % (CERTS_DIR, DATA_DIR, ts))
                print("  old certs backed up")
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import gen_certs
            for cmd in gen_certs.build_openssl_cmds(args.san_dns, args.san_ip,
                                                    out_dir=CERTS_DIR):
                run(ssh, cmd)
            run(ssh, "rm -f %s/server.csr %s/ext.cnf %s/ca.srl 2>/dev/null || true"
                % (CERTS_DIR, CERTS_DIR, CERTS_DIR))
            print("  certs generated on remote: %s" % CERTS_DIR)
        rc, fp = run(ssh, "openssl x509 -in %s/ca.crt -noout -fingerprint -sha256"
                     " | cut -d= -f2 | sed 's/://g' | tr 'A-F' 'a-f'"
                     % CERTS_DIR, quiet=True)
        ca_fp_hex = fp.strip()
        if not ca_fp_hex:
            die("CA fingerprint extraction failed")
        print("  CA SHA256 (terminal uplink_config.server_ca_fingerprint):")
        print("  %s" % ca_fp_hex)
        run(ssh, "chmod 700 %s && chmod 600 %s/*.key" % (CERTS_DIR, CERTS_DIR))
        # ca.crt 下载到本地（终端 PyInstaller assets/platform_ca.pem 替换用）
        try:
            os.makedirs(os.path.dirname(LOCAL_CA), exist_ok=True)
            sftp.get("%s/ca.crt" % CERTS_DIR, LOCAL_CA)
            print("  ca.crt downloaded -> %s (terminal asset)" % LOCAL_CA)
        except Exception as exc:
            print("  WARNING: ca.crt download failed: %s" % exc)

    # ------------------------------------------------------------------
    # 5b. config.json HTTPS 键迁移（幂等：缺才补，保留既有值）
    # ------------------------------------------------------------------
    print("[5b] config https keys migration")
    changed = False
    if "terminal_port" not in cfg:
        cfg["terminal_port"] = 18443
        changed = True
    if "console_port" not in cfg:
        cfg["console_port"] = 8443
        changed = True
    if "tls" not in cfg:
        cfg["tls"] = {"enabled": True,
                      "cert": "%s/certs/fullchain.crt" % DATA_DIR,
                      "key": "%s/certs/server.key" % DATA_DIR,
                      "min_tls_version": "TLSv1_2"}
        changed = True
    if "legacy_http" not in cfg:
        cfg["legacy_http"] = {"enabled": True}  # 过渡期并行，收口后置 false
        changed = True
    if changed:
        with sftp.open(CONFIG_PATH, "w") as fh:
            fh.write(json.dumps(cfg, indent=2, ensure_ascii=False))
        sftp.chmod(CONFIG_PATH, 0o600)
        print("  config migrated: terminal_port=%s console_port=%s tls=on legacy=on"
              % (cfg["terminal_port"], cfg["console_port"]))
    else:
        print("  https keys already present (keep)")

    # ------------------------------------------------------------------
    # 5c. FTP 服务（vsftpd）+ 环境变量注入的 LLM 配置
    # ------------------------------------------------------------------
    print("[5c] ftp service + llm settings")
    import secrets as _secrets
    storage_root = "%s/storage" % posixpath.dirname(APP_DIR)
    run(ssh, "mkdir -p %s" % storage_root)
    run(ssh, "id %s >/dev/null 2>&1 && echo ok || echo missing" % FTP_USER,
        quiet=True)
    run(ssh, "useradd -d %s -s /bin/bash %s 2>/dev/null || true"
        % (storage_root, FTP_USER))
    ftp_password = _secrets.token_urlsafe(12)
    run(ssh, "echo '%s:%s' | chpasswd" % (FTP_USER, ftp_password))
    run(ssh, "chown -R %s:%s %s" % (FTP_USER, FTP_USER, storage_root))
    vsftpd_conf = (
        "listen=YES\n"
        "listen_address=0.0.0.0\n"
        "listen_port=%d\n"
        "pasv_enable=YES\n"
        "pasv_min_port=%d\n"
        "pasv_max_port=%d\n"
        "local_enable=YES\n"
        "write_enable=YES\n"
        "chroot_local_user=YES\n"
        "allow_writeable_chroot=YES\n"
        "local_root=%s\n"
        "xferlog_enable=YES\n"
        "xferlog_file=/var/log/xferlog\n") % (FTP_LISTEN_PORT, FTP_PASV_START,
                                    FTP_PASV_END, storage_root)
    run(ssh, "cp -a /etc/vsftpd/vsftpd.conf /etc/vsftpd/vsftpd.conf.bak.%s 2>/dev/null"
             " || true" % ts)
    run(ssh, "cat > /etc/vsftpd/vsftpd.conf <<'ETPEOF'\n%sETPEOF" % vsftpd_conf)
    # 禁止 FTP 用户走 SSH 登录（密码复用面收敛）
    run(ssh, "grep -q '^DenyUsers' /etc/ssh/sshd_config"
             " && sed -i 's/^DenyUsers.*/& %s/' /etc/ssh/sshd_config"
             " || echo 'DenyUsers %s' >> /etc/ssh/sshd_config" % (FTP_USER, FTP_USER))
    run(ssh, "systemctl reload sshd || systemctl restart sshd")
    run(ssh, "systemctl enable --now vsftpd")
    # settings 写入（经 config_cli，敏感值加密）
    cli = "ETP_CONFIG=%s %s/bin/python %s/server/config_cli.py" % (
        CONFIG_PATH, VENV_DIR, APP_DIR)
    run(ssh, "%s set ftp.enabled 1" % cli)
    run(ssh, "%s set ftp.listen_port %d" % (cli, FTP_LISTEN_PORT))
    run(ssh, "%s set ftp.pasv_start %d" % (cli, FTP_PASV_START))
    run(ssh, "%s set ftp.pasv_end %d" % (cli, FTP_PASV_END))
    run(ssh, "%s set ftp.username %s" % (cli, FTP_USER))
    run(ssh, "printf %%s %s | %s set ftp.password --stdin"
        % (shlex.quote(ftp_password), cli))
    run(ssh, "%s set storage.root_dir %s" % (cli, storage_root))
    print("  ftp: vsftpd on %d (pasv %d-%d), user=%s"
          % (FTP_LISTEN_PORT, FTP_PASV_START, FTP_PASV_END, FTP_USER))
    print("  [敏感信息 · 勿外传] ftp password : %s" % ftp_password)
    # LLM（可选，环境变量注入；key 全程不落盘仓库）
    llm_url = os.environ.get("ETP_LLM_URL", "")
    llm_key = os.environ.get("ETP_LLM_KEY", "")
    llm_model = os.environ.get("ETP_LLM_MODEL", "")
    if llm_url:
        run(ssh, "%s set llm.url %s" % (cli, shlex.quote(llm_url)))
    if llm_model:
        run(ssh, "%s set llm.model %s" % (cli, shlex.quote(llm_model)))
    if llm_key:
        run(ssh, "printf %%s %s | %s set llm.api_key --stdin"
            % (shlex.quote(llm_key), cli))
    print("  llm: url=%s model=%s key=%s" % (
        llm_url or "(unset)", llm_model or "(unset)",
        "injected" if llm_key else "(unset)"))
    run(ssh, "%s set iperf.server_ip %s" % (cli, shlex.quote(host)))
    print("  iperf server_ip: %s" % host)
    # 交换机默认凭据预置（ADR-026，幂等：仅 unset 时写入，避免覆盖管理员改动；
    # password 走 stdin 注入并加密落库，不经命令行明文）
    sw_user = os.environ.get("ETP_SW_DEFAULT_USER", "reader")
    # H7（安全改造 R1）：移除硬编码默认口令。未显式提供 ETP_SW_DEFAULT_PWD 时
    # 不再播种固定口令 —— 避免"全网交换机共用同一默认口令"成为横向移动跳板。
    sw_pwd = os.environ.get("ETP_SW_DEFAULT_PWD", "").strip()
    # 以 password 键判断（它无 DEFAULTS，真正 unset 时才输出 "(unset)"；
    # username 在 DEFAULTS 中默认空串，不能作为幂等依据）
    _, cur_out = run(ssh, "%s get switch.default_password --mask" % cli,
                     quiet=True)
    if not sw_pwd:
        print("  switch default credentials: SKIPPED（未提供 ETP_SW_DEFAULT_PWD；"
              "不再使用内置默认口令，如需播种请显式注入）")
    elif cur_out.strip() == "(unset)":
        run(ssh, "%s set switch.default_username %s"
            % (cli, shlex.quote(sw_user)))
        run(ssh, "printf %%s %s | %s set switch.default_password --stdin"
            % (shlex.quote(sw_pwd), cli))
        print("  switch default credentials: seeded (user=%s, pwd=***)" % sw_user)
    else:
        print("  switch default credentials: already set (keep)")

    # ------------------------------------------------------------------
    # 6. 权限 + systemd
    # ------------------------------------------------------------------
    print("[6] permissions + systemd")
    run(ssh, "chown -R %s:%s /data/terminal-platform" % (SERVICE_USER, SERVICE_USER))
    run(ssh, "systemctl daemon-reload")
    run(ssh, "systemctl enable %s" % SERVICE_NAME)
    run(ssh, "systemctl restart %s" % SERVICE_NAME)
    status = ""
    for _ in range(6):  # 等待启动（最多 6s），失败即拉日志
        time.sleep(1)
        rc, status = run(ssh, "systemctl is-active %s" % SERVICE_NAME,
                         check=False, quiet=True)
        if status.strip() == "active":
            break
    if status.strip() != "active":
        run(ssh, "journalctl -u %s -n 15 --no-pager | tail -15" % SERVICE_NAME,
            check=False)
        die("service not active after retries: %s" % status)
    print("  service active")

    # ------------------------------------------------------------------
    # 7. 防火墙持久放行（红线：必须持久化）
    # ------------------------------------------------------------------
    if args.skip_firewall:
        print("[7] firewall: SKIPPED (--skip-firewall)")
    else:
        print("[7] firewall (persistent)")
        # HTTPS 双端口（ADR-032）：终端 18443 + 管理口 8443
        tls_ports = []
        if not args.skip_certs:
            tls_ports = [int(cfg.get("terminal_port", 18443)),
                         int(cfg.get("console_port", 8443))]
        # 公开客户端下载口（HTTP，默认 80）：浏览器打开 http://<host>/ 即下载页
        dl_port = int(cfg.get("download_port", 80) or 0)
        fw_ports = [service_port] + tls_ports + ([dl_port] if dl_port else [])
        if fw_active:
            for p in fw_ports:
                run(ssh, "firewall-cmd --permanent --add-port=%d/tcp" % p)
            run(ssh, "firewall-cmd --permanent --add-port=%d/tcp" % FTP_LISTEN_PORT)
            run(ssh, "firewall-cmd --permanent --add-port=%d-%d/tcp"
                % (FTP_PASV_START, FTP_PASV_END))
            run(ssh, "firewall-cmd --permanent --add-port=%d-%d/tcp"
                % (18200, 18299))
            run(ssh, "firewall-cmd --reload")
            rc, out = run(ssh, "firewall-cmd --list-all", quiet=True)
            for p in fw_ports:
                if ("%d/tcp" % p) not in out:
                    die("firewall permanent rule NOT verified in --list-all "
                        "output: %d" % p)
            rc, out2 = run(ssh, "firewall-cmd --permanent --list-ports", quiet=True)
            for p in fw_ports:
                if ("%d/tcp" % p) not in out2:
                    die("firewall rule missing in permanent config: %d" % p)
            print("  firewalld permanent rule verified (runtime + permanent): %s"
                  % ([service_port] + tls_ports))
        else:
            # firewalld inactive：走 iptables 并持久化
            all_ports = fw_ports
            rules_needed = all_ports + [FTP_LISTEN_PORT, (FTP_PASV_START, FTP_PASV_END),
                                        (18200, 18299)]
            for p in all_ports:
                rc, out = run(ssh, "iptables -C INPUT -p tcp --dport %d -j ACCEPT "
                                   "2>/dev/null && echo yes || echo no" % p,
                              quiet=True)
                if out.strip() != "yes":
                    run(ssh, "iptables -I INPUT 5 -p tcp --dport %d -j ACCEPT" % p)
            rc, out = run(ssh, "iptables -C INPUT -p tcp --dport %d:%d -j ACCEPT "
                               "2>/dev/null && echo yes || echo no"
                          % (FTP_LISTEN_PORT, FTP_PASV_END), quiet=True)
            if out.strip() != "yes":
                run(ssh, "iptables -I INPUT 5 -p tcp --dport %d:%d -j ACCEPT"
                    % (FTP_LISTEN_PORT, FTP_PASV_END))
                run(ssh, "iptables -I INPUT 5 -p tcp --dport %d:%d -j ACCEPT"
                    % (18200, 18299))
            rc, policy = run(ssh, "iptables -S INPUT | head -1", quiet=True)
            print("  iptables INPUT policy: %s" % policy)
            # 持久化：优先 iptables-services；否则保存到 /etc/sysconfig/iptables
            rc, out = run(ssh, "command -v iptables-save >/dev/null && echo yes "
                               "|| echo no", quiet=True)
            if out.strip() == "yes":
                run(ssh, "iptables-save > /etc/sysconfig/iptables")
                run(ssh, "systemctl is-enabled iptables >/dev/null 2>&1 && "
                         "echo enabled || echo not-enabled", quiet=True)
                print("  iptables rules persisted to /etc/sysconfig/iptables")
                for p in all_ports:
                    rc, out = run(ssh, "grep -c 'dport %d' /etc/sysconfig/iptables"
                                  % p, quiet=True)
                    if out.strip() == "0":
                        die("iptables persistent file missing port rule: %d" % p)
            else:
                die("no firewall tool available (firewalld inactive, "
                    "iptables-save missing) - port %d NOT opened" % service_port)

    # ------------------------------------------------------------------
    # 8. 健康检查（legacy HTTP + HTTPS 双端口 + 跨类 404）
    # ------------------------------------------------------------------
    print("[8] health check")
    run(ssh, "systemctl is-active %s" % SERVICE_NAME)
    terminal_port = int(cfg.get("terminal_port", 18443))
    console_port = int(cfg.get("console_port", 8443))

    def restore_backup():
        """H5：从 pre_<ts> 备份自动恢复 server/ console/ config.json 并重启。

        返回 (ok, message)。无备份（首次部署）时如实返回 False。"""
        if not backup_dir:
            return False, "无可用备份（首次部署，无上一版可回退）"
        rc, _ = run(ssh, "test -d %s/server" % backup_dir,
                    check=False, quiet=True)
        if rc != 0:
            return False, "备份目录缺少 server/：%s" % backup_dir
        script = (
            "set -e; "
            "rm -rf {app}/server && cp -a {bak}/server {app}/server; "
            "if [ -d {bak}/console ]; then rm -rf {app}/console && "
            "cp -a {bak}/console {app}/console; fi; "
            "if [ -f {bak}/config.json ]; then cp -a {bak}/config.json {cfg}; fi; "
            "systemctl restart {svc}; sleep 2; systemctl is-active {svc}"
        ).format(app=APP_DIR, bak=backup_dir, cfg=CONFIG_PATH, svc=SERVICE_NAME)
        rc, out = run(ssh, script, check=False)
        tail = [l for l in out.strip().splitlines() if l.strip()][-1:]
        if tail == ["active"]:
            return True, "已回滚到上一版并重启成功（备份：%s）" % backup_dir
        return False, ("回滚后服务未 active，请人工介入；输出尾部：%s"
                       % out.strip()[-200:])

    def die_health(reason):
        """健康检查未通过 → 先自动回滚（避免线上长时间不可用）→ 再退出。"""
        print("  [health] FAIL: %s" % reason)
        ok, msg = restore_backup()
        print("  [health] 自动回滚：%s" % msg)
        if ok:
            print("  [health] 已回退上一版；请排查本次变更原因后重试部署")
        else:
            print("  [health] 未能自动回滚，请人工处理（备份见上）")
        die(reason)

    out = ""
    for _ in range(15):  # 启动初始化（建表/密钥生成）可能耗时数秒，轮询等待监听
        rc, out = run(ssh, "ss -tlnp | grep -E ':(%d|%d|%d) ' || true"
                      % (service_port, terminal_port, console_port),
                      check=False, quiet=True)
        lines = [l for l in out.splitlines() if l.strip()]
        if len(lines) >= (3 if not args.skip_certs else 1):
            break
        time.sleep(1)
    if not out:
        die_health("port %d NOT listening within 15s" % service_port)
    print("  listening:\n    %s" % out.replace("\n", "\n    "))

    def http_code(url):
        _, o = run(ssh, "curl -s -o /dev/null -w '%%{http_code}' %s" % url,
                   quiet=True)
        return o.strip()

    # legacy HTTP：全量路由
    if http_code("http://127.0.0.1:%d/api/v1/health" % service_port) != "200":
        die_health("legacy HTTP health failed")
    if http_code("http://127.0.0.1:%d/" % service_port) != "200":
        die_health("legacy HTTP console page failed")
    print("  legacy HTTP %d: health 200 / console 200" % service_port)

    if not args.skip_certs:
        cacert = "%s/certs/ca.crt" % DATA_DIR
        # TLS 探活用本机 IP（证书 SAN=172.17.5.215，连 127.0.0.1 会被主机名
        # 校验正确拒绝——SAN 配对原则，与 spike-1 教训一致）
        def https_code(port, path_suffix="/", extra=""):
            _, o = run(ssh, "curl -s -o /dev/null -w '%%{http_code}' --cacert "
                            "%s https://%s:%d%s %s"
                       % (cacert, host, port, path_suffix, extra), quiet=True)
            return o.strip()

        # TLS 终端口：health + 跨类 404
        if https_code(terminal_port, "/api/v1/health") != "200":
            die_health("TLS terminal health failed (verify with own CA)")
        if https_code(terminal_port) != "404":
            die_health("TLS terminal cross-type static NOT 404")
        # TLS 管理口：页面 + health + 跨类 404
        if https_code(console_port) != "200":
            die_health("TLS console page failed")
        if https_code(console_port, "/api/v1/health") != "200":
            die_health("TLS console health failed")
        if https_code(console_port, "/api/v1/terminals/WIN-X/heartbeat",
                      extra="-X POST -d '{}'") != "404":
            die_health("TLS console terminal-route cross-type NOT 404")
        print("  TLS terminal %d: health 200 / cross-type 404 (CA-verified)"
              % terminal_port)
        print("  TLS console  %d: page 200 / health 200 / cross-type 404"
              % console_port)

    rc, journal = run(ssh, "journalctl -u %s -n 3 --no-pager | tail -3" % SERVICE_NAME,
                      quiet=True)
    print("  journal tail:\n    %s" % journal.replace("\n", "\n    "))

    sftp.close()
    ssh.close()

    print("=== deploy done ===")
    print("service   : %s (systemd, enabled)" % SERVICE_NAME)
    print("app dir   : %s" % APP_DIR)
    print("data dir  : %s" % DATA_DIR)
    print("config    : %s" % CONFIG_PATH)
    print("backup    : %s" % (backup_dir or "(first deploy)"))
    if not args.skip_certs:
        print("endpoint  : https://%s:%d/  (console, TLS 管理口)" % (host, console_port))
        print("            https://%s:%d/api/v1/*  (terminal API, TLS 终端口)"
              % (host, terminal_port))
        print("            http://%s:%d/  (legacy 过渡，收口后下线)" % (host, service_port))
        print("CA SHA256 : %s" % ca_fp_hex)
        print("            （终端 uplink_config.server_ca_fingerprint 同值；"
              "ca.crt 已下载 deploy/certs/）")
    else:
        print("endpoint  : http://%s:%d/  (console)  /api/v1/* (terminal API)"
              % (host, service_port))
    print("NOTE      : terminal_token / console_password 见 config.json（敏感，勿外传）")


if __name__ == "__main__":
    main()
