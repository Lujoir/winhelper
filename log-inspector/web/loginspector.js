/* ==========================================
   日志诊断 · 前端业务流程逻辑（Log Inspector）
   业务流程：① 选择条件 → ② 检索 → ③ 下载原始日志(txt)
             → ④ 智能分析 → ⑤ 知识库建议
   命名空间约定（ADR-006）：除入口 initLogInspectorTab 外，
   全部函数/状态使用 li 前缀，避免与 app.js/disk.js/perf.js 冲突。
   工具函数（liEscapeHtml 等）自包含实现，不依赖 app.js。
   ========================================== */

var liCharts = { time: null, source: null };
var liState = { page: 1, totalPages: 1, exportTimer: null, taskId: null, inited: false, access: null };

// ===================== 入口（幂等） =====================
function initLogInspectorTab() {
    if (liState.inited) { return; }
    liState.inited = true;
    liDetectAccess();
    liSearch(1);
    // 本机信息预取（异步不阻塞，点击徽章即显）
    liPrefetchHwinfo();
    var badge = document.getElementById("modeBadge");
    if (badge) {
        badge.title = "终端类型：Windows";
        badge.onclick = liShowSystemPanel;
    }
}

// ===================== 私有工具 =====================
/**
 * 传输函数：主应用复用 app.js 的 apiFetch（桥接/HTTP 自适应）；
 * standalone 页面无 app.js，直接走 pywebview 桩或 fetch。
 */
function liApiFetch(path) {
    if (typeof apiFetch === "function") { return apiFetch(path); }
    if (window.pywebview && window.pywebview.api) {
        return window.pywebview.api.call(path);
    }
    return fetch(path).then(function (r) { return r.json(); });
}

