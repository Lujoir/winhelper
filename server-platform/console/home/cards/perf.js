/* 首页卡片 · 终端性能分析（数据契约见 server/api_home.py _card_perf）
 *
 * 口径说明：阈值与平台瓶颈规则引擎（ADR-006）严格一致 ——
 * CPU > 85%、内存 > 90% 记为高负载，避免同一平台两处口径打架。
 * 单卡失败由框架降级为「数据不可用」，不阻塞整页。 */
"use strict";

/* 数值着色：达阈值红、接近阈值（85%）黄，其余常规绿 */
function perfVal(v, threshold) {
  if (v === null || v === undefined || v === "") { return "-"; }
  var cls = v > threshold ? "v-err"
    : (v > threshold * 0.85 ? "v-warn" : "v-ok");
  return '<b class="' + cls + '">' + homeEsc(v) + "%</b>";
}

HomeCards.register({
  id: "perf",
  title: "终端性能分析",
  summary: "全平台资源负载与近期瓶颈事件概览，明细见终端监控。",
  order: 1.5,

  render: function (d) {
    var actions = homeActions([
      { label: "进入终端监控", fn: "switchTab", arg: "monitor",
        primary: true },
      { label: "刷新摘要", fn: "homeRefresh" }
    ]);
    if (!d.covered) {
      return '<div class="hc-empty">暂无性能数据'
        + '<span class="hc-empty-sub">终端上报指标后自动汇总</span></div>'
        + actions;
    }
    var html = homeRows([
      ["指标覆盖", d.covered + " / " + d.online + " 台在线", false],
      ["平均 CPU", perfVal(d.cpu_avg, 85), true],
      ["平均内存", perfVal(d.mem_avg, 90), true],
      ["高负载终端", '<b class="' + (d.busy > 0 ? "v-warn" : "v-ok") + '">'
        + homeEsc(d.busy) + "</b> 台", true],
      ["近 24h 瓶颈事件", homeEsc(d.bottleneck_24h) + " 条", false]
    ]);
    return html + actions;
  }
});
