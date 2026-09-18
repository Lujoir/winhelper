# EyeTerm 接口总台账（观枢终端平台）

- **版本**：v1.0（首版建档）
- **最后更新**：2026-09-19
- **维护人**：api-registrar-dev（接口登记官）
- **事实来源**：代码实证（server-platform/server/api.py、bridge.py、uplink.py、net-doctor/net_service.py 等），每条注明文件+函数
- **登记统计**：
  - 一、服务端 REST API（SRV）：139 条（2026-09-10 晚新增 ADR-029 IP 冲突深度检测 2 条；2026-09-16（晚）新增 power-control P0 2 条 SRV-080/081，服务端实现 commit ec726af/ADR-038；2026-09-17 新增客户端版本发布管理 6 条 SRV-082~087，commit e24a236/ADR-042；同日新增 update-manifest 别名 SRV-088（327a69b）与第三方数据源 4 条 SRV-089~092（b8a086b/ADR-043）；同日新增开关机管控页 7 条 SRV-093~099——其中 dispatch 批次组 3 条为 ADR-040 漏登补登，commit 95dbb1c/3645d94/983e7bc 前序，主体 a099448/3fe299e；同日晚新增火绒专项 9 条 SRV-100~108（b6a74f6/9ab89ac/7842aab，ADR-033 系，huorong-dev 按常设约定主动补知会）；同日新增 pc_diag 联调通道 4 条 SRV-109~112，commit 5f0ba2f/ADR-040 增补，已生产部署验证；2026-09-18 新增 4.1.5 电源行动与 WoL 产品化 7 条 SRV-113~119，commit 85ae733/ADR-044/045；2026-09-19 新增开关机管控任务化 18 条 SRV-120~137，commit 99f4079/ADR-046/047，server-platform-dev 按常设约定显式列清单知会；同日新增首页资产定位 1 条 SRV-138（asset_locate.py，commit 19694e1，asset-mgmt-dev 交付经 main 转达，api-registrar-dev 审核登记，新建 1.18 中心首页组）；同日补登首页卡片摘要 SRV-139（api_home.py，home-console-dev 实施；登记官误报「未实现」经 main 实证更正后按实登记））
  - 二、终端本地桥接 API（BRG）：78 条（netdoctor 22 条：10 条 2026-09-09 合入转「在用」commit 628c210；AI 诊断 2 条 commit a892f62/bd965ae；冲突检测本地转发 3 条 commit b344bbc/40abd3f；路由追踪 AI 分析 + AI 诊断历史持久化 4 条 2026-09-11 登记，commit c4495bc/916e791；AI 证据增强 3 条 2026-09-18 登记，commit 0b3f43d/45cb17c 4.1.7；桌面管控 5 条 2026-09-16 登记，commit 08b3547/9bdfcdf；power-control 2 条 2026-09-16（晚）登记，主应用挂载 commit 2bbf881；客户端自启与更新 4 条 2026-09-17（7fe4e9b，power-control-dev 直写复核归档）；文件检索 5 条——4 条 2026-09-17 漏登补登（权威引擎 search_service.py 894f20a）+ 索引器部署 1 条 2026-09-18 登记（c598e05/9c3c680，4.1.7）；性能分析 1 条 2026-09-18 登记（record-latest，1eac1b6/8faffde））
  - 三、终端↔平台协议（UPL）：18 条（2026-09-16 新增桌面管控契约 3 条 UPL-011~013，契约冻结/服务端未实现；2026-09-17 新增命令 UPL-014 pc_apply_policy（主仓 a099448）与 UPL-015 pc_diag（5f0ba2f/3dc7e8b 双端闭环）；2026-09-18 新增命令 UPL-016~018 power_action/power_action_abort/wol_relay（主仓 0c5f1e0/客户端 4.1.5，双端就绪））
  - 四、外部依赖接口（EXT）：7 条（2026-09-15 新增火绒终端安全 API v1，试点实证；2026-09-19 EXT-007 补记字段消费落地 commit 8f9d5ad——_list 三时间戳 + _info2 assets 无固定字段名纪律，SRV-108 响应同步扩展）
  - 五、废弃/规划接口（DEP）：5 条
  - **合计 247 条** > 更新 2026-09-19：合计由 232 纠正为 227（api-registrar-dev 入职对账，逐条 grep 实证 SRV119/BRG78/UPL18/EXT7/DEP5，编号连续无跳号；232 为此前误记）> 更新 2026-09-19（晚）：批 A 开关机管控任务化 +18 条（SRV-120~137，commit 99f4079/ADR-047），合计 227→245 > 更新 2026-09-19（晚二）：首页资产定位 +1 条（SRV-138，asset_locate.py，commit 19694e1），合计 245→246 > 更新 2026-09-19（晚三）：首页卡片摘要补登 +1 条（SRV-139，api_home.py；登记官误报「未实现」经 main 实证更正），合计 246→247 > 更新 2026-09-19（晚四）：条目更新批次（条数不变）——GET /runs/{id} 撤除定案（513ffa8，作废方案残留永不启用）入 1.17 组注记；SRV-129/SRV-134 响应删 calendar_fallback；SRV-133 删 covered + 语义调整为例外标记统计（ADR-047 增补纯星期语义）
- **通用约定**：
  - 服务端监听：ThreadingHTTPServer，`0.0.0.0:{port}`，默认 18090（app.py `_load_config` / `main`）；配置经 `$ETP_CONFIG` → `server/config.local.json` → dev 默认三级加载
  - 终端上行鉴权：请求头 `X-ETP-Token`（对照 config.json `terminal_token`）> 更新 2026-09-09：收敛为**多 token 模型**——config token 或 SQLite `terminal_tokens` 表 status='active' 命中均放行（详见 UPL-010，commit 4d2924b）
  - 控制台鉴权：请求头 `X-ETP-Console-Token`（登录换取；auth_upgrade SQLite 持久会话）
  - 白名单准入：终端 API 经 `_admission` 校验（register 强制白名单，空名单 fail-closed；已注册终端豁免）
  - 示例一律脱敏：`<server>`/`<token>`/`127.0.0.1`；禁止出现生产 IP 与真实 token

---

## 一、服务端 REST API（来源：server-platform）

### 1.1 公共

#### SRV-001 健康检查 `GET /api/v1/health`
- **用途**：服务存活探针，返回服务名/版本/时间戳
- **鉴权**：无
- **请求参数**：无
- **响应**：`{"ok":true,"service":"eyeterm-server","version":"1.0.0","ts":1789000000}`
- **调用方式**：`curl http://127.0.0.1:18090/api/v1/health`
- **代码出处**：api.py `dispatch`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

### 1.2 控制台-认证

#### SRV-002 控制台登录 `POST /api/v1/console/login`
- **用途**：用户名+口令换控制台会话 token（PBKDF2 哈希 + SQLite 持久会话 + 失败锁定 423 + IP 限速 429）
- **鉴权**：无
- **请求参数**：

```json
{"username": "admin", "password": "<口令>"}
```
- **响应**：`{"ok":true,"token":"<token>","msg":"...","must_change_password":false,"last_login_at":...,"last_login_ip":"..."}`
- **调用方式**：`curl -X POST http://<server>/api/v1/console/login -H "Content-Type: application/json" -d '{"username":"admin","password":"<口令>"}'`
- **代码出处**：api.py `dispatch`（分支 `/api/v1/console/login`）→ auth_upgrade.py `authenticate`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-09：响应新增 `role` 字段（api.py:214 `role=(r.user or {}).get("role")`，commit 4d2924b，代码实证）

#### SRV-003 控制台自助改密 `POST /api/v1/console/password`
- **用途**：修改当前用户口令（等保三级 8.1.4.1），成功后吊销其它会话、保留当前会话
- **鉴权**：X-ETP-Console-Token
- **请求参数**：`{"old_password":"...","new_password":"...","confirm_password":"..."}`
- **响应**：`{"ok":true,"msg":"..."}`；失败 `{"ok":false,"msg":"..."}`（401/400 等）
- **调用方式**：`curl -X POST http://<server>/api/v1/console/password -H "X-ETP-Console-Token: <token>" -d '{"old_password":"...","new_password":"...","confirm_password":"..."}'`
- **代码出处**：api.py `dispatch`（分支 `/api/v1/console/password`）→ auth_upgrade.py `change_password`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

### 1.3 控制台-终端

#### SRV-004 终端列表 `GET /api/v1/console/terminals`
- **用途**：全部注册终端清单（含硬件摘要与在线状态）
- **鉴权**：X-ETP-Console-Token
- **请求参数**：无（query 无）
- **响应**：`{"ok":true,"now":...,"terminals":[{terminal_id,terminal_type,hostname,os_info,client_version,ip,first_seen,last_seen,online,cpu_model,cpu_cores,mem_total_mb,disk_total_gb,gpu_info,os_arch,group_id}]}`
- **调用方式**：`curl http://<server>/api/v1/console/terminals -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_api`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-005 终端详情 `GET /api/v1/console/terminals/{tid}`
- **用途**：单终端详情 + 资产明细 + 最新指标 + 近期瓶颈/事件
- **鉴权**：X-ETP-Console-Token
- **请求参数**：路径 `{tid}` 终端 ID
- **响应**：`{"ok":true,"terminal":{...},"asset":{...}|null,"latest_metrics":{...}|null,"bottlenecks":[...],"events":[...]}`
- **调用方式**：`curl http://<server>/api/v1/console/terminals/WIN-HOST -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_api`；store.py `get_terminal_asset`
- **状态**：在用（asset 字段来源见待确认项 TBC-001）
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-09：TBC-001 消项——`asset` 字段现优先返回 register 上报的 schema1 真实明细（asset_detail 列已入库），无则仍回退 hwinfo_json（commit 4d2924b，代码实证 api.py:339 / store.py:256-261）

#### SRV-006 终端指标曲线 `GET /api/v1/console/terminals/{tid}/metrics`
- **用途**：指定时间窗（分钟）内指标点序列
- **鉴权**：X-ETP-Console-Token
- **请求参数**：query `minutes`（默认 60，范围 5~10080）
- **响应**：`{"ok":true,"minutes":60,"points":[{ts,cpu_percent,mem_available_percent,disks,...}]}`
- **调用方式**：`curl "http://<server>/api/v1/console/terminals/{tid}/metrics?minutes=120" -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_api`（`_q_int`）
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-007 终端绑定/解绑资产组 `POST /api/v1/console/terminals/{tid}/group`
- **用途**：将终端绑定到资产组；`group_id` 为空即解绑
- **鉴权**：X-ETP-Console-Token
- **请求参数**：`{"group_id": 3}` 或 `{"group_id": null}`（解绑）
- **响应**：`{"ok":true}`；组不存在/非法 400
- **调用方式**：`curl -X POST http://<server>/api/v1/console/terminals/{tid}/group -H "X-ETP-Console-Token: <token>" -d '{"group_id":3}'`
- **代码出处**：api.py `_console_api` → store.py `set_terminal_group`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-008 下发命令 `POST /api/v1/console/terminals/{tid}/commands`
- **用途**：经命令通道向终端下发命令（iperf_client / net_probe / collect_logs / ai_context）
- **鉴权**：X-ETP-Console-Token
- **请求参数**：

```json
{"command": "iperf_client", "args": {...}, "timeout_sec": 120, "source": "console"}
```
- **响应**：`{"ok":true,"command_id":12}`；未知命令类型 400
- **调用方式**：`curl -X POST http://<server>/api/v1/console/terminals/{tid}/commands -H "X-ETP-Console-Token: <token>" -d '{"command":"net_probe","args":{"task_id":"T1","targets":[{"host":"_gateway","method":"ping"}]}}'`
- **代码出处**：api.py `_console_api` → store.py `enqueue_command`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-009 命令列表 `GET /api/v1/console/commands`
- **用途**：命令下发/回执历史
- **鉴权**：X-ETP-Console-Token
- **请求参数**：query `limit`（默认 100）、`terminal_id`（可选筛选）
- **响应**：`{"ok":true,"commands":[{id,terminal_id,command,args,status,created_ts,...}]}`
- **调用方式**：`curl "http://<server>/api/v1/console/commands?limit=50" -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_api` → store.py `list_commands`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-010 终端性能报告导出 `GET /api/v1/console/terminals/{tid}/report`
- **用途**：生成单文件自包含 HTML 终端性能报告（下载）
- **鉴权**：X-ETP-Console-Token
- **请求参数**：query `hours`（默认 24，范围 1~720）
- **响应**：`text/html` 附件，文件名 `EyeTerm_Report_{tid}_{YYYYMMDD}.html`
- **调用方式**：`curl -OJ "http://<server>/api/v1/console/terminals/{tid}/report?hours=24" -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_api` → `build_report_html`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-011 事件列表 `GET /api/v1/console/events`
- **用途**：终端上报事件查询
- **鉴权**：X-ETP-Console-Token
- **请求参数**：query `limit`（默认 100）、`since_ts`、`terminal_id`
- **响应**：`{"ok":true,"events":[{id,terminal_id,ts,level,category,message,detail}]}`
- **调用方式**：`curl "http://<server>/api/v1/console/events?limit=100" -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_api` → store.py `list_events`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-012 瓶颈列表 `GET /api/v1/console/bottlenecks`
- **用途**：指标瓶颈记录查询
- **鉴权**：X-ETP-Console-Token
- **请求参数**：query `limit`（默认 100）、`since_ts`、`terminal_id`
- **响应**：`{"ok":true,"bottlenecks":[{id,terminal_id,ts,kind,metric_key,value,threshold,level,detail,acked}]}`
- **调用方式**：`curl "http://<server>/api/v1/console/bottlenecks" -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_api` → store.py `list_bottlenecks`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-013 瓶颈确认 `POST /api/v1/console/bottlenecks/{bid}/ack`
- **用途**：确认（应答）某条瓶颈告警
- **鉴权**：X-ETP-Console-Token
- **请求参数**：路径 `{bid}` 瓶颈 ID
- **响应**：`{"ok":true}`；不存在 404
- **调用方式**：`curl -X POST http://<server>/api/v1/console/bottlenecks/42/ack -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_api` → store.py `ack_bottleneck`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-014 审计日志 `GET /api/v1/console/audit`
- **用途**：服务端审计记录（鉴权拒绝/准入拒绝/登录审计等）
- **鉴权**：X-ETP-Console-Token
- **请求参数**：query `limit`（默认 50）
- **响应**：`{"ok":true,"audit":[{ts,ip,path,action,detail}]}`
- **调用方式**：`curl "http://<server>/api/v1/console/audit?limit=50" -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_api` → store.py `list_audit`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

### 1.4 控制台-资产组

#### SRV-015 资产组列表 `GET /api/v1/console/asset-groups`
- **用途**：树形资产分组清单
- **鉴权**：X-ETP-Console-Token
- **请求参数**：无
- **响应**：`{"ok":true,"groups":[{id,name,parent_id,...}]}`
- **调用方式**：`curl http://<server>/api/v1/console/asset-groups -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_api` → store.py `asset_group_list`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-016 新建资产组 `POST /api/v1/console/asset-groups`
- **用途**：创建分组（可指定父组，支持树形）
- **鉴权**：X-ETP-Console-Token
- **请求参数**：`{"name":"二层楼","parent_id":1}`（parent_id 可空）
- **响应**：`{"ok":true,"id":5}`；非法（重名/父组不存在）400
- **调用方式**：`curl -X POST http://<server>/api/v1/console/asset-groups -H "X-ETP-Console-Token: <token>" -d '{"name":"二层楼"}'`
- **代码出处**：api.py `_console_api` → store.py `asset_group_create`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-017 资产组重命名 `POST|PUT /api/v1/console/asset-groups/{gid}`
- **用途**：重命名分组
- **鉴权**：X-ETP-Console-Token
- **请求参数**：路径 `{gid}`；body `{"name":"新名称"}`
- **响应**：`{"ok":true}`；不存在 404
- **调用方式**：`curl -X POST http://<server>/api/v1/console/asset-groups/5 -H "X-ETP-Console-Token: <token>" -d '{"name":"三层楼"}'`
- **代码出处**：api.py `_console_api` → store.py `asset_group_rename`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-018 删除资产组 `DELETE /api/v1/console/asset-groups/{gid}`
- **用途**：删除分组；有子组时拒绝（409）
- **鉴权**：X-ETP-Console-Token
- **请求参数**：路径 `{gid}`
- **响应**：`{"ok":true}`；404 不存在 / 409 `"先删除该组下的子目录后再删除本组"`
- **调用方式**：`curl -X DELETE http://<server>/api/v1/console/asset-groups/5 -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_api` → store.py `asset_group_delete`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

### 1.5 控制台-网络测试（iperf/nettest）

#### SRV-019 发起网络测试 `POST /api/v1/console/nettest/launch`
- **用途**：创建 iperf 任务（任务槽 + 临时端口起 `iperf3 -s -1`）并经命令通道下发
- **鉴权**：X-ETP-Console-Token
- **请求参数**：`{"terminal_id":"WIN-X","test_type":"bandwidth_tcp|udp_jitter|latency_gateway|latency_server","duration_sec":10}`
- **响应**：成功 `{"ok":true,"task_id":"IT-xxxxxxxxxx","port":18203,"command_id":9,"command":"iperf_client"}`；失败 `{"ok":false,"error":"unknown test_type|terminal not found|no free port in range"}`（HTTP 400）
- **调用方式**：`curl -X POST http://<server>/api/v1/console/nettest/launch -H "X-ETP-Console-Token: <token>" -d '{"terminal_id":"WIN-X","test_type":"bandwidth_tcp","duration_sec":10}'`
- **代码出处**：api.py `_console_nettest` → iperf.py `launch`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-020 网测任务列表 `GET /api/v1/console/nettest/tasks`
- **用途**：iperf 任务历史与状态
- **鉴权**：X-ETP-Console-Token
- **请求参数**：query `limit`（默认 50）、`terminal_id`（可选）
- **响应**：`{"ok":true,"tasks":[{task_id,terminal_id,test_type,port,status,created_ts,finished_ts,...}]}`
- **调用方式**：`curl "http://<server>/api/v1/console/nettest/tasks" -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_nettest` → store.py `iperf_list`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-021 取消网测任务 `POST /api/v1/console/nettest/task/{task_id}`
- **用途**：取消运行中任务（task_id 以 `IT-` 开头），强杀服务端进程
- **鉴权**：X-ETP-Console-Token
- **请求参数**：路径 `{task_id}`（如 `IT-a1b2c3d4e5`）
- **响应**：`{"ok":true,...}`；任务不存在 `{"ok":false,"error":"task not found"}`
- **调用方式**：`curl -X POST http://<server>/api/v1/console/nettest/task/IT-a1b2c3d4e5 -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_nettest` → iperf.py `cancel`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-022 网测报告导出 `GET /api/v1/console/nettest/report`
- **用途**：最近网测任务汇总，单文件自包含 HTML 报告（下载）
- **鉴权**：X-ETP-Console-Token
- **请求参数**：query `limit`（默认 20）
- **响应**：`text/html` 附件，文件名 `EyeTerm_NetTest_{YYYYMMDD}.html`
- **调用方式**：`curl -OJ "http://<server>/api/v1/console/nettest/report" -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_nettest` → `build_nettest_report_html`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

### 1.6 控制台-AI 智能分析

#### SRV-023 发起 AI 分析（控制台）`POST /api/v1/console/ai/analyze`
- **用途**：聚合终端 6 小时诊断上下文，调算力平台 LLM（模型链），落库分析记录
- **鉴权**：X-ETP-Console-Token
- **请求参数**：`{"terminal_id":"WIN-X","issue_description":"最近很卡"}`
- **响应**：`{"ok":true,"analysis_id":5,"response":"<AI 结论文本>","error":"","duration_ms":3200}`；终端不存在 404
- **调用方式**：`curl -X POST http://<server>/api/v1/console/ai/analyze -H "X-ETP-Console-Token: <token>" -d '{"terminal_id":"WIN-X","issue_description":"开机慢"}'`
- **代码出处**：api.py `_console_api` → `run_ai_analysis` → ai.py `llm_chat_chain`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-024 AI 分析列表 `GET /api/v1/console/ai/analyses`
- **用途**：AI 分析记录查询
- **鉴权**：X-ETP-Console-Token
- **请求参数**：query `limit`（默认 50）、`terminal_id`（可选）
- **响应**：`{"ok":true,"analyses":[{id,terminal_id,ts,trigger,issue,status,model,duration_ms,...}]}`
- **调用方式**：`curl "http://<server>/api/v1/console/ai/analyses" -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_api` → store.py `ai_list`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-09：ai_analyses.trigger 枚举新增 `terminal_diagnose`（现有 console/terminal 不变）；控制台显示文案：console→控制台、terminal→终端上报、terminal_diagnose→终端诊断；**本列表接口不含 context_json**，单条详情走 SRV-071（commit 8aaa64e，ADR-023）

#### SRV-025 AI 分析报告导出 `GET /api/v1/console/ai/report/{id}`
- **用途**：单条 AI 分析生成 HTML 报告（下载）
- **鉴权**：X-ETP-Console-Token
- **请求参数**：路径 `{id}` 分析记录 ID（纯数字）
- **响应**：`text/html` 附件，文件名 `EyeTerm_AI_{tid}_{id}.html`；不存在 404
- **调用方式**：`curl -OJ "http://<server>/api/v1/console/ai/report/5" -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_api` → `build_ai_report_html`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

### 1.7 配置清单（白名单/运行时设置/存储/上传）

#### SRV-026 准入白名单 `GET /api/v1/console/whitelist`
- **用途**：查看白名单与准入策略说明
- **鉴权**：X-ETP-Console-Token
- **请求参数**：无
- **响应**：`{"ok":true,"whitelist":[{id,cidr,note,enabled,...}],"policy":"empty-list rejects new registrations; registered terminals exempt"}`
- **调用方式**：`curl http://<server>/api/v1/console/whitelist -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_api` → `_console_whitelist`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-027 添加白名单 `POST /api/v1/console/whitelist`
- **用途**：新增白名单条目（单 IP 或 CIDR）
- **鉴权**：X-ETP-Console-Token
- **请求参数**：`{"cidr":"192.168.1.0/24","note":"三楼网段"}`
- **响应**：`{"ok":true,"id":3}`；非法 CIDR 400；重复 409 `"entry already exists"`
- **调用方式**：`curl -X POST http://<server>/api/v1/console/whitelist -H "X-ETP-Console-Token: <token>" -d '{"cidr":"192.168.1.0/24","note":"机房"}'`
- **代码出处**：api.py `_console_whitelist` → store.py `whitelist_add`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-028 删除白名单 `DELETE /api/v1/console/whitelist/{id}`
- **用途**：删除白名单条目
- **鉴权**：X-ETP-Console-Token
- **请求参数**：路径 `{id}`
- **响应**：`{"ok":true}`；不存在 404
- **调用方式**：`curl -X DELETE http://<server>/api/v1/console/whitelist/3 -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_whitelist` → store.py `whitelist_delete`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-029 白名单启停 `POST /api/v1/console/whitelist/{id}`
- **用途**：启用/停用条目（不删除）
- **鉴权**：X-ETP-Console-Token
- **请求参数**：`{"enabled": true}`
- **响应**：`{"ok":true}`；不存在 404
- **调用方式**：`curl -X POST http://<server>/api/v1/console/whitelist/3 -H "X-ETP-Console-Token: <token>" -d '{"enabled":false}'`
- **代码出处**：api.py `_console_whitelist` → store.py `whitelist_toggle`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-030 运行时设置 `GET /api/v1/console/settings`
- **用途**：读取全部运行时配置（敏感键脱敏：前 3 + **** + 后 4）
- **鉴权**：X-ETP-Console-Token
- **请求参数**：无
- **响应**：`{"ok":true,"settings":{"llm.url":"https://<llm-host>","llm.model":"...","llm.api_key":"sk-****xxxx","ftp.enabled":"1","ftp.listen_port":"18121",...,"llm.url.updated_at":...}}`（DEFAULTS 键见 settings.py）
- **调用方式**：`curl http://<server>/api/v1/console/settings -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_api` → settings.py `SettingsStore.all_masked`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-031 修改运行时设置 `POST /api/v1/console/settings`
- **用途**：批量更新 settings 键值（敏感键加密落库；键名格式 `<组>.<项>`）
- **鉴权**：X-ETP-Console-Token
- **请求参数**：`{"settings":{"llm.model":"Qwen3.6","iperf.port_start":"18200"}}`（llm.api_key 等 SENSITIVE_KEYS 自动加密）
- **响应**：`{"ok":true,"changed":["llm.model","iperf.port_start"]}`；键名不合法 400
- **调用方式**：`curl -X POST http://<server>/api/v1/console/settings -H "X-ETP-Console-Token: <token>" -d '{"settings":{"ftp.enabled":"1"}}'`
- **代码出处**：api.py `_console_api` → settings.py `SettingsStore.set`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-032 存储状态 `GET /api/v1/console/storage/status`
- **用途**：存储根目录挂载状态 + vsftpd FTP 服务状态
- **鉴权**：X-ETP-Console-Token
- **请求参数**：无
- **响应**：`{"ok":true,"mount":{"root_dir":"/data/terminal-platform/storage","mounted":false,"source":"","mount_cmd":""},"ftp":{"enabled":true,...}}`
- **调用方式**：`curl http://<server>/api/v1/console/storage/status -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_api` → storage.py `mount_status` / `ftp_status`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-033 执行 SMB 挂载 `POST /api/v1/console/storage/mount`
- **用途**：管理员触发执行 `smb.mount_cmd` 配置的挂载命令
- **鉴权**：X-ETP-Console-Token
- **请求参数**：无（命令取自 settings `smb.mount_cmd`）
- **响应**：`{"ok":true,"rc":0,"stdout":"...","stderr":""}`；未配置 `{"ok":false,"error":"smb.mount_cmd 未配置"}`
- **调用方式**：`curl -X POST http://<server>/api/v1/console/storage/mount -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_api` → storage.py `exec_mount`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-034 扫描上传目录 `POST /api/v1/console/storage/scan`
- **用途**：扫描存储根目录（不递归），新增文件自动登记（source=scan）
- **鉴权**：X-ETP-Console-Token
- **请求参数**：无
- **响应**：`{"ok":true,"added":N,"skipped":M}`（store 登记结果，以 storage.py `scan_uploads` 返回为准）
- **调用方式**：`curl -X POST http://<server>/api/v1/console/storage/scan -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_api` → storage.py `scan_uploads`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-035 上传登记列表 `GET /api/v1/console/uploads`
- **用途**：终端上传文件登记记录（FTP 上传后登记 + 扫描补登记）
- **鉴权**：X-ETP-Console-Token
- **请求参数**：query `limit`（默认 50）
- **响应**：`{"ok":true,"uploads":[{id,terminal_id,filename,size,sha256,path,source,ts}]}`
- **调用方式**：`curl "http://<server>/api/v1/console/uploads" -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_api` → store.py `list_uploads`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

### 1.8 知识库（运维知识库 KB，ADR-022）

> 组级语义修正（2026-09-09，commit 31f8e1d，代码实证 api.py:1396-1461 / kb_store.py）：①鉴权为登录控制台即可（X-ETP-Console-Token，**未限 admin**）；②**操作人服务端强制取会话用户名**（api.py:1403 `author = sess["username"]`），客户端传 author 一律忽略；③版本保留最近 5 版（kb_store.py:43 `_MAX_VERSIONS=5`）；④`{kb_id}` 为 TEXT 业务主键（非数字 id），含特殊字符时调用方需 URL 编码；⑤错误码：409 kb_id 重复 / 404 条目或版本不存在 / 400 参数缺失。预置数据：初始化幂等生成 `kb_id=route-nodes`（分类 route_nodes，标题「路由表 · 关键节点」，kb_store.py:59-61）。

#### SRV-036 知识库列表/新建 `GET|POST /api/v1/console/kb`
- **用途**：GET 取全量路由表；POST 新建条目（5 版本迭代存储）
- **鉴权**：X-ETP-Console-Token
- **请求参数**（POST）：`{"kb_id":"route-core","category":"路由","title":"核心路由","content":"...","author":"admin","note":"首版"}`
- **响应**：GET `{"ok":true,"routes":[...]}`；POST `{"ok":true,"kb_id":"route-core","version":1}`；重复 409
- **调用方式**：`curl -X POST http://<server>/api/v1/console/kb -H "X-ETP-Console-Token: <token>" -d '{"kb_id":"route-core","title":"核心路由","content":"..."}'`
- **代码出处**：api.py `_console_kb_api` → kb_store.py
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-09：GET 列表新增 query `category`（精确过滤）/`q`（对 title/kb_id/content LIKE 模糊），响应新增 `categories[]`（DISTINCT 分类，kb_store.py:93-96），routes 按 updated_ts DESC；POST 的 author 改由服务端强制取会话用户名（commit 31f8e1d，ADR-022）

#### SRV-037 知识库条目详情/更新/删除 `GET|PUT|DELETE /api/v1/console/kb/{kid}`
- **用途**：单条目读取（含 history）/ 更新内容（版本+1）/ 删除
- **鉴权**：X-ETP-Console-Token
- **请求参数**（PUT）：`{"title":"...","category":"...","content":"...","author":"admin","note":"..."}`
- **响应**：GET `{"ok":true,"entry":{...,"history":[...]}}`；PUT `{"ok":true,"version":2}`；DELETE `{"ok":true,"deleted":true}`；不存在 404
- **调用方式**：`curl http://<server>/api/v1/console/kb/route-core -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_kb_api`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-09：PUT **字段缺省=保持原值**（api.py:1432-1438，title/content 未传沿用原值），每次保存 version+1 自动迭代，仅保留最近 5 版（kb_store.py:148 删超限版本）；DELETE 为 kb_entries **硬删**且 kb_versions 随条目一并清理（防同 kb_id 重建后版本号错乱，kb_store.py:157-160），kb_history 保留 delete 记录可追溯；author 同样服务端强制（commit 31f8e1d，ADR-022）

#### SRV-038 知识库版本列表 `GET /api/v1/console/kb/{kid}/versions`
- **用途**：条目全部历史版本号列表
- **鉴权**：X-ETP-Console-Token
- **请求参数**：路径 `{kid}`
- **响应**：`{"ok":true,"versions":[1,2,3]}`
- **调用方式**：`curl http://<server>/api/v1/console/kb/route-core/versions -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_kb_api` → kb_store.py `versions`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-09：版本按 version DESC 返回，**最多 5 条**（`_MAX_VERSIONS=5` 滚动窗口）；注意条目删除后 versions 一并清理（见 SRV-037 更新行）（commit 31f8e1d，ADR-022）

#### SRV-039 知识库指定版本 `GET /api/v1/console/kb/{kid}/versions/{ver}`
- **用途**：读取指定版本快照
- **鉴权**：X-ETP-Console-Token
- **请求参数**：路径 `{kid}`、`{ver}`
- **响应**：`{"ok":true,"version":{version,title,content,author,...}}`；不存在 404
- **调用方式**：`curl http://<server>/api/v1/console/kb/route-core/versions/1 -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_kb_api` → kb_store.py `get_version`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-040 知识库回滚 `POST /api/v1/console/kb/{kid}/rollback`
- **用途**：回滚到指定版本（回滚动作本身产生新版本）
- **鉴权**：X-ETP-Console-Token
- **请求参数**：`{"version":1,"author":"admin"}`
- **响应**：`{"ok":true,"version":4}`；版本不存在 404
- **调用方式**：`curl -X POST http://<server>/api/v1/console/kb/route-core/rollback -H "X-ETP-Console-Token: <token>" -d '{"version":1}'`
- **代码出处**：api.py `_console_kb_api` → kb_store.py `rollback`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-09：语义明确——回滚**以旧版内容生成新版本**（version 单调递增不丢历史，非覆盖式回退）；author 服务端强制取会话用户名（commit 31f8e1d，ADR-022）

#### SRV-041 知识库维护记录 `GET /api/v1/console/kb/{kid}/history`
- **用途**：条目创建/更新/回滚/删除的维护履历
- **鉴权**：X-ETP-Console-Token
- **请求参数**：路径 `{kid}`
- **响应**：`{"ok":true,"history":[{ts,action,author,note,...}]}`
- **调用方式**：`curl http://<server>/api/v1/console/kb/route-core/history -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_kb_api` → kb_store.py `history`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-09：history 字段为 actor/action/detail/ts（操作人不信任客户端传入）；DELETE 操作的 history 记录在条目硬删后仍保留（commit 31f8e1d，ADR-022）

### 1.9 静态资源

#### SRV-042 控制台首页 `GET /`（及 `/index.html`）
- **用途**：控制台单页应用入口
- **鉴权**：无（页面本身公开；数据接口需 token）
- **请求参数**：无
- **响应**：`text/html`（console/index.html）
- **调用方式**：浏览器 `http://<server>/`
- **代码出处**：api.py `_static`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-10：静态资源协商缓存（commit 25d48e2）——响应加 `ETag`（`W/"mtime-size"`）与 `Last-Modified`，`Cache-Control: no-cache`；请求带 `If-None-Match` 命中 → **304 revalidate**（api.py:1539-1566），根治 index.html 更新后浏览器启发式缓存旧页面

#### SRV-043 控制台静态文件 `GET /<file>`
- **用途**：console/ 目录下静态资源（js/css/png/svg/json 等，按扩展名映射 Content-Type）
- **鉴权**：无
- **请求参数**：路径为相对文件名；目录穿越防护（normpath 前缀校验，越界 403）
- **响应**：文件内容；不存在 404；类型不在白名单 `application/octet-stream`
- **调用方式**：`curl http://<server>/app.js`
- **代码出处**：api.py `_static`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-10：同 SRV-042，静态资源协商缓存（commit 25d48e2）——ETag（`W/"mtime-size"`）+ Last-Modified + no-cache，If-None-Match 命中 304 revalidate（api.py:1539-1566）

#### SRV-044 favicon `GET /favicon.ico`
- **用途**：图标占位（返回 204 空响应）
- **鉴权**：无
- **请求参数**：无
- **响应**：HTTP 204，`image/x-icon`
- **调用方式**：浏览器自动请求
- **代码出处**：api.py `_static`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

### 1.10 终端上行（X-ETP-Token + 白名单准入 `_admission`）

#### SRV-045 终端注册 `POST /api/v1/terminals/register`
- **用途**：终端注册（幂等，重复注册即更新资产摘要）；register 强制白名单，空名单 fail-closed
- **鉴权**：X-ETP-Token
- **请求参数**：

```json
{
  "terminal_id": "WIN-HOST",          // 必填
  "terminal_type": "windows",          // 默认 windows
  "hostname": "HOST", "os_info": "...", "client_version": "4.0.0",
  "ip": "10.0.0.5",                    // 缺省取 client_ip
  "hwinfo": {"cpu_model":"...","cpu_cores":8,"mem_total_mb":16384,"disk_total_gb":512.0,"gpu_info":"...","os_arch":"x86_64"},
  "asset": {"schema":1,"ts":...,"os":{},"cpu":{},"memory":{},"disks":[],"gpu":[],"network":[],"temps":{}}
}
```
- **响应**：`{"ok":true,"registered":true}`（true=新注册，false=更新）
- **调用方式**：`curl -X POST http://<server>/api/v1/terminals/register -H "X-ETP-Token: <token>" -d '{"terminal_id":"WIN-HOST"}'`
- **代码出处**：api.py `_terminal_api` → store.py `register_terminal`
- **状态**：在用（asset 透传见待确认项 TBC-001）
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-09：①TBC-001 消项——register 分支已补传 `asset=data.get("asset")`（api.py:339），store 端 try/except 降级（store.py:256-261），实测 asset_detail NULL→1；②鉴权切多 token 模型（api.py:218 `check_terminal_token`，见 UPL-010）（commit 4d2924b，代码实证）

