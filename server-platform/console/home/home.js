/* 观枢终端平台 · 控制台「首页」卡片式主界面框架（home-console-dev）
 *
 * 卡片注册机制：
 * - 每张功能卡片 = 独立文件 home/cards/{id}.js，加载后调用
 *   HomeCards.register({id, title, summary, order, render})。
 * - 同 id 重复注册 = 覆盖（框架预置的 stub 占位卡可被各模块
 *   真实卡片文件覆盖，Phase2 各模块 agent 按此契约接入）。
 * - 渲染顺序按 order 升序；数据源统一 GET /console/home/summary
 *   （服务端聚合，单卡失败服务端返回 {ok:false}，前端降级）。
 *
 * 卡片状态三态（统一由框架 helpers 实现，各卡不得自造）：
 * - 加载态：骨架 shimmer
 * - 错误态：「数据不可用」（单卡失败不阻塞整页）
 * - 空态 / 骨架占位（pending 卡）：文案通俗、零实现细节
 */
"use strict";

var HomeCards = (function () {
  var _map = {};
  return {
    register: function (def) {
      if (!def || !def.id) { return; }
      _map[def.id] = def;
    },
    all: function () {
      return Object.keys(_map).map(function (k) { return _map[k]; })
        .sort(function (a, b) { return (a.order || 99) - (b.order || 99); });
    },
    get: function (id) { return _map[id] || null; }
  };
})();

/* ---------- 框架 helpers（卡片渲染统一出口） ---------- */

function homeEsc(s) {
  return String(s == null ? "" : s).replace(/&/g, "&amp;")
    .replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function homeFmtTs(ts) {
  if (!ts) { return "-"; }
  try {
    var d = new Date(ts * 1000);
    function p(n) { return (n < 10 ? "0" : "") + n; }
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate())
      + " " + p(d.getHours()) + ":" + p(d.getMinutes());
  } catch (e) { return "-"; }
}

/* 键值行：左标签右值（STYLE 基线 hm-row 同构） */
function homeRows(rows) {
  return '<div class="hc-rows">' + rows.map(function (r) {
    return '<div class="hc-row"><span class="hc-k">' + homeEsc(r[0])
      + '</span><span class="hc-v">' + (r[2] ? r[1] : homeEsc(r[1]))
      + '</span></div>';
  }).join("") + "</div>";
}

/* 直达按钮组：fn 为页面全局函数名（如 switchTab），arg 为其参数 */
function homeActions(btns) {
  return '<div class="hc-actions">' + btns.map(function (b) {
    return '<button class="btn' + (b.primary ? " primary" : "")
      + '" onclick="' + homeEsc(b.fn) + '(\'' + homeEsc(b.arg || "") + '\')">'
      + homeEsc(b.label) + "</button>";
  }).join("") + "</div>";
}

/* 单卡状态渲染（框架统一，卡片只产 body 内容） */
function homeSetState(el, state, text) {
  var body = el.querySelector(".hc-body");
  if (!body) { return; }
  if (state === "loading") {
    body.innerHTML = '<div class="hc-loading"><i></i><i></i><i></i></div>';
  } else if (state === "error") {
    body.innerHTML = '<div class="hc-empty hc-empty-err">数据不可用'
      + '<span class="hc-empty-sub">' + homeEsc(text || "稍后刷新重试")
      + "</span></div>";
  } else if (state === "pending") {
    body.innerHTML = '<div class="hc-empty">卡片接入中'
      + '<span class="hc-empty-sub">' + homeEsc(text || "该模块功能卡片即将上线")
      + "</span></div>";
  } else if (state === "empty") {
    body.innerHTML = '<div class="hc-empty">' + homeEsc(text || "暂无数据")
      + "</div>";
  }
}

/* ---------- 框架主流程 ---------- */

var _homeSummaryCache = null;

function homeInit() {
  var grid = document.getElementById("homeGrid");
  if (!grid) { return; }
  if (!grid.dataset.inited) {          /* 幂等：卡壳只建一次，后续仅刷数据 */
    grid.innerHTML = HomeCards.all().map(function (def) {
      return '<section class="home-card" id="homeCard-' + homeEsc(def.id)
        + '" data-card="' + homeEsc(def.id) + '">'
        + '<div class="hc-head"><h3>' + homeEsc(def.title || def.id) + "</h3>"
        + (def.tag ? '<span class="hc-tag">' + homeEsc(def.tag) + "</span>" : "")
        + "</div>"
        + '<div class="hc-body">'
        + (def.summary ? '<p class="hc-summary">' + homeEsc(def.summary) + "</p>" : "")
        + '<div class="hc-loading"><i></i><i></i><i></i></div>'
        + "</div></section>";
    }).join("");
    grid.dataset.inited = "1";
  }
  homeRefresh();
}

function homeRefresh() {
  var grid = document.getElementById("homeGrid");
  if (!grid) { return; }
  return apiFetch("/console/home/summary").then(function (j) {
    _homeSummaryCache = j || {};
    homeRender(_homeSummaryCache.cards || {});
  }).catch(function (e) {
    if (e && e.message === "unauthorized") { return; }
    _homeSummaryCache = null;
    HomeCards.all().forEach(function (def) {
      var el = document.getElementById("homeCard-" + def.id);
      if (el) { homeSetState(el, "error", "数据加载失败，点击右上角刷新重试"); }
    });
  });
}

function homeRender(cards) {
  HomeCards.all().forEach(function (def) {
    var el = document.getElementById("homeCard-" + def.id);
    if (!el) { return; }
    var data = cards[def.id];
    try {
      /* 交互型卡片（keepAlive）：完成首渲染后不再被刷新重建。
       * 背景（2026-09-19 缺陷）：homeRender 每次刷新都重写 .hc-body，
       * 会把用户正在输入的内容（如资产定位输入框）瞬间冲空，造成
       * 「填了 IP 点定位却提示请输入线索 / 结果永不返回」的假故障。 */
      if (def.keepAlive && el.dataset.rendered === "1") { return; }
      if (!data) { homeSetState(el, "error", "摘要数据缺失"); return; }
      if (data.ok === false) {
        homeSetState(el, "error", "数据不可用，稍后自动重试");
        return;
      }
      if (data.pending) {
        homeSetState(el, "pending");
        return;
      }
      el.querySelector(".hc-body").innerHTML =
        (def.summary ? '<p class="hc-summary">' + homeEsc(def.summary) + "</p>" : "")
        + def.render(data);
      /* 成功渲染后才锁定，保证失败/占位态仍可被后续刷新纠正 */
      if (def.keepAlive) { el.dataset.rendered = "1"; }
    } catch (e) {
      homeSetState(el, "error", "卡片渲染异常");
    }
  });
}

/* ---------- 已移除：Phase2 stub 占位卡 ---------- */
/* 原实现为未接入模块预注册 7 张「卡片接入中」占位（nettest/ai/dpol/pc/kb/
 * config/sysadmin）。2026-09-19 用户明确反馈「多了好多没有要求增加的卡片」
 * —— 占位卡占据首页篇幅却无任何功能，属噪音，故移除：
 *   首页只呈现各模块**真实接入**的卡片（home/cards/{id}.js 自行 register）。
 * 服务端 home/summary 仍返回这些键的 pending 占位（向后兼容，前端不注册即
 * 不渲染）；对应模块 Phase2 接入时新增 cards/{id}.js 即自动出现。 */
