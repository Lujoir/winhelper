/* desktoppolicy.js · 锁屏及壁纸管理（观枢终端平台｜EyeTerm）
 * dp 前缀隔离；卡片基线见 net-doctor/docs/STYLE.md；文案零实现细节。
 * 数据源：/api/desktoppolicy/*（宿主 apiFetch → pywebview → fetch 三级回退）。
 */
"use strict";

var dpState = { inited: false, busy: false, taskId: null, pollTimer: null };

/* DOM 辅助（自包含，不依赖宿主 $ —— 主应用无全局 $，netdoctor 同例） */
function dp$(sel) { return document.querySelector(sel); }

function dpApiFetch(path, body) {
  if (typeof window.apiFetch === "function") {
    return window.apiFetch(path, body);
  }
  if (window.pywebview && window.pywebview.api && window.pywebview.api.call) {
    // 真实 pywebview 返回 Promise；桩/旧宿主可能同步返回，统一收敛为 Promise
    return Promise.resolve(
      window.pywebview.api.call(path, body ? JSON.stringify(body) : null));
  }
  return fetch(path, {
    method: body ? "POST" : "GET",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  }).then(function (r) { return r.json(); });
}

function dpEsc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

function dpBadge(text, cls) {
  return '<span class="nd-badge ' + (cls || "nd-muted") + '">' + dpEsc(text) + "</span>";
}

function dpResultBadge(r) {
  if (!r || r.state === "never") return dpBadge("未执行", "nd-muted");
  if (r.state === "ok") return dpBadge("已生效", "nd-ok");
  if (r.state === "skipped") return dpBadge("已跳过（远程会话）", "nd-warn");
  if (r.state === "error") {
    if (r.code === "blocked_by_security") return dpBadge("被安全软件拦截", "nd-err");
    if (r.code === "no_admin") return dpBadge("需管理员权限", "nd-warn");
    if (r.code === "rdp_skipped") return dpBadge("远程会话跳过", "nd-warn");
    if (r.code === "rollback_failed") return dpBadge("还原失败", "nd-err");
    return dpBadge("执行失败", "nd-err");
  }
  return dpBadge("未知", "nd-muted");
}

function dpFmtTime(ts) {
  if (!ts) return "—";
  try {
    var d = new Date(ts * 1000);
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") +
      "-" + String(d.getDate()).padStart(2, "0") + " " +
      String(d.getHours()).padStart(2, "0") + ":" +
      String(d.getMinutes()).padStart(2, "0") + ":" +
      String(d.getSeconds()).padStart(2, "0");
  } catch (e) { return "—"; }
}

function dpRenderStatus(data) {
  var rev = (data.revision !== undefined && data.revision !== null && data.revision >= 0)
    ? ("第 " + data.revision + " 版") : "尚未接收";
  dp$("#dpRev").textContent = rev;
  dp$("#dpSession").textContent = data.session_type === "console" ? "本机会话" : "远程会话";
  dp$("#dpReported").textContent = dpFmtTime(data.reported_at);
  var link = dp$("#dpLink");
  if (link) {
    if (data.terminal_id_ready === false) {
      link.textContent = "未接入平台";
      link.className = "nd-badge nd-warn";
    } else if (data.last_poll_error) {
      link.textContent = "平台连接异常（自动重试）";
      link.className = "nd-badge nd-warn";
    } else if (data.last_poll_ok_ts) {
      link.textContent = "平台连接正常";
      link.className = "nd-badge nd-ok";
    } else {
      link.textContent = "等待首个轮询周期";
      link.className = "nd-badge nd-muted";
    }
  }
  var r = data.results || {};
  dp$("#dpResWallpaper").innerHTML = dpResultBadge(r.desktop_wallpaper);
  dp$("#dpResLock").innerHTML = dpResultBadge(r.lock_screen);
  dp$("#dpResPower").innerHTML = dpResultBadge(r.power_plan);
  dp$("#dpResIdle").innerHTML = dpResultBadge(r.idle_lock);
  var mg = dp$("#dpPowerManaged");
  if (mg) {
    if (data.power_managed) {
      mg.textContent = "策略管控中";
      mg.className = "nd-badge nd-info";
    } else {
      mg.textContent = "本机自管";
      mg.className = "nd-badge nd-muted";
    }
  }
  // 显示器布局表
  var ms = data.monitors || [];
  var host = dp$("#dpMonBody");
  if (!ms.length) {
    host.innerHTML = '<div class="nd-empty">未检测到显示器信息</div>';
  } else {
    var rows = ms.map(function (m) {
      return "<tr><td>" + (m.index + 1) + "</td><td class=\"nd-num\">" +
        m.width + " × " + m.height + "</td><td class=\"nd-num\">(" +
        m.left + ", " + m.top + ")</td><td>" +
        (m.primary ? "是" : "否") + "</td></tr>";
    }).join("");
    host.innerHTML = '<table class="nd-table"><thead><tr>' +
      "<th>屏幕</th><th>分辨率</th><th>位置</th><th>主屏</th>" +
      '</tr></thead><tbody>' + rows + "</tbody></table>";
  }
  // 错误说明区
  var notes = [];
  ["desktop_wallpaper", "lock_screen", "power_plan", "idle_lock"].forEach(function (k) {
    var item = r[k];
    if (item && item.state === "error" && item.message) {
      notes.push(item.message);
    }
  });
  dp$("#dpNotes").innerHTML = notes.length
    ? notes.map(function (n) { return '<div class="nd-hint">· ' + dpEsc(n) + "</div>"; }).join("")
    : "";
}

