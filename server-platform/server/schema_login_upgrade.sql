-- =============================================================================
-- 后台管理控制台 登录改造 建表脚本
-- 目标库    : SQLite 3（WAL 模式）
-- 适用架构  : Python 标准库 http.server + sqlite3，零第三方依赖（ADR-002）
-- 合规基线  : GB/T 22239-2019《信息安全技术 网络安全等级保护基本要求》第三级
-- 幂等性    : CREATE TABLE / INDEX 均带 IF NOT EXISTS，可重复执行
--             ALTER TABLE 部分 SQLite 不支持 IF NOT EXISTS，请用 migrate_login_upgrade.py
--             执行（内部通过 PRAGMA table_info 检测后再增列），或手工核对后单条执行
-- -----------------------------------------------------------------------------
-- 【重要】表名占位说明
--   本脚本假定现有账号表名为 console_users，主键为 id，登录名列为 username。
--   若实际表名/列名不同，请先全局替换后再执行：
--     console_users -> <你的账号表名>
-- =============================================================================

PRAGMA journal_mode = WAL;          -- 现有架构已启用，此处显式声明以防迁移环境未开
PRAGMA foreign_keys = ON;           -- 会话/历史表依赖账号表，需开启外键约束
PRAGMA busy_timeout = 5000;         -- http.server 多线程并发写入时避免 database is locked


-- =============================================================================
-- 一、账号表增列（改造点①口令哈希、④失败计数锁定）
--     对应等保三级 8.1.4.1 身份鉴别 a) b) / 8.1.4.8 数据保密性 b)
-- -----------------------------------------------------------------------------
-- 设计要点：
--   1. 不新建账号表、不迁移主键，最小侵入，避免影响现有业务外键。
--   2. password_hash 采用「自描述」字符串格式，算法与迭代次数随哈希一起存储，
--      未来提升迭代次数或更换算法时可再次平滑迁移，无需改表结构：
--        明文（改造前遗留）  : <明文口令原样>
--        PBKDF2（改造后）    : pbkdf2_sha256$<iterations>$<salt_b64>$<dk_b64>
--   3. 保留原口令列不删除、不清空，仅在迁移成功后由程序改写为哈希串，
--      便于灰度期回滚（回滚窗口结束后再执行收尾清理）。
-- =============================================================================

-- 口令与算法
ALTER TABLE console_users ADD COLUMN password_algo TEXT NOT NULL DEFAULT 'plain';
    -- 取值：'plain'（遗留明文，待迁移） | 'pbkdf2_sha256'（已迁移）
ALTER TABLE console_users ADD COLUMN password_updated_at INTEGER;
    -- 口令最后修改时间（Unix 秒）。用于口令有效期（默认 90 天）到期强制改密
ALTER TABLE console_users ADD COLUMN password_must_change INTEGER NOT NULL DEFAULT 0;
    -- 1 = 下次登录强制改密。用于「管理员重置口令」「初始口令」「口令过期」三种场景
    -- 对应等保三级 8.1.4.1 a)「口令应有复杂度要求并定期更换」

-- 登录失败处理（等保三级 8.1.4.1 b)）
ALTER TABLE console_users ADD COLUMN failed_attempts INTEGER NOT NULL DEFAULT 0;
    -- 连续失败次数，登录成功或计数窗口过期时归零
ALTER TABLE console_users ADD COLUMN first_failed_at INTEGER;
    -- 本轮首次失败时间，用于滑动计数窗口（默认 900 秒）判定
ALTER TABLE console_users ADD COLUMN locked_until INTEGER;
    -- 锁定截止时间（Unix 秒）。NULL 或 <= now 表示未锁定
ALTER TABLE console_users ADD COLUMN lock_count INTEGER NOT NULL DEFAULT 0;
    -- 历史累计被锁次数，供安全管理员识别持续爆破目标

-- 账号状态与登录痕迹
ALTER TABLE console_users ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
    -- 'active' 正常 | 'disabled' 停用 | 'expired' 账号到期
    -- 注意：临时锁定不写入此列，由 locked_until 表达，二者语义分离便于自动解锁
ALTER TABLE console_users ADD COLUMN last_login_at INTEGER;
ALTER TABLE console_users ADD COLUMN last_login_ip TEXT;
ALTER TABLE console_users ADD COLUMN last_failed_at INTEGER;
ALTER TABLE console_users ADD COLUMN last_failed_ip TEXT;
    -- 登录成功后在页面提示「上次登录时间/地址」，属等保三级鼓励的用户侧异常自查手段

