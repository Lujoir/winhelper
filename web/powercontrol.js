/* powercontrol.js · 自动开关机（观枢终端平台｜EyeTerm）
 * pc 前缀隔离；卡片基线见 net-doctor/docs/STYLE.md；文案零实现细节。
 * 数据源：/api/powercontrol/snapshot、/api/powercontrol/report
 * （宿主 apiFetch → pywebview → fetch 三级回退）。
 */
"use strict";

var pcState = { busy: false, loaded: false, lastSnap: null,
                reportState: null, opBusy: false, sdTask: null,
                activePolicy: null };

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
    pcState.reportState = data.report_state || null;
    pcState.activePolicy = data.active_policy || null;
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
  var cfgCard = pc$("#pcBiosCfgCard");
  if (cfgCard) {
    cfgCard.style.display =
        (m.capability === "enterprise_configurable") ? "" : "none";
  }
  pcRenderBios(snap);
  pcRenderWake(snap);
  pcRenderTasks(snap);
  pcRenderFast(snap);
  pcRenderReportState();
  pcRenderShutdownCurrent(snap);
  pcRenderPolicy();
}

function pcRenderPolicy() {
  var el = pc$("#pcPolicyLine");
  if (!el) return;
  var pol = pcState.activePolicy;
  if (pol && pol.policy_id) {
    el.style.display = "";
    var tail = String(pol.policy_id).slice(-8);
    el.innerHTML = pcBadge("平台下发生效", "nd-info") +
      '<span class="nd-hint" style="margin-left:6px">' +
      pcEsc(pcTrunc(pol.summary || "", 80)) +
      " · 策略尾号 " + pcEsc(tail) +
      (pol.applied_at ? " · " + pcEsc(pol.applied_at) : "") +
      "（本机设置与平台下发，后到者生效）</span>";
  } else {
    el.style.display = "none";
    el.textContent = "";
  }
}

function pcRenderReportState() {
  var rs = pc$("#pcReportState");
  if (!rs) return;
  var st = pcState.reportState;
  if (st && st.last_ok) {
    var hhmm = (st.last_at || "").split(" ").pop() || st.last_at;
    rs.textContent = "已存档 " + hhmm;
  } else if (st && st.last_error) {
    rs.textContent = "上次上报未成功（打开本页自动重试）";
  } else {
    rs.textContent = "未上报（打开本页自动上报，需已接入平台）";
  }
}

function pcRenderShutdownCurrent(snap) {
  var el = pc$("#pcSdCurrent");
  if (!el) return;
  var items = ((snap.shutdown_tasks || {}).items) || [];
  var hit = null;
  for (var i = 0; i < items.length; i++) {
    if (String(items[i].name || "").indexOf("EyeTermAutoShutdown") === 0) {
      hit = items[i];
      break;
    }
  }
  pcState.sdTask = hit;
  if (!hit) {
    el.innerHTML = pcBadge("未创建", "nd-muted");
    return;
  }
  var on = (hit.status || "").indexOf("就绪") >= 0 ||
           (hit.status || "").indexOf("Ready") >= 0 ||
           (hit.status || "").indexOf("运行") >= 0;
  el.innerHTML = pcBadge(on ? "已启用" : "已停用", on ? "nd-ok" : "nd-warn") +
    '<span class="nd-hint"> ' + pcEsc(hit.next_run || "—") + "</span>";
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
    if (b.human_set_flag) {
      html.push('<div class="nd-hint" style="margin-top:6px">' +
        pcBadge("已登记人工设置", "nd-info") +
        '<span style="margin-left:6px">' + pcEsc(b.human_set_at || "") +
        "（平台可见）</span></div>");
    } else {
      html.push('<div style="margin-top:8px">' +
        '<button class="nd-btn" onclick="pcRegisterHumanSet()">' +
        "我已在 BIOS 人工设置，登记到平台</button>" +
        '<span class="nd-hint" id="pcHumanTip" style="margin-left:8px"></span></div>');
    }
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
    pcState.reportState = { last_ok: true,
                            last_at: new Date().toLocaleString("zh-CN",
                                { hour12: false }) };
    pcRenderReportState();
  }).catch(function (e) {
    if (tip) tip.textContent = pcTrunc((e && e.message) || "上报失败", 140);
  }).then(function () { pcSetBusy(false); });
}

