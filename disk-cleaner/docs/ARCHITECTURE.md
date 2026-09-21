# 架构

```
disk-cleaner/
├── app.py              # Flask 入口（:5010）+ 全部 API 路由（薄封装）
├── disk_cleanup.py     # 垃圾分类扫描/清理 + 大文件/目录树/推荐 + 任务管理器
├── appdata_scan.py     # 应用数据盘点/迁移/删除 + 安装包扫描
├── web/
│   ├── index.html      # 单页 UI（全部模块纵向排列，卡片可折叠）
│   ├── app-lite.js     # 传输层 apiFetch + 共享工具 + 折叠机制
│   ├── disk.js         # 垃圾/安装包/目录树/推荐/仪表盘 前端逻辑
│   ├── appdata.js      # 应用数据盘点/迁移/删除 前端逻辑
│   └── style.css       # 暗色主题样式
└── docs/DECISIONS.md   # 全部设计决策与踩坑史（开发必读）
```

## 分层

```
表现层  web/           纯静态，HTTP 传输
服务层  handle_*()     业务处理器（disk_cleanup.py / appdata_scan.py）
数据层  WinAPI / os.scandir / 环境变量
```

## 后台任务模型

统一任务管理器（disk_cleanup.py `_start_task / _task_view / _cancel_task`）：
- 所有扫描/清理/迁移均为后台线程，前端轮询 `/api/disk/scan-status?scan_id=`
- task 字典含 `_cancel` Event、`progress`（限频上报）、`result`；大对象（目录树）存
  `task["_tree"]` 不进轮询序列化，经 `/api/disk/tree` 懒展开
- 同类任务运行中复用（幂等）；清理互斥锁防并发

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/disk/overview | C 盘容量概览 |
| GET | /api/disk/scan?type=junk\|large\|tree | 启动扫描 |
| GET | /api/disk/scan-status?scan_id= | 轮询任务 |
| GET | /api/disk/scan-cancel?scan_id= | 取消 |
| GET | /api/disk/cleanup?categories=a,b | 启动清理（白名单key） |
| GET | /api/disk/tree?scan_id=&path= | 目录树懒展开 |
| GET | /api/disk/drives | 全部固定磁盘 |
| GET | /api/disk/open-location?path= | 资源管理器定位 |
| GET | /api/appdata/scan | 应用数据盘点 |
| GET | /api/appdata/drives | 迁移目标盘（非C） |
| GET | /api/appdata/migrate?path=&target= | 启动迁移 |
| GET | /api/appdata/delete?paths=a|b | 启动删除（白名单校验） |
| GET | /api/installers/scan?scope=&custom= | 安装包扫描 |

## 与 winhelper 主应用的关系

- 本项目由 winhelper 的磁盘清理功能**迁移**而来，代码同源；功能演进以本项目为准
- **同步契约（2026-09-05 起）**：winhelper 已移除 B/S Web 模式（commit ea5fada，app.py 删除、
  handle_* 抽至 service.py、Flask 移除），今后向 winhelper 发布**只同步 4 个文件**——
  disk_cleanup.py / appdata_scan.py / web/disk.js / web/appdata.js，不再涉及任何 app.py/Flask 内容
- **分叉点**：web/index.html 两边已不同（主应用是多Tab故障分析+磁盘清理；本项目纯磁盘清理单页）；
  style.css / app-lite.js（主应用为 app.js）修改时需双向人工比对，不能盲目复制
- 本项目 Flask 保留：它是独立项目的运行时与 E2E 验证载体（ADR-010），不受主应用技术栈变化影响