-- 三权分立角色（等保三级 8.1.4.2 访问控制 / 8.1.4.3 安全审计 b)）
ALTER TABLE console_users ADD COLUMN role TEXT NOT NULL DEFAULT 'operator';
    -- 'sysadmin'  系统管理员：账号增删改、业务配置，不可查改审计日志
    -- 'auditadmin' 审计管理员：只读审计日志与导出，不可管账号
    -- 'secadmin'  安全管理员：安全策略（口令策略/锁定阈值/限速阈值），不可管账号、不可改审计
    -- 'operator'  普通操作员：仅业务功能

CREATE INDEX IF NOT EXISTS idx_users_status       ON console_users(status);
CREATE INDEX IF NOT EXISTS idx_users_locked_until ON console_users(locked_until);


-- =============================================================================
-- 二、控制台会话表（改造点②会话落库，重启不掉线）
--     对应等保三级 8.1.4.1 b)「登录连接超时自动退出」/ c)「防止鉴别信息窃听」
-- -----------------------------------------------------------------------------
-- 安全设计要点（务必遵守，否则失去落库的安全意义）：
--   1. **token 绝不落库**。仅存 SHA-256(token) 的十六进制值。
--      库文件被拖走 / 备份泄露 / 运维误查表，均无法反推出可用会话凭据。
--      SHA-256 不加盐即可：token 为 secrets.token_urlsafe(32) 产生的 256 位高熵随机值，
--      不存在字典穷举空间，无需 PBKDF2 慢哈希（也避免每次鉴权都付慢哈希开销）。
--   2. **双超时**：idle_expires_at 滑动续期（默认 30 分钟无操作失效），
--      absolute_expires_at 绝对上限（默认 12 小时），防止长期活跃会话永不失效。
--   3. **指纹弱绑定**：记录 User-Agent 哈希与登录 IP，用于会话盗用检测。
--      默认仅告警不阻断（移动网络出口 IP 漂移会造成误杀），可由安全管理员切换为阻断。
--   4. **可吊销**：改密、停用账号、管理员强制下线时批量置 revoked_at，实现全端下线。
-- =============================================================================

CREATE TABLE IF NOT EXISTS console_sessions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 凭据：只存哈希，长度固定 64（SHA-256 hex）。
    -- 唯一性由下方命名唯一索引 idx_sessions_token 承担，不加内联 UNIQUE，
    -- 避免 SQLite 再建一个 sqlite_autoindex（两份索引同一件事，白付写入开销）。
    token_hash            TEXT    NOT NULL,

    -- 归属
    user_id               INTEGER NOT NULL,
    username              TEXT    NOT NULL,   -- 冗余快照：账号改名/删除后审计仍可追溯

    -- 时间轴（均为 Unix 秒，INTEGER 存储便于比较与建索引）
    created_at            INTEGER NOT NULL,
    last_seen_at          INTEGER NOT NULL,
    idle_expires_at       INTEGER NOT NULL,   -- 滑动超时，每次鉴权成功后顺延
    absolute_expires_at   INTEGER NOT NULL,   -- 绝对超时，创建时一次性确定，永不顺延

    -- 客户端指纹
    client_ip             TEXT,
    ua_hash               TEXT,               -- SHA-256(User-Agent) 前 16 字节 hex，避免存长串

    -- 生命周期
    revoked_at            INTEGER,            -- NULL = 有效
    revoke_reason         TEXT,               -- 'logout' | 'password_changed' | 'admin_kick'
                                              -- | 'account_disabled' | 'idle_timeout'
                                              -- | 'absolute_timeout' | 'fingerprint_mismatch'

    -- 会话内标记
    mfa_passed            INTEGER NOT NULL DEFAULT 0,  -- 预留：本次未启用双因素，恒为 0
    must_change_password  INTEGER NOT NULL DEFAULT 0,  -- 1 = 该会话仅可访问改密接口

    FOREIGN KEY (user_id) REFERENCES console_users(id) ON DELETE CASCADE
);

-- 鉴权主查询路径：WHERE token_hash = ? AND revoked_at IS NULL
CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_token   ON console_sessions(token_hash);
-- 定期清理与「我的登录设备」列表
CREATE INDEX IF NOT EXISTS idx_sessions_idle_exp       ON console_sessions(idle_expires_at);
CREATE INDEX IF NOT EXISTS idx_sessions_abs_exp        ON console_sessions(absolute_expires_at);
CREATE INDEX IF NOT EXISTS idx_sessions_user_active    ON console_sessions(user_id, revoked_at);


-- =============================================================================
-- 三、口令历史表（等保三级 8.1.4.1 a) 口令定期更换的配套约束）
-- -----------------------------------------------------------------------------
-- 作用：新口令不得与最近 N 次（默认 3 次）历史口令相同，防止「改回原口令」绕过定期更换。
-- 存储：仅存 PBKDF2 哈希串；校验时用历史记录自身的 salt/iterations 重算比对。
-- 保留：每账号仅保留最近 N+1 条，由程序在写入后删除超出部分，避免表无限膨胀。
-- =============================================================================

