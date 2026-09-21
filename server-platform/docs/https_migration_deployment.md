# HTTPS 专项改造 · 部署说明（ADR-032）

> 版本：v1.0（2026-09-11）｜ 状态：**待 main 批准后执行** ｜ 执行人：https-migration-dev
> 依据：用户批准《变更方案_端口分离与HTTPS改造》+《可行性评估报告》；管理端口 HTTPS 8443（用户拍板）、终端 TLS 18443（可调）、自建 CA、旧 HTTP 18090 并行过渡。

---

## 一、改造摘要

| 端口 | 协议 | 路由域 | 用途 |
|------|------|--------|------|
| 8443  | HTTPS(TLS1.2+) | console（仅 /api/v1/console/*、静态、health） | 管理员控制台入口 |
| 18443 | HTTPS(TLS1.2+) | terminal（仅 /api/v1/terminals/*、/api/v1/ai/analyze、health） | 终端上行 API |
| 18090 | HTTP | all（全量，不变） | legacy 过渡，收口后下线 |

- 证书：自建 CA（RSA3072/10y）签发服务器证书（RSA2048/5y，SAN=DNS:zljtest5.215 + IP:172.17.5.215）；**私钥全程不出服务器**（远端 openssl 生成）。
- 终端侧双层校验：①内置 CA 文件 SHA256 指纹比对（fail-closed）②TLS 证书链+主机名验证（CA 锚定）。
- 兼容与回滚：http:// 地址过渡期完全兼容；回滚 = 终端改回 http://172.17.5.215:18090 秒级生效。

## 二、变更文件清单（本地仓库，均已过门禁）

| 文件 | 变更 |
|------|------|
| server/api.py | dispatch 增加 scope 参数 + `_scope_allowed` 端口路由白名单（鉴权前 404） |
| server/app.py | v1.3.0：三监听装配、TLS context（min TLS1.2 + no-compression）、tls_settings/cert_not_after（过期监控）、证书缺失时 legacy 独活 |
| deploy/gen_certs.py | 新增：证书生成（local cryptography 模式 + 远端 openssl 命令序列） |
| deploy/deploy.py | [5a] 证书远端生成/轮换/下载、[5b] config HTTPS 键幂等迁移、防火墙 8443/18443 --permanent、[8] 健康检查三端口+跨类 404 |
| tools/smoke.py | HTTPS 冒烟组 [13]（ETP_TLS_* / ETP_CA_FILE 环境变量驱动） |
| tools/test_https_split.py | 新增单测 27 项（scope 17 + tls_settings 6 + context/notAfter 4） |
| tools/spike_dual_https.py | P0 spike-1 存档（14/14） |
| tools/spike_meipass_tls.py | P0 spike-2 存档（onefile _MEIPASS 三层 PASS） |
| tools/_init_smoke_auth.py | 隔离冒烟 auth 库初始化工具 |
| docs/DECISIONS.md | ADR-032 落档 |

## 三、门禁结果（全部全绿）

1. P0 spike-1（双 HTTPS 监听/并发/跨类 404/TLS1.2）：**14/14**
2. P0 spike-2（PyInstaller onefile _MEIPASS CA + urllib 指纹校验）：**3 层 PASS**
3. 单测 test_https_split.py：**27/27**
4. 冒烟 smoke.py（隔离环境，本地三监听 + HTTPS 组）：**55/55**
5. py_compile 全部改动文件：通过；零 lint 错误

## 四、部署步骤（生产 172.17.5.215）

### 前置检查（部署脚本自动执行）
- 端口勘察：18090（自身服务豁免）/ 8443 / 18443 无外来占用
- firewalld active 状态确认；磁盘余量

### 执行命令（PowerShell，凭据仅环境变量，零落盘）
```powershell
$env:ETP_SSH_HOST="172.17.5.215"; $env:ETP_SSH_PORT="21232"
$env:ETP_SSH_USER="root"; $env:ETP_SSH_PASS="<会话内提供，零落盘>"
python deploy/deploy.py            # 默认含证书生成 + 防火墙 + config 迁移
# 可选：--rotate-certs 强制换服务器证书（CA 不动）；--skip-certs/--skip-firewall
```

脚本行为要点：
1. 远端备份现网 server/ console/ config.json（带时间戳）后才上传
2. 远端 `data/certs/` openssl 生成 CA+证书（私钥 0600；旧证书存在则先备份）
3. 输出 **CA SHA256 指纹**（终端 uplink_config.server_ca_fingerprint 同值），ca.crt 下载至本地 `deploy/certs/ca.crt`
4. config.json 幂等迁移：补 `terminal_port=18443 / console_port=8443 / tls{...} / legacy_http{enabled:true}`（**只补缺键，绝不覆盖既有值**）
5. 防火墙 8443/18443 TCP --permanent + --reload + 双重复核（runtime + permanent）
6. systemd restart + 健康检查：三端口 ss 监听、legacy 200、**curl --cacert 自 CA 验证** TLS health 200、跨类 404 断言（console 口 POST 终端路由 = 404）

### 三段生产验证（部署后执行）
1. **只读冒烟**：`python tools/smoke.py`（env：ETP_API_BASE=http://172.17.5.215:18090、ETP_TLS_TERMINAL_BASE=https://172.17.5.215:18443、ETP_TLS_CONSOLE_BASE=https://172.17.5.215:8443、ETP_CA_FILE=deploy/certs/ca.crt、ETP_AI_MOCK=1）→ 期望 55/55
2. **真实终端心跳验证**：终端 uplink_config.server_url 改 `https://172.17.5.215:18443` + server_ca_fingerprint=<部署输出的指纹> → 主页平台接入卡显示在线（心跳 200、注册正常、命令通道可达）
3. **管理端 8443 登录验证**：浏览器 `https://172.17.5.215:8443/` → 证书链可信（导入 ca.crt 到管理员机器「受信任的根证书颁发机构」，一次性运维动作）→ 登录 → 终端列表/详情正常

### 审计留痕
- 部署输出（备份路径/指纹/防火墙复核）+ 三段验证结果回传 main 归档

## 五、回滚方案

| 场景 | 动作 | 生效时间 |
|------|------|----------|
| 终端 HTTPS 不通 | 终端 uplink_config.server_url 改回 `http://172.17.5.215:18090` | 秒级（下一拍心跳） |
| 管理端 8443 异常 | 管理员改用 `http://172.17.5.215:18090/`（legacy 常开） | 即时 |
| 服务端整体回退 | 恢复部署备份目录（backups/pre_<ts>）+ systemctl restart | <1 分钟 |
| 仅关 TLS | config `tls.enabled=false` → restart（legacy 独活） | <1 分钟 |

## 六、正式 CA 换装清单（当前为开发自签 CA，可直接投产；如需换发）
1. 服务器：`deploy.py --rotate-certs`（或手动替换 data/certs/*）
2. 终端：替换打包资产 `assets/platform_ca.pem` = 新 ca.crt + exe 重新构建分发
3. 终端：`uplink_config.server_ca_fingerprint` = 新 CA SHA256（hex 小写无冒号）
4. 管理员机器：重新导入新 ca.crt 到受信任根

## 七、已知事项（不阻塞部署）
- 11.6 iperf 冒烟组需 `ETP_IPERF_FAKE=1`（既有测试约定：服务端 iperf.path 默认 Linux 路径），与 HTTPS 改造无关
- 8443/18443 仅监听 IPv4（0.0.0.0），与现网 18090 口径一致
- 控制台 WS/长连接：无（fetch 轮询架构），keep-alive 已实测
