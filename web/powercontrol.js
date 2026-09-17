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

/* pcConfirm — 统一确认对话框（2026-09-17 废除原生 confirm，对齐 app.js uiConfirm 规范）
 * 主应用：委托 app.js 的 uiConfirm（ui-confirm-* 深色样式）；
 * 独立页：无 app.js，走下方内联样式兜底（DOM 类名与 uiConfirm 一致，便于 E2E 复用）。 */
function pcConfirm(opts) {
  if (typeof window.uiConfirm === "function") return window.uiConfirm(opts);
  return new Promise(function (resolve) {
    var o = opts || {};
    var mask = document.createElement("div");
    mask.className = "ui-confirm-mask";
    mask.style.cssText = "position:fixed;inset:0;z-index:1200;background:rgba(6,8,12,.72);" +
      "display:flex;align-items:center;justify-content:center;";
    var card = document.createElement("div");
    card.className = "ui-confirm-card";
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");
    card.style.cssText = "background:#171a23;border:1px solid #262b3a;border-radius:10px;" +
      "min-width:320px;max-width:460px;max-height:70vh;overflow:auto;box-shadow:0 12px 40px rgba(0,0,0,.5);";
    var head = document.createElement("div");
    head.className = "ui-confirm-head";
    head.style.cssText = "padding:12px 16px;border-bottom:1px solid #262b3a;" +
      "font-size:13.5px;font-weight:600;color:#4fc3f7;letter-spacing:.5px;";
    head.textContent = o.title || "确认";
    var body = document.createElement("div");
    body.className = "ui-confirm-body";
    body.style.cssText = "padding:14px 16px;font-size:13px;line-height:1.7;" +
      "color:#e8ebf2;white-space:pre-line;";
    body.textContent = o.message || "";
    var foot = document.createElement("div");
    foot.className = "ui-confirm-foot";
    foot.style.cssText = "display:flex;justify-content:flex-end;gap:8px;" +
      "padding:12px 16px;border-top:1px solid #262b3a;";
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
    var cancelText = (o.cancelText !== undefined) ? o.cancelText : "取消";
    if (cancelText) {
      var bC = document.createElement("button");
      bC.type = "button";
      bC.className = "btn btn-ghost ui-confirm-btn";
      bC.style.cssText = "background:#1d212e;color:#8a93a5;border:1px solid #262b3a;" +
        "border-radius:8px;padding:6px 16px;cursor:pointer;font-size:12.5px;";
      bC.textContent = cancelText;
      bC.addEventListener("click", function () { close(false); });
      foot.appendChild(bC);
    }
    var bO = document.createElement("button");
    bO.type = "button";
    bO.className = "btn btn-primary ui-confirm-btn" + (o.danger ? " ui-confirm-ok-danger" : "");
    bO.style.cssText = o.danger
      ? "background:rgba(239,83,80,.16);color:#ef5350;border:1px solid rgba(239,83,80,.5);" +
        "border-radius:8px;padding:6px 16px;cursor:pointer;font-size:12.5px;"
      : "background:rgba(79,195,247,.14);color:#4fc3f7;border:1px solid rgba(79,195,247,.45);" +
        "border-radius:8px;padding:6px 16px;cursor:pointer;font-size:12.5px;";
    bO.textContent = o.okText || "确定";
    bO.addEventListener("click", function () { close(true); });
    foot.appendChild(bO);
    mask.addEventListener("mousedown", function (e) {
      if (e.target === mask) close(false);   // 仅遮罩本体，卡片内点击不关闭
    });
    document.addEventListener("keydown", onKey, true);
    card.appendChild(head);
    card.appendChild(body);
    card.appendChild(foot);
    mask.appendChild(card);
    document.body.appendChild(mask);
    setTimeout(function () { bO.focus(); }, 0);
  });
}

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
  ["pcBiosBody", "pcSdTasksBody"]
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
    ["pcBiosBody", "pcSdTasksBody"]
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
  /* 写入通道徽章（ADR-006 附注③：老/新接口形态随探测口径，缺失即如实提示） */
  var wb = pc$("#pcWriteBadge");
  if (wb) {
    var wi = (snap.bios || {}).write_iface;
    if (m.capability === "enterprise_configurable" && wi === "new") {
      wb.textContent = "写入：新一代通道";
      wb.className = "nd-badge nd-ok";
      wb.style.display = "";
    } else if (m.capability === "enterprise_configurable" && wi === "old") {
      wb.textContent = "写入：标准通道";
      wb.className = "nd-badge nd-ok";
      wb.style.display = "";
    } else if (m.capability === "enterprise_configurable") {
      wb.textContent = "写入：待确认";
      wb.className = "nd-badge nd-warn";
      wb.style.display = "";
    } else {
      wb.style.display = "none";
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
  pcRenderShutdownTasks(snap);
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
    pcFillBootForm(snap);   // P1a 实机迭代②：配置卡自动回填当前值
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

function pcRenderShutdownTasks(snap) {
  /* 2026-09-17 三卡结构调整：原「关机计划任务」独立卡并入「定时关机」卡
     （渲染目标 #pcSdTasksBody）；唤醒定时器/快速启动快照数据仍在采集上报，
     仅 UI 不再渲染。 */
  var host = pc$("#pcSdTasksBody");
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
  host.innerHTML = '<table class="nd-table"><thead><tr><th>#</th><th>任务名</th><th>重复</th>' +
    '<th>下次运行</th><th>状态</th><th>执行内容</th></tr></thead><tbody>' +
    rows + "</tbody></table>";
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
  var sel = pc$("#pcBootMode");
  var m = sel ? sel.value : "";
  var showTime = m === "daily" || m === "weekly" || m === "single";
  var tRow = pc$("#pcBootTimeRow");
  var dRow = pc$("#pcBootDateRow");
  var wRow = pc$("#pcWeekdaysRow");
  if (tRow) tRow.style.display = showTime ? "" : "none";
  if (dRow) dRow.style.display = m === "single" ? "" : "none";
  if (wRow) wRow.style.display = m === "weekly" ? "" : "none";
}

function pcOnSdModeChange() {
  var m = pc$("#pcSdMode").value || "daily";
  pc$("#pcSdDate").style.display = m === "single" ? "" : "none";
}

/* P1a 实机迭代②：打开页面自动回填当前 BIOS 定时开机设置（ADR-005 v2 rtc） */
function pcMmddToIso(s) {
  /* BIOS "MM/DD/YYYY" → <input type=date> "YYYY-MM-DD"；其它形态原样返回空 */
  var m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(String(s || "").trim());
  if (!m) return "";
  var p2 = function (x) { return ("0" + x).slice(-2); };
  return m[3] + "-" + p2(m[1]) + "-" + p2(m[2]);
}

function pcFillBootForm(snap) {
  var m = (snap.machine || {});
  if (m.capability !== "enterprise_configurable") return;
  var rtc = (snap.bios || {}).rtc || {};
  var mode = "off", time = "", date = "", hint = "";
  var on = !!rtc.alarm_on;
  switch (rtc.alarm) {
    case "Daily Event":
      mode = "daily";
      time = rtc.time || ""; break;
    case "Weekly Event":
      mode = "weekly";
      time = rtc.time || ""; break;
    case "Single Event":
      mode = "single";
      time = rtc.time || ""; date = rtc.date || ""; break;
    case "User Defined":
      mode = "daily";
      time = rtc.user_time || rtc.time || "";
      hint = "当前 BIOS 为自定义时刻（User Defined）模式；应用所选模式将覆盖。";
      break;
    default:
      mode = on ? "daily" : "off";
      time = rtc.time || "";
  }
  var sel = pc$("#pcBootMode");
  if (sel) sel.value = mode;
  var t = pc$("#pcBootTime");
  if (t) t.value = time ? String(time).substring(0, 5) : "";
  var d = pc$("#pcBootDate");
  if (d) d.value = pcMmddToIso(date);
  var wd = rtc.weekdays || {};
  var boxes = document.querySelectorAll(".pc-wd");
  for (var i = 0; i < boxes.length; i++) {
    boxes[i].checked = (wd[boxes[i].value] === "Enabled");
  }
  pcOnBootModeChange();
  var tip = pc$("#pcBiosTip");
  if (tip && hint) tip.textContent = hint;
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

async function pcApplyBios() {
  if (pcState.opBusy) return;
  var mode = (pc$("#pcBootMode") || {}).value || "";
  var time = (pc$("#pcBootTime") || {}).value || "";
  var date = (pc$("#pcBootDate") || {}).value || "";
  if (!mode) {
    pc$("#pcBiosTip").textContent = "请先选择重复周期";
    return;
  }
  if (mode !== "off" && !time) {
    pc$("#pcBiosTip").textContent = "请填写开机时刻";
    return;
  }
  if (mode === "single" && !date) {
    pc$("#pcBiosTip").textContent = "请填写指定日期";
    return;
  }
  var weekdays = null;
  if (mode === "weekly") {
    weekdays = [];
    var boxes = document.querySelectorAll(".pc-wd");
    for (var i = 0; i < boxes.length; i++) {
      weekdays.push(boxes[i].checked ? 1 : 0);
    }
    if (weekdays.indexOf(1) < 0) {
      pc$("#pcBiosTip").textContent = "每周模式请至少勾选一天";
      return;
    }
  }
  var modeText = "";
  if (mode === "weekly") {
    var names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
    var picked = [];
    for (var j = 0; j < weekdays.length; j++) {
      if (weekdays[j]) picked.push(names[j]);
    }
    modeText = "每周（" + picked.join("、") + "）";
  } else {
    var sel = pc$("#pcBootMode");
    modeText = sel.selectedOptions[0].text;
  }
  if (!(await pcConfirm({
    title: "应用定时开机配置",
    message: mode === "off"
      ? "将停用本机的定时开机（应用前自动备份当前配置）。继续？"
      : "将按以下配置修改定时开机（应用前自动备份当前配置，需系统授权确认）：\n\n" +
        "周期：" + modeText +
        "\n时刻：" + (mode === "off" ? "—" : time) +
        (mode === "single" ? ("\n日期：" + date) : "") + "\n\n继续？"
  }))) return;
  pcSetOpBusy(true);
  var tip = pc$("#pcBiosTip");
  tip.textContent = "已受理，等待系统授权与执行…（若未见弹窗请在任务栏确认）";
  var payload = { mode: mode, time: time, date: date };
  if (mode === "weekly") payload.weekdays = weekdays;
  pcApiFetch("/api/powercontrol/bios-apply", payload)
    .then(function (data) {
      if (!data || data.success === false) {
        throw new Error((data && data.error) || "提交失败");
      }
      return pcPollTask(data.task_id, tip, "已应用：当前配置见上方卡片");
    })
    .then(function (r) { if (r) pcLoadSnapshot(); })
    .catch(function (e) {
      tip.textContent = pcTrunc((e && e.message) || "提交失败", 180);
      pcSetOpBusy(false);
    });
}

async function pcRestoreBios() {
  if (pcState.opBusy) return;
  if (!(await pcConfirm({
    title: "还原定时开机配置",
    message: "将把定时开机配置还原为最近一次备份的初始值（需系统授权确认）。继续？",
    danger: true
  }))) return;
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

async function pcSaveShutdown() {
  if (pcState.opBusy) return;
  var mode = (pc$("#pcSdMode") || {}).value || "daily";
  var time = (pc$("#pcSdTime") || {}).value || "";
  var date = (pc$("#pcSdDate") || {}).value || "";
  if (!time) {
    pc$("#pcSdTip").textContent = "请填写关机时刻";
    return;
  }
  if (mode === "single" && !date) {
    pc$("#pcSdTip").textContent = "请填写指定日期";
    return;
  }
  if (!(await pcConfirm({
    title: "保存定时关机任务",
    message: "将创建/更新定时关机任务（需系统授权确认）：\n\n" +
      "周期：" + pc$("#pcSdMode").selectedOptions[0].text +
      "\n时刻：" + time +
      (mode === "single" ? ("\n日期：" + date) : "") +
      "\n\n到点后倒计时 60 秒关机。继续？"
  }))) return;
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

async function pcRemoveShutdown() {
  if (pcState.opBusy) return;
  if (!(await pcConfirm({
    title: "删除定时关机任务",
    message: "将删除定时关机任务。继续？",
    danger: true
  }))) return;
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
