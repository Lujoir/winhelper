/* ==========================================
   Windows 系统故障分析系统 - 前端交互逻辑
   ========================================== */

// 全局状态
let charts = { time: null, source: null };
let currentPage = 1;
let currentLogType = "System";

// DOM 就绪
document.addEventListener("DOMContentLoaded", () => {
    // 默认加载仪表盘
    reloadData();
    loadKnowledge();
});

// ===================== Tab 切换 =====================
function switchTab(tab) {
    document.querySelectorAll(".nav-tab").forEach(el => el.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach(el => el.classList.remove("active"));
    document.querySelector(`[data-tab="${tab}"]`).classList.add("active");
    document.getElementById(`tab-${tab}`).classList.add("active");

    if (tab === "logs") loadLogs(1);
    if (tab === "analysis") loadAnalysis();
    if (tab === "knowledge") loadKnowledge();
}

// ===================== 数据加载 =====================
function reloadData() {
    currentLogType = document.getElementById("logTypeSelector").value;
    const loading = document.getElementById("loadingOverlay");
    loading.classList.add("active");

    // 并行加载仪表盘和分析数据
    Promise.all([
        fetch(`/api/analyze?type=${currentLogType}&max=1000`).then(r => r.json()),
    ]).then(([data]) => {
        loading.classList.remove("active");
        if (data.success) {
            updateDashboard(data);
            // 缓存分析数据
            window._analysisData = data;
            // 如果当前在分析 Tab 则渲染
            if (document.getElementById("tab-analysis").classList.contains("active")) {
                renderFaults(data);
            }
            // 如果当前在日志 Tab 则刷新
            if (document.getElementById("tab-logs").classList.contains("active")) {
                loadLogs(1);
            }
        } else {
            showError(data.error || "加载数据失败");
        }
    }).catch(err => {
        loading.classList.remove("active");
        showError("网络错误: " + err.message);
    });
}

// ===================== 仪表盘更新 =====================
function updateDashboard(data) {
    const s = data.summary;
    const cards = document.getElementById("statsCards");
    cards.innerHTML = `
        <div class="stat-card stat-total"><div class="stat-value">${s.total}</div><div class="stat-label">事件总数</div></div>
        <div class="stat-card stat-critical"><div class="stat-value">${s.critical}</div><div class="stat-label">关键事件</div></div>
        <div class="stat-card stat-error"><div class="stat-value">${s.error}</div><div class="stat-label">错误事件</div></div>
        <div class="stat-card stat-warning"><div class="stat-value">${s.warning}</div><div class="stat-label">警告事件</div></div>
        <div class="stat-card stat-info"><div class="stat-value">${s.info}</div><div class="stat-label">信息事件</div></div>
    `;

    // 更新时间
    document.getElementById("summaryTime").textContent =
        `${data.log_type} · 共分析 ${s.total} 条事件 · ${new Date().toLocaleString()}`;

    // 时间分布图
    drawTimeChart(s.by_hour_labels, s.by_hour_values);
    // 来源TOP10图
    drawSourceChart(s.top_events);

    // 关键事件表格
    const tbody = document.getElementById("criticalEventsBody");
    document.getElementById("criticalBadge").textContent = `共 ${data.critical_count} 条`;
    if (data.critical_events.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="empty-state">✅ 未发现已知问题事件</td></tr>';
    } else {
        tbody.innerHTML = data.critical_events.map(ev => `
            <tr>
                <td style="white-space:nowrap;font-size:12px;color:var(--text-secondary)">${ev.timestamp}</td>
                <td><strong>${ev.event_id}</strong></td>
                <td>${ev.event_name} <span class="known-tag">已知</span></td>
                <td>${ev.source}</td>
                <td><span class="level-badge ${ev.level_class}">${ev.level_name}</span></td>
                <td title="${escapeHtml(ev.description)}">${truncate(ev.description, 60)}</td>
            </tr>
        `).join("");
    }
}

// ===================== 图表绘制 =====================
function drawTimeChart(labels, values) {
    const ctx = document.getElementById("timeChart").getContext("2d");
    if (charts.time) charts.time.destroy();

    const hasData = values.some(v => v > 0);

    charts.time = new Chart(ctx, {
        type: "bar",
        data: {
            labels: labels,
            datasets: [{
                label: "事件数",
                data: values,
                backgroundColor: hasData
                    ? values.map(v => v > 1 ? "rgba(0,188,212,.6)" : "rgba(0,188,212,.15)")
                    : "rgba(0,188,212,.15)",
                borderColor: hasData
                    ? values.map(v => v > 1 ? "rgba(0,188,212,.9)" : "rgba(0,188,212,.3)")
                    : "rgba(0,188,212,.3)",
                borderWidth: 1,
                borderRadius: 3,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: "rgba(30,34,49,.95)",
                    borderColor: "rgba(42,47,69,1)",
                    borderWidth: 1,
                    cornerRadius: 6,
                    padding: 10,
                    titleColor: "#e8eaed",
                    bodyColor: "#9aa0b0",
                },
            },
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: {
                        color: "#5f6577",
                        stepSize: Math.max(1, Math.ceil(Math.max(...values, 1) / 5)),
                    },
                    grid: { color: "rgba(42,47,69,.5)" },
                },
                x: {
                    ticks: {
                        color: "#5f6577",
                        maxTicksLimit: 24,
                        font: { size: 10 },
                    },
                    grid: { display: false },
                },
            },
            animation: { duration: 600 },
        },
    });
}

