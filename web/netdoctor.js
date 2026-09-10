/* ==========================================
   网络排障 · netdoctor（观枢终端平台｜EyeTerm）
   ==========================================
   五功能 + 第六模块 AI 智能诊断：
   ① 配置核查 ② IP 冲突检测 ③ 连通性测试 ④ 路由追踪 ⑤ 网络压测 ⑥ AI 诊断
   命名空间：除入口 initNetDoctorTab 外一律 nd 前缀；工具自包含。
   传输：ndApiFetch 三级回退（apiFetch 守卫 → pywebview → fetch）。
   后台任务：start 返回 task_id → ndPollTask 轮询 task-status → 完成渲染。
   兼容性：禁用 ?. 可选链与 ES2020+ 语法（E2E 门禁 ADR-007）。
   ========================================== */

var ndState = {
    inited: false,
    uplink: null,          /* /api/perf/uplink/status → uplink 对象 */
    nodes: [],
    expectedDns: [],
    confTaskId: null,
    conflictTaskId: null,
    deepTaskId: null,      /* 深度检测平台任务 id（DC-xxx） */
    deepRound: 0,          /* 深度检测轮询轮次（2.5s/轮，60 轮上限） */
    pingTaskId: null,
    tracertTaskId: null,
    stressTaskId: null,
    lastStressResult: null,
    lastPing: null,        /* 最近连通性结果（AI 诊断 network 类用） */
    lastTracert: null,
    historyAgg: null,
    aiCollect: null,       /* AI 诊断数据源缓存 {key: {ok,data,note,ts}} */
    aiHistory: [],
    aiMode: "enterprise",  /* AI 诊断模式：enterprise（中心模型链）/ personal（本地第三方 API） */
    aiPersonal: {},        /* 个人版配置回显 {api_url, model, has_key}（key 永不回显） */
    aiSubmitting: false,   /* 诊断提交防重入标志（id=21/22 双击双提交缺陷修复） */
    uplinkPollTimer: null, /* 中心状态常驻轮询句柄 */
    pollers: {}
};

/* ===================== 传输与工具 ===================== */

function ndApiFetch(path) {
    /* 2026-09-09 冻结缺陷修复：自带超时保护（默认 15s，__apiFetchTimeout 供 E2E 注入）。
       主应用宿主 apiFetch（app.js）已有同款保护则复用；独立页/桩直调路径由此兜底，
       IPC 挂死时 reject 而非永挂起，上层 catch 可达、UI 可自愈。 */
    if (typeof apiFetch === "function") { return apiFetch(path); }
    var tmo = window.__apiFetchTimeout || 15000;
    var call;
    if (window.pywebview && window.pywebview.api) {
        call = window.pywebview.api.call(path);
        call.catch(function () {});   /* 超时后原 promise 拒绝防 unhandledrejection */
    } else {
        call = fetch(path).then(function (r) { return r.json(); });
    }
    return Promise.race([
        call,
        new Promise(function (_, rej) {
            setTimeout(function () { rej(new Error("api_timeout_" + tmo + "ms")); }, tmo);
        })
    ]);
}

function ndEscapeHtml(str) {
    if (str === null || str === undefined) { return ""; }
    return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

function ndBadge(text, cls) {
    return '<span class="nd-badge ' + cls + '">' + ndEscapeHtml(text) + '</span>';
}

function ndStatusBadge(status) {
    var map = {
        ok: ["正常", "nd-ok"], warn: ["需关注", "nd-warn"], err: ["异常", "nd-err"],
        muted: ["—", "nd-muted"], unknown: ["未配置基线", "nd-muted"], running: ["检测中", "nd-info"]
    };
    var m = map[status] || [String(status || "--"), "nd-muted"];
    return ndBadge(m[0], m[1]);
}

function ndFmtMs(v) {
    return (v === null || v === undefined || isNaN(v)) ? "--" : (v + " ms");
}

function ndFmtPct(v) {
    return (v === null || v === undefined || isNaN(v)) ? "--" : (v + "%");
}

function ndSetTip(id, text) {
    var el = document.getElementById(id);
    if (el) { el.textContent = text || ""; }
}

/* ===================== 入口（幂等） ===================== */

function initNetDoctorTab() {
    /* 2026-09-10 Bug0 修复：中心状态每次激活都重查。旧逻辑一次性 inited 守卫导致
       首帧 uplink 处于 connecting/注册退避期时 gate 永久误判「未连接」
       （应用内切换菜单不触发 visibilitychange，无补救路径）。 */
    ndLoadUplink();
    if (ndState.inited) { return; }
    ndState.inited = true;
    try {
        ndState.aiMode = localStorage.getItem("nd_ai_mode") === "personal" ? "personal" : "enterprise";
    } catch (e) { ndState.aiMode = "enterprise"; }
    ndAiRenderMode();
    ndSizesEcho();   /* 压测包长档位回显（localStorage 逗号串，2026-09-10 分档重构） */
    ndLoadConfig();
    ndLoadHistory();
    ndAiInitHistory();
    ndAiRenderSources();
    /* 数据源预采集（静默后台，提交时 5 分钟内直接复用缓存） */
    setTimeout(function () { if (!document.hidden) { ndAiRefreshSources(); } }, 2000);
    /* 设置弹窗：打开时渲染网络排障节点维护区块（与既有 onclick 共存） */
    var sb = document.getElementById("appSettingsBtn");
    if (sb && !sb.dataset.ndHook) {
        sb.dataset.ndHook = "1";
        sb.addEventListener("click", ndRenderSettings);
    }
}

/* DOM 就绪：独立页/主应用默认激活页（网络排障或主页——AI 诊断卡宿主）时自动初始化；
   主应用后续切换由 switchTab 守卫调用（home 分支同样触发） */
(function () {
    var boot = function () {
        var sec = document.getElementById("tab-netdoctor");
        var home = document.getElementById("tab-home");
        if ((sec && sec.classList.contains("active"))
            || (home && home.classList.contains("active"))) { initNetDoctorTab(); }
    };
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }
})();

/* ===================== 中心连接状态 ===================== */

var ndUplinkMap = {
    connected: { text: "已连接中心", cls: "nd-ok" },
    connecting: { text: "连接中", cls: "nd-warn" },
    error: { text: "中心连接异常", cls: "nd-err" },
    disabled: { text: "未启用平台接入", cls: "nd-muted" }
};

function ndConnected() {
    return !!(ndState.uplink && ndState.uplink.state === "connected");
}

/* 第九项：AI 双模式——企业版依赖中心，个人版不依赖中心始终可用 */
function ndAiPersonal() {
    return ndState.aiMode === "personal";
}

function ndAiSetMode(m) {
    ndState.aiMode = m === "personal" ? "personal" : "enterprise";
    try { localStorage.setItem("nd_ai_mode", ndState.aiMode); } catch (e) { /* 存储禁用仅内存态 */ }
    ndAiRenderMode();
    ndRenderUplink();
    ndSetTip("ndAiSummary", ndAiPersonal()
        ? "个人版：使用本机配置的第三方 LLM API，不依赖中心（在「系统设置 · AI 诊断（个人版）」配置）"
        : "企业版：日志包提交中心模型链诊断（需已连接中心）");
}

function ndAiRenderMode() {
    var e = document.getElementById("ndAiModeEnt");
    var p = document.getElementById("ndAiModePers");
    if (e) { e.className = ndAiPersonal() ? "nd-seg" : "nd-seg on"; }
    if (p) { p.className = ndAiPersonal() ? "nd-seg on" : "nd-seg"; }
}

function ndLoadUplink() {
    ndApiFetch("/api/perf/uplink/status").then(function (d) {
        ndState.uplink = d && d.uplink ? d.uplink : null;
        ndRenderUplink();
    }).catch(function () {
        /* 2026-09-09 冻结缺陷修复：失败如实显示（apiFetch 层 15s 超时保证此处可达） */
        ndRenderUplinkFail();
    });
}

function ndRenderUplinkFail() {
    var el = document.getElementById("ndUplinkBody");
    if (el) {
        el.innerHTML = ndBadge("状态刷新失败", "nd-warn")
            + '<span class="nd-hint">　切换页面或下次操作时自动重试</span>';
    }
}

/* 恢复可见时立即主动刷一次中心状态（网络排障页或主页——AI 诊断卡宿主） */
document.addEventListener("visibilitychange", function () {
    if (document.hidden) { return; }
    var sec = document.getElementById("tab-netdoctor");
    var home = document.getElementById("tab-home");
    var active = (sec && sec.classList.contains("active"))
        || (home && home.classList.contains("active"));
    if (active && ndState.inited) {
        ndLoadUplink();
    }
});

/* ===================== 中心状态常驻轻量重查（2026-09-10 主页 AI 卡门控陈旧缺陷修复） =====================
   boot 时主页默认激活即查询，此刻 uplink 常处于 connecting/注册期；用户常驻主页不切页时，
   门控将永久停留旧值（switchTab/visibilitychange 均不会触发）。本定时器自包含兜底：
   - 周期 10s；仅当 document 可见且宿主页（tab-home/tab-netdoctor）处于 active 时发请求，
     hidden 或都不 active 时跳过（不后台空转）；已连接后保持低频轮询，覆盖「停驻期间断连」反向陈旧；
   - 失败沿用 ndRenderUplinkFail 既有「下次自动重试」语义（下个轮询周期即重试，内容幂等不刷屏）；
   - 上一请求未决时跳过本拍（busy 防重叠）；零 home.js 改动，standalone 页同样生效。
   E2E 可注入 window.__ndUplinkPollMs 后调用 ndStartUplinkPoll() 缩短周期。 */
var ND_UPLINK_POLL_MS = 10000;

var ndUplinkPollBusy = false;

function ndUplinkPollTick() {
    if (ndUplinkPollBusy) { return; }
    var active = false;
    try {
        var sec = document.getElementById("tab-netdoctor");
        var home = document.getElementById("tab-home");
        active = (sec && sec.classList.contains("active"))
            || (home && home.classList.contains("active"));
    } catch (e) { return; }
    if (document.hidden || !active || !ndState.inited) { return; }
    ndUplinkPollBusy = true;
    var settle = function () { ndUplinkPollBusy = false; };
    ndApiFetch("/api/perf/uplink/status").then(function (d) {
        settle();
        ndState.uplink = d && d.uplink ? d.uplink : null;
        ndRenderUplink();
    }).catch(function () {
        settle();
        ndRenderUplinkFail();
    });
}

function ndStartUplinkPoll() {
    if (ndState.uplinkPollTimer) { clearInterval(ndState.uplinkPollTimer); }
    ndState.uplinkPollTimer = setInterval(ndUplinkPollTick,
        window.__ndUplinkPollMs || ND_UPLINK_POLL_MS);
}
ndStartUplinkPoll();

function ndRenderUplink() {
    var el = document.getElementById("ndUplinkBody");
    if (!el) { return; }
    /* 二元判定：已注册+心跳成功（state=connected）→ 已连接；其余一律未连接（无中间态） */
    var on = ndConnected();
    el.innerHTML = ndBadge(on ? "已连接" : "未连接", on ? "nd-ok" : "nd-muted");
    /* 依赖中心的功能置灰/解灰（如实标注，不虚构可用）；AI gate 仅对企业版生效（第九项） */
    var ids = ["ndConflictBtn", "ndStressBtn"];
    for (var i = 0; i < ids.length; i++) {
        var b = document.getElementById(ids[i]);
        if (b) { b.disabled = !on; }
    }
    var aiOn = on || ndAiPersonal();
    var aiBtn = document.getElementById("ndAiBtn");
    if (aiBtn) { aiBtn.disabled = !aiOn; }
    var wrap1 = document.getElementById("ndConflictGate");
    if (wrap1) { wrap1.style.display = on ? "none" : "block"; }
    var wrap2 = document.getElementById("ndStressGate");
    if (wrap2) { wrap2.style.display = on ? "none" : "block"; }
    var wrap3 = document.getElementById("ndAiGate");
    if (wrap3) { wrap3.style.display = aiOn ? "none" : "block"; }
    /* AI 日志·网络压测总结子项依赖中心：未连禁勾标注，恢复后重采（2026-09-10 优化7/8） */
    var cbStress = document.getElementById("ndAiChk-net_stress");
    if (cbStress) {
        cbStress.disabled = !on;
        var stS = document.getElementById("ndAiSt-net_stress");
        if (stS && !on) { stS.textContent = "需连接中心"; }
        else if (stS && on && stS.textContent === "需连接中心") { ndAiCollectNow("net_stress"); }
        ndAiSyncNetGroup();
    }
}

/* ===================== 配置加载 ===================== */

function ndLoadConfig() {
    ndApiFetch("/api/netdoctor/config").then(function (d) {
        if (d && d.success !== false) {
            ndState.nodes = d.nodes || [];
            ndState.expectedDns = d.expected_dns || [];
            ndState.aiPersonal = d.ai_personal || {};   /* {api_url, model, has_key}，key 不回显 */
        }
        ndRenderNodes();
    }).catch(function () { ndRenderNodes(); });
}

