# EyeTerm 接口总台账（观枢终端平台）

- **版本**：v1.0（首版建档）
- **最后更新**：2026-09-16
- **维护人**：api-registrar-dev（接口登记官）
- **事实来源**：代码实证（server-platform/server/api.py、bridge.py、uplink.py、net-doctor/net_service.py 等），每条注明文件+函数
- **登记统计**：
  - 一、服务端 REST API（SRV）：79 条（2026-09-10 晚新增 ADR-029 IP 冲突深度检测 2 条）
  - 二、终端本地桥接 API（BRG）：63 条（netdoctor 19 条：10 条 2026-09-09 合入转「在用」commit 628c210；AI 诊断 2 条 commit a892f62/bd965ae；冲突检测本地转发 3 条 commit b344bbc/40abd3f；路由追踪 AI 分析 + AI 诊断历史持久化 4 条 2026-09-11 登记，commit c4495bc/916e791；桌面管控 5 条 2026-09-16 登记，commit 08b3547/9bdfcdf）
  - 三、终端↔平台协议（UPL）：13 条（2026-09-16 新增桌面管控契约 3 条 UPL-011~013，契约冻结/服务端未实现）
  - 四、外部依赖接口（EXT）：7 条（2026-09-15 新增火绒终端安全 API v1，调研阶段）
  - 五、废弃/规划接口（DEP）：5 条
  - **合计 167 条**
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
- **登记记录**：2026-09-09，代码实证

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
- **登记记录**：2026-09-09，代码实证

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
- **清理语义**：done/error 任务在轮询时惰性清理（**实现为 started 超 600s 移除**；代码注释写「60s」与实现不符，以实现为准——desktop_policy.py:1315-1321）
- **代码出处**：desktop_policy.py `handle_dp_task_status`(1306)；bridge.py:134
- **状态**：在用
- **登记记录**：2026-09-16，代码实证（同 BRG-059）

#### BRG-063 日志尾部 `GET /api/desktoppolicy/logs`
- **用途**：日志目录与当日日志尾部（≤60 行）
- **鉴权**：无（本机进程内）
- **请求参数**：无
- **响应**：`{"success":true,"log_dir":"<LOCALAPPDATA>\\winhelper\\desktop_policy\\logs","log_file":"...\\dp_YYYYMMDD.log","exists":bool,"tail":[...]}`
- **⚠️ 已知缺陷（登记如实，2026-09-16 对账发现）**：本接口读 `data_dir()/logs/` 子目录（对齐 CONTRACT.md §4），但主应用运行态**无任何 `set_log_dir` 调用**（grep 实证：定义于 desktop_policy.py:54，调用仅存在于 desktop-policy/tools 测试脚本）——引擎 `log()` 实际写 `data_dir()` 根目录，主应用内本接口 tail 恒空/exists=false；待 desktop-policy-dev 修复（主应用启动时 `set_log_dir(data_dir()/logs)` 或读端兼容双路径）
- **代码出处**：desktop_policy.py `handle_dp_logs`(1325) / `log`(60) / `set_log_dir`(54)；bridge.py:135
- **状态**：在用（路径/结构正确；tail 受上述缺陷影响为空）
- **登记记录**：2026-09-16，代码实证（同 BRG-059）

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
- **登记记录**：2026-09-09，代码实证

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
- **登记记录**：2026-09-09，main 下发（画方《新版本NAD对外接口说明》20251231 版 37 页解析 + 生产实测）> 更新 2026-09-09：admission_log 接入落地，补代码实证 nad_client.py（commit a5156cc）。**文档-实际差异适配 3 处（实测知识，逐条登记）**：①`list` 字段实际为 dict 形态（键为序号字符串）而非文档所述数组——`_norm_macs` 归一为列表（nad_client.py:94-95）；②macs 条目实际为对象（`{"0": {"mac":.., "ips": {"0":{..}}, "macports": null|[..]}}`）且 macports 可为 null（nad_client.py:88-92）；③单页上限实际 1000 而非文档 limit≤10000——实测 1588 台需翻页 2 页，`NAD_PAGE_LIMIT=1000` + `NAD_MAX_PAGES=50` 翻页防御（nad_client.py:28-29）；配置经 `NAD_CONFIG_ID=2` 读第三方接口登记（appkey/app_secret），全量缓存 `_cache`，签名 hmac_sha256_hex(appsecret,"appkey=..&nonce=..&time=..") > 更新 2026-09-10：AI 诊断批次回归核对（net-doctor a892f62 / 主应用 bd965ae，diff 仅 bridge.py/net_service.py/web 前端）——nad_client.py 与 api.py 消费链（`_enrich_ipconflict_admission` api.py:1260/383）未被触碰，在位无回归（代码实证）

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
- **登记记录**：2026-09-15，huorong-dev 下发（官方 API v1 文档解析 + 实测，方案文档与客户端雏形已核对入仓）> 更新 2026-09-15（晚）：**签名谜题破案，试点打通**——①根因定性：此前「凭据被拒」假设不成立，真因 = 官方文档 4 处关键歧义（Header 模式无效/待签路径不带前导斜杠/quote 默认 safe='/'/expires 窗口 86400 非 300s），经用户提供官方参考脚本 `api测试.py` 实测破案（排查 31 变体 + 排除凭据/时钟/代理/TLS 因素； errmsg="Authentication failed" 为纯签名错零差异）；②实测算法已固化方案文档 §7.1 + draft 客户端按实测算法重写（huorong_api_client_draft.py `_sign`/`_post`）；③试点实测数据语义：分页终止 `offset >= data.total`（data 内层 total）、**local_ip 为主取用字段**（local_ip≠connect_ip 仅 2.8%，connect_ip 跨网段/代理上报仅作链路参考）、MAC 唯一 698/710（重复取 last_connect_time 最新，归一化作关联键）、分组名 i18n 键残留需规整（`i18n:db_groups_name:*`）、Win7 旗舰版 59 台 EOL 风险；脱敏样例归档 docs/attachments/huorong_pilot_sample_20260915.json——**待代码实证**（P1 `huorong.py` 正式化落地后对账补证）

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

