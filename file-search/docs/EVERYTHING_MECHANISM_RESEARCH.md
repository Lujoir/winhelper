# Everything 快速检索机制研究报告与 EyeTerm 自研可行性评估

> 版本 v1.0（2026-09-15）｜ 缘起：用户要求反编译 Everything 了解其快速检索与定位方案；
> 结论先行：**反编译 2.2MB 原生 C++ 二进制不现实也不必要**——其方案基于公开的 Windows API
> （NTFS MFT / USN Journal），本文以官方文档 + 公开设计资料 + 本项目实测还原完整机制，
> 并给出 EyeTerm 三条落地路线的评估。

---

## 一、Everything 快速检索机制还原（四层）

### 1. 首建索引：MFT 直读（秒级~分钟级）
- 不做文件系统遍历（目录树递归），而是直接打开卷句柄 `\\.\C:`，
  通过 `DeviceIoControl(FSCTL_ENUM_USN_DATA)` **一次 IOCTL 枚举全卷所有文件记录**
  （MFT 主文件表中的每个 File Record：文件名 + 父目录 FRN + 属性）。
- 250 万文件的 NVMe 卷实测 1~5 秒完成（纯内核态顺序读）。
- 这解释了首建索引「快得反直觉」：不是扫文件，是读一张内核维护的表。

### 2. 实时更新：USN Journal（变更日志）
- NTFS 为每个卷维护 **USN Journal**（文件系统级"流水账"，记录所有增删改/改名事件）。
- Everything 打开卷的 USN 句柄，持久化 `NextUsn` 游标，`FSCTL_READ_USN_JOURNAL`
  消费增量 → **文件一落盘，索引即刻更新**（"刚创建的文件立刻能搜到"的原因）。
- 断电/重启后从持久化游标续读，不重扫。

### 3. 内存索引结构：父引用树（FRN → 路径）
- 每条记录只有「文件名 + 父目录 FRN」，完整路径按 **FRN 父链动态重建**（惰性拼接）。
- 索引本质 = 文件名集合 + 父指针映射，**不索引文件内容**——这是"小而快"的核心取舍
  （148MB 索引库 ≈ 数百万条目；常驻内存数百 MB）。
- 查询 = 纯内存字符串匹配（子串/通配/正则可选），百万级条目毫秒返回。

### 4. 权限模型（本项目死结所在）
- 打开卷句柄（`\\.\C:` 的 GENERIC_READ）与读 USN **均要求管理员令牌**——Windows 安全边界，
  任何基于 MFT/USN 的方案都绕不开。
- Everything 的产品化解法：**Everything Service（SYSTEM 服务）**做索引与监控，
  GUI 客户端（标准用户）向服务请求索引数据 → 免提权。
- **本项目实测反复验证**：服务已装且 Running，但「服务只做索引，HTTP 是客户端实例功能」；
  客户端实例（标准用户/命名实例）读 MFT 失败 → HTTP 起不来（5700 零监听）。
  命名实例（-instance）与服务的数据通道未按预期工作，官方文档对该组合语焉不详。

## 二、反编译可行性评估

| 维度 | 评估 |
|---|---|
| 法律 | MIT 许可覆盖软件本体使用/修改/分发；为互操作/学习目的的反向工程在主流法域属可接受实践，但**不得直接复用反编译产物代码** |
| 技术 | 2.2MB 原生代码（GUI/索引/查询/HTTP/多线程）反编译为可读伪码是**数周~数月专家工作**，投入产出比极低 |
| 必要性 | **核心方案是公开的**（第一节全部来自公开 Windows API 与作者公开论述），反编译能得到的"额外信息"仅是实现细节（内存布局/微优化），对 EyeTerm 无决定性价值 |

**结论：不做二进制反编译；机制已完整还原（第一节），价值等价。**

## 三、EyeTerm 三条落地路线评估

### 路线 A：复用用户自装 Everything（现状，最快可用）
- 用户 GUI 里开 HTTP（5 步）或 EyeTerm「一键启用」按钮（改其 ini + 提权重启，UAC 一次）。
- 优点：今天就能用；缺点：依赖用户已装 Everything、需用户 GUI 常驻。

### 路线 B：捆绑 Everything 服务模式（当前实现，冻结）
- 本机实测服务模式 HTTP 不启动（见一.4），命名实例组合文档缺失——**冻结**，待官方渠道确认。

### 路线 C：自研轻量索引器（**已拍板实施**，2026-09-15 用户裁定；原「推荐的中期方案」）

**实施状态（P1 交付，2026-09-15）**：fs_indexer.py（ctypes 零依赖约 430 行）+ sqlite 引擎 + search_service 全量改造 + E2E 59/59 + sqlite 冒烟 19/19。本节原文保留，实施落地记录见下。
- 架构：EyeTerm 安装器注册一个 **SYSTEM 权限计划任务**（或服务）运行自研索引器子进程；
  终端通过本机 IPC/本地 socket 查询。
