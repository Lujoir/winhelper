/* ==========================================
   性能分析模块（perf-analyzer）前端逻辑
   ==========================================
   同步契约（ADR-001）：本文件为唯一事实来源，同步至 winhelper 主应用 web/
   传输层：pywebview 桥接（桌面模式与 E2E 桩走同一 window.pywebview.api.call 路径）
   注意：避免 ?. 可选链等 esprima 不支持语法（本机无 node，验证门禁 ADR-006）
   ========================================== */

// ===================== 传输层 =====================

function perfWaitBridge(timeout) {
    return new Promise(function (resolve) {
        if (window.pywebview && window.pywebview.api) { resolve(true); return; }
        var started = Date.now();
        var timer = setInterval(function () {
            if (window.pywebview && window.pywebview.api) {
                clearInterval(timer); resolve(true);
            } else if (Date.now() - started > timeout) {
                clearInterval(timer); resolve(false);
            }
        }, 100);
    });
}

async function perfApi(path) {
    var ok = await perfWaitBridge(1500);
    if (!ok) return { success: false, error: "pywebview 桥接不可用" };
    try {
        return await window.pywebview.api.call(path);
    } catch (e) {
        return { success: false, error: String((e && e.message) || e) };
    }
}

// ===================== 全局状态 =====================

var PERF_WINDOW = 60; // 60s 滚动窗口
var perfState = {
    pollTimer: null,       // 实时快照轮询（1s）
    recTimer: null,        // 记录状态轮询（1s）
    recordId: null,
    chartCpuMem: null,
    chartDisk: null,
    hist: { labels: [], cpu: [], mem: [], swap: [], busy: [], read: [], write: [] },
    lastReport: null,
};

// ===================== 初始化（主应用 switchTab 守卫调用） =====================

function initPerfTab() {
    var sec = document.getElementById("tab-perf");
    if (!sec) return;
    perfEnsureCharts();
    perfStartPolling();
    perfRestoreReport();
}

function perfStartPolling() {
    if (perfState.pollTimer) return;
    perfTick();
    perfState.pollTimer = setInterval(perfTick, 1000);
}

function perfStopPolling() {
    if (perfState.pollTimer) {
        clearInterval(perfState.pollTimer);
        perfState.pollTimer = null;
    }
}

async function perfTick() {
    // 离开菜单自动停轮询（ADR-007：回调自检 tab active）
    var sec = document.getElementById("tab-perf");
    if (!sec || !sec.classList.contains("active")) { perfStopPolling(); return; }
    var d = await perfApi("/api/perf/snapshot");
    if (!d || !d.success) return;
    perfRenderSnapshot(d);
}

// ===================== 图表 =====================

function perfChartOpts() {
    return {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: "index", intersect: false },
        scales: {
            x: {
                ticks: { color: "#8a93a5", maxTicksLimit: 8, font: { size: 10 }, maxRotation: 0 },
                grid: { color: "rgba(255,255,255,.05)" }
            },
            y: {
                beginAtZero: true,
                suggestedMax: 100,
                ticks: { color: "#8a93a5", font: { size: 10 } },
                grid: { color: "rgba(255,255,255,.05)" }
            }
        },
        plugins: {
            legend: { labels: { color: "#aab3c5", boxWidth: 12, font: { size: 11 } } },
            tooltip: { titleFont: { size: 11 }, bodyFont: { size: 11 } }
        }
    };
}

