# 架构决策记录（ADR）— 从 winhelper 迁移时的全部记忆

> 本文件是 disk-cleaner 项目的"记忆载体"。所有设计决策、踩坑史、安全约束在此沉淀。
> 子 agent 开发前必读；修改核心行为时必须追加记录。

## ADR-001 安全分级模型（不可妥协）

数据安全优先。文件分三级处理：

| 级别 | 内容 | 策略 |
|---|---|---|
| 🟢 safe | 回收站、系统/用户临时、更新缓存、传递优化、缩略图、WER、着色器、浏览器缓存、系统日志 | 一键清理 + 勾选清理 |
| 🟡 caution | 内存转储（蓝屏证据）、Windows.old、升级残留($WINDOWS.~BT/Config.MSI) | 默认不勾选，显式确认才清 |
| 🔴 sensitive | 大容量数据文件 | **只定位+分析+打开位置，应用内无删除入口** |

## ADR-002 清理白名单（服务端唯一事实来源）

- `disk_cleanup.py::CATEGORY_DEFS` 硬编码全部清理路径；前端只能传分类 key
- key 前置同步校验（曾因校验在后台线程导致非法 key 仍返回 success=True 的缺陷，已修复——**校验必须同步前置**）
- 清理互斥锁防并发；`_MEI*`（PyInstaller 运行时）保护防自杀

## ADR-003 路径通用性（跨机器适配）

- **禁止硬编码盘符**：一律用 `SystemDrive / SystemRoot / ProgramData / LOCALAPPDATA / TEMP` 环境变量
- `DRIVE = SystemDrive`（回收站/概览/升级残留跟随系统盘）
- 浏览器缓存/下载目录：**枚举全部 Profile**（含 Profile 1/2…），勿只扫 Default；已覆盖 Chrome/Edge/360极速/QQ/Brave/Vivaldi/Firefox
- Windows 7 无 DeliveryOptimization/D3DSCache → 目录不存在自动记 0，不报错

## ADR-004 目录树容量分析（squarify treemap）

- 全量统计：**不跳过** WinSxS/回收站/系统卷信息（用户要求"整个盘的文件夹都列出来"）；WinSxS 有硬链接重复计数属正常
- 内存目录树存 task["_tree"]，`/api/disk/tree` 懒展开（limit 上限 5000，勿回退到 80 截断——曾有"目录不全"缺陷）
- 海量小文件识别：文件数≥300 且均值≤256KB（应用日志/缓存特征）
- 无权限目录计入 skipped 并保留 10 条样本路径返回前端展示
- squarify 布局已验证：面积守恒/无重叠（10 轮随机测试）；极端值分布下最小块可能细长，可接受

## ADR-005 安装包判定规则（误判修复史）

EXE 逐级判定（顺序不可换）：
1. `uninstall|卸载` → 排除（卸载程序）
2. `setup|(?<!un)installer|(?<!un)install(?![a-z])|安装` → 明确（注意 `(?![a-z])` 排除 installation；`(?<!un)` 排除 uninstaller）
3. 版本号 `\bv?\d+(\.\d+){1,3}\b` + ≥1MB → 明确（工具负向除外；不限盘符）
4. 架构标记 `win64|win32|x64|x86|64-bit|32-bit|amd64|arm64` + ≥1MB → 明确（工具负向除外）
5. 工具负向（mysql/myisam/innochecksum/ibd2sdi/check/dump/show/admin/config/kill/echo…）→ 排除
6. 其余 → 待确认

历史教训：仅"下载目录≥1MB EXE"会误杀 MySQL 工具集（mysqlcheck 等是程序本体非安装包）。

## ADR-006 应用数据迁移助手

- 区域：微信(WeChat Files/xwechat_files 双版本)/QQ(Tencent Files 含 NT)/钉钉/腾讯会议/桌面/下载
- **微信/QQ 的 db、log 目录扫描时直接排除，绝不触碰**
- 迁移=移动（保留相对结构，同名同大小跳过防重复，重名自动改名），迁移前校验目标盘空间
- 删除白名单=应用数据区域根 + **本轮扫描会话**登记的自定义扫描根（`_SCAN_GRANT`：单会话 + 30 分钟 TTL，每次扫描开始重置）。2026-09-19 安全改造 R1/H1：原全局累积集合 `_EXTRA_ALLOWED_ROOTS` 会跨轮叠加（含全盘模式逐文件登记的父目录），删除授权随扫描扩散至任意曾被扫到的目录，**已废弃**；客户端仍无法注入自定义根
- 分类聚合 marker：FileStorage/msg/nt_data/FileRecv；桌面/下载按扩展名分组

## ADR-007 前端约定

- 传输层 `apiFetch()`：HTTP 为主，保留 pywebview 桥接分支（未来可回桌面壳）
- 卡片折叠：`.section-card[data-collapse]` + `.collapsed` 类
- **JS 校验用 esprima（本机 node 不可用），避免 `?.` 可选链等 esprima 不支持的语法（WebView2 支持但会挡住校验）**
- 勾选优先排序：`(b.checked - a.checked) || (b.size - a.size)` 状态驱动重排

