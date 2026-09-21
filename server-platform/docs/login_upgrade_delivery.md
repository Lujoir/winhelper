# 观枢终端平台｜EyeTerm — 控制台登录改造交付报告

- 交付日期：2026-09-09
- 部署位置：172.17.5.215:18090（/data/terminal-platform/）
- 合规基线：GB/T 22239-2019 第三级（8.1.4.1 身份鉴别 / 8.1.4.3 安全审计 / 8.1.4.8 数据保密性 / 8.1.4.10 剩余信息保护）
- 结论：**改造完成，全部验证通过，已上线**

## 1. 改造内容

| 模块 | 文件 | 说明 |
|------|------|------|
| 鉴别核心 | server/auth_upgrade.py | PBKDF2-HMAC-SHA256（320k 轮自描述哈希）、SQLite 持久会话、失败锁定、IP 限速、审计，零第三方依赖 |
| API 接入 | server/api.py | 登录分支接 auth.authenticate；控制台鉴权点切换 auth.resolve_session；新增 POST /api/v1/console/password（改密后吊销其它会话）；移除重复 if |
| 入口 | server/app.py | auth.DB_PATH 跟随 data_dir（与 eyeterm.db 同目录） |
| 前端 | console/index.html | 登录框加用户名；must_change_password 强制改密弹窗；423/429 重试提示；401 清 token |
| 迁移 | server/migrate_login_upgrade.py | 幂等迁移（13 列 + 5 表 + 13 索引 + 审计防篡改触发器 + 17 项安全策略默认值） |
| 数据库 | data/console_auth.db | eyeterm:eyeterm 600；admin 直插 PBKDF2 哈希（无明文） |

## 2. 安全能力

- 口令：PBKDF2 320k 轮存储，明文口令在库中清零（password_algo=pbkdf2_sha256）
- 会话：SQLite 持久化（重启不掉线），空闲 30min / 绝对 12h，UA 指纹绑定
- 锁定：连续 5 次失败锁 30min（423）；IP 限速 10 次/60s（429），限速前置于慢哈希
- 改密：复杂度校验 + 历史 3 次不重复 + 吊销其它会话；防枚举时延一致
- 审计：append-only（触发器禁 UPDATE/DELETE），已记录 30 条事件（login.success/failure/blocked/locked、session.created、pwd.changed）

## 3. 验证结果（全部通过）

- 本地链路冒烟 6/6（登录/会话/401/改密/新口令/423）
- 远程冒烟 10/10：登录 200 → 会话鉴权 200 → 改密 → 旧口令 401 → 新口令 200 → 改回并踢端 → **重启后 SQLite 会话保持 200**、被踢 token 401 → 5 次失败 423 → 管理员解锁恢复 200
- config 重建后最终验证 3/3：PBKDF2 登录、业务库数据完好（2 终端）、真实终端心跳在线（token 正确）

## 4. S7 剩余信息保护

- config.json 移除明文 console_password（重建后 8 键，eyeterm:eyeterm 600）；全盘按口令值复扫零残留
- eyeterm.db / console_auth.db 均已 VACUUM，WAL/SHM 清除
- 部署备份链：api.py/app.py/index.html 各 3 份时间戳备份保留于服务器

## 5. 事故记录（处理完成）

S7 apply 时清理逻辑误删 config.json（仅此 1 处明文口令载体）。恢复：按 deploy.py 首次部署模板 + 记忆中的 terminal_token 重建；期间 DEV 默认配置短暂运行 49s，终端 token 不匹配全部 401 拒绝，无数据污染；清理了误建脏目录 app/data。重建后全量验证通过。

**教训**：① 自动化删除循环必须内置路径白名单（本次脚本已修复为"代码/文档 KEEP"逻辑）；② config.json 无自动备份是单点，已纳入 deploy 流程认知——变更前必须先备份；③ 含 `$` 的哈希串绝不能经 bash 双引号命令行传递（`$3`/`$salt` 会被参数展开），必须走文件或 stdin。

## 6. 遗留事项

- 本地代码变更（api.py/app.py/index.html/auth_upgrade.py/migrate_login_upgrade.py/schema + tools 6 个运维脚本）待 Git 提交
- 改密端点已就绪但控制台暂无"手动改密"入口（must_change_password 强制路径已通），可按需加按钮
- 等保双因素（R-01 不符合项）仍待二期
