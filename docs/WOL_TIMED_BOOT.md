# 定时开机（WoL）功能开发细节档案

> 观枢终端平台｜EyeTerm · 功能专项文档 ｜ v1.0 ｜ 2026-09-18 ｜ 维护者：chronicler-dev
> 编年索引：CHRONICLE.md #57（2026-09-18 · WoL 远程自动开机实现）
> 受众：后续接手的开发者 / 运维。目标：读完懂全貌、能维护、能排障。
> 考证约定：git 哈希与文档路径均经实证；运行时数据与实机事件标注「会话记忆（main 亲历）」。

---

## 0. 速览

**一句话**：终端 BIOS 定时开机无法远程写入（固件只读）→ 平台改用「网络唤醒（WoL）」产品链路：服务器/同网段受控终端向目标机广播魔术包，唤醒确认**只认平台心跳 last_seen**。

**唤醒链路**：

```
每日 HH:MM（wol_schedules 到期）
   │
   ├─ A. 服务器直发（仅同网段有效）
   │     wol.py direct_send → /24 定向广播 + 255.255.255.255 兜底，端口 9/7
   │     └─ 240s 观察窗：目标机 last_seen 恢复？→ done
   │
   ├─ B. 同网段中继（跨网段唯一主通道）
   │     elect_relays 选举同网段在线终端 → 命令通道下发 wol_relay
   │     终端本地发魔术包（同 /24 广播）→ 240s 观察窗 → done
   │
   └─ C. give_up：直发+中继均未唤醒 → 结论落档（含中继命令回执摘要）
```

**关键事实**（详见 §5）：
- 跨网段服务器直发**必无效**（三层设备默认禁转 directed broadcast，单播/广播对照实验铁证）；
- 唤醒判定唯一权威判据 = 平台 last_seen，**严禁** ICMP/TCP/ARP 探测（§6 红线）；
- 生产兜底：systemd timer 每日 08:30 直发（跨网段场景无效，处理见 §9）。

---

## 1. 需求起源与目标机

| 项 | 值 | 来源 |
|----|----|------|
| 需求 | M720t 办公终端每日定时自动开机（配合平台远程管控） | 用户点名 |
| 机器 | 联想 M720t，主机名 WIN-13F-xx-3 | 会话记忆 |
| 平台侧标识 | terminal_id 对应资产 172.17.90.18 | 会话记忆 |
| 硬件 | i5-9500 | 会话记忆 |
| 网卡 MAC | `6C4B90CB4AE4`（归一格式：大写 12 hex） | 会话记忆 |

需求初衷为「BIOS RTC 定时开机」的远程写入——探索结果为**固件级不可行**（§2），遂转 WoL 路线（§3）。

---

## 2. 失败路线全记录：BIOS 定时开机远程写入探索（2026-09-17）

> 本节是「为什么最终用 WoL」的完整证据链。BIOS 写入路线已定案放弃，勿再投入。

### 2.1 探索时间线（与客户端版本号绑定）

| 版本 | 提交 | 内容 |
|------|------|------|
| 4.1.1 | 主仓库 `c27528a`（09-17 17:39） | 老一代联想 WMI 接口链：`Lenovo_SetBiosSetting` 写入（plain/bracket 两轮参数格式）+ `Lenovo_SaveBiosSetting` 提交（探测-提交合一）。**实证类不存在**（save_class=false，commit 返回 rv="no-save-class"）——M720t 无保存类，写入不落固件。 |
| 4.1.2 | 主仓库 `3dc7e8b`（09-17 18:22）+ server-platform `5f0ba2f`（18:56，ADR-040 follow-up） | **UPL pc_diag 只读诊断通道**上线（本功能定案的关键基础设施，见 §2.3）；接口登记 SRV-109~112 + UPL-015（`38dc3b4`）。 |
| 4.1.3 | 主仓库 `9b5b5df`（09-17 19:13） | 回退到**新一代**联想 WMI 实例级接口 `Lenovo_BiosSetting.SetBiosSetting` → 实证「找不到方法」（方法面缺失）；attempts 记录自此必带 via/err。 |
| 4.1.4 | 主仓库 `a8d5871`（09-17 22:42） | BIOS 诊断终审包：`wmi_surface` 方法面**穷尽枚举**（把该机型 WMI 命名空间全部方法列出来对照）+ attempts old/new 分离 + 心跳间隔统一 + 死锁修复。 |

