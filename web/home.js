/* ==========================================
   主页 · 终端概览（观枢终端平台｜EyeTerm）
   终端配置（含平台对接状态）+ 硬件状态 + 本地网络配置
   数据：/api/perf/hwinfo · /api/perf/snapshot · /api/perf/temps
        · /api/perf/uplink/status · /api/home/network
   命名空间约定：除入口 initHomeTab 外全部使用 hm 前缀，工具自包含。
   实时刷新：snapshot/temps 每 30s（页面隐藏时跳过）。
   ========================================== */

var hmState = { inited: false, timer: null, hwinfo: null, temps: null };

// ===================== 入口（幂等） =====================
function initHomeTab() {
    if (hmState.inited) { return; }
    hmState.inited = true;
    hmLoadStatic();
    hmRefreshLive();
    if (hmState.timer) { clearInterval(hmState.timer); }
    hmState.timer = setInterval(function () {
        if (!document.hidden) { hmRefreshLive(); }
    }, 30000);
}

// ===================== 传输与工具 =====================
function hmApiFetch(path) {
    if (typeof apiFetch === "function") { return apiFetch(path); }
    if (window.pywebview && window.pywebview.api) {
        return window.pywebview.api.call(path);
    }
    return fetch(path).then(function (r) { return r.json(); });
}

function hmEscapeHtml(str) {
    if (str === null || str === undefined) { return ""; }
    return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

function hmFmtSize(bytes) {
    if (bytes === null || bytes === undefined || isNaN(bytes)) { return "--"; }
    bytes = Number(bytes);
    var units = ["B", "KB", "MB", "GB", "TB", "PB"];
    var i = 0;
    while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++; }
    return (i === 0 ? bytes : bytes.toFixed(1)) + " " + units[i];
}

function hmShowError(msg) {
    var div = document.createElement("div");
    div.style.cssText = "position:fixed;bottom:24px;right:24px;z-index:999;padding:14px 20px;" +
        "background:#3a1d1d;border:1px solid #e57373;border-radius:8px;color:#ef9a9a;" +
        "font-size:13px;max-width:420px;box-shadow:0 4px 16px rgba(0,0,0,.4)";
    div.textContent = msg;
    document.body.appendChild(div);
    setTimeout(function () { div.remove(); }, 4000);
}

function hmBar(pct, warnAt) {
    var v = (pct === null || pct === undefined || isNaN(pct)) ? null : Number(pct);
    if (v === null) {
        return '<div class="hm-bar"><div class="hm-bar-fill" style="width:0%;background:#455a64"></div></div>';
    }
    var color = v >= warnAt ? "#ef5350" : (v >= warnAt * 0.8 ? "#ffb74d" : "#66bb6a");
    var w = Math.max(0, Math.min(100, v));
    return '<div class="hm-bar"><div class="hm-bar-fill" style="width:' + w + '%;background:' + color + '"></div></div>';
}

function hmBadge(text, cls) {
    return '<span class="hm-badge ' + cls + '">' + hmEscapeHtml(text) + '</span>';
}

function hmRow(label, valueHtml) {
    return '<div class="hm-row"><span class="hm-row-label">' + hmEscapeHtml(label) +
        '</span><span class="hm-row-value">' + valueHtml + '</span></div>';
}

function hmDash(v) {
    return (v === null || v === undefined || v === "" || v === "--") ? "--" : hmEscapeHtml(v);
}

// ===================== 数据加载 =====================
function hmLoadStatic() {
    hmApiFetch("/api/perf/hwinfo").then(function (d) {
        // 真实接口结构为 {success, hwinfo:{...}}（loginspector 本机信息面板同源）；
        // 兼容顶层结构，防止字段漂移
        if (d && d.success !== false) { hmState.hwinfo = d.hwinfo || d; }
        hmRenderStatic();
    }).catch(function () { hmRenderStatic(); });
    hmApiFetch("/api/perf/uplink/status").then(function (d) {
        hmRenderUplink(d && d.uplink ? d.uplink : null);
    }).catch(function () { hmRenderUplink(null); });
    hmApiFetch("/api/home/network").then(function (d) {
        hmRenderNetwork(d);
    }).catch(function (e) { hmRenderNetwork({ success: false, error: String(e) }); });
}