function dpLoadStatus() {
  return dpApiFetch("/api/desktoppolicy/status").then(function (data) {
    if (!data || data.success === false) throw new Error(data && data.error || "status failed");
    dpRenderStatus(data);
  }).catch(function (e) {
    var host = dp$("#dpStatusWrap");
    if (host) {
      var el = dp$("#dpStatusErr");
      if (el) el.textContent = "状态读取失败（下个周期自动重试）";
    }
  });
}

function dpSetBusy(b) {
  dpState.busy = b;
  ["#dpBtnPoll", "#dpBtnReapply"].forEach(function (sel) {
    var el = dp$(sel);
    if (el) el.disabled = b;
  });
  /* 此处不清空 dpTaskTip：完成/失败提示由任务链收尾自行覆写，
     否则完成提示会被复位动作瞬间清掉（E2E 抓获）。 */
}

function dpRunTask(path) {
  if (dpState.busy) return Promise.resolve();
  dpSetBusy(true);
  dp$("#dpTaskTip").textContent = "正在执行，请稍候…";
  return dpApiFetch(path, {}).then(function (data) {
    if (!data || data.success === false || !data.task_id) {
      throw new Error(data && data.error || "发起失败");
    }
    dpState.taskId = data.task_id;
    return dpPollTask();
  }).catch(function (e) {
    dp$("#dpTaskTip").textContent = "执行失败：" + (e && e.message || "未知错误");
    dpSetBusy(false);
  });
}

function dpPollTask() {
  var taskId = dpState.taskId;
  if (!taskId) { dpSetBusy(false); return Promise.resolve(); }
  return dpApiFetch("/api/desktoppolicy/task-status?task_id=" +
    encodeURIComponent(taskId)).then(function (data) {
      if (!data || data.success === false) throw new Error("任务不存在");
      if (data.status === "running") {
        return new Promise(function (resolve) {
          setTimeout(resolve, 1200);
        }).then(dpPollTask);
      }
      if (data.status === "done") {
        dp$("#dpTaskTip").textContent = "执行完成";
        return dpLoadStatus();
      }
      var err = data.result && data.result.error;
      dp$("#dpTaskTip").textContent = "执行失败：" +
        (err && err.message || "未知错误");
    }).catch(function (e) {
      dp$("#dpTaskTip").textContent = "执行失败：" + (e && e.message || "未知错误");
    }).then(function () {
      dpSetBusy(false);
      dpState.taskId = null;
    });
}

function dpOpenLogs() {
  dpApiFetch("/api/desktoppolicy/logs").then(function (data) {
    if (!data || data.success === false || !data.log_dir) {
      dp$("#dpTaskTip").textContent = "日志目录尚未生成";
      return;
    }
    return dpApiFetch("/api/disk/open-location", { path: data.log_dir });
  }).catch(function () {
    dp$("#dpTaskTip").textContent = "打开日志位置失败";
  });
}

function initDesktopPolicyTab() {
  // 未接入平台时禁止进入本标签页，自动回到主页
  dpApiFetch("/api/perf/uplink/status").then(function (d) {
    var u = d && d.uplink ? d.uplink : null;
    if (!u || u.state !== "connected") {
      if (typeof switchTab === "function") switchTab("home");
      if (typeof showError === "function") showError("锁屏及壁纸管理需接入观枢终端平台后方可使用");
      return;
    }
    dpInitDesktopPolicyTabCore();
  }).catch(function () {
    if (typeof switchTab === "function") switchTab("home");
    if (typeof showError === "function") showError("锁屏及壁纸管理需接入观枢终端平台后方可使用");
  });
}

function dpInitDesktopPolicyTabCore() {
  if (!dpState.inited) {
    dpState.inited = true;
    var btnPoll = dp$("#dpBtnPoll");
    var btnReapply = dp$("#dpBtnReapply");
    var btnLogs = dp$("#dpBtnLogs");
    if (btnPoll) btnPoll.addEventListener("click", function () { dpRunTask("/api/desktoppolicy/policy-now"); });
    if (btnReapply) btnReapply.addEventListener("click", function () { dpRunTask("/api/desktoppolicy/apply-now"); });
    if (btnLogs) btnLogs.addEventListener("click", dpOpenLogs);
    dpBindPowerCfg();
  }
  dpLoadStatus();
  dpLoadPowerCfg();
}

/* ---------- 本地电源配置（关闭显示器 / 睡眠超时） ---------- */
var DP_POWER_OPTIONS = [
  { v: 0, t: "从不" },
  { v: 60, t: "1 分钟" },
  { v: 120, t: "2 分钟" },
  { v: 180, t: "3 分钟" },
  { v: 300, t: "5 分钟" },
  { v: 600, t: "10 分钟" },
  { v: 900, t: "15 分钟" },
  { v: 1200, t: "20 分钟" },
  { v: 1800, t: "30 分钟" },
  { v: 3600, t: "1 小时" },
  { v: 7200, t: "2 小时" }
];

