# 架构决策记录（ADR）— Performance Analyzer

> 本文件是 perf-analyzer 项目的"记忆载体"。所有设计决策、踩坑史、约束在此沉淀。
> 开发前必读；修改核心行为时必须追加记录并 git commit。

## ADR-001 与 winhelper 主应用的同步契约（2026-09-05）

- **演进以本项目为准**，发布同步回主应用：
  - `perf_service.py` → 主应用根目录
  - `web/perf.js` → 主应用 `web/`
- 主应用集成点保持**最小、增量、有守卫**：
  - `web/index.html`：导航按钮 `data-tab="perf"`（置于磁盘清理之后）+ `tab-perf` section（perf 特有样式放 section 内部 `<style>`，**不改 style.css**）
  - `bridge.py`：`from perf_service import handle_perf_*` + ROUTES 增加 `/api/perf/*`（**不改 service.py**，减少共享文件冲突）
  - `web/app.js`：switchTab 末尾追加一行守卫 `if (tab === "perf" && typeof initPerfTab === "function") initPerfTab();`
- **禁止触碰** disk-cleaner 契约文件：disk_cleanup.py / appdata_scan.py / web/disk.js / web/appdata.js（以及 disk-cleaner 项目自身文件）/ web/style.css

## ADR-002 技术选型与依赖约束（2026-09-05）

- **唯一运行时依赖 psutil**（主应用 requirements.txt 同步添加；exe 打包经 PyInstaller import 分析自动收录）
- 不引入 wmi/其他依赖：SSD/机械盘判定不可靠，硬件建议措辞做双分支覆盖（"若为机械盘→升级 NVMe SSD；若已是 SSD→检查空间与后台写入"）
- CPU 占用不用 `psutil.cpu_percent(interval=None)`（全局共享基准，多调用方互相干扰），改为**自维护 cpu_times delta**：每个状态持有者（实时快照 / 各记录线程）独立保存上次 cpu_times，`pct = 100*(Δtotal-Δidle)/Δtotal`
- 磁盘 IO：`disk_io_counters(perdisk=True)` 只取 `PhysicalDrive*`；速率=Δbytes/Δt；IOPS=Δ(read_count+write_count)/Δt；Windows 无 busy_time，活跃度=**`(Δread_ms+Δwrite_ms)/Δt_s/10` 夹取 0-100**（读写并行计数可达 200%，任务管理器同样夹取到 100）
- 首帧 delta 缺失：速率/busy 字段返回 None，前端显示 "--"（不显示 0 误导用户）

## ADR-003 路径与落盘（2026-09-05）

- **禁止硬编码盘符**：记录目录 = `LOCALAPPDATA/winhelper/perf_records`，缺省时逐级 fallback `TEMP` → `SystemDrive`（均为环境变量）
- JSONL 增量 append，每行 flush；文件句柄随线程生命周期打开，线程退出/进程关闭均不丢已落盘数据
- 记录行只存分析必需字段（ts/cpu/mem/swap/disks 速率指标），不存卷容量（容量属实时展示，避免文件膨胀）
- 分析器逐行流式读取，**坏行跳过计数、不中断**；相邻样本时间隙 > max(3×interval, 15s) 视为中断，饱和连续段重新起算

## ADR-004 记录任务模型（2026-09-05，参照 disk_cleanup 任务管理器）

- 全局 `_records` 字典 + 后台 daemon 线程；start 立即返回 record_id，前端轮询 status
- **重复开始幂等**：已有 running 记录则复用返回（带 reused 标记与现间隔），不并行双记录
- 采样 sleep 用 `threading.Event.wait()`（取消即时生效）；单样本异常只计数不终止线程
- stop：置 stop 事件 → join(5s) → 同步流式分析（几万样本秒级）→ 返回报告；分析结果同时写 `analysis.json` 到记录目录 + 内存 `_last_report`，供刷新后经 `/api/perf/record-report` 恢复显示

## ADR-005 瓶颈判定与硬件评估口径（2026-09-05）

- 饱和阈值：CPU >85%、内存可用占比 <10%、磁盘 busy >80%（多盘取各盘独立计算）
- 组件压力分 = 饱和时长 / 总记录时长（saturation_ratio）；排序取最高为瓶颈
- 结论分级：任一 ratio ≥ 0.2 或 p95 越过阈值 → `upgrade`（需升级）；0 < ratio < 0.2 → `watch`（需关注）；全 0 → `ok`（硬件足够）
- 内存补充判定：p95 可用 < 1GB → 直接 upgrade
- 建议 CPU/内存/磁盘各一组措辞（磁盘双分支，见 ADR-002）

## ADR-006 验证门禁（继承 2026-09-05 主应用 dup-const 事故教训）

- **esprima 检不出同作用域重复 const/let**，且本机无 node——web/ 变更必须跑真实浏览器 E2E：
  - `tools/e2e_perf.py`：Playwright chromium + `add_init_script` 注入 pywebview 桩（按路由返回带状态的假数据：record-start 后 status 递增、stop 返回报告）
  - standalone 断言：关键函数 typeof=function + 实时数据填充 + 记录开始/停止/报告全流程 + **pageerror=none**
  - 主应用断言：file:// 加载 `../web/index.html` + 桩 → 遍历全部 6 菜单 → 无 pageerror，且 `web/disk.js`、`web/appdata.js`、`web/app.js` 关键函数仍全部 typeof=function
