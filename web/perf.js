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
    stressId: null,        // 压测任务
    stressTimer: null,     // 压测轮询（0.5s）
    webglActive: false,    // GPU WebGL 负载运行中
    stressDoneVisible: false,
    tempsIntervalMs: 300000,  // 温度采样间隔（毫秒，默认 300s，设置可改）
    hwLoaded: false,       // 硬件规格已加载
    tempsTimer: null,      // 温度轮询（间隔可配置，默认 300s）
    uplinkTimer: null,     // 平台接入状态轮询（10s）
};

// ===================== 初始化（主应用 switchTab 守卫调用） =====================

function initPerfTab() {
    var sec = document.getElementById("tab-perf");
    if (!sec) return;
    perfEnsureCharts();
    perfStartPolling();
    perfStartTempsPolling();
    perfRestoreReport();
    perfLoadHwInfo();
    perfLoadAppConfig();
    perfLoadUplink();
    perfStartUplinkPolling();
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

// ===================== 温度（能力分级，2s 轮询，ADR-011） =====================

function perfStartTempsPolling() {
    if (perfState.tempsTimer) return;
    perfTempsTick();
    perfState.tempsTimer = setInterval(perfTempsTick, perfState.tempsIntervalMs || 300000);
}

function perfStopTempsPolling() {
    if (perfState.tempsTimer) {
        clearInterval(perfState.tempsTimer);
        perfState.tempsTimer = null;
    }
}

/* 读取应用配置（温度采样间隔），并回显设置弹窗输入框 */
function perfLoadAppConfig() {
    perfApi("/api/perf/app-config").then(function (d) {
        if (d && d.success && d.config) {
            perfState.tempsIntervalMs = (d.config.temperature_interval_sec || 300) * 1000;
            var inp = document.getElementById("tempsIntervalInput");
            if (inp) inp.value = d.config.temperature_interval_sec;
            if (perfState.tempsTimer) { perfStopTempsPolling(); perfStartTempsPolling(); }
        }
    }).catch(function () {});
}

/* 保存温度采样间隔（30~3600 秒），成功后重启轮询 */
async function saveTempsInterval() {
    var inp = document.getElementById("tempsIntervalInput");
    var v = parseInt(inp && inp.value, 10);
    if (isNaN(v) || v < 30 || v > 3600) {
        alert("采样间隔需在 30~3600 秒之间");
        return;
    }
    var d = await perfApi("/api/perf/app-config?temperature_interval_sec=" + v);
    if (d && d.success) {
        perfState.tempsIntervalMs = (d.config.temperature_interval_sec) * 1000;
        if (perfState.tempsTimer) { perfStopTempsPolling(); perfStartTempsPolling(); }
        alert("已保存：温度每 " + d.config.temperature_interval_sec + " 秒采样一次");
    } else {
        alert("保存失败：" + ((d && d.error) || "未知错误"));
    }
}

async function perfTempsTick() {
    var sec = document.getElementById("tab-perf");
    if (!sec || !sec.classList.contains("active")) { perfStopTempsPolling(); return; }
    var d = await perfApi("/api/perf/temps");
    if (!d || !d.success) return;
    renderPerfTemps(d);
}

function renderPerfTemps(d) {
    var gpu = d.gpu || {}, cpu = d.cpu || {};
    var gv = document.getElementById("perfGpuTempVal");
    if (gv) gv.textContent = (gpu.available && gpu.temp_c !== null && gpu.temp_c !== undefined)
        ? Math.round(gpu.temp_c) + "°C" : "--";
    var cv = document.getElementById("perfCpuTempVal");
    var extra = document.getElementById("perfCpuTempExtra");
    if (!cv) return;
    if (cpu.available) {
        cv.textContent = (cpu.temp_c !== null && cpu.temp_c !== undefined) ? Math.round(cpu.temp_c) + "°C" : "--";
        cv.style.color = "";
        if (extra) {
            extra.innerHTML = d.admin ? '<span class="hw-badge">管理员模式</span>' : "";
            extra.style.display = d.admin ? "" : "none";
        }
    } else if (cpu.reason === "need_admin") {
        cv.textContent = "需管理员模式";
        cv.style.color = "var(--warn, #ffb74d)";
        if (extra) {
            extra.innerHTML = '<button class="btn btn-ghost" style="font-size:11px;padding:2px 8px;margin-top:2px" onclick="restartPerfAdmin()">以管理员重启</button>';
            extra.style.display = "";
        }
    } else if (cpu.reason === "lhm_unavailable") {
        cv.textContent = "温度组件不可用";
        cv.style.color = "";
        if (extra) { extra.innerHTML = ""; extra.style.display = "none"; }
    } else {
        cv.textContent = "--";
        cv.style.color = "";
        if (extra) { extra.innerHTML = ""; extra.style.display = "none"; }
    }
}

async function restartPerfAdmin() {
    if (!confirm("将以管理员身份重启应用以启用 CPU 温度检测。\n\n重启后界面显示「管理员模式」徽章，CPU 温度实时显示。\n\n注意：若杀毒软件拦截系统级驱动（WinRing0），请选择允许——该驱动仅用于读取传感器数据。确定继续？")) return;
    var d = await perfApi("/api/perf/restart-admin");
    if (d && d.success && d.restarting) {
        var cv = document.getElementById("perfCpuTempVal");
        if (cv) cv.textContent = "重启中…";
        return;
    }
    if (d && d.error === "uac_cancelled") {
        alert("未提权：您在 UAC 弹窗中取消了操作，应用仍在普通模式运行。");
        renderPerfTemps({ admin: false, cpu: { available: false, reason: "need_admin" }, gpu: {} });
        return;
    }
    alert("重启失败: " + ((d && d.error) || "未知错误"));
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

    // 卷容量可视化（横向条形图，阈值着色：<75青 / 75-90黄 / >90红）
    var vc = document.getElementById("perfVolsCanvas");
    if (vc && vols.length) {
        var vLabels = vols.map(function (v) { return v.mount; });
        var vPcts = vols.map(function (v) { return v.percent === null || v.percent === undefined ? 0 : v.percent; });
        var vColors = vPcts.map(function (p) { return p > 90 ? "#ef5350" : (p > 75 ? "#ffb74d" : "#4dd0e1"); });
        if (perfState.chartVols) {
            perfState.chartVols.data.labels = vLabels;
            perfState.chartVols.data.datasets[0].data = vPcts;
            perfState.chartVols.data.datasets[0].backgroundColor = vColors;
            perfState.chartVols.update("none");
        } else if (window.Chart) {
            perfState.chartVols = new Chart(vc.getContext("2d"), {
                type: "bar",
                data: { labels: vLabels, datasets: [{ data: vPcts, backgroundColor: vColors, borderRadius: 4, barThickness: 14 }] },
                options: {
                    indexAxis: "y", responsive: true, maintainAspectRatio: false, animation: false,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { min: 0, max: 100, ticks: { callback: function (v) { return v + "%"; }, color: "#8a93a5", font: { size: 10 } }, grid: { color: "rgba(255,255,255,.06)" } },
                        y: { ticks: { color: "#aab3c5", font: { size: 11 } }, grid: { display: false } }
                    }
                }
            });
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

    // 综合运行状态曲线（记录时序，服务端降采样 series）
    var sr = report.series;
    if (sr && sr.t && sr.t.length) {
        html.push('<div class="perf-report-chart"><div style="font-size:13px;font-weight:600;margin:14px 0 6px">运行状态综合曲线（CPU / 内存 / 磁盘活跃峰值）</div>' +
            '<div style="height:170px"><canvas id="perfReportChart"></canvas></div>' +
            '<div class="perf-report-suggestion">纵轴 0-100%，横轴为记录时间轴（秒）</div></div>');
    }

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
    html.push('<button class="btn btn-primary" onclick="perfExportReport()">导出 HTML 报告</button>');
    html.push('<span class="progress-text" id="perfExportPath"></span>');
    html.push('<button class="btn btn-ghost" id="perfOpenLocBtn" style="display:none" onclick="perfOpenReportLocation()">打开位置</button>');
    html.push('</div>');
    html.push('</div>');

    box.innerHTML = html.join("");
    box.style.display = "";

    // 综合曲线初始化（必须在 innerHTML 之后；旧报告无 series 自动跳过）
    var sr2 = report.series;
    if (sr2 && sr2.t && sr2.t.length && window.Chart) {
        var rc = document.getElementById("perfReportChart");
        if (rc) {
            if (perfState.chartReport) { perfState.chartReport.destroy(); perfState.chartReport = null; }
            var mkLine = function (label, arr, color) {
                return { label: label, data: arr, borderColor: color, backgroundColor: "transparent",
                         borderWidth: 1.5, pointRadius: 0, tension: 0.25, spanGaps: true };
            };
            perfState.chartReport = new Chart(rc.getContext("2d"), {
                type: "line",
                data: { labels: sr2.t.map(function (s) { return s + "s"; }),
                        datasets: [mkLine("CPU %", sr2.cpu, "#4fc3f7"),
                                   mkLine("内存 %", sr2.mem, "#ffd54f"),
                                   mkLine("磁盘活跃 %", sr2.disk_max, "#81c784")] },
                options: { responsive: true, maintainAspectRatio: false, animation: false,
                           plugins: { legend: { labels: { color: "#aab3c5", font: { size: 11 }, boxWidth: 12 } } },
                           scales: {
                               x: { ticks: { color: "#8a93a5", maxTicksLimit: 8, font: { size: 10 } }, grid: { color: "rgba(255,255,255,.05)" } },
                               y: { min: 0, max: 100, ticks: { callback: function (v) { return v + "%"; }, color: "#8a93a5", font: { size: 10 } }, grid: { color: "rgba(255,255,255,.06)" } }
                           } }
            });
        }
    }
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

// ============================================================
// 性能检测（压测）——每阶段 ≤60s 硬上限，编排 58s（ADR-008/009）
// ============================================================

var PERF_STRESS_SECONDS = { full: 58, disk: 25, cpu: 15, mem: 10, gpu: 8 };
var PERF_STRESS_LABEL = { full: "全面检测", disk: "磁盘读写上限", cpu: "CPU 稳定性", mem: "内存稳定性", gpu: "GPU 稳定性" };
var PERF_STAGE_LABEL = { disk: "磁盘读写上限", cpu: "CPU 稳定性", mem: "内存稳定性", gpu: "GPU 稳定性" };
var PERF_LEVEL_COLOR = { ok: "#81c784", stable: "#81c784", edge: "#ffd54f", bad: "#e57373", unstable: "#e57373", skip: "#8a93a5" };

function perfSetStressUI(running) {
    var btns = document.querySelectorAll("[data-stress-mode]");
    for (var i = 0; i < btns.length; i++) btns[i].disabled = running;
    var cancelBtn = document.getElementById("perfStressCancelBtn");
    if (cancelBtn) cancelBtn.style.display = running ? "" : "none";
    var prog = document.getElementById("perfStressProgress");
    if (prog) prog.style.display = running ? "" : (perfState.stressDoneVisible ? "" : "none");
    var badge = document.getElementById("perfStressBadge");
    if (badge) {
        badge.textContent = running ? "压测中" : "未运行";
        badge.style.color = running ? "#ef9a9a" : "";
        badge.style.background = running ? "rgba(229,115,115,.15)" : "";
    }
}

async function startPerfStress(mode) {
    if (perfState.stressId) { alert("已有压测进行中，请等待完成或取消"); return; }
    var secs = PERF_STRESS_SECONDS[mode] || 58;
    if (!confirm("压测约 " + secs + " 秒，期间系统可能短暂卡顿，请提前保存工作。\n\n确定开始「" + (PERF_STRESS_LABEL[mode] || mode) + "」？")) return;
    var d = await perfApi("/api/perf/stress-start?mode=" + encodeURIComponent(mode));
    if (!d || !d.success) {
        alert("启动失败: " + ((d && d.error) || "未知错误"));
        return;
    }
    perfState.stressId = d.stress_id;
    perfState.webglActive = false;
    var resBox = document.getElementById("perfStressResultBox");
    if (resBox) resBox.style.display = "none";
    perfSetStressUI(true);
    perfPollStress();
    if (perfState.stressTimer) clearInterval(perfState.stressTimer);
    perfState.stressTimer = setInterval(perfPollStress, 500);
}

async function cancelPerfStress() {
    if (!perfState.stressId) return;
    var d = await perfApi("/api/perf/stress-cancel?stress_id=" + encodeURIComponent(perfState.stressId));
    if (!d || !d.success) {
        alert("取消失败: " + ((d && d.error) || "未知错误"));
    }
}

function perfStageLiveText(live) {
    if (!live) return "";
    if (live.mb_s !== null && live.mb_s !== undefined) return live.phase + " " + live.mb_s + " MB/s";
    if (live.cpu_pct !== null && live.cpu_pct !== undefined) return "CPU " + live.cpu_pct + "%（" + live.workers + " 线程满载）";
    if (live.mem_pct !== null && live.mem_pct !== undefined) return "内存 " + live.mem_pct + "% · 已分配 " + live.blocks_gb + " GB";
    if (live.gpu_util !== null && live.gpu_util !== undefined) return "GPU " + live.gpu_util + "%" + (live.temp !== null && live.temp !== undefined ? " · " + live.temp + "℃" : "");
    return live.note || "";
}

async function perfPollStress() {
    if (!perfState.stressId) return;
    var t0 = performance.now();
    var d = await perfApi("/api/perf/stress-status?stress_id=" + encodeURIComponent(perfState.stressId));
    var latency = Math.round(performance.now() - t0);
    if (!d || !d.success) {
        perfStopWebGL();
        perfState.stressId = null;
        if (perfState.stressTimer) { clearInterval(perfState.stressTimer); perfState.stressTimer = null; }
        perfSetStressUI(false);
        return;
    }
    var t = d.task;
    // GPU WebGL 负载联动（ADR-009：need_webgl 标志驱动前端着色器满载）
    if (t.need_webgl && !perfState.webglActive) perfStartWebGL();
    if (!t.need_webgl && perfState.webglActive) perfStopWebGL();

    // 进度区刷新
    var stageText = document.getElementById("perfStressStageText");
    var remainText = document.getElementById("perfStressRemaining");
    var liveText = document.getElementById("perfStressLive");
    var latText = document.getElementById("perfStressLatency");
    var bar = document.getElementById("perfStressBar");
    if (stageText) {
        stageText.textContent = t.current_stage
            ? ("当前阶段：" + (PERF_STAGE_LABEL[t.current_stage] || t.current_stage))
            : "准备中…";
    }
    if (remainText) remainText.textContent = t.remaining !== null && t.remaining !== undefined ? ("剩余 " + t.remaining + "s") : "";
    if (liveText) liveText.textContent = t.stage_detail ? perfStageLiveText(t.stage_detail.live) : "";
    if (latText) latText.textContent = "桥接响应 " + latency + "ms";
    if (bar && t.total_seconds) bar.style.width = Math.min(100, Math.round(t.elapsed / t.total_seconds * 100)) + "%";

    if (t.status !== "running") {
        perfState.stressId = null;
        if (perfState.stressTimer) { clearInterval(perfState.stressTimer); perfState.stressTimer = null; }
        perfStopWebGL();
        perfSetStressUI(false);
        perfState.stressDoneVisible = true;
        var prog = document.getElementById("perfStressProgress");
        if (prog) prog.style.display = "none";
        if (t.status === "error") { alert("压测异常: " + (t.error || "未知")); return; }
        if (t.result) renderStressResult(t.result, t.status);
    }
}

function _stressStageCard(title, rows, conclusion) {
    var c = conclusion || {};
    var color = PERF_LEVEL_COLOR[c.level] || "#aab3c5";
    var html = [];
    html.push('<div class="hw-group"><div class="hw-title">' + esc(title) +
        (c.level ? ' <span class="perf-report-level" style="font-size:11px;color:' + color + ';border-color:' + color + '">' + esc(c.level) + '</span>' : '') +
        '</div><div class="hw-body">');
    rows.forEach(function (r) { html.push('<div class="hw-meta">' + r + '</div>'); });
    if (c.text) html.push('<div class="hw-meta"><b>结论：</b>' + esc(c.text) + '</div>');
    if (c.suggestion) html.push('<div class="hw-meta"><b>建议：</b>' + esc(c.suggestion) + '</div>');
    html.push('</div></div>');
    return html;
}

function renderStressResult(result, taskStatus) {
    var box = document.getElementById("perfStressResultBox");
    if (!box || !result) return;
    var ov = result.overall || {};
    var color = PERF_LEVEL_COLOR[ov.level] || "#aab3c5";
    var html = ['<div class="perf-report">'];
    html.push('<div class="perf-report-head"><div class="perf-report-title">检测结果 ' +
        '<span class="perf-report-level" style="color:' + color + ';border-color:' + color + '">' + esc(ov.text || "--") + '</span></div>' +
        '<div class="perf-report-meta">' + (taskStatus === "cancelled" ? "已取消（已完成阶段结果保留）" : "检测完成") + '</div></div>');
    var st = result.stages || {};
    var s;

    s = st.disk;
    if (s) {
        var rows = [];
        if (s.status === "skipped") {
            rows.push(esc(s.reason || "跳过"));
        } else {
            if (s.write) rows.push("顺序写：<b>" + s.write.avg_mb_s + " MB/s</b>（峰值 " + s.write.peak_mb_s + "）");
            if (s.read) rows.push("顺序+随机读：<b>" + s.read.avg_mb_s + " MB/s</b>（峰值 " + s.read.peak_mb_s + "）" + (s.read.note ? " · " + esc(s.read.note) : ""));
            rows.push("目标盘：" + esc(s.drive || "--") + " · 临时文件已清理：" + (s.tmp_cleaned ? "是" : "否"));
        }
        html = html.concat(_stressStageCard("磁盘读写上限", rows, s.conclusion));
    }

    s = st.cpu;
    if (s) {
        var rows2 = [];
        if (s.samples) rows2.push("满载占用：avg <b>" + s.samples.avg + "%</b> / max " + s.samples.max + "%（" + s.workers + " 线程，达标 " + (s.achieved ? "是" : "否") + "）");
        rows2.push("响应间隙 " + (s.status_gaps || 0) + " 次 · 负载线程异常 " + ((s.worker_errors || []).length) + " 个" +
            ((s.post_events || []).length ? " · 压测后硬件事件 " + s.post_events.length + " 条" : " · 无 WHEA/Kernel-Power 新增事件"));
        html = html.concat(_stressStageCard("CPU 稳定性", rows2, s.conclusion));
    }

    s = st.mem;
    if (s) {
        var rows3 = [];
        rows3.push("峰值占用 <b>" + s.peak_percent + "%</b>（目标 ≥" + s.target_percent + "%，达标 " + (s.achieved ? "是" : "否") + "）");
        rows3.push("释放后占用 " + s.percent_after_release + "% · 安全红线可用下限 " + s.floor_gb + " GB" +
            (s.hit_floor ? "（已触达即停）" : "") + (s.error ? " · " + esc(s.error) : ""));
        html = html.concat(_stressStageCard("内存稳定性", rows3, s.conclusion));
    }

    s = st.gpu;
    if (s) {
        var rows4 = [];
        if (s.status === "skipped") {
            rows4.push(esc(s.reason || "跳过"));
        } else {
            (s.cards || []).forEach(function (c) {
                rows4.push(esc(c.name) + "：" + (c.dedicated ? "独立显卡" : "核显"));
            });
            if (s.util_max !== null && s.util_max !== undefined) {
                rows4.push("满载占用峰值 <b>" + s.util_max + "%</b>" + (s.temp_max !== null && s.temp_max !== undefined ? " · 最高温度 " + s.temp_max + "℃" : "") + "（来源 " + esc(s.src || "--") + "）");
            } else {
                rows4.push("指标不可用" + (s.metrics_count ? "" : "（负载已运行）"));
            }
            rows4.push((s.post_events || []).length ? "压测后驱动事件 " + s.post_events.length + " 条" : "无驱动重置（TDR）事件");
        }
        html = html.concat(_stressStageCard("GPU 稳定性", rows4, s.conclusion));
    }

    html.push('<div class="disk-toolbar" style="margin-top:10px">');
    html.push('<button class="btn btn-primary" onclick="perfStressExport()">导出 HTML 报告</button>');
    html.push('<span class="progress-text" id="stressExportPath"></span>');
    html.push('<button class="btn btn-ghost" id="stressOpenLocBtn" style="display:none" onclick="perfOpenStressLocation()">打开位置</button>');
    html.push('</div></div>');
    box.innerHTML = html.join("");
    box.style.display = "";
}

async function perfStressExport() {
    var d = await perfApi("/api/perf/stress-export");
    if (!d || !d.success) {
        alert("导出失败: " + ((d && d.error) || "未知错误"));
        return;
    }
    perfSetText("stressExportPath", d.path || "");
    var btn = document.getElementById("stressOpenLocBtn");
    if (btn) btn.style.display = "";
}

async function perfOpenStressLocation() {
    var el = document.getElementById("stressExportPath");
    var p = el ? el.textContent : "";
    if (!p) return;
    await perfApi("/api/disk/open-location?path=" + encodeURIComponent(p));
}

// ---------- GPU WebGL 满载负载（ADR-009：前端片元着色器，阶段结束自动停） ----------

var perfWebGL = { gl: null, uT: null, rafId: 0 };

function perfEnsureWebGL() {
    if (perfWebGL.gl) return true;
    var canvas = document.getElementById("perfGpuStressCanvas");
    if (!canvas) return false;
    var gl = canvas.getContext("webgl") || canvas.getContext("experimental-webgl");
    if (!gl) return false;
    var vs = "attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}";
    var fs = "precision highp float;uniform float uT;void main(){float x=gl_FragCoord.x*0.001,y=gl_FragCoord.y*0.001;float a=0.;for(int i=0;i<300;i++){a+=sin(x*float(i)+uT)*cos(y*float(i)-uT);x=x*1.0001+y*0.0001;}gl_FragColor=vec4(fract(a),fract(a*0.7),fract(a*0.3),1.);}";
    function sh(type, src) {
        var s = gl.createShader(type);
        gl.shaderSource(s, src);
        gl.compileShader(s);
        return s;
    }
    var prog = gl.createProgram();
    gl.attachShader(prog, sh(gl.VERTEX_SHADER, vs));
    gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, fs));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return false;
    gl.useProgram(prog);
    var buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    var loc = gl.getAttribLocation(prog, "p");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    perfWebGL.gl = gl;
    perfWebGL.uT = gl.getUniformLocation(prog, "uT");
    return true;
}

function perfStartWebGL() {
    if (!perfEnsureWebGL()) { perfState.webglActive = false; return; }
    perfState.webglActive = true;
    var gl = perfWebGL.gl;
    var t0 = performance.now();
    var wrap = document.getElementById("perfGpuStressWrap");
    if (wrap) wrap.style.display = "";
    function frame() {
        if (!perfState.webglActive) return;
        gl.uniform1f(perfWebGL.uT, (performance.now() - t0) * 0.001);
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
        perfWebGL.rafId = requestAnimationFrame(frame);
    }
    frame();
}

function perfStopWebGL() {
    perfState.webglActive = false;
    if (perfWebGL.rafId) { cancelAnimationFrame(perfWebGL.rafId); perfWebGL.rafId = 0; }
    var wrap = document.getElementById("perfGpuStressWrap");
    if (wrap) wrap.style.display = "none";
}

// ============================================================
// 硬件规格信息（进入菜单一次性加载）
// ============================================================

async function perfLoadHwInfo() {
    if (perfState.hwLoaded) return;
    var d = await perfApi("/api/perf/hwinfo");
    if (!d || !d.success || !d.hwinfo) return;  // 字段缺失显示 "--"，不报错
    perfState.hwLoaded = true;
    renderHwInfo(d.hwinfo);
}

function _hwFreq(mhz) {
    if (mhz === null || mhz === undefined) return "--";
    return mhz >= 1000 ? (mhz / 1000).toFixed(1) + " GHz" : mhz + " MHz";
}

function _hwGB(n) {
    if (n === null || n === undefined) return "--";
    return (n / 1073741824).toFixed(0) + " GB";
}

function renderHwInfo(h) {
    var box = document.getElementById("perfHwInfoBox");
    if (!box || !h) return;
    var cpu = h.cpu || {}, mem = h.memory || {}, disks = h.disks || [], gpus = h.gpu || [];
    var html = [];

    // CPU
    html.push('<div class="hw-group"><div class="hw-title">处理器</div><div class="hw-body">');
    html.push('<div class="hw-name">' + esc(cpu.name || "--") + '</div>');
    html.push('<div class="hw-meta">物理核 ' + (cpu.cores === null || cpu.cores === undefined ? "--" : cpu.cores) +
        ' · 逻辑核 ' + (cpu.logical === null || cpu.logical === undefined ? "--" : cpu.logical) +
        ' · 最高 ' + _hwFreq(cpu.max_mhz) + '</div>');
    html.push('</div></div>');

    // 内存
    html.push('<div class="hw-group"><div class="hw-title">内存（总 ' + _hwGB(mem.total) + '）</div><div class="hw-body">');
    var mods = mem.modules || [];
    if (mods.length) {
        mods.forEach(function (m) {
            html.push('<div class="hw-meta">' + esc(m.slot || "--") + ' · ' + _hwGB(m.size) +
                ' · ' + esc(m.type || "--") + ' · ' + (m.speed_mhz ? m.speed_mhz + " MHz" : "--") + '</div>');
        });
    } else {
        html.push('<div class="hw-meta">单条明细不可用（--）</div>');
    }
    html.push('</div></div>');

    // 磁盘
    html.push('<div class="hw-group"><div class="hw-title">物理磁盘</div><div class="hw-body">');
    if (disks.length) {
        disks.forEach(function (dk) {
            var badges = '';
            if (dk.system) badges += ' <span class="hw-badge hw-badge-sys">系统盘</span>';
            if (dk.media && dk.media !== "--") badges += ' <span class="hw-badge">' + esc(dk.media) + '</span>';
            html.push('<div class="hw-name">' + esc(dk.model || "--") + badges + '</div>');
            html.push('<div class="hw-meta">' + esc(dk.name || "--") + ' · ' + _hwGB(dk.size) +
                ' · 总线 ' + esc(dk.bus || "--") +
                (dk.volumes && dk.volumes.length ? ' · 卷 ' + esc(dk.volumes.join(" ")) : '') + '</div>');
        });
    } else {
        html.push('<div class="hw-meta">磁盘信息不可用（--）</div>');
    }
    html.push('</div></div>');

    // GPU
    html.push('<div class="hw-group"><div class="hw-title">显卡</div><div class="hw-body">');
    if (gpus.length) {
        gpus.forEach(function (g) {
            var spec = [];
            if (g.vram_text && g.vram_text !== "--") spec.push("显存 " + g.vram_text);
            if (g.driver && g.driver !== "--") spec.push("驱动 " + g.driver);
            if (g.resolution && g.resolution !== "--") spec.push(g.resolution);
            html.push('<div class="hw-meta">' + esc(g.name || "--") +
                ' <span class="hw-badge">' + (g.dedicated ? "独立显卡" : "核显") + '</span>' +
                (spec.length ? '<div class="hw-meta">' + esc(spec.join(" · ")) + '</div>' : '') + '</div>');
        });
    } else {
        html.push('<div class="hw-meta">显卡信息不可用（--）</div>');
    }
    html.push('</div></div>');

    box.innerHTML = html.join("");
    box.style.display = "";
    var ph = document.getElementById("perfHwInfoPlaceholder");
    if (ph) ph.style.display = "none";
}

// ============================================================
// 平台接入（EyeTerm 服务端联动，v4 uplink）
// ============================================================

var UPLINK_STATE_TEXT = {
    disabled: "未启用", connecting: "连接中", connected: "已连接", error: "连接异常"
};

function perfStartUplinkPolling() {
    if (perfState.uplinkTimer) return;
    perfState.uplinkTimer = setInterval(perfTickUplink, 10000);
}

function perfStopUplinkPolling() {
    if (perfState.uplinkTimer) {
        clearInterval(perfState.uplinkTimer);
        perfState.uplinkTimer = null;
    }
}

async function perfTickUplink() {
    // 平台接入已迁至全局设置弹窗：状态轮询不再依赖性能分析页签激活
    var d = await perfApi("/api/perf/uplink/status");
    if (d && d.success) renderPerfUplink(d.uplink || {});
}

function openAppSettings() {
    var ov = document.getElementById("appSettingsOverlay");
    if (!ov) return;
    ov.style.display = "flex";
    perfLoadUplink();
    perfLoadAppConfig();
    perfStartUplinkPolling();
}

function closeAppSettings() {
    var ov = document.getElementById("appSettingsOverlay");
    if (ov) ov.style.display = "none";
}

async function perfLoadUplink() {
    var d = await perfApi("/api/perf/uplink/status");
    if (d && d.success) renderPerfUplink(d.uplink || {});
}

function renderPerfUplink(u) {
    var stateText = UPLINK_STATE_TEXT[u.state] || "未连接";
    var badge = document.getElementById("perfUplinkBadge");
    if (badge) {
        var on = u.state === "connected";
        var bad = u.state === "error";
        badge.textContent = u.enabled ? stateText : "未启用";
        badge.style.color = on ? "#81c784" : (bad ? "#e57373" : "");
        badge.style.background = on ? "rgba(129,199,132,.12)" : (bad ? "rgba(229,115,115,.12)" : "");
    }
    var sv = document.getElementById("perfUplinkServer");
    if (sv && u.server_url && document.activeElement !== sv) sv.value = u.server_url;
    var st = document.getElementById("perfUplinkStatus");
    if (st) {
        var hb = "--";
        if (u.last_hb_ts) {
            var dt = new Date(u.last_hb_ts * 1000);
            hb = ("0" + dt.getHours()).slice(-2) + ":" + ("0" + dt.getMinutes()).slice(-2) + ":" + ("0" + dt.getSeconds()).slice(-2);
        }
        var rows = [];
        rows.push("状态 <b>" + esc(stateText) + "</b>");
        rows.push("终端ID <b>" + esc(u.terminal_id || "--") + "</b>");
        rows.push("注册 <b>" + (u.registered ? "已注册" : "未注册") + "</b>");
        rows.push("最近心跳 <b>" + hb + "</b>");
        rows.push("心跳间隔 <b>" + (u.heartbeat_interval || 30) + "s</b>");
        rows.push("iperf3 <b>" + (u.iperf3_available ? "就绪" : "未内置") + "</b>");
        rows.push("已执行命令 <b>" + (u.executed_count || 0) + "</b>");
        if (u.last_error) rows.push('<span style="color:#e57373">' + esc(u.last_error) + "</span>");
        st.innerHTML = rows.join(" · ");
    }
}

async function savePerfUplink(enabled) {
    var sv = document.getElementById("perfUplinkServer");
    var tk = document.getElementById("perfUplinkToken");
    var url = "/api/perf/uplink/save?enabled=" + (enabled ? "true" : "false") +
        "&server_url=" + encodeURIComponent(sv ? sv.value.trim() : "");
    if (enabled && tk && tk.value.trim()) {
        url += "&token=" + encodeURIComponent(tk.value.trim());
    }
    var d = await perfApi(url);
    if (!d || !d.success) {
        alert((enabled ? "启用失败" : "停用失败") + ": " + ((d && d.error) || "未知错误"));
        return;
    }
    if (tk) tk.value = "";  // 保存后清空输入框（token 不回显）
    renderPerfUplink(d.uplink || {});
}

async function registerPerfUplink() {
    var d = await perfApi("/api/perf/uplink/register");
    if (!d || !d.success) {
        alert("注册失败: " + ((d && d.error) || "未知错误") +
            "\n请先填写服务端地址与接入 Token 并保存启用。");
        return;
    }
    renderPerfUplink(d.uplink || {});
}
