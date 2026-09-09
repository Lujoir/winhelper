/* ==========================================
   观枢终端平台｜EyeTerm - 前端交互逻辑
   ==========================================
   传输适配层（双模式共用同一套UI逻辑）:
     C/S 桌面模式: window.pywebview.api  -> JS桥接直连本地引擎 (无HTTP)
     B/S Web模式  : fetch()              -> HTTP请求Flask服务
   ========================================== */

// ===================== 传输适配层 =====================

/**
 * 等待 pywebview 桥接注入（桌面模式下 pywebview 会在页面加载后注入 window.pywebview）
 * @param {number} timeout 最长等待毫秒数
 * @returns {Promise<boolean>} 是否存在桌面桥接
 */
function waitPywebviewBridge(timeout = 2500) {
    return new Promise((resolve) => {
        if (window.pywebview && window.pywebview.api) { resolve(true); return; }
        const started = Date.now();
        const timer = setInterval(() => {
            if (window.pywebview && window.pywebview.api) {
                clearInterval(timer); resolve(true);
            } else if (Date.now() - started > timeout) {
                clearInterval(timer); resolve(false);
            }
        }, 100);
    });
}

/**
 * 统一数据通道：自动选择 桥接直连(C/S) 或 HTTP(B/S)
 * @param {string} path 例如 '/api/analyze?type=System&max=1000'
 * @returns {Promise<object>} 后端返回的JSON对象
 */
async function apiFetch(path) {
    // 2026-09-09 冻结缺陷修复：pywebview IPC 通道挂死时调用永挂起，
    // 上层静默 catch 导致 UI 永冻首帧。此处统一加超时保护（默认 15s，
    // window.__apiFetchTimeout 可覆盖供 E2E 注入），超时 reject 交上层错误路径。
    const timeoutMs = window.__apiFetchTimeout || 15000;
    const hasBridge = await waitPywebviewBridge(1500);
    let call;
    if (hasBridge) {
        call = window.pywebview.api.call(path);
        call.catch(() => {});   // 挂起原 promise 落超时后 reject，防 unhandledrejection
    } else {
        call = fetch(path).then(r => r.json());
    }
    return await Promise.race([
        call,
        new Promise((_, rej) => setTimeout(
            () => rej(new Error("api_timeout_" + timeoutMs + "ms")), timeoutMs))
    ]);
}

/** 标识终端类型（观枢终端平台：Windows + 安卓统一管理；modeBadge 同时作为日志诊断「本机信息」入口） */
async function detectMode() {
    const badge = document.getElementById("modeBadge");
    if (!badge) return;
    badge.textContent = "Windows";
}

// DOM 就绪
document.addEventListener("DOMContentLoaded", () => {
    detectMode();
    initCollapsibleCards();
    // 默认加载日志诊断（菜单合并后首个标签页）
    if (typeof initLogInspectorTab === "function") initLogInspectorTab();
});

// ===================== 卡片折叠/展开（一键收纳） =====================

/** 为所有带 data-collapse 的 section-card 注入点击折叠与箭头指示 */
function initCollapsibleCards() {
    document.querySelectorAll(".section-card[data-collapse]").forEach(card => {
        const header = card.querySelector(".card-header");
        if (!header || header.dataset.collapseInit) return;
        header.dataset.collapseInit = "1";
        header.style.cursor = "pointer";
        header.title = "点击 收纳/展开";
        header.addEventListener("click", (e) => {
            // 点击标题栏内的按钮/下拉框时不触发折叠
            if (e.target.closest("button, select, input, a, label")) return;
            card.classList.toggle("collapsed");
            // 展开含仪表盘的卡片后重绘（clientWidth 恢复后重算布局）
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

/** 一键收纳/展开当前标签页内的全部卡片 */
function toggleAllCards(btn) {
    const scope = btn.closest(".tab-content") || document;
    const cards = [...scope.querySelectorAll(".section-card[data-collapse]")];
    const anyExpanded = cards.some(c => !c.classList.contains("collapsed"));
    cards.forEach(c => c.classList.toggle("collapsed", anyExpanded));
    btn.textContent = anyExpanded ? "📂 展开全部" : "📁 收纳全部";
    // 展开操作后重绘仪表盘
    if (!anyExpanded && scope.querySelector("#treemap")) {
        setTimeout(() => { if (typeof renderTreemap === "function") renderTreemap(); }, 60);
    }
}

// ===================== Tab 切换 =====================
function switchTab(tab) {
    document.querySelectorAll(".nav-tab").forEach(el => el.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach(el => el.classList.remove("active"));
    document.querySelector(`[data-tab="${tab}"]`).classList.add("active");
    document.getElementById(`tab-${tab}`).classList.add("active");

    if (tab === "home" && typeof initHomeTab === "function") initHomeTab();
    if (tab === "loginspector" && typeof initLogInspectorTab === "function") initLogInspectorTab();
    if (tab === "disk" && typeof initDiskTab === "function") initDiskTab();
    if (tab === "perf" && typeof initPerfTab === "function") initPerfTab();
    if (tab === "netdoctor" && typeof initNetDoctorTab === "function") initNetDoctorTab();
}

/* 旧 仪表盘/日志查看/故障分析/知识库 四标签的专属逻辑已随菜单合并移除
   （ADR-001）：业务由 web/loginspector.js 的日志诊断五步流程承接；
   传输层（apiFetch/waitPywebviewBridge/detectMode）、卡片收纳、
   公共工具（truncate/escapeHtml/showError）与 disk/perf 分支保持不动。 */

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