#### SRV-046 终端心跳 `POST /api/v1/terminals/{tid}/heartbeat`
- **用途**：保活 + 拉取待执行命令（命令通道）；顺带过期清理
- **鉴权**：X-ETP-Token（+ 准入）
- **请求参数**：body 可为空 `{}`
- **响应**：`{"ok":true,"interval":60,"report_interval":60,"commands":[{id,command,args,timeout_sec,...}]}`（interval=config heartbeat_timeout_sec/3，终端据此覆盖心跳间隔）
- **调用方式**：`curl -X POST http://<server>/api/v1/terminals/WIN-HOST/heartbeat -H "X-ETP-Token: <token>" -d '{}'`
- **代码出处**：api.py `_terminal_api` → store.py `touch_terminal` / `take_pending_commands`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-17：响应新增 `latest_version` 字段（客户端当前发布版本号，**可 null**——无 current 发布时不携带更新引导；api.py:661，commit e24a236/ADR-042，随 SRV-086 manifest 同批）

#### SRV-047 指标上报 `POST /api/v1/terminals/{tid}/metrics`
- **用途**：性能快照入库（对象或数组批量均可；gzip 预留）；入库后过瓶颈规则引擎，命中且超去重窗口落 bottlenecks 并回告
- **鉴权**：X-ETP-Token（+ 准入）
- **请求参数**（单对象，数组则逐个处理）：

```json
{"terminal_id":"WIN-HOST","terminal_type":"windows","client_version":"4.0.0","ts":1789000000,
 "cpu":{"percent":12.5},"mem":{"used_percent":48.2,"available_percent":51.8,"used_mb":7890.1,"total_mb":16384.0},
 "swap":{"used_percent":3.1},
 "disks":[{"mount":"C:\\","used_gb":120.5,"total_gb":256.0,"percent":47.1,"busy_percent":3.0}],
 "volumes":[{"mount":"C:\\","percent":47.1,"used_gb":120.5,"total_gb":256.0}]}
```
- **响应**：`{"ok":true,"accepted":1,"bottlenecks":[{id,rule,metric_key,value,threshold,desc}]}`
- **调用方式**：`curl -X POST http://<server>/api/v1/terminals/WIN-HOST/metrics -H "X-ETP-Token: <token>" -d '{...}'`
- **代码出处**：api.py `_terminal_api` → `_ingest_snapshot` → analysis.py `evaluate`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-048 事件上报 `POST /api/v1/terminals/{tid}/events`
- **用途**：终端事件落库（info/warn/error/critical，非法等级降级 info）
- **鉴权**：X-ETP-Token（+ 准入）
- **请求参数**：`{"ts":1789000000,"level":"warn","category":"general","message":"...","detail":{}}`
- **响应**：`{"ok":true,"event_id":31}`
- **调用方式**：`curl -X POST http://<server>/api/v1/terminals/WIN-HOST/events -H "X-ETP-Token: <token>" -d '{"level":"warn","message":"磁盘超80%"}'`
- **代码出处**：api.py `_terminal_api` → store.py `insert_event`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-049 上传文件登记 `POST /api/v1/terminals/{tid}/uploads`
- **用途**：终端经 FTP 上传文件后的登记上报（source=api）
- **鉴权**：X-ETP-Token（+ 准入）
- **请求参数**：`{"files":[{"filename":"logs_0909.zip","size":123456,"sha256":"...","path":"/data/terminal-platform/storage/logs_0909.zip","ts":1789000000}]}`
- **响应**：`{"ok":true,"registered":1}`
- **调用方式**：`curl -X POST http://<server>/api/v1/terminals/WIN-HOST/uploads -H "X-ETP-Token: <token>" -d '{"files":[...]}'`
- **代码出处**：api.py `_terminal_api` → store.py `insert_upload`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-050 命令回执 `POST /api/v1/terminals/{tid}/commands/{cid}/result`
- **用途**：终端执行完命令回传结果（幂等；409=命令已终态不重试）
- **鉴权**：X-ETP-Token（+ 准入）
- **请求参数**：`{"ok":true,"data":{"task_id":"IT-a1b2c3d4e5","summary":"tcp 887.8 Mbits/sec","result":{}}}` 或 `{"ok":false,"error":"iperf3_timeout"}`
- **响应**：`{"ok":true}`；终端未注册 404 / cid 非法 400 / 命令非 sent 态 409
- **调用方式**：`curl -X POST http://<server>/api/v1/terminals/WIN-HOST/commands/9/result -H "X-ETP-Token: <token>" -d '{"ok":true,"data":{}}'`
- **代码出处**：api.py `_terminal_api` → store.py `complete_command`；iperf.py `complete_from_command`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-051 IP 冲突上报（netdoctor）`POST /api/v1/terminals/{tid}/netdoctor/ipconflict`
- **用途**：终端上报本机 IP+MAC，服务端交叉校验（同 IP 7 天多 MAC → suspect）
- **鉴权**：X-ETP-Token（+ 准入）
- **请求参数**：`{"ip":"192.168.1.23","mac":"AA-BB-CC-DD-EE-FF"}`（均必填）
- **响应**：`{"ok":true,"verdict":"ok|conflict_suspect"}`（verdict 具体值域以 store.py `ipconflict_report` 为准）
- **调用方式**：`curl -X POST http://<server>/api/v1/terminals/WIN-HOST/netdoctor/ipconflict -H "X-ETP-Token: <token>" -d '{"ip":"192.168.1.23","mac":"AA-BB-CC-DD-EE-FF"}'`
- **代码出处**：api.py `_terminal_api` → store.py `ipconflict_report`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-10：判定精化（ADR-028，commit 257ed21，store.py:453-490）——①判定基准从「同 IP 7 天多 MAC」改为「同 IP 窗口内出现**其它 terminal_id** 的报告」→ conflict_suspect=true；②verdict 新增 `suspect_reasons`（`multi_terminal`=其它已知终端 / `unknown_terminal`=terminal_id 不在 terminals 表，保守原则同样 suspect）与 `nic_history`（仅同一 terminal_id 的 MAC 变化→**不** suspect，记 [{mac,last_ts}] 供人工参考，证据照存）；③MAC 经 `_mac_key` 归一比对（去分隔符+小写，格式差异不误判）；④新增只读交叉校验 `ipconflict_lookup`（不落库，ADR-029 深度检测 conclude 步骤复用，无独立端点）；画方 admission 注入链不变（api.py:418）

#### SRV-052 路由节点知识库（netdoctor）`GET /api/v1/terminals/{tid}/netdoctor/route-nodes`
- **用途**：下发路由节点表（CIDR→区域标注），供终端 tracert 逐跳标注
- **鉴权**：X-ETP-Token（+ 准入）
- **请求参数**：无（数据源 settings `netdoctor.route_nodes`，JSON 数组）
- **响应**：`{"ok":true,"nodes":[{"cidr":"172.17.254.0/24","zone":"核心","name":"..."}]}`
- **调用方式**：`curl http://<server>/api/v1/terminals/WIN-HOST/netdoctor/route-nodes -H "X-ETP-Token: <token>"`
- **代码出处**：api.py `_terminal_api`（settings 读取）
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-09：①响应新增 `source` 字段（`"kb"|"settings"`）；②数据源变更（ADR-022，api.py:383-397 `_kb_route_nodes`）：优先 kb_entries 中 category='route_nodes' 最新条目（content 为 JSON 数组 [{match,zone,desc}]，即预置 kb_id=route-nodes），解析失败/无条目回退 settings `netdoctor.route_nodes`；注意响应节点字段为 match/zone/desc（首版登记的 cidr/name 为笔误，以本行为准）（commit 31f8e1d，代码实证）> 更新 2026-09-10：数据内容基线升 v2（kb_id=route-nodes 预置条目内容版本迭代，编年史 #40「route_nodes v2 与 v5 发布」）；**接口契约不变**（source 字段/节点结构 match/zone/desc/kb 优先 settings 兜底链均未变，api.py:1734-1751 复核）

#### SRV-053 iperf 服务端起流（netdoctor）`POST /api/v1/terminals/{tid}/netdoctor/iperf-server`
- **用途**：终端主动压测时，服务端起单会话 `iperf3 -s -1`（不经命令通道）
- **鉴权**：X-ETP-Token（+ 准入）
- **请求参数**：`{"mode":"tcp|udp","duration_sec":60}`（mode 非 udp 一律按 tcp）
- **响应**：`{"ok":true,"task_id":"IT-xxxxxxxxxx","port":18205}`；无空闲端口 503 `"iperf server unavailable (no free port)"`
- **调用方式**：`curl -X POST http://<server>/api/v1/terminals/WIN-HOST/netdoctor/iperf-server -H "X-ETP-Token: <token>" -d '{"mode":"tcp","duration_sec":60}'`
- **代码出处**：api.py `_terminal_api` → iperf.py `spawn_server`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-054 iperf 结果回传（netdoctor）`POST /api/v1/terminals/{tid}/netdoctor/iperf-result`
- **用途**：终端压测完成后回传结果，任务置 done/failed
- **鉴权**：X-ETP-Token（+ 准入）
- **请求参数**：`{"task_id":"IT-xxxxxxxxxx","ok":true,"data":{"mbits_sec":887.8,...}}`
- **响应**：`{"ok":true}`；任务不存在或非 running 404 `"task not running"`
- **调用方式**：`curl -X POST http://<server>/api/v1/terminals/WIN-HOST/netdoctor/iperf-result -H "X-ETP-Token: <token>" -d '{"task_id":"IT-xxx","ok":true,"data":{}}'`
- **代码出处**：api.py `_terminal_api` → store.py `iperf_finish`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-055 AI 分析（终端侧发起）`POST /api/v1/ai/analyze`
- **用途**：终端侧触发 AI 分析（IP 冲突疑似时 net-doctor 自动复用此接口）
- **鉴权**：X-ETP-Token（注意：不经过 `_admission`，仅 token 校验）
- **请求参数**：`{"terminal_id":"WIN-HOST","issue_description":"检测到 IP 冲突疑似"}`
- **响应**：`{"ok":true,"analysis_id":6,"response":"...","error":"","duration_ms":2800}`；终端不存在 404
- **调用方式**：`curl -X POST http://<server>/api/v1/ai/analyze -H "X-ETP-Token: <token>" -d '{"terminal_id":"WIN-HOST","issue_description":"..."}'`
- **代码出处**：api.py `dispatch`（独立分支）→ `run_ai_analysis`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-11：新增 **kind 聚合分支机制**（dispatch api.py:268-281）——①payload `kind` 字段（小写）或 issue 前缀启发：`ipconflict|ip_conflict|ip冲突` 前缀 → kind=ipconflict（**ADR-030** 四源聚合，commit a8a6b95，此前未单列登记）；`路由追踪分析` 前缀 → kind=routetrace（**ADR-031**，commit 2cb39d2）；②routetrace 分支：`route_ctx=data.get("context")`（需含 hops）→ `_aggregate_routetrace_context`（api.py:1405+，hops 非法/空返回 None → **回退一般性分析**；pkg={target,hops,route_nodes(kb 全量，sources 标注 kb/empty),terminal,sources:{hops:"terminal_upload"}}）→ `ROUTETRACE_SYSTEM_PROMPT`（ai.py:543，路径研判/异常识别/结论三段 + 证据硬约束四条：只引用实际 hops/知识库内容、引用必附跳数序号、缺证据显式声明、**禁止按 IP 段推测区域归属**）+ `build_routetrace_context`（ai.py:586）三源预算分配（hops 16KB / route_nodes 8KB / terminal 4KB，总 32KB，`_allocate_budgets` 优先级贪心）；context_record={kind,target,hops_count,sources,sections,stats}；ipconflict 分支不受下述不一致影响（kind 由 issue 前缀推断，无需 route_ctx）；③**⚠️ 契约不一致（对账发现，待修）**：终端本地转发 BRG-055 payload 附加键为 `data` 而非 `context`（net_service.py:1745 vs api.py:279-281）→ routetrace 聚合 route_ctx 恒 None、全部走回退（**消项 2026-09-11（晚）**：终端侧已修 net-doctor 4679b11 / 主应用 e6452e5，extra 改 `context:{target,hops}`，route_ctx 恢复正常注入、聚合分支生效；平台侧无改动，见 BRG-055 更新行）

#### SRV-070 终端 AI 智能诊断 `POST /api/v1/terminals/{tid}/ai/diagnose`
- **用途**：终端推送六类日志包 + 问题概述 → LLM 主备降级链出结论（同步接口，等待 ≤120s，实测约 11s；与 SRV-055 差异：上下文来自终端上报而非服务端聚合）
- **鉴权**：X-ETP-Token（+ 准入，白名单中间件）
- **请求参数**：`{"issue":"问题概述 ≤2000 字（必填）","logs":{"hwinfo":…,"os_info":…,"perf_analysis":…,"perf_stress":…,"system_log":…,"network":…}}`——六类键均可选（采集容错缺省），值可为对象或字符串
- **响应**：成功 200 `{"ok":true,"analysis_id":9,"response_text":"…","model":"Qwen3.6","duration_ms":11340}`；LLM 全链失败 **502** `{"ok":false,"error":"…","analysis_id":N,"duration_ms":N}`（失败记录仍落库）；400 issue 空/超 2000 字、logs 非对象；404 终端未注册；413 body 超限（8MB）
- **截断与存证**：单类日志 32KB 头部截断（`…[truncated]`）；prompt 注入每类 ≤4KB 合计 ≤24KB；context_json 存证每类 ≤4KB
- **调用方式**：`curl -X POST http://<server>/api/v1/terminals/WIN-HOST/ai/diagnose -H "X-ETP-Token: <token>" -d '{"issue":"开机后风扇狂转","logs":{"perf_analysis":"..."}}'`
- **代码出处**：api.py `_terminal_api`（分支 api.py:419-445）→ `run_terminal_diagnose`（api.py:1160+，DIAG_SYSTEM_PROMPT + `build_diagnose_context` + llm_chat_chain，timeout=45×max_retries=0 双模型最坏约 90s）
- **状态**：在用
- **登记记录**：2026-09-09，代码实证（server-platform-dev 下发，commit 8aaa64e，ADR-023；冒烟：真实终端 200 analysis_id=9 model=Qwen3.6 duration=11340ms）> 更新 2026-09-10：截断层重做（ADR-027，commit 47de462+91b2fe6，ai.py）——①预算对齐终端 AI 卡「单类超 32KB 自动截断」承诺：单类原始/存证 32KB（`DIAG_RAW_LIMIT`/`DIAG_EVIDENCE_PER_KEY`；历史缺陷：原 4KB 截断致证据饥饿、LLM 虚构日志依据——analysis_id=16 事故）；注入单类 16KB（`DIAG_PROMPT_PER_KEY`）、全 prompt 日志总预算 32KB（`DIAG_PROMPT_TOTAL`）（原 4KB/24KB）；②优先级贪心预算分配：system_log 恒第一、issue 关键词命中类提前（`_KEY_HINT_WORDS` 六类映射）、快照类（os_info/hwinfo）垫底；③**结构感知截断**：JSON 列表/对象按完整条目粒度二分保留头部前缀（`_clip_json_list/_clip_json_dict`，存证可再解析），非 JSON 头部截断，均带 `…[truncated]`；④双向乱码探测标记 `_detect_mojibake`（91b2fe6 加项，analysis_id=16 取证定性为终端采集层乱码）；⑤context_json 新增 `logs_stats`（每类 original_items/kept_items/truncated，api.py:1461-1465）；⑥`DIAG_SYSTEM_PROMPT` 加证据可信性硬约束（只允许引用日志中实际出现的事件 ID/来源/时间戳、引用必附时间戳、缺日志显式声明证据不足；net_service `_ND_AI_PROMPT_SYSTEM` 同款对齐）；响应字段不变

#### SRV-071 AI 分析单条详情 `GET /api/v1/console/ai/analyses/{id}`（属 1.6 控制台-AI 组）
- **用途**：单条分析详情（**含 context_json**——终端诊断时为 `{issue, logs 各类截断存证}`；列表接口 SRV-024 不含 context_json，行为不变）
- **鉴权**：X-ETP-Console-Token
- **请求参数**：路径 `{id}` 分析记录 ID（纯数字）
- **响应**：`{"ok":true,"analysis":{id,terminal_id,ts,trigger,issue,context,response_text,status,model,duration_ms,...}}`（store.ai_get 全行）；不存在 404
- **调用方式**：`curl http://<server>/api/v1/console/ai/analyses/9 -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_api`（api.py:784-790）→ store.py `ai_get`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证（server-platform-dev 下发，commit 8aaa64e，ADR-023）

#### SRV-078 IP 冲突深度检测启动 `POST /api/v1/terminals/{tid}/netdoctor/ipconflict-deep`
- **用途**：IP 冲突深度检测异步编排（五步 resolve→arp→nad→macaddr→conclude，逐步进度经 SRV-079 轮询；结论由 deep_engine.run_deep_check 汇总）
- **鉴权**：X-ETP-Token（+ 准入）
- **请求参数**：`{"ip":"192.168.1.23","mac":"AA-BB-CC-DD-EE-FF"}`（均必填；ip 经 `ipaddress.ip_address` 校验，mac 经 `deep_engine.mac_key` 归一校验）
- **响应**：`{"ok":true,"task_id":"DC-xxxxxxxx","status":"running"}`；参数非法 400（invalid ip address / invalid mac address）；并发槽满 **429** `"deep check busy, retry later"`（全局信号量 `_DEEP_SEMAPHORE=2`，SSH 逐设备串行）；终端未注册 404
- **后台**：daemon 线程 `_deep_task_worker` 逐步 `deep_task_update(steps)`，结论 verdict 落库（注入 kb_route_nodes CIDR 标注与 settings 上下文）；异常置 status=failed
- **代码出处**：api.py:388-409 → `_deep_task_worker`(api.py:1382) / `_DEEP_SEMAPHORE`(api.py:1379) → store.py `deep_task_create`(509) → deep_engine.py `run_deep_check`
- **状态**：在用
- **登记记录**：2026-09-10，代码实证（ADR-029，随 257ed21 批次入仓；team-lead 晚间批次提示并入登记）> 更新 2026-09-10（晚）：commit 归属修正——双端点实际由 **9e604a7**（deep-engine ADR-029 SSH 网工级编排+异步任务+双端点）引入，**cc34bae** 补 ARP 失败分支同步状态变量（deep 任务 verdict.sources 如实反映 failed）；原「随 257ed21 入仓」归属表述不准，以本行为准（本地转发侧 BRG-052/053 docstring 契约标注 9e604a7+cc34bae）

#### SRV-079 IP 冲突深度检测状态 `GET /api/v1/terminals/{tid}/netdoctor/ipconflict-deep/{task_id}`
- **用途**：深度检测任务进度/结论轮询（steps_json 逐步证据 + verdict 终局判定）
- **鉴权**：X-ETP-Token（+ 准入）
- **请求参数**：路径 `{tid}`、`{task_id}`（如 `DC-a1b2c3d4`）
- **响应**：`{"ok":true,"task":{task_id,terminal_id,ip,mac,status,steps_json,verdict,created_ts,updated_ts}}`（以 store.py `deep_task_get` 行为准）；task 不存在或 terminal_id 不匹配 404 `"task not found"`；终端未注册 404
- **代码出处**：api.py:371-380 → store.py `deep_task_get`
- **状态**：在用
- **登记记录**：2026-09-10，代码实证（ADR-029）> 更新 2026-09-10（晚）：commit 归属修正同 SRV-078（9e604a7 引入 + cc34bae 补 verdict.sources ARP 失败分支同步）

> 注：SRV-001~071 中编号按登记顺序连续分配；「1.x」小节标题与编号的对应关系以条目内「方法与路径」为准（SRV-070 属 1.10 终端上行，SRV-071 属 1.6 控制台-AI）。

### 1.11 控制台-系统管理（sysadmin，ADR-021，2026-09-09 新增）

> 组级约定（代码出处：api.py `_console_sysadmin`，dispatch 分支 api.py:764-773）：全部接口要求 **X-ETP-Console-Token + admin 角色**，非 admin 返回 403 `"需要管理员权限"`（并记 ACCESS_DENIED 审计）——权限门 `auth.require_admin`（auth_upgrade.py:1053）**回库补查账号当前角色与状态**，不信任会话缓存角色，管理员调整角色后即时生效（2026-09-09 回库复核，team-lead 要求）；错误约定：参数缺失/非法 400、不存在 404、冲突 409。四功能：账户管理（console_auth.db）/ 算力网关（settings llm.*）/ 第三方接口登记（third_party_apis 表）/ 终端 token 维护（terminal_tokens 表）。敏感操作（建户/改户/删户/重置/token 创建/轮换/停用）均落 auth 审计。> 更新 2026-09-09：新增第五功能**交换机管理**（ADR-026，SRV-072~077，settings switch.default_* + switches 表，commit f031152）。

#### SRV-056 账户列表 `GET /api/v1/console/sysadmin/users`
- **用途**：控制台账户清单（口令字段恒为 `'****'`，不出明文/哈希）
- **鉴权**：X-ETP-Console-Token + admin
- **请求参数**：无
- **响应**：`{"ok":true,"users":[{id,username,role,status,password:'****',password_algo,password_must_change,last_login_at,last_login_ip,locked_until}]}`
- **调用方式**：`curl http://<server>/api/v1/console/sysadmin/users -H "X-ETP-Console-Token: <admin-token>"`
- **代码出处**：api.py `_console_sysadmin` → auth_upgrade.py `list_users`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证（server-platform-dev 下发，commit 4d2924b）

#### SRV-057 创建账户 `POST /api/v1/console/sysadmin/users`
- **用途**：新建控制台账户（口令走复杂度策略，默认 must_change=1 首登强制改密）
- **鉴权**：X-ETP-Console-Token + admin
- **请求参数**：`{"username":"operator1","password":"<符合复杂度策略>","role":"admin|operator"}`（username/password 必填，role 默认 operator）
- **响应**：`{"ok":true,"id":3,"msg":"..."}`；重名 409；口令不合规 400
- **调用方式**：`curl -X POST http://<server>/api/v1/console/sysadmin/users -H "X-ETP-Console-Token: <admin-token>" -d '{"username":"operator1","password":"...","role":"operator"}'`
- **代码出处**：api.py `_console_sysadmin` → auth_upgrade.py `create_user`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证（server-platform-dev 下发，commit 4d2924b）

#### SRV-058 修改账户角色/状态 `PUT /api/v1/console/sysadmin/users/{id}`
- **用途**：改角色或状态；`status=disabled` 即时吊销该账户全部会话
- **鉴权**：X-ETP-Console-Token + admin
- **请求参数**：`{"role":"operator"}` 和/或 `{"status":"active|disabled"}`（均可选，至少一项）
- **响应**：`{"ok":true,"msg":"..."}`；自降级/自禁用/停用最后一个 admin 均 409；账户不存在 404
- **调用方式**：`curl -X PUT http://<server>/api/v1/console/sysadmin/users/3 -H "X-ETP-Console-Token: <admin-token>" -d '{"status":"disabled"}'`
- **代码出处**：api.py `_console_sysadmin` → auth_upgrade.py `update_user`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证（server-platform-dev 下发，commit 4d2924b）

#### SRV-059 删除账户 `DELETE /api/v1/console/sysadmin/users/{id}`
- **用途**：删户（先吊销会话，关联数据 FK CASCADE 清理）
- **鉴权**：X-ETP-Console-Token + admin
- **请求参数**：路径 `{id}`
- **响应**：`{"ok":true,"msg":"..."}`；自删/删除最后一个 admin 409；不存在 404
- **调用方式**：`curl -X DELETE http://<server>/api/v1/console/sysadmin/users/3 -H "X-ETP-Console-Token: <admin-token>"`
- **代码出处**：api.py `_console_sysadmin` → auth_upgrade.py `delete_user`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证（server-platform-dev 下发，commit 4d2924b）

#### SRV-060 管理员重置口令 `POST /api/v1/console/sysadmin/users/{id}/reset-password`
- **用途**：重置指定账户口令（force_change=true，目标用户下次登录强制改密）
- **鉴权**：X-ETP-Console-Token + admin
- **请求参数**：`{"new_password":"<符合复杂度策略>"}`（必填）
- **响应**：`{"ok":true,"msg":"..."}`；口令不合规 400；账户不存在 404
- **调用方式**：`curl -X POST http://<server>/api/v1/console/sysadmin/users/3/reset-password -H "X-ETP-Console-Token: <admin-token>" -d '{"new_password":"..."}'`
- **代码出处**：api.py `_console_sysadmin` → auth_upgrade.py `admin_reset_password`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证（server-platform-dev 下发，commit 4d2924b）

#### SRV-061 算力网关配置读取 `GET /api/v1/console/sysadmin/llm`
- **用途**：读 LLM 网关配置（api_key 仅脱敏回显）
- **鉴权**：X-ETP-Console-Token + admin
- **请求参数**：无
- **响应**：`{"ok":true,"llm":{"url":"...","model":"...","model_fallback":"...","api_key_masked":"sk-****xxxx","configured":true}}`（configured = url 与 key 均非空）
- **调用方式**：`curl http://<server>/api/v1/console/sysadmin/llm -H "X-ETP-Console-Token: <admin-token>"`
- **代码出处**：api.py `_console_sysadmin`（settings llm.* 读取 + `mask_secret`）
- **状态**：在用
- **登记记录**：2026-09-09，代码实证（server-platform-dev 下发，commit 4d2924b）

#### SRV-062 算力网关配置写入 `POST /api/v1/console/sysadmin/llm`
- **用途**：写 settings 键 `llm.url`/`llm.model`/`llm.model_fallback`/`llm.api_key`（key 加密存储）
- **鉴权**：X-ETP-Console-Token + admin
- **请求参数**：`{"url":"...","model":"...","model_fallback":"...","api_key":"..."}`——各字段可选；**api_key 留空 = 保持不变**
- **响应**：`{"ok":true,"changed":["llm.url","llm.api_key"]}`（实际变更键列表）
- **调用方式**：`curl -X POST http://<server>/api/v1/console/sysadmin/llm -H "X-ETP-Console-Token: <admin-token>" -d '{"model":"Qwen3.6"}'`
- **代码出处**：api.py `_console_sysadmin` → settings.py `SettingsStore.set`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证（server-platform-dev 下发，commit 4d2924b）

#### SRV-063 算力网关连通性测试 `POST /api/v1/console/sysadmin/llm/test`
- **用途**：`GET {llm.url}/v1/models`（Bearer api_key，8s 超时）验证连通；不消耗对话额度
- **鉴权**：X-ETP-Console-Token + admin
- **请求参数**：无（读当前配置）
- **响应**：`{"ok":true,"test":{"ok":true,"status_code":200,"latency_ms":380,"error":""}}`；未配置 `test.error="not_configured"`
- **调用方式**：`curl -X POST http://<server>/api/v1/console/sysadmin/llm/test -H "X-ETP-Console-Token: <admin-token>"`
- **代码出处**：api.py `_console_sysadmin` → `_llm_test`；外部端点见 EXT-001（更新记录）
- **状态**：在用
- **登记记录**：2026-09-09，代码实证（server-platform-dev 下发，commit 4d2924b）

#### SRV-064 第三方接口登记 `GET|POST /api/v1/console/sysadmin/third-party`
- **用途**：外部第三方接口台账的登记/列表（GET 列表 / POST 新增）
- **鉴权**：X-ETP-Console-Token + admin
- **请求参数**（POST）：`{"name":"<必填>","base_url":"<必填>","method":"GET|POST|PUT|DELETE|HEAD","params_json":{},"headers_json":{},"note":""}`（params_json/headers_json 须为 JSON 对象，非法 400）
- **响应**：GET `{"ok":true,"apis":[...]}`；POST `{"ok":true,"id":2}`
- **调用方式**：`curl -X POST http://<server>/api/v1/console/sysadmin/third-party -H "X-ETP-Console-Token: <admin-token>" -d '{"name":"短信网关","base_url":"https://<gateway>/send","method":"POST"}'`
- **代码出处**：api.py `_console_sysadmin` → store.py `third_party_create` / `third_party_list`（表 third_party_apis）
- **状态**：在用
- **登记记录**：2026-09-09，代码实证（server-platform-dev 下发，commit 4d2924b）

#### SRV-065 第三方接口编辑/删除 `PUT|DELETE /api/v1/console/sysadmin/third-party/{id}`
- **用途**：编辑登记项（部分更新，空载荷 400）/ 删除
- **鉴权**：X-ETP-Console-Token + admin
- **请求参数**：路径 `{id}`；PUT body 各字段可选（name/base_url 非空校验、method 白名单、params_json/headers_json 对象校验）
- **响应**：`{"ok":true}`；无更新字段 400 `"nothing to update"`；不存在 404
- **调用方式**：`curl -X PUT http://<server>/api/v1/console/sysadmin/third-party/2 -H "X-ETP-Console-Token: <admin-token>" -d '{"note":"v2"}'`
- **代码出处**：api.py `_console_sysadmin` → store.py `third_party_update` / `third_party_delete`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证（server-platform-dev 下发，commit 4d2924b）

#### SRV-066 第三方接口启停 `POST /api/v1/console/sysadmin/third-party/{id}/toggle`
- **用途**：启用/停用登记项
- **鉴权**：X-ETP-Console-Token + admin
- **请求参数**：`{"enabled": true|false}`
- **响应**：`{"ok":true}`；不存在 404
- **调用方式**：`curl -X POST http://<server>/api/v1/console/sysadmin/third-party/2/toggle -H "X-ETP-Console-Token: <admin-token>" -d '{"enabled":false}'`
- **代码出处**：api.py `_console_sysadmin` → store.py `third_party_toggle`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证（server-platform-dev 下发，commit 4d2924b）

#### SRV-067 终端 token 列表/生成 `GET|POST /api/v1/console/sysadmin/tokens`
- **用途**：终端接入 token 多实例维护（GET 列表，**token 值完整返回**，按需求可查看；POST 生成，`secrets.token_hex(24)`）
- **鉴权**：X-ETP-Console-Token + admin
- **请求参数**：POST `{"label":"办公区终端"}`（可空）
- **响应**：GET `{"ok":true,"tokens":[{id,label,token,status,created_ts,last_used_ts,...}]}`；POST `{"ok":true,"token":{...新行...}}`
- **调用方式**：`curl -X POST http://<server>/api/v1/console/sysadmin/tokens -H "X-ETP-Console-Token: <admin-token>" -d '{"label":"办公区终端"}'`
- **代码出处**：api.py `_console_sysadmin` → store.py `token_list` / `token_create`（表 terminal_tokens，token 唯一索引）；审计 TOKEN_STATUS
- **状态**：在用
- **登记记录**：2026-09-09，代码实证（server-platform-dev 下发，commit 4d2924b）

#### SRV-068 终端 token 轮换/停用/启用 `POST /api/v1/console/sysadmin/tokens/{id}/rotate|disable|enable`
- **用途**：rotate 返回新 token 且旧值立即失效；disable 立即 401 拒绝；enable 恢复
- **鉴权**：X-ETP-Console-Token + admin
- **请求参数**：路径 `{id}` + 动作段（rotate/disable/enable），body 无
- **响应**：rotate `{"ok":true,"token":"<新token>"}`；disable/enable `{"ok":true}`；不存在 404
- **调用方式**：`curl -X POST http://<server>/api/v1/console/sysadmin/tokens/2/rotate -H "X-ETP-Console-Token: <admin-token>"`
- **代码出处**：api.py `_console_sysadmin` → store.py `token_rotate` / `token_set_status`；审计 TOKEN_ROTATED/TOKEN_STATUS
- **状态**：在用
- **登记记录**：2026-09-09，代码实证（server-platform-dev 下发，commit 4d2924b）

#### SRV-072 交换机默认凭据读取 `GET /api/v1/console/sysadmin/switch-default`
- **用途**：交换机默认登录凭据读取（deploy.py 预置，供新增交换机继承）
- **鉴权**：X-ETP-Console-Token + admin
- **请求参数**：无
- **响应**：`{"ok":true,"switch_default":{"username":"...","password_masked":"3@W****u9xn","password_set":true}}`（未配置时 password_masked="(未配置)"）
- **代码出处**：api.py `_console_sysadmin`（api.py:1059-1067）→ settings switch.default_username（明文）/ switch.default_password（SENSITIVE_KEYS 加密，脱敏出库）
- **状态**：在用
- **登记记录**：2026-09-09，代码实证（server-platform-dev 下发，commit f031152，ADR-026）

#### SRV-073 交换机默认凭据写入 `PUT /api/v1/console/sysadmin/switch-default`
- **用途**：更新默认用户名/密码（password 留空=保持不变，对齐 llm api_key 语义）
- **鉴权**：X-ETP-Console-Token + admin
- **请求参数**：`{"username":"...","password":"..."}`（均可选）
- **响应**：`{"ok":true,"changed":["username","password"]}`（实际变更项）
- **调用方式**：`curl -X PUT http://<server>/api/v1/console/sysadmin/switch-default -H "X-ETP-Console-Token: <admin-token>" -d '{"username":"admin"}'`
- **代码出处**：api.py:1068-1083；审计 SWITCH_DEFAULT_UPDATED（detail 不记明文密码）
- **状态**：在用
- **登记记录**：2026-09-09，代码实证（server-platform-dev 下发，commit f031152，ADR-026）

#### SRV-074 交换机台账列表 `GET /api/v1/console/sysadmin/switches`
- **用途**：交换机台账清单（password 恒 `'****'` 脱敏，无明文出库）
- **鉴权**：X-ETP-Console-Token + admin
- **请求参数**：无
- **响应**：`{"ok":true,"switches":[{id,name,ip,ssh_port,username,brand,created_ts,updated_ts,password:'****'}]}`
- **代码出处**：api.py:1085-1094 → store.py `switch_list`（password_set 判定，表 switches password_enc 密文列）
- **状态**：在用
- **登记记录**：2026-09-09，代码实证（server-platform-dev 下发，commit f031152，ADR-026）

#### SRV-075 交换机新增 `POST /api/v1/console/sysadmin/switches`
- **用途**：登记交换机（password 必填，SecretsBox 加密落库）
- **鉴权**：X-ETP-Console-Token + admin
- **请求参数**：`{"name":"…","ip":"…","ssh_port":22,"username":"…","password":"…","brand":"…"}`——name/ip/username/password 必填（缺 400），ip 经 `ipaddress.ip_address` 格式校验（400），ssh_port 默认 22
- **响应**：`{"ok":true,"id":3}`
- **调用方式**：`curl -X POST http://<server>/api/v1/console/sysadmin/switches -H "X-ETP-Console-Token: <admin-token>" -d '{"name":"汇聚A","ip":"192.168.1.2","username":"admin","password":"..."}'`
- **代码出处**：api.py:1095-1114 → store.py `switch_create`；审计 SWITCH_CREATED（detail 不记明文）
- **状态**：在用
- **登记记录**：2026-09-09，代码实证（server-platform-dev 下发，commit f031152，ADR-026）