function ndRenderNodes() {
    var el = document.getElementById("ndNodesBody");
    if (!el) { return; }
    var rows = "";
    for (var i = 0; i < ndState.nodes.length; i++) {
        var n = ndState.nodes[i];
        var method = n.method === "nslookup" ? "DNS 解析" : (n.method === "ntp" ? "NTP 校时" : "ICMP ping");
        var target = n.target || (n.key === "gateway" ? "（动态取本地网关）" :
            n.key === "center" ? "（动态取中心服务器）" : "--");
        rows += '<tr data-ndkey="' + ndEscapeHtml(n.key) + '">'
            + '<td>' + ndEscapeHtml(n.name) + '</td>'
            + '<td>' + ndEscapeHtml(target) + '</td>'
            + '<td>' + method + '</td>'
            + '<td>' + ndStatusBadge("muted") + '</td>'
            + '<td class="nd-num nd-rlat">--</td>'
            + '<td class="nd-num nd-rloss">--</td>'
            + '<td class="nd-rdetail nd-hint">待检测</td></tr>';
    }
    if (!rows) { rows = '<tr><td colspan="7" class="nd-empty">未配置节点</td></tr>'; }
    el.innerHTML = rows;
    var tip = document.getElementById("ndDnsBaselineTip");
    if (tip) {
        tip.textContent = ndState.expectedDns.length
            ? ("DNS 基线：" + ndState.expectedDns.join("，"))
            : "DNS 基线未配置（app_config.netdoctor.expected_dns 为空），核查仅展示实测值";
    }
}

/* ===================== 后台任务轮询 ===================== */

function ndPollTask(taskId, onProgress, onDone) {
    var deadline = Date.now() + 5 * 60 * 1000;   /* 上限 5 分钟 */
    var tick = function () {
        ndApiFetch("/api/netdoctor/task-status?task_id=" + encodeURIComponent(taskId))
            .then(function (d) {
                var t = d && d.task;
                if (!t) { onDone && onDone({ error: "任务不存在或已过期" }); return; }
                if (t.status === "running") {
                    if (Date.now() > deadline) {
                        onDone && onDone({ error: "任务超时" });
                        return;
                    }
                    onProgress && onProgress(t);
                    ndState.pollers[taskId] = setTimeout(tick, 800);
                    return;
                }
                if (t.status === "done") { onDone && onDone(null, t.result); return; }
                onDone && onDone({ error: t.error || "任务失败" });
            })
            .catch(function (e) { onDone && onDone({ error: String(e) }); });
    };
    tick();
}

function ndStopPoller(taskId) {
    if (ndState.pollers[taskId]) {
        clearTimeout(ndState.pollers[taskId]);
        delete ndState.pollers[taskId];
    }
}

/* ===================== ① 配置核查 ===================== */

function ndStartConfigCheck() {
    var btn = document.getElementById("ndConfBtn");
    if (btn) { btn.disabled = true; }
    ndSetTip("ndConfSummary", "采集中（ipconfig /all）…");
    ndApiFetch("/api/netdoctor/config-check").then(function (d) {
        if (!d || d.success === false) {
            ndSetTip("ndConfSummary", "启动失败：" + (d && d.error ? d.error : "未知错误"));
            if (btn) { btn.disabled = false; }
            return;
        }
        ndState.confTaskId = d.task_id;
        ndPollTask(d.task_id, null, function (err, result) {
            if (btn) { btn.disabled = false; }
            if (err) { ndSetTip("ndConfSummary", "核查失败：" + err.error); return; }
            ndRenderConfig(result);
        });
    }).catch(function (e) {
        if (btn) { btn.disabled = false; }
        ndSetTip("ndConfSummary", "核查失败：" + String(e));
    });
}

function ndAdapterCard(a) {
    /* 单网卡卡片：权威配置明细（与主页 ipconfig /all 口径一致）+ 核查结论行 */
    var head = '<div class="nd-adapter-head"><b>' + ndEscapeHtml(a.name) + '</b>';
    head += a.active ? ndBadge("活动", "nd-info") : ndBadge("非活动", "nd-muted");
    if (a.tunnel) { head += ndBadge("隧道", "nd-muted"); }
    head += '</div>';
    var meta = '<div class="nd-kv"><span class="k">描述</span><span>' + ndEscapeHtml(a.desc || "--") + '</span></div>';
    if (a.mac) { meta += '<div class="nd-kv"><span class="k">MAC</span><span>' + ndEscapeHtml(a.mac) + '</span></div>'; }
    meta += '<div class="nd-kv"><span class="k">链路速率</span><span>' + ndEscapeHtml(a.speed || "--") + '</span></div>';
    meta += '<div class="nd-kv"><span class="k">IPv4</span><span>' +
        ndEscapeHtml((a.ipv4 && a.ipv4.length) ? a.ipv4.join("，") : "--") + '</span></div>';
    meta += '<div class="nd-kv"><span class="k">子网掩码</span><span>' +
        ndEscapeHtml((a.subnet && a.subnet.length) ? a.subnet.join("，") : "--") + '</span></div>';
    meta += '<div class="nd-kv"><span class="k">IPv6</span><span>' +
        ndEscapeHtml((a.ipv6 && a.ipv6.length) ? a.ipv6.join("，") : "--") + '</span></div>';
    meta += '<div class="nd-kv"><span class="k">DHCP 启用</span><span>' +
        (a.dhcp === true ? "是" : (a.dhcp === false ? "否" : "--")) + '</span></div>';
    meta += '<div class="nd-kv"><span class="k">DHCP 服务器</span><span>' + ndEscapeHtml(a.dhcp_server || "--") + '</span></div>';
    meta += '<div class="nd-kv"><span class="k">租约获取</span><span>' + ndEscapeHtml(a.lease_obtained || "--") + '</span></div>';
    meta += '<div class="nd-kv"><span class="k">租约到期</span><span>' + ndEscapeHtml(a.lease_expires || "--") + '</span></div>';
    meta += '<div class="nd-kv"><span class="k">DNS 服务器</span><span>' +
        ndEscapeHtml((a.dns && a.dns.length) ? a.dns.join("，") : "--") + '</span></div>';
    meta += '<div class="nd-kv"><span class="k">网关</span><span>' +
        ndEscapeHtml((a.gateway && a.gateway.length) ? a.gateway.join("，") : "--") + '</span></div>';
    meta += '<div class="nd-kv"><span class="k">WINS</span><span>' +
        ndEscapeHtml((a.wins && a.wins.length) ? a.wins.join("，") : "--") + '</span></div>';
    var rows = "";
    for (var j = 0; j < (a.checks || []).length; j++) {
        var c = a.checks[j];
        rows += '<div class="nd-check-row">' + ndStatusBadge(c.status)
            + '<b>' + ndEscapeHtml(c.item) + '</b>'
            + '<span class="nd-hint">' + ndEscapeHtml(c.reason || "") + '</span></div>';
    }
    return '<div class="nd-adapter' + (a.active ? "" : " nd-dim") + '">' + head + meta + rows + '</div>';
}

function ndToggleInactive() {
    var wrap = document.getElementById("ndInactiveWrap");
    var btn = document.getElementById("ndInactiveToggle");
    if (!wrap || !btn) { return; }
    var show = wrap.style.display === "none";
    wrap.style.display = show ? "block" : "none";
    btn.textContent = show ? "收起非活动网卡" : btn.getAttribute("data-label") || "查看非活动网卡";
}

function ndRenderConfig(r) {
    var el = document.getElementById("ndConfBody");
    if (!r || r.success === false) {
        ndSetTip("ndConfSummary", "核查失败：" + ((r && r.error) || "未知"));
        return;
    }
    var overall = r.overall || {};
    var cls = overall.status === "ok" ? "nd-ok" : (overall.status === "err" ? "nd-err" : "nd-warn");
    ndSetTip("ndConfSummary", "");
    var badge = document.getElementById("ndConfBadge");
    if (badge) { badge.innerHTML = ndBadge(overall.text || "--", cls); }
    var adapters = r.adapters || [];
    var actives = [], inactives = [];
    for (var i = 0; i < adapters.length; i++) {
        (adapters[i].active ? actives : inactives).push(adapters[i]);
    }
    var html = "";
    for (var k = 0; k < actives.length; k++) { html += ndAdapterCard(actives[k]); }
    if (!actives.length) {
        html = '<div class="nd-empty">未发现活动网卡</div>';
    }
    if (inactives.length) {
        var label = "查看 " + inactives.length + " 个非活动网卡";
        html += '<button class="nd-btn" id="ndInactiveToggle" data-label="' + ndEscapeHtml(label)
            + '" onclick="ndToggleInactive()">' + ndEscapeHtml(label) + '</button>';
        html += '<div id="ndInactiveWrap" style="display:none">';
        for (var m = 0; m < inactives.length; m++) { html += ndAdapterCard(inactives[m]); }
        html += '</div>';
    }
    el.innerHTML = html;
}

/* ===================== ② IP 冲突检测 ===================== */

function ndStartConflict() {
    var btn = document.getElementById("ndConflictBtn");
    if (btn) { btn.disabled = true; }
    ndSetTip("ndConflictSummary", "向中心查询中…");
    ndApiFetch("/api/netdoctor/ipconflict").then(function (d) {
        if (!d || d.success === false) {
            ndSetTip("ndConflictSummary", "启动失败：" + (d && d.error ? d.error : "未知"));
            if (btn) { btn.disabled = false; }
            return;
        }
        ndState.conflictTaskId = d.task_id;
        ndPollTask(d.task_id, null, function (err, result) {
            if (btn) { btn.disabled = false; }
            if (err) { ndSetTip("ndConflictSummary", "检测失败：" + err.error); return; }
            ndRenderConflict(result);
        });
    }).catch(function (e) {
        if (btn) { btn.disabled = false; }
        ndSetTip("ndConflictSummary", "检测失败：" + String(e));
    });
}

function ndRenderConflict(r) {
    var el = document.getElementById("ndConflictBody");
    if (!r || r.success === false) {
        ndSetTip("ndConflictSummary", "检测失败：" + ((r && r.error) || "未知"));
        return;
    }
    if (r.error === "not_connected") {
        ndSetTip("ndConflictSummary", "");
        el.innerHTML = '<div class="nd-empty">未连接中心平台，功能不可用（设置中可开启平台接入）</div>';
        return;
    }
    var html = '<div class="nd-kv"><span class="k">检测对象</span><span>'
        + ndEscapeHtml(r.adapter || "--") + ' ｜ IP ' + ndEscapeHtml(r.ip || "--")
        + ' ｜ MAC ' + ndEscapeHtml(r.mac || "--")
        + (r.object_note ? '<br><span class="nd-hint">' + ndEscapeHtml(r.object_note) + '</span>' : "")
        + '</span></div>';
    var v = r.verdict;
    if (!v) {
        html += '<div class="nd-empty">平台未返回判定：' + ndEscapeHtml(r.error || "--") + '</div>';
    } else if (v.conflict_suspect) {
        html += '<div style="margin:8px 0">' + ndBadge("疑似 IP 冲突", "nd-err") + '</div>';
        if (v.suspect_reasons && v.suspect_reasons.length) {
            html += '<div class="nd-hint">判定原因：' + ndEscapeHtml(v.suspect_reasons.join("、")) + '</div>';
        }
        var ev = v.evidence || [];
        if (ev.length) {
            html += '<div class="nd-hint">证据（窗口 ' + ndEscapeHtml(v.window_days || "--") + ' 天）：</div><ul class="nd-evlist">';
            for (var i = 0; i < ev.length; i++) {
                html += '<li>' + ndEscapeHtml(typeof ev[i] === "string" ? ev[i] : JSON.stringify(ev[i])) + '</li>';
            }
            html += '</ul>';
        }
        var src = v.sources || {};
        var srcBits = [];
        var srcNames = [["terminal_reports", "终端上报"], ["admission_log", "准入日志"], ["core_switch_state", "核心交换状态"]];
        for (var s = 0; s < srcNames.length; s++) {
            var sv = src[srcNames[s][0]];
            srcBits.push(srcNames[s][1] + "：" + (sv === undefined ? "--" :
                (sv === true || sv === "ok" ? "已接入" : (sv === false || sv === "not_connected" ? "未接入" : String(sv)))));
        }
        html += '<div class="nd-hint">数据源：' + ndEscapeHtml(srcBits.join(" ｜ ")) + '</div>';
        html += ndRenderAiAssist(r.ai);
    } else {
        html += '<div style="margin:8px 0">' + ndBadge("未发现冲突疑似", "nd-ok") + '</div>';
        if (v.evidence && v.evidence.length) {
            html += '<div class="nd-hint">平台说明：' + ndEscapeHtml(v.evidence.join("；")) + '</div>';
        }
    }
    /* 网卡变更史（verdict.nic_history，不构成冲突，折叠小节） */
    var nic = v.nic_history || [];
    if (nic.length) {
        html += '<div class="nd-ai" style="cursor:pointer" onclick="ndToggleNicHist()"><b>网卡变更史（'
            + nic.length + ' 条，不构成冲突）</b><span class="nd-hint" id="ndNicHistArrow"> ▸ 展开</span>'
            + '<div id="ndNicHistBody" style="display:none; margin-top:6px">';
        for (var n = 0; n < nic.length; n++) {
            html += '<div class="nd-hint">· ' + ndEscapeHtml(
                typeof nic[n] === "string" ? nic[n] : JSON.stringify(nic[n])) + '</div>';
        }
        html += '</div></div>';
    }
    ndSetTip("ndConflictSummary", "");
    /* 深度检测入口（2026-09-10 对接服务端 ipconflict-deep，契约 9e604a7+cc34bae：
       平台侧编排 resolve/arp/nad/macaddr/conclude 五步，全链约 15-30s） */
    ndState.conflictLast = r;
    html += '<div class="nd-ai" id="ndDeepWrap"><b>深度检测</b>'
        + '<span class="nd-hint">由中心编排网关 ARP / 准入 / 接入交换机 MAC 交叉核验（约 15-30s）</span>'
        + '<div style="margin-top:8px">'
        + '<button class="nd-btn primary" id="ndDeepBtn" onclick="ndStartDeep()">发起深度检测</button>'
        + '<span class="nd-hint" id="ndDeepTip"></span></div>'
        + '<div id="ndDeepBody"></div></div>';
    /* AI 分析手动按钮（2026-09-10：数据更新后可重跑，服务端聚合最新多方证据） */
    html += '<div class="nd-ai" id="ndAiReWrap"><b>AI 分析（平台多方证据聚合）</b>'
        + '<span class="nd-hint">依托平台算力结合最新多方日志分析冲突，耗时最长约 45s</span>'
        + '<div style="margin-top:8px">'
        + '<button class="nd-btn primary" id="ndAiReBtn" onclick="ndReanalyzeAi()">AI 分析</button>'
        + '<span class="nd-hint" id="ndAiReTip"></span></div>'
        + '<div id="ndAiReBody"></div></div>';
    el.innerHTML = html;
}