### 2.2 定案证据链（三条独立证据 → 固件只读）

1. **排除密码因素**：pc_diag `PasswordState=0`——BIOS 未设管理员密码，不存在「写入被密码挡住」的可能。
2. **读取通道正常**：BIOS 属性读取均正常返回，排除 WMI 命名空间/权限/连接问题。
3. **写入方法面缺失**：两代接口（老一代 `Lenovo_SetBiosSetting`+`Lenovo_SaveBiosSetting`、新一代 `Lenovo_BiosSetting` 实例级 `SetBiosSetting`）在该机型均**方法不存在**；4.1.4 wmi_surface 穷尽枚举确认整个命名空间无可用写入方法。

**结论**：M720t（该世代联想商用机）BIOS 定时开机不具备 WMI 远程写入能力，属固件级能力边界，非软件缺陷。**BIOS Wake on LAN 开关本身也为只读**，只能一次性物理配置（§7）。

### 2.3 关键教训与联调基础设施

- **attempts 必带 via/err（4.1.3 起）**：此前 attempts 只记 rc=0 + rv=null，形成「BIOS 静默忽略写入」的误判假象——rc=0 只代表 WMI 调用本身成功，不代表语义成功。**任何诊断类命令的 attempts 必须记录调用路径（via）与原始错误（err）**。
- **pc_diag 联调通道**（值得复用的基础设施）：终端侧只读诊断命令 handler（SaveBiosSetting 探测 / PasswordState / RTC 读回 / attempts 全量 / 日志尾 / 4.1.4 wmi_surface 穷尽枚举）+ 平台侧 `POST /console/terminals/{tid}/diag`（server-platform `5f0ba2f`）+ `pc_diag_records` 存档。远程固件/系统态诊断走此通道，避免反复拆机与盲目重启。
- pc_diag 属 power-control 域（net-doctor-dev 为同期在途批次的协作者）。

---

## 3. 路线转换决策（2026-09-18）

main 拍板：**产品主路径改为「平台定时 WoL 唤醒」**——不依赖任何 BIOS 写接口，对所有 BIOS 已开启 WoL 的终端通用。同日完成两端交付与定案验证（ADR-044 背景；编年 #57）。

第一段过渡自动化先行：生产 systemd timer 每日 08:30 服务器直发（§4.4）；第二段平台内产品化即本功能（§4）。

---

## 4. 产品实现

### 4.1 终端侧（4.1.5，power-control 域）

**代码位置**：主仓库根 `power_action.py`（power-control 独立仓库 `power-control/power_action.py` 为权威源，主仓库为同步副本）；接入点 `uplink.py`（命令白名单注册）。版本 bump：`702f0c9`（08:38）；重打包发布：`0c5f1e0`（08:46，生产 release id=6）。一包三能力：

| 命令 | args 契约 | 行为 |
|------|-----------|------|
| `power_action` | `{action:"shutdown"\|"restart", delay_sec?:0-3600 缺省 60, force?:bool 缺省 true}` | `shutdown /s\|/r /t N [/f]`；**仅中心可发**（本地 UI 零入口）；执行前弹倒计时知会窗（MessageBoxW 独立线程 TOPMOST，纯知会无确认语义），**不提供本地取消**——撤销走中心 abort |
| `power_action_abort` | `{}` | `shutdown /a`；rc=1116（无 pending）为正常业务态，UI 按「无可撤销」提示非错误 |
| `wol_relay` | `{mac, broadcast, port? 缺省 9}` | 终端本地向同网段广播地址发目标机魔术包；UDP fire-and-forget，回执 ok=true 仅代表已发送 |

**终端侧安全与正确性要点**（排障时先核对这些）：