#### SRV-076 交换机编辑 `PUT /api/v1/console/sysadmin/switches/{id}`
- **用途**：更新台账字段（password 留空=保持原值；password_enc 键存在才更新密码，store 白名单控制）
- **鉴权**：X-ETP-Console-Token + admin
- **请求参数**：`{"name?","ip?","ssh_port?","username?","password?","brand?"}`（name/ip/username 传空 400；ip 传时格式校验 400）
- **响应**：`{"ok":true}`；不存在 404
- **代码出处**：api.py:1115-1133+ → store.py `switch_update`；审计 SWITCH_UPDATED
- **状态**：在用
- **登记记录**：2026-09-09，代码实证（server-platform-dev 下发，commit f031152，ADR-026）

#### SRV-077 交换机删除 `DELETE /api/v1/console/sysadmin/switches/{id}`
- **用途**：删除台账条目
- **鉴权**：X-ETP-Console-Token + admin
- **请求参数**：路径 `{id}`
- **响应**：`{"ok":true}`；不存在/重复删除 404
- **代码出处**：api.py `_console_sysadmin` → store.py `switch_delete`；审计 SWITCH_DELETED
- **状态**：在用
- **登记记录**：2026-09-09，代码实证（server-platform-dev 下发，commit f031152，ADR-026）

> 注（SRV-072~077）：本模块仅凭据/台账维护，**无 SSH 连通测试端点**（ADR-026 边界）；敏感语义：settings `switch.default_password` 为 SENSITIVE_KEYS 成员（list 接口自动脱敏，settings.py:15），switches.password_enc 经 SettingsStore.encrypt 复用同一 SecretsBox（settings.py:73-75，store.py:170-177 表结构）；deploy.py 幂等预置默认凭据（以 password --mask 判断 unset，stdin 注入）。

### 1.12 会话信息

#### SRV-069 当前会话信息 `GET /api/v1/console/session-info`
- **用途**：控制台前端获取当前登录者身份（username/role/must_change_password）
- **鉴权**：X-ETP-Console-Token
- **请求参数**：无
- **响应**：`{"ok":true,"username":"admin","role":"admin","must_change_password":false}`
- **调用方式**：`curl http://<server>/api/v1/console/session-info -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `dispatch`（分支 api.py:243-248）→ auth_upgrade.py `get_user`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证（server-platform-dev 下发，commit 4d2924b）

---

### 1.13 自动开关机（power-control P0，2026-09-16 新增）

> 组级约定：终端只读快照上报 + 控制台时间线查询；存储 `power_snapshots` 表（server/power_control.py `PowerControlStore`，自持连接 WAL，desktop_policy 同款形态，app.py `ctx.pc` 挂载）。终端侧引擎 power-control/power_control.py（P0 全只读，零写操作）。规格 docs/POWER_CONTROL_SPEC.md，决策 power-control/docs/DECISIONS.md（ADR-001~005）。
> 组扩展（2026-09-17，开关机管控页 ADR-040）：策略批量下发（命令通道 `pc_apply_policy`，见 UPL-014）+ 批次记录（pc_policy_dispatch，含 note 列，PRAGMA 预检幂等迁移）+ 手动维护登记台账（`pc_manual_config` 表：terminal_id PK / boot_json / shutdown_json / note / operator / updated_ts）。**手动登记语义：平台侧登记台账，不下发终端**（人工进 BIOS 配置后在平台登记；RTC 列手动登记优先展示，与平台下发批次记录互不覆盖）。下发 payload 校验复用 `_validate_pc_sched` 矩阵（boot/shutdown 同构：enabled/mode/time/weekdays/date）。
> 组扩展（2026-09-18，4.1.5 电源行动与 WoL 产品化 ADR-044/045，commit 85ae733）：①立即重启/关机（power_action，UPL-016）+ 撤销（power_action_abort，UPL-017，shutdown /a 语义，rc=1116 无 pending 为正常业务态）②WoL 唤醒三通道：服务器直发（wol/direct）/ 在线终端跨网段中继（wol-relay，UPL-018）+ 同 /24 中继选举（wol/relays）/ 定时唤醒计划台账（wol/schedules CRUD + wol_attempts 留痕 phase=direct|relay|confirm|giveup）。配套 server/wol.py **调度守护线程（20s 节拍，app.py 装配；config `wol_enabled=false` 可整体关闭）**+ wol_schedules/wol_attempts 两表（power_control.py）。**WoL 确认语义：ok=true 仅代表命令入队/UDP 已发送（无确认）——唤醒成功唯一定案口径为目标机 last_seen 恢复**。终端执行面：主仓 0c5f1e0（客户端 4.1.5，power_action/wol_relay 一包双能力，uplink.py:764+ handler；高危红线：命令串仅由受控白名单参数构造 power_action.py）。

#### SRV-080 电源策略快照上报 `POST /api/v1/terminals/{tid}/powercontrol/snapshot`
- **用途**：终端上报本机电源策略快照（机型/BIOS 自动开机能力与 RTC 项/唤醒定时器/关机计划任务/快速启动），服务端时间线存档
- **鉴权**：X-ETP-Token（+ 准入）
- **请求参数**：快照 JSON 对象（schema=1：`machine/bios/wake_timers/shutdown_tasks/fast_startup/errors`，必含 `collected_ts`；体积上限 256KB）
- **响应**：`{"ok":true,"snapshot_id":1,"terminal_id":"WIN-xxx"}`；非对象 body 400；终端未注册 404
- **代码出处**：api.py `_terminal_api` powercontrol 分支（api.py:462-476，`len(parts)==6 and parts[4]=="powercontrol"`）→ power_control.py `save_snapshot`
- **状态**：在用（P0）
- **调用方式**：`curl -X POST http://<server>/api/v1/terminals/WIN-xxx/powercontrol/snapshot -H "X-ETP-Token: <token>" -H "Content-Type: application/json" -d '{"schema":1,"collected_ts":...,...}'`
- **登记记录**：2026-09-16，代码实证（power-control-dev 实施并登记，api-registrar-dev 复核归档；smoke_power_srv.py 10/10）> 更新 2026-09-16（晚）：复核补强——代码出处行号、在途未提交标注、调用方式补全 > 更新 2026-09-16（晚 2）：**在途标注消项**——服务端实现入库 **ec726af**（server-platform HEAD，ADR-038：api.py +54 / app.py +3 / power_control.py +134 / smoke +136，git show 实证；api.py/app.py/power_control.py 工作区复核已干净）

#### SRV-081 电源快照查询（控制台）`GET /api/v1/console/powercontrol/terminals/{tid}/snapshots`
- **用途**：控制台按终端查快照（`latest=1` 最新一条；否则历史时间线）
- **鉴权**：X-ETP-Console-Token
- **请求参数**：`latest=1`（可选）；`limit`（默认 50，≤500）；`since`（created_ts 下限，可选）
- **响应**：`{"ok":true,"snapshot":{...}}`（latest）或 `{"ok":true,"total":N,"snapshots":[...]}`（history，snapshot 字段已 JSON 解析）；终端不存在 404
- **代码出处**：api.py `_console_powercontrol`（dispatch 分支 api.py:965-966 `parts[:4]==["api","v1","console","powercontrol"]`，定义 api.py:1244+）→ power_control.py `latest_snapshot`/`history_snapshots`
- **状态**：在用（P0；控制台 UI 展示由 server-platform-dev 后续接入）
- **调用方式**：`curl "http://<server>/api/v1/console/powercontrol/terminals/WIN-xxx/snapshots?latest=1" -H "X-ETP-Console-Token: <token>"`；历史 `curl "http://<server>/api/v1/console/powercontrol/terminals/WIN-xxx/snapshots?limit=50" -H "X-ETP-Console-Token: <token>"`
- **登记记录**：2026-09-16，代码实证（power-control-dev 实施并登记，api-registrar-dev 复核归档；smoke_power_srv.py 10/10）> 更新 2026-09-16（晚）：复核补强——代码出处行号、在途标注、调用方式补全 > 更新 2026-09-16（晚 2）：**在途标注消项**——同 SRV-080 入库 ec726af（ADR-038，代码实证）

#### SRV-093 手动维护登记读取 `GET /api/v1/console/powercontrol/terminals/{tid}/manual-config`
- **用途**：单终端手动维护登记读取（人工进 BIOS 配置后在平台的登记台账；**无登记 → config:null**）
- **鉴权**：X-ETP-Console-Token（operator 可用）
- **请求参数**：路径 `{tid}`
- **响应**：`{"ok":true,"config":{boot,shutdown,note,operator,updated_ts}|null}`；终端不存在 404（terminal not found）
- **代码出处**：api.py `_console_powercontrol` GET manual-config 分支(:1413-1418) → power_control.py `manual_get`
- **状态**：在用
- **登记记录**：2026-09-17，代码实证（server-platform-dev 实施，commit 95dbb1c/ADR-040 follow-up，api-registrar-dev 复核登记）

#### SRV-094 手动维护登记/更新 `PUT /api/v1/console/powercontrol/terminals/{tid}/manual-config`
- **用途**：登记/更新手动维护记录（**平台侧台账，不下发终端**；RTC 列手动登记优先展示的数据源）
- **鉴权**：X-ETP-Console-Token（operator 可用；manual_config 审计）
- **请求参数**：body `{"boot":{...}?,"shutdown":{...}?,"note":"..."}`——boot/shutdown 与下发 payload 同构（enabled/mode/time/weekdays/date，仅 enabled=true 的区块生效）复用 `_validate_pc_sched` 校验矩阵；`note` 必填 ≥2 字 ≤500（说明人工维护内容）；**至少启用一项**否则 400
- **响应**：`{"ok":true,"config":{...}}`；终端 404；校验失败 400（note 短/长、未启用任何项）；审计 `powercontrol.manual_config`（detail.ops 记 boot/shutdown）
- **代码出处**：api.py `_console_powercontrol` PUT 分支(:1422-1451) → power_control.py `manual_upsert`
- **状态**：在用
- **登记记录**：2026-09-17，代码实证（同 SRV-093）

#### SRV-095 手动维护登记清除 `DELETE /api/v1/console/powercontrol/terminals/{tid}/manual-config`
- **用途**：清除该终端手动维护登记
- **鉴权**：X-ETP-Console-Token（operator 可用；manual_config 审计）
- **请求参数**：路径 `{tid}`
- **响应**：`{"ok":true,"deleted":bool}`（无登记时 deleted=false）；终端 404；删除成功时审计 `powercontrol.manual_config`（op=delete）
- **代码出处**：api.py `_console_powercontrol` DELETE 分支(:1454-1464) → power_control.py `manual_delete`
- **状态**：在用
- **登记记录**：2026-09-17，代码实证（同 SRV-093）

#### SRV-096 手动登记台账列表 `GET /api/v1/console/powercontrol/manual-configs?limit=`
- **用途**：手动维护登记台账列表（RTC 列「手动优先展示」数据源）
- **鉴权**：X-ETP-Console-Token（operator 可用）
- **请求参数**：query `limit`（默认 200）
- **响应**：`{"ok":true,"configs":[{terminal_id,boot,shutdown,note,operator,updated_ts},...]}`
- **代码出处**：api.py `_console_powercontrol` manual-configs 分支(:1468-1471) → power_control.py `manual_list`
- **状态**：在用
- **登记记录**：2026-09-17，代码实证（同 SRV-093）

#### SRV-097 批量下发定时开关机 `POST /api/v1/console/powercontrol/policies/dispatch`【漏登补登 2026-09-17】
- **用途**：按终端组批量下发定时开关机策略（**经命令通道 pc_apply_policy 到终端执行**，见 UPL-014；批次入 pc_policy_dispatch 表；95dbb1c 增补 note ≤500 字透出批次记录与详情）
- **鉴权**：X-ETP-Console-Token（operator 可用；powercontrol.dispatch 审计）
- **请求参数**：body `{"terminal_ids":[...]（非空数组 ≤100 台、去重否则 400）,"boot":{...}?,"shutdown":{...}?（至少一项，_validate_pc_sched）,"note":"≤500 字"?}`；终端不存在 404（逐台校验）
- **响应**：`{"ok":true,"dispatch_id":N,"policy_id":"<hex32>","total":N,"queued_offline":N}`（offline 判定=last_seen 距今 ≥ heartbeat_timeout_sec，离线终端命令仍入队待心跳拉取）；审计 detail 记 policy_id/终端数/ops
- **代码出处**：api.py `_console_powercontrol` dispatch 分支(:1474-1528) → power_control.py `create_dispatch`/`bind_command` + store.py `enqueue_command`（command=pc_apply_policy，timeout_sec=604800，source=powercontrol）
- **状态**：在用（**ADR-040 主体组漏登补登**：随主仓统筹 a099448/server-platform 3fe299e 引入时未随批登记，本次对账发现补登）
- **登记记录**：2026-09-17，代码实证（引入 commit 主仓 a099448/server-platform 3fe299e；note 增补 95dbb1c；api-registrar-dev 对账补登）

#### SRV-098 批次记录列表 `GET /api/v1/console/powercontrol/policies/dispatches?limit=`【漏登补登】
- **用途**：下发批次记录列表（含 note；与手动登记台账 SRV-096 互不覆盖）
- **鉴权**：X-ETP-Console-Token（operator 可用）
- **请求参数**：query `limit`（默认 50）
- **响应**：`{"ok":true,"dispatches":[{...批次行...}],"total":N}`
- **代码出处**：api.py `_console_powercontrol` dispatches 分支(:1531-1534) → power_control.py `dispatch_list`
- **状态**：在用（漏登补登同 SRV-097）
- **登记记录**：2026-09-17，代码实证（同 SRV-097）

#### SRV-099 批次记录详情 `GET /api/v1/console/powercontrol/policies/dispatches/{id}`【漏登补登】
- **用途**：单批次详情（含逐终端下发/回执状态；id 非数字 404）
- **鉴权**：X-ETP-Console-Token（operator 可用）
- **请求参数**：路径 `{id}`（整数）
- **响应**：`{"ok":true,"dispatch":{...批次详情含 note 与逐终端行...}}`；不存在 404 dispatch not found
- **代码出处**：api.py `_console_powercontrol` dispatches/{id} 分支(:1537-1542) → power_control.py `dispatch_get`
- **状态**：在用（漏登补登同 SRV-097）
- **登记记录**：2026-09-17，代码实证（同 SRV-097）

#### SRV-109 发起只读诊断 `POST /api/v1/console/terminals/{tid}/diag`
- **用途**：发起终端只读诊断（pc_diag 命令通道联调，ADR-040 增补；**高危读面按 admin 把关**：结果含 password_state/rtc_readback 等敏感项）
- **鉴权**：X-ETP-Console-Token（**admin-only**，非 admin 403 + ACCESS_DENIED 审计）
- **请求参数**：路径 `{tid}`；body 无（命令 args 固定 `{"kind":"pc_diag"}`）
- **响应**：`{"ok":true,"diag_id":N,"command_id":N}`（入 pc_diag 命令队列 timeout 600s，source=powercontrol；回执经 :491 钩子存档 pc_diag_records）；终端 404；模块未初始化 500；审计 `powercontrol.diag`（detail 记 diag_id/command_id）
- **代码出处**：api.py `_console_api` diag POST 分支(:1018-1045) → store.py `enqueue_command` + power_control.py `diag_create`；命令白名单 :1001（pc_apply_policy, pc_diag）
- **状态**：在用（已生产部署验证：备份 deploy_20260917_185433，diag#1 completed 全文交付 ADR-006 定案——server-platform-dev 下发）
- **登记记录**：2026-09-17，代码实证（server-platform-dev 实施，commit 5f0ba2f/ADR-040 增补，api-registrar-dev 复核登记；**按常设约定显式列清单知会**）

#### SRV-110 终端诊断记录读取 `GET /api/v1/console/terminals/{tid}/diag`
- **用途**：该终端最近 10 条 pc_diag 诊断记录
- **鉴权**：X-ETP-Console-Token（operator 可读）
- **请求参数**：路径 `{tid}`
- **响应**：`{"ok":true,"diags":[...≤10 条]}`（status pending→completed/failed）
- **代码出处**：api.py diag GET 分支(:1047-1055) → power_control.py `diag_list`
- **状态**：在用（生产已部署验证同 SRV-109）
- **登记记录**：2026-09-17，代码实证（同 SRV-109）

#### SRV-111 诊断记录列表 `GET /api/v1/console/powercontrol/diags?terminal_id=&limit=`
- **用途**：pc_diag 诊断记录跨终端列表（联调通道）
- **鉴权**：X-ETP-Console-Token（operator 可读）
- **请求参数**：query `terminal_id`（可选过滤）、`limit`（默认 50）
- **响应**：`{"ok":true,"diags":[...]}`
- **代码出处**：api.py `_console_powercontrol` diags 分支(:1524-1529) → power_control.py `diag_list`
- **状态**：在用（生产已部署验证同 SRV-109）
- **登记记录**：2026-09-17，代码实证（同 SRV-109）

#### SRV-112 诊断原始 JSON 详情 `GET /api/v1/console/powercontrol/diags/{id}`
- **用途**：单条诊断原始 JSON 详情（save_class/password_state/rtc_readback/last_apply.attempts/log_tail/client_version 全文）
- **鉴权**：X-ETP-Console-Token（operator 可读）
- **请求参数**：路径 `{id}`（整数，非数字 404 路径不匹配）
- **响应**：`{"ok":true,"diag":{...原始 JSON，result_json ≤256KB}}`；不存在 404 diag record not found
- **代码出处**：api.py `_console_powercontrol` diags/{id} 分支(:1532-1537) → power_control.py `diag_get`
- **状态**：在用（生产已部署验证同 SRV-109）
- **登记记录**：2026-09-17，代码实证（同 SRV-109）

#### SRV-113 立即重启/关机 `POST /api/v1/console/powercontrol/terminals/{tid}/power-action`
- **用途**：中心发起立即重启/关机（命令通道 UPL-016 power_action；终端倒计时知会弹窗、不提供本地取消，撤销走 SRV-114）
- **鉴权**：X-ETP-Console-Token（**admin-only** 403+审计 `powercontrol.power_action`）
- **请求参数**：body `{"action":"shutdown"|"restart","delay_sec":0-3600(缺省 60),"force":bool(缺省 true)}`
- **响应**：`{"ok":true,"command_id":N,"action":...,"delay_sec":...,"force":...}`；**409 终端离线（在线守卫）**；400 参数校验
- **代码出处**：api.py `_console_powercontrol` power-action 分支(:1640-1671，`_pc_admin` 装饰)
- **状态**：在用（终端面 4.1.5，主仓 0c5f1e0）
- **登记记录**：2026-09-18，代码实证（server-platform-dev 实施，commit 85ae733/ADR-044，按常设约定显式列清单知会，api-registrar-dev 复核登记）

#### SRV-114 撤销待执行关机/重启 `POST /api/v1/console/powercontrol/terminals/{tid}/power-action/abort`
- **用途**：撤销 pending 关机/重启（UPL-017 power_action_abort，终端执行 `shutdown /a` 语义）
- **鉴权**：X-ETP-Console-Token（**admin-only** 403+审计 `powercontrol.power_action_abort`）
- **请求参数**：路径 `{tid}`；body 无
- **响应**：`{"ok":true,"command_id":N}`；**终端回执 rc=1116 为「无 pending」正常业务态**（非失败）
- **代码出处**：api.py power-action/abort 分支(:1673-1686)
- **状态**：在用
- **登记记录**：2026-09-18，代码实证（同 SRV-113）

#### SRV-115 指派在线终端代发 WoL 魔术包 `POST /api/v1/console/powercontrol/terminals/{tid}/wol-relay`
- **用途**：指派该在线终端代发目标机魔术包（UPL-018 wol_relay，**跨网段中继主通道**）
- **鉴权**：X-ETP-Console-Token（**admin-only** 403+审计 `powercontrol.wol_relay`）
- **请求参数**：body `{"mac":"任意常见分隔格式","broadcast":"IPv4 点分四段","port":1-65535(缺省 9)}`
- **响应**：`{"ok":true,"command_id":N,"mac":"<归一大写 12hex>","broadcast":...,"port":...}`；**409 中继终端离线**；400 校验拒绝
- **WoL 确认语义**：本端 ok=true 仅代表命令入队；终端回执 ok=true 仅代表 UDP 已发送（**无确认**）——唤醒成功唯一定案口径=目标机 last_seen 恢复
- **代码出处**：api.py wol-relay 分支(:1688-1715)
- **状态**：在用
- **登记记录**：2026-09-18，代码实证（同 SRV-113）

#### SRV-116 服务器直发 WoL 魔术包 `POST /api/v1/console/powercontrol/wol/direct`
- **用途**：服务器直发魔术包（手动唤醒，无中继场景）
- **鉴权**：X-ETP-Console-Token（**admin-only** 403+审计 `powercontrol.wol_direct`）
- **请求参数**：body `{"terminal_id":"...","mac"?,"broadcast"?,"port"?}`——mac/broadcast 缺省时**从资产明细自动带出**
- **响应**：`{"ok":true,"mac":...,"sent":["broadcast:port",...],"note":...}`（ok=有任一发送成功）
- **适用边界（2026-09-18 对照实验定案，team-lead 行动项）**：跨网段直发受三层设备 **directed-broadcast 过滤限制**——服务器→跨网段目标机对照实验：单播 3/3 秒达、定向广播 0/3 达（90s 窗，探测端口 9147；生产 IP 脱敏不录）——此前直发跨网段目标 300s 未恢复与 08:30 定时无效均由此解释；**跨网段场景以 SRV-115 wol_relay 为主通道**，服务器直发仅对与服务器同网段的部署形态有效（method=direct 显式指定时保留）；服务端 wol.py 已实现 `direct_applicable` 跳直发优化（auto+跨网段直接起步中继，调度器行为与本注记对齐，ADR-044 增补）
- **代码出处**：api.py wol/direct 分支(:1734-1769)
- **状态**：在用（direct_applicable 优化已随调度器生效）
- **登记记录**：2026-09-18，代码实证（同 SRV-113）> 更新 2026-09-18（晚）：适用边界语义注记追加（对照实验定案，server-platform-dev 显式知会转达 team-lead 行动项；生产 IP 已脱敏）

#### SRV-117 同网段中继选举 `GET /api/v1/console/powercontrol/wol/relays?terminal_id={目标}`
- **用途**：为目标机选举同网段在线中继候选
- **鉴权**：X-ETP-Console-Token（**读端：登录控制台用户即可**，非 admin）
- **请求参数**：query `terminal_id`（目标机）
- **响应**：`{"ok":true,"relays":[{terminal_id,ip,last_seen}...],"target":{terminal_id,mac,asset_ip,broadcasts:[...]}}`（选举规则：同 /24 + 在线 + 排除目标机 + last_seen 降序；**IP 口径：asset.network[] 自报全量 + 连接源兜底（ADR-043 同源）**）
- **代码出处**：api.py wol/relays 分支(:1717-1732)
- **状态**：在用
- **登记记录**：2026-09-18，代码实证（同 SRV-113）

#### SRV-118 WoL 定时唤醒台账 `GET /api/v1/console/powercontrol/wol/schedules`
- **用途**：定时唤醒计划台账 + 最近尝试留痕（调度守护 server/wol.py 20s 节拍消费）
- **鉴权**：X-ETP-Console-Token（**读端 operator 可用**）
- **请求参数**：无
- **响应**：`{"ok":true,"schedules":[...],"attempts":[最近 30 条留痕，phase=direct|relay|confirm|giveup]}`
- **代码出处**：api.py wol/schedules GET 分支(:1771-1778) → power_control.py `wol_schedule_list`/wol_attempts 表
- **状态**：在用
- **登记记录**：2026-09-18，代码实证（同 SRV-113）

#### SRV-119 WoL 定时唤醒计划写操作 `POST|PUT|DELETE /api/v1/console/powercontrol/wol/schedules[/{id}]`
- **用途**：计划创建/更新/删除（调度守护按 time 每日触发，method=auto|direct|relay 缺省 auto 自动选举通道）
- **鉴权**：X-ETP-Console-Token（**admin-only** 403；审计 `powercontrol.wol_schedule` / `powercontrol.wol_schedule_delete`）
- **请求参数**：POST `{"terminal_id","time":"HH:MM","name"?≤60,"mac"?,"method"?}`；PUT（/{id}）`{"enabled"?,"time_hhmm"?,"name"?,"method"?,"mac"?}`；DELETE（/{id}）
- **响应**：`{"ok":true,...}`；**UNIQUE(terminal_id,time) 冲突 409**
- **代码出处**：api.py schedules POST(:1777-1813)/PUT(:1815-1847)/DELETE(:1849-1860) → power_control.py wol_schedules 表
- **状态**：在用
- **登记记录**：2026-09-18，代码实证（同 SRV-113）

### 1.16 火绒终端安全与资产融合（huorong/assets，ADR-033 系，2026-09-17 补登）

> 来源：huorong-dev 按常设约定（统筹提交显式列清单）主动补知会，三批次一并登记——b6a74f6（P1 后端全链）/ 9ab89ac（资产融合，ADR-033 增补）/ 7842aab（弹窗数据层，ADR-033 增补二）。
> 鉴权：全部 console scope（X-ETP-Console-Token）；GET 类 operator 可读，写操作 assign-link/assign-group operator 可执行 + audit；**sync 为 admin-only**（403+ACCESS_DENIED 审计）。
> 数据面铁律：全部只读缓存表，**绝不透传火绒实时请求**；任务类破坏性接口无路由（ADR-033 契约冻结）。clients 端点 IP/MAC 为真实值不脱敏（控制台内网运维场景）。
> ⚠️ 部署状态：代码均已上线、**生产未部署**（火绒凭据未注入），实际可用性以部署后为准。
> 语义变更锚：9ab89ac 起 SRV-101 groups 的 total/online 从镜像列改为**分组覆盖应用后实时聚合**（与统一视图 SRV-105 口径一致）；a6282bf 起 SRV-103 sync 的 409/400 错误文案中文化。

#### SRV-100 火绒概览 KPI `GET /api/v1/console/huorong/overview`
- **用途**：火绒概览统计（组数/终端总数/在线数/在线率/Win7 EOL 计数/最近成功同步时间）
- **鉴权**：X-ETP-Console-Token（operator 可读）
- **请求参数**：无
- **响应**：`{"ok":true, ...store.hr_overview() KPI 字段}`（只读缓存表）
- **代码出处**：api.py `_console_huorong`(1097) overview 分支(:1102-1103) → store.py `hr_overview`
- **状态**：在用（生产待部署）
- **登记记录**：2026-09-17，代码实证（huorong-dev 实施并按常设约定补知会，批次 b6a74f6，api-registrar-dev 复核登记）

#### SRV-101 火绒分组列表 `GET /api/v1/console/huorong/groups`
- **用途**：火绒分组列表（镜像快照）
- **鉴权**：X-ETP-Console-Token（operator 可读）
- **请求参数**：无
- **响应**：`{"ok":true,"groups":[...]}`（只读缓存）；**语义变更（9ab89ac 起）**：条目 total/online 从镜像列改为**分组覆盖应用后实时聚合**（与统一视图 SRV-105 口径一致）
- **代码出处**：api.py `_console_huorong` groups 分支(:1105-1107) → store.py `hr_groups_list`
- **状态**：在用（生产待部署）
- **登记记录**：2026-09-17，代码实证（同 SRV-100）> 语义变更随 9ab89ac 入注（代码实证）

#### SRV-102 火绒终端分页 `GET /api/v1/console/huorong/clients`
- **用途**：火绒终端列表分页（组/在线/关键字过滤；IP/MAC 真实值不脱敏）
- **鉴权**：X-ETP-Console-Token（operator 可读）
- **请求参数**：query `group_id`（int，非法 400 bad group_id）、`online`（仅接受 0/1，其它值忽略）、`q`（关键字）、`page`（默认 1）、`page_size`（默认 50，≤200）
- **响应**：`{"ok":true, ...hr_clients_page 分页数据}`
- **代码出处**：api.py `_console_huorong` clients 分支(:1109-1121) → store.py `hr_clients_page`
- **状态**：在用（生产待部署）
- **登记记录**：2026-09-17，代码实证（同 SRV-100）

#### SRV-103 手动触发火绒同步 `POST /api/v1/console/huorong/sync`
- **用途**：手动触发火绒镜像同步（与周期同步共用**互斥锁**）
- **鉴权**：X-ETP-Console-Token（**admin-only**，operator 一律 403 + ACCESS_DENIED 审计）
- **请求参数**：无
- **响应**：成功 `{"ok":true,"result":{...}}`；同步中 **409**「同步进行中，请稍后再试」（a6282bf 中文化）；凭据未配置 **400**「火绒凭据未配置，请在系统设置中注入后重试」；同步器未初始化 400
- **代码出处**：api.py `_console_huorong` sync 分支(:1131-1147) → huorong syncer `sync_once(trigger="manual")` / `configured()`
- **状态**：在用（生产待部署——凭据未注入时恒 400）
- **登记记录**：2026-09-17，代码实证（同 SRV-100）> 文案中文化随 a6282bf 入注（代码实证）

#### SRV-104 资产融合组树 `GET /api/v1/console/assets/groups`
- **用途**：融合两段组树——`platform_groups`（既有资产组体系）+ `huorong_groups`（火绒 89 组，含覆盖后 total/online/matched 计数）+ `other`（未关联平台终端计数）
- **鉴权**：X-ETP-Console-Token（operator 可读）
- **请求参数**：无
- **响应**：`{"ok":true,"platform_groups":[...],"huorong_groups":[...],"sync":{...幂等惰性镜像同步状态,ADR-041},"other":{platform_total,platform_online}}`
- **代码出处**：api.py `_console_assets`(1159) groups 分支(:1164-1178) → store.py `asset_group_sync_huorong`/`hr_groups_with_stats`/`hr_platform_unlinked`
- **状态**：在用（生产待部署）
- **登记记录**：2026-09-17，代码实证（huorong-dev 补知会，批次 9ab89ac/ADR-033 增补，api-registrar-dev 复核登记）

#### SRV-105 融合终端统一条目分页 `GET /api/v1/console/assets/terminals`
- **用途**：融合统一条目分页（kind=matched/huorong_only/platform_only 三类；双视角组过滤 + 跨侧搜索）
- **鉴权**：X-ETP-Console-Token（operator 可读）
- **请求参数**：query `group_source`（platform|huorong，默认 huorong，非法 400）、`group_id`（含 "other"=未关联组）、`q`（跨两侧标识字段搜索）、`page`、`page_size`（默认 50，≤200）
- **响应**：`{"ok":true,"total":N,"page":1,"page_size":50,"items":[{kind,...合并字段}]}`（内存分页）
- **代码出处**：api.py `_console_assets` terminals 分支(:1180-1193) → store.py `hr_unified_items`
- **状态**：在用（生产待部署）
- **登记记录**：2026-09-17，代码实证（同 SRV-104）

#### SRV-106 手动关联火绒终端↔平台终端 `POST /api/v1/console/assets/assign-link`
- **用途**：手动建立/解除绑定关系（**manual 抢占 auto**；解除写 ignore 对防自动拉回）
- **鉴权**：X-ETP-Console-Token（operator 可执行 + audit）
- **请求参数**：body `{"huorong_client_id":"...","terminal_id":"..."|null}`——client 不存在 400；terminal_id=null → 解除关联 + `hr_ignore_add`（下轮自动匹配 pass 不拉回）；指定关联时目标终端已被其它 manual 占用 → **409**「目标终端已被其它手动关联占用，请先解除」；正向指定即解除该对 ignore（hr_ignore_clear）
- **响应**：关联 `{"ok":true,"linked":true,"match_type":"manual"}`；解除 `{"ok":true,"linked":false,"ignored":bool}`
- **审计**：hr_link / hr_unlink
- **代码出处**：api.py `_console_assets` assign-link 分支(:1195-1220) → store.py `hr_map_manual_set`/`hr_map_delete`/`hr_ignore_add`/`hr_ignore_clear`
- **状态**：在用（生产待部署）
- **登记记录**：2026-09-17，代码实证（同 SRV-104）

#### SRV-107 手动调整火绒分组 `POST /api/v1/console/assets/assign-group`
- **用途**：手动调整火绒终端分组（`hr_group_override` 持久化，同步后应用；group_id=0/null 解除覆盖回原生分组）
- **鉴权**：X-ETP-Console-Token（operator 可执行 + audit）
- **请求参数**：body `{"client_id":"...","group_id":N|0|null}`——client 不存在 400；group_id 非法 400；组不存在 400；0/null → `hr_override_set(null)` + `hr_override_reset_native` 回原生
- **响应**：设置 `{"ok":true,"group_id":N}`；解除 `{"ok":true,"group_id":<原生组>}`
- **审计**：hr_group / hr_group_reset
- **代码出处**：api.py `_console_assets` assign-group 分支(:1222-1244) → store.py `hr_override_set`/`hr_override_apply`/`hr_override_reset_native`
- **状态**：在用（生产待部署）
- **登记记录**：2026-09-17，代码实证（同 SRV-104）

#### SRV-108 火绒块聚合（弹窗数据层）`GET /api/v1/console/huorong/context`
- **用途**：资产右键「第三方数据源信息」弹窗·火绒块（绑定关系/火绒终端字段/组命名路径/病毒统计快照；只读缓存）
- **鉴权**：X-ETP-Console-Token（operator 可读）
- **请求参数**：query `terminal_id`（必填，缺失 400 missing terminal_id）
- **响应**：`{"ok":true,"huorong":{...hr_context_block}}`——与 SRV-089 聚合端点的 huorong 块同函数同源（store.hr_context_block）
- **代码出处**：api.py `_console_huorong` context 分支(:1123-1129) → store.py `hr_context_block`（批次 7842aab/ADR-033 增补二）
- **状态**：在用（生产待部署）
- **登记记录**：2026-09-17，代码实证（huorong-dev 补知会，api-registrar-dev 复核登记）> 更新 2026-09-19：响应向后兼容扩展（commit 8f9d5ad，ADR-033 增补三，huorong-dev 知会）——huorong 块新增 `huorong.client.first_seen/last_off/this_on`（int 秒级时间戳或 null；对应火绒官方 `_list` 的 first_appear_time/last_off_time/this_on_time，详见 EXT-007 更新行）与 `huorong.assets`（{登记字段名:值} dict 或 **null=未登记**）；展示层白名单（主键=控制台截图实证口径，次键=官方文档示例名，生产首轮校准点）：楼层 / 具体位置(回退:位置) / 工号 / 姓名 / 使用科室(回退:部门)；assets=null 或无对应键 → 前端显示「未登记」。同步面：HuorongSyncer _info2 分批 50 client_id/批入库（失败不阻断主同步）。消费方：SRV-138 资产定位火绒条目 registration 白名单投影同口径（asset_locate.py REG_WHITELIST）

---

### 1.17 开关机管控任务化（power_tasks，ADR-046/047，2026-09-19 登记）

