# net-doctor · 卡片设计规格（STYLE）

> 项目：观枢终端平台｜EyeTerm · Windows 终端「网络排障」子系统
> 基准：主应用「终端概览」三卡（hm-card 家族）设计语言；AI 智能诊断卡与五模块卡均已对齐。
> **规则：新建任何卡片必须遵循本规格；交付检查单含样式符合性自检，不合规 = E2E 门禁红。**
> 生效范围：主应用 `web/index.html`（tab-netdoctor + tab-home AI 卡）与独立页 `web/netdoctor-standalone.html`；两处样式需同步维护。

## 1. 卡片结构

- 外壳：`section-card`（主应用全局卡壳）+ 作用域类 `nd-styled`（五模块卡）/ `nd-ai-card`（AI 卡）。
- 头部：`.card-header` 内 `h3` 标题（可带序号 ①②③④⑤）+ `.disk-toolbar` 按钮组 + `.card-badge` 徽章。
- 体部：`.card-body`。
- 中心平台状态条：`.nd-statusbar`（条形卡），标题 `b` 与卡头同规格。

## 2. 字号 / 字重 / 颜色

| 元素 | 规格 |
|---|---|
| 卡头 h3 | 13.5px / 600 / `--accent-cyan` / letter-spacing .5px |
| 正文（表格/键值行） | 12.5px |
| 辅助说明（.nd-hint/.nd-summary） | 12px / `--text-secondary` |
| 徽章（.nd-badge 及派生） | 12px 级，状态色：ok=绿 / warn=橙 / err=红 / muted=灰 |

## 3. 间距

- 卡体 `.card-body`：`padding: 15px 17px`。
- 说明句 `.nd-summary`：`margin: 0 0 10px; line-height: 1.6`。
- 折叠头/分组标题与内容间距 ≥ 6px；卡片间依赖 section-card 全局 `margin-bottom`。

## 4. 行式布局（键值对）

- 类：`.hm-row`（AI 卡行式）或 `.nd-kv`（网卡卡/IP 冲突检测对象等）——同为左标签右值：
  `display:flex; justify-content:space-between; gap:10px; padding:4px 0; font-size:12.5px;`
  `border-bottom:1px dashed rgba(42,47,69,.6)`，末行 `:last-child` 去底线。
- 标签列 `flex-shrink:0` secondary 色；值列 `--text-primary`。
- 表格类数据（连通性节点表/路由追踪逐跳）保持 `<table class="nd-table">`，不强行行式化。

## 5. 按钮

- 主操作：`.nd-btn.primary`（实心主色）——「开始核查/开始检测/开始追踪/开始压测/提交诊断」。
- 次操作：`.nd-btn`；幽灵：`.nd-btn.ghost`；disabled 统一灰化（`--bg-hover` + secondary + not-allowed）。
- AI 卡提交按钮 `#ndAiBtn` 沿用 ndAi 作用域强化（30px 高 + 实心青）。

## 6. 空态

- `.nd-empty`：`--text-muted` / 12.5px / `padding:10px 0`（与 hm-empty 同口径，全 nd 模块统一）。
- 文案通俗、无实现细节（键名/配置文件/内部路径禁止出现——E2E 零残留断言）。

## 7. 表格

- `.nd-table`：12.5px；表头 secondary 色 600 + 底部实线 + 微底纹；数据行斑马（even `rgba(255,255,255,.015)`）+ 行底部细线（无竖线）。
- 单元格 padding 5px 9px；数字列 `.nd-num` 右对齐惯例。

## 8. 徽章

- 状态徽章统一走 `ndBadge(text, cls)` / `ndStatusBadge(status)`，类：`nd-ok/nd-warn/nd-err/nd-muted`。
- 卡头徽章（`.card-badge`）用于全局状态（如核查总评），颜色口径同上。

---
*2026-09-11 初版：AI 卡对齐（00e7a06/5d8af64）扩展至五模块卡 + 中心状态条（本版）。*


## 9. 宽度自适应（全局强制，终端与控制台一体适用）

**功能模块宽度必须自适应电脑分辨率**（2026-09-16 用户定）：

1. 容器宽度一律流式布局（百分比 / flex / grid），**禁止固定像素宽度**（内容性最小宽 min-width 除外）；页面主体 max-width 上限用大断点（如 1600px）而非写死窄值
2. 多栏模块必须设降级断点（如 ≤1080px 单列堆叠），断点外不得出现横向挤压、贴边、溢出
3. 新页面 E2E 必须含多宽度断言（至少 1920 / 1440 / 1080 三档：关键容器宽度随视口变化、无横向滚动条）
4. 本节为全局规范：终端侧各页与控制台（server-platform console）一体执行；既有页面逐批整改（整改项进各自 E2E 防回归）