function ndRenderAiAssist(ai) {
    /* AI 辅助分析结果片段（自动触发与手动重跑共用）：文本 + model 徽章 + analysis_id 可追溯 */
    if (!ai) { return ""; }
    var meta = "";
    if (ai.model) { meta += '<span class="nd-hint"> [' + ndEscapeHtml(String(ai.model)) + ']</span>'; }
    if (ai.analysis_id) { meta += '<span class="nd-hint"> #' + ndEscapeHtml(String(ai.analysis_id)) + '</span>'; }
    return '<div class="nd-ai"><b>AI 辅助分析</b>' + meta
        + (ai.ok ? '<pre class="nd-pre">' + ndEscapeHtml(ai.analysis || "（无内容）") + '</pre>'
                 : '<div class="nd-hint">分析失败：' + ndEscapeHtml(ai.error || "--") + '</div>')
        + '</div>';
}

function ndAiReBtnReset() {
    var b = document.getElementById("ndAiReBtn");
    if (b) { b.disabled = false; }
}

function ndReanalyzeAi() {
    var last = ndState.conflictLast;
    if (!last || !last.ip || !last.mac) { return; }
    var btn = document.getElementById("ndAiReBtn");
    if (btn) { btn.disabled = true; }
    var body = document.getElementById("ndAiReBody");
    if (body) { body.innerHTML = ""; }
    ndSetTip("ndAiReTip", "分析中…（最长约 45s）");
    var ev = (last.verdict && last.verdict.evidence) || [];
    var q = "?ip=" + encodeURIComponent(last.ip) + "&mac=" + encodeURIComponent(last.mac)
        + "&evidence_json=" + encodeURIComponent(JSON.stringify(ev.slice(0, 12)));
    ndApiFetch("/api/netdoctor/conflict-ai-reanalyze" + q).then(function (d) {
        if (!d || d.success === false) {
            ndSetTip("ndAiReTip", "分析失败：" + ((d && d.error) || "未知") + "（可重试）");
            ndAiReBtnReset();
            return;
        }
        ndSetTip("ndAiReTip", "分析完成");
        if (body) { body.innerHTML = ndRenderAiAssist(d.ai); }
        ndAiReBtnReset();
    }).catch(function (e) {
        ndSetTip("ndAiReTip", "分析失败：" + String(e) + "（可重试）");
        ndAiReBtnReset();
    });
}

function ndToggleNicHist() {
    var b = document.getElementById("ndNicHistBody");
    var a = document.getElementById("ndNicHistArrow");
    if (!b) { return; }
    var show = b.style.display === "none";
    b.style.display = show ? "block" : "none";
    if (a) { a.textContent = show ? " ▾ 收起" : " ▸ 展开"; }
}

/* ===================== ②.b IP 冲突深度检测（平台编排，本地轮询转发） ===================== */

function ndDeepBtnReset() {
    var b = document.getElementById("ndDeepBtn");
    if (b) { b.disabled = false; }
}

function ndStartDeep() {
    var last = ndState.conflictLast;
    if (!last || !last.ip || !last.mac) { return; }
    var btn = document.getElementById("ndDeepBtn");
    if (btn) { btn.disabled = true; }
    var body = document.getElementById("ndDeepBody");
    if (body) { body.innerHTML = ""; }
    ndSetTip("ndDeepTip", "任务创建中…");
    var q = "?ip=" + encodeURIComponent(last.ip) + "&mac=" + encodeURIComponent(last.mac);
    ndApiFetch("/api/netdoctor/conflict-deep-start" + q).then(function (d) {
        if (!d || d.success === false) {
            var emap = { not_connected: "未连接中心", busy: "中心检测任务忙，请稍后重试",
                         invalid_ip: "IP 非法", not_registered: "终端未注册",
                         no_active_adapter: "无活动网卡" };
            ndSetTip("ndDeepTip", "发起失败：" + (emap[d && d.error] || (d && d.error) || "未知"));
            ndDeepBtnReset();
            return;
        }
        ndState.deepTaskId = d.task_id;
        ndState.deepRound = 0;
        ndSetTip("ndDeepTip", "任务 " + d.task_id + " 已创建，编排中…");
        ndDeepPollTick();
    }).catch(function (e) {
        ndSetTip("ndDeepTip", "发起失败：" + String(e));
        ndDeepBtnReset();
    });
}

function ndDeepPollTick() {
    var tid = ndState.deepTaskId;
    if (!tid) { return; }
    /* 2.5s × 60 轮 = 150s 上限（服务端实测 9-30s 完成，宽裕量防网络抖动） */
    if ((ndState.deepRound || 0) > 60) {
        ndSetTip("ndDeepTip", "深度检测超时，可稍后重试");
        ndDeepBtnReset();
        return;
    }
    ndApiFetch("/api/netdoctor/conflict-deep-poll?task_id=" + encodeURIComponent(tid))
        .then(function (d) {
            if (!d || d.success === false) {
                ndSetTip("ndDeepTip", "轮询失败：" + ((d && d.error) || "未知"));
                ndDeepBtnReset();
                return;
            }
            var t = d.task || {};
            if (t.status === "running") {
                ndRenderDeep(t, true);
                ndState.deepRound = (ndState.deepRound || 0) + 1;
                window.__ndDeepTimer = window.setTimeout(ndDeepPollTick, 2500);
                return;
            }
            ndRenderDeep(t, false);
            ndSetTip("ndDeepTip",
                t.status === "done" ? "深度检测完成" : ("深度检测失败：" + (t.error || "未知")));
            ndDeepBtnReset();
        }).catch(function () {
            /* 瞬时网络抖动容错：不终止轮询，计入轮次由上限兜底 */
            ndState.deepRound = (ndState.deepRound || 0) + 1;
            window.__ndDeepTimer = window.setTimeout(ndDeepPollTick, 2500);
        });
}

function ndFmtEpoch(sec) {
    /* epoch 秒 → 本地时区 YYYY-MM-DD HH:MM（server-platform 2026-09-10 建议格式化）；
       非法值返回空串由调用方回退原值展示 */
    var n = Number(sec);
    if (!n || !isFinite(n)) { return ""; }
    var d = new Date(n * 1000);
    function p2(x) { return (x < 10 ? "0" : "") + x; }
    return d.getFullYear() + "-" + p2(d.getMonth() + 1) + "-" + p2(d.getDate())
        + " " + p2(d.getHours()) + ":" + p2(d.getMinutes());
}

function ndDeepStepBadge(s) {
    var map = { done: ["nd-ok", "已完成"], skipped: ["nd-muted", "跳过"],
                failed: ["nd-err", "失败"], empty: ["nd-warn", "无数据"],
                match: ["nd-ok", "匹配"], mismatch: ["nd-err", "不匹配"],
                multi: ["nd-warn", "多端口（漂移信号）"], running: ["nd-warn", "进行中"] };
    var m = map[s] || ["nd-muted", s || "--"];
    return ndBadge(m[1], m[0]);
}

function ndRenderDeep(t, running) {
    var el = document.getElementById("ndDeepBody");
    if (!el) { return; }
    var html = "";
    var steps = t.steps || [];
    if (steps.length) {
        html += '<div class="nd-steps">';
        for (var i = 0; i < steps.length; i++) {
            var s = steps[i];
            html += '<div class="nd-step"><div class="nd-step-head">'
                + ndDeepStepBadge(s.status)
                + '<b>' + ndEscapeHtml(s.name || s.step || "--") + '</b>'
                + (s.target ? '<span class="nd-hint">目标 ' + ndEscapeHtml(s.target) + '</span>' : "")
                + '</div>';
            if (s.note) { html += '<div class="nd-hint">' + ndEscapeHtml(s.note) + '</div>'; }
            if (s.zone) { html += '<div class="nd-hint">区域：' + ndEscapeHtml(s.zone) + '</div>'; }
            if (s.banner) { html += '<div class="nd-hint">' + ndEscapeHtml(s.banner) + '</div>'; }
            var ev = s.evidence || [];
            if (ev.length) {
                html += '<ul class="nd-evlist">';
                for (var e2 = 0; e2 < ev.length; e2++) {
                    html += '<li><code>' + ndEscapeHtml(
                        typeof ev[e2] === "string" ? ev[e2] : JSON.stringify(ev[e2])) + '</code></li>';
                }
                html += '</ul>';
            }
            var cmds = s.commands || [];
            if (cmds.length) {
                html += '<details class="nd-cmds"><summary>执行命令 ' + cmds.length + ' 条</summary>';
                for (var c = 0; c < cmds.length; c++) {
                    html += '<div class="nd-hint">$ ' + ndEscapeHtml(cmds[c].cmd || "")
                        + (cmds[c].ok === false ? "（失败）" : "") + '</div>';
                    if (cmds[c].output_tail) {
                        html += '<pre class="nd-pre">' + ndEscapeHtml(cmds[c].output_tail) + '</pre>';
                    }
                }
                html += '</details>';
            }
            html += '</div>';
        }
        html += '</div>';
    } else if (running) {
        html += '<div class="nd-hint">任务编排中…</div>';
    }
    var v = t.verdict;
    if (v) {
        var cmap = { confirmed: ["nd-err", "确认 IP 冲突"], suspect: ["nd-warn", "疑似 IP 冲突"],
                     normal: ["nd-ok", "无冲突"], insufficient_evidence: ["nd-muted", "证据不足"] };
        var cm = cmap[v.conclusion] || ["nd-muted", v.conclusion || "--"];
        html += '<div style="margin:8px 0">' + ndBadge(cm[1], cm[0]) + '</div>';
        if (v.ip || v.mac) {
            html += '<div class="nd-hint">核验对象：IP ' + ndEscapeHtml(v.ip || "--")
                + ' ｜ MAC ' + ndEscapeHtml(v.mac || "--") + '</div>';
        }
        var rs = v.reasons || [];
        if (rs.length) {
            html += '<div class="nd-hint">结论依据：' + ndEscapeHtml(rs.join("、")) + '</div>';
        }
        var src = v.sources || {};
        var srcNames = [["gateway_arp", "网关 ARP"], ["admission", "准入系统"],
                        ["access_mac", "接入交换机 MAC"]];
        var sb = [];
        for (var s3 = 0; s3 < srcNames.length; s3++) {
            var sv = src[srcNames[s3][0]];
            sb.push(srcNames[s3][1] + "：" + (sv === undefined || sv === null ? "--" : String(sv)));
        }
        html += '<div class="nd-hint">数据源：' + ndEscapeHtml(sb.join(" ｜ ")) + '</div>';
        if (v.checked_at) {
            var ct = ndFmtEpoch(v.checked_at);
            html += '<div class="nd-hint">核验时间：' + ndEscapeHtml(ct || String(v.checked_at)) + '</div>';
        }
        if (v.conclusion === "insufficient_evidence") {
            /* 如实降级语义（server-platform 2026-09-10 契约补充）：三源含 skipped/failed
               时平台判 insufficient_evidence，属证据不足而非故障，提示看时间线各步骤原因 */
            html += '<div class="nd-hint">证据不足（各步骤原因见时间线）：部分数据源未接入或核验未命中，'
                + '此结论不代表网络无冲突。</div>';
        }
    }
    el.innerHTML = html;
}

/* ===================== ③ 连通性测试 ===================== */

function ndStartPing() {
    var btn = document.getElementById("ndPingBtn");
    if (btn) { btn.disabled = true; }
    ndRenderNodes();   /* 重置为待检测 */
    ndApiFetch("/api/netdoctor/ping-start").then(function (d) {
        if (!d || d.success === false) {
            ndSetTip("ndPingSummary", "启动失败：" + (d && d.error ? d.error : "未知"));
            if (btn) { btn.disabled = false; }
            return;
        }
        ndState.pingTaskId = d.task_id;
        ndPollTask(d.task_id, function (t) {
            var p = t.progress || {};
            ndSetTip("ndPingSummary", "检测中 " + (p.done || 0) + "/" + (p.total || ndState.nodes.length)
                + (p.current ? " ｜ " + p.current : ""));
            if (p.results) { ndRenderPingRows(p.results); }
        }, function (err, result) {
            if (btn) { btn.disabled = false; }
            if (err) { ndSetTip("ndPingSummary", "检测失败：" + err.error); return; }
            ndRenderPing(result);
        });
    }).catch(function (e) {
        if (btn) { btn.disabled = false; }
        ndSetTip("ndPingSummary", "检测失败：" + String(e));
    });
}

