# -*- coding: utf-8 -*-
"""
网络排障 E2E 门禁（Playwright chromium）
=========================================
场景 1（独立测试页）：net-doctor/web/netdoctor-standalone.html + pywebview 桩
  → 断言 nd* 关键函数、五功能数据填充、pageerror=none + 宽度抽查
场景 2（主应用集成）：file:// web/index.html + 全模块桩
  → 遍历全部菜单（主页/日志诊断/磁盘清理/性能分析/网络排障）
  → 断言各模块关键函数仍可用 + netdoctor 数据填充 + pageerror=none（防破坏其他模块）
场景 3（宽度自适应，2026-09-16 STYLE.md §9）：主应用三档视口 1920/1440/1080
  → 遍历全部 7 菜单断言无横向滚动、导航不挤压、容器流式伸缩、降级生效

依赖：pip install playwright && playwright install chromium
运行：python tools/e2e_netdoctor.py
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ND_ROOT = Path(__file__).resolve().parent.parent          # net-doctor/
WORKSPACE = ND_ROOT.parent                                 # 工作区根
STANDALONE = ND_ROOT / "web" / "netdoctor-standalone.html"
MAIN_INDEX = WORKSPACE / "web" / "index.html"

# IP 脱敏红线（2026-09-10 用户点名，756084f 惯例）：占位提示/示例文案禁真实业务 IP。
# 范围：placeholder/title 属性渲染值 + net-doctor 拥有文件源码级；节点/基线配置数据除外。
_REAL_IP_RE = r"172\.17\.|192\.168\.254|10\.8\.0\.90"

_NO_REAL_IP_JS = r"""(function () {
    var re = /172\.17\.|192\.168\.254|10\.8\.0\.90/;
    var bad = [];
    var els = document.querySelectorAll('input[placeholder], [title]');
    for (var i = 0; i < els.length; i++) {
        var ph = els[i].getAttribute('placeholder') || '';
        var ti = els[i].getAttribute('title') || '';
        if (re.test(ph)) { bad.push('placeholder:' + ph); }
        if (re.test(ti)) { bad.push('title:' + ti); }
    }
    return bad;
})()"""


def check_no_real_ip_assets(page, tag):
    """渲染级：当前页面所有 placeholder/title 属性不含真实业务 IP。"""
    bad = page.evaluate(_NO_REAL_IP_JS)
    check("[%s] 脱敏·placeholder/title 无真实业务 IP" % tag, not bad, bad)


def check_no_real_ip_sources():
    """文件级：net-doctor 拥有文件源码零真实业务 IP（防未来文案回潮，先于渲染拦截）。"""
    import re as _re
    pat = _re.compile(_REAL_IP_RE)
    for f in (ND_ROOT / "web" / "netdoctor.js", STANDALONE):
        hits = [ln for ln in f.read_text(encoding="utf-8").splitlines() if pat.search(ln)]
        check("脱敏·源文件无真实 IP（%s）" % f.name, not hits, hits[:3])

ND_STUB_JS = r"""
window.__stubCalls = [];
window.__stubLastTaskKind = null;
window.__stubUplink = { success: true, uplink: {
    enabled: true, running: true, server_url: "http://127.0.0.1:18090",
    terminal_id: "WIN-STUB-PC", state: "connected", registered: true,
    last_hb_ts: 1788950000, last_error: null, has_token: true,
    executed_count: 1, iperf3_available: true, heartbeat_interval: 30, client_version: "4.0.0" } };
window.__stubDefaultNodes = [
    { key: "gateway",    name: "终端区：本终端网关",             method: "ping",     target: "" },
    { key: "core",       name: "核心交换机",                     method: "ping",     target: "172.17.254.1" },
    { key: "datacenter", name: "数据中心区：数据中心汇聚交换机", method: "ping",     target: "172.17.254.2" },
    { key: "dmz",        name: "DMZ区：DMZ汇聚交换机",           method: "ping",     target: "172.17.254.9" },
    { key: "dns",        name: "内网DNS",                        method: "nslookup", target: "172.17.1.109", probe: "baidu.com" },
    { key: "ntp",        name: "温州总院",                       method: "ntp",      target: "ntp.eye.ac.cn" },
    { key: "internet",   name: "互联网",                         method: "ping",     target: "baidu.com" },
    { key: "center",     name: "中心服务器",                     method: "ping",     target: "" },
    { key: "tcp-sample", name: "TCP 端口检测（示例）",           method: "tcp",      target: "baidu.com:443" },
    { key: "udp-generic", name: "UDP 探测（示例）",              method: "udp",      target: "127.0.0.1:5353" }
];
window.__stubConfig = { success: true,
    expected_dns: ["172.17.1.109", "223.5.5.5"],
    iperf3_available: true,
    uplink: { enabled: true, server_url: "http://127.0.0.1:18090",
              terminal_id: "WIN-STUB-PC", configured: true },
    nodes: window.__stubDefaultNodes };
window.__stubConfCheck = { success: true, ts: 1, overall: { status: "warn", text: "存在需关注项（1 张网卡）" },
    adapters: [
        { name: "以太网", desc: "Stub Intel I226-V", mac: "AA:BB:CC:DD:EE:FF", kind: "以太网",
          speed: "1 Gbps",
          ipv4: ["172.17.90.215"], subnet: ["255.255.255.0"], ipv6: ["fe80::1a2b:3c4d:5e6f:7a8b%12"],
          gateway: ["172.17.90.1"], dns: ["172.17.1.109"],
          dhcp: true, dhcp_server: "172.17.90.1",
          lease_obtained: "2026/9/9 8:30:01", lease_expires: "2026/9/10 8:30:01",
          wins: ["172.17.1.109"],
          active: true, tunnel: false, media_down: false,
          checks: [
              { item: "DHCP", status: "ok", reason: "自动获取，服务器 172.17.90.1" },
              { item: "DNS", status: "warn", reason: "缺少基线 DNS：223.5.5.5", servers: ["172.17.1.109"] },
              { item: "网关", status: "ok", reason: "172.17.90.1" } ] },
        { name: "WLAN", desc: "Stub Wi-Fi 6E", mac: "", kind: "无线局域网",
          ipv4: [], gateway: [], dns: [], dhcp: null, active: false, tunnel: false, media_down: true,
          checks: [ { item: "链路状态", status: "muted", reason: "媒体已断开（未连接）" } ] }
    ] };
window.__stubConflict = { success: true, ip: "172.17.90.215", mac: "AA:BB:CC:DD:EE:FF",
    adapter: "以太网", connected: true, object_note: "与中心通信网卡：以太网",
        verdict: { conflict_suspect: true, window_days: 7,
        suspect_reasons: ["multi_terminal", "unknown_terminal"],
        nic_history: ["2026-08-01 网卡变更为 AA:BB:CC:DD:EE:FF", "2026-09-01 网卡变更为 AA:BB:CC:DD:EE:00"],
        evidence: ["2026-09-08 22:14 终端 STUB-OLD 上报相同 IP", "准入日志检出 MAC 漂移"],
        sources: { terminal_reports: true, admission_log: "not_connected", core_switch_state: "not_connected" } },
    ai: { ok: true, model: "stub-model", analysis_id: "A-STUB-CF",
        analysis: "疑似与终端 STUB-OLD 冲突，建议核查准入交换机绑定。" } };
window.__stubDeepRunning = { success: true, task: { task_id: "DC-E2E01", status: "running",
    steps: [ { step: "resolve", name: "网关解析", status: "done", target: "172.17.254.1",
        note: "知识库最长前缀匹配 172.17.90.0/24", zone: "核心交换层" } ] } };
window.__stubDeepDone = { task_id: "DC-E2E01", terminal_id: "WIN-STUB-PC",
    ip: "172.17.90.215", mac: "AA:BB:CC:DD:EE:FF", status: "done", error: null,
    steps: [
        { step: "resolve", name: "网关解析", status: "done", target: "172.17.254.1",
          note: "知识库最长前缀匹配 172.17.90.0/24", zone: "核心交换层",
          commands: [{ cmd: "display version", ok: true, output_tail: "S5720 V200R019" }] },
        { step: "arp", name: "网关 ARP 核验", status: "match", target: "172.17.254.1",
          commands: [{ cmd: "display arp | include 172.17.90.215", ok: true, output_tail: "IP MAC VLAN" }],
          evidence: ["172.17.90.215  aabb-ccdd-eeff"] },
        { step: "nad", name: "准入系统核验", status: "match", target: "NAD",
          banner: "命中在线终端：赵吕骏的办公电脑", evidence: ["终端名 赵吕骏的办公电脑"] },
        { step: "macaddr", name: "接入交换机 MAC 表", status: "multi", target: "接入交换机",
          note: "同 MAC 出现于 2 端口", evidence: ["GE0/0/1", "GE0/0/7"] },
        { step: "conclude", name: "综合判定", status: "done", note: "多端口漂移且准入在线" }
    ],
    verdict: { ip: "172.17.90.215", mac: "AA:BB:CC:DD:EE:FF", conclusion: "confirmed",
        reasons: ["同 MAC 多端口", "准入在线与上报一致"], checked_at: 1725955200,
        sources: { gateway_arp: "match", admission: "match", access_mac: "multi" } } };
window.__stubPing = { success: true, center_connected: true, ts: 1,
    summary: { total: 10, ok: 7, err: 2, muted: 1 },
    results: [
        { key: "gateway",    name: "终端区：本终端网关",             method: "ping",     target: "172.17.90.1",     ok: true,  status: "ok",  loss_pct: 0,    avg_ms: 1,    max_ms: 2, detail: "平均 1ms / 丢包 0%" },
        { key: "core",       name: "核心交换机",                     method: "ping",     target: "172.17.254.1",    ok: true,  status: "ok",  loss_pct: 0,    avg_ms: 2,    max_ms: 3, detail: "平均 2ms / 丢包 0%" },
        { key: "datacenter", name: "数据中心区：数据中心汇聚交换机", method: "ping",     target: "172.17.254.2",    ok: true,  status: "ok",  loss_pct: 0,    avg_ms: 3,    max_ms: 4, detail: "平均 3ms / 丢包 0%" },
        { key: "dmz",        name: "DMZ区：DMZ汇聚交换机",           method: "ping",     target: "172.17.254.9",    ok: true,  status: "ok",  loss_pct: 0,    avg_ms: 4,    max_ms: 5, detail: "平均 4ms / 丢包 0%" },
        { key: "dns",        name: "内网DNS",                        method: "nslookup", target: "172.17.1.109",    ok: true,  status: "ok",  loss_pct: 0,    avg_ms: null, max_ms: null, detail: "解析 baidu.com → 110.242.68.66" },
        { key: "ntp",        name: "温州总院",                       method: "ntp",      target: "ntp.eye.ac.cn",   ok: true,  status: "warn", loss_pct: 0,    avg_ms: 1062.0, max_ms: 1065.2, detail: "偏差 +1062.0ms · 需校时（5 样本）" },
        { key: "internet",   name: "互联网",                         method: "ping",     target: "baidu.com",       ok: false, status: "err", loss_pct: 100,  avg_ms: null, max_ms: null, detail: "超时或不可达" },
        { key: "center",     name: "中心服务器",                     method: "ping",     target: "127.0.0.1",       ok: false, status: "muted", loss_pct: null, avg_ms: null, max_ms: null, detail: "未连接中心" },
        { key: "tcp-sample", name: "TCP 端口检测（示例）",           method: "tcp",      target: "baidu.com:443",   ok: true,  status: "ok",  loss_pct: 0,    avg_ms: 35,   max_ms: 35, detail: "连接成功" },
        { key: "udp-generic", name: "UDP 探测（示例）",              method: "udp",      target: "127.0.0.1:5353",  ok: false, status: "no_response", loss_pct: 0, avg_ms: null, max_ms: null, detail: "无响应（UDP 无连接，无响应不等于不通）" }
    ] };
window.__stubHistory = { success: true, recent: [], agg: [
    { key: "gateway", n: 8, ok_n: 8, ok_rate: 100.0, avg_ms: 0.9, max_ms: 2, avg_ms_sum: 7.2, avg_ms_n: 8 },
    { key: "ntp", n: 3, ok_n: 3, warn_n: 1, ok_rate: 100.0, avg_ms: 1115.3, max_ms: 1120.0, avg_ms_sum: 3345.9, avg_ms_n: 3 },
    { key: "internet", n: 8, ok_n: 2, ok_rate: 25.0, avg_ms: 22.0, max_ms: 30, avg_ms_sum: 44, avg_ms_n: 2 } ] };
window.__stubTracert = { success: true, target: "172.17.254.2", kb_count: 3, kb_error: null, reached: true, ts: 1,
    hops: [
        { hop: 1, delays: ["<1ms", "<1ms", "<1ms"], ip: "172.17.90.1",  host: null, timeout: false, zone: "终端区", zone_desc: null },
        { hop: 2, delays: ["2ms", "2ms", "1ms"],    ip: "172.17.254.1", host: "core-sw", timeout: false, zone: "核心交换层", zone_desc: "核心交换机" },
        { hop: 3, delays: ["3ms", "3ms", "2ms"],    ip: "172.17.254.2", host: null, timeout: false, zone: "数据中心区", zone_desc: null },
        { hop: 4, delays: ["*", "*", "*"],          ip: null,           host: null, timeout: true,  zone: null, zone_desc: null }
    ] };
window.__stubStress = { success: true, center: "127.0.0.1", sizes: [64, 256], udp_mbps: 100,
    iperf_duration: 10, ts: 1,
    ping: [
        { size: 64,  ok: true, loss_pct: 0, avg_ms: 1, min_ms: 0, max_ms: 2 },
        { size: 256, ok: true, loss_pct: 0, avg_ms: 2, min_ms: 1, max_ms: 3 } ],
    iperf: [
        { mode: "tcp", ok: true, port: 18201, duration_sec: 10, summary: "tcp 941.2 Mbits/sec",
          result: { mode: "tcp", mbits_sec: 941.2, retransmits: 0,
                    interval_max_mbits: 952.1, interval_min_mbits: 930.4, interval_avg_mbits: 941.2 } },
        { mode: "udp", ok: true, port: 18202, duration_sec: 10, summary: "udp 100.0 Mbits/sec jitter 0.155ms loss 0%",
          result: { mode: "udp", mbits_sec: 100.0, jitter_ms: 0.155, lost_percent: 0.0 } } ],
    verdict: { text: "优良", cls: "ok", reason: "零丢包，平均延迟 2ms" } };
window.__stubTaskResults = {
    "confcheck": window.__stubConfCheck,
    "ipconflict": window.__stubConflict,
    "ping": window.__stubPing,
    "tracert": window.__stubTracert,
    "stress": window.__stubStress
};
window.__stubHwinfoMini = { success: true, hwinfo: { hostname: "STUB-PC",
    os: { caption: "Stub Windows 11 Pro", version: "10.0", build: "26200" },
    cpu: { name: "Stub CPU" } } };
window.__stubLogSearchMini = { success: true, events: [
    { timestamp: "2026-09-09 10:00:00", event_id: 6005, level_name: "信息", source: "Stub", description: "e1" },
    { timestamp: "2026-09-09 10:01:00", event_id: 6006, level_name: "信息", source: "Stub",
      description: "<The description for Event ID 6006 cannot be found>" },
    { timestamp: "2026-09-09 10:02:00", event_id: 6008, level_name: "错误", source: "Stub",
      description: "LONGA" + "x".repeat(240) + "LONGEND" } ] };
