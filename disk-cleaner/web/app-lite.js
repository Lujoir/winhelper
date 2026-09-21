/* ==========================================
   Disk Cleaner 独立版 - 传输层与共享工具
   (独立部署为 HTTP 服务; 保留 pywebview 桥接预留)
   ========================================== */

// ===================== 传输适配层 =====================
async function apiFetch(path) {
    // 独立项目默认 HTTP; 若未来嵌入桌面壳, 此处自动切桥接
    if (window.pywebview && window.pywebview.api) {
        return await window.pywebview.api.call(path);
    }
    const resp = await fetch(path);
    return await resp.json();
}

// 全局状态（与模块 JS 共享）
let currentPage = 1;

// DOM 就绪
document.addEventListener("DOMContentLoaded", () => {
    initCollapsibleCards();
    if (typeof initDiskTab === "function") initDiskTab();
});

// ===================== 卡片折叠/展开 =====================
function initCollapsibleCards() {
    document.querySelectorAll(".section-card[data-collapse]").forEach(card => {
        const header = card.querySelector(".card-header");
        if (!header || header.dataset.collapseInit) return;
        header.dataset.collapseInit = "1";
        header.style.cursor = "pointer";
        header.title = "点击 收纳/展开";
        header.addEventListener("click", (e) => {
            if (e.target.closest("button, select, input, a, label")) return;
            card.classList.toggle("collapsed");
            if (!card.classList.contains("collapsed") && card.querySelector("#treemap")) {
                setTimeout(() => { if (typeof renderTreemap === "function") renderTreemap(); }, 60);
            }
        });
        const chev = document.createElement("span");
        chev.className = "card-chevron";
        chev.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16"><path d="M7 10l5 5 5-5z" fill="currentColor"/></svg>';
        header.appendChild(chev);
    });
}

function toggleAllCards(btn) {
    const scope = btn.closest(".tab-content") || document;
    const cards = [...scope.querySelectorAll(".section-card[data-collapse]")];
    const anyExpanded = cards.some(c => !c.classList.contains("collapsed"));
    cards.forEach(c => c.classList.toggle("collapsed", anyExpanded));
    btn.textContent = anyExpanded ? "📂 展开全部" : "📁 收纳全部";
    if (!anyExpanded && scope.querySelector("#treemap")) {
        setTimeout(() => { if (typeof renderTreemap === "function") renderTreemap(); }, 60);
    }
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

function escapeJs(s) {
    return String(s).replace(/\\/g, "\\\\").replace(/'/g, "\\'").replace(/"/g, "&quot;");
}

function fmtNum(n) {
    return (n || 0).toLocaleString("zh-CN");
}

function shortPath(p, max) {
    max = max || 40;
    if (!p) return "";
    if (p.length <= max) return p;
    return p.substring(0, 10) + "…" + p.substring(p.length - (max - 12));
}

function humanSizeRaw(n) {
    n = parseFloat(n || 0);
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return (i === 0 ? n.toFixed(0) : n.toFixed(1)) + " " + units[i];
}

function showError(msg) {
    const div = document.createElement("div");
    div.style.cssText = `
        position:fixed; bottom:24px; right:24px; z-index:999;
        padding:14px 20px; background:#ff5252; color:#fff;
        border-radius:8px; font-size:14px; font-weight:500;
        box-shadow:0 4px 20px rgba(255,82,82,.4); max-width:400px;
    `;
    div.textContent = "❌ " + msg;
    document.body.appendChild(div);
    setTimeout(() => { div.style.opacity = "0"; div.style.transition = "opacity .3s";
        setTimeout(() => div.remove(), 300); }, 5000);
}

function showToast(msg) {
    const div = document.createElement("div");
    div.style.cssText = `
        position:fixed; bottom:24px; right:24px; z-index:999;
        padding:14px 20px; background:#2e7d32; color:#fff;
        border-radius:8px; font-size:14px; font-weight:500;
        box-shadow:0 4px 20px rgba(46,125,50,.4); max-width:420px;
    `;
    div.textContent = msg;
    document.body.appendChild(div);
    setTimeout(() => { div.style.opacity = "0"; div.style.transition = "opacity .4s";
        setTimeout(() => div.remove(), 400); }, 4500);
}

/* =====================================================================
 * uiConfirm — standalone 内联副本（2026-09-14 废除原生 confirm/alert）
 * ---------------------------------------------------------------------
 * 与主应用 web/app.js uiConfirm() 逐字同步（standalone 无 app.js，按
 * perf/net-doctor standalone 先例内联基准副本）；样式见 style.css 尾段。
 * 用法：await uiConfirm({ title, message, okText="确定", cancelText="取消",
 *                          danger=false }) → Promise<boolean>
 *   - cancelText 传空串 = 单按钮提示模式（替代原生 alert），确定返回 true
 *   - danger=true 时确认键红色（破坏性操作）
 *   - ESC / 遮罩点击 = 取消（返回 false）；Enter = 确认
 * 纯 DOM API + textContent 构建，message 动态文本零 HTML 注入面。
 * ===================================================================== */
function uiConfirm(opts) {
    return new Promise(function (resolve) {
        var title = (opts && opts.title) || "确认";
        var message = (opts && opts.message) || "";
        var okText = (opts && opts.okText) || "确定";
        var cancelText = (opts && opts.cancelText !== undefined) ? opts.cancelText : "取消";
        var danger = !!(opts && opts.danger);

        var mask = document.createElement("div");
        mask.className = "ui-confirm-mask";

        var card = document.createElement("div");
        card.className = "ui-confirm-card";
        card.setAttribute("role", "dialog");
        card.setAttribute("aria-modal", "true");

        var head = document.createElement("div");
        head.className = "ui-confirm-head";
        var h3 = document.createElement("h3");
        h3.textContent = title;
        head.appendChild(h3);

        var body = document.createElement("div");
        body.className = "ui-confirm-body";
        body.textContent = message;   // pre-line：\n 分行；textContent：零注入

        var foot = document.createElement("div");
        foot.className = "ui-confirm-foot";

        var done = false;
        function close(result) {
            if (done) return;
            done = true;
            document.removeEventListener("keydown", onKey, true);
            mask.remove();
            resolve(result);
        }
        function onKey(e) {
            if (e.key === "Escape") { e.stopPropagation(); close(false); }
            else if (e.key === "Enter") { e.stopPropagation(); close(true); }
        }

        var btnCancel = null;
        if (cancelText) {
            btnCancel = document.createElement("button");
            btnCancel.className = "btn btn-ghost ui-confirm-btn";
            btnCancel.textContent = cancelText;
            btnCancel.addEventListener("click", function () { close(false); });
            foot.appendChild(btnCancel);
        }

        var btnOk = document.createElement("button");
        btnOk.className = "btn btn-primary ui-confirm-btn" + (danger ? " ui-confirm-ok-danger" : "");
        btnOk.textContent = okText;
        btnOk.addEventListener("click", function () { close(true); });
        foot.appendChild(btnOk);

        mask.addEventListener("mousedown", function (e) {
            if (e.target === mask) close(false);   // 仅遮罩本体，卡片内点击不关闭
        });

        card.appendChild(head);
        card.appendChild(body);
        card.appendChild(foot);
        mask.appendChild(card);
        document.body.appendChild(mask);

        document.addEventListener("keydown", onKey, true);
        btnOk.focus();
    });
}