function ndRenderPingRows(results) {
    var el = document.getElementById("ndNodesBody");
    if (!el) { return; }
    for (var i = 0; i < results.length; i++) {
        var r = results[i];
        var tr = el.querySelector('tr[data-ndkey="' + r.key + '"]');
        if (!tr) { continue; }
        tr.cells[3].innerHTML = ndStatusBadge(r.status);
        tr.cells[4].textContent = ndFmtMs(r.avg_ms);
        tr.cells[5].textContent = ndFmtPct(r.loss_pct);
        tr.cells[6].textContent = r.detail || "";
    }
}

function ndRenderPing(r) {
    if (!r || r.success === false) {
        ndSetTip("ndPingSummary", "检测失败：" + ((r && r.error) || "未知"));
        return;
    }
    ndState.lastPing = r;
    ndRenderPingRows(r.results || []);
    var s = r.summary || {};
    ndSetTip("ndPingSummary", "完成：" + s.ok + " 正常 / " + s.err + " 异常" +
        (s.muted ? " / " + s.muted + " 未连接" : "") +
        "（结果已记录到 netdoctor_records）");
    if (ndState.inited) { ndAiCollectNow("net_conn"); }   /* AI 数据源联动刷新（连通性） */
}

function ndLoadHistory() {
    ndApiFetch("/api/netdoctor/ping-history?limit=100").then(function (d) {
        ndRenderHistory(d);
    }).catch(function () { ndRenderHistory(null); });
}

function ndRenderHistory(d) {
    var el = document.getElementById("ndHistoryBody");
    if (!el) { return; }
    if (d && d.agg) { ndState.historyAgg = d.agg; }
    var agg = (d && d.agg) || [];
    if (!agg.length) {
        el.innerHTML = '<div class="nd-empty">暂无历史记录（每次检测自动留档）</div>';
        return;
    }
    var html = '<table class="nd-table"><tr><th>节点</th><th>样本</th><th>成功率</th><th>平均延迟</th><th>最大延迟</th></tr>';
    for (var i = 0; i < agg.length; i++) {
        var a = agg[i];
        /* 2026-09-09 语义修复：ok=探测通道成功（可达/可用率），warn（偏差过大）单独标注 */
        var note = (a.warn_n > 0)
            ? ' <span class="nd-hint">（' + a.warn_n + ' 次可达 · 偏差过大）</span>' : "";
        html += '<tr><td>' + ndEscapeHtml(a.key) + '</td><td class="nd-num">' + a.n
            + '</td><td class="nd-num">' + ndFmtPct(a.ok_rate) + note
            + '</td><td class="nd-num">' + ndFmtMs(a.avg_ms)
            + '</td><td class="nd-num">' + ndFmtMs(a.max_ms) + '</td></tr>';
    }
    html += '</table>';
    el.innerHTML = html;
}

/* ===================== ④ 路由追踪 ===================== */

function ndStartTracert() {
    var input = document.getElementById("ndTracertTarget");
    var target = (input && input.value || "").trim();
    if (!target) { ndSetTip("ndTracertSummary", "请输入目的 IP 或域名"); return; }
    var btn = document.getElementById("ndTracertBtn");
    if (btn) { btn.disabled = true; }
    ndSetTip("ndTracertSummary", "追踪中（最长约 1 分钟）…");
    var body = document.getElementById("ndTracertBody");
    if (body) { body.innerHTML = '<div class="nd-empty">追踪中…</div>'; }
    ndApiFetch("/api/netdoctor/tracert-start?target=" + encodeURIComponent(target)).then(function (d) {
        if (!d || d.success === false) {
            ndSetTip("ndTracertSummary", "启动失败：" + (d && d.error ? d.error : "未知"));
            if (btn) { btn.disabled = false; }
            return;
        }
        ndState.tracertTaskId = d.task_id;
        ndPollTask(d.task_id, function (t) {
            var p = t.progress || {};
            if (p.stage === "tracing" && p.kb !== undefined) {
                ndSetTip("ndTracertSummary", "追踪中… 知识库 " + p.kb + " 条");
            }
        }, function (err, result) {
            if (btn) { btn.disabled = false; }
            if (err) { ndSetTip("ndTracertSummary", "追踪失败：" + err.error); return; }
            ndRenderTracert(result);
        });
    }).catch(function (e) {
        if (btn) { btn.disabled = false; }
        ndSetTip("ndTracertSummary", "追踪失败：" + String(e));
    });
}

function ndRenderTracert(r) {
    var el = document.getElementById("ndTracertBody");
    if (!r || r.success === false) {
        ndSetTip("ndTracertSummary", "追踪失败：" + ((r && r.error) || "未知"));
        if (el) { el.innerHTML = '<div class="nd-empty">无结果</div>'; }
        return;
    }
    ndState.lastTracert = r;
    if (ndState.inited) { ndAiCollectNow("net_tracert"); }   /* AI 数据源联动刷新（追踪） */
    ndSetTip("ndTracertSummary", "完成：共 " + (r.hops || []).length + " 跳 ｜ 知识库节点 "
        + r.kb_count + " 条" + (r.kb_error ? "（知识库不可用：" + r.kb_error + "）" : ""));
    var html = '<table class="nd-table"><tr><th>跳数</th><th>延迟</th><th>IP</th><th>主机名</th><th>所属区域</th></tr>';
    for (var i = 0; i < (r.hops || []).length; i++) {
        var h = r.hops[i];
        var delays = (h.delays || []).join("　");
        if (h.timeout) { delays = '* * *'; }
        html += '<tr><td class="nd-num">' + h.hop
            + '</td><td class="nd-num">' + ndEscapeHtml(delays)
            + '</td><td>' + ndEscapeHtml(h.ip || (h.timeout ? "请求超时" : "--"))
            + '</td><td>' + ndEscapeHtml(h.host || "--")
            + '</td><td>' + (h.zone ? ndBadge(h.zone, "nd-info") : '<span class="nd-hint">—</span>')
            + (h.zone_desc ? ' <span class="nd-hint">' + ndEscapeHtml(h.zone_desc) + '</span>' : "")
            + '</td></tr>';
    }
    html += '</table>';
    el.innerHTML = html;
}

/* ===================== ⑤ 网络压测 ===================== */

function ndReadSizes() {
    /* 包长档位 4 框读取与校验（2026-09-10 用户要求重构：单逗号串框 → 分档文本框）：
       正整数、范围 1~65500、空框跳过（少于 4 档合法）、重复值去重保序。
       返回 {sizes: 逗号串, err: 行内提示（空串=合法）}；全空按默认档兜底。存储格式仍逗号串，后端零改动。 */
    var out = [];
    for (var i = 1; i <= 4; i++) {
        var el = document.getElementById("ndStressSize" + i);
        var v = el ? String(el.value).trim() : "";
        if (!v) { continue; }
        if (!/^\d+$/.test(v)) { return { sizes: "", err: "包长 " + i + "（" + v + "）不是正整数" }; }
        var n = parseInt(v, 10);
        if (n < 1 || n > 65500) { return { sizes: "", err: "包长 " + i + "（" + v + "）超出范围 1~65500" }; }
        if (out.indexOf(n) < 0) { out.push(n); }
    }
    return { sizes: out.join(","), err: "" };
}

function ndSizesEcho() {
    /* 页面重开回显：localStorage.ndStressSizes（逗号串）按序回填 4 框，缺段保留默认值 */
    var saved = "";
    try { saved = localStorage.getItem("ndStressSizes") || ""; } catch (e) { saved = ""; }
    if (!saved) { return; }
    var parts = saved.split(",");
    for (var i = 0; i < 4 && i < parts.length; i++) {
        var el = document.getElementById("ndStressSize" + (i + 1));
        if (el && parts[i]) { el.value = parts[i]; }
    }
}

function ndStartStress() {
    var btn = document.getElementById("ndStressBtn");
    var cancelBtn = document.getElementById("ndStressCancelBtn");
    var sr = ndReadSizes();
    var errEl = document.getElementById("ndStressSizesErr");
    if (errEl) { errEl.textContent = sr.err; }
    if (sr.err) {
        ndSetTip("ndStressSummary", "包长档位有误，请修正后重试");
        return;   /* 其它参数不受影响，修正后可立即重试 */
    }
    if (btn) { btn.disabled = true; }
    if (cancelBtn) { cancelBtn.style.display = "inline-block"; }
    var dur = document.getElementById("ndStressDur");
    var udp = document.getElementById("ndStressUdp");
    var q = "?duration_sec=" + encodeURIComponent(dur ? dur.value : "10")
        + "&sizes=" + encodeURIComponent(sr.sizes || "64,256,1024,4096")
        + "&udp_mbps=" + encodeURIComponent(udp ? udp.value : "100");
    try { localStorage.setItem("ndStressSizes", sr.sizes || "64,256,1024,4096"); } catch (e) {}
    ndApiFetch("/api/netdoctor/stress-start" + q).then(function (d) {
        if (!d || d.success === false) {
            ndSetTip("ndStressSummary", "启动失败：" + (d && d.error === "not_connected"
                ? "未连接中心" : (d && d.error) || "未知"));
            if (btn) { btn.disabled = false; }
            if (cancelBtn) { cancelBtn.style.display = "none"; }
            return;
        }
        ndState.stressTaskId = d.task_id;
        ndPollTask(d.task_id, function (t) {
            var p = t.progress || {};
            ndSetTip("ndStressSummary", "压测中：" + (p.stage || "--")
                + (p.current ? " ｜ " + p.current : "")
                + "（" + (p.done || 0) + "/" + (p.total || "?") + "）");
        }, function (err, result) {
            if (btn) { btn.disabled = false; }
            if (cancelBtn) { cancelBtn.style.display = "none"; }
            if (err) { ndSetTip("ndStressSummary", "压测失败：" + err.error); return; }
            ndRenderStress(result);
        });
    }).catch(function (e) {
        if (btn) { btn.disabled = false; }
        if (cancelBtn) { cancelBtn.style.display = "none"; }
        ndSetTip("ndStressSummary", "压测失败：" + String(e));
    });
}

function ndCancelStress() {
    if (!ndState.stressTaskId) { return; }
    ndApiFetch("/api/netdoctor/task-cancel?task_id=" + encodeURIComponent(ndState.stressTaskId))
        .then(function () { ndSetTip("ndStressSummary", "已请求取消，等待当前探测收尾…"); })
        .catch(function () {});
}

function ndRenderStress(r) {
    var el = document.getElementById("ndStressBody");
    if (!r || r.success === false) {
        ndSetTip("ndStressSummary", "压测失败：" + ((r && r.error) || "未知"));
        return;
    }
    ndState.lastStressResult = r;
    if (ndState.inited) { ndAiCollectNow("net_stress"); }   /* AI 数据源联动刷新（压测） */
    var v = r.verdict || {};
    var vcls = v.cls === "ok" ? "nd-ok" : (v.cls === "err" ? "nd-err" : "nd-warn");
    var html = '<div style="margin:6px 0">' + ndBadge(v.text || "--", vcls)
        + '<span class="nd-hint" style="margin-left:10px">' + ndEscapeHtml(v.reason || "") + '</span></div>';
    html += '<div class="nd-hint">目标 ' + ndEscapeHtml(r.center || "--")
        + ' ｜ iperf3 每轮 ' + ndEscapeHtml(r.iperf_duration) + 's ｜ UDP 码率 ' + ndEscapeHtml(r.udp_mbps) + 'Mbps</div>';
    html += '<table class="nd-table"><tr><th>包长 ping</th><th>结果</th><th>平均</th><th>最小</th><th>最大</th><th>丢包</th></tr>';
    for (var i = 0; i < (r.ping || []).length; i++) {
        var p = r.ping[i];
        html += '<tr><td class="nd-num">' + p.size + ' B</td>'
            + '<td>' + (p.ok ? ndBadge("通", "nd-ok") : ndBadge("失败", "nd-err"))
            + '</td><td class="nd-num">' + ndFmtMs(p.avg_ms)
            + '</td><td class="nd-num">' + ndFmtMs(p.min_ms)
            + '</td><td class="nd-num">' + ndFmtMs(p.max_ms)
            + '</td><td class="nd-num">' + ndFmtPct(p.loss_pct) + '</td></tr>';
    }
    html += '</table>';
    html += '<table class="nd-table"><tr><th>iperf3</th><th>状态</th><th>结果</th></tr>';
    for (var j = 0; j < (r.iperf || []).length; j++) {
        var x = r.iperf[j];
        if (x.ok) {
            var sm = x.result || {};
            var detail = x.mode === "tcp"
                ? ("聚合 " + ndFmtMs(sm.mbits_sec === undefined ? null : sm.mbits_sec).replace(" ms", " Mbits/sec")
                   + "（intervals 最大 " + (sm.interval_max_mbits !== undefined ? sm.interval_max_mbits : "--")
                   + " / 最小 " + (sm.interval_min_mbits !== undefined ? sm.interval_min_mbits : "--")
                   + " / 平均 " + (sm.interval_avg_mbits !== undefined ? sm.interval_avg_mbits : "--")
                   + " Mbits/sec），重传 " + (sm.retransmits !== undefined ? sm.retransmits : "--"))
                : ("带宽 " + (sm.mbits_sec !== undefined ? sm.mbits_sec : "--") + " Mbits/sec，抖动 "
                   + (sm.jitter_ms !== undefined ? sm.jitter_ms : "--") + " ms，丢包 "
                   + (sm.lost_percent !== undefined ? sm.lost_percent : "--") + "%");
            html += '<tr><td>' + x.mode.toUpperCase() + '</td><td>' + ndBadge("完成", "nd-ok")
                + '</td><td>' + ndEscapeHtml(detail) + '</td></tr>';
        } else {
            html += '<tr><td>' + x.mode.toUpperCase() + '</td><td>' + ndBadge("失败", "nd-err")
                + '</td><td>' + ndEscapeHtml(x.error || "--") + '</td></tr>';
        }
    }
    html += '</table>';
    el.innerHTML = html;
    var exp = document.getElementById("ndStressExportBtn");
    if (exp) { exp.style.display = "inline-block"; }
    ndSetTip("ndStressSummary", "压测完成，可导出 HTML 报告");
}