- JS 避免 `?.` 可选链等 esprima 不支持语法
- 后端冒烟：python 直调 handle_perf_* 断言 success=true 且数值合理

## ADR-007 前端轮询生命周期（2026-09-05）

- `initPerfTab` 由主应用 switchTab 守卫调用；轮询回调每 tick 自检 `#tab-perf` 是否仍 active，**非 active 自动 clearInterval**（离开菜单暂停轮询，无需改 app.js 其他逻辑）
- 图表 Chart.js 窗口 60 点（1s 一点），animation=false；主应用全局已有 window.Chart，standalone 用本地 vendor 副本
- perf.js 自带桥接等待与 `perfApi()`，不依赖 app.js 的 apiFetch（standalone 页面无 app.js）

## ADR-008 压测编排与硬约束（2026-09-06）

- **每阶段 ≤60s 硬上限，全面检测编排总时长 58s**：磁盘 25s（写 12s + 读 12s）→ CPU 15s → 内存 10s → GPU 8s（核显自动跳过）。服务端 worker 逐阶段执行，每阶段以 `cancel` Event + 阶段时长双条件退出，无任何超时绕过路径
- **可取消**：cancel 立即停所有负载并释放（CPU 线程 stop、内存 blocks 清空 + gc、WebGL 前端标志、磁盘临时文件 finally 必删）；cancel 后任务标记 cancelled，已完成阶段结果保留
- **同任务不幂等**：已有 running 压测时 start 明确拒绝（压测破坏性强，禁止隐式复用不同 mode），提示先取消或等待完成
- 压测任务独立 `_stress_tasks` 表，异常完整 try/except，返回 success=false + error，**绝不影响主应用其他功能**
- 前端开始前 confirm 弹窗（"压测约 XX 秒，期间系统可能短暂卡顿，请提前保存工作"）——破坏性体验保护

## ADR-009 压测技术选型与判定标准（2026-09-06）

- **CPU 满载 = hashlib.sha256(256KB) 忙循环线程 ×（N-1）**，不用 multiprocessing——PyInstaller onefile 的 desktop.py 入口未调用 freeze_support()，frozen exe 下 multiprocessing spawn 会递归执行 GUI 入口（灾难）；hashlib 对 >2047 字节输入在 C 层释放 GIL，多线程可真满载全核，且无子进程崩溃风险
  - 稳定判定 stable：负载线程零异常 + 采样连续（间隙 ≤3s）+ status 响应间隙（>5s）≤2 次 + 压测后事件日志无新增 WHEA-Logger/Kernel-Power 严重错误；达标口径 = 半数以上采样 >90%
- **磁盘读写上限**：默认系统盘（可选物理盘）；写 = 64MB 块顺序写临时文件（~12s），读 = 顺序回读与随机偏移 seek 交替（~12s，**注明 OS 缓存影响**——临时文件可能部分驻留缓存，实测读速为上限乐观值）；临时文件建在目标盘 tempfile.mkdtemp，finally rmtree 必删，禁止碰用户数据
  - 结论阈值：写 ≥200 且 读 ≥300 MB/s → ok（SSD 级满足日常）；写 <150 MB/s → bad（机械盘水平，建议升级 SSD）；其间 → edge
- **内存渐进压满**：256MB/步分配 + 全页触摸（真实提交），目标系统占用 ≥90%；**红线（不可妥协）**：预判 available−step < max(1.5GB, 总量 8%) 即停，watchdog 线程 0.15s 轮询 available < 800MB 立即全释放；MemoryError 捕获后仍走 finally 释放
  - 稳定判定 stable：维持期间无 MemoryError + status 持续可响应 + 释放后 1s 占用回落 ≥10 个百分点
- **GPU**：WMI Win32_VideoController 命名判定独显（Geforce/GTX/RTX/Quadro/Tesla/Radeon RX·Pro·R9/FirePro/Intel Arc；"Radeon(TM) Graphics"/Vega 无 RX 视为核显；Intel 仅 Arc 算独显），核显 → skipped 不跑负载
  - 负载生成选型：**前端 WebGL 片元着色器满载 canvas**（WebView2 GPU 加速，唯一免依赖可行方案；后端无 D3D 能力、PIL 运算属 CPU 负载）——服务端置 need_webgl 标志，前端轮询到即启动渲染循环，status 非 running 即停
  - 指标：nvidia-smi 优先（util/mem/temp）；非 NVIDIA 用 typeperf GPU Engine 计数器 max 值；均不可用标注"指标不可用"仍跑负载
  - 稳定判定 stable：满载期间事件日志无 nvlddmkm/Display 驱动重置（TDR）+ 指标采样连续