## ADR-008 已知限制

- 全盘目录树/安装包扫描较慢（C 盘 1-3 分钟），已有进度+取消
- 回收站统计仅覆盖系统盘（当前设计目标即 C 盘清理）
- 腾讯会议/钉钉仅扫 Documents 标准目录，自定义存储位置未覆盖（待办）

## ADR-009 仪表盘 treemap 数据契约

- `renderTreemap` 构造的 data 元素**必须含 `value` 字段**（squarifyLayout 按 value 求和/求面积布局）
- `squarifyLayout` 输出 `rect.item` 即**数据对象本身**（`r.item = list[idx]`），消费方直接读 `rc.item`，**不得解引用 `.d`**
- 历史缺陷（2026-09-05 修复）：data 无 value + `rc.item.d` 双重契约不一致 → 点击"生成仪表盘"报 `Cannot read properties of undefined (reading 'size')` 且面板空白
- 防护：`squarifyLayout` 内 `total <= 0` break 兜底 value 全非正的情况；renderTreemap 入口已 `filter(c => c.size > 0)` 保证正值

## ADR-010 前端改动必须 Playwright E2E 验证

- **esprima 只能查语法，抓不住运行时错误**（ReferenceError / undefined 属性读取 / DOM 元素缺失等）——主应用 treemap ratio 缺陷即教训：契约不一致页面报错，esprima 校验全程通过。独立项目同源 bug（ADR-009）同理。
- **标准流程**（tools/e2e_dashboard.py 固化，可重复执行）：
  1. 起 :5010 服务（脚本 --with-server 自动起停，或手动 `python app.py`）
  2. Playwright 无头 Chromium 打开页面，监听 pageerror 事件
  3. 断言：initDiskTab 自动触发 → 仪表盘 spinner（#tmSpinText）出现 → 实时进度刷新 → 扫描完成 #treemap .tm-block 色块自动渲染（≥10）
  4. 断言全程 pageerror 为 none，退出码反映结果（0=通过）
- 依赖：`pip install playwright && playwright install chromium`（本机无 node，走 Python API）
- **约定：前端任何 JS 改动，commit 前必须跑通本脚本**；每次执行会做一次 C 盘全量树扫描（约1-3分钟）

## ADR-011 与 winhelper 的同步契约（2026-09-05）

- winhelper 主应用已移除 B/S Web 模式（commit ea5fada）：app.py 删除、handle_* 抽至 service.py、Flask 从主应用依赖移除
- **本项目 Flask 保留**——独立运行时与 E2E 验证载体（ADR-010），不受影响
- **发布同步契约**：只同步 disk_cleanup.py / appdata_scan.py / web/disk.js / web/appdata.js，不涉及任何 app.py/Flask 内容
- **分叉点**：web/index.html 两边结构不同（主应用多Tab故障分析+磁盘清理；本项目纯磁盘清理单页）；style.css / app-lite.js（主应用为 app.js）修改需双向人工比对，禁止盲目复制

## ADR-012 esprima 局限与重复声明门禁（2026-09-05 主应用事故教训）

- **esprima-python 检不出同作用域重复 const/let 声明**（最小用例 `function f(){const a=1;const a=2;}` 解析通过）。主应用 renderTreemap 两处 `const atRoot` → 整个 disk.js SyntaxError → initDiskTab 未定义 → switchTab typeof 保护静默跳过初始化，页面全空，而语法校验绿灯
- **门禁补强（双保险）**：
  1. `tools/js_decl_check.py`：esprima AST 作用域扫描，检出同作用域重复 const/let/class 声明（含 switch case 共享块、函数声明与词法声明冲突）；`--selftest` 自检含事故最小用例。web/ JS 同步或修改后必跑
  2. `tools/e2e_dashboard.py` 新增 [1/5] 关键函数 typeof=function 断言（12 个关键入口函数）——SyntaxError 会使整个 JS 文件不执行，typeof 全 undefined，比 pageerror 更早暴露
- 结论重申：**esprima 只做语法初筛，JS 改动的最终门禁是真实浏览器断言（ADR-010）**

## ADR-013 winhelper.exe 重建流程（PyInstaller 文件锁，2026-09-05）

- winhelper.exe 运行中时 PyInstaller 最后一步会 PermissionError（exe 被锁）
- 正确流程：`pyinstaller --distpath dist_new` 构建到新目录 → 停止运行中的 winhelper 进程 → 替换 dist\winhelper.exe → 重启
- 主应用验证手段（可复用）：Playwright + `add_init_script` 注入 pywebview 桩（stub `window.pywebview.api.call` 按路由返回假数据）→ `file://` 加载 index.html → 断言关键函数已定义 + 数据实际填充 + pageerror 为空

## ADR-014 导出格式规范（2026-09-06，用户明确要求，winhelper 与 disk-cleaner 两项目统一生效）