function ndExportStress() {
    if (!ndState.stressTaskId) { return; }
    ndApiFetch("/api/netdoctor/stress-export?task_id=" + encodeURIComponent(ndState.stressTaskId))
        .then(function (d) {
            if (!d || d.success === false) {
                ndSetTip("ndStressSummary", "导出失败：" + (d && d.error ? d.error : "未知"));
                return;
            }
            ndState.lastStressPath = d.path;
            ndSetTip("ndStressSummary", "已导出：" + d.path);
            var btn = document.getElementById("ndStressOpenBtn");
            if (btn) { btn.style.display = "inline-block"; }
        }).catch(function (e) { ndSetTip("ndStressSummary", "导出失败：" + String(e)); });
}

function ndOpenStressLocation() {
    var p = ndState.lastStressPath;
    if (!p) { return; }
    ndApiFetch("/api/disk/open-location?path=" + encodeURIComponent(p))
        .catch(function () {});
}

/* ===================== 系统设置 · 网络排障节点维护 ===================== */

var ND_DYNAMIC_KEYS = { gateway: 1, center: 1 };   /* 目标动态获取，不可编辑 */

function ndRenderSettings() {
    var host = ndSettingsMountPoint();
    if (!host) { return; }
    var rows = "";
    for (var i = 0; i < ndState.nodes.length; i++) {
        var n = ndState.nodes[i];
        var dyn = !!ND_DYNAMIC_KEYS[n.key];
        rows += '<tr data-ndidx="' + i + '">'
            + '<td><input class="nd-input nd-set-name" value="' + ndEscapeHtml(n.name) + '" style="width:170px"></td>'
            + '<td><select class="nd-select nd-set-method">'
            + ['ping', 'nslookup', 'ntp'].map(function (m) {
                return '<option value="' + m + '"' + (n.method === m ? " selected" : "") + '>' +
                    (m === "ping" ? "ICMP ping" : (m === "nslookup" ? "DNS 解析" : "NTP 校时")) + '</option>';
              }).join("")
            + '</select></td>'
        + '<td>' + (dyn
            ? '<input class="nd-input" value="（动态获取）" disabled title="目标动态获取，不可编辑" style="width:150px">'
            : '<input class="nd-input nd-set-target" value="' + ndEscapeHtml(n.target || "") + '" placeholder="IP 或域名" style="width:150px">')
        + '</td>'
            + '<td><input class="nd-input nd-set-probe" value="' + ndEscapeHtml(n.probe || "") + '" placeholder="如 baidu.com" style="width:110px"></td>'
            + '<td><button class="nd-btn" onclick="ndDelSettingRow(' + i + ')">删除</button></td></tr>';
    }
    var dnsVal = (ndState.expectedDns || []).join(", ");
    var apCfg = ndState.aiPersonal || {};
    /* 2026-09-10 UX 重构：两卡独立（settings-card 样式与 perf 端统一）；文案去实现细节 */
    host.innerHTML =
        '<div class="nd-set-card">'
        + '<b>网络监测配置</b>'
        + '<div class="nd-params" style="margin:8px 0 6px"><label>DNS 基线（逗号分隔，空=仅提示未配置）</label>'
        + '<input class="nd-input" id="ndSetDns" value="' + ndEscapeHtml(dnsVal) + '" style="width:240px"></div>'
        + '<table class="nd-table"><tr><th>节点名称</th><th>方式</th><th>目标</th><th>探测参数</th><th>操作</th></tr>'
        + rows + '</table>'
        + '<div class="nd-params" style="margin-top:6px">'
        + '<button class="nd-btn" onclick="ndAddSettingRow()">＋ 添加节点</button>'
        + '<button class="nd-btn" onclick="ndResetNetSettings()">恢复默认</button>'
        + '<button class="nd-btn primary" id="ndSettingsSaveBtn" onclick="ndSaveNetSettings()">保存</button>'
        + '<span class="nd-hint" id="ndSettingsTip"></span>'
        + '</div></div>'
        + '<div class="nd-set-card">'
        + '<b>AI 诊断 · 个人版（不依赖中心，直连第三方 LLM API）</b>'
        + '<div class="nd-params" style="margin:8px 0 4px"><label>API 地址</label>'
        + '<input class="nd-input" id="ndSetAiUrl" value="' + ndEscapeHtml(apCfg.api_url || "") + '"'
        + ' placeholder="https://llm.eye.ac.cn/v1（自动补全 /chat/completions）" style="width:330px"></div>'
        + '<div class="nd-params" style="margin:4px 0"><label>API Key</label>'
        + '<input class="nd-input" type="password" id="ndSetAiKey" value="" autocomplete="new-password"'
        + ' placeholder="' + (apCfg.has_key ? "已保存（留空=不修改）" : "未保存，填写 sk-...") + '" style="width:220px">'
        + '<label>模型名</label>'
        + '<input class="nd-input" id="ndSetAiModel" value="' + ndEscapeHtml(apCfg.model || "") + '"'
        + ' placeholder="如 deepseek-chat" style="width:150px">'
        + '<button class="nd-btn" id="ndSetAiTestBtn" onclick="ndTestPersonal()">测试连通性</button>'
        + '<span class="nd-hint" id="ndSetAiTestTip"></span></div>'
        + '<div class="nd-params" style="margin-top:8px">'
        + '<button class="nd-btn primary" id="ndSetAiSaveBtn" onclick="ndSaveAiPersonal()">保存个人版配置</button>'
        + '<span class="nd-hint" id="ndSetAiTip"></span></div>'
        + '</div>';
}

/* 设置弹窗卡片化挂载点（2026-09-10）：优先 #ndSettingsHost（perf 端预留容器或旧静态容器）；
   都不存在时回退动态创建并 append 到 appSettingsBody 末尾，保证两卡永远有落点 */
function ndSettingsMountPoint() {
    var host = document.getElementById("ndSettingsHost");
    if (host) { return host; }
    var body = document.getElementById("appSettingsBody");
    if (!body) { return null; }
    host = document.createElement("div");
    host.id = "ndSettingsHost";
    body.appendChild(host);
    return host;
}

function ndDelSettingRow(idx) {
    if (idx >= 0 && idx < ndState.nodes.length) {
        ndState.nodes.splice(idx, 1);
        ndRenderSettings();
    }
}

function ndAddSettingRow() {
    ndState.nodes.push({ key: "custom-" + Date.now(), name: "新节点", method: "ping", target: "" });
    ndRenderSettings();
}

function ndSaveNetSettings() {
    var host = document.getElementById("ndSettingsHost");
    var tip = document.getElementById("ndSettingsTip");
    if (!host) { return; }
    var trs = host.querySelectorAll("tr[data-ndidx]");
    var nodes = [];
    for (var i = 0; i < trs.length; i++) {
        var tr = trs[i];
        var n = ndState.nodes[Number(tr.getAttribute("data-ndidx"))] || {};
        var nameEl = tr.querySelector(".nd-set-name");
        var methodEl = tr.querySelector(".nd-set-method");
        var targetEl = tr.querySelector(".nd-set-target");
        var probeEl = tr.querySelector(".nd-set-probe");
        var node = {
            key: n.key || ("custom-" + i),
            name: (nameEl && nameEl.value || "").trim(),
            method: (methodEl && methodEl.value) || "ping",
            target: (targetEl && targetEl.value || "").trim(),
            probe: (probeEl && probeEl.value || "").trim()
        };
        if (!node.name) { node.name = "节点" + (i + 1); }
        nodes.push(node);
    }
    var dnsRaw = (document.getElementById("ndSetDns") || {}).value || "";
    var dns = dnsRaw.split(/[,，;；\s]+/).filter(function (s) { return !!s; });
    if (tip) { tip.textContent = "保存中…"; }
    /* 2026-09-10 UX 重构：本卡保存只提交节点/DNS 段（个人版配置由 AI 卡独立保存） */
    ndApiFetch("/api/netdoctor/config?nodes_json=" + encodeURIComponent(JSON.stringify(nodes))
        + "&expected_dns_json=" + encodeURIComponent(JSON.stringify(dns)))
        .then(function (d) {
            if (!d || d.success === false) {
                if (tip) { tip.textContent = "保存失败：" + ((d && d.error) || "未知"); }
                return;
            }
            ndState.nodes = d.nodes || nodes;
            ndState.expectedDns = d.expected_dns || dns;
            ndRenderNodes();
            ndRenderSettings();
            if (tip) { tip.textContent = "已保存，连通性检测即时生效"; }
        }).catch(function (e) {
            if (tip) { tip.textContent = "保存失败：" + String(e); }
        });
}

/* AI 个人版卡独立保存：仅提交 ai_personal 段（走 handle_net_config ai_personal_json 通道）；
   api_key 留空 = 不修改已保存 Key（后端保留原值） */
function ndSaveAiPersonal() {
    var tip = document.getElementById("ndSetAiTip");
    var aiKey = ((document.getElementById("ndSetAiKey") || {}).value || "").trim();
    var ai = { api_url: ((document.getElementById("ndSetAiUrl") || {}).value || "").trim(),
               model: ((document.getElementById("ndSetAiModel") || {}).value || "").trim() };
    if (aiKey) { ai.api_key = aiKey; }
    if (tip) { tip.textContent = "保存中…"; }
    ndApiFetch("/api/netdoctor/config?ai_personal_json=" + encodeURIComponent(JSON.stringify(ai)))
        .then(function (d) {
            if (!d || d.success === false) {
                if (tip) { tip.textContent = "保存失败：" + ((d && d.error) || "未知"); }
                return;
            }
            ndState.aiPersonal = d.ai_personal || {};
            ndRenderSettings();
            var tip2 = document.getElementById("ndSetAiTip");
            if (tip2) { tip2.textContent = "已保存，个人版诊断即时生效"; }
        }).catch(function (e) {
            if (tip) { tip.textContent = "保存失败：" + String(e); }
        });
}

/* 个人版连通性测试（轻量 GET /models；用表单当前值，不要求先保存） */
function ndTestPersonal() {
    var tip = document.getElementById("ndSetAiTestTip");
    var url = ((document.getElementById("ndSetAiUrl") || {}).value || "").trim();
    var key = ((document.getElementById("ndSetAiKey") || {}).value || "").trim();
    if (!url) { if (tip) { tip.textContent = "请先填写 API 地址"; } return; }
    if (tip) { tip.textContent = "测试中…"; }
    var q = "/api/netdoctor/ai-personal-test?api_url=" + encodeURIComponent(url)
        + (key ? "&api_key=" + encodeURIComponent(key) : "");
    ndApiFetch(q).then(function (d) {
        if (!d || d.success === false) {
            if (tip) { tip.textContent = "测试失败：" + ((d && d.error) || "未知"); }
            return;
        }
        /* 测试对象透明化（2026-09-10 误判 401 缺陷）：明示本次用了哪个 Key */
        var who = d.used_key === "saved" ? "（用已保存 Key 测试）"
            : (d.used_key === "none" ? "（未配置 Key）" : "");
        if (tip) { tip.textContent = (d.ok ? "✓ " : "✗ ") + (d.hint || (d.ok ? "服务可达" : "不可达")) + who; }
    }).catch(function (e) {
        if (tip) { tip.textContent = "测试失败：" + String(e); }
    });
}

function ndResetNetSettings() {
    var tip = document.getElementById("ndSettingsTip");
    ndApiFetch("/api/netdoctor/config?reset=1").then(function (d) {
        if (d && d.success !== false) {
            ndState.nodes = d.nodes || [];
            ndState.expectedDns = d.expected_dns || [];
            ndRenderNodes();
            ndRenderSettings();
            if (tip) { tip.textContent = "已恢复出厂节点表"; }
        } else if (tip) {
            tip.textContent = "恢复失败：" + ((d && d.error) || "未知");
        }
    }).catch(function (e) { if (tip) { tip.textContent = "恢复失败：" + String(e); } });
}

/* ===================== ⑥ AI 智能诊断 ===================== */

/* 2026-09-10 优化4/7：单类勾选 + 网络组拆子选项；时间维度类（system_log/net_conn）
   行内时间范围选择（1h/24h 默认/3d/自定义起止），无时间维度类（实时快照/最近一次
   记录/平台记录接口不支持范围检索的 perf 两类）显示「—」。 */
var ND_AI_SINGLES = [
    { key: "hwinfo",        name: "硬件信息（hwinfo）",              time: false },
    { key: "os_info",       name: "系统信息（os_info）",             time: false },
    { key: "perf_analysis", name: "性能分析记录（perf_analysis）",   time: false },
    { key: "perf_stress",   name: "性能压测记录（perf_stress）",     time: false },
    { key: "system_log",    name: "系统日志（system_log · 检索窗口内事件）", time: true }
];
var ND_AI_NET_SUBS = [
    { key: "net_conn",    name: "连通性历史（含最近检测结果）", time: true,  needCenter: false },
    { key: "net_tracert", name: "路由追踪记录（最近一次）",     time: false, needCenter: false },
    { key: "net_stress",  name: "网络压测总结（最近一次）",     time: false, needCenter: true }
];