function drawSourceChart(topSources) {
    const ctx = document.getElementById("sourceChart").getContext("2d");
    if (charts.source) charts.source.destroy();

    const colors = [
        "#4fc3f7","#b388ff","#ff8a65","#66bb6a","#ffd54f",
        "#f06292","#4dd0e1","#a1887f","#90a4ae","#ce93d8"
    ];

    charts.source = new Chart(ctx, {
        type: "doughnut",
        data: {
            labels: topSources.map(e => e.name),
            datasets: [{
                data: topSources.map(e => e.count),
                backgroundColor: colors.slice(0, topSources.length),
                borderColor: "rgba(15,17,23,1)",
                borderWidth: 2,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: "right",
                    labels: {
                        color: "#9aa0b0",
                        padding: 12,
                        font: { size: 11 },
                        usePointStyle: true,
                        pointStyle: "circle",
                    },
                },
                tooltip: {
                    backgroundColor: "rgba(30,34,49,.95)",
                    borderColor: "rgba(42,47,69,1)",
                    borderWidth: 1,
                    cornerRadius: 6,
                    padding: 10,
                    titleColor: "#e8eaed",
                    bodyColor: "#9aa0b0",
                    callbacks: {
                        label: function(ctx) {
                            const total = ctx.dataset.data.reduce((a,b) => a + b, 0);
                            const pct = ((ctx.parsed / total) * 100).toFixed(1);
                            return ` ${ctx.label}: ${ctx.parsed} (${pct}%)`;
                        },
                    },
                },
            },
            animation: { duration: 600 },
        },
    });
}

// ===================== 日志查看 =====================
function loadLogs(page) {
    currentPage = page || 1;
    const type = document.getElementById("logTypeSelector").value;
    const level = document.getElementById("logLevelFilter").value;
    const perPage = parseInt(document.getElementById("pageSize").value);

    fetch(`/api/events?type=${type}&max=5000&level=${level}&page=${currentPage}&per_page=${perPage}`)
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                renderLogsTable(data);
            } else {
                document.getElementById("logsBody").innerHTML =
                    `<tr><td colspan="5" class="empty-state">错误: ${data.error}</td></tr>`;
            }
        });
}

function renderLogsTable(data) {
    const tbody = document.getElementById("logsBody");
    if (data.events.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="empty-state">📭 没有匹配的事件</td></tr>';
    } else {
        tbody.innerHTML = data.events.map(ev => `
            <tr>
                <td style="white-space:nowrap;font-size:12px;color:var(--text-secondary)">${ev.timestamp}</td>
                <td><strong>${ev.event_id}</strong>${ev.is_known ? '<span class="known-tag">已知</span>' : ''}</td>
                <td>${ev.source}</td>
                <td><span class="level-badge ${ev.level_class}">${ev.level_name}</span></td>
                <td title="${escapeHtml(ev.description)}">${truncate(ev.description, 80)}</td>
            </tr>
        `).join("");
    }

    // 分页
    document.getElementById("paginationInfo").textContent =
        `共 ${data.total} 条 · 第 ${data.page}/${data.total_pages} 页`;

    const bar = document.getElementById("paginationBar");
    let html = `<button class="page-btn" onclick="loadLogs(${data.page - 1})" ${data.page <= 1 ? 'disabled' : ''}>上一页</button>`;

    // 显示页码（最多显示7个）
    const total = data.total_pages;
    let start = Math.max(1, data.page - 3);
    let end = Math.min(total, data.page + 3);
    if (start > 1) html += `<button class="page-btn" onclick="loadLogs(1)">1</button>`;
    if (start > 2) html += `<span style="color:var(--text-muted);padding:0 4px">...</span>`;
    for (let i = start; i <= end; i++) {
        html += `<button class="page-btn ${i === data.page ? 'active' : ''}" onclick="loadLogs(${i})">${i}</button>`;
    }
    if (end < total - 1) html += `<span style="color:var(--text-muted);padding:0 4px">...</span>`;
    if (end < total) html += `<button class="page-btn" onclick="loadLogs(${total})">${total}</button>`;

    html += `<button class="page-btn" onclick="loadLogs(${data.page + 1})" ${data.page >= total ? 'disabled' : ''}>下一页</button>`;

    document.getElementById("pageNumbers").innerHTML = html;
}

// ===================== 故障分析 =====================
function loadAnalysis() {
    const container = document.getElementById("faultsContainer");
    container.innerHTML = '<div class="empty-state-full"><div class="loader-ring"></div><p>正在分析...</p></div>';

    if (window._analysisData && window._analysisData.success) {
        renderFaults(window._analysisData);
    } else {
        reloadData();
    }
}

