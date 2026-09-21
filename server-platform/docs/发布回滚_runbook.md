# EyeTerm 发布与回滚 Runbook

> 安全改造 R1 · H5 配套文档。覆盖标准发布、失败自动回滚、手工回滚三套动作与判据。

---

## 0. 前置条件（每次发布前确认）

| 项 | 要求 | 校验方式 |
|---|---|---|
| SSH 主机指纹已登记 | `deploy/known_hosts` 含目标 `主机:端口` 指纹 | `python tools/record_hostkey.py --host <IP> --port <PORT>` 重新采集比对 |
| 部署凭据 | `ETP_SSH_*` 环境变量或 `tools/.prod_ssh.json` | `python tools/rollback.py verify` |
| CA 资产有效 | `assets/platform_ca.pem` 与生产 `ca.crt` 同指纹且未临近过期 | `python tools/check_ca_asset.py --ref server-platform/deploy/certs/ca.crt` |
| systemd 特权端口能力 | unit 含 `AmbientCapabilities=CAP_NET_BIND_SERVICE`（管理口 443 < 1024） | `grep AmbientCapabilities /etc/systemd/system/terminal-platform.service` |

> 443 管理口为特权端口。若 unit 缺该能力，restart 后会 `bind 443 errno13` 崩溃而**旧进程仍在**，
> 极易误判为网络问题（2026-09-19 实证）。

---

## 1. 标准发布

```bash
# 1) 门禁（本地）
python -m py_compile <changed files>
python tools/smoke.py                 # 需本地 dev 服务；ETP_IPERF_FAKE=1
python tools/e2e_console.py           # 控制台 E2E

# 2) 部署（自动备份 → 上传 → 迁移 → 防火墙 → 健康检查）
python deploy/deploy.py --host <IP> --port <PORT> --user <USER>

# 3) 部署后校验
python tools/rollback.py verify
```

`deploy.py` 的关键行为：
- `[3] backup`：将线上 `server/`、`console/`、`config.json` 复制到 `backups/pre_<ts>/`
- `[8] health check`：端口监听 + legacy/TLS 双口 health + 跨类 404；**任一失败即自动回滚**
  （见下节），随后退出并打印回滚结果

---

## 2. 健康检查失败时的**自动回滚**（deploy.py 内置）

判定点（任一不通过即触发）：
- 15s 内端口未监听
- legacy HTTP health / console 页面非 200
- TLS 终端口 health 非 200 或跨类非 404
- TLS 管理口页面/health 非 200 或跨类非 404

行为：
1. 从本轮 `backups/pre_<ts>/` 恢复 `server/`、`console/`、`config.json`
2. `systemctl restart terminal-platform`，等待 2s 后检查 `is-active`
3. 打印 `[health] 自动回滚：<结果>`；成功时提示"已回退上一版，排查后重试"
4. 退出码非 0

**注意**：首次部署（无 `pre_<ts>` 备份）时无可回滚，会如实打印"无可用备份"。

---

## 3. 手工回滚（`tools/rollback.py`）

```bash
# 1) 列出可用备份（新→旧）
python tools/rollback.py list

# 2) 预览（不加 --yes 只打印目标，不执行）
python tools/rollback.py rollback pre_20260919_070000

# 3) 执行回滚（恢复 server/ console/ config.json → 重启 → health 复验）
python tools/rollback.py rollback pre_20260919_070000 --yes

# 4) 回滚后确认
python tools/rollback.py verify
```

判据：`PASS: 已回滚到 <备份> 且服务健康`（服务 active + legacy health 200）。
失败时备份目录**不会被删除**，可再次回滚或手工修复。

---

## 4. 回滚后验证清单

| 项 | 期望 |
|---|---|
| 服务状态 | `systemctl is-active terminal-platform` = active |
| 端口监听 | 18090 / 18443 / 443 三口 |
| legacy health | `http://<IP>:18090/api/v1/health` = 200 |
| TLS 终端口 | `curl --cacert <ca.crt> https://<IP>:18443/api/v1/health` = 200 |
| TLS 管理口 | `curl --cacert <ca.crt> https://<IP>:443/` = 200（**管理口已迁 443**） |
| 终端心跳 | 控制台终端页 `last_seen` 持续刷新 |
| 业务抽查 | 控制台登录、AI 分析、终端详情（见改造方案 5.3 回归清单） |

---

## 5. 常见问题

| 现象 | 原因 | 处置 |
|---|---|---|
| `未配置 SSH 主机指纹，已拒绝连接` | `deploy/known_hosts` 缺该主机或指纹变更 | `python tools/record_hostkey.py --host <IP> --port <PORT> --write` 重新采集并核对；服务器重装后属预期 |
| `SSH 主机密钥指纹不匹配` | 可能中间人或服务器重装 | **不要**图省事用 `ETP_SSH_INSECURE=1`；先在受控网络确认新指纹 |
| 部署后 443 无监听、进程反复重启 | unit 缺 `CAP_NET_BIND_SERVICE` | 补 `AmbientCapabilities=CAP_NET_BIND_SERVICE` → `daemon-reload` → restart（模板已固化，见 commit 431166c） |
| `自动回滚：无可用备份（首次部署…）` | 首次部署 | 人工修复后重试；后续部署即有备份 |
| health 200 但终端全部离线 | 端口/路由分离或 token 变更 | 检查 `uplink_config.server_url` 指向 18443 + 指纹一致 |
| 回滚后 config.json 被旧版本覆盖导致新配置键丢失 | 备份含旧 config | 从**当前** config 备份恢复差异键，或在回滚后重新执行幂等迁移（`deploy.py` 只补缺键、不覆盖既有值） |

---

## 6. 安全约定

- 凭据只经环境变量或 gitignored 文件传递；**禁止**写入仓库、日志、提交信息
- 测试载荷禁用真实攻击串（如 `rm -rf`），一律良性化（如 `; echo injected`）
- 回滚是**受控降级**动作：回滚后须查明根因，禁止"回滚即结案"
