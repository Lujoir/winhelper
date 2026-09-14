/*
 * filesearch.js — EyeTerm「文件检索」前端逻辑（fs 前缀，2026-09-11）
 * ==================================================================
 *
 * 独立页（filesearch-standalone.html）与主应用 tab-filesearch 共用。
 * 样式规格遵循 net-doctor/docs/STYLE.md（卡片基线/默认收起点按钮展开/零实现细节文案）。
 */

var fsState = {
    inited: false,
    lastQuery: "",
    searching: false
};

function fsApiFetch(path) {
    /* 主应用宿主 apiFetch（app.js，带 15s 超时保护）优先；独立页走 pywebview/fetch */
    if (typeof apiFetch === "function") { return apiFetch(path); }
    var call;
    if (window.pywebview && window.pywebview.api) {
        call = window.pywebview.api.call(path);
        call.catch(function () {});
    } else {
        call = fetch(path).then(function (r) { return r.json(); });
    }
    return Promise.race([
        call,
        new Promise(function (_, rej) {
            setTimeout(function () { rej(new Error("api_timeout_15000ms")); }, 15000);
        })
    ]);
}

function fsSetTip(id, text) {
    var el = document.getElementById(id);
    if (el) { el.textContent = text || ""; }
}

function fsEscapeHtml(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
        .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function fsBadge(text, cls) {
    return '<span class="nd-badge ' + (cls || "nd-muted") + '">' + fsEscapeHtml(text) + "</span>";
}

function fsExpandCard(anchorId) {
    /* 收起卡的搜索按钮点击 → 先展开再搜索（与网络排障五卡联动机制一致） */
    var btn = document.getElementById(anchorId);
    var card = btn ? btn.closest(".section-card") : null;
    if (card && card.classList.contains("collapsed")) {
        card.classList.remove("collapsed");
    }
}

function fsInitCollapsible() {
    /* 独立页折叠注入（主应用由 app.js initCollapsibleCards 提供） */
    if (typeof initCollapsibleCards === "function") { return; }
    var cards = document.querySelectorAll(".section-card[data-collapse]");
    for (var i = 0; i < cards.length; i++) {
        var card = cards[i];
        var header = card.querySelector(".card-header");
        if (!header || header.dataset.collapseInit) { continue; }
        header.dataset.collapseInit = "1";
        header.style.cursor = "pointer";
        var chev = document.createElement("span");
        chev.className = "nd-chevron";
        chev.textContent = "▾";
        header.appendChild(chev);
        (function (c, h) {
            h.addEventListener("click", function (e) {
                if (e.target.closest("button, select, input, a, label")) { return; }
                c.classList.toggle("collapsed");
            });
        })(card, header);
    }
}

function fsFmtSize(n) {
    if (n === null || n === undefined) { return "--"; }
    if (n < 1024) { return n + " B"; }
    if (n < 1048576) { return (n / 1024).toFixed(1) + " KB"; }
    if (n < 1073741824) { return (n / 1048576).toFixed(1) + " MB"; }
    return (n / 1073741824).toFixed(2) + " GB";
}

function fsFmtDate(ts) {
    if (!ts) { return "--"; }
    try {
        var d = new Date(ts * 1000);
        if (isNaN(d.getTime())) { return "--"; }
        function p2(x) { return (x < 10 ? "0" : "") + x; }
        return d.getFullYear() + "-" + p2(d.getMonth() + 1) + "-" + p2(d.getDate())
            + " " + p2(d.getHours()) + ":" + p2(d.getMinutes());
    } catch (e) { return "--"; }
}

function fsStartSearch() {
    fsExpandCard("fsSearchBtn");
    if (fsState.searching) { return; }
    var input = document.getElementById("fsQuery");
    var q = (input && input.value || "").trim();
    if (!q) {
        fsSetTip("fsSummary", "请输入搜索关键词（支持 Everything 语法，如 ext:pdf，大小写不敏感）");
        return;
    }
    fsState.searching = true;
    fsState.lastQuery = q;
    var btn = document.getElementById("fsSearchBtn");
    if (btn) { btn.disabled = true; }
    fsSetTip("fsSummary", "检索中…");
    var body = document.getElementById("fsBody");
    if (body) { body.innerHTML = '<div class="nd-empty">检索中…</div>'; }
    fsApiFetch("/api/filesearch/query?q=" + encodeURIComponent(q) + "&count=200")
        .then(function (d) {
            fsState.searching = false;
            if (btn) { btn.disabled = false; }
            if (!d || d.success === false) { fsRenderError(d || {}); return; }
            fsRenderResults(d);
        }).catch(function (e) {
            fsState.searching = false;
            if (btn) { btn.disabled = false; }
            fsSetTip("fsSummary", "检索失败：" + String(e));
        });
}

function fsRenderError(d) {
    var emap = {
        empty_query: "请输入搜索关键词",
        everything_not_found: "未找到 Everything.exe：请在设置中配置路径，或安装 Everything 1.4 x64",
        http_not_listening: "Everything 已启动但 HTTP 服务器未就绪：读 MFT 需管理员权限，请以管理员运行 EyeTerm 后重试",
        exited_early: "Everything 启动后立即退出（检查实例冲突或权限）"
    };
    var msg = emap[d.error] || d.hint || d.error || "未知错误";
    fsSetTip("fsSummary", "检索失败：" + msg);
    var body = document.getElementById("fsBody");
    if (body) {
        body.innerHTML = '<div class="nd-empty">' + fsEscapeHtml(msg) + "</div>";
    }
}

function fsRenderResults(d) {
    var el = document.getElementById("fsBody");
    if (!el) { return; }
    var rs = d.results || [];
    var total = d.total !== undefined && d.total !== null ? d.total : null;
    fsSetTip("fsSummary", "完成：命中 " + rs.length + " 条"
        + (total !== null ? "（全部匹配 " + total + "，显示前 " + rs.length + "）" : "")
        + " ｜ 关键词 " + fsEscapeHtml(d.q || fsState.lastQuery || "--"));
    if (!rs.length) {
        el.innerHTML = '<div class="nd-empty">无匹配结果</div>';
        return;
    }
    var rows = "";
    for (var i = 0; i < rs.length; i++) {
        var r = rs[i];
        var full = (r.path ? r.path + "\\" : "") + (r.name || "");
        rows += '<tr>'
            + '<td>' + fsEscapeHtml(r.name || "--") + '</td>'
            + '<td title="' + fsEscapeHtml(full) + '">' + fsEscapeHtml(r.path || "--") + '</td>'
            + '<td class="nd-num">' + fsEscapeHtml(fsFmtSize(r.size)) + '</td>'
            + '<td class="nd-num">' + fsEscapeHtml(fsFmtDate(r.date_modified)) + '</td>'
            + '<td><button class="nd-btn" onclick="fsOpenLocation(this)">打开位置</button></td>'
            + '</tr>';
    }
    el.innerHTML = '<div class="nd-hint" style="margin:2px 0 6px">路径列为文件所在目录，'
        + '「打开位置」将在资源管理器中定位该文件</div>'
        + '<table class="nd-table"><tr><th>名称</th><th>路径</th><th>大小</th><th>修改时间</th><th>操作</th></tr>'
        + rows + '</table>';
}

function fsOpenLocation(btn) {
    /* 行内打开位置：取同行路径单元格 + 名称拼接完整路径（复用磁盘模块打开位置模式） */
    if (!btn) { return; }
    var tr = btn.closest("tr");
    if (!tr) { return; }
    var tds = tr.querySelectorAll("td");
    var path = tds[1] ? tds[1].getAttribute("title") || tds[1].textContent : "";
    var name = tds[0] ? tds[0].textContent : "";
    if (!path || !name) { return; }
    var full = path.replace(/\\+$/, "") + "\\" + name;
    fsApiFetch("/api/filesearch/open-location?path=" + encodeURIComponent(full))
        .then(function (d) {
            if (!d || d.success === false) { fsSetTip("fsSummary", "打开位置失败：" + ((d && d.error) || "未知")); }
        }).catch(function (e) { fsSetTip("fsSummary", "打开位置失败：" + String(e)); });
}

function fsLoadStatus() {
    fsApiFetch("/api/filesearch/status").then(function (d) {
        var el = document.getElementById("fsStatusBody");
        if (!el || !d || d.success === false) {
            window.__fsStatusErr = "bad_response: " + JSON.stringify(d).slice(0, 120);
            return;
        }
        var bits = [];
        bits.push(d.http_available ? fsBadge("Everything 在线", "nd-ok") : fsBadge("未就绪", "nd-warn"));
        bits.push('<span class="nd-hint">' + (d.exe_found
            ? "已定位 Everything 程序" : "未找到 Everything 程序（可在下方配置路径）") + "</span>");
        el.innerHTML = bits.join(" ");
    }).catch(function (e) {
        window.__fsStatusErr = "catch: " + String(e).slice(0, 160);
    });
}

function fsSavePath() {
    var input = document.getElementById("fsExePath");
    var p = input ? input.value.trim() : "";
    if (!p) { fsSetTip("fsPathTip", "请填写 Everything.exe 完整路径"); return; }
    fsSetTip("fsPathTip", "保存中…");
    fsApiFetch("/api/filesearch/save-path?path=" + encodeURIComponent(p)).then(function (d) {
        if (!d || d.success === false) {
            fsSetTip("fsPathTip", "保存失败：路径不存在或不可写");
            return;
        }
        fsSetTip("fsPathTip", "已保存");
        fsLoadStatus();
    }).catch(function (e) { fsSetTip("fsPathTip", "保存失败：" + String(e)); });
}

function initFileSearchTab() {
    nd_fs_boot();
    if (fsState.inited) {
        fsLoadStatus();
        return;
    }
    fsState.inited = true;
    fsInitCollapsible();
    fsLoadStatus();
    var input = document.getElementById("fsQuery");
    if (input) {
        input.addEventListener("keydown", function (e) {
            if (e.key === "Enter") { fsStartSearch(); }
        });
    }
}

function nd_fs_boot() { /* 主应用 boot 兼容占位：独立页/主应用均可直接调用 */ }

if (typeof window !== "undefined" && typeof window.initFileSearchTab === "undefined") {
    window.initFileSearchTab = initFileSearchTab;
}
