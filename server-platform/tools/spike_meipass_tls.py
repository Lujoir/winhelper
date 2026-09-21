#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""P0 Spike 2（ADR-032）：_MEIPASS CA + urllib TLS 指纹校验连真实端点。

层1 CA 定位+指纹 fail-closed；层2 context 链+主机名+urllib；层3 错指纹必拒。
env: ETP_SPIKE_URL（缺省自起 mini TLS 闭环）、ETP_SPIKE_FP（期望指纹）。
"""
import hashlib
import json
import os
import ssl
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def locate_cert(name):
    cands = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cands.append(os.path.join(meipass, "certs", name))
    here = os.path.dirname(os.path.abspath(__file__))
    cands += [os.path.join(here, "certs", name),
              os.path.normpath(os.path.join(here, "..", "deploy", "certs", name))]
    for p in cands:
        if os.path.isfile(p):
            return p
    return None


def locate_ca():
    return locate_cert("ca.crt")


def ca_fingerprint(ca_path):
    with open(ca_path, "rb") as fh:
        raw = fh.read()
    der = (ssl.PEM_cert_to_DER_cert(raw.decode("ascii"))
           if b"-----BEGIN" in raw else raw)
    return hashlib.sha256(der).hexdigest()


def make_context(ca_path, expected_fp=None):
    if expected_fp:
        fp = ca_fingerprint(ca_path)
        if fp.lower() != expected_fp.lower():
            raise ssl.SSLError("embedded CA fingerprint mismatch")
    ctx = ssl.create_default_context(cafile=ca_path)
    ctx.check_hostname = True
    return ctx


def https_get(ctx, url, timeout=8):
    try:
        with urllib.request.urlopen(urllib.request.Request(url),
                                    timeout=timeout, context=ctx) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        code = getattr(exc, "code", -1)
        return (code if isinstance(code, int) else -1), {
            "error": "%s: %s" % (type(exc).__name__, exc)}


class MiniH(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        body = json.dumps({"ok": True, "service": "spike-mini"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_mini_tls(cert, key):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), MiniH)
    srv.daemon_threads = True
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.2},
                     daemon=True).start()
    return srv, srv.server_address[1]


def fail(msg):
    print("RESULT: FAIL (%s)" % msg)
    return 1


def main():
    print("=== P0 Spike 2: _MEIPASS CA + urllib TLS pinning ===")
    exp_fp = os.environ.get("ETP_SPIKE_FP", "")
    ext_url = os.environ.get("ETP_SPIKE_URL", "")
    mini = None

    in_exe = bool(getattr(sys, "_MEIPASS", None))
    if ext_url:
        # 外部真实 TLS 端点：CA 走定位链（exe=_MEIPASS 打包 CA / 源码=deploy/certs）
        ca = locate_ca()
        fp = ca_fingerprint(ca) if ca else None
        url = ext_url
        print("  l2 target: external %s" % url)
    elif in_exe:
        # exe 自测闭环：用 _MEIPASS 打包证书（打包时配套 SAN=127.0.0.1）
        ca = locate_ca()
        fp = ca_fingerprint(ca) if ca else None
        mini, port = start_mini_tls(locate_cert("server.crt"),
                                    locate_cert("server.key"))
        url = "https://127.0.0.1:%d/api/v1/health" % port
        print("  l2 target: mini-inproc :%d (MEIPASS certs)" % port)
    else:
        # 源码直跑：现场生成 SAN=127.0.0.1 配套 CA（生产 SAN 证书连 127.0.0.1
        # 会被主机名校验正确拒绝，自测须 SAN 配对）
        import tempfile
        _here = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, os.path.normpath(os.path.join(_here, "..", "deploy")))
        from gen_certs import gen_local
        tmp = tempfile.mkdtemp(prefix="eyeterm_spike2_")
        gen_local(tmp, san_dns="localhost", san_ip="127.0.0.1")
        ca = os.path.join(tmp, "ca.crt")
        fp = ca_fingerprint(ca)
        mini, port = start_mini_tls(os.path.join(tmp, "server.crt"),
                                    os.path.join(tmp, "server.key"))
        url = "https://127.0.0.1:%d/api/v1/health" % port
        print("  l2 target: mini-inproc :%d (paired tmp CA)" % port)

    l1 = bool(ca) and (not exp_fp or (fp or "").lower() == exp_fp.lower())
    print("  l1 ca: %s" % (ca or "(missing)"))
    print("  l1 fp: %s (in_MEIPASS=%s)" % (fp or "-",
                                           bool(getattr(sys, "_MEIPASS", None))))
    print("  l1 ok: %s" % l1)
    if not l1:
        return fail("layer1")

    try:
        ctx = make_context(ca, exp_fp or fp)
        st, body = https_get(ctx, url)
        l2 = (st == 200 and body.get("ok") is True)
        print("  l2 status: %s body: %s" % (st, str(body)[:80]))
        print("  l2 ok: %s" % l2)

        # 层3：错指纹必须 fail-closed
        bad_fp = "00" * 32
        try:
            make_context(ca, bad_fp)
            l3 = False
        except ssl.SSLError:
            l3 = True
        print("  l3 rejected-wrong-fp: %s" % l3)
    finally:
        if mini:
            mini.shutdown()
            mini.server_close()

    if l2 and l3:
        print("RESULT: PASS (layer1+2+3)")
        return 0
    return fail("layer2/3")


if __name__ == "__main__":
    sys.exit(main())