- **事件日志扫描**（压测后）：pywin32 win32evtlog 容错 import（缺失则返回 None 标注未检查），只读 System 日志回溯 N 分钟，过滤 WHEA-Logger/Kernel-Power/nvlddmkm/Display
- **桥接响应延迟**：前端轮询本地 performance.now() 实测并展示（ms），服务端记录 status 调用间隙次数作为卡顿佐证

## ADR-011 温度能力分级与 LibreHardwareMonitorLib（2026-09-06，方案C）

- **权限策略**：exe 保持普通权限（manifest asInvoker 不改）；温度按能力分级显示——GPU 温度 `nvidia-smi` 全员可读（3s 超时，无 NVIDIA → "--"）；CPU 温度需 **IsAdmin + LibreHardwareMonitorLib**，否则占位「需管理员模式」+「以管理员重启」按钮
- **LibreHardwareMonitorLib v0.9.4**（NuGet，lib/net472/LibreHardwareMonitorLib.dll）+ **HidSharp v2.1.0**（lib/netstandard2.0），放 `libs/` 随 exe 打包（winhelper.spec datas）；pythonnet clr.AddReference 加载（先后 HidSharp 再 LHM）；Computer 单例 Open 一次复用（MSR 有开销，轮询 ≥2s），`_lhm_close()` 可释放；任何异常全捕获降级
- **独立接口 handle_perf_temps**（建议 2s 轮询，不并入 1s snapshot——LHM Update 与 nvidia-smi 子进程开销大）；**任何失败返回 success=true + 分项 available 标志，绝不报 error**（前端无脑渲染占位）
- 非管理员实测：AddReference/Open 成功、CPU 无温度读数（MSR 需内核驱动）、GPU 传感器可读但主通道仍用 nvidia-smi；管理员路径（真实核心温度）**待用户点一次「以管理员重启」人工验证 UAC 流程**
- **管理员重启**：`handle_perf_restart_admin` → ShellExecuteW "runas"（打包态 exe 直启 / 开发态 python desktop.py 带参）→ ret>32 成功后 0.3s 延时调 exit hook（desktop.py `window.destroy` 注册进 bridge，避免 bridge→desktop 循环导入；hook 失败兜底 os._exit）；UAC 取消（ret∈{5,1223}）→ 前端 alert「未提权」（无 toast 组件，以 alert 替代）；重启前自动停止进行中的记录任务（flush 落盘）
- **Defender 风险**：LHM 内嵌 WinRing0 内核驱动可能被杀软误报——重启 confirm 弹窗已含说明话术（"该驱动仅用于读取传感器数据"），用户侧如遇拦截需选择允许
- **增强项已做**：长时记录 JSONL 增加 cpu_temp/gpu_temp（取温度轮询缓存，不重复开子进程），分析报告输出温度统计行；压测 CPU 阶段记录温度峰值（cpu_temp_max），GPU 阶段 nvidia-smi 本身含温度

## ADR-013 GUI 无控制台程序子进程弹窗事故与门禁第四道（2026-09-06）

- **事故**：exe 运行中循环弹 cmd 窗口。根因：windowed exe（console=False）里 `subprocess.run` 调用控制台程序（nvidia-smi/powershell/typeperf）每次都生成可见 cmd 窗口；温度 2s 轮询 = 循环弹窗。热修：perf-analyzer 819e806 / 主应用 0db7c48，模块级 `_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)`，5 处调用点（_detect_gpus/_gpu_metrics×2/_ps_cim_dump/_nvidia_temp）全部补 `creationflags=_NO_WINDOW`
- **铁律**：**GUI 无控制台程序中一切子进程必须 CREATE_NO_WINDOW**（有意弹窗的除外，如 explorer 打开位置——白名单按调用源码片段含 "explorer" 豁免）
- **为什么门禁没拦住**：smoke 在控制台跑、E2E 用桩——两种动态验证都看不到弹窗。补**静态检查第四道**：`tools/check_subprocess_window.py`（AST 扫描 subprocess.run/Popen/call/check_output/check_call + from-import 裸名，必须带 creationflags；`--selftest` 自检），已内置进 `tools/e2e_perf.py` main 入口（每次 E2E 先跑，失败退出码 3）
- **发布四连**（web/JS + 服务层变更后全过才可 commit）：esprima 语法初筛 → js_decl_check（重复声明）→ e2e_perf（真实浏览器 84 项断言）→ **check_subprocess_window（子进程窗口）**

## ADR-014 hwinfo 扩展：终端基础信息（2026-09-06，与 log-inspector 界面改造配套）

