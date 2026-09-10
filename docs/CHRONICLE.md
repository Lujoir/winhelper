# 观枢终端平台｜EyeTerm · 建设史编年

> 维护者：chronicler-dev ｜ v1.5 ｜ 2026-09-10 ｜ 补记主页 AI 卡门控陈旧缺陷修复上线（#36，快照升级常驻自愈）

## 卷首语

**观枢终端平台（EyeTerm）** 是面向「Windows + 安卓（规划中）」终端统一管理与故障观测定位的平台体系：观 = 观测终端运行状态，枢 = 管控枢纽；Eye 洞察终端故障，Term 即 Terminal。当前已交付 Windows 终端客户端（工程代号 winhelper）及其三个子系统（磁盘清理 disk-cleaner、日志诊断 log-inspector、性能分析 perf-analyzer），以及服务端 EyeTerm Server（server-platform）。

**体例说明**：
- 本编年只记录里程碑（立项/命名/首次交付/大版本/ADR 诞生/重要规范/对外交付物/重大事故与整改/运维机制建设），不记录日常迭代与纯修复。
- 一事一条，按时间正序；条目三要素「事件 / 意义 / 考证」，**考证字段必填**（git 哈希优先，文档路径次之，会话记忆须标注）。
- 时间考证优先级：git 提交时间 > 交付报告/ADR 文档日期 > 会话记忆。无法确证者集中收录于「存疑与考证」，不猜日期。
- 已有条目只做勘误（注明原因），不静默改写历史。

---

## 一、总览大事记

| # | 日期 | 里程碑 | 一句话摘要 |
|---|------|--------|-----------|
| 1 | 2026-05-22 | EyeTerm 前身立项 | 首提交「Windows 系统故障分析系统」，体系时间原点 |
| 2 | 2026-09-05 | 主应用改纯 C/S 形态 | 移除 B/S Web 模式，桌面单文件 exe 为唯一形态 |
| 3 | 2026-09-05 | disk-cleaner 独立立项 | 磁盘清理功能迁出主应用，独立仓库 v1.0 |
| 4 | 2026-09-05 | 性能分析子系统上线 | 主应用新增性能分析菜单，perf-analyzer 独立仓库 v1 |
| 5 | 2026-09-05 | disk.js 重复声明事故 | 引出「真浏览器 E2E + AST 重复声明门禁」双验证规范 |
| 6 | 2026-09-06 | 正式定名「观枢终端平台｜EyeTerm」 | 产品名与工程代号（winhelper）分离，全体系备案 |
| 7 | 2026-09-06 | 性能分析 v2→v3 | 四阶段压测 + 温度能力分级 + 以管理员重启 |
| 8 | 2026-09-06 | 导出报告 HTML 全局规范 | 全体系报告一律单文件自包含深色 HTML |
| 9 | 2026-09-06 | Log Inspector 立项并首次交付 | 日志诊断独立项目，门禁 E2E 43/43 + 冒烟 29/29 |
| 10 | 2026-09-06 | 主应用菜单合并手术 | 六菜单并为三菜单（日志诊断/磁盘清理/性能分析） |
| 11 | 2026-09-06 | 温度轮询弹窗事故 | 确立 GUI 程序子进程 CREATE_NO_WINDOW 全局红线 |
| 12 | 2026-09-06 | EyeTerm Server MVP v1 首次部署 | 服务端立项，终端接入协议/规则引擎/深色控制台上线生产 |
| 13 | 2026-09-06 | 服务端 v2 四大功能上线 | 配置清单/终端资产+命令通道协议 v1/iperf3 打流/AI 智能分析 |
| 14 | 2026-09-06 | 测试载荷去攻击化规范 | IDS 告警整改，确立测试载荷安全红线（ADR-018） |
| 15 | 2026-09-06 | FTP 凭据下发方案 A 定案 | 命令通道按任务携带凭据，终端零长期密钥（ADR-019） |
| 16 | 2026-09-06 | 终端侧 uplink v4 集成 | 终端注册/心跳/命令通道/指标上报双仓库落地 |
| 17 | 2026-09-08 | LLM 主备模型链 + AI 真实调用验证 | 服务端 AI 能力经真实算力平台验证（含降级链实测） |
| 18 | 2026-09-08 | 终端侧主页菜单上线 | 终端配置/硬件状态/本地网络三卡，导航增至四菜单 |
| 19 | 2026-09-08 | 温度采样降频 5 分钟 | 采样间隔配置化（默认 300s），双仓库同步节流 |
| 20 | 2026-09-08 | 前端 IP 脱敏 | 示例与白名单输出脱敏，隐私规范落地 |
| 21 | 2026-09-09 | 终端侧架构档案 v1.0 建档 | docs/ARCHITECTURE-CLIENT.md 八章建立（archivist-dev） |
| 22 | 2026-09-09 | Windows 一键安装包交付 | Inno Setup 安装包（WebView2 离线静默装/自启/卸载） |
| 23 | 2026-09-09 | 服务端控制台登录改造上线 | PBKDF2+持久会话+锁定限速+审计+剩余信息保护（等保三级） |
| 24 | 2026-09-09 | 服务端资产管理改造上线 | 资产组树形维护（多级嵌套/删除保护）+ 终端绑定资产组 |
| 25 | 2026-09-09 | 客户端↔服务端 iperf3 打流端到端测通 | 四类测试全通（TCP 887.8 Mbps）；firewalld 端口段 TCP/UDP 成对放行教训 |
| 26 | 2026-09-09 | 终端「网络排障」菜单首版上线 | net-doctor 五功能引擎交付，第四个独立子系统（exe v4.0.0） |
| 27 | 2026-09-09 | 服务端「系统管理」模块上线 | 账户/算力网关/第三方接口/多 Token，admin-only + 审计（ADR-021） |
| 28 | 2026-09-09 | SMB 日志存储挂载收尾 | NAS SMB 2.0 对接，fstab 统一挂载点 + _netdev,nofail |
| 29 | 2026-09-09 | 终端 AI 智能诊断全链路上线 | 六类日志包 → llm_chat_chain → 终端第六模块（ADR-023/ADR-008） |
| 30 | 2026-09-09 | 画方准入 HTTP API 接入 | IP 冲突检测接入真实准入数据源，admission_log=connected（ADR-025） |
| 31 | 2026-09-09 | 服务端「交换机管理」上线 | 交换机台账 CRUD + 默认凭据加密 + 审计四类事件（ADR-026） |
| 32 | 2026-09-10 | 终端 AI 诊断 GUI 上线确认 | 18:00 版 exe 运行实证，analysis_id=12 真实链路闭环 |
| 33 | 2026-09-10 | AI 诊断九项优化 + 企业/个人双模式 | 个人版本地直连第三方 LLM 不依赖中心；gate 语义根因修复（ADR-009） |
| 34 | 2026-09-10 | 静态资源 ETag/Last-Modified 协商缓存上线 | If-None-Match 304 revalidate，根治控制台更新后浏览器缓存旧页面 |
| 35 | 2026-09-10 | AI 诊断卡宿主迁移主页上线 | 主页成终端状态+AI 诊断一体化入口；主仓库首批接入内网 GitLab（ADR-010） |
| 36 | 2026-09-10 | 主页 AI 诊断卡门控陈旧缺陷修复 | 快照升级常驻自愈轮询，终端 UI 可信性防线补全 |

