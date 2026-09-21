# Log Inspector 架构与 API 一览

> 子系统：观枢终端平台｜EyeTerm · 日志诊断（业务流程：检索 → 下载 → 智能分析 → 知识库建议）

```
log-inspector/
├── docs/
│   ├── DECISIONS.md            # 项目记忆（ADR）
│   └── ARCHITECTURE.md         # 本文件
├── log_reader.py               # 引擎：win32evtlog 流式读取 / 过滤 / 故障模式 FAULT_PATTERNS
├── log_service.py              # 服务层（框架无关 handler，被 bridge.py 直接调用）
├── web/
│   ├── loginspector.js         # 前端业务流程逻辑（li 前缀命名空间，入口 initLogInspectorTab）
│   └── loginspector-standalone.html  # 独立测试页（E2E 载体）
├── vendor/
│   └── chart.umd.min.js        # Chart.js 本地副本（复制自主应用 web/vendor/）
└── tools/
    └── e2e_loginspector.py     # Playwright E2E（standalone + 主应用双场景）
```

## 数据链路

```
前端 loginspector.js
  └─ apiFetch(path) ─ pywebview 桥 / HTTP
        └─ bridge.py ROUTES
              └─ log_service.handle_*  ──>  log_reader（win32evtlog 流式）
```

## API（bridge.py 路由，GET + query params）

| 路由 | 说明 | 关键参数 |
|---|---|---|
| /api/loginspector/access | 探测各类别日志读取权限 | — → access{System:true,...}, denied[] |
| /api/loginspector/search | 检索（服务端过滤+分页） | types, hours / start,end, levels, source, keyword, event_id, page, per_page |
| /api/loginspector/export-start | 启动 txt 导出后台任务 | 同上（无分页）→ task_id |
| /api/loginspector/export-status | 轮询导出任务 | task_id → status/progress/result{path,count,size} |
| /api/loginspector/export-cancel | 取消导出任务 | task_id |
| /api/loginspector/analyze | 智能分析（同检索条件） | 同 search → conclusion/patterns/known_issues/summary |
| /api/loginspector/report-export | 导出分析报告 HTML | 同 analyze（复用最近分析缓存）→ path |
| /api/loginspector/knowledge | 知识库（自由检索） | q → knowledge[] |
| /api/disk/open-location | 「打开位置」（主应用复用） | path |

### 统一筛选参数约定

- `types`: 逗号分隔，`System,Application,Security,Setup`（默认 System）
- 时间：`hours=72`（默认近3天）或 `start=YYYY-MM-DD HH:MM&end=...`（自定义）
- `levels`: 逗号分隔 `critical,error,warning,info`（默认全部）
- `source` / `keyword` / `event_id`：可选精确/模糊过滤
- 所有接口返回 `denied`（权限不足类别）与 `errors`（单类别读取失败信息），单类别失败不影响其他类别

## 主应用集成点（ADR-001）

- 同步文件：log_service.py、log_reader.py、web/loginspector.js
- 手术：web/index.html（导航 5→3 按钮、tab-loginspector section）、web/app.js（switchTab 守卫 + 删旧函数）、bridge.py（路由）、service.py（删旧 handler）
- 合并后导航：**日志诊断 / 磁盘清理 / 性能分析**

## 铁律索引

1. 报告 HTML / 原始日志 txt 分离（ADR-003/005）
2. 大日志流式处理，导出增量 flush（ADR-002/003）
3. Security 非管理员优雅降级（ADR-004）
4. 子进程必须 CREATE_NO_WINDOW（有意开窗如 explorer 除外）
5. 路径禁止硬编码盘符，默认 %USERPROFILE%\Downloads
6. 任何失败优雅降级（success=false + error）
