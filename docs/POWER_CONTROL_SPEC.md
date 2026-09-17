# EyeTerm 终端自动开关机管控规格（POWER_CONTROL_SPEC）

版本：v1.0 ｜ 落档：2026-09-16 ｜ 状态：方案已定案，P0 开工
需求来源：用户 2026-09-16 需求书 + 当日方案分析与定案（对话存档）

---

## 1. 用户定案（2026-09-16）

1. **主要对象：ThinkCentre / 扬天商用系列**（联想企业线，具备标准 BIOS WMI 接口）
2. **消费系列（IdeaCentre/拯救者/GeekPro/天逸）并存，两条线路都保留**：商用线自动化，消费线如实标注 + 人工 BIOS + 后续可选方案 B/C
3. **优先方案 A**（BIOS/UEFI RTC Wake，厂商 WMI 读写）
4. 场景：主机电源通电但系统关机（S5 + 有电）——正是方案 A 适用场景

## 2. 已实测事实（2026-09-16 本机 spike，只读）

| 项 | 结果 |
|----|------|
| 开发机 | LENOVO 91AY000LCP，SystemFamily = **IdeaCentre GeekPro-17IAX**（消费线），BIOS O6GKT1CA |
| 企业线 `Lenovo_BiosSetting` 等类 | **消费线不存在**（root\wmi 全量枚举确认） |
| 消费线仅有接口 | `LENOVO_OTHER_METHOD.Get/SetFeatureValue`（非公开、无能力枚举）——**禁止用于产品化** |
| 消费线 BIOS 菜单 | 通常含「RTC 定时开机」（可人工设置，软件不可读写） |
| 企业线预期（待商用机实测） | root\wmi：`Lenovo_BiosSetting`（CurrentSetting 格式 "ItemName,Value"）、`Lenovo_SetBiosSetting`、`Lenovo_GetBiosSelections`、`Lenovo_BiosPasswordSettings`；BIOS 项名预期含 Automatic Power On Control / Wake Upon RTC Alarm / RTC Alarm Day/Hour/Minute 等 |
| 平台资产 | 快照缺 Manufacturer/Model/SystemFamily 字段（P0 顺带补齐） |

## 3. 分线路策略（定案）

- **商用线**（`Lenovo_BiosSetting` 存在，厂商识别 = LENOVO + 类存在性探测为准，不单看型号）：自动读/写 BIOS RTC 自动开机配置；**写前强制快照原值，支持平台一键还原**；写需管理员权限 + 可能 BIOS 密码（密码平台加密下发，AES，不落明文/不写日志/不回显）
- **消费线 / 其它厂商**：如实上报「BIOS 不支持远程配置」；UI 指引人工 BIOS 设置并支持平台登记人工状态；后续可选方案 B（WakeToRun，适用睡眠/休眠）与 C（WoL，立即开机，需同网段代理架构）保留
- **自动关机**（两线路通用，纯 Windows 能力）：schtasks 计划任务 + `shutdown /s /t 60`；关机前 10 分钟中文弹窗（延迟 30 分钟/每天上限 3 次/第 3 次强关）；弹窗可被平台策略按组关闭（无人值守机房）；工作日/指定日期由计划任务触发器支持
- **BIOS RTC 周期策略（2026-09-16 按 M720t 实测修正）**：ThinkCentre M720t 实测 `Wake Up on Alarm` 支持 Single/Daily/Weekly/Disabled/User Defined + 逐日开关（Sunday~Saturday）→ **「工作日开机」优先用 Weekly Event + 周一~五 实现**；「每天开机 + 周末开机后静默回关」仅作不支持逐日周期的低代次 BIOS 兜底；P2 策略模板按机型能力动态选择

## 4. 分期计划

### P0（本次派单，只读，无需提权）
1. power-control 子项目立项（参照 desktop-policy 子项目模式：docs/DECISIONS.md ADR-001 起步 + ARCHITECTURE.md）
2. **电源策略快照采集**（全线路通用，本机可实测）：
   - 机型识别：Manufacturer / Model / SystemFamily（顺带补平台资产缺口）
   - BIOS 自动开机配置：企业线 `Lenovo_BiosSetting` 读取 + 适配性探测（类不存在→「不支持远程配置」）
   - Windows 唤醒定时器：`powercfg /waketimers`（解析活动唤醒任务）
   - 现有关机计划任务：schtasks 枚举含 shutdown/关机动作的任务（名称/触发/状态）
   - 快速启动状态（注册表 HiberbootEnabled + powercfg /a 交叉）
