# net-doctor · 决策记录（ADR）

> 项目：观枢终端平台｜EyeTerm · Windows 终端「网络排障」子系统（对外名称：网络排障，2026-09-11 定名）
> 规则：一条 ADR 记录一个不可逆/高影响决策；新决策追加编号，不改写旧条目。

---

## ADR-033 · 连通性 TCP/UDP 协议检测 + 节点自主增删（2026-09-15）

**背景**（用户需求）：连通性测试仅 ICMP/DNS/NTP 三协议；节点表固定不可增删。

**决策**：
1. **协议扩展**：method 值域承载协议 ∈ {ping, nslookup, ntp, tcp, udp}（单字段收敛，避免 proto/method 双轨漂移）；旧配置缺省默认 ping 兼容；默认表追加 TCP 示例锚点节点（baidu.com:443，用户点名场景）。
2. **TCP**：socket create_connection 3s → 通/不通+耗时；**UDP 语义如实分级**：53/123 已知协议端口发真实 DNS/NTP 报文确定判定；通用端口收应用响应=通、ICMP 端口不可达（WSAECONNRESET 10054）=不通、**超时=「无响应」中性态（绝谎报失败）**——徽章 no_response=橙「无响应」+detail 说明、jsonl 记 warn。
3. **TCP/UDP 目标 host:port 端口必填**（_sanitize_nodes 白名单校验，错误消息更新）。
4. **节点自主增删**：行内删除（nd-btn-danger）→ **uiConfirm danger「删除节点确认」**（ES5 then 链消费 Promise，避免 async 破坏 esprima 门禁）；卡内「添加节点」折叠表单（名称/目标/协议下拉/端口必填联动）；「恢复默认节点」走 config `nodes_reset=1`（新参数：仅重置 nodes 保留基线/个人版）；全部走 config SET merge 原子写。
5. **双入口共存决策**：设置弹窗节点维护（改名/改目标/DNS 基线）与卡内增删**互补不收敛**——卡内高频增删就地操作，设置内低频改名+基线统一编辑，数据同源 SET merge 无冲突。
6. **独立页 uiConfirm 内联副本**（netdoctor-standalone 无 app.js，逐字同步 app.js 组件、按钮类适配 nd-btn 体系，perf/app-lite 先例）。

**门禁**：E2E 325/325（+15：协议列文案/TCP 结果/UDP 无响应徽章与说明/添加→行出现+config 参数/删除 uiConfirm danger→行消失/空态恢复引导/nodes_reset 调用）；py_compile+esprima；副本 MD5 MATCH。

**结果**：采纳。

---

## ADR-032 · 补采模态热修三连 + AI 历史写路径加固（2026-09-14）

**编号说明**：ADR-016~031 为 2026-09-10~14 迭代期编号（ADR-016 五模块卡对齐+STYLE.md、ADR-031 routetrace AI 契约等，标题行未落档本文件，事实以编年史/commit 为准），本条接续编号。

**背景**（用户实锤三缺陷）：①补采模态打开即显示「排队」但任务并未发起（假象），且 Go/Skip 提交路径零进行中反馈；②ai_history.jsonl 0 字节（写空/从未写入）；③aiSubmitting 异常路径不复位——补采链 Promise 挂死或 ndAiHandleError 自身异常时，后续 .then(复位/关闭模态) 永不执行，模态卡死 + 防重入标志永挂起。

**决策**：
1. **排队假象消除**：模态行初始文案「排队」→「待采集」（点击「采集并提交」后才进入发起态）。
2. **提交反馈补齐**：Skip/Go 发起瞬间模态内 tip「诊断提交中（模型链约 30~120 秒）…」（Go 先显「补采中…」，进入提交阶段切换）+ 三按钮统一禁用 + 收尾必达 finish。
3. **复位链加固**：新增 `ndAiSafeError`（ndAiHandleError 全异常包裹，兜底 tip 直写）——主提交链与模态 Skip/Go 的 catch 全部改走安全包装；finish 用 try/finally 保证 resolve 必达；补采轮询 pollNd 加 150s 硬上限 + settled 防双回调（超时该源降级继续，不再沿用 ndPollTask 5 分钟上限假死）。
4. **AI 历史写路径加固**（net_service.py）：`_write_ai_history` 前置目录 makedirs；append/delete 三处写盘异常显式返回 `history_write_failed`（不再静默上抛）。
5. **门禁**：E2E +2（模态初始态无「排队」假象、Skip 发起瞬间 tip+三按钮禁用）；冒烟 130/130 维持全绿。

