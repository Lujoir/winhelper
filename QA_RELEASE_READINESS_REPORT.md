# 观枢终端平台（EyeTerm）上线前 QA 评估与发布就绪度报告

> 评估方式：全部为**静态审阅**，未实际运行。环境无 Python 依赖、无 Windows GUI、无 playwright/chromium、无远端服务器与真实事件日志。

## 一、测试覆盖评估
**已覆盖（真实断言，非仅打印）**
- `smoke.py`：最完整的端到端冒烟，真实 `check()` 断言 + 失败 `sys.exit(1)`；覆盖 health/登录(错误口令401)/白名单只读快照+自清理零副作用/注册心跳/指标(瓶颈触发+去重)/事件/错误 token 401/配置加密脱敏/secretsbox 防篡改/上传/控制台流/命令通道/nettest/AI 分析/HTTPS 双端口。**需本地运行的服务端**。
- `smoke_stress.py`：直接驱动 `perf_service._stress_worker`，含真实 `assert`（磁盘临时清理、内存释放回落、cancel 清理）。
- `e2e_uplink.py`：mock 服务端，真实断言覆盖保存/不回显 token/命令回执/注册字段/指标结构/同 id 幂等/状态接口/非法 scheme 拒绝；隔离良好。
- `e2e_dashboard.py`：Playwright 真实断言（关键函数 typeof、treemap≥10 块、无 pageerror）。
- `js_decl_check.py`：作用域感知重复声明检测，含 9 例 selftest，真实门禁。
- `smoke_backend.py`：真实读 System 日志，真实断言（检索/导出 BOM/分析结论/HTML 自包含/Security 降级）。
- `esprima_check.py`：语法门禁（含禁用 `?.`），真实但仅语法层。
- `test_whitelist.py`（扩展读）：白名单 fail-closed/网段/豁免/审计，隔离临时库，真实断言。
> 另：仓库还有 20+ `test_*.py`（AI 诊断、https 双端口、桌面策略、交换机/深探等）与 `e2e_console.py`，服务侧单测/集成体系较完整。

**缺口与严重度**
- 🔴 破坏性磁盘删除引擎：在册磁盘测试仅 `e2e_dashboard`（扫描/色块），`cleanSelected` 真实删除用户文件无自动化覆盖（数据丢失风险）。
- 🔴 部署回滚：deploy.py 仅创建备份，health 失败仅 `die()`，**无自动化回滚**。
- 🟡 并发上报/异常输入：仅命令级幂等被测，多终端并发、畸形 JSON、超界数值、缺字段未覆盖。
- 🟡 鉴权失败路径：错误口令/错误 token 401 已测；缺 token、角色混淆、限流未测。
- 🟡 `test_lhm.py` **无任何断言**（仅打印连接结果即 return 0），非真正门禁。
- 🟡 无 CI（仓库无 `.github/workflows`/`pytest.ini`/`tox.ini`），测试靠人工。
- 🟡 无持续监控/告警（仅部署一次性 curl 健康检查；grep 命中的 alert()/monitor 均为 JS 字面量，非可观测栈）。

## 二、发布就绪度评分 🟡（72/100）· Go/No-Go
**有条件放行**；在补齐「回滚预案」与「生产凭据审计」前建议 **No-Go**。核心功能测试扎实、部署健康检查与防火墙持久化到位，但运维保障（CI、回滚自动化、监控）缺失。

## 三、发布检查清单
- **上线前**：① 确认 prod config 由 deploy 随机生成（非 dev-token/dev-console）；② 替换 build 内开发占位 CA（`assets/platform_ca.pem`）；③ 清除 deploy.py 硬编码交换机默认口令；④ `py_compile` + 8 脚本静态审阅通过；⑤ 准备回滚 runbook。
- **部署中**：① 确认备份目录 `backups/pre_<ts>` 生成；② 观察 health 200 / TLS 跨类 404；③ 防火墙 `--permanent` 复核；④ 比对 CA SHA256。
- **部署后**：① 冒烟 `smoke.py`（env 覆盖凭据）全绿；② 抽测 1 台真机 uplink 接入；③ 确认 systemd `Restart=always` 生效；④ 人工核对告警渠道可达。
- **回滚预案**：`cp -a backups/pre_<ts>/{server,console,config.json}` 恢复 → `systemctl restart terminal-platform` → 复核 health；`firewall-cmd --remove-port` 收口。

## 四、分级 QA 发现
- **🔴 HIGH-1** 无自动化回滚：`deploy.py:429-439` 仅轮询 active，失败 `die()` 不恢复备份（`:191-194` 已建备份）。
- **🔴 HIGH-2** build 含开发占位 CA：`winhelper.spec:20` 注「当前为开发占位 CA」，production 信任薄弱。
- **🔴 HIGH-3** 硬编码口令：`deploy.py:407` `sw_pwd=...("3@Ww18_Bu9xn")` 源码内含真实口令字面量。
- **🟡 MED-1** `test_lhm.py:10-48` 全程打印无 assert，不构成门禁。
- **🟡 MED-2** 破坏性删除无测试：`disk-cleaner/tools` 仅 `e2e_dashboard`（扫描），删除引擎未自动化。
- **🟡 MED-3** 无 CI / 无监控告警：缺 `.github/workflows` 等；无 Prometheus/Sentry。
- **🟡 MED-4** service 缺防护：`terminal-platform.service` 无 `MemoryMax`/`StartLimitIntervalSec`（重启风暴）。
- **🟡 MED-5** dev-token/dev-console 弱凭据依赖人力勿入生产（`smoke.py:29-30` 默认值）。

## 五、未执行/未读声明
- 全部为静态审阅，**未实际运行**上述任一脚本（缺依赖/Windows GUI/远端/真实日志）。
- 仅审阅要求的 8 个脚本 + 扩展读 `test_whitelist.py`/`winhelper.spec`/README；其余 20+ `test_*.py`、`e2e_console.py`、`e2e_mainapp_disk.py`、`smoke_desktop_policy.py` **存在但未逐一审阅**，真实覆盖/可运行性未评估。
- 未读取被测源码 `api.py`/`store.py`/`app.py`（仅经测试推断行为）；`deploy/_out_*.txt` 历史日志未读。
