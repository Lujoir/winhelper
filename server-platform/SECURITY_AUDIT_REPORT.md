# 观枢终端平台（EyeTerm）· 上线前安全审计报告

审计范围：server-platform 服务端（api.py/auth_upgrade/store/secretsbox/storage/deploy）+ 桌面端（bridge.py/service.py/disk_cleanup/appdata_scan/desktop.py）。结论均经静态代码 + git 历史核实，未做运行态探针。

## 一、STRIDE 威胁建模摘要
- **伪装 Spoofing**：终端(X-ETP-Token)+控制台(PBKDF2会话)鉴权齐全；但 legacy HTTP 明文传 token、deploy SSH 不校验主机密钥，存在中间人冒用面。
- **篡改 Tampering**：SQL 全参数化、报告/静态资源经 _esc/normpath 防护；唯一风险点为 storage 的 shell=True 命令拼接。
- **抵赖 Repudiation**：登录/鉴权/会话/账号/改密/隔离操作均入库审计，明文口令不落审计，基本满足。
- **信息泄露 Information Disclosure**：machine.key 与 config.local.json 已被 .gitignore 排除且经 git 历史核实从未入库（已确认）；SecretsBox 加密静态配置；但 deploy 将凭据明文打印到日志。
- **拒绝服务 DoS**：IP 限速/账号锁定/8MB body 上限齐备，慢哈希放大已在设计上规避（限速前置）。
- **提权 Elevation**：角色强制回库复核(require_admin/resolve_session 均回查 status/role)、末管理员保护完备；交换机硬编码默认口令为凭据提权面。

## 二、OWASP Top 10 检查表
- A01 失效访问控制：**通过** — 端口作用域分离(api.py:212/243)、白名单准入(:418)、会话回库鉴权、admin-only 回查(auth_upgrade.py:1058)。
- A02 加密失败：**失败** — legacy 18090 明文全量路由(app.py:368-371)；deploy SSH 无主机密钥校验(deploy.py:98)。
- A03 注入：**部分失败** — SQL 全参数化通过；storage.py:17/63/74 `shell=True` 且 root_dir 经 settings 拼入命令。
- A04 不安全设计：**部分失败** — 过渡期明文端口双开、部署日志泄密。
- A05 安全配置错误：**失败** — 0.0.0.0 全网卡绑定+防火墙放行明文端口；交换机硬编码默认口令。
- A06 脆弱组件：**通过/不适用** — 纯标准库、零三方依赖；paramiko 锁定 3.5.1。
- A07 认证失败：**通过** — PBKDF2-HMAC-SHA256(320000)+常量时间比较+锁定+复杂度+历史+强制改密+超时。
- A08 软件数据完整性：**通过** — SecretsBox encrypt-then-MAC；无不可信反序列化(pickle/yaml 全库零命中)。
- A09 安全日志审计：**通过** — 审计库完整、失败归一化、口令脱敏。
- A10 SSRF：**通过/不适用** — 未发现用户可控 URL 出站；LLM url 来自 settings。

## 三、分级发现
🔴 **严重**
1. `app.py:368-371` + `deploy.py:455/464`：legacy HTTP 18090 以"full routes"明文暴露，且防火墙持久放行。整管理/终端 API 口令与 token 可被同网嗅探/重放。→ 上线前 `legacy_http.enabled=false` 并下墙 18090，仅留 TLS 18443/8443。
2. `deploy.py:98` `paramiko.AutoAddPolicy()` 不校验 SSH 主机密钥，部署通道可被 MITM 窃取 ETP_SSH_PASS 接管服务器。→ 改用 RejectPolicy + 部署前校验已知指纹。

🟠 **高危**
3. `storage.py:17/63/74` `shell=True`，`storage.root_dir`/`smb.mount_cmd` 来自 settings，经 `POST /console/settings`(api.py:960) 可注入 `findmnt`/`mount` 命令。→ 改列表式 subprocess(shell=False)+路径白名单。
4. `deploy.py:407` 交换机默认口令 `3@Ww18_Bu9xn` 硬编码源码；未设 ETP_SW_DEFAULT_PWD 即以固定口令播种。→ 移除硬编码、强制必填或随机生成并脱敏回显。

🟡 **中危**
5. `deploy.py:251/387/596` 将 terminal_token/console_password/ftp 口令明文打印到部署日志。→ 仅受保护文件回显一次，禁 stdout 明文。
6. `api.py:78-80` `ApiContext.login` 遗留明文 `==` 比较，与 PBKDF2 改造并存易致混淆。→ 删除遗留路径，统一 auth.authenticate。
7. `api.py:389` `_client_download` 文件名直插 Content-Disposition，未转义引号。→ RFC5987/引号转义或 _safe_filename。

🟢 **低危**
8. `desktop.py:69` pywebview `js_api` 未设 allowed_origins/CSP，bridge 能力极强(删文件/重启/关机)，本地页一旦 XSS 即桥接 RCE。→ 加 CSP + allowed_origins，复核本地页。
9. `_tp_gate.py:9` shell=True 开发残留(未部署)建议清理；server 全端口 0.0.0.0 绑定暴露面较宽。

## 四、上线阻塞项清单（go-live 必须修复）
- [P0] 关闭 legacy HTTP 18090 明文端口并下墙（发现1）
- [P0] deploy SSH 启用主机密钥校验（发现2）
- [P1] storage 改列表式调用、移除 shell=True（发现3）
- [P1] 移除交换机硬编码默认口令（发现4）

## 五、未能访问/未读到的文件
- `run_ai_analysis`/`gen_certs.py` 完整实现（LLM 提示注入、证书私钥落地链路口径）未细读。
- `console/index.html`(2700行) 与桌面端 `web/index.html` 全文未逐行核验（已抽查前端 esc 用法一处，建议补全量 XSS 复核）。
- `disk-cleaner/` 子模块版本（根目录 disk_cleanup/appdata_scan 已验证白名单成立；子模块同声称待核）。
- 运行态探针（无部署服务器/终端），结论均静态核实，未做动态验证。

## 六、已确认安全项（正向结论）
- `machine.key`(32字节随机)、`config.local.json`(含 dev-token/dev-console) 经 `.gitignore` 排除且 `git log --all` 核实从未入库；仓库仅含公开的 `assets/platform_ca.pem`(CA 公钥)。
- `secretsbox.py` encrypt-then-MAC(HMAC-SHA256) 构造正确、密钥 0600、常量时间校验，威胁模型自洽。
- `auth_upgrade.py` 为高质量实现：PBKDF2 320k 迭代、常量时间、防枚举、锁定、RBAC、会话 SHA-256 落库、强制改密。
- `bridge.py` 路由为静态字典查找，无 `eval/exec` path；`disk_cleanup.py`/`appdata_scan.py` 删除/迁移均走服务端白名单前缀校验，README 白名单声明属实。