> 来源：server-platform-dev 按常设约定显式列清单交付（commit 99f4079，ADR-047 立项；任务为中心统一模型 ADR-046）。SRV-120 起共 18 条。
> 组级约定：console 组（`/api/v1/console/powercontrol/`，api.py `_console_powercontrol`）读 operator / 写 admin（`_pc_admin`）403+审计；终端组（`/api/v1/terminals/{tid}/powercontrol/...`，api.py `_terminal_api`）鉴权 X-ETP-Token（白名单准入 `_admission`）。存储新增 **power_tasks / holidays / pc_shutdown_config** 三表，`wol_schedules` 增 `task_id` 列（boot 保存/编辑时目标展开写回，diff 保留运行态）。示例 host 统一 `http://127.0.0.1:18090`，控制台头 `-H "X-ETP-Console-Token: <token>"`、终端头 `-H "X-ETP-Token: <token>"`，下同。
> **origin 语义（随批入档）**：`platform`=中心任务——控制台 POST /tasks 强制覆写 origin=platform（个性化走终端口），PUT 不可漂移（保留原值）；`client_personal`=终端个性化任务——仅终端口创建、**boot-only**（shutdown+client_personal 校验拒绝）、归属锁定（targets 必含本终端）、每终端上限 5 条 fail-closed（`PERSONAL_TASK_LIMIT=5`）、operator="client:{tid}"。
> **任务载荷规范**（power_control.py `validate_task_payload` :1207-1296，API/单测共用）：`kind`=boot|shutdown；`source`=platform|huorong|nad（shutdown 仅 platform——第三方终端无客户端）；`target_type`=group|terminals（group_id>0 / targets 非空去重 ≤200 台 `_TASK_MAX_TARGETS`）；`repeat`=daily|workday|holiday|weekly|once（shutdown 仅 daily|weekly|once——本地执行）；weekly→`weekdays` 7 位 0/1（周一..周日，至少一天）；once→`once_date` 未来日期；`time`=HH:MM（time/time_hhmm 别名归一）；boot→`method`=auto|direct|relay（shutdown 固定 auto）；`name`≤60（缺省 task_gen_name 自动生成，UNIQUE(kind,name)）；PUT 增量合并（None 字段保持原值）。
> **对账注记（2026-09-19，已闭环）**：规划稿中 GET /tasks/{id}/runs 与 GET /runs/{id}/attempts 以实际路由为准——boot 执行留痕内嵌于任务详情（SRV-122 schedules/attempts）。**GET /runs/{id} 撤除定案（commit 513ffa8）**：该路由属 ADR-047 早期「pc_sched 中心调度 + runs 表」方案残留——方案已作废（架构修正：定时关机由终端本地执行，平台不做中心到期调度），runs 表从未建、run_get 从未实现，路由已撤、**永不启用，防未来误建**；关机任务执行历史最终形态 = ①声明下发批次（复用 pc_policy_dispatch/pc_policy_targets，SRV-097~099/ADR-040 既有登记）+ ②终端回读比对 drift（SRV-126），**无独立 runs 端点**。另 ADR-047 增补「工作日/节假日纯星期语义（日历为例外可选层，用户定案）」：calendar_fallback 响应字段删除（波及 SRV-129/SRV-134，已更新）、holiday-status 删 covered 字段（SRV-133，已更新）；/tasks 系列请求/响应无字段变化。

#### SRV-120 任务列表 `GET /api/v1/console/powercontrol/tasks?kind=&origin=&q=`
- **用途**：任务列表（附目标摘要；q 匹配名称或目标终端 tid/IP/主机名/MAC——个性化任务检索口径）
- **鉴权**：X-ETP-Console-Token（读 operator）
- **请求参数**：query `kind`=boot|shutdown（其余值视为不过滤）、`origin`=platform|client_personal、`q`（小写包含匹配：任务名 / 终端 tid / IP / 主机名 / 资产明细 network[].mac）
- **响应**：`{"ok":true,"tasks":[{...power_tasks 行,"target_summary":"<组名> · N 台"|"指定终端 N 台"}]}`（target_summary 实时聚合，组名取 asset_group_list）
- **调用方式**：`curl "http://127.0.0.1:18090/api/v1/console/powercontrol/tasks?kind=boot&origin=client_personal&q=office" -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_powercontrol` tasks 列表分支(:2022-2089) → power_control.py `task_list`
- **状态**：在用
- **登记记录**：2026-09-19，代码实证（server-platform-dev 实施，commit 99f4079/ADR-047，按常设约定显式列清单知会，api-registrar-dev 逐条核对 api.py/power_control.py 复核登记）

#### SRV-121 创建任务 `POST /api/v1/console/powercontrol/tasks`
- **用途**：创建中心任务（boot：校验后目标展开写回 wol_schedules，task_id 关联）
- **鉴权**：X-ETP-Console-Token（**admin-only** 403 + 审计 `powercontrol.task`）
- **请求参数**：载荷经 `validate_task_payload`（规范见组级约定），示例：

```json
{"kind": "boot", "name": "研发区早开", "repeat": "workday", "time": "08:20",
 "target_type": "group", "group_id": 3, "method": "auto", "enabled": true}
```
- **响应**：`{"ok":true,"task":{...}}`；boot 另附 `"expand":{...含 conflicts}` 与 `"warnings":["<原因>（<tid>）"...]`；同类型同名 409；资产组/终端不存在 404
- **调用方式**：`curl -X POST http://127.0.0.1:18090/api/v1/console/powercontrol/tasks -H "X-ETP-Console-Token: <token>" -H "Content-Type: application/json" -d '{"kind":"shutdown","repeat":"daily","time":"22:00","target_type":"terminals","targets":["<tid>"]}'`
- **代码出处**：api.py tasks POST 分支(:2091-2137) → power_control.py `validate_task_payload`/`task_create`/`wol_expand_for_task`/`task_gen_name`
- **状态**：在用
- **登记记录**：2026-09-19，代码实证（同 SRV-120）

#### SRV-122 任务详情 `GET /api/v1/console/powercontrol/tasks/{id}`
- **用途**：任务详情——boot 附调度行+最近尝试；shutdown 附声明配置模板
- **鉴权**：X-ETP-Console-Token（读 operator）
- **请求参数**：路径 `{id}`
- **响应**：boot → `{"ok":true,"task":{...,"schedules":[<wol_schedules 中 task_id 关联行，剔除 relay_queue/relay_tried>],"attempts":[<每调度行最近 8 条聚合，按 id 降序，截 30>]}}`；shutdown → `task.declared`（`task_shutdown_config` 声明模板；实际执行比对走 SRV-126）；404 task not found
- **调用方式**：`curl http://127.0.0.1:18090/api/v1/console/powercontrol/tasks/12 -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py tasks/{id} GET 分支(:2139-2162) → power_control.py `task_get`/`wol_schedule_list`/`wol_attempts_list`/`task_shutdown_config`
- **状态**：在用
- **登记记录**：2026-09-19，代码实证（同 SRV-120）

#### SRV-123 编辑任务 `PUT /api/v1/console/powercontrol/tasks/{id}`
- **用途**：编辑任务（增量合并校验；boot 重展开 diff 保留运行态）
- **鉴权**：X-ETP-Console-Token（**admin-only** 403 + 审计 `powercontrol.task`）
- **请求参数**：路径 `{id}`；载荷同 SRV-121（增量合并：None 字段保持原值，time/time_hhmm 别名归一）；**origin 不可漂移**（强制保留任务原 origin）
- **响应**：`{"ok":true,"task":{...},"expand"?}`（boot 重展开后 `task_set_enabled` 保持原 enabled）；404 task not found；组/终端不存在 404
- **代码出处**：api.py tasks/{id} PUT 分支(:2164-2208) → power_control.py `task_update`/`task_set_targets`/`wol_expand_for_task`
- **状态**：在用
- **登记记录**：2026-09-19，代码实证（同 SRV-120）

#### SRV-124 删除任务 `DELETE /api/v1/console/powercontrol/tasks/{id}`
- **用途**：删除任务（级联清调度行；runs 保留为历史）
- **鉴权**：X-ETP-Console-Token（**admin-only** 403 + 审计 `powercontrol.task_delete`）
- **请求参数**：路径 `{id}`；body 无
- **响应**：`{"ok":true}`；404 task not found
- **代码出处**：api.py tasks/{id} DELETE 分支(:2210-2220) → power_control.py `task_delete`
- **状态**：在用
- **登记记录**：2026-09-19，代码实证（同 SRV-120）

#### SRV-125 关机任务下发 `POST /api/v1/console/powercontrol/tasks/{id}/dispatch`
- **用途**：关机任务声明下发（展开目标 → 复用 pc_apply_policy 批次链：在线待拉取 / 离线排队上线补投，见 SRV-097/UPL-014 同链）
- **鉴权**：X-ETP-Console-Token（**admin-only** 403 + 审计 `powercontrol.task_dispatch`）
- **请求参数**：路径 `{id}`；body 无
- **响应**：`{"ok":true,"dispatch_id":N,"policy_id":"<hex32>","total":N,"queued_offline":N,"expand_note":"..."}`；仅 shutdown 任务（boot 400「开机任务由平台调度自动执行，无需下发」）；展开目标为空 409；**>100 台 400（单批上限）**；命令 `enqueue_command(pc_apply_policy, timeout_sec=604800, source=powercontrol)`
- **代码出处**：api.py tasks/{id}/dispatch 分支(:2234-2284) → power_control.py `expand_platform_targets`/`task_shutdown_config`/`_validate_pc_sched`(api.py:1535)/`create_dispatch`/`bind_command` + store.py `enqueue_command`
- **状态**：在用
- **登记记录**：2026-09-19，代码实证（同 SRV-120）

#### SRV-126 关机任务回读比对 `GET /api/v1/console/powercontrol/tasks/{id}/drift`
- **用途**：任务声明 vs 各目标上报回读态比对（三态 consistent/drift/not_reported + stale 陈旧标注）
- **鉴权**：X-ETP-Console-Token（读 operator）
- **请求参数**：路径 `{id}`
- **响应**：`{"ok":true,"drift":{"declared":{...声明配置},"note":"...","targets":[{terminal_id,state,stale,reported,reported_ts,version}]}}`（比对归一 `_cfg_equal`，声明形状与 pc_apply_policy shutdown-set 同构；stale 阈值 `SHUTDOWN_STALE_SEC`=7 天）；仅 shutdown（400）；404
- **代码出处**：api.py tasks/{id}/drift 分支(:2222-2232) → power_control.py `shutdown_drift_for_task`(:1074-1097)
- **状态**：在用
- **登记记录**：2026-09-19，代码实证（同 SRV-120）

#### SRV-127 每日开关机汇总 `GET /api/v1/console/powercontrol/daily-summary`
- **用途**：终端「每日开机 / 每日关机」列数据源（资产列表）
- **鉴权**：X-ETP-Console-Token（读 operator）
- **请求参数**：无
- **响应**：`{"ok":true,"map":{<tid>:{"boot":{"time":"HH:MM","total":N}?,"shutdown":{"time":..,"mode":..,"stale":bool}?}}}`——开机=命中该终端的启用中 daily 任务（组展开与执行引擎同源，取最早时间+命中任务数）；关机=终端上报的**本地实际配置**（真实态，非中心声明；stale 超 7 天标注）
- **代码出处**：api.py daily-summary 分支(:2350-2387) → power_control.py `task_list(kind="boot")`/`expand_platform_targets`/`shutdown_config_list`
- **状态**：在用
- **登记记录**：2026-09-19，代码实证（同 SRV-120）

#### SRV-128 终端检索 `GET /api/v1/console/powercontrol/term-search?q=`
- **用途**：终端检索（tid/IP/主机名/MAC；任务向导选择器）
- **鉴权**：X-ETP-Console-Token（读 operator）
- **请求参数**：query `q`（小写包含匹配；MAC 走资产明细 `get_terminal_asset().network[].mac`）
- **响应**：`{"ok":true,"terminals":[{terminal_id,hostname,ip,online}]}`（扫描前 500 终端、输出上限 60；online=last_seen 距今 < heartbeat_timeout_sec 缺省 180）
- **代码出处**：api.py term-search 分支(:2389-2420)
- **状态**：在用
- **登记记录**：2026-09-19，代码实证（同 SRV-120）

#### SRV-129 终端电源配置详情 `GET /api/v1/console/powercontrol/terminal-power-config?terminal_id=`
- **用途**：终端详情弹窗数据源（开机=命中中心任务同执行引擎解析；关机=终端上报实际配置）
- **鉴权**：X-ETP-Console-Token（读 operator）
- **请求参数**：query `terminal_id`（必填）
- **响应**：`{"ok":true,"boot":[{id,name,repeat,weekdays,once_date,time_hhmm,source,origin,method,next_ts,next_trigger}],"shutdown":{"config":{...},"version","reported_ts","stale":bool}|null}`；404 terminal not found > 更新 2026-09-19：响应删除 `calendar_fallback` 字段（ADR-047 增补工作日/节假日纯星期语义，commit 513ffa8；代码实证——server-platform 全目录 grep 零匹配）
- **代码出处**：api.py terminal-power-config 分支(:2422-2445) → power_control.py `boot_tasks_for_terminal`/`shutdown_config_get`
- **状态**：在用
- **登记记录**：2026-09-19，代码实证（同 SRV-120）

#### SRV-130 节假日日历读/写 `GET|POST /api/v1/console/powercontrol/holidays`
- **用途**：节假日日历（GET 查询 / POST 单条登记更新 upsert；workday=调休上班，供 repeat=workday/holiday 判定）
- **鉴权**：X-ETP-Console-Token（GET 读 operator；POST **admin-only** 403 + 审计 `powercontrol.holiday`）
- **请求参数**：GET query `year`（4 位数字，缺省全部）；POST `{"date":"YYYY-MM-DD","type":"holiday|workday","name"?≤60}`
- **响应**：GET `{"ok":true,"holidays":[...]}`；POST `{"ok":true}`；date 非 YYYY-MM-DD 400、type 非法 400
- **代码出处**：api.py holidays GET 分支(:2294-2298)/POST 分支(:2300-2315) → power_control.py `holiday_list`/`holiday_upsert`
- **状态**：在用
- **登记记录**：2026-09-19，代码实证（同 SRV-120）

#### SRV-131 节假日批量导入 `POST /api/v1/console/powercontrol/holidays/import`
- **用途**：年度日历批量导入
- **鉴权**：X-ETP-Console-Token（**admin-only** 403 + 审计 `powercontrol.holiday`）
- **请求参数**：`{"items":[{"date":"YYYY-MM-DD","type":"holiday|workday","name"?}...]}`——非空数组，**单次上限 500 条**（400）
- **响应**：`{"ok":true,"imported":N,"invalid":N}`
- **代码出处**：api.py holidays/import 分支(:2317-2332) → power_control.py `holiday_import`（返回 imported/invalid，逐条容错）
- **状态**：在用
- **登记记录**：2026-09-19，代码实证（同 SRV-120）

#### SRV-132 删除节假日 `DELETE /api/v1/console/powercontrol/holidays/{date}`
- **用途**：删除单条日历记录
- **鉴权**：X-ETP-Console-Token（**admin-only** 403 + 审计 `powercontrol.holiday_delete`）
- **请求参数**：路径 `{date}`（YYYY-MM-DD）
- **响应**：`{"ok":true}`；404 holiday not found
- **代码出处**：api.py holidays/{date} DELETE 分支(:2334-2343) → power_control.py `holiday_delete`
- **状态**：在用
- **登记记录**：2026-09-19，代码实证（同 SRV-120）

#### SRV-133 节假日例外标记统计 `GET /api/v1/console/powercontrol/holiday-status`
- **用途**：当年例外标记统计（可选层：无标记时纯星期语义，用户定案；供 UI 与运行留痕）> 更新 2026-09-19：语义由「日历覆盖状态（缺失年提示）」调整为「例外标记统计」——holidays 日历降级为纯星期基座上的可选例外层（ADR-047 增补，commit 513ffa8）
- **鉴权**：X-ETP-Console-Token（读 operator）
- **请求参数**：无
- **响应**：`{"ok":true,"status":{"year":2026,"holiday_count":N,"workday_count":N}}` > 更新 2026-09-19：响应删除 `covered` 字段（同上；power_control.py `holiday_status`(:1009-1019) 代码实证）
- **代码出处**：api.py holiday-status 分支(:2345-2348) → power_control.py `holiday_status`(:1009-1019)
- **状态**：在用
- **登记记录**：2026-09-19，代码实证（同 SRV-120）> 更新 2026-09-19：covered 字段删除 + 语义调整（ADR-047 增补工作日/节假日纯星期语义，server-platform-dev 对账回复交付，api-registrar-dev 代码实证核对一致——power_control.py holiday_status docstring「当年例外标记统计（可选层：无标记时纯星期语义，用户定案）」）

#### SRV-134 终端拉取命中开机任务 `GET /api/v1/terminals/{tid}/powercontrol/boot-tasks`
- **用途**：终端侧拉取命中本终端的启用中开机任务（组展开与执行引擎同源），按下次触发升序
- **鉴权**：X-ETP-Token（白名单准入 `_admission`）
- **请求参数**：路径 `{tid}`；body 无
- **响应**：`{"ok":true,"terminal_id":"...","tasks":[{id,name,repeat,weekdays,once_date,time_hhmm,source,origin,method,next_ts,next_trigger}],"generated_ts":N}`；404 terminal not registered > 更新 2026-09-19：响应删除 `calendar_fallback` 字段（ADR-047 增补工作日/节假日纯星期语义，commit 513ffa8；代码实证——server-platform 全目录 grep 零匹配）
- **代码出处**：api.py `_terminal_api` powercontrol 子路由 boot-tasks GET 分支(:589-605) → power_control.py `boot_tasks_for_terminal`
- **状态**：在用
- **登记记录**：2026-09-19，代码实证（同 SRV-120）

#### SRV-135 终端个性化开机任务创建 `POST /api/v1/terminals/{tid}/powercontrol/boot-tasks`
- **用途**：终端个性化开机任务创建（origin=client_personal，目标锁定本终端）
- **鉴权**：X-ETP-Token
- **请求参数**：载荷同 SRV-121 规范（kind/origin/source/target_type/targets 由服务端**强制覆写**为 boot/client_personal/platform/terminals/[本 tid]；name 缺省自动生成；operator="client:{tid}"）

```json
{"repeat": "daily", "time": "07:50"}
```
- **响应**：`{"ok":true,"task_id":N,"task":{...},"expand":{...}}`；载荷非法 400；**每终端上限 5 条 fail-closed（409「每终端个性化开机任务上限 5 条」）**；404 terminal not registered
- **代码出处**：api.py boot-tasks POST 分支(:606-636) → power_control.py `validate_task_payload`/`task_count_personal`/`task_create`/`wol_expand_for_task`（`PERSONAL_TASK_LIMIT=5`）
- **状态**：在用
- **登记记录**：2026-09-19，代码实证（同 SRV-120）

#### SRV-136 终端个性化任务维护 `PUT|DELETE /api/v1/terminals/{tid}/powercontrol/boot-tasks/{task_id}`
- **用途**：终端编辑/删除本终端个性化任务（**归属锁定**）
- **鉴权**：X-ETP-Token
- **请求参数**：路径 `{tid}`、`{task_id}`（数字）；PUT 载荷同 SRV-121 增量合并
- **归属锁定**：仅可操作 **origin=client_personal 且 targets 含本终端** 的任务——否则 403「仅可维护本终端的个性化任务」；task 不存在 404
- **响应**：PUT `{"ok":true,"task":{...},"expand":{...}}`（kind/origin/source/target_type/group_id/targets 锁定不可漂移，重展开保持 enabled）；DELETE `{"ok":true}`
- **代码出处**：api.py boot-tasks/{task_id} 分支(:639-683) → power_control.py `task_get`/`task_update`/`task_set_targets`/`task_delete`
- **状态**：在用
- **登记记录**：2026-09-19，代码实证（同 SRV-120）

#### SRV-137 终端关机配置上报 `POST /api/v1/terminals/{tid}/powercontrol/shutdown-config`
- **用途**：终端本地关机配置上报（架构修正：定时关机终端本地执行；连接时+变更时各上报一次，中心按版本去重存档）
- **鉴权**：X-ETP-Token
- **请求参数**：`{"config":{...pc_apply_policy shutdown 形状...},"version":"...","ts"?}`（config 非对象 400）
- **响应**：`{"ok":true,"terminal_id":"...","updated":bool,"duplicate":bool}`——同版本重复上报幂等（仅刷新 reported_ts，updated=false/duplicate=true）；新版本覆盖
- **代码出处**：api.py shutdown-config 分支(:572-588) → power_control.py `shutdown_config_save`(:1026-1051，pc_shutdown_config 表 terminal_id 主键 UPSERT)
- **状态**：在用
- **登记记录**：2026-09-19，代码实证（同 SRV-120）

---

### 1.18 中心首页（home，api_home.py，2026-09-19 起）

> 来源：home 组为 home-console-dev 辖区（api_home.py 自治演进）；本条 asset-locate 为 asset-mgmt-dev 实施（commit 19694e1），经 main 转达交付说明、api-registrar-dev 审核登记。
> 组级约定：api.py `_console_api` 头部单点转发(:904-906，`parts[:4]==["api","v1","console","home"]` → api_home.handle(:46-49))，本组新增端点不改 api.py。鉴权：组级沿用 dispatch 层 console resolve_session（operator/admin 可读，组内 GET 不重复鉴权、不审计）；**端点级加固由各端点自理**（asset-locate 自行 admin-only + 审计，与 sysadmin 组同口径）。
> 规划锚 → **更正（2026-09-19）**：`GET /api/v1/console/home/summary` 已实现在位（此前登记官误报「未实现」——检索只扫了 handle 分发分支、未读 `_summary` 函数体；经 main 实证更正），实登记为 **SRV-139**。卡片数据契约 v1：cards 键=前端卡片注册 id（monitor/assets/release 已实现；nettest/ai/dpol/pc/kb/config/sysadmin 为 `{"ok":true,"pending":true}` 骨架占位，Phase2 各模块 agent 按键追加自有字段，向后兼容）。每卡片独立容错（单卡失败 `{"ok":false,"error":...}` 不阻塞整页，PRD 硬要求）。

#### SRV-138 资产定位检索聚合 `POST /api/v1/console/home/asset-locate`
- **用途**：首页「资产定位」卡数据供给——自由文本提取标识符 → 中心/火绒/画方三源检索聚合 →（可选 AI 推断块）→ 结构化资产画像
- **鉴权**：X-ETP-Console-Token（**admin-only**：sess 非管理员/缺失 403「需要管理员权限」+ ACCESS_DENIED 审计 `auth.Ev.ACCESS_DENIED` reason=admin_required，与 sysadmin 组同口径）
- **请求参数**：body `{"text": "<自由文本>"}`——缺失/空白 400 "text required"；**>2000 字符 400 "text too long (max 2000)"**（`MAX_TEXT=2000`）；非 POST 方法 404 "not found"
- **管线四阶段**（server/asset_locate.py；各阶段独立 try/except，单阶段失败不拖垮整响应）：
  1. **extract**：IPv4（含 /掩码、:端口 尾巴容错，非法段入 ambiguous）；MAC 四形态（冒号/连字符/裸 12 位/点分 4 段，归一 12 位小写；12 位纯数字不认 MAC 防吞工号）；计算机名（连字符或字母数字混合 token，2-31 位）；工号（**5-11 位独立纯数字 token；4 位仅认「工号/编号/职工号」前缀形态**）；无标识符时 2-64 位无空白整串兜底为 hostname 候选；不可判候选入 `ambiguous`（透明展示，不参与检索）
  2. **search**：三源隔离检索（顺序 try/except，单源异常 status 透出不阻断）——platform=中心 terminals（ip/hostname/资产明细 mac，附在线/分组）；huorong=hr_clients 镜像（store.hr_locate_clients，含 hr_client_assets 登记信息与 hr_terminal_map 关联；条目含 first_seen/last_off/this_on 三时间戳，与 huorong-dev 批次 8f9d5ad 字段对齐）；nad=画方准入（nad_client 现行匹配模式 ip/mac + 登记名兜底；**status=not_configured 表示数据源未接入，如实标注**）
  3. **aggregate**：MAC 主键聚类合并（无 MAC 回落 IP/hostname；每簇每源最多一员，同键多候选歧义各自成簇）；命中评分 **mac→high / ip|link→medium / 其余→low**，排序 score→keys 数→last_seen
  4. **inference**：编排 ai_analysis 内部函数 `infer_asset_locate(ctx, raw_text, identifiers, payload)`（payload={asset_profile, matches[:3], sources 各源 status}；**AI 落库 trigger=asset_locate 入 ai_analyses，归 ai-analysis-dev 辖区**）；未提取标识符/未命中跳过（skipped_no_identifiers/skipped_not_found）；模块未落盘 not_ready；任何失败降级 available=false **不阻塞确定数据**
- **响应**：200 + `{"ok":true,"ts":N,"query":{"text_length":N,"elapsed_ms":N},"not_found":bool,"extract":{identifiers:[{type,value,raw}],counts:{ip,mac,hostname,empid},ambiguous:[{suspect_type,value,reason}],empty:bool},"search":{"sources":{platform|huorong|nad:{status,hits}}},"matches":[{score,keys,identity:{mac,hostname,current_ip},platform,huorong,nad}],"asset_profile":{computer_name,source_platform,identity,registration:<白名单投影 楼层/具体位置/工号/姓名/使用科室>,admission,timeline:{platform_last_seen,huorong_last_seen,huorong_this_on,nad_onlts,latest},cross_check:{ip_consistent:bool|null,notes}},"inference":{available,status:ok|skipped_no_identifiers|skipped_not_found|not_ready|unavailable,model,blocks,disclaimer:"AI 推测内容，非登记数据，仅供定位参考",error}}`；**无任何命中如实 not_found=true + asset_profile=null（不虚构，HTTP 仍 200）**；当前在用 IP 口径 nad>火绒>平台；IP 交叉校验 ≥2 源不一致→false+notes、一致→true、单源→null
- **调用方式**：`curl -X POST http://127.0.0.1:18090/api/v1/console/home/asset-locate -H "X-ETP-Console-Token: <token>" -H "Content-Type: application/json" -d '{"text":"3楼财务室 172.17.90.215 主机名 WIN-FIN-03"}'`
- **代码出处**：server/asset_locate.py `handle`(:663-744)（分发链 api.py `_console_api` :904-906 → api_home.py `handle` :46-49）；提取 `_extract`(:81-185)/`_search_platform`(:212-255)/`_search_huorong`(:258-298)/`_search_nad`(:337-381)/`_aggregate`(:426-499)/`_build_profile`(:530-604)/`_run_inference`(:622-650)
- **状态**：在用
- **登记记录**：2026-09-19，代码实证（asset-mgmt-dev 实施，commit 19694e1，经 main 转达交付说明；api-registrar-dev 审核登记——条目按代码为准，审核差异 3 点见下）
- **审核注记（2026-09-19，登记官）**：①交付说明「404/405 语义」中 405 无对应分支——代码 method≠POST 实际返回 404，以代码为准；②交付说明「工号 4-11 位」实为 5-11 位独立 token + 4 位仅前缀形态，以代码为准；③asset_locate.py 模块头注释仍写「api.py 单点转发、不进 api_home.py」，与实际链路（经 api_home.py 分发）不符，属注释过时不影响功能——已提醒 asset-mgmt-dev 顺手修正

#### SRV-139 首页卡片摘要聚合 `GET /api/v1/console/home/summary`
- **用途**：中心首页卡片式主界面数据聚合（一次请求喂全部卡片；卡片键=前端卡片注册 id，Phase2 各模块按键追加自有字段，向后兼容）
- **鉴权**：X-ETP-Console-Token（dispatch 层 console 统一 resolve_session，operator/admin 可读；组级 GET 不重复鉴权、不审计）
- **请求参数**：无
- **响应**：200 `{"ok":true,"ts":N,"cards":{...}}`，10 张卡：
  - `monitor`（实现）：`{"ok":true,"total":N,"online":N,"offline":N,"version_counts":{<client_version>:N}}`——在线判定 last_seen 距今 < heartbeat_timeout_sec（缺省 180）
  - `assets`（实现）：`{"ok":true,"platform_groups":N,"huorong_groups":N,"other_total":N,"other_online":N}`——平台资产组 / 火绒镜像组（hr_groups_with_stats）/ 未关联平台终端统计（hr_platform_unlinked，含在线计数）
  - `release`（实现）：`{"ok":true,"current_version":..,"published_at":..,"release_note":..,"total_releases":N}`——**operator 可见脱敏投影**（仅版本号/时间/数量，不含 token/sha256/安装包文件名，admin-only 端点数据的投影）；发布模块未初始化降级 `{"ok":true,"current_version":null,"total_releases":0}`
  - `nettest`/`ai`/`dpol`/`pc`/`kb`/`config`/`sysadmin`（7 卡）：`{"ok":true,"pending":true}` 骨架占位（Phase2 各模块 agent 接入替换）
  - 已实现卡异常时 `{"ok":false,"error":"<str[:120]>"}` 单卡失败不阻塞整页
