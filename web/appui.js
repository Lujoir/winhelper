/* appui.js · 客户端控制 UI（三大改造②③，2026-09-17）
 * ac 前缀=开机自启设置卡；ub 前缀=更新提示条。ADR-011/012。
 * 宿主 apiFetch 存在则复用（app.js 已支持 body 透传）；独立环境回退 pywebview/fetch。
 */
"use strict";

var acState = { pollTimer: null, busy: false };

function ac$(sel) { return document.querySelector(sel); }

function acEsc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

function acApiFetch(path, body) {
  if (typeof window.apiFetch === "function") {
    return window.apiFetch(path, body);
  }
  if (window.pywebview && window.pywebview.api && window.pywebview.api.call) {
    return Promise.resolve(
      window.pywebview.api.call(path, body ? JSON.stringify(body) : null));
  }
  return fetch(path, {
    method: body ? "POST" : "GET",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  }).then(function (r) { return r.json(); });
}

/* ---------------- 开机自启（设置 · 客户端卡） ---------------- */

function acRenderAutostart(enabled) {
  var badge = ac$("#acAutostartBadge");
  var btn = ac$("#acAutostartBtn");
  if (badge) {
    badge.textContent = enabled ? "已开启" : "未开启";
    badge.className = "nd-badge " + (enabled ? "nd-ok" : "nd-muted");
  }
  if (btn) btn.textContent = enabled ? "关闭开机自启" : "开启开机自启";
  acState.enabled = !!enabled;
}

function acLoadAutostart() {
  var badge = ac$("#acAutostartBadge");
  if (!badge) return;
  return acApiFetch("/api/app/autostart").then(function (d) {
    if (d && d.success === false) {
      badge.textContent = "读取失败";
      return;
    }
    acRenderAutostart(!!(d && d.enabled));
  }).catch(function () {
    badge.textContent = "读取失败";
  });
}

function acToggleAutostart() {
  if (acState.busy) return;
  acState.busy = true;
  var tip = ac$("#acAutostartTip");
  if (tip) tip.textContent = "正在保存…";
  var target = !acState.enabled;
  return acApiFetch("/api/app/autostart-set", { enabled: target })
    .then(function (d) {
      if (d && d.success === false) {
        throw new Error((d && d.error) || "保存失败");
      }
      acRenderAutostart(!!(d && d.enabled));
      if (tip) {
        tip.textContent = d && d.enabled
          ? "已开启（下次开机自动启动，托盘常驻）"
          : "已关闭";
      }
    })
    .catch(function (e) {
      if (tip) tip.textContent = acEsc(String((e && e.message) || e)).slice(0, 80);
    })
    .then(function () { acState.busy = false; });
}

/* ---------------- 更新提示条 ---------------- */

function ubShow(version) {
  var b = ac$("#ubBanner");
  if (!b) return;
  var v = ac$("#ubVer");
  if (v) v.textContent = version;
  b.style.display = "flex";
  var tip = ac$("#ubTip");
  if (tip) tip.textContent = "";
  var btn = ac$("#ubApplyBtn");
  if (btn) btn.disabled = false;
}

function ubHide() {
  var b = ac$("#ubBanner");
  if (b) b.style.display = "none";
}

function ubDismiss() {
  var v = (acState.pendingVersion || "") + "";
  try { sessionStorage.setItem("ubDismissedVer", v); } catch (e) {}
  ubHide();
}

function ubApplyNow() {
  if (acState.busy) return;
  acState.busy = true;
  var btn = ac$("#ubApplyBtn");
  var tip = ac$("#ubTip");
  if (btn) btn.disabled = true;
  if (tip) tip.textContent = "正在准备更新（本窗口将自动关闭并安装）…";
  return acApiFetch("/api/app/update-apply", {}).then(function (d) {
    if (!d || d.success === false) {
      throw new Error((d && d.error) || "无法启动更新");
    }
    if (tip) tip.textContent = "正在退出并安装更新，完成后自动重新打开…";
  }).catch(function (e) {
    if (tip) tip.textContent = String((e && e.message) || e).slice(0, 90);
    if (btn) btn.disabled = false;
  }).then(function () { acState.busy = false; });
}

function ubPoll() {
  return acApiFetch("/api/app/update-status").then(function (d) {
    var u = (d && d.update) || {};
    var dismissed = "";
    try { dismissed = sessionStorage.getItem("ubDismissedVer") || ""; }
    catch (e) {}
    acState.pendingVersion = u.version || "";
    if (u.status === "ready" && u.version && dismissed !== u.version) {
      ubShow(u.version);
    } else if (u.status !== "ready") {
      ubHide();
    }
  }).catch(function () {});
}

function initAppUi() {
  // 自启卡：设置弹窗打开时刷新状态（MutationObserver，零侵入 perf.js 的 openAppSettings）
  var ov = ac$("#appSettingsOverlay");
  if (ov && window.MutationObserver) {
    new MutationObserver(function () {
      if (ov.style.display === "flex") acLoadAutostart();
    }).observe(ov, { attributes: true, attributeFilter: ["style"] });
  }
  acLoadAutostart();
  if (acState.pollTimer) clearInterval(acState.pollTimer);
  acState.pollTimer = setInterval(ubPoll, 30000);
  ubPoll();
}