function ndAiAllKeys() {
    return ["hwinfo", "os_info", "perf_analysis", "perf_stress", "system_log",
            "net_conn", "net_tracert", "net_stress"];
}

var ND_AI_KEY_NAMES = { hwinfo: "硬件信息", os_info: "系统信息", perf_analysis: "性能分析记录",
    perf_stress: "性能压测记录", system_log: "系统日志", net_conn: "连通性历史",
    net_tracert: "路由追踪记录", net_stress: "网络压测总结" };

function ndAiKeyName(k) { return ND_AI_KEY_NAMES[k] || k; }

/* 时间范围参数：{hours:n} 或 {start,end(YYYY-MM-DD HH:MM)}；custom 无效 → null */
function ndAiTimeParams(key) {
    var t = (ndState.aiTime || {})[key] || { preset: "24h" };
    if (t.preset === "custom") {
        var s = Date.parse((t.start || "").replace("T", " "));
        var e = Date.parse((t.end || "").replace("T", " "));
        if (isNaN(s) || isNaN(e) || e <= s) { return null; }
        return { start_ts: Math.floor(s / 1000), end_ts: Math.floor(e / 1000),
                 start: (t.start || "").replace("T", " "), end: (t.end || "").replace("T", " ") };
    }
    var hours = t.preset === "1h" ? 1 : (t.preset === "3d" ? 72 : 24);
    return { hours: hours };
}

function ndAiTimeNote(tp) {
    if (!tp) { return "时间范围无效"; }
    if (tp.hours !== undefined) { return "近 " + tp.hours + " 小时"; }
    return "自定义范围";
}

/* 提交走 net_service 转发代理（token 仅后端注入）；pywebview 双参透传 JSON body，
   独立页/浏览器回退 fetch POST。诊断为长请求（服务端 ≤120s），独立 130s 客户端超时
   （不经 ndApiFetch 的 15s 通用保护）。 */
function ndAiPostDiagnose(issue, logs) {
    var body = JSON.stringify({ issue: issue, logs: logs,
        mode: ndAiPersonal() ? "personal" : "enterprise" });
    var call;
    if (window.pywebview && window.pywebview.api) {
        call = window.pywebview.api.call("/api/netdoctor/ai-diagnose", body);
        call.catch(function () {});
    } else {
        call = fetch("/api/netdoctor/ai-diagnose", { method: "POST",
            headers: { "Content-Type": "application/json" }, body: body })
            .then(function (r) { return r.json(); });
    }
    return Promise.race([
        call,
        new Promise(function (_, rej) {
            setTimeout(function () { rej(new Error("客户端超时（120s），模型链可能仍在处理，可重试")); }, 130000);
        })
    ]).then(function (d) {
        if (!d || d.success === false) {
            var err = new Error((d && d.error) || "未知错误");
            if (d && d.analysis_id) { err.analysis_id = d.analysis_id; }   /* 失败诊断平台已留档 */
            throw err;
        }
        return d;
    });
}

function ndAiSize(d) {
    try { var n = JSON.stringify(d).length; return (n / 1024).toFixed(1) + " KB"; }
    catch (e) { return "?"; }
}

/* 结构感知预算裁剪（2026-09-10 尾巴1：id=22 存证 JSON 完整性 FAIL）。
   旧实现 JSON.stringify 后裸 slice(0,32768) 会撕裂转义序列/多字节字符，
   服务端 json.loads(存证) 失败——与服务端修复前同病。新策略：
   1) 整包 ≤budget 原样；2) 找对象内最大数组按完整元素粒度二分裁剪（事件/历史不撕裂）；
   3) 退化：非数组键按序列化长度升序贪心装入；输出恒为合法 JSON。
   预算 32768 与服务端 evidence 契约对齐，服务端原样存证即可通过验收。 */
function ndAiPackLogs(data, budget) {
    var text;
    try { text = JSON.stringify(data); } catch (e) { return "{\"truncated\":true}"; }
    if (text.length <= budget) { return text; }
    if (!data || typeof data !== "object") { return "{\"truncated\":true}"; }

    var arrKey = null, arrLen = 0, f;
    for (f in data) {
        if (Object.prototype.hasOwnProperty.call(data, f)
                && Object.prototype.toString.call(data[f]) === "[object Array]"
                && data[f].length > arrLen) {
            arrLen = data[f].length;
            arrKey = f;
        }
    }

    var copyWithout = function (skip) {
        var o = {};
        for (var k in data) {
            if (Object.prototype.hasOwnProperty.call(data, k) && k !== skip) { o[k] = data[k]; }
        }
        return o;
    };

    if (arrKey) {
        var arr = data[arrKey];
        var lo = 0, hi = arrLen, best = 0;
        while (lo <= hi) {
            var mid = (lo + hi) >> 1;
            var probe = copyWithout(arrKey);
            probe[arrKey] = arr.slice(0, mid);
            if (JSON.stringify(probe).length <= budget) { best = mid; lo = mid + 1; }
            else { hi = mid - 1; }
        }
        var out = copyWithout(arrKey);
        out[arrKey] = arr.slice(0, best);
        var t2 = JSON.stringify(out);
        if (t2.length <= budget) { return t2; }
        var t3 = JSON.stringify(copyWithout(arrKey));   /* 数组清空仍超 → 丢弃其它大字段重试 */
        if (t3.length <= budget) { return t3; }
        var only = {};
        only[arrKey] = arr.slice(0, Math.max(best, 1));
        var t4 = JSON.stringify(only);
        return t4.length <= budget ? t4 : "{\"truncated\":true}";
    }

    /* 无数组结构：键粒度贪心（短字段优先装入） */
    var entries = [];
    for (f in data) {
        if (Object.prototype.hasOwnProperty.call(data, f)) {
            var len = 0;
            try { len = JSON.stringify(data[f]).length; } catch (e2) { len = 0; }
            entries.push([f, len, data[f]]);
        }
    }
    entries.sort(function (a, b) { return a[1] - b[1]; });
    var acc = {};
    var bestText = "{}";
    for (var i = 0; i < entries.length; i++) {
        acc[entries[i][0]] = entries[i][2];
        var t5 = JSON.stringify(acc);
        if (t5.length <= budget) { bestText = t5; }
        else { delete acc[entries[i][0]]; }
    }
    return bestText;
}

/* 证据瘦身提质（2026-09-10 证据饥饿缺陷，analysis_id=16 实证）：
   占位符描述（"<The description..."）替换为「(描述缺失)」、单条描述超 200 字符截断
   （log_service DESC_TRUNC=2000，终端侧收紧）——32KB 预算内装入更多有效事件。
   兼容真实字段 description 与旧字段 desc。 */
function ndAiSlimEvents(evs) {
    for (var i = 0; i < evs.length; i++) {
        var ev = evs[i];
        if (!ev || typeof ev !== "object") { continue; }
        var fields = ["description", "desc"];
        for (var j = 0; j < fields.length; j++) {
            var d = ev[fields[j]];
            if (typeof d !== "string") { continue; }
            if (d.indexOf("<The description") === 0) { ev[fields[j]] = "(描述缺失)"; }
            else if (d.length > 200) { ev[fields[j]] = d.slice(0, 200); }
        }
    }
    return evs;
}