window.__stubBigSearch = (function () {
    var evs = [];
    for (var i = 0; i < 150; i++) {
        evs.push({ timestamp: "2026-09-09 10:" + ("0" + (i % 60)).slice(-2) + ":00",
                   event_id: 7000 + i, level_name: "信息", source: "BigStub",
                   description: "BIGEVT" + i + " " + new Array(200).join("y") });
    }
    return { success: true, events: evs, summary: { total: 150 } };
})();
window.pywebview = { api: {
    call: function (path, body) {
        if (window.__stubHang) { return new Promise(function () {}); }   /* 冻结缺陷注入：永挂起 */
        window.__stubCalls.push(path);
        return new Promise(function (resolve) {
            var p = path.split("?")[0];
            if (p.indexOf("/api/perf/uplink/status") === 0) return resolve(window.__stubUplink);
            if (p.indexOf("/api/perf/hwinfo") === 0) return resolve(window.__stubHwinfoMini);
            if (p.indexOf("/api/loginspector/search") === 0) {
                window.__stubLastLogPath = path;
                if (window.__stubBigLog) { return resolve(window.__stubBigSearch); }
                return resolve(window.__stubLogSearchMini);
            }
            if (p.indexOf("/api/netdoctor/ai-diagnose") === 0) {
                var req = {};
                try { req = JSON.parse(body || "{}"); } catch (e) {}
                window.__stubLastAiReq = req;
                window.__stubLastAiMode = req.mode || "enterprise";
                window.__stubAiCallCount = (window.__stubAiCallCount || 0) + 1;
                var reply = function () {
                    if ((req.mode || "enterprise") === "personal") {
                        if (!window.__stubPersonalConfigured) {
                            return resolve({ success: false, error: "not_configured" });
                        }
                        if (window.__stubPersonal401) {
                            return resolve({ success: false,
                                error: 'personal_http_401: {"error":"Invalid API key"}' });
                        }
                        return resolve({ success: true,
                            response_text: "【故障原因分析】个人版直连链路 OK，日志已接收。【处理意见】立即处理：**无**；建议观察：持续观察丢包率。【风险提示】个人版结论仅本机展示，不落中心。",
                            model: "personal-stub", duration_ms: 2345 });
                    }
                    if (window.__stubAiFail) {
                        return resolve({ success: false, error: "stub ai fail", analysis_id: "A-STUB-ERR" });
                    }
                    return resolve({ success: true, analysis_id: "A-STUB-1",
                        response_text: "【故障原因分析】网络链路正常，网关与核心可达，NTP 偏差 +1062ms。【处理意见】立即处理：**校时服务器同步**；建议观察：持续观察丢包率。【风险提示】如需进一步定位，请提供准入日志数据。",
                        model: "stub-model", duration_ms: 12345 });
                };
                if (window.__stubAiDelay) { setTimeout(reply, window.__stubAiDelay); return; }
                reply();
            }
            if (p.indexOf("/api/netdoctor/ai-personal-test") === 0) {
                window.__stubLastPersonalTest = path;
                var used = path.indexOf("api_key=") > -1 ? "provided"
                    : ((window.__stubConfig.ai_personal || {}).api_key ? "saved" : "none");
                if (used === "none") {
                    return resolve({ success: true, ok: false, used_key: "none",
                        hint: "未配置 API Key，请先填写并保存（设置 → AI 诊断（个人版））" });
                }
                return resolve({ success: true, ok: true, http_code: 200, used_key: used,
                    hint: "服务可达（HTTP 200）" });
            }
            if (p.indexOf("/api/netdoctor/config-check") === 0) {
                window.__stubLastTaskKind = "confcheck";
                return resolve({ success: true, task_id: "STUB01", reused: false });
            }
            if (p.indexOf("/api/netdoctor/ipconflict") === 0) {
                window.__stubLastTaskKind = "ipconflict";
                return resolve({ success: true, task_id: "STUB02", reused: false });
            }
            if (p.indexOf("/api/netdoctor/conflict-deep-start") === 0) {
                window.__stubLastDeepStart = path;
                if (window.__stubDeepStartBusy) {
                    return resolve({ success: false, error: "busy",
                        detail: "deep check busy, retry later" });
                }
                return resolve({ success: true, task_id: "DC-E2E01", status: "running",
                    ip: "172.17.90.215", mac: "AA:BB:CC:DD:EE:FF" });
            }
            if (p.indexOf("/api/netdoctor/conflict-deep-poll") === 0) {
                window.__stubDeepPollCount = (window.__stubDeepPollCount || 0) + 1;
                if (window.__stubDeepPollCount < 2) { return resolve(window.__stubDeepRunning); }
                return resolve({ success: true, task: window.__stubDeepDone });
            }
            if (p.indexOf("/api/netdoctor/ping-start") === 0) {
                window.__stubLastTaskKind = "ping";
                return resolve({ success: true, task_id: "STUB03", reused: false });
            }
            if (p.indexOf("/api/netdoctor/tracert-start") === 0) {
                window.__stubLastTaskKind = "tracert";
                window.__stubLastTracertStart = path;
                window.__stubTracertStartCount = (window.__stubTracertStartCount || 0) + 1;
                return resolve({ success: true, task_id: "STUB04", reused: false });
            }
            if (p.indexOf("/api/netdoctor/stress-start") === 0) {
                window.__stubLastTaskKind = "stress";
                window.__stubLastStressStart = path;
                window.__stubStressStartCount = (window.__stubStressStartCount || 0) + 1;
                return resolve({ success: true, task_id: "STUB05", reused: false });
            }
            if (p.indexOf("/api/perf/stress-start") === 0) {
                window.__stubPerfStressDone = true;   /* 压测任务完成后 record-latest 才命中（初始 found:false） */
                return resolve({ success: true, stress_id: "PERFS1" });
            }
            if (p.indexOf("/api/perf/stress-status") === 0) {
                if (path.indexOf("stress_id=") > -1) {
                    return resolve({ success: true, task: { status: "done" } });
                }
                return resolve({ success: false });
            }
            if (p.indexOf("/api/perf/record-latest") === 0) {
                window.__stubLastRecordLatest = path;
                if (path.indexOf("kind=stress") > -1 && window.__stubPerfStressDone) {
                    return resolve({ success: true, found: true, record_id: 42,
                        report: { verdict: "TCP 850.3 Mbits/sec · 综合: 良好", center: "127.0.0.1" } });
                }
                return resolve({ success: true, found: false });
            }
            if (p.indexOf("/api/netdoctor/task-status") === 0) {
                var kind = window.__stubLastTaskKind || "ping";
                return resolve({ success: true, task: { id: "STUB", kind: kind, status: "done",
                    progress: {}, result: window.__stubTaskResults[kind], error: null, elapsed: 0.1 } });
            }
            if (p.indexOf("/api/netdoctor/task-cancel") === 0) return resolve({ success: true, cancelled: true });
            if (p.indexOf("/api/netdoctor/config") === 0) {
                window.__stubConfigCalls = window.__stubConfigCalls || [];
                window.__stubConfigCalls.push(path);
                if (path.indexOf("nodes_reset=1") > -1) {
                    window.__stubConfig.nodes = window.__stubDefaultNodes;
                    return resolve({ success: true, nodes: window.__stubConfig.nodes,
                                     expected_dns: window.__stubConfig.expected_dns,
                                     ai_personal: { has_key: false } });
                }
                if (path.indexOf("reset=1") > -1) {
                    window.__stubConfig.nodes = window.__stubDefaultNodes;
                    window.__stubConfig.expected_dns = ["172.17.1.109", "223.5.5.5"];
                    return resolve({ success: true, nodes: window.__stubConfig.nodes,
                                     expected_dns: window.__stubConfig.expected_dns,
                                     ai_personal: (function () {
                                         var ap = window.__stubConfig.ai_personal || {};
                                         return { api_url: ap.api_url || "", model: ap.model || "", has_key: !!ap.api_key };
                                     })() });
                }
                if (path.indexOf("nodes_json=") > -1) {
                    try {
                        window.__stubConfig.nodes = JSON.parse(
                            decodeURIComponent(path.split("nodes_json=")[1].split("&")[0]));
                    } catch (e) { return resolve({ success: false, error: "nodes_json" }); }
                    if (path.indexOf("expected_dns_json=") > -1) {
                        try {
                            window.__stubConfig.expected_dns = JSON.parse(
                                decodeURIComponent(path.split("expected_dns_json=")[1].split("&")[0]));
                        } catch (e) {}
                    }
                }
                if (path.indexOf("ai_personal_json=") > -1) {
                    try {
                        var aiIn = JSON.parse(
                            decodeURIComponent(path.split("ai_personal_json=")[1].split("&")[0]));
                        var curKey = (window.__stubConfig.ai_personal || {}).api_key || "";
                        window.__stubConfig.ai_personal = {
                            api_url: aiIn.api_url || "",
                            model: aiIn.model || "",
                            api_key: (aiIn.api_key || curKey)
                        };
                    } catch (e) {}
                }
                return resolve({ success: true, nodes: window.__stubConfig.nodes,
                                 expected_dns: window.__stubConfig.expected_dns,
                                 ai_personal: (function () {
                                     var ap = window.__stubConfig.ai_personal || {};
                                     return { api_url: ap.api_url || "", model: ap.model || "", has_key: !!ap.api_key };
                                 })() });
            }
            if (p.indexOf("/api/netdoctor/conflict-ai-reanalyze") === 0) {
                window.__stubLastReAi = path;
                var replyRe = function () {
                    if (window.__stubReAiFail) {
                        return resolve({ success: false, error: "ai_http_500: stub analyze fail" });
                    }
                    return resolve({ success: true, ai: { ok: true,
                        analysis: "【故障原因分析】重跑聚合分析 OK。【处理意见】观察。【风险提示】无。",
                        model: "stub-rerun", analysis_id: "AI-9" } });
                };
                if (window.__stubReAiDelay) { setTimeout(replyRe, window.__stubReAiDelay); return; }
                replyRe();
            }
            if (p.indexOf("/api/netdoctor/trace-ai-analyze") === 0) {
                window.__stubLastTraceAi = path;
                var replyTa = function () {
                    if (window.__stubTraceAiFail) {
                        return resolve({ success: false, error: "ai_http_500: stub trace fail" });
                    }
                    return resolve({ success: true, ai: { ok: true,
                        analysis: "【故障原因分析】路由路径分析 OK。【处理意见】观察。【风险提示】无。",
                        model: "stub-trace", analysis_id: "RT-7" } });
                };
                if (window.__stubTraceAiDelay) { setTimeout(replyTa, window.__stubTraceAiDelay); return; }
                replyTa();
            }
            if (p.indexOf("/api/netdoctor/ai-history") === 0) {
                window.__stubAiHist = window.__stubAiHist || [];
                if (p.indexOf("ai-history-append") >= 0) {
                    var recsH = [];
                    try { recsH = JSON.parse(decodeURIComponent(
                        (path.split("records_json=")[1] || "").split("&")[0])); } catch (e) {}
                    if (!Array.isArray(recsH)) { recsH = [recsH]; }
                    for (var ih = 0; ih < recsH.length; ih++) {
                        if (recsH[ih] && recsH[ih].ts) { window.__stubAiHist.unshift(recsH[ih]); }
                    }
                    window.__stubAiHist.sort(function (a, b) { return (b.ts || 0) - (a.ts || 0); });
                    return resolve({ success: true });
                }
                if (p.indexOf("ai-history-delete") >= 0) {
                    var qh = path.split("?")[1] || "";
                    if (qh.indexOf("all=1") >= 0) { window.__stubAiHist = []; return resolve({ success: true }); }
                    var tsh = parseInt((qh.split("ts=")[1] || "0"), 10);
                    window.__stubAiHist = window.__stubAiHist.filter(function (r) {
                        return Number(r.ts) !== tsh; });
                    return resolve({ success: true });
                }
                return resolve({ success: true, history: window.__stubAiHist.slice(0, 200) });
            }
            if (p.indexOf("/api/netdoctor/ping-history") === 0) {
                window.__stubLastHistoryPath = path;
                return resolve(window.__stubHistory);
            }
            if (p.indexOf("/api/netdoctor/tracert-last") === 0) {
                return resolve(window.__stubTracertLast === undefined
                    ? { success: false, error: "no_record" }
                    : { success: true, record: window.__stubTracertLast });
            }
            if (p.indexOf("/api/netdoctor/stress-last") === 0) {
                return resolve(window.__stubStressLast === undefined
                    ? { success: false, error: "no_record" }
                    : { success: true, record: window.__stubStressLast });
            }
            if (p.indexOf("/api/netdoctor/net-snapshot") === 0) {
                window.__stubSnapshotCalls = (window.__stubSnapshotCalls || 0) + 1;
                return resolve({ success: true, ts: 1, link_speeds: { "ethernet": "1 Gbps" },
                    probes: [ { key: "gateway", target: "127.0.0.1", ok: true, loss_pct: 0,
                                min_ms: 1, avg_ms: 2, max_ms: 3 },
                              { key: "core", target: "127.0.0.2", ok: false, loss_pct: 100,
                                min_ms: null, avg_ms: null, max_ms: null } ] });
            }
            if (p.indexOf("/api/netdoctor/stress-export") === 0)
                return resolve({ success: true, path: "C:\\stub\\NetStress_20260909_1000.html" });
            if (p.indexOf("/api/disk/open-location") === 0) return resolve({ success: true });
            return resolve({ success: true });
        });
    } } };
