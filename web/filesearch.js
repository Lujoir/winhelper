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
    sortCol: "",       /* 表头排序列：name/ext/size/date_modified（四列均服务端重查，2026-09-16）| 空=默认 */
    sortAsc: true,     /* 正序/倒序（换列复位正序） */
    exts: [],          /* 类型预筛 chips 选中后缀（多选 OR，空=全部） */
    advExts: [],       /* 类型下拉手动选择的扩展名 */
    customExts: [],    /* 自定义类型输入（逗号/分号分隔，与 chips/下拉互斥） */
    dateFrom: "",     /* 修改时间起（YYYY-MM-DD） */
    dateTo: "",       /* 修改时间止（YYYY-MM-DD） */
    typeLoaded: false /* 是否已加载类型统计 */
};

/* 常用后缀快捷筛选（ext: 语法拼接，多选=OR 分号多值） */
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

function fsClearCustomExt() {
    /* chips/类型下拉操作时清自定义输入（互斥切换） */
    fsState.customExts = [];
    var input = document.getElementById("fsCustomExt");
    if (input && input.value) { input.value = ""; }
}

function fsOnCustomExt() {
    /* 自定义类型输入：逗号/分号（含中英文）/空格分隔多扩展名 → ext: 多值 OR；
       自定义生效时清 chips 与类型下拉选中态（互斥） */
    var input = document.getElementById("fsCustomExt");
    if (!input) { return; }
    var raw = (input.value || "").toLowerCase();
    var parts = raw.split(/[,;\s，；]+/).filter(function (s) { return s && /^[a-z0-9]+$/.test(s); });
    fsState.customExts = parts;
    if (parts.length && (fsState.exts.length || fsState.advExts.length)) {
        fsState.exts = [];
        fsState.advExts = [];
        fsSyncChips();
        var btn = document.getElementById("fsTypeFilterBtn");
        if (btn) { btn.textContent = "选择类型"; }
    }
}

function fsChipToggle(i) {
    var chip = FS_EXT_CHIPS[i];
    if (!chip) { return; }
    fsClearCustomExt();
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
    /* 检索词 + 类型筛选拼接（空格 AND，ext:a;b 分号 OR；chips/下拉/自定义互斥下仅一组生效） */
    var merged = fsState.exts.slice();
    for (var i = 0; i < fsState.advExts.length; i++) {
        if (merged.indexOf(fsState.advExts[i]) < 0) { merged.push(fsState.advExts[i]); }
    }
    for (var j = 0; j < fsState.customExts.length; j++) {
        if (merged.indexOf(fsState.customExts[j]) < 0) { merged.push(fsState.customExts[j]); }
    }
    if (!merged.length) { return base; }
    return base + " ext:" + merged.join(";");
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

function fsLoadStats() {
    /* 加载索引库中的扩展名统计，用于手动类型筛选 */
    if (fsState.typeLoaded) { return; }
    var panel = document.getElementById("fsTypeFilterPanel");
    if (!panel) { return; }
    fsApiFetch("/api/filesearch/stats").then(function (d) {
        fsState.typeLoaded = true;
        if (!d || d.success === false || !d.exts || !d.exts.length) {
            panel.innerHTML = '<div class="nd-hint">暂无可选类型</div>';
            return;
        }
        fsState._typeStats = d.exts;
        fsRenderTypeFilter();
    }).catch(function () {
        panel.innerHTML = '<div class="nd-hint">类型加载失败</div>';
    });
}

function fsRenderTypeFilter() {
    var panel = document.getElementById("fsTypeFilterPanel");
    var btn = document.getElementById("fsTypeFilterBtn");
    if (!panel) { return; }
    var exts = fsState._typeStats || [];
    if (!exts.length) { panel.innerHTML = '<div class="nd-hint">暂无可选类型</div>'; return; }
    var html = '';
    for (var i = 0; i < exts.length; i++) {
        var e = exts[i].ext, c = exts[i].count;
        var on = fsState.advExts.indexOf(e) >= 0;
        html += '<label class="nd-type-item" title="' + fsEscapeHtml(e) + '">'
            + '<input type="checkbox" data-ext="' + fsEscapeHtml(e) + '" ' + (on ? 'checked' : '') + '>'
            + '<span>' + fsEscapeHtml(e.toUpperCase()) + ' (' + Number(c).toLocaleString() + ')</span>'
            + '</label>';
    }
    panel.innerHTML = html;
    var checks = panel.querySelectorAll("input[type=checkbox]");
    for (var j = 0; j < checks.length; j++) {
        checks[j].addEventListener("change", function () {
            fsToggleTypeFilter(this.getAttribute("data-ext"), this.checked);
        });
    }
    if (btn) {
        var n = fsState.advExts.length;
        btn.textContent = n ? '已选 ' + n + ' 类型' : '选择类型';
    }
}

function fsToggleTypeFilter(ext, on) {
    fsClearCustomExt();
    var idx = fsState.advExts.indexOf(ext);
    if (on) {
        if (idx < 0) { fsState.advExts.push(ext); }
    } else {
        if (idx >= 0) { fsState.advExts.splice(idx, 1); }
    }
    var btn = document.getElementById("fsTypeFilterBtn");
    if (btn) {
        var n = fsState.advExts.length;
        btn.textContent = n ? '已选 ' + n + ' 类型' : '选择类型';
    }
}

function fsFmtInputDate(d) {
    if (!d) { return ""; }
    var s = String(d).slice(0, 10);
    return /^\d{4}-\d{2}-\d{2}$/.test(s) ? s : "";
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
        fsSetTip("fsSummary", "请输入搜索关键词（支持语法筛选，如 ext:pdf，大小写不敏感）");
        return;
    }
    fsDoSearch(q);
}

