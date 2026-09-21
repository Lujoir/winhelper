# Architecture — Performance Analyzer（观枢终端平台｜EyeTerm · 性能分析模块）

```
perf-analyzer/
├── docs/DECISIONS.md          # ADR（项目记忆）
├── docs/ARCHITECTURE.md       # 本文件
├── perf_service.py            # 服务层（框架无关，handle_* 约定同主应用）
├── web/perf.js                # 前端逻辑（唯一事实来源，同步至主应用 web/）
├── web/perf-standalone.html   # 独立测试页（E2E 载体，自带精简样式）
├── vendor/chart.umd.min.js    # Chart.js 本地副本（standalone 用；主应用已有全局副本）
└── tools/e2e_perf.py          # Playwright E2E（standalone 全流程 + 主应用遍历）
```

## 服务层 API（perf_service.py）

| 处理器 | 路由（主应用 bridge.py 挂载） | 说明 |
|---|---|---|
| `handle_perf_snapshot` | `/api/perf/snapshot` | 实时快照：CPU%（cpu_times delta）/频率/核心数、内存、swap、各物理盘速率/IOPS/busy%、各卷容量 |
| `handle_perf_record_start` | `/api/perf/record-start` | 开始记录：interval ∈ {1,2,5,10}s 默认 2；daemon 线程 JSONL 增量落盘；重复开始幂等复用 |
| `handle_perf_record_status` | `/api/perf/record-status` | 轮询：状态/时长/样本数/最近瞬时样本/文件大小 |
| `handle_perf_record_stop` | `/api/perf/record-stop` | 停止 + 流式分析 → 报告（统计/饱和段/瓶颈排序/硬件评估）|
| `handle_perf_record_report` | `/api/perf/record-report` | 最近一份报告（内存或记录目录 analysis.json）|
| `handle_perf_record_export` | `/api/perf/record-export` | 导出 Markdown 到记录目录，返回路径（前端配 `/api/disk/open-location` "打开位置"）|

返回值约定：可 JSON 序列化 dict，`success: true/false` + `error`，与主应用一致。

## 数据流

```
[实时] perf.js --1s--> /api/perf/snapshot --> 60点滚动曲线 + 指标卡（离开菜单自动停轮询）
[记录] 开始 -> daemon线程 --interval--> perf_YYYYMMDD_HHMMSS.jsonl（LOCALAPPDATA/winhelper/perf_records）
[停止] join线程 -> 逐行流式分析（坏行跳过）-> avg/p95/max/min + 饱和连续段 + 瓶颈排序 + 硬件结论
[交付] UI 报告卡片 + 导出 Markdown 到记录目录 + 打开位置（复用主应用 open-location）
```

## 集成点（winhelper 主应用，最小增量）

1. `perf_service.py` 复制到主应用根目录；`web/perf.js` 复制到主应用 `web/`
2. `web/index.html`：导航栏磁盘清理后加"性能分析"按钮 + `tab-perf` section（perf 特有样式在 section 内部 `<style>`）
3. `bridge.py`：`from perf_service import handle_perf_snapshot, ...` + ROUTES 5 条 `/api/perf/*`（record-report/export 共 6 条）
4. `web/app.js`：switchTab 末尾 `if (tab === "perf" && typeof initPerfTab === "function") initPerfTab();`
5. `requirements.txt`：`psutil>=5.9`
6. exe 打包：PyInstaller import 分析自动收录 psutil

## 平台接入（v4 uplink，ADR-017）

`uplink.py` 独立于 Web 框架的终端接入模块，协议依据 server-platform ADR-015/016/019（心跳命令通道协议 v1 冻结稿）。

- **配置**：`%LOCALAPPDATA%/winhelper/uplink_config.json`（0600 语义，token 明文仅此一处；UPLINK_CONFIG_DIR 环境变量供 E2E 隔离）
- **心跳循环**：daemon 线程 30s（服务端响应 interval 可覆盖）+ 失败指数退避（cap 600s）；心跳成功拍附带一次 metrics 上报（snapshot 映射，节流与心跳同拍）
- **命令分发**：`COMMAND_HANDLERS` 注册表 → 独立 daemon 线程（不阻塞心跳）→ 按 id 幂等 → 回执重试 2（409 终态不重试）
- **iperf_client**：内置 iperf3（`_MEIPASS/libs/iperf3`）→ 每任务 `%TEMP%` 独立 staging（并发安全）→ `-J` JSON 解析 → finally 清理
- **net_probe**：`route print -4` 网关解析（metric 最小，语言无关）+ `ping -n 1 -w 2000` / socket TCP 计时；`_gateway` 别名解析（ADR-015）
- **collect_logs**：v1 hook 回执 ok=false（ADR-019 结构已冻结；FTP 凭据用完即弃不落盘不回显；日志引擎由 log-inspector 接管后替换）
- **ai_context**：固定 ok=false error=ai_disabled
- **编码**：子进程输出 bytes + utf-8/gbk 多编码解码（PYTHONUTF8=1 下 text=True 解码 GBK 输出会 UnicodeDecodeError，E2E 实测踩中）
- **前端**：「平台接入」卡片（硬件配置之后）：服务器地址 + Token（password，保存后清空不回显）+ 启用/停用/立即注册 + 状态行（10s 轮询，tab 非激活自动停）
- **bridge.py**：3 条 `/api/perf/uplink/*` 路由 + `uplink_autostart()`（import 时按配置恢复心跳，失败静默）
- **exe 打包**：`winhelper.spec` datas 增 `perf-analyzer/libs/iperf3/*`（iperf3.exe 3.1.3 + cygwin1.dll，iperf.fr 官方 win64 包）

## 已知限制

- Windows 专属实现（PhysicalDrive* 过滤、busy% 依赖 read/write_time）；Linux 可运行但磁盘指标结构不同（过滤后为空）
- 记录元数据（status/report）在内存，进程重启后 JSONL 数据仍在但不可继续轮询；analysis.json 可离线查看
- p95 为排序插值，样本量极大时收集列常驻内存（10 万样本 ≈ 数十 MB，可接受）
