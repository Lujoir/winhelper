# net-doctor · 架构文档

> 观枢终端平台｜EyeTerm · Windows 终端「网络排障」子系统
> 最后更新：2026-09-09

## 1. 定位

winhelper 主应用第五个功能菜单「网络排障」，面向终端侧网络故障的自助诊断与上报：
配置核查（离线）→ IP 冲突检测（需中心）→ 连通性测试（离线）→ 路由追踪（离线+知识库）→ 网络压测（需中心）。

## 2. 目录结构

```
net-doctor\
├── net_service.py              # 服务层（框架无关）：五功能引擎 + 后台任务管理
├── web\
│   ├── netdoctor.js            # 前端逻辑（函数一律 nd 前缀，无可选链）
│   └── netdoctor-standalone.html  # 独立测试页（E2E 载体）
├── tools\
│   ├── e2e_netdoctor.py        # Playwright E2E（standalone + 主应用双场景）
│   └── smoke_netdoctor.py      # 后端引擎真实冒烟
└── docs\
    ├── DECISIONS.md            # ADR-001~
    └── ARCHITECTURE.md         # 本文件
```

## 3. 数据流

```
前端 netdoctor.js (nd*)
   │  ndApiFetch：apiFetch 守卫 → pywebview 桩 → fetch 三级回退
   ▼
bridge.py ROUTES  /api/netdoctor/*        （主应用集成点，pywebview api.call）
   ▼
net_service.py handle_net_*（同步返回 task_id 或数据）
   ├── 任务管理器 _start_task/_task_view/_cancel_task（daemon 线程 + Event 取消）
   │     kind: confcheck | ipconflict | ping | tracert | stress
   ▼
引擎层
   ├── 配置核查：powershell ipconfig /all（UTF8 强制 + 三级解码）→ 逐网卡核查
   │            DHCP / DNS(对比 app_config.netdoctor.expected_dns) / 网关
   ├── IP 冲突：活动网卡 IP+MAC → 平台 POST ipconflict（X-ETP-Token）
   │            conflict_suspect → 自动 POST /api/v1/ai/analyze
   ├── 连通性：app_config.netdoctor.nodes 逐节点 ping / nslookup / w32tm(stripchart)
   │            → 结果追加 JSONL netdoctor_records\ping_YYYYMMDD.jsonl
   ├── 路由追踪：tracert -w 500 -h 15 → 逐跳解析 → 平台 route-nodes 知识库
   │            ipaddress CIDR 前缀匹配标注区域
   └── 网络压测：多档包长 ping 中心 + 平台 iperf-server → 本地捆绑 iperf3.exe
                （环境变量 → _MEIPASS/libs/iperf3 → 项目 libs/iperf3）
                → iperf-result 回传 → 综合总结 → 导出 NetStress_*.html
```

## 4. 外部依赖契约

### 4.1 平台服务端（172.17.5.215:18090，X-ETP-Token 鉴权）
| 端点 | 方法 | 用途 |
|---|---|---|
| `/api/v1/terminals/{tid}/netdoctor/ipconflict` | POST | IP 冲突 verdict |
| `/api/v1/terminals/{tid}/netdoctor/route-nodes` | GET | 路由区域知识库 |
| `/api/v1/terminals/{tid}/netdoctor/iperf-server` | POST | 起单会话 iperf3 -s -1 |
| `/api/v1/terminals/{tid}/netdoctor/iperf-result` | POST | 回传压测总结 |
| `/api/v1/ai/analyze` | POST | 冲突疑似时 AI 辅助结论 |

凭据源：`%LOCALAPPDATA%/winhelper/uplink_config.json`（与 uplink.py 共读同一文件，token 永不回显/落日志）。

### 4.2 本地配置（%LOCALAPPDATA%/winhelper/app_config.json）
```json
{ "netdoctor": {
    "expected_dns": [],
    "nodes": [ {"key":"gateway","name":"终端区：本终端网关","method":"ping","target":""},
               {"key":"core","name":"核心交换机","method":"ping","target":"172.17.254.1"},
               {"key":"datacenter","name":"数据中心区：数据中心汇聚交换机","method":"ping","target":"172.17.254.2"},
               {"key":"dmz","name":"DMZ区：DMZ汇聚交换机","method":"ping","target":"172.17.254.9"},
               {"key":"dns","name":"内网DNS","method":"nslookup","target":"172.17.1.109","probe":"baidu.com"},
               {"key":"ntp","name":"温州总院","method":"ntp","target":"ntp.eye.ac.cn"},
               {"key":"internet","name":"互联网","method":"ping","target":"baidu.com"},
               {"key":"center","name":"中心服务器","method":"ping","target":""} ] } }
```
缺键回退内置默认值；expected_dns 空 = 仅提示未配置基线。

## 5. API 面（bridge ROUTES）

| 路由 | 语义 | 模式 |
|---|---|---|
| `/api/netdoctor/config` | 读节点表/基线/中心状态 | 同步 |
| `/api/netdoctor/config-check` | 启动配置核查任务 | task |
| `/api/netdoctor/ipconflict` | 启动 IP 冲突检测任务 | task |
| `/api/netdoctor/ping-start` | 启动连通性全量检测 | task |
| `/api/netdoctor/ping-history` | 最近 JSONL 记录摘要 | 同步 |
| `/api/netdoctor/tracert-start` | 启动路由追踪（target） | task |
| `/api/netdoctor/stress-start` | 启动网络压测（时长/包长/UDP码率） | task |
| `/api/netdoctor/stress-export` | 导出压测 HTML 报告（task_id） | 同步 |
| `/api/netdoctor/task-status` | 通用任务轮询 | 同步 |
| `/api/netdoctor/task-cancel` | 通用任务取消 | 同步 |

## 6. 安全与健壮性红线

1. GUI 无控制台程序：一切子进程（powershell/ping/tracert/nslookup/w32tm/iperf3）`CREATE_NO_WINDOW`。
2. 解码三级兜底：utf-8-sig → utf-8 → gbk → errors=replace；PowerShell 调用前置 `[Console]::OutputEncoding=UTF8`。
3. 路径禁硬编码盘符（LOCALAPPDATA/TEMP 逐级 fallback；测试隔离用 NETDOCTOR_CONFIG_DIR 环境变量）。
4. 后台线程 daemon + 可取消 + 异常全捕获；数据面失败如实标注（超时/解析失败/未配置）。
5. token 仅进请求头，异常/日志/回显永不携带。
6. 运行时依赖仅标准库 + 主应用既有 psutil/requests，不新增第三方包。