- 本台账对账基线 commit：工作区当前版本（bridge.py / uplink.py 有未提交修改，以台账登记时点代码为准）> 更新 2026-09-09：net-doctor 合入基线 commit 628c210（bridge.py ROUTES 含 /api/netdoctor/* 10 条）> 更新 2026-09-09：系统管理模块基线 commit 4d2924b（sysadmin 13 路由 + 多 token 模型 UPL-010 + session-info，代码实证 api.py:88-104/243-248/764-773/829+）> 更新 2026-09-09：知识库模块语义修正基线 commit 31f8e1d（KB 9 端点 ADR-022 语义 + route-nodes source 字段，代码实证 api.py:1396-1461/1369-1377/383-397、kb_store.py:43/59/157）> 更新 2026-09-09：终端 AI 智能诊断基线 commit 8aaa64e（SRV-070 diagnose + SRV-071 单条详情 + trigger=terminal_diagnose，代码实证 api.py:419-445/784-790/1160+，ADR-023）> 更新 2026-09-09：画方 admission_log 接入基线 commit a5156cc（nad_client.py + _enrich_ipconflict_admission，EXT-006 补代码实证与 3 处文档-实际差异，ADR-024）> 更新 2026-09-09：交换机管理基线 commit f031152（SRV-072~077 sysadmin 组，ADR-026，代码实证 api.py:1059-1133、settings.py:15/31、store.py:170-177）> 更新 2026-09-10：AI 诊断批次基线 net-doctor a892f62 / 主应用 bd965ae（BRG-050 补登+mode 双模式扩展、BRG-051 新增、BRG-040 ai_personal_json 写配置；bridge.py `call` body 双参透传仅 ai-diagnose 一条，bridge.py:128-133；EXT-006/BRG-042 回归核对无变化）> 更新 2026-09-10（晚）：AI 诊断收尾批次基线——server-platform 子仓 47de462/91b2fe6（ADR-027 截断重做+证据硬约束，SRV-070）、257ed21（ADR-028 判定精化 SRV-051 + ADR-029 深度检测 SRV-078/079）、25d48e2（静态资源 ETag 协商缓存，SRV-042/043）；net-doctor 子仓 c7219c7/1f60f95（BRG-051 used_key 三态+防假阳性、BRG-050 URL 归一化+提示词加固、BRG-042 Find-NetRoute 路由解析）；主仓同步 a81bd1c/3069ea7（net-doctor 指针前进 1f60f95）> 更新 2026-09-10（晚 2）：server-platform 9e604a7+cc34bae（deep-engine ADR-029 双端点归属修正 + verdict.sources ARP 失败同步）；net-doctor 深度检测本地转发 2 条 BRG-052/053（conflict-deep-start/poll，query 传参非 body；NET_ROUTES 已定义、主应用 bridge.py ROUTES 挂载待同步——待办移交主应用集成）> 更新 2026-09-10（晚 2）：BRG-054 conflict-ai-reanalyze 入账（主应用 b344bbc / net-doctor acee817）；BRG-052/053 撤「挂载待同步」（主应用 40abd3f hotfix 挂载 bridge.py:102-103，v8 内按钮不可用根因即漏挂载）——主应用内 netdoctor 冲突检测三路由双端就绪> 更新 2026-09-11：路由追踪 AI 分析批次基线——net-doctor a8aa49e（trace-ai-analyze 对接）+ f22e2bf（AI 诊断历史 JSONL 持久化三路由）；server-platform 2cb39d2（ADR-031 routetrace 分支）+ a8a6b95（ADR-030 ipconflict 聚合分支补登记）；主仓 c4495bc/916e791（bridge 挂载 4 条，bridge.py:106-109）；**遗留缺陷：BRG-055↔SRV-055 键名不一致（data vs context），routetrace 聚合走回退，待 net-doctor-dev/server-platform-dev 定责修复****遗留缺陷：BRG-055↔SRV-055 键名不一致（data vs context），routetrace 聚合走回退，待 net-doctor-dev/server-platform-dev 定责修复**【已消项 2026-09-11（晚）：net-doctor 4679b11 / 主应用 e6452e5，extra 改 context{target,hops}，缺陷关闭；注意 main 此前重建的 exe 在热修提交前，下次换装前需全量重建一次（team-lead 换装时执行）】> 更新 2026-09-15：火绒对接调研批次基线——EXT-007 新增（火绒终端安全 API v1，huorong-dev 下发，官方文档解析 + 连通性实测；签名认证待凭据修复，消费方 huorong.py//console/huorong/* 均为规划未实现，落地后对账补代码实证）> 更新 2026-09-15（晚）：火绒试点打通批次基线——签名谜题破案（官方参考脚本 api测试.py 定稿 4 处歧义，31 变体根因），EXT-007 状态推进「试点通过/已实证」（89 分组 + 710 终端全量），TBC-006 三项全关闭；台账鉴权段按实测算法修订（URL 参数签名，原 Header 模式描述为文档歧义）> 更新 2026-09-16：桌面管控批次基线——主应用 08b3547（bridge.py ROUTES 5 条 /api/desktoppolicy/* + web/desktoppolicy.js + index.html 导航「锁屏及壁纸管理」）+ desktop-policy 子仓 9bdfcdf（终端引擎 desktop_policy.py + CONTRACT v1.1 冻结，双仓副本 MD5 一致 017C788D）；BRG-059~063 新增（2.8 组），UPL-011~013 契约冻结/服务端未实现（P1 在途，TBC-007 跟踪）；对账发现 BRG-063 日志目录写读不一致缺陷已如实登记（主应用无 set_log_dir 调用，待 desktop-policy-dev 修）
- 对账方法：grep api.py `_terminal_api`/`_console_api`/`_console_kb_api`/`_console_nettest` 分支 + bridge.py `ROUTES`，与台账逐条比对，输出差异清单（新增未登记/已废弃仍登记/字段不符）
- 维护规则：接口变更（改参数/改路径/废弃）必须同步更新台账，条目内追加 `> 更新 YYYY-MM-DD：变更点（出处）`，保留历史痕迹
