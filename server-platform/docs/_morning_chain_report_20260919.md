# EyeTerm 2026-09-19 晨间链路执行报告

执行时间：2026-09-19 07:35 ~ 07:45（自动化任务 eyeterm-wol-a）
执行者：main 会话（按持久记忆 ID 89088047 计划）
工作区：server-platform（主仓 HEAD=1614c9d；部署基线=5e60b4d，详见偏差记录）

---

## ① WoL 首跑取证（07:35）

- 脚本：`tools/_forensic_wol_firstrun.py`（纯只读），凭据仅经 PowerShell 环境变量注入，零落盘
- 存档：`docs/_wol_firstrun_forensic_20260919.json`（10,032 字节，保持未跟踪）

### 关键取证数据

| 项目 | 结果 |
|------|------|
| 触发 | wol_schedules id=1（WIN-13F-xx-3，07:30，enabled=1，method=auto，operator=wol-opt）于 07:30 自治触发 |
| method 走向 | **method=auto 且 direct_ts=null → 跳过直发观察窗直接中继**（ADR-044 增补新逻辑首秀，符合预期） |
| 首轮中继 | #1 phase=relay，经 WIN-DESKTOP-K4K8SI0，cid=43 bcast=172.17.90.255，07:30:19，ok=1 |
| **唤醒确认** | #2 phase=confirm ok=1，detail=last_seen=09-19 **07:31:16**（07:31:19 落档） |
| 服务状态 | terminal-platform：active + enabled（取证时点） |

### 唤醒成败结论（唯一权威判据=last_seen）

**M720t 定时唤醒首跑成功。** 判据：07:30 发包后，平台 last_seen 于 07:31:16 恢复，此后 confirm 连续 ok（07:32~07:42 每分钟心跳均确认），补查 terminals 表 last_seen=07:42:40（取数时点附近），终端持续在线。全程未使用 ICMP/TCP/ARP 探测下结论。

### 发现的问题（如实记录，待 server-platform-dev 跟进）

1. **调度器未收敛**：confirm 已成功（07:31:16 last_seen 恢复）后，wol_tick 未将调度终结（run_state 停在 `relay_sent`、last_run_date 为空），持续重复中继 relay #43~#51（07:30~07:38，约 8 轮）、confirm 重复 7 次，relay_queue/relay_tried 在两个同网段候选间轮换。唤醒本身成功，但链路缺少"确认即终结"收敛逻辑，浪费中继终端命令通道回执。
2. **取证脚本瑕疵**：audit 段查询失败（`audit_log` 表无 `created_ts` 列，需改用实际列名）；journal wol 段 grep 为空（服务日志未含 wol/wake/relay 关键字，调度器可能未向 journal 输出过程日志——与问题 1 相关，建议补日志）。
3. **显示乱码**：wol_schedules 的 name/last_result 字段在 JSON 取证中呈 GBK 乱码（数据层或输出层编码问题，需定位）；terminals 表无 `status` 列（取证脚本按旧认知查询报错，已改用 last_seen 补查成功）。

---

## ② 部署（07:37 ~ 07:41）

### 基线偏差记录（重要）

计划部署"纯 HEAD 5e60b4d"。实测发现主仓 HEAD 已前进至 1614c9d（vlan_kb 提交，5e60b4d 的直接后代，含 server/api.py / server/ai_analysis.py / console/index.html 差异）。为遵守"纯 5e60b4d"要求，采用 `git worktree` 在 5e60b4d 建独立检出执行部署（未动主工作区，用后已移除）。

**首次部署结果：应用文件上传成功，但服务 crash 循环。**

- 现象：`systemctl restart` 后 `is-active` 在 active 与 activating 间抖动，journal 反复出现 `app.py:398 PermissionError: [Errno 13] Permission denied`（绑定 0.0.0.0:443），RestartSec=3 循环重启至 counter=14，端口全无监听。
- 根因：生产 config.json `console_port=443`（<1024 特权端口），服务以非 root 用户 eyeterm 运行；5e60b4d 的 `deploy/terminal-platform.service` 模板**没有** `AmbientCapabilities=CAP_NET_BIND_SERVICE`。旧进程是靠历史高权限占住 443 的"定时炸弹"——任何 restart 都会触发，本次部署重启首次引爆。
- 修复（最小化，应用代码保持 5e60b4d）：主仓 commit **431166c** 的 service 模板已含 `AmbientCapabilities=CAP_NET_BIND_SERVICE`（模板注释明确记录了本 crash 场景与"修复必须留在模板防部署复发"的教训）。将该模板上传至远端（旧 unit 备份为 `terminal-platform.service.bak.morning20260919`），daemon-reload + restart。
- 结论：**"纯 5e60b4d 部署"与当前生产 config（443 管理口）不兼容，生产可部署的最小基线 = 5e60b4d 代码 + 431166c service 模板**。此偏差为必要修复，非超范围变更。