function hmRefreshLive() {
    hmApiFetch("/api/perf/snapshot").then(function (d) {
        hmRenderLive(d);
    }).catch(function () { /* 下一拍重试 */ });
    hmApiFetch("/api/perf/temps").then(function (d) {
        hmState.temps = d;
        hmRenderTemps(d);
    }).catch(function () { /* 静默 */ });
}

function hmRefreshAll() {
    var btn = document.getElementById("homeRefreshBtn");
    if (btn) { btn.disabled = true; btn.textContent = "刷新中…"; }
    hmState.inited = false;
    initHomeTab();
    setTimeout(function () {
        if (btn) { btn.disabled = false; btn.textContent = "刷新"; }
    }, 1500);
}

// ===================== 渲染：终端配置（平台对接） =====================
var hmUplinkStateMap = {
    connected: { text: "已连接", cls: "hm-ok" },
    connecting: { text: "连接中", cls: "hm-warn" },
    error: { text: "异常", cls: "hm-err" },
    disabled: { text: "未启用", cls: "hm-muted" }
};

function hmRenderUplink(u) {
    var el = document.getElementById("homeUplinkBody");
    if (!el) { return; }
    if (!u) {
        el.innerHTML = '<div class="hm-empty">平台对接状态不可用</div>';
        return;
    }
    var st = hmUplinkStateMap[u.state] || { text: u.state || "--", cls: "hm-muted" };
    var hb = "--";
    if (u.last_hb_ts) {
        var d = new Date(u.last_hb_ts * 1000);
        var p = function (n) { return (n < 10 ? "0" : "") + n; };
        hb = d.getFullYear() + "/" + (d.getMonth() + 1) + "/" + d.getDate() + " " +
            p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
    }
    var html = "";
    html += hmRow("平台接入", hmBadge(st.text, st.cls) +
        (u.enabled ? "" : ' <span class="hm-hint">（设置中可开启）</span>'));
    html += hmRow("服务地址", u.server_url ? hmEscapeHtml(u.server_url) : "--");
    html += hmRow("终端 ID", hmDash(u.terminal_id));
    html += hmRow("客户端版本", hmDash(u.client_version));
    html += hmRow("注册状态", u.registered ? hmBadge("已注册", "hm-ok") : hmBadge("未注册", "hm-muted"));
    html += hmRow("最近心跳", hb);
    html += hmRow("心跳间隔", (u.heartbeat_interval || "--") + " 秒");
    if (u.state === "error" && u.last_error) {
        html += '<div class="hm-err-line">错误：' + hmEscapeHtml(u.last_error) + '</div>';
    }
    el.innerHTML = html;
}

// ===================== 渲染：硬件静态规格 =====================
function hmRenderStatic() {
    var el = document.getElementById("homeHwStatic");
    if (!el) { return; }
    var h = hmState.hwinfo;
    if (!h) {
        el.innerHTML = '<div class="hm-empty">硬件信息加载中…（首次采集可能需数秒）</div>';
        return;
    }
    var html = "";
    var gpus = h.gpu || [];
    for (var i = 0; i < gpus.length; i++) {
        var g = gpus[i];
        html += '<div class="hm-sub">' + hmEscapeHtml(g.name || "--") +
            ' ' + (g.dedicated ? hmBadge("独显", "hm-info") : hmBadge("核显", "hm-muted")) + '</div>' +
            '<div class="hm-kv">显存 ' + hmDash(g.vram_text) +
            ' ｜ 驱动 ' + hmDash(g.driver) +
            ' ｜ 分辨率 ' + hmDash(g.resolution) + '</div>';
    }
    var disks = h.disks || [];
    for (var j = 0; j < disks.length; j++) {
        var dk = disks[j];
        var media = (dk.media && dk.media !== "--") ? hmBadge(dk.media, dk.media === "SSD" ? "hm-info" : "hm-muted") : "";
        var sysTag = dk.system ? hmBadge("系统盘", "hm-warn") : "";
        var vols = (dk.volumes && dk.volumes.length) ? "（" + dk.volumes.join(" ") + "）" : "";
        html += '<div class="hm-sub">' + hmEscapeHtml(dk.model || dk.name || "--") +
            ' ' + media + sysTag + '</div>' +
            '<div class="hm-kv">' + (dk.size ? hmFmtSize(dk.size) : "--") +
            ' ｜ ' + hmDash(dk.bus) + ' ' + hmEscapeHtml(vols) + '</div>';
    }
    var mods = (h.memory && h.memory.modules) || [];
    if (mods.length) {
        var parts = [];
        for (var k = 0; k < mods.length; k++) {
            var m = mods[k];
            parts.push(m.slot + ": " + (m.size ? hmFmtSize(m.size) : "--") +
                (m.type && m.type !== "--" ? " " + m.type : "") +
                (m.speed_mhz ? " @" + m.speed_mhz + "MHz" : ""));
        }
        html += '<div class="hm-kv">内存条：' + hmEscapeHtml(parts.join("｜")) + '</div>';
    }
    if (!html) { html = '<div class="hm-empty">暂无硬件规格数据</div>'; }
    el.innerHTML = html;
    hmRenderOs(h);
}

