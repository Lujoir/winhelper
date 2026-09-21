# file-search · 决策记录（ADR）

> 项目：观枢终端平台｜EyeTerm · Windows 终端「文件检索」子系统
> 规则：一条 ADR 记录一个不可逆/高影响决策；新决策追加编号，不改写旧条目。

---

## ADR-001 · Everything 1.4.1.969 x64 便携捆绑 + HTTP Server 方案（2026-09-11）

**决策**（用户拍板，不再调研）：
1. **捆绑**：Everything 1.4.1.969 x64 便携版随 EyeTerm 分发（MIT 许可，合法）；分发必须附带 voidtools License.txt 与版权声明；**不修改 Everything 本体**，只做配置/编排适配。
2. **HTTP Server 方案**：预置 Everything.ini（`http_server_enabled=1`、绑 `127.0.0.1:5700`、`http_server_download=0` 文件下载关闭），search_service 经 HTTP API 查询（`/?search=x&json=1&count=200`）。
3. **实例隔离**：已有 Everything 实例的机器用 `-instance eyeterm` 独立实例（独立 ini/database），或探测第三方实例 HTTP 可用（127.0.0.1:5700）则复用。
4. **权限如实处理**：读 MFT 需管理员——EyeTerm 启动拉起时失败给明确指引（非报错吞掉）；自启策略 = 随 EyeTerm 拉起，**非开机常驻**。

**实测结论（2026-09-11，Medium 完整性级 shell）**：
- 本机用户自装实例（F:\Program Files\Everything-1.4.1.969.x64，HTTP 关闭）运行中——不动其配置。
- 拉起 `-instance eyeterm -config <数据目录ini> -database <数据目录db> -startup`：进程存活但 HTTP 5700 无监听、ini 未回写（证据链：netstat 无监听 + mtime 不变 + 10s 轮询）——与权限对话框挂起特征一致。
- **结论**：Medium 环境下无法完成 HTTP 拉起验证；真实全链实测留管理员会话（tools/fs_smoke_real.py 入口，探测可用即跑）。search_service 启动编排按「拉起→探测→失败给指引」设计，不假设成功。

**结果**：采纳。HTTP API JSON 形状按 Everything 1.4 HTTP 文档口径实现并做防御性解析（字段缺失容错），E2E 用兼容桩（Python http.server 模拟 5700 同形状）。

---

## ADR-002 · 集成契约与边界（2026-09-11）

