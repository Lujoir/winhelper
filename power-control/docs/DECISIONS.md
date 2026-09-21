# power-control · 决策记录（ADR）

> 项目：观枢终端平台｜EyeTerm · Windows 终端「自动开关机」子系统
> 规则：一条 ADR 记录一个不可逆/高影响决策；新决策追加编号，不改写旧条目。
> 权威规格：`docs/POWER_CONTROL_SPEC.md`（主仓 docs/，用户 2026-09-16 定案）。

---

## ADR-001 · 项目立项与同步契约（2026-09-16）

**背景**：EyeTerm 需新增终端「自动开关机」管控能力。用户 2026-09-16 定案：主要对象 ThinkCentre/扬天商用线（方案 A：BIOS RTC Wake 厂商 WMI 读写），消费线（IdeaCentre/拯救者/GeekPro/天逸）并存保留、如实标注「不支持远程配置」+ 人工 BIOS 指引。P0 只读（无任何本机写操作）。

**决策**：
1. 独立项目 `power-control\`，目录结构（与 desktop-policy/net-doctor 同模式）：
   - `power_control.py`（终端侧快照引擎，框架无关）
   - `web/powercontrol.js` + `web/powercontrol-standalone.html`（前端，函数一律 `pc` 前缀）
   - `tools/`（单测 / smoke / E2E）
   - `docs/`（DECISIONS.md / ARCHITECTURE.md）
2. 终端侧与主应用（winhelper）集成点（最小增量）：
   - `bridge.py`：ROUTES 增加 `/api/powercontrol/snapshot`、`/api/powercontrol/report`（不改 service.py）
   - `web/index.html`：导航按钮 `data-tab="powercontrol"`（置于「锁屏及壁纸管理」之后）+ `tab-powercontrol` section + script 引入 + 首帧自愈 INITS 登记
   - `web/app.js`：switchTab 末尾追加 powercontrol 分支（typeof 守卫）
   - 禁止触碰其它子项目契约文件（desktop-policy/huorong/net-doctor/file-search/log-inspector 各自契约文件）。
3. 服务端模块 `server-platform\server\power_control.py`（自持 SQLite 存储，desktop_policy 模块同款形态）+ `api.py` 终端上报分支 + 控制台查询组 + `app.py` 挂载一行。遵循 ADR-002 零第三方依赖。
4. P0 红线：全只读采集；零 BIOS 写、零计划任务写、零 shutdown 调用；测试夹具禁用真实关机命令串（用 `stub_shutdown_sim.exe` 类占位）。

**结果**：采纳。

---

## ADR-002 · 企业线判定与采集通道（2026-09-16）

**背景**：main 已实测开发机（LENOVO 91AY000LCP，SystemFamily=IdeaCentre GeekPro-17IAX 消费线）root\wmi 无 Lenovo_BiosSetting 类；消费线仅有 LENOVO_OTHER_METHOD 非公开接口（禁止产品化）。企业线预期接口为 root\wmi Lenovo_BiosSetting（CurrentSetting "ItemName,Value"）等，待商用实机验证。

**决策**：
1. **分线判定 = 厂商 + 类存在性探测**（不单看型号/SystemFamily）：
   - Manufacturer 含 LENOVO 且 `Lenovo_BiosSetting` 类存在 → 企业线可配置
   - Manufacturer 含 LENOVO 且类不存在 → 如实上报「本机 BIOS 未提供企业线远程配置接口，需在 BIOS 菜单人工设置」
   - 其它厂商 → 「其他厂商（未适配远程配置）」
2. 采集通道全部走标准库：`subprocess` 调 PowerShell（`[Console]::OutputEncoding=UTF8` 前缀 + `-NoProfile -NonInteractive`，输出统一 UTF-8 解码，gbk 兜底）；powercfg / schtasks 直调；注册表用 `winreg`。零第三方依赖、零 ctypes 依赖（P0 无需）。
3. CurrentSetting 解析为**独立纯函数**（`parse_current_setting`），企业线样例以 mock 单测覆盖（RTC 各项/杂项/空值/null）；真机验证留待取得 ThinkCentre/扬天实机（门禁遗留项，如实标注）。
4. RTC 项映射候选名单（大小写/空格/括号归一后匹配）：总开关（Automatic Power On Control）、唤醒开关（Wake Upon RTC Alarm / Automatic Power On RTC Alarm）、日期（RTC Alarm Date (Of Month) / Day）、时（Hour (UTC)/Hour）、分（Minute）、秒（Second）；未知项原样保留不丢弃。多数商用 BIOS 仅支持「每天 HH:MM」——周期语义如实呈现，不夸大。

**结果**：采纳。

---

## ADR-003 · P0 上报路径：按需直连，不动 uplink.py（2026-09-16）

**背景**：SPEC P0 第 4 条为「随心跳上报快照」，但 uplink.py 心跳协议为主仓共享契约文件（perf-analyzer 等多方依赖），单方面扩展心跳载荷风险高；P0 需要的是「服务端可存档、控制台可查」的最小闭环。

**决策**：
1. P0 上报 = power_control 引擎内置 `PlatformReporter`（语义复制 desktop_policy PlatformTransport：uplink_config.json 的 server_url/token/terminal_id → 进程内 uplink 内存 tid → WIN-<host> 兜底；X-ETP-Token 头），POST `/api/v1/terminals/{tid}/powercontrol/snapshot`。
2. 触发方式：主应用菜单打开自动上报（12 小时节流，状态落 `last_report.json`）+ 手动「上报平台」按钮；不触碰 uplink.py 一行。
3. 心跳随载上报列入 P1（需与 uplink 契约所有方协调后并入心跳 JSON）。
4. 上报仅传输快照 JSON（本机只读采集结果），不构成本机写操作，不违反 P0 零写红线。

**结果**：采纳。

---

## ADR-004 · subprocess 铁律与失败如实上报（2026-09-16）

**决策**：
1. 全部子进程：`CREATE_NO_WINDOW`（0x08000000）+ 超时（默认 30s，schtasks /v 60s）+ 失败重试 3 次（指数退避 0.5/1/2s）+ 日期分文件日志（`%LOCALAPPDATA%\winhelper\power-control\logs\pc_YYYYMMDD.log`）。
2. 解码三级兜底 utf-8 → gbk → latin-1（中文系统控制台输出）。
3. 任一采集段失败：该段返回 `ok=false + error 原文`，不阻断其它段，顶层 `errors` 汇总——快照永远可出（部分可用如实标注）。
4. `powercfg /waketimers` 部分系统需管理员：非管理员下返回 need_admin=true 徽章，UI 如实提示「需管理员权限查看」，不伪造空结论。
5. schtasks 列解析按表头名模糊定位（任务名/要运行的任务/下次运行时间/状态/计划类型，中英双语），失败回退固定下标（1/8/2/3/18）。

**结果**：采纳。

---

## ADR-005 · M720t 实测样例对齐（解析器契约 v2，2026-09-16）

**背景**：main 于用户提供的商用实机（ThinkCentre M720t-D234，10SWA03ECD，BIOS M1YKT56A）完成只读探查，取得企业线真实数据——与本机预期差异显著，解析器按实测重写。实测 255 项 dump 固化为 `tools/fixtures/lenovo_bios_m720t_20260916.txt`。

**实测事实**：
1. root\wmi 存在 Lenovo_BiosSetting / Lenovo_SetBiosSetting / Lenovo_BiosPasswordSettings 等 12 个 Lenovo* 类；**不存在 Lenovo_GetBiosSelections**（可选项内嵌 CurrentSetting `[Optional:...]` 段）。
2. CurrentSetting 两形态：
   - 常规：`ItemName,Value;[Optional:v1,v2,...]`（可再带 `[Status:ShowOnly]`）
   - 值带括号：`Alarm Time(HH:MM:SS),[08:00:00]`、`Alarm Date(MM/DD/YYYY),[01/01/2017][Status:ShowOnly]`
   - 另有非 Optional 括号段（`Primary Boot Sequence,...;[Excluded from boot order:...]`）→ 解析为 note，不混入值。
   - 全量枚举含 CurrentSetting 为空的项 → 容错跳过。
3. 该机 RTC 实况：Wake Up on Alarm = Daily Event，Alarm Time = [08:00:00]，After Power Loss = Last State，WakeOnLAN = Automatic；PasswordState=0（未设 BIOS 密码）。
4. `powercfg /waketimers` 真实输出（zh-CN Win10）为 `[SERVICE] <path> (svc) 设置的计时器在 18:48:18 过期(位于 2026/9/16 上)。` + 下一行 `原因: ...`——与旧文档「计时器 ID [0]: ...」格式不同，解析器双格式兼容。
5. 本机（消费线开发机）实测：`powercfg /waketimers` 非管理员返回拒绝访问 → need_admin 徽章路径真实可达（smoke 实证）。

**决策**（解析器契约 v2）：
1. 条目结构 `{item, value, optional[], status, note}`；`[Status:...]`/`[Optional:...]` 提取后，值按首个 `;` 截断，`[V]` 括号剥除。
2. RTC 语义键：`alarm`（Wake Up on Alarm：Disabled=关，Daily Event/Weekly Event/Single Event/User Defined=开）、`time`/`user_time`（HH:MM:SS）、`date`、`day`、`weekdays{}`（Sunday~Saturday 逐日开关）、`after_power_loss`、`wake_on_lan`、`alarm_on`、`cycle_text`、`summary`（如「每天 08:00:00」）；旧机型候选（Automatic Power On Control 等）保留兼容，只按实测可选值推导语义、不虚构。
3. mock 单测样例全部替换为 M720t 实测字符串 + fixture dump 全量解析断言（37 项单测含 17 项解析器项）。
4. **周期策略修正（对 SPEC §3 的反馈，回报 main）**：M720t 的 Wake Up on Alarm 支持 Weekly Event + 逐日开关（Sunday~Saturday）→「工作日开机」在该 BIOS 上可用 Weekly Event+周一~五 直接实现，优于 SPEC 的「每天开机 + 周末回关」组合；P2 策略模板设计按机型能力动态选择。

**结果**：采纳。

---

## ADR-006 · BIOS 写入路径与值格式两轮尝试（2026-09-16，P1a）

**决策**：
1. 写入走 `Lenovo_SetBiosSetting.SetBiosSetting("Item,Value")`（PowerShell `Invoke-CimMethod -Namespace root/wmi`），返回 `PCSET=<ReturnValue>`（0=成功）；命令串由受控项名/值域构造，单引号防御转义。
2. **值格式两轮尝试**：第一轮无括号值（`Alarm Time(HH:MM:SS),08:30:00`）→ 全量回读 → 不匹配项第二轮带括号值（`...,​[08:30:00]`，与读取形态对齐）→ 再回读。真实格式以 M720t 实机定案（结论回填 ADR-006 附注）。
3. **回读校验归一**：剥 `[]`/空白、忽略大小写（`[08:00:00]` ≡ `08:00:00`）；读回≠目标即 failed——**绝不静默成功**，失败明细（item/want/got）逐项回传。
4. 写入仅两条路径触发：UI「应用」按钮（提权异步任务）与真机验证脚本；单测/调试全 mock，禁真实写。

**结果**：采纳。附注①（2026-09-17 首轮实机）：M720t「每天 09:00」应用失败——症状=两轮格式 rv=0「接受但不落盘」、读回旧值 08:00:00（Wake Up on Alarm 原 Daily 匹配故仅 Time 报 failed）；最高概率原因=固件写入 pending 需 **Lenovo_SaveBiosSetting.SaveBiosSetting() 显式提交**。已实施修复：`bios_apply_targets` 每轮写入后探测并调用 SaveBiosSetting 提交（探测+提交合一 PowerShell，类不存在 PCSAVE=NA 自动跳过向后兼容；commit 作为 attempts 条目可追溯）；mock runner 扩展 pending/commit 语义三用例（修复路径/兼容/症状复现），91/91。**附注②（待实测回填）**：修复版 verify exe（weekly/bios-write 子命令均走同一提交链）输出将最终坐实——若 Save 后仍不落盘则转向候选因 2（SVP 密码前缀，需过设计）。项名核对结论：readback 命中 08:00:00 证明 `(HH:MM:SS)` 圆括号键名匹配无误，截图方括号为显示差异。

**附注③（2026-09-17 定案修正，diag 数据实锤）**：M720t 的 rv=null **不是 BIOS 静默忽略，而是命令从未执行成功**——`_ps_bios_write` 的 `Invoke-CimMethod -ClassName Lenovo_SetBiosSetting` 在该机型抛「invalid class」，PowerShell 外壳 rc=0 与命令成败解耦，`_parse_set_rv` 无 PCSET 输出得 null；diag 佐证 `save_class=false`（老 Save 类同缺失）、PasswordState=0（排除密码）、读取通道（Lenovo_BiosSetting CurrentSetting）正常。**根因：M720t（2018+ ThinkCentre）用新一代 Lenovo BIOS WMI 接口——Lenovo_BiosSetting 实例自带 per-instance `SetBiosSetting("Item,Value")` 方法，老接口类 Lenovo_SetBiosSetting/Lenovo_SaveBiosSetting 双双缺失。**修复（随 4.1.3）：①写入链接口回退——单脚本内老接口优先（兼容旧机型），异常或 rv!=0 → 枚举 Lenovo_BiosSetting 实例按 `CurrentSetting.StartsWith('Item,')` 前缀匹配目标项，`Invoke-CimMethod -InputObject` 调用实例级 SetBiosSetting（modern 无需 Save，落盘由回读校验兜底）；输出 PCSET=<rv|NA>/PCVIA=<old|new>/PCERR=<异常摘要>，rv=null 不再静默。②attempts 补 via/err 摘要（缺陷 F 的 BIOS 路径版）。③能力探测扩展：`probe_write_iface` 只读探测 old/new/none/unknown，probe_lenovo_bios 与 pc_diag 均携带；snapshot bios.write_iface + UI 写入通道徽章（新一代/标准/待确认）；`_job_bios_apply` 对 none 提前拒绝（不浪费提权往返），unknown 不武断拒绝（脚本内兜底）。单测 mock runner 扩 iface 形态三模拟（new 成功/new 拒绝/none 双缺失）+ probe 五用例，105/105；两脚本 PowerShell Parser 静态解析零错误。**待 90.18 升级 4.1.3 后实测回填：modern 接口方法签名/参数格式如与文档有出入，pc_diag 只读探测实锤后修正。**

**附注④（2026-09-17 diag#2/#3 定案，随 4.1.4）**：diag#2 实测两事实——①via=new err=「找不到方法 SetBiosSetting」：M720t 的 Lenovo_BiosSetting 实例**方法枚举层面缺失**，per-instance SetBiosSetting 假设不成立；②probe write_iface=old 但老接口写入无 PCSET：4.1.3 attempts 设计 old/new 共用一条记录只留最后一个 err，**老接口真实失败原因被 new 的错误覆盖（记录缺陷）**。修复：①写入链输出协议升级 PCOLD/PCNEW 分开记录（各自 rv/err 原文，skip=老成功未尝试），禁止后者覆盖前者，attempts.old/.new 独立字段；②pc_diag 新增 wmi_surface（穷尽枚举 root\wmi 全部 Lenovo_* 类名 + SetBiosSetting/BiosSetting/SaveBiosSetting/BiosSettingInterface 四类方法名清单 + probe 判定依据三项布尔）——ADR-006 终审证据；③顺带两观察修正（server-platform 实测）：apply_policy 收尾同步刷新 _LAST_APPLY（中心下发失败现场不再对 diag 不可见）+ 心跳间隔自报统一取生效值（_state.effective_interval：服务端下发覆盖 > 本地配置 > 默认；修复自报 30/实际 60 双值矛盾；连带修复 pc_diag 持 _lock 内调 helper 的 Lock 不可重入死锁——单测抓出，真机将挂死命令线程）。门禁 108/108 + 35/35 + E2E 96/96。**diag#3（4.1.4）wmi_surface 数据到手后 ADR-006 终审：有可用写入方法→实现；无→固件能力边界定案（该机型走人工登记）。**

**附注⑤（2026-09-18，main 定稿）：中心发起的重启/关机（UPL power_action / power_action_abort）**。用户点名需求（参考脚本 定时关机_V1.0.2.bat）：shutdown -s -f -t 延迟、倒计时缓冲、shutdown -a 撤销。管控语义：**仅中心可发起**——本地 UI（自动开关机页等）不提供任何立即关机/重启入口（E2E 门禁断言源码+DOM 无入口、无 power_action 调用）；终端侧仅倒计时知会弹窗（MessageBoxW 独立线程非阻塞）、**不提供本地取消**（中心意图优先），撤销走 power_action_abort（shutdown /a，rc=1116 无 pending 如实回执）。参数约束：action 白名单 shutdown|restart；delay_sec 0-3600（缺省 60，非法拒绝且不执行任何命令）；force 缺省 True 对齐 -f（false=给应用正常关闭机会）。安全：命令串仅受控参数构造（build_power_action_argv 纯函数）、CREATE_NO_WINDOW、执行前后写 pc_*.log 本地审计 + 服务端回执 cid 审计双存档；shutdown 为 OS 命令不依赖 WMI，与 WoL 唤醒早期 WMI 未就绪问题无交集。实现：power-control/power_action.py（零第三方依赖、runner 可注入、单测全 mock 禁真实 shutdown）；uplink @command_handler 注册两命令。门禁 46/46 + 108/108 + E2E 104/104（+8 管控语义断言）。随 4.1.5 出包。

## ADR-007 · 写前原值快照双存档与一键还原（2026-09-16，P1a）

**决策**：
1. 写前强制快照：读 RTC 项全集（Wake Up on Alarm/Alarm Time/Alarm Date/逐日 7 项）→ 本地 `bios_backup.json`（原子写）+ 平台快照上报双存档；平台存档失败不阻断写入（日志 WARN）。
2. 一键还原 = 逐项写回备份值 + 回读校验；备份标记 restored；无备份时还原请求直接拒绝。
3. 快照范围 = 「本次将写入的项 ∪ 当前存在的 RTC 项」，不碰 Wake On LAN 等无关项。

**结果**：采纳。

## ADR-008 · 单操作提权 worker（2026-09-16，P1a/P1b）

**决策**：
1. **单操作提权子进程**（main 定案过渡方案）：`ShellExecuteW(runas)` 拉起自身执行**单个**操作（BIOS 写/还原 或 schtasks 单操作），UAC 确认制；子进程结果写 `opfile+".result"` JSON 回传，父进程轮询（默认 240s——含人工 UAC 时间；策略执行路径 30s）。
2. 态判据 = `sys.frozen`（**不能**用 `.exe` 后缀——python.exe 同样 .exe 结尾，单测抓出）；exe 态由 desktop.py 顶层最早拦截 `--pc-elevated-worker` 并 exit（任何 UI/服务初始化之前，非提权调用零开销）；python 态由引擎 `__main__` 拦截。
3. worker 最小职责红线：只执行 spec 描述的单个操作，禁止提权会话内做其它事；UAC 取消/超时如实返回不静默。
4. 提权形态常驻化（最高权限计划任务/LocalSystem 服务）延至 P2 与 desktop-policy 联合定案。

**结果**：采纳。

## ADR-009 · 异步任务模式与平台下发执行器（2026-09-16，P1）

**决策**：
1. UI 提权操作走**异步任务**（PCT-xxxxxxx：POST 回 task_id + 1.5s 轮询）——提权全流程含 UAC 等待远超 `apiFetch` 15s 超时上限；desktop-policy dpRunTask 同款形态。
2. **顺带缺陷修复（跨模块）**：宿主 `apiFetch(path)` 此前不透传 body——desktoppolicy 电源修改与 powercontrol 写操作在主应用内参数全丢（独立页正常、主应用静默失效）；已补 body 形参（POST 语义），desktop-policy 同步受益。
3. **pc_apply_policy 执行器**（协议 main 定稿）：注册进 uplink `@command_handler`（ADR-015 机制）；boot/shutdown 可只出现其一；boot 步骤企业线判定复用 P0 探测，消费线 rejected（capability=not_supported）而 shutdown 照做；执行链复用 P1a/P1b 引擎与安全闭环；回执 `{policy_id,op,ok,steps,capability}`。**无人值守 UAC 限制如实回执**（提权等待 30s，无人确认=失败），常驻化 P2 解决。
4. `build_bios_targets` 通用化：`weekly`+`weekdays`（7 位周一~周日 0/1，协议契约）为正则形态，`workday` 为其快捷写法。
5. 成功后落 `policy_state.json`，「自动开关机」页显示「平台下发生效（策略尾号+摘要）」，页面注明**本机设置与平台下发后到者生效**（P1 单机语义；P2 覆盖优先级按 SPEC）。

**结果**：采纳。

---

## ADR-010 · 注册预置：文件方案 + 客户端解码（2026-09-17，三大改造①）

**决策**：
1. 安装包文件名携带配置（协议 main 定稿）：`EyeTerm_Setup_x64_{ver}_{cfg64}.exe`，`cfg64 = base64url(zlib(json{"s","t"}))`。
2. **Inno 侧只做固定前缀切分**（`EyeTerm_Setup_x64_` 之后、`.exe` 之前），raw 写 `{app}\config_bootstrap.json`（`{"cfg64":"..."}`）——Inno Pascal 不做 zlib/base64 解压（无内置库，自写 inflate 风险高）；解码/校验统一在客户端 `bootstrap.py`（可单测）。**切分语义关键点：cfg64（base64url）字符集含下划线，不能按末个下划线切分**（Python 正则首版即犯此错——贪婪回溯截短 cfg64，单测抓出修正为版本段惰性）。
3. **文件方案优于 HKLM 注册表**：卸载随目录清除无残留；明文可审计便于批量运维（管理员直接替换文件）；Inno `SaveStringToFile` 单行实现；token 明文可见性与既有 uplink_config.json 同级（接受）。
4. 采用策略：**仅当 uplink_config 中心未配置时采用**（避免升级重装把用户既有正确配置覆盖为旧预置值）；解码失败/字段不全 → 静默返回，**完全回退手动流程，零行为变化**。
5. 校验：server 必须 http(s)://、token 非空；bootstrap 文件兼容两形态（cfg64 raw / 运维手工明文 {"server","token"}）。

**结果**：采纳。Inno 编译实测待 main 构建管线（本机无 ISCC；Pascal 逻辑静态论证 + 客户端同构单测 22 项覆盖解析/回退）。

## ADR-011 · 托盘常驻：ctypes 手写三陷阱（2026-09-17，三大改造②）

**决策**：
1. `Shell_NotifyIconW` 手写（零第三方依赖红线）；隐藏消息窗口 + WM_APP 回调；右键菜单 `TrackPopupMenu(TPM_RETURNCMD)` 同步取选择 + `SetForegroundWindow` 前置规避菜单不消失系统行为；图标 exe 同目录 app.ico（安装器随包）→ 系统 IDI_APPLICATION 兜底。
2. **实测三陷阱**（全部冒烟抓出并修复）：
   - `Shell_NotifyIconW` 属 **shell32** 而非 user32——误用 AttributeError 被线程吞掉造成**假成功**（start() 返回值改为以真实 ADD 结果为准）；
   - WNDPROC ctypes 回调对象必须**实例级长引用**，否则 GC 后消息回调 → 0xC0000409 崩溃；
   - `ctypes.wintypes` 无 WNDCLASSW（3.12 实测），需自定义；`DefWindowProcW` 必须显式 argtypes（WM_RBUTTONUP 的 lparam 超 int32 → OverflowError）。
3. 关窗收纳：pywebview `events.closing` 返回 False 阻止默认关闭 → `window.hide()`；托盘「退出」→ 删图标 → destroy 真退出（后台线程全 daemon，主线程返回即净退；退出前写 updater main.pid 锚点）。
4. **「服务化」评估（用户原话，如实回报）**：GUI（pywebview）无法进 session 0，真服务化需把业务引擎拆为后台进程 + 前端壳——架构级改造。本期以「开机自启 + 托盘常驻」满足服务化体验；拆分架构列 **P2 评估项**，不做承诺。

**结果**：采纳。冒烟 2/2（真实图标 ADD/DELETE + 消息循环退出，stderr 干净）。

## ADR-012 · 自动更新引擎与版本链（2026-09-17，三大改造③）

**决策**：
1. 触发：心跳响应 `latest_version`（server-platform 并行交付）→ 三段数字 semver 比对（修饰后缀剥除按 0；空/垃圾版本视为不可比不误更新）→ 拉取清单 `GET {server}/api/v1/client/update-manifest?version=`（TBC-002：形状按 `{"latest_version","download_url","sha256"}` 先行实现，联调对齐）→ 下载到 `%PROGRAMDATA%\EyeTerm\update\`（sha256 校验、失败重试 3 次、512MB 上限、.part 原子落盘）→ 状态机 `idle|pending|downloading|ready|failed` 落档。
2. 应用：UI 提示条（30s 轮询 /api/app/update-status；「稍后」sessionStorage 本会话不再弹同版本）→「立即更新」→ `/api/app/update-apply` 启动 updater 子进程（`--et-updater` desktop.py 顶层第三拦截形态）→ 等主进程退出（pid 文件 + OpenProcess 轮询，最多 2 分钟）→ 安装包 `/SILENT /SUPPRESSMSGBOXES /NORESTART` → 安装器 postinstall 自启新客户端。**权限预判**：安装包签名/权限沿用既有安装器（admin），updater 不做提权动作。
3. 旧版安装包保留 update 目录（升级覆盖由安装器自然处理）；预置配置随文件名继承（cfg64 携带在更新包文件名内 → 装完即注册语义自动延续）。
4. **顺带缺陷修复**：bridge 无 `register_exit_hook`（desktop.py 注册 AttributeError 被吞，性能「以管理员重启」hook 从未生效）——本次补齐 bridge 侧 hook，更新退出链复用。
5. 版本链：CLIENT_VERSION 4.1.0（uplink）= installer `#define MyAppVersion` 4.1.0；server manifest 为发布源。

