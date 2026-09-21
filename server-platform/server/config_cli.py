# -*- coding: utf-8 -*-
"""运行时配置 CLI（远端服务器执行，部署脚本与运维使用）。

用法（venv python）：
    python config_cli.py --config /data/terminal-platform/config.json list
    python config_cli.py set llm.url https://llm.example.local
    python config_cli.py set llm.api_key --stdin   # 值经 stdin 传入（避免 ps 泄露）
    python config_cli.py get llm.api_key --mask
敏感键自动加密落库；输出侧一律脱敏。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from secretsbox import SecretsBox          # noqa: E402
from settings import SettingsStore, mask_secret  # noqa: E402
from store import Store                    # noqa: E402

# 说明：list 子命令经 SettingsStore.all_masked() 输出——它合并「settings 表
# 实际存在的键」与「DEFAULTS 预置键」，因此像 llm.model_fallback / llm.api_key /
# switch.default_password / ftp.password 这类不在 DEFAULTS 中的键也能列出；
# 敏感键由 all_masked 内部脱敏，无需在此重复判断。


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="/data/terminal-platform/config.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_set = sub.add_parser("set")
    p_set.add_argument("key")
    p_set.add_argument("value", nargs="?")
    p_set.add_argument("--stdin", action="store_true")
    p_get = sub.add_parser("get")
    p_get.add_argument("key")
    p_get.add_argument("--mask", action="store_true")
    sub.add_parser("list")
    args = ap.parse_args()

    with open(args.config, "rb") as fh:
        config = json.loads(fh.read().decode("utf-8-sig"))
    data_dir = config.get("data_dir") or "/data/terminal-platform/data"
    store = Store(os.path.join(data_dir, "eyeterm.db"))
    box = SecretsBox(os.path.join(data_dir, "keys", "machine.key"))
    settings = SettingsStore(store, box)

    if args.cmd == "set":
        value = args.value
        if args.stdin:
            value = sys.stdin.read().strip()
        if value is None:
            raise SystemExit("set: missing value (or use --stdin)")
        settings.set(args.key, value)
        print("set %s -> %s" % (args.key,
                                mask_secret(value) if settings.is_sensitive(args.key)
                                else value))
    elif args.cmd == "get":
        value = settings.get(args.key)
        if value is None:
            print("(unset)")
        elif args.mask:
            print(mask_secret(value))
        elif settings.is_sensitive(args.key):
            raise SystemExit("refusing to print sensitive value (use --mask)")
        else:
            print(value)
    else:
        data = settings.all_masked()
        for key in sorted(k for k in data if not k.endswith(".updated_at")):
            print("%-22s = %s" % (key, data[key]))


if __name__ == "__main__":
    main()
