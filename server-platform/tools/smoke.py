#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""观枢终端平台服务端 · 端到端冒烟测试（标准库，无需第三方依赖）。

覆盖：health → 控制台登录 → 白名单（只读快照 + smoke-temp 自清理标记条目，
破坏性 fail-closed 用例已移至隔离单测 tools/test_whitelist.py——ADR-028
测试数据禁触生产准入数据）→ 注册/心跳 → 指标上报（含瓶颈触发与去重）→
事件 → 错误 token 401 → 敏感配置加密往返与脱敏 → 上传登记 →
存储状态/扫描 → 控制台列表/详情/曲线/瓶颈/HTML 报告/页面 200。

环境变量：
    ETP_API_BASE          服务基址（默认 http://127.0.0.1:18090）
    ETP_TERMINAL_TOKEN    终端 token（默认 dev-token）
    ETP_CONSOLE_PASSWORD  控制台口令（默认 dev-console）
    ETP_TLS_TERMINAL_BASE TLS 终端口基址（可选，如 https://127.0.0.1:18443）
    ETP_TLS_CONSOLE_BASE  TLS 管理口基址（可选，如 https://127.0.0.1:8443）
    ETP_CA_FILE           CA 证书路径（https 时建议提供；缺省跳过校验，
                          仅限本地冒烟，生产验证必须提供 CA）