function perfEnsureCharts() {
    if (!window.Chart) return;
    var c1 = document.getElementById("perfChartCpuMem");
    if (!perfState.chartCpuMem && c1) {
        perfState.chartCpuMem = new Chart(c1, {
            type: "line",
            data: {
                labels: [],
                datasets: [
                    { label: "CPU %", data: [], borderColor: "#4fc3f7", backgroundColor: "rgba(79,195,247,.10)", fill: true, tension: .3, pointRadius: 0, borderWidth: 2 },
                    { label: "内存 %", data: [], borderColor: "#ffb74d", backgroundColor: "rgba(255,183,77,.08)", fill: true, tension: .3, pointRadius: 0, borderWidth: 2 },
                    { label: "虚拟内存 %", data: [], borderColor: "#ba68c8", backgroundColor: "transparent", borderDash: [4, 3], tension: .3, pointRadius: 0, borderWidth: 1.5 }
                ]
            },
            options: perfChartOpts()
        });
    }
    var c2 = document.getElementById("perfChartDisk");
    if (!perfState.chartDisk && c2) {
        perfState.chartDisk = new Chart(c2, {
            type: "line",
            data: {
                labels: [],
                datasets: [
                    { label: "磁盘活跃 %", data: [], borderColor: "#81c784", backgroundColor: "rgba(129,199,132,.10)", fill: true, tension: .3, pointRadius: 0, borderWidth: 2 },
                    { label: "读 MB/s", data: [], borderColor: "#4fc3f7", backgroundColor: "transparent", tension: .3, pointRadius: 0, borderWidth: 1.5 },
                    { label: "写 MB/s", data: [], borderColor: "#e57373", backgroundColor: "transparent", borderDash: [4, 3], tension: .3, pointRadius: 0, borderWidth: 1.5 }
                ]
            },
            options: perfChartOpts()
        });
    }
}

// ===================== 实时快照渲染 =====================

function perfSetText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
}

function perfRenderSnapshot(d) {
    var cpu = d.cpu || {};
    var mem = d.memory || {};
    var swap = d.swap || {};

    perfSetText("perfCpuVal", cpu.percent === null || cpu.percent === undefined ? "--" : cpu.percent.toFixed(1) + "%");
    perfSetText("perfFreqVal", cpu.freq_mhz ? (cpu.freq_mhz >= 1000 ? (cpu.freq_mhz / 1000).toFixed(2) + " GHz" : cpu.freq_mhz + " MHz") : "--");
    perfSetText("perfCoresVal", cpu.count ? cpu.count + " 核" : "--");
    perfSetText("perfMemVal", mem.percent === undefined ? "--" : mem.percent + "%");
    perfSetText("perfMemAvailVal", mem.available_text || "--");
    perfSetText("perfSwapVal", swap.percent === undefined ? "--" : swap.percent + "%");

    // 汇总各物理盘最大活跃度
    var disks = d.disks || [];
    var maxBusy = null, sumRead = 0, sumWrite = 0, sumIops = 0;
    disks.forEach(function (m) {
        if (m.busy_pct !== null && m.busy_pct !== undefined) {
            maxBusy = (maxBusy === null) ? m.busy_pct : Math.max(maxBusy, m.busy_pct);
        }
        sumRead += (m.read_mb_s || 0);
        sumWrite += (m.write_mb_s || 0);
        sumIops += (m.iops || 0);
    });
    perfSetText("perfDiskVal", maxBusy === null ? "--" : maxBusy.toFixed(1) + "%");
    perfSetText("perfIoVal", (sumRead + sumWrite).toFixed(1) + " MB/s");
    perfSetText("perfIopsVal", sumIops.toFixed(0) + " 次/s");

    // 磁盘明细表
    var tb = document.getElementById("perfDisksBody");
    if (tb) {
        if (!disks.length) {
            tb.innerHTML = '<tr><td colspan="5" class="empty-state">等待数据…</td></tr>';
        } else {
            tb.innerHTML = disks.map(function (m) {
                return '<tr><td>' + m.name + '</td><td>' + (m.read_mb_s === null || m.read_mb_s === undefined ? "--" : m.read_mb_s.toFixed(2)) +
                    '</td><td>' + (m.write_mb_s === null || m.write_mb_s === undefined ? "--" : m.write_mb_s.toFixed(2)) +
                    '</td><td>' + (m.iops === null || m.iops === undefined ? "--" : m.iops.toFixed(1)) +
                    '</td><td>' + (m.busy_pct === null || m.busy_pct === undefined ? "--" : m.busy_pct.toFixed(1) + "%") + '</td></tr>';
            }).join("");
        }
    }

    // 卷容量表
    var vols = d.volumes || [];
    var vb = document.getElementById("perfVolsBody");
    if (vb) {
        if (!vols.length) {
            vb.innerHTML = '<tr><td colspan="5" class="empty-state">等待数据…</td></tr>';
        } else {
            vb.innerHTML = vols.map(function (v) {
                return '<tr><td>' + v.mount + '</td><td>' + perfSize(v.total) + '</td><td>' + perfSize(v.used) +
                    '</td><td>' + perfSize(v.free) + '</td><td>' + (v.percent === null || v.percent === undefined ? "--" : v.percent + "%") + '</td></tr>';
            }).join("");
        }
    }

    // 曲线数据（60 点窗口）
    var h = perfState.hist;
    var ts = new Date();
    var label = ("0" + ts.getHours()).slice(-2) + ":" + ("0" + ts.getMinutes()).slice(-2) + ":" + ("0" + ts.getSeconds()).slice(-2);
    h.labels.push(label);
    h.cpu.push(cpu.percent === null || cpu.percent === undefined ? null : cpu.percent);
    h.mem.push(mem.percent === undefined ? null : mem.percent);
    h.swap.push(swap.percent === undefined ? null : swap.percent);
    h.busy.push(maxBusy === null ? null : maxBusy);
    h.read.push(Number(sumRead.toFixed(2)));
    h.write.push(Number(sumWrite.toFixed(2)));
    ["labels", "cpu", "mem", "swap", "busy", "read", "write"].forEach(function (k) {
        while (h[k].length > PERF_WINDOW) h[k].shift();
    });

    if (perfState.chartCpuMem) {
        var ds1 = perfState.chartCpuMem.data;
        ds1.labels = h.labels.slice();
        ds1.datasets[0].data = h.cpu.slice();
        ds1.datasets[1].data = h.mem.slice();
        ds1.datasets[2].data = h.swap.slice();
        perfState.chartCpuMem.update("none");
    }
    if (perfState.chartDisk) {
        var ds2 = perfState.chartDisk.data;
        ds2.labels = h.labels.slice();
        ds2.datasets[0].data = h.busy.slice();
        ds2.datasets[1].data = h.read.slice();
        ds2.datasets[2].data = h.write.slice();
        perfState.chartDisk.update("none");
    }
}

