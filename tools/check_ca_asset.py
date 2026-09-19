#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""构建期 CA 资产校验（安全改造 R1 · H4）。

背景
----
`assets/platform_ca.pem` 随客户端打包，用于 uplink HTTPS 验签（配合
`uplink_config.server_ca_fingerprint` 双层校验）。历史上该资产一度是"开发占位
CA"，`winhelper.spec` 的注释至今仍写着"当前为开发占位 CA"，且构建流程没有任何
校验 —— 一旦误打包占位/过期 CA，客户端将无法与服务端建立可信连接（或更糟：
信任了错误的 CA）。

本脚本作为**构建前置门禁**：
  1. 解析 `assets/platform_ca.pem`，计算 DER 主体 SHA256 指纹（hex 小写）
  2. 与期望指纹比对（`--expect` / 环境变量 / `--ref` 参考证书文件）
  3. 校验证书剩余有效期 ≥ `--min-days`（默认 90 天；无法解析时告警不阻断）
  4. 任一失败 → 非零退出（构建应中止）

用法
----
    python tools/check_ca_asset.py --ref deploy/certs/ca.crt
    python tools/check_ca_asset.py --expect 733c1039...02126010b
    python tools/check_ca_asset.py                 # 仅做自检（结构 + 有效期）
"""
import argparse
import base64
import hashlib
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
DEFAULT_ASSET = os.path.join(ROOT, "assets", "platform_ca.pem")

_PEM_RE = re.compile(
    rb"-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----", re.S)


def pem_to_der(path):
    """读取 PEM 首个证书块 → DER bytes（失败抛 ValueError）。"""
    with open(path, "rb") as fh:
        raw = fh.read()
    m = _PEM_RE.search(raw)
    if not m:
        raise ValueError("未找到 CERTIFICATE PEM 块（文件可能不是证书或为占位）")
    b64 = re.sub(rb"\s+", b"", m.group(1))
    try:
        der = base64.b64decode(b64, validate=True)
    except Exception as exc:
        raise ValueError("PEM base64 解码失败：%s" % exc)
    if len(der) < 100:
        raise ValueError("DER 长度异常（%d 字节），可能是占位文件" % len(der))
    return der


def fingerprint(der):
    """DER 主体 SHA256 指纹（hex 小写，与终端 uplink 指纹口径一致）。"""
    return hashlib.sha256(der).hexdigest()


def not_after_days(path):
    """尽力解析证书 notAfter，返回剩余天数；无法解析返回 None。"""
    try:
        import ssl
        info = ssl._ssl._test_decode_cert(path)   # 私有 API：仅用于构建期自检
        date_str = info.get("notAfter")
        if not date_str:
            return None
        ts = time.mktime(time.strptime(date_str, "%b %d %H:%M:%S %Y %Z"))
        return int((ts - time.time()) // 86400)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="校验随包 CA 资产")
    ap.add_argument("--asset", default=DEFAULT_ASSET)
    ap.add_argument("--expect", default=os.environ.get("EYETERM_CA_FINGERPRINT", ""),
                    help="期望指纹（hex，可含冒号/大写）")
    ap.add_argument("--ref", default="",
                    help="参考证书文件（如服务器 ca.crt），以其指纹为期望值")
    ap.add_argument("--min-days", type=int, default=90,
                    help="证书剩余有效期下限（天），默认 90")
    args = ap.parse_args()

    if not os.path.isfile(args.asset):
        print("FAIL: 资产不存在 %s" % args.asset)
        return 2

    try:
        der = pem_to_der(args.asset)
    except ValueError as exc:
        print("FAIL: %s" % exc)
        return 2

    got = fingerprint(der)
    print("asset    : %s" % args.asset)
    print("sha256   : %s" % got)

    expect = (args.expect or "").replace(":", "").strip().lower()
    if args.ref:
        if not os.path.isfile(args.ref):
            print("FAIL: 参考证书不存在 %s" % args.ref)
            return 2
        ref_fp = fingerprint(pem_to_der(args.ref))
        print("ref      : %s → %s" % (args.ref, ref_fp))
        if expect and expect != ref_fp:
            print("FAIL: --expect 与 --ref 指纹不一致")
            return 2
        expect = ref_fp

    if expect:
        if expect != got:
            print("FAIL: 指纹不匹配（资产可能为占位/旧 CA）")
            print("      expected=%s" % expect)
            return 2
        print("OK: 指纹与期望一致")
    else:
        print("WARN: 未提供期望指纹（--expect / --ref / EYETERM_CA_FINGERPRINT），"
              "仅做结构自检")

    days = not_after_days(args.asset)
    if days is None:
        print("WARN: 未能解析证书有效期（跳过），建议人工确认")
    else:
        print("expires  : 剩余 %d 天" % days)
        if days < args.min_days:
            print("FAIL: 剩余有效期 %d 天 < 下限 %d 天" % (days, args.min_days))
            return 2

    print("PASS: CA 资产校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
