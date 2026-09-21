/* 首页卡片 · 资产管理（home-console-dev 维护；数据契约见 server/api_home.py） */
"use strict";

HomeCards.register({
  id: "assets",
  title: "资产管理",
  summary: "资产分组与终端关联情况总览。",
  order: 2,

  render: function (d) {
    var html = homeRows([
      ["平台资产组", d.platform_groups, false],
      ["安全分组", d.huorong_groups, false],
      ["待关联终端", '<b class="'
        + (d.other_total > 0 ? "v-warn" : "v-ok") + '">'
        + homeEsc(d.other_total) + "</b>", true],
      ["待关联在线", d.other_online, false]
    ]);
    if (!d.platform_groups && !d.huorong_groups) {
      html += '<div class="hc-empty">暂无资产分组</div>';
    }
    html += homeActions([
      { label: "进入资产管理", fn: "switchTab", arg: "assets", primary: true }
    ]);
    return html;
  }
});
