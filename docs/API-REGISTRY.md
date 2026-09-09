# EyeTerm 接口总台账（观枢终端平台）

- **版本**：v1.0（首版建档）
- **最后更新**：2026-09-09
- **维护人**：api-registrar-dev（接口登记官）
- **事实来源**：代码实证（server-platform/server/api.py、bridge.py、uplink.py、net-doctor/net_service.py 等），每条注明文件+函数
- **登记统计**：
  - 一、服务端 REST API（SRV）：55 条
  - 二、终端本地桥接 API（BRG）：49 条（其中 netdoctor 10 条为开发中状态）
  - 三、终端↔平台协议（UPL）：9 条
  - 四、外部依赖接口（EXT）：5 条
  - 五、废弃/规划接口（DEP）：5 条
  - **合计 123 条**
- **通用约定**：
  - 服务端监听：ThreadingHTTPServer，`0.0.0.0:{port}`，默认 18090（app.py `_load_config` / `main`）；配置经 `$ETP_CONFIG` → `server/config.local.json` → dev 默认三级加载
  - 终端上行鉴权：请求头 `X-ETP-Token`（对照 config.json `terminal_token`）
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
- **登记记录**：2026-09-09，代码实证

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
- **登记记录**：2026-09-09，代码实证

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
- **登记记录**：2026-09-09，代码实证

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

### 1.8 知识库（运维知识库 KB）

#### SRV-036 知识库路由表/新建 `GET|POST /api/v1/console/kb`
- **用途**：GET 取全量路由表；POST 新建条目（5 版本迭代存储）
- **鉴权**：X-ETP-Console-Token
- **请求参数**（POST）：`{"kb_id":"route-core","category":"路由","title":"核心路由","content":"...","author":"admin","note":"首版"}`
- **响应**：GET `{"ok":true,"routes":[...]}`；POST `{"ok":true,"kb_id":"route-core","version":1}`；重复 409
- **调用方式**：`curl -X POST http://<server>/api/v1/console/kb -H "X-ETP-Console-Token: <token>" -d '{"kb_id":"route-core","title":"核心路由","content":"..."}'`
- **代码出处**：api.py `_console_kb_api` → kb_store.py
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-037 知识库条目详情/更新/删除 `GET|PUT|DELETE /api/v1/console/kb/{kid}`
- **用途**：单条目读取（含 history）/ 更新内容（版本+1）/ 删除
- **鉴权**：X-ETP-Console-Token
- **请求参数**（PUT）：`{"title":"...","category":"...","content":"...","author":"admin","note":"..."}`
- **响应**：GET `{"ok":true,"entry":{...,"history":[...]}}`；PUT `{"ok":true,"version":2}`；DELETE `{"ok":true,"deleted":true}`；不存在 404
- **调用方式**：`curl http://<server>/api/v1/console/kb/route-core -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_kb_api`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-038 知识库版本列表 `GET /api/v1/console/kb/{kid}/versions`
- **用途**：条目全部历史版本号列表
- **鉴权**：X-ETP-Console-Token
- **请求参数**：路径 `{kid}`
- **响应**：`{"ok":true,"versions":[1,2,3]}`
- **调用方式**：`curl http://<server>/api/v1/console/kb/route-core/versions -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_kb_api` → kb_store.py `versions`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

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
- **登记记录**：2026-09-09，代码实证

#### SRV-041 知识库维护记录 `GET /api/v1/console/kb/{kid}/history`
- **用途**：条目创建/更新/回滚/删除的维护履历
- **鉴权**：X-ETP-Console-Token
- **请求参数**：路径 `{kid}`
- **响应**：`{"ok":true,"history":[{ts,action,author,note,...}]}`
- **调用方式**：`curl http://<server>/api/v1/console/kb/route-core/history -H "X-ETP-Console-Token: <token>"`
- **代码出处**：api.py `_console_kb_api` → kb_store.py `history`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

### 1.9 静态资源

#### SRV-042 控制台首页 `GET /`（及 `/index.html`）
- **用途**：控制台单页应用入口
- **鉴权**：无（页面本身公开；数据接口需 token）
- **请求参数**：无
- **响应**：`text/html`（console/index.html）
- **调用方式**：浏览器 `http://<server>/`
- **代码出处**：api.py `_static`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

