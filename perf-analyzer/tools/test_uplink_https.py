# -*- coding: utf-8 -*-
"""
uplink.py HTTPS 传输层单测（2026-09-11 专项，与 net_service 冒烟 [19] 同模式）
==============================================================================
覆盖：
  1. load_config 旧配置缺 server_ca_fingerprint 兼容（默认空=跳过比对）+ 已存指纹读回不丢
  2. _uplink_ssl_context 构造（TLSv1.2+ / CERT_REQUIRED / check_hostname=False / 指纹规范化匹配）
  3. 指纹 mismatch 拒绝 + CA 不可得 fail-closed（ca_missing）+ 假 CA 不可解析
  4. 真 TLS 冒烟：openssl 生成临时 CA/证书（SAN IP:127.0.0.1）→ 本地 HTTPS 服务线程
     → _post 全链 200 + 篡改指纹拒绝（openssl 不可用时 SKIP 不计 FAIL）
  5. http:// 过渡兼容：明文 server 线程 _post 200（对 http 现状零行为变化）

运行：python tools/test_uplink_https.py（退出码 0=全过）
"""

import base64
import json
import os
import shutil
import ssl
import sys
import tempfile
import threading
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import uplink  # noqa: E402

ERRORS = []


def check(cond, msg, extra=None):
    tag = "PASS" if cond else "FAIL"
    print("  [%s] %s%s" % (tag, msg, (" | %s" % (extra,) if extra and not cond else "")))
    if not cond:
        ERRORS.append(msg)


def _write_cfg(d, server, fingerprint=""):
    with open(os.path.join(d, "uplink_config.json"), "w", encoding="utf-8") as f:
        cfg = {"enabled": True, "server_url": server, "token": "tok",
               "terminal_id": "WIN-STUB"}
        if fingerprint:
            cfg["server_ca_fingerprint"] = fingerprint
        json.dump(cfg, f, ensure_ascii=False)


class _EchoHandler:
    """POST 回显：{"ok":true,"echo":path,"body":<json>}（http/https 共用）。"""

    def __call__(self):
        import http.server
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b"{}"
                body = json.dumps({"ok": True, "echo": self.path,
                                   "body": json.loads(raw.decode("utf-8"))}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        return H


def _run_http_server(tls_pem=None, tls_key=None):
    """起本地回显服务（127.0.0.1 随机端口），返回 (httpd, port)。"""
    import http.server
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _EchoHandler()())
    if tls_pem:
        sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        sctx.load_cert_chain(tls_pem, tls_key)
        httpd.socket = sctx.wrap_socket(httpd.socket, server_side=True)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


def _find_openssl():
    import shutil as _sh
    p = _sh.which("openssl")
    if p:
        return p
    for cand in (r"C:\Program Files\Git\usr\bin\openssl.exe",
                 r"C:\Program Files\Git\mingw64\bin\openssl.exe"):
        if os.path.isfile(cand):
            return cand
    return None


def _gen_tls_material(openssl, sdir):
    """openssl 生成自签 CA + 服务端证书（SAN IP:127.0.0.1）。返回 (ca_pem, srv_pem, srv_key)。"""
    import subprocess as sp
    os.makedirs(sdir, exist_ok=True)
    ca_key = os.path.join(sdir, "ca.key")
    ca_pem = os.path.join(sdir, "ca.pem")
    srv_key = os.path.join(sdir, "srv.key")
    srv_csr = os.path.join(sdir, "srv.csr")
    srv_pem = os.path.join(sdir, "srv.pem")
    srv_ext = os.path.join(sdir, "srv.ext")
    with open(srv_ext, "w") as f:
        f.write("subjectAltName=IP:127.0.0.1\nextendedKeyUsage=serverAuth\n")
    for cmd in (
        [openssl, "req", "-x509", "-newkey", "rsa:2048", "-keyout", ca_key,
         "-out", ca_pem, "-days", "2", "-nodes", "-subj", "/CN=Uplink Test CA"],
        [openssl, "req", "-newkey", "rsa:2048", "-keyout", srv_key,
         "-out", srv_csr, "-nodes", "-subj", "/CN=127.0.0.1"],
        [openssl, "x509", "-req", "-in", srv_csr, "-CA", ca_pem, "-CAkey", ca_key,
         "-CAcreateserial", "-out", srv_pem, "-days", "2", "-extfile", srv_ext],
    ):
        pr = sp.run(cmd, capture_output=True)
        if pr.returncode != 0:
            return None
    return (ca_pem, srv_pem, srv_key) if os.path.isfile(srv_pem) else None


