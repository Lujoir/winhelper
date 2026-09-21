#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""P0 Spike 1（ADR-032 门禁）：同进程双 HTTPS 监听 + legacy HTTP 并行。

验证 ThreadingHTTPServer 多监听装配（TLS 终端口 / TLS 管理口 / HTTP 旧口）
在纯标准库下成立，覆盖批准方案的三个风险点：

  A. TLS1.2 握手：服务端 minimum_version=TLSv1_2，客户端分别以
     max=TLS1.2 与默认（TLS1.3 可协商）握手均 200。
  B. 并发：40 线程 × 每线程独立 TLS 连接（每次新握手）全 200；
     TLS 上 HTTP/1.1 keep-alive 连接复用 5 连发全 200。
  C. 跨类 404（scope 路由分离）：
       terminal 口 GET /（静态）→ 404；terminal 口 console/login → 404；
       console 口 terminals/heartbeat → 404；console 口 terminals 列表 → 404。
  D. legacy HTTP 口全量路由不受影响（health 200）。
  E. 抗错握手：明文 HTTP 客户端打 TLS 端口 → 握手失败，服务端存活、
     后续 TLS 请求仍 200（不 crash、不进入不可恢复状态）。

实现要点（写入 app.py 的结论依据）：
  - 每监听一个 ThreadingHTTPServer 实例，监听 socket 用
    ssl_ctx.wrap_socket(server_side=True) 包裹后 serve_forever；
  - 握手失败（SSLError ⊂ OSError）由 socketserver get_request 异常路径
    吞掉（handle_error 静默），服务不中断（E 组实证）；
  - scope 参数经 Handler 工厂闭包注入（terminal/console/all）。