function perfSize(n) {
    if (n === null || n === undefined) return "--";
    var units = ["B", "KB", "MB", "GB", "TB"];
    var i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return (i === 0 ? n.toFixed(0) : n.toFixed(1)) + " " + units[i];
}

// ===================== 长时间记录 =====================

function perfSetRecordUI(running) {
    var b1 = document.getElementById("perfRecStartBtn");
    var b2 = document.getElementById("perfRecStopBtn");
    var sel = document.getElementById("perfInterval");
    if (b1) b1.disabled = running;
    if (b2) b2.disabled = !running;
    if (sel) sel.disabled = running;
    var badge = document.getElementById("perfRecBadge");
    if (badge) {
        badge.textContent = running ? "记录中" : (perfState.lastReport ? "已完成分析" : "未开始");
        badge.style.background = running ? "rgba(229,115,115,.15)" : "";
        badge.style.color = running ? "#ef9a9a" : "";
    }
}

async function perfStartRecord() {
    var sel = document.getElementById("perfInterval");
    var itv = sel ? sel.value : "2";
    var d = await perfApi("/api/perf/record-start?interval=" + encodeURIComponent(itv));
    if (!d || !d.success) {
        alert("开始记录失败: " + ((d && d.error) || "未知错误"));
        return;
    }
    perfState.recordId = d.record_id;
    if (d.reused) {
        var badge = document.getElementById("perfRecBadge");
        if (badge) badge.title = d.message || "已有记录进行中，已复用";
    }
    perfSetRecordUI(true);
    perfPollRecordStatus();
    if (perfState.recTimer) clearInterval(perfState.recTimer);
    perfState.recTimer = setInterval(perfPollRecordStatus, 1000);
}

