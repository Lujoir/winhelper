# 阶段 0 · 生产只读盘点报告（R1 前置）

| 项 | 内容 |
|---|---|
| 执行时间 | 2026-09-19 11:44（主盘点） / 11:50（settings 补采） |
| 执行方式 | `tools/inventory_tls_readiness.py`（SSH 只读；凭据经 gitignored 文件注入，不回显） |
| 目标 | 172.17.5.215（应用根 `/data/terminal-platform`） |
| 性质 | 只读查询 + 打包备份；**未修改任何配置、未重启服务、未变更防火墙规则** |
| 原始数据 | `tools/_s0_inventory_out.json` |

---

## 1. 采集结果

### A 端口监听（单进程 pid=1335073）

```
0.0.0.0:18090  明文 legacy（scope=all）
0.0.0.0:18443  TLS 终端口
0.0.0.0:443    TLS 管理口   ← 由 8443 迁移后
```

### B 终端清单（5 台）

| 终端 | 版本 | IP | 最近心跳 | TLS 能力 |
|---|---|---|---|---|
| WIN-Jun-office-PC | 4.1.7 | 172.17.90.215 | 10s（在线） | 有 |
| WIN-DESKTOP-K4K8SI0 | 4.1.6 | 172.17.90.6 | 55s（在线） | 有 |
| WIN--0003 | 4.1.6 | 172.17.7.53 | 56s（在线） | 有 |
| WIN-13F-xx-3 | 4.1.3 | 172.17.90.18 | ~2h（离线） | 有 |
| **WIN-13F-MYH** | **4.0.0** | 172.17.90.21 | **~8 天（离线）** | **无** |

版本分布：`4.1.7×1 / 4.1.6×2 / 4.1.3×1 / 4.0.0×1`

### C settings 表（敏感值仅记长度）

| 键 | 值 |
|---|---|
| `storage.root_dir` | `/data/terminal-platform/storage` |
| `smb.mount_cmd` | (unset) |
| `iperf.server_ip` | 172.17.5.215 |
| `iperf.path` | (unset，走默认 /usr/bin/iperf3) |
| `llm.url` / `llm.model` | https://llm.eye.ac.cn / DeepSeek-V4.1 |
| `switch.default_username` | reader |
| `llm.api_key` / `switch.default_password` / `ftp.password` | set(len=132 / 80 / 88) —— 均为**加密落库** |
| `terminal_token` / `console_password` | unset（存于 config.json，符合设计） |

### D config.json 关键键

```json
{"port": 18090, "terminal_port": 18443, "console_port": 443,
 "tls": {"enabled": true, "min_tls_version": "TLSv1_2"},
 "legacy_http": {"enabled": true},
 "session_ttl_hours": 8, "heartbeat_timeout_sec": 180}
```
敏感项存在性：`terminal_token`、`console_password` 均存在（值未读取）。

### E 明文流量占比 —— **未测出（需替代手段）**

iptables INPUT 链无 18090/18443 计数规则（环境为 firewalld/nftables），两次采样均为空。
→ 需改用：① 临时纯计数规则（项目已有先例 `tools/_verify_stage2.py`，无 `-j` 不阻断）；或 ② 直接用"软收口"实测（见第 3 节）。

### F systemd 能力与防火墙放行

- unit：`User=eyeterm` + **`AmbientCapabilities=CAP_NET_BIND_SERVICE`**（commit `431166c` 的远端修复已生效，与部署模板一致 —— 无单边状态）
- 防火墙放行端口：
  ```
  443/tcp  8443/tcp  9990/tcp  10050/tcp  18090/tcp  18121/tcp
  18122-18141/tcp  18200-18299/tcp  18443/tcp  21232/tcp  18200-18299/udp
  ```

### G 备份

```
/data/terminal-platform/backups/_s0_pre_r1_20260919_114427.tar.gz
大小 1,752,470,491 B（≈1.67 GB，含 config.json + data/）
```

---

## 2. 盘点发现的问题

| # | 发现 | 影响 | 处置 |
|---|---|---|---|
| 1 | **`8443/tcp` 仍放行**（管理口已迁 443，8443 无服务监听） | 遗留暴露面：防火墙为其保留开放条目 | 列入 H2 收口清理项（与 18090 同批） |
| 2 | **WIN-13F-MYH 为 4.0.0 且离线 8 天** | **无 TLS 能力**，是 H2 收口的硬阻塞；离线状态也无法远程升级 | 需用户决策：升级后纳管 / 确认停用并从在册清单剔除 |
| 3 | 明文流量占比未取得 | 无法提前量化"还有几台走明文" | 改用临时计数规则或直接软收口实测 |
| 4 | `smb.mount_cmd` 未配置 | H6 中"mount_cmd 注入"路径当前不可达，但 `storage.root_dir` 已配置且同样经 shell 拼接 → **H6 仍需修**（注入面收窄但未消除） | 维持 H6 排期 |
| 5 | `WIN-13F-xx-3`（4.1.3）离线 ~2h | 有 TLS 能力，唤醒后可纳入灰度 | H2 灰度时一并处理 |

---

## 3. H2 收口建议（基于盘点事实）

1. **在线 3 台（4.1.7/4.1.6×2）**：可直接经命令通道下发 `uplink_config`（`server_url=https://172.17.5.215:18443` + CA 指纹），逐台观察 `last_seen` 连续 + 18443 心跳 200，失败秒级回改。
2. **离线 2 台**：`WIN-13F-xx-3`（4.1.3）唤醒/上线后切换；`WIN-13F-MYH`（4.0.0）需先升级客户端 ≥4.1.8（**需用户决策**）。
3. **收口判据不依赖精确计数**：采用软收口——防火墙 DROP 18090 → 观察 24h → 无掉线再关 `legacy_http` 并下墙；同时清理遗留 8443/tcp。
4. **管理口能力依赖已被证实具备**（`CAP_NET_BIND_SERVICE` 已声明），后续任何重启不会重现 09-19 凌晨的 bind 443 崩溃。

---

## 4. 阶段 0 出口核对

| 出口条件 | 状态 |
|---|---|
| 终端就绪度盘点 | 完成（发现 4.0.0 阻塞项） |
| 生产 settings / config 快照 | 完成 |
| 门禁基线固化 | 完成（单测 21/22 绿，1 项既有红；冒烟 48/48；e2e_console 206/206） |
| 备份 | 完成（1.67 GB，远端 backups/） |
| 明文流量量化 | **未完成**（改用软收口实测替代） |
