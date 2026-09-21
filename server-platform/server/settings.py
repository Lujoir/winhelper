# -*- coding: utf-8 -*-
"""观枢终端平台服务端 · 运行时配置存储（settings 表，敏感值自动加解密）。

- 非敏感值：明文字符串直存。
- 敏感值（SENSITIVE_KEYS 内）：secretsbox 加密后存 b64 密文，读取时解密；
  列表输出时一律脱敏（前3 + **** + 后4）。
- config.json 只承载启动级参数（port/token/口令/路径）；运行时可变配置
  （白名单/算力平台/FTP/iperf 端口段）一律走本存储，控制台可热改。
"""
import time

from secretsbox import SecretsBox

# 敏感键清单：这些 key 的值落库即加密、出接口即脱敏
# huorong.ak/sk（火绒 API 凭据，ADR-033）按任务口径一并加密（sk 为真密钥，
# ak 属标识符加密属超面保护，零成本）
SENSITIVE_KEYS = ("llm.api_key", "ftp.password", "switch.default_password",
                  "huorong.ak", "huorong.sk", "nad.wol_api_key")

# 键名规范：<组>.<项>。预置默认值（未设置时 get 返回 default）
DEFAULTS = {
    "llm.url": "",
    "llm.model": "",
    "ftp.enabled": "0",
    "ftp.listen_port": "18121",
    "ftp.pasv_start": "18122",
    "ftp.pasv_end": "18141",
    "ftp.username": "eyetermftp",
    "storage.root_dir": "/data/terminal-platform/storage",
    "smb.mount_cmd": "",
    "iperf.port_start": "18200",
    "iperf.port_end": "18299",
    "iperf.path": "/usr/bin/iperf3",
    "switch.default_username": "",
    # 火绒终端安全（ADR-033）：凭据由 main 部署时经 config_cli --stdin 注入
    "huorong.url": "",
    "huorong.ak": "",
    "huorong.sk": "",
    "huorong.enabled": "0",
    "huorong.sync_interval_sec": "300",
    "huorong.tls_fingerprint": "",
    # 远程唤醒（WoL 双路线，ADR-044 增补）：relay=同网段中继（默认），
    # nad=画方准入唤醒（接口未接入时调度器如实回退 relay，不许哑等）
    "wol.wake_mode": "relay",
    "wol.pinned_relay": "",        # 专用代理兜底终端 tid（候选耗尽时用）
    "wol.relay_step_sec": "120",   # 单台中继观察窗
    "wol.relay_window_sec": "300", # 整轮全局超窗（用户口径 5 分钟）
    "wol.max_inflight": "3",       # 同时在途唤醒链上限（防风暴）
    # 画方准入唤醒（方案一，stub）：规格到手后接通 wake_via_nad
    "nad.wol_api_url": "",
    "nad.wol_api_key": "",
}


def mask_secret(value):
    """脱敏显示：保留前3后4（过短则全掩码）。"""
    if not value:
        return "(未配置)"
    if len(value) <= 8:
        return "****"
    return value[:3] + "****" + value[-4:]


class SettingsStore(object):
    """settings 表访问层（敏感值透明加解密）。"""

    def __init__(self, store, box):
        self._store = store
        self._box = box

    def get(self, key, default=None):
        row = self._store.settings_get(key)
        if row is None:
            return DEFAULTS.get(key, default)
        value = row["value"]
        if key in SENSITIVE_KEYS:
            try:
                return self._box.decrypt(value)
            except ValueError:
                return default
        return value

    def set(self, key, value):
        if key in SENSITIVE_KEYS:
            value = self._box.encrypt(value)
        self._store.settings_set(key, value,
                                 1 if key in SENSITIVE_KEYS else 0)

    def delete(self, key):
        self._store.settings_delete(key)

    def encrypt(self, value):
        """供非 settings 存储的敏感字段（如 switches.password_enc）复用
        同一 SecretsBox 加密（ADR-026）。"""
        return self._box.encrypt(value)

    def decrypt(self, value):
        return self._box.decrypt(value)

    def all_masked(self):
        """全量输出（敏感脱敏），供控制台配置页渲染。"""
        out = {}
        for key, value, masked, updated in self._store.settings_list():
            if masked:
                try:
                    plain = self._box.decrypt(value)
                except ValueError:
                    plain = ""
                out[key] = mask_secret(plain)
            else:
                out[key] = value
            out[key + ".updated_at"] = updated
        for key, value in DEFAULTS.items():
            if key not in out:
                out[key] = mask_secret(value) if key in SENSITIVE_KEYS else value
                out[key + ".updated_at"] = 0
        return out

    @staticmethod
    def is_sensitive(key):
        return key in SENSITIVE_KEYS

    def now(self):
        return int(time.time())
