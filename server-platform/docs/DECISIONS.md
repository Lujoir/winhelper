# 观枢终端平台服务端 · 决策记录（ADR）

> 项目：观枢终端平台（EyeTerm）服务端
> 用途：重要架构/工程决策的持久记忆。**任何重要决策必须追加于此并 git commit。**
> 格式：ADR-XXX ｜ 状态 ｜ 背景 ｜ 决策 ｜ 理由 ｜ 后果

---

## ADR-001 ｜ 服务端口选定 18090 ｜ 已接受

- **背景**：目标服务器已有 CloudSinoAgent(9990)、Zabbix Agent(10050) 占用；80/443 未开放防火墙。选端口必须先 `ss -tlnp` 排查。
- **决策**：服务监听端口 **18090**（高位空闲段，易记忆、避开常见冲突）。端口值**不硬编码**，存放于部署时注入的 `/data/terminal-platform/config.json`，代码读取配置。
- **约束**：每次部署前必须 `ss -tlnp` 复核 18090 未被占用；变更端口只改配置+防火墙，不改代码。
- **状态**：2026-09-06 预置。首次部署时若 ss 发现冲突，在勘明后改配置并更新本条。

## ADR-002 ｜ 运行时 python39 venv + 纯标准库（零第三方依赖）｜ 已接受（2026-09-10 修订）

- **背景**：服务器默认 Python 3.6.8，python39 模块流可用；服务器外网可达性未知，pip 安装第三方包存在失败风险；平台规模小（辅助管理，非高并发网关）。
- **决策**：服务端仅用 Python 标准库——`http.server.ThreadingHTTPServer`（HTTP 服务）+ `sqlite3`（存储）+ `json/hashlib/secret`（协议/鉴权）。运行时创建 `/data/terminal-platform/venv`（python39），当前 venv 无第三方包，为后续依赖扩展预留。
- **理由**：零依赖 = 部署最稳（不受外网/DNS/镜像影响）、升级最简（SFTP 覆盖即完成）；MVP 并发量（≤ 数百终端 × 分钟级上报）标准库完全够用。
- **后果**：若未来需要 TLS/更高效框架，再评估引入依赖并修订本 ADR；py39 兼容红线：禁用 3.10+ 语法（match、`X | Y` 注解）。
- **修订（2026-09-10，ADR-029 裁定）**：零依赖原则改为**例外清单制**——终端侧 net_service/主应用维持纯标准库不变；**服务端例外清单**：`paramiko==3.5.1`（仅 `deep_engine` 深度检测引擎使用；版本锁定理由与凭据约束见 ADR-029）。新增例外必须以 ADR 记录并经评审。

## ADR-003 ｜ 存储方案 SQLite + 保留策略 ｜ 已接受

