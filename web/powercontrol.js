/* powercontrol.js · 自动开关机（观枢终端平台｜EyeTerm）
 * pc 前缀隔离；卡片基线见 net-doctor/docs/STYLE.md；文案零实现细节。
 * 数据源：/api/powercontrol/snapshot、/api/powercontrol/report
 * （宿主 apiFetch → pywebview → fetch 三级回退）。
 */
"use strict";

var pcState = { busy: false, loaded: false, lastSnap: null };

/* DOM 辅助（自包含，不依赖宿主 $） */
function pc$(sel) { return document.querySelector(sel); }

function pcEsc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

function pcTrunc(s, n) {
  s = String(s == null ? "" : s);
  return s.length > n ? s.substring(0, n) + "…" : s;
}

function pcBadge(text, cls) {
  return '<span class="nd-badge ' + (cls || "nd-muted") + '">' +
    pcEsc(text) + "</span>";
}

function pcApiFetch(path, body) {
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

function initPowerControlTab() {
  if (pcState.busy) return;
  pcLoadSnapshot();
}

function pcSetBusy(b) {
  pcState.busy = b;
  ["#pcBtnRefresh", "#pcBtnReport"].forEach(function (sel) {
    var el = pc$(sel);
    if (el) el.disabled = b;
  });
}

function pcLoadSnapshot() {
  if (pcState.busy) return Promise.resolve();
  pcSetBusy(true);
  ["pcBiosBody", "pcWakeBody", "pcTasksBody", "pcFastBody"]
    .forEach(function (id) {
      var el = pc$("#" + id);
      if (el) el.innerHTML = '<div class="nd-empty">正在读取本机配置…</div>';
    });
  var errEl = pc$("#pcErr");
  if (errEl) errEl.textContent = "";
  return pcApiFetch("/api/powercontrol/snapshot").then(function (data) {
    if (!data || data.success === false || !data.snapshot) {
      throw new Error((data && data.error) || "快照读取失败");
    }
    pcState.loaded = true;
    pcState.lastSnap = data.snapshot;
    pcRenderSnapshot(data.snapshot);
  }).catch(function (e) {
    ["pcBiosBody", "pcWakeBody", "pcTasksBody", "pcFastBody"]
      .forEach(function (id) {
        var el = pc$("#" + id);
        if (el) el.innerHTML = '<div class="nd-empty">读取失败（可点击刷新重试）</div>';
      });
    if (errEl) errEl.textContent = "读取失败：" + pcTrunc((e && e.message) || e, 120);
  }).then(function () { pcSetBusy(false); });
}

function pcRenderSnapshot(snap) {
  var m = snap.machine || {};
  var mb = pc$("#pcMachineBadge");
  if (mb) {
    mb.textContent = m.system_family || m.model || m.manufacturer || "未知机型";
  }
  var cb = pc$("#pcCapBadge");
  if (cb) {
    if (m.capability === "enterprise_configurable") {
      cb.textContent = "企业线可配置";
      cb.className = "nd-badge nd-ok";
    } else {
      cb.textContent = "不支持远程配置";
      cb.className = "nd-badge nd-warn";
    }
  }
  var ct = pc$("#pcCollectTime");
  if (ct) ct.textContent = snap.collected_at || "—";
  pcRenderBios(snap);
  pcRenderWake(snap);
  pcRenderTasks(snap);
  pcRenderFast(snap);
}

function pcRenderBios(snap) {
  var host = pc$("#pcBiosBody");
  if (!host) return;
  var b = snap.bios || {}, m = snap.machine || {}, rtc = b.rtc || {};
  var html = [];
  if (m.capability === "enterprise_configurable") {
    html.push('<div class="hm-row"><span class="hm-row-label">定时开机</span>' +
      '<span class="hm-row-value">' +
      pcBadge(rtc.alarm_on ? "已开启" : "已关闭",
              rtc.alarm_on ? "nd-ok" : "nd-muted") + "</span></div>");
    if (rtc.alarm_on && rtc.summary) {
      html.push('<div class="hm-row"><span class="hm-row-label">计划摘要</span>' +
        '<span class="hm-row-value">' + pcEsc(rtc.summary) + "</span></div>");
    }
    if (rtc.cycle_text) {
      html.push('<div class="hm-row"><span class="hm-row-label">重复周期</span>' +
        '<span class="hm-row-value">' + pcEsc(rtc.cycle_text) + "</span></div>");
    }
    if (rtc.time) {
      html.push('<div class="hm-row"><span class="hm-row-label">开机时间</span>' +
        '<span class="hm-row-value nd-num">' + pcEsc(rtc.time) + "</span></div>");
    }
    if (rtc.alarm === "User Defined" && rtc.user_time) {
      html.push('<div class="hm-row"><span class="hm-row-label">自定义时刻</span>' +
        '<span class="hm-row-value nd-num">' + pcEsc(rtc.user_time) + "</span></div>");
    }
    if (rtc.date) {
      html.push('<div class="hm-row"><span class="hm-row-label">指定日期</span>' +
        '<span class="hm-row-value nd-num">' + pcEsc(rtc.date) + "</span></div>");
    }
    if (rtc.day) {
      html.push('<div class="hm-row"><span class="hm-row-label">指定星期</span>' +
        '<span class="hm-row-value">' + pcEsc(rtc.day) + "</span></div>");
    }
    if (rtc.after_power_loss) {
      html.push('<div class="hm-row"><span class="hm-row-label">来电恢复</span>' +
        '<span class="hm-row-value">' + pcEsc(rtc.after_power_loss) + "</span></div>");
    }
    if (rtc.wake_on_lan) {
      html.push('<div class="hm-row"><span class="hm-row-label">网络唤醒</span>' +
        '<span class="hm-row-value">' + pcEsc(rtc.wake_on_lan) + "</span></div>");
    }
    html.push('<div class="nd-hint" style="margin-top:8px">以上为开机配置的当前状态（只读展示）。</div>');
  } else {
    html.push('<div class="nd-empty">' +
      pcEsc(b.reason || "本机不支持远程配置定时开机") + "</div>");
    html.push('<div class="nd-hint">如需定时开机：开机自检时按屏幕提示进入 BIOS 设置，' +
      "在电源管理菜单中查找「定时开机 / RTC Alarm / Wake Up on Alarm」类选项进行设置。</div>");
  }
  host.innerHTML = html.join("");
}

function pcRenderWake(snap) {
  var host = pc$("#pcWakeBody");
  if (!host) return;
  var w = snap.wake_timers || {};
  if (w.need_admin) {
    host.innerHTML = '<div class="nd-empty">需管理员权限查看（当前会话未提权，不影响其它检测）</div>';
    return;
  }
  if (w.error) {
    host.innerHTML = '<div class="nd-empty">读取失败：' +
      pcEsc(pcTrunc(w.error, 120)) + "</div>";
    return;
  }
  var items = w.items || [];
  if (!items.length) {
    host.innerHTML = '<div class="nd-empty">当前没有活动的唤醒定时器</div>';
    return;
  }
  var typeText = function (t) {
    if (t === "SERVICE") return "系统服务";
    if (t === "PROCESS") return "进程";
    if (t === "DEVICE") return "设备";
    return "—";
  };
  var rows = items.map(function (t, i) {
    return "<tr><td class=\"nd-num\">" + (i + 1) + "</td><td>" +
      pcEsc(typeText(t.type)) + "</td><td>" +
      pcEsc(pcTrunc(t.owner || "—", 64)) + "</td><td class=\"nd-num\">" +
      pcEsc(t.wake_time || "—") + "</td><td>" +
      pcEsc(pcTrunc(t.reason || t.description || "—", 80)) + "</td></tr>";
  }).join("");
  host.innerHTML = '<p class="nd-summary">允许把电脑从睡眠中唤醒的计划（系统与软件登记的唤醒来源）。</p>' +
    '<table class="nd-table"><thead><tr><th>#</th><th>来源类型</th><th>发起方</th>' +
    '<th>唤醒时间</th><th>说明</th></tr></thead><tbody>' + rows + "</tbody></table>";
}

function pcRenderTasks(snap) {
  var host = pc$("#pcTasksBody");
  if (!host) return;
  var t = snap.shutdown_tasks || {};
  if (t.error) {
    host.innerHTML = '<div class="nd-empty">读取失败：' +
      pcEsc(pcTrunc(t.error, 120)) + "</div>";
    return;
  }
  var items = t.items || [];
  if (!items.length) {
    host.innerHTML = '<div class="nd-empty">未发现关机类计划任务</div>';
    return;
  }
  var rows = items.map(function (x, i) {
    return "<tr><td class=\"nd-num\">" + (i + 1) + "</td><td>" +
      pcEsc(pcTrunc(x.name || "—", 40)) + "</td><td>" +
      pcEsc(x.schedule_type || "—") + "</td><td class=\"nd-num\">" +
      pcEsc(x.next_run || "—") + "</td><td>" +
      pcEsc(x.status || "—") + "</td><td>" +
      pcEsc(pcTrunc(x.action || "—", 60)) + "</td></tr>";
  }).join("");
  host.innerHTML = '<p class="nd-summary">本机已登记的关机类计划任务（只读列表，不会改动）。</p>' +
    '<table class="nd-table"><thead><tr><th>#</th><th>任务名</th><th>重复</th>' +
    '<th>下次运行</th><th>状态</th><th>执行内容</th></tr></thead><tbody>' +
    rows + "</tbody></table>";
}

function pcRenderFast(snap) {
  var host = pc$("#pcFastBody");
  if (!host) return;
  var f = snap.fast_startup || {};
  var cls, text;
  if (f.enabled === true) { cls = "nd-ok"; text = "已开启"; }
  else if (f.enabled === false) { cls = "nd-warn"; text = "已关闭"; }
  else { cls = "nd-muted"; text = "未知"; }
  var html = ['<div class="hm-row"><span class="hm-row-label">快速启动</span>' +
    '<span class="hm-row-value">' + pcBadge(text, cls) + "</span></div>"];
  if (f.note) {
    html.push('<div class="nd-hint" style="margin-top:6px">' + pcEsc(f.note) + "</div>");
  }
  html.push('<div class="nd-hint" style="margin-top:2px">快速开启会让「关机」进入混合休眠：部分场景下定时开机与网络唤醒可能不生效，按需关闭。</div>');
  host.innerHTML = html.join("");
}

function pcReportNow() {
  if (pcState.busy) return;
  pcSetBusy(true);
  var tip = pc$("#pcReportTip");
  if (tip) tip.textContent = "正在上报…";
  return pcApiFetch("/api/powercontrol/report").then(function (data) {
    if (!data || data.success === false) {
      throw new Error((data && data.error) || "上报失败");
    }
    if (tip) {
      tip.textContent = "已上报存档（" +
        new Date().toLocaleTimeString("zh-CN", { hour12: false }) + "）";
    }
    var rs = pc$("#pcReportState");
    if (rs) rs.textContent = "已存档";
  }).catch(function (e) {
    if (tip) tip.textContent = pcTrunc((e && e.message) || "上报失败", 140);
  }).then(function () { pcSetBusy(false); });
}