- **调用方式**：`curl http://127.0.0.1:18090/api/v1/console/home/summary -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api_home.py `_summary`(:53-78)/`_card_monitor`(:92-105)/`_card_assets`(:108-121)/`_card_release`(:124-140)；分发 api.py `_console_api`(:904-906) → api_home.py `handle`(:41-42)
- **状态**：在用（3 卡实读数据 + 7 卡 pending 占位）
- **登记记录**：2026-09-19，代码实证（home-console-dev 实施，api-registrar-dev 复核登记）> 更新 2026-09-19：本条此前被登记官**误判「规划未实现」**（检索只扫 handle 分发分支、未读 _summary 函数体即下结论），经 main 实证更正后按实补登——登记方法论入档：登记前必须读函数体实现，不得以 docstring/分支存在性推断实现状态

---

### 1.14 客户端版本发布管理（client releases，ADR-042，2026-09-17 新增）

> 组级约定：console 组（SRV-082~085）整组 admin-only 403+审计（`_require_admin`，定制名含终端 token，分发属管理员操作）；存储 `client_releases` 表（client_release.py `ClientReleaseStore`，server-platform e24a236 新模块 +336 行）。上传 body 上限 **256MB**（`_MAX_UPLOAD_BYTES`，仅作用于 release 上传端点）。
> **scope 变更（ADR-032 双端口白名单）**：terminal 口（18443）放行追加 `/api/v1/client/manifest`、`/api/v1/client/update-manifest`（327a69b 追加，白名单 in 元组 api.py:216-222）与 `/download/*`（终端自动更新拉包用）；console 口经静态 GET 规则天然可用 `/download/*`；legacy 口全量。dispatch docstring 注释曾未同步（原写仅 terminals/ai/analyze）——**已消项 2026-09-17**：9ce055f 同步注释并在 docstring 头部标注「白名单以 _scope_allowed 为准」（api.py:232，代码实证），台账原「以 _scope_allowed 实现为准」标注保留作锚点。
> 定制文件名协议：`EyeTerm_Setup_x64_{ver}_{cfg64}_{md58}.exe`——cfg64=base64url(zlib(json{"s","t"})) ≤160 截断（`_CFG64_MAX`）；md58=json 原文 MD5 前 8 位（安装器解析后校验完整性）；服务端不存定制包实体（票据制即时分发）。

#### SRV-082 上传安装包 `PUT /api/v1/console/client/releases/{version}?filename=&note=`
- **用途**：上传客户端安装包（body=raw bytes ≤256MB；sha256/size 落库；**同版本覆盖更新**）
- **鉴权**：X-ETP-Console-Token（admin-only，403+审计）
- **请求参数**：路径 `{version}`（URL 编码）；query `filename`（展示文件名）、`note`（发布说明，均可选）；body=安装包原始字节
- **响应**：`{"ok":true,"release":{version,filename,sha256,size,note,...}}`；超限/非法 4xx（ClientReleaseError 映射）；审计 `cr_release_uploaded`
- **调用方式**：`curl -X PUT "http://<server>/api/v1/console/client/releases/5.0.1?filename=EyeTerm_Setup_x64.exe&note=首版" -H "X-ETP-Console-Token: <token>" --data-binary @EyeTerm_Setup_x64.exe`
- **代码出处**：api.py `_console_client`(1454) PUT 分支(:1477-1490) → client_release.py `upload`(:168+, `_MAX_UPLOAD_BYTES`:31)
- **状态**：在用
- **登记记录**：2026-09-17，代码实证（server-platform-dev 实施，e24a236/ADR-042；单测 37/37+冒烟 30/30）

#### SRV-083 版本列表 `GET /api/v1/console/client/releases`
- **用途**：已上传版本清单（含 is_current / rollback_flag 回滚标记）
- **鉴权**：X-ETP-Console-Token（admin-only，403+审计）
- **请求参数**：无
- **响应**：`{"ok":true,"releases":[{id,version,filename,sha256,size,note,is_current,rollback_flag,created_at,...}]}`
- **调用方式**：`curl http://<server>/api/v1/console/client/releases -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_client` GET 分支(:1493-1495) → client_release.py `list`
- **状态**：在用
- **登记记录**：2026-09-17，代码实证（同 SRV-082）

#### SRV-084 设置当前版本 `POST /api/v1/console/client/releases/{id}/set-current`
- **用途**：指定发布为当前版本（**回滚=指回旧版**，置 rollback_flag=1；manifest/下载随 current 切换）
- **鉴权**：X-ETP-Console-Token（admin-only，403+审计）
- **请求参数**：路径 `{id}`（整数，非数字 400 bad release id）
- **响应**：`{"ok":true,"release":{...rollback_flag...}}`；id 不存在 404；审计 `cr_release_published`（记 version+rollback）
- **调用方式**：`curl -X POST http://<server>/api/v1/console/client/releases/3/set-current -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_client` POST 分支(:1498-1514，`rest[2]=="set-current"`) → client_release.py `set_current`
- **状态**：在用
- **登记记录**：2026-09-17，代码实证（同 SRV-082）

#### SRV-085 定制安装包文件名生成 `POST /api/v1/console/client/custom-name`
- **用途**：按平台地址+token 生成定制文件名（协议见组级约定）+ 一次性下载票据 URL（10 分钟）；审计**不记 token**
- **鉴权**：X-ETP-Console-Token（admin-only，403+审计）
- **请求参数**：`{"server":"https://<server>","token":"<可空=当前 config terminal_token>"}`（server 须 http(s):// 前缀否则 400；token 双空 400「终端 Token 未配置」；无 current 版本 400）
- **响应**：`{"ok":true,"version":"...","filename":"EyeTerm_Setup_x64_{ver}_{cfg64}_{md58}.exe","download_url":"/download/client/setup?ticket=<一次性票据>","expires_in":600}`
- **调用方式**：`curl -X POST http://<server>/api/v1/console/client/custom-name -H "X-ETP-Console-Token: <token>" -H "Content-Type: application/json" -d '{"server":"https://<server>"}'`（token 留空=用服务端已配置终端 token）
- **代码出处**：api.py `_console_client` custom-name 分支(:1520-1545) → client_release.py `build_custom_filename`(:76-85, `md58` :80) / `issue_ticket`(:294, `_TICKET_TTL_SEC`:33=600)
- **状态**：在用
- **登记记录**：2026-09-17，代码实证（同 SRV-082）

#### SRV-086 客户端更新清单 `GET /api/v1/client/manifest`
- **用途**：终端拉取当前发布版本的更新清单（自动更新数据源；**terminal 口白名单放行**，scope 变更见组级约定）
- **鉴权**：X-ETP-Token（多 token 模型同 UPL-010；401 审计 auth_reject）
- **请求参数**：无
- **响应**：`{"ok":true,"manifest":{latest_version,download_url,sha256,size,release_note}}`；**无 current 发布时 manifest=null**
- **调用方式**：`curl http://<server>/api/v1/client/manifest -H "X-ETP-Token: <token>"`
- **代码出处**：api.py `_terminal_api` manifest 分支(:295-304) + `_scope_allowed` 白名单(:219) → client_release.py `manifest`(:287+)
- **状态**：在用（终端消费侧待 uplink/主应用版本跟进——心跳已搭载 latest_version，见 SRV-046/UPL-002 更新行）
- **登记记录**：2026-09-17，代码实证（同 SRV-082）> 更新 2026-09-17（晚）：新增顶层扁平别名端点 SRV-088（同数据源同鉴权）——**消费侧区分：通用清单认本条嵌套形状 {ok,manifest:{...}}；power-control updater.check_async 认 SRV-088 扁平形状**

#### SRV-087 安装包下载 `GET /download/client/setup[?ticket=]`
- **用途**：安装包分发唯一出口——无 ticket=通用包公开下载（当前 current 版本，Content-Disposition=原始文件名）；带 ticket=定制包（**一次性** 10 分钟票据，Content-Disposition=定制文件名，安装器从文件名解析平台地址与 token）；服务端不存定制包实体
- **鉴权**：无（公开；安全性靠 current 版本管控 + 一次性短时票据）
- **请求参数**：query `ticket`（可选；一次性，消费即失效）
- **响应**：安装包二进制（Content-Disposition 按通用/定制文件名）；票据无效或已使用 **404**（client_release.py:319）、已过期 **410**（:321「请重新生成」）、版本记录/文件缺失 404
- **调用方式**：`curl -OJ http://<server>/download/client/setup`（通用包）；`curl -OJ "http://<server>/download/client/setup?ticket=<票据>"`（定制包，票据来自 SRV-085）
- **代码出处**：api.py `_client_download`(:347+, 分发 :337-338) → client_release.py `consume_ticket`(:319-321) / 按记录读包(:269-275)；白名单 `_scope_allowed`:220
- **状态**：在用
- **登记记录**：2026-09-17，代码实证（同 SRV-082；e2e 139/139 pageerror=0）

#### SRV-088 更新清单别名（扁平形状）`GET /api/v1/client/update-manifest`
- **用途**：SRV-086 manifest 的**对齐别名端点**（TBC-002 消项：power-control `updater.check_async` 消费，响应形状对齐终端 updater 期望）——同数据源 `cr.manifest()`、同鉴权，仅响应形状不同
- **鉴权**：X-ETP-Token（多 token 模型同 UPL-010；401 审计 auth_reject）；terminal 口白名单已含（api.py:216-222）
- **请求参数**：query `version`（**接收但忽略**——当前版本比对由终端侧 semver 完成）；无其他参数
- **响应（顶层扁平，键恒在）**：`{"ok":true,"latest_version":"...","download_url":"...","sha256":"...","size":N,"release_note":"..."}`——无 current 发布时各值为 **null**（消费侧判 latest_version）
- **与 SRV-086 形状区分**：本条顶层扁平（键恒在）；SRV-086 嵌套 `{ok,manifest:{...}}`（无 current 时 manifest=null）——两条端点同数据源不同形状，混用形状会导致消费侧解析失败
- **调用方式**：`curl "http://<server>/api/v1/client/update-manifest?version=4.0.0" -H "X-ETP-Token: <token>"`
- **代码出处**：api.py 双路径分支(:303-323，扁平构造 :313-322) + `_scope_allowed` 白名单(:216-222)；消费方 power-control updater.check_async（TBC-002 对齐）
- **状态**：在用（**已部署生产**：327a69b 随 2026-09-17 晚部署批次上线，probe 401 路由生效——power-control-dev 代跑 deploy 确认；下游生产 current=4.1.0/release id=1，同版本语义不触发更新，发布 4.1.1 起客户端全流程〔心跳 latest_version → 提示条 → apply〕生效）
- **登记记录**：2026-09-17，代码实证（server-platform-dev 实施，commit 327a69b/ADR-042 follow-up；冒烟 33/33 含 +3 扁平两态/401，e2e 139/139 零回归，api-registrar-dev 复核登记）> 更新 2026-09-17（晚）：补部署状态注记（power-control-dev 显式知会，api-registrar-dev 对账确认）

### 1.15 第三方数据源（thirdparty，ADR-043，2026-09-17 新增）

> 组级约定：console 组（X-ETP-Console-Token）；GET 类 operator 可用、isolate 为 admin-only。聚合弹窗一次请求多区块，**各区块独立降级**（单区块异常不阻断响应，降级块带 available:false + reason）。
> 数据源依赖：huorong 块 = store.hr_context_block（火绒镜像，模块所有权 ADR-033 增补二，commit 7842aab）；nad 块 = 画方准入 API（nad_client，ADR-024，IP 优先实时查询失败回退 MAC，**60s 缓存**，fetched_at=nad_cache_ts）；online-log/isolate = **骨架降级**（画方联动 API 规格待 main 索取后接入，届时端点语义升级非新增）。
> 高危操作基线：isolate 密码重校验复用 auth.authenticate 全链（锁定 423/限速 429 自动生效），成功产生的临时会话立即吊销（revoke_session reason=isolate_reauth），不留冗余会话。
> 行号基线注记（2026-09-17，3645d94 增补后）：本组条目代码出处行号以登记基线 b8a086b 为准；3645d94（+63 行）后实测锚点——`_terminal_net_from_asset` :1594 / `_TP_TIMELINE_DAYS=7` :1593 / `_thirdparty_nad_block` :1615 / `_console_thirdparty` :1646 / isolated 分支 :1649 / 聚合分支 :1660 / online-log 分支 :1686 / isolate 分支 :1709；后续漂移随对账批次更新。

#### SRV-089 第三方数据源聚合 `GET /api/v1/console/thirdparty/{tid}`
- **用途**：单终端第三方数据源信息弹窗聚合（一次请求：terminal 摘要 + huorong 块 + nad 实时证据 + online_log/isolate 可用性标记）
- **鉴权**：X-ETP-Console-Token（GET 类 operator 可用）
- **请求参数**：路径 `{tid}`
- **响应**：`{"ok":true,"terminal":{terminal_id,hostname,ip},"huorong":{...hr_context_block 或降级 {linked:false,error:"火绒镜像读取失败"}},"nad":{available,reason,matched,evidence[],fetched_at,query:{ip,mac}},"online_log":{available:false,reason},"isolate":{available:false,reason}}`；终端不存在 404
- **代码出处**：api.py `_console_thirdparty`(1627) GET 聚合分支(:1642-1665) / `_thirdparty_nad_block`(1602-1624，`nad_find_by_ip/mac(full=True)` :1609-1611)
- **状态**：在用（nad/huorong 真实数据源；online_log/isolate 骨架降级）
- **登记记录**：2026-09-17，代码实证（server-platform-dev 实施，commit b8a086b/ADR-043；api-registrar-dev 复核登记）> 更新 2026-09-17（晚）：**画方查询键口径修正**（commit 3645d94，ADR-043 follow-up）——terminals.ip 列实为 HTTP 连接源 IP（NAT 场景非终端本机地址），查询键改为 **asset.network[] 终端自报 IP 优先**（新 helper `_terminal_net_from_asset` api.py:1594）→ MAC 回退 → 连接源兜底（source_ip ≠ asset_ip 才查，:1626）；nad.query 扩为 `{ip,mac,asset_ip,source_ip}` 四键（:1629-1630，asset_ip/source_ip 透出供前端区分展示）；**口径定语：画方查询键 = 终端自报 IP，非连接源**

#### SRV-090 IP 在线日志查询 `GET /api/v1/console/thirdparty/{tid}/online-log?ip=&mac=&start=&end=`
- **用途**：IP 在线日志时间线查询（**骨架降级**：画方「IP 在线日志」接口规格待接入，参数校验已真实生效）
- **鉴权**：X-ETP-Console-Token（GET 类 operator 可用）
- **请求参数**：路径 `{tid}`；query `ip`/`mac`（**至少一项否则 400「IP 与 MAC 至少提供一项」**）、`start`/`end`（时间范围，透传）
- **响应**：`{"ok":true,"available":false,"reason":"画方「IP 在线日志」接口规格待接入（ADR-043 骨架）","query":{ip,mac,start,end}}`（原样回显查询参数）；终端不存在 404 > 更新 2026-09-17（晚，commit 3645d94）：响应新增并列字段 **`platform_timeline`**（`{available,days,total,points:[{ts,gap,cpu,mem}]}`）——**过渡数据源**：平台通信时间线（store.metrics_timeline store.py:404，metrics 上报间隔采样，默认 7 天/240 点上限 `_TP_TIMELINE_DAYS=7` api.py:1593），语义边界已在响应 reason 与前端标注；画方真源接入后 online_log 切换而 platform_timeline 保留（不改骨架 available 语义）
- **代码出处**：api.py `_console_thirdparty` online-log 分支(基线 b8a086b :1668-1680；3645d94 后 :1686+)
- **状态**：在用（骨架降级；接入后语义升级）
- **登记记录**：2026-09-17，代码实证（同 SRV-089）> 更新 2026-09-17（晚）：过渡数据源 platform_timeline 并列透出（3645d94，构造 api.py:1699-1707）

#### SRV-091 隔离终端列表 `GET /api/v1/console/thirdparty/isolated`
- **用途**：当前被隔离（阻断）终端列表（**骨架降级空列表**：画方阻断状态源待接入）
- **鉴权**：X-ETP-Console-Token（GET 类 operator 可用）
- **请求参数**：无
- **响应**：`{"ok":true,"available":false,"terminals":[],"reason":"画方阻断状态源待接入（终端隔离 ADR-043 骨架）"}`
- **代码出处**：api.py `_console_thirdparty` isolated 分支(:1631-1635)
- **状态**：在用（骨架降级；接入后语义升级）
- **登记记录**：2026-09-17，代码实证（同 SRV-089）

#### SRV-092 终端隔离阻断/解除 `POST /api/v1/console/thirdparty/{tid}/isolate`
- **用途**：终端隔离阻断（block）/解除（unblock）——**高危操作**：admin-only + 密码重校验 + 意图审计；当前画方 API 待接入（校验与审计真实生效，操作未执行 → 501）
- **鉴权**：X-ETP-Console-Token（**admin-only**，非 admin 403 + ACCESS_DENIED 审计）
- **请求参数**：路径 `{tid}`（不存在 404）；body `{"action":"block|unblock","password":"<管理员口令>"}`（action 非法 400；password 空 400「需要管理员密码重校验」）
- **响应**：校验通过但画方未接入 → **501**「画方阻断/解除接口待接入，操作未执行」；密码重校验失败 → **403**（authenticate 401/400 映射，非 423/429 一律 403）/ **423** 账号锁定 / **429** 限速（复用 authenticate 全链语义映射）
- **审计**：`tp_isolate_auth_fail`（重校验失败，记 action/terminal/operator）/ `tp_isolate_attempt`（校验通过意图，audit_log 表可直查）；重校验临时会话即用即吊销
- **代码出处**：api.py `_console_thirdparty` isolate 分支(:1683-1725，语义映射注释 :1701-1703)
- **状态**：在用（骨架：校验/审计/权限链真实生效，执行面待画方 API 接入后升级）
- **登记记录**：2026-09-17，代码实证（同 SRV-089；tools/smoke_thirdparty.py +303 行 / test_thirdparty.py +106 行）

---

## 二、终端本地桥接 API（来源：winhelper 主应用 bridge.py）

> 传输通道：pywebview js_api（`window.pywebview.api.call(path)`），全程无 HTTP、无端口；前端传 `/api/xxx?query` 形式路径，`ApiBridge.call` 解析后按 ROUTES 分发到 handler(params)。**鉴权：无（本机进程内调用）**。响应统一含 `success`（或 `ok`）字段，异常 `{"success":false,"error":"..."}`。
> 代码出处统一为：bridge.py `ROUTES` + 对应 service 文件函数。

### 2.1 日志诊断（log_service.py）

#### BRG-001 可访问性探测 `GET /api/loginspector/access`
- **用途**：检测当前进程能否访问事件日志（Security 非管理员降级提示）
- **请求参数**：无
- **响应**：`{"success":true,"accessible":{...}}`（各类别可访问性，以 log_service.py `handle_log_access` 为准）
- **代码出处**：bridge.py `ROUTES` → log_service.py `handle_log_access`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-002 日志检索 `GET /api/loginspector/search`
- **用途**：Windows 事件日志条件检索（服务端过滤，分页）
- **请求参数**：query `types`（类别，默认 System，多选）、`start`/`end`（时间范围）、`hours`（默认 72）、`levels`、`source`、`keyword`、`event_id`、`page`（默认 1）、`per_page`（5~200，默认 50）
- **响应**：`{"success":true,"events":[...],"total":N,"page":1,...}`（统计卡+分页，以 handler 返回为准）
- **代码出处**：log_service.py `handle_log_search`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-003 导出启动 `GET /api/loginspector/export-start`
- **用途**：启动后台 txt 导出任务（默认近 3 天全量系统日志，流式逐批写、utf-8-sig）
- **请求参数**：query `types`/`start`/`end`/`hours`/`levels` 等同检索条件
- **响应**：`{"success":true,"task_id":"..."}`
- **代码出处**：log_service.py `handle_log_export_start`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-004 导出状态轮询 `GET /api/loginspector/export-status`
- **用途**：导出进度查询
- **请求参数**：query `task_id`（必填）
- **响应**：`{"success":true,"status":"running|done|cancelled|failed","file":"...","count":N,...}`
- **代码出处**：log_service.py `handle_log_export_status`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-005 导出取消 `GET /api/loginspector/export-cancel`
- **用途**：取消导出任务
- **请求参数**：query `task_id`（必填）
- **响应**：`{"success":true,"cancelled":true}`（以 handler 为准）
- **代码出处**：log_service.py `handle_log_export_cancel`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-006 智能分析 `GET /api/loginspector/analyze`
- **用途**：FAULt_PATTERNS 模式分析（结论徽章+模式卡+证据+建议+已知问题事件）
- **请求参数**：query 条件同检索（分析扫描上限 30000 条）
- **响应**：`{"success":true,"result":{summary,patterns:[{...evidence,suggestions}],known_issues:[...],suggestions:[...]}}`
- **代码出处**：log_service.py `handle_log_analyze`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-007 分析报告导出 `GET /api/loginspector/report-export`
- **用途**：导出单文件自包含深色 HTML 分析报告（LogAnalysis_*.html）
- **请求参数**：无（复用最近一次分析结果，缺失则现算）
- **响应**：`{"success":true,"file":"<路径>"}`
- **代码出处**：log_service.py `handle_log_report_export`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-008 知识库建议 `GET /api/loginspector/knowledge`
- **用途**：内置故障模式知识检索（自动关联 + 自由检索）
- **请求参数**：query `q`（关键字，可空）
- **响应**：`{"success":true,"items":[...]}`
- **代码出处**：log_service.py `handle_log_knowledge`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

### 2.2 磁盘清理（disk_cleanup.py）

#### BRG-009 磁盘总览 `GET /api/disk/overview`
- **用途**：各盘容量/使用率总览
- **请求参数**：无
- **响应**：`{"success":true,"drives":[...]}`
- **代码出处**：disk_cleanup.py `handle_disk_overview`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-010 垃圾扫描启动 `GET /api/disk/scan`
- **用途**：启动垃圾/大文件扫描任务（后台线程 + scan_id 轮询）
- **请求参数**：query `type`（junk|bigfile，默认 junk）、`root`（默认系统盘）、`min_mb`（大文件阈值，默认 200）
- **响应**：`{"success":true,"scan_id":"..."}`
- **代码出处**：disk_cleanup.py `handle_disk_scan`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-011 扫描状态 `GET /api/disk/scan-status`
- **用途**：扫描进度/结果轮询
- **请求参数**：query `scan_id`
- **响应**：`{"success":true,"status":"running|done|cancelled","items":[...],...}`
- **代码出处**：disk_cleanup.py `handle_disk_scan_status`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-012 扫描取消 `GET /api/disk/scan-cancel`
- **用途**：取消扫描任务
- **请求参数**：query `scan_id`
- **响应**：`{"success":true,"cancelled":true}`（以 handler 为准）
- **代码出处**：disk_cleanup.py `handle_disk_scan_cancel`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-013 执行清理 `GET /api/disk/cleanup`
- **用途**：按类别清理（白名单前置校验；微信/QQ 的 db/log 不碰）
- **请求参数**：query `categories`（逗号分隔类别名）
- **响应**：`{"success":true,"freed_bytes":N,"detail":{...}}`（以 handler 为准）
- **代码出处**：disk_cleanup.py `handle_disk_cleanup`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-014 打开所在位置 `GET /api/disk/open-location`
- **用途**：资源管理器定位文件（有意开窗，豁免 CREATE_NO_WINDOW 红线）
- **请求参数**：query `path`（必填）
- **响应**：`{"success":true}`；路径为空/不存在时 error
- **代码出处**：disk_cleanup.py `handle_disk_open_location`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-015 大文件树浏览 `GET /api/disk/tree`
- **用途**：浏览已完成扫描的任务目录树
- **请求参数**：query `scan_id`、`path`（默认根）、`limit`（10~5000，默认 2000）
- **响应**：`{"success":true,"nodes":[...]}`
- **代码出处**：disk_cleanup.py `handle_disk_tree`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-016 盘符列表 `GET /api/disk/drives`
- **用途**：可用盘符枚举（扫描根选择）
- **请求参数**：无
- **响应**：`{"success":true,"drives":["C:\\","D:\\",...]}`
- **代码出处**：disk_cleanup.py `handle_disk_drives`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

### 2.3 应用数据盘点与迁移（appdata_scan.py）

#### BRG-017 应用数据扫描 `GET /api/appdata/scan`
- **用途**：常见应用数据目录占用盘点
- **请求参数**：无
- **响应**：`{"success":true,"apps":[...]}`
- **代码出处**：appdata_scan.py `handle_appdata_scan`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-018 盘符列表（应用数据）`GET /api/appdata/drives`
- **用途**：迁移目标盘枚举
- **请求参数**：无
- **响应**：`{"success":true,"drives":[...]}`
- **代码出处**：appdata_scan.py `handle_appdata_drives`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-019 应用数据迁移 `GET /api/appdata/migrate`
- **用途**：迁移单个应用数据目录到目标盘
- **请求参数**：query `path`（源目录）、`target`（目标根）
- **响应**：`{"success":true,...}`（以 handler 为准）
- **代码出处**：appdata_scan.py `handle_appdata_migrate`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-020 应用数据删除 `GET /api/appdata/delete`
- **用途**：删除指定应用数据路径（逗号分隔多个）
- **请求参数**：query `paths`
- **响应**：`{"success":true,...}`（以 handler 为准）
- **代码出处**：appdata_scan.py `handle_appdata_delete`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-021 安装包扫描 `GET /api/installers/scan`
- **用途**：扫描残留安装包（scope：both|下载目录|自定义）
- **请求参数**：query `scope`（默认 both）、`custom`（自定义目录）
- **响应**：`{"success":true,"installers":[...]}`
- **代码出处**：appdata_scan.py `handle_installer_scan`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

### 2.4 性能分析（perf_service.py）

#### BRG-022 实时性能快照 `GET /api/perf/snapshot`
- **用途**：CPU/内存/swap/磁盘/卷 + 温度缓存即时快照
- **请求参数**：无
- **响应**：`{"success":true,"cpu":{"percent":...},"memory":{...},"swap":{...},"disks":[...],"volumes":[...],"cpu_temp":...,"gpu_temp":...}`
- **代码出处**：perf_service.py `handle_perf_snapshot`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-023 记录启动 `GET /api/perf/record-start`
- **用途**：启动性能记录任务（采样 interval 秒，默认 2）
- **请求参数**：query `interval`（秒）
- **响应**：`{"success":true,"record_id":"..."}`
- **代码出处**：perf_service.py `handle_perf_record_start`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-024 记录状态 `GET /api/perf/record-status`
- **用途**：记录任务进度/实时值轮询
- **请求参数**：query `record_id`
- **响应**：`{"success":true,"status":"...","samples":N,...}`
- **代码出处**：perf_service.py `handle_perf_record_status`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-025 记录停止 `GET /api/perf/record-stop`
- **用途**：停止记录并落盘记录文件
- **请求参数**：query `record_id`
- **响应**：`{"success":true,"record_id":"...","file":"..."}`
- **代码出处**：perf_service.py `handle_perf_record_stop`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-026 记录分析报告 `GET /api/perf/record-report`
- **用途**：生成记录分析报告数据（饱和度/统计/评估建议）
- **请求参数**：无（取最近记录）或 `record_id`
- **响应**：`{"success":true,"report":{record_id,interval,elapsed,file,stats,disk_stats,series,assessment,components,disk_saturation}}`
- **代码出处**：perf_service.py `handle_perf_record_report`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-027 记录报告导出 `GET /api/perf/record-export`
- **用途**：导出单文件自包含 HTML 报告（perf_report_*.html）
- **请求参数**：query `record_id`（可选）
- **响应**：`{"success":true,"file":"<html路径>"}`
- **代码出处**：perf_service.py `handle_perf_record_export`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-028 压测启动 `GET /api/perf/stress-start`
- **用途**：四阶段压测（磁盘 25s/CPU 15s/内存 10s/GPU 8s，可取消；内存红线保护）
- **请求参数**：无（阶段时长内置）
- **响应**：`{"success":true,"task_id":"..."}`
- **代码出处**：perf_service.py `handle_perf_stress_start`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-029 压测状态 `GET /api/perf/stress-status`
- **用途**：压测阶段进度轮询
- **请求参数**：无（当前任务）
- **响应**：`{"success":true,"stage":"...","progress":N,...}`
- **代码出处**：perf_service.py `handle_perf_stress_status`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-18：**消费场景收窄**（net-doctor 4.1.8 批，a843027/47a03c0）——netdoctor perf_stress 证据回采已切换至 BRG-075 record-latest?kind=stress（本端点为内存态查询，重启后 stress_id/任务态丢失回采恒失效）；剩余用途仅补采流程中任务进行中的状态轮询（带 stress_id，任务查询语义），端点保留不废弃

#### BRG-030 压测取消 `GET /api/perf/stress-cancel`
- **用途**：取消进行中的压测
- **请求参数**：无
- **响应**：`{"success":true,"cancelled":true}`（以 handler 为准）
- **代码出处**：perf_service.py `handle_perf_stress_cancel`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-031 压测导出 `GET /api/perf/stress-export`
- **用途**：导出压测 HTML 报告（含温度峰值）
- **请求参数**：无
- **响应**：`{"success":true,"file":"<html路径>"}`
- **代码出处**：perf_service.py `handle_perf_stress_export`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-032 硬件信息 `GET /api/perf/hwinfo`
- **用途**：硬件配置明细（os/hostname/cpu/memory/disks/gpu；SSD/HDD 判定、系统盘徽章）
- **请求参数**：无
- **响应**：`{"success":true,"hwinfo":{...}}`（**注意解包 hwinfo 结构**）
- **代码出处**：perf_service.py `handle_perf_hwinfo`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-033 温度读数 `GET /api/perf/temps`
- **用途**：CPU/GPU 温度（CPU 需管理员 + LibreHardwareMonitor；后端节流，`temperature_interval_sec` 默认 300）
- **请求参数**：无
- **响应**：`{"success":true,"cpu_temp":...,"gpu_temp":...}`（管理员模式外 CPU 温度为 null）
- **代码出处**：perf_service.py `handle_perf_temps`（节流缓存 `_temps_last`）
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-034 应用配置读取/保存 `GET /api/perf/app-config`
- **用途**：app_config.json 读写（temperature_interval_sec 默认 300，范围 30~3600）
- **请求参数**：GET 无；POST/保存键值经 params（以 handler 为准）
- **响应**：`{"success":true,"config":{...}}`
- **代码出处**：perf_service.py `handle_perf_app_config`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-035 以管理员重启 `GET /api/perf/restart-admin`
- **用途**：UAC 提权重启应用（CPU 温度采集需要管理员）
- **请求参数**：无
- **响应**：`{"success":true,...}`（重启进程即退出）
- **代码出处**：perf_service.py `handle_perf_restart_admin`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-075 最近记录证据源 `GET /api/perf/record-latest`
- **用途**：按 kind 取最近一次「性能分析记录分析报告」或「性能检测（压测）记录结果」（跨模块证据源，net-doctor AI 诊断证据回填消费端；重启后可回读）
- **鉴权**：无（本机进程内）
- **请求参数**：query `kind`（**analysis**=性能分析记录〔内存优先 → analysis_*.json 按修改时间回退〕| **stress**=压测记录〔内存最近完成 → stress_*.json 按修改时间回退〕；缺省 analysis；非法值 `{"success":false,"error":"kind 仅支持 analysis|stress"}`）
- **响应**：`{"success":true,"found":true,"kind":...,"record_id":...,"report":{...}}`；found=false 时不带 record_id/report
- **配套行为变更（非端点）**：压测 worker 收尾新增结构化落盘 `stress_<id>.json`（与 analysis_<id>.json 对称）——重启后压测结果可回读的前提
- **代码出处**：bridge.py:131 挂载（主仓 1eac1b6）；perf_service.py `handle_perf_record_latest`(442-460+)；子仓 perf-analyzer 8faffde（ADR-022 侧）
- **状态**：在用
- **登记记录**：2026-09-18，代码实证（perf-analyzer-dev 实施，主仓 1eac1b6/子仓 8faffde，team-lead 转发知会，api-registrar-dev 复核登记）> 更新 2026-09-18（晚）：**消费方扩展**（net-doctor 4.1.8 批，a843027/47a03c0）——netdoctor AI perf_stress 证据源回采路径由 BRG-029 stress-status **切换为本端点**（kind=stress；理由：stress-status 内存态重启后 stress_id/任务态丢失回采恒失效，本端点落盘记录跨重启可回读）；BRG-029 剩余用途收窄为补采任务进行中轮询

### 2.5 平台接入（uplink.py，挂载于 /api/perf/uplink/*）

#### BRG-036 uplink 状态 `GET /api/perf/uplink/status`
- **用途**：心跳循环运行态/注册态/配置概览；**token 永不回显（仅 has_token 布尔）**
- **请求参数**：无
- **响应**：`{"success":true,"uplink":{enabled,running,server_url,terminal_id,state,registered,last_hb_ts,last_error,has_token,executed_count,iperf3_available,heartbeat_interval,client_version}}`
- **代码出处**：uplink.py `handle_uplink_status`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-037 uplink 配置保存 `GET|POST /api/perf/uplink/save`
- **用途**：保存 server_url/token/enabled 并应用启停（token 留空=不修改）
- **请求参数**：query/params `server_url`（须 http/https 前缀）、`token`、`enabled`（1/true/on/yes）
- **响应**：成功同 BRG-036 结构；失败 `{"success":false,"error":"server_url_must_start_with_http|missing_server_or_token|save_failed: ..."}`
- **代码出处**：uplink.py `handle_uplink_save`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### BRG-038 uplink 手动注册 `GET /api/perf/uplink/register`
- **用途**：立即注册 + 完整一拍（心跳 + 循环未跑时补指标上报）
- **请求参数**：无
- **响应**：成功同 BRG-036 结构；失败 `{"success":false,"error":"missing_server_or_token|..."}`
- **代码出处**：uplink.py `handle_uplink_register`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

### 2.6 主页（home_service.py）

#### BRG-039 本地网络概览 `GET /api/home/network`
- **用途**：网卡/DNS/网关/DHCP 权威解析（PowerShell UTF8 + ipconfig /all + psutil 降级，60s 缓存）
- **请求参数**：无
- **响应**：`{"success":true,"adapters":[...],...}`（asset schema network 字段同源）
- **代码出处**：home_service.py `handle_home_network`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

### 2.7 网络排障（net_service.py，已合入主应用）

> net-doctor 子系统已合入主应用（commit 628c210，main 下发；路由挂载已代码实证 bridge.py:94-103）：bridge.py `ROUTES` 已挂载全部 10 条 `/api/netdoctor/*` 路由（import 自 net_service.py `handle_net_*`）；前端调用方 web/netdoctor.js。前端传参走 query/params，任务类接口返回 `task_id` 供轮询。仓库同步契约见 net-doctor ADR-001（net_service.py/web 副本与主应用同步）。

#### BRG-040 netdoctor 配置读取 `GET /api/netdoctor/config`
- **用途**：节点表/DNS 基线/uplink 配置面状态
- **代码出处**：bridge.py `ROUTES` → net_service.py `handle_net_config`；调用方 net-doctor/web/netdoctor.js（合入 commit 628c210，main 下发）
- **状态**：在用
- **登记记录**：2026-09-09，代码实证（net_service.py 定义，挂载待合入）> 更新 2026-09-09：bridge.py ROUTES 已挂载，转「在用」（代码实证 bridge.py:94-103）> 更新 2026-09-10：写配置新增 `ai_personal_json`（个人版 LLM 配置 api_url/api_key/model：**api_key 留空=不修改**，与 uplink token 同款语义；读取端只回传 `has_key` 布尔不回显明文；api_url 截 300 字、model 截 120 字）；`reset=1` 恢复出厂时 ai_personal **保留**（用户凭据不随恢复清除）；配置落点 app_config.json `netdoctor.ai_personal`（`_load_app_config` 读端容错）（代码实证 net_service.py:1400-1468/126-162，commit a892f62/bd965ae）

#### BRG-041 配置核查 `GET /api/netdoctor/config-check`
- **用途**：DHCP/DNS 基线比对核查任务（离线可用）
- **代码出处**：bridge.py `ROUTES` → net_service.py `handle_net_config_check`（合入 commit 628c210，main 下发）
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-09：bridge.py ROUTES 已挂载，转「在用」（代码实证 bridge.py:95）

#### BRG-042 IP 冲突检测 `GET /api/netdoctor/ipconflict`
- **用途**：活动网卡 IP+MAC → 平台 ipconflict 端点交叉校验；未连中心拒绝（error=not_connected）；疑似时自动调 `/api/v1/ai/analyze`
- **代码出处**：bridge.py `ROUTES` → net_service.py `handle_net_ipconflict` → `run_ipconflict_result`（合入 commit 628c210，main 下发）
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-09：bridge.py ROUTES 已挂载，转「在用」（代码实证 bridge.py:96）> 更新 2026-09-09：admission_log 数据源将接入画方 NAD 准入 API（EXT-006，cross 校验源 admission_log+交换机端口证据）——sources.admission_log 由 not_connected 转 connected，server-platform-dev 实施中，落地后本条与 EXT-006 补代码实证 > 更新 2026-09-09：**sources.admission_log=connected 已上线**（commit a5156cc，api.py:383 `_enrich_ipconflict_admission` 注入）：①同 IP 异 MAC 升级判定含 `admission_registered_macs`（准入登记 MAC 与上报 MAC 不一致 → conflict_suspect=true，evidence 追加 nad 证据块 {source:'nad',name,ou,ttype,manfct/model,online,block,reginfo,macs}，api.py:1196-1200）；②core_switch_state 三态近似：上报 MAC 在准入库且有 macports 交换机记录 → `nas_connected`、有登记无端口 → `registered_no_port`、不在准入库 → `not_connected`（api.py:1187-1195）；③增强逻辑异常防御不阻断主流程（error:xx，api.py:1176-1177）。响应新增字段：admission_hit/admission/conflict_suspect/evidence/admission_registered_macs > 更新 2026-09-10：AI 诊断批次回归核对——nad_client.py `nad_fetch_terms`（nad_client.py:109）、api.py `_enrich_ipconflict_admission`（api.py:1260）及 ipconflict 注入调用点（api.py:383）、bridge.py ipconflict 路由（bridge.py:99）均在位，批次未触碰该链路，无回归（代码实证）> 更新 2026-09-10（晚）：①检测对象锁定改 **Find-NetRoute 路由解析**（目标=uplink server_url 主机所在通信网卡，失败回退活动网卡并 `object_note` 如实标注，net_service.py:684-712 `run_ipconflict_result`，net-doctor 1f60f95 ADR-013）；②verdict 透传 SRV-051 新契约（ADR-028：suspect_reasons/nic_history）；③疑似深度取证消费 SRV-078/079（ADR-029 深度检测，前端深度证据渲染）

#### BRG-043 连通性检测启动 `GET /api/netdoctor/ping-start`
- **用途**：8 节点逐节点 ping / nslookup / w32tm(stripchart) 探测，JSONL 落盘
- **代码出处**：bridge.py `ROUTES` → net_service.py `handle_net_ping_start`（合入 commit 628c210，main 下发）
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-09：bridge.py ROUTES 已挂载，转「在用」（代码实证 bridge.py:97）

#### BRG-044 连通性历史 `GET /api/netdoctor/ping-history`
- **用途**：连通性检测历史记录查询
- **请求参数**：query `limit`（默认 100）
- **代码出处**：bridge.py `ROUTES` → net_service.py `handle_net_ping_history`（合入 commit 628c210，main 下发）
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-09：bridge.py ROUTES 已挂载，转「在用」（代码实证 bridge.py:98）

#### BRG-045 路由追踪 `GET /api/netdoctor/tracert-start`
- **用途**：tracert 逐跳解析 + 平台 route-nodes CIDR 区域标注
- **请求参数**：query `target`
- **代码出处**：bridge.py `ROUTES` → net_service.py `handle_net_tracert_start`（合入 commit 628c210，main 下发）
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-09：bridge.py ROUTES 已挂载，转「在用」（代码实证 bridge.py:99）

#### BRG-046 网络压测启动 `GET /api/netdoctor/stress-start`
- **用途**：多包大小持续 ping 中心 + 平台 iperf-server + 本地 iperf3 客户端（需连中心）
- **请求参数**：query `duration_sec`、`sizes`、`udp_mbps`
- **代码出处**：bridge.py `ROUTES` → net_service.py `handle_net_stress_start`（合入 commit 628c210，main 下发）
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-09：bridge.py ROUTES 已挂载，转「在用」（代码实证 bridge.py:100）

#### BRG-047 任务状态轮询 `GET /api/netdoctor/task-status`
- **用途**：通用后台任务轮询
- **请求参数**：query `task_id`
- **代码出处**：bridge.py `ROUTES` → net_service.py `handle_net_task_status`（合入 commit 628c210，main 下发）
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-09：bridge.py ROUTES 已挂载，转「在用」（代码实证 bridge.py:102）

#### BRG-048 任务取消 `GET /api/netdoctor/task-cancel`
- **用途**：取消运行中任务
- **请求参数**：query `task_id`
- **代码出处**：bridge.py `ROUTES` → net_service.py `handle_net_task_cancel`（合入 commit 628c210，main 下发）
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-09：bridge.py ROUTES 已挂载，转「在用」（代码实证 bridge.py:103）

#### BRG-049 压测报告导出 `GET /api/netdoctor/stress-export`
- **用途**：导出压测 HTML 报告（需先有完成的压测任务）
- **请求参数**：query `task_id`
- **代码出处**：bridge.py `ROUTES` → net_service.py `handle_net_stress_export`（合入 commit 628c210，main 下发）
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-09：bridge.py ROUTES 已挂载，转「在用」（代码实证 bridge.py:101）

#### BRG-050 AI 智能诊断（双模式）`POST /api/netdoctor/ai-diagnose`
- **用途**：六类日志包 + 问题概述 → AI 诊断结论。mode 双模式：`enterprise`=转发代理至平台终端诊断接口（SRV-070，中心模型链）；`personal`=本机直连第三方 OpenAI 兼容 API（不依赖中心）
- **鉴权**：无（本机进程内调用；enterprise 模式的平台侧 token 仅由 uplink 后端注入，前端不接触）
- **请求参数**：经 `bridge.call(path, body)` 第二参 JSON 透传（bridge.py `call` 对本路径特殊分支：body 解析失败按 None 处理、单参旧调用兼容）；method 不敏感（bridge 按 path 分发）
  - `mode`：`"enterprise"`（缺省）| `"personal"`
  - `issue`：问题概述（必填，超 2000 字截断，空串 → issue_empty）
  - `logs`：对象，键匹配 `^[a-z_]{1,32}$` 的日志类（hwinfo/os_info/perf_analysis/perf_stress/system_log/network），值对象或字符串均收；personal 侧序列化后每类截 32768 字符
- **响应**：成功 `{"success":true,"response_text":"<三段结构诊断>","model":"...","duration_ms":N}`；enterprise 另含 `analysis_id`（中心落库可追溯，**失败诊断也落库并透传**）；personal **无 analysis_id**（本地诊断不经中心库）
- **错误**：`{"success":false,"error":"..."}`——enterprise：`not_connected`（未连中心）/`body_invalid`/`issue_empty`/`ai_diagnose_http_<code>: <服务端error>`（含 analysis_id）；personal：`not_configured`/`issue_empty`/`personal_http_<code>: <响应体前200字>`/`personal_request_failed`/`personal_bad_response`/`personal_empty_response`（**错误信息永不携带 api_key**）
- **enterprise 细节**：`uplink_configured()` 前置 → `_platform_post("/api/v1/terminals/{tid}/ai/diagnose", payload, timeout=125)`（目标即 SRV-070）；同步等待 ≤120s（服务端模型链）+ 本端 125s 保护
- **personal 细节**：配置读 app_config.json `netdoctor.ai_personal`（api_url/api_key/model，缺 url 或 model 即 not_configured；api_key 可空=无鉴权服务）；地址自适配 `_personal_chat_url`（完整 `/v1/chat/completions` 原样 / 以 `/v1` 结尾补 `/chat/completions` / 其余按 base 补 `/v1/chat/completions`）；urllib 直连 120s 超时，temperature 0.3、stream false
- **提示词**：`_ND_AI_PROMPT_SYSTEM` 三段结构（【故障原因分析】引用日志依据/【处理意见】立即处理+建议观察/【风险提示】数据缺失）单份沉淀于 net_service（与 server-platform/server/ai.py `DIAG_SYSTEM_PROMPT` 语义对齐，防漂移；企业版 prompt 在中心侧）
- **代码出处**：bridge.py `call`（ai-diagnose 特殊分支 bridge.py:128-133）→ net_service.py `handle_net_ai_diagnose`(1561) / `_ai_diagnose_enterprise`(1576) / `_ai_diagnose_personal`(1670) / `_ai_personal_config`(1660) / `_nd_ai_logs_text`(1621) / `_ND_AI_PROMPT_SYSTEM`(1610)
- **状态**：在用（exe 待用户窗口重启生效，代码已入库）
- **登记记录**：2026-09-10，代码实证（**补登**：路由与 body 双参自主应用 98c0595 第六模块已存在但漏登台账，本次补登）> 更新 2026-09-10：mode 参数扩展（net-doctor a892f62 / 主应用 bd965ae，ADR-009）——enterprise=中心转发原逻辑不变（b794381）；personal=本机 urllib 直连 OpenAI 兼容 /v1/chat/completions（120s 超时）；三段提示词常量 `_ND_AI_PROMPT_SYSTEM` 单份沉淀（与 server/ai.py DIAG_SYSTEM_PROMPT 语义对齐）；个人版配置存 app_config.json netdoctor.ai_personal（url/api_key/model，key 留空保持语义，经 BRG-040 `ai_personal_json` 写入）；personal 响应无 analysis_id（meta 本地诊断）> 更新 2026-09-10（晚）：①personal URL 预处理 `_personal_norm_url`——折叠路径连续斜杠（保留 scheme:// 头；实证 `https://host//v1/...` 会脱离 API 路由落入站点前端兜底页致假阳性 200），`_personal_chat_url`/`_personal_models_url` 均已接入（net-doctor 1f60f95）；②`_ND_AI_PROMPT_SYSTEM` 加**证据可信性硬约束**三条（只引用日志中实际出现的事件 ID/来源/时间戳、引用必附时间戳、缺日志显式声明证据不足；与 server DIAG_SYSTEM_PROMPT 同款加固，analysis_id=16 虚构 WHEA-Logger 17 实证，net_service.py:1664-1678）；enterprise/personal 本体契约不变

#### BRG-051 个人版 LLM 连通性测试 `GET /api/netdoctor/ai-personal-test`
- **用途**：个人版第三方 LLM 连通性测试（轻量 GET /models 探测，不消耗对话额度；供设置弹窗保存前验证）
- **鉴权**：无（本机进程内调用）
- **请求参数**：query `api_url`（必填）、`api_key`（可选，非空时加 `Authorization: Bearer <key>` 头）
- **响应**（**语义注意：测试动作完成即 success=true，业务结论看 ok 字段**）：
  - 可达 `{"success":true,"ok":true,"http_code":200,"hint":"服务可达（HTTP 200）"}`
  - 服务可达但被拒（key/地址错）`{"success":true,"ok":false,"http_code":401,"hint":"服务可达但请求被拒（HTTP 401，请检查 Key / 地址）"}`
  - 连接失败/超时 `{"success":true,"ok":false,"http_code":null,"hint":"连接失败：<原因>"}`（15s 超时）
  - 缺参 `{"success":false,"error":"api_url_required"}`
- **地址自适配**：`_personal_models_url`（完整 `/models` 原样 / 以 `/v1` 结尾补 `/models` / 其余按 base 补 `/v1/models`）
- **代码出处**：bridge.py ROUTES（bridge.py:108，bd965ae +1 路由）→ net_service.py `handle_net_ai_personal_test`(1723) / `_personal_models_url`(1648)；调用方 web/netdoctor.js
- **状态**：在用（exe 待用户窗口重启生效，代码已入库）
- **登记记录**：2026-09-10，代码实证（net-doctor a892f62 / 主应用 bd965ae）> 更新 2026-09-10（晚）：语义修复（c7219c7）+ 防假阳性（1f60f95）——①api_key 留空=**回退已保存配置 Key**（对齐保存端「留空不修改」语义），响应新增 `used_key` 三态（provided=表单现值 / saved=已保存 / none）；②used_key=none 不发请求，直接 `{"success":true,"ok":false,"used_key":"none","hint":"未配置 API Key，请先填写并保存（设置 → AI 诊断（个人版））"}`；③401 专用 hint「Key 被拒（HTTP 401）——请核对 Key 是否完整/有效」（net_service.py:1795-1848）；④200 防假阳性：响应非 OpenAI 结构（无 `data` 字段）或为 HTML 兜底页 → ok=false「服务可达但该地址不是 API 接口（返回了网页）」，请检查 URL 路径；CT 为 JSON 但读取截断等异常保守放行不误杀；⑤URL 先经 `_personal_norm_url` 连续斜杠折叠

#### BRG-052 IP 冲突深度检测启动（本地转发）`GET /api/netdoctor/conflict-deep-start`
- **用途**：发起平台侧深度检测（ADR-029 五步编排执行在平台，本地仅秒级转发创建任务，不占本地任务引擎）
- **鉴权**：无（本机进程内）；平台侧 X-ETP-Token 由 uplink 后端注入，前端不接触
- **请求参数**：query `ip`/`mac`（**均可选**——与 main 口述「body 传参」不符，代码实证为 query 传参，netdoctor.js:574 拼 query 串；net_service.py:1548-1567 读 params）
  - ip 缺省 = 本地同源采集：active 带 IPv4+MAC 网卡 + Find-NetRoute 锁定中心路由出口网卡（与 BRG-042 `_pick_conflict_adapter` 同源；无候选 → no_active_adapter）
  - mac 缺省随采集携带；显式传入时归一为大写冒号格式（`replace("-",":").upper()`）
  - ip 经 `ipaddress.ip_address` 本地预校验，非法 → invalid_ip（不发平台请求）
- **转发目标**：`POST /api/v1/terminals/{tid}/netdoctor/ipconflict-deep`（SRV-078）
- **响应**：成功 `{"success":true,"task_id":"DC-xxxxxxxx","status":"running","ip":"...","mac":"..."}`；失败 `{"success":false,"error":"...","detail":"<平台error>"}`——`not_connected`（未连中心）/`no_active_adapter`/`invalid_ip`/平台错误映射（400→`invalid_ip`、404→`not_registered`、429→`busy`，其余 `platform_http_<code>`）
- **代码出处**：net_service.py `handle_net_conflict_deep_start`(1540)；调用方 net-doctor/web/netdoctor.js:574；契约 server ADR-029（9e604a7+cc34bae）+ net-doctor ADR-014
- **状态**：在用（net_service `NET_ROUTES`:1918 已定义，standalone 可用；**主应用 bridge.py ROUTES 挂载待同步**——grep 实证当前未挂载，主应用内调用将返回「未知接口」）
- **登记记录**：2026-09-10（晚），代码实证（net-doctor-dev/server-platform-dev 双向确认增量，team-lead 下发）> 更新 2026-09-10（晚 2）：主应用 bridge.py ROUTES 已挂载（**40abd3f** hotfix「v8 内按钮不可用根因」），转「在用（双端就绪）」，撤「挂载待同步」

#### BRG-053 IP 冲突深度检测轮询（本地转发）`GET /api/netdoctor/conflict-deep-poll`
- **用途**：深度检测任务进度/结论轮询（执行在平台侧，本地纯转发 task 视图）
- **鉴权**：无（本机进程内）；平台侧 token 后端注入
- **请求参数**：query `task_id`（必填，缺 → missing_task_id）；转发前 `quote(task_id, safe="")` URL 编码
- **转发目标**：`GET /api/v1/terminals/{tid}/netdoctor/ipconflict-deep/{task_id}`（SRV-079）
- **响应**：成功 `{"success":true,"task":{status:"running|done|failed",steps:[...],verdict:{...}}}`（平台 task 视图原样透传，net_service.py:1594-1599）；失败 `{"success":false,"error":"..."}`——`not_connected`/`missing_task_id`/`platform_http_<code>`（未知任务即 platform_http_404）
- **代码出处**：net_service.py `handle_net_conflict_deep_poll`(1585)；调用方 net-doctor/web/netdoctor.js:602（encodeURIComponent）；契约同 BRG-052
- **状态**：在用（net_service `NET_ROUTES`:1919 已定义，standalone 可用；**主应用 bridge.py ROUTES 挂载待同步**——同 BRG-052）
- **登记记录**：2026-09-10（晚），代码实证（同 BRG-052 来源）> 更新 2026-09-10（晚 2）：主应用 bridge.py ROUTES 已挂载（40abd3f，同 BRG-052），转「在用（双端就绪）」

#### BRG-054 IP 冲突 AI 辅助分析重跑（本地转发）`GET|POST /api/netdoctor/conflict-ai-reanalyze`
- **用途**：手动重跑 IP 冲突 AI 辅助分析（数据更新后可重跑；与 BRG-042 疑似时自动分析同款引擎，issue 由最新本地证据构造）
- **鉴权**：无（本机进程内）；平台侧 X-ETP-Token 由 uplink 后端注入
- **请求参数**：query/params（method 不敏感，bridge 按 path 分发；前端 netdoctor.js:580 以 query 串调用）——`ip`（必填，ipaddress 本地校验 → invalid_ip）、`mac`（随 issue 携带，无本地校验）、`evidence_json`（可选，JSON 数组字符串，**最多 12 条**、构造 issue 时每条截 120 字；解析失败按空处理）
- **issue 构造**：`"IP冲突疑似核查：终端 {tid} IP={ip} MAC={mac}；平台证据：{条目以「; 」join 或「无明细」}"`
- **转发目标**：`POST /api/v1/ai/analyze`（SRV-055），复用 `_ai_analyze_issue(tid, issue, timeout=45)`（45s 超时）
- **响应**：`{"success":true,"ai":{"ok":true,"analysis":"<结论>","model":"...","analysis_id":"N"}}`；业务失败 `{"success":true,"ai":{"ok":false,"error":"ai_http_<code>: ..."}}`；本地失败 `{"success":false,"error":"not_connected|invalid_ip"}`
- **提取链热修复（2026-09-10）**：平台实证返回字段为 **`response`**，旧提取链 analysis/content/result 永远落空致「（无内容）」；`analysis_id` 透传供 UI 追溯（net_service.py:685-699）
- **代码出处**：net_service.py `handle_net_conflict_ai_reanalyze`(1610) / `_ai_analyze_issue`(685)；bridge.py ROUTES（bridge.py:104）+ net_service `NET_ROUTES`(1954)；调用方 net-doctor/web/netdoctor.js:580
- **状态**：在用（bridge.py 与 NET_ROUTES 均已挂载，bridge 挂载 commit b344bbc「AI辅助分析修复与手动重跑，net-doctor acee817 同步」）
- **登记记录**：2026-09-10（晚 2），代码实证（team-lead 下发追加，与上批 4 条一并入账）> 更新 2026-09-11：`_ai_analyze_issue` 签名扩展 `extra` 参数（payload.update(extra)，net_service.py:765-772）——供 routetrace 分支附加 {kind,data}（见 BRG-055）；本条请求/响应结构不变

#### BRG-055 路由追踪 AI 分析（本地转发）`GET|POST /api/netdoctor/trace-ai-analyze`
- **用途**：路由追踪结果 AI 研判（平台 routetrace 聚合分支，ADR-031；LLM 真实耗时 30~60s，本地 45s 超时转发，平台侧继续跑完落库）；前端显隐门控=tracert 完成渲染 且 中心已连接 才显示入口（未连接不发起）+ 防重入
- **鉴权**：无（本机进程内）；平台侧 X-ETP-Token 由 uplink 后端注入
- **请求参数**：query/params `target`（必填 → missing_target）、`hops_json`（可选，前端结果区全量 JSON 数组字符串，元素 {hop,delays,ip,host,zone}；解析失败/非数组按 []）
- **issue 构造**：逐跳格式化（前 30 跳，`#<hop> <ip>（<zone>）`，非 dict 元素截 80 字）→「路由追踪分析：终端 {tid} 目标 {target}；逐跳：#1 … → #2 …」
- **转发**：`_ai_analyze_issue(tid, issue, extra={"kind": "routetrace", "data": hops})` → SRV-055（45s 超时）
- **响应**：`{"success":true,"ai":{ok,analysis,model,analysis_id}}`；本地失败 `{"success":false,"error":"not_connected|missing_target"}`；业务失败在 ai.ok（ai_http_<code>）
- **⚠️ 契约不一致（2026-09-11 对账发现，待修）**：本端 payload 附加键为 `data`（net_service.py:1745），平台 dispatch routetrace 分支读 `context`（api.py:279-281）→ route_ctx 恒 None → `_aggregate_routetrace_context` 返回 None → **routetrace 三源聚合分支实际永不生效，全部回退一般性分析**；修复方向二选一（终端改键 context / 平台兼容 data），定责修复后本条与 SRV-055 补更新行 > 更新 2026-09-11（晚）：**契约不一致已消项**——payload 契约修正：键名 `data`→`context`，且结构由裸 hops 数组升级为 `{target, hops}` 对象（完全对齐平台 `_aggregate_routetrace_context` 期望的 route_ctx 形状；net_service.py:1747-1749，net-doctor 4679b11 / 主应用 e6452e5，冒烟 +6 形状断言 130/130、E2E 298/298）；routetrace 三源聚合分支恢复生效
- **代码出处**：net_service.py `handle_net_trace_ai_analyze`(1719)；bridge.py:106 + `NET_ROUTES`:2143；调用方 net-doctor/web/netdoctor.js
- **状态**：在用（双端挂载就绪；聚合分支受上述不一致影响走回退）
- **登记记录**：2026-09-11，代码实证（net-doctor a8aa49e / server-platform 2cb39d2 ADR-031 / 主应用 bridge 挂载 c4495bc）

#### BRG-056 AI 诊断历史读取 `GET /api/netdoctor/ai-history`
- **用途**：AI 诊断历史读取（**JSONL 文件持久化主存储**，2026-09-11 用户要求重启可见，替代内存/localStorage 方案）
- **请求参数**：无
- **响应**：`{"success":true,"history":[{ts,...}]}`（按 ts 倒序，**上限 200 条滚动**；坏行静默跳过）
- **存储**：`%LOCALAPPDATA%\winhelper\netdoctor_records\ai_history.jsonl`（`_ai_history_path` → `_records_dir`）；写经 `.tmp` + `os.replace` 原子替换（`_write_ai_history`）
- **代码出处**：net_service.py `handle_net_ai_history`(1783) / `_read_ai_history`(1753) / `_ai_history_path`(1749)；bridge.py:107 + `NET_ROUTES`:2144
- **状态**：在用
- **登记记录**：2026-09-11，代码实证（net-doctor f22e2bf / 主应用 916e791）

#### BRG-057 AI 诊断历史追加 `GET|POST /api/netdoctor/ai-history-append`
- **用途**：追加历史记录（records_json 兼容单对象/数组——数组用于 localStorage 一次性迁移）；合并后超 200 条滚动淘汰最旧
- **请求参数**：query/params `records_json`（JSON 字符串；元素须为含 `ts` 的 dict；解析失败或无有效记录 → invalid_records）
- **响应**：`{"success":true,"total":N}`（≤200）；失败 `{"success":false,"error":"invalid_records"}`
- **代码出处**：net_service.py `handle_net_ai_history_append`(1788)；bridge.py:108 + `NET_ROUTES`:2145
- **状态**：在用
- **登记记录**：2026-09-11，代码实证（f22e2bf / 916e791）

#### BRG-058 AI 诊断历史删除 `GET|POST /api/netdoctor/ai-history-delete`
- **用途**：删除历史记录：`all=1` 清空文件（removed=清除总数）或 `ts=<时间戳>` 单条剔除（重写文件；无命中 removed=0）
- **请求参数**：query/params `all`（="1" 清空）或 `ts`（整数；两者皆缺 → missing_ts）
- **响应**：`{"success":true,"removed":N}`；失败 `{"success":false,"error":"missing_ts"}`
- **代码出处**：net_service.py `handle_net_ai_history_delete`(1806)；bridge.py:109 + `NET_ROUTES`:2146
- **状态**：在用
- **登记记录**：2026-09-11，代码实证（f22e2bf / 916e791）

#### BRG-076 最近路由追踪回读 `GET /api/netdoctor/tracert-last`
- **用途**：最近一次路由追踪回读（tracert JSONL 最近 7 天最新一条；AI 诊断 net_tracert 源**会话失忆回填**）
- **鉴权**：无（本机进程内）
- **请求参数**：无
- **响应**：`{"success":true,...,{ts,ts_text,target,hops[],reached}}`（无记录时 found 语义以 handler 为准）
- **代码出处**：bridge.py:167 挂载（主仓 0b3f43d）；net_service.py `handle_net_tracert_last`(1091)；JSONL 落盘 `tracert_YYYYMMDD.jsonl`(:1052 通用追加)
- **状态**：在用（双仓 MD5 一致）
- **登记记录**：2026-09-18，代码实证（net-doctor-dev 实施，子仓 45cb17c/主仓 0b3f43d，4.1.7 AI 证据增强，按常设约定显式列清单知会，api-registrar-dev 复核登记）

#### BRG-077 最近压测总结回读 `GET /api/netdoctor/stress-last`
- **用途**：最近一次网络压测总结回读（stress JSONL 最新一条；AI net_stress 源历史回填，**不需中心**）
- **鉴权**：无（本机进程内）
- **请求参数**：无
- **响应**：`{"success":true,...,{ts,ts_text,center,ping[],iperf[],verdict}}`
- **代码出处**：bridge.py:169 挂载；net_service.py `handle_net_stress_last`(1099)；JSONL `stress_YYYYMMDD.jsonl`
- **状态**：在用（双仓 MD5 一致）
- **登记记录**：2026-09-18，代码实证（同 BRG-076）

#### BRG-078 网络性能实时快照 `GET /api/netdoctor/net-snapshot`
- **用途**：网络性能实时快照（**AI 第 9 源 net_perf_snapshot**）：链路速率（复用 home_service 缓存）+ 网关/核心交换机各 10 包 ping 采样（min/avg/max/丢包率）
- **鉴权**：无（本机进程内）
- **请求参数**：无
- **响应**：链路速率 + 网关/核心 ping 采样块；**免中心，正常网络 <5s，同步执行**；ping 走既有 `_ping_summary` 引擎（:376，count=10/timeout 500ms，非裸 subprocess）
- **代码出处**：bridge.py:170 挂载；net_service.py `handle_net_net_snapshot`(1107-1123+)
- **状态**：在用（双仓 MD5 一致）
- **登记记录**：2026-09-18，代码实证（同 BRG-076；AI 诊断源清单 8 源→**9 源**，prompt 系统词增补第 4 条〔性能类缺证据输出约定〕为服务端内部行为，门禁 E2E 373/373 + smoke 137/137）

### 2.8 桌面管控（desktop_policy.py，锁屏及壁纸管理）

> 来源：desktop-policy 子仓引擎 `desktop_policy.py`（commit 9bdfcdf，P2）+ 主应用集成 commit **08b3547**（bridge.py ROUTES 5 条 / web/desktoppolicy.js / index.html 导航 `data-tab="desktoppolicy"` 置于 filesearch 之后，菜单名「锁屏及壁纸管理」/ app.js switchTab 守卫 `initDesktopPolicyTab`）。
> 传输与鉴权：pywebview bridge IPC（C/S 模式，无 HTTP 端口）；前端 web/desktoppolicy.js（`dp` 前缀）经 `dpFetch` → `window.pywebview.api.call('/api/desktoppolicy/...')`；鉴权无（本机进程内）。bridge 按 path 分发无方法语义，下述 GET/POST 为 handler docstring 标注的调用语义。
> 引擎形态：主应用内常驻线程（`get_engine` 单例首次访问创建并启动，`run_forever` 轮询默认 120s 抖动 ±15%；未注册平台时拉取失败走离线兜底不回退）；耗时动作（policy-now/apply-now）即时返回 `task_id` 经 task-status 轮询（`_dp_start_task` 后台线程）。双仓副本：主仓根 desktop_policy.py 与 desktop-policy/desktop_policy.py MD5 一致（2026-09-16 核对）。

#### BRG-059 引擎状态 `GET /api/desktoppolicy/status`
- **用途**：引擎状态 + 显示器布局 + 四类策略最近执行结果摘要
- **鉴权**：无（本机进程内）
- **请求参数**：无
- **响应**：`{"success":true,"engine_running":true,"poll_sec":120,"revision":N,"reported_at":epoch,"session_type":"console|rdp","monitors":[{index,width,height,left,top,primary}],"results":{"desktop_wallpaper":{state,...},"lock_screen":{...},"power_plan":{...},"idle_lock":{...}},"has_cached_policy":bool}`（每策略经 `_summarize` 归纳为 `state: ok|error|skipped|never` + 可选 code/message/detail）
- **代码出处**：desktop_policy.py `handle_dp_status`(1245) / `_summarize`(1272)；bridge.py:131；调用方 web/desktoppolicy.js
- **状态**：在用
- **登记记录**：2026-09-16，代码实证（desktop-policy 9bdfcdf / 主应用 08b3547，team-lead 下发登记）

#### BRG-060 立即拉取策略 `POST /api/desktoppolicy/policy-now`
- **用途**：立即执行一轮「策略拉取→本地应用→结果回传」（`Engine.tick`，后台任务）
- **鉴权**：无（本机进程内）
- **请求参数**：无
- **响应**：`{"success":true,"task_id":"dp<epoch>_<seq>"}` → BRG-062 轮询；任务结果 `{"ok":true,"revision":N,"results":{...},"reported":bool}`（reported=回传是否成功）；平台不可达时离线兜底 `{"ok":false,"offline":true}`（保持现有配置不回退）
- **代码出处**：desktop_policy.py `handle_dp_policy_now`(1283) / `Engine.tick`(1002) / `_dp_start_task`(1221)；bridge.py:132
- **状态**：在用
- **登记记录**：2026-09-16，代码实证（同 BRG-059）

#### BRG-061 离线重应用 `POST /api/desktoppolicy/apply-now`
- **用途**：不联网，按本地缓存策略（state.json `policies`）重新应用四类策略
- **鉴权**：无（本机进程内）
- **请求参数**：无
- **响应**：`{"success":true,"task_id":...}` → 轮询；任务结果 `{"ok":true,"results":{...}}`；无缓存策略 `{"ok":false,"error":{"code":"apply_failed","message":"暂无已缓存策略"}}`
- **代码出处**：desktop_policy.py `handle_dp_apply_now`(1289)；bridge.py:133
- **状态**：在用
- **登记记录**：2026-09-16，代码实证（同 BRG-059）

#### BRG-062 任务状态轮询 `GET /api/desktoppolicy/task-status?task_id=`
- **用途**：BRG-060/061 后台任务进度与结果轮询
- **鉴权**：无（本机进程内）
- **请求参数**：query `task_id`（必填）
- **响应**：`{"success":true,"status":"running|done|error","result":{...}}`；未知 task_id `{"success":false,"error":"任务不存在"}`；error 任务 result 固定 `{"ok":false,"error":{"code":"apply_failed","message":<异常串>}}`
- **清理语义**：done/error 任务在轮询时惰性清理（**实现为 started 超 600s 移除**；登记时曾发现代码注释写「60s」与实现不符【已消项 2026-09-16（晚）：注释对齐为「发起超 600s（10 分钟）后移除」，desktop_policy.py:1317，热修 c316d87/72d51bc】——desktop_policy.py:1319-1323）
- **代码出处**：desktop_policy.py `handle_dp_task_status`(1306)；bridge.py:134
- **状态**：在用
- **登记记录**：2026-09-16，代码实证（同 BRG-059）> 更新 2026-09-16（晚）：注释语义备注消项（热修对齐，代码实证）

#### BRG-063 日志尾部 `GET /api/desktoppolicy/logs`
- **用途**：日志目录与当日日志尾部（≤60 行）
- **鉴权**：无（本机进程内）
- **请求参数**：无
- **响应**：`{"success":true,"log_dir":"<LOCALAPPDATA>\\winhelper\\desktop_policy\\logs","log_file":"...\\dp_YYYYMMDD.log","exists":bool,"tail":[...]}`
- **⚠️ 已知缺陷（登记如实，2026-09-16 对账发现）【已消项 2026-09-16（晚）】**：本接口读 `data_dir()/logs/` 子目录（对齐 CONTRACT.md §4），但主应用运行态**无任何 `set_log_dir` 调用**（grep 实证：定义于 desktop_policy.py:54，调用仅存在于 desktop-policy/tools 测试脚本）——引擎 `log()` 实际写 `data_dir()` 根目录，主应用内本接口 tail 恒空/exists=false > 更新 2026-09-16（晚）：**缺陷已消项**——热修 `log()` 默认目录直接对齐 `data_dir()/logs`（desktop_policy.py:64 `_log_dir or os.path.join(data_dir(), "logs")`，零侵入：主应用与独立运行同路径，`set_log_dir` 保留为测试注入口）+ `os.makedirs(d, exist_ok=True)` 自动建目录（:65）；验证冒烟 21/21（含 3 条主应用态防回归断言：写读同路径/tail 含探针/目录自建）+ 单测 18/18 + E2E 40/40（desktop-policy c316d87 / 主应用 72d51bc，代码实证 log():60-71，team-lead 下发消项）
- **代码出处**：desktop_policy.py `handle_dp_logs`(1327) / `log`(60) / `set_log_dir`(54)；bridge.py:135
- **状态**：在用（缺陷已消项：写读同路径，tail 可见）
- **登记记录**：2026-09-16，代码实证（同 BRG-059）> 更新 2026-09-16（晚）：热修复核通过（见上），`handle_dp_logs` 行号 1325→1327 > 更新 2026-09-16（晚 2）：主应用 2bbf881 增量（desktop_policy.py `read_power_state` 电源状态读取新增 +77 行，供 UI 管控徽章）使 handle_dp_* 行号整体下移——本组行号以登记基线 72d51bc 为准（status 1245 / policy-now 1283 / apply-now 1289 / task-status 1306 / logs 1327），2bbf881 基线下实测对应 1464/1522/1528/1545/1564（工作区另有后续在途增量，行号以最近对账为准）；**响应结构不变**（handle_dp_*/`_summarize` 未改动，代码实证）