function fsDoSearch(q) {
    /* 检索词经类型筛选拼接后查询；表头排序列（name/size/date_modified）透传服务端 sort 参数 */
    fsState.searching = true;
    fsState.lastQuery = q;
    var btn = document.getElementById("fsSearchBtn");
    if (btn) { btn.disabled = true; }
    fsSetTip("fsSummary", "检索中…");
    var body = document.getElementById("fsBody");
    if (body) { body.innerHTML = '<div class="nd-empty">检索中…</div>'; }
    var url = "/api/filesearch/query?q=" + encodeURIComponent(fsBuildQuery(q)) + "&count=200";
    if (fsState.sortCol) {   /* 四列均服务端排序（ext 于 2026-09-16 服务端化） */
        url += "&sort=" + (fsState.sortCol === "date_modified" ? "mtime" : fsState.sortCol)
            + "&ascending=" + (fsState.sortAsc ? "1" : "0");
    }
    if (fsState.dateFrom) { url += "&date_from=" + encodeURIComponent(fsState.dateFrom); }
    if (fsState.dateTo) { url += "&date_to=" + encodeURIComponent(fsState.dateTo); }
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
    /* 表头点击排序：同列翻转正倒序，换列复位正序；四列均服务端重查（含 ext） */
    if (fsState.sortCol === col) { fsState.sortAsc = !fsState.sortAsc; }
    else { fsState.sortCol = col; fsState.sortAsc = true; }
    if (fsState.lastQuery) { fsDoSearch(fsState.lastQuery); }
}

function fsExtOf(name) {
    var i = String(name || "").lastIndexOf(".");
    return i > 0 ? String(name).slice(i + 1).toLowerCase() : "";
}

/* ---- 文件资源管理器风格：类型图标（内联 SVG 零外部资源）+ 友好类型名 ---- */
var FS_ICON_COLORS = {
    folder: "#f0b429", doc: "#2b7cd3", xls: "#1e7145", ppt: "#d24726",
    pdf: "#e53935", image: "#9c5bd8", video: "#3f51b5", audio: "#e91e63",
    archive: "#8d6e63", exe: "#607d8b", code: "#00acc1", text: "#78909c",
    mail: "#5c6bc0", key: "#a1887f", unknown: "#9e9e9e"
};

