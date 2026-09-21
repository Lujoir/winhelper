# desktop-policy · 分阶段实施计划 v1

> 2026-09-16 提报 main 批准。P0 已完成（ADR-003），权限方案已定案（ADR-005）。

## P1 · 服务端「终端桌面管控」模块（约 2 天）

1. `server-platform/server/desktop_policy.py`：资源库管理（上传解析分辨率/宽高比/大小/sha256）、四类策略 CRUD + 校验、匹配推荐算法（exact → aspect_higher_res → default_fallback+告警）、revision 递增发布。
2. `store.py` 新表迁移（CONTRACT.md §3：dp_wallpapers / dp_policies / dp_deliveries）。
3. 路由两组：终端组 `/api/v1/terminals/{tid}/desktoppolicy/*`（X-ETP-Token）+ console 组 8 端点（对齐 huorong 形态）。
4. 下发记录全链路：publish→pending→delivered→applied/partial/failed/warn + error_code 留痕。
5. 分工：服务端实现与控制台 UI 归 server-platform-dev，desktop-policy-dev 提供契约与联调用例。
6. 验证：冒烟（上传/匹配三分支/发布/终端拉取/回传落库）+ 接口登记 api-registrar-dev。

## P2 · 终端引擎 + winhelper「锁屏及壁纸管理」（约 3 天）

1. `desktop_policy.py` 引擎（stdlib-only）：轮询拉取（120s±抖动）+ state.json/壁纸缓存/sha256 + 离线兜底（不可达保持上次配置不回退）；执行引擎（多屏拼接 GDI 性能路径+纯 Python 兜底、SPI 应用、CSP 经提权助手、powercfg 快照回写、GetLastInputInfo 超时锁屏）；自检三件套（ADR-004）+ RDP 跳过 + 重试 3 次退避 + report 回传；WM_DISPLAYCHANGE 重适配；资源红线 <50MB / 空闲 CPU<1%。
2. 主应用集成（628c210 先例）：bridge ROUTES 4 条 + index.html 菜单「锁屏及壁纸管理」（filesearch 之后）+ app.js switchTab 守卫。
3. `web/desktoppolicy.js`（dp 前缀）+ standalone 页 + STYLE.md 卡片基线 + E2E（pywebview 桩）+ 冒烟。

## P3 · 权限落地 + 真机验收（约 2 天）

1. 安装器注册计划任务 `/rl HIGHEST` 提权助手（写 HKLM PersonalizationCSP），schtasks /run 触发 + 状态文件取回；安装说明含安全软件放行指引（ADR-004 部署前提）。
2. 无拦截终端复测 spike A 应用环节 + 多分辨率实测报告（1080p 下发 4K 无变形无黑边、双屏异分辨率、4K、RDP 跳过）。
3. Win10 1809+ 与 Win11 双实测；powercfg /q 与注册表值对比报告。
4. 验收五条按规格第八节逐条过；exe/安装包由 main 统一重建。

## 里程碑

- 每阶段双仓提交，commit 哈希回报 team-lead；契约变更同步 api-registrar-dev。
- 当前：P0 完成，CONTRACT.md v1 冻结待 server-platform-dev 确认，P1 可启动。