- **背景**：无独立数据库服务器；数据形态为时序指标 + 事件 + 瓶颈记录，单机单写。
- **决策**：SQLite 单库 `/data/terminal-platform/data/eyeterm.db`，4 张核心表：
  - `terminals`：终端注册信息（`terminal_type` ∈ windows/linux/**android**（预留）/linux 预留，字段含 `client_version`、`os_info`、`last_seen`）
  - `metrics`：指标快照（ts + cpu/mem/swap 数值列 + `disks_json`/`volumes_json`/`raw_json` 完整快照）
  - `events`：事件/故障上报（level/category/message/detail）
  - `bottlenecks`：瓶颈识别结果（kind/value/threshold/detail，含 `acknowledged` 确认位）
- **保留策略**（config.json 可调，默认值）：`metrics` 30 天、`events` 90 天、`bottlenecks` 90 天；服务内后台线程每日 03:30 清理；启动时也执行一次。
- **理由**：SQLite 单文件易备份（部署脚本直接拷贝 db 即完成备份）；WAL 模式支持读并发。
- **约束**：写入统一经 `store.py` 的锁序列化（check_same_thread=False + threading.Lock）。

## ADR-004 ｜ 终端接入协议 v1（REST /api/v1/*）｜ 已接受

- **背景**：C/S 架构，终端 = Windows 为主（winhelper 客户端），协议层必须预留 Linux/安卓。
- **决策**：
  - 基础：REST，路径前缀 `/api/v1/`，JSON 编码，UTF-8。
  - 端点：`POST /terminals/register`（注册/更新注册信息）、`POST /terminals/{tid}/heartbeat`、`POST /terminals/{tid}/metrics`、`POST /terminals/{tid}/events`。
  - 鉴权：所有终端上行请求带 `X-ETP-Token` 头（token 为部署时生成的随机串，存 config.json）。
  - 指标快照 schema：**对齐 winhelper perf snapshot**——`cpu`、`mem`、`swap`、`disks[]`（含 mount/used/total/percent）、`volumes[]`；每条快照强制携带 `terminal_id`、`terminal_type`（windows/linux/android）、`client_version`、`ts`，实现字段版本化演进（未知字段忽略不报错，向前兼容）。
  - 批量与压缩**预留**：metrics 接口同时接受单对象与数组（自动识别）；`Content-Encoding: gzip` 请求体已在服务端支持，终端端暂不启用。
  - 心跳超时判定：`last_seen` 超过 `heartbeat_timeout`（默认 180s）标记 offline；在线状态为派生值不落库。
- **约束**：接口变更须先向 main 报备再实施（工程约定）。

## ADR-005 ｜ 鉴权体系：终端 token + 控制台独立口令 ｜ 已接受

- **背景**：安全红线——上行 API 用 token 鉴权（终端→服务端），控制台用独立口令；凭据不进仓库，部署时注入。
- **决策**：
  - 终端侧：`X-ETP-Token`（长期 token，部署时随机生成）。
  - 控制台侧：`POST /api/v1/console/login {password}` 换会话 token（内存保存，8 小时过期），后续控制台 API 带 `X-ETP-Console-Token` 头；静态页面本身不含敏感数据可匿名获取。
  - 凭据载体：`/data/terminal-platform/config.json`（0600 权限），首次部署由 deploy.py 生成（secrets.token_hex），后续部署**保留不覆盖**。
- **红线**：config.json / token / 口令 / SSH 密码一律不入仓库；deploy 凭据仅从环境变量读取；对外输出脱敏。

## ADR-006 ｜ 瓶颈识别规则引擎（内置阈值，预留扩展）｜ 已接受

- **背景**：服务端需辅助分析终端性能瓶颈；阈值必须与终端端（winhelper）一致。
- **决策**：v1 内置规则（`analysis.py` 规则表驱动，每条规则 = 名称 + 提取函数 + 比较器 + 阈值 + 说明）：
  - **CPU 饱和**：`cpu.percent > 85`
  - **内存饱和**：内存可用率 `< 10%`（等价 mem used percent > 90，以 available 口径为准，与终端端一致）
  - **磁盘饱和**：任一磁盘空间 `percent > 80`；若快照磁盘条目含 `busy_percent` 字段则同时参与判定（> 80），为终端端后续提供 busy 指标预留
  - **SWAP 异常**：swap 使用率 > 50% 记 warning 级事件
- **去重**：同终端同规则 10 分钟窗口内不重复记录 bottleneck（防上报风暴）。
- **扩展**：规则为数据表结构，新增规则只需追加条目；未来可升级为阈值可配置（config.json 注入）。

## ADR-007 ｜ 部署拓扑：systemd + SFTP + 防火墙持久化 ｜ 已接受

- **背景**：服务器为 systemd 系（Anolis OS 8.10）；历史事故教训（INC-20260617-001）：防火墙只改运行时规则导致故障升级。
- **决策**：
  - 服务单元 `terminal-platform.service`：`Restart=always`、`RestartSec=3`、`WantedBy=multi-user.target`（开机自启）。
  - 部署脚本 `deploy/deploy.py`（本机运行）：环境变量取 SSH 凭据 → SFTP 上传 `server/`、`console/` 至 `/data/terminal-platform/app/` → **远端原文件先备份（带时间戳目录）** → 生成/保留 config.json → systemd 安装启用 → `firewall-cmd --permanent --add-port=18090/tcp && firewall-cmd --reload`（firewalld inactive 则走 iptables 并 `iptables-save` 持久化）→ 健康检查（systemd active / ss 监听 / API 200 / 控制台 200 / `firewall-cmd --list-all` 永久规则复核）。
- **约束**：本地仓库与远端部署状态分离；凭据零落盘。

## ADR-008 ｜ 控制台与导出报告规范 ｜ 已接受

- **背景**：全局规范——**导出报告一律 HTML**；控制台风格与终端端一致。
- **决策**：
  - 控制台：纯静态单页 `console/index.html` + fetch，零外部依赖（无 CDN/无框架）；手写 canvas 折线图（CPU/内存/磁盘曲线）；深色主题，标题统一「观枢终端平台｜EyeTerm」。
  - 页面结构：终端列表（在线状态/最近心跳/版本）→ 终端详情（指标卡片 + 曲线 + 事件流 + 瓶颈记录）。
  - 导出：`GET /api/v1/console/report/{terminal_id}` 服务端渲染**单文件自包含 HTML 报告**（内联 CSS、深色风、含指标摘要/曲线/瓶颈/事件，文件名 `EyeTerm_Report_{terminal_id}_{date}.html`）。
- **理由**：零依赖与 ADR-002 一致；HTML 报告符合全局规范（弃 Markdown/CSV）。

## ADR-009 ｜ Phase 1 勘察受阻记录 → 新机基线回填 ｜ 已完成

- **背景**：2026-09-06 首次勘察时 172.17.5.215 不可达（双源 ICMP/TCP 全失败，判定主机离线）。当日用户**重装系统**后恢复，凭据更换（会话内提供，零落盘）。
- **新机实测基线（2026-09-06 11:51，重装后）**：
  - 主机名 `zljtest5.215`；Anolis OS 8.10 / 内核 5.10.134-18.an8.x86_64；4 vCPU / 7.6 GiB / swap 7.9G
  - 磁盘：sda 100G LVM（VG `ao`：root 61.2G / swap 7.9G / home 29.9G），**VFree=0** → /data 以 root 分支普通目录落地（/data/terminal-platform/），无法从 LVM 划分
  - 端口：21232 sshd、10050 zabbix、**9990+9991**（java/CloudSinoAgent 占两端口）；18090 空闲
  - Python：重装后为最小安装，`python`/`python3`/`python39` 命令均无；python39 模块流可用，`dnf module enable -y python39 && dnf install -y python39` 后提供二进制 **`python3.9`**（无 `python39` 命令，注意）；实测 Python 3.9.25
  - 防火墙：firewalld active / public zone / ens32，初始放行 21232/9990/10050；18090 已由部署脚本 `--permanent` 放行并复核
- **状态**：勘察完成，本条作为平台记忆回填来源。

## ADR-011 ｜ 首次部署实录与教训 ｜ 已接受

- **部署实录（2026-09-06 11:56 部署成功）**：服务 `terminal-platform`（systemd enabled + Restart=always），监听 0.0.0.0:18090，`/data/terminal-platform/{app,data,venv,backups}`，config 注入 `/data/terminal-platform/config.json`（0600，eyeterm 属主）。systemd 专用系统用户 `eyeterm`（nologin）。备份：`/data/terminal-platform/backups/pre_20260906_115625`（增量部署自动备份）。
- **教训 1（ExecStart 路径）**：unit 内 ExecStart/WorkingDirectory 必须与实际上传布局一致（app/server/app.py 而非 app/app.py），否则 crash-loop 且"上传成功≠服务可用"。
- **教训 2（服务启动检查）**：部署脚本 `sleep 1` 后查 is-active 会撞上 RestartSec=3 的 crash-loop（状态 activating），必须轮询等待并在失败时自动拉 journalctl。
- **教训 3（防火墙复核）**：`firewall-cmd --list-all` 的 ports 行是多端口并列（`ports: 21232/tcp 9990/tcp ...`），复核匹配"端口子串"而非"行前缀"；且应同时复核 `--permanent --list-ports`（runtime 与持久配置双重验证，INC-20260617-001 教训的落地）。
- **教训 4（端口勘察）**：增量部署时端口勘察须豁免**自身服务**的占用（is-active 时视为合法，restart 即可），否则把自己当冲突误判。
- **教训 5（Anolis python39）**：包内二进制名是 `python3.9`，`python39` 命令不存在；模块流启用后还须 `dnf install`（最小安装默认不含）。
- **验证**：远端 API 冒烟 17/17 + Playwright E2E 12/12 全绿（含瓶颈触发/去重/HTML 报告/canvas 渲染/ack 交互/零 pageerror）。

## ADR-010 ｜ 控制台必须过无头浏览器 E2E（教训）｜ 已接受

- **背景**：2026-09-06 本地联调中，冒烟（纯 API）17 项全绿，但 Playwright E2E 抓到两个真实 UI 缺陷：①`curveCard`/`diskCard` 初始 `display:none` 且无代码恢复显示（卡片永远隐藏）；②`selectTerminal` 漏调 `refreshCurve()`，曲线请求从未发出、canvas 保持默认 300×150（**页面无任何 JS 报错，纯静默失败**，与 DNS 项目 disk.js 事故模式一致）。
- **决策**：控制台任何 JS/HTML 变更后，门禁 = `tools/e2e_console.py`（Playwright：登录→列表→详情→指标卡→磁盘卡→canvas 像素断言→瓶颈/事件表→交互→pageerror=0）必须全绿，再 commit。纯 API 冒烟不能替代 UI E2E。
- **工具**：`tools/e2e_console.py`（canvas 用 getImageData 断言有像素；请求用 response 监听确认 API 真实发出）。

## ADR-012 ｜ 敏感配置加密方案：机器密钥 + 纯标准库对称加密 ｜ 已接受

- **背景**：算力平台 API key、FTP 密码等敏感配置需运行时可改（控制台/CLI），且静态存储必须不可见明文（红线：凭据零明文落仓库/DB/备份）。py39 标准库无 AES，引入 cryptography 存在内网 pip 源不可达风险（ADR-002 零依赖原则）。
- **决策**（`server/secretsbox.py` + `server/settings.py`）：
  - 密钥：机器密钥文件 `<data_dir>/keys/machine.key`（32 字节随机，hex 存储，0600，首次访问自动生成）。
  - 加密：encrypt-then-MAC——HMAC-SHA256 CTR 流密码（keystream = HMAC(key, nonce‖counter)）逐字节异或；tag = HMAC(mac_key, nonce‖ciphertext)，mac_key=HMAC(key,"mac")；编码 b64(nonce‖ct‖tag)。
  - 存储：SQLite `settings` 表（key/value/masked/updated_at），敏感键（`llm.api_key`、`ftp.password`）value 为密文；非敏感键明文。
  - 输出：API/控制台一律脱敏（前3+****+后4），接口永不回显明文；明文仅解密后服务端内存使用（AI 调用/FTP 认证）。
- **威胁模型（明确声明）**：防御目标是静态配置泄露（仓库/DB/备份/日志中出现明文凭据）；持有 root 或机器密钥文件的攻击者等同于可解密，不在防御范围（与磁盘加密同理）。自制 CTR+HMAC 在该威胁模型下安全性足够，且换来零依赖部署确定性。
- **运维入口**：`server/config_cli.py`（远端 venv python 执行 list/get/set，set 支持 --stdin 防止 ps 泄露）。

## ADR-013 ｜ 终端白名单准入：fail-closed + 存量豁免 ｜ 已接受

- **背景**：终端 API（注册/心跳/上报/事件/登记）需 IP/CIDR 白名单强制准入，被拒需审计。
- **决策**：
  - **空名单 = fail-closed**：拒绝所有**新注册**。理由：安全默认原则——白名单功能的本意是"未明确允许即禁止"；若空=全开，则攻击者清空白名单即可自由接入，功能形同虚设。
  - **已注册终端豁免**：心跳/指标/事件/登记请求只要 terminal_id 已注册即放行（不做源 IP 复核）。理由：避免白名单启用/误操作瞬间踢掉全部存量终端造成业务中断；注册时的首次信任 + token 持有已构成准入。未注册终端则必须命中白名单。
  - 匹配：`ipaddress` 标准 IP/CIDR（含单 IP /32）；条目可停用/启用；非法 CIDR 管理接口拒绝。
  - 审计：所有 403 拒绝写入 `audit_log`（ts/ip/path/action/reason），控制台配置页可查。
  - 已知边界：已注册终端源 IP 变化（换网段/DHCP）仍放行——由终端 token 与 terminal_id 承担身份，属 MVP 接受项。

## ADR-014 ｜ 日志存储：vsftpd + SMB 挂载目录 + 扫描登记 ｜ 已接受

- **背景**：终端上传日志需要 FTP 服务端；存储目录要求可指向 SMB 挂载。
- **决策**：
  - **FTP 服务端选型 vsftpd**（系统包）：①dnf 内网源已实测可达，零 python 依赖（符合 ADR-002）；②成熟稳定，被动端口段原生可控；③自写 FTP 协议服务端工作量大且不可靠，pyftpdlib 是第三方包（违反零依赖）。备选纯 python 方案否决理由如上。
  - 端口规划（与 iperf 段 18200-18299 分沟）：**FTP 控制 18121/tcp + 被动段 18122-18141/tcp**，全部 firewalld `--permanent` 放行（deploy.py 自动化）。
  - 专用用户 `eyetermftp`（home=存储目录，chroot+写权限）；**安全收敛**：shell 为 /bin/bash（vsftpd PAM shells 要求）但 sshd_config 追加 `DenyUsers eyetermftp`，杜绝 FTP 密码复用为 SSH 登录面；密码随机生成、settings 加密存储、仅部署输出一次。
  - **SMB 挂载**：`smb.mount_cmd` 配置化（settings），控制台显示挂载状态（findmnt）+ 管理员手动"执行挂载"按钮；不在部署流程自动挂载（避免部署期间依赖外部存储可用性）。
  - **上传登记**：`upload_files` 表；双来源——终端上传后 HTTP 上报登记（POST /api/v1/terminals/{tid}/uploads，source=api）+ 服务端目录扫描补登记（storage.scan_uploads，source=scan，按 filename+size 去重）；控制台"扫描登记"按钮 + storage status 展示 FTP/挂载生效状态。

## ADR-015 ｜ 心跳命令通道协议 v1（冻结稿，终端侧联调基础）｜ 已冻结

- **背景**：终端需被动接收平台任务（iperf 客户端执行/日志收集/AI 上下文拉取/网络探测）。终端无公网 IP、平台不可反连终端，采用**心跳捎带（piggyback）**模式。
- **协议 v1（冻结）**：
  1. **下发**：`POST /api/v1/terminals/{tid}/heartbeat` 响应体新增 `commands` 数组：`[{id, command, args, timeout_sec}]`；终端按 `id` 幂等执行；**单次下发语义**——响应即置 `sent`，同一命令不会再次下发。
  2. **回执**：`POST /api/v1/terminals/{tid}/commands/{cid}/result`，body `{"ok": true|false, "data": {...}}`（失败时 `"data": {"error": "..."}`）；仅 `sent` 状态可回执（409=重复回执/未知命令）。
  3. **状态机**：`pending`（创建）→ `sent`（心跳下发时）→ `executed`（回执 ok）/ `failed`（回执 ok=false）｜ `timeout`（sent 后超 timeout_sec 未回执；pending 超 timeout_sec+300 未领取，均惰性判定于心跳时）。
  4. **命令类型白名单**（服务端只下发类型化命令，**不透传任意命令行**——安全边界）：`iperf_client`（args: server_ip/server_port/duration_sec/mode tcp|udp/reverse）、`collect_logs`（args: level/minutes/keyword）、`ai_context`（args: issue_description）、`net_probe`（args: targets[{host, method: ping|tcp, port}]）。未知类型终端应回执 ok=false；服务端管理接口对未知类型直接 400。
  5. **扩展**：新增类型只扩白名单与终端处理器；协议字段版本化（未知 args 字段终端忽略）。
  6. **丢包/重试**：MVP 不做自动重发（下发即 sent，超时即 timeout）；需要重执行由管理端重新创建命令。
- **资产字段**：terminals 表扩展 cpu_model/cpu_cores/mem_total_mb/disk_total_gb/gpu_info/os_arch/hwinfo_json（对齐 winhelper hwinfo，注册接口 `hwinfo` 对象可选携带，未知字段进 hwinfo_json；ALTER 幂等迁移，存量库自动补列）。
- **终端侧对接要求（perf/log agent 实现清单）**：心跳解析 commands → 按 command 类型分发处理器 → 回执 POST result；命令执行不得阻塞心跳循环（超时由 timeout_sec 约束，终端侧自行取消长任务）。

## ADR-016 ｜ iperf3 打流服务：独立任务槽 + 临时端口 + 单会话服务端 ｜ 已接受

- **背景**：平台需按需对指定终端发起网络测试（带宽/UDP 抖动/时延），iperf3 服务端不能常驻单端口（并发任务互相踩踏、会话残留）。
- **决策**（`server/iperf.py` + `iperf_tasks` 表）：
  - **每次任务独立任务槽 + 临时端口**：launch 时从配置段（18200-18299，settings 可调）分配空闲端口（bind 试探 + running 任务占口查重）；`iperf3 -s -1 -p <port>` **单会话模式**——接受一次测试后自动退出，天然无残留进程；超时（duration+45s）强杀兜底。
  - **测试项与命令映射**（经 ADR-015 命令通道下发）：`bandwidth_tcp`→iperf_client(mode=tcp)；`udp_jitter`→iperf_client(mode=udp)；`latency_gateway`→net_probe(targets=[{host:"_gateway",method:"ping"}])，**约定 "_gateway" 由终端解析为其本地默认网关**；`latency_server`→net_probe(ping 平台地址，settings `iperf.server_ip` 部署时注入)。
  - **结果链路**：终端客户端执行后回执命令 result（data 携带 task_id + iperf JSON/摘要）→ 服务端回执钩子关联 iperf_tasks 置 done；服务端进程 stdout 独立捕获存 log_text。
  - **iperf3 客户端分发方案**：首选 winhelper 安装包内置 iperf3.exe（离线可用、版本受控）；后备：预留平台 HTTP 下载端点（token 鉴权）。版本以服务端 dnf 安装版本为准（3.x 兼容）。
  - 端口段 18200-18299 firewalld `--permanent` 放行（deploy 自动化）；与 FTP 段 18121-18141 分沟。
  - 测试支撑：`ETP_IPERF_FAKE=1` 环境变量切换为 sleep 进程模拟服务端（本地冒烟无 iperf3 时验证任务生命周期/并发端口/回执联动）。
- **控制台**：「网络测试」页发起（终端+类型+时长）/任务列表（状态/端口/结果摘要）/HTML 报告导出（单文件自包含，最近 N 任务）。

## ADR-017 ｜ AI 智能分析：算力平台调用与隐私边界 ｜ 已接受

- **调用**（`server/ai.py`）：算力平台 OpenAI 兼容 `/v1/chat/completions`；url/model 存 settings（明文），api_key 加密存储（ADR-012），调用时解密到内存；标准库 urllib；超时 60s + 1 次重试（4xx 除 429 不重试）；temperature 0.3。
- **上下文聚合**（build_context）：终端资产摘要 + 近 6h 指标统计（CPU/内存/SWAP 均值峰值）+ 最近磁盘快照 + 瓶颈记录（≤15 条）+ 事件（≤15 条）+ 上传文件名列表；**总长截断 6000 字符**。
- **隐私边界（红线）**：发送给 LLM 的内容**仅限运维诊断数据**（上列聚合字段）；**不发送**——用户个人文件内容、凭据/token/口令、config.json 任何字段、终端任意文件正文；系统提示词中显式声明该边界。
- **知识注入**：服务端内置 FAULT_PATTERNS 常量（CPU/内存/磁盘/swap/IO/掉线/崩溃 7 类常见终端故障模式），随 system prompt 注入。
- **触发**：终端侧 `POST /api/v1/ai/analyze`（X-ETP-Token，body: terminal_id + issue_description）或控制台代发起；结果落 `ai_analyses` 表（含上下文快照/响应/状态/耗时）；控制台「AI 分析」页展示历史与详情 + 单条 HTML 报告导出。
- **测试纪律**：冒烟全程使用本地 mock OpenAI 服务（固定响应，端口随机），**不消耗真实额度**；部署后仅一次真实连通性验证（极短 prompt）。
- **已知限制**：LLM 输出不做结构化解析（原文存展示）；上下文窗口按 6000 字符控制，超长历史依赖外部化（暂不做摘要压缩）。

## ADR-018 ｜ 测试载荷去攻击化 + 技术栈指纹弱化 ｜ 已接受

- **事故背景**：2026-09-06 深信服告警 E26090605685（通用系统命令注入，12:57 检出）——实为 smoke.py 负向用例使用了真实攻击样式字符串（删除类命令）测"未知命令类型 400"路径，IDS 特征命中（源=开发机，目的=平台端口）。**服务端行为正确**（类型白名单 400 拒绝），无实际风险，但产生无效告警噪音。
- **测试载荷去攻击化规范（红线）**：
  1. 一切负向/异常/安全测试**禁止使用真实攻击样式字符串**——删除类（r*m -rf / d*l /f 样式）、关机类（sh*tdown）、反弹 shell（n* -e / b*sh -i）、路径穿越（..\/..\/）、SQL 注入（' OR 1=1 / DR*P TABLE）、XSS（<*script>）等 IDS 特征库命中的样式一律不用；
  2. 改用**语义等价的良性标记串**（如 `unknown_type_probe_x`、`bad_type_for_test`）——测试要验证的是"服务端对非法输入的拒绝行为"，不是载荷本身的攻击性；
  3. **东西向流量同样过 IDS**：内网测试机→内网服务的流量与公网流量同等监控，不要假设"内网测试不会被告警"；
  4. 该规范适用于 smoke/E2E/联调脚本/终端侧测试代码的一切请求体与 URL；
  5. 注释与文档同样避免出现完整攻击样式串（防复制误用与文件扫描误报）。
- **技术栈指纹弱化**：Server 响应头固定 `EyeTerm`（覆盖 version_string()），不再输出应用版本号与 Python 版本（EyeTermServer/x.y + Python/3.9.25 会为攻击者提供版本定向信息）。
- **处置记录**：热修 ac01cc9（载荷改良性探测串）；响应头弱化随本次 commit；全项目载荷审计通过（仅注释一处残留已同步脱敏）。

## ADR-019 ｜ FTP 凭据下发方案：命令通道按任务携带（方案 A）｜ 已接受

- **背景**：终端执行 collect_logs 需 FTP 凭据上传日志。方案 A=命令下发时 args 携带凭据（终端不存长期密钥）；方案 B=终端配置长期 FTP 账号。
- **决策：方案 A**。理由：①终端侧凭据面最小化（磁盘/配置文件零 FTP 密钥，凭据只存在于任务执行窗口的内存中）；②凭据泄露影响域=单任务（窃取者拿不到长期凭证）；③服务端可随时轮换（settings ftp.password 加密存储，轮换无需终端侧配合——下次任务自动带新凭据）；④B 方案在终端侧明文/半明文落盘，违反凭据最小化原则。
- **实现机制**：服务端下发 collect_logs 命令时，从 settings 解密当前 `ftp.password`（ADR-012），连同 host/port/username 注入 args；服务端可随时 `config_cli set ftp.password --stdin` 轮换（vsftpd chpasswd + settings 同步更新），终端下次任务自动使用新凭据。
- **collect_logs 命令 args 结构 v1（终端侧实现依据）**：
```
{
  "task_id": "LG-xxxxxxxx",
  "days": 3,                        // 采集最近 N 天系统日志（默认 3）
  "format": "txt",                  // 输出格式，utf-8-sig（全局规范对齐）
  "level_filter": "",               // 可选：级别过滤（如 error）
  "keyword": "",                    // 可选：关键字过滤
  "filename_hint": "syslog_<terminal_id>_<ts>.txt",
  "ftp": {
    "host": "<server_ip>",          // settings iperf.server_ip 同源
    "port": 18121,
    "username": "eyetermftp",
    "password": "<服务端解密注入>",
    "remote_dir": "/"               // 相对 vsftpd chroot 根（storage 目录）
  }
}
```
- **终端回执约定**：`{"ok": bool, "data": {"task_id": ..., "filename": ..., "size": ..., "sha256": ..., "lines": N}}`；失败时 data.error 说明（收集失败/上传失败分开表述）。上传后终端须再 POST uploads 登记（或依赖服务端扫描补登记，双通道兜底见 ADR-014）。
- **限制声明**：①FTP 协议本身明文（vsftpd 未启用 TLS），凭据与日志内容内网明文传输——内网环境 MVP 接受，后续升级 FTPS；②命令通道（HTTP）明文携带密码，同上；③密码全局单一（vsftpd 单账号），并发 collect_logs 任务共享当前密码——任务窗口短且密码可轮换，可接受。

## ADR-020 ｜ LLM 模型链（主备降级）与冒烟配置隔离 ｜ 已接受

- **背景**：①用户要求主模型响应超时自动切换备选模型；②冒烟套件 mock 测试曾覆盖生产 `llm.url`/`llm.model`/`llm.api_key` 且仅恢复 url——2026-09-08 事故：生产 AI 分析先 401（key 被随机测试 key 覆盖）后 503 model_not_found（model 被覆盖为 mock-model）。
- **决策**：
  1. **模型链**：ai.py 新增 `llm_chat_chain(url, api_key, models[], messages)`；`llm_chat` 失败返回携带 `can_fallback` 标记（超时/连接失败/5xx/429/模型不可用 → True；4xx 鉴权参数类 → False）；链式循环仅在 can_fallback 时切换下一模型，结果含 `tried_models` 实际尝试链。
  2. **配置**：settings `llm.model`（主）+ 新增 `llm.model_fallback`（备）。当前：主 **Qwen3.6** / 备 **minimax**；换模型/优先级只改 settings。
  3. **冒烟隔离**：mock 测试不再触碰 `llm.api_key`（mock LLM 不校验鉴权）；测试后恢复 `llm.url` + `llm.model` 原状（orig 值从 GET settings 捕获，api_key 为 masked 不可恢复故不触碰）。
- **验证**（2026-09-08 生产实测）：正常链 Qwen3.6 → ok（analysis_id=6，1124 字符）；降级链主模型置无效值 → 自动切换 minimax → ok（analysis_id=7，1126 字符，tried_models 含两模型）。
- **后果**：新增 settings 键 `llm.model_fallback`；分析记录 model 字段记录实际使用模型；smoke 重跑不再破坏生产 AI 配置（本次事故已恢复：llm.api_key/llm.model/llm.url 均为真实值）。
- **日志采集范围 v1**：最近 N 天（默认 3）系统日志 txt 全量导出（对齐终端端 log 引擎既有能力）；uplink 实现时留 hook（collect_logs 处理器接口化），后续由 log-inspector 子系统接管增强（级别/来源/关键字过滤的精确化）。

## ADR-021 ｜ 控制台「系统管理」模块：admin-only 权限模型 + 账户/网关/第三方/Token 四功能 ｜ 已接受

- **背景**：控制台需要系统管理能力（控制台账户管理、算力网关配置与连通性测试、第三方接口登记、终端接入 token 多实例维护）；此前控制台单账号单口令、终端 token 仅 config.json 单值。
- **决策**（2026-09-09 实施并部署 172.17.5.215）：
  1. **权限模型**：`/api/v1/console/sysadmin/*` 整组 **admin-only**——`auth_upgrade.require_admin(sess)` 回库实时复核 role+status（不信任会话建立时快照，授权动态生效）；登录响应与 `GET /console/session-info` 携带 `role`，前端 operator 隐藏「系统管理」导航、apiFetch 对 403 统一提示无权限。
  2. **admin 角色迁移**：历史库 `console_users.role` 默认 'operator'（权限模型启用前无意义）——启动时 `auth.ensure_admin_role()` 幂等迁移：整库无 admin 时把名为 admin 的账号提升为 admin，避免上线即锁死存量管理员（生产实测：admin operator→admin 生效）。
  3. **账户管理**（console_auth.db，等保三级延续）：create_user（复杂度走 check_password_policy、初始口令强制改密）/update_user（防自降级/防自禁用 409、防降级或停用最后一个 active admin 409、disabled 即时吊销全部会话）/delete_user（防自删 409、防删最后一个 admin 409、先吊销再删，sessions/password_history 随 FK CASCADE）/admin_reset_password 复用（force_change=True）/list_users（password 恒 '****'）。
  4. **会话状态复核加固**：`resolve_session` 新增账号 status 实时复核（account_inactive 即吊销返回 None）——停用/删除账号的存量会话立即失效，双保险于「停用瞬间吊销」。
  5. **算力网关**：GET/POST `/sysadmin/llm` 读写 settings 现有键（llm.url/model/model_fallback 明文、api_key 加密、POST 时 key 留空=保持不变）；POST `/sysadmin/llm/test` 服务端 GET `{url}/v1/models`（/v1 前缀自适应）Bearer 鉴权 8s 超时 → {ok,status_code,latency_ms,error}，未配置返回 not_configured。控制台「算力平台」卡片自配置页迁至系统管理页并新增「测试连通性」。
  6. **第三方接口**：新表 `third_party_apis`（name/base_url/method/params_json/headers_json/enabled/note）；CRUD+toggle；params_json/headers_json 写入前 json.loads 校验（非法 400）。
  7. **终端 Token 多实例**：新表 `terminal_tokens`（token 唯一索引/label/status/last_used_ts）；Store 初始化把 config.json terminal_token 幂等迁移入表（label='default'）；上行鉴权收敛为 `ApiContext.check_terminal_token()`——config token 或表内 active token 命中均放行，表命中节流更新 last_used_ts（60s）；生成/轮换/停用/启用即时生效，关键操作（建户/删户/重置/启停 token/轮换）写 console_audit_log。
  8. **TBC-001 顺带修复**：`_terminal_api` register 分支补传 `asset=data.get("asset")`（历史断言失败遗留——schema1 资产明细从未入库）；store 端 asset 序列化 try/except 静默降级 NULL（零 assert）。部署后重启终端 winhelper 实测：`WIN-Jun-office-PC` asset_detail 由 NULL→完整 schema1 JSON，`GET /console/terminals/{tid}` asset 返回结构化数据（非 hwinfo 回退）。
  9. **PUT 请求体修复**：app.py Handler 仅对 POST 读取 body——sysadmin 的 PUT（账户/第三方更新）body 恒空导致 400；改为 `method in ("POST","PUT")` 读取（顺带修复 kb PUT 更新同样拿不到 body 的存量问题）。
- **验证门禁**（全绿）：py_compile 四文件 / esprima 校验 index.html 脚本 / `tools/test_sysadmin.py` 本地单测 44 项（临时库，含账户防线+token 兼容+CRUD）/ `tools/smoke_sysadmin.py` 冒烟 42 项（本地 42+生产 42）/ Playwright E2E 27 项（含 sysadmin 页断言，工具同步更新）；生产部署后真实终端（WIN-Jun-office-PC）心跳全链路 200、token disable→401/enable→200/rotate 旧失效新生效矩阵在真实终端验证。
- **工具沉淀**：`tools/dev_bootstrap.py`（本地开发库 admin 引导，口令走 ETP_ADMIN_PWD）；`tools/test_sysadmin.py`（零凭据单测）；`tools/smoke_sysadmin.py`（环境变量驱动远程冒烟）。
- **已知限制**：第三方接口仅登记（不代理调用）；token 明文回显（需求要求可查看，页面 admin-only）；LLM test 用 /v1/models 连通性（不消耗额度）；等保双因素（R-01）仍属二期。

## ADR-022 ｜ 控制台「知识库」页 + 路由表数据源统一（kb_entries 优先） ｜ 已接受

- **背景**：kb_store（三表 kb_entries/kb_versions/kb_history + 5 版本迭代 + 回滚）与 6 条 kb API 早已部署但**从未真正可用**（api.py 缺 `import kb_store`、PUT 引用未定义的 `DASH`、versions/rollback 路由索引错位永不命中）；net-doctor 终端 tracert 标注读 settings `netdoctor.route_nodes`（空值），用户需要可在控制台维护的路由表。
- **决策**（2026-09-09 实施并部署 172.17.5.215）：
  1. **控制台「知识库」页**：nav 置于 AI 分析与配置清单之间；条目表（标题/分类/版本/更新人/更新时间/操作）+ 分类下拉（SELECT DISTINCT）+ 搜索框（title/kb_id/content LIKE）+ 新增/编辑弹窗（kb_id 仅创建时填、title、category 可输入可选、content 大文本域、变更备注）+ 版本历史弹窗（最近 5 版 + 每版「回滚」按钮，confirm 后 POST rollback）。JS 函数 kb 前缀（与 nd/sa 隔离）。
  2. **操作人语义**：kb API 全部强制取当前会话用户名（`sess["username"]`），客户端传入 author 一律忽略——与控制台审计口径一致。
  3. **路由表数据源统一**：`GET /api/v1/terminals/{tid}/netdoctor/route-nodes` 改为**优先读 kb_entries 中 category='route_nodes' 最新条目**（content 为 JSON 数组 [{match,zone,desc}]，响应带 source=kb），解析失败/无条目回退 settings `netdoctor.route_nodes`（source=settings）。用户在知识库维护路由表，终端 tracert 即时联动，无需改配置。
  4. **预置条目**：KbStore 初始化幂等预置（create 遇 kb_id 存在静默跳过）`route-nodes` / `route_nodes` / 「路由表 · 关键节点」/ content `[{"match":"172.17.254.0/24","zone":"核心交换层","desc":"核心/汇聚交换机"}]`，author=system。
  5. **存量缺陷修复**（kb API 从未实测暴露）：①api.py 补 `import kb_store`（NameError）；②`_console_kb_api` versions/rollback/history 分支 rest 索引错位（`len(rest)>=3` 致子路由永不命中）修正为 rest[1]；③PUT 分支 `DASH` 未定义（NameError）改为字段缺省保持原值语义；④`kb_store.delete()` 同步清理 kb_versions（防同 kb_id 重建后版本错乱/可回滚到已删内容），kb_history 保留（审计追溯）。
- **验证门禁**（全绿）：py_compile / esprima（58KB 脚本块）/ Playwright E2E 33 项（新增 kb 6 断言：渲染/预置条目/新增/2 版本/回滚 v3/回滚内容核对 + pageerror=0）/ 生产冒烟 `tools/smoke_kb.py` 14/14（seed 条目、categories、**route-nodes source=kb 联动实测**、CRUD、versions [2,1]、回滚 v3 内容=v1、删除 404、真实终端心跳 200）。
- **部署**：备份 /data/terminal-platform/backups/pre_20260909_132811、pre_20260909_133117；生产 kb_entries 现有 route-nodes 预置条目可编辑。
- **已知限制**：kb API 登录即可操作（未限 admin）——知识库为运维协作内容；回滚采用「以旧版内容生成新版本」策略（版本单调递增，不丢历史）。

## ADR-023 ｜ 终端 AI 智能诊断（终端推送日志包 → LLM 链出结论） ｜ 已接受

- **背景**：终端侧（net-doctor-dev 并行）新增「AI 智能诊断」入口，主动推送运行日志包+问题概述，服务端调 LLM 出结论并记录；与服务端聚合式 /ai/analyze 互补。
- **决策**（2026-09-09 实施并部署 172.17.5.215）：
  1. **端点**：`POST /api/v1/terminals/{tid}/ai/diagnose`（X-ETP-Token 复用终端鉴权 + 白名单准入；_terminal_api 6 段分支与 netdoctor 同级）。请求 `{issue ≤2000 字, logs{六类可选: hwinfo/os_info/perf_analysis/perf_stress/system_log/network}}`，值可为对象或字符串。
  2. **校验**：终端不存在 404；issue 空/超长 400；logs 非对象 400；body 超限走既有 _MAX_BODY 413。
  3. **截断策略**：单类日志原始 32KB 头部截断（尾标 …[truncated]）；注入 prompt 每类 ≤4KB（六类合计 ≤24KB，对齐 build_context 6000 字符的体量控制先例，处于 LLM 上下文安全范围）；context_json 存证每类 ≤4KB。
  4. **prompt 构建**：ai.py `DIAG_SYSTEM_PROMPT`（终端运维诊断专用，结构同 SYSTEM_PROMPT：故障原因分析/处理意见/风险提示）+ `build_diagnose_context`（issue 置顶 + 六类结构化分节，缺类别提示 LLM 数据缺失）；LLM 调用**必须走既有 llm_chat_chain 主备降级链**（timeout=45 × max_retries=0，双模型最坏 ~90s+切换间隔，满足终端 HTTP 同步等待 ≤120s 契约）。
  5. **落库**：ai_analyses trigger=`terminal_diagnose`（新枚举）、issue 存概述、context_json={issue, logs(4KB/类存证)}、response_text/model/duration_ms/status；LLM 全链失败 → 记录仍落库（status=failed）并返回 **502** {ok:false, error, analysis_id, duration_ms}。
  6. **控制台配套**：AI 分析页 trigger 文案映射（console→控制台/terminal→终端上报/terminal_diagnose→终端诊断）；新增 `GET /api/v1/console/ai/analyses/{id}` 单条详情端点（含 context_json），详情弹窗对终端诊断显示 issue + 各类日志摘要（每类前 300 字符）+ 诊断结论。
- **验证门禁**（全绿）：py_compile / `tools/test_ai_diagnose.py` 本地单测 33 项（截断策略/对象文本化/缺键容错/空 logs/注入与存证上限/mock 链成功与失败路径/trigger 落库/路由 400·404 校验）/ esprima / 生产冒烟 `tools/smoke_ai_diagnose.py` 9/9（真实终端 diagnose 200 → analysis_id=9 + 非空 response_text + model=Qwen3.6 + duration=11340ms；历史可见 trigger=terminal_diagnose；详情含 issue+hwinfo 存证；空 issue 400；logs 非对象 400；现有 /ai/analyze 回归 200；心跳 200）。
- **部署**：备份 /data/terminal-platform/backups/pre_20260909_155852。终端侧由 net-doctor-dev 并行开发，契约以本 ADR 为准。
- **已知限制**：同步接口终端侧需设 ≥120s 超时；日志包体量受 _MAX_BODY 与六类 32KB 截断约束（超大日志建议终端侧先摘要）；4860 类六类键之外的上报字段忽略（向前兼容）。

## ADR-025 ｜ IP 冲突检测接入画方准入真实数据源 ｜ 已接受

- **背景**：ADR-015 ipconflict 的 admission_log/core_switch_state 两源此前如实标注 not_connected；main 已实测画方准入 HTTP API（errno=0，total=1588 台），凭据登记于系统管理第三方接口 id=2。
- **决策**（2026-09-09 实施并部署 172.17.5.215）：
  1. **nad_client.py**（纯标准库 urllib+ssl+hashlib+hmac+random）：凭据运行时从 third_party_apis id=2 读取（base_url/params_json 的 app_key/app_secret/enctype/version，enabled=false 或删除=未配置）；sign=hmac_sha256_hex(appsecret, "appkey={}&nonce={}&time={}")；POST /httpapi/term/get 自签证书 verify=False、超时 10s。
  2. **实测结构适配**（探针捕获，与接口文档有三处差异）：①data.list 为 {"0":{...}} 形态 dict（非数组）；②macs 为 {"0":{mac,ips,macports}} 条目对象数组（mac/ips 在条目内）；③ips 亦为 dict 形态；④**单页实际上限 1000**（total=1588 需翻页）——nad_fetch_terms 自动翻页（页空/取满 total/NAD_MAX_PAGES=50 断尾），解析层 _norm_page_list/_norm_macs/_norm_ips 统一归一。
  3. **60s 结果缓存**：模块级时间戳防连点（全量 ~1MB/次拉取对低频手动触发可接受）；nad_cache_invalidate 供运维强制刷新。
  4. **verdict 注入**（api.py `_enrich_ipconflict_admission`，store 保持无网络依赖）：命中 → sources.admission_log=connected + verdict.admission 证据块 {source:'nad', name, ou(namepath), ttype, manfct/model, online, block, reginfo.stat, macs:[{mac,ips,macports}]}；**同 IP 异 MAC 升级**：准入登记 MAC 与上报 MAC 不一致 → conflict_suspect=true + evidence 追加 nad 块 + admission_registered_macs；core_switch_state 近似：上报 MAC 有登记且有 macports → nas_connected / 有登记无端口 → registered_no_port / 不在库 → not_connected；未配置/异常 → not_configured 或 error:... 降级，绝不阻断平台内判定。
- **验证门禁**（全绿）：py_compile / `tools/test_nad_client.py` 本地单测 37 项（签名对照独立实现/配置四态/缓存 TTL+invalidate/IP·MAC 归一索引/原始形态归一/verdict 注入五场景）/ 回归 test_sysadmin 44 + test_ai_diagnose 33 / 生产冒烟 `tools/smoke_nad_ipconflict.py` 10/10。
- **生产冒烟证据**（真实数据）：真实终端 WIN-Jun-office-PC（ip=172.17.90.215，mac=F4:F1:9E:3C:69:E4）上报 → admission_log=connected，命中准入登记「…附属…医院…院区…信息科 办公终端」，core_switch_state=registered_no_port（准入库该 MAC 有登记、无交换机端口记录——真实状态）；同 IP 伪造 MAC 00-11-22-33-44-55 → conflict_suspect=true + admission_registered_macs=['F4:F1:9E:3C:69:E4'] + evidence 含 source:nad 块。
- **部署**：备份 pre_20260909_172613、pre_20260909_173340。
- **已知限制**：①macports 依赖准入侧 NAS 采集覆盖度，未覆盖时 core_switch_state=registered_no_port（如实近似非虚构）；②60s 缓存内数据有延迟；③单页上限若准入平台变更需同步 NAD_PAGE_LIMIT。

## ADR-026 ｜ 系统管理·交换机管理（默认凭据 + 台账维护） ｜ 已接受

- **背景**：为后续交换机采集功能预留基础数据：管理员提供只读默认凭据（reader / 只读账号，所有交换机默认使用），需台账维护能力；**本需求只做维护不做 SSH 连通**（服务端零依赖红线不引入 SSH 客户端）。
- **决策**（2026-09-09 实施并部署 172.17.5.215）：
  1. **默认凭据**：settings 新键 `switch.default_username`（明文，入 DEFAULTS 默认空串）/ `switch.default_password`（入 SENSITIVE_KEYS，SecretsBox 加密落库、出接口脱敏——对齐 llm.api_key 模式）。SettingsStore 暴露 encrypt/decrypt 代理供 switches.password_enc 复用同一 machine.key。
  2. **台账表**：`switches(id, name, ip, ssh_port DEFAULT 22, username, password_enc, brand, created_ts, updated_ts)`，password_enc 存 SecretsBox 密文；store CRUD（list 输出不含 password_enc，附 password_set 布尔）。
  3. **API**（/api/v1/console/sysadmin/*，admin-only 延续）：GET/PUT `/switch-default`（PUT password 留空=保持不变）；GET/POST `/switches`（name/ip/username/password 必填 400，ipaddress 格式校验 400）、PUT/DELETE `/switches/{id}`（PUT 密码留空保持原值；404 处理）；列表 password 恒 '****'；增删改写 console_audit_log（Ev.SWITCH_DEFAULT_UPDATED/CREATED/UPDATED/DELETED，detail 记对象与变更字段、**不记明文密码**）。
  4. **UI**：系统管理页第五卡片「交换机管理 · 默认凭据」+ 台账表（名称/IP/端口/账号/品牌/密码/操作）+ 新增/编辑弹窗（编辑时密码留空保持）；sa 前缀续用；文案注明默认凭据用于未单独维护凭据的交换机、只读权限。
  5. **部署预置**：deploy.py 经 config_cli 幂等预置 reader/真实口令——**以 `switch.default_password --mask` 是否 "(unset)" 为幂等依据**（username 在 DEFAULTS 中默认空串，get 永不返回 unset，不能作依据）；password 走 `printf %s '<quoted>' | config_cli set <key> --stdin` 注入（不经命令行明文、不受特殊字符影响）。
- **验证门禁**（全绿）：py_compile / esprima / `tools/test_switch_admin.py` 本地单测 24 项（默认凭据加密落库且密文非明文/GET 回读无明文/留空保持/列表脱敏恒 ****/CRUD 往返+404/ip 校验 400/PUT 新密码密文更新且可解密回原值/operator 403/审计四类落库且不记明文）/ 生产冒烟 `tools/smoke_switch_admin.py` 13/13（预置 reader 生效且响应无明文、留空保持、单台 CRUD 往返、重复删除 404、全列表无明文残留、心跳 200）；生产审计抽查 switch.created/updated/deleted/default_updated 四类事件全部 success 落库。
- **部署**：备份 pre_20260909_182454、pre_20260909_182547（后者含预置凭据 seed）。
- **已知限制**：①默认凭据为全局单套（按需求"所有交换机默认使用"），单台可独立覆盖；②无 SSH 连通测试（需求明确排除，未来采集功能另议需引入 SSH 客户端依赖时须重估零依赖红线）。

