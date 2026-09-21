# -*- coding: utf-8 -*-
"""SSH 主机密钥校验（安全改造 R1 · H3）。

背景
----
此前 `deploy/deploy.py`、`server/deep_engine.py` 与运维脚本一律使用
`paramiko.AutoAddPolicy()`，不校验主机密钥 —— 中间人可冒充服务器，
窃取 `ETP_SSH_PASS` 进而接管服务器。

策略（默认安全，仅显式才放宽）
------------------------------
1. 预期指纹来源（优先级从高到低）：
   - 环境变量 `ETP_SSH_HOSTKEY`（SHA256 hex；冒号与大小写不敏感）
   - 已知主机文件（默认 `deploy/known_hosts`，可用 `ETP_SSH_KNOWN_HOSTS` 覆盖）：
     每行 `主机:端口  指纹`；只有一列时视为全局指纹
2. 有预期指纹 → `RejectPolicy` + 连接后逐字节比对，不匹配立即抛错（fail-closed）
3. 无预期指纹 → **默认同样拒绝**并给出采集指引；仅当显式设置
   `ETP_SSH_INSECURE=1` 时才回退 `AutoAddPolicy`（打印告警，供紧急运维）
4. 采集：`python tools/record_hostkey.py --host <IP> --port <PORT> [--write]`
   （只做密钥交换，无需账号密码）

用法
----
    client = paramiko.SSHClient()
    mode, expected = ssh_hostkey.harden(client, host, port)
    if mode == "missing":
        raise RuntimeError(ssh_hostkey.MISSING_HINT)
    client.connect(...)
    if mode == "pinned":
        ssh_hostkey.verify(client, expected)
"""
import hashlib
import os
import sys

try:
    import paramiko
except ImportError:                      # 部署机可能未安装（延迟报错，不阻断导入）
    paramiko = None

_HERE = os.path.dirname(os.path.abspath(__file__))
KNOWN_HOSTS = os.environ.get("ETP_SSH_KNOWN_HOSTS") or os.path.normpath(
    os.path.join(_HERE, "..", "deploy", "known_hosts"))

MISSING_HINT = (
    "未配置 SSH 主机指纹，已拒绝连接（防中间人）。\n"
    "  采集：python tools/record_hostkey.py --host <IP> --port <PORT>\n"
    "        核对后加 --write 写入 deploy/known_hosts\n"
    "  或设置环境变量 ETP_SSH_HOSTKEY=<SHA256 hex>\n"
    "  紧急绕过（不推荐，会打印告警）：ETP_SSH_INSECURE=1"
)


def norm_fp(fp):
    """指纹归一：去冒号、转小写，便于跨格式比较。"""
    return str(fp or "").replace(":", "").strip().lower()


def load_expected(host=None, port=None):
    """读取预期主机指纹（环境变量优先，其次 known_hosts；无则返回空串）。"""
    fp = os.environ.get("ETP_SSH_HOSTKEY")
    if fp:
        return norm_fp(fp)
    try:
        with open(KNOWN_HOSTS, encoding="utf-8") as fh:
            lines = [l.strip() for l in fh
                     if l.strip() and not l.startswith("#")]
    except OSError:
        return ""
    want = "%s:%s" % (host, port)
    generic = ""
    for line in lines:
        parts = line.split()
        if len(parts) == 1:
            generic = parts[0]
        elif parts[0] == want:
            return norm_fp(parts[1])
    return norm_fp(generic)


def key_fingerprint(key):
    """主机公钥 → SHA256 hex 小写指纹。

    自算 SHA256（不依赖 paramiko 的默认算法）：实测 paramiko 5.0 的
    `PKey.get_fingerprint()` 无参数且返回 **MD5**（16 字节）摘要，强度不足；
    这里统一按 SSH 标准口径取 `SHA256(公钥 wire-format blob)`，与
    `ssh-keyscan` / `ssh-keygen -lf` 一致。
    """
    try:
        blob = key.asbytes()
    except Exception:
        blob = b""
    if blob:
        return hashlib.sha256(blob).hexdigest()
    fp = key.get_fingerprint()              # 兜底（异常对象/旧版本）
    return fp.hex() if hasattr(fp, "hex") else norm_fp(fp)


def fingerprint_of(client):
    """取已连接客户端观察到的主机密钥指纹（SHA256 hex 小写）。"""
    return key_fingerprint(client.get_transport().get_remote_server_key())


class PinnedPolicy(paramiko.MissingHostKeyPolicy if paramiko else object):
    """按预期指纹校验主机密钥的策略（在 paramiko 完成认证**之前**生效）。

    paramiko 自带的 `RejectPolicy` 只检查它自己加载的 `host_keys`，与本模块登记的
    指纹无关 —— 直接使用会导致所有连接以 "not found in known_hosts" 失败。
    因此这里实现自定义策略：密钥交换完成后立即比对指纹，不匹配即抛
    `SSHException`；此时口令尚未发送，不存在凭据泄露。
    """

    def __init__(self, expected):
        self.expected = norm_fp(expected)
        self.seen = ""

    def missing_host_key(self, client, hostname, key):
        got = key_fingerprint(key)
        self.seen = got
        if self.expected and got != self.expected:
            raise paramiko.SSHException(
                "SSH 主机密钥指纹不匹配（可能存在中间人）：expected=%s got=%s"
                % (self.expected, got))
        # 指纹一致：接受本次主机密钥（不写入任何 known_hosts 文件）


def harden(client, host=None, port=None, expected=None):
    """设置主机密钥策略，返回 (mode, expected)。

    mode：
      "pinned"   已有预期指纹 → RejectPolicy；连接后须调用 verify()
      "insecure" 显式 ETP_SSH_INSECURE=1 → AutoAddPolicy（告警已打印）
      "missing"  无预期指纹且未显式放宽 → 调用方应终止（见 MISSING_HINT）
    """
    if paramiko is None:
        raise RuntimeError("paramiko not installed")
    expected = norm_fp(expected) if expected else load_expected(host, port)
    if expected:
        # 校验必须发生在认证之前（否则口令已发给可能的中间人）；
        # 且不能用 RejectPolicy —— 它只认 paramiko 自加载的 host_keys。
        client.set_missing_host_key_policy(PinnedPolicy(expected))
        return "pinned", expected
    if os.environ.get("ETP_SSH_INSECURE") == "1":
        sys.stderr.write(
            "WARN: ETP_SSH_INSECURE=1 —— 本次未校验 SSH 主机密钥"
            "（仅限紧急运维，请尽快配置指纹）\n")
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        return "insecure", ""
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    return "missing", ""


def verify(client, expected):
    """连接后校验指纹，不匹配抛错（fail-closed）。返回实际指纹。"""
    got = fingerprint_of(client)
    if norm_fp(got) != norm_fp(expected):
        raise RuntimeError(
            "SSH 主机密钥指纹不匹配（可能存在中间人）：expected=%s got=%s"
            % (expected, got))
    return got
