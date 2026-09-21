# 火绒终端安全管理系统对接方案（EyeTerm）

- 版本：v1.0（调研稿，待用户确认后进入实施）
- 日期：2026-09-15
- 作者：huorong-dev（火绒对接专项）
- 依据：官方《火绒终端安全管理系统API说明文档》(API v1)（F:/Desktop/111/temp/API v1.html，源 https://172.17.1.3:8080/docs/api.html）
- 附件：`docs/attachments/huorong_api_client_draft.py`（客户端雏形，零凭据，可作为 server-platform/server/huorong.py 底稿）

---

## 1. 对接能力面（官方全览：15 个唯一路径 / 17 个文档化操作口径）

统一规范：HTTP POST + JSON；响应 `{errno, errmsg, data}`；errno：0=成功 / 1=认证失败 / 2=参数错误 / 3=服务端内部错误 / 4=API 未授权。

| 分类 | 端点 | 说明 | 版本要求 | 读写 |
|---|---|---|---|---|
| 分组 | `/api/group/_list` | 全部分组树（group_id/parent_group/group_name） | - | 只读 |
| 分组 | `/api/group/_info` | 单分组信息（group_id） | - | 只读 |
| 分组 | `/api/group/_create` / `_delete` / `_rename` | 分组增删改 | - | **写** |
| 终端 | `/api/clnts/_list` | 全量终端基本信息（client_id/IP/MAC/分组/OS/版本/病毒库日期/在线状态），分页 limit≤200 | - | 只读 |
| 终端 | `/api/clnts/_online` | 在线终端 MAC 列表，分页 | - | 只读 |
| 终端 | `/api/clnts/_info2` | 终端详情 v2，可选 hardware/software/assets/netconf | v2.0.6.0+ | 只读 |
| 终端 | `/api/clnts/_info` | 终端详情 v1（clients[]/mac[]） | - | 只读 |
| 终端 | `/api/clnts/_rename` / `_group` | 改名 / 移动分组 | - | **写** |
| 终端 | `/api/clnts/_leak` | 高危漏洞未修复终端 + 全局 KPI（all_client / risk_client） | - | 只读 |
| 终端 | `/api/clnts/_virus_events` | 病毒事件统计（type 0=按终端 / 1=按分组 / 2=全量；含处理结果 success/fail/ignored/trusted） | - | 只读 |
| 任务 | `/api/task/_create` | 查杀（quick/full/custom_scan，v2.0.8.0+）、网络隔离（netctrl）、通知（message） | v2.0.8.0+（扫描） | **破坏性** |
| 软件 | `/api/swinfo/_search` | 软件三维度统计：按软件 / 按版本 / 按终端（groupby），ostype 必填 | - | 只读 |

## 2. 价值场景评估与优先级

| # | 场景 | 数据源 | EyeTerm 侧呈现 | 价值 | 优先级 |
|---|---|---|---|---|---|
| S1 | **终端安全状态聚合** | `_list`（病毒库日期 definitions / 在线 / 终端版本 / 分组） | 资产明细弹窗新增「安全」维度（杀软在线、病毒库新鲜度、所属安全分组）；控制台终端列表附安全徽章 | 高——补齐 EyeTerm 资产模型的安全维度，识别"病毒库陈旧/杀软失联"终端 | **P1** |
| S2 | **高危漏洞风险 KPI** | `_leak`（一次调用得 all_client / risk_client + 风险终端明细） | 控制台「终端安全」概览卡主指标（风险终端占比）+ 风险终端清单（组名/IP/OS/状态） | 高——单次调用即得全局风险率，是安全板块的天然门面 | **P1** |
| S3 | **病毒事件看板** | `_virus_events` type=2（按终端 count + success/fail/ignored/trusted） | 安全概览页：事件总量、处理成功率、TOP 风险终端表；可按时间窗过滤 | 中高——反映真实威胁态势与处置闭环质量 | **P2** |
| S4 | **分组-设备映射同步**（试点已授权） | `_group/_list` + `_list` 聚合 | 与 EyeTerm asset_groups/终端模型按 MAC 关联，形成"安全分组 ↔ 平台终端"视图 | 中高——是 S1/S3 的数据底座 | **P1（试点）** |
| S5 | **软件资产统计** | `_swinfo/_search` 三维度 | 资产合规视图（如浏览器/办公软件版本分布、安装率） | 中——数据量大、呈现成本高，非首屏刚需 | **P3** |
| S6 | **远程处置**（查杀/隔离/通知） | `/api/task/_create` | 控制台对单终端/分组下发查杀、隔离、通知 | 高但**破坏性**——误操作影响面大 | **P4（审批门禁后另行评估）** |