## ADR-027 ｜ 终端 AI 诊断截断层重做：32KB 承诺一致 + 结构感知裁剪 + 优先级预算分配 ｜ 已接受

- **背景**（生产缺陷，analysis_id=16）：用户问题「今早电脑重启了，分析原因」，LLM 虚构日志依据（引用未提供的 WHEA-Logger 17 等事件）。三层根因：
  1. **证据饥饿**：`DIAG_PROMPT_PER_KEY=DIAG_EVIDENCE_PER_KEY=4000`——尽管 `DIAG_RAW_LIMIT=32KB`，LLM 提示词每类只注入前 4000 字符（≈5~6 条事件，且终端采集为最新优先倒序），问题涉及时段（今早重启）的事件完全不在提示词内；
  2. **存证 JSON 损坏**：`truncate_text` 裸字符切割（`text[:limit]`）撕裂 JSON 转义序列——system_log 存证恰在 4000 字符处截断，`json.loads` 报 Invalid control character（存证不可解析=不可信）；
  3. **承诺不符**：终端 AI 卡 UI 明示「单类超 32KB 自动截断」，实际注入/存证仅 4000 字符。
- **决策**（2026-09-10 实施并部署 172.17.5.215，仅服务端；终端侧提示词/采集优化另由 net-doctor-dev 负责）：
  1. **预算与承诺对齐**：`DIAG_PROMPT_PER_KEY` 4000→**16KB**；`DIAG_EVIDENCE_PER_KEY` 4000→**32KB**（=DIAG_RAW_LIMIT，存证层与 RAW 层合一，不再二次截断丢失）；新增 `DIAG_PROMPT_TOTAL=32KB` 全 prompt 日志注入总预算（防溢出，LLM 上下文安全范围）。
  2. **优先级预算分配**（`_allocate_diag_budgets` 贪心）：`system_log` 恒第一优先；问题概述命中关键词的类提前（`_KEY_HINT_WORDS` 六类关键词表，如「网络/断网」→network、「卡顿/CPU」→perf_analysis）；快照类（os_info/hwinfo）垫底——日志类不被快照类挤占；分节顺序=优先级序（高优先证据前置）；**因预算未注入的类别在 prompt 尾部如实声明**（防 LLM 把「未注入」误判为「未采集」而虚构）。
  3. **结构感知截断**（`_clip_json_value/_clip_json_list/_clip_json_dict`）：JSON 值/JSON 文本按「完整事件/完整条目」粒度裁剪——list 二分保留完整条目前缀；dict 小值键优先完整保留+大值键键内结构裁剪+放不下的尾部键按完整键丢弃；仅字符串标量才字符截断且截断发生在对象层、序列化转义由 `json.dumps` 统一完成——**绝不撕裂已序列化 JSON 文本，裁剪结果恒可通过 json.loads**。非 JSON 文本才回退字符截断+…[truncated] 尾注；JSON 分支不拼文本尾注（保可解析性），截断与否由 stats 承载。
  4. **stats 如实存证**：`context_json.logs_stats[key]={original_chars, evidence_chars, original_items, kept_items, truncated, suspect_mojibake}`（原始条数=顶层 list 或 dict 内首个 list 长度；非 JSON 文本 kept_items=None）；控制台诊断详情弹窗每类日志显示「N/M 条 · 已截断 · 疑似乱码 · 原始 X 字符」徽章。
- **加项1（同日，与终端侧 net-doctor-dev 对齐）·企业版提示词证据可信性硬约束**：`DIAG_SYSTEM_PROMPT` 新增三条硬约束（与终端个人版 `_ND_AI_PROMPT_SYSTEM` 同款）：①只允许引用随请求日志中实际出现的事件 ID/来源/时间戳，严禁编造/推测/凭常识填充「日志依据」；②引用事件必须附原文时间戳，无法给出时间戳的依据不得引用；③某类日志未提供/为空/截断必须显式声明「该类证据不足」。配合 ADR-027 主体修复（提示词证据从 4KB/类提升到 16KB/类）从根上消除「虚构依据」土壤。
- **加项2（同日）·中文乱码排查（analysis_id=16 取证）**：
  - **取证**：生产库 16 号存证 `os.caption="Microsoft Windows 11 רҵ?…վ"`（专业工作站）、`level_name="??ؼ?"`（关键）——希伯来/阿拉伯字母混入样式 = **GBK 字节被 UTF-8 误解码**方向 mojibake。
  - **服务端解码链排查结论（三环节均排除）**：①`_read_body` 按 Content-Length 完整读取，无字节截断；②`_parse_body` 严格 `body.decode("utf-8")`（errors 默认 strict，错位/坏字节直接 400，不存在 charset 错位空间；gzip 分支同）；③截断层为 Python str **码点级**切割（单测断言中文截断后无 U+FFFD、无半个字），结构感知裁剪发生在对象层。同 JSON 内 issue（终端 UI 输入）中文正常而 os_info/system_log（终端子进程采集层）乱码——**字段级差异证明错位发生在终端采集层（代码页错位），服务端如实存证**。终端侧修复由 net-doctor-dev 负责（672e3ff 已交付）。
  - **服务端防御性增强**：`_detect_mojibake` 双向探测（U+FFFD 替换符 / UTF-8→GBK 高频双字产物 / CJK 语境混入 ≥3 个西里尔·亚美尼亚·希伯来·阿拉伯字母）→ `logs_stats.suspect_mojibake`，控制台「疑似乱码」徽章；**联调复测直接看该标记=false 即验证乱码消除**（新 exe 真实链路，以新 analysis_id 记录）。
- **验证门禁**（全绿）：py_compile / `tools/test_ai_diagnose.py` **62 项**（新增：正常 JSON 裁剪小键完整保留、超长 JSON dict/list/JSON 字符串裁剪后可解析且条目完整、转义敏感内容无损、非 JSON 回退、中文按字符截断无半个字、六类全满 system_log 优先拿满 16KB+总注入≤32KB+尾部类声明、关键词优先级提前、32KB 存证与 RAW 层一致性、logs_stats 落库、双向 mojibake 探测+不误标、证据可信性硬约束三条入 prompt）/ 回归 test_sysadmin 44 + test_nad_client 37 + test_switch_admin 24 全绿 / esprima 64.5KB 控制台脚本块 / 生产冒烟 `tools/smoke_ai_diagnose.py` 10/10（新增 logs_stats 断言；diagnose 200→analysis_id=17 model=Qwen3.6）。
- **已知限制**：①JSON 分支无文本尾注，截断判定以 logs_stats 为准（控制台已展示）；②`_try_parse_json` 对超 128KB 文本跳过解析直接回退字符截断（防御性，正常入口已被 32KB RAW 层约束）；③关键词优先级为启发式非强过滤，未命中时保持默认序；④乱码探测为启发式 suspect 标记（非定性），西里尔/阿拉伯字母在正常运维中文文本中理论上可能误报（概率极低），探测不修改原文只作标记；⑤乱码根因修复在终端采集层（net-doctor-dev），服务端 suspect_mojibake=false 为联调复测验收依据。

## ADR-028 ｜ 测试数据隔离定型 + IP 冲突判定精化（终端维度归因） ｜ 已接受

- **背景**（用户实测截图暴露）：①生产库 `ipconflict_reports` 含冒烟假 MAC 污染行（AA-BB-CC-DD-EE-01、00-11-22-33-44-55——历史版 `smoke_nad_ipconflict.py` 直接向生产端点 POST 假数据落库），假证据进入用户可见判定；②「同 IP 7 天多 MAC → suspect」判定过粗：同一 terminal_id 真实网卡变更/虚拟适配器/上报格式差异（F4:F1:9E:3C:69:E4 与 F4-F1-9E-3C-69-E4）均被误判为疑似冲突。
- **决策**（2026-09-10 实施并部署 172.17.5.215）：
  1. **测试数据隔离定型（红线，对齐 ADR-020 精神）**：一切冒烟/E2E/联调必须使用隔离 config/db（临时库或本地实例），**测试数据禁触生产库**；生产冒烟一律只读（登录/心跳/GET/路由负向 400·404），写路径全场景验证由隔离单测承担。`smoke_nad_ipconflict.py` 已改造为只读模式（R1-R4），写场景由新 `tools/test_ipconflict.py`（隔离临时库）覆盖；违反本条即事故（历史污染案例为本条依据）。
  2. **判定精化**（store.ipconflict_report 重写）：按 (terminal_id, 归一 MAC) 归因——①同 IP 窗口内出现**其它 terminal_id** 报告 → suspect（multi_terminal=双终端抢占）；terminal_id 不在 terminals 表的未知来源 → suspect（unknown_terminal，保守原则）；②仅同一 terminal_id 的 MAC 变化 → **不 suspect**，记 `nic_history`（网卡变更史，证据照存供人工参考）；③`verdict.suspect_reasons` 统一归口判定依据（api 层 nad 侧准入登记不一致升级追加 nad_registered_mismatch）。
  3. **MAC 归一比对**（`_mac_key`：去 `:-.` 空白分隔符 + 小写）：冒烟/终端上报格式差异（连字符/冒号/华为四位段）不再产生假异 MAC；同网卡异格式记录归并为一条（保留最近一次原始格式），不重复计数。
- **污染清理记录**：dry-run 审计 13 行 → 识别 4 行假 MAC（#1/#4/#6/#8）→ 备份 `/data/terminal-platform/backups/ipconflict_cleanup_20260910_191407.json` → 删除 → 残留验证 0；9 行真实上报保留（含真实网卡 88:AE:DD:AF:36:54 与 F4:F1:9E:3C:69:E4 多格式记录）。一次性脚本 `tools/_clean_ipconflict_smoke.py`（dry-run/apply 两段式，远端 venv python 操作）留存备查。
- **验证门禁**（全绿）：py_compile / `tools/test_ipconflict.py` **16 项**（空证据不报错/同终端换 MAC 不误报+nic_history/同网卡异格式归一不新增/第三块网卡累积/双终端同 IP suspect+multi_terminal/未知 terminal_id 保守 suspect+unknown_terminal/窗口外不参与/窗口内参与/路由 200 verdict 结构完整/nad not_configured 降级/新 IP 干净判定/缺参 400×2）/ 回归 test_ai_diagnose 62 全绿 / 生产只读冒烟。
- **已知限制**：①nad 侧准入登记不一致（终端换网卡后准入库未更新）仍会升级 suspect（nad_registered_mismatch）——准入库为权威登记源，不一致即值得警示（提示更新准入登记），过严与否待用户实测反馈；②nic_history/evidence 的 last_ts 为该 (terminal_id, mac) 最近上报时间；③ipconflict_reports 无自动清理策略（流水表，量级小，暂不需要）。

## ADR-029 ｜ IP 冲突深度检测引擎（网工级 SSH 编排）+ paramiko 依赖例外 ｜ 已接受

