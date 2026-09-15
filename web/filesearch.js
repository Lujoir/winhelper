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
    searching: false,
    sortCol: "",       /* 表头排序列：name/size/date_modified（服务端重查）| ext（前端排）| 空=默认 */
    sortAsc: true,     /* 正序/倒序（换列复位正序） */
    exts: []           /* 类型预筛选中后缀（多选 OR，空=全部） */
};

/* 常用后缀快捷筛选（走 Everything 原生 ext: 语法拼接，多选=OR 分号多值） */
var FS_EXT_CHIPS = [
    { label: "全部", exts: [] },
    { label: "pdf", exts: ["pdf"] },
    { label: "doc(x)", exts: ["doc", "docx"] },
    { label: "ppt(x)", exts: ["ppt", "pptx"] },
    { label: "xls(x)", exts: ["xls", "xlsx"] },
    { label: "zip", exts: ["zip"] },
    { label: "exe", exts: ["exe"] },
    { label: "txt", exts: ["txt"] }
];

function fsInitChips() {
    var host = document.getElementById("fsChips");
    if (!host || host.dataset.chipInit) { return; }
    host.dataset.chipInit = "1";
    var html = "";
    for (var i = 0; i < FS_EXT_CHIPS.length; i++) {
        html += '<button type="button" class="nd-chip" data-i="' + i + '" onclick="fsChipToggle(' + i + ')">'
            + fsEscapeHtml(FS_EXT_CHIPS[i].label) + '</button>';
    }
    host.innerHTML = html;
    fsSyncChips();
}

function fsChipToggle(i) {
    var chip = FS_EXT_CHIPS[i];
    if (!chip) { return; }
    if (i === 0) {                       /* 「全部」= 清空筛选（互斥） */
        fsState.exts = [];
    } else {
        var sel = fsState.exts.slice();
        for (var k = 0; k < chip.exts.length; k++) {
            var pos = sel.indexOf(chip.exts[k]);
            if (pos >= 0) { sel.splice(pos, 1); } else { sel.push(chip.exts[k]); }
        }
        fsState.exts = sel;
        if (!sel.length) { fsState.exts = []; }
    }
    fsSyncChips();
}

function fsSyncChips() {
    var host = document.getElementById("fsChips");
    if (!host) { return; }
    var btns = host.querySelectorAll(".nd-chip");
    for (var i = 0; i < btns.length; i++) {
        var idx = parseInt(btns[i].getAttribute("data-i"), 10);
        var exts = FS_EXT_CHIPS[idx] ? FS_EXT_CHIPS[idx].exts : [];
        var on = false;
        if (idx === 0) { on = fsState.exts.length === 0; }
        else if (exts.length) {
            on = true;
            for (var k = 0; k < exts.length; k++) {
                if (fsState.exts.indexOf(exts[k]) < 0) { on = false; break; }
            }
        }
        if (on) { btns[i].classList.add("on"); } else { btns[i].classList.remove("on"); }
    }
}

function fsBuildQuery(base) {
    /* 检索词 + 类型筛选拼接（Everything 原生语法：空格 AND，ext:a;b 分号 OR） */
    if (!fsState.exts.length) { return base; }
    return base + " ext:" + fsState.exts.join(";");
}

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
    fsDoSearch(q);
}