def main():
    old_cfg_dir = os.environ.get("UPLINK_CONFIG_DIR")
    old_ca_env = os.environ.get(uplink.UPLINK_CA_ENV)
    tmp = tempfile.mkdtemp(prefix="uplink_https_test_")
    try:
        print("== uplink HTTPS 传输层单测 ==")
        os.environ["UPLINK_CONFIG_DIR"] = tmp

        # ---- 1. 配置兼容 ----
        _write_cfg(tmp, "https://127.0.0.1:1")   # 旧配置：无指纹字段
        cfg = uplink.load_config()
        check(cfg.get("server_ca_fingerprint") == "",
              "旧配置缺指纹字段 → 默认空（跳过比对）", cfg.get("server_ca_fingerprint"))
        _write_cfg(tmp, "https://127.0.0.1:1", "a" * 64)
        cfg = uplink.load_config()
        check(cfg.get("server_ca_fingerprint") == "a" * 64,
              "已存指纹读回不丢（白名单合并）", cfg.get("server_ca_fingerprint"))

        # ---- 2. 指纹计算与 fail-closed 分支（假 PEM：指纹可算但内容非法） ----
        fake_pem = ("-----BEGIN CERTIFICATE-----\n"
                    + base64.b64encode(b"\x30\x82\x01\x00" + b"B" * 32).decode()
                    + "\n-----END CERTIFICATE-----\n")
        fake_path = os.path.join(tmp, "fake_ca.pem")
        with open(fake_path, "w") as f:
            f.write(fake_pem)
        # PEM_cert_to_DER_cert 仅做 base64 层解码（不验 DER 语义），格式合法的假 PEM
        # 可算出指纹；内容合法性由 load_verify_locations 兜底（见下方 ca_error 断言）。
        bad_path = os.path.join(tmp, "bad_ca.pem")
        with open(bad_path, "w") as f:
            f.write("this is not a pem at all")
        check(uplink._builtin_ca_fingerprint(bad_path) == "",
              "非 PEM 文本不可解析 → 指纹空串")
        check(uplink._builtin_ca_fingerprint(os.path.join(tmp, "no.pem")) == "",
              "CA 文件不存在 → 指纹空串")

        fp_fake = uplink._builtin_ca_fingerprint(fake_path)
        check(len(fp_fake) == 64 and all(c in "0123456789abcdef" for c in fp_fake),
              "格式合法 PEM 指纹可计算（64 位小写 hex）")
        os.environ[uplink.UPLINK_CA_ENV] = fake_path
        ctx_bad, err_bad = uplink._uplink_ssl_context({"server_ca_fingerprint": fp_fake})
        check(err_bad.startswith("ca_error") and ctx_bad is None,
              "指纹一致但 CA 内容非法 → fail-closed ca_error（不进入连接）", err_bad)

        # ---- 3. 指纹层（mismatch / ca_missing） ----
        ctx2, err2 = uplink._uplink_ssl_context({"server_ca_fingerprint": "deadbeef"})
        check(err2 == "ca_fingerprint_mismatch" and ctx2 is None,
              "下发指纹与内置不一致 → 拒绝连接", err2)
        os.environ[uplink.UPLINK_CA_ENV] = os.path.join(tmp, "no_such_ca.pem")
        code, resp = uplink._post("/api/v1/terminals/register", {})
        check(code == -1 and resp.get("error") == "ca_missing",
              "无可用 CA → https fail-closed ca_missing（http 不受影响）", (code, resp))
        os.environ.pop(uplink.UPLINK_CA_ENV, None)

        # ---- 4. http:// 过渡兼容（明文全链 200，行为不变） ----
        httpd_plain, p_plain = _run_http_server()
        try:
            _write_cfg(tmp, "http://127.0.0.1:%d" % p_plain)
            code, resp = uplink._post("/api/v1/terminals/WIN-STUB/heartbeat", {"ping": 1})
            check(code == 200 and resp.get("ok") is True
                  and resp.get("echo") == "/api/v1/terminals/WIN-STUB/heartbeat",
                  "http:// 全链 POST 200（过渡兼容，零行为变化）", (code, resp))
        finally:
            httpd_plain.shutdown()
            httpd_plain.server_close()

        # ---- 5. 真 TLS 冒烟（openssl 生成临时 CA → HTTPS 服务线程 → 全链） ----
        openssl = _find_openssl()
        if not openssl:
            check(True, "真 TLS 冒烟（openssl 不可用，SKIP）")
        else:
            tls = _gen_tls_material(openssl, os.path.join(tmp, "tls"))
            if not tls:
                check(True, "真 TLS 冒烟（openssl 生成失败，SKIP）")
            else:
                ca_pem, srv_pem, srv_key = tls
                ca_fp = uplink._builtin_ca_fingerprint(ca_pem)
                check(len(ca_fp) == 64 and all(c in "0123456789abcdef" for c in ca_fp),
                      "真 CA 指纹为 64 位小写 hex", ca_fp[:16] + "...")
                os.environ[uplink.UPLINK_CA_ENV] = ca_pem
                # SSL context 构造（合法 CA）：TLSv1.2+ / CERT_REQUIRED / check_hostname=False
                ctx_t, err_t = uplink._uplink_ssl_context({"server_ca_fingerprint": ""})
                check(err_t == "" and ctx_t is not None
                      and ctx_t.minimum_version == ssl.TLSVersion.TLSv1_2
                      and ctx_t.verify_mode == ssl.CERT_REQUIRED
                      and ctx_t.check_hostname is False,
                      "SSL context 构造：TLSv1.2+ / CERT_REQUIRED / check_hostname=False", err_t)
                httpd_tls, p_tls = _run_http_server(srv_pem, srv_key)
                try:
                    _write_cfg(tmp, "https://127.0.0.1:%d" % p_tls, ca_fp)
                    code, resp = uplink._post("/api/v1/terminals/register", {"hw": 1})
                    check(code == 200 and resp.get("ok") is True
                          and (resp.get("body") or {}).get("hw") == 1,
                          "https:// 真 TLS POST 200 + CA 链验证通过", (code, resp))
                    # 指纹规范化：大写带冒号的同一指纹应匹配成功
                    fp_colon = ":".join(ca_fp[i:i + 2].upper() for i in range(0, 64, 2))
                    _write_cfg(tmp, "https://127.0.0.1:%d" % p_tls, fp_colon)
                    code2, resp2 = uplink._post("/api/v1/terminals/WIN-STUB/heartbeat", {})
                    check(code2 == 200 and resp2.get("ok") is True,
                          "指纹下发大写带冒号 → 规范化匹配成功（不误拒）", (code2, resp2))
                    _write_cfg(tmp, "https://127.0.0.1:%d" % p_tls, "0" * 64)
                    code3, resp3 = uplink._post("/api/v1/terminals/register", {})
                    check(code3 == -1 and resp3.get("error") == "ca_fingerprint_mismatch",
                          "真 TLS 篡改指纹 → 连接前拒绝（双层校验第一层生效）", (code3, resp3))
                finally:
                    os.environ.pop(uplink.UPLINK_CA_ENV, None)
                    httpd_tls.shutdown()
                    httpd_tls.server_close()

        print("== 结果：%d 项失败 ==" % len(ERRORS))
        return 1 if ERRORS else 0
    except Exception:
        traceback.print_exc()
        return 2
    finally:
        if old_cfg_dir is not None:
            os.environ["UPLINK_CONFIG_DIR"] = old_cfg_dir
        else:
            os.environ.pop("UPLINK_CONFIG_DIR", None)
        if old_ca_env is not None:
            os.environ[uplink.UPLINK_CA_ENV] = old_ca_env
        else:
            os.environ.pop(uplink.UPLINK_CA_ENV, None)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
