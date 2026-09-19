/* powercontrol.js · 自动开关机（观枢终端平台｜EyeTerm）
 * pc 前缀隔离；卡片基线见 net-doctor/docs/STYLE.md；文案零实现细节。
 * 数据源：/api/powercontrol/snapshot（本机快照，仅读本地）、
 *         /api/powercontrol/center-tasks、/api/powercontrol/center-task-create
 *         （定时开机=中心任务驱动：4.1.8 页改追加，连接门控 + 只读任务列表
 *         + 个性化任务创建；执行在中心侧，终端不做本地定时）。
 * 定时关机：本地配置本地执行（schtasks 引擎不变），配置变更由引擎侧
 * 静默上报中心（版本化，见 power_control.py），本页不含上报逻辑。
 * （宿主 apiFetch → pywebview → fetch 三级回退）。
 */
"use strict";

var pcState = { busy: false, loaded: false, lastSnap: null,
                opBusy: false, sdTask: null,
                activePolicy: null,
                center: { busy: false, connected: null, tasks: [],
                          quota: null, error: "", tip: "" } };

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
  pcLoadCenter();
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
  pcRenderBios(snap);
  pcRenderShutdownTasks(snap);
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
    /* 4.1.8 紧凑化：两段说明合并为一行核心指引；「登记到平台」按钮语义
       已并入中心任务卡的个性化任务维护（备注填写即登记）。 */
    html.push('<div class="nd-hint">如需定时开机：开机自检进 BIOS → 电源管理菜单' +
      "设置 RTC Alarm / Wake Up on Alarm 类选项。</div>");
    if (b.human_set_flag) {
      html.push('<div class="nd-hint" style="margin-top:6px">' +
        pcBadge("已登记人工设置", "nd-info") +
        '<span style="margin-left:6px">' + pcEsc(b.human_set_at || "") +
        "（平台可见）</span></div>");
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

/* 4.1.8：pcReportNow 已随「平台存档」卡删除；页改追加后本页对中心只读
   （任务列表）+ 个性化创建，/report human_set 通道保留但 UI 无调用方
   （人工登记语义并入个性化任务备注，见中心任务模块）。 */

/* ======================================================================
 * P1 · 定时开机配置 / 定时关机操作（提权异步任务；UAC 确认制）
 * ==================================================================== */

var _PC_OP_BTNS = ["#pcBtnSdSave", "#pcBtnSdToggle", "#pcBtnSdRemove"];

function pcSetOpBusy(b) {
  pcState.opBusy = b;
  _PC_OP_BTNS.forEach(function (sel) {
    var el = pc$(sel);
    if (el) el.disabled = b;
  });
}

function pcOnSdModeChange() {
  var m = pc$("#pcSdMode").value || "daily";
  pc$("#pcSdDate").style.display = m === "single" ? "" : "none";
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

/* ======================================================================
 * 定时开机（中心任务）· 4.1.8 页改追加
 * 中心任务驱动：连接门控 → 生效任务只读列表（最早触发在最上）→
 * 个性化任务创建（origin=client_personal，数量限额以中心校验为准）。
 * 执行在中心侧，终端不做本地定时；人工 BIOS 设置场景=进 BIOS 后
 * 在此登记一条个性化任务（备注栏填写）。
 * ==================================================================== */

function pcCenterSetBusy(b) {
  pcState.center.busy = b;
  var el = pc$("#pcBtnCenterRefresh");
  if (el) el.disabled = b;
}

/* 连接门控：沿用企业版/平台接入既有检测（/api/perf/uplink/status 的
   uplink.state === "connected"）；查询失败一律按未连接处理。 */
function pcCheckConnected() {
  return pcApiFetch("/api/perf/uplink/status").then(function (d) {
    var u = d && d.uplink ? d.uplink : null;
    return !!(u && u.state === "connected");
  }).catch(function () { return false; });
}

function pcLoadCenter(keepTip) {
  var body = pc$("#pcCenterBody");
  if (!body || pcState.center.busy) return Promise.resolve();
  if (!keepTip) pcState.center.tip = "";
  pcCenterSetBusy(true);
  pcSetConnBadge(null);
  body.innerHTML = '<div class="nd-empty">正在检查中心连接…</div>';
  return pcCheckConnected().then(function (conn) {
    pcState.center.connected = conn;
    if (!conn) { pcRenderCenterGate(); return null; }
    return pcApiFetch("/api/powercontrol/center-tasks").then(function (r) {
      if (!r || r.success === false) {
        pcState.center.error = (r && r.error) || "任务列表获取失败";
        pcState.center.tasks = [];
        pcState.center.quota = null;
      } else {
        pcState.center.error = "";
        pcState.center.tasks = pcSortTasks(r.tasks || []);
        pcState.center.quota = r.quota || null;
      }
      pcRenderCenterConnected();
    });
  }).catch(function () {
    pcState.center.connected = false;
    pcRenderCenterGate();
  }).then(function () { pcCenterSetBusy(false); });
}

function pcSetConnBadge(state) {
  var b = pc$("#pcCenterConnBadge");
  if (!b) return;
  if (state === null) {
    b.style.display = "";
    b.textContent = "检测连接…";
    b.className = "nd-badge nd-muted";
  } else if (state === true) {
    b.style.display = "";
    b.textContent = "中心已连接";
    b.className = "nd-badge nd-ok";
  } else {
    b.style.display = "";
    b.textContent = "中心未连接";
    b.className = "nd-badge nd-muted";
  }
}

/* 未连接降级：置灰 + 一句说明 + 人工指引一行（快照卡照常可用） */
function pcRenderCenterGate() {
  pcSetConnBadge(false);
  var body = pc$("#pcCenterBody");
  if (!body) return;
  body.style.opacity = ".62";
  body.innerHTML =
    '<div class="nd-hint">连接中心后开放定时开机管理。</div>' +
    '<div class="nd-hint" style="margin-top:6px">如需定时开机：开机自检进 BIOS → ' +
    "电源管理菜单设置 RTC Alarm / Wake Up on Alarm 类选项。</div>";
}

function pcParseHHMM(s) {
  var m = /^(\d{1,2}):(\d{2})/.exec(String(s || "").trim());
  if (!m) return null;
  var h = +m[1], mi = +m[2];
  if (h > 23 || mi > 59) return null;
  return h * 60 + mi;
}

/* 下次触发时间戳：契约定稿（2026-09-19）后优先采用中心 next_ts（epoch 秒），
   缺失时降级为本地按 repeat/time 推算。未知/不可解析/已过期单次排最后（大数）。 */
function pcNextFireTs(t) {
  var BIG = 8640000000000000;
  if (!t || typeof t !== "object") return BIG;
  var cts = Number(t.next_ts);
  if (isFinite(cts) && cts > 0) return cts * 1000;
  var mins = pcParseHHMM(t.time_hhmm);
  if (mins == null) return BIG;
  var now = new Date();
  var midnight = new Date(now.getFullYear(), now.getMonth(),
                          now.getDate()).getTime();
  function at(dayOffset) {
    return midnight + dayOffset * 86400000 + mins * 60000;
  }
  var rep = String(t.repeat || "").toLowerCase();
  if (rep === "once") {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(t.once_date || "").trim());
    if (!m) return BIG;
    var ts = new Date(+m[1], +m[2] - 1, +m[3]).getTime() + mins * 60000;
    return ts > now.getTime() ? ts : BIG;
  }
  if (rep === "weekly") {
    var wd = String(t.weekdays || "");
    for (var off = 0; off < 8; off++) {
      var d = new Date(midnight + off * 86400000);
      var idx = (d.getDay() + 6) % 7;   /* 周一=0 … 周日=6 */
      if (wd.charAt(idx) === "1") {
        var wts = at(off);
        if (wts > now.getTime()) return wts;
      }
    }
    return BIG;
  }
  var dts = at(0);   /* daily / workday / holiday / 未知 → 按每天近似 */
  return dts > now.getTime() ? dts : at(1);
}

function pcSortTasks(tasks) {
  var arr = [];
  for (var i = 0; i < tasks.length; i++) {
    var t = tasks[i];
    if (!t || typeof t !== "object") continue;
    if (t.kind !== undefined && String(t.kind) !== "boot") continue;
    var en = t.enabled;
    if (en !== undefined && en !== 1 && en !== true && en !== "1") continue;
    arr.push(t);
  }
  arr.sort(function (a, b) {
    var ta = pcNextFireTs(a), tb = pcNextFireTs(b);
    if (ta !== tb) return ta - tb;
    var ma = pcParseHHMM(a.time_hhmm) || 0, mb = pcParseHHMM(b.time_hhmm) || 0;
    if (ma !== mb) return ma - mb;
    return String(a.name || "").localeCompare(String(b.name || ""));
  });
  return arr;
}

function pcModeText(t) {
  var rep = String((t && t.repeat) || "").toLowerCase();
  var names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
  if (rep === "daily") return "每天";
  if (rep === "weekly") {
    var wd = String((t && t.weekdays) || ""), picked = [];
    for (var i = 0; i < 7; i++) { if (wd.charAt(i) === "1") picked.push(names[i]); }
    return picked.length ? "每周（" + picked.join("、") + "）" : "每周";
  }
  if (rep === "once") {
    var d = String((t && t.once_date) || "");
    return d ? "单次 " + d.substring(5) : "单次";
  }
  if (rep === "workday") return "工作日";
  if (rep === "holiday") return "节假日";
  return rep ? rep : "—";
}

function pcSourceLabel(src) {
  var s = String(src || "").toLowerCase();
  var map = { platform: "平台", client_personal: "个性化",
              huorong: "安全软件", nad: "准入系统" };
  return map[s] || (s || "—");
}

function pcRenderCenterConnected() {
  pcSetConnBadge(true);
  var body = pc$("#pcCenterBody");
  if (!body) return;
  body.style.opacity = "";
  var html = [];
  if (pcState.center.error) {
    html.push('<div class="nd-hint" style="color:#ef9a9a">任务列表获取失败：' +
      pcEsc(pcTrunc(pcState.center.error, 120)) +
      "（可点击右上角刷新重试）</div>");
  }
  var q = pcState.center.quota;
  if (q && (q.limit !== undefined && q.limit !== null)) {
    html.push('<div class="nd-hint" style="margin-bottom:6px">个性化任务：' +
      pcEsc(String(q.used != null ? q.used : "—")) + " / " +
      pcEsc(String(q.limit)) + "</div>");
  }
  html.push('<div id="pcCenterListBody">' + pcRenderCenterList() + "</div>");
  html.push(pcRenderCenterForm());
  body.innerHTML = html.join("");
}

function pcRenderCenterList() {
  var tasks = pcState.center.tasks || [];
  if (!tasks.length) {
    return '<div class="nd-empty">暂无生效的开机任务</div>';
  }
  var rows = tasks.map(function (t, i) {
    var next = pcNextFireTs(t);
    var nextText;
    if (t.next_trigger) {
      nextText = String(t.next_trigger);
    } else if (next < 8640000000000000) {
      nextText = pcFormatTs(next);
    } else {
      nextText = "—";
    }
    if (t.calendar_fallback && nextText !== "—") {
      nextText = "≈" + nextText;
    }
    var cfAttr = t.calendar_fallback
      ? ' title="日历未就绪，此为近似预估"' : "";
    var own = String(t.origin || "") === "client_personal" &&
              t.task_id !== undefined && t.task_id !== null &&
              t.task_id !== "";
    var act = own
      ? '<button class="nd-btn" onclick="pcDeletePersonalTask(' +
        pcEsc(String(t.task_id)) + ')">删除</button>'
      : "—";
    return "<tr><td class=\"nd-num\">" + (i + 1) + "</td><td>" +
      pcEsc(pcTrunc(t.name || "—", 28)) + "</td><td>" +
      pcEsc(pcModeText(t)) + "</td><td class=\"nd-num\">" +
      pcEsc(String(t.time_hhmm || "—")) + "</td><td>" +
      pcBadge(pcSourceLabel(t.source), "nd-info") + "</td><td class=\"nd-num\"" +
      cfAttr + ">" + pcEsc(nextText) + "</td><td>" + act + "</td></tr>";
  }).join("");
  return '<table class="nd-table"><thead><tr><th>#</th><th>任务名</th>' +
    "<th>计划模式</th><th>时刻</th><th>来源</th><th>下次触发</th>" +
    "<th>操作</th></tr></thead><tbody>" + rows + "</tbody></table>" +
    '<div class="nd-hint" style="margin-top:6px">按下次触发时间排序（以中心为准），' +
    "最早触发在最上；到点由中心执行唤醒，本机不做本地定时。仅个性化任务可删除。</div>";
}

function pcFormatTs(ms) {
  var d = new Date(ms);
  var p2 = function (x) { return ("0" + x).slice(-2); };
  return (d.getMonth() + 1) + "-" + p2(d.getDate()) + " " +
    p2(d.getHours()) + ":" + p2(d.getMinutes());
}

function pcRenderCenterForm() {
  return '<div style="margin-top:12px;padding-top:10px;' +
    'border-top:1px dashed rgba(42,47,69,.6)">' +
    '<div class="nd-hint" style="margin-bottom:6px">新建个性化开机任务' +
    "（仅对本机生效；已手动在 BIOS 设置的，可在此登记并填备注）</div>" +
    '<div class="pc-form-row">' +
    '<span class="pc-form-label">计划模式</span>' +
    '<select class="nd-select" id="pcCenterMode" onchange="pcOnCenterModeChange()">' +
    '<option value="daily">每天</option>' +
    '<option value="weekly">每周（勾选星期）</option>' +
    '<option value="once">指定日期（单次）</option>' +
    '<option value="workday" disabled>工作日（日历就绪后开放）</option>' +
    '<option value="holiday" disabled>节假日（日历就绪后开放）</option>' +
    "</select>" +
    '<input class="nd-input" type="time" id="pcCenterTime" autocomplete="off">' +
    "</div>" +
    '<div class="pc-form-row" id="pcCenterDateRow" style="display:none">' +
    '<span class="pc-form-label">指定日期</span>' +
    '<input class="nd-input" type="date" id="pcCenterDate" autocomplete="off">' +
    "</div>" +
    '<div class="pc-form-row" id="pcCenterWeekRow" style="display:none">' +
    '<span class="pc-form-label">重复在</span>' +
    '<span style="display:flex;gap:8px;flex-wrap:wrap;font-size:12px">' +
    '<label><input type="checkbox" class="pc-cwd" value="0">周一</label>' +
    '<label><input type="checkbox" class="pc-cwd" value="1">周二</label>' +
    '<label><input type="checkbox" class="pc-cwd" value="2">周三</label>' +
    '<label><input type="checkbox" class="pc-cwd" value="3">周四</label>' +
    '<label><input type="checkbox" class="pc-cwd" value="4">周五</label>' +
    '<label><input type="checkbox" class="pc-cwd" value="5">周六</label>' +
    '<label><input type="checkbox" class="pc-cwd" value="6">周日</label>' +
    "</span></div>" +
    '<div class="pc-form-row">' +
    '<span class="pc-form-label">备注</span>' +
    '<input class="nd-input" type="text" id="pcCenterRemark" maxlength="60" ' +
    'style="flex:1;min-width:160px" placeholder="选填，如：已在 BIOS 手工设置，此为登记">' +
    "</div>" +
    '<div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:8px">' +
    '<button class="nd-btn primary" id="pcBtnCenterCreate" onclick="pcCreatePersonalTask()">提交任务</button>' +
    '<span class="nd-hint" id="pcCenterTip">' + pcEsc(pcState.center.tip || "") + "</span>" +
    "</div>" +
    "</div>";
}

function pcOnCenterModeChange() {
  var sel = pc$("#pcCenterMode");
  var m = sel ? sel.value : "daily";
  var dRow = pc$("#pcCenterDateRow");
  var wRow = pc$("#pcCenterWeekRow");
  if (dRow) dRow.style.display = m === "once" ? "" : "none";
  if (wRow) wRow.style.display = m === "weekly" ? "" : "none";
}

async function pcCreatePersonalTask() {
  if (pcState.center.busy || !pcState.center.connected) return;
  var tip = pc$("#pcCenterTip");
  var mode = (pc$("#pcCenterMode") || {}).value || "daily";
  var time = (pc$("#pcCenterTime") || {}).value || "";
  var date = (pc$("#pcCenterDate") || {}).value || "";
  var remark = ((pc$("#pcCenterRemark") || {}).value || "").trim();
  if (!time) {
    if (tip) tip.textContent = "请填写开机时刻";
    return;
  }
  var payload = { repeat: mode, time: time, remark: remark };
  var modeText = "";
  if (mode === "weekly") {
    var wd = [0, 0, 0, 0, 0, 0, 0];
    var boxes = document.querySelectorAll(".pc-cwd");
    var picked = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
    var names = [];
    for (var i = 0; i < boxes.length; i++) {
      var idx = +boxes[i].value;
      if (boxes[i].checked) { wd[idx] = 1; names.push(picked[idx]); }
    }
    if (names.length === 0) {
      if (tip) tip.textContent = "每周模式请至少勾选一天";
      return;
    }
    payload.weekdays = wd;
    modeText = "每周（" + names.join("、") + "）";
  } else if (mode === "once") {
    if (!date) {
      if (tip) tip.textContent = "请选择开机日期";
      return;
    }
    payload.once_date = date;
    modeText = "单次 " + date;
  } else {
    modeText = "每天";
  }
  if (!(await pcConfirm({
    title: "新建个性化开机任务",
    message: "将向中心提交一条仅对本机生效的开机任务：\n\n" +
      "计划模式：" + modeText + "\n开机时刻：" + time +
      (remark ? "\n备注：" + remark : "") +
      "\n\n到点由中心执行唤醒。继续？"
  }))) return;
  pcState.center.tip = "正在提交…";
  if (tip) tip.textContent = "正在提交…";
  pcApiFetch("/api/powercontrol/center-task-create", payload)
    .then(function (r) {
      if (!r || r.success === false) {
        throw new Error((r && r.error) || "提交失败");
      }
      pcState.center.tip = "已提交，任务列表已刷新";
      return pcLoadCenter(true);
    })
    .catch(function (e) {
      pcState.center.tip = "";
      if (tip) tip.textContent = pcTrunc((e && e.message) || "提交失败", 160);
    });
}

/* 删除本终端的个性化开机任务（仅 origin=client_personal 行可见入口；
   中心归属锁定，越权/不存在以中心 error 原文如实提示）。 */
function pcDeletePersonalTask(taskId) {
  if (pcState.center.busy || !pcState.center.connected) return;
  pcConfirm({
    title: "删除个性化开机任务",
    message: "将删除本机的个性化开机任务（任务 " + taskId + "）。\n继续？",
    danger: true
  }).then(function (ok) {
    if (!ok) return;
    pcState.center.tip = "";
    pcApiFetch("/api/powercontrol/center-task-delete?task_id=" +
               encodeURIComponent(taskId))
      .then(function (r) {
        if (!r || r.success === false) {
          throw new Error((r && r.error) || "删除失败");
        }
        pcState.center.tip = "已删除，任务列表已刷新";
        return pcLoadCenter(true);
      })
      .catch(function (e) {
        pcState.center.tip = "";
        var tip = pc$("#pcCenterTip");
        if (tip) {
          tip.textContent = pcTrunc((e && e.message) || "删除失败", 160);
        }
      });
  });
}