- **背景**：用户提供的网工级排查流程（权威规格）：①按业务 IP 在核心/区域汇聚交换机 `display arp | include <IP>` 检索多条 ARP 并核对 IP-MAC 匹配 → ②按 IP+MAC 去画方准入匹配资产定位接入交换机与端口 → ③顺藤摸瓜取其网络隶属网关/接入设备 IP → ④登录该交换机 `display mac-address | include <MAC>` 检测同 MAC 多端口/多 VLAN 在线。Phase A 实测盘点（2026-09-10）：NAD macports={nasoid,nasif,nasname,manip} 1348/1594 条目直出接入交换机管理 IP；平台服务器→管理网 192.168.254.0/24 与 172.17.254.1 TCP22 全通（Comware-7.1.070）；paramiko 5.x 移除老算法无法协商 Comware，3.5.1 协商通过；switches 台账空、reader 交换机侧认证失败（待用户开户）。
- **决策**（Phase B，main 裁定六项后实施）：
  1. **依赖例外**（修订 ADR-002 为例外清单制）：`paramiko==3.5.1` 仅 `server/deep_engine.py` 使用。版本锁定理由：5.x 移除 ssh-rsa/ssh-dss 导致 Comware V7 协商失败（实测 unknown cipher）；凭据零回显（仅内存持有，不进证据链/日志/审计明文）；deploy.py 幂等安装段（失败不阻断部署，引擎降级 not_installed）。
  2. **引擎编排**（`deep_engine.run_deep_check` 五步，逐步 emit 进度+证据链，任一步失败不阻断）：①resolve——kb_entries route_nodes 最长前缀匹配（新增可选 `gw_ip` 字段，缺→skipped 提示知识库维护「网段→网关设备」映射）；②arp——网关 `display arp | include <ip>`：0 条=empty、1 条校验 MAC 匹配（match/mismatch，mismatch=网关层冲突信号）、≥2 条不同 MAC=multi（冲突实锤）；③nad——复用 nad_find_by_ip，命中 macports.manip 作接入交换机登录目标；④macaddr——接入交换机 `display mac-address | include <四位段>`：同 MAC 多端口=multi（漂移/环路信号→suspect）、单端口=done、无=empty；⑤conclude——合成 confirmed（ARP multi/mismatch）/ suspect（MAC 多端口/平台内交叉）/ normal（ARP match+接入层无异常）/ insufficient_evidence（关键步骤全失败或证据为空），`verdict.sources` 如实标注各数据源状态。
  3. **命令安全**：只读命令白名单模板（screen-length disable / display arp|include / display mac-address|include / display version）；ip 经 ipaddress 校验、mac 归一为十六进制后进 include 参数——无注入面；逐命令 {cmd, ok, output_tail≤1500} 存档于 steps_json（审计）。
  4. **双端点**（契约已同步 net-doctor-dev 做 UI）：`POST /api/v1/terminals/{tid}/netdoctor/ipconflict-deep {ip,mac}` → task_id（异步线程编排，全局 Semaphore(2) 并发，满 429）；`GET .../ipconflict-deep/{task_id}` → 逐步进度+verdict。任务落 `ipconflict_deep_tasks` 表（task_id 唯一，steps_json 实时回写）。
  5. **NAD 清单导出**（台账补录辅助，admin-only）：`GET /api/v1/console/sysadmin/nad-switches`（聚合预览+in_ledger 标记）+ `POST /api/v1/console/sysadmin/switches/import-nad`（批量入台账，已存在 IP 跳过，凭据=全局缺省加密落库，审计 nad_import）。
  6. **管理网可达性（Phase B 首项实测）**：192.168.254.1/.4/.9 与 172.17.254.1 全部可达（Comware banner），mac-address 步骤无需降级；172.17.254.9 拒连（设备侧问题，如实标注）。
- **验证门禁**（全绿）：py_compile / `tools/test_deep_engine.py` **40 项**（MAC 三格式互转/ARP·MAC 解析器含表头跳过与多端口/编排七分支 mock SSH：normal·confirmed·suspect·认证失败降级·凭据零回显·无 gw_ip skipped·NAD 降级/任务生命周期/lookup 只读不落库/路由 POST·GET·400×2·404）/ 回归 test_ipconflict 16 + test_ai_diagnose 62 / 生产部署后只读冒烟+一次真实任务验证。
- **已知限制**：①reader 交换机侧开户未完成前，SSH 步骤如实标注「认证失败：请核对交换机 reader 凭据」不阻断；②解析器基于 Comware V7 标准输出格式编写（宽列车解析），真实交换机输出样本以生产首任务 steps_json 存档校准；③display version 命令保留在白名单暂未编排进步骤（设备信息留 Phase B+ 扩展）；④deep 任务记录无自动清理（低频运维操作）。

## ADR-030 ｜ /ai/analyze IP 冲突聚合分支（平台四源证据 → LLM） ｜ 已接受

- **背景**：终端冲突疑似时自动调 /ai/analyze，但 issue_description 仅 120 字×6 条薄证据串（且终端侧提取字段错位在修）。证据（冲突上报/NAD 准入/深度检测）都在平台侧——由平台聚合喂 LLM，多源互相印证得出冲突判定。
- **决策**（2026-09-10 实施并部署 172.17.5.215）：
  1. **触发**：POST /api/v1/ai/analyze 请求可选 `kind:"ipconflict"` 字段，或 issue_description 前缀 `ipconflict/ip_conflict/ip冲突` 自动识别；普通请求不受影响。
  2. **四源聚合**（`_aggregate_ipconflict_context`）：①ipconflict_reports——该终端最近上报 IP 的交叉判定（复用 ADR-028 ipconflict_lookup，含 suspect_reasons/nic_history/evidence，只读不落库）；②NAD 准入资产——nad_find_by_ip（名称/部门/在线/IP/MAC/接入交换机 macports）；③最近一次 ipconflict_deep 任务（deep_task_latest，steps 摘要化 step/name/status/target/note/evidence_count + verdict）；④数据源状态位 sources{terminal_reports/admission/gateway_arp/access_mac}。**无冲突上报记录 → pkg=None 回退原一般性分析路径**（kind 语义不改变无冲突场景行为）。
  3. **prompt 规范对齐 ADR-027**：`build_ipconflict_context` 复用结构感知裁剪与预算分配设施——单类注入 ≤16KB、总量 ≤32KB、存证每类 ≤32KB（JSON 类可再解析）；优先级 conflict_reports > deep_task > admission > sources（冲突证据最优先）；缺失源在 prompt 尾部如实声明「本次未提供」；`IPCONFLICT_SYSTEM_PROMPT` 含 DIAG_SYSTEM_PROMPT 同款证据可信性硬约束三条（严禁编造/引用附原文时间戳/缺失声明证据不足）。
  4. **响应结构**：{ok, response, analysis_id, model} 不变——顺带补齐 run_ai_analysis 一直缺失的 `model` 字段（终端侧解析对齐）。
  5. **落库**：ai_analyses context={kind:"ipconflict", ip, mac, sources, sections(四源存证), stats}；trigger 沿用 terminal/console。
- **验证门禁**（全绿）：py_compile / `tools/test_ai_ipconflict.py` **29 项**（四源聚合含 prompt 四节+硬约束三条+存证可解析/缺源 no_task·not_configured 如实标注+未提供声明/无冲突上报回退一般性分析/issue 前缀与 kind 字段双识别/预算裁剪超长 deep_task 注入受控且 JSON 完好/stats 如实截断标记/响应含 model）/ 回归 deep_engine 40 + ipconflict 16 + ai_diagnose 62 / 生产只读冒烟。
- **已知限制**：①分析目标 IP/MAC 取该终端最近一次冲突上报（无上报则回退），不解析终端 asset 实时网卡（终端字段错位修复后再增强）；②deep_task steps 证据链只注入摘要（step/name/status/target/note/evidence_count），完整 output_tail 以任务详情端点为准（避免 prompt 撑爆）；③llm 分析依赖各源真实数据质量，reader 未开户期 ARP 源为 failed 降级态（LLM 会按硬约束声明证据不足）。

## ADR-031 ｜ /ai/analyze routetrace 分支（路由追踪 AI 研判） ｜ 已接受

- **背景**：终端 tracert 已具备逐跳 zone 标注能力（net-doctor-dev，ADR-022 route_nodes 联动），用户要求对追踪结果做 AI 路由研判（区域链重建/异常识别/结论），与 ADR-030 同款聚合模式。
- **决策**（2026-09-11 实施并部署 172.17.5.215）：
  1. **触发**：POST /api/v1/ai/analyze 请求可选 `kind:"routetrace"` + `context:{target, hops:[{hop, ip, host, delays, timeout, zone, zone_desc}]}`（hops 终端上传，15 跳量级）；issue_description 前缀「路由追踪分析」自动识别。hops 非法/空 → 回退一般性分析。
  2. **三源聚合**（`_aggregate_routetrace_context`）：①hops 全量（终端上传，含超时跳与 zone 标注）②route_nodes 知识库全量（`_kb_route_nodes`，LLM 据此判定各跳区域归属）③终端基础信息（名称/IP/网段 /24 提取）。
  3. **prompt 规范**：`ROUTETRACE_SYSTEM_PROMPT` 路由分析专家角色（区域判定/路径链重建/异常识别四类/结论三态 normal·suspect·需关注）+ 证据硬约束三条同款（只引用提供的 hops 与知识库、引用跳点必须附跳数序号、未匹配区域跳如实标注「知识库未覆盖」）；**硬约束④（2026-09-11 用户纠偏增补）**：禁止根据 IP 地址段、相邻跳点或常识推测任何区域/VLAN/网段归属——知识库未覆盖跳点一律只标注「知识库未覆盖」，知识库补充只能来自管理员权威数据（IPCONFLICT_SYSTEM_PROMPT 同步收紧「冲突画像」归属推测表述）；预算**逐类上限**：hops 16KB / route_nodes 8KB / terminal 4KB / 总 32KB（`_allocate_budgets` 通用化，caps 语义区别于 ADR-027 均一 cap）；结构感知裁剪（超长 hops 按完整跳点裁剪、JSON 恒可解析）；macaddr 同款「原始行不去重」语义沿用（超时跳 timeout:true 保真）。
  4. **落库/响应**：context={kind:"routetrace", target, hops_count, sources{hops/route_nodes/terminal}, sections, stats}；响应 {ok, response, analysis_id, model} 不变。
- **验证门禁**（全绿）：py_compile / `tools/test_ai_routetrace.py` **25 项**（三节聚合/追踪目标/超时跳保真/知识库注入/系统提示词三态+硬约束/存证可解析/超大 hops 16KB cap 注入与 32KB 存证分离断言+裁剪后 JSON 完好+头部跳点保留/缺源声明/路由 dispatch 前缀与 kind 双识别/空 hops·非法 hops 回退）/ 回归 ai_ipconflict 29 + deep_engine 40 + ai_diagnose 62 + ipconflict 16 / 生产只读冒烟 + 真实 LLM 调用一次。
- **已知限制**：①hops 由终端上传（net-doctor tracert 引擎产出），服务端不校验 zone 标注正确性（LLM 依知识库复核，未覆盖跳按硬约束标注）；②预算 caps 为任务书口径（hops 16KB/知识库 8KB），调整改 ROUTETRACE_CAPS 常量即可；③终端网段按 /24 近似（业务环境标准段宽）。

## ADR-032 ｜ HTTPS 专项改造：双 TLS 监听端口分离 + 自建 CA + 终端指纹双层校验 ｜ 已接受

- **背景**：平台原为单端口 18090 HTTP 明文，终端 token 与控制台会话同端口暴露于链路嗅探面。用户已批准《变更方案_端口分离与HTTPS改造》：管理端口=**HTTPS 8443**（用户 2026-09-11 拍板）、终端端口默认 **18443/TLS**（config 可调）、自建 CA + 服务器证书（SAN=DNS:zljtest5.215 + IP:172.17.5.215）、旧 HTTP 18090 并行保留至收口后下线、终端 http:// 地址过渡期完全兼容（回滚=客户端改回旧地址秒级生效）。
- **决策**（2026-09-11 实施，P0 spike 先行门禁制）：
  1. **P0 spike 双门禁（先于实施）**：①`tools/spike_dual_https.py`（**14/14**）——同进程三监听（TLS 终端口/TLS 管理口/HTTP 旧口）纯标准库可行：TLS1.2 握手（min 服务端强制 + max 客户端受限双口径）、40 并发每连接新握手 1.55s 全 200、keep-alive 复用、scope 跨类 404 六断言、legacy 并行不受扰、**明文探测 TLS 端口后服务存活**（SSLError ⊂ OSError 走 socketserver get_request 异常路径 + handle_error 静默）；②`tools/spike_meipass_tls.py`（**三层 PASS**）——PyInstaller onefile 内 `sys._MEIPASS/certs/ca.crt` 定位 + PEM→DER SHA256 指纹层1 fail-closed + `create_default_context(cafile)` 证书链/主机名层2 + 错指纹层3 必拒，`in_MEIPASS=True` 实证。**结论：架构成立，无需 TLS 终结兜底**。
  2. **端口路由分离（scope）**：`api.dispatch(..., scope=None|"all"|"terminal"|"console")` + `_scope_allowed` 白名单——terminal 口仅放行 `/api/v1/health`、`/api/v1/terminals/*`、`/api/v1/ai/analyze`；console 口仅 `/api/v1/health`、`/api/v1/console/*`、静态 GET；跨类访问在**鉴权层之前即 404**（不泄露路由存在性）。legacy 口 scope=all 全量不变。
  3. **监听装配（app.py）**：`tls_settings()` 解析 config 新键（`terminal_port` 默认 18443 / `console_port` 默认 8443 / `tls{enabled,cert,key,min_tls_version}` 默认 data_dir/certs/ 与 TLSv1_2 / `legacy_http.enabled` 默认 true）；证书缺失时 **TLS 跳过并 WARN、legacy 独活**（平滑部署保证）；`ThreadingHTTPServer` 监听 socket 以 `ssl_ctx.wrap_socket(server_side=True)` 包裹，`minimum_version=TLSv1_2` + `OP_NO_COMPRESSION`（CRIME）；三监听各自 daemon serve 线程，SIGTERM/INT 全部 shutdown。
  4. **证书体系**：`deploy/gen_certs.py`——自建 CA（RSA3072/3650 天/Critical CA:TRUE）+ 服务器证书（RSA2048/1825 天/EKU serverAuth/SAN=DNS+IP）；**生产路径远端 openssl 生成（私钥不出服务器）**，`build_openssl_cmds` 序列由 deploy.py 执行；cryptography 本地模式仅供开发/测试；CA 指纹 SHA256（hex 小写无冒号）随部署输出；ca.crt 下载本地 deploy/certs/ 供终端打包。
  5. **健康检查加证书过期监控**：`cert_not_after()` 极简 DER 解析（Certificate→tbs→validity→notAfter，兼容 PEM/DER、UTCTime/GeneralizedTime，失败软返回 None）——启动日志输出剩余天数，后续可挂健康 API。
  6. **deploy.py**：新步骤 [5a] 证书（远端生成/轮换 `--rotate-certs`、旧证书备份、指纹提取、ca.crt 下载）+ [5b] config HTTPS 键幂等迁移（缺才补）+ 防火墙 8443/18443 TCP `--permanent`（firewalld 与 iptables 双分支均复核永久配置）+ [8] 健康检查扩展（三端口 ss 监听、`curl --cacert` 自 CA 验证 HTTPS health、跨类 404 远端断言）。
  7. **终端侧协同契约**（net-doctor-dev 已交付 net_service 同构）：CA 资产 `assets/platform_ca.pem`（正式 CA 生成后直接替换文件，终端零代码改动）；指纹字段 `uplink_config.server_ca_fingerprint`（SHA256 hex 小写无冒号，空=跳过层1 仅 TLS 验签）；CA 定位链 `NETDOCTOR_CA_PATH 环境变量 → _MEIPASS/assets → 脚本目录 assets`（全缺 https fail-closed ca_missing）；端口不写死全由 `uplink_config.server_url` 下发；uplink.py（心跳/注册/命令通道）https 同构改造由 perf-analyzer-dev 执行。
