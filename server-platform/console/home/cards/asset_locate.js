/* 首页卡片 · 资产定位（asset-mgmt-dev 维护；管线端点
 * POST /api/v1/console/home/asset-locate，admin-only。
 * 数据流：摘要占位走 home/summary（api_home.py _card_asset_locate），
 * 检索结果由卡片自取数（不走 summary）。 */
"use strict";

var _alSourceLabels = {platform: "平台", huorong: "安全", nad: "准入"};

function alEsc(s) { return homeEsc(s); }

function alFmtMac(mac) {
  var m = String(mac || "").replace(/[^0-9a-fA-F]/g, "");
  if (m.length !== 12) { return alEsc(mac || ""); }
  return m.toUpperCase().replace(/(.{2})(?=.)/g, "$1:");
}

function alOnlineBadge(p, h, n) {
  var online = (n && n.online) || (h && h.online) || (p && p.online);
  return online
    ? '<span class="al-dot al-on"></span>在线'
    : '<span class="al-dot al-off"></span>离线';
}

function alSection(title, inner) {
  return '<div class="al-sec"><div class="al-sec-t">' + alEsc(title)
    + "</div>" + inner + "</div>";
}

/* 主结果块：asset_profile + 命中源摘要素材（键值行走框架 homeRows） */
function alProfile(j) {
  var p = j.asset_profile || {};
  var m0 = (j.matches || [])[0] || {};
  var reg = p.registration || {};
  var adm = p.admission || {};
  var rows = [
    ["当前 IP", p.identity && p.identity.current_ip || "", false],
    ["MAC", alFmtMac(p.identity && p.identity.mac), false],
    ["设备状态", alOnlineBadge(m0.platform, m0.huorong, m0.nad), true],
    ["来源", (p.source_platform || "").split("+")
      .map(function (s) { return _alSourceLabels[s] || s; })
      .join(" + "), false]
  ];
  [["楼层", reg["楼层"]], ["具体位置", reg["具体位置"]],
   ["工号", reg["工号"]], ["姓名", reg["姓名"]],
   ["使用科室", reg["使用科室"]]].forEach(function (kv) {
    if (kv[1]) { rows.push([kv[0], kv[1], false]); }
  });
  if (adm && adm.terminal_name) {
    rows.push(["准入登记名", adm.terminal_name, false]);
  }
  if (adm && adm.alias) { rows.push(["接入区域", adm.alias, false]); }
  if (adm && adm.owner) {
    rows.push(["责任人", adm.owner + (adm.owner_uuid
      ? "（工号 " + adm.owner_uuid + "）" : ""), false]);
  }
  if (adm && adm.access_location) {
    rows.push(["接入位置", adm.access_location, false]);
  }
  var latest = p.timeline && p.timeline.latest;
  if (latest && latest.ts) {
    rows.push(["最后活跃", homeFmtTs(latest.ts)
      + "（" + (_alSourceLabels[latest.source] || latest.source) + "）", false]);
  }
  var html = '<div class="al-name">' + alEsc(p.computer_name || "未知设备")
    + "</div>" + homeRows(rows);
  var notes = (p.cross_check && p.cross_check.notes) || [];
  if (notes.length) {
    html += '<div class="al-warn">' + notes.map(alEsc).join("<br>")
      + "</div>";
  }
  return alSection("定位结果", html);
}

/* 其他候选（多命中时列摘要行） */
function alMatches(j) {
  var ms = j.matches || [];
  if (ms.length < 2) { return ""; }
  var scoreCn = {high: "高", medium: "中", low: "低"};
  var rows = ms.slice(1).map(function (m) {
    var idn = m.identity || {};
    return [idn.hostname || "（未知名）",
      alEsc(idn.current_ip || "-")
      + ' <span class="al-badge al-badge-' + alEsc(m.score) + '">'
      + alEsc(scoreCn[m.score] || m.score) + "</span>", true];
  });
  return alSection("其他候选（" + (ms.length - 1) + "）", homeRows(rows));
}

/* AI 推断区（独立于确定数据，恒带推测标注） */
function alInference(j) {
  var inf = j.inference || {};
  if (!inf.available) {
    if (inf.status === "unavailable") {
      return '<div class="al-ai-sub">AI 推测暂不可用，以上为登记数据</div>';
    }
    return "";
  }
  var blocks = (inf.blocks || []).map(function (b) {
    var ev = (b.evidence || []).slice(0, 3);
    return '<div class="al-ai-block"><span class="al-ai-badge">AI 推测</span>'
      + '<span class="al-ai-title">' + alEsc(b.title || "") + "</span>"
      + '<div class="al-ai-text">' + alEsc(b.text || "") + "</div>"
      + (ev.length ? '<div class="al-ai-ev" title="'
        + alEsc(ev.join("；")) + '">依据：' + alEsc(ev.join("；")) + "</div>"
        : "")
      + "</div>";
  }).join("");
  return alSection("智能推断", blocks
    + '<div class="al-ai-sub">' + alEsc(inf.disclaimer || "") + "</div>");
}