**决策**：
1. **独立项目** file-search\：search_service.py（架构参照 net_service：即时返回 + 后台任务 + ROUTES dict）+ web/filesearch.js（fs 前缀）+ tools/。
2. **主应用集成最小增量**：index.html 导航「文件检索」（性能分析之后）+ tab-filesearch；bridge.py ROUTES 挂 /api/filesearch/*；app.js switchTab 分支——NET/ROUTES 全键覆盖检查单必须执行（bridge 漏挂载教训）。
3. **UI 规格**：net-doctor/docs/STYLE.md 八类规格 + 五卡默认收起点按钮展开模式 + 文案零实现细节 + 占位禁真实 IP；结果上限 200 条；「打开位置」复用 /api/disk/open-location。
4. **desktop.py 仅加一行钩子**调用 search_service 启动编排；installer/EyeTerm.iss 增补清单先报 main 后实施。
5. **零新增 pip 依赖**；CREATE_NO_WINDOW 铁律；不动其它子项目契约文件。
6. **捆绑文件不入 git**（Everything.exe/ini/db/License.txt 进 .gitignore），由 installer 清单管理。

**结果**：采纳。

---

## ADR-003 · 服务模式为主路径（installer 静默安装 Everything 服务）+ {app} 注册表探测（2026-09-14）

**背景**：main 批准 iss 实施并裁定——实测「Medium 权限下 eyeterm 实例 HTTP 拉起失败（权限墙）」是预期内约束，解药不是让终端提权，而是**安装包以管理员身份静默安装 Everything 为 Windows 服务**（SYSTEM 读 MFT 无压力 + 服务承载 HTTP 127.0.0.1:5700）；裁定①维持 desktop 钩子首用时拉起为兜底（服务在 → HTTP 探测直接复用）。

**决策**：
1. **EyeTerm.iss 增补合入**：[Files] 五件（exe/lng/License.txt/README/预置 ini → {app}\everything，服务读 exe 同目录 Everything.ini，UTF-16 LE）；[Code] ssInstall 先 -uninstall-service + taskkill 清残留（防覆盖文件被占用），ssPostInstall 静默 `-install-service`（主路径）；[UninstallRun] 文件删除前卸服务；[Registry] HKLM\Software\EyeTerm\EverythingPath。
2. **search_service 定位链插第三级**：env → 配置文件 → **注册表 HKLM/HKCU Software\EyeTerm\EverythingPath（winreg 标准库）** → 常见路径——installer 安装目录自动命中。
3. **合规**：License.txt 官方全文（MIT + PCRE BSD）随包；README-voidtools.txt 注明来源；捆绑二进制不入 git（third_party/）。

**结果**：采纳。E2E 30/30 维持全绿；管理员会话实测方案见 docs/INSTALLER_CHECKLIST.md 第七节（编译/安装/服务验证/主路径复用/兜底拉起/{app} 探测/卸载七步）。

---

## ADR-004 · 服务 config 阻塞根因（app_data=0）+ 检索三功能迭代（2026-09-15）

**背景**：生产实测服务 Running/Automatic 但 5700 零监听（ini 在 exe 同目录且 http 键正确；`-install-service -config` 未生效）。

**查证结论**（voidtools 官方文档 /zh-cn/support/everything/ini/、/support/everything/everything_service/、/support/everything/http/）：
1. `app_data` 选项**永远存储在 exe 同目录 Everything.ini**；值缺省=1（安装版默认）→ 实例转读 %APPDATA%\Everything——服务以 SYSTEM 运行即读 `systemprofile\AppData\Roaming\Everything`（空）→ 全默认 → HTTP 不开。**预置 ini 缺 `app_data=0` 即 5700 零监听根因**。
2. `-install-service` 官方语法不支持 config（仅 [-install-service-pipe-name] [-install-service-security-descriptor]），`-config` 是实例运行参数——生产「ImagePath 未变」属预期。
3. 服务职责=NTFS 索引+USN 监控（~1MB 内存），使**客户端实例以标准用户运行借服务索引**；HTTP server 为客户端功能（Lite 版对比语境）→ 若 svc 形态不开 HTTP，5700 由 eyeterm 实例承载（ensure_running 拉起链自愈，免提权）。

**决策**：
1. **预置 ini 补 `app_data=0`**（服务与实例同锚 exe 同目录配置）→ 装完服务即读同目录 ini → http_server_enabled=1 生效；iss 编排（覆盖前停旧服务/装完重装）已使升级场景自动生效，iss 本体无改动。
2. **实测双路径判定**（INSTALLER_CHECKLIST 第七节）：A=服务直接承载 5700（app_data=0 生效，最优）；B=svc 不开 HTTP → EyeTerm 首检索拉起实例借服务索引开 5700（官方模型自愈）——任一走通即 PASS。
3. **功能三件**：①表头点击排序——名称/大小/修改时间走 Everything HTTP 原生 `sort`（官方四值 name/path/date_modified/size）+`ascending` 透传（服务层校验），类型列=扩展名前端排；②类型预筛 chips（全部/pdf/doc(x)/ppt(x)/xls(x)/zip/exe/txt）走 Everything 原生 `ext:` 语法拼接、多选 OR 分号多值、「全部」互斥清空；③结果行右键自绘菜单（STYLE.md 风格）：打开文件位置/复制路径（execCommand 兜底，file:// 无 clipboard secure context 保证）。
4. **顺手修陈年 bug**：路径列 title 存完整路径被行内/右键取用再拼名称=双拼——新增 `data-path` 纯目录属性，title 保留悬停语义。
5. **门禁**：E2E 58/58（+18：排序翻转请求参数/类型前端排不发请求与行序/ext 拼接含多值 OR/chip 选中态/右键菜单出现·携带路径·复制反馈·打开动作，独立页+主应用双场景）+ net-doctor E2E 306/306 零回归；副本 MD5 双对 MATCH。

**结果**：采纳。生产 5700 监听验证随下次管理员会话（方案七/八步）。

---

## ADR-005 · 放弃 Everything 集成，自研轻量索引器（路线 C，用户拍板）（2026-09-15）

**背景**：路线 B（捆绑 Everything + 服务模式）在生产反复受阻——服务 Running 但 5700 零监听
（app_data 链路已修但仍存 svc 形态 HTTP 不确定性）、命名实例组合官方文档缺失、Medium 权限拉起权限墙。
用户拍板：**放弃 Everything 集成，自研轻量索引器**（机制研究 docs/EVERYTHING_MECHANISM_RESEARCH.md 路线 C）。
一切 Everything 捆绑/服务/实例/HTTP 依赖全部移除。

**决策**：
1. **fs_indexer 索引器**（独立小 exe，P2 PyInstaller 打包 <20MB，P1 先 Python 交付）：
   SYSTEM 计划任务常驻——`FSCTL_ENUM_USN_DATA` 全量首建 + `FSCTL_READ_USN_JOURNAL` 增量
   （NextUsn 每卷持久化 usn_state，1s 阻塞消费循环秒级实时）+ sqlite
   `C:\ProgramData\EyeTerm\filesearch\index.db`（files/usn_state；path 写入时物化父链；
   size/mtime FindFirstFile 补——USN 记录不含；非 NTFS 卷跳过记录）。
2. **终端查询改造**：search_service **全部 Everything 依赖删除** → 直读 index.db（普通权限只读）；
   检索契约前端零改动（q/ext: 拼接/sort 正倒序/右键/上限 200/打开位置）；db 不存在或空 →
   `indexer_not_running` 明确提示。bridge FS_ROUTES 4→3 同步（save-path 删）。
3. **P2（安装包阶段）**：fs_indexer.spec 轻量打包 + iss 计划任务注册（schtasks /RU SYSTEM /RL HIGHEST，
   开机触发+失败重启）+ iss 移除 Everything 五件/服务编排/注册表 EverythingPath/third_party；
   desktop 钩子（Everything 拉起）删除。
4. **门禁**：P1 本机真数据实测（tools/fs_smoke_real.py 管理员会话）+ sqlite 冒烟 19/19 + E2E 59/59；
   P2 装后开箱即用（无 UAC/无配置）。

**结果**：P1 采纳交付（本 commit）。P2 待排期。路线 B 归档冻结。

---

## ADR-006 · journal 失效自愈：重置→当场重建→再消费 单轮闭环（2026-09-16）

**背景**：本机换库复测实证 READ_USN 结构体修复生效（1784 彻底消失，增量消费首次真实工作：
F 盘 applied=12 / G 盘 applied=3 / S 盘 applied=0、C 盘 partial 跳过，检索/排序/删除失效全 PASS）；
唯 D 盘 consume `read_journal_failed err=1181`（JOURNAL_DELETED）——D 盘游标为前日 17:25，
期间其 USN 日志失效（日志删除或 JournalID 变化；F/G/S 同期游标仍活）。旧引擎对这类失败
只会每轮重报，无自愈路径。

**决策**：
1. **consume_volume 自愈语义**：命中 journal 失效族（err ∈ {1178 DELETE_IN_PROGRESS,
   1179 ENTRY_DELETED, 1180 NOT_ACTIVE, 1181 DELETED} 或 journal_id 变化）→ **自动重置该卷**
   （`DELETE FROM usn_state WHERE volume=?` 连带该卷 files 全清——半新半旧不可留）→
   返回 `{"ok": false, "error": "journal_reset", "rebuild": true}`；非失效码（如 err=50）
   仍按普通失败上报、不动游标。
2. **run_once 当场重建**：consume 返回 rebuild=true 时立即 `build_volume`（幂等，partial
   语义保护）并在重建成功后再消费一轮——自愈闭环单轮完成，report 以 `journal_reset_rebuild`
   模式条目如实留痕。
3. **语义边界**：journal 失效重建是**标准恢复路径（非异常）**，日志 INFO 级
   「日志已重置，重建索引（卷 X）」；既有 `journal_invalid`/`read_journal_failed` 仅保留给
   非失效族错误。
4. **smoke 增量目标卷选择**（fs_smoke_real）：先跑健康探测轮再选目标卷——
   consume ok=健康；journal_reset_rebuild ok=自愈重建成功（亦算健康，D 盘实证路径）；
   重建失败才 fallback 下一健康游标卷。单卷日志失效不再阻塞全链判定。
5. **门禁**：sqlite 冒烟 T8 组（id 变化/1181/1180 触发重置 + 非失效码不重置 + run_once
   闭环单轮完成 + 重建后游标落新 ID + 旧索引零残留）；本机重测预期 D 盘自动重建
   （894k 条约 1-2 分钟）→ 四卷 consume 健康 → 增量命中。

**结果**：采纳（本 commit）。