## 3. 集成架构（推荐：服务端 server-platform 新模块）

```
火绒控制台(172.17.1.3:8080, HTTPS 自签)
        ▲  HRESS 签名 POST/JSON（服务端发起，终端侧不直连）
        │
┌───────┴────────────────────────────────────────────┐
│ server-platform                                    │
│  server/huorong.py    # 客户端（签名/分页/重试/门禁） │
│  server/settings.py   # huorong.* 配置（Secret 加密）│
│  server/store.py      # hr_* 缓存表 + 同步审计       │
│  server/api.py        # /api/v1/console/huorong/*   │
│  同步器：后台线程周期同步 + 手动触发，UI 只读缓存      │
└───────┬────────────────────────────────────────────┘
        ▼
控制台 UI（新「终端安全」区块 + 资产明细安全维度）
```

### 3.1 模块划分（server-platform/server/）

- **huorong.py（新）**：`HuorongClient`（见附件雏形）——签名、分页迭代、errno 归一化、网络重试、任务门禁（`enable_tasks=False` 时 `create_task` 一律拦截）。
- **settings.py（改）**：`SENSITIVE_KEYS` 追加 `"huorong.secret"`；`DEFAULTS` 新增：
  - `huorong.base_url`（明文，如 https://172.17.1.3:8080）
  - `huorong.ak`（明文，属标识符非密钥）
  - `huorong.secret`（**加密**，控制台脱敏显示 `CWH****G0FZ9` 形态）
  - `huorong.enabled`（0/1 总开关）
  - `huorong.sync_interval_sec`（默认 300，抖动 ±10%）
  - `huorong.tls_fingerprint`（自签证书 SPKI SHA-256 指纹 pin；留空=不校验，仅限隔离内网联调）
- **store.py（改）**：新表（隔离于既有业务表）：
  - `hr_groups(group_id PK, parent_group, group_name, updated_at)`
  - `hr_clients(client_id PK, mac_norm, local_ip, connect_ip, client_name, computer_name, group_id, os_version, version, definitions, is_online, last_connect_time, updated_at)`
  - `hr_leak_stat(snapshot_at, all_client, risk_client)`（KPI 时序，供趋势）
  - `hr_sync_log(id, started_at, finished_at, ok, groups_n, clients_n, error)`（同步审计）
- **api.py（改）**：`_console_api` 挂 `/api/v1/console/huorong/*`（沿用 console 鉴权）：
  - `GET  /status`（enabled/连通状态/最近同步/规模）
  - `GET  /groups`、`GET /clients`（缓存表读取，支持 group_id 过滤 + 分页）
  - `GET  /risk`（leak KPI 最新值 + 风险终端明细）
  - `GET  /virus-events`（S3，P2）
  - `POST /sync`（手动触发同步，写 hr_sync_log）

### 3.2 同步策略

- 周期同步（默认 300s，单飞行守卫防重入）+ 手动触发；UI 永远只读缓存表，不透传火绒实时请求（控制台响应时延稳定，且避免对火绒服务端形成请求风暴）。
- `_list` 分页 limit=200（上限），按 `total` 翻页拉全量；示例规模 total=2000 → 每轮 ~10 次请求，负载可控。
- 增量优化（P2 可选）：以 `last_connect_time` 做脏终端过滤，减少全量翻页。

## 4. 凭据管理

- AccessKeyId / Secret 由用户在会话内提供，**禁止写入任何 git 跟踪文件/文档/日志**（红线）。
- 生产集成：Secret 经 settings SecretsBox AES 加密落库（`huorong.secret` 入 SENSITIVE_KEYS），读取仅内存解密，接口输出一律脱敏（前3+****+后4），零回显。
- AK 属标识符可明文落库；base_url 明文落库。
- 凭据轮换：控制台设置页支持更新 secret（写 settings 即生效，客户端对象重建）。

## 5. API 真实连通性验证（2026-09-15 实测记录）

### 5.1 验证范围与红线
仅只读 `/api/group/_list`（后经团队授权扩展 `_list`/`clnts/_list` 计划）；任务类/写操作接口**零调用**。凭据经环境变量注入临时脚本，脚本与命令行痕迹已清除，凭据零落盘。