#### SRV-043 控制台静态文件 `GET /<file>`
- **用途**：console/ 目录下静态资源（js/css/png/svg/json 等，按扩展名映射 Content-Type）
- **鉴权**：无
- **请求参数**：路径为相对文件名；目录穿越防护（normpath 前缀校验，越界 403）
- **响应**：文件内容；不存在 404；类型不在白名单 `application/octet-stream`
- **调用方式**：`curl http://<server>/app.js`
- **代码出处**：api.py `_static`
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

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
- **登记记录**：2026-09-09，代码实证

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
- **登记记录**：2026-09-09，代码实证

#### SRV-052 路由节点知识库（netdoctor）`GET /api/v1/terminals/{tid}/netdoctor/route-nodes`
- **用途**：下发路由节点表（CIDR→区域标注），供终端 tracert 逐跳标注
- **鉴权**：X-ETP-Token（+ 准入）
- **请求参数**：无（数据源 settings `netdoctor.route_nodes`，JSON 数组）
- **响应**：`{"ok":true,"nodes":[{"cidr":"172.17.254.0/24","zone":"核心","name":"..."}]}`
- **调用方式**：`curl http://<server>/api/v1/terminals/WIN-HOST/netdoctor/route-nodes -H "X-ETP-Token: <token>"`
- **代码出处**：api.py `_terminal_api`（settings 读取）
- **状态**：在用
- **登记记录**：2026-09-09，代码实证

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
- **登记记录**：2026-09-09，代码实证

> 注：SRV-001~055 中编号按登记顺序连续分配；「1.x」小节标题与编号的对应关系以条目内「方法与路径」为准。

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

### 2.7 网络排障（net-doctor 子系统，**开发中**）

> net-doctor-dev 并行开发中：net-doctor/net_service.py 已实现 `handle_net_*` 处理器与 `_platform_get/_platform_post` 平台联动，web/netdoctor.js 已有调用方；**bridge.py ROUTES 尚未挂载以下路由**（待 net-doctor 交付合入主应用）。前端传参走 query/params，任务类接口返回 `task_id` 供轮询。

#### BRG-040 netdoctor 配置读取 `GET /api/netdoctor/config`（开发中）
- **用途**：节点表/DNS 基线/uplink 配置面状态
- **代码出处**：net_service.py `handle_net_config`；调用方 net-doctor/web/netdoctor.js
- **状态**：开发中（bridge 未挂载）
- **登记记录**：2026-09-09，代码实证（net_service.py 定义，挂载待合入）

#### BRG-041 配置核查 `GET /api/netdoctor/config-check`（开发中）
- **用途**：DHCP/DNS 基线比对核查任务（离线可用）
- **代码出处**：net_service.py `handle_net_config_check`
- **状态**：开发中（bridge 未挂载）
- **登记记录**：2026-09-09，代码实证

#### BRG-042 IP 冲突检测 `GET /api/netdoctor/ipconflict`（开发中）
- **用途**：活动网卡 IP+MAC → 平台 ipconflict 端点交叉校验；未连中心拒绝（error=not_connected）；疑似时自动调 `/api/v1/ai/analyze`
- **代码出处**：net_service.py `handle_net_ipconflict` → `run_ipconflict_result`
- **状态**：开发中（bridge 未挂载）
- **登记记录**：2026-09-09，代码实证

#### BRG-043 连通性检测启动 `GET /api/netdoctor/ping-start`（开发中）
- **用途**：8 节点逐节点 ping / nslookup / w32tm(stripchart) 探测，JSONL 落盘
- **代码出处**：net_service.py `handle_net_ping_start`
- **状态**：开发中（bridge 未挂载）
- **登记记录**：2026-09-09，代码实证

#### BRG-044 连通性历史 `GET /api/netdoctor/ping-history`（开发中）
- **用途**：连通性检测历史记录查询
- **请求参数**：query `limit`（默认 100）
- **代码出处**：net_service.py `handle_net_ping_history`
- **状态**：开发中（bridge 未挂载）
- **登记记录**：2026-09-09，代码实证

#### BRG-045 路由追踪 `GET /api/netdoctor/tracert-start`（开发中）
- **用途**：tracert 逐跳解析 + 平台 route-nodes CIDR 区域标注
- **请求参数**：query `target`
- **代码出处**：net_service.py `handle_net_tracert_start`
- **状态**：开发中（bridge 未挂载）
- **登记记录**：2026-09-09，代码实证

#### BRG-046 网络压测启动 `GET /api/netdoctor/stress-start`（开发中）
- **用途**：多包大小持续 ping 中心 + 平台 iperf-server + 本地 iperf3 客户端（需连中心）
- **请求参数**：query `duration_sec`、`sizes`、`udp_mbps`
- **代码出处**：net_service.py `handle_net_stress_start`
- **状态**：开发中（bridge 未挂载）
- **登记记录**：2026-09-09，代码实证

