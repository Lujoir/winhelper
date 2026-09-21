# desktop-policy · 决策记录（ADR）

> 项目：观枢终端平台｜EyeTerm · Windows 终端「锁屏及壁纸管理」（桌面管控）子系统
> 规则：一条 ADR 记录一个不可逆/高影响决策；新决策追加编号，不改写旧条目。
> 权威规格：`docs/DESKTOP_POLICY_SPEC.md`（主仓 docs/，用户 2026-09-16 批准）。

---

## ADR-001 · 项目立项与同步契约（2026-09-16）

**背景**：服务端控制台需新增「终端桌面管控」模块（壁纸资源库/四类策略/终端组下发/下发记录），终端侧需新增 Windows 常驻执行程序（策略拉取/本地执行/离线兜底）。与 disk-cleaner / perf-analyzer / log-inspector / net-doctor 同模式：独立项目承载演进。

**决策**：
1. 独立项目 `desktop-policy\`，目录结构：
   - `desktop_policy.py`（终端执行引擎，框架无关；P2 阶段落地）
   - `web/desktoppolicy.js` + `web/desktoppolicy-standalone.html`（前端，函数一律 `dp` 前缀；P2）
   - `tools/`（P0 spike 脚本、E2E 与冒烟）
   - `docs/`（DECISIONS.md / CONTRACT.md / 验证报告）
2. 终端侧与主应用（winhelper）集成点（最小增量，P2 执行）：
   - `bridge.py`：ROUTES 增加 `/api/desktoppolicy/*`（不改 service.py）
   - `web/index.html`：导航按钮 `data-tab="desktoppolicy"`（置于 filesearch 之后）+ `tab-desktoppolicy` section
   - `web/app.js`：switchTab 末尾追加 `if (tab === "desktoppolicy" && typeof initDesktopPolicyTab === "function") initDesktopPolicyTab();`
   - 禁止触碰其它子项目契约文件（net-doctor/disk-cleaner/perf-analyzer/log-inspector/home 各自契约文件）。
3. 常驻执行体形态：desktop-policy 引擎作为主应用内常驻后台线程（随主应用启动）承载策略轮询与执行；管理员权限需求（HKLM/电源）见 ADR-005 选型。规格中"Windows 服务或启动项常驻"的独立进程形态，在权限方案定案后评估是否需要独立进程（倾向复用主应用进程 + 安装器注册计划任务补足权限缺口，避免双常驻进程内存翻倍）。
4. 服务端模块归属 `server-platform\server\`：`desktop_policy.py`（策略/资源库/下发记录逻辑）+ store 新表 + `/api/v1/console/desktoppolicy/*` 路由组 + `/api/v1/terminals/{tid}/desktoppolicy/*` 终端 API 组。与 server-platform-dev 协调分工（控制台 UI 由其承接）。

**结果**：采纳（待 P0 spike 通过后进入实施）。

---

## ADR-002 · P0 spike 三项与判定标准（2026-09-16）

**背景**：规格第九节用户提醒——①多显示器独立壁纸是最高难度点；②锁屏壁纸 HKLM 需管理员。P0 三 spike 先行（各 ≤0.5 天），任一失败回报 main 另议路线。

**决策**：三项 spike 全部脚本化落 `tools/`，stdlib-only（ctypes/zlib/struct/subprocess/winreg），默认防破坏模式（备份→验证→自动还原现场），输出中文 PASS/FAIL 报告：

1. **spike A 多屏拼接壁纸**（`tools/spike_multimon_wallpaper.py`）：
   - 机制选型：生成与虚拟桌面包围盒等尺寸的拼接图（逐屏 cover 缩放裁剪）+ `WallpaperStyle=2`（拉伸到虚拟桌面）→ 每屏 1:1 映射实现"视觉独立壁纸"（业界通行方案）。
   - 判定：EnumDisplayMonitors 枚举（含 DPI 感知修正、负坐标副屏）；拼接图逐屏区域采样色与期望一致；GDI 抓屏（GetDC(0)+BitBlt+GetDIBits）回采比对 → 每屏独立显示、无变形无黑边自动判定 + PNG 存档供人眼复核；缩放正确性单测（4K 源 → 1080p 屏区域 cover 裁剪采样断言）。
2. **spike B PersonalizationCSP 锁屏**（`tools/spike_lockscreen_csp.py`）：
   - 判定：HKLM PersonalizationCSP 三值（LockScreenImagePath/LockScreenImageUrl/LockScreenStatus=1 DWORD）写入+读回一致；记录当前进程权限形态（IsUserAnAdmin）；原值备份还原；锁屏视觉生效标注"需人眼 Win+L 复核"（--lock 参数可选触发 LockWorkStation）。
3. **spike C powercfg 备份还原**（`tools/spike_powercfg_backup.py`）：
   - 判定：/getacticescheme 提取 GUID → /q 全量快照（AC/DC 索引 JSON 化，按 GUID 别名定位）→ 修改 VIDEOIDLE AC 通道（测试值避开原值防假 PASS）→ /q 断言生效 → 从快照还原 → /q 比对全等 PASS；附加 /export+/import 全方案级备份验证（导入副本验证后删除）。
   - 中文系统 powercfg 输出 GBK 解码三级兜底（gbk→utf-8→latin-1）。

**失败处理**：任一 spike 判定 FAIL → 停止实施，回报 main 另议路线（候选：拼接改 GDI DirectWrite 路径 / 锁屏改运行时提权服务代理 / 电源改 PowerSetActiveScheme ctypes 直调）。

**结果**：采纳。spike 执行结果与结论见 ADR-003。

---

## ADR-003 · P0 spike 实测结论（2026-09-16，本机 Win11 实测）

**环境**：Jun-office-PC · Win11 · 双屏异分辨率（2560x1440 主 + 1680x1050 副，虚拟桌面 4240x1440 origin(0,0)）· 普通权限 shell + UAC 提权复测。

**spike A 多屏拼接壁纸——机制层 PASS，应用环节环境阻断（4/5）**：
- PASS：per-monitor-v2 DPI 感知；EnumDisplayMonitors 双屏枚举（含主屏标识/负坐标能力）；虚拟桌面拼接图生成（stdlib zlib PNG 手写编码器，4240x1440 仅 35.7KB）；cover 缩放单测（4K→1080p 等比裁剪采样断言）；GDI StretchBlt+GetDIBits 探针（BGR 字节序修正后中心色一致）→ P2 性能缩放路径可行；GDI 抓屏回采链路可用。
- 环境阻断：SPI_SETDESKWALLPAPER 返回 True 但渲染层不消费——TranscodedWallpaper 转码文件 mtime 前后恒定（系统从未执行转码）；本机在跑火绒全家桶（HipsDaemon/HipsTray/wsctrl/usysdiag）+ 深信服 EDR（Sangfor*）+ OneDrive 同步，壁纸变更被终端安全软件静默拦截。已排除：GPO 壁纸策略（Policies 键不存在）、Spotlight、会话错位（SessionId=1 一致）、自写 PNG 格式问题（img1.jpg 同样不生效）、ActiveDesktop（Win11 已移除 0x80040154）。
- **判死依据与复测路径**：同一脚本在无拦截终端（或放行后本机）复测即定论；拼接方案本身是业界通行路线，实现符合全部已知机制，不构成方案失败。

**spike B PersonalizationCSP 锁屏——PASS**：管理员下 HKLM 三值（LockScreenImagePath/LockScreenImageUrl REG_SZ + LockScreenStatus REG_DWORD=1）写入读回全通；原值备份还原干净（键壳残留不算残留）；普通权限 IsUserAnAdmin=False 只读（写 HKLM 需管理员实测确认）；界面级生效待人眼 Win+L 复核（期望图已存 spike_out/lockscreen_spike.png）。

**spike C powercfg 备份还原——PASS**：/getactivescheme GUID 提取；快照解析按 GUID 别名（VIDEOIDLE 实测 GUID=3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e，别名解析比硬编码稳）；修改 AC=601→断言→快照级还原与初始快照全等 PASS；/export+/import 标准平衡方案全链路 PASS（导入副本验证后清理）；**OEM 方案（Lenovo 默认）/import 报「值不在范围」为数据特异性**——引擎回滚以快照级为准，.pow 全量备份仅作运维辅助。
- 附带实测：powercfg /setacvalueindex+/setactive 普通权限即可修改当前用户活动方案；/export、/import 需管理员（SeBackup/SeRestore）。

**结果**：采纳。三 spike 无一构成路线失败，P0 通过（A 项遗留一台无拦截终端的最终确认）。

---

## ADR-004 · 壁纸生效判定铁律 + 安全拦截检测（2026-09-16）

**背景**：spike A 实测两个陷阱：①采样点落在应用窗口上误判（抓屏抓的是含窗口的合成桌面）；②SPI_GETDESKWALLPAPER 读回值与注册表 Wallpaper 值不一致（读的另有缓存），不能作为生效依据。

**决策**：
1. **生效判定唯一标准 = 抓屏回采比对**（BitBlt GetDC(0)+GetDIBits），判定前必须先最小化全部窗口清场；SPI 读回与注册表值仅作记录不作判定。
2. **执行后自检三件套**（P2 引擎内建，防安全软件静默拦截）：①TranscodedWallpaper mtime 是否推进；②注册表 Wallpaper 值是否落位；③抓屏采样是否与拼接期望一致。三检不过 → 上报 `blocked_by_security`（执行结果回传 error.code 枚举，见 CONTRACT.md），服务端下发记录留痕并告警。
3. **部署前提（写入安装说明与控制台提示）**：目标终端需将 EyeTerm 终端程序加入安全软件信任区/放行壁纸变更（火绒「系统防护」、深信服 EDR 策略），否则壁纸策略将持续被拦截并如实上报。

**结果**：采纳。

---

## ADR-005 · 管理员权限方案定案：计划任务最高权限 + 主应用用户会话常驻（2026-09-16）

**背景**：规格第九节用户提醒——锁屏 HKLM 需管理员，普通权限运行须服务方式部署（用户原话倾向 LocalSystem）。需实测依据后定案。

**实测依据**（本机 2026-09-16）：
1. spike B：HKLM 写入需提权 token（IsUserAnAdmin=False 只读；提权后写入读回全通）——**不要求 SYSTEM，任何提权 token 均可**。
2. spike C：电源计划 setacvalueindex/setactive 普通权限即可改当前用户活动方案（实测 601 秒写入读回）；/export、/import 需管理员。
3. Windows 会话亲和：壁纸/桌面 SPI 属于交互会话资源，**LocalSystem（Session 0）无桌面可设**，服务进程执行壁纸必须再 CreateProcessAsUser 到用户会话——形态复杂且双常驻进程内存翻倍（违反 <50MB 红线）。

**决策**（混合形态，按动作分通道）：
1. **主执行体 = winhelper 主应用内常驻线程**（用户会话，随主应用启动）：策略轮询、壁纸/锁屏文件、多屏拼接、执行自检、离线兜底——全部用户会话可完成。
2. **权限缺口 = 安装器注册计划任务（/rl HIGHEST）**：安装时注册一个以最高权限运行的辅助任务（EyeTerm 提权助手：按参数写 HKLM PersonalizationCSP / 清理锁屏值），主应用经 `schtasks /run` 触发、经状态文件/退出码取回结果。锁屏值写入频率低（策略变更时），触发式完全够用。
3. **电源计划 = 直接普通权限执行**（实测可行）；若目标方案为全机统一策略再评估提权执行（保留口子）。
4. **LocalSystem 服务否决**：Session 0 无法执行壁纸（核心功能之一）、双进程常驻内存翻倍、服务安装/升级/卸载复杂度高——三重成本无对应收益。
5. RDP 防误应用：引擎轮询 WTSGetActiveConsoleSessionId + 会话状态（WTSQuerySessionInformation），会话非 console 时跳过壁纸/锁屏应用并上报 `rdp_skipped`。

**结果**：采纳（用户提醒②已按实测定案；安装器改动在 P3）。

---

## ADR-006 · terminal_id 三级解析与轮询状态透出（2026-09-16，BRG-064）

**背景**：joint_preflight 首跑拿不到 tid。main 实测定向：uplink.py 注册成功后
tid 只存内存（`_set_state(terminal_id=tid)`）不回写配置文件——服务端认
WIN-Jun-office-PC，而 transport 读配置必然为空。修复定向为「进程内直取（首选）」。

**决策**：`PlatformTransport._cfg` 的 terminal_id 三级解析：
1. **配置值**（uplink_config.json，用户显式自定义优先）；
2. **进程内直取**：`import uplink; uplink.handle_uplink_status(None)` 公共访问器
   （与 bridge /api/perf/uplink/status 同源，含注册成功后的内存 tid；框架无关
   环境 import 失败静默 None）——**不动 uplink.py 一行，未新增 accessor**，
   现成访问器即满足，无需协调 perf-analyzer-dev；
3. **同源 hostname 兜底**：复刻 `uplink._default_terminal_id` 的 `WIN-<host>`
   规则（服务端注册记录默认口径，独立运行环境可用）。

仍为空 → `_request` 抛 `not_registered` → tick 离线兜底跳过 +
`Engine.last_poll_error` 透出（handle_dp_status 返回 terminal_id_ready /
last_poll_error / last_poll_ok_ts，前端状态条徽章三态：未接入（黄）/
连接异常（黄）/正常（绿））——「轮询跳过并如实上报，不静默失败」语义闭环。

**结果**：采纳。预检回传与全链联调随窗口执行。
