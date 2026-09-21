# -*- coding: utf-8 -*-
"""
真机注册冒烟：对 EyeTerm 服务端（172.17.5.215:18090）做一次真实
注册（DEV-<hostname>）→ 心跳 → 指标上报，验证平台控制台出现本终端。

凭据纪律（红线）：token 只从环境变量 ETP_TERMINAL_TOKEN 读取，
禁止写入任何文件/日志/输出；脚本运行结束 token 不留存。

运行（PowerShell）：
    $env:ETP_TERMINAL_TOKEN = "<token>"
    python tools/uplink_smoke_real.py
"""

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

API_BASE = os.environ.get("ETP_API_BASE", "http://172.17.5.215:18090").rstrip("/")
TOKEN = os.environ.get("ETP_TERMINAL_TOKEN", "")

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" | " + detail) if detail and not cond else ""))


def post(path, payload):
    url = API_BASE + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-ETP-Token", TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {}
    except Exception as e:
        return -1, {"error": str(e)}


def main():
    if not TOKEN:
        print("缺少环境变量 ETP_TERMINAL_TOKEN（token 不落盘，仅会话内传入）")
        return 2

    import uplink
    hostname = socket.gethostname()
    tid = "DEV-" + hostname          # 冒烟命名：DEV- 前缀（与正式 WIN- 前缀区分）
    print("=== uplink 真机冒烟 ===")
    print("server: %s" % API_BASE)
    print("terminal_id: %s" % tid)

    # 1. 注册（携带 hwinfo 资产）
    cfg = dict(uplink.load_config(), server_url=API_BASE, token=TOKEN,
               terminal_id=tid, heartbeat_interval=30)
    code, resp = post("/api/v1/terminals/register", uplink._register_payload(cfg))
    check("register 200", 200 <= code < 300, "code=%s body=%s" % (code, json.dumps(resp)[:120]))

    # 2. 心跳（确认已注册可领取命令）
    code, resp = post("/api/v1/terminals/%s/heartbeat" % tid, {})
    check("heartbeat 200 + ok", 200 <= code < 300 and resp.get("ok") is True,
          "code=%s body=%s" % (code, json.dumps(resp)[:160]))
    if 200 <= code < 300:
        cmds = resp.get("commands") or []
        print("  pending commands in this heartbeat: %d" % len(cmds))
        # 冒烟终端不执行真实命令（避免干扰平台任务），按未知类型规则回执 ok=false
        for c in cmds:
            rc, rr = post("/api/v1/terminals/%s/commands/%s/result" % (tid, c.get("id")),
                          {"ok": False, "data": {"error": "smoke_terminal_no_exec"}})
            check("command %s 回执" % c.get("id"), 200 <= rc < 300, "code=%s" % rc)

    # 3. 指标上报（真实 snapshot 映射，含温度缓存则附带）
    payload = uplink._metrics_payload(cfg, tid)
    check("metrics payload 组装", payload is not None)
    if payload:
        print("  cpu.percent=%s mem.used_percent=%s disks=%d volumes=%d"
              % (payload["cpu"].get("percent"), payload["mem"].get("used_percent"),
                 len(payload.get("disks") or []), len(payload.get("volumes") or [])))
        code, resp = post("/api/v1/terminals/%s/metrics" % tid, payload)
        check("metrics 200 accepted=1", 200 <= code < 300 and resp.get("accepted") == 1,
              "code=%s body=%s" % (code, json.dumps(resp)[:160]))

    print("=" * 46)
    if FAILED:
        print("冒烟失败：%d 项" % len(FAILED))
        return 1
    print("真机冒烟通过（%d 项）——控制台应已出现终端 %s" % (len(PASSED), tid))
    return 0


if __name__ == "__main__":
    sys.exit(main())