CREATE TABLE IF NOT EXISTS console_password_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    password_hash TEXT    NOT NULL,        -- pbkdf2_sha256$...，不存明文
    changed_at    INTEGER NOT NULL,
    changed_by    TEXT,                    -- 'self' 用户自助 | 'admin:<username>' 管理员重置
    FOREIGN KEY (user_id) REFERENCES console_users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pwdhist_user ON console_password_history(user_id, changed_at DESC);


-- =============================================================================
-- 四、登录限速表（改造点③限速审计；等保三级 8.1.4.4 入侵防范）
-- -----------------------------------------------------------------------------
-- 与「账号失败计数锁定」的分工，二者缺一不可：
--   · 账号维度锁定（console_users.locked_until）：防单账号定向爆破
--   · IP 维度限速（本表）                     ：防撞库/喷洒攻击（每 IP 轮换账号试少量口令，
--                                               永不触发单账号阈值）
-- 落库而非内存：进程重启后限速状态不丢失，否则攻击者只需触发一次重启即清零。
-- 【性能要点】限速判定必须**前置于 PBKDF2 计算**，否则攻击者可用高频请求
--             拖垮单线程 http.server（每次哈希约 200ms，即成 DoS 放大器）。
-- =============================================================================

CREATE TABLE IF NOT EXISTS console_login_throttle (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type    TEXT    NOT NULL,        -- 'ip' | 'user' | 'ip_user'
    scope_key     TEXT    NOT NULL,        -- 对应 IP 串 / 用户名 / "ip|user"
    window_start  INTEGER NOT NULL,        -- 当前计数窗口起点（Unix 秒）
    attempts      INTEGER NOT NULL DEFAULT 0,
    blocked_until INTEGER,                 -- 命中阈值后的封禁截止时间
    updated_at    INTEGER NOT NULL,
    UNIQUE (scope_type, scope_key)
);

CREATE INDEX IF NOT EXISTS idx_throttle_blocked ON console_login_throttle(blocked_until);


-- =============================================================================
-- 五、安全审计表（改造点③补全登录成功/失败/限速审计）
--     对应等保三级 8.1.4.3 安全审计 a) b) c) d)
-- -----------------------------------------------------------------------------
-- 【若现有架构已有审计表】不要重复建表，改为按下方字段清单核对补齐缺失列即可。
-- 等保三级 8.1.4.3 a) 要求审计记录至少覆盖：事件的日期和时间、用户、事件类型、
-- 事件是否成功、及其他与审计相关的信息 —— 下表字段与之逐项对应。
-- =============================================================================

CREATE TABLE IF NOT EXISTS console_audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,

    occurred_at INTEGER NOT NULL,          -- 【日期和时间】Unix 秒，UTC 存储，展示时转本地
    occurred_ms INTEGER,                   -- 毫秒补位，保证同秒内多事件可稳定排序

    event_type  TEXT    NOT NULL,          -- 【事件类型】见下方事件字典
    result      TEXT    NOT NULL,          -- 【是否成功】'success' | 'failure' | 'blocked'

    username    TEXT,                      -- 【用户】登录失败时也要记录用户输入的账号名
    user_id     INTEGER,                   -- 账号不存在时为 NULL

    client_ip   TEXT,                      -- 【其他相关信息】
    user_agent  TEXT,
    session_id  INTEGER,                   -- 关联 console_sessions.id，便于串联同会话行为
    target      TEXT,                      -- 被操作对象（如管理员重置了哪个账号）
    reason      TEXT,                      -- 失败原因内部码：'bad_password' | 'no_such_user'
                                           -- | 'account_locked' | 'account_disabled'
                                           -- | 'throttled' | 'pwd_policy' | 'pwd_reused'
    detail      TEXT                       -- JSON 文本，放可变附加字段（标准库 json 序列化）
);

-- 事件字典（event_type 取值约定，程序侧应使用常量而非裸字符串）：
--   login.success            登录成功
--   login.failure            登录失败（口令错误 / 账号不存在，对外提示须一致）
--   login.blocked            被拒绝（账号锁定 / 停用 / 限速命中）
--   login.locked             账号因连续失败被锁定（阈值触发瞬间记一条）
--   login.unlocked           锁定自动到期解除 或 管理员手工解锁
--   logout                   主动注销
--   session.created          会话创建
--   session.expired          会话因空闲/绝对超时失效
--   session.revoked          会话被吊销（改密、强制下线）
--   session.fingerprint_warn 会话指纹（IP/UA）与创建时不一致
--   password.changed         用户自助改密成功
--   password.change_failed   改密失败（旧口令错 / 复杂度不足 / 与历史重复）
--   password.reset           管理员重置口令
--   password.migrated        遗留明文口令自动迁移为 PBKDF2 哈希
--   access.denied            越权访问尝试（角色不足），含当前角色与所需角色
--   account.created / account.updated / account.disabled / account.enabled / account.deleted
--   policy.updated           安全策略变更（口令策略、锁定阈值、限速阈值）
--   audit.exported           审计日志导出（导出行为本身也必须留痕）

