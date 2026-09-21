#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""安全改造 R1 · H3 单测：SSH 主机密钥校验策略。

覆盖：指纹归一 / 来源优先级（env > known_hosts 精确行 > 通用行）/ 三种策略态
（pinned / insecure / missing）/ 比对正反例（不匹配必须抛错 = fail-closed）。

零网络：paramiko 客户端与传输层均为打桩对象。
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))

import ssh_hostkey as hk  # noqa: E402

import paramiko  # noqa: E402

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                          (" | " + detail) if detail and not cond else ""))


class _FakeKey(object):
    def __init__(self, fp, blob=None):
        self._fp = fp
        self._blob = fp if blob is None else blob

    def asbytes(self):
        return self._blob

    def get_fingerprint(self):
        return self._fp


class _FakeTransport(object):
    def __init__(self, blob):
        self._blob = blob

    def get_remote_server_key(self):
        return _FakeKey(None, self._blob)


class _FakeClient(object):
    def __init__(self, fp=b"\xaa" * 32):
        self._transport = _FakeTransport(fp)
        self.policy = None

    def set_missing_host_key_policy(self, policy):
        self.policy = policy

    def get_transport(self):
        return self._transport


def main():
    # 1) 指纹归一
    check("指纹归一（去冒号 + 转小写）",
          hk.norm_fp("AA:BB:CC") == "aabbcc" and hk.norm_fp(None) == ""
          and hk.norm_fp("  AaBb  ") == "aabb")

    # 2) 来源优先级：env > known_hosts
    os.environ["ETP_SSH_HOSTKEY"] = "AA:BB"
    check("环境变量指纹优先", hk.load_expected("h", 22) == "aabb")
    del os.environ["ETP_SSH_HOSTKEY"]

    fd, path = tempfile.mkstemp()
    os.close(fd)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# 注释\n10.0.0.1:22  deadbeef\ncafebabe\n")
    old_known = hk.KNOWN_HOSTS
    hk.KNOWN_HOSTS = path
    check("known_hosts 精确行匹配",
          hk.load_expected("10.0.0.1", 22) == "deadbeef")
    check("known_hosts 通用行回退",
          hk.load_expected("10.0.0.9", 22) == "cafebabe")

    # 3) 无指纹 → missing（RejectPolicy，调用方须终止）
    hk.KNOWN_HOSTS = os.path.join(tempfile.gettempdir(),
                                  "definitely_missing_known_hosts")
    os.environ.pop("ETP_SSH_INSECURE", None)
    client = _FakeClient()
    mode, _ = hk.harden(client, "1.2.3.4", 22)
    check("无指纹 → missing 且 RejectPolicy",
          mode == "missing" and isinstance(client.policy, paramiko.RejectPolicy))

    # 4) 显式 ETP_SSH_INSECURE=1 → insecure（AutoAddPolicy）
    os.environ["ETP_SSH_INSECURE"] = "1"
    client = _FakeClient()
    mode, _ = hk.harden(client, "1.2.3.4", 22)
    check("显式放宽 → insecure 且 AutoAddPolicy",
          mode == "insecure"
          and isinstance(client.policy, paramiko.AutoAddPolicy))
    os.environ.pop("ETP_SSH_INSECURE", None)

    # 5) 提供指纹 → pinned，比对一致通过（SHA256 自算，以实际计算值为期望）
    blob = b"\xab" * 32
    real_fp = hk.key_fingerprint(_FakeKey(None, blob))
    check("指纹口径为 SHA256（64 hex）", len(real_fp) == 64)
    client = _FakeClient(blob)
    mode, expected = hk.harden(client, "1.2.3.4", 22, expected=real_fp)
    check("提供指纹 → pinned 且自定义指纹策略",
          mode == "pinned" and isinstance(client.policy, hk.PinnedPolicy))
    try:
        got = hk.verify(client, expected)
        ok = (got == real_fp)
    except Exception as exc:
        ok = False
        print("    verify 异常：%s" % exc)
    check("指纹一致 → 校验通过", ok)

    # 6) 指纹不一致 → 必抛错（fail-closed）
    client_bad = _FakeClient(b"\xcd" * 32)
    try:
        hk.verify(client_bad, real_fp)
        raised = False
    except RuntimeError as exc:
        raised = "不匹配" in str(exc)
    check("指纹不一致 → 抛错（fail-closed）", raised)

    # 7) 期望指纹带冒号/大写仍可匹配（格式容忍）
    colon = ":".join(real_fp[i:i + 2] for i in range(0, len(real_fp), 2))
    try:
        hk.verify(_FakeClient(blob), colon.upper())
        tolerant = True
    except Exception:
        tolerant = False
    check("预期指纹含冒号/大写仍可匹配", tolerant)

    # 8) PinnedPolicy：认证前比对（匹配接受 / 不匹配抛 SSHException）
    policy = hk.PinnedPolicy(real_fp)
    try:
        policy.missing_host_key(None, "host", _FakeKey(None, blob))
        accepted = (policy.seen == real_fp)
    except Exception:
        accepted = False
    check("PinnedPolicy 指纹匹配 → 接受", accepted)

    policy_bad = hk.PinnedPolicy(real_fp)
    try:
        policy_bad.missing_host_key(None, "host", _FakeKey(None, b"\xcd" * 32))
        rejected = False
    except paramiko.SSHException:
        rejected = True
    check("PinnedPolicy 指纹不匹配 → 认证前抛错", rejected)

    hk.KNOWN_HOSTS = old_known
    try:
        os.remove(path)
    except OSError:
        pass

    print("\n=== ssh hostkey tests: pass %d / fail %d ==="
          % (len(PASSED), len(FAILED)))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