- **验证门禁**（全绿）：py_compile 全部改动文件 / 单测 `tools/test_https_split.py` **27 项**（scope 白名单 11 + dispatch gate 6 + tls_settings 解析 6 + context/notAfter 4，含 notAfter≈1825d 与垃圾输入软失败）/ 冒烟 `tools/smoke.py` **55/55**（legacy 主流程 48 项不受扰 + [13] HTTPS 分离组 7 项：TLS 终端口真实心跳 200、跨类 404 双向、TLS 管理口登录/会话 200、legacy 并行 200；本地隔离环境 ETP_CONFIG + 临时 data_dir + 配对 CA，iperf 组按既有约定 ETP_IPERF_FAKE=1）。
- **回滚**：终端改回 http://172.17.5.215:18090 秒级生效（过渡期 legacy 常开）；服务端回滚 = `legacy_http.enabled=true` 保持 + `tls.enabled=false` 重启（或整体回退上一版 app.py/api.py + 旧 config）。
- **约束**：正式 CA 换装时同步三处——服务器 data/certs/*（deploy --rotate-certs）、终端 assets/platform_ca.pem、终端 uplink_config.server_ca_fingerprint；config.json 迁移只补缺键绝不覆盖既有值；生产部署须 main 批准后执行（只读冒烟→真实终端心跳→管理端 8443 登录三段验证）。
- **生产部署记录**（2026-09-11 18:27，main 批准执行）：三监听上线（18090/18443/8443），备份 pre_20260911_182701，防火墙 --permanent 双重复核，config 幂等迁移（llm.* 未触碰）。**正式 CA SHA256=733c1039f0e5be612911ec68041fd9d4586afb6ef88dcf8d9f7383702126010b**（终端 uplink_config.server_ca_fingerprint 同值；ca.crt 已下载 deploy/certs/ 并由 net-doctor-dev 写入主应用 assets/platform_ca.pem，指纹 MATCH）。
- **生产排障三课（commit abb2978）**：
  1. **CA 畸形（重复扩展）**：`openssl req -x509` 会叠加发行版 openssl.cnf 默认 v3_ca 模板与 `-addext`，产出 SKI×3/BasicConstraints×2 的畸形 CA——OpenSSL 1.1.1k(FIPS) 链验证拒之（error 20 unable to get local issuer），OpenSSL 3.x 容错放行（本地全绿掩盖了问题）。修复：CA 一律 `req -new`（CSR 不携带扩展）+ `x509 -req -signkey` 自签 + extfile 单一扩展来源。**教训：证书扩展必须单一来源；"本地验证通过"≠跨 OpenSSL 版本通过，生产验证必须在与服务端同款 OpenSSL 的客户端上做**。
  2. **服务端必须发送完整链**：`load_cert_chain(server.crt)` 只发叶证书，1.1.1 客户端对「仅叶 + 自签根锚」组合同样报 unable to get local issuer（`--cacert fullchain` 反而通过暴露了该语义）。修复：gen_certs 产出 fullchain.crt（叶+CA），`tls_settings` 未显式配 cert 时优先 fullchain。
  3. **TLS 探活的 SAN 配对**：服务器侧 curl 探活必须连本机 IP（SAN=172.17.5.215），连 127.0.0.1 会被主机名校验正确拒绝——与 spike-1 教训同源，健康检查已固化用 host 连接。
- **终端侧换装验证**（net-doctor-dev，eeb5463/c918a48）：assets/platform_ca.pem 指纹 MATCH；NETDOCTOR_CONFIG_DIR 隔离 + 正式指纹真打 https://172.17.5.215:18443——TLS 握手+内置 CA 验签通过，401 invalid token（假 token 预期）证明 TLS→HTTP→鉴权全链路通；CA 定位链补「仓库根 assets」fallback。生产冒烟 53/55（HTTPS 分离组 7/7 全绿；2 个白名单测试项为与生产数据的边缘交互，非 HTTPS 问题）。health 探针统一口径：三端口均为 GET /api/v1/health → 200。
- **端口演进记录（2026-09-19 00:21，main 批准 B1，非推翻原方案而是演进）**：管理口 8443 → **443**。因果：09-11 拍板 8443 在先；09-19 用户以「https://172.17.5.215/」直接访问验证"改造未生效"——实为端口记忆错位 + 证书未导入 + 入口未迁移三层收口缺口（审计结论：双 HTTPS 本身在位且健康），用户真实期望是**不带端口**的管理入口。B1 零代码方案：config `console_port: 8443→443` 一键 + systemd unit `[Service]` 增 `AmbientCapabilities=CAP_NET_BIND_SERVICE`（User=eyeterm 非 root 可绑特权口）+ 防火墙 443/tcp `--permanent`（runtime/permanent 双态复核）。8443 退役判定：上线至退役零用户流量、零存量依赖；其防火墙条目**保留**至全量终端迁移批次随 18090 下线一并清理（main 指示）。执行与验证证据（_b1_apply.py，含 18090/18443 失活自动紧急回滚预案）：备份 config.json.bak.20260919_002148 / terminal-platform.service.bak.20260919_002148；restart 后 18090(legacy)/18443(tls-terminal)/443(tls-console) 三监听、TLS1.2 ECDHE-RSA-AES256-GCM-SHA384 握手 + TLS1.0 拒绝、console 页 200 / console API 未认证 401 / 跨类 POST 404、证书剩余 1817 天；在线 3 终端 last_seen 全部 RECOVERED（心跳 ≤2 拍自愈）；M720t 07:30 WoL 链路不受扰（wol_schedules 持久化，重启后调度器重新武装）。管理员侧配套：导入 ca.crt 至受信任的根证书颁发机构后访问 https://172.17.5.215/ 无告警。

## ADR-033 ｜ 火绒终端安全管理系统集成（只读镜像 + 周期同步 + 破坏性硬门禁） ｜ 已接受

- **背景**：EyeTerm 新增「终端安全」维度（与资产/网络监测/AI 诊断并列）。火绒企业版 API（HTTPS 自签，HRESS 签名）经试点全量实测打通：89 分组 + 710 终端（4 页 × 200 拉完），签名算法四歧义点（Header 模式无效/资源路径无前导斜杠/quote 默认 safe='/'/expires=+86400）以官方参考脚本实测定案（详见 docs/huorong_integration_plan.md §5.3/§7.1）。P1 实施获用户批准。
- **决策**（2026-09-16 实施，P1 后端全链）：
  1. **模块**：`server/huorong.py`（新）——`HuorongClient`（URL 参数签名/紧凑 JSON body/Content-MD5/分页迭代 total 语义+短页+连续空页+max_pages 三重防御/errno 归一化 HuorongApiError/errno=3 与 HTTP 5xx 与网络类重试 ≤2/limit 收敛官方上限 200）+ `mirror_rows` 镜像归一（local_ip 为主 IP、is_online 归一、last_connect_time epoch 与字符串双兼容、分组 total/online 快照由终端聚合）+ `HuorongSyncer`（周期同步器）。
  2. **凭据**（settings 键位）：`huorong.url` / `huorong.ak` / `huorong.sk` / `huorong.enabled` / `huorong.sync_interval_sec`（默认 300）/ `huorong.tls_fingerprint`（预留）。**huorong.ak + huorong.sk 入 SENSITIVE_KEYS**（SecretsBox 加密落库、控制台脱敏零回显；ak 属标识符加密为超面保护）。真实凭据由 main 部署时经 config_cli --stdin 注入，代码零硬编码零落盘。
  3. **存储镜像**（store 新三表）：`hr_groups(group_id PK, name, parent, total, online 快照)` + `hr_clients(client_id PK, name, computer_name, ip, connect_ip, mac, group_id, online, os, version, last_seen, updated_at)` + `hr_sync_log`（同步审计）。写入=**全量整体替换单事务**（天然幂等，火绒侧删除同步消失）；控制台**只读缓存，绝不透传火绒实时请求**（响应时延稳定 + 无请求风暴）。
  4. **同步器**：进程内 daemon 线程（默认 300s，可配 60-3600s）+ 手动触发共用互斥锁（非阻塞获锁，忙返 busy）+ 失败指数退避（interval×2^连续失败，封顶 3600s）+ **认证失败（errno=1）连续 3 次暂停周期同步**防凭据锁死（手动成功自动恢复）+ 启动 8s 短延迟避让主服务装配。
  5. **只读 API**（契约冻结，console scope，X-ETP-Console-Token）：`GET /api/v1/console/huorong/overview`（groups_count/clients_total/online/online_rate/win7_eol_count/last_sync）｜`GET /groups`｜`GET /clients?group_id=&online=&q=&page=&page_size=`（q 按 name/computer_name/ip/mac LIKE 转义；IP/MAC 真实值不脱敏——控制台为可信管理员界面）｜`POST /sync`（**admin-only**，operator 403+审计；同步中 409；未配置 400）。
  6. **破坏性硬门禁**（红线）：任务类接口（查杀/隔离/通知 /api/task/_create）在客户端层 `enable_tasks=False`（默认）时一律抛 HuorongTaskDisabled，且**本模块不提供任何任务下发路由**；未来开启（S6）必须 main/用户审批 + 会话鉴权 + 目标白名单 + 操作留痕，禁止自动触发。
  7. **TLS 自签**：默认 CERT_NONE（仅隔离内网联调可接受）；`huorong.tls_fingerprint` 配置后对端证书 DER SHA-256 pin（fail-closed：取不到证书即拒绝），SPKI pin 留后续增强。
- **签名金向量**（实测算法固化回归基线）：AK=AKTEST1234567890 / SK=SKTEST-secret-key / expires=1726358400 / 空 body（Content-MD5=1B2M2Y8AsgTpgAmY7PhCfg==）/ resource=api/group/_list → sign=`X0MbXEIjPFPZtNPXMjGfwbe%2Bcmk%3D`（quote 后）。
- **验证门禁**（全绿）：py_compile 全部改动文件 / `tools/test_huorong.py` **80 项**（金向量 5 + mirror_rows 归一 11 + mock HTTP 冒烟 12【签名由 mock 服务端独立长算实现校验，零失配断言】+ errno 四分支与重试次数 + 分页四语义 + 破坏性门禁与全测程零任务调用 + TLS pin 三态 + 同步幂等/镜像收敛/过滤转义/退避/认证锁死/互斥 busy 22 + 路由契约/scope 跨类 404/admin 鉴权 17 + settings 键位 7）/ 回归 10 套件：https_split 27 + ipconflict 16 + deep_engine 40 + ai_ipconflict 30 + ai_routetrace 27 + ai_diagnose 62 + whitelist 12 + switch_admin 24 全绿（nad_client 36/1 的「平台内证据保留」为基线先在失败，HEAD 版本复现，与本改动无关）/ 生产真实 API 只读同步一轮验证待凭据注入后执行。
- **已知限制**：①overview.win7_eol_count 以 os LIKE '%Windows 7%' 近似（试点 59 台 Win7 旗舰版口径）；②hr_sync_log 无自动清理（低频审计表）；③last_connect_time 若遇未知格式静默置 NULL（防御性）；④同 MAC 多记录全保留（client_id 主键），EyeTerm 资产映射的「同 MAC 取最新」在 P2 消费侧实现；⑤周期同步线程未做分布式锁（单实例部署，ADR-002 边界内）。
- **教训沉淀**：同类国产安全设备对接，官方文档签名描述与实现存在多处歧义——**优先索取官方参考测试脚本**，以实测为准（试点 31 变体排查档案：docs/attachments/huorong_auth_forensics_guide.md）。
- **增补（2026-09-16，用户需求变更：取消独立「终端安全」菜单 → 与资产管理页融合）**：
  1. **自动关联引擎**（store.hr_relink，每次 hr 同步成功后执行，幂等）：匹配键优先级 **MAC → IP → 主机名**，任一键命中多候选即歧义回落下一键，全歧义不关联；MAC 归一=大写去分隔符，平台侧取 terminals.asset_detail.network[]（缺则 hwinfo 回退）**逐网卡**任一命中，火绒侧同 MAC 多条取 last_connect_time 最新参与 MAC 键（旧条目仍可用 IP/主机名键）；IP=local_ip 优先、connect_ip 次之 vs 平台最近上报 ip；主机名 computer_name vs hostname 不区分大小写。manual 映射不被自动覆盖且其两端不参与自动分配。
  2. **新三表**：`hr_terminal_map(hr_client_id PK, terminal_id UNIQUE, match_type[mac/ip/hostname/manual])` 1:1 映射；`hr_link_ignore(client_id, terminal_id PK)`（用户解除过的配对，自动 pass 永久跳过——解除不拉回语义）；`hr_group_override(hr_client_id PK, group_id)`（手动调组，同步时镜像写入后**再应用**不丢失）。hr_clients 增列 `native_group_id`（火绒原生分组快照，group_id 为覆盖生效值，解除覆盖可还原；原生组已从火绒侧消失则置 NULL）。
  3. **统一视图 API**（console scope，operator 可用）：`GET /api/v1/console/assets/groups` → 两段树（platform_groups 现有资产组体系 + huorong_groups 89 组含覆盖后 total/online/matched + other 未关联平台终端计数）——两套 group id 命名空间不混排；`GET /api/v1/console/assets/terminals?group_source=(platform|huorong)&group_id=&q=&page=&page_size=` → 统一条目 {kind: matched/huorong_only/platform_only, match_type, platform:{概览摘要}, huorong:{...win7_eol}}，「其他」= huorong 视角 group_id="other"（装 platform_only），q 跨两侧标识字段。
  4. **手动操作**：`POST /assets/assign-link {huorong_client_id, terminal_id|null}`（manual 抢占 auto；撞其它 manual 409；null 解除+写 ignore；operator+audit）；`POST /assets/assign-group {client_id, group_id}`（0/null 解除覆盖回原生；悬空组跳过应用；不动关联映射）。
  5. **原 4 端点与同步线程保留**（/console/huorong/* 供资产页头部复用）；hr_sync_log 增列 matched_n；sync_once 返回增补 matched/override_applied/relink 统计（关联率报告数据源）。
  - **验证门禁增补**（全绿）：`tools/test_huorong_link.py` **50 项**（三分支/多网卡/同 MAC 取最新/歧义回落/connect_ip 次选/幂等/manual 抢占与 conflict/ignore 不拉回/覆盖持久与还原/悬空跳过/统一视图三类 kind+双视角+「其他」+q 范围+win7_eol/路由契约+鉴权 409·400 语义）/ 回归 test_huorong 80 + https_split 27 + ipconflict 16 + deep_engine 40 + switch_admin 24 + whitelist 12 + sysadmin 零失败。
  - **已知限制增补**：①matched 条目在双视角均出现（平台组视角 + 火绒组视角），UI 按 kind 渲染；②解除覆盖回原生依赖 native_group_id（本轮起镜像才有该列，存量库首次同步自动补齐）；③关联率报告待生产凭据注入后首轮同步产出。
- **增补二（2026-09-17，资产右键「第三方数据源信息」弹窗·火绒块数据层）**：
  1. **API 事实**：官方 /api/clnts/_virus_events 为**统计聚合口径**（type=2 全量一次拉全部终端的 count + success/fail/ignored/trusted 分布）——**无病毒名/事件时间/查杀类型明细流**，弹窗「病毒状态」只能给统计快照，明细维度属官方接口能力边界（后续若用户坚持明细需另行评估火绒日志导出类接口）。
  2. **病毒统计镜像**：新表 `hr_virus_stat(client_id PK, total, success, fail, ignored, trusted, snapshot_ts)`，sync_once 主镜像成功后同轮拉取（type=2 分页全量），失败不阻断主同步（result.virus_sync=failed 标记 + 日志）；行归一 `virus_row` 键名宽容解析（count/total/success/fail/ignored/trusted 变体），真实响应键名以生产首轮校准。
  3. **弹窗数据端点**：`GET /api/v1/console/huorong/context?terminal_id=`（只读缓存，operator）→ store.hr_context_block 聚合：{linked, match_type, bound_ts, client{...}, group_id, group_name, **group_path**（父链上溯反序拼「A/B/C」，环/断链防御 max_depth=10）, virus|null}；未关联/映射悬空一律 {linked:False}（悬空下轮 relink 自愈）。
  - **验证门禁**：test_huorong.py 增病毒镜像成功路径（FakeClient.iter_virus_stats）仍 80/80；test_huorong_link.py 增 [7] 段 **60/60**（行归一/键名宽容/空 client_id 拒绝/组路径父链/病毒并入/无快照 null/未关联/路由 200·400·401）。

## ADR-034 ｜ 控制台「终端安全」区块 UI（火绒缓存快照只读视图） ｜ 已接受

- **背景**：火绒 P1 后端（ADR-033）由 huorong-dev 并行实施，控制台 UI 按 huorong-dev 定稿契约先行开发（桩数据驱动，后端未就绪不影响 UI 交付与验证）。UI 只改 console/index.html + tools/e2e_console.py，与后端文件零交叉。
- **决策**（2026-09-16 实施，server-platform-dev）：
  1. **导航与布局**：tabbar「资产管理」后新增「终端安全」页（data-page=security）。概览 4 m-card（终端总数/在线终端+在线率小字+bar/安全分组/Win7 已停更——>0 加 v-err 红色）+ 「最近同步 + 缓存快照提示 + 立即同步」行；主体 hr-split 两栏（≤1080px 单列降级）：左 300px 分组树 + 右终端表。
  2. **分组树**：全量 89 组按 parent DFS 缩进渲染（parent 0/null/缺失/孤儿一律按根防御），组项显示 名称 + 在线/总数计数；「全部分组」首项显示平台级 online/total；组名搜索框前端过滤；i18n 键残留兜底映射（i18n:db_groups_name:ungrouped→未分组）；点击 data-gid 事件委托（不拼 onclick 字符串，规避注入）。
  3. **终端表**（契约字段 name/os/last_seen/group_name 直供）：名称/计算机名/IP/MAC/分组/系统（去 Microsoft 前缀短显+title 全文）/版本/状态徽章（绿 b-on 在线、灰 b-gray 离线）/最后在线；os 含 Windows 7 加红色「已停更」徽章（b-eol）；组筛选（点组）+ 在线筛选（全部/仅在线/仅离线）+ 关键字搜索（名称/IP/MAC，回车或按钮）+ 分页（page_size=20，上一页/下一页 link-btn）。
  4. **同步交互**：立即同步按钮 hr-spin 转圈+disabled；成功 toast「同步完成：分组 x · 终端 y」+ hrLoad 全量重载；409（sync in progress）toast「同步进行中，请稍候」；其余错误原样 toast（apiFetch 口径）。
  5. **未就绪降级**：overview/groups 404 或网络失败 → 区块整体切「终端安全 · 同步服务未就绪」卡 + 重试按钮（hrMain 隐藏）；详细错误仅 console.warn（UI 文案零实现细节红线）；切回该页自动重试一次。
  6. **代码隔离**：hr 前缀函数/变量（HR_PAGE_SIZE/hrGroups/hrSelGroup 等），CSS 独立 .hr-* 类 + .b-eol/.b-gray 徽章类，不触碰既有命名空间；ES5 语法一致（function/var，无箭头/match）。
- **验证门禁**（全绿）：esprima 解析 79.4KB 内联脚本块 / py_compile e2e 脚本 / Playwright E2E **54 项**（既有 31 项零回归 + 新增 23 项终端安全断言：概览数字渲染 710/152/89/59 + 在线率 21.4% + Win7 v-err 红卡 + 最近同步渲染 + 分组树 90 项（全部分组+89）+ Win7「已停更」徽章 + 灰离线徽章 + 组筛选 8 台且组名直显 + 全量分页 36 页翻页第 2 页 + 在线筛选 142 台全绿无灰 + 名称搜索 1 台 + IP 搜索 1 台 + 同步 409/200 双路径（toast+按钮恢复+概览重载）+ 未就绪降级视图 + 主视图隐藏）+ pageerror=0。桩数据 Playwright route 拦截按 huorong-dev 契约同构（online_rate 0~1、clients 字段 name/os/last_seen/group_name、sync 409/200）。
- **联调待办**：huorong-dev 后端就绪后真实 API 冒烟（E2E 桩已与契约同构，真实链路仅需替换 route 拦截为真服务）；部署由 main 统一安排。
- **已知限制**：①同步按钮转圈态的时长断言在 Playwright sync route handler 中不可靠（handler 内 sleep 阻塞事件循环），改为 409/200 双路径行为断言覆盖同一代码闭环；②组树不做折叠（89 组量级滚动+搜索可达）；③分页组件为最简上一页/下一页（无页码跳转）。

## ADR-035 ｜ 资产管理页融合火绒安全视图（取消独立「终端安全」菜单） ｜ 已接受

- **背景**：用户需求变更（P1 收官后）——独立「终端安全」菜单取消，火绒能力融入「资产管理」页：火绒 89 组进资产分组体系、平台注册终端自动关联火绒终端（概览挂载）、未匹配归「其他」、支持手动关联与调组。后端 `/console/huorong/*` 4 端点与同步线程保留不动（ADR-033），新增 `/console/assets/*` 4 端点（huorong-dev，commit 9ab89ac，契约冻结）。
- **决策**（2026-09-16 实施，server-platform-dev）：
  1. **导航与降级语义**：tabbar 移除「终端安全」+ page-security 整块删除；资产页头部迁入概览 4 卡 + 最近同步 + 立即同步（数据源不变 huorong/overview·sync）。融合页不整块降级：概览/组树/条目三区独立失败静默置空态 + console.warn（资产主功能不因火绒不可用受损）。
  2. **左栏两段树**：平台资产组树（原 agTree 功能不变）+ 「安全分组」段（89 火绒组按 parent DFS 缩进 + total/online/matched 计数 + i18n 键兜底「未分组」+ 组搜索）+ 「其他」虚拟组固定收尾（data-gid=other，计数 = other.platform_total）。两段选中互斥（agSelected/azSel），再点已选组回总览。
  3. **三类条目卡**（/console/assets/terminals，kind 驱动）：①matched = 平台摘要卡（terminal_id/IP/系统/CPU/内存/磁盘/GPU/客户端，取 platform.asset 子对象）+ 火绒增补行（MAC/安全分组/安全版本 hr_version/最后在线 + win7_eol「已停更」徽章）+ 头部匹配方式徽章（自动匹配/手动关联）+ 操作区；②huorong_only = 精简卡（计算机名/IP/MAC/系统/安全分组/安全版本）+「终端未接入平台」空态 + 操作区；③platform_only = 平台摘要卡，无火绒操作（调组沿用既有组树+绑定体系）。分页 20/页 + 关键字搜索（q 跨两侧标识字段）。
  4. **手动操作**：「指定关联终端」弹窗（单选平台终端列表 + 搜索 + 当前关联标记；manual 抢占 auto 由后端裁定）、「解除关联」（卡片直触发 uiConfirm；解除=terminal_id null，ignore 语义由后端保证不拉回）、409 冲突（撞其它 manual）透传后端文案；「调整分组」弹窗（组树选组器单选 + 「跟随源分组」= group_id 0 解除覆盖）。操作后联动刷新（azLoad + 条目 + loadAssets）。
  5. **uiConfirm 组件**（新交互规范）：居中确认弹窗（Promise 风格 uiConfirm(title,text,okText)→bool、防重入、遮罩/取消/确定三出口），替代原生 confirm 用于本次全部新交互；存量 confirm（agDelete 等）不在本次范围。
  6. **代码组织**：hr 前缀收缩为概览/同步/判定工具（hrRenderOverview/hrSync/hrGroupName/hrIsWin7/hrShortOs），融合逻辑全部 az 前缀（azLoad/azRenderTree/azLoadEntries/azRenderEntries/azOpenLink/azOpenGroup 等）；卡片操作走 data-az-* 事件委托（不拼 onclick 字符串）。
- **验证门禁**（全绿）：esprima 90.8KB 内联脚本 / py_compile / Playwright E2E **65/65**（既有 31 项零回归 + 新增 34 项融合断言：导航零残留×2、概览迁移×5、组树 89+其他+i18n 兜底×3、三类卡渲染与计数×9、分页×2、搜索×1、手动关联弹窗×2、uiConfirm 解除×2、调组×2、「其他」视图×1、选中互斥×1、同步 409/200 双路径×4、pageerror=0）。桩数据与冻结契约同构（kind/match_type/hr_version/win7_eol/asset 子对象/other 视角 group_id="other"）。
- **联调待办**：huorong-dev 后端 9ab89ac 已就绪，真实 API 冒烟由双方协商后部署统一安排。
- **已知限制**：①platform_only 条目无卡片级调组入口（沿用平台组树+绑定弹窗既有路径）；②匹配方式徽章二态展示（自动/手动），mac/ip/hostname 三种自动键不细分；③条目卡平台字段为列表摘要（asset 子对象），全量明细仍走「终端监控」页。

## ADR-036 ｜ 终端桌面管控服务端模块（壁纸资源库 + 四类策略 + 匹配推荐 + 下发记录） ｜ 已接受

- **背景**：desktop-policy 专项（P0 spike 已过，CONTRACT.md v1.1 冻结，双方裁定采纳 server-platform-dev 三项建议）。本侧承接 P1 服务端实现：server/desktop_policy.py（新，stdlib-only，kb_store 惰性单例形态）+ terminal/console 两组路由 + 控制台「桌面管控」UI。
- **决策**（2026-09-16 实施）：
  1. **壁纸资源库**：上传 `PUT /console/desktoppolicy/wallpapers/{name}?category=&mime=`（v1.1 裁定 raw bytes 替代 multipart，body=图片原始字节，端点层 10MB 上限）；PNG/JPEG 纯 Python 头解析（PNG IHDR 偏移读取 / JPEG SOF 段扫描）；sha256 去重（重复内容 400）；文件落 data/desktop_policy/wallpapers/{id}.{ext}；下架引用保护（策略 payload 引用中 409）。
  2. **四类策略**：desktop_wallpaper（mode 枚举 + wallpaper_ids 池 + rotation 参数）/ lock_screen（wallpaper_id）/ power_plan（plan 枚举 + custom 六项 60~86400s 与电源键枚举）/ idle_lock（minutes 1~1440）；payload 全类含 enabled 布尔（显式启停语义）。wallpaper_id 全链整数（v1.1）；per_monitor item 与 lock_screen 下发 checksum（sha256，供终端防坏缓存）。
  3. **匹配推荐（服务端执行）**：exact（宽高全等）→ aspect_higher_res（宽高比容差 ±0.02 且不低于目标屏，取面积最小最贴近）→ default_fallback（default 分类优先 + 告警）；多命中取面积最小、平局取 id 大（最新）。
  4. **发布与生效**：revision 全局单调（publish = max+1，保证 report (revision, terminal_id) 唯一定位）；dp_policies.group_id 绑定平台 asset_groups 且 **NULL=全部终端**（v1.1 裁定；生效策略 = 组内精确优先 → 全局兜底，各取 enabled 且 revision 最大）；publish 仅 bump revision，dp_deliveries 行在终端拉取时 upsert（pending→delivered，终态不因重复拉取重置）。
  5. **report 状态机**：applied（全 ok 无告警）/ warn（全 ok 但含 default_fallback 或 blocked_by_security）/ partial / failed；error_code 记首个非 ok 码或告警码；error_detail 存证 results/monitors/virtual/session_type 原始 JSON。
  6. **console 8 端点**（overview/wallpapers GET+PUT+DELETE/policies GET+POST/{id}/publish/deliveries；POST/DELETE admin-only 403+审计）+ **terminal 3 端点**（policy?revision=&mi= / wallpaper/{id} 带 X-DP-Checksum 头 / report）；_terminal_api 签名扩展 query 参数（终端 GET 首次需要 query）。
  7. **控制台 UI**：导航「桌面管控」（AI 分析后）；概览 5 卡（壁纸/策略/7日下发/告警/拦截——warn 黄色、blocked 红色告警位，blocked_by_security「终端安全软件拦截」中文徽章）；壁纸库（上传表单 file+名称+分类 + 列表 + 分类筛选 + 下架 uiConfirm）；策略（列表 rN 版本徽章 + 编辑弹窗四类分节表单——壁纸池勾选/锁屏单选/电源计划 custom 联动展开/超时锁屏 + 发布 uiConfirm）；下发记录（状态/错误码/终端三重筛选 + 中文徽章映射 + 详情 JSON 弹窗 + 分页）。
- **验证门禁**（全绿）：py_compile / esprima 106KB / `tools/test_desktop_policy.py` **53 项**（图片解析/壁纸库 CRUD 与引用拒绝/匹配三分支/校验 400 矩阵/发布与生效策略/终端拉取 upsert/report 状态机四态/overview）/ `tools/smoke_desktop_policy.py` **31 项** mock 冒烟（上传 raw bytes/400 矩阵/发布/拉取 checksum/下载/report 状态机/筛选/overview/operator 403 矩阵/页面 200，**二次运行幂等验证通过**——随机尾字节壁纸 + reset-password 口令重置）/ Playwright E2E **83/83**（既有 65 零回归 + 新增 18 项桌面管控断言）+ pageerror=0。
- **已知限制**：①rotation 为策略参数原样下发，P1 无服务端轮换调度（契约未定义触发机制，壁纸池轮换留 P2/P3 增强）；②策略修改不自动 bump revision（需显式发布）；③deliveries 无自动清理（低频表）；④operator 账户由冒烟脚本幂等重建（本地开发库）。

## ADR-037 ｜ 控制台宽度自适应整改（STYLE.md §9 全局规范落地） ｜ 已接受

- **背景**：用户定全局规范「功能模块宽度必须自适应分辨率」（STYLE.md §9）：流式布局禁固定像素、多栏模块降级断点、E2E 多宽度断言。AI 分析页截图实证 ~1080px 视口双栏挤压。
- **整改**（2026-09-16 实施）：
  1. **AI 分析页**：ai-split 降级断点 1080→1200（双栏最小内容宽 460+380+gap，1080 恰卡边界导致挤压，上调 120 保证详情列可读）。
  2. **页宽统一**：nettest/config/kb/sysadmin 四页主体 max-width 1180→1400（规范建议用大断点而非写死窄值；AI/桌面管控/资产已为 1400 或 none）。
  3. **tabbar**：加 overflow-x:auto（8 页签窄视口横向滚动不溢出）。
  4. **资产管理融合页**：aside+main 既有 flex 布局 + 860px aside 收窄断点，无固定像素宽，审计通过无需改动；桌面管控页（ADR-036）开发即按 §9 执行（cards grid 流式 + flex-wrap 表单行）。
- **E2E 多宽度门禁**（新增 10 项）：1920/1440/1080 三档视口循环——①无横向滚动条（scrollWidth≤clientWidth）②AI 页降级断点生效（≤1200 单列 1 列、>1200 双栏 2 列，computed gridTemplateColumns 列数断言）③主体容器宽度随视口变化（流式非固定）④三档宽度互异防固定值回归。
- **验证门禁**（全绿）：Playwright E2E **83/83**（既有 73 零回归 + 多宽度 10 项）+ pageerror=0 + esprima。

## ADR-038 ｜ power-control P0 只读快照服务端增量（power-control-dev 实施） ｜ 已接受

- **背景**：自动开关机专项 P0——终端采集自动开关机能力快照（capability/vendor_line/BIOS RTC 等）上报服务端存档，控制台按终端查询。power-control-dev 直接在 server-platform 仓实施最小增量（ADR-005 v2 快照形状，接口登记 SRV-080/081，详 power-control/docs/ARCHITECTURE.md §4）。
- **增量**（2026-09-16，server-platform-dev 核查后入库）：`server/power_control.py`（新）——PowerControlStore 自持 WAL 连接 + power_snapshots 表（terminal_id/collected_ts/schema_ver/capability/vendor_line/snapshot/created_ts + 倒序索引）save/latest/history + PowerControlError；app.py `ctx.pc` 挂载；api.py terminal 6 段分支（POST /terminals/{tid}/powercontrol/snapshot）+ console dispatch 钩子 `_console_powercontrol`（latest 单条 / 历史列表 limit≤500+since）。零第三方依赖（ADR-002），纯追加零迁移脚本。
- **核查与共存验证**（server-platform-dev）：基于 ADR-036 提交后无行级冲突；py_compile 三文件通过；同服务双模块冒烟共存全绿（desktop-policy 31/31 + power-control 10/10）。控制台 UI（SRV-081，终端监控详情卡 + 时间线弹窗，capability 枚举只增+未知值中性兜底）排期第三批，待前两项联调完成后实施。
- **部署**：与火绒融合 UI（258a78e）+ 桌面管控（bb4f248）合并同批，一次重启生效，无迁移脚本。

## ADR-039 ｜ 资产管理页终端卡片详情（复用资产明细渲染 + 机型/心跳间隔/电源快照增补） ｜ 已接受

- **背景**：用户需求——资产总控页终端卡片可点击查看详情，内容涵盖客户端「终端概览」三卡（终端配置/硬件状态/本地网络）。main 已核对数据全部已有（asset schema1 + metrics + terminals 列 + power_snapshots.machine），终端侧零改动。
- **决策**（2026-09-16 实施）：
  1. **渲染复用**：直接复用终端监控页 assetModal 渲染器 renderAssetModal（终端配置/硬件状态——使用率徽章/分区容量条/温度/GPU 明细/硬盘/内存条 SPD——本地网络三卡全量，防御性渲染完整），弹窗加宽 760→860、标题改「终端详情」；终端监控页「资产明细」按钮同步改走新入口（同一渲染+增补，两页一致）。
  2. **增补三项**（renderAssetModal 加 extra 可选参，向后兼容；占位 id data-ad-machine/data-ad-hb/adPowerHolder + adPatch 代际守卫异步回填，防串台）：①机型——power-control 最新快照 machine（manufacturer/model/system_family 拼接，SRV-081 提前合并交付），取不到显示 --；②心跳间隔——/metrics?minutes=120 相邻采样差中位数（「约 N 秒」，服务端观测值；客户端配置值不在服务端契约，列为 schema v2 gap 建议）；③电源策略快照折叠卡——capability 中文徽章（企业可配置/不支持自动控制/未知值原文兜底）+ 采集时间/厂商线/RTC 自动开机/来电策略。
  3. **点击热区**：资产总览卡（loadAssets）+ 融合条目 matched/platform_only 卡（az 卡）加 data-tid 热区（事件委托 adBindCards/azBindCards 扩展，不拼 onclick 字符串；卡内操作按钮优先命中不受冒泡影响）；huorong_only 卡无平台终端不设详情热区。
- **验证门禁**（全绿）：esprima 110KB / Playwright E2E **91/91**（既有 83 零回归 + 新增 8 项：卡片打开详情/三区块渲染/机型回填/电源快照卡/心跳间隔填充/asset=null 降级摘要/1080 弹窗不溢出）+ pageerror=0。生产部署方式：console/index.html 单文件热更（与 13a05c5 同文件合并生效）。
- **gap 建议（schema v2，转 main 转派终端侧）**：①心跳间隔为服务端观测推断，客户端配置值（上报周期）建议进 asset 或注册字段；②机型（Manufacturer/Model/SystemFamily）现依赖 power_snapshots 快照（需终端上报过才有），建议 schema v2 直接入 asset.machine 免依赖第三方模块数据。

## ADR-040 ｜ 平台侧「开关机管控」（定时开关机策略批量下发） ｜ 已接受

- **背景**：用户需求——已注册终端定时开关机配置查看 + 在线终端下发 + 批量维护。协议骨架 main 定稿：下发复用 ADR-015 命令通道（pc_apply_policy），批次表 + per-terminal 状态机，回执走既有 commands result 链路。终端侧执行器由 power-control-dev 并行实现（P1 写入引擎远程触发面），真机联调待其交付。
- **决策**（2026-09-16 实施，commit 3fe299e）：
  1. **命令准入**：console 下发白名单追加 `pc_apply_policy`（timeout_sec=604800——pending 排队窗口 7 天+300s，离线终端上线心跳拉取即天然补投；回执窗口同长无害）；回执分支钩子 `pc.on_command_result`（幂等更新，异常不阻断命令链路，commands 表留原始记录）。
  2. **批次存储**（power_control.py 追加两表，自持连接同款形态）：pc_policy_dispatch（policy_id UNIQUE/操作员/payload/时间）+ pc_policy_targets（dispatch_id+terminal_id UNIQUE/command_id/status/error_detail/result_json）。状态机：pending（在线待拉取）→ offline_queued（离线入队，上线补投）→ success / rejected（capability=not_supported 或 result.rejected）→ failed（steps 逐项错误拼接 error_detail）；查询态 converge：关联命令 timeout 且未终态 → expired（展示态，枚举只增）。
  3. **console API**（operator 可用 + auth.audit "powercontrol.dispatch" 留痕）：POST /console/powercontrol/policies/dispatch（terminal_ids ≤100 去重/终端存在校验/boot+shutdown 形状校验矩阵——enabled 布尔、mode 枚举、HH:MM、weekly 7 位 weekdays、single YYYY-MM-DD、至少一项）/ GET dispatches（批次列表附终态计数）/ GET dispatches/{id}（详情含 targets）。_console_powercontrol 签名扩展 body/client_ip。
  4. **控制台 UI**：导航「开关机管控」（桌面管控后）。终端列表（分页 20/页 + 全选/复选 + 在线 dot + 能力徽章 + RTC 定时开机摘要——当页逐台懒拉 latest 快照，勾选态跨重绘保留）+ 策略表单（定时开机/定时关机两张子卡：启用开关 + daily/weekly/single 模式联动字段 + 周勾选）+ 批量下发（uiConfirm 列明台数与动作摘要 + **「写入定时任务，非立即关机」明确文案** + 离线排队说明）+ 批次记录（列表附成功/失败/排队计数 + 回执详情视图：六态中文徽章 + steps 错误明细列 + 刷新）。
- **验证门禁**（全绿）：py_compile / esprima 119KB / `tools/smoke_pc_policy.py` **23 项** mock 冒烟（批量下发全参/400 校验矩阵六项/未知终端 404/批次列表详情/心跳拉命令 policy_id 匹配/回执三态落库含 steps 明细）/ Playwright E2E **100/100**（既有 91 零回归 + 新增 9 项：页签/列表渲染/能力徽章/RTC 摘要/全选下发 uiConfirm 台数与定时文案/批次详情自动展开六态徽章/steps 明细/批次列表行）+ pageerror=0。
- **已知限制**：①rejected 判定依赖终端回执自报 capability/not_supported，服务端不二次校验终端真实现状；②离线排队依赖命令 7 天窗口，超期 converge 为 expired 后需重新下发（无自动重投）；③批次回执无主动推送，UI 手动刷新。
- **增补（2026-09-17，用户四点需求：组筛选 + 手动维护登记 + 勾选口径 + 批次备注）**：
  1. **①资产组加载（纯前端）**：pw 页头部组下拉（复用 /console/asset-groups，排除 root；pw 页先于资产页打开时独立拉取并缓存 agGroups）；过滤=组内直绑终端（**与资产维护既有语义一致：group_id 直绑，不含子组归并**）；默认「全部资产」，分页保留。
  2. **②勾选口径收紧**：仅「在线且能力=enterprise_configurable」的行 checkbox 可勾进入批量下发；离线/不支持/未知（多为离线无快照）禁勾 + title 提示改走手动登记；pwToggleAll/pwDispatch 收集均跳过 disabled（双保险）。
  3. **③手动维护登记（核心）**：pc_manual_config 表（terminal_id PK/boot_json/shutdown_json/note/operator/updated_ts——配置 JSON 与下发 payload 同构，复用 _validate_pc_sched 全量校验矩阵）。端点：GET/PUT/DELETE /console/powercontrol/terminals/{tid}/manual-config（PUT 备注必填 ≥2 字 ≤500、至少启用一项、operator+auth.audit "powercontrol.manual_config" 留痕）+ GET /console/powercontrol/manual-configs（台账列表）。**语义=平台侧登记台账（人工进 BIOS 配置后在平台登记，不下发终端，终端侧零改动）**。前端：行级入口（离线/不支持/未知行显示「手动登记」，已有登记显示「登记」）+ 弹窗表单（开/关机启用+mode+time+date 联动，备注必填）+「RTC 定时开机」列**手动登记优先展示**（「手动登记」徽章 + 摘要；平台下发信息保留在批次记录，不静默互覆）+ 清除登记。
  4. **④批次备注（可选）**：pc_policy_dispatch 加 note 列（PRAGMA 预检幂等迁移，ADR-042 教训复用）；下发表单可选备注 ≤500 字随批次入库，批次列表/详情透出。
  5. **门禁（全绿）**：单测 tools/test_power_manual.py **9/9**（CRUD/覆盖/JSON 往返/删除）/ 冒烟 smoke_thirdparty 模式回归 + E2E **162/162**（158 零回归 + 4：组下拉、禁勾口径、手动登记全流程至 RTC 列手动徽章、批次备注展示）+ pageerror=0 + esprima 150.8KB。

## ADR-041 ｜ 资产管理分组树优化（根节点 + 火绒分组镜像同步 + 展开收起） ｜ 已接受

- **背景**：用户四点需求——资产组默认根节点可选、火绒 89 组层级镜像同步为资产组子树、树节点展开收起（资产组默认展开到二级）、安全分组区默认全部收起。
- **决策**（2026-09-16 实施）：
  1. **数据层**（store.py 扩展，资产域 owner 直改共享层）：asset_groups 幂等迁移三列（source=manual|huorong|root / huorong_group_id / deleted 软删）；`asset_group_ensure_root`（「全部资产」根节点幂等确保，source=root）；`asset_group_sync_huorong(groups, matched_map)`——镜像同步幂等引擎：huorong_group_id 匹配更新（改名/父跟随）、缺失父先子后拓扑创建、库内有映射但火绒侧不含 → 软删（deleted=1 终端挂载保留）；同步后按 matched_map（hr_terminal_map join hr_clients）重挂终端到对应资产组节点；`asset_group_delete` 保护——root/huurong 来源拒绝手动删（root/root、synced/synced）、has_children 检查排除软删行；asset_group_list 过滤软删并返回来源列。
  2. **同步触发时机**：GET /console/assets/groups 惰性幂等触发（每次资产页加载/刷新，响应带 sync 摘要统计）——不挂钩 huorong 同步线程（不碰其模块文件），数据新鲜度滞后 ≤ 一个火绒同步周期；响应即镜像形态。**终端挂载语义**：huorong 同步组内终端归属由火绒镜像决定（含 matched 关联挂载），会覆盖该组终端的手动组绑定；manual 组与 platform_only 终端不受影响（留根/未分组）。
  3. **前端交互**：①根节点「全部资产」置顶（source=root 特殊渲染无操作按钮，点击=查看全部平台终端）②树节点 ▸/▾ 展开收起（agUserCollapsed/agUserExpanded 双集合：浅层默认展开可手动收起、深层（≥2 级）默认收起可手动展开，用户态尊重至刷新）；资产组默认展开到二级（根+一级子可见）③安全分组区默认全部收起（仅顶层可见，azUserExpanded 集合，点击 ▸ 展开）④huorong 来源节点屏蔽「改/删」按钮（跟随镜像，仅保留＋添加子目录）。
  4. **azLoad/azLoadEntries 失败 toast 静默化**（对齐 ADR-035 三区独立静默原则，console.warn + 空态重试文案——消除与删除成功 toast 的覆盖竞态）。
- **验证门禁**（全绿）：`tools/test_asset_sync.py` **16 项**（根确保/建三/层级链/来源标记/matched 挂载/幂等重同步/改名跟随/软删隐藏/软删子不阻删除/huurong 组拒绝手动删/root 拒绝删/manual 不受扰）/ Playwright E2E **129/129**（既有 126 零回归 + 新增 3 项：根节点存在可选+查看全部终端、安全分组默认收起 13 顶层+其他、toggle 展开/收起、i18n 未分组深层展开后兜底可见）+ pageerror=0 + esprima 124KB。
- **已知限制**：①火绒组改名跟随同步，用户不可在资产侧改 huorong 组名（如需脱离镜像须改为手动重建）；②同步覆盖 huorong 组内终端的手动组绑定（镜像语义）；③展开状态前端内存态，刷新重置（按需求「直至刷新」）。

## ADR-042 ｜ 客户端版本发布管理 + 下载入口 + 定制安装包文件名协议 ｜ 已接受

- **背景**：用户三大改造（2026-09-17）——①安装包文件名组装配置信息（一键安装即完成连接注册，解析失败静默回退手动流程）②中心侧版本发布与控制 + 最新客户端下载入口 ③已安装客户端自动更新（客户端侧引擎由 power-control-dev 按 main 协议并行实现，本侧只做服务端面）。
- **决策**（2026-09-17 实施）：
  1. **模块**：`server/client_release.py`（新，stdlib-only，power_control 同款自持连接+单例形态）。表 `client_releases`（version UNIQUE/filename/sha256/size/published_at/rollback_flag/note）+ `client_current`（单行指针，同一时间仅一个 current）。**回滚=指回旧版**：set_current 对非最新版本置 rollback_flag=1（「更新版本」判定=published_at 更晚、平局时 id 更大——同秒上传以入库顺序为准）。存储落 `<data_dir>/storage/client/{version}/{filename}`；同版本重复上传=覆盖更新（换名清旧文件、刷新 sha256/size/published_at），便于测试迭代。
  2. **定制文件名协议**（main 定稿，服务端生成+参考实现，终端侧安装器独立实现同协议）：`EyeTerm_Setup_x64_{ver}_{cfg64}_{md58}.exe`；cfg64=base64url(zlib(json{"s":server_url,"t":terminal_token}))，超 160 字符防御性截断（常规配置长度约 90-110 不触发）；md58=json 原文 MD5 前 8 位。安装器解析失败/字段缺失/校验失败 → **静默回退手动配置流程**（截断即必然解压失败=安全回退）。服务端不存定制包实体——下载时以通用包响应、Content-Disposition 用定制文件名，安装器从保存的文件名解析配置。
  3. **下载鉴权选型（回报 main 的评估结论）**：选 **10 分钟一次性下载票据（用后即焚）**，否决"仅限 console 会话内下载"。理由：①控制台会话是 header-token（localStorage）模式，无 cookie——浏览器 `<a>` 下载带不上 header，fetch+blob 对 26MB+ 安装包内存不友好，加 cookie 引入第二会话载体得不偿失；②管理员真实场景是"生成链接 → 微信/邮件发给终端用户"，跨会话跨设备可下载是硬需求；③一次性+10 分钟把泄露面压到最小，且比长期直链安全得多。票据内存态（服务重启失效=管理员重新生成，可接受），签发/消费有惰性清理防膨胀。
  4. **API**：
     - console（整组 admin-only 403+审计；定制名含 token，operator 不可见）：`PUT /console/client/releases/{version}?filename=&note=`（body=raw bytes，同版本覆盖）/ `GET /console/client/releases`（列表含 is_current/rollback_flag）/ `POST /console/client/releases/{id}/set-current`（发布/回滚）/ `POST /console/client/custom-name`（body {server, token 可空=当前 config 终端 token} → {filename, download_url 带 ticket, expires_in 600}；审计只记 server 不记 token）。
     - 终端：`GET /api/v1/client/manifest`（X-ETP-Token）→ {ok, manifest:{latest_version, download_url:"/download/client/setup", sha256, size, release_note}}（无 current 时 manifest=null）；**心跳响应捎带 latest_version**（省一次请求，power-control 侧消费，异常时不阻断心跳）。
     - 下载：`GET /download/client/setup`（无 ticket=通用包公开下载；带 ticket=定制包，Content-Disposition 用定制名，票据一次性/过期 404/410）。三口 scope：terminal 口白名单追加 manifest+`/download/`；console 口 GET 非 /api/ 已天然放行；legacy 全量。
  5. **上传通道**：app.py `_read_body` 对 `/api/v1/console/client/releases/` 前缀单独放宽至 `_MAX_UPLOAD_BODY`（256MB，api.py 定义），全局 8MB 防滥用限制不变。
  6. **控制台 UI**：登录页加「客户端下载」公开入口（#loginDownloadLink）；新导航页「客户端发布」（data-page=release，sysadmin 前，**admin-only** 随 applyRole 隐藏）：版本发布卡（版本号/说明/文件选择/上传 + 版本表：版本/文件名/大小/SHA256 前 12/上传时间/状态徽章 当前版本·曾回滚·已发布/设为当前——uiConfirm 确认）+ 定制安装包分发卡（平台地址默认 location.origin、token 留空=当前值占位提示、生成后展示定制文件名+完整下载链接+复制按钮、明示一次性 10 分钟语义）；cr 前缀 ES5。
  7. **顺带修复（store.py，ADR-041 引入的并发缺陷）**：`_asset_group_migrate` 原 try-ALTER/except OperationalError 实现不可靠——并发 DDL 下 sqlite3 抛 DatabaseError（duplicate column）甚至 SystemError（C 层 NULL，无法捕获），且每次空跑 ALTER 与同库其他连接读写交织存在 schema 冲突面；改为 PRAGMA table_info 预检（锁内串行、列已在时零 DDL）。本地 E2E 实测该缺陷在服务重启后首次并发访问资产页即触发 asset-groups 500，修复后消除。
- **验证门禁**（全绿）：py_compile 七文件 / esprima 129.2KB 内联脚本 / `tools/test_client_release.py` **37 项**（协议 round-trip/篡改 md58 拒绝/截断回退语义/中文编码/存储 CRUD/同版本覆盖/换名清旧/格式 400 矩阵/回滚标记三态/平局语义/manifest/票据一次性·过期·惰性清理/单例）/ `tools/smoke_client_release.py` **30 项**（自包含隔离环境：脚本内 spawn 临时 config 服务+auth 空库自举 PBKDF2+白名单引导+端口预检防孤儿互踩；无 current 三态/上传 400 矩阵/发布回滚链/manifest/心跳捎带/通用下载 Content-Disposition/定制名格式与 token 零泄露/票据下载一次性/operator 403 矩阵/manifest 401，**二次运行幂等**）/ Playwright E2E **139/139**（既有 129 零回归 + 新增 10 项：登录页下载链接、发布页导航可见与表单渲染、列表行/当前版本/曾回滚徽章/sha256 截断显示、定制名协议格式正则断言、票据 URL）+ pageerror=0。回归：test_asset_sync 16/16。
- **联调待办**：①power-control-dev 终端侧按协议实现 manifest 消费+下载+文件名解析引擎，联调时以 `parse_custom_filename` 参考实现对拍；②生产部署（合并总批）后生产只读冒烟 + 上传真实安装包发布一轮；③客户端下载入口当前指向通用包（无 token），定制分发按管理员操作路径走。
- **联调补充（2026-09-17 对齐 TBC-002）**：power-control updater.check_async 消费端点为 `GET /api/v1/client/update-manifest?version={当前版本}`，期望顶层扁平形状——与已登记的 `/api/v1/client/manifest`（嵌套 `{ok, manifest:{...}}`）路径与形状均不同。决策：**加别名端点复用同一数据源**（updater 已按 TBC-002 实现，改动最小）：两路径同一鉴权（X-ETP-Token）+ 同一 `cr.manifest()` 数据；update-manifest 返回**扁平形状**（键恒在，无 current 时值 null，消费侧判 `latest_version is None` 即无更新），`?version=` 参数接收但忽略（semver 比较在终端侧）。scope 白名单/docstring/冒烟（33 项，+3：扁平两态+401）同步。manifest 嵌套形状保持 ADR-042 原登记不变（其他消费者兼容）。
- **已知限制**：①票据内存态，服务重启后未消费的票据失效（重新生成即可）；②同版本覆盖上传后，已签发未消费的票据下载到的是新文件内容（version 语义以库记录为准）；③cfg64 截断场景（超长配置）实际不会用到——如出现说明配置异常，安装器静默回退手动流程即预期行为；④manifest 无 current 时返回 null，终端侧引擎需处理"无更新"分支。

## ADR-043 ｜ 资产管理第三方数据源（火绒+画方准入双源弹窗、IP 在线日志与终端隔离骨架） ｜ 已接受

- **背景**：用户五点需求（2026-09-17）——建档链路（已有）梳理入档、资产右键「第三方数据源信息」弹窗（火绒块+画方准入块）、IP 在线日志查询（依赖画方联动接口，规格未到）、终端隔离（同依赖，高危）。
- **决策**：
  1. **绑定关系选型**：终端↔画方准入**实时查询+模块级 60s 缓存**（复用 ADR-024 nad_terminals_cached），**不建持久化绑定表**——画方 1588 台登记数据全动态（IP/MAC/在线/阻断），持久化绑定是负资产；弹窗为低频人工查看场景。IP 优先查询、失败回退 MAC（MAC 从 asset.network[] 提取，与 hr 绑定匹配同源模式）。
  2. **火绒块所有权**：调 `store.hr_context_block(tid)`（huorong-dev ADR-033 增补二交付：linked/match_type/bound_ts/client.*/group_name/group_path/virus{total,success,fail,ignored,trusted,snapshot_ts}|null），server-platform-dev 不自行 join hr_* 表。病毒文案口径（huorong-dev 裁定）：virus=null→暂未接入；total=0→无病毒事件记录；fail>0→有 N 条处理失败需关注（警示色）；else 事件 N 条+成功率。**时间语义区分**：镜像快照时间（hr_clients.updated_at/virus.snapshot_ts）标注「数据同步时间」，client.last_seen（火绒侧 last_connect_time）标注「最近连上火绒」——两者严禁混用。
  3. **画方证据块扩展**：`nad_client.nad_evidence_full(term, only_mac)`（新公开函数）——ADR-024 的 `_evidence_block` 冻结形状不变（ipconflict 在用），扩展块 reginfo 透出**完整 dict**（登记人/负责人等登记字段原样透出，前端防御渲染）+ reginfo_stat 便捷位；`nad_find_by_ip/nad_find_by_mac` 加 `full=False` 参数；`nad_cache_ts()` 暴露缓存时点供前端标注。
  4. **API**（console 组，读 operator 可用）：`GET /console/thirdparty/{tid}`（一次聚合：terminal 摘要+huorong 块+nad 块+online_log/isolate 可用性标记，各区块独立 try/except 降级）；`GET /console/thirdparty/{tid}/online-log?ip&mac&start&end`（IP/MAC 至少一项 400，骨架降级 available:false）；`GET /console/thirdparty/isolated`（骨架空列表）；`POST /console/thirdparty/{tid}/isolate {action:block|unblock, password}`（**admin-only 403** + **密码重校验复用 auth.authenticate 全链**（锁定/限速/审计自动生效，临时会话即用即吊销）+ 审计 tp_isolate_auth_fail/tp_isolate_attempt；画方阻断 API 未接入前返回 501 并留意图审计）。
  5. **HTTP 语义边界**：401 严格保留「控制台会话无效」（apiFetch 全局跳登录）；isolate 重校验失败映射 403（密码未通过）/423（账号锁定）/429（限速）——实测 401 会误触发前端全局会话过期逻辑。
  6. **前端**：资产卡片 contextmenu（tpBind，事件委托 + Esc/外点关闭）三入口；tpModal 两区块弹窗（键值对 + 徽章 + macs 接入端口卡 + 各区块采集时间标注）；tpLogModal（IP/MAC 预填自上次聚合数据 + 近 7 天默认窗口 + 查询降级提示）；tpIsoModal（动作选择 + 密码 + 403/501 错误文案透出）；资产卡片「隔离中」红标（tpIsoKnown 集合驱动，画方 block 状态源接入前恒空不显示）+「查看隔离终端」筛选按钮（骨架 toast）。