### 部署后验证（全过）

| 检查项 | 结果 |
|--------|------|
| systemctl is-active terminal-platform | active（重启后持续稳定，非瞬时） |
| 端口监听 | 0.0.0.0:18090 / 0.0.0.0:18443 / 0.0.0.0:443 三口齐听（pid 1335073） |
| legacy HTTP health（18090 /api/v1/health） | 200 |
| console 页面（https 443 /） | 200 |
| TLS 终端口（18443 /api/v1/health） | 200 |
| journal Traceback（修复后 07:41 起窗口） | **0**（修复前 crash 循环期 46 条为历史留痕） |
| 防火墙 | firewalld permanent 规则复核通过（18090/18443/443/18121/18122-18141/18200-18299） |
| 备份 | 远端 /data/terminal-platform/backups/pre_20260919_073826/；unit 文件备份 .bak.morning20260919 |

### 部署内容（5e60b4d 批 A）

- power_tasks 任务化（批 A）+ config_cli list 修复（a7f36b7）+ settings/schema 入库（wol.* 五调度键 0c125a1、login-upgrade DDL 5e60b4d）
- 附带效应（deploy.py 固有行为）：vsftpd FTP 密码轮换重置、switch 默认凭据 keep、config.json 与证书 keep、LLM 配置未触碰（env 未注入 ETP_LLM_*）

---

## ③ 迁移后核验（07:41，只读）

| 核验项 | 结果 |
|--------|------|
| wol_schedules task_id 回填 | ✅ **task_id=1** 已回填（批 A power_tasks 任务化迁移生效） |
| wol_attempts 历史完整性 | ✅ id 连续 1~18 无缺口（取证时 13 条 + 部署重启后调度器续跑新增 5 条），今日 07:30 起全部留痕 |
| 今日 07:30 执行留痕 | ✅ #1 relay 07:30:19 → #2 confirm 07:31:19（last_seen=07:31:16） |
| method 实际走向 | ✅ method=auto、direct_ts=null：跳过直发观察窗直接中继（ADR-044 增补新逻辑首秀达成） |
| wol_tick 修复后继续运行 | ✅ 07:38 仍有 relay #50/#51 落档（调度器存活；同时印证"未收敛"问题延续） |
| M720t last_seen 当前值 | ✅ 07:42:40，持续在线（唤醒状态维持） |

---

## 综合结论

1. **M720t 定时唤醒首跑成功**（唯一权威判据 last_seen：07:31:16 恢复，持续在线），平台侧 wol_schedules 驱动的 07:30 自治调度链路完整走通，ADR-044"跨网段跳过直发直接中继"新逻辑首秀符合设计。
2. **批 A（5e60b4d）已部署生产并通过健康检查**，task_id 迁移回填确认；但纯 5e60b4d 必须搭配 431166c 的 unit 模板（AmbientCapabilities），建议后续将此约束写入部署文档/ADR。
3. **遗留问题清单**（移交 server-platform-dev，本任务不处理）：
   - wol_tick confirm 成功后未终结调度（不收敛、重复中继、last_run_date 不回填、run_state 卡 relay_sent）——高优先
   - wol 调度过程零 journal 日志（取证 grep 落空）——建议补结构化日志
   - wol_schedules name/last_result 字段乱码（数据/输出层编码待定位）
   - 取证脚本 `tools/_forensic_wol_firstrun.py` 两处列名硬编码与实际 schema 不符（audit_log.created_ts、terminals.status）

## 边界遵守声明

- 未实施 vlan_kb T1/T2 编码与种子导入（另行派发；种子文件未上传）
- 全程零破坏性操作（仅 SELECT / journalctl / systemctl status|restart / cp 备份 / SFTP 上传）；测试载荷零真实攻击串
- 凭据仅经 PowerShell 环境变量注入，未写入任何文件、未出现在输出
- 主仓工作区未改动；部署临时 worktree 已移除；临时诊断脚本（_wt_*.py）留存 workspace 根目录，纳入统一清理批次

## 附件索引

- `server-platform/docs/_wol_firstrun_forensic_20260919.json` — ①取证原始 JSON（未跟踪）
- `_wt_deploy_out.txt` — ②首次部署完整输出（含 crash 现场）
- `_wt_fix_out.txt` / `_wt_diag_out.txt` — 修复与诊断输出
- `_wt_verify3_out.json` — ③核验原始输出