function fsFileIconSvg(cat) {
    /* 类别 → 16px 内联 SVG（类别着色 + data 类供 E2E 断言），零外部资源零依赖 */
    var c = FS_ICON_COLORS[cat] || FS_ICON_COLORS.unknown;
    var cls = "fs-ic fs-ic-" + cat;
    var body;
    if (cat === "folder") {
        body = '<path d="M1.5 3.5h4.2l1.3 1.6h7.5v7.4a1 1 0 0 1-1 1H1.5z" fill="' + c + '"/>';
    } else if (cat === "xls") {
        body = '<path d="M2 1.5h8.2L13.6 5v9.5H2z" fill="' + c + '" opacity=".16"/>'
            + '<path d="M2 1.5h8.2L13.6 5v9.5H2z" fill="none" stroke="' + c + '" stroke-width="1.2"/>'
            + '<path d="M4.4 6.4h6.8M4.4 8.7h6.8M4.4 11h6.8M6.2 6.4v6.2M9.4 6.4v6.2" stroke="' + c + '" stroke-width="1"/>';
    } else if (cat === "video") {
        body = '<rect x="1.8" y="3" width="12.4" height="10" rx="1.4" fill="none" stroke="' + c + '" stroke-width="1.3"/>'
            + '<path d="M6.8 5.8l4.2 2.2-4.2 2.2z" fill="' + c + '"/>';
    } else if (cat === "audio") {
        body = '<path d="M6.2 12.2V4l6-1.2v8" fill="none" stroke="' + c + '" stroke-width="1.3"/>'
            + '<circle cx="4.7" cy="12.3" r="1.6" fill="' + c + '"/><circle cx="10.7" cy="10.9" r="1.6" fill="' + c + '"/>';
    } else if (cat === "archive") {
        body = '<rect x="2.4" y="2.2" width="11.2" height="11.6" rx="1.2" fill="' + c + '" opacity=".18"/>'
            + '<rect x="2.4" y="2.2" width="11.2" height="11.6" rx="1.2" fill="none" stroke="' + c + '" stroke-width="1.2"/>'
            + '<path d="M8 2.4v3.2M8 6.4v1.6M8 9v1.6" stroke="' + c + '" stroke-width="1.2" stroke-dasharray="1.6 1"/>';
    } else if (cat === "exe") {
        body = '<circle cx="8" cy="8" r="5.6" fill="none" stroke="' + c + '" stroke-width="1.3"/>'
            + '<circle cx="8" cy="8" r="1.8" fill="' + c + '"/>'
            + '<path d="M8 2.4v2M8 11.6v2M2.4 8h2M11.6 8h2" stroke="' + c + '" stroke-width="1.3"/>';
    } else if (cat === "image") {
        body = '<rect x="2" y="2.6" width="12" height="10.8" rx="1.2" fill="none" stroke="' + c + '" stroke-width="1.2"/>'
            + '<circle cx="5.6" cy="6.2" r="1.2" fill="' + c + '"/>'
            + '<path d="M3.4 12.4l3.4-3.6 2.2 2.2 2-2 3.4 3.4" fill="none" stroke="' + c + '" stroke-width="1.2"/>';
    } else if (cat === "code") {
        body = '<path d="M5.6 4.6L2.2 8l3.4 3.4M10.4 4.6L13.8 8l-3.4 3.4M9 2.8l-2 10.4" fill="none" stroke="'
            + c + '" stroke-width="1.3" stroke-linecap="round"/>';
    } else if (cat === "mail") {
        body = '<rect x="1.8" y="3.4" width="12.4" height="9.2" rx="1.2" fill="none" stroke="' + c + '" stroke-width="1.2"/>'
            + '<path d="M2.2 4.2L8 9l5.8-4.8" fill="none" stroke="' + c + '" stroke-width="1.2"/>';
    } else if (cat === "key") {
        body = '<circle cx="6" cy="6" r="3.2" fill="none" stroke="' + c + '" stroke-width="1.4"/>'
            + '<path d="M8.4 8.4l5 5M11 11l1.6-1.6" stroke="' + c + '" stroke-width="1.4"/>';
    } else {
        /* 文档底形：doc/ppt/pdf/text/unknown 通用 */
        body = '<path d="M3.4 1.6h6L13 5.2v9.2H3.4z" fill="' + c + '" opacity=".16"/>'
            + '<path d="M3.4 1.6h6L13 5.2v9.2H3.4z" fill="none" stroke="' + c + '" stroke-width="1.2"/>'
            + '<path d="M9.4 1.6v3.6H13" fill="none" stroke="' + c + '" stroke-width="1.2"/>';
        if (cat === "doc" || cat === "text") {
            body += '<path d="M5.4 8.4h5.2M5.4 10.6h5.2" stroke="' + c + '" stroke-width="1"/>';
        }
    }
    return '<svg class="' + cls + '" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">' + body + "</svg>";
}

