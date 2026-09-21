# EyeTerm 文件检索 · installer 清单（路线 C 自研索引器版，2026-09-15）

> **方向变更（用户拍板，ADR-005）**：放弃 Everything 集成（捆绑/服务/实例/HTTP 全移除），
> 自研轻量索引器 fs_indexer（USN 机制见 docs/EVERYTHING_MECHANISM_RESEARCH.md 第五节实施记录）。
> 本文为 **P2 安装包清单**（P1 代码已交付：fs_indexer.py + search_service v2 + E2E 59/59 + 冒烟 19/19）。

## 一、P2 安装包构成

| 构件 | 来源 | 目标 | 说明 |
|---|---|---|---|
| winhelper.exe | dist\ | {app} | 主程序（终端查询直读 index.db，普通权限只读） |
| fs_indexer.exe | PyInstaller 打包 fs_indexer.py（目标 <20MB） | {app}\tools\ | SYSTEM 计划任务常驻索引器（首建+增量循环） |
| platform_ca.pem | assets\ | {app}\assets | 平台根证书（沿用） |

## 二、计划任务注册（安装时）

```bat
schtasks /Create /F /TN "EyeTerm\FileIndexer" /RU SYSTEM /RL HIGHEST
  /SC ONSTART /DELAY 0000:30
  /TR "\"{app}\tools\fs_indexer.exe\" run"
```

- /RU SYSTEM：读 MFT/USN 免提权（权限模型核心）；/RL HIGHEST。
- ONSTART + 30s 延迟：开机即索引；另配失败重启（任务计划 GUI 或 /SC ONLOGNE 替代方案在 P2 定稿——
  兜底：EyeTerm 启动时检测 index.db 陈旧/缺失可提示并尝试 `schtasks /Run`）。
- 卸载：`schtasks /Delete /TN "EyeTerm\FileIndexer" /F` + 删 {app} + **保留或删除
  C:\ProgramData\EyeTerm\filesearch\index.db（倾向保留，重装即用）**。
- 打包注意：fs_indexer.py 仅标准库（ctypes/sqlite3），onefile 控制台程序即可（无窗口输出需求），
  排除大依赖目标 <20MB；`fs_indexer.exe run` 常驻循环内部已含异常自愈。

## 三、Everything 全量移除清单（P2 执行）

- [x] search_service.py：HTTP 5700/拉起编排/定位链/实例 ini/保存路径设置全删（P1 已完成）
- [x] bridge.py：FS_ROUTES 4→3（save-path 删，P1 已完成）
- [x] 前端：设置卡路径行/品牌文案/错误映射（P1 已完成）
- [ ] installer/EyeTerm.iss：删 [Files] 五件（Everything.exe/lng/License.txt/README-voidtools.txt/预置 ini）
  + [Code] ssInstall/ssPostInstall 服务编排 + [UninstallRun] DelEverythingSvc + [Registry] EverythingPath
- [ ] installer：新增 fs_indexer.exe [Files] + schtasks [Run]/[UninstallRun]
- [ ] desktop.py：Everything 拉起钩子删除
- [ ] third_party/：目录删除（.gitignore 同步移除该条）
- [ ] 装后开箱验证：无 UAC/无配置 → 检索可用（索引器首建期间如实提示「索引未就绪」）

## 四、Everything 路线归档（冻结，历史参考）

- 路线 B 捆绑+服务模式：服务 Running 但 5700 零监听（app_data=0 修复后仍存 svc 形态 HTTP 不确定性）；
  命名实例组合官方文档缺失；Medium 拉起权限墙。详见 git 历史（iss 服务编排版本）与 ADR-003/004。
- 合规件（License.txt 等）随 third_party 移除一并出库，无需继续维护。
