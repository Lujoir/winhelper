# 观枢终端平台服务端 · 架构与协议

> 项目：观枢终端平台（EyeTerm）服务端
> 定位：辅助管理终端系统——终端性能监测、故障辅助分析、瓶颈识别
> 终端形态：Windows 为主（winhelper 客户端），协议层预留 Linux / 安卓

---

## 1. 总体架构

```
┌──────────────┐   X-ETP-Token    ┌─────────────────────────────────────────┐
│  终端(Windows)│ ──── REST ────▶ │  服务端 18090 (python39 + stdlib)        │
│  winhelper   │  /api/v1/*      │  app.py ← 路由/静态/后台任务              │
├──────────────┤                 │  api.py ← 终端上行 / 控制台 API          │
│ 终端(Linux/   │                 │  analysis.py ← 瓶颈规则引擎              │
│ Android 预留) │                 │  store.py ← SQLite(WAL) + 保留策略       │
└──────────────┘                 └───────┬─────────────────────────────────┘
                                         │ fetch (X-ETP-Console-Token)
                                 ┌───────▼──────────┐
                                 │ 控制台(纯静态 SPA)│ ← 浏览器（深色主题）
                                 │ console/index.html│
                                 └──────────────────┘
```

- 运行时：`/data/terminal-platform/venv`（python39），纯标准库（ADR-002）
- 存储：`/data/terminal-platform/data/eyeterm.db`（SQLite，WAL，ADR-003）
- 配置：`/data/terminal-platform/config.json`（部署时生成/注入，凭据不入仓库，ADR-005）
- 服务：`terminal-platform.service`（systemd，开机自启，ADR-007）

## 2. 配置文件（远端 config.json，不进仓库）

```json
{
  "port": 18090,
  "terminal_token": "<部署时随机生成>",
  "console_password": "<部署时随机生成>",
  "session_ttl_hours": 8,
  "heartbeat_timeout_sec": 180,
  "retention_days": {"metrics": 30, "events": 90, "bottlenecks": 90},
  "bottleneck_dedup_min": 10
}
```

## 3. 终端接入协议 v1

### 3.1 通用约定

- 基址：`http://<server>:18090/api/v1/`
- 编码：JSON / UTF-8；成功 `{"ok": true, ...}`；失败 `{"ok": false, "error": "..."}` + HTTP 4xx/5xx
- 鉴权：上行一律带请求头 `X-ETP-Token: <terminal_token>`
- 兼容：未知 JSON 字段忽略（向前兼容）；`metrics` 支持对象或数组（批量预留）；请求体支持 `Content-Encoding: gzip`（预留）
- 版本化：每条上行必须携带 `terminal_id`、`terminal_type`(windows/linux/android)、`client_version`、`ts`(unix 秒)

### 3.2 注册

```
POST /api/v1/terminals/register
{
  "terminal_id": "WIN-PC001",       // 终端唯一标识（客户端生成，建议 hostname+机器码）
  "terminal_type": "windows",
  "hostname": "PC001",
  "os_info": "Windows 10 Pro 22H2",
  "client_version": "1.0.0",
  "ip": "10.1.2.3"                  // 可选，缺省取连接源地址
}
→ 200 {"ok": true, "registered": true}   // 幂等：重复注册即更新
```

### 3.3 心跳

```
POST /api/v1/terminals/{terminal_id}/heartbeat
→ 200 {"ok": true, "interval": 60, "report_interval": 60}
```

### 3.4 指标上报（对齐 winhelper perf snapshot）

```
POST /api/v1/terminals/{terminal_id}/metrics
{
  "terminal_type": "windows",
  "client_version": "1.0.0",
  "ts": 1725600000,
  "cpu": {"percent": 37.5},
  "mem": {"used_percent": 62.0, "available_percent": 38.0, "used_mb": 4820, "total_mb": 7780},
  "swap": {"used_percent": 5.0},
  "disks": [
    {"mount": "C:", "used_gb": 120.5, "total_gb": 237.9, "percent": 50.6, "busy_percent": 12.0}
  ],
  "volumes": [...]
}
→ 200 {"ok": true, "bottlenecks": []}    // 服务端规则命中时返回本次新识别的瓶颈
```

### 3.5 事件上报

```
POST /api/v1/terminals/{terminal_id}/events
{
  "level": "warn",                  // info|warn|error|critical
  "category": "service",            // 自由分类，如 service/disk/crash
  "message": "人类可读描述",
  "detail": {}                      // 可选结构化补充
}
→ 200 {"ok": true, "event_id": 123}
```