用法：python tools/spike_dual_https.py   （端口 0 = OS 随机分配，可重复运行）
"""
import hashlib
import http.client
import json
import os
import ssl
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "deploy")))
from gen_certs import gen_local  # noqa: E402

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" | " + str(detail)) if (detail and not cond) else ""))


# ---------------------------------------------------------------- scope dispatch
class ApiError404(Exception):
    pass


def dummy_dispatch(scope, method, path):
    """scope 路由分离哑实现（与 api.dispatch 的 scope gate 同构）。"""
    if path == "/api/v1/health":
        return 200, {"ok": True, "scope": scope}
    if scope == "terminal":
        if path.startswith("/api/v1/terminals/") or path == "/api/v1/ai/analyze":
            return 200, {"ok": True, "scope": scope}
        raise ApiError404()
    if scope == "console":
        if path.startswith("/api/v1/console/"):
            return 200, {"ok": True, "scope": scope}
        if method == "GET" and not path.startswith("/api/"):
            return 200, {"ok": True, "scope": scope, "static": True}
        raise ApiError404()
    return 200, {"ok": True, "scope": "all"}  # legacy 全量


def mk_handler(scope):
    class ScopeHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "EyeTerm"

        def log_message(self, fmt, *args):
            pass

        def _handle(self, method):
            path = self.path.split("?")[0]
            try:
                status, obj = dummy_dispatch(scope, method, path)
            except ApiError404:
                status, obj = 404, {"ok": False, "error": "not found"}
            try:
                body = json.dumps(obj).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                pass

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

    ScopeHandler.scope = scope
    return ScopeHandler


class DualServer(ThreadingHTTPServer):
    """TLS 监听：wrap listening socket；握手失败走 get_request 异常路径。"""

    def __init__(self, addr, handler, ssl_ctx=None):
        ThreadingHTTPServer.__init__(self, addr, handler)
        self.daemon_threads = True
        if ssl_ctx is not None:
            self.socket = ssl_ctx.wrap_socket(self.socket, server_side=True)

    def handle_error(self, request, client_address):
        pass  # 连接级异常（错握手/提前断开）静默，服务不退出


# ---------------------------------------------------------------- 客户端
def client_ctx(ca_path, max_tls=None):
    ctx = ssl.create_default_context(cafile=ca_path)
    ctx.check_hostname = True
    if max_tls is not None:
        ctx.maximum_version = max_tls
    return ctx


def http_req(ctx, url, payload=None, timeout=8):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data,
                                 method="POST" if data else "GET")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {}
    except Exception as exc:
        return -1, {"error": "%s: %s" % (type(exc).__name__, exc)}


# ---------------------------------------------------------------- main
def main():
    print("=== P0 Spike 1: dual HTTPS listen + legacy HTTP ===")
    tmp = tempfile.mkdtemp(prefix="eyeterm_spike_")
    ca_der = gen_local(tmp, san_dns="localhost", san_ip="127.0.0.1")
    ca_path = os.path.join(tmp, "ca.crt")
    fp = hashlib.sha256(ca_der).hexdigest()
    print("spike certs: %s  CA_SHA256=%s..." % (tmp, fp[:16]))

    srv_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    srv_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    srv_ctx.load_cert_chain(os.path.join(tmp, "server.crt"),
                            os.path.join(tmp, "server.key"))

    tls_term = DualServer(("127.0.0.1", 0), mk_handler("terminal"), srv_ctx)
    tls_cons = DualServer(("127.0.0.1", 0), mk_handler("console"), srv_ctx)
    legacy = DualServer(("127.0.0.1", 0), mk_handler("all"), None)
    for s in (tls_term, tls_cons, legacy):
        threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.2},
                         daemon=True).start()
    tport, cport, lport = (tls_term.server_address[1], tls_cons.server_address[1],
                           legacy.server_address[1])
    print("listening: terminal(TLS)=%d console(TLS)=%d legacy(HTTP)=%d\n"
          % (tport, cport, lport))
    time.sleep(0.3)

    ctx_all = client_ctx(ca_path)
    ctx_12 = client_ctx(ca_path, max_tls=ssl.TLSVersion.TLSv1_2)

    # ---- A. TLS1.2 握手 ----
    print("[A] TLS1.2 handshake")
    c1, _ = http_req(ctx_12, "https://127.0.0.1:%d/api/v1/health" % tport)
    check("A1 TLS1.2-max client -> terminal 200", c1 == 200, c1)
    c2, _ = http_req(ctx_12, "https://127.0.0.1:%d/api/v1/health" % cport)
    check("A2 TLS1.2-max client -> console 200", c2 == 200, c2)
    c3, _ = http_req(ctx_all, "https://127.0.0.1:%d/api/v1/health" % tport)
    check("A3 default(TLS1.3-capable) -> terminal 200", c3 == 200, c3)

    # ---- B. 并发 + keep-alive ----
    print("[B] concurrency (fresh TLS handshake per conn)")
    results = []

    def worker():
        st, _ = http_req(ctx_all, "https://127.0.0.1:%d/api/v1/health" % tport)
        results.append(st)

    threads = [threading.Thread(target=worker) for _ in range(40)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    dt = time.time() - t0
    check("B1 40 concurrent fresh-handshake all 200 (%.2fs)" % dt,
          results == [200] * 40, results)

    conn = http.client.HTTPSConnection("127.0.0.1", cport, context=ctx_all,
                                       timeout=8)
    ka_ok = True
    for _ in range(5):
        conn.request("GET", "/api/v1/health")
        r = conn.getresponse()
        r.read()
        ka_ok = ka_ok and r.status == 200
    conn.close()
    check("B2 keep-alive reuse 5x on TLS 200", ka_ok)

    # ---- C. 跨类 404（scope 分离）----
    print("[C] scope cross-type 404")
    c4, _ = http_req(ctx_all, "https://127.0.0.1:%d/" % tport)
    check("C1 terminal port GET / -> 404", c4 == 404, c4)
    c5, _ = http_req(ctx_all, "https://127.0.0.1:%d/api/v1/console/login" % tport,
                     {"password": "x"})
    check("C2 terminal port console/login -> 404", c5 == 404, c5)
    c6, _ = http_req(ctx_all,
                     "https://127.0.0.1:%d/api/v1/terminals/WIN-X/heartbeat" % cport,
                     {"ts": 1})
    check("C3 console port terminals/heartbeat -> 404", c6 == 404, c6)
    c7, _ = http_req(ctx_all, "https://127.0.0.1:%d/api/v1/terminals" % cport)
    check("C4 console port terminals list -> 404", c7 == 404, c7)
    c8, _ = http_req(ctx_all, "https://127.0.0.1:%d/" % cport)
    check("C5 console port static / -> 200", c8 == 200, c8)
    c9, _ = http_req(ctx_all, "https://127.0.0.1:%d/api/v1/health" % tport)
    check("C6 health allowed on both TLS ports", c9 == 200, c9)

    # ---- D. legacy HTTP 全量 ----
    print("[D] legacy HTTP untouched")
    d1, _ = http_req(None, "http://127.0.0.1:%d/api/v1/health" % lport)
    check("D1 legacy health 200", d1 == 200, d1)
    d2, _ = http_req(None, "http://127.0.0.1:%d/" % lport)
    check("D2 legacy static / 200", d2 == 200, d2)

    # ---- E. 抗错握手 ----
    print("[E] wrong-protocol resilience")
    import socket
    s = socket.create_connection(("127.0.0.1", tport), timeout=5)
    s.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
    s.settimeout(3)
    try:
        s.recv(1024)
    except Exception:
        pass
    s.close()
    c10, _ = http_req(ctx_all, "https://127.0.0.1:%d/api/v1/health" % tport)
    check("E1 plaintext probe on TLS port, server alive", c10 == 200, c10)

    for srv in (tls_term, tls_cons, legacy):
        srv.shutdown()
        srv.server_close()

    print("\n=== spike result: %d passed / %d failed ===" % (len(PASSED), len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  FAILED: %s" % f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