/* 数据源可用性脚注（零实现细节：平台/安全/准入） */
function alSources(j) {
  var src = (j.search || {}).sources || {};
  var bad = [];
  [["platform", src.platform], ["huorong", src.huorong], ["nad", src.nad]]
    .forEach(function (kv) {
      if (kv[1] && kv[1].status !== "ok") {
        bad.push(_alSourceLabels[kv[0]]);
      }
    });
  if (!bad.length) { return ""; }
  return '<div class="al-sub">' + alEsc(bad.join("、"))
    + "数据源暂不可用，结果可能不全</div>";
}

function alRender(j) {
  if (!j || j.ok !== true) {
    return '<div class="hc-empty hc-empty-err">定位服务异常，稍后重试</div>';
  }
  var amb = (j.extract && j.extract.ambiguous) || [];
  if (j.extract && j.extract.empty) {
    return '<div class="hc-empty">未识别到可用线索'
      + '<span class="hc-empty-sub">可输入 IP、MAC、计算机名或工号</span></div>';
  }
  if (j.not_found) {
    return '<div class="hc-empty">未找到匹配资产'
      + '<span class="hc-empty-sub">可换个线索再试，或确认资产已登记</span></div>'
      + (amb.length ? '<div class="al-sub">已忽略疑似片段：'
        + alEsc(amb.map(function (a) { return a.value; }).join("、"))
        + "</div>" : "");
  }
  return alProfile(j) + alMatches(j) + alInference(j) + alSources(j);
}

/* 检索交互：卡片自取数（不经 home/summary） */
function alLocate() {
  var inp = document.getElementById("alInput");
  var res = document.getElementById("alResult");
  var btn = document.getElementById("alBtn");
  if (!res) { return; }
  var text = (inp && inp.value || "").trim();
  if (!text) {
    res.innerHTML = '<div class="hc-empty">请输入 IP、MAC、计算机名或工号</div>';
    return;
  }
  if (inp) { inp.disabled = true; }
  if (btn) { btn.disabled = true; }
  res.innerHTML = '<div class="hc-loading"><i></i><i></i><i></i></div>';
  var done = function () {
    if (inp) { inp.disabled = false; }
    if (btn) { btn.disabled = false; }
  };
  /* 前端超时兜底：后端聚合需等画方准入/火绒/AI 推断等外部源，任一环节
   * 慢都会让请求长时间无响应。必须给用户一个终态，杜绝永久转圈
   * （2026-09-19 生产实况：结果区停留 loading，用户误判「检索不到任何信息」）。 */
  var settled = false;
  var timer = setTimeout(function () {
    if (settled) { return; }
    settled = true;
    done();
    res.innerHTML = '<div class="hc-empty hc-empty-err">定位超时'
      + '<span class="hc-empty-sub">超过 25 秒未返回，请稍后重试</span></div>';
  }, 25000);
  apiFetch("/console/home/asset-locate", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({text: text})
  }).then(function (j) {
    if (settled) { return; }
    settled = true;
    clearTimeout(timer);
    done();
    res.innerHTML = alRender(j);
  }).catch(function (e) {
    if (settled) { return; }
    settled = true;
    clearTimeout(timer);
    done();
    if (e && e.message === "unauthorized") { return; }
    res.innerHTML = '<div class="hc-empty hc-empty-err">'
      + alEsc(e && e.message || "定位失败，稍后重试") + "</div>";
  });
}

HomeCards.register({
  id: "asset-locate",
  title: "资产定位",
  summary: "输入 IP / MAC / 主机名 / 工号，跨平台-安全-准入三源定位资产。",
  order: 2.5,
  /* 交互型卡片：输入框/结果区不被首页刷新重建（防输入被冲空） */
  keepAlive: true,

  render: function () {
    /* summary 占位数据（_card_asset_locate 仅保框架数据流）；
     * 真实结果由 alLocate 自取数填充 alResult。 */
    return '<div class="al-input-row">'
      + '<input id="alInput" maxlength="2000" placeholder="IP / MAC / 主机名 / 工号"'
      + ' onkeydown="if(event.key===\'Enter\'){alLocate();}">'
      + '<button class="btn primary" id="alBtn" onclick="alLocate()">定位</button>'
      + "</div>"
      + '<div id="alResult"><div class="hc-empty">输入线索后点击定位</div></div>';
  }
});