async function perfPollRecordStatus() {
    if (!perfState.recordId) return;
    var d = await perfApi("/api/perf/record-status?record_id=" + encodeURIComponent(perfState.recordId));
    if (!d || !d.success || !d.record) return;
    var r = d.record;
    perfSetText("perfRecElapsed", (r.elapsed || 0) + "s");
    perfSetText("perfRecSamples", r.sample_count || 0);
    perfSetText("perfRecInterval", (r.interval || 2) + "s");
    perfSetText("perfRecFile", (r.file || "--").split("\\").pop());
    var ls = r.last_sample || {};
    perfSetText("perfRecCpu", ls.cpu_pct === null || ls.cpu_pct === undefined ? "--" : ls.cpu_pct + "%");
    perfSetText("perfRecMem", ls.mem_pct === undefined ? "--" : ls.mem_pct + "%");
    var maxBusy = null;
    var dk = ls.disks || {};
    Object.keys(dk).forEach(function (k) {
        var b = dk[k] && dk[k].busy_pct;
        if (b !== null && b !== undefined) maxBusy = (maxBusy === null) ? b : Math.max(maxBusy, b);
    });
    perfSetText("perfRecDisk", maxBusy === null ? "--" : maxBusy + "%");
    // 状态异常兜底（线程报错自动复位按钮）
    if (r.status === "error") {
        perfState.recordId = null;
        if (perfState.recTimer) { clearInterval(perfState.recTimer); perfState.recTimer = null; }
        perfSetRecordUI(false);
        alert("记录线程异常: " + (r.error || "未知"));
    }
}

async function perfStopRecord() {
    if (!perfState.recordId) return;
    var rid = perfState.recordId;
    var d = await perfApi("/api/perf/record-stop?record_id=" + encodeURIComponent(rid));
    if (perfState.recTimer) { clearInterval(perfState.recTimer); perfState.recTimer = null; }
    perfState.recordId = null;
    perfSetRecordUI(false);
    if (!d || !d.success) {
        alert("停止/分析失败: " + ((d && d.error) || "未知错误"));
        return;
    }
    renderPerfReport(d.report);
}

// ===================== 报告渲染 =====================

