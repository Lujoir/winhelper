/* ==========================================
   网络排障 · netdoctor（观枢终端平台｜EyeTerm）
   ==========================================
   五功能：① 配置核查 ② IP 冲突检测 ③ 连通性测试 ④ 路由追踪 ⑤ 网络压测
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
    pingTaskId: null,
    tracertTaskId: null,
    stressTaskId: null,
    lastStressResult: null,
    pollers: {}
};

/* ===================== 传输与工具 ===================== */

function ndApiFetch(path) {
    if (typeof apiFetch === "function") { return apiFetch(path); }
    if (window.pywebview && window.pywebview.api) {
        return window.pywebview.api.call(path);
    }
    return fetch(path).then(function (r) { return r.json(); });
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
    if (ndState.inited) { return; }
    ndState.inited = true;
    ndLoadConfig();
    ndLoadUplink();
    ndLoadHistory();
}

/* DOM 就绪：独立页默认激活时自动初始化（主应用由 switchTab 守卫调用） */
(function () {
    var boot = function () {
        var sec = document.getElementById("tab-netdoctor");
        if (sec && sec.classList.contains("active")) { initNetDoctorTab(); }
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

function ndLoadUplink() {
    ndApiFetch("/api/perf/uplink/status").then(function (d) {
        ndState.uplink = d && d.uplink ? d.uplink : null;
        ndRenderUplink();
    }).catch(function () {
        ndState.uplink = null;
        ndRenderUplink();
    });
}

function ndRenderUplink() {
    var el = document.getElementById("ndUplinkBody");
    if (!el) { return; }
    var u = ndState.uplink;
    var m = u ? (ndUplinkMap[u.state] || { text: u.state || "--", cls: "nd-muted" })
              : { text: "状态不可用", cls: "nd-muted" };
    var html = ndBadge(m.text, m.cls);
    html += '<span class="nd-hint">　终端 ' + ndEscapeHtml(u && u.terminal_id ? u.terminal_id : "--") +
        ' ｜ iperf3 ' + (u && u.iperf3_available ? "已内置" : "不可用") +
        (u && u.server_url ? ' ｜ ' + ndEscapeHtml(u.server_url) : "") + '</span>';
    el.innerHTML = html;
    /* 依赖中心的功能置灰/解灰（如实标注，不虚构可用） */
    var on = ndConnected();
    var ids = ["ndConflictBtn", "ndStressBtn"];
    for (var i = 0; i < ids.length; i++) {
        var b = document.getElementById(ids[i]);
        if (b) { b.disabled = !on; }
    }
    var wrap1 = document.getElementById("ndConflictGate");
    if (wrap1) { wrap1.style.display = on ? "none" : "block"; }
    var wrap2 = document.getElementById("ndStressGate");
    if (wrap2) { wrap2.style.display = on ? "none" : "block"; }
}

/* ===================== 配置加载 ===================== */

function ndLoadConfig() {
    ndApiFetch("/api/netdoctor/config").then(function (d) {
        if (d && d.success !== false) {
            ndState.nodes = d.nodes || [];
            ndState.expectedDns = d.expected_dns || [];
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
    var html = "";
    for (var i = 0; i < (r.adapters || []).length; i++) {
        var a = r.adapters[i];
        var head = '<div class="nd-adapter-head"><b>' + ndEscapeHtml(a.name) + '</b>';
        head += a.active ? ndBadge("活动", "nd-info") : ndBadge("非活动", "nd-muted");
        if (a.tunnel) { head += ndBadge("隧道", "nd-muted"); }
        head += '</div>';
        var meta = '<div class="nd-kv"><span class="k">描述</span><span>' + ndEscapeHtml(a.desc || "--") + '</span></div>';
        if (a.ipv4 && a.ipv4.length) {
            meta += '<div class="nd-kv"><span class="k">IPv4</span><span>' + ndEscapeHtml(a.ipv4.join("，")) + '</span></div>';
        }
        if (a.mac) { meta += '<div class="nd-kv"><span class="k">MAC</span><span>' + ndEscapeHtml(a.mac) + '</span></div>'; }
        var rows = "";
        for (var j = 0; j < a.checks.length; j++) {
            var c = a.checks[j];
            rows += '<div class="nd-check-row">' + ndStatusBadge(c.status)
                + '<b>' + ndEscapeHtml(c.item) + '</b>'
                + '<span class="nd-hint">' + ndEscapeHtml(c.reason || "") + '</span></div>';
        }
        html += '<div class="nd-adapter' + (a.active ? "" : " nd-dim") + '">' + head + meta + rows + '</div>';
    }
    if (!html) { html = '<div class="nd-empty">未解析到网卡信息</div>'; }
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
        + ' ｜ MAC ' + ndEscapeHtml(r.mac || "--") + '</span></div>';
    var v = r.verdict;
    if (!v) {
        html += '<div class="nd-empty">平台未返回判定：' + ndEscapeHtml(r.error || "--") + '</div>';
    } else if (v.conflict_suspect) {
        html += '<div style="margin:8px 0">' + ndBadge("疑似 IP 冲突", "nd-err") + '</div>';
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
        if (r.ai) {
            html += '<div class="nd-ai"><b>AI 辅助分析</b>' +
                (r.ai.ok ? '<pre class="nd-pre">' + ndEscapeHtml(r.ai.analysis || "（无内容）") + '</pre>'
                         : '<span class="nd-hint">分析失败：' + ndEscapeHtml(r.ai.error || "--") + '</span>') + '</div>';
        }
    } else {
        html += '<div style="margin:8px 0">' + ndBadge("未发现冲突疑似", "nd-ok") + '</div>';
        if (v.evidence && v.evidence.length) {
            html += '<div class="nd-hint">平台说明：' + ndEscapeHtml(v.evidence.join("；")) + '</div>';
        }
    }
    ndSetTip("ndConflictSummary", "");
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
    ndRenderPingRows(r.results || []);
    var s = r.summary || {};
    ndSetTip("ndPingSummary", "完成：" + s.ok + " 正常 / " + s.err + " 异常" +
        (s.muted ? " / " + s.muted + " 未连接" : "") +
        "（结果已记录到 netdoctor_records）");
}

function ndLoadHistory() {
    ndApiFetch("/api/netdoctor/ping-history?limit=100").then(function (d) {
        ndRenderHistory(d);
    }).catch(function () { ndRenderHistory(null); });
}

function ndRenderHistory(d) {
    var el = document.getElementById("ndHistoryBody");
    if (!el) { return; }
    var agg = (d && d.agg) || [];
    if (!agg.length) {
        el.innerHTML = '<div class="nd-empty">暂无历史记录（每次检测自动留档）</div>';
        return;
    }
    var html = '<table class="nd-table"><tr><th>节点</th><th>样本</th><th>成功率</th><th>平均延迟</th><th>最大延迟</th></tr>';
    for (var i = 0; i < agg.length; i++) {
        var a = agg[i];
        html += '<tr><td>' + ndEscapeHtml(a.key) + '</td><td class="nd-num">' + a.n
            + '</td><td class="nd-num">' + ndFmtPct(a.ok_rate)
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

function ndStartStress() {
    var btn = document.getElementById("ndStressBtn");
    var cancelBtn = document.getElementById("ndStressCancelBtn");
    if (btn) { btn.disabled = true; }
    if (cancelBtn) { cancelBtn.style.display = "inline-block"; }
    var dur = document.getElementById("ndStressDur");
    var sizes = document.getElementById("ndStressSizes");
    var udp = document.getElementById("ndStressUdp");
    var q = "?duration_sec=" + encodeURIComponent(dur ? dur.value : "10")
        + "&sizes=" + encodeURIComponent(sizes ? sizes.value : "64,256,1024,4096")
        + "&udp_mbps=" + encodeURIComponent(udp ? udp.value : "100");
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