"""

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name + ("" if cond else "  ← " + str(detail)[:200]))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  | " + str(detail)[:140]))


def assert_functions(page, fns, tag):
    for fn in fns:
        check("[%s] %s 为函数" % (tag, fn),
              page.evaluate("typeof %s" % fn) == "function")


def run_scenario_1(page):
    print("\n[场景1] 独立测试页（netdoctor-standalone.html + 桩）")
    page.goto(STANDALONE.as_uri())
    page.wait_for_timeout(1000)
    check_no_real_ip_sources()
    check_no_real_ip_assets(page, "nd")
    check("Rename·独立页定名网络排障（2026-09-11 回退）", "网络排障" in page.title()
          and "网络排障" in page.inner_text("#tab-netdoctor .page-header h2"))
    # 卡片折叠（2026-09-11：五模块卡默认收起 + 按钮联动展开）
    assert_functions(page, ["ndExpandCard", "ndInitCollapsible"], "nd")
    check("折叠·五卡默认全收起", page.evaluate(
        "(function(){ var cs = document.querySelectorAll('#tab-netdoctor .section-card.nd-styled');"
        " if (cs.length !== 5) { return -1; } var n = 0;"
        " for (var i = 0; i < cs.length; i++) { if (cs[i].classList.contains('collapsed')) { n++; } }"
        " return n; })()") == 5)
    page.click("#ndConfBtn")
    page.wait_for_timeout(500)
    check("折叠·点开始核查先展开再发起", page.evaluate(
        "(function(){ var c = document.getElementById('ndConfBtn').closest('.section-card');"
        " return !c.classList.contains('collapsed'); })()")
        and page.evaluate("window.__stubLastTaskKind") == "confcheck")
    before_cls = page.evaluate(
        "document.getElementById('ndConfBtn').closest('.section-card').className")
    page.click("#ndConfBtn")
    page.wait_for_timeout(400)
    check("折叠·已展开卡再点不重复展开", before_cls == page.evaluate(
        "document.getElementById('ndConfBtn').closest('.section-card').className"),
        (before_cls, page.evaluate(
            "document.getElementById('ndConfBtn').closest('.section-card').className")))
    page.evaluate("(function(){ var cs = document.querySelectorAll('#tab-netdoctor .section-card.nd-styled');"
        " for (var i = 0; i < cs.length; i++) { cs[i].classList.remove('collapsed'); } })()")
    check("AI排版·独立页问题概述行式（等效基线）", page.evaluate(
        "(function(){ var r = document.querySelector('.nd-ai-card .hm-row');"
        " return !!r && getComputedStyle(r).justifyContent === 'space-between'; })()"))

    assert_functions(page, ["initNetDoctorTab", "ndStartConfigCheck", "ndStartConflict",
                            "ndStartPing", "ndStartTracert", "ndStartStress",
                            "ndPollTask", "ndRenderConfig", "ndRenderStress",
                            "ndApiFetch", "ndExportStress"], "nd")
    upl = page.inner_text("#ndUplinkBody")
    check("中心状态·二元已连接", "已连接" in upl and "未连接" not in upl, upl[:80])
    check("中心状态·仅徽章无附加信息", "WIN-STUB-PC" not in upl and "18090" not in upl
          and "已内置" not in upl and "iperf3" not in upl.lower(), upl[:80])
    check("依赖中心按钮可用", page.evaluate("document.getElementById('ndConflictBtn').disabled") is False
          and page.evaluate("document.getElementById('ndStressBtn').disabled") is False)
    check("门禁提示隐藏", page.evaluate("document.getElementById('ndConflictGate').style.display") == "none")

    nodes_rows = page.evaluate("document.querySelectorAll('#ndNodesBody tr').length")
    check("节点表 10 行", nodes_rows == 10, nodes_rows)
    body_txt = page.inner_text("#ndNodesBody")
    check("节点表·核心交换机", "核心交换机" in body_txt)
    check("节点表·内网DNS", "内网DNS" in body_txt and "DNS 解析" in body_txt)
    check("节点表·动态网关标注", "动态取本地网关" in body_txt)
    tip = page.inner_text("#ndDnsBaselineTip")
    check("DNS 基线提示", "DNS 基线：" in tip and "223.5.5.5" in tip, tip[:80])
    check("文案·核查卡两行提示零残留", page.evaluate(
        "(function(){ var t = document.getElementById('tab-netdoctor').textContent;"
        " return t.indexOf('核查每张活动网卡') < 0"
        " && t.indexOf('未配置 DNS 基线，当前展示实测值') < 0; })()"))
    check("文案·IP 冲突卡说明句零残留", page.evaluate(
        "(function(){ var t = document.getElementById('tab-netdoctor').textContent;"
        " return t.indexOf('取本机活动网卡 IP+MAC') < 0"
        " && t.indexOf('准入日志等数据源') < 0; })()"))
    check("文案·路由追踪卡说明句零残留", page.evaluate(
        "(function(){ var t = document.getElementById('tab-netdoctor').textContent;"
        " return t.indexOf('逐跳显示延迟') < 0"
        " && t.indexOf('标注所属网络区域') < 0; })()"))
    hist = page.inner_text("#ndHistoryBody")
    check("历史摘要·gateway", "gateway" in hist and "100%" in hist, hist[:80])
    check("历史摘要·ntp 可达100%且标注偏差过大", "可达 · 偏差过大" in hist
          and "1115.3" in hist, hist[:120])
    check("历史摘要·warn 不改成功率", page.evaluate(
        "document.querySelectorAll('#ndHistoryBody tr').length") == 4)

    # ① 配置核查
    page.click("#ndConfBtn")
    page.wait_for_timeout(700)
    conf = page.inner_text("#ndConfBody")
    check("核查·网卡卡名", "以太网" in conf and "Stub Intel I226-V" in conf, conf[:80])
    check("核查·DHCP 正常", "自动获取" in conf, conf[:80])
    check("核查·DNS 基线差异", "缺少基线 DNS：223.5.5.5" in conf, conf[:80])
    check("核查·非活动网卡不在默认视图", "媒体已断开" not in conf and "WLAN" not in conf, conf[:80])
    check("核查·总体徽章", "需关注" in page.inner_text("#ndConfBadge"))
    check("核查·链路速率", "链路速率" in conf and "1 Gbps" in conf, conf[:100])
    check("核查·子网掩码", "255.255.255.0" in conf, conf[:100])
    check("核查·IPv6", "fe80::1a2b" in conf, conf[:100])
    check("核查·DHCP 启用/服务器", "DHCP 启用" in conf and "DHCP 服务器" in conf, conf[:100])
    check("核查·租约获取与到期", "租约获取" in conf and "2026/9/10 8:30:01" in conf, conf[:100])
    check("核查·WINS", "WINS" in conf and "172.17.1.109" in conf, conf[:100])
    check("核查·非活动默认隐藏", page.evaluate(
        "var w = document.getElementById('ndInactiveWrap'); w && w.style.display === 'none' && w.innerHTML.indexOf('WLAN') > -1"))
    check("核查·折叠按钮文案", "1 个非活动网卡" in page.inner_text("#ndInactiveToggle"),
          page.inner_text("#ndInactiveToggle"))
    page.click("#ndInactiveToggle")
    page.wait_for_timeout(200)
    check("核查·非活动展开可见", page.evaluate(
        "var w = document.getElementById('ndInactiveWrap'); w && w.style.display === 'block'")
        and "媒体已断开" in page.inner_text("#ndInactiveWrap"))
    page.click("#ndInactiveToggle")
    page.wait_for_timeout(200)
    check("核查·非活动收起", page.evaluate(
        "var w = document.getElementById('ndInactiveWrap'); w && w.style.display === 'none'"))

    # ② IP 冲突
    page.click("#ndConflictBtn")
    page.wait_for_timeout(700)
    cf = page.inner_text("#ndConflictBody")
    check("冲突·疑似徽章", "疑似 IP 冲突" in cf, cf[:80])
    check("冲突·检测对象标注与中心通信网卡", "与中心通信网卡" in cf, cf[:120])
    check("冲突·判定原因清单", "判定原因" in cf and "multi_terminal" in cf, cf[:200])
    check("冲突·网卡变更史折叠节", "网卡变更史（2 条，不构成冲突）" in cf
          and page.evaluate("document.getElementById('ndNicHistBody').style.display") == "none")
    page.evaluate("ndToggleNicHist()")
    page.wait_for_timeout(150)
    check("冲突·变更史展开可见", "网卡变更为" in page.inner_text("#ndNicHistBody"))
    page.evaluate("ndToggleNicHist()")
    check("冲突·证据列表", "STUB-OLD" in cf, cf[:80])
    check("冲突·数据源如实", "准入日志：未接入" in cf and "核心交换状态：未接入" in cf, cf[:80])
    check("冲突·AI 分析", "AI 辅助分析" in cf and "STUB-OLD" in page.inner_text("#ndConflictBody .nd-ai"))

    # ②.c AI 辅助分析升级（response 字段修复 + model/#analysis_id 可追溯 + 手动重跑）
    assert_functions(page, ["ndReanalyzeAi", "ndRenderAiAssist"], "nd")
    check("AI升级·model 徽章与 analysis_id 渲染",
          "[stub-model]" in cf and "#A-STUB-CF" in cf, cf[:160])
    check("AI升级·手动按钮存在可用", page.evaluate("!!document.getElementById('ndAiReBtn')")
          and page.evaluate("document.getElementById('ndAiReBtn').disabled") is False)
    page.evaluate("ndReanalyzeAi()")
    page.wait_for_timeout(600)
    re_body = page.inner_text("#ndAiReBody")
    check("AI重跑·成功渲染文本+model+#id",
          "分析完成" in page.inner_text("#ndAiReTip")
          and "重跑聚合分析 OK" in re_body and "[stub-rerun]" in re_body and "#AI-9" in re_body,
          (page.inner_text("#ndAiReTip") + "|" + re_body)[:160])
    check("AI重跑·按钮复位", page.evaluate("document.getElementById('ndAiReBtn').disabled") is False)
    page.evaluate("window.__stubReAiFail = true; ndReanalyzeAi()")
    page.wait_for_timeout(600)
    check("AI重跑·失败态提示可重试且按钮复位",
          "分析失败" in page.inner_text("#ndAiReTip") and "可重试" in page.inner_text("#ndAiReTip")
          and page.evaluate("document.getElementById('ndAiReBtn').disabled") is False,
          page.inner_text("#ndAiReTip"))
    page.evaluate("window.__stubReAiFail = false; ndReanalyzeAi()")
    page.wait_for_timeout(600)
    check("AI重跑·重试成功恢复渲染", "重跑聚合分析 OK" in page.inner_text("#ndAiReBody"))
    check("AI重跑·发起携带 ip/mac/evidence", page.evaluate(
        "(function(){ var p = window.__stubLastReAi || '';"
        " return p.indexOf('ip=172.17.90.215') >= 0 && p.indexOf('mac=AA%3ABB') >= 0"
        " && p.indexOf('evidence_json=') >= 0; })()"), page.evaluate("window.__stubLastReAi"))
    # 热修复门禁（2026-09-11）：聚合分析走专用 60s 通道，不被 ndApiFetch 通用层掐断——
    # 将通用层超时压到 400ms、桩延迟 900ms（>400 且 <<60s），若误走通用层必超时失败
    page.evaluate("window.__apiFetchTimeout = 400; window.__stubReAiDelay = 900;")
    page.evaluate("ndReanalyzeAi()")
    check("AI重跑·分析中预期提示", "30~60" in page.inner_text("#ndAiReTip"))
    page.wait_for_function(
        "document.getElementById('ndAiReTip').textContent.indexOf('分析完成') >= 0", timeout=6000)
    check("ReAi·慢响应(900ms>通用层400ms)不被掐断最终渲染成功",
          "重跑聚合分析 OK" in page.inner_text("#ndAiReBody"),
          page.inner_text("#ndAiReBody")[:120])
    page.evaluate("window.__apiFetchTimeout = undefined; window.__stubReAiDelay = 0;")

    # ②.b IP 冲突深度检测（平台编排时间线，契约 9e604a7+cc34bae）
    assert_functions(page, ["ndStartDeep", "ndDeepPollTick", "ndRenderDeep"], "nd")
    check("冲突·深度检测入口可用", page.evaluate("!!document.getElementById('ndDeepBtn')")
          and page.evaluate("document.getElementById('ndDeepBtn').disabled") is False)
    page.evaluate("window.__stubDeepPollCount = 0; ndStartDeep()")
    page.wait_for_timeout(500)
    check("深度·任务创建提示", "已创建" in page.inner_text("#ndDeepTip")
          and "DC-E2E01" in page.inner_text("#ndDeepTip"), page.inner_text("#ndDeepTip"))
    check("深度·发起携带 ip/mac", page.evaluate(
        "(function(){ var p = window.__stubLastDeepStart || '';"
        " return p.indexOf('ip=172.17.90.215') >= 0 && p.indexOf('mac=AA%3ABB') >= 0; })()"),
        page.evaluate("window.__stubLastDeepStart"))
    check("深度·进行中步骤渲染", page.evaluate(
        "(function(){ var t = document.getElementById('ndDeepBody').textContent;"
        " return t.indexOf('网关解析') >= 0 && t.indexOf('已完成') >= 0; })()"),
        page.inner_text("#ndDeepBody")[:120])
    page.wait_for_function(
        "document.getElementById('ndDeepTip').textContent.indexOf('深度检测完成') >= 0", timeout=9000)
    deep = page.inner_text("#ndDeepBody")
    check("深度·verdict 确认冲突徽章", "确认 IP 冲突" in deep, deep[:80])
    check("深度·结论依据渲染", "同 MAC 多端口" in deep and "准入在线与上报一致" in deep, deep[:120])
    check("深度·数据源三源如实", "网关 ARP：match" in deep and "准入系统：match" in deep
          and "接入交换机 MAC：multi" in deep, deep[:160])
    check("深度·步骤时间线 5 步全渲染", page.evaluate(
        "document.querySelectorAll('#ndDeepBody .nd-step').length") == 5)
    check("深度·漂移信号徽章（multi）", "多端口（漂移信号）" in deep, deep[:80])
    check("深度·banner 命中准入终端", "赵吕骏的办公电脑" in deep, deep[:80])
    check("深度·evidence 等宽渲染", page.evaluate(
        "document.querySelectorAll('#ndDeepBody .nd-evlist code').length") >= 4)
    check("深度·命令折叠存在", page.evaluate(
        "document.querySelectorAll('#ndDeepBody .nd-cmds').length") == 2)
    check("深度·按钮复位可重试", page.evaluate("document.getElementById('ndDeepBtn').disabled") is False)
    check("深度·核验时间本地化（YYYY-MM-DD HH:MM）", page.evaluate(
        r"(function(){ var t = document.getElementById('ndDeepBody').textContent;"
        r" return /\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(t); })()"))
    page.evaluate(r"(function(){ ndRenderDeep({ status:'done', steps:[],"
        r" verdict:{ conclusion:'insufficient_evidence', ip:'172.17.90.215', mac:'AA:BB:CC:DD:EE:FF',"
        r" checked_at: 1725955200, sources:{ gateway_arp:'skipped', admission:'failed' } } }, false); })()")
    check("深度·证据不足降级提示（非故障语义，契约补充）",
          "证据不足（各步骤原因见时间线）" in page.inner_text("#ndDeepBody"),
          page.inner_text("#ndDeepBody")[:120])
    page.evaluate("window.__stubDeepStartBusy = true;")
    page.evaluate("ndStartDeep()")
    page.wait_for_timeout(500)
    check("深度·429 并发满提示（busy）", "任务忙" in page.inner_text("#ndDeepTip")
          and page.evaluate("document.getElementById('ndDeepBtn').disabled") is False,
          page.inner_text("#ndDeepTip"))
    page.evaluate("window.__stubDeepStartBusy = false; window.__stubDeepPollCount = 0;")

    # ③ 连通性
    page.click("#ndPingBtn")
    page.wait_for_timeout(900)
    pg = page.inner_text("#ndNodesBody")
    check("连通·延迟填充", "1 ms" in pg and "3 ms" in pg, pg[:80])
    check("连通·丢包填充", "0%" in pg and "100%" in pg, pg[:80])
    check("连通·DNS 解析说明", "解析 baidu.com" in pg, pg[:80])
    check("连通·NTP 偏移需校时", "需校时" in pg and "1062.0" in pg, pg[:80])
    check("连通·NTP 徽章需关注", page.evaluate(
        "var r = document.querySelector('tr[data-ndkey=\\\"ntp\\\"]'); r && r.cells[3].innerHTML.indexOf('需关注') > -1"))
    check("连通·center 未连接置灰", "未连接中心" in pg, pg[:80])
    check("连通·摘要", "7 正常 / 2 异常" in page.inner_text("#ndPingSummary"),
          page.inner_text("#ndPingSummary"))
    # TCP/UDP 协议检测（2026-09-15 增强）
    check("Node·TCP 方式列文案", "TCP 连接" in pg, pg[:80])
    check("Node·UDP 方式列文案", "UDP 探测" in pg, pg[:80])
    check("Node·TCP 结果渲染（连接成功+耗时）", "连接成功" in pg and "35 ms" in pg, pg[-120:])
    check("Node·UDP 无响应如实分级", "无响应" in pg and "无响应不等于不通" in pg, pg[-120:])
    check("Node·UDP 徽章中性态", page.evaluate(
        "var r = document.querySelector('tr[data-ndkey=\\\"udp-generic\\\"]');"
        " r && r.cells[3].innerHTML.indexOf('无响应') > -1"))
    check("Node·TCP 徽章正常态", page.evaluate(
        "var r = document.querySelector('tr[data-ndkey=\\\"tcp-sample\\\"]');"
        " r && r.cells[3].innerHTML.indexOf('正常') > -1"))

    # 节点自主增删（添加 / uiConfirm 删除 / 空态恢复引导）
    page.evaluate("ndToggleNodeForm()")
    check("Node·表单展开", page.evaluate(
        "document.getElementById('ndNodeForm').style.display") == "flex")
    page.fill("#ndNodeName", "测试节点")
    page.fill("#ndNodeTarget", "baidu.com")
    page.select_option("#ndNodeMethod", "tcp")
    check("Node·TCP 选中后端口必填展示", page.evaluate(
        "document.getElementById('ndNodePort').style.display") != "none")
    page.fill("#ndNodePort", "443")
    page.evaluate("ndAddNodeSubmit()")
    page.wait_for_timeout(400)
    check("Node·添加后行出现", "测试节点" in page.inner_text("#ndNodesBody"))
    check("Node·添加走 config SET", page.evaluate(
        "(function(){ var cs = window.__stubConfigCalls || [];"
        " for (var i = cs.length - 1; i >= 0; i--) { if (cs[i].indexOf('nodes_json') >= 0) {"
        " return decodeURIComponent(cs[i]).indexOf('测试节点') >= 0; } } return false; })()"))
    page.evaluate("ndDelNode('tcp-sample')")
    page.wait_for_timeout(300)
    check("Node·删除走 uiConfirm danger", page.evaluate(
        "(function(){ var m = document.querySelector('.ui-confirm-mask');"
        " return !!m && m.textContent.indexOf('删除节点确认') >= 0"
        " && m.textContent.indexOf('TCP 端口检测（示例）') >= 0"
        " && !!m.querySelector('.ui-confirm-ok-danger'); })()"))
    page.evaluate("(function(){ var b = document.querySelector('.ui-confirm-ok-danger'); if (b) b.click(); })()")
    page.wait_for_timeout(400)
    check("Node·确认后行消失", page.evaluate(
        "document.getElementById('ndNodesBody').textContent.indexOf('TCP 端口检测（示例）') < 0"))
    page.evaluate("(function(){ ndState.nodes = []; ndRenderNodes(); })()")
    check("Node·空态恢复引导", "恢复默认节点" in page.inner_text("#ndNodesBody"))
    page.evaluate("ndResetNodes()")
    page.wait_for_timeout(400)
    check("Node·恢复走 nodes_reset", page.evaluate(
        "(function(){ var cs = window.__stubConfigCalls || [];"
        " return cs.length && cs[cs.length - 1].indexOf('nodes_reset=1') >= 0; })()"))
    check("Node·恢复后默认表回填", "TCP 端口检测（示例）" in page.inner_text("#ndNodesBody"))

    # ④ 路由追踪
    check("文案·路由追踪 placeholder 固定短句", page.evaluate(
        "document.getElementById('ndTracertTarget').placeholder") == "请输入目标域名或IP")
    page.fill("#ndTracertTarget", "172.17.254.2")
    page.click("#ndTracertBtn")
    page.wait_for_timeout(900)
    tr = page.inner_text("#ndTracertBody")
    check("追踪·逐跳", "core-sw" in tr and "172.17.254.1" in tr, tr[:80])
    check("追踪·区域标注", "核心交换层" in tr and "数据中心区" in tr, tr[:80])
    check("追踪·超时跳", "请求超时" in tr, tr[:80])
    check("追踪·知识库计数", "知识库节点 3 条" in page.inner_text("#ndTracertSummary"),
          page.inner_text("#ndTracertSummary"))

    # ④.a 空目标默认规则（2026-09-11 用户要求：已连→中心；未连→baidu.com；透明回填）
    check("Tracert自动·非空原值不动", page.evaluate(
        "(function(){ var p = window.__stubLastTracertStart || '';"
        " return p.indexOf('target=172.17.254.2') >= 0; })()"),
        page.evaluate("window.__stubLastTracertStart"))
    page.evaluate("(function(){ document.getElementById('ndTracertTarget').value = '';"
        " window.__stubLastTracertStart = ''; ndStartTracert(); })()")
    page.wait_for_timeout(700)
    check("Tracert自动·空+已连→中心IP且回填", page.evaluate(
        "(function(){ var p = window.__stubLastTracertStart || '';"
        " var v = document.getElementById('ndTracertTarget').value;"
        " return p.indexOf('target=127.0.0.1') >= 0 && v === '127.0.0.1'; })()"),
        (page.evaluate("window.__stubLastTracertStart") or "")[:120])
    check("Tracert自动·summary 显示自动来源", "（自动：中心服务器）" in page.inner_text("#ndTracertSummary"),
          page.inner_text("#ndTracertSummary")[:100])
    page.evaluate("(function(){ window.__stubUplink.uplink.state = 'connecting';"
        " document.getElementById('ndTracertTarget').value = '';"
        " window.__stubLastTracertStart = ''; ndStartTracert(); })()")
    page.wait_for_timeout(700)
    check("Tracert自动·空+未连→baidu.com", page.evaluate(
        "(function(){ var p = window.__stubLastTracertStart || '';"
        " return p.indexOf('target=baidu.com') >= 0"
        " && document.getElementById('ndTracertTarget').value === 'baidu.com'; })()"),
        page.evaluate("window.__stubLastTracertStart"))
    check("Tracert自动·未连 summary 来源", "（自动：baidu.com）" in page.inner_text("#ndTracertSummary"))
    page.evaluate("(function(){ window.__stubUplink.uplink.state = 'connected';"
        " document.getElementById('ndTracertTarget').value = ''; })()")
    page.fill("#ndTracertTarget", "example.com")
    page.evaluate("window.__stubLastTracertStart = ''; ndStartTracert()")
    page.wait_for_timeout(700)
    check("Tracert自动·非空不覆盖且发起原值", page.evaluate(
        "(function(){ var p = window.__stubLastTracertStart || '';"
        " return p.indexOf('target=example.com') >= 0"
        " && document.getElementById('ndTracertTarget').value === 'example.com'; })()"))

    # ④.b 路由追踪 AI 分析（2026-09-11 对接平台 routetrace 分支，长超时通道）
    assert_functions(page, ["ndStartTraceAi", "ndApplyTraceAiGate", "ndLongPost"], "nd")
    page.evaluate("(function(){ if (ndState.uplinkPollTimer) { clearInterval(ndState.uplinkPollTimer); ndState.uplinkPollTimer = null; } })()")
    check("TraceAI·已连接且结果渲染后显示", page.evaluate(
        "document.getElementById('ndTraceAiBtn').style.display") != "none")
    page.evaluate("(function(){ window.__stubUplink.uplink.state = 'connecting'; ndLoadUplink(); })()")
    page.wait_for_function("(function(){ return !ndConnected(); })()", timeout=4000)
    diag = page.evaluate("(function(){ return { d: document.getElementById('ndTraceAiBtn').style.display,"
                          " c: ndConnected(), last: !!ndState.lastTracert }; })()")
    check("TraceAI·未连接隐藏（非置灰）", diag["d"] == "none" and diag["c"] is False, diag)
    page.evaluate("(function(){ window.__stubUplink.uplink.state = 'connected'; ndLoadUplink(); })()")
    page.wait_for_function("(function(){ return ndConnected(); })()", timeout=4000)
    check("TraceAI·连接恢复重显", page.evaluate(
        "document.getElementById('ndTraceAiBtn').style.display") != "none")
    page.evaluate("ndStartTraceAi()")
    page.wait_for_function(
        "document.getElementById('ndTraceAiBody').textContent.indexOf('RT-7') >= 0", timeout=5000)
    check("TraceAI·点击携带 target/hops_json", page.evaluate(
        "(function(){ var p = window.__stubLastTraceAi || '';"
        " return p.indexOf('target=') >= 0 && p.indexOf('hops_json=%5B') >= 0; })()"),
        page.evaluate("window.__stubLastTraceAi"))
    check("TraceAI·结果渲染 model/#id", "路由路径分析 OK" in page.inner_text("#ndTraceAiBody")
          and "[stub-trace]" in page.inner_text("#ndTraceAiBody")
          and "#RT-7" in page.inner_text("#ndTraceAiBody"))
    page.evaluate("window.__apiFetchTimeout = 400; window.__stubTraceAiDelay = 900;")
    page.evaluate("ndStartTraceAi()")
    check("TraceAI·分析中预期提示", "30~60" in page.inner_text("#ndTraceAiTip"))
    page.wait_for_function(
        "document.getElementById('ndTraceAiTip').textContent.indexOf('分析完成') >= 0", timeout=6000)
    check("TraceAI·慢响应(900ms>通用层400ms)不被掐断", "路由路径分析 OK" in page.inner_text("#ndTraceAiBody"))
    page.evaluate("window.__apiFetchTimeout = undefined; window.__stubTraceAiDelay = 0;")
    page.evaluate("window.__stubTraceAiFail = true; ndStartTraceAi()")
    page.wait_for_timeout(500)
    check("TraceAI·失败态提示可重试", "分析失败" in page.inner_text("#ndTraceAiTip")
          and page.evaluate("document.getElementById('ndTraceAiBtn').disabled") is False)
    page.evaluate("window.__stubTraceAiFail = false; ndStartTraceAi()")
    page.wait_for_function(
        "document.getElementById('ndTraceAiBody').textContent.indexOf('RT-7') >= 0", timeout=5000)
    check("TraceAI·重试成功恢复渲染", "路由路径分析 OK" in page.inner_text("#ndTraceAiBody"))
    # 无结果时隐藏：临时置空断言后立即恢复（lastTracert 是 AI 采集 net_tracert 数据源，不可留空污染）
    page.evaluate("(function(){ var keep = ndState.lastTracert; ndState.lastTracert = null;"
        " ndApplyTraceAiGate(); window.__traceHiddenOk ="
        " document.getElementById('ndTraceAiBtn').style.display === 'none';"
        " ndState.lastTracert = keep; ndApplyTraceAiGate(); })()")
    check("TraceAI·无结果时隐藏", page.evaluate("window.__traceHiddenOk") is True)

    # ⑤.a 包长档位分档重构（2026-09-10 用户要求：单逗号串框 → 4 档独立文本框）
    assert_functions(page, ["ndReadSizes", "ndSizesEcho"], "nd")
    check("Sizes·4 框默认回显", page.evaluate(
        "(function(){ var g=function(i){return document.getElementById('ndStressSize'+i).value;};"
        " return g(1)==='64' && g(2)==='256' && g(3)==='1024' && g(4)==='4096'; })()"))
    page.evaluate("(function(){ document.getElementById('ndStressSize1').value='128';"
        " document.getElementById('ndStressSize2').value='';"
        " document.getElementById('ndStressSize3').value='2048';"
        " document.getElementById('ndStressSize4').value='2048';"
        " window.__stubStressStartCount=0; ndStartStress(); })()")
    page.wait_for_timeout(700)
    check("Sizes·空框跳过+去重保序拼接", page.evaluate(
        "(function(){ var p=window.__stubLastStressStart||'';"
        " return p.indexOf('sizes=128%2C2048') >= 0 || p.indexOf('sizes=128,2048') >= 0; })()"),
        page.evaluate("window.__stubLastStressStart"))
    check("Sizes·发起成功后记忆存储", page.evaluate(
        "(function(){ try { return localStorage.getItem('ndStressSizes')==='128,2048'; }"
        " catch(e){ return false; } })()"))
    # 重开回显：boot 即调 ndSizesEcho()——清框后直调同函数等价验证（reload 会重置 AI 预采集时序）
    page.evaluate("(function(){ document.getElementById('ndStressSize1').value='';"
        " document.getElementById('ndStressSize2').value=''; ndSizesEcho(); })()")
    check("Sizes·重开回显记忆值", page.evaluate(
        "(function(){ var g=function(i){return document.getElementById('ndStressSize'+i).value;};"
        " return g(1)==='128' && g(2)==='2048'; })()"))
    page.evaluate("(function(){ document.getElementById('ndStressSize1').value='0';"
        " window.__stubStressStartCount=0; ndStartStress(); })()")
    page.wait_for_timeout(300)
    check("Sizes·0 超范围红字且不发起", "超出范围" in page.inner_text("#ndStressSizesErr")
          and page.evaluate("window.__stubStressStartCount") == 0,
          page.inner_text("#ndStressSizesErr"))
    page.evaluate("(function(){ document.getElementById('ndStressSize1').value='abc'; ndStartStress(); })()")
    page.wait_for_timeout(300)
    check("Sizes·非正整数红字", "不是正整数" in page.inner_text("#ndStressSizesErr"))
    page.evaluate("(function(){ document.getElementById('ndStressSize1').value='64';"
        " document.getElementById('ndStressSize2').value='256';"
        " document.getElementById('ndStressSize3').value='1024';"
        " document.getElementById('ndStressSize4').value='4096';"
        " try { localStorage.removeItem('ndStressSizes'); } catch(e){} })()")

    # ⑤.b iperf3 时长档位扩充 + 自定义（2026-09-10 用户要求，上限 300 硬约束）
    assert_functions(page, ["ndOnDurChange", "ndReadDur", "ndDurEcho"], "nd")
    check("Dur·档位 8 项默认 10", page.evaluate(
        "(function(){ var s=document.getElementById('ndStressDur');"
        " return s.options.length===8 && s.value==='10'; })()"))
    page.evaluate("(function(){ document.getElementById('ndStressDur').value='custom'; ndOnDurChange();"
        " document.getElementById('ndStressDurCustom').value='0';"
        " window.__stubStressStartCount=0; ndStartStress(); })()")
    page.wait_for_timeout(300)
    check("Dur·自定义框展开+非法红字不发起", page.evaluate(
        "document.getElementById('ndStressDurCustom').style.display") == "inline-block"
        and "正整数" in page.inner_text("#ndStressDurErr")
        and page.evaluate("window.__stubStressStartCount") == 0,
        page.inner_text("#ndStressDurErr"))
    page.evaluate("(function(){ document.getElementById('ndStressDurCustom').value='400'; ndStartStress(); })()")
    page.wait_for_timeout(700)
    check("Dur·超 300 截断发起+提示", page.evaluate(
        "(function(){ var p=window.__stubLastStressStart||'';"
        " return p.indexOf('duration_sec=300') >= 0; })()")
        and "已按 300 秒执行" in page.inner_text("#ndStressDurErr"),
        (page.evaluate("window.__stubLastStressStart") or "")[:120])
    check("Dur·custom 记忆 custom:300", page.evaluate(
        "(function(){ try { return localStorage.getItem('ndStressDur')==='custom:300'; }"
        " catch(e){ return false; } })()"))
    page.evaluate("(function(){ document.getElementById('ndStressDur').value='120'; ndOnDurChange();"
        " document.getElementById('ndStressDurCustom').value=''; ndStartStress(); })()")
    page.wait_for_timeout(700)
    check("Dur·固定档隐藏框+发起 120", page.evaluate(
        "document.getElementById('ndStressDurCustom').style.display") == "none"
        and "duration_sec=120" in (page.evaluate("window.__stubLastStressStart") or ""))
    page.evaluate("(function(){ try { localStorage.setItem('ndStressDur','custom:180'); } catch(e){}"
        " document.getElementById('ndStressDur').value='10';"
        " document.getElementById('ndStressDurCustom').value=''; ndDurEcho(); })()")
    check("Dur·回显自定义=custom+180", page.evaluate(
        "(function(){ return document.getElementById('ndStressDur').value==='custom'"
        " && document.getElementById('ndStressDurCustom').value==='180'; })()"))
    page.evaluate("(function(){ try { localStorage.removeItem('ndStressDur'); } catch(e){}"
        " document.getElementById('ndStressDur').value='10'; ndOnDurChange(); })()")

    # ⑤.c 压测进度条（2026-09-10 用户要求：6 段加权 + 斜纹流动 + 三态终态，纯前端）
    assert_functions(page, ["ndStressProgressInit", "ndStressProgressTick", "ndStressProgressEnd"], "nd")
    page.evaluate("(function(){ ndStressProgressInit(); })()")
    page.evaluate("ndStressProgressTick({ stage:'ping', done:0, total:6, current:'ping -l 64' })")
    w1 = page.evaluate("parseFloat(document.querySelector('#ndStressProg .nd-progress-fill').style.width)")
    check("进度·条出现且阶段名正确", page.evaluate("!!document.getElementById('ndStressProg')")
          and page.inner_text("#ndStressProgName") == "多档 ping 64 字节",
          page.inner_text("#ndStressProgName"))
    page.wait_for_timeout(1300)
    page.evaluate("ndStressProgressTick({ stage:'ping', done:0, total:6, current:'ping -l 64' })")
    w2 = page.evaluate("parseFloat(document.querySelector('#ndStressProg .nd-progress-fill').style.width)")
    check("进度·宽度随时间推进", w2 > w1, (w1, w2))
    page.evaluate("ndStressProgressTick({ stage:'iperf-tcp', done:4, total:6, current:'iperf3 TCP' })")
    check("进度·阶段推进段名与百分比", page.inner_text("#ndStressProgName") == "iperf3 TCP"
          and page.inner_text("#ndStressProgPct").endswith("%"),
          page.inner_text("#ndStressProgName"))
    page.evaluate("ndStressProgressEnd('cancelled', '')")
    check("进度·取消态灰条已取消", page.evaluate(
        "document.getElementById('ndStressProg').classList.contains('cancelled')")
        and "已取消" in page.inner_text("#ndStressProgName"))
    page.evaluate("ndStressProgressEnd('failed', 'stub err')")
    check("进度·失败态红字", page.evaluate(
        "document.getElementById('ndStressProg').classList.contains('failed')")
        and "压测失败" in page.inner_text("#ndStressProgName"))
    page.evaluate("ndStressProgressEnd('done', '')")
    check("进度·完成态满条绿字一闪", page.evaluate(
        "(function(){ var p = document.getElementById('ndStressProg');"
        " return p.classList.contains('done') && p.classList.contains('flash')"
        " && parseFloat(p.querySelector('.nd-progress-fill').style.width) === 100; })()"))

    # ⑤ 网络压测
    page.click("#ndStressBtn")
    page.wait_for_timeout(900)
    st = page.inner_text("#ndStressBody")
    check("压测·综合结论", "优良" in page.inner_text("#ndStressBody"), st[:80])
    check("压测·包长表", "64 B" in st and "256 B" in st, st[:80])
    check("压测·TCP intervals", "intervals 最大 952.1" in st and "941.2" in st, st[:80])
    check("压测·UDP 抖动丢包", "抖动 0.155" in st and "100 Mbits/sec" in st, st[:80])
    page.click("#ndStressExportBtn")
    page.wait_for_timeout(500)
    check("压测·导出路径", "NetStress_20260909_1000.html" in page.inner_text("#ndStressSummary"),
          page.inner_text("#ndStressSummary"))
    check("压测·打开位置可见",
          page.evaluate("document.getElementById('ndStressOpenBtn').style.display") == "inline-block")

    # ⑦ 接口挂起恢复（2026-09-09 主页冻结缺陷回归门禁：超时保护 + 失败可见 + 自愈恢复）
    page.evaluate("window.__apiFetchTimeout = 600; window.__stubHang = true;")
    page.evaluate("ndLoadUplink()")
    page.wait_for_timeout(1400)
    check("挂起·netdoctor 失败态可见（不再静默永冻）",
          "状态刷新失败" in page.inner_text("#ndUplinkBody"), page.inner_text("#ndUplinkBody")[:80])
    page.evaluate("window.__stubHang = false")
    page.evaluate("ndLoadUplink()")
    page.wait_for_timeout(600)
    check("挂起·netdoctor 自愈恢复已连接", "已连接" in page.inner_text("#ndUplinkBody")
          and "状态刷新失败" not in page.inner_text("#ndUplinkBody"))

    # ⑧ AI 智能诊断（第六模块 · 2026-09-10 Bug0 修复 + 七项优化回归）
    page.wait_for_timeout(2200)   # 数据源预采集（init 后 2s 触发）

    # 优化0：中心 gate 判定 Bug——首帧 connecting 置灰后，心跳在线重进菜单必自愈为可用态
    page.evaluate("(function(){ window.__stubUplink.uplink.state='connecting'; initNetDoctorTab(); })()")
    page.wait_for_timeout(600)
    check("Bug0·首帧 connecting 置灰", page.evaluate(
        "document.getElementById('ndAiBtn').disabled") is True)
    check("Bug0·gate 可见", page.evaluate(
        "document.getElementById('ndAiGate').style.display") == "block")
    page.evaluate("(function(){ window.__stubUplink.uplink.state='connected'; initNetDoctorTab(); })()")
    page.wait_for_timeout(600)
    check("Bug0·心跳在线重进菜单必为可用态", page.evaluate(
        "document.getElementById('ndAiBtn').disabled") is False
        and page.evaluate("document.getElementById('ndAiGate').style.display") == "none")

    # 优化1：AI 卡位于模块最上
    check("Opt1·AI 卡位于 tab 最上", page.evaluate(
        "(function(){ var c = document.querySelector('#tab-netdoctor .section-card');"
        " return !!(c && c.querySelector('#ndAiBtn') && c.textContent.indexOf('AI 智能诊断') >= 0); })()"))

    # 优化2/3：placeholder 与 textarea 留白
    check("Opt2·问题概述 placeholder", page.get_attribute(
        "#ndAiIssue", "placeholder") == "请输入本机在哪个时间段出现的问题概况（如：今天上午 9-11 点频繁卡顿）")
    check("Opt3·textarea 左右内边距", page.evaluate(
        "(function(){ var s = getComputedStyle(document.getElementById('ndAiIssue'));"
        " return parseFloat(s.paddingLeft) >= 10 && parseFloat(s.paddingRight) >= 10; })()"))

    # 优化4/8：诊断日志默认收起 + 标题行展开/收起 + 探测按钮同区
    check("Opt4·诊断日志默认收起", page.evaluate(
        "document.getElementById('ndAiLogsBody').style.display") == "none")
    check("Opt8·收起时探测按钮隐藏", page.evaluate(
        "document.getElementById('ndAiRefreshBtn').style.display") == "none")
    page.click("#ndAiLogsHead")
    page.wait_for_timeout(200)
    check("Opt4·标题行点击展开", page.evaluate(
        "document.getElementById('ndAiLogsBody').style.display") == "block")
    check("Opt8·探测按钮在诊断日志行右侧且展开后可见", page.evaluate(
        "(function(){ var b = document.getElementById('ndAiRefreshBtn');"
        " return !!(b && b.closest('#ndAiLogsHead') && b.style.display === 'inline-block'); })()"))
    check("Opt4·10 行（5 单选+组头+4 子项）", page.evaluate(
        "document.querySelectorAll('#ndAiSourcesBody tr').length") == 10)
    check("Opt4·网络组 4 子项", page.evaluate(
        "document.querySelectorAll('#ndAiSourcesBody tr.nd-ai-sub').length") == 4)
    check("Opt4·启用项默认全选", page.evaluate(
        "Array.prototype.every.call(document.querySelectorAll("
        "'#ndAiSourcesBody input[type=checkbox]:not(:disabled)'), function(c){ return c.checked; })"))
    # 证据体量透明化（折叠态标题行汇总；预采集 9 源中 7 源可用——perf 两类桩不可用+快照可用）
    agg_txt = page.evaluate("document.getElementById('ndAiLogsAgg').textContent")
    check("Agg·折叠态汇总已采集 N/9 源·共 X KB", "已采集 7/9 源" in agg_txt and "KB" in agg_txt, agg_txt)

    # 优化7：时间范围选择（system_log/net_conn 行内下拉，实时/记录类无）
    check("Opt7·实时与记录类无时间选择", page.evaluate(
        "document.getElementById('ndAiTime-hwinfo') === null"
        " && document.getElementById('ndAiTime-os_info') === null"
        " && document.getElementById('ndAiTime-perf_analysis') === null"
        " && document.getElementById('ndAiTime-perf_stress') === null"))
    check("Opt7·system_log/net_conn 有时间选择", page.evaluate(
        "!!document.getElementById('ndAiTime-system_log') && !!document.getElementById('ndAiTime-net_conn')"))
    page.select_option("#ndAiTime-system_log", "1h")
    page.wait_for_timeout(700)
    check("Opt7·system_log 范围参数 hours=1", "hours=1" in (page.evaluate("window.__stubLastLogPath") or ""),
          page.evaluate("window.__stubLastLogPath"))
    check("Opt7·体量提示随范围变化", "近 1 小时" in page.inner_text("#ndAiSt-system_log"),
          page.inner_text("#ndAiSt-system_log"))
    page.select_option("#ndAiTime-net_conn", "3d")
    page.wait_for_timeout(700)
    check("Opt7·连通性历史范围参数 hours=72",
          "hours=72" in (page.evaluate("window.__stubLastHistoryPath") or ""),
          page.evaluate("window.__stubLastHistoryPath"))
    page.select_option("#ndAiTime-system_log", "custom")
    page.wait_for_timeout(200)
    check("Opt7·自定义起止输入展开", page.evaluate(
        "document.getElementById('ndAiTimeCustom-system_log').style.display") == "flex")
    page.fill("#ndAiTimeStart-system_log", "2026-09-09T08:00")
    page.fill("#ndAiTimeEnd-system_log", "2026-09-09T12:00")
    page.dispatch_event("#ndAiTimeEnd-system_log", "change")
    page.wait_for_timeout(700)
    lp = page.evaluate("window.__stubLastLogPath") or ""
    check("Opt7·自定义起止传参", "start=" in lp and "end=" in lp and "2026-09-09" in lp, lp[:140])
    check("Opt7·自定义范围体量提示", "自定义范围" in page.inner_text("#ndAiSt-system_log"),
          page.inner_text("#ndAiSt-system_log"))

    # 中心依赖联动（未连置灰 + 压测子项禁勾标注 + 组头半选）
    page.evaluate("(function(){ window.__stubUplink.uplink.state='disabled'; ndLoadUplink(); })()")
    page.wait_for_timeout(400)
    check("AI·未连中心置灰联动", page.evaluate(
        "document.getElementById('ndAiBtn').disabled") is True)
    check("AI·gate 显示", page.evaluate(
        "document.getElementById('ndAiGate').style.display") == "block")
    check("Opt4·压测子项禁勾并标注需连接中心", page.evaluate(
        "document.getElementById('ndAiChk-net_stress').disabled") is True
        and "需连接中心" in page.inner_text("#ndAiSt-net_stress"))
    check("Opt4·组头反映启用项（压测禁用后其余全选→组头选中）", page.evaluate(
        "document.getElementById('ndAiChk-netgroup').checked") is True
        and page.evaluate("document.getElementById('ndAiChk-netgroup').indeterminate") is False)
    page.evaluate("(function(){ window.__stubUplink.uplink.state='connected'; ndLoadUplink(); })()")
    page.wait_for_timeout(700)
    check("AI·恢复连接解禁", page.evaluate(
        "document.getElementById('ndAiBtn').disabled") is False
        and page.evaluate("document.getElementById('ndAiChk-net_stress').disabled") is False)

    # 组头主从联动
    page.evaluate("ndAiNetGroupChange(false)")
    check("Opt4·组头取消→子项全取消", page.evaluate(
        "(function(){ var ks=['net_conn','net_tracert','net_stress','net_perf_snapshot'],ok=true;"
        " for(var i=0;i<4;i++){ok=ok&&!document.getElementById('ndAiChk-'+ks[i]).checked;} return ok; })()"))
    page.evaluate("ndAiSyncNetGroup()")
    check("Opt4·子项全取消→组头未选", page.evaluate(
        "document.getElementById('ndAiChk-netgroup').checked") is False)
    page.evaluate("ndAiNetGroupChange(true)")
    page.wait_for_timeout(200)
    check("Opt4·组头全选→子项全选", page.evaluate(
        "(function(){ var ks=['net_conn','net_tracert','net_stress','net_perf_snapshot'],ok=true;"
        " for(var i=0;i<4;i++){ok=ok&&document.getElementById('ndAiChk-'+ks[i]).checked;} return ok; })()"))

    # ---- 4.1.7 AI 证据增强：会话失忆回填 + 第 9 源快照 + 引导条 ----
    page.evaluate("ndState.lastTracert = null")   # 模拟重启后回填前
    page.evaluate("ndState.lastStressResult = null")
    page.evaluate("(function(){ window.__stubTracertLast = { ts: 1789000000,"
        " ts_text: '2026-09-17 10:00:00', target: '172.17.254.2', reached: true,"
        " hops: [ { hop: 1, delays: ['1ms'], ip: '172.17.90.1', timeout: false } ] }; })()")
    page.evaluate("ndAiCollectNow('net_tracert')")
    page.wait_for_timeout(500)
    check("回填·net_tracert 命中 JSONL 并回填会话态", page.evaluate(
        "(function(){ var c = ndState.aiCollect.net_tracert;"
        " return !!c && c.ok && c.note.indexOf('历史回填') >= 0"
        " && !!ndState.lastTracert && (ndState.lastTracert.hops || []).length === 1; })()"),
        page.evaluate("JSON.stringify(ndState.aiCollect.net_tracert || {})"))
    page.evaluate("(function(){ window.__stubStressLast = { ts: 1789001000,"
        " ts_text: '2026-09-17 10:10:00', center: '127.0.0.1',"
        " verdict: 'tcp 880 Mbits/sec', ping: [], iperf: [] }; })()")
    page.evaluate("ndAiCollectNow('net_stress')")
    page.wait_for_timeout(500)
    check("回填·net_stress 命中 JSONL（历史回填不需中心）", page.evaluate(
        "(function(){ var c = ndState.aiCollect.net_stress;"
        " return !!c && c.ok && c.note.indexOf('历史回填') >= 0"
        " && !!ndState.lastStressResult; })()"),
        page.evaluate("JSON.stringify({c: ndState.aiCollect.net_stress || null,"
            " stub: window.__stubStressLast || null})"))
    page.evaluate("ndAiCollectNow('net_perf_snapshot')")
    page.wait_for_timeout(600)
    check("快照·第 9 源采集含链路速率与探针", page.evaluate(
        "(function(){ var c = ndState.aiCollect.net_perf_snapshot;"
        " return !!c && c.ok && c.note.indexOf('avg 2ms') >= 0"
        " && (window.__stubSnapshotCalls || 0) >= 1; })()"),
        page.evaluate("JSON.stringify(ndState.aiCollect.net_perf_snapshot || {})"))
    check("快照·预填清单含第 9 源勾选框", page.evaluate(
        "!!document.getElementById('ndAiChk-net_perf_snapshot')"))
    page.fill("#ndAiIssue", "这台电脑网速很慢，是不是网络性能有问题")
    page.evaluate("ndAiGuideHint(document.getElementById('ndAiIssue').value)")
    page.wait_for_timeout(200)
    check("引导·性能类+快照可用 → 不出提示条（快照已 ok）", page.evaluate(
        "document.getElementById('ndAiGuide') === null"))
    page.evaluate("(function(){ ndState.aiCollect.net_perf_snapshot = { ts: 1, ok: false };"
        " ndState.aiCollect.net_conn = { ts: 1, ok: false }; })()")
    page.evaluate("ndAiGuideHint(document.getElementById('ndAiIssue').value)")
    page.wait_for_timeout(200)
    check("引导·性能类且证据双缺 → 提示条出现", page.evaluate(
        "(function(){ var g = document.getElementById('ndAiGuide');"
        " return !!g && g.textContent.indexOf('连通性测试') >= 0"
        " && g.textContent.indexOf('不受影响') >= 0; })()"))
    page.fill("#ndAiIssue", "")   # 复位空概述（后续空概述拒绝用例依赖）
    page.evaluate("ndAiCountIssue()")
    # 恢复会话态与桩（回填污染清理：Complete 补采用例依赖原值）
    page.evaluate("(function(){ window.__stubTracertLast = undefined;"
        " window.__stubStressLast = undefined;"
        " ndState.lastTracert = window.__stubTracert;"
        " ndState.lastStressResult = window.__stubStress;"
        " ndState.aiCollect.net_perf_snapshot = { ts: Date.now(), ok: true,"
        "   data: { success: true }, note: '实时' }; })()")
    page.wait_for_timeout(200)

    # 提交流：空概述拒绝 → 补采模态（直接提交）→ 结构化渲染 → 历史 → 失败态
    page.click("#ndAiBtn")
    page.wait_for_timeout(300)
    check("AI·空概述拒绝", "请先填写问题概述" in page.inner_text("#ndAiSummary"))
    page.fill("#ndAiIssue", "网络卡顿，网页打开慢，请结合日志诊断")
    check("AI·字数计数", page.inner_text("#ndAiIssueCount") == "18")
    # 缺失源补采模态（应用内自绘，替代原生 confirm）：列未采集源，可就地补采或直接提交
    page.click("#ndAiBtn")
    page.wait_for_function(
        "(function(){ var o = document.getElementById('ndAiCompleteOverlay'); return !!o && o.style.display === 'flex'; })()", timeout=5000)
    check("Modal·缺失源模态列出未采集源", page.evaluate(
        "(function(){ var t = document.getElementById('ndAiCompleteList').textContent;"
        " return t.indexOf('性能分析记录') >= 0 && t.indexOf('性能压测记录') >= 0; })()"))
    check("Fix·模态初始态为待采集（无排队假象）", page.evaluate(
        "(function(){ var t = document.getElementById('ndAiCompleteList').textContent;"
        " return t.indexOf('待采集') >= 0 && t.indexOf('排队') < 0; })()"))
    page.evaluate("document.getElementById('ndAiCompleteSkip').click()")
    check("Fix·Skip 发起瞬间提示与三按钮禁用", page.evaluate(
        "(function(){ var tip = document.getElementById('ndAiCompleteTip');"
        " var g = document.getElementById('ndAiCompleteGo'),"
        " s = document.getElementById('ndAiCompleteSkip'),"
        " c = document.getElementById('ndAiCompleteCancel');"
        " return !!tip && tip.textContent.indexOf('诊断提交中') >= 0"
        " && g.disabled && s.disabled && c.disabled; })()"))
    page.wait_for_function("document.getElementById('ndAiBody').textContent.indexOf('A-STUB-1') >= 0",
                           timeout=8000)
    check("Opt5·结构化三段渲染", page.evaluate(
        "!!document.querySelector('#ndAiBody .nd-ai-sec-cause')")
        and "网络链路正常" in page.inner_text("#ndAiBody .nd-ai-sec-cause"))
    check("Opt5·处理意见段分卡", "校时" in page.inner_text("#ndAiBody .nd-ai-sec-advice"))
    check("Opt5·风险提示段分卡", page.evaluate(
        "!!document.querySelector('#ndAiBody .nd-ai-sec-risk')"))
    check("Opt5·元信息徽章化", page.evaluate(
        "(function(){ var m = document.querySelector('#ndAiBody .nd-ai-meta');"
        " return !!(m && m.querySelectorAll('.nd-badge').length >= 2"
        " && m.textContent.indexOf('A-STUB-1') >= 0"
        " && m.textContent.indexOf('12345') >= 0); })()"))
    check("AI·请求体 logs 组装（单类+网络子项）", page.evaluate(
        "window.__stubLastAiReq && window.__stubLastAiReq.issue && window.__stubLastAiReq.logs"
        " && !!window.__stubLastAiReq.logs.hwinfo && !!window.__stubLastAiReq.logs.system_log"
        " && !!window.__stubLastAiReq.logs.net_conn"
        " && !!window.__stubLastAiReq.logs.net_tracert && !!window.__stubLastAiReq.logs.net_stress"))
    check("AI·logs 不可用类剔除", page.evaluate(
        "window.__stubLastAiReq && !window.__stubLastAiReq.logs.perf_analysis"
        " && !window.__stubLastAiReq.logs.perf_stress"))
    # 证据瘦身（占位符替换 + 200 字符截断）
    check("Slim·占位符描述替换为描述缺失", page.evaluate(
        "window.__stubLastAiReq.logs.system_log.indexOf('(描述缺失)') >= 0"
        " && window.__stubLastAiReq.logs.system_log.indexOf('<The description') < 0"))
    check("Slim·单条描述 200 字符截断", page.evaluate(
        "window.__stubLastAiReq.logs.system_log.indexOf('LONGA') >= 0"
        " && window.__stubLastAiReq.logs.system_log.indexOf('LONGEND') < 0"))

    # 历史文件持久化 + 手动删除（2026-09-11 用户要求）+ model 徽章删除
    assert_functions(page, ["ndAiLoadHistory", "ndAiDeleteHistory", "ndAiClearHistory"], "nd")
    page.evaluate("ndAiLoadHistory()")
    page.wait_for_timeout(400)
    check("Hist·提交后历史列表渲染（含删除按钮）", page.evaluate(
        "(function(){ var t = document.getElementById('ndAiHistoryBody').textContent;"
        " return t.indexOf('A-STUB-1') >= 0 && t.indexOf('删除') >= 0; })()"))
    check("Hist·append 生效（桩文件态含记录）", page.evaluate(
        "(function(){ var h = window.__stubAiHist || [];"
        " return h.length >= 1 && h[0].analysis_id === 'A-STUB-1'; })()"))
    page.evaluate("(function(){ ndState.aiHistory = []; ndAiLoadHistory(); })()")
    page.wait_for_timeout(400)
    check("Hist·模拟重启（清内存重读文件）后仍渲染", "A-STUB-1" in page.inner_text("#ndAiHistoryBody"))
    # Markdown 修饰符清洗
    check("Clean·** 与反引号剥离", page.evaluate(
        "document.getElementById('ndAiBody').textContent.indexOf('**') < 0"
        " && document.getElementById('ndAiBody').textContent.indexOf('`') < 0"))
    check("AI·历史落档", "网络卡顿" in page.inner_text("#ndAiHistoryBody")
          and "A-STUB-1" in page.inner_text("#ndAiHistoryBody"))
    page.evaluate("ndAiShowHistory(0)")
    page.wait_for_timeout(200)
    check("AI·历史回看结构化", "网络链路正常" in page.inner_text("#ndAiBody .nd-ai-sec-cause"))
    check("Meta·模型徽章已删除（回看渲染亦无，保留 analysis_id 与耗时）", page.evaluate(
        "(function(){ var m = document.querySelector('#ndAiBody .nd-ai-meta');"
        " return m.textContent.indexOf('stub-model') < 0 && m.textContent.indexOf('模型') < 0"
        " && m.textContent.indexOf('analysis_id') >= 0 && m.textContent.indexOf('耗时') >= 0; })()"))
    # 手动删除（行内两段确认）+ 清空（文件与列表同步）
    page.evaluate("(function(){ var b = document.querySelector('#ndAiHistoryBody button[data-ts]');"
        " if (b) { b.click(); } })()")
    page.wait_for_timeout(200)
    check("Hist·行内确认态（首次点变确认删除）", page.evaluate(
        "(function(){ var b = document.querySelector('#ndAiHistoryBody button[data-ts]');"
        " return b && b.textContent === '确认删除'; })()"))
    page.evaluate("(function(){ var b = document.querySelector('#ndAiHistoryBody button[data-ts]');"
        " if (b) { b.click(); } })()")
    page.wait_for_timeout(400)
    check("Hist·删除后文件与列表同步（空态）", page.evaluate(
        "(function(){ return (window.__stubAiHist || []).length === 0"
        " && document.getElementById('ndAiHistoryBody').textContent.indexOf('暂无历史') >= 0; })()"))
    check("Hist·空态无清空按钮", page.evaluate("!document.getElementById('ndAiClearBtn')"))

    # 结果区文本可选复制（2026-09-10 用户反馈）
    check("Selectable·结果与历史列表 user-select=text", page.evaluate(
        "(function(){ var a = getComputedStyle(document.getElementById('ndAiBody')).userSelect;"
        " var h = getComputedStyle(document.getElementById('ndAiHistoryBody')).userSelect;"
        " return a === 'text' && h === 'text'; })()"))
    check("Selectable·结果文本实际可选中", page.evaluate(
        "(function(){ var el = document.getElementById('ndAiBody');"
        " var r = document.createRange(); r.selectNodeContents(el);"
        " var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);"
        " var n = s.toString().length; s.removeAllRanges(); return n > 0; })()"))
    check("Selectable·全局选择行为未扩大化", page.evaluate(
        "getComputedStyle(document.body).userSelect") != "text")

    # 防重入（尾巴2：id=21/22 双击双提交白烧两次 LLM 调用）——模态直接提交后的飞行中重入
    page.evaluate("window.__stubAiCallCount = 0; window.__stubLastAiReq = null; window.__stubAiDelay = 1500;")
    page.click("#ndAiBtn")
    page.wait_for_function(
        "(function(){ var o = document.getElementById('ndAiCompleteOverlay'); return !!o && o.style.display === 'flex'; })()", timeout=5000)
    page.evaluate("document.getElementById('ndAiCompleteSkip').click()")
    page.wait_for_timeout(400)
    page.evaluate("ndAiSubmit()")   # 提交中程序化重入（Playwright click 会被 disabled 拦到超时，故直调 handler）
    page.wait_for_timeout(200)
    check("Reentry·提交中重入被忽略并提示", "请勿重复点击" in page.inner_text("#ndAiSummary"),
          page.inner_text("#ndAiSummary"))
    page.wait_for_function("window.__stubLastAiReq !== null && !ndState.aiSubmitting", timeout=10000)
    page.evaluate("window.__stubAiDelay = 0;")
    check("Reentry·重入不产生第二次请求", page.evaluate("window.__stubAiCallCount") == 1,
          page.evaluate("window.__stubAiCallCount"))

    # 结构感知预算裁剪（尾巴1：id=22 存证 JSON 完整性 FAIL——裸切割撕裂转义）
    page.evaluate("window.__stubBigLog = true; window.__stubLastAiReq = null; window.__stubAiCallCount = 0;")
    page.evaluate("ndAiCollectNow('system_log')")
    page.wait_for_timeout(600)
    page.click("#ndAiBtn")
    page.wait_for_function(
        "(function(){ var o = document.getElementById('ndAiCompleteOverlay'); return !!o && o.style.display === 'flex'; })()", timeout=5000)
    page.evaluate("document.getElementById('ndAiCompleteSkip').click()")
    page.wait_for_function("window.__stubLastAiReq !== null && !ndState.aiSubmitting", timeout=10000)
    pack_txt = page.evaluate(
        "(function(){ try { return window.__stubLastAiReq.logs.system_log; } catch (e) { return null; } })()")
    pack_ok = page.evaluate(
        "(function(){ try { var t = window.__stubLastAiReq.logs.system_log;"
        " var o = JSON.parse(t);"
        " return t.length <= 32768 && o.events && o.events.length >= 50; } catch (e) { return false; } })()")
    check("Pack·超预算裁剪后可 json.loads 且 ≤32KB", pack_ok is True
          and isinstance(pack_txt, str) and len(pack_txt) <= 32768, len(pack_txt or ""))
    check("Pack·按完整事件粒度裁剪（summary 保留不撕裂）", page.evaluate(
        "(function(){ try { var o = JSON.parse(window.__stubLastAiReq.logs.system_log);"
        " return o.events.length >= 50 && o.summary && o.summary.total === 150; } catch (e) { return false; } })()"))
    page.evaluate("window.__stubBigLog = false;")

    # 补采模态（缺失源可选补采，替代原生 confirm）
    page.evaluate(
        "(function(){ ndState.lastTracert = null; ndState.lastStressResult = null;"
        " delete ndState.aiCollect.net_tracert; delete ndState.aiCollect.net_stress;"
        " window.__stubAiCallCount = 0; window.__stubLastAiReq = null;"
        " window.__stubTracertStartCount = 0; window.__stubStressStartCount = 0; })()")
    page.click("#ndAiBtn")
    page.wait_for_function(
        "(function(){ var o = document.getElementById('ndAiCompleteOverlay'); return !!o && o.style.display === 'flex'; })()", timeout=5000)
    check("Complete·模态出现（自绘非原生）", page.evaluate(
        "document.getElementById('ndAiCompleteOverlay').style.display") == "flex")
    check("Complete·可补采勾选行/不可补采按钮行", page.evaluate(
        "(function(){ var rows = document.querySelectorAll('#ndAiCompleteList .nd-modal-row');"
        " var chks = document.querySelectorAll('#ndAiCompleteList .nd-bf-chk:not([disabled])');"
        " var rbtns = document.querySelectorAll('#ndAiCompleteList button[data-retry]');"
        " return rows.length >= 3 && chks.length >= 2 && rbtns.length >= 1; })()"))
    check("Complete·不可补采项无勾选框", page.evaluate(
        "(function(){ var rows = document.querySelectorAll('#ndAiCompleteList .nd-modal-row');"
        " for (var i = 0; i < rows.length; i++) {"
        "  if (rows[i].textContent.indexOf('性能分析记录') >= 0"
        "   && rows[i].querySelector('.nd-bf-chk')) { return false; } } return true; })()"))
    page.evaluate("document.getElementById('ndAiCompleteGo').click()")
    page.wait_for_function("window.__stubAiCallCount === 1"
                           " && document.getElementById('ndAiCompleteOverlay').style.display === 'none'",
                           timeout=15000)
    check("Complete·补采任务全跑且自动提交一次", page.evaluate(
        "window.__stubTracertStartCount") == 1
        and page.evaluate("window.__stubStressStartCount") == 1
        and page.evaluate("window.__stubAiCallCount") == 1)
    # 全链真实生效（2026-09-11 用户实测三项停在排队的修复门禁）
    check("Complete·tracert 结果回填 lastTracert（数据源行更新）", page.evaluate(
        "(function(){ var st = document.getElementById('ndAiSt-net_tracert');"
        " return !!ndState.lastTracert && !!st && st.textContent.indexOf('最近一次') >= 0; })()"),
        page.evaluate("document.getElementById('ndAiSt-net_tracert') ?"
                      " document.getElementById('ndAiSt-net_tracert').textContent : 'NO EL'"))
    check("Complete·stress 结果回填 lastStressResult", page.evaluate(
        "(function(){ var st = document.getElementById('ndAiSt-net_stress');"
        " return !!ndState.lastStressResult && !!st && st.textContent.indexOf('最近一次') >= 0; })()"))
    check("Complete·perf_stress 回采走 record-latest（落盘可跨重启）", page.evaluate(
        "(function(){ var st = document.getElementById('ndAiSt-perf_stress');"
        " var p = window.__stubLastRecordLatest || '';"
        " return p.indexOf('/api/perf/record-latest?kind=stress') === 0"
        " && !!st && st.textContent.indexOf('最近记录 #42') >= 0; })()"),
        page.evaluate("document.getElementById('ndAiSt-perf_stress') ?"
                      " document.getElementById('ndAiSt-perf_stress').textContent : 'NO EL'"))
    check("Complete·tracert 行内目标默认中心 IP 且可编辑", page.evaluate(
        "(function(){ var el = document.getElementById('ndAiBfTarget');"
        " return !!el && el.value === '127.0.0.1' && !el.disabled; })()"))
    # 取消路径：不发请求
    page.evaluate(
        "(function(){ ndState.lastTracert = null; delete ndState.aiCollect.net_tracert;"
        " window.__stubAiCallCount = 0; })()")
    page.click("#ndAiBtn")
    page.wait_for_function(
        "(function(){ var o = document.getElementById('ndAiCompleteOverlay'); return !!o && o.style.display === 'flex'; })()", timeout=5000)
    page.evaluate("document.getElementById('ndAiCompleteCancel').click()")
    page.wait_for_timeout(300)
    dbg = page.evaluate(
        "(function(){ var o = document.getElementById('ndAiCompleteOverlay');"
        " return JSON.stringify({ c: window.__stubAiCallCount,"
        " d: o ? o.style.display : 'NULL',"
        " s: document.getElementById('ndAiSummary').textContent }); })()")
    check("Complete·取消不发请求且模态关闭", page.evaluate(
        "window.__stubAiCallCount") == 0
        and page.evaluate("document.getElementById('ndAiCompleteOverlay').style.display") == "none"
        and "已取消提交" in page.inner_text("#ndAiSummary"), dbg)
    # 未连中心：net_stress 补采项禁用
    page.evaluate("(function(){ window.__stubUplink.uplink.state='disabled'; ndAiSetMode('personal');"
                  " ndState.lastTracert = null; delete ndState.aiCollect.net_tracert;"
                  " ndState.lastStressResult = null; delete ndState.aiCollect.net_stress; })()")
    page.click("#ndAiBtn")
    page.wait_for_function(
        "(function(){ var o = document.getElementById('ndAiCompleteOverlay'); return !!o && o.style.display === 'flex'; })()", timeout=5000)
    dbg2 = page.evaluate(
        "(function(){ var c = document.querySelector('#ndAiCompleteList .nd-bf-chk[data-key=net_stress]');"
        " var row = c ? c.parentNode : null;"
        " return JSON.stringify({ has: !!c, dis: c ? c.disabled : null,"
        " t: row ? row.textContent : '',"
        " conn: ndConnected() }); })()")
    check("Complete·未连中心压测项禁用且标注原因", page.evaluate(
        "(function(){ var c = document.querySelector('#ndAiCompleteList .nd-bf-chk[data-key=net_stress]');"
        " var row = c ? c.parentNode : null;"
        " return c && c.disabled && row && row.textContent.indexOf('需连接中心') >= 0; })()"), dbg2)
    page.evaluate("(function(){ window.__stubUplink.uplink.state='connected'; ndAiSetMode('enterprise'); })()")
    page.evaluate("document.getElementById('ndAiCompleteCancel').click()")
    page.wait_for_timeout(300)
    # 失败态样式区分（经补采模态直接提交）
    page.evaluate("window.__stubAiFail = true")
    page.click("#ndAiBtn")
    page.wait_for_function(
        "(function(){ var o = document.getElementById('ndAiCompleteOverlay'); return !!o && o.style.display === 'flex'; })()", timeout=5000)
    page.evaluate("document.getElementById('ndAiCompleteSkip').click()")
    page.wait_for_timeout(900)
    check("Opt5·失败态样式区分", page.evaluate(
        "!!document.querySelector('#ndAiBody .nd-ai-err')")
        and "stub ai fail" in page.inner_text("#ndAiBody .nd-ai-err"))
    check("Opt5·失败留档追溯引导", "A-STUB-ERR" in page.inner_text("#ndAiBody .nd-ai-err"))
    page.evaluate("window.__stubAiFail = false")

    # ⑨ AI 双模式（第九项）：企业版 gate 仅限企业版 / 切换记忆 / 个人版不依赖中心
    page.evaluate("(function(){ window.__stubUplink.uplink.state='disabled'; ndLoadUplink(); })()")
    page.wait_for_timeout(300)
    check("Opt9·企业版未连中心置灰", page.evaluate(
        "document.getElementById('ndAiBtn').disabled") is True)
    page.evaluate("ndAiSetMode('personal')")
    page.wait_for_timeout(300)
    check("Opt9·切个人版解禁（不依赖中心）", page.evaluate(
        "document.getElementById('ndAiBtn').disabled") is False
        and page.evaluate("document.getElementById('ndAiGate').style.display") == "none")
    check("Opt9·segmented 状态", page.evaluate(
        "document.getElementById('ndAiModePers').className") == "nd-seg on")
    # 个人版未配置 → not_configured 引导（经补采模态直接提交）
    page.click("#ndAiBtn")
    page.wait_for_function(
        "(function(){ var o = document.getElementById('ndAiCompleteOverlay'); return !!o && o.style.display === 'flex'; })()", timeout=5000)
    page.evaluate("document.getElementById('ndAiCompleteSkip').click()")
    page.wait_for_timeout(600)
    check("Opt9·个人版未配置引导", "个人版未配置" in page.inner_text("#ndAiSummary"),
          page.inner_text("#ndAiSummary"))
    # 个人版提交 mock 链路
    page.evaluate("window.__stubPersonalConfigured = true")
    page.click("#ndAiBtn")
    page.wait_for_function(
        "(function(){ var o = document.getElementById('ndAiCompleteOverlay'); return !!o && o.style.display === 'flex'; })()", timeout=5000)
    page.evaluate("document.getElementById('ndAiCompleteSkip').click()")
    page.wait_for_function(
        "document.getElementById('ndAiBody').textContent.indexOf('个人版直连链路 OK') >= 0",
        timeout=8000)
    check("Opt9·个人版提交渲染三段+元信息", page.evaluate(
        "!!document.querySelector('#ndAiBody .nd-ai-sec-cause')")
        and "本地诊断" in page.inner_text("#ndAiBody .nd-ai-meta")
        and "耗时" in page.inner_text("#ndAiBody .nd-ai-meta"))
    check("Opt9·个人版请求 mode=personal", page.evaluate(
        "window.__stubLastAiMode") == "personal")
    check("Opt9·个人版历史落档", "本地" in page.inner_text("#ndAiHistoryBody"))
    # 诊断 401 指引（personal_http_401 → 设置入口）
    page.evaluate("window.__stubPersonal401 = true")
    page.click("#ndAiBtn")
    page.wait_for_function(
        "(function(){ var o = document.getElementById('ndAiCompleteOverlay'); return !!o && o.style.display === 'flex'; })()", timeout=5000)
    page.evaluate("document.getElementById('ndAiCompleteSkip').click()")
    page.wait_for_timeout(700)
    check("Personal401·错误文案含设置指引", "检查 API Key 是否已保存且有效" in page.inner_text("#ndAiBody"),
          page.inner_text("#ndAiBody")[:120])
    page.evaluate("window.__stubPersonal401 = false")
    # 模式记忆（同文档 localStorage 持久 + 重新初始化读取）
    check("Opt9·模式记忆写入", page.evaluate(
        "localStorage.getItem('nd_ai_mode')") == "personal")
    page.evaluate("(function(){ ndState.inited = false; initNetDoctorTab(); })()")
    page.wait_for_timeout(400)
    check("Opt9·重新初始化仍个人版", page.evaluate(
        "document.getElementById('ndAiModePers').className") == "nd-seg on"
        and page.evaluate("document.getElementById('ndAiBtn').disabled") is False)
    # 切回企业版恢复 gate
    page.evaluate("ndAiSetMode('enterprise')")
    page.wait_for_timeout(300)
    check("Opt9·切回企业版恢复 gate", page.evaluate(
        "document.getElementById('ndAiBtn').disabled") is True
        and page.evaluate("document.getElementById('ndAiGate').style.display") == "block")

    # ⑩ 门控陈旧回归（2026-09-10 主页 AI 卡缺陷）：常驻轮询自动翻转，全程不切 tab
    page.evaluate("window.__ndUplinkPollMs = 400; ndStartUplinkPoll();")
    page.evaluate("(function(){ window.__stubUplink.uplink.state='connecting'; ndLoadUplink(); })()")
    page.wait_for_timeout(300)
    check("Poll·connecting 初态门控未连接", page.evaluate(
        "document.getElementById('ndAiBtn').disabled") is True
        and page.evaluate("document.getElementById('ndAiGate').style.display") == "block")
    page.evaluate("(function(){ window.__stubUplink.uplink.state='connected'; })()")   # 仅翻桩，不手动刷新
    page.wait_for_function(
        "document.getElementById('ndAiBtn').disabled === false"
        " && document.getElementById('ndAiGate').style.display === 'none'", timeout=3000)
    check("Poll·轮询周期内门控自动翻转已连接", "已连接"
          in page.evaluate("document.getElementById('ndUplinkBody').textContent"))
    page.evaluate("(function(){ window.__stubUplink.uplink.state='error'; })()")
    page.wait_for_function("document.getElementById('ndAiBtn').disabled === true", timeout=3000)
    check("Poll·停驻期间断连反向翻转", page.evaluate(
        "document.getElementById('ndAiGate').style.display") == "block")
    page.evaluate("(function(){ window.__ndUplinkPollMs = undefined; ndStartUplinkPoll(); })()")

    # ⑪ 宽度自适应抽查（STYLE.md §9）：独立页三档视口无横向滚动 + 主体大断点
    for w in (1920, 1440, 1080):
        page.set_viewport_size({"width": w, "height": 960})
        page.wait_for_timeout(250)
        check("独立页·W%d 无横向滚动" % w, page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
            " && document.body.scrollWidth <= document.body.clientWidth + 1"))
    page.set_viewport_size({"width": 1920, "height": 960})
    page.wait_for_timeout(250)
    check("独立页·主体上限大断点 1600px（禁写死窄值）", page.evaluate(
        "getComputedStyle(document.querySelector('.main-content')).maxWidth") == "1600px")


MAIN_STUB_EXTRA = r"""
window.__stubHwinfo = { success: true, hwinfo: { source: "cim", hostname: "STUB-PC",
    os: { caption: "Stub Windows 11 Pro", version: "10.0", build: "26200" },
    cpu: { name: "Stub CPU", cores: 8, logical: 16 },
    memory: { total: 17179869184, modules: [] }, disks: [], gpu: [] } };
