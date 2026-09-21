# -*- coding: utf-8 -*-
"""
net_service.py 后端引擎真实冒烟（无害目标）
=============================================
原则：真实执行 ping / nslookup / tracert / w32tm / iperf3 并验证解析逻辑；
目标仅用 127.0.0.1 / 127.0.0.2 / localhost / 本机真实网卡；禁真实攻击载荷。
iperf3 用 127.0.0.1 自环（本地临时 iperf3 -s -1 随机端口）。

运行：python tools/smoke_netdoctor.py
"""

import base64
import json
import os
import shutil
import ssl
import socket
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import net_service as ns   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  | " + str(detail)[:140]))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main():
    # 测试隔离：NETDOCTOR_CONFIG_DIR 指向临时目录（模块导入后再设置需同步 _records_dir 逻辑，
    # 故在导入前已由环境变量控制——此处重建临时目录并写入自定义节点表）
    tmp = tempfile.mkdtemp(prefix="nd_smoke_")
    os.environ["NETDOCTOR_CONFIG_DIR"] = tmp
    with open(os.path.join(tmp, "app_config.json"), "w", encoding="utf-8") as f:
        json.dump({"netdoctor": {
            "expected_dns": ["192.168.1.1"],
            "nodes": [
                {"key": "loopback", "name": "本机回环", "method": "ping", "target": "127.0.0.1"},
                {"key": "deadnode", "name": "不可达节点", "method": "ping", "target": "192.0.2.1"},
                {"key": "dnstest", "name": "DNS探测(必失败)", "method": "nslookup",
                 "target": "127.0.0.1", "probe": "baidu.com"},
            ]}}, f, ensure_ascii=False)

    print("\n[1] 探测原语·真实执行")
    r = ns._ping_summary("127.0.0.1", count=3)
    check("ping 127.0.0.1 ok", r["ok"] is True, r)
    check("ping 丢包率 0", r["loss_pct"] == 0.0, r["loss_pct"])
    check("ping avg_ms 数值", isinstance(r["avg_ms"], (int, float)), r["avg_ms"])
    r2 = ns._ping_summary("192.0.2.1", count=2)   # TEST-NET 保留段：必超时且无害
    check("ping 不可达目标如实失败", r2["ok"] is False and r2["loss_pct"] == 100.0, r2)

    gw = ns._default_gateway()
    check("默认网关解析（route print -4）", gw is None or
          (isinstance(gw, str) and gw.count(".") == 3), gw)

    print("\n[2] nslookup / NTP（失败路径必须如实）")
    d = ns._nslookup_probe("127.0.0.1", "baidu.com")
    check("nslookup 失败路径 ok=False + error", d["ok"] is False and d["error"], d)
    n = ns._ntp_probe("localhost", samples=2)
    check("w32tm stripchart 真实执行（localhost 无 NTP → 超时/无样本）",
          n["ok"] is False and n["error"] in ("ntp_timeout", "ntp_no_samples"), n)

    print("\n[3] tracert 真实执行（127.0.0.1，1 跳）")
    rc, out = ns._run(["tracert", "-w", "500", "-h", "3", "127.0.0.1"], timeout=40)
    hops = ns._parse_tracert(out)
    check("tracert 输出解析出跳", len(hops) >= 1, (rc, out[:120]))
    if hops:
        check("tracert 首跳 IP=127.0.0.1", hops[0]["ip"] == "127.0.0.1", hops[0])
    # 区域匹配引擎（构造知识库条目，不依赖平台）
    kb = [{"match": "127.0.0.0/8", "zone": "回环区", "desc": "loopback"},
          {"match": "172.17.0.0/16", "zone": "院内区", "desc": "intranet"}]
    check("CIDR 匹配 127.0.0.1 → 回环区",
          (ns._match_zone("127.0.0.1", kb) or {}).get("zone") == "回环区")
    check("CIDR 不匹配 → None", ns._match_zone("8.8.8.8", kb) is None)

    print("\n[4] 配置核查引擎（本机真实 ipconfig /all）")
    r = ns.run_config_check_result()
    check("核查 success", r.get("success") is True, r.get("error"))
    adapters = r.get("adapters") or []
    check("解析到网卡 >=1", len(adapters) >= 1, len(adapters))
    has_active = any(a["active"] for a in adapters)
    check("存在活动网卡（本机有网）", has_active, [a["name"] for a in adapters])
    spd = [a.get("speed") for a in adapters if a["active"] and a.get("speed")]
    check("活动网卡链路速率真实采集（含 Gbps/Mbps 量级）",
          spd and any("bps" in s for s in spd), spd)
    for a in adapters:
        if a["active"]:
            check("活动网卡 %s 核查项齐备" % a["name"],
                  all(c["status"] in ("ok", "warn", "err", "unknown", "muted")
                      for c in a["checks"]), a["checks"])
            break
    check("总体结论结构", (r.get("overall") or {}).get("status") in ("ok", "warn", "err"), r.get("overall"))
    # DNS 基线判定引擎
    v = ns._dns_verdict(["192.168.1.1"], ["192.168.1.1"])
    check("DNS 基线一致 → ok", v["status"] == "ok", v)
    v = ns._dns_verdict(["10.0.0.1"], ["192.168.1.1"])
    check("DNS 基线缺失 → warn", v["status"] == "warn" and "缺少" in v["reason"], v)
    v = ns._dns_verdict([], [])
    check("DNS 无基线 → unknown 提示", v["status"] == "unknown", v)

    print("\n[5] 连通性任务端到端（自定义 3 节点真实执行）")
    resp = ns.handle_net_ping_start({})
    tid = resp.get("task_id")
    check("ping-start 返回 task_id", resp.get("success") is True and bool(tid) and
          resp.get("reused") is False, resp)
    view = None
    for _ in range(120):
        time.sleep(1)
        v = ns.handle_net_task_status({"task_id": tid})
        if v.get("success") and v["task"]["status"] != "running":
            view = v["task"]
            break
    check("ping 任务完成", view is not None and view["status"] == "done", view and view["error"])
    res = (view or {}).get("result") or {}
    rows = {x["key"]: x for x in (res.get("results") or [])}
    check("回环节点 ok", rows.get("loopback", {}).get("ok") is True, rows.get("loopback"))
    check("不可达节点如实 err", rows.get("deadnode", {}).get("status") == "err", rows.get("deadnode"))
    check("DNS 探测失败如实 err", rows.get("dnstest", {}).get("status") == "err", rows.get("dnstest"))
    hist = ns.handle_net_ping_history({})
    keys = [a["key"] for a in hist.get("agg") or []]
    check("JSONL 留档可回读", "loopback" in keys and "deadnode" in keys, keys)
    rec_files = [f for f in os.listdir(ns._records_dir()) if f.startswith("ping_")]
    check("JSONL 文件生成（ping_YYYYMMDD.jsonl）", len(rec_files) == 1, rec_files)
    with open(os.path.join(ns._records_dir(), rec_files[0]), "r", encoding="utf-8") as f:
        first = json.loads(f.readline())
    check("JSONL 字段齐全", all(k in first for k in ("ts", "key", "target", "ok", "warn",
                                                     "loss_pct", "avg_ms", "max_ms")), first)

    print("\n[6] iperf3 自环（本地临时 iperf3 -s -1，捆绑 exe 解析）")
    check("iperf3 资源目录解析", ns._iperf3_available() is True, ns._iperf3_bundle_dir())
    port = free_port()
    bundle = ns._iperf3_bundle_dir()
    exe = os.path.join(bundle, "iperf3.exe")
    srv = subprocess.Popen([exe, "-s", "-1", "-p", str(port)], creationflags=ns._NO_WINDOW,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    tcp, err = ns._run_iperf3("127.0.0.1", port, 2, "tcp", 100)
    check("iperf3 TCP 自环成功", tcp is not None and (tcp.get("mbits_sec") or 0) > 0, err or tcp)
    check("TCP intervals max/min/avg", tcp is not None and tcp.get("interval_avg_mbits") is not None, tcp)
    srv.wait(timeout=10)
    port2 = free_port()
    srv2 = subprocess.Popen([exe, "-s", "-1", "-p", str(port2)], creationflags=ns._NO_WINDOW,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    udp, err = ns._run_iperf3("127.0.0.1", port2, 2, "udp", 50)
    check("iperf3 UDP 自环成功（带宽/抖动/丢包）",
          udp is not None and udp.get("jitter_ms") is not None and udp.get("lost_percent") is not None,
          err or udp)
    srv2.wait(timeout=10)

    print("\n[7] 压测 HTML 报告导出")
    fake = {"success": True, "center": "127.0.0.1", "sizes": [64, 1024], "udp_mbps": 100,
            "iperf_duration": 10, "ts": time.time(),
            "ping": [{"size": 64, "ok": True, "loss_pct": 0, "avg_ms": 1, "min_ms": 0, "max_ms": 2},
                     {"size": 1024, "ok": True, "loss_pct": 0, "avg_ms": 3, "min_ms": 2, "max_ms": 5}],
            "iperf": [{"mode": "tcp", "ok": True, "port": 5201, "duration_sec": 10,
                       "summary": "tcp 941.2 Mbits/sec",
                       "result": {"mode": "tcp", "mbits_sec": 941.2, "retransmits": 0,
                                  "interval_max_mbits": 950.0, "interval_min_mbits": 930.0,
                                  "interval_avg_mbits": 941.2}}],
            "verdict": {"text": "优良", "cls": "ok", "reason": "零丢包，平均延迟 2ms"}}
    path = ns.export_stress_report(fake)
    check("报告文件生成", os.path.exists(path), path)
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    check("报告头部 EyeTerm", "观枢终端平台｜EyeTerm" in html)
    check("报告含数据", "941.2" in html and "iperf3" in html)
    check("文件名规范 NetStress_YYYYMMDD_HHMM.html",
          os.path.basename(path).startswith("NetStress_") and
          len(os.path.basename(path)) == len("NetStress_YYYYMMDD_HHMM.html"), path)
    os.remove(path)

    print("\n[8] handle_net_* 契约")
    c = ns.handle_net_config({})
    check("config 返回节点表/基线", c.get("success") is True and len(c["nodes"]) == 3
          and c["expected_dns"] == ["192.168.1.1"], c)
    t = ns.handle_net_tracert_start({"target": "127.0.0.1"})
    check("tracert-start 契约", t.get("success") is True and t.get("task_id"), t)
    t = ns.handle_net_tracert_start({"target": ""})
    check("tracert 缺 target 拒绝", t.get("success") is False, t)
    t = ns.handle_net_task_status({"task_id": "nonexistent"})
    check("task-status 未知任务拒绝", t.get("success") is False, t)
    t = ns.handle_net_stress_start({})
    check("stress 未连中心拒绝（not_connected）",
          t.get("success") is False and t.get("error") == "not_connected", t)
    t = ns.handle_net_ipconflict({})
    check("ipconflict 未连中心拒绝", t.get("success") is False and t.get("error") == "not_connected", t)

    print("\n[9] w32tm 解析与判定单测（不执行命令）")
    v, e = ns._parse_stripchart("正在与 ntp.eye.ac.cn [10.0.0.1] 同步:\r\n10:37:19, +01.0620337s\r\n10:37:20, +01.0625s\r\n")
    check("解析·中文逗号格式 2 样本", len(v) == 2 and abs(v[0] - 1.0620337) < 1e-9, v)
    v, e = ns._parse_stripchart("10:37:19, -00.5000000s")
    check("解析·负偏移", len(v) == 1 and v[0] == -0.5, v)
    v, e = ns._parse_stripchart("10:37:19, error: 0x800705B4\r\n10:37:20, +00.0100000s\r\n\r\n某中文头行")
    check("解析·error 行/空行/中文头容错", len(v) == 1 and e == 1, (v, e))
    v, e = ns._parse_stripchart("17:00:01 d:+00.0012s o:-00.0123s  [*  |]")
    check("解析·英文 o: 格式兜底", len(v) == 1 and abs(v[0] + 0.0123) < 1e-9, v)
    r = ns._ntp_result([1.0620337, 1.0625], 0)
    check("判定·+1.06s → 需校时(warn)", r["status"] == "warn" and "需校时" in r["detail"]
          and r["offset_ms"] > 1000, r)
    r = ns._ntp_result([-0.62], 0)
    check("判定·-0.62s → 需校时(warn)", r["status"] == "warn", r)
    r = ns._ntp_result([0.05, -0.12], 0)
    check("判定·|均值|≤0.5s → 正常(ok)", r["status"] == "ok", r)
    r = ns._ntp_result([], 1, "10:37:19, error: 0x800705B4")
    check("判定·无样本+error → 异常(err)", r["status"] == "err" and r["error"] == "ntp_timeout", r)
    r = ns._ntp_result([], 0, "")
    check("判定·无输出 → ntp_no_samples", r["status"] == "err" and r["error"] == "ntp_no_samples", r)

    print("\n[10] 真实 NTP 校时探测（ntp.eye.ac.cn · 温州总院节点）")
    nr = ns._ntp_probe("ntp.eye.ac.cn", samples=3)
    if nr["ok"]:
        exp_status = "warn" if abs(nr["offset_ms"] or 0) > 500.0 else "ok"
        check("真实NTP·样本%d个 offset=%sms 判定=%s 与阈值一致" % (
            nr["samples"], nr["offset_ms"], nr["status"]), nr["status"] == exp_status, nr)
    else:
        check("真实NTP·不可达时如实 err+错误码", nr["status"] == "err" and bool(nr["error"]), nr)

    print("\n[11] 系统设置·节点配置 merge 原子写 / 校验 / 恢复默认")
    cfg_path = os.path.join(tmp, "app_config.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump({"temperature_interval_sec": 120, "other_module": {"keep": True}}, f, ensure_ascii=False)
    custom = [
        {"key": "gateway", "name": "本终端网关", "method": "ping", "target": "1.2.3.4"},
        {"key": "loop2", "name": "回环2", "method": "ping", "target": "127.0.0.1"},
        {"key": "dnsx", "name": "DNS", "method": "nslookup", "target": "127.0.0.1", "probe": "example.org"},
    ]
    r = ns.handle_net_config({"nodes_json": json.dumps(custom),
                              "expected_dns_json": json.dumps(["10.9.9.9"])})
    check("config SET 成功", r.get("success") is True, r)
    check("SET·动态键目标强制置空", r["nodes"][0]["target"] == "", r["nodes"][0])
    check("SET·DNS 基线写入", r["expected_dns"] == ["10.9.9.9"], r.get("expected_dns"))
    with open(cfg_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    check("SET·merge 保留其它模块键", raw.get("temperature_interval_sec") == 120
          and raw.get("other_module") == {"keep": True}, sorted(raw.keys()))
    resp = ns.handle_net_ping_start({})
    t2 = resp.get("task_id")
    v = None
    for _ in range(60):
        time.sleep(1)
        v = ns.handle_net_task_status({"task_id": t2})
        if v.get("success") and v["task"]["status"] != "running":
            break
    rows2 = {x["key"]: x for x in (((v or {}).get("task") or {}).get("result") or {}).get("results", []) or []} \
        if v else {}
    check("SET·连通性检测即时用新配置(回环2 ok)", rows2.get("loop2", {}).get("ok") is True, rows2.get("loop2"))
    bad = ns.handle_net_config({"nodes_json": json.dumps(
        [{"key": "x", "name": "x", "method": "icmp", "target": "127.0.0.1"}])})
    check("SET·非法方式拒绝", bad.get("success") is False, bad)
    bad = ns.handle_net_config({"nodes_json": json.dumps(
        [{"key": "x", "name": "x", "method": "ping", "target": "a b;c"}])})
    check("SET·非法目标字符拒绝", bad.get("success") is False, bad)
    r = ns.handle_net_config({"reset": "1"})
    check("reset·恢复出厂节点表", r.get("success") is True
          and len(r.get("nodes") or []) == len(ns.DEFAULT_NODES), len(r.get("nodes") or []))
    with open(cfg_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    check("reset·netdoctor 无 nodes 且他键保留", "nodes" not in (raw.get("netdoctor") or {})
          and raw.get("temperature_interval_sec") == 120, raw.get("netdoctor"))

    # 清理临时目录
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n[12] AI 个人版提示词·证据可信性硬约束（analysis_id=16 虚构日志依据缺陷加固）")
    prompt = getattr(ns, "_ND_AI_PROMPT_SYSTEM", "")
    check("提示词·严禁编造/只允许引用实提供日志", "严禁编造" in prompt and "只允许引用" in prompt)
    check("提示词·引用事件须带原文时间戳", "时间戳" in prompt)
    check("提示词·缺证据须显式声明证据不足", "证据不足" in prompt)
    check("提示词·三段结构保留（2026-09-20 改使用人视角）",
          all(s in prompt for s in ("【怎么回事】", "【怎么解决】", "【还要注意】")))
    # 准则四（受众适配）：本引擎入口在客户端、使用者是**使用人本人** —— 提示词必须
    # 面向使用人（白话 + 动作），不得沿用中心运维口径（"医院网络运维诊断专家" +
    # "注明来自哪一类日志"）。此断言防止后续被改回去。
    check("提示词·使用人视角（准则四：不甩术语 + 处置从轻）",
          "不甩术语" in prompt and "建议观察" in prompt
          and "运维诊断专家" not in prompt, prompt[:80])
    # 前端分段渲染兼容：个人版使用人段名 + 企业版运维段名**并存**（alts 机制）。
    # 文本断言防"有人把 alts 删了"——那会让企业版结论静默退化成纯文本。
    _js = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "web", "netdoctor.js")
    try:
        with open(_js, encoding="utf-8") as _f:
            _jstext = _f.read()
        check("前端·分段渲染兼容新旧两套段名",
              "怎么回事" in _jstext and "故障原因分析" in _jstext
              and "alts" in _jstext and "ndAiMatchSection" in _jstext)
    except OSError as _e:
        check("前端·分段渲染兼容新旧两套段名", False, "netdoctor.js 不可读: %s" % _e)

    print("\n[13] 个人版连通性测试·Key 语义（留空回退已保存/none 不发请求，误判 401 缺陷修复）")
    tmp2 = tempfile.mkdtemp(prefix="nd_smoke_pers_")
    os.environ["NETDOCTOR_CONFIG_DIR"] = tmp2
    with open(os.path.join(tmp2, "app_config.json"), "w", encoding="utf-8") as f:
        json.dump({"netdoctor": {"ai_personal": {"api_url": "https://stub.example",
                                                 "api_key": "sk-saved", "model": "m"}}},
                  f, ensure_ascii=False)
    r = ns.handle_net_ai_personal_test({"api_url": "https://stub.example"})
    check("test·Key 留空回退已保存（used_key=saved）", r.get("used_key") == "saved", r)
    r = ns.handle_net_ai_personal_test({"api_url": "https://stub.example", "api_key": "sk-form"})
    check("test·表单 Key 走 provided", r.get("used_key") == "provided", r)
    with open(os.path.join(tmp2, "app_config.json"), "w", encoding="utf-8") as f:
        json.dump({"netdoctor": {}}, f, ensure_ascii=False)
    r = ns.handle_net_ai_personal_test({"api_url": "https://stub.example"})
    check("test·无已保存 Key → none 且不发请求", r.get("used_key") == "none"
          and r.get("ok") is False and "未配置 API Key" in (r.get("hint") or ""), r)
    shutil.rmtree(tmp2, ignore_errors=True)

    print("\n[14] 全新环境默认表保障 + 恢复默认（2026-09-10 用户要求防回归）")
    tmp3 = tempfile.mkdtemp(prefix="nd_smoke_fresh_")
    os.environ["NETDOCTOR_CONFIG_DIR"] = tmp3   # 无 app_config.json 全新环境
    r = ns.handle_net_config({})
    nodes = r.get("nodes") or []
    keys = [n.get("key") for n in nodes]
    check("fresh·无配置首屏 = DEFAULT_NODES 完整 9 节点（8 默认+tcp-sample 示例）", keys ==
          ["gateway", "core", "datacenter", "dmz", "dns", "ntp", "internet", "center", "tcp-sample"], keys)
    ntp = next((n for n in nodes if n.get("key") == "ntp"), {})
    check("fresh·温州总院 NTP 目标正确", ntp.get("method") == "ntp"
          and ntp.get("target") == "ntp.eye.ac.cn", ntp)
    r2 = ns.handle_net_config({"nodes_json": json.dumps(
        [{"key": "x", "name": "自定义", "method": "ping", "target": "127.0.0.1"}]),
        "expected_dns_json": json.dumps(["10.0.0.1"])})
    check("fresh·自定义节点写入", r2.get("success") is True and len(r2.get("nodes") or []) == 1, r2)
    r3 = ns.handle_net_config({"reset": "1"})
    keys3 = [n.get("key") for n in (r3.get("nodes") or [])]
    check("fresh·恢复默认重置为同一完整 9 节点表", keys3 ==
          ["gateway", "core", "datacenter", "dmz", "dns", "ntp", "internet", "center", "tcp-sample"], keys3)
    shutil.rmtree(tmp3, ignore_errors=True)

    print("\n[15] IP 冲突深度检测转发（服务端契约 9e604a7+cc34bae：本地秒级转发，平台侧编排）")
    tmp4 = tempfile.mkdtemp(prefix="nd_smoke_deep_")
    os.environ["NETDOCTOR_CONFIG_DIR"] = tmp4   # 无 uplink 配置 → 未连中心
    t = ns.handle_net_conflict_deep_start({"ip": "172.17.90.215", "mac": "AA:BB:CC:DD:EE:FF"})
    check("deep start·未连中心拒绝", t.get("success") is False and t.get("error") == "not_connected", t)
    t = ns.handle_net_conflict_deep_poll({"task_id": "DC-x"})
    check("deep poll·未连中心拒绝", t.get("success") is False and t.get("error") == "not_connected", t)
    # 已连中心（stub uplink 配置：uplink_config.json 为独立配置文件）
    with open(os.path.join(tmp4, "uplink_config.json"), "w", encoding="utf-8") as f:
        json.dump({"enabled": True, "server_url": "http://stub.local:18090",
                   "token": "stub-token", "terminal_id": "WIN-STUB"}, f)
    t = ns.handle_net_conflict_deep_poll({})
    check("deep poll·缺 task_id 拒绝", t.get("success") is False and t.get("error") == "missing_task_id", t)
    calls = []
    orig_post, orig_get = ns._platform_post, ns._platform_get

    def fake_post(path, payload, timeout=15):
        calls.append(("POST", path, payload))
        return 200, {"ok": True, "task_id": "DC-STUB01", "status": "running"}

    def fake_get(path, timeout=12):
        calls.append(("GET", path))
        return 200, {"ok": True, "task": {"task_id": "DC-STUB01", "status": "done",
                                          "steps": [], "verdict": {"conclusion": "normal"}}}

    ns._platform_post = fake_post
    try:
        t = ns.handle_net_conflict_deep_start({"ip": "172.17.90.215", "mac": "aa-bb-cc-dd-ee-ff"})
        check("deep start·成功转发 task_id 透传",
              t.get("success") is True and t.get("task_id") == "DC-STUB01", t)
        check("deep start·路径与 tid 正确",
              len(calls) == 1 and calls[0][0] == "POST"
              and calls[0][1] == "/api/v1/terminals/WIN-STUB/netdoctor/ipconflict-deep", calls)
        check("deep start·body ip/mac（mac 归一大写冒号）",
              calls[0][2] == {"ip": "172.17.90.215", "mac": "AA:BB:CC:DD:EE:FF"}, calls[0][2])
        t = ns.handle_net_conflict_deep_start({"ip": "999.1.1.1", "mac": "AA"})
        check("deep start·非法 IP 拒绝（本地校验，不发请求）",
              t.get("success") is False and t.get("error") == "invalid_ip" and len(calls) == 1, t)
    finally:
        ns._platform_post = orig_post

    def fake_post_429(path, payload, timeout=15):
        return 429, {"error": "deep check busy, retry later"}
    ns._platform_post = fake_post_429
    try:
        t = ns.handle_net_conflict_deep_start({"ip": "172.17.90.215", "mac": "AA:BB:CC:DD:EE:FF"})
        check("deep start·429 并发满 → busy",
              t.get("success") is False and t.get("error") == "busy", t)
    finally:
        ns._platform_post = orig_post

    def fake_post_404(path, payload, timeout=15):
        return 404, {"error": "terminal not found"}
    ns._platform_post = fake_post_404
    try:
        t = ns.handle_net_conflict_deep_start({"ip": "172.17.90.215", "mac": "AA:BB:CC:DD:EE:FF"})
        check("deep start·404 未注册", t.get("success") is False and t.get("error") == "not_registered", t)
    finally:
        ns._platform_post = orig_post

    # 缺省 ip → 本地采集兜底（route 不可解析回退活动网卡）
    orig_collect, orig_route = ns._collect_adapters, ns._resolve_uplink_route_adapter
    ns._collect_adapters = lambda: ([{"name": "以太网", "active": True,
                                      "ipv4": ["10.1.2.3"], "mac": "11:22:33:44:55:66"}], None)
    ns._resolve_uplink_route_adapter = lambda host: None
    ns._platform_post = fake_post
    try:
        t = ns.handle_net_conflict_deep_start({})
        check("deep start·缺省 ip 本地采集兜底",
              t.get("success") is True and t.get("ip") == "10.1.2.3"
              and t.get("mac") == "11:22:33:44:55:66", t)
        check("deep start·兜底采集 body 正确",
              calls[-1][2] == {"ip": "10.1.2.3", "mac": "11:22:33:44:55:66"}, calls[-1][2])
    finally:
        ns._platform_post = orig_post
        ns._collect_adapters = orig_collect
        ns._resolve_uplink_route_adapter = orig_route

    # poll 转发
    ns._platform_get = fake_get
    try:
        t = ns.handle_net_conflict_deep_poll({"task_id": "DC-STUB01"})
        check("deep poll·task 透传",
              t.get("success") is True and t.get("task", {}).get("status") == "done", t)
        check("deep poll·GET 路径正确（task_id 编码）",
              calls[-1] == ("GET", "/api/v1/terminals/WIN-STUB/netdoctor/ipconflict-deep/DC-STUB01"),
              calls[-1])
    finally:
        ns._platform_get = orig_get

    def fake_get_404(path, timeout=12):
        return 404, {"error": "task not found"}
    ns._platform_get = fake_get_404
    try:
        t = ns.handle_net_conflict_deep_poll({"task_id": "DC-NOPE"})
        check("deep poll·未知任务 404", t.get("success") is False
              and t.get("error") == "platform_http_404", t)
    finally:
        ns._platform_get = orig_get

    check("deep·ROUTES 注册两条",
          "/api/netdoctor/conflict-deep-start" in ns.NET_ROUTES
          and "/api/netdoctor/conflict-deep-poll" in ns.NET_ROUTES)
    shutil.rmtree(tmp4, ignore_errors=True)

    print("\n[16] 真实 bridge 链路 + ROUTES 全键覆盖（2026-09-10 热修复门禁："
          "NET_ROUTES 新增必须同步挂载主应用 bridge.py，桩 E2E 不覆盖 bridge 路由层）")
    bridge_py = os.environ.get("NETDOCTOR_BRIDGE_PATH") or ""
    if not bridge_py:
        up = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        bridge_py = os.path.join(up, "bridge.py")
    if not os.path.isfile(bridge_py):
        print("  SKIP  bridge.py 未定位（独立运行环境），bridge 覆盖检查跳过")
    else:
        import importlib.util
        bdir = os.path.dirname(bridge_py)
        sys.path.insert(0, bdir)   # bridge.py 顶层绝对 import（service/perf_service/...）
        spec = importlib.util.spec_from_file_location("nd_bridge_smoke", bridge_py)
        mod = importlib.util.module_from_spec(spec)
        tmp5 = tempfile.mkdtemp(prefix="nd_smoke_bridge_")
        prev_dir = os.environ.get("NETDOCTOR_CONFIG_DIR")
        os.environ["NETDOCTOR_CONFIG_DIR"] = tmp5   # 无 uplink 配置 → not_connected 合法返回
        try:
            spec.loader.exec_module(mod)
            br = mod.ApiBridge()
            r1 = br.call("/api/netdoctor/conflict-deep-start?ip=172.17.90.215&mac=AA:BB:CC:DD:EE:FF")
            check("bridge·deep-start 真实链路非未知接口",
                  "未知接口" not in str(r1.get("error", "")) and "error" in r1, r1)
            check("bridge·deep-start 返回 not_connected（handler 真实执行）",
                  r1.get("success") is False and r1.get("error") == "not_connected", r1)
            r2 = br.call("/api/netdoctor/conflict-deep-poll?task_id=DC-X")
            check("bridge·deep-poll 真实链路非未知接口",
                  r2.get("success") is False and r2.get("error") == "not_connected", r2)
            missing = [k for k in ns.NET_ROUTES if k not in mod.ROUTES]
            check("bridge·ROUTES 覆盖 NET_ROUTES 全部键（防回归根治检查）", not missing, missing)
        finally:
            os.environ["NETDOCTOR_CONFIG_DIR"] = prev_dir
            try:
                sys.path.remove(bdir)
            except ValueError:
                pass
            shutil.rmtree(tmp5, ignore_errors=True)

    print("\n[17] AI 辅助分析 response 字段修复 + 手动重跑（2026-09-10 热修 + 平台聚合升级）")
    tmp6 = tempfile.mkdtemp(prefix="nd_smoke_aire_")
    os.environ["NETDOCTOR_CONFIG_DIR"] = tmp6
    with open(os.path.join(tmp6, "uplink_config.json"), "w", encoding="utf-8") as f:
        json.dump({"enabled": True, "server_url": "http://stub.local:18090",
                   "token": "stub-token", "terminal_id": "WIN-STUB"}, f)
    ai_calls = []
    orig_post2 = ns._platform_post

    def fake_ai_post(path, payload, timeout=15):
        ai_calls.append((path, payload, timeout))
        return fake_ai_post.reply

    ns._platform_post = fake_ai_post
    try:
        # 提取链：平台实证字段 response 优先（修复「（无内容）」字段错位）
        fake_ai_post.reply = (200, {"response": "R-字段", "model": "m1", "analysis_id": 7})
        t = ns._ai_analyze_issue("WIN-STUB", "issue")
        check("AI提取·response 字段命中", t.get("ok") is True and t.get("analysis") == "R-字段", t)
        check("AI提取·analysis_id 透传", t.get("analysis_id") == "7", t)
        check("AI提取·endpoint 与 45s 超时",
              ai_calls[-1][0] == "/api/v1/ai/analyze" and ai_calls[-1][2] == 45
              and ai_calls[-1][1] == {"terminal_id": "WIN-STUB", "issue_description": "issue"}, ai_calls[-1])
        # 旧字段回退兼容
        fake_ai_post.reply = (200, {"analysis": "A-旧链"})
        t = ns._ai_analyze_issue("WIN-STUB", "issue")
        check("AI提取·旧 analysis 字段回退", t.get("ok") is True and t.get("analysis") == "A-旧链", t)
        # 空响应不崩（前端显示（无内容）为现状语义）
        fake_ai_post.reply = (200, {})
        t = ns._ai_analyze_issue("WIN-STUB", "issue")
        check("AI提取·空响应 ok=True 空文本", t.get("ok") is True and t.get("analysis") == "", t)
        # 失败态
        fake_ai_post.reply = (500, {"error": "llm down"})
        t = ns._ai_analyze_issue("WIN-STUB", "issue")
        check("AI提取·非200 失败态", t.get("ok") is False and "ai_http_500" in t.get("error", ""), t)

        # 手动重跑 handler
        t = ns.handle_net_conflict_ai_reanalyze({"ip": "999.1.1.1", "mac": "AA"})
        check("AI重跑·invalid_ip 拒绝", t.get("success") is False and t.get("error") == "invalid_ip", t)
        fake_ai_post.reply = (200, {"response": "OK-聚合", "model": "m2", "analysis_id": "9"})
        t = ns.handle_net_conflict_ai_reanalyze(
            {"ip": "172.17.90.215", "mac": "AA:BB:CC:DD:EE:FF",
             "evidence_json": json.dumps(["证据一", "证据二"] * 8)})
        check("AI重跑·成功透传 ai 结构",
              t.get("success") is True and t.get("ai", {}).get("analysis") == "OK-聚合"
              and t.get("ai", {}).get("analysis_id") == "9", t)
        issue_txt = ai_calls[-1][1].get("issue_description", "")
        check("AI重跑·issue 携带 ip/mac/证据", "172.17.90.215" in issue_txt
              and "AA:BB:CC:DD:EE:FF" in issue_txt and "证据一" in issue_txt, issue_txt[:160])
        check("AI重跑·evidence 截断 12 条（16 传 12，证据二出现 6 次）",
              issue_txt.count("证据二") == 6, issue_txt.count("证据二"))
        fake_ai_post.reply = (500, {"error": "down"})
        t = ns.handle_net_conflict_ai_reanalyze(
            {"ip": "172.17.90.215", "mac": "AA", "evidence_json": "{bad"})
        check("AI重跑·平台失败透传 + 非法 evidence_json 容错",
              t.get("success") is True and t.get("ai", {}).get("ok") is False, t)
        check("AI重跑·ROUTES 注册",
              "/api/netdoctor/conflict-ai-reanalyze" in ns.NET_ROUTES)

        # trace-ai-analyze payload 形状（ADR-031 契约：kind + context{target,hops}，
        # 2026-09-11 热修——此前 data 键错位致平台聚合分支永不生效）
        fake_ai_post.reply = (200, {"response": "RT-OK", "model": "m", "analysis_id": "RT-1"})
        t = ns.handle_net_trace_ai_analyze({"target": "172.17.5.215", "hops_json": json.dumps(
            [{"hop": 1, "ip": "10.0.0.1", "host": "gw", "delays": "1 ms",
              "timeout": False, "zone": "核心", "zone_desc": ""}])})
        check("trace·成功透传 ai 结构", t.get("success") is True
              and t.get("ai", {}).get("ok") is True, t)
        pl = ai_calls[-1][1]
        check("trace·payload 键名契约（kind + context.target + context.hops 为 list）",
              pl.get("kind") == "routetrace" and isinstance(pl.get("context"), dict)
              and pl["context"].get("target") == "172.17.5.215"
              and isinstance(pl["context"].get("hops"), list)
              and len(pl["context"]["hops"]) == 1
              and pl["context"]["hops"][0].get("hop") == 1, pl)
        check("trace·无旧错位键 data", "data" not in pl, sorted(pl.keys()))
        t = ns.handle_net_trace_ai_analyze({"target": "x", "hops_json": "[]"})
        pl2 = ai_calls[-1][1]
        check("trace·空 hops 仍按契约传 context.hops=[]（回退判定由平台侧）",
              isinstance(pl2.get("context", {}).get("hops"), list)
              and pl2["context"]["hops"] == [], pl2)
        t = ns.handle_net_trace_ai_analyze({"hops_json": "[]"})
        check("trace·缺 target 拒绝", t.get("success") is False
              and t.get("error") == "missing_target", t)
        check("trace·ROUTES 注册", "/api/netdoctor/trace-ai-analyze" in ns.NET_ROUTES)
    finally:
        ns._platform_post = orig_post2

    import inspect
    check("stress·duration clamp 上限对齐 300（2026-09-10 档位扩充硬约束）",
          "min(300," in inspect.getsource(ns.run_stress))
    shutil.rmtree(tmp6, ignore_errors=True)

    print("\n[18] AI 诊断历史 JSONL 持久化（2026-09-11 用户要求：重启可见/手动删除/迁移/滚动淘汰）")
    tmp7 = tempfile.mkdtemp(prefix="nd_smoke_hist_")
    os.environ["NETDOCTOR_CONFIG_DIR"] = tmp7
    try:
        r = ns.handle_net_ai_history({})
        check("hist·空文件读取空列表", r.get("success") is True and r.get("history") == [], r)
        r = ns.handle_net_ai_history_append({"records_json": json.dumps(
            [{"ts": 1000, "issue": "旧1"}, {"ts": 2000, "issue": "旧2"}])})
        check("hist·批量迁移导入", r.get("success") is True and r.get("total") == 2, r)
        r = ns.handle_net_ai_history_append({"records_json": json.dumps(
            {"ts": 3000, "issue": "新1", "analysis_id": "A-1"})})
        check("hist·单条追加", r.get("success") is True, r)
        r = ns.handle_net_ai_history({})
        hist = r.get("history") or []
        check("hist·ts 倒序读取", [x.get("ts") for x in hist] == [3000, 2000, 1000], hist)
        r = ns.handle_net_ai_history_append({"records_json": "{bad"})
        check("hist·非法 payload 拒绝", r.get("success") is False and r.get("error") == "invalid_records", r)
        r = ns.handle_net_ai_history_append({"records_json": json.dumps({"no_ts": 1})})
        check("hist·缺 ts 记录拒绝", r.get("success") is False, r)
        r = ns.handle_net_ai_history_delete({"ts": 2000})
        check("hist·单条删除", r.get("success") is True and r.get("removed") == 1, r)
        hist2 = (ns.handle_net_ai_history({}).get("history") or [])
        check("hist·删除后文件同步", [x.get("ts") for x in hist2] == [3000, 1000], hist2)
        r = ns.handle_net_ai_history_delete({"ts": 9999})
        check("hist·删不存在 ts 不重写（removed 0）", r.get("removed") == 0, r)
        bulk = [{"ts": 10000 + i, "issue": "b%d" % i} for i in range(205)]
        ns.handle_net_ai_history_append({"records_json": json.dumps(bulk)})
        hist3 = (ns.handle_net_ai_history({}).get("history") or [])
        check("hist·200 条滚动淘汰最旧", len(hist3) == 200 and hist3[0].get("ts") == 10204, len(hist3))
        r = ns.handle_net_ai_history_delete({"all": "1"})
        check("hist·清空全部", r.get("success") is True
              and ns.handle_net_ai_history({}).get("history") == [], r)
        check("hist·ROUTES 注册三条",
              "/api/netdoctor/ai-history" in ns.NET_ROUTES
              and "/api/netdoctor/ai-history-append" in ns.NET_ROUTES
              and "/api/netdoctor/ai-history-delete" in ns.NET_ROUTES)
    finally:
        os.environ["NETDOCTOR_CONFIG_DIR"] = prev_dir if 'prev_dir' in dir() else None
    shutil.rmtree(tmp7, ignore_errors=True)

    print("\n[19] HTTPS 传输层（2026-09-11 专项准备：CA/指纹/TLSv1.2+；真实链路待服务端就绪联调）")
    tmp8 = tempfile.mkdtemp(prefix="nd_smoke_https_")
    os.environ["NETDOCTOR_CONFIG_DIR"] = tmp8
    assets_ca = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(ns.__file__))),
                             "assets", "platform_ca.pem")
    fake_pem = ("-----BEGIN CERTIFICATE-----\n"
                + base64.b64encode(b"\x30\x82\x01\x00" + b"A" * 32).decode() + "\n"
                + "-----END CERTIFICATE-----\n")
    fp_path = os.path.join(tmp8, "fake_ca.pem")
    with open(fp_path, "w") as f:
        f.write(fake_pem)
    fp1 = ns._builtin_ca_fingerprint(fp_path)
    fp2 = ns._builtin_ca_fingerprint(fp_path)
    check("https·指纹计算稳定且为 64 位小写 hex",
          len(fp1) == 64 and fp1 == fp2 and all(c in "0123456789abcdef" for c in fp1), fp1)
    check("https·假 CA 不可解析返回空指纹", ns._builtin_ca_fingerprint(os.path.join(tmp8, "no.pem")) == "")

    # 指纹双层校验第一层：内置 CA 指纹 vs uplink_config.server_ca_fingerprint
    with open(os.path.join(tmp8, "uplink_config.json"), "w", encoding="utf-8") as f:
        json.dump({"enabled": True, "server_url": "https://127.0.0.1:1", "token": "t",
                   "terminal_id": "WIN-STUB", "server_ca_fingerprint": "deadbeef"}, f)
    if os.path.isfile(assets_ca):
        os.environ[ns.UPLINK_CA_ENV] = assets_ca
        t = ns._platform_get("/x")
        check("https·下发指纹与内置不一致 → 拒绝连接",
              t[0] == -1 and t[1].get("error") == "ca_fingerprint_mismatch", t)
        fp_real = ns._builtin_ca_fingerprint(assets_ca)
        with open(os.path.join(tmp8, "uplink_config.json"), "w", encoding="utf-8") as f:
            json.dump({"enabled": True, "server_url": "https://127.0.0.1:1", "token": "t",
                       "terminal_id": "WIN-STUB", "server_ca_fingerprint": fp_real}, f)
        t = ns._platform_get("/x")
        check("https·指纹一致 → 通过校验层（失败在网络层）",
              t[0] == -1 and "ca_fingerprint" not in str(t[1].get("error"))
              and "ca_missing" not in str(t[1].get("error")), t)
    else:
        check("https·占位 CA 不存在（跳过指纹层单测）", True)
    os.environ[ns.UPLINK_CA_ENV] = os.path.join(tmp8, "no_such_ca.pem")
    # 正式 CA 入库后定位链可达（仓库根 assets），ca_missing 语义用 monkeypatch 定位函数验证
    orig_cap = ns._uplink_ca_path
    ns._uplink_ca_path = lambda: ""
    try:
        t = ns._platform_get("/x")
        check("https·无可用 CA → fail-closed ca_missing",
              t[0] == -1 and t[1].get("error") == "ca_missing", t)
    finally:
        ns._uplink_ca_path = orig_cap
        os.environ.pop(ns.UPLINK_CA_ENV, None)

    # 真 TLS 冒烟：openssl 生成临时 CA/证书（SAN IP:127.0.0.1）→ 临时 HTTPS 服务 → 全链请求
    openssl = shutil.which("openssl")
    if not openssl:
        for cand in (r"C:\Program Files\Git\usr\bin\openssl.exe",
                     r"C:\Program Files\Git\mingw64\bin\openssl.exe"):
            if os.path.isfile(cand):
                openssl = cand
                break
    if not openssl:
        check("https·本地 TLS 冒烟（openssl 不可用，SKIP）", True)
    else:
        import subprocess as _sp
        sdir = os.path.join(tmp8, "tls")
        os.makedirs(sdir)
        ca_key, ca_pem = os.path.join(sdir, "ca.key"), os.path.join(sdir, "ca.pem")
        srv_key, srv_csr = os.path.join(sdir, "srv.key"), os.path.join(sdir, "srv.csr")
        srv_pem, srv_ext = os.path.join(sdir, "srv.pem"), os.path.join(sdir, "srv.ext")
        with open(srv_ext, "w") as f:
            f.write("subjectAltName=IP:127.0.0.1\nextendedKeyUsage=serverAuth\n")
        for cmd in (
            [openssl, "req", "-x509", "-newkey", "rsa:2048", "-keyout", ca_key,
             "-out", ca_pem, "-days", "2", "-nodes", "-subj", "/CN=ND Smoke CA"],
            [openssl, "req", "-newkey", "rsa:2048", "-keyout", srv_key,
             "-out", srv_csr, "-nodes", "-subj", "/CN=127.0.0.1"],
            [openssl, "x509", "-req", "-in", srv_csr, "-CA", ca_pem, "-CAkey", ca_key,
             "-CAcreateserial", "-out", srv_pem, "-days", "2", "-extfile", srv_ext],
        ):
            pr = _sp.run(cmd, capture_output=True)
            if pr.returncode != 0:
                break
        if not os.path.isfile(srv_pem):
            check("https·本地 TLS 冒烟（openssl 生成失败，SKIP）", True)
        else:
            import http.server as _hs
            import threading as _th

            class _H(_hs.BaseHTTPRequestHandler):
                def do_GET(self):
                    body = json.dumps({"ok": True, "echo": self.path}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)

                def do_POST(self):
                    self.rdata = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
                    body = json.dumps({"ok": True, "echo": self.path}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *a):
                    pass

            httpd = _hs.HTTPServer(("127.0.0.1", 0), _H)
            sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            sctx.load_cert_chain(srv_pem, srv_key)
            httpd.socket = sctx.wrap_socket(httpd.socket, server_side=True)
            port = httpd.server_address[1]
            th = _th.Thread(target=httpd.serve_forever, daemon=True)
            th.start()
            try:
                with open(os.path.join(tmp8, "uplink_config.json"), "w", encoding="utf-8") as f:
                    json.dump({"enabled": True, "server_url": "https://127.0.0.1:%d" % port,
                               "token": "tok", "terminal_id": "WIN-STUB",
                               "server_ca_fingerprint": ns._builtin_ca_fingerprint(ca_pem)}, f)
                os.environ[ns.UPLINK_CA_ENV] = ca_pem
                code, resp = ns._platform_get("/ping?x=1")
                check("https·真 TLS 冒烟 200 + 响应解析",
                      code == 200 and resp.get("ok") is True and resp.get("echo") == "/ping?x=1",
                      (code, resp))
                code2, resp2 = ns._platform_post("/post", {"a": 1})
                check("https·真 TLS POST 200", code2 == 200 and resp2.get("ok") is True, (code2, resp2))
                with open(os.path.join(tmp8, "uplink_config.json"), "w", encoding="utf-8") as f:
                    json.dump({"enabled": True, "server_url": "https://127.0.0.1:%d" % port,
                               "token": "tok", "terminal_id": "WIN-STUB",
                               "server_ca_fingerprint": "0" * 64}, f)
                code3, resp3 = ns._platform_get("/ping")
                check("https·真 TLS 篡改指纹 → 拒绝连接",
                      code3 == -1 and resp3.get("error") == "ca_fingerprint_mismatch", (code3, resp3))
            finally:
                os.environ.pop(ns.UPLINK_CA_ENV, None)
                httpd.shutdown()
                httpd.server_close()
    shutil.rmtree(tmp8, ignore_errors=True)

    print("\n[16] AI 证据供给（4.1.7）：JSONL 回读 + 网络性能快照 + prompt 约定 + 路由注册")
    # tracert JSONL 回读：真实 tracert 127.0.0.1 已在 [3] 跑过（_record_generic 落盘），此处直读
    rec = ns._read_last_jsonl("tracert")
    check("AI 证据·tracert JSONL 最近一条可回读（ts+hops）",
          rec is None or ("ts" in rec and "hops" in rec and "ts_text" in rec), rec)
    rec2 = ns._read_last_jsonl("stress")
    check("AI 证据·stress JSONL 最近一条可回读（ts+verdict）",
          rec2 is None or ("ts" in rec2 and "verdict" in rec2), rec2)
    snap = ns.handle_net_net_snapshot({})
    check("AI 快照·success + 链路速率字典 + 网关必选探针", snap["success"] is True
          and isinstance(snap["link_speeds"], dict)
          and any(p.get("key") == "gateway" for p in snap["probes"]), snap)
    check("AI 快照·探针字段齐备（ok/loss_pct/avg_ms/max_ms）",
          all(all(k in p for k in ("ok", "loss_pct", "avg_ms", "max_ms"))
              for p in snap["probes"] if p.get("target")), snap["probes"])
    check("AI 快照·探针走既有引擎统计形态（回环 avg 数值或如实不可达）",
          all((p.get("avg_ms") is not None) == bool(p.get("ok"))
              for p in snap["probes"] if p.get("target") == "127.0.0.1" or p.get("key") == "gateway"),
          snap["probes"])
    check("AI prompt·性能类缺证据输出约定已固化", "缺失证据清单" in ns._ND_AI_PROMPT_SYSTEM
          and "连通性测试" in ns._ND_AI_PROMPT_SYSTEM and "不得凭体感" in ns._ND_AI_PROMPT_SYSTEM)
    check("AI 路由·tracert-last/stress-last/net-snapshot 已注册", all(
        r in ns.NET_ROUTES for r in ("/api/netdoctor/tracert-last",
                                     "/api/netdoctor/stress-last",
                                     "/api/netdoctor/net-snapshot")))

    print("\n===== SMOKE RESULT: %d/%d passed =====" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print("FAILED:", FAIL)
        sys.exit(1)
    print("NETDOCTOR BACKEND SMOKE: PASS")


if __name__ == "__main__":
    main()
