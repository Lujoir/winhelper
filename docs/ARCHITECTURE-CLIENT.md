# 观枢终端平台｜EyeTerm · Windows 终端侧应用架构档案

> 维护者：archivist-dev ｜ v1.0 ｜ 2026-09-08 ｜ 事实来源：主应用代码（接口注明出处）
> 子项目（perf-analyzer/、disk-cleaner/、log-inspector/、server-platform/）仅在「同步契约」节说明。

## 1. 应用概述

- 正式名称：观枢终端平台｜EyeTerm（工程代号 winhelper）
- 形态：C/S 桌面端单文件 exe（dist\winhelper.exe ~26.3MB，双击即用）
- 技术栈：Python 3.12 + pywebview（WebView2）+ 原生 HTML/CSS/JS（Chart.js 本地副本）；PyInstaller onefile（winhelper.spec）
- 能力边界：Windows 终端本机管理（磁盘/性能/日志/资产）+ 平台对接（资产上报/命令通道）

## 2. 进程与运行形态

- 入口 desktop.py：pywebview 窗口加载 web/index.html，注入 bridge.ApiBridge 为 js_api；exit hook 供「以管理员重启」（perf_service.register_exit_hook）
- 双进程：Python 主进程（业务/采集/心跳）+ WebView2 子进程（UI）
- onefile 启动：资源解压 %TEMP%；异常退出可能残留（见已知问题）
- 前端→后端唯一通道：window.pywebview.api.call → bridge.ApiBridge.call() 按 ROUTES 分发；无本地 HTTP 端口

## 3. 运行环境要求

- Windows 10/11 x64（实测 Win11 专业工作站版 26200）；WebView2 Runtime（Win11 内置）
- Python 依赖随 exe 打包：psutil、pywin32、pywebview/pythonnet/clr_loader
- 随包资源（winhelper.spec datas）：web/ 前端、perf-analyzer/libs/*.dll（温度）、libs/iperf3/*
- 离线可用：磁盘/性能/日志全本机；仅平台接入需内网（http://172.17.5.215:18090）

## 4. 模块地图

### 4.1 桥接层 bridge.py（唯一路由表 ROUTES）

- /api/loginspector/*（access·search·export-start·export-status·export-cancel·analyze·report-export·knowledge）→ log_service.py（日志诊断）
- /api/disk/*（overview·scan·scan-status·scan-cancel·cleanup·open-location·tree·drives）→ service.py→disk_cleanup.py
- /api/appdata/*（scan·drives·migrate·delete）、/api/installers/scan → service.py→appdata_scan.py
- /api/perf/*（snapshot·record-*·stress-*·hwinfo·temps·app-config·restart-admin）→ perf_service.py
- /api/perf/uplink/*（status·save·register）→ uplink.py
- /api/home/network → home_service.py

call(path)：解析 path+query → ROUTES 分发 → 处理器(params)->dict。import 时执行 uplink_autostart()。

### 4.2 服务层

- service.py：disk/appdata 处理器转发（纯转发至 disk_cleanup/appdata_scan）
- perf_service.py：实时快照/记录/压测/硬件/温度/应用配置。温度采样后端节流（间隔取 app_config.json 的 temperature_interval_sec 默认 300，范围 30-3600）；hwinfo CIM 采集；子进程 CREATE_NO_WINDOW 红线
- home_service.py：主页网络配置。PowerShell 强制 UTF8 采集 + ipconfig /all 权威解析（DNS 多行/网关/DHCP，兼容中英文）合并，psutil 降级，缓存 60s
- log_service.py + log_reader.py：日志检索/txt 导出/智能分析/知识库（win32evtlog，utf-8-sig 流式）
- disk_cleanup.py / appdata_scan.py：磁盘扫描清理/应用数据迁移/安装包（task_id+轮询+取消）
- uplink.py：平台接入心跳线程（见第 5 章）

## 5. 外联配置

- 平台接入 uplink：配置文件 LOCALAPPDATA 下 winhelper\uplink_config.json（enabled/server_url/token/terminal_id/heartbeat_interval）；token 仅落本机，状态接口永不回显
- 协议（X-ETP-Token 鉴权，30s 心跳，服务端 interval 覆盖）：register（hwinfo 摘要+asset 明细 schema1）/ heartbeat（返回待执行命令与 interval）/ metrics（psutil 快照）/ commands result（回执）
- 命令白名单（ADR-015）：iperf_client（内置 iperf3）/ collect_logs（v1 hook）/ ai_context（禁用回执）/ net_probe（网关 ping/TCP）
- 服务端准入：register 强制 IP 白名单 fail-closed（空名单拒 403）；已注册终端豁免；token 错 401、非白名单 403 记审计
- 失败退避：指数退避重试，网络恢复自动重连

## 6. 功能清单（前端入口 → bridge 路由 → 后端 → 数据源）

### 6.1 主页（默认激活）
- 终端配置卡：hwinfo（响应为 success+hwinfo 包裹结构）+ uplink/status
- 硬件状态卡：snapshot 30s 实时 + temps 节流缓存 + hwinfo 静态规格（GPU/磁盘/内存条）
- 本地网络配置卡：/api/home/network（ipconfig 权威解析合并，含 DHCP 行）
- 刷新按钮：hmRefreshAll 全量重载

### 6.2 日志诊断
- 检索：loginspector/search（类别+时间+级别+来源+关键字，分页+统计卡+双图）
- txt 导出：export-start/status/cancel 任务模式（utf-8-sig 流式，近 3 天默认）
- 智能分析：analyze（FAULT_PATTERNS）+ report-export（HTML 单文件）
- 知识库建议：knowledge；本机信息面板：hwinfo

### 6.3 磁盘清理
- 概览/扫描/清理/打开位置/树/盘列表（disk/*）；应用数据迁移（appdata/*）；安装包清理（installers/scan）
- 任务管理器模式：task_id + 前端轮询 + 可取消

### 6.4 性能分析
- 实时指标 snapshot（1s 轮询+60s 曲线）；长记录 record-*（JSONL 落盘+分析+导出 HTML）
- 压测 stress-*（四阶段 60s 内：磁盘 25/CPU 15/内存 10/GPU 8）
- 硬件规格 hwinfo（CIM 三级降级）；温度 temps（管理员 CPU + nvidia-smi GPU）
- 平台接入设置（uplink）+ 温度采样间隔（app-config）+ 以管理员重启 restart-admin

### 6.5 设置齿轮（全局）
- 中心平台配置：server_url/token 保存即注册启用（uplink/save）
- 温度采样间隔：app-config（30-3600s，默认 300，热生效）

## 7. 资源占用与静默策略

- 温度采样：后端节流（_temps_last 缓存），间隔 300s 默认可配，子进程开销降至约 1/150
- 前端守卫：document.hidden 跳过实时刷新；perf 页离开即停轮询（ADR-007）
- metrics：随心跳同拍上报（10s 以上节流）；采集全部请求驱动、无常驻高频线程

## 8. 已知问题与待办

- onefile 解压残留：异常退出累积，待启动清扫
- 托盘常驻未实现（最小化即任务栏）；UI/后台分离双进程待立项
- CIM 首采慢（10-20s）：主页硬件信息首载延迟，注册资产首次可能走降级兜底
- 管理员 CPU 温度依赖 LHM：非管理员显示需管理员提示；杀软可能拦 WinRing0
- 温度采样节流依赖前端请求驱动：无前端调用时缓存不更新（uplink metrics 温度可能滞后）