window.__stubSnapshot = { success: true, ts: 1,
    cpu: { percent: 10.0, count: 16, freq_mhz: 3000 },
    memory: { total: 17179869184, used: 5000000000, available: 12000000000, percent: 30.0,
              total_text: "16.0 GB", available_text: "11.2 GB" },
    swap: { total: 1, used: 0, percent: 1.0 }, disks: [],
    volumes: [{ mount: "C:\\", total: 1, used: 0.5, free: 0.5, percent: 50.0 }] };
window.__stubTemps = { success: true, admin: false,
    cpu: { available: false, temp_c: null, reason: "not_admin" },
    gpu: { available: true, temp_c: 45.0, reason: null } };
window.__stubHomeNet = { success: true, source: "powershell", adapters: [
    { name: "以太网", desc: "Stub Intel I226-V", status: "Up", mac: "AA-BB-CC-DD-EE-FF",
      speed: "1.0 Gbps", ipv4: ["172.17.90.215"], plen: [24], gw: "172.17.90.1",
      dns: ["172.17.1.109"] } ] };
window.__stubLogAccess = { success: true,
    access: { System: true, Application: true, Security: false, Setup: true },
    denied: [{ type: "Security", name: "安全日志", error: "拒绝访问" }] };
function __stubEvents(n) {
    var arr = [];
    for (var i = 0; i < n; i++) {
        arr.push({ timestamp: "2026-09-09 09:0" + (i % 10) + ":00", event_id: 6005 + (i % 3),
                   level_name: "信息", source: "Stub Source", description: "Stub event " + i });
    }
    return arr;
}
window.__stubLogSearch = { success: true, events: __stubEvents(12),
    summary: { total: 12, critical: 0, error: 0, warning: 0, info: 12, sources: { "Stub Source": 12 } } };