function esc(s) {
    return String(s === null || s === undefined ? "" : s)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function renderPerfReport(report) {
    if (!report) return;
    perfState.lastReport = report;
    var box = document.getElementById("perfReportBox");
    if (!box) return;

    var a = report.assessment || {};
    var levelColor = { ok: "#81c784", watch: "#ffd54f", upgrade: "#e57373" }[a.level] || "#aab3c5";

    var html = [];
    html.push('<div class="perf-report">');
    html.push('<div class="perf-report-head">');
    html.push('<div class="perf-report-title">分析报告 <span class="perf-report-level" style="color:' + levelColor + ';border-color:' + levelColor + '">' + esc(a.level_text || "--") + '</span></div>');
    html.push('<div class="perf-report-meta">记录时长 ' + esc(report.elapsed) + 's · 采样间隔 ' + esc(report.interval) + 's · 有效行 ' + esc((report.sample_lines || 0) - (report.bad_lines || 0)) + ' · 坏行跳过 ' + esc(report.bad_lines || 0) + '</div>');
    html.push('</div>');

    // 结论条目
    (a.items || []).forEach(function (it) {
        var c = { ok: "#81c784", watch: "#ffd54f", upgrade: "#e57373" }[it.severity] || "#aab3c5";
        html.push('<div class="perf-report-item">');
        html.push('<span class="perf-sev" style="color:' + c + ';border-color:' + c + '">[' + esc(it.severity) + ']</span> <b>' + esc(it.component) + '</b>：' + esc(it.reason));
        html.push('<div class="perf-report-suggestion">→ ' + esc(it.suggestion) + '</div>');
        html.push('</div>');
    });

    // 指标统计表
    var st = report.stats || {};
    html.push('<div class="table-wrapper"><table class="event-table"><thead><tr><th>指标</th><th>avg</th><th>p95</th><th>max</th><th>min</th></tr></thead><tbody>');
    var rows = [
        ["CPU 占用 (%)", st.cpu_pct],
        ["内存使用率 (%)", st.mem_used_pct],
        ["可用内存 (GB)", st.mem_available_gb],
        ["虚拟内存使用 (%)", st.swap_pct]
    ];
    Object.keys(report.disk_stats || {}).forEach(function (name) {
        var ds = report.disk_stats[name];
        rows.push(["磁盘 " + name + " 活跃 (%)", ds.busy_pct]);
        rows.push(["磁盘 " + name + " 读 (MB/s)", ds.read_mb_s]);
        rows.push(["磁盘 " + name + " 写 (MB/s)", ds.write_mb_s]);
        rows.push(["磁盘 " + name + " IOPS", ds.iops]);
    });
    rows.forEach(function (row) {
        var s = row[1];
        if (!s) {
            html.push('<tr><td>' + esc(row[0]) + '</td><td colspan="4" class="empty-state">--</td></tr>');
        } else {
            html.push('<tr><td>' + esc(row[0]) + '</td><td>' + s.avg + '</td><td>' + s.p95 + '</td><td>' + s.max + '</td><td>' + s.min + '</td></tr>');
        }
    });
    html.push('</tbody></table></div>');

    // 饱和与瓶颈
    html.push('<div class="perf-sat-row">');
    (report.components || []).forEach(function (c, i) {
        html.push('<div class="perf-sat-item"><div class="perf-sat-rank">#' + (i + 1) + '</div><div><b>' + esc(c.name) + '</b>' +
            (i === 0 && a.level !== "ok" ? ' <span style="color:#e57373">← 主要瓶颈</span>' : '') +
            '<div class="perf-sat-detail">饱和 ' + esc(c.saturation_seconds) + 's（占比 ' + Math.round((c.saturation_ratio || 0) * 100) + '%）· p95 ' + esc(c.p95 === null || c.p95 === undefined ? "--" : c.p95) + ' ' + esc(c.unit || "") + '</div></div></div>');
    });
    html.push('</div>');

    // 操作
    html.push('<div class="disk-toolbar" style="margin-top:10px">');
    html.push('<button class="btn btn-primary" onclick="perfExportReport()">导出 Markdown 报告</button>');
    html.push('<span class="progress-text" id="perfExportPath"></span>');
    html.push('<button class="btn btn-ghost" id="perfOpenLocBtn" style="display:none" onclick="perfOpenReportLocation()">打开位置</button>');
    html.push('</div>');
    html.push('</div>');

    box.innerHTML = html.join("");
    box.style.display = "";
}

async function perfExportReport() {
    var d = await perfApi("/api/perf/record-export");
    if (!d || !d.success) {
        alert("导出失败: " + ((d && d.error) || "未知错误"));
        return;
    }
    perfSetText("perfExportPath", d.path || "");
    var btn = document.getElementById("perfOpenLocBtn");
    if (btn) btn.style.display = "";
}

async function perfOpenReportLocation() {
    var pathEl = document.getElementById("perfExportPath");
    var p = pathEl ? pathEl.textContent : "";
    if (!p) return;
    await perfApi("/api/disk/open-location?path=" + encodeURIComponent(p));
}

async function perfRestoreReport() {
    // 页面刷新/重新进入后恢复最近一份报告
    if (perfState.lastReport) { renderPerfReport(perfState.lastReport); return; }
    var d = await perfApi("/api/perf/record-report");
    if (d && d.success && d.found && d.report) {
        renderPerfReport(d.report);
        perfSetRecordUI(false);
    }
}