var FS_TYPE_NAMES = {
    folder: "文件夹",
    image: "图片文件",
    pdf: "PDF 文档",
    doc: "文档",
    ppt: "演示文稿",
    xls: "工作表",
    archive: "压缩文件",
    exe: "可执行文件",
    code: "代码文件",
    text: "文本文件",
    audio: "音频文件",
    video: "视频",
    mail: "邮件",
    key: "密钥文件",
    unknown: "文件"
};

var FS_EXT_CATEGORY = {
    /* images */
    png: "image", jpg: "image", jpeg: "image", gif: "image", bmp: "image", webp: "image", svg: "image", ico: "image", tiff: "image", tif: "image",
    /* pdf */
    pdf: "pdf",
    /* documents */
    doc: "doc", docx: "doc", odt: "doc", rtf: "doc", wps: "doc",
    /* presentations */
    ppt: "ppt", pptx: "ppt", odp: "ppt", pps: "ppt", ppsx: "ppt",
    /* spreadsheets */
    xls: "xls", xlsx: "xls", csv: "xls", ods: "xls", xlsm: "xls",
    /* archives */
    zip: "archive", rar: "archive", "7z": "archive", tar: "archive", gz: "archive", bz2: "archive", xz: "archive", cab: "archive", iso: "archive",
    /* executables */
    exe: "exe", msi: "exe", dll: "exe", sys: "exe",
    /* code */
    js: "code", py: "code", html: "code", htm: "code", css: "code", java: "code", cpp: "code", c: "code", h: "code", hpp: "code", cs: "code", go: "code", rs: "code", php: "code", swift: "code", kt: "code", sql: "code", json: "code", xml: "code", yaml: "code", yml: "code", ts: "code", jsx: "code", tsx: "code", vue: "code", scss: "code", sass: "code", less: "code",
    /* text */
    txt: "text", md: "text", log: "text", ini: "text", conf: "text", cfg: "text", env: "text", properties: "text", nfo: "text",
    /* audio */
    mp3: "audio", wav: "audio", flac: "audio", ogg: "audio", aac: "audio", m4a: "audio", wma: "audio", ape: "audio",
    /* video */
    mp4: "video", avi: "video", mkv: "video", mov: "video", wmv: "video", flv: "video", webm: "video", mpeg: "video", mpg: "video", ts: "video",
    /* mail */
    eml: "mail", msg: "mail", pst: "mail", ost: "mail",
    /* license/keys */
    lic: "key", key: "key", license: "key"
};

function fsFileCategory(name, isDir) {
    if (isDir) { return "folder"; }
    var ext = fsExtOf(name);
    return FS_EXT_CATEGORY[ext] || "unknown";
}

function fsFileIcon(name, isDir) {
    return fsFileIconSvg(fsFileCategory(name, isDir));
}