function hmRenderOs(h) {
    var el = document.getElementById("homeOsBody");
    if (!el) { return; }
    var os = h.os || {};
    var html = "";
    html += hmRow("主机名", hmDash(h.hostname));
    html += hmRow("操作系统", hmDash(os.caption));
    html += hmRow("系统版本", hmDash(os.version) + (os.build ? '（Build ' + hmEscapeHtml(os.build) + '）' : ""));
    html += hmRow("处理器", hmEscapeHtml((h.cpu && h.cpu.name) || "--") +
        ((h.cpu && h.cpu.cores) ? '<span class="hm-hint">　' + h.cpu.cores + " 核" +
            (h.cpu.logical ? " / " + h.cpu.logical + " 线程" : "") + '</span>' : ""));
    html += hmRow("内存总量", (h.memory && h.memory.total) ? hmFmtSize(h.memory.total) : "--");
    el.innerHTML = html;
}

// ===================== 渲染：实时指标 =====================
function hmRenderLive(s) {
    var el = document.getElementById("homeLiveBody");
    if (!el) { return; }
    if (!s || s.success === false) {
        el.innerHTML = '<div class="hm-empty">实时指标暂不可用</div>';
        return;
    }
    var cpu = s.cpu || {}, mem = s.memory || {}, swap = s.swap || {};
    var html = "";
    html += '<div class="hm-meter"><div class="hm-meter-head"><span>CPU 使用率</span>' +
        '<b>' + (cpu.percent === null || cpu.percent === undefined ? "--" : cpu.percent + "%") + '</b></div>' +
        hmBar(cpu.percent, 85) +
        '<div class="hm-kv">' + (cpu.count || "--") + " 线程" +
        (cpu.freq_mhz ? " ｜ 当前 " + cpu.freq_mhz + " MHz" : "") + '</div></div>';
    html += '<div class="hm-meter"><div class="hm-meter-head"><span>内存使用率</span>' +
        '<b>' + (mem.percent === null || mem.percent === undefined ? "--" : mem.percent + "%") + '</b></div>' +
        hmBar(mem.percent, 90) +
        '<div class="hm-kv">可用 ' + hmDash(mem.available_text) + " / 共 " + hmDash(mem.total_text) +
        (swap.total ? " ｜ Swap " + swap.percent + "%" : "") + '</div></div>';
    var vols = s.volumes || [];
    if (vols.length) {
        html += '<div class="hm-meter"><div class="hm-meter-head"><span>分区容量</span></div>';
        for (var i = 0; i < vols.length; i++) {
            var v = vols[i];
            html += '<div class="hm-vol"><div class="hm-meter-head"><span>' + hmEscapeHtml(v.mount) +
                '</span><b>' + (v.percent === null ? "--" : v.percent + "%") +
                ' <i class="hm-hint">' + hmFmtSize(v.used) + " / " + hmFmtSize(v.total) + '</i></b></div>' +
                hmBar(v.percent, 90) + '</div>';
        }
        html += '</div>';
    }
    el.innerHTML = html;
}