**结果**：采纳。E2E 306/306 pageerror=0、冒烟 130/130、三仓副本 MD5 MATCH。根因备注：ai_history 0 字节的运行时主因更可能是 exe 版本滞后（路由未含）叠加无成功诊断，代码层已消除写路径静默失败面。

---

## ADR-015 · AI 辅助分析字段修复 + 手动重跑升级（2026-09-10）

**背景**：①api-registrar-dev/platform 冒烟实证——IP 冲突自动 AI 分析提取链 `analysis/content/result` 与平台 /ai/analyze 实际响应字段 `response` 不匹配，分析文本永远「（无内容）」；②用户要求多方日志结合依托平台算力分析冲突（服务端聚合分支 server-platform-dev 并行实现中，就绪前行为=现状）。

**决策**：
1. **提取链修复**（net_service.py）：抽公共 `_ai_analyze_issue(tid, issue, timeout=45)`——提取链 `response → analysis → content → result`，analysis_id 透传；run_ipconflict_result 与手动重跑共用。
2. **手动重跑**：新路由 `/api/netdoctor/conflict-ai-reanalyze`（NET_ROUTES + bridge ROUTES 同步挂载——按 [16] 检查单铁律）——params {ip, mac, evidence_json?}（最新证据 JSON 数组，最多 12 条，非法 JSON 容错），issue 构造与自动触发同格式，45s 超时。
3. **UI 升级**（netdoctor.js）：AI 辅助分析区统一 `ndRenderAiAssist`（分析文本 + model 徽章 + #analysis_id 可追溯）；结果区新增「AI 分析（平台多方证据聚合）」按钮——点击分析中提示（最长约 45s）→ 成功渲染于 #ndAiReBody / 失败提示「可重试」且按钮复位；suspect 自动触发保持不变。
4. **门禁**：smoke [17]（提取链 response 命中/旧链回退/空响应/失败态/analysis_id 透传/endpoint+timeout/invalid_ip/issue 构造含 ip+mac+证据/16 传 12 截断/ROUTES 注册）+ [16] bridge 全键覆盖自动兜住新路由；E2E +10（自动 AI 徽章渲染、手动成功/失败重试/重试成功/发起参数携带 ip+mac+evidence_json、场景 2 函数可用）。

**结果**：采纳。E2E 222/222 pageerror=0（+10）+ 冒烟 103/103（+12）。服务端聚合分支上线后本侧无需改动（handler 已透传最新证据，服务端就地聚合）。

---


**背景**：服务端深度检测引擎上线并冻结契约（server-platform commit 9e604a7+cc34bae，生产 172.17.5.215:18090 已实测）：平台侧后台编排 resolve/arp/nad/macaddr/conclude 五步，任务秒级创建、全链约 9-30s；steps 带逐命令执行明细与 evidence，verdict 四态（confirmed/suspect/normal/insufficient_evidence）+ 中文 reasons + sources 三源状态。