**结果**：采纳。清单端点联调等 server-platform 交付；更新全流程 E2E 以 stub 桩覆盖（提示条展示/稍后/apply→退出提示）。

## ADR-013 · 定时开机中心任务驱动 + 定时关机本地配置版本化上报（2026-09-18/19，4.1.8 页改追加）

**决策**（用户定案架构，main 派单两批合并落地）：
1. **定时开机=中心任务驱动**：原「BIOS 定时开机配置卡」（企业线 WMI 写入 UI / 消费线登记按钮）由「定时开机（中心任务）」卡取代——连接门控（`/api/perf/uplink/status` 的 `state === "connected"`，复用锁屏及壁纸管理既有门控口径）→ 生效任务只读列表（GET 中心、按下次触发时间升序、最早在最上；行=任务名/计划模式/时刻/来源/下次触发）→ 个性化任务创建表单（每天/每周勾选/单次先行；工作日/节假日置灰待日历；POST 中心 `origin=client_personal`，超限以中心 error 原文如实透出；「登记到平台」语义并入表单备注）。执行在中心侧，终端零本地定时。**本机快照卡保留**（BIOS RTC 只读展示，企业/消费双分支指引不变）。
2. **中心通道复用 ADR-003 直连**：`_center_call` 封装 PlatformReporter 通道，HTTPError 提取响应体 error、断链统一 `not_connected`；三条路径常量集中（`PC_SCHEDULES_EFF_PATH`/`PC_SCHEDULES_PATH`/`SD_CONFIG_REPORT_PATH`，暂定 `/api/v1/terminals/{tid}/powercontrol/{schedules-effective|schedules|shutdown-config}`，**契约定稿后仅改常量**）。桥接新增 `/api/powercontrol/center-tasks`、`/api/powercontrol/center-task-create` 两路由。
3. **定时关机=本地配置本地执行 + 版本化上报**：schtasks 引擎与 apply_policy 执行器零改动；`_job_shutdown_set/_job_shutdown_toggle/_job_shutdown_remove` 成功后落本地存档 `%LOCALAPPDATA%\winhelper\power-control\shutdown_config.json`（自增 `config_version` + 语义 sha256 前 16 位 `config_hash` + `report_pending`）并静默异步上报（UI 与平台下发共用 job 钩子，触发点全覆盖）。触发点=**连接成功 / 配置变更**：uplink 新增 `on_connected` 监听器（状态由非 connected 翻转触发，后台线程、异常隔离）；断链不弹窗，失败保留 pending，下次触发自然重报，中心按 version/hash 去重。v0（从未配置）也如实上报无配置状态。pc_diag 诊断读取与存档互不影响。
4. UI 侧随形态调整移除：`pcApplyBios/pcRestoreBios/pcFillBootForm/pcRegisterHumanSet` 及 `.pc-wd` 回填；`bios-apply/bios-restore/report` 桥接路由与引擎能力保留（UI 无调用方；平台侧如需 BIOS 还原/存档语义另行决策）。