### 2.9 自动开关机（power_control.py，power-control P0）

> 来源：power-control 子仓引擎 `power_control.py`（P0 全只读，零写操作）+ 主应用统筹入库 commit **17f506c**（bridge.py:60 `from power_control import handle_pc_snapshot, handle_pc_report` + ROUTES :144-145，power_control.py 全文件 808 行 + docs/POWER_CONTROL_SPEC.md v1.0；子仓指针跟进 8a8bb43）。双仓副本：主仓根 power_control.py 与 power-control/power_control.py 一致（31.78KB，2026-09-16 核对）。
> 传输与鉴权：同 2.8（pywebview bridge IPC，无 HTTP 端口，鉴权无本机进程内）。平台上报通道 `PlatformReporter` 语义复制 desktop_policy `PlatformTransport`（X-ETP-Token 同源 uplink_config.json，不 import uplink）。
> 引擎形态：P0 快照采集全只读；已接入平台时触发 12h 节流的后台自动上报（随心跳上报列入 P1，需 uplink 契约协调）。

#### BRG-064 电源快照采集 `GET /api/powercontrol/snapshot`
- **用途**：本机电源策略快照（机型/BIOS 自动开机能力徽章与 RTC 项/唤醒定时器/关机计划任务/快速启动）；P0 全只读；触发 12h 节流的后台自动上报（已接入平台时）
- **鉴权**：无（本机进程内）
- **请求参数**：无
- **响应**：`{"success":true,"snapshot":{schema:1, collected_at, collected_ts, machine:{hostname,manufacturer,model,system_family,vendor_line,vendor_line_text,capability,capability_text}, bios:{remote_configurable,wmi_class_found,reason,items[],rtc{}}, wake_timers:{ok,need_admin,count,items[]}, shutdown_tasks:{ok,count,items[]}, fast_startup:{registry_present,hiberboot_enabled,available,enabled,note}, errors[]}}`；采集异常 `{"success":false,"error":...}`
- **RTC 形状（企业线）**：`{alarm, alarm_on, time, user_time, date, day, weekdays{}, after_power_loss, wake_on_lan, cycle_text, summary}`（仅实测命中项出现；分线判定=厂商+Lenovo_BiosSetting 类存在性）
- **代码出处**：power_control.py `handle_pc_snapshot`(581) / `collect_snapshot`(790)（同步契约：power-control/power_control.py → 主应用根）；bridge.py:60 import + :144 ROUTES（挂载 commit 17f506c 统筹入库 > 更正 2026-09-16（晚 2）：先前误归 2bbf881——该提交 bridge.py 19 行为 desktop-policy 增量，powercontrol 挂载实经 17f506c 入库）
- **状态**：在用（P0）
- **登记记录**：2026-09-16，代码实证（power-control-dev 实施；解析器以 ThinkCentre M720t 实测 CurrentSetting 两形态为准，power-control ADR-005）

#### BRG-065 电源快照手动上报 `GET /api/powercontrol/report`
- **用途**：立即采集并推送平台存档（POST /api/v1/terminals/{tid}/powercontrol/snapshot，SRV-080；X-ETP-Token 同源 uplink_config，语义复制 desktop_policy PlatformTransport，不 import uplink）；P1 扩展：body `{"human_set":true}` 消费线「已在 BIOS 人工设置」登记后上报
- **鉴权**：无（本机进程内；平台侧凭据由引擎注入，前端不接触）
- **请求参数**：无（可选 body human_set）
- **响应**：`{"success":true,"reported":true,"server":{"ok":true,"snapshot_id":N}}`；未接入平台 `{"success":false,"error":"尚未接入中心平台，请先在主页完成平台接入"}`；平台不可达 `{"success":false,"error":"上报失败（平台不可达或未接入）"}`
- **代码出处**：power_control.py `handle_pc_report`(776) / `PlatformReporter`（terminal_id 三级解析同 desktop-policy ADR-006 口径）；bridge.py:145 ROUTES（挂载 commit 17f506c 统筹入库，更正同 BRG-064）
- **状态**：在用（P1 human_set 登记已用）
- **登记记录**：2026-09-16，代码实证（power-control-dev 实施）> 更新 2026-09-17：P1 human_set 扩展（power-control ADR-005 消费线策略配套）

### 2.10 客户端自启与更新（appctl.py/updater.py，客户端 4.1.0）