- MAC 归一（`normalize_mac`）：`AA:BB:CC:DD:EE:FF` / `AA-BB-CC-DD-EE-FF` / `aabbccddeeff` 等分隔形态 → 大写 12 hex，非法拒绝；
- 魔术包（`build_magic_packet`）：`6×0xFF + 16×目标MAC` = 102 字节（AMD 帕洛阿尔托标准）；
- 广播地址校验（`validate_broadcast`）：严格 IPv4 点分四段、每段 0-255（**拒绝** inet_aton 简写形态如 `172.17.90`）；
- socket：`SO_BROADCAST` + 3s 超时，发送即关闭；
- 高危红线（power-control 技术红线）：命令串仅由受控白名单参数构造（`build_power_action_argv` 纯函数）；子进程 `CREATE_NO_WINDOW`（GUI 无控制台程序全局红线）；单测全 mock，**禁真实 shutdown**（ADR-006 附注⑤，power-control/docs/DECISIONS.md）；
- 本地审计：执行前后写引擎日志（日期分文件），与服务端命令回执 cid 双存档。

### 4.2 服务端（server-platform，commit `85ae733` 09-18 09:09，ADR-044/045）

**模块**：`server/wol.py`（调度守护）；**存储**：`server/power_control.py` 追加两表；**装配**：`server/app.py` 守护线程。

**常量**（wol.py 顶部，改行为前先看这里）：

| 常量 | 值 | 含义 |
|------|----|------|
| `TICK_SEC` | 20 | 调度线程节拍 |
| `WAKE_WAIT_SEC` | 240 | 直发/中继后的唤醒观察窗 |
| `DIRECT_PORTS` | (9, 7) | 直发双端口（与 systemd timer 一致） |
| `MAX_BROADCASTS` | 4 | 单次直发广播地址上限（防御异常多网卡） |

**两表 schema**（ADR-044.1）：

- `wol_schedules`：`terminal_id + time_hhmm` UNIQUE；`mac` 可空（自动从资产带出，可手填补录）；`method`（auto|direct|relay）；`run_state` 状态机（见下）；`direct_ts` / `relay_ts` / `relay_cid` / `relay_tid`；`last_run_date`（防当日重触发）；`last_result`；`enabled`。
- `wol_attempts`：`schedule_id` / `phase`（direct|relay|confirm|giveup）/ `relay_terminal_id` / `ok` / `detail`——全链路留痕，排障第一入口。

**调度状态机 `wol_tick`**（每 20s 一拍，单次 tick 快速返回不阻塞）：

| 步骤 | 条件 | 动作 |
|------|------|------|
| ① 唤醒确认 | run_state ∈ (running, relay_sent) 且目标机 `last_seen ≥ base_ts`（direct_ts 或 relay_ts） | 写 confirm attempt → run_state=done，last_result=「目标机已上线（时刻）」 |
| ② 到期触发 | `time_hhmm == 当前 HH:MM` 且当日未跑且非进行中 | `_fire_direct`：MAC 归一（失败→failed/no_mac）→ `target_broadcasts`（候选 IP 的 /24 定向广播去重 + 全网广播兜底，≤4 个）→ `direct_send` 双端口 → run_state=running |
| ③ 直发升级 | running 且 `now - direct_ts ≥ 240s` | `_fire_relay`：`elect_relays` 选举（无中继/MAC 未知→failed 落档）→ 命令通道下发 `wol_relay`（timeout 120s，source=powercontrol）→ run_state=relay_sent |
| ④ 结论落档 | relay_sent 且 `now - relay_ts ≥ 240s` | `_final_giveup`：取中继命令状态与回执摘要写入 attempt（giveup）→ run_state=failed |

**中继选举 `elect_relays`**：候选 IP 口径沿 ADR-043——目标机与候选终端均取「asset.network[] 自报 IPv4 全量 + HTTP 连接源 IP」；候选与目标机任一 IP 同 /24（`derive_broadcast` 相等）即入列；在线口径 = `last_seen` 距今 < `heartbeat_timeout_sec`（缺省 180）；排除目标机自身；按 last_seen 新→旧取首选。

