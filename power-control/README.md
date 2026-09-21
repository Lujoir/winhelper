# power-control · 终端自动开关机（观枢终端平台｜EyeTerm）

Windows 终端「自动开关机」子系统。本机快照只读采集（机型识别、BIOS 企业线
探测与解析、Windows 唤醒定时器、关机类计划任务、快速启动状态）+ 4.1.8 追加：
**定时开机=中心任务驱动**（连接门控 + 生效任务只读展示 + 个性化任务创建，
执行在中心侧）；**定时关机=本地配置本地执行**（schtasks 引擎不变，配置变更
版本化静默上报中心，触发点=连接成功/配置变更）。

- 权威规格：主仓 `docs/POWER_CONTROL_SPEC.md`
- 决策：`docs/DECISIONS.md`（ADR-001~013）
- 架构：`docs/ARCHITECTURE.md`

## 分线策略（定案）

| 线路 | 判定 | 能力 |
|---|---|---|
| 联想企业线（ThinkCentre/扬天） | LENOVO + `Lenovo_BiosSetting` 类存在 | 企业线可配置（读取 RTC 自动开机项） |
| 联想消费线（IdeaCentre/拯救者/GeekPro/天逸） | LENOVO + 类不存在 | 不支持远程配置（BIOS 人工设置指引） |
| 其它厂商 | 非 LENOVO | 不支持远程配置（未适配） |

## 快速使用

```powershell
# mock 单测（解析器/分线/CSV/重试/上报）
python tools\test_power_control.py

# 本机真实采集冒烟（消费线分支实测）
python tools\smoke_power.py

# E2E（Playwright，双场景 + 宽度自适应）
python tools\e2e_powercontrol.py
```

## 同步契约（发布到主应用）

| 本项目 | 主应用 |
|---|---|
| `power_control.py` | 根 `power_control.py` |
| `web/powercontrol.js` | `web/powercontrol.js` |

主应用改动（主仓承载）：`bridge.py` 11 条路由（8 既有 + center-tasks/
center-task-create/center-task-delete）、`web/index.html` 导航+面板、
`web/app.js` switchTab 守卫、`uplink.py` on_connected 监听器（连接成功触发
关机配置上报）。中心接口契约定稿（2026-09-19，boot-tasks 统一端点）见
DECISIONS.md ADR-013 附注。