**决策**：
1. **本地纯转发，不占本地任务引擎**（net_service.py）：`handle_net_conflict_deep_start`（POST 平台 ipconflict-deep，{ip, mac}；缺省时本地采集——`_resolve_uplink_route_adapter` + `_pick_conflict_adapter` 与常规检测同源锁定中心路由出口网卡；ipaddress 本地预校验，mac 归一为大写冒号）与 `handle_net_conflict_deep_poll`（GET 平台 task 视图，task_id 经 quote 编码直接透传）。错误映射 400→invalid_ip / 404→not_registered / 429→busy；不新增路由文件依赖，ROUTES 注册两条 `/api/netdoctor/conflict-deep-{start,poll}`。
2. **UI 挂常规检测结果尾部**（netdoctor.js）：IP 冲突结果卡内 `.nd-ai` 区块含「发起深度检测」按钮 + `#ndDeepBody`；轮询 2.5s × 60 轮上限（150s 兜底），running 阶段渲染已到步骤时间线、瞬时网络抖动 catch 不终止轮询；done 后渲染 verdict 徽章（四态色阶）+ 结论依据 + 数据源三源如实 + 核验对象/时间，steps 渲染为 `.nd-step` 时间线卡（状态徽章含 match/mismatch/multi/empty 语义色，multi 显式标「多端口（漂移信号）」），evidence 等宽 `<code>` 列表（macaddr 步骤 MAC 表原始行不去重，正是漂移检测信号），commands 走 `<details>` 折叠（cmd + 失败标注 + output_tail）。
3. **防重入与复位**：发起后按钮禁用，完成/失败/超时/错误路径统一 `ndDeepBtnReset()` 复位；成功完成可再次发起。
4. **门禁**：冒烟 [15] 15 断言（mock `_platform_post/_platform_get`：未连/缺参/成功透传/路径与 tid/非法 IP 本地拒/429/404/缺省采集兜底/poll 透传与编码/404/ROUTES）；E2E 场景 1 十四断言（发起携带 ip/mac query、running 步骤渐进渲染、done verdict+reasons+三源+5 步时间线+漂移徽章+banner+evidence+命令折叠、按钮复位、429 busy 提示）+ 场景 2 主应用上下文回归。

**结果**：采纳。E2E 210/210 pageerror=0（+20）+ 冒烟 87/87（+15）。遗留：真实中心全链路 UI 压测随下次联调验证（服务端已实测 9s 完成）。

**热修复增补（2026-09-10 晚，api-registrar-dev 代码实证发现）**：bridge.py ROUTES 漏挂载深度检测两条路由——NET_ROUTES 已定义但主应用 ndApiFetch 经 bridge.call 分发报「未知接口」，v8 主应用内按钮实际不可用；E2E 场景 2 因 pywebview 桩注入未走真实 bridge 而漏检。修复：bridge.py import +2 与 ROUTES +2（纯挂载）。门禁固化（smoke [16]）：动态加载主应用 bridge.py（NETDOCTOR_BRIDGE_PATH 可注入，默认 workspace 根）①ApiBridge.call 真实链路跑两条新路由断言非「未知接口」+ handler 真实执行（not_connected）②**ROUTES 覆盖 NET_ROUTES 全部键**——今后 NET_ROUTES 任何新增若 bridge 漏挂载，冒烟直接红。**教训入档交付检查单：凡 NET_ROUTES 新增，交付前必须确认 bridge.py ROUTES 同步挂载 + 真实链路冒烟（桩 E2E 不覆盖 bridge 路由层）。**回执修订（checked_at 本地化 ndFmtEpoch + insufficient_evidence 降级提示）与门禁升级后：E2E 212/212、冒烟 91/91。

---


