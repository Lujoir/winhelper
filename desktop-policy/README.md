# desktop-policy · 锁屏及壁纸管理（桌面管控专项）

观枢终端平台｜EyeTerm · Windows 终端桌面管控子系统。

- 权威规格：主仓 `docs/DESKTOP_POLICY_SPEC.md`（2026-09-16 用户批准）
- 四类策略：桌面壁纸 / 锁屏壁纸 / 电源计划 / 超时锁屏；按终端组下发 + 定时轮换
- 壁纸自适应：多屏拼接（每屏独立）、cover 缩放裁剪（不变形不黑边）、WM_DISPLAYCHANGE 重适配
- 技术红线：Python 3 标准库 only（Win32 全 ctypes）、凭据零硬编码、简体中文 UI 零实现细节、常驻内存 <50MB 空闲 CPU <1%、Win10 1809+ 与 Win11 双实测、RDP 会话防误应用

## 目录

```
desktop-policy/
├── docs/DECISIONS.md   # ADR 决策记录
├── tools/              # P0 spike 与验证脚本
├── web/                # P2：前端（dp 前缀）
└── desktop_policy.py   # P2：终端执行引擎
```

## P0 spike（当前阶段）

```powershell
# 仅枚举显示器布局（零改动）
python tools/spike_multimon_wallpaper.py --enum-only
# 完整拼接实测（临时改壁纸，结束自动还原）
python tools/spike_multimon_wallpaper.py
# 保留拼接壁纸供人眼复核
python tools/spike_multimon_wallpaper.py --keep

# 锁屏 CSP 实测（需管理员；无管理员时仅只读检测）
python tools/spike_lockscreen_csp.py

# 电源计划备份还原实测（结束自动还原）
python tools/spike_powercfg_backup.py
```

全部 spike 默认备份现场并在结束时还原；`--keep`/`--lock` 为人工验收辅助开关。
