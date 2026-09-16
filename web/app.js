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
    // 应用启动即查询平台接入状态，同步显隐锁屏及壁纸管理菜单
    appCheckUplinkForDesktopPolicy();
});

/** 启动时查询平台接入状态，用于控制锁屏及壁纸管理导航入口 */
async function appCheckUplinkForDesktopPolicy() {
    try {
        var d = await apiFetch("/api/perf/uplink/status");
        var u = d && d.uplink ? d.uplink : null;
        updateDesktopPolicyNavVisibility(u ? u.state : null);
    } catch (e) {
        updateDesktopPolicyNavVisibility(null);
    }
}

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
    /* AI 诊断卡宿主在主页（2026-09-10 自网络排障移入）：home 激活同样触发
       netdoctor 初始化（幂等）与中心连接状态刷新 */
    if ((tab === "netdoctor" || tab === "home") && typeof initNetDoctorTab === "function") initNetDoctorTab();
    if (tab === "filesearch" && typeof initFileSearchTab === "function") initFileSearchTab();
    if (tab === "desktoppolicy" && typeof initDesktopPolicyTab === "function") initDesktopPolicyTab();
}

/** 根据平台接入状态显隐「锁屏及壁纸管理」导航入口（仅已连接平台时可见） */
function updateDesktopPolicyNavVisibility(state) {
    var btn = document.getElementById("nav-desktoppolicy");
    if (!btn) return;
    var connected = (state === "connected");
    btn.style.display = connected ? "" : "none";
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

/* =====================================================================
 * uiConfirm — 全项目唯一合法对话框出口（2026-09-14 废除原生 confirm/alert）
 * ---------------------------------------------------------------------
 * 背景：WebView2 原生弹窗标题强制显示来源「127.0.0.1:<端口> 显示」（pywebview
 * 内置服务端口，每次启动变化），且位置贴顶不居中——用户要求弹窗居中于窗体
 * 中心且不得出现任何端口/服务类信息。
 * 用法：await uiConfirm({ title, message, okText="确定", cancelText="取消",
 *                          danger=false }) → Promise<boolean>
 *   - cancelText 传空串 = 单按钮提示模式（替代原生 alert），确定返回 true
 *   - danger=true 时确认键红色（破坏性操作，如压测/取消类）
 *   - ESC / 遮罩点击 = 取消（返回 false）；Enter = 确认
 * 约定：web/ 源码禁止再出现原生 confirm( / alert( 调用（本注释除外）；
 * 第二批（disk.js/appdata.js）与后续一切模块一律走本组件。
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
