# Log Inspector 项目决策记录（ADR）

> 项目：系统日志诊断（Log Inspector）— 观枢终端平台｜EyeTerm 的日志诊断子系统。
> 本文件是项目记忆：所有影响后续开发的决策以 ADR 形式追加，每次追加需 git commit。

## ADR-001 项目定位与同步契约（2026-09-06）

- Log Inspector 为独立项目（本仓库），**演进以本项目为准**；发布时同步回 winhelper 主应用。
- 本 agent 拥有的引擎文件：`log_service.py`（服务层）、`log_reader.py`（事件日志引擎，自主应用迁入并演进）、`web/loginspector.js`（前端业务逻辑）。
- 本 agent **获授权对主应用做菜单合并手术**：
  - `web/index.html`：导航「仪表盘/日志查看/故障分析/知识库」4 按钮合并为「日志诊断」1 按钮（data-tab=loginspector，置最左，默认激活）；4 个旧 tab section 删除；新增 tab-loginspector section。
  - `web/app.js`：保留传输层（apiFetch/waitPywebviewBridge/detectMode）、initCollapsibleCards/toggleAllCards；switchTab 删旧 4 分支、加 loginspector 守卫；删除旧 4 tab 专属函数（reloadData/updateDashboard/drawTimeChart/drawSourceChart/loadLogs/renderLogsTable/loadAnalysis/renderFaults/toggleFault/getSeverityLabel/loadKnowledge）；**保留 showError/escapeHtml/truncate**（disk.js/appdata.js 仍在调用，属公共工具非旧 tab 专属）。
  - `bridge.py`：删除 /api/analyze、/api/events、/api/faults、/api/log-types、/api/knowledge 旧路由；新增 /api/loginspector/* 路由组。
  - `service.py`：删除 handle_analyze/handle_events/handle_faults/handle_log_types/handle_knowledge（确认 disk/perf 无引用），只保留磁盘相关 re-export。
- 禁止触碰：perf_service.py、web/perf.js、disk_cleanup.py、appdata_scan.py、web/disk.js、web/appdata.js、web/style.css、perf-analyzer/、disk-cleaner/、web/vendor/。

## ADR-002 时间范围过滤与流式读取（2026-09-06）

- 大日志量禁止一次性载入内存：`log_reader.iter_events()` 以生成器逐批读取（win32evtlog BACKWARDS_READ，从最新往回翻页），每批按 since/until 剪枝——事件时间早于 since 时整体停止（后端数据按时间倒序），晚于 until 时跳过。
- 检索/导出/分析三条链路共用该迭代器；检索走"边读边过滤+上限"模式（默认上限 20000 条，防内存爆炸），导出走"边读边写盘+增量 flush"模式。
- 级别/来源/关键字/事件ID 过滤在 Python 侧完成（事件日志 API 无原生过滤）。

## ADR-003 原始日志导出任务模型（2026-09-06）

- 用户明确要求：导出 txt（**utf-8-sig** BOM 便于记事本直接打开）、头部元信息行、每事件一块（[时间] [级别] [来源] [事件ID] + 描述）；**默认条件 = 近 3 天全量系统日志**。
- 任务模型参照 disk_cleanup：task_id + daemon 线程 + 轮询（export-start/export-status/export-cancel）；cancel threading.Event 每批检查，立即终止；写盘逐批 flush；输出目录默认 `%USERPROFILE%\Downloads`（环境变量组装，禁止硬编码盘符）；完成后前端展示路径 + 「打开位置」（复用 /api/disk/open-location）。
- 文件名：`LogExport_YYYYMMDD_HHMMSS.txt`。
- 铁律：**报告类一律 HTML**（见 ADR-005），原始日志按用户要求 txt，两者分开。

## ADR-004 Security 类别权限降级（2026-09-06）

- Security 日志读取需管理员权限。实现：`log_reader.check_log_access(log_name)` 用 OpenEventLog 探测，失败即标记该类别不可用。
- search/analyze/export 统一返回 `denied: [类别名]` 列表 + 每类别 error 文案；前端对不可用类别置灰并提示"需管理员权限"，其余类别正常工作。任何失败优雅降级（success=false + error），不影响其他菜单。

## ADR-005 报告 HTML / 原始日志 txt 分离（2026-09-06）

- 智能分析报告导出为**单文件自包含 HTML**（深色风格，与 perf 报告规范一致），头部标注「观枢终端平台｜EyeTerm」，文件名 `LogAnalysis_YYYYMMDD_HHMMSS.html`，写入 Downloads，导出后同样支持「打开位置」。
- 报告内容：结论徽章、统计概览、故障模式列表（证据 + 处理建议）、已知问题事件表、检索条件元信息。

## ADR-006 前端命名空间策略（2026-09-06）

- `loginspector.js` 所有函数/变量使用 `li` 前缀（liSearch、liRunAnalysis…），唯一公开入口 `initLogInspectorTab()`；内部私有工具函数（liEscapeHtml/liTruncate/liShowError）自包含实现，**不依赖 app.js 的 truncate/escapeHtml/showError**，避免与主应用脚本互相污染，同时保证 standalone 页面可独立运行。
- 与 app.js 共存的重复实现（escapeHtml 等）属有意为之（前缀隔离），esprima 不会误报。
- esprima 门禁：禁用 `?.` 可选链（esprima-python 不支持）；警惕 esprima 检不出同作用域重复 const/let（disk 项目事故教训），新函数一律 li 前缀规避。

## ADR-007 智能分析输出结构（2026-09-06）

- `analyze` 返回：`conclusion`（level: critical/error/warning/info + text）、`patterns[]`（name/severity/count/evidence[≤5 条事件样本]/suggestions[]）、`known_issues[]`（CRITICAL_EVENT_IDS 命中，≤50 条）、`summary`（级别统计 + 时间分布 + 来源 TOP）。
- 每条 pattern 自带 FAULT_PATTERNS 的 suggestions → 前端「知识库建议」区直接消费；另设知识库自由检索框（/api/loginspector/knowledge?q= 按名称/事件ID/关键字过滤知识卡）。
- 结论级别判定：patterns 中存在 critical → critical；否则 error → error；否则 warning → warning；否则 info（系统状态正常）。

## ADR-008 E2E 门禁（2026-09-06）

- `tools/e2e_loginspector.py`（Playwright + pywebview 桩）双场景：
  1. standalone：file:// 打开 loginspector-standalone.html，全流程断言（检索→统计/图表/表格→下载任务→分析→知识库→报告导出按钮），pageerror=none。
  2. 主应用：file:// 打开 web/index.html，3 菜单遍历（日志诊断/磁盘清理/性能分析），断言 disk/perf 关键函数 typeof=function 保持（initDiskTab/startJunkScan/startLargeScan/renderTreemap/initPerfTab/perfStartRecord/perfStopRecord/renderPerfReport 等）+ 新函数 initLogInspectorTab 在列 + 数据填充 + pageerror=none。
- 后端真实冒烟：win32evtlog 真实读 System 近 3 天，导出 txt 行数>0 且格式抽检（BOM/元信息行/事件块分隔线）；非 admin Security 降级；分析引擎对真实数据输出结论。

## ADR-009 首次交付记录（2026-09-06）

- **门禁结果**：E2E 双场景 43/43 全绿（standalone 27 + 主应用 16，pageerror=none）；后端真实冒烟 29/29 全绿（真实读本机 System 近3天 315 事件，txt 导出 160KB/BOM/事件块抽检，Security 非管理员 denied 降级，分析结论 critical，HTML 报告 15.9KB 落盘）；esprima 双份 JS 通过。
- **提交**：log-inspector `ec02063`/`7011af8`；主应用 winhelper `04b9500`。
- **exe 重建**：`dist_new\winhelper.exe`（26,297,621 B）→ 替换 `dist\winhelper.exe` → Start-Process 启动成功（构建时无运行实例，未触及管理员权限问题）。
- **教训（ADR-006 补充）**：standalone 页面无 app.js，loginspector.js 不得直接调用 apiFetch——已内置 liApiFetch（typeof apiFetch 守卫 + pywebview 桩 + fetch 三级回退）。该问题 E2E 首跑即暴露（全流程静默失败但 pageerror=none，因 try/catch 吞掉 ReferenceError）——**验证门禁必须断言数据填充而非仅 pageerror**。
- **导出时间格式**：TimeGenerated.Format() 输出随系统本地化变化，导出块统一用 datetime strftime 格式化（冒烟抽检 [YYYY-MM-DD HH:MM:SS] 头后通过）。
- **补充需求落地**：①顶栏 logTypeSelector 下沉为条件区类别多选（System/Application/Security/Setup，Security 无权限自动置灰）；②modeBadge 固定显示「Windows」（detectMode 仅保留桥接就绪检测），点击弹出「本机基础信息」面板（OS/主机名/网络/CPU/内存条/物理盘介质/显卡独显核显），数据源 /api/perf/hwinfo（消费 perf-analyzer-dev 扩展的 os/hostname/network 字段，防御性渲染），启动异步预取+失败重试不报 error。
- **已知限制**：①Security/Setup 在非管理员/无该日志的系统上优雅降级（置灰或 denied 提示）；②检索上限 20000 条（超出截断提示）、分析上限 30000 条；③故障模式关键词匹配沿用主应用 FAULT_PATTERNS 精度（如 disk/IO 关键词较宽泛）；④/agent 明确主应用页面中 escapeHtml/truncate/showError 保留供 disk.js/appdata.js 使用，loginspector.js 用 li 前缀私有实现，存在有意为之的重复实现。
