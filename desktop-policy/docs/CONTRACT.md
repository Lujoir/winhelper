# desktop-policy · 终端↔服务端契约 v1.1（冻结稿）

> 2026-09-16 冻结；同日 v1.1 修订三处（server-platform-dev 提案，双方确认采纳）：
> ①壁纸上传 POST multipart → raw bytes PUT；②wallpaper_id 统一整数；
> ③group_id 绑定平台 asset_groups + publish 语义定稿。
> 变更须经双方确认后升版。传输：平台 HTTPS 18443 终端 API，头部 `X-ETP-Token`。
> 铁律：字段只增不删不改义；枚举值只增不改；时间戳 epoch 秒（int）；错误码小写下划线。

## 1. 终端 → 服务端

### 1.1 策略拉取（终端轮询，默认 120s 可配置）

`GET /api/v1/terminals/{tid}/desktoppolicy/policy?revision={last}&mi={monitors简报}`

- `mi` 简报格式：`idx,w,h,primary;idx,w,h,primary`（URL 安全，完整显示器信息经 1.2 report 上报）。
- **响应（有更新）**：

```json
{
  "ok": true,
  "revision": 42,
  "server_time": 1726450000,
  "policies": {
    "desktop_wallpaper": {
      "enabled": true,
      "mode": "stretch",
      "per_monitor": [
        {"monitor_index": 0, "wallpaper_id": 12, "match": "exact",
         "checksum": "sha256hex（可选，终端缓存校验）"},
        {"monitor_index": 1, "wallpaper_id": 5, "match": "aspect_higher_res"}
      ],
      "rotation": {"freq": "daily", "anchor_date": "2026-10-01"}
    },
    "lock_screen": {"enabled": true, "wallpaper_id": 3},
    "power_plan": {
      "enabled": true,
      "plan": "custom",
      "custom": {
        "display_off_ac": 600, "display_off_dc": 300,
        "sleep_ac": 1800, "sleep_dc": 900,
        "disk_off_ac": 1200, "disk_off_dc": 720,
        "power_button_ac": "sleep", "power_button_dc": "shutdown"
      }
    },
    "idle_lock": {"enabled": true, "minutes": 15, "screen_saver_secure": true}
  }
}
```

- **响应（无更新）**：`{"ok": true, "unchanged": true, "revision": 42}`
- **匹配推荐算法（服务端执行，match 枚举）**：`exact`（分辨率完全一致）→ `aspect_higher_res`（宽高比一致且壁纸 ≥ 目标屏）→ `default_fallback`（无匹配，下发默认壁纸 + 服务端告警记录）。
- `mode` 枚举：`fill|fit|stretch|tile|center`（stretch=拼接图模式，终端默认）。
- **壁纸文件下载**：`GET /api/v1/terminals/{tid}/desktoppolicy/wallpaper/{wallpaper_id}` → 图片二进制（`X-DP-Checksum` 头带 sha256；终端校验后缓存复用）。

### 1.2 执行结果回传（执行完成/失败/被拦截时）

`POST /api/v1/terminals/{tid}/desktoppolicy/report`

```json
{
  "revision": 42,
  "reported_at": 1726450123,
  "monitors": [
    {"index": 0, "device": "\\\\.\\DISPLAY1", "width": 2560, "height": 1440,
     "primary": true, "dpi": 100, "offset_x": 0, "offset_y": 0}
  ],
  "virtual": {"x": 0, "y": 0, "width": 4240, "height": 1440},
  "session_type": "console",
  "results": [
    {"policy": "desktop_wallpaper", "ok": true,
     "detail": {"mode": "stretch", "monitors": 2, "verify": "grab_ok"},
     "duration_ms": 820},
    {"policy": "lock_screen", "ok": false,
     "error": {"code": "no_admin", "message": "锁屏设置需管理员权限"}},
    {"policy": "power_plan", "ok": true,
     "detail": {"plan": "custom", "backup_guid": "6708c478-...",
                "rollback": "not_needed"}},
    {"policy": "idle_lock", "ok": true, "detail": {"minutes": 15}}
  ]
}
```

**error.code 枚举（冻结，只增不改）**：

| code | 语义 | 触发通道 |
|---|---|---|
| `blocked_by_security` | 终端安全软件拦截壁纸变更（自检三件套不过，ADR-004） | desktop_wallpaper |
| `no_admin` | 缺管理员权限（HKLM/提权助手不可用） | lock_screen |
| `rdp_skipped` | RDP/非 console 会话，按策略跳过本地应用 | desktop_wallpaper / lock_screen |
| `file_missing` | 壁纸文件下载失败或校验不过 | 全部 |
| `apply_failed` | 重试 3 次（指数退避）后仍失败 | 全部 |
| `rollback_ok` | 执行失败后已按备份还原 | power_plan |
| `rollback_failed` | 还原也失败（最高告警） | power_plan |

