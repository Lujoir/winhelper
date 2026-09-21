# -*- coding: utf-8 -*-
"""观枢终端平台服务端 · iperf3 打流任务管理器。

模型（ADR-016）：**每次任务独立任务槽 + 临时端口**起 `iperf3 -s -1 -p <port>`
（-1 = 单测试会话后自动退出）；平台经命令通道向终端下发 iperf_client/net_probe
命令，终端作客户端连接本端口；服务端进程退出后收集 stdout 日志；超时强杀。
并发任务各占独立端口（bind 试探 + running 槽位查重）互不冲突。

测试项：bandwidth_tcp（TCP 带宽）/ udp_jitter（UDP 抖动）/
latency_gateway（终端→网关时延，net_probe）/ latency_server（终端→服务器时延）。

测试支撑：ETP_IPERF_FAKE=1 环境变量使服务端用 sleep 进程模拟（本地冒烟无 iperf3）。
"""
import json
import os
import secrets
import sys
import socket
import subprocess
import threading
import time

TEST_TYPES = ("bandwidth_tcp", "udp_jitter", "latency_gateway", "latency_server")


def _port_free(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


class IperfManager(object):
    """iperf3 服务端任务槽管理。store 提供 iperf_tasks 表访问。"""

    def __init__(self, store, settings):
        self._store = store
        self._settings = settings
        self._procs = {}  # task_id -> Popen
        self._fake = os.environ.get("ETP_IPERF_FAKE") == "1"

    # ---- 端口分配 ----

    def _allocate_port(self):
        start = int(self._settings.get("iperf.port_start") or 18200)
        end = int(self._settings.get("iperf.port_end") or 18299)
        busy = set(row["port"] for row in self._store.iperf_running_ports())
        for port in range(start, end + 1):
            if port in busy:
                continue
            if _port_free(port):
                return port
        return None

    def _server_command(self, port):
        if self._fake:  # 测试支撑：本地冒烟无 iperf3，用 sleep 进程模拟
            return [sys.executable, "-c", "import time; time.sleep(3)"]
        iperf = self._settings.get("iperf.path") or "/usr/bin/iperf3"
        return [iperf, "-s", "-1", "-p", str(port)]

    # ---- 任务生命周期 ----

    def launch(self, terminal_id, test_type, duration_sec=10, source="console"):
        """创建任务：分配端口 → 起 iperf3 -s -1 → 下发命令通道命令。"""
        if test_type not in TEST_TYPES:
            return {"ok": False, "error": "unknown test_type"}
        if not self._store.get_terminal(terminal_id):
            return {"ok": False, "error": "terminal not found"}
        port = self._allocate_port()
        if port is None:
            return {"ok": False, "error": "no free port in range"}
        task_id = "IT-" + secrets.token_hex(5)
        now = int(time.time())
        try:
            proc = subprocess.Popen(self._server_command(port),
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT)
        except OSError as exc:
            self._store.iperf_insert(task_id, terminal_id, test_type, port,
                                     "failed", now, now, {},
                                     "server spawn failed: %s" % exc)
            return {"ok": False, "error": "iperf3 not available: %s" % exc}
        self._procs[task_id] = proc

        # 命令通道下发（ADR-015 协议）
        server_ip = self._settings.get("iperf.server_ip") or ""
        if test_type == "bandwidth_tcp":
            args = {"task_id": task_id, "server_ip": server_ip,
                    "server_port": port, "duration_sec": int(duration_sec),
                    "mode": "tcp"}
            command = "iperf_client"
        elif test_type == "udp_jitter":
            args = {"task_id": task_id, "server_ip": server_ip,
                    "server_port": port, "duration_sec": int(duration_sec),
                    "mode": "udp"}
            command = "iperf_client"
        elif test_type == "latency_gateway":
            args = {"task_id": task_id,
                    "targets": [{"host": "_gateway", "method": "ping"}]}
            command = "net_probe"
        else:  # latency_server
            args = {"task_id": task_id,
                    "targets": [{"host": server_ip or "_gateway",
                                 "method": "ping"}]}
            command = "net_probe"
        timeout = int(duration_sec) + 45
        cid = self._store.enqueue_command(terminal_id, command, args,
                                          timeout_sec=timeout, source="iperf")
        self._store.iperf_insert(task_id, terminal_id, test_type, port,
                                 "running", now, None, {}, "", command_id=cid)

        # 服务端进程收割线程：退出读日志 / 超时强杀
        worker = threading.Thread(target=self._reap, daemon=True,
                                  args=(task_id, proc, timeout))
        worker.start()
        return {"ok": True, "task_id": task_id, "port": port,
                "command_id": cid, "command": command}

    def _reap(self, task_id, proc, timeout_sec):
        """服务端进程收割：正常退出记录日志；超时 kill。"""
        deadline = time.time() + timeout_sec
        try:
            proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                out, _ = proc.communicate(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                out = b""
            self._store.iperf_finish(task_id, "timeout",
                                     _safe_json(out), "server timeout, killed")
            self._procs.pop(task_id, None)
            return
        try:
            out, _ = proc.communicate(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            out = b""
        # 进程退出≠任务完成：客户端结果经命令回执到达。此处仅登记服务端日志。
        self._store.iperf_set_log(task_id, out.decode("utf-8", "replace")[:8000])
        self._procs.pop(task_id, None)

    def spawn_server(self, terminal_id, mode, duration_sec):
        """终端主动压测（net-doctor 功能五）：起单会话 server（-1 自动退出），
        不经命令通道。返回 (task_id, port)；无空闲端口返回 None。"""
        port = self._allocate_port()
        if port is None:
            return None
        task_id = "IT-" + secrets.token_hex(5)
        now = int(time.time())
        try:
            proc = subprocess.Popen(self._server_command(port),
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT)
        except OSError:
            return None
        self._procs[task_id] = proc
        test_type = "stress_tcp" if mode == "tcp" else "stress_udp"
        self._store.iperf_insert(task_id, terminal_id, test_type, port,
                                 "running", now, None, {}, "", command_id=None)
        worker = threading.Thread(target=self._reap, daemon=True,
                                  args=(task_id, proc, int(duration_sec) + 45))
        worker.start()
        return task_id, port

    def complete_from_command(self, command, args, result):
        """命令回执钩子：iperf_client/net_probe 回执关联任务置 done。"""
        task_id = (args or {}).get("task_id")
        if not task_id:
            return
        row = self._store.iperf_get(task_id)
        if not row or row["status"] != "running":
            return
        ok = bool(result.get("ok"))
        status = "done" if ok else "failed"
        self._store.iperf_finish(task_id, status, result.get("data") or {},
                                 "client reported %s" % status)

    def cancel(self, task_id):
        row = self._store.iperf_get(task_id)
        if not row:
            return {"ok": False, "error": "task not found"}
        proc = self._procs.pop(task_id, None)
        if proc:
            try:
                proc.kill()
            except OSError:
                pass
        if row["status"] == "running":
            self._store.iperf_finish(task_id, "failed", {}, "cancelled")
        return {"ok": True}


def _safe_json(raw):
    """iperf3 -s 不输出 JSON（-J 是客户端选项）；原样截断文本。"""
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, TypeError):
        return {"log": raw.decode("utf-8", "replace")[:4000]}