---

## 二、分年纪事

### 2026 年 5 月

#### 2026-05-22 · EyeTerm 前身立项：Windows 系统故障分析系统
- 事件：工作区创建，完成首个 git 提交——「Windows系统故障分析系统 - 基于事件日志的智能故障诊断工具」（基于 Windows 事件日志的智能故障诊断桌面工具）。
- 意义：EyeTerm 全体系的时间原点；此后由该工具逐步演进为终端管理平台，于 2026-09-06 正式定名。
- 考证：主仓库（winhelper）提交 `84ea539`（2026-05-22）。

### 2026 年 9 月

#### 2026-09-05 · 主应用移除 B/S Web 模式，改为纯 C/S 桌面形态
- 事件：winhelper 删除 app.py 与 Flask 依赖，服务层抽至 service.py，桌面模式（pywebview + 桥接）成为唯一形态；此前 B/S 客户端形态终结。
- 意义：确立「单文件 exe、无本地 HTTP 端口」的产品形态基线，是后续所有子系统架构（bridge ROUTES 分发）的前提。
- 考证：主仓库提交 `ea5fada`（2026-09-05）；另见 log-inspector/docs/DECISIONS.md ADR-001（旧引擎自主应用迁入记录）。

#### 2026-09-05 · 磁盘清理独立项目 disk-cleaner 立项（v1.0）
- 事件：磁盘清理功能从 winhelper 迁出为独立仓库，首提交包含垃圾清理/安装包识别/应用数据迁移/目录树/仪表盘完整功能；ADR-001~008（安全分级、清理白名单、路径通用性、treemap、安装包判定、应用数据迁移、前端约定、已知限制）随迁入建档。
- 意义：EyeTerm 首个独立子系统诞生，确立「演进以子项目仓库为准、发布同步回主应用」的协作模式。
- 考证：disk-cleaner 仓库提交 `3cdeb9b`（2026-09-05）；disk-cleaner/docs/DECISIONS.md ADR-001~008。