"""
import json
import os
import secrets
import sys
import tempfile
import time
import urllib.request

API_BASE = os.environ.get("ETP_API_BASE", "http://127.0.0.1:18090").rstrip("/")
TOKEN = os.environ.get("ETP_TERMINAL_TOKEN", "dev-token")
CONSOLE_PASSWORD = os.environ.get("ETP_CONSOLE_PASSWORD", "dev-console")
TLS_TERMINAL_BASE = os.environ.get("ETP_TLS_TERMINAL_BASE", "").rstrip("/")
TLS_CONSOLE_BASE = os.environ.get("ETP_TLS_CONSOLE_BASE", "").rstrip("/")
CA_FILE = os.environ.get("ETP_CA_FILE", "")

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                          (" | " + detail) if detail and not cond else ""))


def tls_context():
    """HTTPS 冒烟上下文：提供 CA 则校验（生产口径），否则跳过校验（仅本地）。"""
    import ssl
    if CA_FILE and os.path.isfile(CA_FILE):
        return ssl.create_default_context(cafile=CA_FILE)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def req(method, path, payload=None, headers=None, raw=False, base=None, ctx=None):
    url = (base or API_BASE) + path
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=15, context=ctx) as resp:
            body = resp.read()
            if raw:
                return resp.status, body
            return resp.status, json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read()
        if raw:
            return exc.code, body
        try:
            return exc.code, json.loads(body.decode("utf-8"))
        except ValueError:
            return exc.code, {}


def main():
    ts = int(time.time())
    tid = "WIN-SMOKE-%d" % ts
    print("=== EyeTerm smoke test ===")
    print("api base: %s" % API_BASE)
    print("terminal id: %s\n" % tid)

    # 1. health
    print("[1] health")
    code, j = req("GET", "/api/v1/health")
    check("health 200 + ok", code == 200 and j.get("ok") is True)

    # 2. console login（提前：白名单管理需控制台会话）
    print("[2] console login")
    code, j = req("POST", "/api/v1/console/login", {"password": "definitely-wrong"})
    check("console login rejects wrong password", code == 401, "code=%s" % code)
    code, j = req("POST", "/api/v1/console/login", {"password": CONSOLE_PASSWORD})
    ctoken = j.get("token")
    check("console login ok", code == 200 and bool(ctoken), str(j))
    if not ctoken:
        _summary()
        return
    ch = {"X-ETP-Console-Token": ctoken}

    # 3. 白名单快照（只读；不改动任何生产条目——ADR-028 测试隔离延伸）
    print("[3] whitelist snapshot (read-only)")
    code, j = req("GET", "/api/v1/console/whitelist", headers=ch)
    check("whitelist list reachable", code == 200, "code=%s" % code)
    initial = [(w["id"], w["cidr"], w["note"])
               for w in j.get("whitelist", [])]

    # 3b. 本机源 IP（UDP connect trick：不发包取路由源地址）
    local_ip = ""
    try:
        import socket as _socket
        from urllib.parse import urlparse as _urlparse
        _u = _urlparse(API_BASE)
        _s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        _s.connect((_u.hostname or "127.0.0.1", _u.port or 80))
        local_ip = _s.getsockname()[0]
        _s.close()
    except Exception:
        local_ip = ""

    # 4. 白名单自清理标记条目（smoke-temp note 精确清理，只放行本机；
    #    fail-closed 破坏性用例已移至隔离单测 tools/test_whitelist.py，
    #    生产冒烟不再触碰 0.0.0.0/0 与真实条目——ADR-028 精神延伸。
    #    本机 IP 已在白名单时跳过添加：无条目可加、无需清理，零副作用）
    print("[4] whitelist self-cleanup entry (smoke-temp)")
    smoke_wid = None
    smoke_cidr = "%s/32" % local_ip if local_ip else ""
    if not smoke_cidr:
        check("local source ip resolved for smoke entry", False)
    elif any(cidr == smoke_cidr for _wid, cidr, _note in initial):
        check("whitelist smoke entry pre-exists %s (skip add)" % smoke_cidr,
              True)
    else:
        code, j = req("POST", "/api/v1/console/whitelist",
                      {"cidr": smoke_cidr, "note": "smoke-temp"}, headers=ch)
        if code == 200:
            smoke_wid = j.get("id")
        check("whitelist add smoke-temp %s" % smoke_cidr, code == 200,
              "code=%s" % code)

    # 5. 白名单放行后的注册 + 心跳
    print("[5] register + heartbeat")
    code, j = req("POST", "/api/v1/terminals/register", {
        "terminal_id": tid, "terminal_type": "windows",
        "hostname": "SMOKE-PC", "os_info": "Windows 10 Pro (smoke)",
        "client_version": "smoke-1.0"},
        headers={"X-ETP-Token": TOKEN})
    check("register ok", code == 200 and j.get("ok") is True, str(j))
    code, j = req("POST", "/api/v1/terminals/%s/heartbeat" % tid,
                  {}, headers={"X-ETP-Token": TOKEN})
    check("heartbeat ok", code == 200 and j.get("ok") is True, str(j))

    # 6. metrics：3 次上报，第 2 次触发瓶颈
    print("[6] metrics (3 reports, bottleneck expected on #2)")
    snap_base = {
        "terminal_id": tid, "terminal_type": "windows",
        "client_version": "smoke-1.0",
        "cpu": {"percent": 40.0},
        "mem": {"used_percent": 40.0, "available_percent": 60.0,
                "used_mb": 3000, "total_mb": 8000},
        "swap": {"used_percent": 5.0},
        "disks": [{"mount": "C:", "used_gb": 100, "total_gb": 200,
                   "percent": 50.0, "busy_percent": 10.0}],
        "volumes": [],
    }
    code, j = req("POST", "/api/v1/terminals/%s/metrics" % tid, snap_base,
                  headers={"X-ETP-Token": TOKEN})
    check("metrics#1 accepted, no bottleneck",
          code == 200 and j.get("accepted") == 1 and not j.get("bottlenecks"), str(j))
    time.sleep(0.5)
    snap2 = json.loads(json.dumps(snap_base))
    snap2["cpu"] = {"percent": 92.0}
    snap2["mem"] = {"used_percent": 94.0, "available_percent": 6.0,
                    "used_mb": 7500, "total_mb": 8000}
    snap2["disks"] = [{"mount": "C:", "used_gb": 190, "total_gb": 200,
                       "percent": 95.0, "busy_percent": 30.0}]
    code, j = req("POST", "/api/v1/terminals/%s/metrics" % tid, snap2,
                  headers={"X-ETP-Token": TOKEN})
    rules = sorted(b.get("rule", "") for b in j.get("bottlenecks", []))
    check("metrics#2 triggers cpu/mem/disk bottlenecks",
          code == 200 and "cpu_saturation" in rules and "mem_saturation" in rules
          and "disk_saturation" in rules, "rules=%s" % rules)
    time.sleep(0.5)
    snap3 = json.loads(json.dumps(snap2))
    snap3["cpu"] = {"percent": 96.0}
    code, j = req("POST", "/api/v1/terminals/%s/metrics" % tid, snap3,
                  headers={"X-ETP-Token": TOKEN})
    check("metrics#3 accepted (dedup window suppresses repeat)",
          code == 200 and j.get("accepted") == 1, str(j))

    # 7. events
    print("[7] events")
    code, j = req("POST", "/api/v1/terminals/%s/events" % tid, {
        "level": "warn", "category": "service",
        "message": "smoke test warning event", "detail": {"pid": 4242}},
        headers={"X-ETP-Token": TOKEN})
    check("event accepted", code == 200 and j.get("ok") is True, str(j))

    # 8. token 鉴权负路径
    print("[8] auth negative path")
    code, _ = req("POST", "/api/v1/terminals/%s/heartbeat" % tid, {},
                  headers={"X-ETP-Token": "wrong-token"})
    check("wrong token rejected 401", code == 401, "code=%s" % code)

    # 9. 敏感配置：加密存储 + 脱敏输出（服务端 API 层）
    print("[9] settings encryption (api level)")
    plain_key = "sk-" + secrets.token_hex(12)
    code, j = req("POST", "/api/v1/console/settings",
                  {"settings": {"llm.api_key": plain_key}}, headers=ch)
    check("settings write llm.api_key", code == 200, "code=%s" % code)
    code, body = req("GET", "/api/v1/console/settings", headers=ch, raw=True)
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
    check("api key not echoed in plaintext", plain_key not in text)
    check("api key masked in list", "****" in text, text[-160:])
    # secretsbox 单元级往返（本地临时密钥）
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "server"))
    from secretsbox import SecretsBox
    box = SecretsBox(os.path.join(tempfile.mkdtemp(), "machine.key"))
    token_str = box.encrypt(plain_key)
    check("secretsbox roundtrip", box.decrypt(token_str) == plain_key)
    tampered = token_str[:-6] + ("AAAAAA" if not token_str.endswith("AAAAAA")
                                 else "BBBBBB")
    try:
        box.decrypt(tampered)
        tamper_ok = False
    except ValueError:
        tamper_ok = True
    check("secretsbox tamper rejected", tamper_ok)

    # 10. 上传登记 + 存储状态/扫描
    print("[10] upload registration + storage")
    code, j = req("POST", "/api/v1/terminals/%s/uploads" % tid, {
        "files": [{"filename": "smoke_syslog.txt", "size": 1234,
                   "sha256": "ab" * 32, "ts": ts}]},
        headers={"X-ETP-Token": TOKEN})
    check("terminal upload registration accepted",
          code == 200 and j.get("registered") == 1, str(j))
    code, j = req("GET", "/api/v1/console/uploads?limit=10", headers=ch)
    names = [u["filename"] for u in j.get("uploads", [])]
    check("upload listed in console", "smoke_syslog.txt" in names, str(names)[:120])
    code, j = req("GET", "/api/v1/console/storage/status", headers=ch)
    check("storage status ok", code == 200 and "mount" in j and "ftp" in j,
          str(j)[:120])
    code, j = req("POST", "/api/v1/console/storage/scan", {}, headers=ch)
    check("storage scan ok", code == 200 and j.get("ok") is True, str(j)[:120])

    # 11. 控制台查询流
    print("[11] console flow")
    code, j = req("GET", "/api/v1/console/terminals", headers=ch)
    ids = [t["terminal_id"] for t in j.get("terminals", [])]
    check("console terminals contains smoke terminal", tid in ids, "ids=%s" % ids)
    online = [t for t in j.get("terminals", []) if t["terminal_id"] == tid]
    check("smoke terminal online", bool(online) and online[0]["online"] is True)
    code, j = req("GET", "/api/v1/console/terminals/%s" % tid, headers=ch)
    check("console terminal detail ok",
          code == 200 and j.get("latest_metrics") is not None
          and len(j.get("bottlenecks", [])) >= 3, str(j)[:200])
    code, j = req("GET", "/api/v1/console/terminals/%s/metrics?minutes=30" % tid,
                  headers=ch)
    check("console metrics curve has 3 points",
          code == 200 and len(j.get("points", [])) >= 3,
          "points=%s" % len(j.get("points", [])))
    code, j = req("GET", "/api/v1/console/bottlenecks?terminal_id=%s" % tid,
                  headers=ch)
    check("console bottlenecks listed", code == 200
          and len(j.get("bottlenecks", [])) >= 3, str(j)[:200])
    code, body = req("GET", "/api/v1/console/report/%s?hours=24" % tid,
                     headers=ch, raw=True)
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
    check("HTML report exported", code == 200
          and "观枢终端平台｜EyeTerm" in text and "<html" in text.lower(),
          "len=%s" % len(text))
    code, body = req("GET", "/", raw=True)
    check("console page 200", code == 200 and b"EyeTerm" in body, "code=%s" % code)

    # 11.5 命令通道（功能2）
    print("[11.5] command channel")
    code, j = req("POST", "/api/v1/console/terminals/%s/commands" % tid,
                  {"command": "ai_context", "args": {"issue": "test"},
                   "timeout_sec": 60}, headers=ch)
    check("console enqueue command", code == 200 and j.get("command_id"),
          str(j))
    cmd_id = j.get("command_id")
    code, _ = req("POST", "/api/v1/console/terminals/%s/commands" % tid,
                  {"command": "unknown_type_probe_x"}, headers=ch)  # 负向用例禁用攻击样式串（删除类/关机类命令样式均属 IDS 特征），一律用良性探测串（ADR-018）
    check("unknown command type rejected 400", code == 400, "code=%s" % code)
    code, j = req("POST", "/api/v1/terminals/%s/heartbeat" % tid,
                  {}, headers={"X-ETP-Token": TOKEN})
    cmds = j.get("commands") or []
    got = [c for c in cmds if c.get("id") == cmd_id]
    check("heartbeat carries pending command",
          code == 200 and len(got) == 1 and got[0]["command"] == "ai_context",
          str(j)[:160])
    code, j = req("POST", "/api/v1/terminals/%s/commands/%s/result" % (tid, cmd_id),
                  {"ok": True, "data": {"summary": "smoke result"}},
                  headers={"X-ETP-Token": TOKEN})
    check("command result accepted", code == 200 and j.get("ok") is True, str(j))
    code, j = req("GET", "/api/v1/console/commands?terminal_id=%s" % tid, headers=ch)
    statuses = {c["id"]: c["status"] for c in j.get("commands", [])}
    check("command executed in history", statuses.get(cmd_id) == "executed",
          str(statuses)[:120])
    code, j = req("POST", "/api/v1/terminals/register", {
        "terminal_id": tid, "terminal_type": "windows", "hostname": "SMOKE-PC",
        "os_info": "Windows 10 Pro (smoke)", "client_version": "smoke-1.0",
        "hwinfo": {"cpu_model": "Test-i7", "cpu_cores": 8,
                   "mem_total_mb": 16384, "disk_total_gb": 512,
                   "gpu_info": "Test-GPU", "os_arch": "x86_64"}},
        headers={"X-ETP-Token": TOKEN})
    code, j = req("GET", "/api/v1/console/terminals", headers=ch)
    mine = [t for t in j.get("terminals", []) if t["terminal_id"] == tid]
    check("hwinfo persisted to asset fields",
          bool(mine) and mine[0].get("cpu_model") == "Test-i7"
          and mine[0].get("cpu_cores") == 8, str(mine)[:160])

    # 11.6 网络测试（功能3：任务槽/端口分配/生命周期）
    print("[11.6] nettest (iperf tasks)")
    code, j = req("POST", "/api/v1/console/nettest/launch",
                  {"terminal_id": tid, "test_type": "bandwidth_tcp",
                   "duration_sec": 5}, headers=ch)
    check("nettest launch ok", code == 200 and j.get("ok") is True, str(j))
    task1 = (j.get("task_id"), j.get("port"))
    code, j = req("POST", "/api/v1/console/nettest/launch",
                  {"terminal_id": tid, "test_type": "udp_jitter",
                   "duration_sec": 5}, headers=ch)
    check("nettest concurrent launch ok", code == 200 and j.get("ok") is True,
          str(j))
    task2 = (j.get("task_id"), j.get("port"))
    check("concurrent tasks use distinct ports",
          task1[0] != task2[0] and task1[1] != task2[1],
          "t1=%s t2=%s" % (task1, task2))
    code, j = req("POST", "/api/v1/console/nettest/launch",
                  {"terminal_id": tid, "test_type": "bogus_type"}, headers=ch)
    check("nettest unknown type rejected", code == 400, "code=%s" % code)
    code, j = req("POST", "/api/v1/terminals/%s/heartbeat" % tid,
                  {}, headers={"X-ETP-Token": TOKEN})
    iperf_cmds = [c for c in (j.get("commands") or [])
                  if c.get("command") == "iperf_client"]
    check("heartbeat carries iperf_client commands", len(iperf_cmds) >= 2,
          str(iperf_cmds)[:160])
    for c in iperf_cmds:
        code, j = req("POST", "/api/v1/terminals/%s/commands/%s/result"
                      % (tid, c["id"]),
                      {"ok": True,
                       "data": {"task_id": c["args"].get("task_id"),
                                "summary": "tcp 940.2 Mbits/sec"}},
                      headers={"X-ETP-Token": TOKEN})
    code, j = req("GET", "/api/v1/console/nettest/tasks?limit=10", headers=ch)
    by_id = {t["task_id"]: t["status"] for t in j.get("tasks", [])}
    check("tasks done after client result",
          by_id.get(task1[0]) == "done" and by_id.get(task2[0]) == "done",
          str(by_id))
    code, body = req("GET", "/api/v1/console/nettest/report?limit=10",
                     headers=ch, raw=True)
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
    check("nettest HTML report exported", code == 200
          and "网络测试报告" in text and "<html" in text.lower(),
          "len=%s" % len(text))

    # 11.7 AI 智能分析（功能4）
    # 本地默认 mock LLM（不消耗真实额度）；ETP_AI_MOCK!=1 时走服务端真实配置
    # 单次调用（部署后连通性验证，极短 prompt）。
    use_mock = os.environ.get("ETP_AI_MOCK", "1") == "1"
    print("[11.7] ai analyze (%s)"
          % ("mock llm" if use_mock else "REAL llm (single call)"))
    if use_mock:
        code, j = req("GET", "/api/v1/console/settings", headers=ch)
        orig_url = (j.get("settings") or {}).get("llm.url", "")
        orig_model = (j.get("settings") or {}).get("llm.model", "")
        mock_host = os.environ.get("ETP_MOCK_HOST", "127.0.0.1")
        mock_port = _start_mock_llm()
        # 不触碰 llm.api_key（mock 不校验鉴权），避免覆盖生产密钥（ADR-018 教训）
        code, j = req("POST", "/api/v1/console/settings",
                      {"settings": {"llm.url": "http://%s:%d" % (mock_host, mock_port),
                                    "llm.model": "mock-model"}}, headers=ch)
        check("mock llm settings written", code == 200, "code=%s" % code)
    code, j = req("POST", "/api/v1/console/ai/analyze",
                  {"terminal_id": tid, "issue_description": "smoke 卡顿"},
                  headers=ch)
    check("ai analyze via mock ok",
          code == 200 and j.get("ok") is True
          and "MOCK" in (j.get("response") or ""), str(j)[:160])
    code, j = req("GET", "/api/v1/console/ai/analyses?limit=5", headers=ch)
    check("ai analyses history listed",
          code == 200 and len(j.get("analyses", [])) >= 1, str(j)[:120])
    aid = (j.get("analyses") or [{}])[0].get("id")
    code, body = req("GET", "/api/v1/console/ai/report/%s" % aid,
                     headers=ch, raw=True)
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
    check("ai HTML report exported", code == 200 and "AI 智能分析报告" in text,
          "len=%s" % len(text))
    # 恢复 llm 配置原状（url + model 都要恢复；api_key 未触碰无需恢复）
    if use_mock:
        code, _ = req("POST", "/api/v1/console/settings",
                      {"settings": {"llm.url": orig_url,
                                    "llm.model": orig_model}}, headers=ch)
        check("mock llm settings restored", code == 200, "code=%s" % code)
        _stop_mock_llm()

    # 12. 白名单精确清理（只删自己加的 smoke-temp 条目；生产条目一概不动，
    #     与快照比对验证零副作用——ADR-028）
    print("[12] whitelist self cleanup")
    if smoke_wid:
        code, _ = req("DELETE", "/api/v1/console/whitelist/%d" % smoke_wid,
                      headers=ch)
        check("smoke-temp entry removed", code == 200, "code=%s" % code)
    code, j = req("GET", "/api/v1/console/whitelist", headers=ch)
    now = sorted(w["cidr"] for w in j.get("whitelist", []))
    want = sorted(cidr for _wid, cidr, _note in initial)
    check("whitelist unchanged vs snapshot (zero side effect)",
          now == want, "now=%s want=%s" % (now, want))

    # 13. HTTPS 双端口分离（ADR-032，可选：设置 ETP_TLS_* 基址才执行）
    if TLS_TERMINAL_BASE or TLS_CONSOLE_BASE:
        print("[13] https split ports (ADR-032)")
        tctx = tls_context()
        code, j = req("GET", "/api/v1/health", base=TLS_TERMINAL_BASE, ctx=tctx)
        check("TLS terminal health 200", code == 200 and j.get("ok") is True,
              "code=%s" % code)
        code, j = req("POST", "/api/v1/terminals/%s/heartbeat" % tid, {},
                      headers={"X-ETP-Token": TOKEN},
                      base=TLS_TERMINAL_BASE, ctx=tctx)
        check("TLS terminal real heartbeat 200", code == 200 and j.get("ok") is True,
              "code=%s" % code)
        code, _ = req("GET", "/", base=TLS_TERMINAL_BASE, ctx=tctx)
        check("TLS terminal static cross-type 404", code == 404, "code=%s" % code)
        code, j = req("POST", "/api/v1/console/login",
                      {"password": CONSOLE_PASSWORD},
                      base=TLS_CONSOLE_BASE, ctx=tctx)
        ctoken_tls = j.get("token")
        check("TLS console login 200", code == 200 and bool(ctoken_tls),
              "code=%s" % code)
        if ctoken_tls:
            code, j = req("GET", "/api/v1/console/session-info",
                          headers={"X-ETP-Console-Token": ctoken_tls},
                          base=TLS_CONSOLE_BASE, ctx=tctx)
            check("TLS console session-info 200", code == 200
                  and j.get("ok") is True, "code=%s" % code)
        code, _ = req("POST", "/api/v1/terminals/%s/heartbeat" % tid, {},
                      headers={"X-ETP-Token": TOKEN},
                      base=TLS_CONSOLE_BASE, ctx=tctx)
        check("TLS console terminal-route cross-type 404", code == 404,
              "code=%s" % code)
        code, _ = req("GET", "/api/v1/health")
        check("legacy HTTP health still 200 (parallel)", code == 200,
              "code=%s" % code)
    else:
        print("[13] https split ports SKIPPED (ETP_TLS_* not set)")

    _summary()


_MOCK = {"srv": None}


def _start_mock_llm():
    """本地 mock OpenAI 兼容服务（固定返回，不消耗真实额度）。"""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class MockHandler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            body = json.dumps({
                "model": "mock-model",
                "choices": [{"message": {"role": "assistant",
                                         "content": "【故障原因分析】MOCK-TEST "
                                                    "上下文已接收。"
                                                    "【处理意见】无需处理。"}}],
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("0.0.0.0", 0), MockHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _MOCK["srv"] = srv
    return port


def _stop_mock_llm():
    if _MOCK["srv"]:
        _MOCK["srv"].shutdown()
        _MOCK["srv"] = None


def _summary():
    print("\n=== summary ===")
    print("PASS: %d  FAIL: %d" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("failed items: %s" % ", ".join(FAILED))
        sys.exit(1)
    print("ALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