- 索引器实现（Python ctypes 零依赖，约 500 行）：
  1. `FSCTL_ENUM_USN_DATA` 全量枚举各 NTFS 卷（首建 10~30 秒）
  2. `FSCTL_READ_USN_JOURNAL` + NextUsn 持久化（实时增量）
  3. 存储：sqlite（file name/path/FRN/mtime/size，FTS5 前缀索引）——绕开 Python 大内存问题
  4. 查询：LIKE/前缀 + 排序/分页（与现 UI 契约兼容）
- 性能预期：首建 10~30 秒/卷、增量实时、查询 10~100ms（sqlite FTS）——足够运维检索场景。
- 工程量：1~2 天（含计划任务注册/IPC/E2E）。
- 权限：计划任务以 SYSTEM 跑 → 读 MFT 无墙；终端零提权。
- 风险：非 NTFS 卷（exFAT/FAT32 U 盘）不在 USN 体系 → 降级为按需遍历或不索引。

## 四、建议

1. ~~**短期**（今天）：路线 A 的「一键启用」按钮交付~~（未实施，路线 B 捆绑先行后随本次裁定废弃）。
2. **路线 C 自研索引器：用户 2026-09-15 拍板实施**（P1 当日交付，见第五节实施记录）。
3. 路线 B（Everything 捆绑/服务模式）**冻结归档**：服务模式 HTTP 不启动 + 命名实例组合不可靠 + 预置 ini
   app_data 缺省链路脆弱——教训入 ADR-003/004/005。

---

## 五、路线 C 实施记录（P1 交付，2026-09-15）

**交付清单**：
1. **fs_indexer.py**（file-search 根，约 430 行 ctypes 零依赖）：`FSCTL_ENUM_USN_DATA` 全量首建
   （纯内核态顺序读，秒级/卷）+ `FSCTL_READ_USN_JOURNAL` 增量（NextUsn 持久化 usn_state 表，
   1s 轮询阻塞消费循环秒级实时）+ 存储 sqlite `C:\ProgramData\EyeTerm\filesearch\index.db`
   （表 files(volume,frn,parent_frn,name,path,is_dir,size,mtime) + usn_state；**path 写入时物化父链**；
   size/mtime 经 FindFirstFileW 补齐——USN 记录不含这两个字段；非 NTFS 卷跳过并记录）。
   运行模式 `run`（常驻）/`--once`（首建缺卷+各卷消费一轮）/`--status`；IsUserAnAdmin 守卫。
   目录删除递归删子树、改名重算子树 path、journal ID 变更自动重建。
2. **search_service.py v2**：**Everything 依赖全删**（HTTP 5700/拉起编排/exe 定位链/实例 ini/保存路径设置）
   → 直读 index.db（sqlite 只读 uri + busy_timeout 与索引器写并发共存）。检索契约前端零改动：
   q 原生 `ext:` 拼接段服务端解析转 LIKE OR 组（多选语义保持）、sort name/size/mtime 正倒序透传、
   上限 200、目录纳入检索（行 path 归一所在目录）、库缺失/空 → `indexer_not_running` 如实提示。
   bridge 同步 FS_ROUTES 4→3（save-path 删，全键覆盖检查）。
3. **前端**：状态卡换索引语义（就绪徽章+文件数+卷）、保存路径功能删、Everything 品牌文案清除；
   排序/右键/ext chips 契约零改动。
4. **门禁**：sqlite 冒烟 **19/19**（无管理员可跑：子串/大小写/中文目录行/ext 组内 OR/三列正倒序/
   LIKE 转义/上限 200/未就绪提示/契约形状）+ E2E **59/59**；**真数据实测** tools/fs_smoke_real.py
   （管理员会话执行：真 USN 首建→基线检索→新建文件实时入索引→删除失效→排序契约）。

**与第三节预估的差异记录**：
- size/mtime 需 FindFirstFileW 补齐（USN 记录不含），首建为批量一次性成本（增量仅单文件，开销可忽略）；
- 查询用 LIKE + ESCAPE 转义而非 FTS5（运维检索场景 LIKE 子串已满足，避免增量双写 FTS 表的复杂度）；
- 检索范围含目录（对齐 Everything 体验，目录行「打开位置」定位其父）；
- **更正（2026-09-15 重测）**：S: 卷经 GetDriveTypeW/GetVolumeInformationW 判定实为真实固定 NTFS 分区
  （本机资产明细确有 S: 分区），被正常索引 1 万+条属**正确行为**——早前「S: SUBST 幽灵卷」推测作废；
- **FRN 键教训（重测三轮定位）**：MFT 64 位 FRN 高 16 位为 sequence number，子记录保存的父引用 seq
  与父记录枚举 seq 可能不同步——全链路 FRN 键必须统一低 48 位（_FRN_MASK），否则父链物化全灭；
- partial 语义：build 硬错误（如 C 盘 ~2/3 处 1392 稳定复现）时已收记录入库 + usn_state 落 partial 行
  （游标 NULL）——UI 如实展示「部分卷索引未完成」，consume 静默跳过，下次 run 重建该卷重试。