function dpBuildPowerSelect(id, currentSec) {
  var html = '<select id="' + id + '" class="nd-input dp-power-select" style="width:auto;min-width:110px;">';
  var matched = false;
  for (var i = 0; i < DP_POWER_OPTIONS.length; i++) {
    var o = DP_POWER_OPTIONS[i];
    var sel = (o.v === currentSec) ? ' selected' : '';
    if (sel) matched = true;
    html += '<option value="' + o.v + '"' + sel + '>' + dpEsc(o.t) + '</option>';
  }
  // 当前值不在标准选项中时追加一个真实值选项
  if (!matched && currentSec >= 0) {
    html += '<option value="' + currentSec + '" selected>' + dpEsc(currentSec + " 秒") + '</option>';
  }
  html += '</select>';
  return html;
}

function dpLoadPowerCfg() {
  var host = dp$("#dpPowerCfgBody");
  if (!host) return;
  host.innerHTML = '<div class="nd-empty">正在读取本地电源配置…</div>';
  return dpApiFetch("/api/desktoppolicy/powercfg/read").then(function (data) {
    if (!data || data.success === false) throw new Error(data && data.error && data.error.message || "读取失败");
    dpRenderPowerCfg(data);
  }).catch(function (e) {
    if (host) host.innerHTML = '<div class="nd-empty">未获取到本地配置（' + dpEsc(e && e.message || "未知错误") + '）</div>';
  });
}

function dpRenderPowerCfg(data) {
  var host = dp$("#dpPowerCfgBody");
  if (!host) return;
  var scheme = data.scheme || {};
  var display = data.display_off || {};
  var sleep = data.sleep || {};
  var html = '<div class="nd-summary">当前电源方案：' + dpEsc(scheme.name || scheme.guid || "—") +
    '。修改后点击保存即可生效；若平台策略配置了电源项，下次策略发布将覆盖此处的手动设置。</div>';
  html += '<div class="hm-row"><span class="hm-row-label">关闭显示器（接通电源）</span>' +
    dpBuildPowerSelect("dpDisplayAc", display.ac_sec) + '</div>';
  html += '<div class="hm-row"><span class="hm-row-label">关闭显示器（使用电池）</span>' +
    dpBuildPowerSelect("dpDisplayDc", display.dc_sec) + '</div>';
  html += '<div class="hm-row"><span class="hm-row-label">进入睡眠（接通电源）</span>' +
    dpBuildPowerSelect("dpSleepAc", sleep.ac_sec) + '</div>';
  html += '<div class="hm-row"><span class="hm-row-label">进入睡眠（使用电池）</span>' +
    dpBuildPowerSelect("dpSleepDc", sleep.dc_sec) + '</div>';
  html += '<div style="margin-top:12px"><button class="nd-btn primary" id="dpBtnSavePowerCfg">保存</button>' +
    '<span class="nd-hint" id="dpPowerCfgTip" style="margin-left:10px"></span></div>';
  host.innerHTML = html;
  var btn = dp$("#dpBtnSavePowerCfg");
  if (btn) btn.addEventListener("click", dpSavePowerCfg);
}

function dpBindPowerCfg() {
  // 渲染后事件委托到容器
  var host = dp$("#dpPowerCfgBody");
  if (!host) return;
  host.addEventListener("change", function (e) {
    if (e.target && e.target.classList && e.target.classList.contains("dp-power-select")) {
      dp$("#dpPowerCfgTip").textContent = "";
    }
  });
}

function dpSavePowerCfg() {
  var tip = dp$("#dpPowerCfgTip");
  var btn = dp$("#dpBtnSavePowerCfg");
  if (!tip || !btn) return;
  btn.disabled = true;
  tip.textContent = "保存中…";
  function gv(id) { var el = dp$("#" + id); return el ? parseInt(el.value, 10) : null; }
  var body = {
    display_off_ac: gv("dpDisplayAc"),
    display_off_dc: gv("dpDisplayDc"),
    sleep_ac: gv("dpSleepAc"),
    sleep_dc: gv("dpSleepDc")
  };
  dpApiFetch("/api/desktoppolicy/powercfg/set", body).then(function (data) {
    if (!data || data.success === false || !data.result || !data.result.ok) {
      var msg = data && data.result && data.result.error && data.result.error.message;
      throw new Error(msg || data && data.error && data.error.message || "保存失败");
    }
    tip.textContent = "已保存";
    return dpLoadPowerCfg();
  }).catch(function (e) {
    tip.textContent = "保存失败：" + (e && e.message || "未知错误");
  }).then(function () {
    if (btn) btn.disabled = false;
  });
}

/* 独立页（无主应用 $ 时内联降级） */
function dpInitStandalone() {
  if (typeof window.$ !== "function") {
    window.$ = function (sel) { return document.querySelector(sel); };
  }
  initDesktopPolicyTab();
}