- **所有可导出的报告一律 HTML 格式**，不再使用 Markdown/CSV 等纯文本格式
- 现状核查（2026-09-06）：disk-cleaner 当前**无任何导出功能**（无 export/download 类 API 与前端入口，功能以界面展示为主），无需整改
- 未来新增导出功能的默认要求：
  1. **单文件自包含**：内联 CSS、零外部依赖（无 CDN/外链字体/图片）
  2. **深色专业风格**，与主应用视觉一致
  3. **文件名含模块与时间戳**（如 `DiskCleaner_容量分析_20260906_1530.html`）
  4. **导出后「打开位置」**（复用 /api/disk/open-location 在资源管理器中定位）

## ADR-015 系统正式名称（2026-09-06，用户定名）

- 系统正式名称：**观枢终端平台｜EyeTerm**（Eye=洞察终端故障，Term=Terminal；定位 Windows+安卓统一管理、故障观测定位）
- 适用范围：
  1. 本项目文档（DECISIONS/ARCHITECTURE/README）提及**系统全称**时一律用新名称
  2. 未来按 ADR-014 导出的 HTML 报告，头部标题统一「观枢终端平台｜EyeTerm」
- 不改动部分：
  1. "winhelper" 作为工程/exe 代号继续沿用（指代主应用代码库与 winhelper.exe 可执行文件，改名会造成指代歧义）
  2. 界面代码不动（主应用 nav 标题由 main 统一处理）
- 本项目名 "Disk Cleaner" 为子系统/模块名，继续使用，归属观枢终端平台（EyeTerm）

## ADR-016 主应用磁盘页全断根因：pywebview 6.x 内置 HTTP 静态资源竞态丢失（2026-09-11 诊断）

**症状**：v8.x exe 磁盘清理页全断——概览全「--」、垃圾分析初始态、清理按钮灰、安装包表空；骨架正常、无 pageerror、无 console.error。

**根因（取证定案，非推测）**：
1. 打包环境 pywebview 已升 **6.2.1**（旧构建为更早版本）——6.x 对本地 file:// URL **自动改走内置 Bottle HTTP 服务器**（`webview/__init__.py:274` `if (http_server or has_local_urls): start_global_server`，无参数可禁用），页面实际由 `http://127.0.0.1:<随机端口>/index.html` 承载
2. 该服务器（wsgiref + ThreadingMixIn，**listen backlog 默认 5**）在首帧并发加载 10 个静态资源（9 script/css + favicon）时**丢弃部分请求**（CDP 实测 `status=0`，非 404）
3. 两次独立实验（dist_v3 09-10 构建 / dist_new 09-11 构建）首帧均丢 `disk.js` + `appdata.js` → **整文件未执行** → `initDiskTab/initAppdataTab` 未定义 → `switchTab` 的 typeof 守卫（app.js:118）静默跳过初始化 → 磁盘页全断且零报错
4. **reload 后全部 200、`initDiskTab=function`、一切正常**——竞态只存在于首帧；服务器已就绪
5. 排除项（全部实测排除）：disk.js 语法/重复声明 OK、后端 IPC 四路直测全通（overview/drives/junk/installers）、exe 内 bridge 51~55 个 handle_* import 全可解析、磁盘域 web 文件 hash 与工作区一致
6. **波及面**：随机竞态，受害者按时序轮换——今天丢 disk.js/appdata.js 则磁盘页全断，明天丢 netdoctor.js/perf.js 则别的模块全断。磁盘页只是本次受害者，**不是磁盘域代码缺陷**

**取证方法（可复用）**（disk-cleaner/tools/）：
- `e2e_live_exe.py`：启动 exe（`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port`）→ Playwright connect_over_cdp → 真实页面断言 + **真实 IPC 直测**（`await window.pywebview.api.call(...)`）——修复前红（initDiskTab undefined/数据「--」）修复后应绿
- `_net_forensic.py`：CDP reload 抓逐资源状态码——铁证层（首帧 status=0 vs reload 200）
- `inspect_exe.py`：解包 exe（CArchive+PYZ）核验 web hash 漂移与 bridge/net_service import 断链
- `e2e_mainapp_disk.py`：file:// 桩 E2E（桩环境全绿证明前端代码本身无缺陷；**file:// 测不出本缺陷**——缺陷只存在于 exe 的 HTTP 承载模式）

**修复建议（根因点均在主应用域：desktop.py / index.html，disk-cleaner 未越界修改，转派 main）**：
1. desktop.py 一行治本：`import socketserver; socketserver.TCPServer.request_queue_size = 128`（wsgiref WSGIServer 继承 TCPServer，扩大 listen backlog 消除首帧并发溢出）
2. 前端自愈兜底（强烈建议，覆盖所有模块的任意静态资源竞态）：index.html 尾部内联脚本——DOMContentLoaded 后检查 `initHomeTab/initLogInspectorTab/initDiskTab/initPerfTab/initNetDoctorTab`，任一 `typeof!=='function'` 且 sessionStorage 重载计数 <2 → `location.reload()`
3. 可选替代：requirements 固定 pywebview<5（恢复 file:// 直读），但需回归桌面端全部 API 兼容性，不如 1+2 组合
