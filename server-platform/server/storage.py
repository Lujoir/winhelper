# -*- coding: utf-8 -*-
"""观枢终端平台服务端 · 日志存储管理（存储目录 / SMB 挂载 / FTP 上传登记）。

方案（ADR-014）：FTP 服务端用系统 vsftpd（成熟、被动段可控、零 python 依赖），
由 deploy.py 安装配置并持久放行端口；本模块负责运行时：
- 存储根目录管理（默认 /data/terminal-platform/storage）
- SMB 挂载状态检测与管理员触发的挂载执行（mount 命令配置化）
- 上传登记：终端 HTTP 上报登记（source=api）+ 目录扫描补登记（source=scan）
"""
import hashlib
import os
import shlex
import subprocess

# mount 类命令白名单（H6）：配置项 smb.mount_cmd 的首命令必须命中
_MOUNT_ALLOWED_HEADS = {"mount", "mount.cifs", "mount.nfs", "mount.nfs4", "umount"}


def _split_cmd(cmd):
    """命令串 → argv（H6）。

    注意：`shlex.split` 默认按 POSIX 语义把反斜杠当转义符，Windows 路径会被破坏，
    因此仅在类 Unix 平台启用 POSIX 解析。
    """
    return shlex.split(cmd, posix=(os.name != "nt"))


def _run_cmd(argv, timeout=30):
    """执行外部命令 —— **列表式 argv + shell=False**（安全改造 R1 · H6）。

    原实现 `subprocess.run(cmd, shell=True)` 直接拼接字符串，而 `storage.root_dir`
    与 `smb.mount_cmd` 来自 settings（管理员可经 /console/settings 修改），
    构成命令注入面（findmnt/mount 参数拼接）。现统一为：
      · 调用方传列表 argv（传字符串时经 shlex 拆分，兼容旧调用点）
      · `shell=False`，参数不再经过 shell 解析
    """
    if isinstance(argv, str):
        argv = _split_cmd(argv)
    try:
        proc = subprocess.run(argv, shell=False, capture_output=True,
                              timeout=timeout)
        return proc.returncode, proc.stdout.decode("utf-8", "replace").strip(), \
            proc.stderr.decode("utf-8", "replace").strip()
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return -1, "", str(exc)


def sha256_file(path, chunk=1024 * 1024):
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while True:
                block = fh.read(chunk)
                if not block:
                    break
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return ""


class StorageManager(object):
    """存储目录 + SMB 挂载 + 扫描登记。"""

    def __init__(self, store, settings):
        self._store = store
        self._settings = settings

    @property
    def root_dir(self):
        return self._settings.get("storage.root_dir") or "/data/terminal-platform/storage"

    def ensure_root(self):
        root = self.root_dir
        if not os.path.isdir(root):
            try:
                os.makedirs(root)
            except OSError:
                pass
        return root

    # ---- SMB 挂载 ----

    def mount_status(self):
        """检测存储根目录是否为挂载点。"""
        root = self.ensure_root()
        rc, out, _ = _run_cmd(["findmnt", "-n", "-o", "SOURCE", "--", root])
        mounted = (rc == 0 and bool(out))
        return {"root_dir": root, "mounted": mounted, "source": out if mounted else "",
                "mount_cmd": self._settings.get("smb.mount_cmd") or ""}

    def exec_mount(self):
        """管理员触发的挂载执行。

        H6：配置的 mount 命令先解析为 argv 并做**首命令白名单**校验，
        再以 `shell=False` 执行；不再把配置串原样交给 shell。
        """
        cmd = (self._settings.get("smb.mount_cmd") or "").strip()
        if not cmd:
            return {"ok": False, "error": "smb.mount_cmd 未配置"}
        argv = _split_cmd(cmd)
        if not argv:
            return {"ok": False, "error": "smb.mount_cmd 解析为空"}
        head = os.path.basename(argv[0])
        if head not in _MOUNT_ALLOWED_HEADS:
            return {"ok": False,
                    "error": "smb.mount_cmd 首命令 %s 不在允许列表 %s（已拒绝执行）"
                             % (head, sorted(_MOUNT_ALLOWED_HEADS))}
        self.ensure_root()
        rc, out, err = _run_cmd(argv, timeout=60)
        ok = (rc == 0 and self.mount_status()["mounted"])
        return {"ok": ok, "rc": rc, "stdout": out[:400], "stderr": err[:400]}

    # ---- 扫描登记 ----

    def scan_uploads(self, terminal_id=""):
        """扫描存储根目录（不递归），新增文件自动登记（source=scan）。"""
        root = self.ensure_root()
        known = self._store.known_upload_names()
        added = 0
        skipped = 0
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            return {"ok": False, "error": "storage dir unreadable", "added": 0}
        for name in entries:
            full = os.path.join(root, name)
            if not os.path.isfile(full):
                continue
            size = os.path.getsize(full)
            if (name, size) in known:
                skipped += 1
                continue
            digest = sha256_file(full)
            self._store.insert_upload(terminal_id, name, size, digest, full, "scan",
                                      int(os.path.getmtime(full)))
            known.add((name, size))
            added += 1
        return {"ok": True, "added": added, "skipped": skipped, "root_dir": root}

    # ---- FTP 状态（实际服务由 vsftpd 承载，此处仅汇报配置与进程状态）----

    def ftp_status(self):
        enabled = self._settings.get("ftp.enabled") == "1"
        rc, out, _ = _run_cmd(["systemctl", "is-active", "vsftpd"])
        port = self._settings.get("ftp.listen_port")
        pasv = "%s-%s" % (self._settings.get("ftp.pasv_start"),
                          self._settings.get("ftp.pasv_end"))
        return {"configured_enabled": enabled, "vsftpd_active": out == "active",
                "listen_port": port, "pasv_range": pasv,
                "username": self._settings.get("ftp.username"),
                "password_set": bool(self._settings.get("ftp.password")),
                "root_dir": self.root_dir}