#### 2026-09-05 · 性能分析子系统上线（主应用 + perf-analyzer 独立仓库）
- 事件：winhelper 新增性能分析菜单（实时指标/长时间记录/瓶颈分析与硬件评估），同日 perf-analyzer 独立仓库建立 v1；同步契约 ADR-001（只拥有 perf_service.py + web/perf.js，bridge ROUTES /api/perf/*，禁触 disk-cleaner 契约文件）同日确立。
- 意义：性能分析能力首次交付，且从诞生起即按「独立仓库 + 最小集成点」模式管理，为后续子项目拆分提供模板。
- 考证：主仓库提交 `4264c72`；perf-analyzer 仓库提交 `4192b95`（均 2026-09-05）；perf-analyzer/docs/DECISIONS.md ADR-001~005。

#### 2026-09-05 · disk.js 重复声明事故与「真浏览器 E2E + 重复声明门禁」规范确立
- 事件：主应用 web/disk.js 因同步合并引入 `const atRoot` 同作用域重复声明，整个 disk.js SyntaxError 导致磁盘菜单数据全部静默失败（switchTab 的 typeof 保护吞掉错误）；同日 Playwright（pywebview 桩）真实浏览器 E2E 验证修复、exe 重建。disk-cleaner 侧随后固化两项门禁：Playwright E2E 固化为 tools/e2e_dashboard.py（ADR-010）与 esprima AST 重复声明检测 tools/js_decl_check.py（ADR-012/013）。
- 意义：体系内首起系统性前端质量事故；确立「esprima 只能查语法，web/ 变更必须真浏览器 E2E 断言数据填充」的验证铁律，成为全体系前端门禁基石。
- 考证：主仓库提交 `5f4eaad`；disk-cleaner 仓库提交 `921bb1c`（ADR-010）、`4b8d4b1`（ADR-012/013，含「esprima 检不出同作用域重复 const」最小用例结论）；均为 2026-09-05。

#### 2026-09-06 · 系统正式定名「观枢终端平台｜EyeTerm」
- 事件：用户正式定名，主应用窗口/导航/弹窗标题更名为「观枢终端平台｜EyeTerm」（winhelper 保留为工程代号）；disk-cleaner 以 ADR-015 备案文档全称与 HTML 报告头部统一规则。
- 意义：产品从工具集合升格为统一平台品牌；「Eye=洞察终端故障 + Term=Terminal」命名语义自此贯穿全体系交付物。
- 考证：主仓库提交 `2800966`；disk-cleaner 仓库提交 `fd87cda`（ADR-015）；均 2026-09-06。

#### 2026-09-06 · 性能分析 v2→v3：压测编排与温度能力分级
- 事件：性能检测（压测）四阶段编排（磁盘 25s/CPU 15s/内存 10s/GPU 8s，全程可取消 + 内存红线 watchdog）与硬件规格卡片上线（v2）；随后温度能力分级（GPU nvidia-smi 全员 / CPU 需管理员 + LibreHardwareMonitorLib）与「以管理员重启」交付（v3）；winhelper.spec 温度组件打包路径同步修正。
- 意义：性能分析从被动观测升级为主动检测（压测 + 温度 + 硬件评估），形成完整硬件健康诊断能力；ADR-008/009 固化压测安全硬约束（禁 multiprocessing、内存红线不可妥协）。
- 考证：perf-analyzer 提交 `2506c6b`（v2）、`3d35f37`（v3）；主应用同步 `2d7f3ad`、`558276f`、`a16da6b`；均 2026-09-06；perf-analyzer/docs/DECISIONS.md ADR-008~009。

#### 2026-09-06 · 「导出报告一律单文件自包含 HTML」全局规范确立
- 事件：用户明确全局规范——winhelper 及所有子项目所有可导出报告一律 HTML 单文件自包含（内联 CSS、深色风格、文件名含模块与日期、导出后提供「打开位置」），不再使用 Markdown/CSV。perf-analyzer 两处导出（记录分析报告、压测报告）当日完成 HTML 化交付，disk-cleaner 以 ADR-014 入档（核查确认暂无导出功能），主应用性能分析导出同步 HTML 化。
- 意义：EyeTerm 首个全体系交付物格式规范，此后任何新增导出功能默认 HTML。
- 考证：disk-cleaner 提交 `97794d3`（ADR-014）；perf-analyzer 提交 `46c2e5f`；主应用提交 `558276f`；均 2026-09-06。

#### 2026-09-06 · Log Inspector 独立项目立项并首次交付
- 事件：日志诊断独立仓库建立（引擎 log_service.py/log_reader.py + 前端 loginspector.js + standalone 页面 + ADR-001~009），一次性交付五步流程：系统日志检索（类别多选/时间范围/级别/来源/关键字/事件 ID）→ txt 原始日志导出（任务模型，默认近 3 天全量）→ 智能分析（FAULT_PATTERNS 模式卡 + 结论徽章）→ HTML 分析报告导出 → 知识库建议；另含本机信息面板（modeBadge）。首次交付门禁全绿：E2E 双场景 43/43（standalone 27 + 主应用 16）+ 后端真实冒烟 29/29。
- 意义：EyeTerm 第三个独立子系统诞生并当日完成首次交付；ADR-001 确立其获授权对主应用执行菜单合并手术（导航 4 合 1）。
- 考证：log-inspector 仓库提交 `dde1c4b`（init）→ `ec02063`（完整实现）→ `7011af8`（全绿）→ `b24dbeb`（ADR-009 交付记录），均 2026-09-06；log-inspector/docs/DECISIONS.md ADR-001~009。

#### 2026-09-06 · 主应用菜单合并手术：六菜单并为三菜单
- 事件：主应用旧「仪表盘/日志查看/故障分析/知识库」四菜单合并为「日志诊断」（置最左、默认激活），导航由六菜单收敛为三菜单（日志诊断/磁盘清理/性能分析）；app.js 删除旧 10 个 tab 函数（保留 showError/escapeHtml/truncate 公共工具），bridge.py 删旧 5 条日志路由改挂 /api/loginspector/* 八条路由，service.py 删旧 5 个 handler。
- 意义：主应用完成「外壳 + 三子系统」架构定型，消除旧内联功能与独立项目的双轨并存。
- 考证：主仓库提交 `04b9500`、`f84ab2f`（log-inspector gitlink 更新至 b24dbeb）；log-inspector/docs/DECISIONS.md ADR-001（手术授权与范围）；均 2026-09-06。

#### 2026-09-06 · 温度轮询弹窗事故与 CREATE_NO_WINDOW 全局红线
- 事件：windowed exe 中 subprocess 调用控制台程序（nvidia-smi/powershell/typeperf）未加 CREATE_NO_WINDOW，温度 2s 轮询导致循环弹出 cmd 窗口；perf-analyzer 5 处 subprocess.run 修复并沉淀门禁第四道 check_subprocess_window（ADR-013），主应用 perf_service 同步修复。
- 意义：确立全体系红线——GUI 无控制台程序的一切子进程调用必须 CREATE_NO_WINDOW（有意开窗如 explorer 除外）；控制台 smoke 与桩 E2E 均发现不了此类问题，须静态 grep 检查 creationflags。
- 考证：主仓库提交 `0db7c48`；perf-analyzer 提交 `819e806`（修复）、`c2a0f4b`（ADR-013 门禁）；均 2026-09-06。

#### 2026-09-06 · EyeTerm Server MVP v1 立项与首次生产部署
- 事件：服务端独立仓库建立，MVP v1 交付终端接入协议/SQLite 存储/瓶颈规则引擎/深色控制台及部署与冒烟工具（含 ADR-001~009）；同日修复 Anolis python39 二进制名兼容并完成首次生产部署（172.17.5.215:18090，systemd terminal-platform），ADR-011 记录首次部署五条教训（ExecStart 路径/启动轮询/防火墙双重复核/端口自查豁免/python3.9 命名）；控制台 E2E 门禁（ADR-010）与静默失败修复随之建立。部署当日服务器重装（Anolis OS 8.10 基线）后 ADR-009 回填新机基线。
- 意义：EyeTerm 自此形成「终端侧 + 服务端」双端架构；首个对外部署交付物落地生产环境。
- 考证：server-platform 仓库提交 `5a21967`、`fb122bd`、`02d41aa`、`df3cd8e`（均 2026-09-06）；server-platform/docs/DECISIONS.md ADR-009/010/011。

#### 2026-09-06 · 服务端 v2 四大功能上线（ADR-012~017）
- 事件：服务端 v2 一日内连续交付四功能并全部部署验证：①配置清单——终端白名单准入（fail-closed + 存量豁免 + 审计）、敏感配置机器密钥加密存储、vsftpd/SMB 配置化（ADR-012/013/014，冒烟 30 项 + E2E 16 项）；②终端资产管理——资产字段扩展、心跳命令通道协议 v1 冻结（类型白名单/单次下发/状态机/回执，ADR-015，冒烟 36 + E2E 18）；③iperf3 打流服务——独立任务槽 + 临时端口段 18200-18299 + 超时强杀（ADR-016，冒烟 43 + E2E 20）；④AI 智能分析——运维诊断上下文聚合 + OpenAI 兼容调用（key 加密）+ FAULT_PATTERNS 知识注入 + 终端/控制台双触发（ADR-017 隐私边界，mock 冒烟 47 + E2E 22）。期间修复两起部署遗漏事故（deploy 清单补 iperf.py、改动态枚举 server/*.py 根治）。
- 意义：服务端从接入框架升级为管理平台（准入/资产/网络测试/AI 诊断四大能力）；命令通道协议 v1 冻结成为终端侧对接的稳定契约。
- 考证：server-platform 提交 `385d8fd`、`eba91c5`、`8dcb172`、`bf7b67d`、`214cc8d`、`e887b43`（均 2026-09-06）；ADR-012~017。

#### 2026-09-06 · 测试载荷去攻击化规范（IDS 告警整改）
- 事件：冒烟负向用例中的 `rm -rf` 攻击串触发深信服 IDS 命令注入告警（事件号 E26090605685，白名单正确 400 拒绝，属自测流量误伤）；载荷良性化后行为不变，规范入档并完成全项目载荷审计；Server 响应头指纹同步弱化（固定 EyeTerm，不暴露版本/Python）。
- 意义：确立「测试载荷禁真实攻击串」安全红线，避免安全设备误报与生产干扰；属 EyeTerm 首起由外部安全设备反馈驱动的规范建设。
- 考证：server-platform 提交 `ac01cc9`、`21fdccc`（均 2026-09-06）；ADR-018。

#### 2026-09-06 · FTP 凭据下发方案 A 定案
- 事件：ADR-019 确定 FTP 凭据下发采用「命令通道按任务携带凭据、终端零长期密钥」方案，collect_logs 参数结构 v1 同步冻结；测试数据清理工具 cleanup_testdata.py 交付，生产库测试数据清理（清理前备份 pre_cleanup_20260906_134627）。
- 意义：终端侧无需持有长期密钥，收敛凭据暴露面；为日志采集类命令的凭据管理定下基线。
- 考证：server-platform 提交 `f7aca5e`（2026-09-06）；ADR-019。

#### 2026-09-06 · 终端侧 uplink v4 平台接入集成
- 事件：perf-analyzer 完成 uplink v4（终端注册/心跳命令通道/指标上报/内置 iperf3，ADR-017），主应用同步集成平台接入能力（终端注册/心跳/命令通道/平台接入卡片）。
- 意义：终端与服务端的端云链路首次贯通，EyeTerm 从单机工具体系升级为可集中管控的终端平台。
- 考证：perf-analyzer 提交 `b6f76f8`；主应用提交 `f4254fb`；均 2026-09-06。

#### 2026-09-08 · 服务端 LLM 主备模型链上线，AI 真实调用验证通过
- 事件：AI 分析接入真实算力平台（OpenAI 兼容接口）并验证通过（analysis_id=5/6/7）；同日修复冒烟测试用生产 llm 配置被测试值覆盖的事故（先 401 后 503），LLM 主备模型链上线——主模型超时/连接/5xx/429/模型不可用自动切换备选（4xx 鉴权类不切），冒烟改造为隔离键 + 测后恢复（ADR-020）。
- 意义：EyeTerm AI 智能分析从 mock 走向真实调用；「冒烟禁触碰生产 llm.* 配置」入档，模型降级链保障 AI 能力可用性。
- 考证：server-platform 提交 `e8f3aaf`（2026-09-08，ADR-020）；验证结果依据会话记忆（2026-09-08）。

#### 2026-09-08 · 终端侧主页菜单上线（导航增至四菜单）
- 事件：主应用新增「主页」菜单（默认激活，置最左）——终端配置/硬件状态/本地网络三卡可视化；同日修复主页网卡名中文乱码（PowerShell 输出强制 UTF8）与 hwinfo 响应结构解包，本地网络配置改用 ipconfig /all 权威解析（DNS 多行/网关/DHCP 合并采集）。
- 意义：主应用由「功能菜单集合」补齐「终端概览门户」层，导航定型为主页/日志诊断/磁盘清理/性能分析四菜单。
- 考证：主仓库提交 `08cac08`、`a39f4b9`、`b83b5f5`（均 2026-09-08）；docs/ARCHITECTURE-CLIENT.md 第 6.1 节。

#### 2026-09-08 · 温度采样降频与采样间隔配置化
- 事件：温度采样默认间隔由高频轮询降为 5 分钟（300s），新增 /api/perf/app-config 支持自定义采样间隔（范围 30-3600s）与设置弹窗自定义，后端按 app_config.json 节流缓存；perf-analyzer 同步实现并更新 E2E 导航断言为四菜单。
- 意义：以配置化架构决策（而非一次性改值）解决高频采集负载问题，成为跨仓库同步交付的又一案例。
- 考证：主仓库提交 `670a010`；perf-analyzer 提交 `40f4d74`；均 2026-09-08。

#### 2026-09-08 · 前端 IP 脱敏规范落地
- 事件：前端平台配置示例与控制台白名单示例的敏感 IP 全面脱敏（172.17.x 真实段 → 127.0.0.1/192.168 文档示例段）。
- 意义：交付物与界面不再泄露内网地址段，隐私/安全脱敏从文档层落到产品界面层。
- 考证：主仓库提交 `756084f`（2026-09-08）。

#### 2026-09-09 · 终端侧应用架构档案 v1.0 建档
- 事件：archivist-dev 建立 docs/ARCHITECTURE-CLIENT.md v1.0（八章：应用概述/进程形态/运行环境/模块地图/外联配置/功能清单等），以代码为事实来源，注明子项目同步契约。
- 意义：主应用首次拥有与代码同步的权威架构档案，EyeTerm 体系文档基建（编年史 + 架构档案 + 各仓库 DECISIONS/交付报告）成形。
- 考证：主仓库提交 `a04d8ed`（2026-09-09，docs: 终端侧应用架构档案 v1.0）。

#### 2026-09-09 · Windows 一键安装包交付（Inno Setup）
- 事件：基于 Inno Setup 6.7.3 的一键安装包交付：WebView2 离线包静默安装与版本检测、开机自启、卸载支持、Win7 拦截提示。
- 意义：EyeTerm 首个面向终端分发的安装级交付物，客户端从「拷贝 exe」升级为标准化安装交付。
- 考证：主仓库提交 `0bdfdce`（2026-09-09，feat: Windows 一键安装包）；installer/ 目录。

#### 2026-09-09 · 服务端控制台登录改造上线（等保三级）
- 事件：控制台登录体系整体升级并部署上线（172.17.5.215:18090）：PBKDF2-HMAC-SHA256 320k 轮自描述哈希、SQLite 持久会话（重启不掉线，空闲 30min/绝对 12h + UA 指纹绑定）、失败锁定 423（5 次锁 30min）、IP 限速 429（前置慢哈希）、改密端点（复杂度校验 + 历史 3 次不重复 + 吊销其它会话）、append-only 审计（触发器禁 UPDATE/DELETE）；S7 剩余信息保护：config.json 移除明文口令、双库 VACUUM、全盘复扫零残留。验证：本地冒烟 6/6 + 远程冒烟 10/10 + config 重建后 3/3。过程中发生 S7 清理循环误删 config.json 事故，按部署模板 + 记忆 token 重建，业务库无数据污染（短暂以 DEV 配置运行 49 秒）。合规基线 GB/T 22239-2019 第三级。
- 意义：EyeTerm Server 首次达到等保三级身份鉴别/安全审计/剩余信息保护要求；「含 $ 哈希串严禁经 bash 双引号命令行传递（须 sftp+stdin）」「config.json 变更前必须备份」两条运维教训入档。
- 考证：交付报告 server-platform/docs/login_upgrade_delivery.md（交付日期 2026-09-09）；涉及文件 server/auth_upgrade.py、server/api.py、server/app.py、server/migrate_login_upgrade.py、console/index.html、data/console_auth.db；**代码尚未提交至 server-platform 仓库（待考证，见存疑 #3）**。

#### 2026-09-09 · 服务端控制台资产管理改造：资产组树形维护与终端绑定上线
- 事件：「资产清单」菜单升级为「资产管理」，左侧新增资产维护入口（资产组树），支持根目录下创建/重命名/删除多级嵌套子目录（层级自由扩展），删除保护：含子组拒绝并返回 409，删除组时直属终端自动解绑保证关联一致；终端资产绑定资产组（单终端单组，支持绑定/改绑/解绑），组视图展示直属终端并支持批量绑定（已属其它组自动改绑），未分组终端以虚拟节点呈现。实现于 store.py（新增 asset_groups 表 + terminals.group_id 列，幂等迁移、索引后置于 ALTER，共 6 个存储方法）与 api.py（/console/asset-groups CRUD、/console/terminals/{tid}/group 绑定端点，均在控制台会话鉴权内）；console/index.html 改双栏布局（参照钉钉组织架构树交互）。验证：存储层本地测试 18/18（含存量库迁移场景）、远程 API 冒烟 16/16（多级创建/绑定双向一致/409 保护/自动解绑/测试数据自清理）、esprima JS 语法检查通过；已部署 172.17.5.215:18090 并重启验证。
- 意义：终端资产管理从「平铺清单」升级为「组织化资产组」治理模型，是服务端资产维度的首个结构性功能扩展；资产组数据模型为后续按组策略下发与分组统计奠基。
- 考证：server-platform 提交 `4d2924b`（2026-09-09）——资产组代码（store.py asset_groups 表、api.py 资产组端点、console/index.html 双栏布局）随系统管理模块批次首次入库；主仓库 `239cc2b`（子仓库指针登记）；部署备份 index.html.bak.20260909_090701（服务器侧）。**勘误（2026-09-10）**：原记「待考证」（见存疑 #3），经考证代码已入库，回填哈希消项；来源：team-lead 会话记忆（2026-09-09）+ git 实证。

#### 2026-09-09 · 客户端↔服务端 iperf3 打流端到端测通（四类全通）
- 事件：终端（winhelper uplink 内置 iperf3 3.1.3）与服务端四类打流测试端到端全通：TCP 887.8 Mbits/sec、UDP 1.0 Mbps（jitter 0.155ms / loss 0%）、服务器与网关 ping 均 1ms / loss 0%，任务闭环 24-48s（launch→命令通道下发→心跳拉取→执行→JSON 回传→done）。过程中定位一起数据面故障：服务器 firewalld 仅放行 18200-18299/tcp 缺 UDP，UDP 打流包被丢致客户端挂起超时（iperf3_timeout），补放行 18200-18299/udp 后一次通过。
- 意义：服务端 v2 iperf3 打流能力（ADR-016）首次真实联调打通，「命令通道→任务槽→打流→回执」全链路验证；沉淀运维教训——iperf 端口段放行必须 TCP/UDP 成对，任务超时 + 命令回执 failed 组合优先排查数据面过滤。
- 考证：server-platform 提交 `721afb1`（2026-09-10 补录，含 docs/iperf_e2e_report.md 交付报告与 server/iperf.py spawn_server 入库）；交付报告 server-platform/docs/iperf_e2e_report.md；联调工具主仓库 `3f04672`（tools/_iperf_e2e.py）；实测数据来源：会话记忆（2026-09-09）。

#### 2026-09-09 · 终端「网络排障」菜单首版上线（net-doctor）
- 事件：主应用新增导航菜单「网络排障」，net-doctor 独立项目首版交付五功能引擎：配置核查（DHCP/DNS 基线比对，离线可用）、IP 冲突检测（终端上报 IP+MAC，服务端交叉校验）、连通性测试（8 节点含 w32tm stripchart 校时 / nslookup 指定 DNS，结果 JSONL 落 %LOCALAPPDATA%\winhelper\netdoctor_records）、路由追踪（tracert + route-nodes CIDR 区域标注）、网络压测（多档包长 ping + iperf3 TCP/UDP，HTML 报告导出）；主应用集成 bridge.py 10 条 /api/netdoctor/* 路由 + 导航 + switchTab 守卫。集成时 web/home.js 存在损坏块，被 E2E 门禁拦截后修复（门禁有效拦截案例）。随后用户实测完成四项修复：中心状态简化徽章、核查补全 ipconfig /all 全字段 + 非活动网卡折叠、w32tm 中文逗号格式解析 + 校时偏差阈值、节点目标入设置弹窗维护区块。
- 意义：EyeTerm 第四个独立子系统诞生，终端侧首次具备网络故障观测与排障能力；exe 重建至 client_version 4.0.0 上线。
- 考证：net-doctor 仓库提交 `539b993`（首版）、`97abfd1`（四项修复）；主仓库提交 `628c210`（集成）、`6afcac9`（修复同步）；均 2026-09-09。E2E 75/75、后端冒烟 41/41、exe 版本 4.0.0 来源：会话记忆（2026-09-09）。

#### 2026-09-09 · 服务端「系统管理」模块上线（ADR-021）
- 事件：控制台新增「系统管理」模块四功能：账户管理、算力网关（连通性测试）、第三方接口、终端 Token（多 token 鉴权模型）；admin-only 权限模型 + 审计事件覆盖。同批次顺带修复两缺陷：register 上报补传 asset（TBC-001，配套终端资产 schema1 结构化上报）与 PUT 请求体缺陷；真实终端验证 asset_detail 由 NULL 转为结构化数据。
- 意义：服务端运维自治能力（账户/凭据/外部系统接入）首次成模块交付；多 token 鉴权模型为终端规模化接入奠基；同批次入库一并补齐登录改造代码（auth_upgrade.py 等）的历史欠账（存疑 #3 消项）。
- 考证：server-platform 提交 `4d2924b`（2026-09-09，ADR-021，含 tools/smoke_sysadmin.py、tools/test_sysadmin.py）；终端侧 schema1 上报主仓库 `77e1081`；指针与接口台账主仓库 `239cc2b`、`ed95c63`；TBC-001 消项记录 `a47c14b`；asset_detail NULL→1 实测来源：会话记忆（2026-09-09）。

#### 2026-09-09 · SMB 日志存储挂载收尾（NAS 对接）
- 事件：服务端日志存储对接 NAS 收尾完成：NAS 仅支持 SMB 2.0（SMB 3.x 协商报 error 95），按 2.0 协议挂载成功；fstab 挂载点统一对齐 /data/terminal-platform/storage，credentials=/etc/eyeterm/smb.cred + _netdev,nofail；读写验证通过。
- 意义：v2 配置清单（ADR-014 SMB 存储配置化）遗留的挂载收尾完成，服务端日志归档外部存储落地；_netdev,nofail 保证网络未就绪时不阻塞系统启动。
- 考证：来源：会话记忆（2026-09-09，服务器侧运维操作，无 git 变更）；服务器侧 fstab 与 /etc/eyeterm/smb.cred（凭据文件）待现场核验。

#### 2026-09-09 · 终端 AI 智能诊断全链路上线（第六模块）
- 事件：终端侧第六模块「AI 智能诊断」全链路上线：服务端新增 diagnose API——终端推送六类日志包，issue 类目走 llm_chat_chain 主备模型链出结论（截断策略：单类 32KB / 注入 4KB / 存证 4KB，trigger=terminal_diagnose 落库）；真实联调验证 analysis_id=12（Qwen3.6，30.3s 从系统日志定位 Schannel 根因）；终端侧由 net-doctor 承载第六模块（六类日志聚合 + 转发代理 + 结果/历史渲染），主应用 bridge 集成 /api/netdoctor/ai-diagnose；exe 18:00 版就位 dist（用户次日打开生效）。同日附带三项修复：①主页平台接入卡轮询冻结——apiFetch 统一 15s 超时（全模块保护）+ uplink 轮询失败可见 + 可见即刷 + 挂起注入回归门禁（底层 IPC 挂死触发器未定位，现象由永冻降级为自愈）；②网络排障核查卡片补链路速率；③连通性 ok 语义修复（探测通道成功即 ok，偏差过大 warn 标注）。
- 意义：EyeTerm AI 能力从服务端控制台延伸到终端一线场景，终端可自主发起智能诊断，「终端采集→服务端模型链→结论回显」全链路闭环；前端 IPC 超时兜底确认为防御规范并纳入回归门禁。
- 考证：server-platform 提交 `8aaa64e`（2026-09-09，ADR-023）；net-doctor 提交 `b794381`（ADR-008）、`e00e1d7`、`1a1d235`、`f82f511`；主仓库提交 `98c0595`、`29ef6f3`、`94a681b`、`590e2e8`；均 2026-09-09。E2E 112/112、analysis_id=12 实测、exe 18:00 版来源：会话记忆（2026-09-09）。

#### 2026-09-09 · 画方准入 HTTP API 接入：IP 冲突检测接入真实数据源（ADR-025）
- 事件：main 解析厂商 PDF 确认画方 NAD 准入 HTTP API 契约（HMAC-SHA256 签名 + term/get 查询接口），实测拉取 1588 台资产（含 MAC→IP→接入交换机端口链路）；server-platform 新增 nad_client.py——凭据走「第三方接口」配置（id=2）+ HMAC 签名 + 自动翻页（实测单页上限 1000）+ 60s 缓存，ipconflict 端点注入 admission 证据（api.py `_enrich_ipconflict_admission`）：同 IP 异 MAC 升级 conflict_suspect、core_switch_state 三态判定（nas_connected / registered_no_port / not_connected）；适配 3 处厂商文档与实际差异（响应 list/dict 形态、macs 条目为对象、单页上限需翻页）；未配置/异常降级不阻断主流程。
- 意义：IP 冲突检测（BRG-042）的 admission_log 数据源由 not_connected 转 connected，EyeTerm 首个外部网络准入系统数据源生产打通；「实测优先于厂商文档」的外部对接方法（3 处差异适配）入档。
- 考证：server-platform 提交 `a5156cc`（2026-09-09 17:35，ADR-025）；接口台账登记主仓库 `38946f6`（EXT-006 补代码实证 + BRG-042 更新行）；1588 台实测来源：会话记忆（2026-09-09）。

#### 2026-09-09 · 服务端「交换机管理」上线（ADR-026）
- 事件：控制台系统管理模块新增交换机管理（第五卡片）：交换机台账 CRUD + 默认 SSH 只读凭据（settings 敏感键 SENSITIVE_KEYS 加密存储）+ 审计四类事件；deploy 幂等预置 reader 凭据（stdin 注入，不经命令行）。
- 意义：网络设备侧数据进入服务端管控，与画方准入端口证据（BRG-042 三态判定）形成「准入 ↔ 交换机」设备数据闭环；SSH 凭据 stdin 注入延续「敏感串不经 bash 命令行」运维红线。
- 考证：server-platform 提交 `f031152`（2026-09-09 18:28，ADR-026，代码实证 api.py:1059-1133、store.py:170-177）；接口台账 SRV-072~077 主仓库 `d6e96d1`（2026-09-09）。

#### 2026-09-10 · 终端 AI 诊断 GUI 上线确认（18:00 版运行实证）
- 事件：08:55 终端启动运行 2026-09-09 18:00 版 exe（含第六模块 AI 智能诊断），GUI 侧上线得到运行实证；服务端 analysis_id=12 真实链路此前已验证（Qwen3.6，30.3s 从系统日志定位 Schannel 根因）。
- 意义：「终端 AI 智能诊断全链路上线」（#29）的交付闭环确认——服务端 API、终端 GUI、真实诊断链路三者齐备。
- 考证：会话记忆（2026-09-10 08:55 终端启动确认，无 git 变更）；链路考证承接 #29（server-platform `8aaa64e`、net-doctor `b794381`、主仓库 `98c0595`）。

#### 2026-09-10 · AI 智能诊断九项优化与企业版/个人版双模式（ADR-009）
- 事件：AI 诊断迭代九项：①连接判定 Bug 根因修复（一次性守卫致首帧 connecting 误判，含 gate 语义修正）②AI 卡置顶 ③时间段引导 ④留白优化 ⑤日志默认收起 + 网络类子选项 ⑥结果三段结构化渲染与失败态 ⑦探测按钮移位 ⑧时间范围勾选与数据源新鲜度 ⑨**企业版/个人版双模式**——enterprise 走中心 llm_chat_chain 转发代理（原逻辑），personal 由终端本机 urllib 直连第三方 OpenAI 兼容 /v1/chat/completions（120s 超时，不依赖中心、未配置引导设置）；三段提示词 _ND_AI_PROMPT_SYSTEM 单份沉淀 net_service（与中心侧语义对齐防漂移）；个人版配置存 app_config netdoctor.ai_personal（url/api_key/model，key 留空=保持）；bridge 新增 /api/netdoctor/ai-personal-test 连通性测试（GET /models，15s）。E2E 153/153。
- 意义：AI 诊断从「中心依赖」扩展为「企业/个人双轨」，无中心/离线场景可用；连接判定根因修复消除首帧误判；接口台账同批由接口登记官补登（BRG-050 追溯漏登 + BRG-051 新增）。
- 考证：net-doctor 提交 `a892f62`（2026-09-10 09:49，ADR-009）；主应用提交 `bd965ae`（2026-09-10 09:49，bridge.py +ai-personal-test 路由）；接口台账主仓库 `75f7784`（2026-09-10 09:57，台账 149 条）。E2E 153/153、新 exe 09:51 构建就位**待用户重启终端生效（截至本条登记未确认运行）**来源：main 下发会话记忆（2026-09-10）。

#### 2026-09-10 · 静态资源 ETag/Last-Modified 协商缓存上线
- 事件：server-platform 静态资源服务（_static）支持 If-None-Match 304 revalidate——弱验证器 W/"mtime-size"（mtime+size 指纹）+ Last-Modified 协商，命中返回 304 不重传正文；no-cache 语义保持（每次仍协商校验，杜绝陈旧副本）。根治 2026-09-09 控制台 index.html 更新后浏览器启发式缓存旧页面问题（用户实测踩中）；实测首次 200、再验证 304，已部署生产。
- 意义：服务端静态分发补上「变更必达」一环，控制台页面更新不再依赖用户手动强刷；与前端「资源版本号参数」策略互补，形成缓存治理闭环。
- 考证：server-platform 提交 `25d48e2`（2026-09-10 14:38）；主仓库 `b4c7964`（2026-09-10 15:03，子仓库 gitlink 前进）；部署备份 api.py/app.py.bak.20260910_etag（服务器侧）；first 200 + revalidate 304 实测来源：会话记忆（2026-09-10）。

#### 2026-09-10 · AI 智能诊断卡宿主迁移主页上线（ADR-010，内网 GitLab 首批接入）
- 事件：应用户要求，网络排障页「AI 智能诊断」卡迁移至主页「终端概览」上方（移动非复制）：net-doctor 侧 netdoctor.js boot/visibilitychange 覆盖 home 激活逻辑 + E2E 宿主断言（ADR-010），主应用 index.html 卡片迁移 + app.js switchTab home 分支联动 initNetDoctorTab（网络排障页保留 5 大探测模块）；E2E 155/155 双场景 pageerror=0；main 验收后 PyInstaller 重建 exe，替换 dist_new 并重启运行（PID 53108），用户可见。同批主仓库完成内网 GitLab 接入并推送全量历史（0b890c9 Initial → b69725d merge 保留本地全史 → 0289ca4 推送至 origin/main）。
- 意义：主页成为「终端状态 + AI 诊断」一体化入口，网络排障聚焦探测职能；主仓库首次接入内网 GitLab 远程（git.wzeye.cn/zlj/eyeterm），EyeTerm 核心仓库自此具备中心化版本载体（此前仅本地与 GitHub 个人镜像）。
- 考证：net-doctor 提交 `4e579a0`（2026-09-10 15:58，ADR-010）；主应用提交 `0289ca4`（2026-09-10 15:58，origin/main 同步实证）；GitLab 接入提交 `0b890c9`、`b69725d`（均 2026-09-10）；E2E 155/155、exe 重建与 PID 53108 来源：会话记忆（2026-09-10）。

#### 2026-09-10 · 主页 AI 诊断卡门控陈旧缺陷修复：快照升级常驻自愈
- 事件：用户实测反馈主页 AI 智能诊断卡显示「未连接中心平台」，而同屏平台接入徽章=已连接——根因：boot 期 home 默认激活即查中心状态，此时 uplink 尚在注册期，门控误判；ndState.uplink 为缓存值仅 switchTab/visibilitychange 才重查，常驻主页无补救路径。修复：netdoctor.js 自包含常驻轻量轮询（10s，ND_UPLINK_POLL_MS）——可见 + 宿主页（tab-home/tab-netdoctor）激活才请求、busy 防重叠、已连接后保持低频轮询覆盖停驻期断连的反向陈旧；零 home.js 改动，standalone 页天然生效；net_service 同步副本 MD5 一致。E2E 160/160 双场景 pageerror=0（新增 5 断言：connecting→connected 不切 tab 自动翻转、error 反向翻转）；exe 重建替换 dist_new 并重启（PID 51800）。
- 意义：主页 AI 诊断卡从「激活时快照」升级为「常驻自愈」，与主页平台接入卡冻结修复（apiFetch 15s 超时自愈，#29 附带修复 `29ef6f3`）共同构成终端 UI 可信性防线——状态陈旧自动翻转，用户不再看到说谎的界面。
- 考证：net-doctor 提交 `f603838`（2026-09-10 16:13）；主应用提交 `4330047`（2026-09-10 16:13，origin/main 同步实证，GitLab 推送区间 0289ca4..4330047）；E2E 160/160、PID 51800 来源：会话记忆（2026-09-10）。

---

## 三、存疑与考证

1. **主应用功能演进静默期（2026-05-23 ~ 2026-09-04）**：主仓库首提交 `84ea539`（2026-05-22）与 `ea5fada`（2026-09-05）之间无任何 git 提交。此期间日志诊断/磁盘清理等功能的迭代时间、首个 exe 构建时间均无法从 git 考证；B/S 形态存续的证据仅来自 `ea5fada` 提交说明与 log-inspector ADR-001（旧引擎自主应用迁入）。待补充考证。
2. **首个 winhelper.exe 构建时间待考证**：git 中首次提及 exe 重建为 2026-09-05（`5f4eaad`「exe已重建」）；「dist_new 构建 → 停进程 → 替换 dist → 重启」流程在 `5f4eaad` 与 log-inspector ADR-009 中固化。首个 exe 诞生的准确日期无据。
3. **【已消项 2026-09-10】2026-09-09 控制台登录改造与资产管理改造代码补录入库**：原记代码未入 git（server-platform 最后提交 `e8f3aaf` 2026-09-08）。经考证：两类改造代码已随 `4d2924b`（2026-09-09，系统管理模块批次）首次入库——该提交含 server/auth_upgrade.py（1515 行）、store.py 资产组改造（asset_groups 表）等历史增量；登录迁移脚本与两份交付报告（login_upgrade_delivery.md / iperf_e2e_report.md）随后经 `721afb1`（2026-09-10）补录。条目 #23/#24 考证已回填。原判「未提交」系 2026-09-09 建档时点早于入库时点所致。
4. **【已消项 2026-09-10】运维知识库 kb_store 归属考证**：原记「server-platform 仓库无对应提交」。经考证：kb_store.py 已随 `31f8e1d`（2026-09-09，ADR-022 控制台知识库页）首次入库，kb 冒烟工具（server/kb_smoke.py）经 `721afb1` 补录。功能开发完成于 2026-09-08（会话记忆），首次入库于 2026-09-09，两者不矛盾。
5. **终端资产明细（asset schema1）部署确认待考证**：功能本体源自 `eba91c5`（2026-09-06，ADR-015）；会话记忆称 2026-09-08 已部署至生产并提供控制台资产明细弹窗，但 api.py register 传参 asset 曾因断言失败未写回（部署后 asset 走 hwinfo 回退），补丁是否已重新应用并部署待确认。
6. **disk-cleaner ADR 范围勘误**：任务简报记为「ADR-001~011」，实际仓库 DECISIONS.md 已演进至 ADR-015（`a29b0fb`/`4b8d4b1`/`97794d3`/`fd87cda`）。本编年以仓库为准。
7. **安卓端统一管理**：为规划目标（会话记忆，无日期、无交付物），暂不入大事记，仅于卷首语注明。

---

## 四、附录：各仓库提交历史索引

| 仓库 | 位置 | 首提交 | 最新提交（建档时） | 提交数 | 决策记录 |
|------|------|--------|-------------------|--------|---------|
| winhelper 主应用 | workspace 根（.git）；远程 git.wzeye.cn/zlj/eyeterm（2026-09-10 接入） | `84ea539` 2026-05-22 | `4330047` 2026-09-10 | 62 | docs/ARCHITECTURE-CLIENT.md |
| disk-cleaner | disk-cleaner\（.git） | `3cdeb9b` 2026-09-05 | `35a31a1` 2026-09-06 | 10 | ADR-001~015（docs/DECISIONS.md） |
| log-inspector | log-inspector\（.git） | `dde1c4b` 2026-09-06 | `5ca2c1f` 2026-09-08 | 7 | ADR-001~009（docs/DECISIONS.md） |
| perf-analyzer | perf-analyzer\（.git） | `4192b95` 2026-09-05 | `11c4ddc` 2026-09-09 | 15 | ADR-001~018（docs/DECISIONS.md） |
| server-platform | server-platform\（.git） | `5a21967` 2026-09-06 | `25d48e2` 2026-09-10 | 21 | ADR-001~026（24 空缺，docs/DECISIONS.md）+ docs/login_upgrade_delivery.md、docs/iperf_e2e_report.md |
| net-doctor | net-doctor\（.git） | `539b993` 2026-09-09 | `f603838` 2026-09-10 | 10 | ADR-001~010（docs/DECISIONS.md） |

> 检索方式：`git log --date=short --format="%h %ad %s"`（各仓库根目录执行）。子项目仓库均位于主仓库 workspace 之下，主仓库以 gitlink 方式引用三者（server-platform 当前未以 gitlink 跟踪）。