- **路由不变** `/api/perf/hwinfo`，与硬件规格同一次缓存一次性获取（进程内缓存，新增字段前端旧硬件卡片自然忽略）
- **os**：Win32_OperatingSystem Caption+Version+BuildNumber（文本形如「Windows 11 专业版 10.0.26200」= Caption + " " + Version）；降级 winreg `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion`（ProductName/DisplayVersion/CurrentBuildNumber）
- **hostname**：socket.gethostname()，fallback %COMPUTERNAME%
- **network**：WMI `Win32_NetworkAdapterConfiguration -Filter "IPEnabled=True"` 优先（每网卡 name/mac/ipv4/ipv6/gateway/dns，IPEnabled 过滤后状态即 up）；降级 psutil `net_if_addrs` + `net_if_stats`（AF_INET/AF_INET6/AF_LINK 分流，**网关与 DNS psutil 无 API，显示 "--"**）；状态 up/down（CIM 路径恒 up，psutil 路径按 isup）
- **排除规则**（自行斟酌项入档）：环回（名称含 loopback / lo）排除；**无任何 IP（IPv4+IPv6 均空）的适配器排除**——蓝牙等虚拟适配器自然被过滤，不再单列蓝牙关键词（IPEnabled=True 或有 IP 即保留，避免误杀虚拟网卡上的真实业务地址）
- CIM 管道注意：IPAddress/DefaultIPGateway/DNSServerSearchOrder 均为数组，`-join '/'` 合并后单行输出；IPv4/IPv6 以是否含 `:` 分流
- 项目内调用点盘点（819e806 后）：perf_service.py 全部 5 处均已携带，无 Popen/call/check_output 遗漏；LHM 走 pythonnet 进程内加载（非子进程）不受影响；disk_cleanup 的 explorer Popen 属有意开窗（白名单），不受影响
- 主应用同步：perf_service.py 两仓库已由热修对齐（后续同步以本 ADR 为准，任何新增子进程调用必须落笔即带 creationflags）

## ADR-012 导出报告 HTML 化（2026-09-06，用户需求）

- 两个导出接口（记录分析 / 压测检测）由 Markdown 改为**单文件自包含 HTML**：内联 CSS 深色风格（与主应用一致 #0f1117/#171a23），结论徽章 + 指标表格 + 建议卡片 + 瓶颈排序，可直接浏览器打开/打印
- 文件名：`perf_report_<rid>.html` / `perf_stress_<sid>.html`（写入记录目录）；接口返回 `html_size`（不再返回全文 markdown 字段，避免报文过大）
- 报告头部标题统一「**观枢终端平台｜EyeTerm** · 性能分析报告 / 性能检测报告」（2026-09-06 用户定名，系统正式名称，已入全局记忆）；perf-standalone 测试页 title 同步
- 标准库 `html.escape` 转义所有动态值；`_html_doc` 统一壳（charset/viewport/CSS）；`_stat_row`/markdown 生成器已废弃（保留 _stat_row 以防外部引用，内部无调用）

## ADR-015 hwinfo GPU 规格扩展（2026-09-06，用户反馈：显卡概况只有型号）