**服务端校验双保险**：服务端先行 `normalize_mac` / `validate_broadcast`，终端 `handle_wol_relay` 收到后再拒一次——两端同构校验，任一端被绕过另一端兜底。

**⚠ 已知实现现状（排障者注意）**：wol.py 模块 docstring 已写明「跨网段/method=relay 跳过直发直接中继」的目标设计，但**当前 `wol_tick` 到期触发一律直发起步**（`method` 字段在调度器内尚未分叉生效）——跨网段目标会先经历 240s 无效直发观察窗再升级中继。跨网段跳过直发的优化在排期中（§9 行动项）。以代码为准，勿按 docstring 预期行为排障。

**console API**（`/console/powercontrol/*` 组，均控制台会话鉴权；写操作 admin-only + 审计）：

| 端点 | 用途 |
|------|------|
| `GET /console/powercontrol/wol/relays?terminal_id=` | 中继选举读：relays 列表 + 目标机 MAC/广播预填 |
| `GET/POST/PUT/DELETE /console/powercontrol/wol/schedules` | 定时唤醒 CRUD（POST/PUT/DELETE admin-only 403+审计；time HH:MM / method 枚举 / mac 归一 / 同名同刻 409） |
| `POST /console/powercontrol/wol/direct` | 服务器直发即时动作（`{terminal_id, mac?, broadcast?, port?}`，admin-only+审计） |
| `POST /console/powercontrol/terminals/{tid}/power-action`（+ `/abort`） | 立即重启/关机（仅在线终端，409 离线拒绝；enqueue timeout 120s） |

**验证门禁**（ADR-044/045，全绿）：`tools/test_wol.py` 7/7 组（纯函数/到期直发/观察窗不升级/中继升级命令形状/确认 done/无中继 failed/giveup 落档/停用不触发）+ E2E 179/179（既有 164 零回归 + 新增 15 项）+ pageerror=0 + esprima。

### 4.3 console UI（pw 页「立即开关机与远程唤醒」卡）

- 行级操作列（admin + 在线才显示）：「重启」「关机」→ uiConfirm danger 确认 → 下发后横幅（命令号 + 撤销按钮 + 120s 观察窗），撤销后 5s×12 轮询 abort 回执，rc=1116 如实展示；
- WoL 定时唤醒表：五态徽章（待触发 / 直发已执行 / 已转中继 / 已上线 / 未唤醒）+ 新建弹窗（终端/名称/时间/方式/MAC 自动带出可手填）+ 启停/删除（uiConfirm danger）；
- 手动唤醒弹窗：目标终端自动带 MAC/广播 + 同网段在线终端代发下拉，服务器直发/中继二选一；
- 最近唤醒尝试表（direct/relay/confirm/giveup 全留痕）；
- `pwTs` 时间格式化（独立函数，不与 perf/其它页共用）。

### 4.4 生产 systemd timer 兜底（过渡自动化）

- `wol-win13f.timer`：每日 08:30 触发；
- `/usr/local/bin/wol_win13f.py`：服务器直发 M720t，三地址三连发（/24 定向广播 172.17.90.255 与全网广播 255.255.255.255 组合、端口 9/7，与 wol.py `DIRECT_PORTS` 同构）。
- **服务器侧脚本无 git 考证（生产运维件，待现场核验）**。journal 实证 09-18 08:30 Succeeded 三地址发包（会话记忆）。
- 跨网段直发无效的定案后，timer 的处置见 §5.3 / §9。

### 4.5 接口登记索引

接口登记官批次（主仓库 `e6e9974` 09-18 09:16，docs/API-REGISTRY.md）：**SRV-113~119**（服务端 power_action / wol schedules CRUD / relays / direct 等）+ **UPL-016~018**（终端 power_action / power_action_abort / wol_relay）。台账总量 226 条（会话记忆）。

---

## 5. 通道实证与修正（2026-09-18~19）

### 5.1 成功案例 ×2（同网段直发）