/* ======================================================================
 * P1 · 定时开机配置 / 定时关机操作（提权异步任务；UAC 确认制）
 * ==================================================================== */

var _PC_OP_BTNS = ["#pcBtnBiosApply", "#pcBtnBiosRestore", "#pcBtnSdSave",
                   "#pcBtnSdToggle", "#pcBtnSdRemove"];

function pcSetOpBusy(b) {
  pcState.opBusy = b;
  _PC_OP_BTNS.forEach(function (sel) {
    var el = pc$(sel);
    if (el) el.disabled = b;
  });
}

function pcOnBootModeChange() {
  var m = pc$("#pcBootMode"), d = pc$("#pcBootDate");
  if (d) d.style.display = (m && m.value === "single") ? "" : "none";
}

function pcOnSdModeChange() {
  var m = pc$("#pcSdMode"), d = pc$("#pcSdDate");
  if (d) d.style.display = (m && m.value === "single") ? "" : "none";
}

function pcPollTask(tid, tip, doneText) {
  return new Promise(function (resolve) {
    var poll = function () {
      pcApiFetch("/api/powercontrol/task-status?task_id=" +
        encodeURIComponent(tid)).then(function (data) {
        var t = (data && data.task) || {};
        if (data && data.success === false) {
          tip.textContent = pcTrunc(data.error || "任务查询失败", 140);
          pcSetOpBusy(false);
          return resolve(null);
        }
        if (t.status === "running") { setTimeout(poll, 1500); return; }
        pcSetOpBusy(false);
        var r = t.result || {};
        if (t.status === "done") {
          tip.textContent = doneText;
        } else {
          tip.textContent = pcTrunc(r.error || "操作未成功，请重试", 160);
        }
        resolve(r);
      }).catch(function (e) {
        tip.textContent = pcTrunc((e && e.message) || "任务查询失败", 140);
        pcSetOpBusy(false);
        resolve(null);
      });
    };
    setTimeout(poll, 600);
  });
}

function pcApplyBios() {
  if (pcState.opBusy) return;
  var mode = (pc$("#pcBootMode") || {}).value || "daily";
  var time = (pc$("#pcBootTime") || {}).value || "";
  var date = (pc$("#pcBootDate") || {}).value || "";
  if (mode !== "off" && !/^\d{1,2}:\d{2}$/.test(time.trim())) {
    pc$("#pcBiosTip").textContent = "请填写开机时刻（如 08:00）";
    return;
  }
  if (mode === "single" && !date.trim()) {
    pc$("#pcBiosTip").textContent = "请填写指定日期（如 12/31/2026）";
    return;
  }
  if (!window.confirm(
      mode === "off"
        ? "将停用本机的定时开机（应用前自动备份当前配置）。继续？"
        : "将按以下配置修改定时开机（应用前自动备份当前配置，需系统授权确认）：\n\n" +
          "周期：" + pc$("#pcBootMode").selectedOptions[0].text +
          "\n时刻：" + (mode === "off" ? "—" : time) +
          (mode === "single" ? ("\n日期：" + date) : "") + "\n\n继续？")) {
    return;
  }
  pcSetOpBusy(true);
  var tip = pc$("#pcBiosTip");
  tip.textContent = "已受理，等待系统授权与执行…（若未见弹窗请在任务栏确认）";
  pcApiFetch("/api/powercontrol/bios-apply",
             { mode: mode, time: time.trim(), date: date.trim() })
    .then(function (data) {
      if (!data || data.success === false) {
        throw new Error((data && data.error) || "提交失败");
      }
      return pcPollTask(data.task_id, tip, "已应用：当前配置见上方卡片");
    })
    .then(function (r) { if (r) pcLoadSnapshot(); })
    .catch(function (e) {
      tip.textContent = pcTrunc((e && e.message) || "提交失败", 140);
      pcSetOpBusy(false);
    });
}

function pcRestoreBios() {
  if (pcState.opBusy) return;
  if (!window.confirm(
      "将把定时开机配置还原为最近一次备份的初始值（需系统授权确认）。继续？")) {
    return;
  }
  pcSetOpBusy(true);
  var tip = pc$("#pcBiosTip");
  tip.textContent = "已受理，等待系统授权与执行…";
  pcApiFetch("/api/powercontrol/bios-restore", {})
    .then(function (data) {
      if (!data || data.success === false) {
        throw new Error((data && data.error) || "提交失败");
      }
      return pcPollTask(data.task_id, tip, "已还原为初始值");
    })
    .then(function (r) { if (r) pcLoadSnapshot(); })
    .catch(function (e) {
      tip.textContent = pcTrunc((e && e.message) || "提交失败", 140);
      pcSetOpBusy(false);
    });
}

