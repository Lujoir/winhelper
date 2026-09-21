#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ADR-032 单测：scope 路由分离 + TLS 配置解析 + 证书过期解析。

运行：python tools/test_https_split.py
（纯标准库 + 本机 cryptography 生成测试证书，py39 兼容目标运行时）
"""
import os
import shutil
import ssl
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "server")))
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "deploy")))
sys.path.insert(0, _HERE)

import api  # noqa: E402
import app  # noqa: E402
from gen_certs import gen_local  # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" | " + str(detail)) if (detail and not cond) else ""))


def dispatch_status(scope, method, path, headers=None, body=b""):
    """无 ctx 依赖的 dispatch 调用（scope 404 与 health 不触库）。"""
    try:
        result = api.dispatch(None, method, path, {}, headers or {}, body,
                              "127.0.0.1", scope=scope)
        if isinstance(result, tuple):
            return result[0]
        return result.status
    except api.ApiError as exc:
        return exc.status


def main():
    tmp = tempfile.mkdtemp(prefix="eyeterm_test_tls_")
    try:
        # ---- 1. scope 白名单纯函数 ----
        print("[1] _scope_allowed")
        sa = api._scope_allowed
        check("terminal allows health",
              sa("terminal", "GET", "/api/v1/health"))
        check("terminal allows terminals/*",
              sa("terminal", "POST", "/api/v1/terminals/WIN-A/heartbeat"))
        check("terminal allows ai/analyze",
              sa("terminal", "POST", "/api/v1/ai/analyze"))
        check("terminal rejects console/login",
              not sa("terminal", "POST", "/api/v1/console/login"))
        check("terminal rejects static /",
              not sa("terminal", "GET", "/"))
        check("console allows console/*",
              sa("console", "POST", "/api/v1/console/login"))
        check("console allows static GET /",
              sa("console", "GET", "/"))
        check("console rejects terminal heartbeat",
              not sa("console", "POST", "/api/v1/terminals/WIN-A/heartbeat"))
        check("console rejects ai/analyze",
              not sa("console", "POST", "/api/v1/ai/analyze"))
        check("console rejects POST static",
              not sa("console", "POST", "/index.html"))
        check("all allows everything",
              sa("all", "GET", "/") and sa("all", "POST",
                                           "/api/v1/terminals/X/heartbeat"))

        # ---- 2. dispatch 带 scope ----
        print("[2] dispatch scope gate")
        check("health 200 on terminal scope",
              dispatch_status("terminal", "GET", "/api/v1/health") == 200)
        check("health 200 on console scope",
              dispatch_status("console", "GET", "/api/v1/health") == 200)
        check("console/login 404 on terminal scope (pre-auth)",
              dispatch_status("terminal", "POST", "/api/v1/console/login",
                              {}, b"{}") == 404)
        check("terminals/heartbeat 404 on console scope (pre-auth)",
              dispatch_status("console", "POST",
                              "/api/v1/terminals/WIN-X/heartbeat",
                              {}, b"{}") == 404)
        check("static 404 on terminal scope",
              dispatch_status("terminal", "GET", "/") == 404)
        check("legacy all scope unchanged (health)",
              dispatch_status(None, "GET", "/api/v1/health") == 200)

        # ---- 3. tls_settings 解析 ----
        print("[3] tls_settings")
        data_dir = os.path.join(tmp, "data")
        ts = app.tls_settings({}, data_dir)
        check("defaults: enabled", ts["enabled"] is True)
        check("defaults: legacy enabled + port 18090",
              ts["legacy_enabled"] is True and ts["legacy_port"] == 18090)
        check("defaults: terminal 18443 / console 8443",
              ts["terminal_port"] == 18443 and ts["console_port"] == 8443)
        check("defaults: cert path under data_dir/certs",
              ts["cert"] == os.path.join(data_dir, "certs", "server.crt"))
        cfg2 = {"terminal_port": 20001, "console_port": 20002,
                "tls": {"enabled": False, "certs_dir": "/x",
                        "min_tls_version": "TLSv1_3"},
                "legacy_http": {"enabled": False}}
        ts2 = app.tls_settings(cfg2, data_dir)
        check("custom ports + tls off + legacy off",
              ts2["terminal_port"] == 20001 and ts2["console_port"] == 20002
              and ts2["enabled"] is False and ts2["legacy_enabled"] is False)
        check("min tls 1.3 mapping",
              ts2["min_version"] == ssl.TLSVersion.TLSv1_3)

        # ---- 4. 证书生成 + TLS context + 过期解析 ----
        print("[4] tls context + cert_not_after")
        certs = os.path.join(tmp, "certs")
        gen_local(certs, san_dns="localhost", san_ip="127.0.0.1")
        ctx = app.build_tls_context(
            os.path.join(certs, "server.crt"),
            os.path.join(certs, "server.key"), ssl.TLSVersion.TLSv1_2)
        check("context min TLS1.2",
              ctx.minimum_version == ssl.TLSVersion.TLSv1_2)
        check("context cert chain loaded (load_cert_chain no-raise)", True)
        na = app.cert_not_after(os.path.join(certs, "server.crt"))
        days = (na - time.time()) / 86400 if na else None
        check("not_after ≈ 1825d (%.1f)" % days,
              na is not None and 1820 <= days <= 1826)
        check("not_after fails soft on garbage",
              app.cert_not_after(os.path.join(certs, "ca.key")) is None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n=== %d passed / %d failed ===" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  FAILED: %s" % f)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