window.__stubDiskOverview = { success: true, drive: "C:\\", total: 536870912000,
    total_text: "500 GB", used: 268435456000, used_text: "250 GB", free: 268435456000,
    free_text: "250 GB", used_percent: 50.0 };
var __baseStubCall = null;
"""


def run_scenario_2(page):
    print("\n[场景2] 主应用集成（web/index.html + 全模块桩）遍历全部菜单")
    page.goto(MAIN_INDEX.as_uri())
    page.wait_for_timeout(1400)
    check_no_real_ip_assets(page, "main")

    assert_functions(page, ["switchTab", "initHomeTab", "initLogInspectorTab", "initDiskTab",
                            "initPerfTab", "initNetDoctorTab",
                            "hmRenderNetwork", "liSearch", "startJunkScan", "startPerfStress",
                            "ndStartPing", "ndRenderStress"], "main")
    check("主应用·主页激活", page.evaluate(
        "document.getElementById('tab-home').classList.contains('active')"))
    home_txt = page.inner_text("#homeNetBody")
    check("主应用·主页网络卡正常", "以太网" in home_txt and "172.17.90.215" in home_txt, home_txt[:80])

    # AI 诊断卡宿主迁移（2026-09-10：网络排障 → 主页终端概览之上）
    check("主应用·AI 卡已移至主页且网络排障页无残留", page.evaluate(
        "!!document.querySelector('#tab-home #ndAiBtn') && !document.querySelector('#tab-netdoctor #ndAiBtn')"))
    check("Rename·导航与页头定名网络排障（2026-09-11 回退）", page.evaluate(
        "(function(){ var b = document.querySelector('button[data-tab=netdoctor]');"
        " var h = document.querySelector('#tab-netdoctor .page-header h2');"
        " return b.textContent.indexOf('网络排障') >= 0 && h.textContent === '网络排障'; })()"))
    check("主应用·主页激活即初始化 AI（提交可用+日志区在位）", page.evaluate(
        "document.getElementById('ndAiBtn').disabled") is False
        and page.evaluate("!!document.getElementById('ndAiLogsHead')")
        and page.evaluate("typeof ndAiSubmit") == "function")
    # AI 卡排版对齐概览三卡（2026-09-11 用户要求，computed style 抽查）
    check("AI排版·标题字号=概览卡标题", page.evaluate(
        "(function(){ return getComputedStyle(document.querySelector('.nd-ai-card h3')).fontSize"
        " === getComputedStyle(document.querySelector('.hm-card h3')).fontSize; })()",
        ))
    check("AI排版·标题青色一致", page.evaluate(
        "(function(){ return getComputedStyle(document.querySelector('.nd-ai-card h3')).color"
        " === getComputedStyle(document.querySelector('.hm-card h3')).color; })()"))
    check("AI排版·卡体内边距=概览卡", page.evaluate(
        "(function(){ var a = getComputedStyle(document.querySelector('.nd-ai-card .card-body'));"
        " var h = getComputedStyle(document.querySelector('.hm-card'));"
        " return a.paddingLeft === h.paddingLeft && a.paddingRight === h.paddingRight"
        " && a.paddingTop === h.paddingTop && a.paddingBottom === h.paddingBottom; })()"))
    check("AI排版·问题概述行左标签右值", page.evaluate(
        "(function(){ var r = document.querySelector('.nd-ai-card .hm-row');"
        " if (!r) { return false; } var s = getComputedStyle(r);"
        " return s.display === 'flex' && s.justifyContent === 'space-between'"
        " && !!r.querySelector('#ndAiIssueCount'); })()"))
    check("AI排版·提交诊断贴右缘（与箭头间距一致）", page.evaluate(
        "(function(){ var b = document.getElementById('ndAiBtn').getBoundingClientRect();"
        " var c = document.querySelector('.nd-ai-card .card-chevron');"
        " var cr = c ? c.getBoundingClientRect() : null;"
        " return !!cr && (cr.left - b.right) >= 0 && (cr.left - b.right) <= 24; })()"))

    for tab, fn in (("loginspector", "initLogInspectorTab"), ("disk", "initDiskTab"),
                    ("perf", "initPerfTab"), ("netdoctor", "initNetDoctorTab")):
        page.click('button[data-tab="%s"]' % tab)
        page.wait_for_timeout(700)
        check("切换 %s 后 %s 保持" % (tab, fn),
              page.evaluate("typeof %s" % fn) == "function")
        check("切换 %s 后激活" % tab, page.evaluate(
            "document.getElementById('tab-%s').classList.contains('active')" % tab))

    check("主应用·netdoctor 五卡默认收起", page.evaluate(
        "(function(){ var cs = document.querySelectorAll('#tab-netdoctor .section-card.nd-styled');"
        " if (cs.length !== 5) { return -1; } var n = 0;"
        " for (var i = 0; i < cs.length; i++) { if (cs[i].classList.contains('collapsed')) { n++; } }"
        " return n; })()") == 5)
    page.evaluate("(function(){ var cs = document.querySelectorAll('#tab-netdoctor .section-card.nd-styled');"
        " for (var i = 0; i < cs.length; i++) { cs[i].classList.remove('collapsed'); } })()")
    check("主应用·netdoctor 节点表填充", page.evaluate(
        "document.querySelectorAll('#ndNodesBody tr').length") == 10)
    check("主应用·netdoctor 中心已连接", "已连接" in page.inner_text("#ndUplinkBody")
          and "未连接" not in page.inner_text("#ndUplinkBody"))
    # 五模块卡排版对齐 AI 卡基线（2026-09-11，STYLE.md 规格）
    check("五卡·卡头字号=AI卡", page.evaluate(
        "(function(){ var h = document.querySelector('.nd-styled h3');"
        " return !!h && getComputedStyle(h).fontSize"
        " === getComputedStyle(document.querySelector('.nd-ai-card h3')).fontSize; })()"))
    check("五卡·卡体padding=AI卡", page.evaluate(
        "(function(){ var a = getComputedStyle(document.querySelector('.nd-ai-card .card-body'));"
        " var b = getComputedStyle(document.querySelector('.nd-styled .card-body'));"
        " return a.paddingLeft === b.paddingLeft && a.paddingTop === b.paddingTop; })()"))
    check("文案·实现细节零残留", page.evaluate(
        "(function(){ var t = document.getElementById('tab-netdoctor').textContent;"
        " return t.indexOf('app_config') < 0 && t.indexOf('expected_dns') < 0"
        " && t.indexOf('netdoctor.') < 0; })()"))

    # netdoctor 功能动作在主应用上下文里同样可用
    page.click("#ndConfBtn")
    page.wait_for_timeout(700)
    check("主应用·核查渲染", "以太网" in page.inner_text("#ndConfBody"))

    # 主应用·IP 冲突 + 深度检测（平台编排对接回归）
    page.click("#ndConflictBtn")
    page.wait_for_timeout(700)
    check("主应用·冲突渲染", "疑似 IP 冲突" in page.inner_text("#ndConflictBody"))
    check("主应用·深度函数可用", page.evaluate(
        "typeof ndStartDeep === 'function' && typeof ndDeepPollTick === 'function'"
        " && typeof ndRenderDeep === 'function'"))
    check("主应用·AI 重跑函数可用", page.evaluate(
        "typeof ndReanalyzeAi === 'function' && typeof ndRenderAiAssist === 'function'"))
    page.evaluate("window.__stubDeepPollCount = 0; ndStartDeep()")
    page.wait_for_function(
        "document.getElementById('ndDeepTip').textContent.indexOf('深度检测完成') >= 0", timeout=9000)
    check("主应用·深度 verdict 渲染", "确认 IP 冲突" in page.inner_text("#ndDeepBody"),
          page.inner_text("#ndDeepBody")[:120])
    page.evaluate("window.__stubDeepStartBusy = undefined; window.__stubDeepPollCount = 0;")

    # 设置弹窗：两卡独立（网络监测配置 / AI 个人版）+ 卡片化挂载 + 各自保存
    page.click("#appSettingsBtn")
    page.wait_for_timeout(500)
    check("设置·两卡在 ndSettingsHost 内渲染", page.evaluate(
        "document.querySelectorAll('#ndSettingsHost .nd-set-card').length") == 2)
    check("设置·节点区块渲染", page.evaluate(
        "document.querySelectorAll('#ndSettingsHost tr[data-ndidx]').length") == 10)
    check("设置·动态目标不可编辑", page.evaluate(
        "(function(){ var ins = document.querySelectorAll('#ndSettingsHost .nd-input[disabled]'); return ins.length === 2; })()"))
    check("设置·文案无实现细节字样", page.evaluate(
        "(function(){ var t = document.getElementById('ndSettingsHost').textContent;"
        " return t.indexOf('app_config.json') < 0 && t.indexOf('netdoctor.ai_personal') < 0"
        " && t.indexOf('明文本机可接受') < 0; })()"))
    # AI 个人版卡：none 测试（无已保存 Key）
    check("Opt9·设置·个人版区块渲染", page.evaluate(
        "!!document.getElementById('ndSetAiUrl') && !!document.getElementById('ndSetAiKey')"
        " && !!document.getElementById('ndSetAiModel') && !!document.getElementById('ndSetAiTestBtn')"
        " && !!document.getElementById('ndSetAiSaveBtn')"))
    page.fill("#ndSetAiUrl", "https://api.stub-llm.example")
    page.click("#ndSetAiTestBtn")
    page.wait_for_timeout(500)
    check("PersonalTest·无已保存 Key → none 提示", "未配置 API Key" in page.inner_text("#ndSetAiTestTip"),
          page.inner_text("#ndSetAiTestTip"))
    # AI 卡独立保存（仅 ai_personal 段，走 config 通道）
    page.fill("#ndSetAiModel", "stub-chat")
    page.fill("#ndSetAiKey", "sk-stub-123")
    page.evaluate("window.__stubConfigCalls = [];")
    page.click("#ndSetAiSaveBtn")
    page.wait_for_timeout(600)
    cfg_calls = page.evaluate("(window.__stubConfigCalls || []).join('||')")
    check("Opt9·设置·AI 卡独立保存仅提交个人版段", page.evaluate(
        "(function(){ var c = window.__stubConfigCalls || [];"
        " return c.length === 1 && c[0].indexOf('ai_personal_json=') >= 0"
        " && c[0].indexOf('nodes_json=') < 0; })()"), cfg_calls[:200])
    check("Opt9·设置·个人版配置保存", page.evaluate(
        "(function(){ var ap = window.__stubConfig.ai_personal || {};"
        " return ap.api_url === 'https://api.stub-llm.example' && ap.model === 'stub-chat'"
        " && ap.api_key === 'sk-stub-123'; })()"))
    check("Opt9·设置·key 脱敏回显", page.evaluate(
        "(function(){ var k = document.getElementById('ndSetAiKey');"
        " return k.value === '' && k.placeholder.indexOf('已保存（留空=不修改）') >= 0; })()"))
    # saved 路径：Key 框留空 → 回退已保存 Key（不传 key 参数）
    page.click("#ndSetAiTestBtn")
    page.wait_for_timeout(500)
    pt_path = page.evaluate("window.__stubLastPersonalTest") or ""
    check("PersonalTest·Key 留空走已保存路径", "（用已保存 Key 测试）" in page.inner_text("#ndSetAiTestTip")
          and "api_key=" not in pt_path,
          (page.inner_text("#ndSetAiTestTip") + " | " + pt_path)[:140])
    # 节点卡保存（编辑→持久化→连通性表即时生效）
    page.fill('.nd-set-target[value="172.17.254.1"]', "172.17.254.99")
    page.click("#ndSettingsSaveBtn")
    page.wait_for_timeout(600)
    check("设置·保存持久化", page.evaluate(
        "(window.__stubConfig.nodes.find(function(n){return n.key==='core';})||{}).target") == "172.17.254.99")
    check("设置·连通性节点表即时生效", "172.17.254.99" in page.inner_text("#ndNodesBody"))
    page.click("#ndSettingsSaveBtn")   # 幂等复存（编辑已持久化的 99）
    page.wait_for_timeout(400)
    page.click(".app-settings-close")  # 关闭设置弹窗，避免遮罩拦截后续点击
    page.wait_for_timeout(300)

    # 主应用上下文 AI 提交（bridge call 双参透传 body；AI 卡宿主在主页）
    page.click('button[data-tab="home"]')
    page.wait_for_timeout(600)
    page.fill("#ndAiIssue", "主应用内提交诊断验证")
    page.click("#ndAiBtn")
    page.wait_for_function(
        "(function(){ var o = document.getElementById('ndAiCompleteOverlay'); return !!o && o.style.display === 'flex'; })()", timeout=5000)
    page.evaluate("document.getElementById('ndAiCompleteSkip').click()")
    page.wait_for_function("document.getElementById('ndAiBody').textContent.indexOf('A-STUB-1') >= 0",
                           timeout=8000)
    check("主应用·AI 提交与结果渲染",
          "网络链路正常" in page.inner_text("#ndAiBody .nd-ai-sec-cause")
          and page.evaluate("window.__stubLastAiReq && !!window.__stubLastAiReq.issue"
                            " && !!window.__stubLastAiReq.logs.system_log"))
    check("主应用·AI 结构化渲染与元信息徽章", page.evaluate(
        "!!document.querySelector('#ndAiBody .nd-ai-sec-cause')"
        " && !!document.querySelector('#ndAiBody .nd-ai-meta')"))
    check("主应用·AI 结果区文本可选复制（全局未扩大化）", page.evaluate(
        "(function(){ var el = document.getElementById('ndAiBody');"
        " var us = getComputedStyle(el).userSelect;"
        " var r = document.createRange(); r.selectNodeContents(el);"
        " var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);"
        " var ok = s.toString().length > 0; s.removeAllRanges();"
        " return us === 'text' && ok"
        " && getComputedStyle(document.body).userSelect !== 'text'; })()"))

    page.click('button[data-tab="home"]')
    page.wait_for_timeout(600)
    check("主应用·回主页数据保持", "以太网" in page.inner_text("#homeNetBody"))

    # 回归：磁盘/性能/日志诊断在遍历后仍能产出数据
    page.click('button[data-tab="disk"]')
    page.wait_for_timeout(700)
    check("主应用·磁盘概览填充", page.evaluate(
        "document.getElementById('diskUsedPercent').textContent.indexOf('50') >= 0"))
    page.click('button[data-tab="loginspector"]')
    page.wait_for_timeout(700)
    check("主应用·日志检索填充", "Stub event" in page.inner_text("#liBody") or
          page.evaluate("document.querySelectorAll('#liBody tr').length") > 1)

    # 主页平台接入：挂起→失败可见→恢复（2026-09-09 冻结缺陷回归门禁）
    page.click('button[data-tab="home"]')
    page.wait_for_timeout(500)
    page.evaluate("window.__apiFetchTimeout = 600; window.__stubHang = true;")
    page.evaluate("hmUplinkTick()")
    page.wait_for_timeout(1400)
    check("主应用·uplink 挂起失败可见（不再静默永冻）",
          "状态刷新失败" in page.inner_text("#homeUplinkBody"),
          page.inner_text("#homeUplinkBody")[:80])
    page.evaluate("window.__stubHang = false")
    page.evaluate("hmUplinkTick()")
    page.wait_for_timeout(600)
    check("主应用·uplink 自愈恢复已连接", "已连接" in page.inner_text("#homeUplinkBody")
          and "状态刷新失败" not in page.inner_text("#homeUplinkBody"))

    # 门控陈旧回归（主页常驻缺陷 2026-09-10）：桩翻转后不切 tab，轮询周期内门控自动翻转
    page.evaluate("window.__ndUplinkPollMs = 400; ndStartUplinkPoll();")
    page.evaluate("(function(){ window.__stubUplink.uplink.state='connecting'; ndLoadUplink(); })()")
    page.wait_for_timeout(300)
    check("主应用·门控未连接（connecting 初态，主页常驻）", page.evaluate(
        "document.getElementById('ndAiBtn').disabled") is True)
    page.evaluate("(function(){ window.__stubUplink.uplink.state='connected'; })()")
    page.wait_for_function("document.getElementById('ndAiBtn').disabled === false", timeout=3000)
    check("主应用·轮询周期内门控自动翻转（未切 tab）", "已连接"
          in page.evaluate("document.getElementById('ndUplinkBody').textContent"))
    page.evaluate("(function(){ window.__ndUplinkPollMs = undefined; ndStartUplinkPoll(); })()")

    # HTTPS 专项（2026-09-11）：连接徽章协议标识
    page.evaluate("(function(){ window.__stubUplink.uplink.server_url = 'https://127.0.0.1:18443';"
        " window.__stubUplink.uplink.state = 'connected'; ndLoadUplink(); })()")
    page.wait_for_timeout(500)
    check("HTTPS·https 地址徽章含锁标识", "🔒" in page.inner_text("#ndUplinkBody"),
          page.inner_text("#ndUplinkBody")[:60])
    page.evaluate("(function(){ window.__stubUplink.uplink.server_url = 'http://127.0.0.1:18090';"
        " ndLoadUplink(); })()")
    page.wait_for_timeout(500)
    check("HTTPS·http 地址无锁标识", "🔒" not in page.inner_text("#ndUplinkBody"))


# ---------------------------------------------------------------------------
# 场景 3：全页宽度自适应（2026-09-16 STYLE.md §9 固化，全终端页一体适用）
# 三档视口 1920/1440/1080：页面无横向滚动、导航不挤压换行、容器流式伸缩、降级生效
# ---------------------------------------------------------------------------

WIDTH_TABS = ["home", "loginspector", "disk", "perf", "netdoctor",
              "filesearch", "desktoppolicy"]

_WIDTH_PROBE_JS = r"""
(function () {
    var de = document.documentElement, b = document.body;
    var tabs = document.querySelectorAll('.nav-tab');
    var hs = [];
    for (var i = 0; i < tabs.length; i++) { hs.push(tabs[i].offsetHeight); }
    var mc = document.querySelector('.main-content');
    var homeCard = document.querySelector('#tab-home .hm-card');
    var t = document.querySelector('.nav-title');
    return { docSW: de.scrollWidth, deCW: de.clientWidth,
             bodySW: b.scrollWidth, bodyCW: b.clientWidth,
             tabHs: hs,
             actionsRight: Math.round(document.querySelector('.nav-actions').getBoundingClientRect().right),
             vw: window.innerWidth,
             mainW: mc ? Math.round(mc.getBoundingClientRect().width) : 0,
             homeCardW: homeCard ? Math.round(homeCard.getBoundingClientRect().width) : 0,
             titleDisp: t ? getComputedStyle(t).display : "gone" };
})()
"""

# 活动页内卡片流式宽度 + 页面滚动断言（须先激活对应 tab 再测，隐藏页宽恒 0）
_WIDTH_NDCARD_JS = r"""
(function () {
    var de = document.documentElement, b = document.body;
    var c = document.querySelector('#tab-netdoctor .section-card');
    return { ndW: c ? Math.round(c.getBoundingClientRect().width) : 0,
             docSW: de.scrollWidth, deCW: de.clientWidth,
             bodySW: b.scrollWidth, bodyCW: b.clientWidth };
})()
"""

_WIDTH_TAB_SCROLL_JS = r"""
(function () {
    var de = document.documentElement, b = document.body;
    return { docSW: de.scrollWidth, deCW: de.clientWidth,
             bodySW: b.scrollWidth, bodyCW: b.clientWidth };
})()
"""

# filesearch / desktoppolicy 页宽度遍历用 benign 桩（不参与形状断言，防未捕获异常）
WIDTH_STUB_JS = r"""
(function () {
    var prev = window.pywebview.api.call;
    function extra(path) {
        var p = path.split("?")[0];
        if (p.indexOf("/api/filesearch/status") === 0)
            return { success: true, db_exists: false, file_count: 0, dir_count: 0, volumes: [] };
        if (p.indexOf("/api/desktoppolicy/status") === 0)
            return { success: true, revision: -1, session_type: "console",
                     reported_at: 0, results: {}, monitors: [] };
        if (p.indexOf("/api/filesearch/") === 0 || p.indexOf("/api/desktoppolicy/") === 0)
            return { success: false };
        return undefined;
    }
    window.pywebview.api.call = function (path, body) {
        var e = extra(path);
        if (e !== undefined) { window.__stubCalls.push(path); return Promise.resolve(e); }
        return prev(path, body);
    };
})();
"""


def run_scenario_3(page):
    print("\n[场景3] 全页宽度自适应（1920/1440/1080 · STYLE.md §9）")
    page.goto(MAIN_INDEX.as_uri())
    page.wait_for_timeout(1200)
    snaps = {}
    nd_snaps = {}
    for w in (1920, 1440, 1080):
        tag = "W%d" % w
        page.set_viewport_size({"width": w, "height": 960})
        page.wait_for_timeout(250)
        page.click('button[data-tab="home"]')          # 探针在主页活动态测量
        page.wait_for_timeout(250)
        r = page.evaluate(_WIDTH_PROBE_JS)
        snaps[w] = r
        check("%s·页面无横向滚动条" % tag,
              r["docSW"] <= r["deCW"] + 1 and r["bodySW"] <= r["bodyCW"] + 1,
              (r["docSW"], r["deCW"], r["bodySW"], r["bodyCW"]))
        check("%s·导航页签不挤压换行（等高）" % tag,
              max(r["tabHs"]) - min(r["tabHs"]) <= 2 and r["tabHs"][0] <= 40,
              r["tabHs"])
        check("%s·导航操作区未贴边挤出" % tag, r["actionsRight"] <= r["vw"], r["actionsRight"])
        check("%s·主体容器随视口流式伸缩" % tag, r["mainW"] >= w - 40, (w, r["mainW"]))
        page.click('button[data-tab="netdoctor"]')     # 排障卡宽度须在活动页测量
        page.wait_for_timeout(250)
        r_nd = page.evaluate(_WIDTH_NDCARD_JS)
        nd_snaps[w] = r_nd["ndW"]
        check("%s·排障卡随视口伸缩不窄于最小可用宽" % tag,
              r_nd["ndW"] >= min(900, w - 120), (w, r_nd["ndW"]))
        check("%s·netdoctor 页无横向溢出" % tag,
              r_nd["docSW"] <= r_nd["deCW"] + 1 and r_nd["bodySW"] <= r_nd["bodyCW"] + 1,
              (r_nd["docSW"], r_nd["deCW"]))
        for tab in ("loginspector", "disk", "perf", "filesearch", "desktoppolicy"):
            page.click('button[data-tab="%s"]' % tab)
            page.wait_for_timeout(220)
            r2 = page.evaluate(_WIDTH_TAB_SCROLL_JS)
            check("%s·%s 页无横向溢出" % (tag, tab),
                  r2["docSW"] <= r2["deCW"] + 1 and r2["bodySW"] <= r2["bodyCW"] + 1,
                  (tab, r2["docSW"], r2["deCW"]))
    # 降级生效断言（导航 ≤1240 收紧隐藏标题；多栏 minmax 下限）
    check("降级·1080 导航标题隐藏（收紧形态）", snaps[1080]["titleDisp"] == "none",
          snaps[1080]["titleDisp"])
    check("降级·1920 导航完整形态（标题可见）", snaps[1920]["titleDisp"] == "block",
          snaps[1920]["titleDisp"])
    check("降级·1080 主页卡片不低于最小列宽（minmax 生效）",
          snaps[1080]["homeCardW"] >= 330, snaps[1080]["homeCardW"])
    check("容器·主页卡片宽度随视口增长（1080→1920）",
          snaps[1920]["homeCardW"] > snaps[1080]["homeCardW"],
          (snaps[1080]["homeCardW"], snaps[1920]["homeCardW"]))
    check("容器·排障卡宽度随视口显著增长（1080→1920）",
          nd_snaps[1920] - nd_snaps[1080] > 500,
          (nd_snaps[1080], nd_snaps[1920]))


def main():
    s1_only = "--s1-only" in sys.argv
    with sync_playwright() as p:
        browser = p.chromium.launch()
        # 场景 1：独立页
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append("S1:" + str(e)))
        page.on("dialog", lambda d: d.accept())   # 提交确认 confirm 自动继续（场景 1 用 JS override 覆盖）
        page.add_init_script(ND_STUB_JS)
        try:
            run_scenario_1(page)
        finally:
            browser.close()

        # 场景 2：主应用（复用 netdoctor 桩 + 追加其他模块桩；路由优先分流后回落 netdoctor 桩）
        errors2 = []
        errors3 = []
        if s1_only:
            print("\n[场景2] 跳过（--s1-only）")
            print("[场景3] 跳过（--s1-only）")
        else:
            browser = p.chromium.launch()
            page2 = browser.new_page(viewport={"width": 1440, "height": 900})
            page2.on("pageerror", lambda e: errors2.append("S2:" + str(e)))
            page2.on("dialog", lambda d: d.accept())   # 主页 AI 提交确认（未采集源存在时）自动继续
            page2.add_init_script(ND_STUB_JS)
            page2.add_init_script(MAIN_STUB_EXTRA)
            page2.add_init_script(r"""
        (function () {
            var ndCall = window.pywebview.api.call;
            function extra(path) {
                var p = path.split("?")[0];
                if (p.indexOf("/api/perf/hwinfo") === 0) return window.__stubHwinfo;
                if (p.indexOf("/api/perf/snapshot") === 0) return window.__stubSnapshot;
                if (p.indexOf("/api/perf/temps") === 0) return window.__stubTemps;
                if (p.indexOf("/api/perf/app-config") === 0)
                    return { success: true, config: { temperature_interval_sec: 300, min: 30, max: 3600, default: 300 } };
                if (p.indexOf("/api/home/network") === 0) return window.__stubHomeNet;
                if (p.indexOf("/api/loginspector/access") === 0) return window.__stubLogAccess;
                if (p.indexOf("/api/loginspector/search") === 0) return window.__stubLogSearch;
                /* 轮询类端点一律 success:false 安全终止（disk/appdata/perf 渲染形状不参与本门禁） */
                if (p.indexOf("/api/perf/record-status") === 0 || p.indexOf("/api/perf/stress-status") === 0
                    || p.indexOf("/api/perf/record-latest") === 0
                    || p.indexOf("/api/disk/scan-status") === 0 || p.indexOf("/api/appdata/scan-status") === 0
                    || p.indexOf("/api/installers/scan-status") === 0)
                    return { success: false };
                if (p.indexOf("/api/disk/overview") === 0) return window.__stubDiskOverview;
                if (p.indexOf("/api/disk/scan") === 0) return { success: true, scan_id: "J1" };
                if (p.indexOf("/api/disk/tree") === 0 || p.indexOf("/api/disk/drives") === 0)
                    return { success: true, drives: [] };
                if (p.indexOf("/api/appdata/scan") === 0) return { success: true, scan_id: "A1" };
                if (p.indexOf("/api/appdata/drives") === 0) return { success: true, drives: [] };
                if (p.indexOf("/api/installers/scan") === 0) return { success: true, scan_id: "I1" };
                return undefined;
            }
            window.pywebview.api.call = function (path, body) {
                var e = extra(path);
                if (e !== undefined) {
                    window.__stubCalls.push(path);
                    return Promise.resolve(e);
                }
                return ndCall(path, body);
            };
        })();
        """)
            try:
                run_scenario_2(page2)
            finally:
                browser.close()

            # 场景 3：全页宽度自适应（独立页面独立收集 pageerror）
            browser = p.chromium.launch()
            page3 = browser.new_page(viewport={"width": 1920, "height": 960})
            page3.on("pageerror", lambda e: errors3.append("S3:" + str(e)))
            page3.on("dialog", lambda d: d.accept())
            page3.add_init_script(ND_STUB_JS)
            page3.add_init_script(MAIN_STUB_EXTRA)
            page3.add_init_script(WIDTH_STUB_JS)
            try:
                run_scenario_3(page3)
            finally:
                browser.close()

        print("\n[JS errors] S1 pageerror=%d, S2 pageerror=%d, S3 pageerror=%d"
              % (len(errors), len(errors2), len(errors3)))
        for e in errors + errors2 + errors3:
            print("  JS ERROR:", e[:240])
        check("no-pageerror（三场景）",
              len(errors) == 0 and len(errors2) == 0 and len(errors3) == 0,
              errors + errors2 + errors3)

    total = len(PASS) + len(FAIL)
    print("\n===== RESULT: %d/%d passed =====" % (len(PASS), total))
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
        sys.exit(1)
    print("E2E NETDOCTOR GATE: PASS")


if __name__ == "__main__":
    main()