## 4. API 一览

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/api/v1/health` | 无 | 健康检查（部署/监控探针用） |
| POST | `/api/v1/terminals/register` | X-ETP-Token | 终端注册/更新（幂等，payload 可带 hwinfo + asset 资产明细） |
| POST | `/api/v1/terminals/{tid}/heartbeat` | X-ETP-Token | 心跳 |
| POST | `/api/v1/terminals/{tid}/metrics` | X-ETP-Token | 指标上报（单条/批量） |
| POST | `/api/v1/terminals/{tid}/events` | X-ETP-Token | 事件上报 |
| POST | `/api/v1/ai/analyze` | X-ETP-Token | 终端侧发起 AI 分析 |
| POST | `/api/v1/terminals/{tid}/ai/diagnose` | X-ETP-Token | 终端 AI 智能诊断：推送六类日志包+问题概述 → LLM 链出结论（同步 ≤120s，ADR-023） |
| POST | `/api/v1/console/login` | 无（用户名+口令） | 控制台登录换会话 token（响应含 role） |
| GET | `/api/v1/console/session-info` | X-ETP-Console-Token | 当前会话信息（username/role） |
| GET | `/api/v1/console/terminals` | X-ETP-Console-Token | 终端列表（含在线状态） |
| GET | `/api/v1/console/terminals/{tid}` | X-ETP-Console-Token | 终端详情（最新指标 + asset 资产明细） |
| GET | `/api/v1/console/terminals/{tid}/metrics?minutes=60` | X-ETP-Console-Token | 指标曲线数据 |
| GET | `/api/v1/console/events?limit=100&terminal_id=` | X-ETP-Console-Token | 事件查询 |
| GET | `/api/v1/console/bottlenecks?limit=100&terminal_id=` | X-ETP-Console-Token | 瓶颈查询 |
| POST | `/api/v1/console/bottlenecks/{id}/ack` | X-ETP-Console-Token | 瓶颈确认 |
| GET | `/api/v1/console/report/{tid}?hours=24` | X-ETP-Console-Token | 导出 HTML 报告 |
| POST | `/api/v1/console/password` | X-ETP-Console-Token | 自助改密（吊销其它会话） |
| GET/POST/PUT/DELETE | `/api/v1/console/sysadmin/users[...]` | admin | 账户管理（列表/建户/角色状态/删除/重置口令，ADR-021） |
| GET/POST | `/api/v1/console/sysadmin/llm[/test]` | admin | 算力网关读写 + 连通性测试（ADR-021） |
| GET/POST/PUT/DELETE | `/api/v1/console/sysadmin/third-party[...]` | admin | 第三方接口登记 CRUD/启停（ADR-021） |
| GET/POST | `/api/v1/console/sysadmin/tokens[...]` | admin | 终端 token 生成/轮换/停用/启用（ADR-021） |
| GET/POST/PUT/DELETE | `/api/v1/console/kb[...]` | X-ETP-Console-Token | 知识库条目 CRUD/分类搜索/版本/回滚（ADR-022，操作人=会话用户名） |
| GET | `/` 及静态资源 | 无 | 控制台页面 |

> 终端 token 鉴权（ADR-021）：config.json 的 terminal_token **或** terminal_tokens 表 status='active' 的 token 命中均放行；控制台 sysadmin 路由组整组 admin-only（`require_admin` 实时复核角色）。
> 路由表数据源（ADR-022）：`GET /terminals/{tid}/netdoctor/route-nodes` 优先读 kb_entries category='route_nodes' 最新条目（source=kb），解析失败/无条目回退 settings netdoctor.route_nodes（source=settings）。预置条目「路由表 · 关键节点」（kb_id=route-nodes）随服务初始化幂等生成。

## 5. 瓶颈规则（v1 内置，ADR-006）

| 规则 | 条件 | 说明 |
|------|------|------|
| cpu_saturation | cpu.percent > 85 | CPU 饱和 |
| mem_saturation | mem.available_percent < 10 | 内存可用不足一成 |
| disk_saturation | 任一磁盘 percent > 80（busy_percent > 80 同判，字段预留） | 磁盘饱和 |
| swap_pressure | swap.used_percent > 50 | 换页压力（warning 事件级） |

去重：同终端同规则 10 分钟窗口内不重复落库。规则表驱动，`analysis.py` 追加条目即可扩展。

## 6. 数据保留

metrics 30 天 / events 90 天 / bottlenecks 90 天；后台线程每日 03:30 清理，启动时补清理一次。

## 7. 部署与运维

- 部署：`python deploy/deploy.py`（SSH/SFTP 凭据走环境变量 `ETP_SSH_*`；先备份远端原文件 → 上传 → venv → systemd → 防火墙 `--permanent` → 健康检查）
- 冒烟：`python tools/smoke.py`（环境变量 `ETP_API_BASE`、`ETP_TERMINAL_TOKEN`；注册→3 次上报→事件→瓶颈验证→控制台 200）
- 日志：journald（`journalctl -u terminal-platform`）
- 备份：直接拷贝 `/data/terminal-platform/data/eyeterm.db`（部署脚本自动带时间戳备份 app 文件）
