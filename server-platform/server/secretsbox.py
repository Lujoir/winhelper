# -*- coding: utf-8 -*-
"""观枢终端平台服务端 · 机器绑定对称加密盒（纯标准库，零第三方依赖）。

用途：敏感配置（LLM key / FTP 密码等）加密存储于 SQLite，密钥不落仓库。

威胁模型（ADR-012）：防御目标是**静态配置泄露**——仓库、数据库文件、
备份归档中不出现明文凭据。持有 root 或本机密钥文件的攻击者本就等同
于持有解密能力，不在防御范围（与磁盘加密同理）。

构造：encrypt-then-MAC
- 流密码：HMAC-SHA256(key, nonce || counter_be64) 串联为 keystream，逐字节异或
- 认证：tag = HMAC-SHA256(mac_key, nonce || ciphertext)，mac_key = HMAC(key, b"mac")
- 编码：base64(nonce[16] || ciphertext || tag[32])
- 密钥：机器密钥文件（hex，32 字节随机，0600），首次访问自动生成
"""
import base64
import binascii
import hashlib
import hmac
import os
import secrets

KEY_BYTES = 32
NONCE_LEN = 16
TAG_LEN = 32


def _keystream(key, nonce, length):
    out = b""
    counter = 0
    while len(out) < length:
        block = hmac.new(key, nonce + counter.to_bytes(8, "big"),
                         hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


class SecretsBox(object):
    """基于机器密钥文件的对称加解密。"""

    def __init__(self, key_path):
        self._key_path = key_path
        self._key = self._load_or_create()

    def _load_or_create(self):
        directory = os.path.dirname(self._key_path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        if os.path.isfile(self._key_path):
            with open(self._key_path, "r") as fh:
                hexkey = fh.read().strip()
            try:
                key = binascii.unhexlify(hexkey)
                if len(key) != KEY_BYTES:
                    raise ValueError
            except (ValueError, binascii.Error):
                raise SystemExit("machine key file corrupted: %s" % self._key_path)
            return key
        key = secrets.token_bytes(KEY_BYTES)
        fd = os.open(self._key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(binascii.hexlify(key).decode())
        os.chmod(self._key_path, 0o600)
        return key

    def encrypt(self, plaintext):
        if not isinstance(plaintext, str):
            plaintext = str(plaintext)
        nonce = secrets.token_bytes(NONCE_LEN)
        data = plaintext.encode("utf-8")
        cipher = bytes(a ^ b for a, b in zip(data, _keystream(self._key, nonce,
                                                              len(data))))
        mac_key = hmac.new(self._key, b"mac", hashlib.sha256).digest()
        tag = hmac.new(mac_key, nonce + cipher, hashlib.sha256).digest()
        return base64.b64encode(nonce + cipher + tag).decode("ascii")

    def decrypt(self, token):
        try:
            raw = base64.b64decode(token.encode("ascii"), validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("ciphertext not base64")
        if len(raw) < NONCE_LEN + TAG_LEN:
            raise ValueError("ciphertext too short")
        nonce = raw[:NONCE_LEN]
        cipher = raw[NONCE_LEN:-TAG_LEN]
        tag = raw[-TAG_LEN:]
        mac_key = hmac.new(self._key, b"mac", hashlib.sha256).digest()
        expect = hmac.new(mac_key, nonce + cipher, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expect):
            raise ValueError("ciphertext auth failed")
        data = bytes(a ^ b for a, b in zip(cipher, _keystream(self._key, nonce,
                                                              len(cipher))))
        return data.decode("utf-8")