- 三级显存取值：**nvidia-smi**（memory.total 精确 MiB，规避 WMI AdapterRAM 32 位上限）→ **注册表 QWORD**（`HKLM\SYSTEM\...\{4d36e968-...}\` 子键 `HardwareInformation.qwMemorySize`，按 DriverDesc 与 WMI Name 双向子串匹配）→ WMI AdapterRAM 兜底（≥4GB 标注「WMI 上限值，可能偏小」）
- **核显共享显存**：注册表匹配到但 qmem=0 时不得提前 return——交由「共享系统内存」兜底标注（首版实机即踩：Intel 条目曾显示 "--"）
- CIM `==GPU==` 段改管道字段 `Name|AdapterRAM|DriverVersion|VideoModeDescription`；分辨率截取前三个纯数字 token，规避色深本地化文本（中文 GBK 乱码）
- 驱动版本：nvidia-smi driver_version → WMI DriverVersion；新增字段缺失显示 "--" 不报错
- 前端 perf.js 显卡区：型号 + 独显/核显徽章 + 「显存 x · 驱动 y · 分辨率 z」规格行
- 实测：RTX 5060 Ti → 8.0 GB（nvidia-smi 可用值）/ 595.95 / 2560 x 1440；Intel(R) Graphics → 共享系统内存 / 32.0.101.8724 / 1680 x 1050
- E2E 桩同步 vram_text/driver/resolution + 「显存 24 GB」断言；教训：**standalone 场景加载本项目 web/perf.js——主应用改动必须回灌项目仓库再跑 E2E**（本次先改主应用即踩旧文件）

## ADR-016 可视化增强：卷容量图 + 记录综合运行状态曲线（2026-09-06，用户需求）

- **实时指标**：卷容量表下方新增 Chart.js 横向条形图（`perfVolsCanvas`，indexAxis:y），阈值着色 <75 青 / 75-90 黄 / >90 红，随 1s 快照增量 update("none")
- **长时记录**：分析器流式收集时序点（ts/cpu/mem_pct/各盘busy最大值）→ 停止后**降采样 ≤240 点**输出 `report.series{t,cpu,mem,disk_max}`；报告卡片（renderPerfReport）新增三线 Chart.js 曲线（CPU/内存/磁盘活跃，0-100% 轴，spanGaps），旧报告无 series 自动跳过
- **HTML 报告**：`_report_html` 内联 **SVG polyline** 三色曲线（不引外部 Chart.js，保持单文件自包含可打印），0/25/50/75/100% 网格 + 秒轴标签
- 实测：6s 真实记录 → series 6 点（首点 CPU None 为 cpu_times priming 预期行为）→ 导出 HTML 含 `<svg>` ✓
- 教训补充：**standalone 页与主应用 index.html 是两份 markup**——canvas 挂载点（如 perfVolsCanvas）必须两处同步，否则桩场景图表断言失败（本次实测踩中）；perf.js/perf_service.py 改完先回灌项目仓库再跑 E2E

## ADR-017 平台接入（uplink，终端侧）（2026-09-06，main 下发 v4 任务）

协议依据 server-platform ADR-015/016（心跳命令通道协议 v1 冻结）与 ADR-019（collect_logs FTP 凭据方案 A）。

- **模块边界**：`uplink.py` 独立于 Web 框架（注册/心跳循环/命令分发/指标上报），网络层纯标准库 urllib；复用 perf_service 的 snapshot/hwinfo（进程内调用，不重复采集）。bridge.py 只加 3 条 `/api/perf/uplink/*` 路由 + `uplink_autostart()` 一行
- **配置安全**：服务器地址 + token 落盘 `%LOCALAPPDATA%/winhelper/uplink_config.json`（0600 语义）；状态接口**永不回显 token**（仅 has_token 布尔）；前端 password 输入框保存后清空；token 禁止入库/入文档/入日志。UPLINK_CONFIG_DIR 环境变量供 E2E 隔离（不碰用户真实配置）
- **心跳循环**：daemon 线程 30s；服务端响应 interval 字段可覆盖（≥5s）；失败指数退避 30→600s cap；stop 用 join(timeout=3) 等旧线程退出防竞态双循环；配置停用时线程自退出（save 保存后 start 重启）
- **命令分发映射表**：`COMMAND_HANDLERS` 装饰器注册（iperf_client/net_probe/collect_logs/ai_context），新增类型只加条目；每命令独立 daemon 线程执行（不阻塞心跳）；按 id 内存幂等集合（服务端单次下发不重发，重启窗口无重复风险）；回执重试 2 次、409 终态不重试
- **iperf3 打包**：iperf.fr 官方 win64（iperf3.exe 3.1.3 + cygwin1.dll）随 exe 打包（spec datas `libs/iperf3/*`）；执行时复制到 `%TEMP%/winhelper_iperf3_<ts>` 独立 staging（并发任务不争用）→ `-J` JSON 解析（TCP sum_received.bits_per_second / UDP sum.jitter_ms+lost_percent）→ finally rmtree 清理
- **net_probe**：`route print -4` 解析默认网关（0.0.0.0/0.0.0.0 行且接口列为 IP 以区分持久路由段，metric 最小者优先，语言无关）；`ping -n 1 -w 2000` 正则解析 time=/时间=/平均=；TCP 探测 socket.create_connection 计时；禁 raw ICMP socket（需管理员）
- **collect_logs**：v1 hook 回执 ok=false error=collect_logs_not_implemented_v1（ADR-019 args.ftp 凭据**用完即弃**，handler 不读取不落盘不回显）；日志引擎由 log-inspector 接管后替换 handler
- **ai_context**：固定 ok=false error=ai_disabled（AI 暂缓，网络未开通）
- **踩坑 1（编码）**：PYTHONUTF8=1 下 subprocess text=True 用 utf-8 解码 GBK 输出（中文系统 ping/route）直接 UnicodeDecodeError 且 reader 线程静默崩——**子进程输出一律 bytes + utf-8/gbk 多编码解码**（_decode_output）
- **踩坑 2（并行写覆盖）**：主应用 bridge.py 被并行 agent（log-inspector 菜单手术）整文件覆盖回旧版，uplink import/路由丢失——**并行协作时对共享文件用小块增量编辑且提交前必复核关键内容仍在**
- **验证**：tools/e2e_uplink.py（mock_etp_server 随机端口+随机 token，44 项：注册 hwinfo/token 鉴权/4 类命令回执/metrics 结构/幂等单回执/负路径/停用）；e2e_perf.py 98 项全绿（standalone 89 + 主应用 3 菜单遍历，含 uplink 卡片保存启用/停用/token 清空断言，子进程窗口检查覆盖 uplink.py）
- **真机冒烟**：tools/uplink_smoke_real.py（token 仅环境变量传入不落盘，terminal_id=DEV-<hostname>）；首跑被服务端白名单 fail-closed 拦截 403（预期），待放行 172.17.90.215 后重试
- **主应用菜单合并适配**：主应用已并菜单（日志诊断/磁盘清理/性能分析 3 个），e2e_perf 主应用场景同步更新 MAIN_TABS 与 typeof 清单（移除已合并的 loadLogs/loadAnalysis/loadKnowledge）

## ADR-018 app_config.json merge 写回（2026-09-09，net-doctor 交付时发现的跨模块 bug，main 派单修复）

- **缺陷**：`_save_app_config` 为整文件覆盖写回，且调用链传入的 cfg 来自 `_load_app_config`（只保留 temperature_interval_sec）——用户在性能分析保存温度间隔时，会把 net-doctor 写入 `app_config.json` 的 `netdoctor.*` 配置节（nodes 节点表/expected_dns，net-doctor ADR-004）及未来任何其它模块键全部剥离。net_service 读端已容错回退内置默认（功能不受损），但用户自定义配置静默丢失。
- **修复**：`_save_app_config` 改为 **merge 原子写**——读现有文件（缺失/损坏容错为 {}，损坏内容无从保留）→ 仅更新 `_PERF_CONFIG_KEYS`（perf 命名空间，现仅 temperature_interval_sec，后续 perf 新配置键必须登记进来）→ 其它模块键原样保留 → 先写 .tmp 再 os.replace；写失败清理 tmp 半成品（原实现失败静默 pass 会残留 tmp）。读端 `_load_app_config` 保持只取 perf 键不变（读端只关心自己命名空间是合理设计）。
- **边界**：不触碰 net_service.py / netdoctor.js（net-doctor 契约文件）；改动仅限本项目 perf_service.py + 同步主应用根目录副本。
- **验证**：临时单测 13/13（LOCALAPPDATA 隔离临时目录）：netdoctor.nodes/expected_dns 与 uplink 键保留、temperature_interval_sec 更新、handle_perf_app_config 全链路二次保存不丢键、缺失/损坏文件容错、无 .tmp 残留、_load_app_config 读回一致；py_compile 通过。纯 Python 后端改动不涉 web/，不触发浏览器 E2E 门禁。

## ADR-019 设置弹窗卡片化重构（2026-09-10，用户实测反馈四点要求）

- **背景**：主应用设置弹窗单列堆叠——文字贴边、说明冗长技术化（出现 app_config.json 等实现细节）、多类配置混杂无区分、保存语义不清。用户要求：①文字清理 ②类型区分 ③每类独立保存 ④排版不贴边。
- **外壳**：`app-settings-body` padding `16px 20px 20px`（≥16px 门禁）；标题「设置 · 中心平台配置」→「设置」；新增 `settings-card` 卡片规格（圆角 10px、1px 边框、内边距 14~16px、卡片间距 12px、`rgba(255,255,255,.015)` 底）+ `settings-card-head/-title/-desc` + `settings-row` + `settings-input`（收敛原 5 处重复内联样式）。样式仍放 index.html 内联 style 块（ADR-001：不碰 style.css）。
- **卡1 中心平台接入**（`settingsCardUplink`）：徽章/服务端地址/Token/保存并启用/立即注册/停用接入/状态行全部归入，**savePerfUplink 等逻辑零改动**；说明清理为一句「接入观枢终端平台服务端后，本终端保持在线并上报运行状态，供平台统一管理与诊断。」
- **卡2 性能分析**（`settingsCardPerf`）：温度采样间隔行归入，保存按钮 `id="perfSaveAppConfigBtn"`（onclick `saveTempsInterval()` 函数名不变）——原本已有独立保存入口，本次仅归位卡片。说明一句化，30~3600 范围留在 UI（必要约束），配置文件路径删除。
- **卡3**：`<div id="ndSettingsHost" class="settings-card"></div>` 空容器留给 net-doctor（其 ndRenderSettings 整体替换 innerHTML，容器类名不丢；内容与文案归 net-doctor-dev 范围）。
- **文字清理口径**：UI 不出现文件名/配置键名/环境变量路径；占位符保留必要示例（127.0.0.1 脱敏口径不变）；「留空不修改」等必要操作提示保留在 placeholder。
- **perf.js 基线回灌**：主应用 web/perf.js 领先项目副本（温度间隔段 perfLoadAppConfig/saveTempsInterval/tempsIntervalMs 未同步）——本次先整文件字节级回灌（MD5 一致），再动 UI；重申 ADR-015 教训「主应用改动必须回灌项目仓库」。
- **E2E（107 项全绿，新增 10 项）**：①卡片 ≥3 ②body padding 各向 ≥16（computed style）③卡样式规格（圆角/边框/内边距）④三保存按钮独立可见（perfUplinkSaveBtn/perfSaveAppConfigBtn/ndSettingsSaveBtn——net-doctor 的按钮由其 ndRenderSettings 渲染）⑤温度输入框回显桩值 300 ⑥静态文案零实现细节（clone body 后**摘除 #ndSettingsHost 子树**再断言——net-doctor 动态渲染文本不在本任务范围，断言必须排除）⑦开关弹窗。配套：STUB_JS 增加 `/api/perf/app-config` 桩（GET 回显+带参保存）。
- **两个并行演进适配**：a) 温度轮询默认 300s 后，standalone 场景 admin 态翻转断言等不到第二次 tick——重启断言后 evaluate 临时调 `tempsIntervalMs=500` 重启轮询（仅 E2E，产品代码不动）；b) net-doctor 并行集成使导航变 5 菜单，「导航共 4 个」断言同步 ==5（遍历范围保持 perf 视角 3 菜单不变，netdoctor 完整遍历归其自家 E2E）。

## ADR-020 uplink HTTPS 同构改造（2026-09-11，HTTPS 专项衔接任务）

- **背景**：net_service.py 已完成传输层 https 支持（0f19675），uplink.py 全部平台调用（register/heartbeat/命令回执/metrics）仍走明文 http——心跳/命令通道是明文面，必须补齐专项才闭环。任务口径：与 net_service **同构复用勿重复造轮子**。
- **改造点（仅 uplink.py，双仓库同步 8eda8394）**：
  - 新增 HTTPS 传输层段：`UPLINK_CA_ENV="NETDOCTOR_CA_PATH"`（与 net_service 共用同一环境变量）+ `_uplink_ca_path()`（env → _MEIPASS/assets/platform_ca.pem → _MEIPASS/platform_ca.pem → 脚本目录 assets，四级候选全 miss 返回空）+ `_builtin_ca_fingerprint()`（PEM→DER→SHA256 小写 hex）+ `_uplink_ssl_context(cfg)`（TLSv1.2+ / CERT_REQUIRED / check_hostname=False IP 自签场景 / 下发指纹非空时强制与内置 CA 比对，mismatch 拒绝）——逐行同构 net_service:171-260。
  - `_post()` 唯一平台调用出口，https:// 分支构造 context（err 非空 fail-closed 返回 `(-1, {"error": err})`），`urlopen(context=ctx)`；http:// 过渡兼容 context=None 直连。**一个函数覆盖全部 4 类平台调用**，心跳行为（30s 周期/指数退避/回执重试语义）零变化。
  - `load_config()` 默认键加 `server_ca_fingerprint: ""`——白名单合并模式下旧配置缺字段兼容、服务端下发的指纹不再被丢弃。指纹由管理面写入配置文件，UI/状态接口不回显。
  - CA 资产共用主应用根 `assets/platform_ca.pem`（spec datas 已有，未重复添加）；**dev 态**（python 直跑）脚本目录 assets 不存在 → env 注入是单测唯一通道。
- **单测 tools/test_uplink_https.py（14/14）**：配置兼容 2 + 指纹计算 4 + fail-closed 3（mismatch/ca_missing/ca_error）+ http 兼容 1 + 真 TLS 4（openssl 自签 CA + SAN IP:127.0.0.1 + http.server HTTPS 线程：全链 200/大写冒号指纹规范化匹配/篡改指纹拒绝/context 构造规格）。openssl 缺失时真 TLS 段 SKIP 不计 FAIL。
- **踩坑**：`ssl.PEM_cert_to_DER_cert` 只做 base64 层解码不验 DER 语义——格式合法的假 PEM 也能算出指纹，内容非法性由 `load_verify_locations` 兜底（指纹一致但 CA 内容非法 → ca_error fail-closed，同样拒绝连接，测试已断言）。「不可解析」用例必须用非 PEM 文本而非畸形 DER。
- **门禁**：py_compile ✓ / 单测 14/14 ✓ / e2e_uplink 43/43（http 零行为变化）✓ / e2e_perf 107/107（含子进程窗口检查，双场景 pageerror=none）✓。真实 https 链路待服务端就绪与 HTTPS 联调批次合批验证、exe 由 main 统一重建。
- **事故与修复（同步覆盖）**：主应用 uplink.py 领先项目仓库（77e1081 资产结构化 schema1：_hwinfo_from_asset/_hwinfo_fallback/_asset_detail + register asset 透传，未回灌项目）——首版整文件 copyfile 同步将其抹掉（主应用 0f17626）。发现于 commit 后核对 diff（+97/-66 超预期）→ 提取 77e1081 差异回灌合并（修复 commit：perf-analyzer 85f50cb / 主应用 3c054f1，合并版 vs 77e1081 除 HTTPS 增量与 BOM 外零删除行）。**教训强化 ADR-015/017：整文件同步前必须先双向 diff（git show 旧提交对比），双仓库任意一侧超前的功能段都要先回灌基线再动刀；同步后 diff 行数超预期即报警。**

## ADR-021 废除原生 confirm/alert · uiConfirm 全局对话框（2026-09-14，第一批）

- **背景**：WebView2 原生弹窗标题强制显示来源「127.0.0.1:<pywebview 内置服务端口> 显示」（端口每次启动变化）且位置贴顶不居中——用户两条硬要求：①弹窗居中于窗体中心 ②不得出现端口/服务类信息。全项目实测 perf.js 18 处（2 confirm + 16 alert）为主，disk.js/appdata.js 共 5 处为第二批（disk-cleaner-dev）。
- **组件**：`uiConfirm({title, message, okText="确定", cancelText="取消", danger=false}) → Promise<boolean>`，实现于主应用 web/app.js（文件头注释标注「全项目唯一合法对话框出口」）——fixed 全屏遮罩 + flex 居中深色卡（z-index 1200，高于设置弹窗 998）；**纯 DOM API + textContent 构建**（message 动态错误文本零 HTML 注入面，\n 经 `white-space:pre-line` 分行）；ESC/遮罩点击（仅遮罩本体，`e.target === mask` 防卡片内误关）= 取消，Enter = 确认，btnOk.focus()；danger=true 确认键红（.ui-confirm-ok-danger）；**cancelText 传空串 = 单按钮提示模式**（替代原生 alert 语义，perf 域封装为 `perfNotify(title, message)` helper）。样式纯追加 style.css 尾部 .ui-confirm-* 块（遵守 ADR-001 共享文件最小冲突原则）；standalone 页（无 app.js）内联同款副本 + 页内样式（.btn-ghost 一并补齐），注释标注实现基准在 app.js。
- **perf.js 18 处全替换**：2 处 confirm → `await uiConfirm(...)`（管理员重启：okText「重启」常规键；开始压测：**保留用户点名语义文案**「压测约 XX 秒，期间系统可能短暂卡顿，请提前保存工作。」+ danger 红键 + okText「开始」）；16 处 alert → `await perfNotify(...)`（错误提示 title 语义化：导出失败/启动失败/取消失败…，成功提示「已保存」）。全部调用点所在函数均为 async（原本就 await perfApi）。
- **E2E 门禁（124/124 全绿）**：①ui_confirm_interact helper：wait `.ui-confirm-card` → `getBoundingClientRect` 中心 == 窗体中心（±2px）→ textContent 断言无 "127.0.0.1"/"localhost"/`:数字` 端口样式 → danger 键类断言 → 点击确定（foot 最后子按钮）——三次交互（压测 full/GPU danger、重启常规）全过；②原生 dialog 计数 0（原「confirm 3 次」断言废弃）；③**源码 grep 门禁 check_no_native_dialogs()**：web/*.js + index.html + standalone 页逐行匹配 `(?<![.\w])(confirm|alert)\s*\(`，注释行与组件文件白名单外零残留——disk.js/appdata.js 为第二批白名单（改造后收缩移除）；④双场景 pageerror=none；⑤两场景关键函数断言加 uiConfirm/perfNotify typeof=function。
- **踩坑**：门禁首跑 FAIL 18 处残留——扫描对象是主应用 web/（WORKSPACE/web）而替换只做了项目仓库副本，perf.js 未同步即跑门禁；**同步与门禁顺序必须为「先同步后跑」**（复用 ADR-015「standalone 加载项目副本」反向教训：门禁扫描主应用路径）。
- **第二批收口（2026-09-14）**：disk-cleaner-dev 完成 disk.js/appdata.js 5 处替换（主应用 b5c3902）后，源码 grep 门禁移除第二批白名单（全域零残留，白名单仅剩组件注释行）；standalone 场景补 ESC 取消（dispatchEvent Escape → Promise resolve false + 遮罩移除）与单按钮模式（cancelText 空 → foot 仅 1 键 → resolve true）两断言，加上既有 dialog 计数断言完整覆盖三组件行为——全量 126/126 全绿。

## ADR-022 record-latest：最近一次记录的证据源路由（2026-09-18，net-doctor AI 诊断 4.1.7 衔接）

- **背景**：net-doctor-dev「AI 诊断性能证据增强」需要回填最近一次性能分析记录与压测记录。原状：`/api/perf/record-report` 无参=内存最近报告（仅 record-stop 后才有）→ 磁盘 analysis_*.json 按 mtime 回退 → 均无则 `{success:true, found:false}`（**重启后磁盘回退仍可取，但从未跑过记录时为 found:false；且只覆盖性能分析记录**）；压测结果纯内存态（`_stress_tasks` 会话内），无列表路由、无结构化持久化，重启即丢——net-doctor 原判断「重启后取不到」对 analysis 不准确、对 stress 完全成立。
- **定案**：新增统一路由 `GET /api/perf/record-latest?kind=analysis|stress`（kind 默认 analysis；非法 kind → `{success:false, error:"kind 仅支持 analysis|stress"}`）。响应形状：`{success:true, found:true, kind, record_id, report}`，found=false 时无 record_id/report；顶层 record_id 为冗余直取字段（analysis=record_id、stress=stress_id），消费端不必钻 report 结构。选择新路由而非给 record-report 加参：语义清晰、不动既有消费端（perf.js 不改）。
- **配套改造（重启可用性对齐）**：`_stress_worker` finally 落盘结构化结果 `stress_<id>.json`（done/cancelled 口径与 stress-export 一致；载荷={stress_id,mode,status,total_seconds,started_at,finished_at,generated_at,result}；持久化失败静默不影响内存返回），与 analysis_<id>.json 对称；record-latest stress 分支=内存最近 finished → 磁盘 stress_*.json mtime 最新（文件名随机哈希字典序≠时间序，沿用 record-report 的 mtime 排序教训）。
- **门禁**：py_compile 双仓 ✓；smoke_stress.py 增量 5 组断言全绿（真实压测 5 项原有 + record-latest 磁盘回退/内存优先/analysis 匹配/非法 kind 拒绝，SMOKE_ALL_PASS）；双仓 perf_service.py MD5 一致（0F29D910…）；前端 web/ 零变更故 E2E 无新增场景，主应用侧 bridge.py 仅 import+路由各一行。