/* 剥离模型输出中的 Markdown 修饰符（** 加粗 / 反引号），保留纯文本 + 既有结构化样式 */
function ndAiCleanMd(t) {
    return String(t || "").replace(/\*\*/g, "").replace(/`/g, "");
}

/* 单类采集：前端聚合（零跨模块 import，全部走现成 bridge 路由）；失败降级「不可用」不阻断 */
function ndAiCollectOne(key) {
    var fail = function () { return Promise.resolve({ ok: false, data: null, note: "不可用" }); };
    if (key === "os_info") {
        return ndApiFetch("/api/perf/hwinfo").then(function (d) {
            var h = d && d.hwinfo ? d.hwinfo : null;
            if (!h || !h.os) { return fail(); }
            return { ok: true, data: { hostname: h.hostname, os: h.os }, note: "实时" };
        }).catch(fail);
    }
    if (key === "system_log") {
        /* 2026-09-10 优化7：按勾选行时间范围过滤（loginspector search 的 hours / start+end 参数） */
        var tp = ndAiTimeParams("system_log");
        if (!tp) { return Promise.resolve({ ok: false, data: null, note: ndAiTimeNote(tp) }); }
        var q = "/api/loginspector/search?per_page=200";
        if (tp.hours !== undefined) { q += "&hours=" + tp.hours; }
        else { q += "&start=" + encodeURIComponent(tp.start) + "&end=" + encodeURIComponent(tp.end); }
        return ndApiFetch(q).then(function (d) {
            var evs = (d && d.events) || [];
            var data = evs.length ? { events: ndAiSlimEvents(evs), summary: d.summary || null } : null;
            var note = evs.length
                ? (ndAiTimeNote(tp) + " · " + evs.length + " 条 · "
                    + (evs[0].timestamp || evs[0].time_text || ""))
                : (ndAiTimeNote(tp) + " · 0 条");
            if (!data) { return { ok: false, data: null, note: note }; }
            return { ok: true, data: data, note: note };
        }).catch(fail);
    }
    if (key === "net_conn") {
        /* 连通性历史：JSONL 按 ts 过滤（ping-history hours / start_ts+end_ts，net_service 同步支持） */
        var tpn = ndAiTimeParams("net_conn");
        if (!tpn) { return Promise.resolve({ ok: false, data: null, note: ndAiTimeNote(tpn) }); }
        var qh = "/api/netdoctor/ping-history?limit=200";
        if (tpn.hours !== undefined) { qh += "&hours=" + tpn.hours; }
        else { qh += "&start_ts=" + tpn.start_ts + "&end_ts=" + tpn.end_ts; }
        return ndApiFetch(qh).then(function (d) {
            var parts = {}, n = 0;
            var agg = (d && d.agg) || [];
            if (agg.length) { parts.history = agg; n++; }
            if (ndState.lastPing) { parts.connectivity = ndState.lastPing; n++; }
            if (!n) { return { ok: false, data: null, note: ndAiTimeNote(tpn) + " · 无检测记录" }; }
            return { ok: true, data: parts, note: ndAiTimeNote(tpn) + " · " + n + " 组数据" };
        }).catch(fail);
    }
    if (key === "net_tracert") {
        if (!ndState.lastTracert) {
            return Promise.resolve({ ok: false, data: null, note: "不可用（尚未追踪）" });
        }
        return Promise.resolve({ ok: true, data: ndState.lastTracert,
            note: "最近一次 · " + ((ndState.lastTracert.hops || []).length) + " 跳" });
    }
    if (key === "net_stress") {
        if (!ndConnected()) { return Promise.resolve({ ok: false, data: null, note: "需连接中心" }); }
        if (!ndState.lastStressResult) {
            return Promise.resolve({ ok: false, data: null, note: "不可用（尚未压测）" });
        }
        return Promise.resolve({ ok: true, data: ndState.lastStressResult, note: "最近一次 · 压测总结" });
    }
    var route = key === "hwinfo" ? "/api/perf/hwinfo"
        : key === "perf_analysis" ? "/api/perf/record-report"
        : "/api/perf/stress-status";
    return ndApiFetch(route).then(function (d) {
        var data = null, note = "已采集";
        if (key === "hwinfo") {
            data = d && d.hwinfo ? d.hwinfo : null;
            note = "实时";
        } else {
            data = (d && d.success !== false) ? (d.report || d.task || (d.result || null)) : null;
        }
        if (!data || (typeof data === "object" && !Object.keys(data).length)) { return fail(); }
        return { ok: true, data: data, note: note };
    }).catch(fail);
}

/* 2026-09-10 优化4/8：诊断日志默认收起，标题行点击展开/收起；「重新探测数据源」
   移至标题行右侧（展开后可见）；网络配置与检测数据拆 3 子选项勾选。 */
function ndAiToggleLogs() {
    var body = document.getElementById("ndAiLogsBody");
    var arrow = document.getElementById("ndAiLogsArrow");
    var btn = document.getElementById("ndAiRefreshBtn");
    if (!body) { return; }
    var show = body.style.display === "none";
    body.style.display = show ? "block" : "none";
    if (arrow) { arrow.textContent = show ? "▾" : "▸"; }
    if (btn) { btn.style.display = show ? "inline-block" : "none"; }
}

function ndAiTimeCell(key) {
    var opts = [["1h", "最近 1 小时"], ["24h", "最近 24 小时"], ["3d", "最近 3 天"], ["custom", "自定义"]];
    var preset = ((ndState.aiTime || {})[key] || {}).preset || "24h";
    var sel = '<select class="nd-select nd-time-select" id="ndAiTime-' + key
        + '" onchange="ndAiTimeChange(\'' + key + '\')">';
    for (var i = 0; i < opts.length; i++) {
        sel += '<option value="' + opts[i][0] + '"' + (preset === opts[i][0] ? " selected" : "")
            + '>' + opts[i][1] + '</option>';
    }
    sel += '</select>';
    sel += '<div class="nd-params" id="ndAiTimeCustom-' + key + '" style="display:none;margin-top:4px">'
        + '<input type="datetime-local" class="nd-input nd-time-input" id="ndAiTimeStart-' + key
        + '" onchange="ndAiTimeChange(\'' + key + '\')">'
        + '<span class="nd-hint">至</span>'
        + '<input type="datetime-local" class="nd-input nd-time-input" id="ndAiTimeEnd-' + key
        + '" onchange="ndAiTimeChange(\'' + key + '\')"></div>';
    return sel;
}

function ndAiSourceRow(s, sub) {
    var cb = '<input type="checkbox" id="ndAiChk-' + s.key + '" checked'
        + (s.needCenter ? "" : ' onchange="ndAiSyncNetGroup()"') + '>';
    return '<tr' + (sub ? ' class="nd-ai-sub"' : "") + '><td>' + cb + '</td>'
        + '<td>' + ndEscapeHtml(s.name) + '</td>'
        + '<td>' + (s.time ? ndAiTimeCell(s.key) : '<span class="nd-hint">—</span>') + '</td>'
        + '<td class="nd-hint" id="ndAiSt-' + s.key + '">待探测</td></tr>';
}

function ndAiRenderSources() {
    var body = document.getElementById("ndAiSourcesBody");
    if (!body) { return; }
    var rows = "";
    for (var i = 0; i < ND_AI_SINGLES.length; i++) { rows += ndAiSourceRow(ND_AI_SINGLES[i], false); }
    rows += '<tr><td><input type="checkbox" id="ndAiChk-netgroup" checked onchange="ndAiNetGroupChange(this.checked)"></td>'
        + '<td><b>网络配置与检测数据</b></td>'
        + '<td><span class="nd-hint">—</span></td>'
        + '<td class="nd-hint" id="ndAiSt-netgroup">—</td></tr>';
    for (var j = 0; j < ND_AI_NET_SUBS.length; j++) { rows += ndAiSourceRow(ND_AI_NET_SUBS[j], true); }
    body.innerHTML = rows;
}

/* 网络组主选框 ↔ 子选项联动（禁用项不计入） */
function ndAiNetGroupChange(on) {
    for (var i = 0; i < ND_AI_NET_SUBS.length; i++) {
        var cb = document.getElementById("ndAiChk-" + ND_AI_NET_SUBS[i].key);
        if (cb && !cb.disabled) { cb.checked = on; }
    }
}

function ndAiSyncNetGroup() {
    var m = document.getElementById("ndAiChk-netgroup");
    if (!m) { return; }
    var total = 0, on = 0;
    for (var i = 0; i < ND_AI_NET_SUBS.length; i++) {
        var cb = document.getElementById("ndAiChk-" + ND_AI_NET_SUBS[i].key);
        if (!cb || cb.disabled) { continue; }
        total++;
        if (cb.checked) { on++; }
    }
    m.disabled = total === 0;
    m.checked = total > 0 && on === total;
    m.indeterminate = on > 0 && on < total;
}

/* 时间范围变化：记录选择并即时重采该类（体量提示随范围变化） */
function ndAiTimeChange(key) {
    var sel = document.getElementById("ndAiTime-" + key);
    var wrap = document.getElementById("ndAiTimeCustom-" + key);
    if (wrap) { wrap.style.display = (sel && sel.value === "custom") ? "flex" : "none"; }
    ndState.aiTime = ndState.aiTime || {};
    ndState.aiTime[key] = {
        preset: sel ? sel.value : "24h",
        start: (document.getElementById("ndAiTimeStart-" + key) || {}).value || "",
        end: (document.getElementById("ndAiTimeEnd-" + key) || {}).value || ""
    };
    ndAiCollectNow(key);
}

/* 证据体量透明化（折叠态标题行汇总：已采集 N/8 源 · 共 X KB） */
function ndAiUpdateAgg() {
    var el = document.getElementById("ndAiLogsAgg");
    if (!el) { return; }
    var keys = ndAiAllKeys();
    var okN = 0, totalB = 0;
    for (var i = 0; i < keys.length; i++) {
        var r = ndState.aiCollect ? ndState.aiCollect[keys[i]] : null;
        if (r && r.ok) {
            okN++;
            try { totalB += JSON.stringify(r.data).length; } catch (e) { /* 忽略体量统计失败 */ }
        }
    }
    el.textContent = "已采集 " + okN + "/" + keys.length + " 源 · 共 "
        + (totalB / 1024).toFixed(1) + " KB";
}

function ndAiCollectNow(key) {
    var st = document.getElementById("ndAiSt-" + key);
    if (st) { st.textContent = "采集中…"; }
    ndAiCollectOne(key).then(function (r) {
        r.ts = Date.now();
        ndState.aiCollect = ndState.aiCollect || {};
        ndState.aiCollect[key] = r;
        if (st) { st.textContent = r.ok ? (r.note + " · 约 " + ndAiSize(r.data)) : r.note; }
        ndAiUpdateAgg();
    });
}

function ndAiRefreshSources() {
    ndState.aiCollect = {};
    var keys = ndAiAllKeys();
    for (var i = 0; i < keys.length; i++) {
        (function (key) {
            if (key === "net_stress" && !ndConnected()) {
                var skip = { ok: false, data: null, note: "需连接中心", ts: Date.now() };
                ndState.aiCollect[key] = skip;
                var st0 = document.getElementById("ndAiSt-" + key);
                if (st0) { st0.textContent = skip.note; }
                ndAiUpdateAgg();
                return;
            }
            ndAiCollectNow(key);
        })(keys[i]);
    }
}

function ndAiCountIssue() {
    var t = document.getElementById("ndAiIssue");
    var c = document.getElementById("ndAiIssueCount");
    if (t && c) { c.textContent = String((t.value || "").length); }
}

function ndAiInitHistory() {
    try {
        var raw = localStorage.getItem("nd_ai_history");
        ndState.aiHistory = raw ? JSON.parse(raw) : [];
        if (!ndState.aiHistory.length) { ndState.aiHistory = []; }
    } catch (e) { ndState.aiHistory = []; }
    ndAiRenderHistory();
}

function ndAiSaveHistory(d, issue) {
    try {
        var h = ndState.aiHistory || [];
        h.unshift({ ts: Date.now(), issue: String(issue || "").slice(0, 60),
                    analysis_id: d.analysis_id, model: d.model,
                    duration_ms: d.duration_ms,
                    text: String(d.response_text || "").slice(0, 20000) });
        ndState.aiHistory = h.slice(0, 20);
        try { localStorage.setItem("nd_ai_history", JSON.stringify(ndState.aiHistory)); }
        catch (e) { /* file:// 或存储禁用：内存态即可 */ }
    } catch (e) { /* 存储失败不影响诊断结果展示 */ }
    ndAiRenderHistory();
}

function ndAiRenderHistory() {
    var el = document.getElementById("ndAiHistoryBody");
    if (!el) { return; }
    var h = ndState.aiHistory || [];
    if (!h.length) { el.innerHTML = '<div class="nd-empty">暂无历史</div>'; return; }
    var rows = "";
    for (var i = 0; i < h.length; i++) {
        rows += '<tr style="cursor:pointer" onclick="ndAiShowHistory(' + i + ')">'
            + '<td>' + ndEscapeHtml(h[i].issue || "--") + '</td>'
            + '<td class="nd-num">' + ndEscapeHtml(new Date(h[i].ts).toLocaleString()) + '</td>'
            + '<td>' + ndEscapeHtml(h[i].analysis_id || "本地") + '</td></tr>';
    }
    el.innerHTML = '<table class="nd-table"><tr><th>问题摘要</th><th>时间</th><th>analysis_id</th></tr>' + rows + '</table>';
}

function ndAiShowHistory(i) {
    var h = (ndState.aiHistory || [])[i];
    if (!h) { return; }
    ndAiRenderResult({ analysis_id: h.analysis_id, model: h.model,
                       duration_ms: h.duration_ms, response_text: h.text });
}

function ndAiWaitCollect(maxTicks) {
    return new Promise(function (resolve) {
        var n = 0;
        var t = setInterval(function () {
            n++;
            var keys = ndAiAllKeys();
            var done = !!ndState.aiCollect && keys.every(function (k) {
                return ndState.aiCollect[k];
            });
            if (done || n >= maxTicks) { clearInterval(t); resolve(); }
        }, 300);
    });
}

/* 2026-09-10 优化5：response_text 结构化渲染。服务端模型链强制输出
   【故障原因分析】/【处理意见】/【风险提示】三段（server/ai.py 诊断 prompt），
   按段分卡着色；元信息徽章化；无段落标记时回退纯文本。 */
var ND_AI_SECTIONS = [
    { title: "故障原因分析", cls: "nd-ai-sec-cause" },
    { title: "处理意见", cls: "nd-ai-sec-advice" },
    { title: "风险提示", cls: "nd-ai-sec-risk" }
];

function ndAiSplitSections(text) {
    var t = String(text || "");
    var found = [];
    for (var i = 0; i < ND_AI_SECTIONS.length; i++) {
        var token = "【" + ND_AI_SECTIONS[i].title + "】";
        var p = t.indexOf(token);
        if (p < 0) { token = ND_AI_SECTIONS[i].title; p = t.indexOf(token); }
        if (p >= 0) { found.push({ sec: ND_AI_SECTIONS[i], hs: p, cs: p + token.length }); }
    }
    if (!found.length || found[0].sec !== ND_AI_SECTIONS[0]) { return null; }
    var segs = [];
    for (var j = 0; j < found.length; j++) {
        var end = (j + 1 < found.length) ? found[j + 1].hs : t.length;
        var body = t.slice(found[j].cs, end).replace(/^[\s：:、\-—·.]*/, "").replace(/\s+$/, "");
        segs.push({ title: found[j].sec.title, cls: found[j].sec.cls, body: body });
    }
    return segs;
}

function ndAiRenderResult(d) {
    var el = document.getElementById("ndAiBody");
    if (!el) { return; }
    var meta = '<div class="nd-ai-meta">'
        + '<span class="nd-badge nd-info">'
        + (d.analysis_id ? ("analysis_id " + ndEscapeHtml(d.analysis_id)) : "本地诊断") + '</span>'
        + '<span class="nd-badge nd-muted">模型 ' + ndEscapeHtml(d.model || "--") + '</span>'
        + '<span class="nd-badge nd-muted">耗时 '
        + ndEscapeHtml(d.duration_ms !== null && d.duration_ms !== undefined
            ? d.duration_ms + " ms" : "--") + '</span></div>';
    var segs = ndAiSplitSections(ndAiCleanMd(d.response_text));
    if (segs) {
        var html = meta;
        for (var i = 0; i < segs.length; i++) {
            html += '<div class="nd-ai-sec ' + segs[i].cls + '"><b class="nd-ai-sec-title">'
                + ndEscapeHtml(segs[i].title) + '</b><pre class="nd-pre">'
                + ndEscapeHtml(segs[i].body || "（无内容）") + '</pre></div>';
        }
        el.innerHTML = html;
    } else {
        el.innerHTML = meta + '<pre class="nd-pre" style="max-height:340px;overflow:auto">'
            + ndEscapeHtml(ndAiCleanMd(d.response_text) || "（无内容）") + '</pre>';
    }
}

/* 失败态样式区分：错误卡（重试引导 + 平台留档 analysis_id 追溯提示） */
function ndAiRenderError(msg, analysisId) {
    var el = document.getElementById("ndAiBody");
    if (!el) { return; }
    el.innerHTML = '<div class="nd-ai-err"><div style="margin-bottom:4px">'
        + ndBadge("诊断失败", "nd-err") + '</div>'
        + '<pre class="nd-pre">' + ndEscapeHtml(msg || "未知错误") + '</pre>'
        + (analysisId ? '<div class="nd-hint" style="margin-top:6px">已在平台留档（analysis_id '
            + ndEscapeHtml(analysisId) + '），可在控制台「AI 分析」页追溯</div>' : "")
        + '<div class="nd-hint" style="margin-top:4px">可点击「提交诊断」重试</div></div>';
}

function ndAiSubmit() {
    /* 防重入（2026-09-10 尾巴2：id=21/22 双击双提交白烧两次 LLM 调用）：
       提交全程置位 aiSubmitting + 禁用按钮，重复点击仅提示；确认对话框链路一并覆盖 */
    if (ndState.aiSubmitting) {
        ndSetTip("ndAiSummary", "诊断提交中，请勿重复点击（模型链处理约 10-30 秒）");
        return;
    }
    ndState.aiSubmitting = true;
    var btn0 = document.getElementById("ndAiBtn");
    if (btn0) { btn0.disabled = true; }
    /* 2026-09-10 Bug0 修复兜底：gate 状态可能为旧值（tab 常驻期间缓存），
       提交前强制重查一次中心状态，杜绝「心跳在线却判未连接」。
       个人版不依赖中心，跳过重查直接提交（第九项）。 */
    if (ndAiPersonal() || ndConnected()) { ndAiSubmitProceed(); return; }
    ndSetTip("ndAiSummary", "正在确认中心连接状态…");
    var fail0 = function (msg) {
        ndState.aiSubmitting = false;
        if (btn0) { btn0.disabled = false; }
        ndSetTip("ndAiSummary", msg);
    };
    ndApiFetch("/api/perf/uplink/status").then(function (d) {
        ndState.uplink = d && d.uplink ? d.uplink : null;
        ndRenderUplink();
        if (!ndConnected()) { fail0("未连接中心，功能不可用"); return; }
        ndAiSubmitProceed();
    }).catch(function () {
        fail0("未连接中心，功能不可用（中心状态确认失败）");
    });
}

/* 提交前强制刷新网络三子源（预采集可能早于 ③④⑤ 实测，避免「尚未追踪/尚未压测」陈旧态） */
function ndAiFreshNetSubs() {
    var t0 = Date.now();
    var keys = ["net_conn", "net_tracert", "net_stress"];
    for (var i = 0; i < keys.length; i++) { ndAiCollectNow(keys[i]); }
    return new Promise(function (resolve) {
        var n = 0;
        var t = setInterval(function () {
            n++;
            var done = keys.every(function (k) {
                var c = ndState.aiCollect ? ndState.aiCollect[k] : null;
                return c && (c.ts || 0) >= t0;
            });
            if (done || n >= 15) { clearInterval(t); resolve(); }
        }, 200);
    });
}

function ndAiSubmitProceed() {
    var issue = ((document.getElementById("ndAiIssue") || {}).value || "").trim();
    if (!issue) {
        ndSetTip("ndAiSummary", "请先填写问题概述");
        ndState.aiSubmitting = false;
        var b1 = document.getElementById("ndAiBtn");
        if (b1) { b1.disabled = false; }
        return;
    }
    var btn = document.getElementById("ndAiBtn");
    if (btn) { btn.disabled = true; }
    ndSetTip("ndAiSummary", "采集日志并提交分析中（模型链处理约 10-30 秒）…");
    var c0 = ndState.aiCollect && ndState.aiCollect.hwinfo;
    var needFresh = !c0 || (Date.now() - (c0.ts || 0) > 300000);
    var ready = needFresh ? (ndAiRefreshSources(), ndAiWaitCollect(20)) : ndAiFreshNetSubs();
    ready.then(function () {
        var prep = ndAiPrepareLogs();
        if (prep.total > 4 * 1024 * 1024) { throw new Error("日志总体量超限（>4MB）"); }
        if (prep.missing.length) {
            /* 补采模态接管（2026-09-10）：应用内自绘模态替代原生 confirm，支持就地补采；
               模态期间 aiSubmitting 保持置位，模态收尾时 resolve 复位 */
            return new Promise(function (resolve) {
                ndAiShowCompleteModal(issue, prep, resolve);
            });
        }
        return ndAiPostPrepared(issue, prep);
    }).catch(function (e) {
        ndAiHandleError(e);
    }).then(function () {
        ndState.aiSubmitting = false;   /* 成功/失败/取消/模态四路径统一复位（issue_empty 分支已单独复位） */
        if (btn) { btn.disabled = false; }
    });
}

/* ============ 补采模态（2026-09-10：缺失源可选补采，替代原生 confirm） ============ */

var ND_AI_BACKFILL = {
    net_tracert: { label: "路由追踪记录", hint: "约 30 秒", needCenter: false },
    net_stress:  { label: "网络压测总结", hint: "需连接中心 · 约 1 分钟", needCenter: true },
    perf_stress: { label: "性能压测记录", hint: "约 60 秒", needCenter: false }
};

/* 组装提交日志（结构感知裁剪 + 缺失源清单） */
function ndAiPrepareLogs() {
    var logs = {};
    var total = 0;
    var missing = [];
    var missingNames = [];
    var keys = ndAiAllKeys();
    for (var i = 0; i < keys.length; i++) {
        var k = keys[i];
        var cb = document.getElementById("ndAiChk-" + k);
        if (!cb || !cb.checked) { continue; }   /* disabled+checked（如未连中心的压测项）仍计缺失，交补采模态 */
        var r = ndState.aiCollect ? ndState.aiCollect[k] : null;
        if (!r || !r.ok) { missing.push(k); missingNames.push(ndAiKeyName(k)); continue; }
        logs[k] = ndAiPackLogs(r.data, 32768);   /* 单类 ≤32KB，结构感知裁剪保证合法 JSON */
        total += logs[k].length;
    }
    return { logs: logs, total: total, missing: missing, missingNames: missingNames };
}

function ndAiPostPrepared(issue, prep) {
    return ndAiPostDiagnose(issue, prep.logs).then(function (d) {
        ndAiRenderResult(d);
        ndAiSaveHistory(d, issue);
        ndSetTip("ndAiSummary", "诊断完成");
    });
}

/* 诊断失败统一处理：not_configured 引导 / 401 指引 / 结构化错误卡 */
function ndAiHandleError(e) {
    var msg = (e && e.message ? e.message : String(e));
    if (msg.indexOf("not_configured") >= 0) {
        ndSetTip("ndAiSummary", "个人版未配置：请到「系统设置 · AI 诊断（个人版）」填写 API 地址 / API Key / 模型名并保存");
        return;
    }
    if (msg.indexOf("personal_http_401") >= 0) {
        msg += " → 设置 → AI 诊断（个人版）检查 API Key 是否已保存且有效";
    }
    ndAiRenderError(msg, e && e.analysis_id);
    ndSetTip("ndAiSummary", "诊断失败：" + msg + "，可重试");
}

/* 模态内重采：完成后同步刷新主界面状态列与体量汇总 */
function ndAiRecollectOne(k) {
    return new Promise(function (resolve) {
        ndAiCollectOne(k).then(function (r) {
            r.ts = Date.now();
            ndState.aiCollect = ndState.aiCollect || {};
            ndState.aiCollect[k] = r;
            var st = document.getElementById("ndAiSt-" + k);
            if (st) { st.textContent = r.ok ? (r.note + " · 约 " + ndAiSize(r.data)) : r.note; }
            ndAiUpdateAgg();
            resolve();
        });
    });
}

/* 可补采任务执行（全部走现有任务引擎，零后端新增）；onDone(ok) */
function ndAiBackfillTask(key, stEl, onDone) {
    var setSt = function (t) { if (stEl) { stEl.textContent = t; } };
    var pollNd = function (taskId) {
        ndPollTask(taskId, null, function (err) {
            if (err) { setSt("失败"); onDone(false); return; }
            setSt("完成");
            onDone(true);
        });
    };
    if (key === "net_tracert") {
        setSt("运行中…");
        var center = "";
        for (var i = 0; i < ndState.nodes.length; i++) {
            if (ndState.nodes[i].key === "center") { center = ndState.nodes[i].target || ""; break; }
        }
        if (!center && ndState.uplink && ndState.uplink.server_url) {
            var u = ndState.uplink.server_url.split("://");
            center = (u[1] || "").split("/")[0].split(":")[0];
        }
        ndApiFetch("/api/netdoctor/tracert-start?target=" + encodeURIComponent(center || "127.0.0.1"))
            .then(function (d) {
                if (!d || d.success === false) { setSt("失败：" + ((d && d.error) || "未知")); onDone(false); return; }
                pollNd(d.task_id);
            }).catch(function () { setSt("失败"); onDone(false); });
        return;
    }
    if (key === "net_stress") {
        setSt("运行中…");
        ndApiFetch("/api/netdoctor/stress-start?duration_sec=10&sizes=64,256&udp_mbps=100")
            .then(function (d) {
                if (!d || d.success === false) { setSt("失败：" + ((d && d.error) || "未知")); onDone(false); return; }
                pollNd(d.task_id);
            }).catch(function () { setSt("失败"); onDone(false); });
        return;
    }
    if (key === "perf_stress") {
        setSt("运行中…");
        ndApiFetch("/api/perf/stress-start?mode=full")
            .then(function (d) {
                if (!d || !d.success || !d.stress_id) {
                    setSt("失败：" + ((d && d.error) || "未知")); onDone(false); return;
                }
                var sid = d.stress_id;
                var ticks = 0;
                var t = setInterval(function () {
                    ticks++;
                    ndApiFetch("/api/perf/stress-status?stress_id=" + encodeURIComponent(sid))
                        .then(function (s) {
                            var task = s && s.task;
                            if (!s || s.success === false || (task && task.status === "error")) {
                                clearInterval(t); setSt("失败"); onDone(false); return;
                            }
                            if (task && task.status && task.status !== "running") {
                                clearInterval(t); setSt("完成"); onDone(true); return;
                            }
                            if (ticks > 90) { clearInterval(t); setSt("超时"); onDone(false); }
                        }).catch(function () { clearInterval(t); setSt("失败"); onDone(false); });
                }, 1000);
            }).catch(function () { setSt("失败"); onDone(false); });
        return;
    }
    onDone(false);
}

/* 应用内补采模态：列缺失源（可补采勾选 / 不可补采重新采集按钮），三按钮收尾 */
function ndAiShowCompleteModal(issue, prep, done) {
    var ov = document.getElementById("ndAiCompleteOverlay");
    if (!ov) {
        ov = document.createElement("div");
        ov.id = "ndAiCompleteOverlay";
        ov.innerHTML = '<div class="nd-modal"><b>完善诊断数据源</b>'
            + '<div class="nd-hint">以下勾选源未采集成功，可就地补采后自动提交；也可直接提交（对应维度按证据不足声明）。</div>'
            + '<div id="ndAiCompleteList"></div>'
            + '<div class="nd-params" style="margin-top:10px">'
            + '<button class="nd-btn primary" id="ndAiCompleteGo">采集并提交</button>'
            + '<button class="nd-btn" id="ndAiCompleteSkip">直接提交</button>'
            + '<button class="nd-btn" id="ndAiCompleteCancel">取消</button>'
            + '<span class="nd-hint" id="ndAiCompleteTip"></span>'
            + '</div></div>';
        document.body.appendChild(ov);
    }
    var list = ov.querySelector("#ndAiCompleteList");
    var backfill = [], retriable = [];
    for (var i = 0; i < prep.missing.length; i++) {
        var k = prep.missing[i];
        if (ND_AI_BACKFILL[k]) { backfill.push(k); } else { retriable.push(k); }
    }
    var rows = "";
    for (var j = 0; j < backfill.length; j++) {
        var bf = ND_AI_BACKFILL[backfill[j]];
        var needCenter = bf.needCenter && !ndConnected();
        rows += '<div class="nd-modal-row">'
            + '<input type="checkbox" class="nd-bf-chk" data-key="' + backfill[j] + '"'
            + (needCenter ? " disabled" : " checked") + '>'
            + '<span>' + ndEscapeHtml(bf.label) + '</span>'
            + '<span class="nd-hint">' + (needCenter ? "需连接中心，本次不可补采" : bf.hint) + '</span>'
            + '<span class="nd-hint nd-bf-status">排队</span></div>';
    }
    for (var m = 0; m < retriable.length; m++) {
        rows += '<div class="nd-modal-row">'
            + '<span class="nd-hint">✗</span>'
            + '<span>' + ndEscapeHtml(ndAiKeyName(retriable[m])) + '</span>'
            + '<button class="nd-btn" data-retry="' + retriable[m] + '">重新采集</button>'
            + '<span class="nd-hint nd-bf-status"></span></div>';
    }
    list.innerHTML = rows;
    var tip = ov.querySelector("#ndAiCompleteTip");

    var finish = function () { ov.style.display = "none"; done(); };
    var buttons = [ov.querySelector("#ndAiCompleteGo"),
                   ov.querySelector("#ndAiCompleteSkip"),
                   ov.querySelector("#ndAiCompleteCancel")];
    for (var g = 0; g < buttons.length; g++) { buttons[g].disabled = false; }   /* 复用重置（Go 路径曾禁用） */
    ov.style.display = "flex";

    ov.querySelector("#ndAiCompleteSkip").onclick = function () {
        /* POST 收尾后才 finish（resolve 复位）——保证模态/提交期间防重入标志不提前释放 */
        ndAiPostPrepared(issue, prep).catch(function (e) { ndAiHandleError(e); }).then(function () { finish(); });
    };
    ov.querySelector("#ndAiCompleteCancel").onclick = function () {
        finish();
        ndSetTip("ndAiSummary", "已取消提交（存在未采集成功的勾选源）");
    };
    var retryBtns = list.querySelectorAll("button[data-retry]");
    for (var r = 0; r < retryBtns.length; r++) {
        (function (btn) {
            btn.onclick = function () {
                var key = btn.getAttribute("data-retry");
                var stEl = btn.parentNode.querySelector(".nd-bf-status");
                if (stEl) { stEl.textContent = "采集中…"; }
                btn.disabled = true;
                ndAiCollectOne(key).then(function (res) {
                    res.ts = Date.now();
                    ndState.aiCollect = ndState.aiCollect || {};
                    ndState.aiCollect[key] = res;
                    if (stEl) { stEl.textContent = res.ok ? (res.note + " · 约 " + ndAiSize(res.data)) : res.note; }
                    btn.disabled = false;
                });
            };
        })(retryBtns[r]);
    }
    ov.querySelector("#ndAiCompleteGo").onclick = function () {
        for (var g = 0; g < buttons.length; g++) { buttons[g].disabled = true; }
        var chosen = [];
        var chks = list.querySelectorAll(".nd-bf-chk:checked");
        for (var c = 0; c < chks.length; c++) { chosen.push(chks[c].getAttribute("data-key")); }
        var seq = chosen.slice();
        var next = function () {
            if (!seq.length) {
                var prep2 = ndAiPrepareLogs();   /* 补采后重整日志；仍失败的源降级为证据不足照常声明 */
                ndAiPostPrepared(issue, prep2).catch(function (e) { ndAiHandleError(e); }).then(function () { finish(); });
                return;
            }
            var k = seq.shift();
            var rowEl = null;
            var rws = list.querySelectorAll(".nd-modal-row");
            for (var x = 0; x < rws.length; x++) {
                var chk = rws[x].querySelector(".nd-bf-chk");
                if (chk && chk.getAttribute("data-key") === k) { rowEl = rws[x]; break; }
            }
            var stEl = rowEl ? rowEl.querySelector(".nd-bf-status") : null;
            ndAiBackfillTask(k, stEl, function (ok) {
                if (ok) { ndAiRecollectOne(k).then(next); }
                else { next(); }   /* 单个任务失败不阻断，该源降级 */
            });
        };
        next();
    };
}