- **待接入（画方 API 规格到手后）**：IP 在线日志真数据源（online-log 端点替换桩返回）、阻断/解除阻断真调用（isolate 端点 501 处替换，审计 detail 带画方 errno/ errmsg 原始回执，成功后更新 tpIsoKnown 驱动标签）、isolated 列表真数据源。均沿 ADR-024 模式（id=2 凭据/HMAC 签名/自签证书/纯标准库）扩展 nad_client。
- **验证门禁**（全绿）：py_compile / esprima 140.6KB / 单测 test_thirdparty **15/15**（扩展块形状/reginfo 完整透出/ADR-024 冻结形状不变/find full 参数/only_mac/缓存时点/非法形态防御）/ 冒烟 smoke_thirdparty **21/21**（自包含 spawn：聚合降级/404/400 矩阵/operator 403/密码重校验 403+501/审计直查双断言）/ E2E **150/150**（139 零回归+11 新增：右键三入口/弹窗两区块/未关联与未配置降级/日志预填与降级/隔离密码三态/隔离筛选 toast）+ pageerror=0。
- **已知限制**：①隔离「隔离中」标签在画方 block 状态源接入前不显示（渲染逻辑就绪）；②online-log/isolated 为骨架降级（UI 明示原因）；③火绒块 virus 明细维度（病毒名/事件时间/查杀类型）受官方 API 统计聚合口径限制，无法提供（huorong-dev 已如实告知）。
- **增补（2026-09-17 IP 口径修正 + 过渡时间线，main 批准）**：
  1. **IP 口径修正**：实锤 winhelper register payload 不含 ip 字段 → terminals.ip 列 = HTTP 连接源 IP（client_ip 兜底）——NAT/代理场景非终端本机地址，用它查画方会查错对象。修正：`_terminal_net_from_asset` 从 asset.network[] 取自报网卡 IP/MAC（多网卡取首个含 IPv4 项），**nad 查询键 = 自报 IP 优先 → MAC 回退 → 连接源兜底**；nad.query 透出 {ip, mac, asset_ip, source_ip} 四值；信息弹窗 IP 行展示自报 IP、连接源不同时括注「（连接源 x.x.x.x）」；tpLog 预填同步自报口径。**不动 winhelper register payload**（终端侧改码重建不值当，纯服务端修正）。
  2. **IP 在线日志过渡数据源（方案 A+B）**：online-log 端点 available=False 骨架语义不变（台账零变更），reason 文案增强（「接口尚未接入（规格索取中）；准入层入网时段记录在画方平台，平台侧暂不掌握」）；新增 `store.metrics_timeline(tid, days=7, limit=240)`（窗口内该终端 metrics 上报时间点升序 + gap 间隔 + cpu/mem 快照），响应并列透出 `platform_timeline` 独立区块；前端结果区渲染近 30 次上报表格，**显著标注「仅代表该终端与本平台的通信记录（心跳/数据上报到达时间），不代表准入层的入网时段」**；ipconflict_reports 因仅冲突事件写入数据稀疏，排除。
  3. 门禁：冒烟 25/25（+4：自报 IP 口径双值透出/timeline 空窗口降级/上报后数据点/cpu+mem+gap 字段）、E2E **154/154**（+1 时间线区块渲染与「不代表」标注断言）+ pageerror=0；store 回归 16/16。