function hmRenderTemps(t) {
    var el = document.getElementById("homeTempsBody");
    if (!el) { return; }
    if (!t || t.success === false) {
        el.innerHTML = '<div class="hm-empty">温度不可用</div>';
        return;
    }
    var c = t.cpu || {}, g = t.gpu || {};
    var cpuText = (c.available && c.temp_c !== null && c.temp_c !== undefined)
        ? c.temp_c + " ℃" : "--" + (c.reason && !t.admin ? " " + hmBadge("需管理员", "hm-muted") : "");
    var gpuText = (g.available && g.temp_c !== null && g.temp_c !== undefined) ? g.temp_c + " ℃" : "--";
    var html = "";
    html += hmRow("CPU 温度", cpuText + (t.admin ? "" : ' <span class="hm-hint">（以管理员重启可读）</span>'));
    html += hmRow("GPU 温度", gpuText);
    el.innerHTML = html;
}

// ===================== 渲染：本地网络配置 =====================
function hmRenderNetwork(d) {
    var el = document.getElementById("homeNetBody");
    if (!el) { return; }
    if (!d || d.success === false || !d.adapters) {
        el.innerHTML = '<div class="hm-empty">网络配置不可用' +
            (d && d.error ? '：' + hmEscapeHtml(d.error) : "") + '</div>';
        return;
    }
    var html = "";
    var adapters = d.adapters;
    for (var i = 0; i < adapters.length; i++) {
        html += hmNetCard(adapters[i], d);
    }
    if (!html) { html = '<div class="hm-empty">未发现网络适配器</div>'; }
    el.innerHTML = html;
}

function hmNetCard(a, d) {
    var up = String(a.status).toLowerCase() === "up";
    var ipText = "--";
    if (a.ipv4 && a.ipv4.length) {
        var segs = [];
        for (var k = 0; k < a.ipv4.length; k++) {
            var plen = (a.plen && a.plen[k] !== undefined) ? "/" + a.plen[k] : "";
            segs.push(a.ipv4[k] + plen);
        }
        ipText = segs.join("，");
    }
    var html = '<div class="hm-net-card' + (up ? "" : " hm-net-down") + '">' +
        '<div class="hm-net-head"><b>' + hmEscapeHtml(a.name) + '</b>' +
        (up ? hmBadge("已连接", "hm-ok") : hmBadge("断开", "hm-muted")) +
        (d.source === "psutil" ? hmBadge("精简", "hm-muted") : "") +
        '</div>' +
        '<div class="hm-kv">' + hmEscapeHtml(a.desc) + '</div>' +
        '<div class="hm-net-grid">';
    html += hmRow("IPv4", hmEscapeHtml(ipText));
    html += hmRow("网关", hmDash(a.gw));
    html += hmRow("DNS", (a.dns && a.dns.length) ? hmEscapeHtml(a.dns.join("，")) : "--");
    html += hmRow("MAC", hmDash(a.mac));
    html += hmRow("速率", hmDash(a.speed));
    html += hmRow("DHCP", (a.dhcp === null || a.dhcp === undefined)
        ? "--" : (a.dhcp ? "是" : "否"));
    html += '</div></div>';
    return html;
}

// DOMContentLoaded 时若主页为默认激活 tab，自动初始化
(function () {
    var boot = function () {
        var sec = document.getElementById("tab-home");
        if (sec && sec.classList.contains("active")) { initHomeTab(); }
    };
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }
})();

// ===================== 渲染：本地网络配置 =====================
function hmRenderNetwork(d) {
    var el = document.getElementById("homeNetBody");
    if (!el) { return; }
    if (!d || d.success === false || !d.adapters) {
        el.innerHTML = '<div class="hm-empty">网络配置不可用' +
            (d && d.error ? '：' + hmEscapeHtml(d.error) : "") + '</div>';
        return;
    }
    var html = "";
    var adapters = d.adapters;
    for (var i = 0; i < adapters.length; i++) {
        html += hmNetCard(adapters[i], d);
    }
    if (!html) { html = '<div class="hm-empty">未发现网络适配器</div>'; }
    el.innerHTML = html;
}

/* uplink status 30s refresh (fix: stuck at connecting) */
setInterval(function () {
    if (document.hidden) { return; }
    var sec = document.getElementById("tab-home");
    if (!sec) { return; }
    if (!sec.classList.contains("active")) { return; }
    hmApiFetch("/api/perf/uplink/status").then(function (d) {
        var u = d ? d.uplink : null;
        hmRenderUplink(u);
    }).catch(function () {});
}, 30000);