function pcSaveShutdown() {
  if (pcState.opBusy) return;
  var mode = (pc$("#pcSdMode") || {}).value || "daily";
  var time = (pc$("#pcSdTime") || {}).value || "";
  var date = (pc$("#pcSdDate") || {}).value || "";
  if (!/^\d{1,2}:\d{2}$/.test(time.trim())) {
    pc$("#pcSdTip").textContent = "请填写关机时刻（如 22:00）";
    return;
  }
  if (mode === "single" && !date.trim()) {
    pc$("#pcSdTip").textContent = "请填写指定日期（如 12/31/2026）";
    return;
  }
  if (!window.confirm(
      "将创建/更新定时关机任务（需系统授权确认）：\n\n" +
      "周期：" + pc$("#pcSdMode").selectedOptions[0].text +
      "\n时刻：" + time +
      (mode === "single" ? ("\n日期：" + date) : "") +
      "\n\n到点后倒计时 60 秒关机。继续？")) {
    return;
  }
  pcSetOpBusy(true);
  var tip = pc$("#pcSdTip");
  tip.textContent = "已受理，等待系统授权与执行…";
  pcApiFetch("/api/powercontrol/shutdown-set",
             { mode: mode, time: time.trim(), date: date.trim() })
    .then(function (data) {
      if (!data || data.success === false) {
        throw new Error((data && data.error) || "提交失败");
      }
      return pcPollTask(data.task_id, tip, "定时关机已保存并启用");
    })
    .then(function (r) { if (r) pcLoadSnapshot(); })
    .catch(function (e) {
      tip.textContent = pcTrunc((e && e.message) || "提交失败", 140);
      pcSetOpBusy(false);
    });
}

function pcToggleShutdown() {
  if (pcState.opBusy) return;
  var enable = !(pcState.sdTask &&
                 ((pcState.sdTask.status || "").indexOf("就绪") >= 0 ||
                  (pcState.sdTask.status || "").indexOf("Ready") >= 0 ||
                  (pcState.sdTask.status || "").indexOf("运行") >= 0));
  pcSetOpBusy(true);
  var tip = pc$("#pcSdTip");
  tip.textContent = "已受理，等待系统授权与执行…";
  pcApiFetch("/api/powercontrol/shutdown-toggle", { enable: enable })
    .then(function (data) {
      if (!data || data.success === false) {
        throw new Error((data && data.error) || "提交失败");
      }
      return pcPollTask(data.task_id, tip,
                        enable ? "定时关机任务已启用" : "定时关机任务已停用");
    })
    .then(function (r) { if (r) pcLoadSnapshot(); })
    .catch(function (e) {
      tip.textContent = pcTrunc((e && e.message) || "提交失败", 140);
      pcSetOpBusy(false);
    });
}

function pcRemoveShutdown() {
  if (pcState.opBusy) return;
  if (!window.confirm("将删除定时关机任务。继续？")) return;
  pcSetOpBusy(true);
  var tip = pc$("#pcSdTip");
  tip.textContent = "已受理，等待系统授权与执行…";
  pcApiFetch("/api/powercontrol/shutdown-remove", {})
    .then(function (data) {
      if (!data || data.success === false) {
        throw new Error((data && data.error) || "提交失败");
      }
      return pcPollTask(data.task_id, tip, "定时关机任务已删除");
    })
    .then(function (r) { if (r) pcLoadSnapshot(); })
    .catch(function (e) {
      tip.textContent = pcTrunc((e && e.message) || "提交失败", 140);
      pcSetOpBusy(false);
    });
}

function pcRegisterHumanSet() {
  var tip = pc$("#pcHumanTip");
  if (tip) tip.textContent = "正在登记…";
  return pcApiFetch("/api/powercontrol/report", { human_set: true })
    .then(function (data) {
      if (!data || data.success === false) {
        throw new Error((data && data.error) || "登记失败");
      }
      if (tip) tip.textContent = "已登记（平台可见）";
      pcLoadSnapshot();
    })
    .catch(function (e) {
      if (tip) tip.textContent = pcTrunc((e && e.message) || "登记失败", 120);
    });
}