**决策**（五件套合批）：
1. **设置弹窗两卡化**：网络监测配置卡 / AI 诊断·个人版卡各自 `.nd-set-card` 独立卡（样式与 perf 端统一），**各带独立保存**（网络卡保存 nodes+DNS 段；AI 卡 ndSaveAiPersonal 仅提交 ai_personal 段，走既有 ai_personal_json 通道）；挂载 `#ndSettingsHost`（perf 端容器），不存在时回退动态创建 append 到 appSettingsBody；UI 零实现细节（app_config.json/netdoctor.* 键名等说明整行删除），动态目标说明降级为输入框 title。
2. **补采模态**：缺失源不再用原生 confirm——应用内自绘模态「完善诊断数据源」，可补采三类（路由追踪→tracert-start 目标取节点表 center 动态值；网络压测→stress-start 快档，未连中心禁勾标注；性能压测→前端直调 perf stress-start?mode=full 并轮询 stress-status，**零后端新增**）；不可补采类给单源「重新采集」按钮；三按钮「采集并提交（失败源降级证据不足照常提交）/直接提交/取消」；模态期间 aiSubmitting 保持置位，POST 收尾后才 resolve 复位（防重入窗口覆盖补采全程）。
3. **URL 归一化防假阳性**：`_personal_norm_url` 折叠路径连续斜杠（https://host//v1/... 落站点兜底页 200-HTML 的假「可达」）；连通性测试 200 响应体校验——HTML 或非 {data:…} JSON 结构判 ok=False 并提示「该地址不是 API 接口」。
4. **IP 冲突检测对象**：`_resolve_uplink_route_adapter`（PowerShell Find-NetRoute → 源 IP → 接口名/MAC）锁定与中心通信网卡，`_pick_conflict_adapter` 纯函数按 MAC 匹配候选（smoke 可单测），失败回退活动网卡并如实标注；结果透出 object_note + verdict.suspect_reasons 清单 + nic_history 折叠小节。
5. **AI 卡头部按钮组**：ndAi 作用域限定样式（实心激活段/主色提交按钮/禁用灰化/等宽等高/贴右缘），不波及 nd-btn 其它使用位置。

**结果**：采纳。E2E 190/190 pageerror=0 + 冒烟 72/72。已知交互时序：模态 POST 收尾先于 resolve（防重入窗口覆盖补采全程）。

---

## ADR-012 · 模块更名「网络排障」→「网络监测配置」+ 默认表保障（2026-09-10）