function fsDoSearch(q) {
    /* 检索词经类型筛选拼接后查询；表头排序列（name/size/date_modified）透传 Everything 原生 sort 参数 */
    fsState.searching = true;
    fsState.lastQuery = q;
    var btn = document.getElementById("fsSearchBtn");
    if (btn) { btn.disabled = true; }
    fsSetTip("fsSummary", "检索中…");
    var body = document.getElementById("fsBody");
    if (body) { body.innerHTML = '<div class="nd-empty">检索中…</div>'; }
    var url = "/api/filesearch/query?q=" + encodeURIComponent(fsBuildQuery(q)) + "&count=200";
    if (fsState.sortCol === "name" || fsState.sortCol === "size" || fsState.sortCol === "date_modified") {
        url += "&sort=" + fsState.sortCol + "&ascending=" + (fsState.sortAsc ? "1" : "0");
    }
    fsApiFetch(url)
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

function fsSortBy(col) {
    /* 表头点击排序：同列翻转正倒序，换列复位正序；类型列前端排，其余服务端重查 */
    if (fsState.sortCol === col) { fsState.sortAsc = !fsState.sortAsc; }
    else { fsState.sortCol = col; fsState.sortAsc = true; }
    if (col === "ext") {
        if (fsState.lastResults && fsState.lastResults.length) {
            fsRenderResults({ results: fsState.lastResults });
        }
        return;
    }
    if (fsState.lastQuery) { fsDoSearch(fsState.lastQuery); }
}

function fsExtOf(name) {
    var i = String(name || "").lastIndexOf(".");
    return i > 0 ? String(name).slice(i + 1).toLowerCase() : "";
}

function fsRenderError(d) {
    var emap = {
        empty_query: "请输入搜索关键词",
        indexer_not_running: "文件索引未就绪：索引器首次运行需要数分钟，请稍后重试；如持续未就绪请联系管理员检查索引器状态"
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
    fsState.lastResults = rs.slice(0);
    var total = d.total !== undefined && d.total !== null ? d.total : null;
    fsSetTip("fsSummary", "完成：命中 " + rs.length + " 条"
        + (total !== null ? "（全部匹配 " + total + "，显示前 " + rs.length + "）" : "")
        + " ｜ 关键词 " + fsEscapeHtml(d.q || fsState.lastQuery || "--"));
    if (!rs.length) {
        el.innerHTML = '<div class="nd-empty">无匹配结果</div>';
        return;
    }
    var shown = rs.slice(0);
    if (fsState.sortCol === "ext") {   /* 类型排序=按扩展名前端排（Everything HTTP 无此 sort 值） */
        shown.sort(function (a, b) {
            var ea = fsExtOf(a.name), eb = fsExtOf(b.name);
            var c = ea < eb ? -1 : (ea > eb ? 1 : 0);
            return fsState.sortAsc ? c : -c;
        });
    }
    var arrow = function (col) {
        if (fsState.sortCol !== col) { return ""; }
        return fsState.sortAsc ? " ▲" : " ▼";
    };
    var rows = "";
    for (var i = 0; i < shown.length; i++) {
        var r = shown[i];
        var full = (r.path ? r.path + "\\" : "") + (r.name || "");
        rows += '<tr oncontextmenu="fsRowMenu(event,this)">'
            + '<td>' + fsEscapeHtml(r.name || "--") + '</td>'
            + '<td>' + fsEscapeHtml(fsExtOf(r.name) || "--") + '</td>'
            + '<td title="' + fsEscapeHtml(full) + '" data-path="' + fsEscapeHtml(r.path || "") + '">'
            + fsEscapeHtml(r.path || "--") + '</td>'
            + '<td class="nd-num">' + fsEscapeHtml(fsFmtSize(r.size)) + '</td>'
            + '<td class="nd-num">' + fsEscapeHtml(fsFmtDate(r.date_modified)) + '</td>'
            + '<td><button class="nd-btn" onclick="fsOpenLocation(this)">打开位置</button></td>'
            + '</tr>';
    }
    el.innerHTML = '<div class="nd-hint" style="margin:2px 0 6px">路径列为文件所在目录，'
        + '「打开位置」将在资源管理器中定位该文件；右键结果行可复制完整路径</div>'
        + '<table class="nd-table"><tr>'
        + '<th class="nd-th-sort" onclick="fsSortBy(\'name\')">名称' + arrow("name") + '</th>'
        + '<th class="nd-th-sort" onclick="fsSortBy(\'ext\')">类型' + arrow("ext") + '</th>'
        + '<th>路径</th>'
        + '<th class="nd-th-sort nd-num" onclick="fsSortBy(\'size\')">大小' + arrow("size") + '</th>'
        + '<th class="nd-th-sort nd-num" onclick="fsSortBy(\'date_modified\')">修改时间' + arrow("date_modified") + '</th>'
        + '<th>操作</th>'
        + '</tr>' + rows + '</table>';
}

function fsOpenLocation(btn) {
    /* 行内打开位置：取同行路径单元格（data-path 纯目录）+ 名称拼接完整路径 */
    if (!btn) { return; }
    var tr = btn.closest("tr");
    if (!tr) { return; }
    var tds = tr.querySelectorAll("td");
    var path = tds[2] ? (tds[2].getAttribute("data-path") || tds[2].textContent) : "";
    var name = tds[0] ? tds[0].textContent : "";
    if (!path || !name) { return; }
    fsOpenPath(path.replace(/\\+$/, "") + "\\" + name);
}

function fsOpenPath(full) {
    if (!full) { return; }
    fsApiFetch("/api/filesearch/open-location?path=" + encodeURIComponent(full))
        .then(function (d) {
            if (!d || d.success === false) { fsSetTip("fsSummary", "打开位置失败：" + ((d && d.error) || "未知")); }
        }).catch(function (e) { fsSetTip("fsSummary", "打开位置失败：" + String(e)); });
}

/* ---- 结果行右键菜单（自绘，STYLE.md 风格）：打开文件位置 / 复制路径 ---- */

function fsRowMenu(e, tr) {
    if (!e || !tr) { return; }
    var tds = tr.querySelectorAll("td");
    var name = tds[0] ? tds[0].textContent : "";
    var path = tds[2] ? (tds[2].getAttribute("data-path") || tds[2].textContent) : "";
    if (!name) { return; }
    fsShowCtxMenu(e, name, path);
}

function fsShowCtxMenu(e, name, path) {
    var m = document.getElementById("fsCtxMenu");
    if (!m) {
        m = document.createElement("div");
        m.id = "fsCtxMenu";
        m.className = "nd-ctx";
        m.innerHTML = '<div class="nd-ctx-item" data-act="open">打开文件位置</div>'
            + '<div class="nd-ctx-item" data-act="copy">复制路径</div>';
        document.body.appendChild(m);
        m.addEventListener("click", function (ev) {
            var it = ev.target;
            while (it && it !== m && (it.className || "").toString().indexOf("nd-ctx-item") < 0) {
                it = it.parentElement;
            }
            if (!it || it === m) { return; }
            var full = m.getAttribute("data-full") || "";
            if (it.getAttribute("data-act") === "open") { fsOpenPath(full); }
            else if (fsCopyText(full)) { fsSetTip("fsSummary", "路径已复制"); }
            else { fsSetTip("fsSummary", "复制失败（剪贴板不可用）"); }
            fsHideCtxMenu();
        });
    }
    m.setAttribute("data-full", (path ? path.replace(/\\+$/, "") + "\\" : "") + name);
    m.style.display = "block";
    var x = e.clientX, y = e.clientY;
    var r = m.getBoundingClientRect();
    if (x + r.width > window.innerWidth - 8) { x = window.innerWidth - r.width - 8; }
    if (y + r.height > window.innerHeight - 8) { y = window.innerHeight - r.height - 8; }
    m.style.left = x + "px";
    m.style.top = y + "px";
}

function fsHideCtxMenu() {
    var m = document.getElementById("fsCtxMenu");
    if (m) { m.style.display = "none"; }
}

function fsCopyText(t) {
    /* file:// 环境无 navigator.clipboard secure context 保证，走 execCommand 兜底 */
    var ta = document.createElement("textarea");
    ta.value = t;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return ok;
}

function fsLoadStatus() {
    fsApiFetch("/api/filesearch/status").then(function (d) {
        var el = document.getElementById("fsStatusBody");
        if (!el || !d || d.success === false) {
            window.__fsStatusErr = "bad_response: " + JSON.stringify(d).slice(0, 120);
            return;
        }
        var ready = d.db_exists && d.file_count > 0;
        var bits = [];
        bits.push(ready ? fsBadge("索引就绪", "nd-ok") : fsBadge("索引未就绪", "nd-warn"));
        bits.push('<span class="nd-hint">'
            + (ready
                ? "已索引 " + Number(d.file_count).toLocaleString() + " 个文件（"
                  + Number(d.dir_count).toLocaleString() + " 个目录）"
                  + (d.volumes && d.volumes.length ? " · 卷 " + d.volumes.join(" / ") : "")
                : "索引器首次运行需要数分钟，期间检索暂不可用；如长时间未就绪请联系管理员检查索引器状态")
            + "</span>");
        el.innerHTML = bits.join(" ");
    }).catch(function (e) {
        window.__fsStatusErr = "catch: " + String(e).slice(0, 160);
    });
}

function initFileSearchTab() {
    nd_fs_boot();
    fsInitChips();
    if (fsState.inited) {
        fsLoadStatus();
        return;
    }
    fsState.inited = true;
    fsInitCollapsible();
    fsLoadStatus();
    document.addEventListener("click", fsHideCtxMenu);
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