> 来源：power-control-dev「三大改造批」（commit **7fe4e9b**，客户端 4.1.0：文件名 bootstrap 配置/托盘常驻/自启/自动更新引擎 + apiFetch body 修复）。**复核归档注记（2026-09-17 晚）**：本组 4 条由 power-control-dev 直写台账未走登记知会（违反当日常设约定），api-registrar-dev 对账发现并复核——bridge.py:186-189 挂载实证 + :224（body 双参透传扩展至 /api/app/*）+ appctl.py/updater.py 存在实证，条目内容合格保留；组归属从 2.9 划出独立成组。后续直写台账请附显式清单知会。

#### BRG-066 开机自启状态 `GET /api/app/autostart`
- **用途**：三大改造②——读 HKCU Run「EyeTerm」键（安装器 [Tasks] autostart 同一键）
- **鉴权**：无（本机进程内）
- **响应**：`{"success":true,"enabled":bool}`
- **代码出处**：appctl.py `get_autostart`；bridge.py ROUTES
- **状态**：在用
- **登记记录**：2026-09-17，代码实证（power-control-dev，三大改造批）

#### BRG-067 开机自启设置 `GET|POST /api/app/autostart-set`
- **用途**：写入/删除 HKCU Run 启动项（CreateKeyEx 幂等创建；客户端设置弹窗「客户端」卡开关，web/appui.js）
- **请求参数**：body `{"enabled":bool}`
- **响应**：`{"success":true,"enabled":bool}`
- **代码出处**：appctl.py `set_autostart`；bridge.py ROUTES
- **状态**：在用
- **登记记录**：2026-09-17，代码实证（同批）

#### BRG-068 更新状态查询 `GET /api/app/update-status`
- **用途**：三大改造③——更新引擎状态透出（前端提示条 30s 轮询，web/appui.js ub 前缀）
- **响应**：`{"success":true,"update":{"status":"idle|downloading|ready|failed","version","error"}}`
- **代码出处**：appctl.py `update_status` → updater.py 状态机（ADR-012）；bridge.py ROUTES
- **状态**：在用（清单端点 TBC-002 已消项：server-platform 327a69b `/api/v1/client/update-manifest` 扁平形状已部署生产并实测，updater.fetch_manifest 已对齐）
- **登记记录**：2026-09-17，代码实证（同批）> 更新 2026-09-17（晚）：TBC-002 消项（发布链代跑 + 扁平形状四态单测 26/26）> 更新 2026-09-17（晚 2）：power-control-dev 补发显式知会（按常设约定），api-registrar-dev 对账确认直改注记准确——消项依据：server-platform 327a69b update-manifest 已部署生产（deploy 代跑 + probe 401 路由生效）+ updater.fetch_manifest 形状对齐（扁平/相对路径拼接/null·401 静默）

#### BRG-069 立即更新 `GET|POST /api/app/update-apply`
- **用途**：拉起 updater 子进程（--et-updater：等主进程退出 → /SILENT 安装 → 安装器 postinstall 自启新客户端）→ bridge `_request_exit()` 主进程退出
- **响应**：`{"success":true,"exiting":true}`；无可安装包 `{"success":false,"error":"尚无可安装的更新包"}`
- **代码出处**：appctl.py `update_apply` + bridge `handle_app_update_apply`（exit hook 补齐——desktop.py 此前注册 AttributeError 被吞的既有缺陷一并消项）；bridge.py ROUTES
- **状态**：在用
- **登记记录**：2026-09-17，代码实证（同批）

### 2.11 文件检索（file_search.py + file-search 仓 search_service.py，路线 C 自研索引）

> 来源：**漏登补登**（2026-09-17）——路由随主仓 c37cf97（菜单集成 4 路由）/5f32dbe（路线 C 索引引擎，Everything 依赖移除）/17f506c（统筹：跳段续扫等）入库，未随批知会登记；net-doctor-dev 按常设约定知会 894f20a 参数增强时触发本组首登。
> 架构：**file-search 子仓 search_service.py 为权威引擎**（FS_ROUTES :238 恒 4 条，894f20a 检索排序/筛选增强：sort=ext 服务端化 + mtime_from/mtime_to 规范参数）；主仓根 `file_search.py` 为 bridge 挂载的同步副本（bridge.py:42 import + :154-157 ROUTES）。
> ⚠️ 主仓副本滞后警示（2026-09-17 发现）【已消项同日晚】：主仓 file_search.py 曾落后子仓**三个批次**（①894f20a 的 sort=ext/mtime_from|to；②results 契约缺 is_dir——主应用目录行「文件夹」类型判断实际失效；③status 缺 partial_volumes 透出；含品牌文案中性化）——**已由 net-doctor-dev 整文件同步（主仓 commit e39ec89），双仓 MD5 一致（C6C3089A，2026-09-17 实证）**，主应用运行态恢复全量契约；子仓→主仓同步已纳入 net-doctor-dev 批次交付清单（双仓 MD5 校验）。
> 索引库：`C:\ProgramData\EyeTerm\filesearch\index.db`（WAL 只读连接）；结果上限 200；一切子进程 CREATE_NO_WINDOW。
> 组扩展（2026-09-18，4.1.7 索引器部署机制，主仓 c598e05/子仓 9c3c680）：部署主通道 BRG-074（SYSTEM 计划任务 EyeTermFileIndexer ONSTART+HIGHEST + 立即 Run，管理员直执行/非管理员 UAC runas，幂等）+ 常驻 worker 入口 `--fs-indexer-worker`（命名互斥单实例）+ status 三态（ready/building/not_deployed，BRG-071 更新行）；安装器 iss 方案 A 增量不覆盖。**双仓 MD5 一致（同步纪律首次完整生效，2026-09-18 实证）**；门禁 smoke 61/61 + E2E 92/92。

#### BRG-070 文件检索 `GET /api/filesearch/query`
- **用途**：文件名关键词检索（直读 sqlite 索引库，只读）
- **鉴权**：无（本机进程内）
- **请求参数**：query `q`（关键词，支持原生 `ext:a;b` 多扩展名拼接段，组内 OR 组间 AND）、`count`（上限，默认/硬顶 200）、`sort`（name|size|mtime|date_modified|**ext**——ext=扩展名字典序服务端化：目录固定排最前不随正倒序翻转、组内名称稳定序，894f20a）、`ascending`、`mtime_from`/`mtime_to`（修改时间闭区间，epoch 秒或 YYYY-MM-DD，日期串止端 23:59:59 天边界；`date_from`/`date_to` 为兼容别名，894f20a）
- **响应**：`{"success":true,"q":...,"total":N,"count":N,"results":[{name,path,is_dir,size,date_modified}]}`（≤200；**is_dir 为目录行类型判断依据**，e39ec89 补齐——此前缺失期间主应用目录「文件夹」类型判断失效）；引擎不可用如实提示；q 与 ext: 全空 400 语义
- **代码出处**：bridge.py:42/:154；权威实现 file-search/search_service.py `handle_fs_query`(89)/`FS_SORTABLE`(24-30，含 ext)/`_parse_date_param`(34)；主仓副本 file_search.py（**e39ec89 已同步**，与子仓 MD5 一致 C6C3089A）
- **状态**：在用（**全量契约就绪**：ext 排序/mtime 参数/is_dir 已随 e39ec89 同步主仓）
- **登记记录**：2026-09-17，代码实证（漏登补登：路由 c37cf97/5f32dbe/17f506c 批次未随批登记；894f20a 参数增强经 net-doctor-dev 按常设约定知会，api-registrar-dev 对账发现整组漏登一并首登）> 更新 2026-09-17（晚 2）：**主仓副本同步完成转全量在用**（e39ec89 整文件同步，MD5 一致实证；补齐 sort=ext 排序分支 :136-140/mtime 参数/is_dir 契约 :172）

#### BRG-071 检索状态 `GET /api/filesearch/status`
- **用途**：索引器状态查询（索引库存在性/条数等；7a88b91 后前端「检索状态卡」已移除不再消费其渲染，端点仍正常服务）
- **鉴权**：无（本机进程内）
- **请求参数**：无
- **响应**：`{"success":true,file_count,dir_count,volumes,partial_volumes:[...],...}`（partial_volumes 为 e39ec89 补齐项）> 更新 2026-09-18（4.1.7，c598e05/9c3c680）：**响应新增三态字段**——`indexer_task_registered`（bool，schtasks 查询）、`state`（**ready**=库有数据 / **building**=计划任务已注册但库未就绪〔首建分钟级进行中〕/ **not_deployed**=任务未注册）、`hint`（三态文案：ready 空串 / building「索引构建中（首次需数分钟），请稍后重试」/ not_deployed「检索索引未部署：请重新安装客户端或由管理员部署」——**废除旧误导性「请稍后重试」**）；partial 卷如实透出
- **代码出处**：bridge.py:155；file-search/search_service.py `handle_fs_status`(176)；主仓副本 file_search.py `handle_fs_status`(246-276，e39ec89 后持续同步，4.1.7 双仓 MD5 一致)
- **状态**：在用（前端已恢复消费：4.1.7 三态渲染 + 部署按钮 + 轮询到 ready）
- **登记记录**：2026-09-17，代码实证（漏登补登同 BRG-070）> 更新 2026-09-17（晚 2）：partial_volumes 字段随主仓同步补齐（e39ec89 实证 :180-188）> 更新 2026-09-18：三态字段新增（4.1.7，实证 file_search.py:246-276）

#### BRG-072 检索统计 `GET /api/filesearch/stats`
- **用途**：索引统计（扩展名分布 TOP30 + 日期范围，供筛选 UI）
- **鉴权**：无（本机进程内）
- **请求参数**：无
- **响应**：`{"success":true,"exts":[{ext,count}...≤30],"date_min":...,"date_max":...}`
- **代码出处**：bridge.py:156；file-search/search_service.py `handle_fs_stats`(212)；主仓副本 file_search.py(:210-216 同实现)
- **状态**：在用
- **登记记录**：2026-09-17，代码实证（漏登补登同 BRG-070）

#### BRG-073 检索结果定位 `GET /api/filesearch/open-location`
- **用途**：资源管理器定位文件所在目录（复用 /api/disk/open-location 模式）
- **鉴权**：无（本机进程内）
- **请求参数**：query 路径参数（以 handler 为准）
- **响应**：`{"success":true,...}`；子进程 CREATE_NO_WINDOW 纪律（explorer 有意开窗除外）
- **代码出处**：bridge.py:157；file-search/search_service.py `handle_fs_open_location`(200)
- **状态**：在用
- **登记记录**：2026-09-17，代码实证（漏登补登同 BRG-070）

#### BRG-074 索引器部署引导 `POST /api/filesearch/indexer-deploy`
- **用途**：文件检索索引器部署引导——注册 SYSTEM 计划任务 EyeTermFileIndexer（ONSTART + HIGHEST）并立即 Run 一次（装完即首建）
- **鉴权**：无（本机进程内）；**执行权限双通道**：管理员会话直接执行（schtasks /Create /F + /Run）；非管理员走 UAC 提权（ShellExecuteW runas 一次拉起 Create+Run 组合）
- **请求参数**：无
- **响应**：以 handler 为准（部署触发结果；幂等——/F 覆盖 + Run 重复触发无害）
- **配套**：常驻 worker 入口 `--fs-indexer-worker`（命名互斥单实例 + 日志落盘 + 首建完成事件）；desktop.py worker flag 分派（与 --pc-elevated-worker 同款模式）；安装器 EyeTerm.iss 方案 A（SYSTEM 计划任务 ONSTART + 装后立即 Run + 卸载清理，增量不覆盖既有行）
- **代码出处**：bridge.py:159 挂载（主仓 c598e05）；file-search/search_service.py `handle_fs_indexer_deploy`(:132，FS_ROUTES :322 同挂，子仓 9c3c680)；**双仓 MD5 一致（同步纪律生效）**
- **状态**：在用（客户端 4.1.7；门禁 smoke 61/61 含 T10 部署组 + E2E 92/92 含三态与部署流程）
- **登记记录**：2026-09-18，代码实证（net-doctor-dev 实施，主仓 c598e05/子仓 9c3c680，按常设约定显式列清单知会，api-registrar-dev 复核登记）

---

## 三、终端↔平台协议（uplink.py + 命令通道，ADR-015/016/019 冻结稿）

> 协议依据：server-platform ADR-015/016/019（心跳命令通道协议 v1）；客户端版本 uplink v4（CLIENT_VERSION="4.0.0"）。
> 传输层：纯标准库 urllib（uplink.py `_post`），JSON body，请求头 `X-ETP-Token`；token 仅存 `%LOCALAPPDATA%/winhelper/uplink_config.json`（0600 语义），任何日志/异常/状态接口不回显。
> 2026-09-16 扩展：桌面管控契约端点 UPL-011~013——终端引擎 desktop_policy.py `PlatformTransport` 直连平台（X-ETP-Token 同源 uplink_config.json 的 server_url/token/terminal_id，语义复制不 import）；契约 desktop-policy/docs/CONTRACT.md v1.1 冻结稿，**服务端 P1 在途未实现**（TBC-007 跟踪）。

#### UPL-001 注册协议 `POST /api/v1/terminals/register`
- **用途**：注册/更新终端（幂等）；携带资产摘要 + 完整结构化资产明细
- **请求参数**：payload 由 uplink.py `_register_payload` 构造：
  - `terminal_id`（默认 `WIN-<主机名净化>`，`_default_terminal_id`）
  - `terminal_type`（"windows"）、`hostname`、`client_version`
  - `os_info`（取 asset.os.text）
  - `hwinfo`（摘要：cpu_model/cpu_cores/mem_total_mb/disk_total_gb/gpu_info/os_arch，`_hwinfo_from_asset`）
  - `asset`（schema 1 明细：os/hostname/cpu/memory/disks/gpu/network/temps，`_asset_detail`；CIM 失败走 `_hwinfo_fallback` 兜底）
- **响应**：`{"ok":true,"registered":bool}`；非 2xx 记 last_error=`register_http_<code>`
- **触发**：启动 autostart、手动注册（BRG-038）、循环检测未注册时（指数退避，倍数上限 20）
- **代码出处**：uplink.py `register_once` / `_register_payload` / `_loop`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-09：①X-ETP-Token 校验收敛为多 token 模型（见 UPL-010；config token 幂等迁移入 terminal_tokens 表 label='default'，register 请求契约与终端侧不变）；②payload `asset` 字段现已入库（TBC-001 消项，api.py:339）（commit 4d2924b，代码实证）

#### UPL-002 心跳协议 `POST /api/v1/terminals/{tid}/heartbeat`
- **用途**：保活 + 命令通道拉取；响应 `commands[]` 逐条 spawn 线程执行（不阻塞心跳）
- **请求参数**：`{}`
- **响应关键字段**：`interval`（服务端覆盖心跳间隔，≥5 生效）、`report_interval`、`commands[]`
- **节奏**：默认 30s（`heartbeat_interval`），失败指数退避 ×20 上限（600s）；配置热更新（每拍重读配置）
- **代码出处**：uplink.py `_heartbeat_once` / `_loop`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-17：响应新增 `latest_version`（服务端当前发布版本，可 null；api.py:661，commit e24a236/ADR-042）——终端据此可触发自动更新拉包（GET /api/v1/client/manifest → SRV-086）；uplink 消费侧待终端版本跟进

#### UPL-003 指标上报协议 `POST /api/v1/terminals/{tid}/metrics`
- **用途**：心跳同拍附带一次 snapshot 映射上报（节流 ≥ 心跳间隔；复用 perf_service 采集不重复采样）
- **请求参数**：`_metrics_payload` 映射（cpu.percent / mem.used_percent / mem.available_percent / mem.used_mb / mem.total_mb / swap.used_percent / disks[卷容量+busy_percent] / volumes）；物理盘 busy 经 hwinfo 卷→盘映射挂载（覆盖服务端 disk_saturation 双判定）
- **响应**：`{"ok":true,"accepted":N,"bottlenecks":[...]}`
- **代码出处**：uplink.py `_report_metrics` / `_metrics_payload`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### UPL-004 命令回执协议 `POST /api/v1/terminals/{tid}/commands/{cid}/result`
- **用途**：命令执行结果回传（按 id 幂等：本地 executed 集合 + 服务端单次下发）
- **请求参数**：`{"ok":bool,"data":{...}}`（失败时 data 含 `error`；data 必带 `task_id` 的命令见 UPL-005）
- **重试**：3 次（`_RESULT_RETRIES`），间隔 1s；**409 终态不重试**
- **代码出处**：uplink.py `_post_result` / `_dispatch`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### UPL-005 命令：iperf_client
- **用途**：内置 iperf3 客户端打流（exe 打包随应用，每次任务独立 staging `%TEMP%/winhelper_iperf3_<ts>`，finally 必删）
- **args 契约**：`{"task_id":"IT-xxx"(必), "server_ip":"..."(必), "server_port":5201, "duration_sec":10(1~300), "mode":"tcp|udp"(默认tcp), "reverse":bool}`
- **回执 data**：成功 `{"task_id","summary":"tcp 887.8 Mbits/sec"|"udp 1.0 Mbits/sec jitter 0.155ms loss 0.0%","mode","server":"ip:port","duration_sec","result":{mbits_sec,jitter_ms,lost_percent|retransmits}}`；失败 error：`iperf_args_invalid|iperf_server_ip_missing|iperf3_not_bundled|iperf3_timeout|iperf3_exit_N: detail|iperf3_json_parse_failed|...`
- **代码出处**：uplink.py `_cmd_iperf_client` / `_parse_iperf_json`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### UPL-006 命令：net_probe
- **用途**：网关解析 + ping/TCP 探测（系统 ping，禁 raw socket）
- **args 契约**：`{"task_id":"..."(必), "targets":[{"host":"ip|_gateway","method":"ping|tcp","port":80}]}`
  - `host=="_gateway"`：`route print -4` 解析默认网关（0.0.0.0/0 活动路由、metric 最小、语言无关）
- **回执 data**：`{"task_id","results":[{host,port,method,ok,latency_ms,loss_pct,error?}],"gateway":"<网关IP>"}`
- **代码出处**：uplink.py `_cmd_net_probe` / `_ping` / `_tcp_probe` / `_default_gateway`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### UPL-007 命令：collect_logs
- **用途**：日志收集（**v1 hook 未实现**：结构已冻结待 log-inspector 日志引擎接入）
- **args 契约**：`args.ftp`（FTP 凭据）——**红线：用完即弃，禁止落盘/打日志/回显（ADR-019）**；v1 不读取不保留
- **回执 data**：固定 `{"task_id":...,"error":"collect_logs_not_implemented_v1"}`（ok=false）
- **代码出处**：uplink.py `_cmd_collect_logs`
- **状态**：在用（协议冻结，处理器待替换实现）
- **登记记录**：2026-09-09，代码实证

#### UPL-008 命令：ai_context
- **用途**：终端侧 AI 上下文收集（**暂缓**）
- **args 契约**：`{"task_id":...}`
- **回执 data**：固定 `{"task_id":...,"error":"ai_disabled"}`（ok=false）
- **代码出处**：uplink.py `_cmd_ai_context`
- **状态**：在用（固定禁用回执；启用需替换 handler）
- **登记记录**：2026-09-09，代码实证

#### UPL-009 命令分发总则（未知命令/幂等）
- **用途**：未知命令类型回执 `{"error":"unknown_command_type: <type>"}`（ok=false）；测试载荷用良性探测串、禁攻击样式串（ADR-018）
- **幂等**：本地 executed 集合（>500 截断至最近 400）；服务端命令单次下发不重发
- **代码出处**：uplink.py `_dispatch` / `COMMAND_HANDLERS`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### UPL-010 终端 Token 鉴权模型 v2（多 token，2026-09-09 新增）
- **用途**：终端上行（`/api/v1/terminals/*` 与 `/api/v1/ai/analyze`）的 X-ETP-Token 校验由单 config token 收敛为**多 token 模型**
- **语义**（代码出处：api.py:88-104 `ApiContext.check_terminal_token`，切换点 api.py:218）：
  1. config.json `terminal_token` 命中 → 放行（**不落表**，保持向后兼容零开销）
  2. SQLite `terminal_tokens` 表 `status='active'` 条目命中 → 放行，节流更新 `last_used_ts`（60s 内不重复写，防高频心跳刷库；非关键写失败不影响鉴权结论）
  3. 均未命中 → 401（invalid terminal token）
- **兼容性**：对现有终端完全兼容（config token 优先路径不变）；server-platform-dev 实测 WIN-Jun-office-PC 心跳 200 不打断
- **管理面**：token 生命周期经 SRV-067/068 维护（生成/轮换/停用/启用 + 审计）
- **表**：`terminal_tokens`（store.py:164，token 唯一索引 idx_terminal_tokens_token；config token 幂等迁移 label='default'）
- **状态**：在用
- **登记记录**：2026-09-09，代码实证（server-platform-dev 下发，commit 4d2924b）

#### UPL-011 桌面管控策略拉取 `GET /api/v1/terminals/{tid}/desktoppolicy/policy?revision={last}&mi={简报}`
- **用途**：终端按 revision 增量拉取四类策略（desktop_wallpaper / lock_screen / power_plan / idle_lock；轮询默认 120s 可配置，抖动 ±15%）
- **鉴权**：X-ETP-Token（终端 API）
- **请求参数**：query `revision`（int，首拉 -1）、`mi`（显示器简报 `idx,w,h,primary;idx,w,h,primary`，URL 安全；完整显示器信息经 UPL-013 上报）
- **响应（有更新）**：`{"ok":true,"revision":42,"server_time":epoch,"policies":{"desktop_wallpaper":{enabled,mode,per_monitor:[{monitor_index,wallpaper_id,match,checksum?}],rotation?},"lock_screen":{enabled,wallpaper_id},"power_plan":{enabled,plan,custom?},"idle_lock":{enabled,minutes,screen_saver_secure}}}`；无更新 `{"ok":true,"unchanged":true,"revision":42}`
- **枚举（冻结只增）**：match `exact|aspect_higher_res|default_fallback`（服务端匹配推荐算法执行）；mode `fill|fit|stretch|tile|center`（stretch=拼接图模式，终端默认）
- **代码出处**：终端侧实证 desktop-policy/desktop_policy.py `PlatformTransport.fetch_policy`(898)；服务端消费方 server/desktop_policy.py（**P1 在途，待代码实证**）
- **状态**：**契约冻结 v1.1 / 服务端未实现**（server-platform P1 在途，server-platform-dev 承接；终端侧传输层已实证）
- **登记记录**：2026-09-16，代码实证（终端侧 desktop-policy 9bdfcdf；契约 desktop-policy/docs/CONTRACT.md v1.1 冻结稿，team-lead 下发登记）

#### UPL-012 桌面管控壁纸下载 `GET /api/v1/terminals/{tid}/desktoppolicy/wallpaper/{wallpaper_id}`
- **用途**：按整数 id 下载壁纸二进制（终端缓存命中跳过；下载后 sha256 校验，校验失败删缓存抛 file_missing）
- **鉴权**：X-ETP-Token（终端 API）
- **请求参数**：路径 `{wallpaper_id}`（v1.1 全链统一整数 dp_wallpapers.id；终端缓存文件 `cache\{id}` 免扩展名，格式由文件头判定）
- **响应**：图片二进制（`X-DP-Checksum` 头携带 sha256 供终端校验）
- **代码出处**：终端侧实证 desktop_policy.py `PlatformTransport.download_wallpaper`(903)（timeout 120s；sha256 校验 906-911）；服务端消费方同 UPL-011（P1 在途）
- **状态**：**契约冻结 v1.1 / 服务端未实现**（同 UPL-011）
- **登记记录**：2026-09-16，代码实证（终端侧，同 UPL-011）

#### UPL-013 桌面管控执行结果回传 `POST /api/v1/terminals/{tid}/desktoppolicy/report`
- **用途**：一轮策略执行完成/失败/被拦截后的结构化结果上报（驱动服务端 dp_deliveries 状态 pending→delivered→applied/partial/failed/warn）
- **鉴权**：X-ETP-Token（终端 API）
- **请求参数**：`{"revision":42,"reported_at":epoch秒,"monitors":[{index,device,width,height,primary,dpi,offset_x,offset_y}],"virtual":{x,y,width,height},"session_type":"console|rdp","results":[{policy,ok,detail?|error?}]}`（policy 枚举=四类策略；reported_at/时间戳一律 epoch 秒 int）
- **error.code 枚举（冻结只增不改）**：`blocked_by_security`（终端安全软件拦截壁纸变更，ADR-004 自检三件套不过）/`no_admin`（锁屏 HKLM 缺管理员）/`rdp_skipped`（RDP/非 console 会话跳过本地应用）/`file_missing`（壁纸文件下载失败或校验不过）/`apply_failed`（重试 3 次后仍失败）/`rollback_ok`（电源计划失败已按备份还原）/`rollback_failed`（还原也失败，最高告警）
- **重试语义**：失败重试 3 次（2s/8s/32s 指数退避）；`rdp_skipped` / `blocked_by_security` 不重试直接上报
- **代码出处**：终端侧实证 desktop_policy.py `PlatformTransport.report`(913) / `Engine._report_with_retry`(1051) / `Engine.tick` payload 构造(1030-1046)；服务端消费方同 UPL-011（P1 在途）
- **状态**：**契约冻结 v1.1 / 服务端未实现**（同 UPL-011）
- **登记记录**：2026-09-16，代码实证（终端侧，同 UPL-011）

#### UPL-014 命令：pc_apply_policy（2026-09-17 新增）
- **用途**：自动开关机平台下发执行面（SRV-097 dispatch 的终端执行端；server 侧 source=powercontrol，timeout_sec=604800）
- **args 契约**：`{"policy_id":"<hex32>","op":"apply","boot":{"enabled":bool,"mode":"daily|weekly|single|disabled","time":"HH:MM","weekdays":[7x0/1 周一~周日]?,"date"?}?,"shutdown":{enabled,mode,time,date}?}`
- **回执 data**：`{policy_id,op:"apply",ok,steps:{...},error?:截断200字}`（成功落 policy_state 供「自动开关机」页展示当前生效策略）
- **执行链**：复用 power-control P1a/P1b 引擎（`apply_policy`：写前快照→写入→回读校验；schtasks EyeTermAutoShutdown）；无人值守提权（UAC）限制如实回执（power-control ADR-009）
- **代码出处**：uplink.py `_cmd_pc_apply_policy`(701-716，延迟 import power_control)；主仓统筹 a099448（协议 main 定稿，server-platform 同款）
- **状态**：在用
- **登记记录**：2026-09-17，代码实证（api-registrar-dev 对账发现补登——dispatch 服务端批次漏登时随批补全协议侧）

#### UPL-015 命令：pc_diag（2026-09-17 新增）
- **用途**：只读诊断命令（SRV-109 下发；回执经 api.py:491 钩子存档 pc_diag_records，status pending→completed/failed，result_json ≤256KB）
- **args 契约**：`{"kind":"pc_diag"}`
- **回执 data**：`{save_class, password_state, rtc_readback, last_apply.attempts[], log_tail, client_version}`
- **白名单**：命令类型白名单 api.py:1001（pc_apply_policy, pc_diag）
- **代码出处**：server-platform 5f0ba2f（api.py :491 回执钩子 / :1001 白名单 / :1037 入队）；终端 handler uplink.py `_cmd_pc_diag`(746-747，主仓 commit **3dc7e8b**/客户端 4.1.2 交付；另 pc_diag 日志取数通道 uplink.py:136)
- **状态**：在用（**双端生产实证闭环**：服务端 deploy_20260917_185433 + 终端 4.1.2 已装 WIN-13F-xx-3〔18:53，client_version 实证〕，diag#1 completed 全字段回执存档；unknown_command_type 仅存在于 4.1.1 及更早版本——新终端装 4.1.2 后 pc_diag 开箱可用）
- **登记记录**：2026-09-17，代码实证（server-platform-dev 实施，commit 5f0ba2f/ADR-040 增补，按常设约定显式列清单知会，api-registrar-dev 复核登记）> 更新 2026-09-17（晚 2）：**终端执行面闭环**——4.1.2 客户端交付（主仓 3dc7e8b，含 handler + pc_diag 日志取数），生产 diag#1 双端实证 completed（attempts/RTC 读回/save_class/password_state 全字段），server-platform-dev 显式知会、api-registrar-dev 对账确认（代码实证 uplink.py:746-747）

#### UPL-016 命令：power_action（2026-09-18 新增）
- **用途**：中心发起立即重启/关机（SRV-113 下发；终端倒计时知会弹窗、不提供本地取消，撤销走 UPL-017）
- **args 契约**：`{"action":"shutdown"|"restart","delay_sec":0-3600,"force":bool}`（服务端在线守卫 409 后才入队）
- **执行链**：终端 handler uplink.py:764+（延迟 import power_action.py——**高危红线：命令串仅由受控白名单参数构造**，main 定稿 2026-09-18）；主仓 0c5f1e0（客户端 4.1.5 重打包，power_action + wol_relay 一包双能力）
- **状态**：在用（双端就绪：服务端 85ae733/终端 4.1.5）
- **登记记录**：2026-09-18，代码实证（server-platform-dev 服务端 85ae733/ADR-044 + power-control-dev 终端面 0c5f1e0，api-registrar-dev 复核登记）

#### UPL-017 命令：power_action_abort（2026-09-18 新增）
- **用途**：撤销 pending 关机/重启（SRV-114 下发；终端执行 shutdown /a 语义）
- **args 契约**：空/最小（以 handler 为准）
- **回执 data**：rc=1116 为「无 pending」正常业务态（服务端按业务态处理非失败）
- **代码出处**：uplink.py power_action/abort handler（0c5f1e0 同批）；服务端 api.py:1673-1686
- **状态**：在用（双端就绪同 UPL-016）
- **登记记录**：2026-09-18，代码实证（同 UPL-016）

#### UPL-018 命令：wol_relay（2026-09-18 新增）
- **用途**：指派在线终端代发 WoL 魔术包（SRV-115 下发；跨网段中继主通道）
- **args 契约**：`{"mac":"<归一大写 12hex>","broadcast":"IPv4 点分四段","port":1-65535}`
- **回执语义**：终端 ok=true 仅代表 **UDP 已发送（无确认）**——唤醒成功唯一定案口径=目标机 last_seen 恢复（服务端 SRV-118 attempts phase=confirm/giveup 留痕）
- **代码出处**：uplink.py wol_relay handler（0c5f1e0）；服务端 api.py:1688-1715
- **状态**：在用（双端就绪同 UPL-016）
- **登记记录**：2026-09-18，代码实证（同 UPL-016）

---

## 四、外部依赖接口

#### EXT-001 算力平台 LLM（OpenAI 兼容）
- **用途**：AI 智能分析（上下文聚合 + FAULT_PATTERNS 注入 + 模型链调用）
- **端点**：`POST {llm.url}/v1/chat/completions`（base 已含路径则不重复拼接）
- **鉴权**：`Authorization: Bearer {llm.api_key}`（api_key 加密落库 settings，出接口脱敏）
- **请求参数**：`{"model":"<llm.model>","messages":[{role,content}],"temperature":0.3}`
- **响应消费**：`choices[0].message.content`、`model`
- **调用策略**：ai.py `llm_chat` 超时+1 次重试；`llm_chat_chain` 主模型（llm.model）→ 备选（llm.model_fallback），切换条件=超时/连接失败/5xx/429/模型不可用（can_fallback=True），**4xx 鉴权/参数类不切换**；返回 `tried_models` 实际尝试链
- **配置键**：settings `llm.url` / `llm.api_key`（SENSITIVE）/ `llm.model` / `llm.model_fallback`（后两个无 DEFAULTS 预置，按需配置）
- **调用方**：api.py `run_ai_analysis`（SRV-023/055）
- **代码出处**：server-platform/server/ai.py `llm_chat` / `llm_chat_chain`；api.py `run_ai_analysis`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证 > 更新 2026-09-09：新增消费方 SRV-063 连通性测试（`GET {llm.url}/v1/models`，Bearer，8s 超时，不消耗对话额度；api.py `_llm_test`，commit 4d2924b）

#### EXT-002 vsftpd FTP 文件上传
- **用途**：终端日志/文件上传通道（deploy.py 安装配置 vsftpd 并持久放行端口）
- **端口**：控制 18121（`ftp.listen_port`），被动段 18122-18141（`ftp.pasv_start`/`ftp.pasv_end`）
- **账号**：`ftp.username`（默认 eyetermftp）、`ftp.password`（SENSITIVE，加密落库）；`ftp.enabled` 开关
- **状态探测**：`systemctl is-active vsftpd`（storage.py `ftp_status`）
- **闭环**：终端上传后经 SRV-049（uploads 登记，source=api）；服务端可经 SRV-034 扫描补登记（source=scan）
- **代码出处**：server-platform/server/storage.py；settings.py DEFAULTS
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### EXT-003 SMB 存储
- **用途**：日志存储后端（挂载到 `storage.root_dir`，默认 /data/terminal-platform/storage）
- **配置**：settings `smb.mount_cmd`（挂载命令字符串，原样执行，超时 60s）
- **状态探测**：`findmnt -n -o SOURCE <root>`（storage.py `mount_status`）
- **触发**：控制台 SRV-033（storage/mount）
- **代码出处**：server-platform/server/storage.py `mount_status` / `exec_mount`
- **状态**：在用（挂载收尾待 server-platform-dev）
- **登记记录**：2026-09-09，代码实证

#### EXT-004 iperf3 服务端（平台侧）
- **用途**：网测/压测服务端（每次任务独立端口 + `-s -1` 单会话自动退出；超时强杀；stdout 日志收集）
- **端口段**：18200-18299（settings `iperf.port_start`/`iperf.port_end`；bind 试探 + running 槽位查重）
- **二进制**：`iperf.path`（默认 /usr/bin/iperf3）
- **服务器 IP**：settings `iperf.server_ip`（下发 iperf_client args.server_ip）
- **防火墙要求**：18200-18299 **TCP/UDP 成对放行**（2026-09-09 实测教训：缺 UDP 会导致 iperf3 UDP 测试客户端挂起超时）
- **测试类型**：bandwidth_tcp / udp_jitter / latency_gateway / latency_server（iperf.py `TEST_TYPES`）+ stress_tcp/stress_udp（net-doctor spawn_server 路径）
- **代码出处**：server-platform/server/iperf.py `_allocate_port` / `launch` / `spawn_server` / `cancel`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### EXT-005 NTP 校时探测（net-doctor 连通性节点）
- **用途**：连通性测试节点之一「温州总院」，`w32tm /stripchart /computer:<host> /dataonly /samples:<n>` 解析偏移样本
- **目标**：settings/net-doctor 节点表 `{"key":"ntp","method":"ntp","target":"ntp.eye.ac.cn"}`（net-doctor 节点配置默认值，可在 app_config.json netdoctor.nodes 调整）
- **回执 error**：`ntp_timeout` / `ntp_no_samples`
- **代码出处**：net-doctor/net_service.py `_ntp_probe`（节点表常量；已随子系统合入主应用，commit 628c210）
- **状态**：在用
- **登记记录**：2026-09-09，代码实证（net_service.py 定义）> 更新 2026-09-09：net-doctor 合入主应用，转「在用」

#### EXT-006 画方 NAD 准入 HTTP API（网络准入控制）
- **用途**：终端准入台账查询（IP↔MAC↔接入交换机端口定位证据）——net-doctor IP 冲突检测的 admission_log 数据源 + 端口级证据来源
- **端点**：base `https://<nad-host>:9002`（**自签证书，调用方忽略证书校验**；真实 base_url 与凭据已登记于系统管理第三方接口 id=2，见 SRV-064 体系，运维经 GET /api/v1/console/sysadmin/third-party 查阅）
  - `POST /httpapi/term/get` — 终端准入台账查询
  - `POST /httpapi/term/dict` — 字典接口（已实测 errno=0）
  - `POST /httpapi/spanbanip/get|db|delete` — 镜像封禁查询/库/解封
- **鉴权**：HMAC-SHA256 签名五字段请求体（`appkey`/`sign`/`time`/`nonce`/`enctype`/`version`；`sign = hash_hmac(appsecret, "appkey=..&nonce=..&time=..")`）
- **请求参数**（term/get）：`data: {"where":…, "curpage":N, "limit":≤10000}`
- **响应**（term/get）：`{"total":N,"list":[{oid,name,ou,ttype,warn,block,online,reginfo,listinfo,macs:[{ips:[…],macports:[{nasname,nasif}]}]}]}`（macports 即接入交换机 nasname/nasif 证据链）
- **实测**：total=1588 台（main 生产实测）
- **消费方**：net-doctor `run_ipconflict_result`（BRG-042）admission_log 数据源接入——server-platform-dev 实施中
- **代码出处**：server-platform/server/nad_client.py（接入客户端，commit a5156cc）；消费方 api.py `_enrich_ipconflict_admission`（api.py:1163-1200）→ BRG-042 ipconflict 端点（api.py:383 注入）
- **状态**：在用（API 已实测联通；admission_log 数据源接入已落地）
- **登记记录**：2026-09-09，main 下发（画方《新版本NAD对外接口说明》20251231 版 37 页解析 + 生产实测）> 更新 2026-09-09：admission_log 接入落地，补代码实证 nad_client.py（commit a5156cc）。**文档-实际差异适配 3 处（实测知识，逐条登记）**：①`list` 字段实际为 dict 形态（键为序号字符串）而非文档所述数组——`_norm_macs` 归一为列表（nad_client.py:94-95）；②macs 条目实际为对象（`{"0": {"mac":.., "ips": {"0":{..}}, "macports": null|[..]}}`）且 macports 可为 null（nad_client.py:88-92）；③单页上限实际 1000 而非文档 limit≤10000——实测 1588 台需翻页 2 页，`NAD_PAGE_LIMIT=1000` + `NAD_MAX_PAGES=50` 翻页防御（nad_client.py:28-29）；配置经 `NAD_CONFIG_ID=2` 读第三方接口登记（appkey/app_secret），全量缓存 `_cache`，签名 hmac_sha256_hex(appsecret,"appkey=..&nonce=..&time=..") > 更新 2026-09-10：AI 诊断批次回归核对（net-doctor a892f62 / 主应用 bd965ae，diff 仅 bridge.py/net_service.py/web 前端）——nad_client.py 与 api.py 消费链（`_enrich_ipconflict_admission` api.py:1260/383）未被触碰，在位无回归（代码实证）> 更新 2026-09-17：**模块形状变更（ADR-043，commit b8a086b）**——`nad_find_by_ip`/`nad_find_by_mac` 新增 `full=False` 可选参数（nad_client.py:216/236，**默认行为不变=ADR-024 冻结形状**，ipconflict 证据链消费者无感）；新公开 `nad_evidence_full`(:254，reginfo 完整 dict 扩展块) 与 `nad_cache_ts`(:282，缓存时点)；首个 full=True 消费方 = SRV-089 `_thirdparty_nad_block`(api.py:1609-1611) > 更新 2026-09-17（晚 2）：**生产实测字段路径增补（commit 983e7bc，ADR-043 follow-up，1547 台 term/get 实测）**——①**macports 形态与 ips 相同**：数字键 dict（`{"0":{nasoid,nasif,nasname,manip}}`）非文档暗示的 list，`_norm_macs` 曾漏归一（dict 迭代出键字符串被过滤）致接入位置恒空，已根修（nad_client.py:89-108）——**所有 nad 消费者注意**（ipconflict 证据链 BRG-042/SRV-055 未来透出 macports 亦受益）；②顶层字段存在：`owner`={name,uuid}（uuid=工号；示例值脱敏不录）、`onlts`（最后在线 epoch）、`expired`/`first`/`listinfo.stat`——现已入 nad_evidence_full 透出 owner/owner_name/owner_uuid/onlts（nad_client.py:261-283，前端防御渲染）；③`reginfo.stat=2` 实测语义「已注册」（**单点实证**，完整枚举映射仍缺——如获完整枚举文档请补登）；④**per-port 在线态与 macports→IP 归属映射在 term/get 中不存在**（画方 UI 在线点应为其它接口维度）——后续索取规格时建议一并问清；⑤顺带更正：前述 `_thirdparty_nad_block` 行号 3645d94 后实测为 :1615-1617