function liEscapeHtml(str) {
    if (!str) { return ""; }
    return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

function liTruncate(str, len) {
    if (!str) { return ""; }
    str = String(str);
    return str.length > len ? str.substring(0, len) + "..." : str;
}

function liShowError(msg) {
    var div = document.createElement("div");
    div.style.cssText = "position:fixed;bottom:24px;right:24px;z-index:999;padding:14px 20px;" +
        "background:#ff5252;color:#fff;border-radius:8px;font-size:14px;font-weight:500;" +
        "box-shadow:0 4px 20px rgba(255,82,82,.4);max-width:400px;";
    div.textContent = "❌ " + msg;
    document.body.appendChild(div);
    setTimeout(function () {
        div.style.opacity = "0"; div.style.transition = "opacity .3s";
        setTimeout(function () { div.remove(); }, 300);
    }, 5000);
}

function liSevLabel(sev) {
    var map = { critical: "严重", error: "错误", warning: "警告", info: "信息" };
    return map[sev] || sev;
}

function liSetStep(n) {
    var flow = document.getElementById("liFlow");
    if (!flow) { return; }
    var steps = flow.querySelectorAll(".li-step");
    for (var i = 0; i < steps.length; i++) {
        steps[i].classList.toggle("on", i < n);
    }
}

// ===================== 权限探测（ADR-004） =====================
async function liDetectAccess() {
    try {
        var data = await liApiFetch("/api/loginspector/access");
        if (!data || !data.success) { return; }
        liState.access = data.access || {};
        var denied = data.denied || [];
        var tips = [];
        document.querySelectorAll("#liTypeChecks .li-type").forEach(function (cb) {
            if (liState.access[cb.value] === false) {
                cb.disabled = true; cb.checked = false;
                cb.parentElement.classList.add("li-type-denied");
                tips.push(cb.parentElement.textContent.trim());
            }
        });
        if (tips.length) {
            var tip = document.getElementById("liDeniedTip");
            tip.textContent = "⚠ 以下类别需要管理员权限，已置灰: " + tips.join("、");
            tip.style.display = "inline-block";
        }
    } catch (e) { /* 权限探测失败不影响主流程 */ }
}

// ===================== 条件读取 =====================
function liOnTimeRangeChange() {
    var v = document.getElementById("liTimeRange").value;
    document.getElementById("liCustomRange").style.display = v === "custom" ? "flex" : "none";
}

function liReadConditions() {
    var types = [];
    document.querySelectorAll("#liTypeChecks .li-type:checked").forEach(function (cb) {
        types.push(cb.value);
    });
    if (types.length === 0) { types = ["System"]; }

    var params = { types: types.join(",") };
    var range = document.getElementById("liTimeRange").value;
    if (range === "custom") {
        var s = document.getElementById("liStart").value || "";
        var e = document.getElementById("liEnd").value || "";
        if (s) { params.start = s.replace("T", " "); }
        if (e) { params.end = e.replace("T", " "); }
        if (!s && !e) { params.hours = "72"; }
    } else {
        params.hours = range || "72";
    }

    var levels = [];
    document.querySelectorAll("#liTypeChecks").forEach(function () { });
    document.querySelectorAll(".li-level:checked").forEach(function (cb) {
        levels.push(cb.value);
    });
    if (levels.length) { params.levels = levels.join(","); }

    var source = document.getElementById("liSource").value;
    if (source) { params.source = source; }
    var kw = document.getElementById("liKeyword").value.trim();
    if (kw) { params.keyword = kw; }
    var eid = document.getElementById("liEventId").value.trim();
    if (eid) { params.event_id = eid; }
    return params;
}

function liBuildQuery(params) {
    var parts = [];
    Object.keys(params).forEach(function (k) {
        parts.push(encodeURIComponent(k) + "=" + encodeURIComponent(params[k]));
    });
    return parts.join("&");
}

function liUpdateCondBadge() {
    var p = liReadConditions();
    var names = { System: "系统日志", Application: "应用程序", Security: "安全日志", Setup: "安装程序" };
    var typeText = p.types.split(",").map(function (t) { return names[t] || t; }).join("+");
    var timeText = p.start ? (p.start + " ~ " + (p.end || "现在")) : ("近" + p.hours + "小时");
    document.getElementById("liCondBadge").textContent = typeText + " · " + timeText;
}

// ===================== ② 检索 =====================
async function liSearch(page) {
    liState.page = page || 1;
    var btn = document.getElementById("liSearchBtn");
    var body = document.getElementById("liBody");
    btn.disabled = true;
    body.innerHTML = '<tr><td colspan="5" class="empty-state">检索中…</td></tr>';
    try {
        var params = liReadConditions();
        params.page = liState.page;
        params.per_page = document.getElementById("liPerPage").value || "50";
        var qs = liBuildQuery(params);
        var data = await liApiFetch("/api/loginspector/search?" + qs);
        if (!data || !data.success) {
            body.innerHTML = '<tr><td colspan="5" class="empty-state">检索失败: ' +
                liEscapeHtml((data && data.error) || "未知错误") + '</td></tr>';
            liShowError((data && data.error) || "检索失败");
            return;
        }
        liRenderStats(data.summary || {});
        liUpdateSources(data.sources || []);
        liRenderTable(data);
        liShowDenied(data.denied || []);
        liSetStep(2);
        liUpdateCondBadge();
    } catch (e) {
        body.innerHTML = '<tr><td colspan="5" class="empty-state">检索请求失败: ' + liEscapeHtml(e.message) + '</td></tr>';
        liShowError("检索请求失败: " + e.message);
    } finally {
        btn.disabled = false;
    }
}

function liShowDenied(denied) {
    var tip = document.getElementById("liDeniedTip");
    if (!denied.length) {
        if (!document.querySelector("#liTypeChecks .li-type-denied")) { tip.style.display = "none"; }
        return;
    }
    var names = denied.map(function (d) { return d.name || d.type; }).join("、");
    tip.textContent = "⚠ 以下类别读取失败（需管理员权限或不存在），已跳过: " + names;
    tip.style.display = "inline-block";
}

function liRenderStats(s) {
    var el = document.getElementById("liStats");
    el.innerHTML =
        '<div class="stat-card stat-total"><div class="stat-value">' + (s.total || 0) + '</div><div class="stat-label">事件总数</div></div>' +
        '<div class="stat-card stat-critical"><div class="stat-value">' + (s.critical || 0) + '</div><div class="stat-label">关键事件</div></div>' +
        '<div class="stat-card stat-error"><div class="stat-value">' + (s.error || 0) + '</div><div class="stat-label">错误事件</div></div>' +
        '<div class="stat-card stat-warning"><div class="stat-value">' + (s.warning || 0) + '</div><div class="stat-label">警告事件</div></div>' +
        '<div class="stat-card stat-info"><div class="stat-value">' + (s.info || 0) + '</div><div class="stat-label">信息事件</div></div>';
    document.getElementById("liTotalBadge").textContent = "共 " + (s.total || 0) + " 条";

    if (typeof Chart === "function") {
        liDrawTimeChart(s.by_hour_labels || [], s.by_hour_values || []);
        liDrawSourceChart(s.top_events || []);
    }
}

function liDrawTimeChart(labels, values) {
    var canvas = document.getElementById("liTimeChart");
    if (!canvas) { return; }
    var ctx = canvas.getContext("2d");
    if (liCharts.time) { liCharts.time.destroy(); }
    liCharts.time = new Chart(ctx, {
        type: "bar",
        data: {
            labels: labels,
            datasets: [{
                label: "事件数",
                data: values,
                backgroundColor: "rgba(0,188,212,.55)",
                borderColor: "rgba(0,188,212,.9)",
                borderWidth: 1,
                borderRadius: 3,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                y: { beginAtZero: true, ticks: { color: "#5f6577" }, grid: { color: "rgba(42,47,69,.5)" } },
                x: { ticks: { color: "#5f6577", maxTicksLimit: 12, font: { size: 10 } }, grid: { display: false } }
            },
            animation: { duration: 400 }
        }
    });
}

function liDrawSourceChart(top) {
    var canvas = document.getElementById("liSourceChart");
    if (!canvas) { return; }
    var ctx = canvas.getContext("2d");
    if (liCharts.source) { liCharts.source.destroy(); }
    var colors = ["#4fc3f7", "#b388ff", "#ff8a65", "#66bb6a", "#ffd54f", "#f06292", "#4dd0e1", "#a1887f", "#90a4ae", "#ce93d8"];
    liCharts.source = new Chart(ctx, {
        type: "doughnut",
        data: {
            labels: top.map(function (e) { return e.name; }),
            datasets: [{
                data: top.map(function (e) { return e.count; }),
                backgroundColor: colors.slice(0, top.length),
                borderColor: "rgba(15,17,23,1)",
                borderWidth: 2,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: "right", labels: { color: "#9aa0b0", padding: 10, font: { size: 10 }, usePointStyle: true, pointStyle: "circle" } }
            },
            animation: { duration: 400 }
        }
    });
}

function liUpdateSources(sources) {
    var sel = document.getElementById("liSource");
    var current = sel.value;
    var html = '<option value="">全部来源</option>';
    sources.forEach(function (s) {
        html += '<option value="' + liEscapeHtml(s) + '">' + liEscapeHtml(liTruncate(s, 40)) + '</option>';
    });
    sel.innerHTML = html;
    if (current) { sel.value = current; }
}

function liRenderTable(data) {
    var tbody = document.getElementById("liBody");
    var events = data.events || [];
    if (events.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="empty-state">📭 没有匹配的事件</td></tr>';
    } else {
        tbody.innerHTML = events.map(function (ev) {
            return '<tr>' +
                '<td style="white-space:nowrap;font-size:12px;color:var(--text-secondary,#8a93a5)">' + liEscapeHtml(ev.timestamp) + '</td>' +
                '<td><strong>' + ev.event_id + '</strong>' + (ev.is_known ? '<span class="known-tag">已知</span>' : '') + '</td>' +
                '<td>' + liEscapeHtml(liTruncate(ev.source, 34)) + '</td>' +
                '<td><span class="level-badge ' + liEscapeHtml(ev.level_class) + '">' + liEscapeHtml(ev.level_name) + '</span></td>' +
                '<td>' + liDescBlock(ev.description, 80) + '</td>' +
                '</tr>';
        }).join("");
    }
    liState.totalPages = data.total_pages || 1;
    liState.page = data.page || 1;
    document.getElementById("liPageInfo").textContent =
        "共 " + data.total + " 条 · 第 " + data.page + "/" + data.total_pages + " 页" +
        (data.truncated ? "（已达单次检索上限）" : "");

    var html = '<button class="page-btn" onclick="liGotoPage(' + (data.page - 1) + ')" ' + (data.page <= 1 ? "disabled" : "") + '>上一页</button>';
    var total = data.total_pages;
    var start = Math.max(1, data.page - 3);
    var end = Math.min(total, data.page + 3);
    if (start > 1) { html += '<button class="page-btn" onclick="liGotoPage(1)">1</button>'; }
    if (start > 2) { html += '<span style="color:var(--text-muted,#6b7280);padding:0 4px">...</span>'; }
    for (var i = start; i <= end; i++) {
        html += '<button class="page-btn ' + (i === data.page ? "active" : "") + '" onclick="liGotoPage(' + i + ')">' + i + '</button>';
    }
    if (end < total - 1) { html += '<span style="color:var(--text-muted,#6b7280);padding:0 4px">...</span>'; }
    if (end < total) { html += '<button class="page-btn" onclick="liGotoPage(' + total + ')">' + total + '</button>'; }
    html += '<button class="page-btn" onclick="liGotoPage(' + (data.page + 1) + ')" ' + (data.page >= total ? "disabled" : "") + '>下一页</button>';
    document.getElementById("liPageNums").innerHTML = html;
}

function liGotoPage(n) {
    if (n < 1 || n > liState.totalPages) { return; }
    liSearch(n);
}

function liChangePage(delta) {
    liGotoPage(liState.page + delta);
}

// ===================== ③ 下载原始日志（txt 导出任务，ADR-003） =====================
async function liStartExport() {
    var btn = document.getElementById("liExportBtn");
    try {
        var qs = liBuildQuery(liReadConditions());
        var data = await liApiFetch("/api/loginspector/export-start?" + qs);
        if (!data || !data.success) {
            liShowError((data && data.error) || "导出任务启动失败");
            return;
        }
        liState.taskId = data.task_id;
        btn.disabled = true;
        document.getElementById("liExportCancelBtn").style.display = "";
        document.getElementById("liExportProgress").style.display = "";
        document.getElementById("liExportResult").style.display = "none";
        document.getElementById("liExportResult").innerHTML = "";
        liSetExportText("导出任务已启动，流式读取中…");
        if (liState.exportTimer) { clearInterval(liState.exportTimer); }
        liState.exportTimer = setInterval(liPollExport, 800);
        liPollExport();
    } catch (e) {
        liShowError("导出请求失败: " + e.message);
    }
}

function liSetExportText(t) {
    document.getElementById("liExportText").textContent = t;
}

async function liPollExport() {
    if (!liState.taskId) { return; }
    try {
        var data = await liApiFetch("/api/loginspector/export-status?task_id=" + encodeURIComponent(liState.taskId));
        if (!data || !data.success) {
            liFinishExport(null, (data && data.error) || "任务不存在或已过期");
            return;
        }
        var t = data.task || {};
        var p = t.progress || {};
        if (t.status === "running") {
            var pct = Math.min(95, Math.round((p.written || 0) / 100));
            document.getElementById("liExportBar").style.width = pct + "%";
            liSetExportText("已写出 " + (p.written || 0) + " 条 · 已扫描 " + (p.scanned || 0) + " 条 · 用时 " + (t.elapsed || 0) + "s（流式处理中）");
        } else if (t.status === "done") {
            liFinishExport(t.result, null);
        } else if (t.status === "cancelled") {
            liFinishExport(null, "导出已取消（部分数据未写出）");
        } else {
            liFinishExport(null, t.error || "导出失败");
        }
    } catch (e) {
        liFinishExport(null, "轮询失败: " + e.message);
    }
}

function liFinishExport(result, errMsg) {
    if (liState.exportTimer) { clearInterval(liState.exportTimer); liState.exportTimer = null; }
    liState.taskId = null;
    document.getElementById("liExportBtn").disabled = false;
    document.getElementById("liExportCancelBtn").style.display = "none";
    var box = document.getElementById("liExportResult");
    box.style.display = "";
    if (errMsg) {
        document.getElementById("liExportBar").style.width = "0%";
        liSetExportText("—" + errMsg);
        if (errMsg.indexOf("取消") < 0) { liShowError(errMsg); }
        return;
    }
    document.getElementById("liExportBar").style.width = "100%";
    liSetExportText("导出完成：" + result.count + " 条事件");
    var path = result.path || "";
    box.innerHTML = "✅ 已生成 <b>" + liEscapeHtml(result.filename || "LogExport.txt") + "</b>（" +
        result.count + " 条事件）<br>" +
        '<span style="font-size:12px;color:var(--text-secondary,#8a93a5)">路径: ' + liEscapeHtml(path) + '</span> ' +
        '<button class="btn btn-mini btn-ghost" data-p="' + liEscapeHtml(path) + '" onclick="liOpenLocation(this.dataset.p)">📂 打开位置</button>';
    liSetStep(3);
}

async function liCancelExport() {
    if (!liState.taskId) { return; }
    try {
        await liApiFetch("/api/loginspector/export-cancel?task_id=" + encodeURIComponent(liState.taskId));
    } catch (e) { /* 取消失败继续等轮询 */ }
}

async function liOpenLocation(path) {
    try {
        var data = await liApiFetch("/api/disk/open-location?path=" + encodeURIComponent(path));
        if (!data.success) { liShowError(data.error || "定位失败"); }
    } catch (e) {
        liShowError("定位请求失败: " + e.message);
    }
}

// ===================== ④ 智能分析（ADR-007） =====================
async function liRunAnalysis() {
    var btn = document.getElementById("liAnalysisBtn");
    var box = document.getElementById("liPatterns");
    btn.disabled = true;
    box.innerHTML = '<div class="empty-state-full"><div class="loader-ring"></div><p>正在按当前条件分析…</p></div>';
    try {
        var qs = liBuildQuery(liReadConditions());
        var data = await liApiFetch("/api/loginspector/analyze?" + qs);
        if (!data || !data.success) {
            box.innerHTML = '<div class="empty-state-full">分析失败: ' + liEscapeHtml((data && data.error) || "未知") + '</div>';
            liShowError((data && data.error) || "分析失败");
            return;
        }
        liRenderAnalysis(data);
        liSetStep(4);
    } catch (e) {
        box.innerHTML = '<div class="empty-state-full">分析请求失败: ' + liEscapeHtml(e.message) + '</div>';
        liShowError("分析请求失败: " + e.message);
    } finally {
        btn.disabled = false;
    }
}

function liRenderAnalysis(data) {
    // 结论徽章
    var c = data.conclusion || {};
    var colorMap = { critical: "#ef5350", error: "#ff8a65", warning: "#ffd54f", info: "#66bb6a" };
    var color = colorMap[c.level] || "#aab3c5";
    var concl = document.getElementById("liConclusion");
    concl.style.display = "";
    concl.innerHTML = '<div class="li-conclusion" style="border-color:' + color + '">' +
        '<span class="li-concl-badge" style="color:' + color + ';border-color:' + color + '">' +
        liEscapeHtml(liSevLabel(c.level)) + '</span>' +
        '<div><b>' + liEscapeHtml(c.text || "") + '</b>' +
        '<div style="font-size:12px;color:var(--text-secondary,#8a93a5);margin-top:4px">' +
        '扫描 ' + (data.scanned || 0) + ' 条事件 · ' + liEscapeHtml(data.conditions || "") +
        ' · ' + liEscapeHtml(data.generated_at || "") + '</div></div></div>';
    document.getElementById("liAnalysisBadge").textContent = liSevLabel(c.level) + " · " + ((data.patterns || []).length) + " 类模式";

    // 故障模式卡片（含证据 + 建议）
    var box = document.getElementById("liPatterns");
    var patterns = data.patterns || [];
    if (patterns.length === 0) {
        box.innerHTML = '<div class="empty-state-full">✅ 未检测到故障模式，系统状态良好</div>';
    } else {
        box.innerHTML = patterns.map(function (f) {
            var evHtml = (f.evidence || []).map(function (e) {
                return '<li><code>' + liEscapeHtml(e.timestamp) + '</code> [' +
                    liEscapeHtml(liTruncate(e.source, 30)) + ' / ID ' + e.event_id + '] ' +
                    liDescBlock((e.description || "").replace(/\s+/g, " "), 90) + '</li>';
            }).join("");
            var sugHtml = (f.suggestions || []).map(function (s) { return "<li>" + liEscapeHtml(s) + "</li>"; }).join("");
            return '<div class="fault-card" onclick="liToggleFault(this)">' +
                '<div class="fault-header">' +
                '<span class="fault-severity ' + liEscapeHtml(f.severity) + '"></span>' +
                '<span class="fault-name">' + liEscapeHtml(f.name) + '</span>' +
                '<span class="fault-count">' + f.count + ' 次</span>' +
                '<span class="level-badge ' + liEscapeHtml(f.severity) + '">' + liEscapeHtml(liSevLabel(f.severity)) + '</span>' +
                '<svg class="fault-arrow" viewBox="0 0 24 24" width="18" height="18"><path d="M7 10l5 5 5-5z" fill="currentColor"/></svg>' +
                '</div>' +
                '<div class="fault-body">' +
                (evHtml ? '<h5>📎 典型证据</h5><ul>' + evHtml + '</ul>' : "") +
                '<h5>🔧 处理建议（知识库）</h5><ul>' + sugHtml + '</ul>' +
                '</div></div>';
        }).join("");
    }

    // 已知问题事件表
    var ki = data.known_issues || [];
    document.getElementById("liKnownBadge").textContent = "共 " + (data.known_count || ki.length) + " 条";
    var kb = document.getElementById("liKnownBlock");
    if (ki.length === 0) {
        document.getElementById("liKnownBody").innerHTML =
            '<tr><td colspan="6" class="empty-state">✅ 未发现已知问题事件</td></tr>';
    } else {
        document.getElementById("liKnownBody").innerHTML = ki.map(function (ev) {
            return '<tr>' +
                '<td style="white-space:nowrap;font-size:12px;color:var(--text-secondary,#8a93a5)">' + liEscapeHtml(ev.timestamp) + '</td>' +
                '<td><strong>' + ev.event_id + '</strong></td>' +
                '<td>' + liEscapeHtml(ev.known_name) + ' <span class="known-tag">已知</span></td>' +
                '<td>' + liEscapeHtml(liTruncate(ev.source, 30)) + '</td>' +
                '<td><span class="level-badge ' + liEscapeHtml(ev.level_class) + '">' + liEscapeHtml(ev.level_name) + '</span></td>' +
                '<td>' + liDescBlock(ev.description, 60) + '</td>' +
                '</tr>';
        }).join("");
    }
    kb.style.display = "";

    // ⑤ 知识库建议区：分析出的模式自动带建议
    liRenderKnowledgeFromPatterns(patterns);
    document.getElementById("liReportBtn").style.display = "";
    liSetStep(5);
}

function liToggleFault(el) {
    el.classList.toggle("expanded");
}

// ===================== ⑤ 知识库建议 =====================
function liRenderKnowledgeFromPatterns(patterns) {
    var cards = (patterns || []).map(function (f) {
        return {
            name: f.name,
            severity: f.severity,
            event_ids: [],
            sources: [],
            suggestions: f.suggestions || [],
            matched: f.count
        };
    });
    liRenderKnowledgeList(cards, "liKnowledgeGrid", true);
}

function liRenderKnowledgeList(list, containerId, showMatched) {
    var container = document.getElementById(containerId);
    if (!list || list.length === 0) {
        container.innerHTML = '<div class="empty-state-full">没有匹配的知识条目</div>';
        return;
    }
    container.innerHTML = list.map(function (k) {
        var ids = (k.event_ids || []).join(", ");
        return '<div class="knowledge-card">' +
            '<h3>' + liEscapeHtml(k.name) + (showMatched && k.matched ? '<span class="known-tag" style="margin-left:8px">本次匹配 ' + k.matched + ' 次</span>' : '') + '</h3>' +
            '<div class="knowledge-meta">' +
            '<span class="knowledge-tag level-badge ' + liEscapeHtml(k.severity) + '">' + liEscapeHtml(liSevLabel(k.severity)) + '</span>' +
            (ids ? '<span class="knowledge-tag" style="background:rgba(100,181,246,.15);color:var(--color-info,#4fc3f7)">事件ID: ' + liEscapeHtml(ids) + '</span>' : '') +
            '</div>' +
            '<div class="knowledge-body"><strong style="font-size:12px">处理建议</strong><ul>' +
            (k.suggestions || []).map(function (s) { return "<li>" + liEscapeHtml(s) + "</li>"; }).join("") +
            '</ul></div></div>';
    }).join("");
}

async function liFilterKnowledge() {
    var q = document.getElementById("liKbQuery").value.trim();
    try {
        var data = await liApiFetch("/api/loginspector/knowledge" + (q ? "?q=" + encodeURIComponent(q) : ""));
        if (!data || !data.success) {
            liShowError((data && data.error) || "知识库加载失败");
            return;
        }
        liRenderKnowledgeList(data.knowledge || [], "liKnowledgeGrid", false);
    } catch (e) {
        liShowError("知识库请求失败: " + e.message);
    }
}

// ===================== 终端标识 + 本机基础信息面板（团队补充需求2） =====================
// 数据源 /api/perf/hwinfo（perf-analyzer-dev 提供，含扩展 os/hostname/network 字段）；
// 启动时异步预取 + 会话内缓存；点击 modeBadge 弹出；禁止触碰 perf_service.py。

function liHumanSize(n) {
    if (n === null || n === undefined || isNaN(n)) { return "--"; }
    var units = ["B", "KB", "MB", "GB", "TB"];
    var i = 0, v = Number(n);
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return (i === 0 ? v : v.toFixed(v >= 100 ? 0 : 1)) + " " + units[i];
}

function liMhzText(mhz) {
    if (!mhz) { return "--"; }
    return (mhz / 1000).toFixed(2) + " GHz";
}

async function liPrefetchHwinfo() {
    if (liState.hwinfoState === "ok" || liState.hwinfoState === "loading") { return; }
    liState.hwinfoState = "loading";
    try {
        var data = await liApiFetch("/api/perf/hwinfo");
        if (data && data.success && data.hwinfo) {
            liState.hwinfo = data.hwinfo;
            liState.hwinfoState = "ok";
        } else {
            liState.hwinfoState = "error";
        }
    } catch (e) {
        liState.hwinfoState = "error";
    }
}

function liShowSystemPanel() {
    var panel = document.getElementById("liSysPanel");
    if (!panel) {
        panel = document.createElement("div");
        panel.id = "liSysPanel";
        panel.className = "li-sys-overlay";
        document.body.appendChild(panel);
    }
    panel.style.display = "flex";
    liPrefetchHwinfo().then(function () { liRenderSystemPanel(); });
    liRenderSystemPanel();
}

function liHideSystemPanel(ev) {
    if (ev && ev.target && ev.target.closest && ev.target.closest(".li-sys-panel")) { return; }
    var panel = document.getElementById("liSysPanel");
    if (panel) { panel.style.display = "none"; }
}

function liSysRetry() {
    liState.hwinfoState = null;
    liPrefetchHwinfo().then(function () { liRenderSystemPanel(); });
}

function liSysRow(k, v) {
    return '<div class="li-sys-row"><span class="li-sys-k">' + liEscapeHtml(k) + '</span>' +
        '<span class="li-sys-v">' + v + '</span></div>';
}

function liRenderSystemPanel() {
    var panel = document.getElementById("liSysPanel");
    if (!panel) { return; }
    var inner = document.querySelector("#liSysPanel .li-sys-body");
    var state = liState.hwinfoState;
    var html = "";

    if (state === "loading") {
        html = '<div class="empty-state-full"><div class="loader-ring"></div><p>正在读取本机信息…</p></div>';
    } else if (state === "error") {
        html = '<div class="empty-state-full"><p>本机信息读取失败</p>' +
            '<button class="btn btn-ghost" onclick="liSysRetry()">↻ 重试</button></div>';
    } else {
        var h = liState.hwinfo || {};
        var cpu = h.cpu || {};
        var mem = h.memory || {};
        var osRaw = h.os;
        var osText = (osRaw && typeof osRaw === "object")
            ? (osRaw.text || [osRaw.caption, osRaw.version].filter(Boolean).join(" ") || "--")
            : (osRaw || "--");
        var hostText = h.hostname || h.computer || "--";

        html += '<h4>操作系统</h4>';
        html += liSysRow("操作系统版本", liEscapeHtml(osText));
        html += liSysRow("计算机名", liEscapeHtml(hostText));

        // 网络（perf-analyzer-dev 扩展字段，防御性渲染）
        html += '<h4>本地网络配置</h4>';
        var nets = h.network || [];
        if (nets.length) {
            html += '<table class="event-table li-sys-table"><thead><tr><th>网卡</th><th>IPv4</th><th>IPv6</th><th>MAC</th><th>状态</th><th>网关 / DNS</th></tr></thead><tbody>';
            nets.forEach(function (n) {
                html += '<tr><td>' + liEscapeHtml(liTruncate(n.name || "--", 22)) + '</td>' +
                    '<td>' + liEscapeHtml(n.ipv4 || "--") + '</td>' +
                    '<td style="font-size:11px">' + liEscapeHtml(liTruncate(n.ipv6 || "--", 26)) + '</td>' +
                    '<td style="font-size:11px">' + liEscapeHtml(n.mac || "--") + '</td>' +
                    '<td>' + liEscapeHtml(n.status === "up" || n.state === "up" ? "在线" : (n.status || n.state || "--")) + '</td>' +
                    '<td style="font-size:11px">' + liEscapeHtml(liTruncate(n.gateway || "--", 20)) +
                    (n.dns ? ' / ' + liEscapeHtml(liTruncate(n.dns, 20)) : '') + '</td></tr>';
            });
            html += '</tbody></table>';
        } else {
            html += '<div class="li-sys-none">网络信息暂不可用（等待模块扩展）</div>';
        }

        html += '<h4>CPU</h4>';
        html += liSysRow("型号", liEscapeHtml(cpu.name || "--"));
        html += liSysRow("核心 / 线程", (cpu.cores || "--") + " 核 / " + (cpu.logical || "--") + " 线程");
        html += liSysRow("最高频率", liEscapeHtml(liMhzText(cpu.max_mhz)) +
            (cpu.cur_mhz ? "（当前 " + liEscapeHtml(liMhzText(cpu.cur_mhz)) + "）" : ""));

        html += '<h4>内存</h4>';
        html += liSysRow("总容量", liEscapeHtml(liHumanSize(mem.total)));
        var mods = mem.modules || [];
        if (mods.length) {
            html += '<table class="event-table li-sys-table"><thead><tr><th>插槽</th><th>容量</th><th>代际</th><th>频率</th></tr></thead><tbody>';
            mods.forEach(function (m) {
                html += '<tr><td>' + liEscapeHtml(m.slot || "--") + '</td>' +
                    '<td>' + liEscapeHtml(liHumanSize(m.size)) + '</td>' +
                    '<td>' + liEscapeHtml(m.type || "--") + '</td>' +
                    '<td>' + (m.speed_mhz ? m.speed_mhz + " MHz" : "--") + '</td></tr>';
            });
            html += '</tbody></table>';
        } else {
            html += '<div class="li-sys-none">未获取到内存条详情</div>';
        }

        html += '<h4>硬盘</h4>';
        var disks = h.disks || [];
        if (disks.length) {
            html += '<table class="event-table li-sys-table"><thead><tr><th>型号</th><th>容量</th><th>介质</th><th>卷</th><th>标识</th></tr></thead><tbody>';
            disks.forEach(function (d) {
                html += '<tr><td>' + liEscapeHtml(liTruncate(d.model || "--", 30)) + '</td>' +
                    '<td>' + liEscapeHtml(liHumanSize(d.size)) + '</td>' +
                    '<td>' + liEscapeHtml((d.media || "--") + (d.bus && d.bus !== "--" ? " · " + d.bus : "")) + '</td>' +
                    '<td>' + liEscapeHtml((d.volumes || []).join(" ") || "--") + '</td>' +
                    '<td>' + (d.system ? '<span class="known-tag">系统盘</span>' : "--") + '</td></tr>';
            });
            html += '</tbody></table>';
        } else {
            html += '<div class="li-sys-none">未获取到磁盘信息</div>';
        }

        html += '<h4>显卡</h4>';
        var gpus = h.gpu || [];
        if (gpus.length) {
            gpus.forEach(function (g) {
                var spec = [];
                if (g.vram_text && g.vram_text !== "--") spec.push("显存 " + g.vram_text);
                if (g.driver && g.driver !== "--") spec.push("驱动 " + g.driver);
                if (g.resolution && g.resolution !== "--") spec.push(g.resolution);
                html += liSysRow(liEscapeHtml(liTruncate(g.name || "--", 40)),
                    '<span class="li-sys-gpu-tag">' + (g.dedicated ? "独显" : "核显") + '</span>' +
                    (spec.length ? '<div class="li-sys-v" style="font-size:12px">' + liEscapeHtml(spec.join(" · ")) + '</div>' : ''));
            });
        } else {
            html += '<div class="li-sys-none">未获取到显卡信息</div>';
        }
    }

    if (!inner) {
        panel.innerHTML = '<div class="li-sys-panel">' +
            '<div class="li-sys-head"><h3>本机基础信息</h3>' +
            '<button class="btn btn-mini btn-ghost" onclick="liHideSystemPanel()">✖ 关闭</button></div>' +
            '<div class="li-sys-body"></div></div>';
        panel.addEventListener("click", liHideSystemPanel);
        inner = document.querySelector("#liSysPanel .li-sys-body");
    }
    inner.innerHTML = html;
}

// ===================== HTML 报告导出（ADR-005） =====================
async function liExportReport() {
    var btn = document.getElementById("liReportBtn");
    btn.disabled = true;
    try {
        var qs = liBuildQuery(liReadConditions());
        var data = await liApiFetch("/api/loginspector/report-export?" + qs);
        if (!data || !data.success) {
            liShowError((data && data.error) || "报告导出失败");
            return;
        }
        var box = document.getElementById("liConclusion");
        var note = document.createElement("div");
        note.style.cssText = "font-size:12px;margin-top:8px;color:var(--text-secondary,#8a93a5)";
        note.innerHTML = "📄 报告已生成: <b>" + liEscapeHtml(data.filename) + "</b> " +
            '<button class="btn btn-mini btn-ghost" data-p="' + liEscapeHtml(data.path) + '" onclick="liOpenLocation(this.dataset.p)">📂 打开位置</button>';
        var old = document.getElementById("liReportNote");
        if (old) { old.remove(); }
        note.id = "liReportNote";
        box.appendChild(note);
    } catch (e) {
        liShowError("报告导出请求失败: " + e.message);
    } finally {
        btn.disabled = false;
    }
}

// ===================== 描述收缩/展开（文字可复制） =====================
var liDescSeq = 0;
function liDescBlock(text, maxChars) {
    text = (text === null || text === undefined) ? "" : String(text);
    var id = "liDesc" + (++liDescSeq);
    var clamped = text.replace(/\s+/g, " ").length > maxChars;
    var html = '<div class="li-desc' + (clamped ? ' li-desc-clamp' : '') + '" id="' + id + '">' + liEscapeHtml(text) + '</div>';
    if (clamped) {
        html += '<a href="javascript:void(0)" class="li-desc-toggle" data-target="' + id + '" onclick="liToggleDesc(\'' + id + '\', event);return false;">展开</a>';
    }
    return html;
}
function liToggleDesc(id, ev) {
    if (ev) { ev.stopPropagation(); }
    var el = document.getElementById(id);
    if (!el) return;
    var clamped = el.classList.toggle("li-desc-clamp");
    var t = document.querySelector('.li-desc-toggle[data-target="' + id + '"]');
    if (t) { t.textContent = clamped ? "展开" : "收起"; }
}