#### BRG-047 任务状态轮询 `GET /api/netdoctor/task-status`（开发中）
- **用途**：通用后台任务轮询
- **请求参数**：query `task_id`
- **代码出处**：net_service.py `handle_net_task_status`
- **状态**：开发中（bridge 未挂载）
- **登记记录**：2026-09-09，代码实证

#### BRG-048 任务取消 `GET /api/netdoctor/task-cancel`（开发中）
- **用途**：取消运行中任务
- **请求参数**：query `task_id`
- **代码出处**：net_service.py `handle_net_task_cancel`
- **状态**：开发中（bridge 未挂载）
- **登记记录**：2026-09-09，代码实证

#### BRG-049 压测报告导出 `GET /api/netdoctor/stress-export`（开发中）
- **用途**：导出压测 HTML 报告（需先有完成的压测任务）
- **请求参数**：query `task_id`
- **代码出处**：net_service.py `handle_net_stress_export`
- **状态**：开发中（bridge 未挂载）
- **登记记录**：2026-09-09，代码实证

---

## 三、终端↔平台协议（uplink.py + 命令通道，ADR-015/016/019 冻结稿）

> 协议依据：server-platform ADR-015/016/019（心跳命令通道协议 v1）；客户端版本 uplink v4（CLIENT_VERSION="4.0.0"）。
> 传输层：纯标准库 urllib（uplink.py `_post`），JSON body，请求头 `X-ETP-Token`；token 仅存 `%LOCALAPPDATA%/winhelper/uplink_config.json`（0600 语义），任何日志/异常/状态接口不回显。

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
- **登记记录**：2026-09-09，代码实证

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
- **登记记录**：2026-09-09，代码实证

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
- **代码出处**：net-doctor/net_service.py `_ntp_probe`（节点表常量）
- **状态**：开发中（随 net-doctor 子系统合入）
- **登记记录**：2026-09-09，代码实证（net_service.py 定义）

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

#### DEP-005 netdoctor 本地桥接挂载 — 规划（开发中）
- **内容**：bridge.py ROUTES 挂载 `/api/netdoctor/*` 10 条（BRG-040~049），index.html 导航新增 data-tab="netdoctor"（性能分析之后），app.js switchTab 守卫
- **负责**：net-doctor-dev（并行开发中，net_service.py + web/netdoctor.js 已备，E2E/smoke 齐备）
- **状态**：规划（待 net-doctor 交付合入主应用后更新本台账为「在用」）
- **登记记录**：2026-09-09，代码实证（net_service.py 定义 + bridge.py ROUTES 缺失佐证）

---

## 附：待确认项（TBC）

| 编号 | 事项 | 说明 |
|------|------|------|
| TBC-001 | register 的 `asset` 字段透传 | store.py `register_terminal` 签名已支持 `asset=None` 并写 `asset_detail` 列；但 api.py `_terminal_api` register 分支调用时**未传 asset**（仅 hwinfo）→ 终端上报的 schema1 资产明细未入库，SRV-005 的 `asset` 字段实际走 `get_terminal_asset` 的 hwinfo 回退。待 server-platform-dev 确认 a1 补丁后更新台账 |
| TBC-002 | settings 无预置键 | `llm.api_key`/`llm.model_fallback`/`iperf.server_ip`/`netdoctor.route_nodes`/`ftp.password` 不在 settings.py DEFAULTS（按需 set 后生效），GET settings 时未配置键不出现或为默认值 |
| TBC-003 | BRG 各 handler 响应字段全集 | 39 条已挂载路由的响应以 success/error 公共字段 + 关键字段记录；逐字段全集可在联调对账时以 service 文件 handler 返回值补录 |
| TBC-004 | config.json 键清单 | app.py dev 默认：port/terminal_token/console_password/session_ttl_hours/report_interval/retention_days/bottleneck_dedup_min/data_dir；生产 config.json 实际键以部署实例为准（口令已移除，登录走 console_auth.db） |

## 附：对账约定

- 本台账对账基线 commit：工作区当前版本（bridge.py / uplink.py 有未提交修改，以台账登记时点代码为准）
- 对账方法：grep api.py `_terminal_api`/`_console_api`/`_console_kb_api`/`_console_nettest` 分支 + bridge.py `ROUTES`，与台账逐条比对，输出差异清单（新增未登记/已废弃仍登记/字段不符）
- 维护规则：接口变更（改参数/改路径/废弃）必须同步更新台账，条目内追加 `> 更新 YYYY-MM-DD：变更点（出处）`，保留历史痕迹
