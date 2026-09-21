/* 首页卡片 · 终端监控（home-console-dev 维护；数据契约见 server/api_home.py） */
"use strict";

HomeCards.register({
  id: "monitor",
  title: "终端监控",
  summary: "全平台终端接入与在线状态总览。",
  order: 1,

  render: function (d) {
    var html = homeRows([
      ["终端总数", d.total, false],
      ["在线", '<b class="v-ok">' + homeEsc(d.online) + "</b>", true],
      ["离线", '<b class="' + (d.offline > 0 ? "v-err" : "v-ok") + '">'
        + homeEsc(d.offline) + "</b>", true]
    ]);
    var vc = d.version_counts || {};
    var keys = Object.keys(vc).sort(function (a, b) {
      return vc[b] - vc[a];
    }).slice(0, 2);
    if (keys.length) {
      html += homeRows(keys.map(function (k) {
        return ["客户端版本 " + k, vc[k] + " 台", false];
      }));
    } else {
      html += '<div class="hc-empty">暂无终端接入</div>';
    }
    html += homeActions([
      { label: "进入终端监控", fn: "switchTab", arg: "monitor", primary: true },
      { label: "刷新摘要", fn: "homeRefresh" }
    ]);
    return html;
  }
});
