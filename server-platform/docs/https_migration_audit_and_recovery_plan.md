# HTTPS 迁移状态审计与恢复实施方案

> 版本：v1.0（2026-09-19 00:20）｜ 状态：**待 main 批准后执行** ｜ 执行人：https-migration-dev
> 触发：用户指出口令改造（管理后台 HTTPS 迁移）未生效；main 实测 443 无监听、console 与终端仍共用 http://…:18090。
> 关联：ADR-032（双 HTTPS 监听）+ docs/https_migration_deployment.md（部署说明 v1.0）。

---

## 一、审计结论（SSH 实查 2026-09-19 00:05–00:20，证据齐备）

**服务端双 HTTPS 监听完整在位且健康——"未生效"的真相不是服务端缺失，而是三层收口未做。**

### 1.1 服务器实况（172.17.5.215，systemd active，pid 1185510）

| 项 | 实测 | 判定 |
|----|------|------|
| 监听 | 0.0.0.0:18090 HTTP legacy（全量路由）/ 0.0.0.0:18443 TLS terminal / **0.0.0.0:8443 TLS console** | ✅ 三监听齐 |
| 8443 console | 页面 200（观枢终端平台管理控制台）、/api/v1/console/* 未认证 401、跨类 POST 404（scope 隔离正确） | ✅ 健康 |
| 18443 terminal | TLS health 200（main 亦实测 manifest 200） | ✅ 健康 |
| 防火墙 | runtime = permanent：8443/18443/18090/21232/… 双态一致 | ✅ |
| TLS 强度 | TLS1.2 握手 ECDHE-RSA-AES256-GCM-SHA384；TLS1.0 正确拒绝（alert 70） | ✅ |
| 证书 | CN=zljtest5.215，SAN=DNS:zljtest5.215+IP:172.17.5.215；证书至 **2031-09-10**，CA 至 **2036-09-08**；私钥 0600 | ✅ |
| config.json | terminal_port=18443 / console_port=8443 / tls.enabled / legacy_http.enabled=true（幂等迁移已生效） | ✅ |
| 生产代码 | app/server/app.py v1.3.0 含 ADR-032 装配（与本地一致） | ✅ |
| CA 指纹 | 服务端 ca.crt = 本地 deploy/certs/ca.crt = 客户端内置 assets/platform_ca.pem = `733c1039…02126010b` 三方一致 | ✅ |
| 443 | 无监听、无防火墙——**443 从未在既定方案内**（ADR-032 = 8443） | ⚠️ 缺口见 1.2 |

### 1.2 "未生效"根因：三层收口缺口

| # | 缺口 | 事实 |
|---|------|------|
| A | **端口记忆错位** | 用户访问 https://172.17.5.215/（443）→ 拒绝连接。方案端口实为 8443（用户 2026-09-11 拍板，ADR-032 记录），用户当前记忆为 443。二者冲突，需 main/用户裁决 |
| B | **证书信任未落地** | 管理员机器未导入 ca.crt（部署说明"三段验证"第 3 步未执行）——即便访问 :8443 浏览器也报证书不受信 |
| C | **入口未迁移** | 管理员日常仍用 http://…:18090/（legacy 全量路由在服务）；全部存量终端 server_url 仍为 http://…:18090 |

### 1.3 终端能力矩阵（服务端 DB 实测 5 台）

| 终端 | 版本 | 状态 | TLS 能力 |
|------|------|------|----------|
| Jun-office-PC | 4.1.7 | 在线 | ✅ |
| DESKTOP-K4K8SI0 / 维护-0003 | 4.1.6 ×2 | 在线 | ✅ |
| 13F-xx-3（M720t） | 4.1.3 | 关机（8470s） | ✅（09-17 构建） |
| 13F-MYH | 4.0.0 | 离线 7.6 天 | ❌ 生产包不含 TLS（09-09 构建，早于 09-11 TLS commit 0f17626） |

客户端 `server_url` 零硬编码（全部来自 uplink_config）→ 终端迁移 = 改配置，无需改代码。

---

## 二、决策点：管理口端口 443 vs 8443（请 main 裁决）

| 方案 | 内容 | 代价 |
|------|------|------|
| **B1（推荐）** | console_port 8443 → **443**，8443 退役 | 零代码改动：config 一键 + systemd `AmbientCapabilities=CAP_NET_BIND_SERVICE`（eyeterm 非 root 可 bind 443）+ 防火墙 443；8443 无存量依赖（用户从未使用过），退役零影响 |
| B2 | 8443 与 443 并存（双管理口） | 需 app.py 支持 console_ports 数组（~15 行 + 单测 + 门禁 + restart）；8443 无实际用户，并存价值存疑 |

推荐 B1：直接满足用户"https://172.17.5.215/"不带端口的访问习惯；18443 终端口与 18090 legacy 完全不动。

---

## 三、实施方案 B1（批准后执行，预计 15 分钟）

### 3.1 变更步骤（生产 172.17.5.215）

1. 备份：`cp config.json config.json.bak.<ts>`；`cp /etc/systemd/system/terminal-platform.service …bak.<ts>`
2. config.json：`console_port: 8443 → 443`（仅此一键）
3. unit `[Service]` 追加一行：`AmbientCapabilities=CAP_NET_BIND_SERVICE`
4. 防火墙：`firewall-cmd --permanent --add-port=443/tcp && firewall-cmd --reload`（8443 暂留，回滚窗口内不设障）
5. `systemctl daemon-reload && systemctl restart terminal-platform`

### 3.2 验证清单

- `ss -tlnp`：443/18443/18090 三监听（8443 消失）
- 本机 `curl -sk https://127.0.0.1/` → console 页 200；`/api/v1/console/terminals` → 401；跨类 POST → 404
- journal：TLS cert loaded + listening 行正常，无异常栈
- 终端：terminals 表 last_seen 持续增长（restart 后心跳 ≤2 拍自动恢复）
- 外部（导入 ca.crt 后）：`https://172.17.5.215/` 锁标正常 → 登录 → 终端列表正常

### 3.3 回退（<1 分钟）

config 改回 8443 + unit 还原 + daemon-reload + restart；防火墙 8443 本就放行。管理员侧回退 = 改用 http://…:18090/（legacy 常开）。

### 3.4 审计留痕

变更前后 config/unit/防火墙快照 + journal 监听装配行 + 三端口健康输出，归档 docs/ 并随验收报 main。

---

## 四、终端与入口迁移时序（收口路线）

| 阶段 | 动作 | 状态 |
|------|------|------|
| 0（现在） | 18443 TLS + 18090 HTTP 并存；终端全走 18090；管理口按第二节切换 | 服务端就绪 |
| 1 | 管理员机导入 ca.crt（一次性运维，用户本人操作，main 下发指引）→ 管理入口切 https://172.17.5.215/ | 待执行 |
| 2 | 4.1.8 客户端发布：新装机默认地址 = https://…:18443 + server_ca_fingerprint=733c…（4.1.8 由 power-control-dev 线交付） | 计划内 |
| 3 | 存量 4.1.3/4.1.6/4.1.7（均含 TLS 能力）经命令通道逐台下发改 uplink_config（server_url=https://…:18443 + 指纹），每台观察 last_seen 连续 + 18443 心跳 200；失败即时回改 http://（秒级生效） | 逐台灰度 |
| 4 | 13F-MYH（4.0.0 无 TLS）：唤醒/回归后先升级客户端 ≥4.1.8，再走阶段 3 | 挂起 |
| 5 | **收口**：全部终端走 18443 后，config `legacy_http.enabled=false` → 18090 下线（防火墙随后关闭）；管理口旧 8443 防火墙条目同步清理 | 全量完成后 |

---

## 五、对在线终端与 M720t 唤醒链路影响评估

- **变更仅触碰管理口端口与 systemd unit 能力位，不触碰 18443/18090 路由域** → 终端心跳/命令通道/升级拉包零影响。
- restart 中断秒级：心跳失败 1–2 拍后自动恢复（客户端退避重试 30–60s）；命令通道无长连接。
- **M720t 明早 07:30 WoL**：调度持久化于 wol_schedules 表，进程重启后调度器重新武装，无空窗。时窗纪律：**今晚执行须在 01:00 前，或延后至明早 08:30 后**；避开 06:30–08:30。
- WoL 跨网段语义（wol_relay 中继为主通道）与本次变更无交集。

---

## 六、收口后的剩余事项（不阻塞批准）

1. console API 暴露证书过期天数（cert_not_after 已在启动日志，补 console 展示可选增强）
2. api-registrar-dev 登记：443（console TLS）/18443（terminal TLS）/18090（legacy 过渡）三入口台账
3. ADR-032 补遗：管理口 8443→443 决策记录 + 用户记忆错位澄清
4. 服务器 data 目录历史备份 certs_backup_20260911_182701 与 deploy 临时文件按保留策略处置

---

## 七、批准请求

请 main 批准：① 管理口端口裁决（B1 443 / B2 并存）；② 批准后 15 分钟内完成 3.1–3.2 并回报验证证据。批准前不动生产。