## ADR-044 ｜ WoL 定时唤醒产品化（wol_schedules + 平台内调度 + 同网段中继兜底） ｜ 已接受

- **背景**：M720t BIOS RTC 定时开机固件不支持（T1-T5 定案），main 拍板产品主路径改为 **定时 WoL 唤醒**。第一段过渡自动化已上线（生产 systemd timer 每日 08:30 服务器直发 172.17.90.255:9/:7 + 全网广播兜底）；本 ADR 为第二段平台内产品化。**服务器直发对目标网段（90.x）的可达性未经验证**（2026-09-18 晨唤醒来自同网段 Jun-office-PC，非服务器直发），故中继兜底是主通道关键。
- **决策**（2026-09-18 实施）：
  1. **存储**（power_control.py 追加两表）：wol_schedules（terminal_id+time_hhmm UNIQUE / mac 可空自动带出 / method=auto|direct|relay / run_state 状态机 / direct_ts/relay_ts/relay_cid/relay_tid / last_run_date 防当日重触发 / last_result）+ wol_attempts（schedule_id/phase=direct|relay|confirm|giveup/relay_terminal_id/ok/detail 留痕）。
  2. **调度器**（server/wol.py 新模块，app.py 守护线程 20s 节拍，`wol_enabled=false` 可整体关闭；systemd timer 保留为底层兜底直至平台调度真实验证）：状态机 wol_tick——①到期（HH:MM 匹配+当日未跑+非进行中）→ 直发（魔术包发目标机候选 IP 的 /24 定向广播 + 255.255.255.255 兜底，端口 9/7）②确认=目标机 last_seen ≥ 触发时刻即 done③直发观察窗 240s 未上线 → **同网段中继选举**（候选 IP=asset.network[] 自报全量 + 连接源 IP；/24 相同 + 在线 hb_timeout 内；排除目标机；按 last_seen 新→旧取首选）→ 经命令通道下发 `wol_relay` 给中继终端④中继观察窗 240s 未上线 → giveup 结论落档（附中继命令回执摘要）。
  3. **契约对齐（power-control-dev 定稿）**：wol_relay args {mac, broadcast, port?缺省 9}；服务端先行校验双保险（normalize_mac/validate_broadcast 严格 IPv4 点分），终端 handle_wol_relay 再拒一次；ok=true 仅代表 UDP 已发送，唤醒确认统一以目标机 last_seen 恢复为准。单发 single 关机任务 Windows 语义=触发后转 Ready 残留（不自动删），已过期无执行风险；如需自动清理 4.1.6 在终端 XML 加 DeleteExpiredTaskAfter（服务端 UI 按「已过期 single=历史态」呈现即可）。
  4. **console API**（_console_powercontrol 组）：`GET /console/powercontrol/wol/relays?terminal_id=`（选举读：relays 列表+目标机 MAC/广播预填）、`GET/POST/PUT/DELETE /console/powercontrol/wol/schedules`（CRUD；POST/PUT/DELETE admin-only 403+审计；time HH:MM/method 枚举/mac 归一/同名同刻 409）、`POST /console/powercontrol/wol/direct {terminal_id, mac?, broadcast?, port?}`（服务器直发即时动作，admin-only+审计）。命令白名单追加 power_action/power_action_abort/wol_relay。
  5. **console UI**（pw 页新卡「立即开关机与远程唤醒」）：WoL 定时唤醒表（状态徽章：待触发/直发已执行/已转中继/已上线/未唤醒）+ 新建弹窗（终端/名称/时间/方式/MAC 自动带出可手填）+ 启停/删除（uiConfirm danger）+ 最近唤醒尝试表（直发/中继/确认上线/本轮结束）+ 手动唤醒弹窗（目标终端自动带 MAC/广播 + 同网段在线终端代发下拉，服务器直发/中继二选一）。pwTs 时间格式化（独立于 perf/其他页）。
- **验证门禁**（全绿）：py_compile / `tools/test_wol.py` **7/7 组**（纯函数/到期直发/观察窗不升级/中继升级命令形状/确认 done/无中继 failed/giveup 落档/停用不触发）/ E2E **179/179**（既有 164 零回归 + 新增 15 项：schedules CRUD 往返/400 校验/relays 形状/direct MAC 归一与 400/power-action 校验矩阵/在线守卫/新卡片与两弹窗开合）+ pageerror=0 + esprima。

### ADR-044 增补（2026-09-18 夜）｜ 跨网段直发定案与调度器跳直发优化

- **实证定案（main 会话对照实验）**：服务器同时发单播+定向广播到同网段在线中继终端的 9147 端口——**单播 3/3 秒达，定向广播 0/3 达**（90s 窗；生产 IP 按脱敏铁律不录）。铁证：路径通、防火墙无碍，纯粹是三层设备默认禁转 directed broadcast。此前 SRV-116 直发 90.18 未恢复与 08:30 timer 无效全部由此解释。**产品结论：跨网段唤醒唯一可行主通道=wol_relay 同网段中继；直发仅对与服务器同网段的部署形态有效。**
- **调度器优化（wol.py）**：新增 `direct_applicable()`——UDP connect 探测（不发包）取服务器到目标方向的本机源 IP，与目标候选 IP /24 比对；到期触发按 `method` 字段起步：`method=relay` 跳直发直入中继；`method=auto` 且跨网段（direct_applicable=False）跳过 240s 直发观察窗直接中继（省 4 分钟无效等待）；`method=direct` 用户意志优先仍直发；目标机无已知 IP 保守保持直发尝试。顺带接通 sched.method 字段（此前 tick 未消费，console 下拉为摆设）。giveup 措辞区分「直发+中继均未唤醒」/「中继未唤醒」。
- **验证**：`tools/test_wol.py` 扩至 **12/12 组**（新增：跨网段 auto 跳直发/中继 giveup 措辞/method=relay 显式/method=direct 覆写/direct_applicable 回归）；E2E console **95/95** 全绿。E2E 健壮性修复（本次暴露）：详情端点 latest_metrics 只取最近 1 小时（api.py:883），历史种子过期即整套超时——e2e_console.py 运行前自动给 WIN-SMOKE-TOKEN 播种新鲜快照，任意时刻可复现绿基线。
- **部署与运维行动**（待凭据注入执行）：①上传 wol.py 重启服务；②M720t 每日 08:30 注册进 wol_schedules（调度器自动走中继主通道，选举命中 Jun-office-PC）；③停用 wol-win13f.timer（跨网段直发已被证明无效，保留徒增误导；unit 文件留档）；④台账 SRV-116 注记「跨网段受 directed-broadcast 过滤限制，跨网段以 wol_relay 为主通道」（api-registrar 归口）。
- **已知限制**：①同网段判定为 /24 广播域近似（无 VLAN 数据库，铁律：不推测网段语义）；②观察窗内调度线程不重试直发（单轮 direct+relay 两段，失败人工介入或次日照常）；③中继代发依赖中继终端 4.1.5+ 客户端；④wol_schedules 按 terminal_id+time 唯一，跨日循环无节假日概念。

### ADR-044 增补（2026-09-19）｜ 双路线唤醒架构（relay/nad）+ 系统管理配置卡

- **背景**：跨网段直发已定案不可行（见上条增补），主通道收敛为「同网段中继代发」。画方准入系统已在终端侧部署（其接口位于各 VLAN 内），理论上可直接在目标 VLAN 内发唤醒广播——但**接口规格未交付**，故以 stub 级适配器预埋，接线后无需改调度器。
- **架构（两条路线，全局模式切换 `wol.wake_mode`）**：
  - **relay（默认）**：平台调度器选举**同网段在线候选**→**随机乱序入队**（避免固定打同一台中继）→逐台代发，单台观察窗耗尽即轮替下一台；候选耗尽后 `wol.pinned_relay` 专用代理兜底；再无则 give_up 落档（附中继命令回执摘要）。
  - **nad（画方准入）**：`wake_via_nad()` 适配器——url 未配置 →「画方准入唤醒接口未接入，已回退中继模式」；url 已配置 →「画方接口适配器未接通（规格待交付），已回退中继模式」。**两态均照常走 relay 链**，诚实留痕（attempts phase=nad, ok=0）+ 不哑等（本条为硬要求：任何降级位都不得让调度悬空）。
  - 其余调度语义：`time_due_with_grace` 逾期 ≤30min 补触（修「精确分钟匹配 + 在途上限 → 超额计划当天漏跑」）、`wol.max_inflight` 在途并发上限、`wol.relay_step_sec`/`wol.relay_window_sec` 轮替节奏。