**重试语义**：失败重试 3 次（2s/8s/32s 退避）；`rdp_skipped` / `blocked_by_security` 不重试直接上报。

## 2. 服务端 console API（/api/v1/console/desktoppolicy/*）

对齐 huorong 路由组形态（只读缓存优先 + admin 分离 + 403 审计）：

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| GET | `/overview` | 统计：策略数/资源数/近7日下发成功与告警数/拦截数 | console 会话 |
| GET | `/wallpapers` | 资源库列表（分类/分辨率/宽高比/大小筛选，分页） | console 会话 |
| POST | `/wallpapers` | 上传壁纸（~~multipart~~ **v1.1 改 raw bytes PUT**，见下） | admin（403+审计） |
| DELETE | `/wallpapers/{id}` | 下架壁纸（引用中拒绝） | admin（403+审计） |
| GET | `/policies` | 策略列表（含绑定终端组与生效 revision） | console 会话 |
| POST | `/policies` | 新建/修改策略（四类策略 payload 同 1.1 响应结构） | admin（403+审计） |
| POST | `/policies/{id}/publish` | 发布到终端组（revision+1，写下发记录） | admin（403+审计） |
| GET | `/deliveries` | 下发记录列表（终端/策略/状态/错误码筛选，分页） | console 会话 |

**v1.1 定稿说明**：
1. **壁纸上传改 raw bytes PUT**（方案 a，零新依赖）：`PUT /console/desktoppolicy/wallpapers/{name}?category=&mime=` body=图片原始二进制；服务端解析分辨率/宽高比/大小/sha256 入库，sha256 重复返回既有 id，重名 409。旧 POST /wallpapers（multipart）作废。
2. **wallpaper_id 全链统一为整数**（dp_wallpapers.id）：payload `"wallpaper_id": 12`（数字）、终端 GET `/wallpaper/12`、缓存文件名 `cache\12`（免扩展名歧义，格式由文件头判定）。
3. **group_id 绑定平台 asset_groups**；`group_id: null` = 全部终端。
4. **publish 语义**：仅 bump revision + 写组级发布事件；dp_deliveries 行在终端首次拉取 policy 时 upsert（pending→delivered），组内终端动态增减不产生记录漂移。

**下发记录状态枚举**：`pending`（已发布待终端拉取）→ `delivered`（终端已拉取）→ `applied`（全策略 ok）→ `partial`（部分 ok）→ `failed`（全失败）→ `warn`（default_fallback 或 blocked_by_security）。

## 3. 服务端 SQLite 新表（迁移脚本）

```sql
CREATE TABLE dp_wallpapers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'default',
  width INTEGER NOT NULL, height INTEGER NOT NULL,
  aspect REAL NOT NULL, size_bytes INTEGER NOT NULL,
  sha256 TEXT NOT NULL UNIQUE, path TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL, created_by TEXT NOT NULL DEFAULT '');

CREATE TABLE dp_policies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, group_id INTEGER,
  payload TEXT NOT NULL,              -- JSON（1.1 policies 结构）
  revision INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  updated_at INTEGER NOT NULL, updated_by TEXT NOT NULL DEFAULT '');

CREATE TABLE dp_deliveries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  policy_id INTEGER NOT NULL, revision INTEGER NOT NULL,
  terminal_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  error_code TEXT, error_detail TEXT,
  reported_at INTEGER,
  created_at INTEGER NOT NULL);
```

## 4. 终端本地存储

- 策略缓存：`%LOCALAPPDATA%\winhelper\desktop_policy\state.json`（revision + policies + 每策略上次应用结果）
- 壁纸缓存：`%LOCALAPPDATA%\winhelper\desktop_policy\cache\{wallpaper_id}`（整数 id，免扩展名；sha256 校验，v1.1）
- 拼接产物：`%LOCALAPPDATA%\winhelper\desktop_policy\stitch.png`（虚拟桌面尺寸变化时重建）
- 日志：`%LOCALAPPDATA%\winhelper\desktop_policy\logs\dp_YYYYMMDD.log`（日期分文件，简体中文文案）

## 5. 集成点（主应用，P2）

- bridge.py ROUTES：`/api/desktoppolicy/status|policy-now|apply-now|task-status|logs`（status 读本地状态；policy-now/apply-now 后台任务返回 task_id 经 task-status 轮询；logs 返回日志目录与尾部；「打开日志目录」复用 /api/disk/open-location）
- index.html：导航 `data-tab="desktoppolicy"`（filesearch 之后），菜单名「锁屏及壁纸管理」
- app.js：switchTab 守卫 `initDesktopPolicyTab()`；UI 遵循 net-doctor/docs/STYLE.md 卡片基线（dp 前缀），文案零实现细节、占位禁真实业务 IP