CREATE INDEX IF NOT EXISTS idx_audit_time    ON console_audit_log(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_type    ON console_audit_log(event_type, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_user    ON console_audit_log(username, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_ip      ON console_audit_log(client_ip, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_result  ON console_audit_log(result, occurred_at DESC);


-- -----------------------------------------------------------------------------
-- 5.1 审计记录防篡改/防删除（等保三级 8.1.4.3 c)「审计记录应受保护，
--     定期备份，避免受到未预期的删除、修改或覆盖」）
-- 用数据库触发器在库层面拒绝 UPDATE / DELETE，即使有人拿到 sqlite3 命令行也改不动。
-- 归档裁剪需求：先 DROP TRIGGER -> 归档 -> 重建 TRIGGER，该操作本身应留痕并双人复核。
-- -----------------------------------------------------------------------------

DROP TRIGGER IF EXISTS trg_audit_no_update;
CREATE TRIGGER trg_audit_no_update
BEFORE UPDATE ON console_audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit log is append-only: UPDATE denied');
END;

DROP TRIGGER IF EXISTS trg_audit_no_delete;
CREATE TRIGGER trg_audit_no_delete
BEFORE DELETE ON console_audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit log is append-only: DELETE denied');
END;


-- =============================================================================
-- 六、安全策略参数表（供安全管理员在线调整，避免改代码重启）
--     对应等保三级「安全参数可配置」要求
-- =============================================================================

CREATE TABLE IF NOT EXISTS console_security_policy (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    value_type  TEXT NOT NULL DEFAULT 'int',   -- 'int' | 'str' | 'bool'
    description TEXT,
    updated_at  INTEGER,
    updated_by  TEXT
);

INSERT OR IGNORE INTO console_security_policy (key, value, value_type, description) VALUES
    ('pwd_min_length',        '10',     'int',  '口令最小长度'),
    ('pwd_min_classes',       '3',      'int',  '口令需覆盖的字符类别数（大写/小写/数字/符号）'),
    ('pwd_max_age_days',      '90',     'int',  '口令最长使用天数，到期强制改密，0=不限'),
    ('pwd_history_count',     '3',      'int',  '禁止与最近 N 次历史口令重复'),
    ('pbkdf2_iterations',     '320000', 'int',  '新口令 PBKDF2-HMAC-SHA256 迭代次数'),
    ('lock_threshold',        '5',      'int',  '连续失败达此次数即锁定账号'),
    ('lock_window_seconds',   '900',    'int',  '失败计数滑动窗口（秒）'),
    ('lock_duration_seconds', '1800',   'int',  '账号锁定时长（秒），0=需管理员手工解锁'),
    ('ip_window_seconds',     '60',     'int',  'IP 限速统计窗口（秒）'),
    ('ip_max_attempts',       '10',     'int',  '单 IP 窗口内最大登录尝试次数'),
    ('ip_block_seconds',      '600',    'int',  'IP 触发限速后的封禁时长（秒）'),
    ('session_idle_seconds',  '1800',   'int',  '会话空闲超时（秒），等保要求超时自动退出'),
    ('session_abs_seconds',   '43200',  'int',  '会话绝对最长存活（秒）'),
    ('session_max_per_user',  '5',      'int',  '单账号并发会话上限，超出踢最旧'),
    ('bind_session_ip',       '0',      'bool', '会话 IP 变化是否直接阻断（0=仅告警）'),
    ('bind_session_ua',       '1',      'bool', '会话 User-Agent 变化是否直接阻断'),
    ('audit_retain_days',     '365',    'int',  '审计日志保留天数（等保三级底线 180 天）');


-- =============================================================================
-- 七、验收自检（执行完毕后运行，用于确认改造到位）
-- =============================================================================
-- SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'console_%';
-- PRAGMA table_info(console_users);        -- 核对新增 15 列是否齐全
-- PRAGMA index_list(console_sessions);     -- 核对 token_hash 唯一索引存在
-- SELECT COUNT(*) FROM console_users WHERE password_algo='plain';  -- 待迁移明文账号数
-- SELECT key, value FROM console_security_policy ORDER BY key;
-- 期望：明文账号数随用户陆续登录自然归零；若长期不归零说明存在僵尸账号，应停用清理。
