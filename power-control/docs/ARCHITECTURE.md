# power-control · 架构（ARCHITECTURE）

> 项目：观枢终端平台｜EyeTerm · Windows 终端「自动开关机」子系统
> 权威规格：`docs/POWER_CONTROL_SPEC.md`；决策：`docs/DECISIONS.md`
> 状态：P0（只读快照）已实施；P1 写入/还原、P2 平台批量下发未开工。

## 1. 总体链路（P0）

```
┌─ 终端（winhelper 主应用） ──────────────────────────────┐
│ web/powercontrol.js (pc 前缀)                            │
│   → apiFetch /api/powercontrol/snapshot                  │
│ bridge.py ROUTES → power_control.handle_pc_snapshot      │
│   → collect_snapshot()（五段只读采集）                    │
│   （打开菜单自动上报/手动上报）→ PlatformReporter          │
└──────────────────────┬───────────────────────────────────┘
                       │ POST /api/v1/terminals/{tid}/powercontrol/snapshot
                       │ X-ETP-Token
┌─ 平台（server-platform） ───────────────────────────────┐
│ api.py 终端分支 → power_control.PowerControlStore        │
│   → power_snapshots 表（时间线存档）                      │
│ api.py 控制台组 → 按 terminal_id 查最新/历史              │
└──────────────────────────────────────────────────────────┘
```

## 2. 目录

| 路径 | 说明 |
|---|---|
| `power_control.py` | 终端侧引擎：机型识别 / BIOS 企业线探测与解析 / 唤醒定时器 / 关机计划任务 / 快速启动，全部只读 |
| `web/powercontrol.js` | 前端（`pc` 前缀隔离）；同步契约 → 主应用 `web/powercontrol.js` |
| `web/powercontrol-standalone.html` | 独立测试页（自包含样式，STYLE.md 基线） |
| `tools/test_power_control.py` | mock 单测（解析器/分线判定/CSV 解析/重试/快照组装/上报） |
| `tools/smoke_power.py` | 本机真实采集冒烟（消费线分支实测） |
| `tools/e2e_powercontrol.py` | Playwright 双场景 E2E + 宽度自适应断言 |

## 3. 同步契约（子项目 → 主应用）

| 子项目源 | 主应用目标 |
|---|---|
| `power_control.py` | 主应用根 `power_control.py` |
| `web/powercontrol.js` | `web/powercontrol.js` |

主应用集成点（子项目只提供内容，改动落主仓）：`bridge.py`（2 条路由）、`web/index.html`（导航/section/script/自愈 INITS）、`web/app.js`（switchTab 守卫 1 行）。

## 4. 快照 JSON（schema 1）

```{ schema, collected_at, collected_ts,
  machine:  { hostname, manufacturer, model, system_family,
              vendor_line, vendor_line_text, capability, capability_text },
  bios:     { remote_configurable, wmi_class_found, reason, items: [{item,value}],
              rtc: {alarm, alarm_on, time, user_time, date, day, weekdays{},
                    after_power_loss, wake_on_lan, cycle_text, summary} },
  wake_timers:    { ok, need_admin, count, items:[{type,owner,wake_time,reason,author,description}], error },
  shutdown_tasks: { ok, count, items:[{name,next_run,status,action,schedule_type}], error },
  fast_startup:   { registry_present, hiberboot_enabled, available, enabled, note },
  errors: [ "...", ... ] }
```

- `capability`：`enterprise_configurable`（企业线可配置）｜`not_supported`（不支持远程配置）
- **capability 枚举约定（2026-09-16，与 server-platform-dev 对齐）**：取值**只增不改**——既有两态语义冻结，后续若出现中间态（P1 展望，如 BIOS 密码未配置时的 `enterprise_auth_required`）只允许新增枚举值；消费方（控制台 UI 徽章）对未知值必须兜底为中性样式并展示 `capability_text` 原文，不得因未知值丢弃快照。
- RTC 项（企业线，M720t 实测契约 v2，ADR-005）：`alarm/alarm_on/time/user_time/date/day/weekdays{}/after_power_loss/wake_on_lan/cycle_text/summary`；仅实测命中项出现，不虚构。
- 唤醒定时器双格式解析（M720t 实测格式 A `[SERVICE] ... 设置的计时器在 ... 过期` + 经典格式 B）；非管理员返回 `need_admin=true`。
- 测试夹具禁真实关机命令串（ADR-018 惯例，用 `stub_shutdown_sim.exe` 占位）。

## 5. 服务端

| 端点 | 说明 |
|---|---|
| `POST /api/v1/terminals/{tid}/powercontrol/snapshot` | 终端上报（X-ETP-Token + 准入），存 `power_snapshots` 时间线 |
| `GET /api/v1/console/powercontrol/terminals/{tid}/snapshots?latest=1&limit=` | 控制台查最新/历史（控制台会话鉴权） |

存储：`server-platform/server/power_control.py`（自持连接 + WAL，desktop_policy 模块同款形态），表 `power_snapshots(id, terminal_id, collected_ts, created_ts, snapshot)`。

## 6. 性能与安全边界

- 常驻内存：无独立常驻进程（按需采集，随主应用进程）；采集一次 2~8s（schtasks /v 占大头）。
- P0 零写操作：无 BIOS 写、无计划任务写、无 shutdown 调用；重试与日志只读操作。
- 凭据零硬编码；日志不落 token/口令。