### 5.2 实测结果
- **连通性：正常**。服务端返回 HTTP 200 + 标准信封 `{errno, errmsg}`，信封解析链路确认可用；HTTPS 自签证书以 `CERT_NONE` 通过（仅联调，生产方案见 §7.4）。
- **签名认证：未通过**。两轮共 15 种变体全部 `errno=1 Authentication failed`：

| 轮次 | 变体覆盖 |
|---|---|
| 第一轮（12） | Header/URL 双签名模式、签名 urlencode 与原文、expires +300s/+3600s/毫秒、`HRESS` 前缀带空格、CanonicalizedResource 带前导斜杠、Content-MD5 base64 与 hex 形态、签名串小写 method、省略 Content-MD5 头、Secret 小写 |
| 第二轮（3） | 以服务器 Date 头校准时间（实测时钟偏移仅 1s，排除时钟因素）、签名串 AK/expires 顺序互换、Date 校准+原文签名 |

### 5.3 结论（2026-09-15 18:35 更新：签名谜题已破案，实测打通）

- **定性修正**：此前「凭据被拒/平台总开关关闭」假设**均不成立**。真因 = **官方文档未写全的关键签名细节**，经用户提供官方参考测试脚本（api测试.py）实测破案——两对凭据（现行对 + 旧对）均 HTTP 200 + errno=0。
- **与文档的关键歧义点（31 变体未能命中的原因）**：
  1. 认证**全部走 URL 参数** `?ak=&expires=&sign=`——**无 Authorization 头、无 HRESS 前缀**（文档 Header 模式为无效描述；此前 15 个 Header 模式变体注定全败）；
  2. CanonicalizedResource **不带前导斜杠**（`api/group/_list` 而非 `/api/group/_list`——此前 URL 模式变体均带斜杠，差之毫厘）；
  3. 签名 quote 用**默认 safe='/'**（+/= 转义、斜杠保留）；
  4. expires = now + **86400**；body 为**紧凑 JSON**（separators=(',',':')）。
- 排查过程中排除的因素（方法论沉淀）：凭据（A/B 双组实验）、时钟偏移（1s）、代理劫持（环境无代理）、TLS 层、平台总开关；errmsg="Authentication failed" 为纯签名错，**零差异无分组价值**。
- 完整排查档案与取证指引存档：`docs/attachments/huorong_auth_forensics_guide.md`；实测算法客户端：`docs/attachments/huorong_api_client_draft.py`（已按实测算法重写）。

## 6. 试点任务实测结论（2026-09-15 已完成全量拉取）

### 6.1 实测规模与数据结构
- **分组**：89 组（13 根组 / 15 组含子级 / 12 空组），真实科室-楼层结构（防护组/1F视光诊室/收费处/检验科/行政电脑等）。
- **终端**：710 台全量（4 页 × 200 拉完）；字段含 client_id / client_name / computer_name / **local_ip / connect_ip 双 IP** / mac / group_id / is_online / os_version / version / last_connect_time。
- **分页语义（实测确认）**：`data` 内层含 `total` 字段（本次 710），翻页终止条件 = `offset >= data.total`（客户端已按此实现，含短页/空页防御）。
- **IP 字段语义**：local_ip ≠ connect_ip 仅 20/710（2.8%）→ **取用优先级 local_ip 为主**（终端业务 IP），connect_ip 仅作链路参考（多为跨网段/代理上报场景）。
- **MAC 质量**：唯一 MAC 698/710，重复 12 条（1.7%）→ 关联键去重策略：同 MAC 取 `last_connect_time` 最新一条。
- **OS 分布**（风险提示）：Win10 专业版 256 / 教育版 90 / 家庭中文版 79 / **Win7 旗舰版 59（EOL 风险）** / Win11 专业版 58。
- 分组名含 i18n 键残留（如 `i18n:db_groups_name:ungrouped` = 未分组），镜像时需键名规整。
- 脱敏样例归档：`docs/attachments/huorong_pilot_sample_20260915.json`（IP/MAC 尾4位掩码、终端名前2字符）。

### 6.2 与 EyeTerm 资产模型映射建议（试点后定稿）
- **关联键：MAC 归一化**（小写去分隔符；huorong `mac` ↔ uplink asset schema1 network 适配器 MAC）；同 MAC 多条取最新；次选 hostname 精确匹配兜底；未匹配终端归入「未关联」桶（不做推测性映射）。
- 火绒分组与 EyeTerm asset_groups **不合并**：安全分组只读镜像到 `hr_groups`（安全域视角，89 组量级全量同步无压力），避免双源写冲突。
- 同步开销评估：全量 710 终端 = 4 次分页请求，300s 同步周期完全无压力。

