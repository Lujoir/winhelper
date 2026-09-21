# -*- coding: utf-8 -*-
"""
本地 mock EyeTerm 服务端（标准库，127.0.0.1 随机端口，凭据随机不入库）
================================================================
供 tools/e2e_uplink.py 使用：实现 register / heartbeat（可注入 commands）/
metrics / 命令回执 端点，token 鉴权，记录所有请求供断言。

注意（ADR-018）：一切测试载荷禁用真实攻击样式字符串，未知命令类型用
良性探测串（如 unknown_type_probe_x）。
"""

import json
import random
import string
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class MockEtpServer:
    """终端上行 API 的本地 mock（E2E 专用）。"""

    def __init__(self, token=None):
        self.token = token or "e2e-" + "".join(
            random.choice(string.ascii_lowercase + string.digits) for _ in range(12))
        self.seen = []                # [{"method","path","body","token_ok","token"}]
        self.commands_queue = []      # 心跳响应注入的 commands
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # 静默
                pass

            def _record(self, body):
                tok = self.headers.get("X-ETP-Token") or ""
                with outer.lock:
                    outer.seen.append({
                        "method": self.command, "path": self.path,
                        "body": body, "token": tok,
                        "token_ok": (tok == outer.token),
                    })

            def _reply(self, obj, code=200):
                data = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length) if length else b"{}"
                    body = json.loads(raw.decode("utf-8"))
                except Exception:
                    body = {}
                self._record(body)
                p = self.path
                if p.endswith("/register"):
                    if not self.headers.get("X-ETP-Token"):
                        self._reply({"ok": False, "error": "token_missing"}, 401)
                        return
                    self._reply({"ok": True, "registered": True})
                    return
                if "/heartbeat" in p:
                    if not self.headers.get("X-ETP-Token"):
                        self._reply({"ok": False, "error": "token_missing"}, 401)
                        return
                    with outer.lock:
                        cmds = outer.commands_queue[:]
                        outer.commands_queue = []
                    self._reply({"ok": True, "interval": 1, "commands": cmds})
                    return
                if "/commands/" in p and p.endswith("/result"):
                    self._reply({"ok": True, "accepted": True})
                    return
                if p.endswith("/metrics"):
                    self._reply({"ok": True, "accepted": 1, "bottlenecks": []})
                    return
                if p.endswith("/events"):
                    self._reply({"ok": True, "event_id": 1})
                    return
                self._reply({"ok": False, "error": "mock_unknown_path"}, 404)

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.url = "http://127.0.0.1:%d" % self.port
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True, name="mock-etp")

    def start(self):
        self.thread.start()

    def shutdown(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass

    def push_command(self, cmd):
        """注入下次心跳响应携带的命令（单次下发语义）。"""
        with self.lock:
            self.commands_queue.append(cmd)

    def calls(self, path_substr=None):
        with self.lock:
            s = list(self.seen)
        if path_substr:
            s = [c for c in s if path_substr in c["path"]]
        return s

    def results(self):
        """命令回执列表 → [{cid, ok, data}]"""
        out = []
        for c in self.calls("/commands/"):
            p = c["path"]
            cid = p.split("/commands/")[1].split("/result")[0]
            out.append({"cid": cid, "body": c.get("body") or {}})
        return out