function renderFaults(data) {
    // 风险摘要
    const riskSummary = document.getElementById("riskSummary");
    const high = data.high_risk_faults || [];
    const faults = data.faults || [];
    const criticalCount = faults.filter(f => f.severity === "critical").length;
    const errorCount = faults.filter(f => f.severity === "error").length;
    const warningCount = faults.filter(f => f.severity === "warning").length;

    riskSummary.innerHTML = `
        <div class="risk-card" style="border-left-color:var(--color-critical)">
            <h4 style="color:var(--color-critical)">${criticalCount}</h4>
            <p>严重故障</p>
        </div>
        <div class="risk-card" style="border-left-color:var(--color-error)">
            <h4 style="color:var(--color-error)">${errorCount}</h4>
            <p>错误故障</p>
        </div>
        <div class="risk-card" style="border-left-color:var(--color-warning)">
            <h4 style="color:var(--color-warning)">${warningCount}</h4>
            <p>警告故障</p>
        </div>
        <div class="risk-card" style="border-left-color:var(--color-info)">
            <h4 style="color:var(--color-info)">${faults.length}</h4>
            <p>故障类型总计</p>
        </div>
    `;

    // 故障列表
    const container = document.getElementById("faultsContainer");
    if (faults.length === 0) {
        container.innerHTML = `
            <div class="empty-state-full">
                <svg viewBox="0 0 24 24" width="48" height="48"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z" fill="#66bb6a" opacity="0.4"/></svg>
                <p style="color:var(--color-success)">未检测到故障模式，系统状态良好</p>
            </div>`;
        return;
    }

    const severityOrder = { critical: 0, error: 1, warning: 2, info: 3 };
    faults.sort((a, b) => (severityOrder[a.severity] || 9) - (severityOrder[b.severity] || 9));

    container.innerHTML = faults.map((f, i) => `
        <div class="fault-card" onclick="toggleFault(this)">
            <div class="fault-header">
                <span class="fault-severity ${f.severity}"></span>
                <span class="fault-name">${f.name}</span>
                <span class="fault-count">${f.count} 次</span>
                <span class="level-badge ${f.severity}">${getSeverityLabel(f.severity)}</span>
                <svg class="fault-arrow" viewBox="0 0 24 24" width="18" height="18"><path d="M7 10l5 5 5-5z" fill="currentColor"/></svg>
            </div>
            <div class="fault-body">
                <h5>🔧 修复建议</h5>
                <ul>
                    ${f.suggestions.map(s => `<li>${s}</li>`).join("")}
                </ul>
            </div>
        </div>
    `).join("");
}

function toggleFault(el) {
    el.classList.toggle("expanded");
}

function getSeverityLabel(severity) {
    const map = { critical: "严重", error: "错误", warning: "警告", info: "信息" };
    return map[severity] || severity;
}

// ===================== 知识库 =====================
function loadKnowledge() {
    fetch("/api/knowledge")
        .then(r => r.json())
        .then(data => {
            const container = document.getElementById("knowledgeContainer");
            if (!data.success) {
                container.innerHTML = `<div class="empty-state-full">加载失败: ${data.error}</div>`;
                return;
            }
            container.innerHTML = data.knowledge.map(k => `
                <div class="knowledge-card">
                    <h3>${k.name}</h3>
                    <div class="knowledge-meta">
                        <span class="knowledge-tag level-badge ${k.severity}">${getSeverityLabel(k.severity)}</span>
                        ${k.event_ids.length > 0 ?
                            `<span class="knowledge-tag" style="background:rgba(100,181,246,.15);color:var(--color-info)">
                                事件ID: ${k.event_ids.join(", ")}
                            </span>` : ''}
                    </div>
                    <div class="knowledge-body">
                        <strong style="color:var(--text-primary);font-size:12px;">修复建议</strong>
                        <ul>
                            ${k.suggestions.map(s => `<li>${s}</li>`).join("")}
                        </ul>
                    </div>
                </div>
            `).join("");
        });
}

// ===================== 工具函数 =====================
function truncate(str, len) {
    if (!str) return "";
    return str.length > len ? str.substring(0, len) + "..." : str;
}

function escapeHtml(str) {
    if (!str) return "";
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
              .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

function showError(msg) {
    const div = document.createElement("div");
    div.style.cssText = `
        position:fixed; bottom:24px; right:24px; z-index:999;
        padding:14px 20px; background:#ff5252; color:#fff;
        border-radius:8px; font-size:14px; font-weight:500;
        box-shadow:0 4px 20px rgba(255,82,82,.4);
        animation: fadeIn .3s ease; max-width:400px;
    `;
    div.textContent = "❌ " + msg;
    document.body.appendChild(div);
    setTimeout(() => { div.style.opacity = "0"; div.style.transition = "opacity .3s";
        setTimeout(() => div.remove(), 300); }, 5000);
}