## 7. 安全考量

### 7.1 签名实现细节（2026-09-15 实测定稿，附件客户端已实现）
- **认证全部走 URL 参数**：`{HOST}/{path}?ak={AK}&expires={ts}&sign={sig}`——**无 Authorization 头、无 Content-MD5 头**（官方文档 Header 模式实测无效）。
- **资源路径不带前导斜杠**：`api/group/_list`；待签串 5 行：`AK \n expires \n POST \n Content-MD5 \n path`。
- `Content-MD5` = `base64(md5(请求体二进制))`；body 为**紧凑 JSON**（`json.dumps(payload, separators=(',',':'))`），MD5 对发送字节原文计算。
- 签名 = `urllib.parse.quote(base64(hmac-sha1(SK, 待签串)))`，**quote 用默认 safe='/'**（+/= 转义、斜杠保留）。
- expires = `int(time.time()) + 86400`（24h 窗口，实测通过；服务端时钟偏移 1s 可忽略）。
- 教训沉淀：官方文档与实际实现存在 4 处歧义（Header 模式无效/斜杠/quote safe/expires 窗口），**以官方参考脚本为准**；未来对接同类国产安全设备 API 时优先索取官方测试脚本。

### 7.2 错误码处理
| errno | 处理 |
|---|---|
| 0 | 正常 |
| 1 | 认证失败：**不重试**；置「凭据异常」状态并告警（连续 3 次后禁用同步防锁死） |
| 2 | 参数错误：不重试，按缺陷记录 |
| 3 | 服务端内部错误：指数退避重试 ≤2 次 |
| 4 | API 未授权：不重试；提示 AK 权限配置问题 |
| HTTP 层 | 连接/读超时 15s；网络类异常重试 ≤2 次；全失败置 degraded 状态 |

### 7.3 破坏性操作门禁（任务类）
- `huorong.py` 层硬门禁：`enable_tasks=False` 时 `create_task` 直接抛 `HuorongTaskDisabled`（代码级兜底，UI 不可达）。
- 未来开启（S6）必须：main/用户审批 + 控制台会话鉴权 + 目标终端白名单确认 + 操作留痕（who/when/目标/type）+ 单独审计表；**禁止任何自动/定时触发**。

### 7.4 TLS 自签证书
- 联调期：`CERT_NONE`（已实测可用），仅在隔离内网可接受。
- 生产（推荐）：控制台设置页配置火绒证书 **SPKI SHA-256 指纹 pin**（`huorong.tls_fingerprint`），传输前校验对端证书指纹，防中间人；备选：将火绒自签 CA 导入服务端信任库。

## 8. 分阶段实施建议与工作量

| 阶段 | 内容 | 交付 | 工作量 |
|---|---|---|---|
| P0 | 凭据修复复验（附件脚本即用） | errno=0 + 分组真实样例 | 0.5d（等凭据） |
| P1 | `huorong.py` 正式化 + settings/store 改造 + `/console/huorong/*` 只读 API + 试点同步（分组-设备映射） | 分组同步跑通、映射样例、hr_* 表 | 2d |
| P2 | 控制台「终端安全」概览（S1 安全徽章 + S2 漏洞 KPI + S3 病毒事件看板）+ 资产明细安全维度 + E2E | 安全板块可用 | 2d |
| P3 | 软件资产统计（S5）+ KPI 趋势（hr_leak_stat 时序图） | 合规视图 | 1d |
| P4 | 远程处置 S6（另行审批立项） | - | 不计入 |

合计（P0-P3）：约 **5.5 天**。P1 起代码归 server-platform 仓库，与 server-platform-dev 协调合入（动手前同步）。

## 9. 待确认清单（请 main 转呈用户）

1. ~~凭据修复~~（已解决：签名算法破案后两对凭据均实测通过）。
2. ~~脱敏尺度~~（已按默认执行：IP/MAC 尾4位掩码、终端名前2字符，样例已归档；如需调整口径请反馈）。
3. 「终端安全」控制台入口形态：独立菜单区块 vs 嵌入既有概览页（方案默认前者，P2 实施）。
4. S6 远程处置是否立项（默认不立项，保持硬门禁）。
5. Win7 旗舰版终端 59 台（试点实测）：是否纳入 P2 安全概览的专项风险提示。
