/* 首页卡片 · 客户端发布（home-console-dev 维护；数据契约见 server/api_home.py） */
"use strict";

HomeCards.register({
  id: "release",
  title: "客户端发布",
  summary: "当前发布版本与版本库概况。",
  order: 3,

  render: function (d) {
    if (!d.current_version) {
      return '<div class="hc-empty">尚未发布客户端版本</div>'
        + homeActions([
          { label: "进入客户端发布", fn: "switchTab", arg: "release",
            primary: true }
        ]);
    }
    var html = homeRows([
      ["当前版本", '<b class="v-ok">' + homeEsc(d.current_version) + "</b>", true],
      ["发布时间", homeFmtTs(d.published_at), false],
      ["版本库累计", d.total_releases + " 个", false]
    ]);
    if (d.release_note) {
      html += '<p class="hc-note">' + homeEsc(String(d.release_note).slice(0, 60))
        + (String(d.release_note).length > 60 ? "…" : "") + "</p>";
    }
    html += homeActions([
      { label: "进入客户端发布", fn: "switchTab", arg: "release", primary: true }
    ]);
    return html;
  }
});