- **配置键（settings.py，ADR-044 增补）**：`wol.wake_mode`(relay) / `wol.pinned_relay` / `wol.relay_step_sec`(120) / `wol.relay_window_sec`(300) / `wol.max_inflight`(3) / `nad.wol_api_url` / `nad.wol_api_key`（**SENSITIVE_KEYS，仅存密文，端点只返掩码**）。
- **端点（台账 SRV-140/141）**：`GET`/`POST /api/v1/console/sysadmin/wol`（admin-only + 审计）；POST 校验：wake_mode∈{relay,nad}、pinned_relay 终端须存在、step 30-600 / window 60-1800 / inflight 1-10、`nad_api_key` **留空即保持不变**。
- **控制台**：系统管理页新增「远程唤醒（WoL）」卡（模式切换、专用中继 tid、轮替节奏三参数、画方 URL/Key），Key 输入框 type=password 且只显示脱敏值。
- **验证**：`tools/test_wol.py` **15/15 组**（新增 13/14/15：nad 未接入回退 / nad 已配置规格待交付回退 / `wake_via_nad` 两态与 `_wake_mode` 非法值兜底）；E2E console **206/206**（新增 6 条：卡渲染与双选项、默认值回填、切 nad+step=90 保存回读、恢复 relay 清场、Key 掩码态）。
- **E2E 环境教训（复用）**：E2E 连外部实例（BASE=127.0.0.1:18090，不自起服务）→ 服务端代码改动后**必须重启 dev 实例**再跑，否则内存里是旧路由表；且鉴权层前置 401 会掩盖「路由不存在」，用未授权请求探测端点存在性会误判（401≠路由存在）。
- **状态**：调度链路与控制台配置面在用；**画方唤醒执行面待接口规格交付**（stub 到位，接线不改调度器）。

### ADR-044 增补（2026-09-18 晚）｜ 终端 4.1.8「自动开关机」页停止快照上报对平台存档的影响面（power-control-dev 增补）

- **改动**：客户端 4.1.8 起「自动开关机」页**仅读本地**——删除页面级自动上报（12h 节流 _auto_report_async）与「上报平台」按钮（powercontrol.js pcReportNow + 平台存档卡 DOM 双仓移除）；唯一人工动作为 BIOS 卡「登记到平台」（/report human_set，消费线人工登记链保留）。
- **影响面（平台侧）**：POST /api/v1/terminals/{tid}/powercontrol/snapshot 的**页面级自动流量停止**——平台「开关机管控」页的快照存档/last_report 状态将随各终端升级 4.1.8 后**停止更新**（历史数据保留）。**不受影响**：写入路径快照双存档（bios_apply/_job_bios_apply，ADR-007 语义）继续上报；pc_apply_policy 执行器回执与 pc_diag 诊断链不变；power_action/wol_relay 命令通道不变。
- **待平台侧决策（后续批次）**：开关机管控页快照列改口径（读 metrics 资产或标记「客户端 4.1.8+ 不再自动上报」）或恢复上报（需产品理由——用户明确要求页面仅读本地）。

## ADR-045 ｜ power_action 立即重启/关机（中心发起 + 撤销窗口） ｜ 已接受

- **背景**：用户点名需求（4.1.5）——管控语义：仅中心可发起立即关机/重启，终端无本地入口；参考脚本（定时关机_V1.0.2.bat）语义对齐。与 ADR-040 定时策略是**两条独立链**：立即动作不创建/不动终端计划任务。
- **决策**（2026-09-18 实施，与 power-control-dev 契约定稿同日）：
  1. **命令契约**：`power_action {action:"shutdown"|"restart", delay_sec:0-3600 缺省 60, force:缺省 true}` + `power_action_abort {}`（撤销=shutdown /a；rc=1116 无 pending 是正常业务态，UI 按「无可撤销」提示非错误）。终端侧仅倒计时知会弹窗，无本地取消——管控意图中心优先。
  2. **console API**（admin-only 403+auth.audit 双事件）：`POST /console/powercontrol/terminals/{tid}/power-action` + `/power-action/abort`——服务端先行校验（action 枚举/delay 整数 0-3600/force 布尔归一），**仅在线终端可发起**（409 离线拒绝：立即动作排 7 天队列无意义），enqueue timeout_sec=120（拉取窗口短于倒计时），回执复用命令通道。审计事件 powercontrol.power_action / power_action_abort。
  3. **console UI**：pw 终端行操作列（admin+在线显示）「重启」「关机」→ uiConfirm danger 确认 → 下发后横幅（命令号 + 撤销按钮 + 120s 观察窗），撤销后轮询 abort 命令回执（5s×12 次），note/1116 语义如实展示。
- **验证门禁**：E2E 8.997 段覆盖（400 校验矩阵/在线守卫 200-or-409 双合规）；回执形状以终端单测为权威（power-control 侧已固化）。
- **已知限制**：①撤销窗口实际起算于终端拉取命令后（下发→拉取有 ≤1 心跳间隔），UI 横幅 120s 为观察窗非精确倒计时；②force=true /f 强制语义默认开启（对齐参考脚本），不等待应用自行退出。

## ADR-047 ｜ 开关机管控任务化重构（power_tasks 统一任务模型） ｜ 已接受

- **背景**：用户 PRD——开关机管控页从「终端列表+策略表单+批次记录」重构为**任务为中心**（开机/关机任务统一管理、向导式新建、增删改）。实施中经 main 三次范围追加（终端侧中心任务驱动接口、资产管理透出、终端个性化任务）与一次架构修正（关机改终端本地执行，平台调度下发方案作废）。
- **决策**（2026-09-18 实施，批 A）：
  1. **统一任务模型** `power_tasks`（kind=boot|shutdown / name UNIQUE(kind,name) / source / origin=platform|client_personal / target_type=group|terminals / target_json / repeat / weekdays 7 位 / once_date / time_hhmm / enabled / method）：**组目标触发时动态展开**（expand_platform_targets 与执行引擎同一段逻辑，展示与执行永不漂移）；root 组=全部平台终端；已注销目标自动剔除并留痕。
  2. **boot 任务=平台调度**：保存时目标展开写回 wol_schedules（新增 task_id 关联列，diff 同步保留同名同刻行运行态），wol_tick 引擎照跑；wol_tick 按任务复核启停与当日 repeat（task_due_today）；**触发窗修正**：到期判定由精确分钟匹配改为逾期 ≤30min 补触（time_due_with_grace）——修复「精确匹配+在途上限 MAX_INFLIGHT=3 → 批量超额行错过触发分钟当天漏跑」存量缺陷。
  3. **存量迁移（部署时序红线）**：migrate_legacy_schedules 幂等增量——task_id 为空的 wol_schedules 行建任务回填，**不删不重建调度行**，M720t 生产计划（每日 07:30）引擎无缝接续；app.py 启动时自动执行。
  4. **shutdown 任务=声明配置模板集（架构修正定案）**：定时关机由终端本地 schtasks 执行（离线也生效），平台不做到期调度（pc_sched 方案与 runs 表已建即废，未投产即移除）；repeat 收敛 daily/weekly/once（本地执行无节假日概念）；中心三动作——`POST /tasks/{id}/dispatch`（展开目标→复用 pc_apply_policy 批次链，离线排队上线补投）、`GET /tasks/{id}/drift`（声明 vs 终端上报回读比对：consistent/drift/not_reported+超期 stale 标注）、任务详情透出声明形状（task_shutdown_config 与 pc_apply_policy shutdown-set 同构）。
  5. **关机配置上报通道**：`pc_shutdown_config` 表（terminal_id PK/config_json/version/reported_ts）+ 终端口 `POST /terminals/{tid}/powercontrol/shutdown-config`（连接时+变更时上报，同版本幂等仅刷时间戳、新版本覆盖）；超期阈值 SHUTDOWN_STALE_SEC=7 天标注「配置状态陈旧」。
  6. **节假日日历**：holidays 表（date PK/type=holiday|workday/name）+ 双通道维护（JSON 批量导入 ≤500 条/次 + 单条增删 UI）；task_due_today 判定：workday=周一~五且非 holiday 标记或调休标记、holiday=标记或周末（调休除外）；**当年日历缺失如实回退（周一~五/周末）并留痕+UI 提示**，不虚构数据。
  7. **终端个性化开机任务**（origin=client_personal）：终端口 `GET/POST /terminals/{tid}/powercontrol/boot-tasks` + `PUT/DELETE /{task_id}`（归属锁定：仅 origin=client_personal 且目标含本终端，否则 403）；每终端上限 PERSONAL_TASK_LIMIT=5 fail-closed；console 任务列表 origin 过滤+终端三键检索（tid/IP/主机名/MAC）。
  8. **终端命中解析**：`GET /terminals/{tid}/powercontrol/boot-tasks` 返回命中启用任务按 next_trigger_ts 升序（once 过期→null 殿后）；`GET /console/powercontrol/terminal-power-config`（详情弹窗：开机=中心命中、关机=上报真实态）+ `GET /console/powercontrol/daily-summary`（资产/终端列表「每日开机=中心 daily 命中最早时刻+总数徽章」「每日关机=上报真实态+陈旧徽章」）。
  9. **UI 重构**（console pw 页）：任务卡（列表/类型徽章/目标摘要/计划文本/启停/下发/详情展开——boot 展开行+尝试链、shutdown 回读比对）置顶 + 节假日日历卡 + 终端与手动操作卡（撤勾选列与批量下发表单，加每日开机/关机两列）+ 手动唤醒卡 + 批次记录卡（保留为历史）；新建/编辑 4 步向导（类型→资产源→目标→计划：第三方源置灰+关机仅本平台说明+组目标动态语义说明）； pt 前缀 ES5，资产页卡片/详情弹窗同步透出（ptDailyKv/adPowerCfgHolder）。
  10. **写权限**：任务/日历/下发写操作 admin-only（对齐 wol schedules），读 operator；control 台创建强制 origin=platform，PUT origin 不可漂移；同名任务 409。
  - **勘误**：本条初稿误编号 ADR-046（该号已被首页资产定位 AI 推断占用），改为 ADR-047。
- **增补（2026-09-19 凌晨，用户拍板语义修正：工作日/节假日按星期直接定义）**：
  1. **默认判定=纯星期语义**：workday=周一~五、holiday=周六/日，零外部数据依赖；holidays 表**降级为可选例外层**（type=holiday 把工作日变假、type=workday 把周末变班——未来调休场景手动标记口），task_due_today 本体判定逻辑本就如此，无需改动。
  2. **配套简化**：boot-tasks 响应 `calendar_fallback` 字段删除（不存在日历缺失态）；holiday_status 移除 covered 语义（仅例外标记计数）；UI 文案改「默认周一至周五为工作日、周六日为假日；如需调休例外可在日历手动标记」+ 日历卡标题标注（可选）；向导选项改「工作日（默认周一至周五）/节假日（默认周末）」。
  3. **runs/pc_sched 残留清理**：作废调度方案的 `GET /runs/{id}` 路由（pc.run_get 无实现，调用即 500，api-registrar 对账发现）撤除；过时注释勘误。关机任务执行历史=声明下发批次（pc_policy_dispatch/targets）+ 回读比对 drift，无 runs 呈现。
  4. **E2E 确定性加固**（本轮暴露三类环境敏感断言）：详情断言精确定位种子终端（列表首行随 smoke 重灌漂移且无新鲜指标）、tp 三方段固定目标终端为种子卡、home 卡片计数放宽 ≥10（并行加卡不再脆断）。
  - **验证门禁**（全绿）：py_compile / esprima / test_power_tasks **101/101**（日历断言改写：星期默认为主路径+例外覆盖为辅，covered 删除断言）/ 回归七件套：test_wol 12 + test_huorong_link 60 + test_asset_locate 67 + test_ai_asset_locate 76 + test_thirdparty 17 + test_asset_sync 16 / Playwright E2E **191/191** + pageerror=0。
- **验证门禁**（全绿）：py_compile 六文件 / esprima 186.2KB 内联脚本 native confirm=0 / `tools/test_power_tasks.py` **101/101**（纯函数 4 组+存储 8 组+路由含终端口个性化/归属锁定/上限 409/上报幂等/drift） / 回归 test_wol 12/12 + test_sysadmin 44/44 / Playwright E2E **190/190**（pw 段重写：任务列表/徽章/日历回退提示/向导 4 步/第三方置灰/下发确认+toast/每日列；tp 弹窗流补幂等关闭修复前置拦截缺陷；4.1.5 段注册在线终端保证 400 校验先于 409 语义）+ pageerror=0。
- **部署**：服务端增量（power_control.py/api.py/wol.py/app.py + console/index.html，无新依赖）；迁移随启动自动执行；部署后核验项——①M720t 07:30 迁移前后 schedule 生效性（task 回填+task_due_today 真值）②组目标展开规模留痕（drift note/任务详情展开行数）③日历导入后 workday/holiday 任务次日留痕核对。
- **已知限制**：①日历数据待 main 向用户索取 2026 年度安排导入，空表期回退语义；②第三方资产源（火绒/画方）为批 B——UI 已置灰预埋、source 校验 fail-closed；③任务级 last_result 由引擎在 done/giveup 时更新，触发中过程态看详情展开行；④组目标行数随组规模增长（每终端 1 行 wol_schedules），百级规模可控；⑤shutdown 声明 once 模式过期后不自动清理（展示「已过期」语义，可手动删）。

## 运维记录 ｜ 2026-09-18 晨 shutdown-set 未执行取证 ｜ 已闭环

- **结论**：shutdown-set(single 08:27)+toggle **从未创建下发**（非迟到/非创建失败）：pc_policy_dispatch 无行、commands 无 pc_apply_policy、journal 07:50-08:35 零 dispatch POST（console 侧仅 login+GET 轮询）。终端 08:00:55 已在线（register+命令 42 拉取可证），通道畅通。执行责任在服务端窗口内未发起。
- **残留**：零（客户端未收到任务，无 schtasks 残留，无需 shutdown-remove）。single 任务残留语义见 ADR-044.3。
- **当日 08:30 timer**：journal Succeeded 三地址发包，但机器 08:00:55 起持续在线，非关机→唤醒闭环；真正首验=当晚用户关机→次日 08:30 直发，不通则 wol_relay 经 Jun-office-PC（4.1.5）代发二次验证。

## 运维记录 ｜ 2026-09-18 白昼唤醒事件与方法论勘误（用户实机指正） ｜ 已闭环

- **事件**：M720t 于 08:41 后脱离平台（用户关机）。09:56（main 同网段）与 09:59（server-platform-dev 经 Jun-office-PC 本体三路广播，等价 wol_relay 执行语义）两次魔术包发送；随后多轮 ICMP/TCP445/ARP 探测全静默，据此作出「机器 S3 睡眠 + BIOS 网络唤醒未武装」判读。**该判读错误，已作废**——用户实机确认电脑早已开机：防火墙/安全软件使 ICMP/TCP/445 在开机状态下也全静默，探测静默≠关机。ARP offload「特征」解读同样不成立。
- **方法论定案（产品级，写入 wol 语义与文案）**：**唤醒/在线判定唯一权威判据=平台 last_seen**（ADR-044.2 确认语义本就如此设计，本轮教训反证其正确）。唤醒观察窗内**禁止**使用 ICMP/TCP 探测作为辅助判据（会制造假阴性）；give_up 文案保留「终端 BIOS 网络唤醒未开启」可能原因提示，且不得引导用户用 ping 判定。待下次部署批次落入 console wol 尝试详情文案。
- **19:23→19:37 恢复事件**：19:35 发包后 19:37 last_seen 恢复（online=True, cv=4.1.3）——无法区分发包唤醒效果与用户手动开机，**如实存档不下结论**。
- **干净终验流程（未来 M720t 复验）**：平台确认 last_seen 消失（真关机）→ 发包（直发或中继）→ last_seen 恢复=成功；全程仅以 last_seen 为准。

## ADR-046 ｜ 首页「资产定位」AI 推断函数（多源登记证据 → 结构化推断块） ｜ 已接受

- **背景**：首页「资产定位」由 asset-mgmt 检索聚合管线（中心/火绒/画方多源，输出 asset_profile 确定数据块）产出，AI 环节基于聚合证据推测楼层/房间/科室/使用人。内部函数供管线调用，**不新增 HTTP 路由**；新端点无（接口登记官无需登记，内部函数契约以本条为准）。
- **决策**（2026-09-18 实施，ai-analysis-dev）：
  1. **函数**（`server/ai.py`）：`build_asset_locate_context`（证据包 → prompt+存证+stats，复用 ADR-027 结构感知裁剪与预算分配设施；优先级 sources > terminal > hints，单类上限 24/4/4KB、总注入 ≤32KB）+ `run_asset_locate_inference(ctx, profile, terminal_id=None)`（llm_chat_chain 主备链 timeout=45×0 次重试；输出 inference 块由 asset-locate 管线并入响应）。
  2. **输入形状 v1**（与 asset-mgmt-dev 对齐，全键可选——缺失在 prompt 如实声明）：`profile={terminal{terminal_id/hostname/ip/mac/os_info}, sources[{source, source_label, matched, fields{原始键值对}}], hints{name_pattern_peers[], vlan_kb{}}}`；sources 兼容 dict 形态（键=source）。
  3. **输出形状**：`{status: ok|unavailable, inferences[{field: floor|room|department|user, value, confidence: high|medium|low|none, evidence[], note}], disclaimer:"AI 推测，非权威数据", model, dropped, unavailable_reason, analysis_id, duration_ms}`；四字段恒齐全（模型未给/依据不足 → null 条「证据不足，无法推断」）。
  4. **硬约束 prompt**（ADR-030/031 同款纪律 + 资产域收紧）：严禁编造依据/引用必须指向输入实际字段值（「来源.字段=值」格式）/缺失声明证据不足/**禁止按 IP 网段推测楼层区域归属（铁律：vlan_kb 与命名对照数据存在时除外）**/confidence 四级口径（high=权威登记直出、medium=多源交叉、low=命名模式弱信号、none=无推断）；知识注入 ASSET_LOCATE_KB（火绒登记常见实名键、准入登记名形态、命名规律仅限有据对照）。
  5. **编造拒绝（输出校验层）**：模型输出严格 JSON 解析（容忍围栏/前后缀）；field/confidence 白名单 + 中文别名归一（楼层→floor、高→high 等），不可辨剔除；evidence 逐条接地校验（直接引用 prompt 原文 / 含 ≥3 字符原子 / 键值分隔符键部含原子），无接地依据剔除计 dropped；全部条目被剔除 → status=unavailable(model_output_validation_failed)，不产出可信度存疑的空块。
  6. **降级**：LLM 全链失败/输出不可解析/校验全灭 → inference 块 unavailable+原因，asset-locate 确定数据照常返回（管线不因 AI 挂）；零证据短路（无任何输入原子）→ 确定性四字段「证据不足」块，不消耗模型调用；terminal_id 提供时落 ai_analyses（trigger=asset_locate，context 存 sections/stats/dropped），入 AI 分析历史，落库失败不阻断返回。
  7. **管线契约适配层**（`server/ai_analysis.py`，asset-mgmt-dev 定稿契约）：`infer_asset_locate(ctx, raw_text, identifiers, payload) → {available, model, blocks[{title,text,evidence}], error}`（附加 inferences/dropped 预留键）——管线侧 try-import + 未就绪降级互不阻塞；适配器做 payload→profile 映射（asset_profile.registration→火绒源 / admission→画方准入源 / platform.group→中心平台源 / identifiers→检索标识源 / sources 状态→数据源状态条目）与结果形状转换（有值字段每字段一块含中文置信度，全不足 → 单块「推断结论：证据不足，无法推断」）；**raw_text 不进 LLM prompt**（隐私最小化：检索标识已结构化提取，原文不透传）；内部异常兜底 available=False。
- **验证门禁**（全绿）：py_compile / `tools/test_ai_asset_locate.py` **76 项**（mock LLM：prompt 三节构建与缺源声明/围栏 JSON 解析/别名归一/依据接地引用/编造依据剔除含 dropped 口径/证据不足 null 条/四字段恒齐全/全编造与不可解析与链失败三路降级/零证据短路不耗模型/落库 trigger 与 sections/多字段超量预算截断 ≤24KB 单类 ≤32KB 总量/校验层直验/契约适配层 payload 映射与 blocks 转换与全不足单块与失败透传与畸形 payload 兜底/落库 terminal_id 取最佳命中）+ 回归 test_ai_ipconflict 30 / test_ai_diagnose 62 / test_ai_routetrace 27 零回归。
- **已知限制**：①依据接地为启发式引用完整性防线（值原子/键部匹配），非语义真值判定——LLM 引用真实数据但推理逻辑错误时不拦截，由 disclaimer 与 confidence 弱化表达；②形状 v1 与 asset-mgmt-dev 双向确认中，若其聚合输出键名有出入按 ADR 本条别名机制适配；③推断仅四字段白名单，扩展字段需同步 ASSET_FIELDS 与别名表。
