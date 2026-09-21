# 火绒 API 认证排查档案与接口日志取证指引

- 日期：2026-09-15
- 维护：huorong-dev
- 关联：`huorong_integration_plan.md` §5、`huorong_api_client_draft.py`（复验工具）
- 红线执行：凭据仅环境变量注入会话，零落盘；本文档所有构造描述均用占位符（AK/SK 不出现）；破坏性接口零调用

---

## 1. 实验档案（三轮 + A/B 对照，共 47 次只读请求）

验证端点：`POST /api/group/_list`（第三轮含 1 次 `POST /api/clnts/_list` 交叉验证）。
服务端行为恒定：HTTP 200 + `{errno:1, errmsg:"Authentication failed"}`。

### 1.1 第一轮（15 变体，现行凭据 B）

覆盖：Header/URL 双签名传输、签名 urlencode 与原文、expires +300s/+3600s/毫秒、`HRESS` 前缀带空格、CanonicalizedResource 带前导斜杠、Content-MD5 base64 与 hex、签名串小写 method、省略 Content-MD5 头、Secret 小写、服务器 Date 头校准（时钟偏移实测 1s）、签名串 AK/expires 顺序互换。

### 1.2 第三轮定向矩阵（16 变体，A/B 双组同跑）

| 变体 | 构造差异点（占位描述） | A 旧对(2026-02-02) | B 现行对(2026-09-15) |
|---|---|---|---|
| R00_std_noproxy | 标准构造（文档逐字），绕过系统代理 | errno=1 | errno=1 |
| R00_std_withproxy | 标准构造，走系统代理（对照分离代理因素） | errno=1 | errno=1 |
| R01_expires_ttl300 | expires 用 TTL(300) 而非绝对时间戳 | errno=1 | errno=1 |
| R02_key_ak_colon_sk | HMAC key = "AK:SK" 拼接整体 | errno=1 | errno=1 |
| R03_sts_with_sk_line | 签名串插入 SK 行（AK\nSK\nexp\nPOST\nmd5\ncanon） | errno=1 | errno=1 |
| R04_sts_akcolon_first | 签名串首段 AK:SK | errno=1 | errno=1 |
| R05_sts_no_md5_seg | 签名串省略 Content-MD5 段（4 段式） | errno=1 | errno=1 |
| R06_auth_no_expires | Authorization 无 expires（HRESS AK:sig） | errno=1 | errno=1 |
| R07_sig_b64url | 签名 base64url（-_/）编码 | errno=1 | errno=1 |
| R08_sig_strip_pad | 签名去 padding（rstrip =） | errno=1 | errno=1 |
| R09_url_sign_safe | URL 传输 sign 仅转义保留 +/= | errno=1 | errno=1 |
| R10_url_named_hress_signature | URL 参数名 hress_signature | errno=1 | errno=1 |
| R11_body_empty | 空 body（MD5 对空串） | errno=1 | errno=1 |
| R12_ct_plain | Content-Type 无 charset | errno=1 | errno=1 |
| R13_md5_header_hex | Content-MD5 头 hex 形态（签名串仍 b64） | errno=1 | errno=1 |
| R14_auth_comma | Authorization 逗号分隔 | errno=1 | errno=1 |
| R15_crosscheck_clnts_list | 标准构造交叉验证 /api/clnts/_list | errno=1 | errno=1 |

errmsg 原文：全部变体两组均为 `Authentication failed`（英文，**零差异**——errmsg 无分组线索）。

### 1.3 环境因素排除证据

- **代理劫持**：环境变量探测 HTTP(S)_PROXY/ALL_PROXY 全空；绕过代理与走代理结果一致 → 排除 TLS 拦截代理剥改鉴权头。
- **时钟偏移**：以服务器 Date 头校准后偏移仅 1s → 排除时间窗问题。
- **TLS 层**：自签证书 `CERT_NONE` 通过，HTTP 200 + 标准信封 → 请求确实到达 API 应用层。
- **凭据**：A/B 两组（不同时期生成）同变体同错误 → **彻底排除凭据因素**（含"新 Key 未生效"假设——旧 Key 同拒）。

## 2. 判定结论

按 A/B 判定矩阵：**凭据因素排除完毕**。剩余假设空间仅两支：

1. **平台总开关/服务层门禁**（首选假设）：火绒控制台「API 接口」区块存在启用总开关，关闭时对所有请求（无论签名对错）统一返回 errno=1。此假设与"31 种签名变体全拒 + errmsg 零差异"高度自洽——任何签名变体都无法区分"签名错"与"服务没开"。
2. **未文档化的签名细节**：无法盲扫穷尽（组合空间无限），需日志证据指引方向。

## 3. 《服务端接口日志取证指引》（请用户从火绒侧取证）

### 3.1 取证步骤（按优先级）

**步骤 1：确认 API 启用总开关**
- 打开火绒控制台 →「API 接口」区块 → 区块内第一项（截图中被弹窗遮挡处）；
- 确认其是否为启用开关、当前状态是否为「开启」；
- 若为关闭态 → 开启后无需我们重试矩阵，直接告诉我们，跑标准构造单变体即可复验。

**步骤 2：查看「接口日志」（最直接证据源）**
- 「API 接口」区块 →「接口日志」菜单；
- 时间过滤：今天 13:00–15:00（我们测试请求的时间窗）；
- 请求特征（便于定位我们的测试）：
  - 接口：`/api/group/_list`（少量 `/api/clnts/_list`）
  - 方法：POST；来源：用户本机出口 IP；客户端：`Python-urllib/3.x`
  - 数量级：今天约 47 条
- 记录每个字段的值，尤其「结果/状态/原因」类字段——拒绝原因文案直接指向根因。

**步骤 3：判定分支**
- 日志**有记录且有拒绝原因** → 把原因文案截图/抄录给我们（可脱敏），定向修复；
- 日志**有记录但无原因字段** → 抄录接口名/时间/来源等全部可见字段；
- 日志**完全无记录** → 说明请求未进 API 处理层或日志不含拒绝请求 → 强化总开关假设，回到步骤 1；
- 日志菜单本身无法打开/无权限 → 请截图「API 接口」区块完整页面（弹窗关掉后再截），我们按可见配置项逐项核对。

### 3.2 可选旁证（如控制台支持）

- 若「API 接口配置」页有「IP 白名单 / 调用方限制」类配置：核对是否限制了调用方 IP；
- 若有「接口权限分配」：确认该 AK 是否被授权访问分组/终端类接口（对应 errno=4 场景，但部分实现可能归并到 errno=1）。

## 4. 复验工具

- 第三轮矩阵脚本：`tools/_hr_verify_r3.py`（凭据环境变量注入，可整体复跑）；
- 正式客户端雏形：`docs/attachments/huorong_api_client_draft.py`（凭据修复后 `python huorong_api_client_draft.py` + 环境变量即完成单变体复验）。