function fsFileTypeName(name, isDir) {
    /* 资源管理器式友好类型名：目录一律「文件夹」；带扩展名类别=「EXT + 类别名」；
       压缩/可执行/文本/PDF/代码等固定名；未知扩展名「XXX 文件」兜底，无扩展名「文件」。 */
    var cat = fsFileCategory(name, isDir);
    var ext = fsExtOf(name);
    if (cat === "folder") { return "文件夹"; }
    if (cat === "unknown") {
        return ext ? ext.toUpperCase() + " " + FS_TYPE_NAMES.unknown : "文件";
    }
    var base = FS_TYPE_NAMES[cat] || FS_TYPE_NAMES.unknown;
    if (cat === "image" || cat === "video" || cat === "audio"
            || cat === "doc" || cat === "ppt" || cat === "xls") {
        return ext ? ext.toUpperCase() + " " + base : base;
    }
    return base;
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
    var arrow = function (col) {
        if (fsState.sortCol !== col) { return ""; }
        return fsState.sortAsc ? " ▲" : " ▼";
    };
    var rows = "";
    for (var i = 0; i < shown.length; i++) {
        var r = shown[i];
        var isDir = !!r.is_dir;
        var full = (r.path ? r.path + "\\" : "") + (r.name || "");
        var typeName = fsFileTypeName(r.name, isDir);
        var icon = fsFileIcon(r.name, isDir);
        rows += '<tr class="' + (isDir ? "fs-dir" : "fs-file") + '" oncontextmenu="fsRowMenu(event,this)">'
            + '<td class="fs-name-cell"><span class="fs-icon">' + icon + '</span><span class="fs-name" title="' + fsEscapeHtml(r.name) + '">'
            + fsEscapeHtml(r.name || "--") + '</span></td>'
            + '<td class="fs-type-cell" title="' + fsEscapeHtml(typeName) + '">' + fsEscapeHtml(typeName) + '</td>'
            + '<td title="' + fsEscapeHtml(full) + '" data-path="' + fsEscapeHtml(r.path || "") + '">'
            + fsEscapeHtml(r.path || "--") + '</td>'
            + '<td class="nd-num">' + (isDir ? "--" : fsEscapeHtml(fsFmtSize(r.size))) + '</td>'
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
    /* 行内打开位置：取同行路径单元格（data-path 纯目录）+ fs-name 拼接完整路径 */
    if (!btn) { return; }
    var tr = btn.closest("tr");
    if (!tr) { return; }
    var tds = tr.querySelectorAll("td");
    var path = tds[2] ? (tds[2].getAttribute("data-path") || tds[2].textContent) : "";
    var nameCell = tds[0] ? tds[0].querySelector(".fs-name") : null;
    var name = nameCell ? nameCell.textContent : (tds[0] ? tds[0].textContent : "");
    if (!path || !name) { return; }
    fsOpenPath(path.replace(/\\+$/, "") + "\\" + name.trim());
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
    var nameCell = tds[0] ? tds[0].querySelector(".fs-name") : null;
    var name = nameCell ? nameCell.textContent : (tds[0] ? tds[0].textContent : "");
    var path = tds[2] ? (tds[2].getAttribute("data-path") || tds[2].textContent) : "";
    if (!name) { return; }
    fsShowCtxMenu(e, name.trim(), path);
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

function initFileSearchTab() {
    nd_fs_boot();
    fsInitChips();
    if (fsState.inited) { return; }   /* 引擎状态自检在服务层（fs_indexer），UI 无状态卡（2026-09-16） */
    fsState.inited = true;
    fsInitCollapsible();
    fsLoadStats();
    document.addEventListener("click", fsHideCtxMenu);
    var input = document.getElementById("fsQuery");
    if (input) {
        input.addEventListener("keydown", function (e) {
            if (e.key === "Enter") { fsStartSearch(); }
        });
    }
    var ce = document.getElementById("fsCustomExt");
    if (ce) { ce.addEventListener("input", fsOnCustomExt); }
    var df = document.getElementById("fsDateFrom");
    var dt = document.getElementById("fsDateTo");
    if (df) {
        df.addEventListener("change", function () { fsState.dateFrom = fsFmtInputDate(this.value); });
    }
    if (dt) {
        dt.addEventListener("change", function () { fsState.dateTo = fsFmtInputDate(this.value); });
    }
}

function nd_fs_boot() { /* 主应用 boot 兼容占位：独立页/主应用均可直接调用 */ }

if (typeof window !== "undefined" && typeof window.initFileSearchTab === "undefined") {
    window.initFileSearchTab = initFileSearchTab;
}