| 时点 | BIOS WoL 设置 | 过程 | 结果 |
|------|---------------|------|------|
| 09-18 08:07 | Automatic | 同网段发魔术包（经同网段 Jun-office-PC 代发，与 ADR-044 背景「09-18 晨唤醒来自同网段」互证） | 08:09:56 last_seen 恢复，唤醒成功 |
| 09-18 19:43 | **Primary**（用户已手工设置） | 干净终验：平台心跳确认真离线（last_seen 288s 前）→ 19:43:35 同网段三路广播 → 60 秒后 last_seen 恢复（cv=4.1.3） | **唤醒成立，方法学干净** |

（以上均为会话记忆：main 亲历；干净终验流程见 §6。）

### 5.2 跨网段直发无效定案（产品级关键结论）

- **实测一**：09-18 20:58 服务器直发（SRV-116 端点）对 172.17.90.18——端点 ok=true 但 300s 内 last_seen 未恢复（目标机当时确已关机，无效结论成立）。
- **实测二（对照实验铁证）**：服务器同时向同网段在线终端 172.17.90.215:9147 发单播与定向广播——**单播 3/3 秒达、定向广播 0/3 被滤**。三层设备默认禁转 directed broadcast（业界默认安全实践），`255.255.255.255` 从不过网段。
- **产品结论**：**跨网段唤醒唯一主通道 = wol_relay 同网段中继**；服务器直发仅对「与服务器同网段」的目标形态有效。这与 wol.py docstring 的直发适用性判定一致。

### 5.3 行动项（server-platform 排期中）

1. 调度器对跨网段目标**跳过直发观察窗直接中继**（消除 240s 无效等待，见 §4.2 现状注记）；
2. 注册 M720t 的 wol_schedules（每日 08:30），由平台调度承担（当前靠 systemd timer）；
3. systemd timer 停用或标注「跨网段无效」（对 M720t 场景 timer 实际无效，避免误导排障）。

---

## 6. 方法论红线（全平台电源管控通用，写在此处供一切在线/唤醒判定引用）

1. **电源态/唤醒判定唯一权威判据 = 平台 last_seen 心跳**。
2. **严禁** ICMP / TCP / ARP 探测作为判定或辅助判据——防火墙/安全软件使这些探测在**开机状态下也全静默**，探测静默 ≠ 关机（用户实机纠错确立；2026-09-18 白昼唤醒事件中多轮探测判读全部作废）。give_up 文案保留「终端 BIOS 网络唤醒未开启」可能原因提示，且**不得引导用户用 ping 判定**。
3. **干净终验流程**：平台确认 last_seen 消失（真关机）→ 发包（直发或中继）→ last_seen 恢复 = 成功。全程只看 last_seen。
4. 勘误入档：server-platform docs/DECISIONS.md 运维记录「白昼唤醒事件与方法论勘误」（commit `6079c6d` 09-18 19:44）；编年 #57。

---

## 7. 一次性物理配置（BIOS 写只读的补偿，换机/重装系统后需重做核对）

| 配置 | 位置 | 状态 |
|------|------|------|
| BIOS Wake on LAN = **Primary** | M720t BIOS（开机 F1） | 用户已手工设置（BIOS 写入只读，只能现场改） |
| 关闭 Windows 快速启动 | 控制面板 → 电源选项 | 完整性前提（会话记忆：已列清单） |
| 网卡「允许此设备唤醒计算机」 | 设备管理器 → 网卡 → 电源管理 | 完整性前提 |
| 网卡「唤醒魔包」启用 | 网卡属性 → 高级 | 完整性前提 |

---

## 8. 维护与排障手册

**日常维护（加一台机器的定时开机）**：console → power-control 页 → WoL 定时唤醒 → 新建（选终端、时间、方式；MAC 自动带出）→ 启用。前提：目标机完成 §7 物理配置；跨网段目标确保同网段有常开受控终端可做中继。

**排障第一入口**：`wol_attempts` 表 / console「最近唤醒尝试」表。失败形态对照：