**附：文档-实际偏差清单汇总（2026-09-17 增设，供厂商一次性书面确认）**

> 背板：产出自 2026-09-09 首次接入（37 页说明解析+生产实测）与 2026-09-17 1547 台 term/get 实测（983e7bc）；与《新版本NAD对外接口说明》20251231 版逐条对照。编号 **DEV-001~008**，后续新增偏差续号；规格索取函直接整节引用。
> **状态约定**：每条初始【待厂商确认】；厂商书面确认（邮件/工单/对接记录，来源落备注）后转【已确认】；确认前台账语义仍以实测为准。

- **形态偏差（文档暗示形态 ≠ 实际，消费者必踩）**：
  - **DEV-001**【待厂商确认】`list` 实际为 dict 形态（键=序号字符串）而非文档数组——`_norm_macs` 归一（nad_client.py:94-95）
  - **DEV-002**【待厂商确认】`macs` 条目实际为对象 `{"0":{mac,ips,macports}}` 且 `macports` 可为 null（nad_client.py:88-92）
  - **DEV-003**【待厂商确认】`macports` 实际为**数字键 dict**（`{"0":{nasoid,nasif,nasname,manip}}`，与 ips 同构）而非文档暗示 list——漏归一致接入位置恒空，983e7bc 根修（nad_client.py:89-108）
  - **DEV-004**【待厂商确认】单页上限实际 **1000** 而非文档 limit≤10000——实测 1588 台需翻页（`NAD_PAGE_LIMIT=1000`+`NAD_MAX_PAGES=50` 防御，nad_client.py:28-29）
- **语义缺口（缺文档/待厂商确认）**：
  - **DEV-005**【待厂商确认】`reginfo.stat` 完整枚举缺失——仅 stat=2「已注册」单点实证
  - **DEV-006**【待厂商确认】macports→IP 归属映射在 term/get 中不存在
  - **DEV-007**【待厂商确认】per-port 在线态在 term/get 中不存在（画方 UI 在线点应为其它接口维度，需厂商指认端点）
  - **DEV-008**【待厂商确认】文档未载但实测存在字段：`owner`={name,uuid:工号}、`onlts`（最后在线 epoch）、`expired`/`first`/`listinfo.stat`

#### EXT-007 火绒终端安全管理系统 API v1（终端安全维度，调研阶段）
- **用途**：EyeTerm「终端安全」维度数据源（方案场景：S1 终端安全状态聚合 / S2 高危漏洞风险 KPI / S3 病毒事件看板 / S4 分组-设备映射同步 / S5 软件资产统计；S6 远程处置为**破坏性**接口，EyeTerm 侧默认硬门禁禁用 `enable_tasks=False`，开启须另行审批立项）
- **服务方**：火绒终端安全管理系统控制台（独立安全产品，非 EyeTerm 组件）
- **端点**：base `https://<hr-host>:8080`（HTTPS 自签证书；真实地址经 settings `huorong.base_url` 配置后生效，不出台账）。**端点计数口径差异**：huorong-dev 下发称 14、实列 15 个路径（group 5 + clnts 8 + task 1 + swinfo 1），以路径清单为准，官方计数口径见 TBC-006： > 更新 2026-09-15：**计数口径定论**（huorong-dev 回查官方文档目录）——官方文档操作章节 17 个条目（分组 3.1.1~3.1.5 + 终端 3.2.1~3.2.8 + 任务 3.3.1~3.3.3 查杀/隔离/通知 + 软件 3.4.1），任务三操作**共用 /api/task/_create 路径仅 type 不同**；按唯一路径口径 = 15，本条目登记正确；下发「14」系任务摘要少计非官方口径；方案文档 §1 已同步修正（huorong_integration_plan.md:11）
  - 分组（5）：`POST /api/group/_list`（全部分组树 group_id/parent_group/group_name）/ `_info`（group_id）/ `_create`（parent_group+group_name）/ `_delete`（group_id）/ `_rename`（group_id+group_name）
  - 终端（8）：`POST /api/clnts/_online`（limit≤200/offset，在线 MAC）/ `_list`（全量终端：client_id/local_ip/connect_ip/mac/client_name/computer_name/group_id/os_version/version/**definitions 病毒库日期**/is_online/last_connect_time，分页 limit≤200）/ `_rename` / `_group`（批量移动分组）/ `_info`（clients[]/mac[]）/ `_info2`（**v2.0.6.0+**，options: hardware/software/assets/netconf）/ `_leak`（高危漏洞未修复终端 + 全局 KPI all_client/risk_client）/ `_virus_events`（type 0=按终端/1=按分组/2=全量，begin_time/end_time/limit/offset，返回 count+success/fail/ignored/trusted）
  - 任务（1，**破坏性**）：`POST /api/task/_create`（type=quick_scan/full_scan/**custom_scan(v2.0.8.0+)**/netctrl 隔离/message 通知；clients[] 或 groups[] + param）
  - 软件（1）：`POST /api/swinfo/_search`（view.begin/count + order + fuzzy_query + groupby: software.list/softwareVer.list/client.list + ostype: Windows/Macos/Linux_Desktop/Linux）
- **统一规范**：HTTP POST + JSON；响应信封 `{errno, errmsg, data}`；errno：0=成功 / 1=认证失败（**不重试**，连续 3 次禁用同步防锁死）/ 2=参数错误（不重试）/ 3=服务端内部错误（指数退避重试 ≤2）/ 4=API 未授权（不重试，AK 权限配置问题）；HTTP 层超时 15s、网络类重试 ≤2
- **鉴权**（2026-09-15 实测定稿，**修订原官方文档描述**）：认证**全部走 URL 参数** `{HOST}/{path}?ak={AK}&expires={ts}&sign={sig}`——**无 Authorization 头、无 Content-MD5 头，官方文档 Header 模式实测无效**（31 变体排查根因，详见登记记录）。待签串 5 行：`AK \n expires \n POST \n Content-MD5 \n path`——①`Content-MD5 = base64(md5(请求体二进制))`（对发送字节原文计算，仅入待签串不发头）；②**资源路径不带前导斜杠**（`api/group/_list`）；③body 为**紧凑 JSON**（`separators=(',',':')`）；④签名 = `urllib.parse.quote(base64(hmac-sha1(SK, 待签串)))`，**quote 默认 safe='/'**（+/= 转义、斜杠保留）；⑤expires = `now + 86400`（24h 窗口，服务端时钟偏移 1s 可忽略）；⑥官方文档「子资源按字典序」对应 query 传参形态，实测参数全走 JSON body，该要素未触发
- **凭据**：AccessKeyId（标识符，明文落 settings `huorong.ak`）/ Secret（**SENSITIVE**，settings `huorong.secret` SecretsBox AES 加密、接口脱敏零回显）——**凭据零落盘不入台账**（红线）
- **调用方式**：仅服务端发起（规划 `server-platform/server/huorong.py` HuorongClient：签名/分页/errno 归一/重试/任务门禁；终端与前端不直连火绒）。示意（实测形态，集成后）：
  `curl -X POST "https://<hr-host>:8080/api/group/_list?ak=<AK>&expires=<ts>&sign=<quoted-sig>" -H "Content-Type: application/json" -d '{}'`
- **消费方（规划，均未实现）**：settings `huorong.*`（base_url/ak/secret/enabled/sync_interval_sec/tls_fingerprint——SPKI SHA-256 pin 防中间人）；store `hr_groups/hr_clients/hr_leak_stat/hr_sync_log` 缓存表；控制台 `/api/v1/console/huorong/*`（status/groups/clients/risk/virus-events/sync，届时按 SRV 组登记）
- **代码出处**：客户端雏形 server-platform/docs/attachments/huorong_api_client_draft.py（11.5KB，零凭据）；对接方案 server-platform/docs/huorong_integration_plan.md v1.0（官方《火绒终端安全管理系统API说明文档》API v1 解析 + §5 实测记录）
- **状态**：**试点通过/已实证**（两对凭据 HTTP 200 + errno=0；试点全量拉取 **89 分组 + 710 终端**；任务类接口**零调用**红线维持）；EyeTerm 服务端集成仍为规划（待 P1 `huorong.py` 正式化后对账补代码实证）
- **登记记录**：2026-09-15，huorong-dev 下发（官方 API v1 文档解析 + 实测，方案文档与客户端雏形已核对入仓）> 更新 2026-09-15（晚）：**签名谜题破案，试点打通**——①根因定性：此前「凭据被拒」假设不成立，真因 = 官方文档 4 处关键歧义（Header 模式无效/待签路径不带前导斜杠/quote 默认 safe='/'/expires 窗口 86400 非 300s），经用户提供官方参考脚本 `api测试.py` 实测破案（排查 31 变体 + 排除凭据/时钟/代理/TLS 因素； errmsg="Authentication failed" 为纯签名错零差异）；②实测算法已固化方案文档 §7.1 + draft 客户端按实测算法重写（huorong_api_client_draft.py `_sign`/`_post`）；③试点实测数据语义：分页终止 `offset >= data.total`（data 内层 total）、**local_ip 为主取用字段**（local_ip≠connect_ip 仅 2.8%，connect_ip 跨网段/代理上报仅作链路参考）、MAC 唯一 698/710（重复取 last_connect_time 最新，归一化作关联键）、分组名 i18n 键残留需规整（`i18n:db_groups_name:*`）、Win7 旗舰版 59 台 EOL 风险；脱敏样例归档 docs/attachments/huorong_pilot_sample_20260915.json——**待代码实证**（P1 `huorong.py` 正式化落地后对账补证）> 更新 2026-09-19：**字段消费落地**（commit 8f9d5ad，ADR-033 增补三，huorong-dev 按常设约定知会，api-registrar-dev 复核入档；逐字段核实自官方 API v1 文档 §3.2.1/§3.2.5）——①`/api/clnts/_list` 基础响应新增消费 3 字段（均为 Unix 秒级时间戳）：`first_appear_time`→平台 first_seen（首次上线）/ `last_off_time`→last_off（上次关机）/ `this_on_time`→this_on（本次开机）；`last_seen_time`（最后上线时间）官方存在但未新增消费（卡内既有「最近连上火绒」= last_connect_time 最后通讯时间，任务口径视为已有；四时间字段同在 _list 响应，无需 _info2）；②`/api/clnts/_info2`（v2.0.6.0+，请求 options=["assets"]）：`data.list[].assets` 为 **name/value 键值对数组、无固定 API 字段名**（登记字段名由火绒控制台管理员配置）——EXT 纪律不猜字段名，平台 hr_client_assets 表 assets_json **全量保留原始 name→value**；③HuorongSyncer 每轮同步新增 _info2 分批调用（**50 client_id/批**，options=["assets"]），失败不阻断主同步（返回 assets_sync 状态）；控制台仍只读缓存、无实时透传变化。消费落点：SRV-108 context 响应扩展（first_seen/last_off/this_on + assets 块）与 SRV-138 资产定位火绒条目（registration 白名单投影）

---

## 五、废弃/规划接口

#### DEP-001 `ApiContext.login`（内存会话版控制台登录）— 废弃
- **原用途**：明文口令对照 config.json `console_password` + 内存 dict 会话
- **废弃原因**：等保三级登录改造，改走 auth_upgrade.py `authenticate`（PBKDF2 + SQLite 持久会话 + 失败锁定 + IP 限速 + 审计），即 SRV-002
- **代码出处**：api.py `ApiContext.login`（**全仓无调用方**，grep 已证）
- **状态**：废弃（替代：SRV-002）
- **登记记录**：2026-09-09，代码实证

#### DEP-002 `ApiContext.check_console`（内存会话校验）— 废弃
- **原用途**：内存 dict 校验控制台 token
- **废弃原因**：改走 auth_upgrade.py `resolve_session`（api.py dispatch 控制台分支）
- **代码出处**：api.py `ApiContext.check_console`（**全仓无调用方**）
- **状态**：废弃（替代：X-ETP-Console-Token + auth.resolve_session）
- **登记记录**：2026-09-09，代码实证

#### DEP-003 `ApiContext.login_rate_ok`（内存滑动窗口限速）— 废弃
- **原用途**：登录 IP 限速（60s 窗口 10 次）
- **废弃原因**：限速内建于 auth_upgrade（429 retry_after）
- **代码出处**：api.py `ApiContext.login_rate_ok`（**全仓无调用方**）
- **状态**：废弃（替代：auth_upgrade 内建限速）
- **登记记录**：2026-09-09，代码实证

#### DEP-004 旧日志诊断路由 5 条（winhelper 主应用）— 已删除
- **原路径**：`/api/analyze`、`/api/events`、`/api/faults`、`/api/log-types`、`/api/knowledge`
- **废弃原因**：菜单合并（日志诊断独立项目 log-inspector 接管），service.py 头部注释明确「旧 handle_analyze/handle_events/handle_faults/handle_log_types/handle_knowledge 随 /api/analyze 等旧路由一并移除」；当前 bridge.py ROUTES 无这些路径
- **替代**：/api/loginspector/* 8 条（BRG-001~008）
- **代码出处**：service.py 模块 docstring；bridge.py `ROUTES`（缺失佐证）
- **状态**：废弃（已删除，保留记录）
- **登记记录**：2026-09-09，代码实证

#### DEP-005 netdoctor 本地桥接挂载 — 已完成（转正式条目 BRG-040~049）
- **内容**：bridge.py ROUTES 挂载 `/api/netdoctor/*` 10 条（BRG-040~049），index.html 导航新增 data-tab="netdoctor"（性能分析之后），app.js switchTab 守卫
- **负责**：net-doctor-dev（net_service.py + web/netdoctor.js 已备，E2E/smoke 齐备）
- **状态**：已完成（本条保留作历史记录）
- **登记记录**：2026-09-09，代码实证（net_service.py 定义 + bridge.py ROUTES 缺失佐证）> 更新 2026-09-09：已合入主应用 commit 628c210（main 下发，bridge.py:94-103 已代码实证），BRG-040~049 转「在用」

---

## 附：待确认项（TBC）

| 编号 | 事项 | 说明 |
|------|------|------|
| TBC-001 | register 的 `asset` 字段透传 | **【已消项 2026-09-09】**修复：api.py register 分支补传 `asset=data.get("asset")`（api.py:339，缺键安全为 None）+ store.py 序列化 try/except (TypeError, ValueError) 降级加固（store.py:256-261）；真实终端 WIN-Jun-office-PC asset_detail NULL→1 验证通过。修复 commit 4d2924b（server-platform-dev 修复、team-lead 通知消项，代码实证） |
| TBC-002 | settings 无预置键 | `llm.api_key`/`llm.model_fallback`/`iperf.server_ip`/`netdoctor.route_nodes`/`ftp.password` 不在 settings.py DEFAULTS（按需 set 后生效），GET settings 时未配置键不出现或为默认值 |
| TBC-003 | BRG 各 handler 响应字段全集 | 39 条已挂载路由的响应以 success/error 公共字段 + 关键字段记录；逐字段全集可在联调对账时以 service 文件 handler 返回值补录 |
| TBC-004 | config.json 键清单 | app.py dev 默认：port/terminal_token/console_password/session_ttl_hours/report_interval/retention_days/bottleneck_dedup_min/data_dir；生产 config.json 实际键以部署实例为准（口令已移除，登录走 console_auth.db） |
| TBC-006 | 火绒 API 端点计数口径 | 【**全部关闭 2026-09-15（晚）**】①计数核对：官方文档 17 个文档化操作（分组 5 + 终端 8 + 任务 3 + 软件 1，任务三操作共用 /api/task/_create 仅 type 不同），唯一路径口径 = 15，台账登记正确；②签名传参 A/B：**确认全部走 URL query 参数**（?ak=&expires=&sign=），官方 Header 模式实测无效；③urlencode 形态定稿：quote(base64(hmac-sha1)) 默认 safe='/'（+/= 转义、斜杠保留），待签路径不带前导斜杠，expires=now+86400，body 紧凑 JSON。全要素见 EXT-007 鉴权段与方案文档 §7.1；遗留仅「P1 huorong.py 落地后对账补代码实证」（随 EXT-007 跟踪） |
| TBC-007 | 桌面管控服务端侧实证缺口 | UPL-011~013 服务端实现（server/desktop_policy.py，P1 在途）落地后对账补代码实证并转「在用」；另 CONTRACT.md §2 console 组 8 端点（/api/v1/console/desktoppolicy/*：overview/wallpapers CRUD/policies/publish/deliveries，admin 403+审计语义）随 P1 验收另行登记，本批未登记 |

## 附：对账约定

- 本台账对账基线 commit：工作区当前版本（bridge.py / uplink.py 有未提交修改，以台账登记时点代码为准）> 更新 2026-09-09：net-doctor 合入基线 commit 628c210（bridge.py ROUTES 含 /api/netdoctor/* 10 条）> 更新 2026-09-09：系统管理模块基线 commit 4d2924b（sysadmin 13 路由 + 多 token 模型 UPL-010 + session-info，代码实证 api.py:88-104/243-248/764-773/829+）> 更新 2026-09-09：知识库模块语义修正基线 commit 31f8e1d（KB 9 端点 ADR-022 语义 + route-nodes source 字段，代码实证 api.py:1396-1461/1369-1377/383-397、kb_store.py:43/59/157）> 更新 2026-09-09：终端 AI 智能诊断基线 commit 8aaa64e（SRV-070 diagnose + SRV-071 单条详情 + trigger=terminal_diagnose，代码实证 api.py:419-445/784-790/1160+，ADR-023）> 更新 2026-09-09：画方 admission_log 接入基线 commit a5156cc（nad_client.py + _enrich_ipconflict_admission，EXT-006 补代码实证与 3 处文档-实际差异，ADR-024）> 更新 2026-09-09：交换机管理基线 commit f031152（SRV-072~077 sysadmin 组，ADR-026，代码实证 api.py:1059-1133、settings.py:15/31、store.py:170-177）> 更新 2026-09-10：AI 诊断批次基线 net-doctor a892f62 / 主应用 bd965ae（BRG-050 补登+mode 双模式扩展、BRG-051 新增、BRG-040 ai_personal_json 写配置；bridge.py `call` body 双参透传仅 ai-diagnose 一条，bridge.py:128-133；EXT-006/BRG-042 回归核对无变化）> 更新 2026-09-10（晚）：AI 诊断收尾批次基线——server-platform 子仓 47de462/91b2fe6（ADR-027 截断重做+证据硬约束，SRV-070）、257ed21（ADR-028 判定精化 SRV-051 + ADR-029 深度检测 SRV-078/079）、25d48e2（静态资源 ETag 协商缓存，SRV-042/043）；net-doctor 子仓 c7219c7/1f60f95（BRG-051 used_key 三态+防假阳性、BRG-050 URL 归一化+提示词加固、BRG-042 Find-NetRoute 路由解析）；主仓同步 a81bd1c/3069ea7（net-doctor 指针前进 1f60f95）> 更新 2026-09-10（晚 2）：server-platform 9e604a7+cc34bae（deep-engine ADR-029 双端点归属修正 + verdict.sources ARP 失败同步）；net-doctor 深度检测本地转发 2 条 BRG-052/053（conflict-deep-start/poll，query 传参非 body；NET_ROUTES 已定义、主应用 bridge.py ROUTES 挂载待同步——待办移交主应用集成）> 更新 2026-09-10（晚 2）：BRG-054 conflict-ai-reanalyze 入账（主应用 b344bbc / net-doctor acee817）；BRG-052/053 撤「挂载待同步」（主应用 40abd3f hotfix 挂载 bridge.py:102-103，v8 内按钮不可用根因即漏挂载）——主应用内 netdoctor 冲突检测三路由双端就绪> 更新 2026-09-11：路由追踪 AI 分析批次基线——net-doctor a8aa49e（trace-ai-analyze 对接）+ f22e2bf（AI 诊断历史 JSONL 持久化三路由）；server-platform 2cb39d2（ADR-031 routetrace 分支）+ a8a6b95（ADR-030 ipconflict 聚合分支补登记）；主仓 c4495bc/916e791（bridge 挂载 4 条，bridge.py:106-109）；**遗留缺陷：BRG-055↔SRV-055 键名不一致（data vs context），routetrace 聚合走回退，待 net-doctor-dev/server-platform-dev 定责修复****遗留缺陷：BRG-055↔SRV-055 键名不一致（data vs context），routetrace 聚合走回退，待 net-doctor-dev/server-platform-dev 定责修复**【已消项 2026-09-11（晚）：net-doctor 4679b11 / 主应用 e6452e5，extra 改 context{target,hops}，缺陷关闭；注意 main 此前重建的 exe 在热修提交前，下次换装前需全量重建一次（team-lead 换装时执行）】> 更新 2026-09-15：火绒对接调研批次基线——EXT-007 新增（火绒终端安全 API v1，huorong-dev 下发，官方文档解析 + 连通性实测；签名认证待凭据修复，消费方 huorong.py//console/huorong/* 均为规划未实现，落地后对账补代码实证）> 更新 2026-09-15（晚）：火绒试点打通批次基线——签名谜题破案（官方参考脚本 api测试.py 定稿 4 处歧义，31 变体根因），EXT-007 状态推进「试点通过/已实证」（89 分组 + 710 终端全量），TBC-006 三项全关闭；台账鉴权段按实测算法修订（URL 参数签名，原 Header 模式描述为文档歧义）> 更新 2026-09-16：桌面管控批次基线——主应用 08b3547（bridge.py ROUTES 5 条 /api/desktoppolicy/* + web/desktoppolicy.js + index.html 导航「锁屏及壁纸管理」）+ desktop-policy 子仓 9bdfcdf（终端引擎 desktop_policy.py + CONTRACT v1.1 冻结，双仓副本 MD5 一致 017C788D）；BRG-059~063 新增（2.8 组），UPL-011~013 契约冻结/服务端未实现（P1 在途，TBC-007 跟踪）；对账发现 BRG-063 日志目录写读不一致缺陷已如实登记（主应用无 set_log_dir 调用，待 desktop-policy-dev 修）> 更新 2026-09-16（晚）：桌面管控热修消项基线——desktop-policy c316d87 / 主应用 72d51bc（`log()` 默认目录对齐 data_dir()/logs + makedirs(exist_ok=True) 自建 + BRG-062 注释 600s 对齐；冒烟 21/21 含 3 条主应用态防回归断言、单测 18/18、E2E 40/40）；BRG-063 缺陷关闭、BRG-062 注释语义消项（台账条目内已追加更新行），双仓副本复核 MD5 仍一致（55C856F6，主应用 72d51bc 同步 c316d87）；条数不变 167 > 更新 2026-09-16（晚 2）：power-control P0 批次基线——SRV-080/081（§1.13）+ BRG-064/065（2.9 组）共 4 条由 power-control-dev 登记、api-registrar-dev 复核归档；复核修正 4 项：①头部统计同步 167→171 ②BRG-064/065 从 2.8 组划出独立 2.9 组（原挂桌面管控组下归属错误）③SRV-080/081 补 curl 调用方式 ④代码出处补精确行号（bridge.py:60/144-145 挂载 commit 2bbf881 代码实证；server api.py:462-476/965-966/1244+ 工作区实证但**未提交，commit 待补**）；附注：主应用 2bbf881 为混合主题提交（bridge.py powercontrol 挂载 + desktop_policy.py read_power_state 增强 + web 管控徽章），2.8 组行号基线漂移注记见 2.8 组级更新行；同批另发现桌面管控 2bbf881 增量不改 BRG-059~063 响应结构 > 更新 2026-09-16（晚 3）：SRV-080/081 在途标注消项——server-platform **ec726af**（ADR-038）入库（git show 实证 5 文件 +334：api.py/app.py/power_control.py/smoke_power_srv.py/DECISIONS.md，三 py 文件工作区复核干净），头部统计同步；power-control 子仓快照形状权威出处（docs/ARCHITECTURE.md §4 v2 契约 + E2E 夹具）与 BRG-064 登记形状一致（power-control-dev 提供锚点） > 更新 2026-09-16（晚 4）：BRG-064/065 挂载出处更正 2bbf881→**17f506c**（主仓统筹入库：bridge.py +4 挂载 + power_control.py 全文件 808 行 + SPEC v1.0 + 原版登记 43 行；子仓指针 8a8bb43；先前误归因工作区未提交窗口期）——**分叉提醒**：17f506c 已含原版登记并随 1bbcaba 推送远端，本仓 a05a9f2 修正版（统计同步/2.9 分组/curl/行号 4 项）与之并存，合并对账时以修正版为准 > 更新 2026-09-17：客户端版本发布管理批次基线——server-platform **e24a236**（ADR-042：client_release.py 新模块 +336 行 / api.py +162 / app.py +11 / store.py +17，单测 37/37+冒烟 30/30+e2e 139/139）；SRV-082~087 新增（§1.14 组）+ SRV-046/UPL-002 心跳响应 latest_version 更新行；scope 变更（terminal 口白名单 +manifest 与 /download/*）登记于 §1.14 组级约定；已知瑕疵：api.py dispatch docstring terminal scope 注释未同步（以 _scope_allowed 为准，已知会 server-platform-dev）；合计 171→177 > 更新 2026-09-17（晚）：docstring 瑕疵消项——9ce055f 注释同步+头部标注（纯注释改动，代码实证 api.py:232）；server-platform-dev 提供 client_release.py 行号基线（31/32/33/80/319-321，与登记一致）；合计不变 177 > 更新 2026-09-17（晚 2）：update-manifest 别名批次基线——server-platform **327a69b**（api.py +26，冒烟 33/33+e2e 139/139 零回归）；SRV-088 新增（SRV-086 扁平形状别名，TBC-002 消项，消费侧 updater.check_async；?version= 接收但忽略）；terminal 口白名单 +update-manifest（api.py:216-222 in 元组）；SRV-086 条目补消费侧形状区分注记；合计 177→178 > 更新 2026-09-17（晚 2）：第三方数据源批次基线——server-platform **b8a086b**（ADR-043：api.py +60/nad_client.py +46/console +358，smoke_thirdparty.py +303/test +106/e2e_console +72）；SRV-089~092 新增（§1.15 组，online-log/isolated 骨架降级、isolate admin-only+密码重校验+501 待画方）；nad_client 形状变更（full 参数默认不变，EXT-006 条目已注记）——**勘误**：知会所写「影响 SRV-024 组」有误，SRV-024 为 AI 分析列表，nad_client 实际消费者为 EXT-006 与 ipconflict 证据链（BRG-042/SRV-055 链，默认行为不变无回归）；合计 178→182 > 更新 2026-09-17（晚 3）：ADR-043 增补批次基线——server-platform **3645d94**（api.py +63/store.py +46/console +45，无新端点）：①SRV-089 画方查询键口径修正（终端自报 IP 优先/连接源兜底，query 四键）②SRV-090 platform_timeline 过渡数据源并列透出（7 天/240 点）③spanbanip 接真将单独立项另行知会；行号基线注记见 §1.15 组级约定；条数不变 182 > 更新 2026-09-17（晚 4）：开关机管控页批次基线——server-platform **95dbb1c**（ADR-040 follow-up：group filter + manual registry + batch gating + note，api.py +68/power_control.py +94/test_power_manual.py +72）；SRV-093~096 新增（manual-config 4 条）+ **SRV-097~099 漏登补登**（policies/dispatch 批次组——ADR-040 主体随主仓统筹 a099448/server-platform 3fe299e 引入时未随批登记，对账发现补登）+ UPL-014 命令 pc_apply_policy 补登（终端 handler 已在位，uplink.py:701-716）；§1.13 组级约定扩展（手动登记不下发终端语义 + _validate_pc_sched 校验矩阵）；合计 182→190。**流程提醒**：统筹提交（a099448 类）触达多仓时，接口登记知会须显式覆盖全部新增端点，避免漏登 > 更新 2026-09-17（晚 5）：火绒专项补知会批次基线——huorong-dev 按常设约定**首例主动适用**（三批次显式列清单 9 条：b6a74f6 P1 后端全链 / 9ab89ac 资产融合 ADR-033 增补 / 7842aab 弹窗数据层 ADR-033 增补二）；SRV-100~108 新增（§1.16 组）；语义变更锚（SRV-101 实时聚合口径、SRV-103 中文化文案）已随条目入注；⚠️ 全组生产未部署（火绒凭据未注入），部署后随对账核销；合计 190→199 > 更新 2026-09-17（晚 6）：file-search 漏登补登 + 并行直写发现批次基线——①BRG-070~073 新增（2.11 组：路由 c37cf97/5f32dbe/17f506c 漏登，net-doctor-dev 知会 894f20a 参数增强触发首登；**主仓 file_search.py 副本待同步**——FS_SORTABLE 无 ext、无 mtime_*，主应用运行态该二能力暂不可用，待同步后转全量在用）②**发现 power-control-dev「三大改造批」（7fe4e9b）直写台账 BRG-066~069 未走知会**——对账复核合格（bridge.py:186-189+:224 实证）归档为新组 2.10，编号占用已避让（filesearch 改用 070~073）；两事件同期暴露直写与漏登双风险，常设约定执行需双向（知会方列全清单、登记方勤对账）；合计 199→207 > 更新 2026-09-17（晚 7）：file-search 主仓副本同步完成基线——主仓 **e39ec89**（net-doctor-dev 整文件同步，双仓 MD5 一致 C6C3089A，模块级验证 7/7）；2.11 组滞后警示消项，BRG-070 转全量在用（ext 排序/mtime 参数/is_dir 契约）、BRG-071 补 partial_volumes；主仓副本此前实际落后三批次（排序/筛选增强、is_dir 契约、partial_volumes/文案），同步已纳入 net-doctor-dev 批次交付清单；条数不变 207 > 更新 2026-09-17（晚 8）：pc_diag 联调通道批次基线——server-platform **5f0ba2f**（ADR-040 增补：api.py +69/power_control.py +87，**已生产部署验证** deploy_20260917_185433，diag#1 completed 全文交付 ADR-006 定案）；SRV-109~112 新增 + UPL-015 命令新增（终端 handler 待 4.1.2，如实标注）；合计 207→212；4.1.1 发布收口约定同期生效（首例全链路完成 17:47:16）> 更新 2026-09-17（晚 9）：UPL-015 终端执行面闭环——主仓 **3dc7e8b**（4.1.2 版本 bump + pc_diag handler uplink.py:746-747 + pc_diag 日志取数 + installer 修复），4.1.2 已装 WIN-13F-xx-3 生产双端实证 diag#1 completed 全字段；unknown_command_type 限定 4.1.1 及更早；条数不变 212 > 更新 2026-09-18：4.1.5 电源行动与 WoL 批次基线——server-platform **85ae733**（ADR-044/045：api.py +252/wol.py 新模块 322/power_control.py +151/app.py +11/test_wol.py +179）+ 主仓 **0c5f1e0**（客户端 4.1.5 终端面）；SRV-113~119 新增（§1.13 组）+ UPL-016~018 三命令新增（双端就绪）；WoL 确认语义（UDP 无确认，last_seen 定案）与 wol_enabled=false 总开关已入组级约定；合计 212→226 > 更新 2026-09-18（晚）：file-search 4.1.7 索引器部署批次基线——主仓 **c598e05**/子仓 **9c3c680**（BRG-074 新增部署引导 + BRG-071 status 三态更新行；双仓 MD5 一致=同步纪律首次完整生效实证；门禁 smoke 61/61 + E2E 92/92）；/query、/stats、/open-location 契约不变（仅 hint 字段值随三态更新，非契约变更）；合计 226→227 > 更新 2026-09-18（晚 2）：4.1.7 AI 证据增强双批基线——①perf-analyzer BRG-075 record-latest（主仓 1eac1b6/子仓 8faffde；stress_<id>.json 结构化落盘配套非端点）②net-doctor BRG-076~078 tracert-last/stress-last/net-snapshot（主仓 0b3f43d/子仓 45cb17c；AI 源清单 8→9 源 net_perf_snapshot；net_service.py 双仓 MD5 一致实证）；两批同日同源主题（AI 证据回填/供给）按 team-lead 建议同批处理；门禁 E2E 373/373 + smoke 137/137；合计 227→232
- 对账方法：grep api.py `_terminal_api`/`_console_api`/`_console_kb_api`/`_console_nettest` 分支 + bridge.py `ROUTES`，与台账逐条比对，输出差异清单（新增未登记/已废弃仍登记/字段不符）
- 维护规则：接口变更（改参数/改路径/废弃）必须同步更新台账，条目内追加 `> 更新 YYYY-MM-DD：变更点（出处）`，保留历史痕迹
- 常设约定（2026-09-17，team-lead 批准）：**凡跨仓/多功能统筹提交（main 或模块发起），登记知会必须附「新增/变更端点显式清单」（路径+方法+一句语义）**，不允许「参考某某 ADR 自行对账」式模糊知会；登记官可随时以「api.py 分支 diff + 命令白名单 + 终端侧协议」三清单代跑对账兜底（双保险）。首例档案：SRV-097~099 + UPL-014 漏登补登（ADR-040 主体随统筹 a099448/3fe299e 引入未随批知会，commit 2161aaf 溯源补登留痕）
- 常设约定（2026-09-17，team-lead 批准，4.1.1 批次起执行）：**客户端发布收口**——①构建方出产物+完整摘要（版本/MD5/SHA256/size/构建时间）；②发布动作（SRV-082 上传 release + SRV-084 set-current）**一律收口 server-platform-dev** 执行并回报发布态与对拍结果；③**独立对拍**：发布方必须从 SRV-087 /download 全量下载回读做 SHA256/MD5 对拍（自产自发盲区消除）；④紧急热修例外：任一方可先发，**30 分钟内**补独立对拍并知会登记官与 main；⑤**消费观察定案口径**：目标终端心跳拿到 latest_version（SRV-046/UPL-002）→ client_version 变更，随发布回报——此为「推送完成」唯一定案口径。首例 4.1.1：power-control 构建 → server-platform 独立对拍 → 消费观察 17:47:16 升级完成（team-lead 下发；发布对拍细节待 server-platform-dev 发布回报归档后补登）