3. **Lenovo 企业线读取适配器**：CurrentSetting "A,B" 解析、RTC 项映射（开关/日期/时分秒）、可选值查询（`Lenovo_GetBiosSelections` 若存在）——本机无企业线类，解析逻辑用 mock 单测覆盖（构造 Lenovo_BiosSetting 返回样例），真机验证留待取得 ThinkCentre/扬天实机
4. 上报：随心跳上报快照（JSON），服务端建表存档 + 控制台可查
5. 主应用「自动开关机」菜单首版：本机电源策略快照卡（BIOS 自动开机状态/唤醒定时器/关机任务/快速启动 + 机型与能力徽章：企业线可配置 / 不支持远程配置（标注人工 BIOS 指引））；UI 遵守 STYLE.md（含 §9 宽度自适应）
6. 全部 subprocess `CREATE_NO_WINDOW`（既有红线）+ 超时控制 + 日期分文件日志；P0 无任何写操作

### P1（2026-09-16 20:45 用户启动：BIOS 写入 + 定时关机任务）
**P1a：BIOS 定时开机写入闭环（企业线）**
- 写入：`Lenovo_SetBiosSetting`（Wake Up on Alarm：Single/Daily/Weekly/User Defined/Disabled + Alarm Time(HH:MM:SS) + Weekly 时逐日开关）——M720t 实测值格式为准（Alarm Time 读取形态为 `[08:00:00]`，写入值格式以实机实测定）
- 安全闭环：**写前原值快照（本地 JSON + 平台双存档）→ 写入 → 回读校验（读回≠目标即 apply_failed 不静默）→ 一键还原**
- 本机配置入口（自动开关机页）：开机时刻/周期（每天/工作日=Weekly+周一~五/指定日期/停用）+ 应用按钮（提权执行）+ 还原按钮
- 提权模式（main 定案的过渡方案）：**单操作提权子进程**（runas 拉起提权 python 执行单次写/任务操作，UAC 确认制，输出回传）——P2 可升级为安装器注册最高权限计划任务/LocalSystem 服务（与 desktop-policy 权限定案合并决策）
- 真机验证（M720t，用户配合）：读原值 → 改时刻 → 回读比对 → 还原 → 比对初始值；通过后用户可实机关机验证次日自动开机
**P1b：定时关机任务（企业线/消费线通用，纯 Windows 能力）**
- schtasks 创建关机任务（任务名 EyeTermAutoShutdown*，动作 shutdown /s /t 60 /d p:0:0），周期 每天/工作日/指定日期 + 时刻，支持 启用/停用/删除
- 本机配置入口同页；提权同 P1a 模式
- 关机前提醒弹窗（10 分钟/延迟 30 分钟/每日 3 次上限/平台可关）→ P2 弹窗引擎
**P1 顺带**：消费线人工状态登记（BIOS 人工设置后平台登记）；心跳随载上报（12h 节流改为随心跳）
- 权限常驻化（计划任务/服务）与 BIOS 密码支持延至 P2 与 desktop-policy 联合定案

### P2（后续派单）
平台设备多选批量下发、策略模板（仅开机/仅关机/组合、按组差异化时间）、执行回执（成功/失败/离线未送达）、覆盖优先级（单机 > 组）、关机弹窗引擎、WoL（含同网段代理架构）

## 5. 技术红线（继承 agent 定义）

- Python 3 标准库 only；Win32 走 ctypes；服务端遵循 ADR-002 零第三方依赖
- 关机/重启命令高危：仅按策略生成；调试禁直呼 shutdown；测试载荷禁真实关机串（ADR-018）
- BIOS 写前强制快照原值（P1 起）；密码内存处理即时清零
- 简体中文 UI、零实现细节、占位禁真实业务 IP（STYLE.md）
- 常驻 <50MB、空闲 CPU <1%；Win10 1809+ 与 Win11 双实测
- 能力分层如实呈现（S5 仅 A/C；WakeToRun 仅 S3/S4；Fast Startup 使关机语义混合），禁止夸大

## 6. 验收标准（全量；按分期逐段验收）

1. 平台勾选 N 台下发「每日 07:30 开机、18:30 关机」，回执全成功，实机次日开/当晚关按预期（P2）
2. 终端本机自定义时间后，平台可见覆盖组策略（单机优先）（P1/P2）
3. BIOS 配置变更可平台一键还原初始值（P1）
4. 关机弹窗「延迟/强制」按策略实测（P2）
5. 全链路日志可追溯（谁/何时/对哪些设备/下发什么/结果）（P2 起全链）
6. **P0 验收**：本机快照采集与上报准确（机型/快速启动/唤醒定时器/关机任务/BIOS 能力徽章），消费机如实标注不支持，企业线解析 mock 单测全绿，服务端存档与控制台可查，主应用菜单可见快照卡，E2E/smoke 门禁全绿、pageerror=0