**决策**：
1. **更名仅限用户可见文案**：主应用导航项、tab-netdoctor 页头标题、设置弹窗区块标题（ndRenderSettings）、standalone 页头/title/nav-tag、压测导出 HTML 报告 brand。**内部标识一律不动**：tab id="netdoctor"、路由 /api/netdoctor/*、nd 前缀、bridge ROUTES / NET_ROUTES 键、app.js switchTab 判断——更名零行为与兼容面影响。
2. **默认表保障（运维口径）**：net_service.py DEFAULT_NODES 恒为完整 8 节点（含温州总院 ntp.eye.ac.cn 与核心/数据中心/DMZ 目标）；全新环境（无 app_config.json）首屏即默认表；「恢复默认」重置为同一完整表。**旧版本初始化的 app_config.json（nodes 已保存）不会自动升级新默认表——机器上点一次「恢复默认」即可**。
   防回归：smoke [14] 全新环境 8 节点/温州总院目标/自定义写入/恢复默认四断言。

**结果**：采纳。E2E 更名断言（导航+页头+独立页 title/h2）。

**修订（2026-09-11）**：用户定名回退——「网络监测配置」废弃，**「网络排障」为最终定名**。按本 ADR 改动面反向回退（导航/页头/设置卡标题/独立页 title+nav+页头/压测报告 brand 共 9 处用户可见文案），内部标识仍零改动；E2E 更名断言同步回退。同日 ADR-016 将五模块卡与中心状态条排版对齐 AI 卡基线并固化 STYLE.md。

---

## ADR-011 · AI 诊断证据饥饿与可信性终端侧加固（2026-09-10）

**背景**：analysis_id=16 实证三缺陷——system_log 24h 窗口证据饥饿（服务端每类 4KB 截断，重启时段事件不在场）致模型虚构 WHEA-Logger 17 等「日志依据」；事件 description 多为 `<The description...>` 占位符浪费预算；os_info 中文乱码；response 中 `**`/反引号未清洗直接渲染。

**决策**（终端侧五项，服务端截断/预算修复由 server-platform-dev 并行）：
1. **提示词硬约束**（`_ND_AI_PROMPT_SYSTEM`，个人版直调生效；企业版 prompt 在中心侧由服务端同款加固）：只允许引用随请求日志中实际出现的事件 ID/来源/时间戳，严禁编造；引用须带原文时间戳；某类日志缺失/截断必须显式声明证据不足。
2. **system_log 采集瘦身**（`ndAiSlimEvents`）：占位符描述替换「(描述缺失)」、单条描述 200 字符截断（log_service DESC_TRUNC=2000，终端侧收紧 ~10×），32KB 预算内事件密度大幅提升；兼容真实字段 description 与旧 desc。
3. **os_info 乱码定位结论（不改码）**：实证链路——perf_service hwinfo 采集 `os.caption='Microsoft Windows 11 专业工作站版'` 正确；`_platform_post` `ensure_ascii=False + encode("utf-8")` 正确；bridge `json.loads(str)` 正确。剩余嫌疑在服务端接收/截断/落库环节（含 UTF-8 多字节拦腰截断可能），转 server-platform-dev。
4. **渲染清洗**（`ndAiCleanMd`）：结构化与纯文本两分支统一剥离 `**`/反引号。
5. **透明化**：诊断日志折叠态标题行实时汇总「已采集 N/8 源 · 共 X KB」（`ndAiUpdateAgg`，采集完成即更新）；勾选源存在未采集成功时提交前 `confirm` 明示源名单与影响，可取消。

**E2E 桩对齐**：loginspector 事件桩从虚构字段（time_text/level/desc）修正为真实 API 结构（`_public_event`：timestamp/level_name/description）——沿用 hwinfo 结构对齐教训。

**结果**：采纳。E2E 166/166 pageerror=0（+6）+ 冒烟 65/65（+4 提示词断言）。

---

## ADR-010 · AI 智能诊断卡宿主迁移主页（2026-09-10）

**背景**：用户要求 AI 诊断卡从「网络排障」页移到「主页」终端概览之上（移动非复制）；网络排障保留 5 模块；独立 standalone 页不动。

**决策**：
1. **宿主迁移**（主应用 index.html）：AI section-card（data-collapse 保留）移入 `#tab-home` 的 `.home-wrap` 顶部（终端概览 header 之前）；tab-netdoctor 内删除。nd-* 样式块仍留在 tab-netdoctor 的 `<style>` 内（style 全局生效，迁移不影响渲染）。
2. **初始化/刷新语义扩展**（netdoctor.js + app.js）：
   - boot IIFE 激活判定从「tab-netdoctor active」扩展为「tab-netdoctor 或 tab-home active」——主应用启动（home 为默认激活页）即初始化 AI 卡；
   - `app.js switchTab` home 分支同样触发 `initNetDoctorTab()`（幂等：全量初始化一次，之后每次激活仅重查中心状态）；
   - visibilitychange 恢复刷新条件同步覆盖主页。
   - home.js 的 30s 平台接入轮询与主页三卡逻辑零改动；`toggleAllCards` 按 `.tab-content` 作用域隔离，网络排障页「收纳全部」自动变为仅作用其余 5 卡，主页不加该按钮。
3. **同步契约不变**：net-doctor 拥有 net_service.py + web/netdoctor.js；index.html/app.js 按 628c210 先例由本项目执行集成增量。

**结果**：采纳。E2E 场景 2 新增断言「#tab-home 内存在 #ndAiBtn 且 #tab-netdoctor 无残留、主页激活即初始化可用」；AI 提交流验证改在主页上下文执行。

---

## ADR-009 · AI 诊断九项优化：gate 语义修正 + 双模式（企业/个人）（2026-09-10）

**背景**：用户实测反馈 8+1 项优化。核心 Bug（第 0 项）：服务端心跳在线但 AI 模块永久置灰「未连接中心」——根因是 `initNetDoctorTab` 的 `ndState.inited` 一次性守卫使 `ndLoadUplink()` 只在首次激活拉取；首帧 uplink 处于 connecting/注册退避期时 gate 误判，且应用内切换菜单不触发 visibilitychange，无任何补救路径。

**决策**：
1. **gate 语义修正**：`initNetDoctorTab` 每次激活都重查 `ndLoadUplink()`（全量初始化仍一次性）；`ndAiSubmit` 提交前对未连接态强制重查一次（双保险）。重查逻辑此后是长期语义：**中心状态是"每次进入模块时的瞬时值"，不是缓存值**。
2. **AI 双模式**（第 9 项，main 定）：
   - 企业版 = 现有转发代理（`_ai_diagnose_enterprise`，原逻辑不动）；
   - 个人版 = 本机直连第三方 OpenAI 兼容 API（`_ai_diagnose_personal`，urllib POST /v1/chat/completions，120s 超时），**不依赖中心**——gate 仅对企业版生效；
   - 三段结构提示词在 net_service 常量沉淀一份（`_ND_AI_PROMPT_SYSTEM`，与 server-platform/server/ai.py DIAG_SYSTEM_PROMPT 语义对齐）；个人版直调使用，企业版 prompt 在中心侧（转发契约 issue+logs 不变）。
   - 个人版配置存 `app_config.json netdoctor.ai_personal`（api_url/api_key/model，本机明文与 uplink token 同级）；api_key 留空=不修改，GET 回传 has_key 脱敏；恢复出厂（reset）保留凭据；`/api/netdoctor/ai-personal-test` 轻量 GET /models 验证（bridge 同步 +1 路由）。
   - 模式切换 segmented 记忆 localStorage `nd_ai_mode`；个人版历史无 analysis_id，UI 标「本地」/「本地诊断」。
3. **日志源时间维度**（第 7 项）：system_log 走 loginspector search 现有 hours/start+end 参数；连通性历史在 `handle_net_ping_history`/`ping_history` 新增 hours / start_ts+end_ts 过滤（epoch 秒）。perf 记录类平台接口不支持范围检索（perf_service 契约文件禁改）不提供时间选择——「如可行也支持」判定为不可行。
4. **数据源新鲜度**：③④⑤ 完成时钩子重采对应 AI 子源（net_conn/net_tracert/net_stress），提交前 `ndAiFreshNetSubs` 强制刷新三子源——杜绝"预采集早于实测 → 子源永久『尚未追踪/压测』"陈旧态。

**结果**：采纳。验证：E2E 153/153 双场景 pageerror=0（基线 112 → 139（八项）→ 153（第九项））+ 冒烟 61/61 + py_compile/esprima。真实第三方 API 联调待用户配置后开箱验证。

---

## ADR-008 · AI 智能诊断：转发代理 + bridge.call 双参增量（2026-09-09）

**背景**：第六模块「AI 智能诊断」需把前端聚合的六类日志（单类 ≤32KB，总 ≤4MB）提交平台 `POST /api/v1/terminals/{tid}/ai/diagnose`（X-ETP-Token）。两条路径二选一：前端直连平台（token 无法安全到达前端，否决）vs net_service 转发代理（采纳——token 仅后端注入，复用 `_platform_post` 语义）。

**决策**：
1. 提交路径 = net_service 新增 `handle_net_ai_diagnose(params, body)` 转发代理（ADR-002「不 import uplink/自包含」约束下的**记录在案破例**：仅新增平台转发，不 import uplink，语义复制不变）。
2. 大 payload 过桥：bridge.ApiBridge.call 签名扩为 `call(path, body=None)`——仅 `/api/netdoctor/ai-diagnose` 特判解析 body JSON，其余路由行为不变（向后兼容，旧单参调用零影响）。bridge 属 net-doctor 集成点文件，此为本项目契约内增量。
3. 日志收集按 main 规格**前端聚合**（零跨模块 import）：六类并行调用现成 bridge 路由（hwinfo/record-report/stress-status/loginspector.search + netdoctor 自有 network 数据），失败降级「不可用」不阻断；单类前端截 32KB（与服务端契约双保险），总体 >4MB 拒绝。
4. 诊断长请求（服务端 ≤120s）独立 130s 客户端超时，不经 ndApiFetch 的 15s 通用保护。

**结果**：采纳。服务端 API 部署后由 main 通知联调。

---


## ADR-001 · 项目立项与同步契约（2026-09-09）

**背景**：winhelper 主应用需新增「网络排障」菜单，含配置核查 / IP 冲突检测 / 网络连通性测试 / 路由追踪 / 网络压测五个功能。与 disk-cleaner / perf-analyzer / log-inspector 同模式：独立项目承载演进，发布同步回主应用。

**决策**：
1. 独立项目 `net-doctor\`，目录结构：`net_service.py`（服务层，框架无关）+ `web/netdoctor.js`（前端，函数一律 `nd` 前缀）+ `web/netdoctor-standalone.html`（独立测试页，E2E 载体）+ `tools/`（E2E 与冒烟）。
2. 同步契约：`net_service.py` → 主应用根目录；`web/netdoctor.js` → 主应用 `web/`。
3. 主应用集成点（最小增量）：
   - `bridge.py`：`from net_service import handle_net_*` + ROUTES 增加 `/api/netdoctor/*`（不改 service.py）
   - `web/index.html`：导航按钮 `data-tab="netdoctor"`（置于性能分析之后）+ `tab-netdoctor` section
   - `web/app.js`：switchTab 末尾追加 `if (tab === "netdoctor" && typeof initNetDoctorTab === "function") initNetDoctorTab();`
4. 禁止触碰其它子项目契约文件（disk_cleanup.py / appdata_scan.py / web/disk.js / web/appdata.js / perf_service.py / web/perf.js / log_service.py / log_reader.py / web/loginspector.js / home_service.py / web/home.js）。

**结果**：采纳。后续演进以本项目为准。

---

## ADR-002 · net_service.py 自包含，不 import uplink/home_service（2026-09-09）

**背景**：IP 冲突检测与压测需与平台服务端（172.17.5.215:18090）通信（X-ETP-Token 鉴权），iperf3 需捆绑 exe 解析；uplink.py 已有同语义的 `_post` / `_iperf3_bundle_dir`。直接 import 可复用，但会把 net-doctor 与 uplink 内部符号耦合。

**决策**：net_service.py 复制语义、不 import uplink（团队指令明确）：
- 平台通信：自实现 `_platform_get/_platform_post`，配置读取同一份 `%LOCALAPPDATA%/winhelper/uplink_config.json`（server_url/token/terminal_id），请求头 `X-ETP-Token`；异常/日志永不携带 token。
- iperf3 路径解析：`NETDOCTOR_IPERF_EXE` 环境变量（测试隔离）→ 兼容 `UPLINK_IPERF_EXE` → `sys._MEIPASS/libs/iperf3` → 项目 `libs/iperf3`。
- 配置核查自实现 `ipconfig /all` 权威解析（PowerShell 强制 UTF8 输出 + utf-8-sig/utf-8/gbk 三级兜底解码），不复用 home_service。

**结果**：采纳。代价是少量重复代码，收益是框架无关 + 可独立冒烟。

---

## ADR-003 · 后台任务管理沿用 disk_cleanup `_start_task` 模式（2026-09-09）

**决策**：所有耗时探测（配置核查/IP 冲突/连通性/路由追踪/压测）一律后台 daemon 线程执行，同步返回 `task_id`，前端统一走 `/api/netdoctor/task-status` 轮询、`/api/netdoctor/task-cancel` 取消。同类任务运行中幂等复用。线程内一切异常捕获置任务 error，不崩主进程；取消通过 `threading.Event`。

**结果**：采纳。检测/压测永不阻塞 UI。

---

## ADR-004 · 连通性节点配置化 + JSONL 记录（2026-09-09）

**决策**：
1. 节点表存 `app_config.json` 的 `netdoctor.nodes`（默认 8 节点：本终端网关/核心交换机/数据中心汇聚/DMZ汇聚/内网DNS(nslookup)/温州总院(ntp:w32tm)/互联网/中心服务器），`netdoctor.expected_dns` 默认空数组（空=仅提示未配置基线）。target 为空时：key=gateway 动态取默认网关（route print -4 解析）、key=center 取 uplink server_url 的 host。
2. 每节点探测结果追加 JSONL 至 `%LOCALAPPDATA%\winhelper\netdoctor_records\ping_YYYYMMDD.jsonl`（字段 ts/key/target/ok/loss_pct/avg_ms/max_ms），供丢包/高延迟趋势。
3. 数据面失败的节点如实标注（超时/解析失败/未配置），不虚构"正常"。

**已知风险**：~~perf_service 的 `_save_app_config` 为整文件覆盖式写回，可能丢掉 netdoctor.* 键~~（已闭环：perf-analyzer-dev 于 2026-09-09 按 ADR-018 改为 merge 原子写，commit 11c4ddc/cf66fa7；本侧 net_service 的 SET 路径同为 merge 写，两侧互不覆盖）。读取端对缺键容错（回退内置默认值 + netdoctor 段缺失兜底）长期保留，作为跨模块配置写入的防御纵深。

**结果**：采纳。

---

## ADR-005 · 服务端契约对接（2026-09-09）

**决策**：按已部署的服务端契约直接对接（终端侧零改动服务端）：
- `POST /api/v1/terminals/{tid}/netdoctor/ipconflict` body {ip, mac} → verdict
- `GET  /api/v1/terminals/{tid}/netdoctor/route-nodes` → 知识库节点（match 支持 IP/CIDR，用 ipaddress 模块前缀匹配）
- `POST /api/v1/terminals/{tid}/netdoctor/iperf-server` body {mode, duration_sec} → {task_id, port}
- `POST /api/v1/terminals/{tid}/netdoctor/iperf-result` body {task_id, ok, data} → 回传总结
- `POST /api/v1/ai/analyze` body {terminal_id, issue_description}（conflict_suspect=true 时自动调用）
- 中心连接判定复用 `GET /api/perf/uplink/status`（前端 fetch，不经 bridge）

**结果**：采纳。未连接中心时：IP 冲突/压测模块置灰提示"未连接中心"，连通性测试的 center 节点显示"未连接"。

---

## ADR-006 · 压测输出与 HTML 报告（2026-09-09）

**决策**：
1. 压测 = ①中心服务器多档包长 ping（默认 [64,256,1024,4096]，每档 `ping -n 20 -l <size>`）+ ②iperf3（先平台 iperf-server 起服务端拿端口，再本地捆绑 iperf3.exe `-J -c center -p port -t sec`；UDP 轮次 `-u -b <mbits>` 默认 100M）；完成后调 iperf-result 回传。默认总时长 1 分钟，UI 可自定义。
2. 总结口径：TCP 带宽取 iperf3 -J intervals 的实测 max/min/avg，UDP 带宽/抖动/丢包，ping 各档 avg/max/min/丢包；综合结论徽章（优良/可用/拥塞/异常，按丢包与延迟阈值判定）。
3. 导出单文件自包含 HTML 报告（深色风格，头部「观枢终端平台｜EyeTerm」，文件名 `NetStress_YYYYMMDD_HHMM.html`）到 netdoctor_records 目录；「打开位置」复用主应用 `/api/disk/open-location`。

**结果**：采纳（遵守 2026-09-06 全局规范：一切导出报告一律 HTML）。

---

## ADR-007 · 验证门禁（2026-09-09）

**决策**（强制执行）：
1. web/ 变更必须 Playwright 真浏览器 E2E（add_init_script 注入 pywebview 桩按路由返回假数据）：断言关键函数 `typeof === "function"` + 数据填充 + pageerror = none；前端禁用 `?.` 可选链（本机无 node）。
2. 主应用集成后：file:// 加载 web/index.html + 桩 → 遍历全部菜单无 pageerror；校验 disk/appdata/perf/home/loginspector 关键函数仍可用。
3. 后端引擎真实冒烟：ping/nslookup/tracert/w32tm 用无害目标（127.0.0.1 / localhost / 127.0.0.2）真实执行验证解析；iperf3 用 127.0.0.1 自环（本地临时 iperf3 -s 随机端口）。
4. 铁律：GUI 无控制台程序一切子进程 `CREATE_NO_WINDOW`；路径禁硬编码盘符；不新增第三方包。

**结果**：采纳。