| 现象（last_result / detail） | 根因 | 处置 |
|------------------------------|------|------|
| `目标机 MAC 未知或非法，请编辑补录`（attempt: no_mac） | 资产未上报网卡 MAC 且计划未手填 MAC | 补录 MAC 或等资产上报后重试 |
| `直发已执行（无可达广播）` | 目标机无候选 IP（资产与连接源均空）或广播地址全部非法 | 核对资产 network[] 上报 |
| `直发未唤醒，同网段无在线中继终端，本轮结束` | 跨网段目标且同网段无常开受控终端 | 部署常开中继终端（4.1.5+）或把目标机纳入服务器同网段 |
| giveup：`命令状态=… result=…` | 中继终端收令但发送失败/未拉取 | 核对中继终端版本 ≥4.1.5、命令通道连通性 |
| 直发 ok 但跨网段目标从未上线 | 三层设备禁转 directed broadcast（§5.2 定案） | 属预期：等中继升级或等「跳过直发」行动项落地 |
| 状态停在 running 超过 240s 未转 relay_sent | 调度线程异常（查 journal wol tick error）或 `wol_enabled=false` | 检查 app.py 守护线程与配置总开关 |

**版本前提**：中继代发要求中继终端 ≥ 4.1.5；调度器为服务端侧，无终端版本要求（目标机只要 BIOS WoL 已开）。

---

## 9. 当前状态与待办（截至 2026-09-18）

- **已闭环**：M720t 每日 08:30 自动开机（当前由 systemd timer + 人工已验证的唤醒链路承担；20:58 实测后 timer 对跨网段目标无效已定案，处置待 §5.3 行动项）。
- **排期中**：调度器跨网段跳过直发；M720t wol_schedules 注册；timer 停用/标注。
- **4.1.8 批**（与本功能弱关联）：perf_stress 接线已入库；wol give_up 文案补充（§6.2 的探测红线文案落 console 尝试详情）待排期。
- **4.1.7 发布收口**进行中（file-search 索引器部署通道，与本功能无直接耦合但同期）。

---

## 10. 参与者与考证索引

**参与者**：用户（BIOS 物理配置 / 实机验证 / 方法论纠错 / 里程碑定性「里程碑式的一幕，终于实现了自动开机」）；main（统筹 / 路线拍板 / UC-BC 对照实验 / 纠错定案）；server-platform-dev（服务端调度 + 产品化 + 发布收口 + 勘误入档）；power-control-dev（终端 4.1.5 三命令）；api-registrar-dev（SRV-113~119 / UPL-016~018 登记）；net-doctor-dev（pc_diag 联调通道协作者——pc_diag 属 power-control 域，net-doctor 为同期在途批次）。

**git 实证**：

| 提交 | 仓库/位置 | 内容 |
|------|-----------|------|
| `c27528a` / `9b5b5df` / `a8d5871` | 主仓库 | BIOS 探索 4.1.1/4.1.3/4.1.4 |
| `3dc7e8b` + server-platform `5f0ba2f` | 主仓库 + server-platform | pc_diag 联调通道（4.1.2） |
| `702f0c9` / `0c5f1e0` | 主仓库 | 终端 4.1.5（power_action.py + uplink 注册 + 重打包） |
| `85ae733` / `6079c6d` | server-platform | 服务端 WoL 产品化（ADR-044/045）/ 方法论勘误 |
| `e6e9974` | 主仓库 | 接口登记 SRV-113~119 + UPL-016~018（docs/API-REGISTRY.md） |

**文档实证**：server-platform/docs/DECISIONS.md ADR-044（WoL 定时唤醒产品化）/ ADR-045（power_action 立即重启/关机）/ 运维记录×2（shutdown-set 取证、白昼唤醒勘误）；power-control/docs/DECISIONS.md ADR-006 附注⑤；本仓库编年 docs/CHRONICLE.md #57。

**会话记忆来源（main 亲历，2026-09-18 下发）**：M720t 硬件参数与 MAC、release id=6、08:07/19:43/20:58 实测细节、UC-BC 对照实验、BIOS Primary 物理配置、systemd timer 三地址三连发、台账 226 条、§7 完整性配置清单。