**结果**：采纳。单测 118/118（+13：通道错误翻译/形状校验/归一化/存档版本摘要/上报 pending 生命周期/job 钩子）；E2E 113/113（门控降级/排序/来源徽章/配额/创建 payload/超限/拉取失败可见/pageerror=0）。遗留：中心三接口契约待 server-platform 定稿（对齐 `power_tasks` 模型），定稿后核对路径与字段名（`time_hhmm/weekdays 7 位/once_date/source 枚举`），排序如中心提供 `next_fire` 字段则优先采用。

**附注（2026-09-19 契约定稿对齐，server-platform 批 A commit 99f4079）**：
1. 路径定稿：GET/POST `/api/v1/terminals/{tid}/powercontrol/boot-tasks`（原暂定 schedules-effective/schedules 两常量合一）；POST `/terminals/{tid}/powercontrol/shutdown-config`（与暂定一致）；PUT/DELETE `/terminals/{tid}/powercontrol/boot-tasks/{task_id}`（个性化维护，origin=client_personal 归属锁定 403）。常量收敛为 `PC_BOOT_TASKS_PATH` + `SD_CONFIG_REPORT_PATH`。
2. 字段定稿：task_id/name/repeat/weekdays(7位周一..周日)/once_date/time_hhmm/source/origin/method/next_trigger(本地时间文本)/next_ts(epoch)/calendar_fallback；来源徽章映射与我方实现一致；POST 409 超限（每终端 5 条）/400 校验原文透出（原实现已对齐）。
3. 排序定稿：中心按 next_trigger 升序返回；客户端 `pcNextFireTs` 优先采用 `next_ts`（缺省降级本地推算），下次触发展示优先 `next_trigger` 文本；`calendar_fallback` 行加「≈」+title 近似标记。
4. 补个性化任务删除闭环：engine `handle_pc_center_task_delete`（DELETE，task_id isdigit 校验）+ bridge 路由 + UI 仅 origin=client_personal 且有 task_id 的行渲染「删除」（danger 确认→刷新）；PUT 编辑端点暂不接 UI（产品需要时按同形 handler 补）。
5. 部署时序：服务端批 A 延后至明早 07:30 唤醒验证成功后部署（迁移红线）；本调整随 4.1.8 客户端批入库，不出独立包。
6. 验证：单测 120/120（+删除用例 2、路径断言更新）；E2E 117/117（+中心 next_ts 排序/中心文本/日历近似标记/删除入口仅在个性化行/删除流程）。
