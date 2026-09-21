# 观枢终端平台｜EyeTerm · 服务端

服务端用于**辅助管理终端系统**：终端性能监测、故障辅助分析、瓶颈识别。
当前接入终端以 Windows 为主（winhelper 客户端），协议层预留 Linux / 安卓终端。

- 项目记忆（ADR）：`docs/DECISIONS.md` —— **开发前必读**
- 架构与协议：`docs/ARCHITECTURE.md`

## 快速开始（本地开发）

```powershell
# 1. 生成本地开发配置（不入库，已被 .gitignore 排除）
@'
{ "port": 18090, "terminal_token": "dev-token", "console_password": "dev-console" }
'@ | Out-File -Encoding utf8 server/config.local.json

# 2. 启动服务（任意 Python >= 3.7）
python server/app.py

# 3. 冒烟测试
$env:ETP_API_BASE = "http://127.0.0.1:18090"
$env:ETP_TERMINAL_TOKEN = "dev-token"
python tools/smoke.py
```

## 目录结构

```
server-platform/
├── docs/
│   ├── DECISIONS.md          # ADR 项目记忆
│   └── ARCHITECTURE.md       # 架构 / 协议 v1 / API 一览
├── server/
│   ├── app.py                # 入口：ThreadingHTTPServer + 后台保留策略清理
│   ├── api.py                # REST /api/v1/* 分发 + 鉴权 + HTML 报告
│   ├── store.py              # SQLite 存储层（terminals/metrics/events/bottlenecks）
│   └── analysis.py           # 瓶颈识别规则引擎（阈值与终端端一致）
├── console/
│   └── index.html            # Web 控制台（深色主题，零依赖，canvas 曲线）
├── deploy/
│   ├── deploy.py             # SFTP 部署 + systemd + 防火墙持久化 + 健康检查
│   └── terminal-platform.service
└── tools/
    ├── smoke.py              # 端到端冒烟（注册→上报→瓶颈→控制台→报告）
    └── ssh_run.py            # SSH 辅助（凭据仅从环境变量读取）
```

## 部署

```powershell
# 凭据仅经环境变量临时注入（严禁写入任何文件）
$env:ETP_SSH_HOST = "..."; $env:ETP_SSH_PORT = "21232"
$env:ETP_SSH_USER = "root"; $env:ETP_SSH_PASS = "..."
python deploy/deploy.py            # 首次部署自动生成远端 config.json 并打印 token/口令
```

部署内容：python39 venv → `/data/terminal-platform/` → systemd（terminal-platform.service，
开机自启/异常重启）→ 防火墙 `--permanent` 放行 18090 → 健康检查。

## 安全红线

1. **凭据零落盘**：SSH 密码 / terminal_token / console_password 一律不入仓库；
   远端配置仅存于 `/data/terminal-platform/config.json`（0600，部署时生成）。
2. 防火墙变更**必须持久化**（firewalld --permanent 或 iptables-save），部署后复查。
3. 上行 API 用 token 鉴权；控制台独立口令；对外输出脱敏。
4. 服务端代码兼容 python3.6/3.9（禁用 3.10+ 语法）。

## 工程约定

- 每次改动：`python -m py_compile server/*.py tools/*.py` → 冒烟 → git commit（中文消息）
- 重要决策追加至 `docs/DECISIONS.md` 并 commit
- 与终端端（winhelper / perf-analyzer / log-inspector）接口变更先报备 main 再实施
