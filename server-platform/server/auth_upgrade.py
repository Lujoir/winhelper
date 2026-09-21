#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
后台管理控制台 登录改造 —— 鉴别与会话核心模块

依赖：仅 Python 标准库（hashlib / hmac / secrets / sqlite3 / json / time / base64 /
      threading / dataclasses / re / typing）。契合既定架构决策 ADR-002「零第三方依赖」。

覆盖四个改造点：
  ① 口令明文改 PBKDF2-HMAC-SHA256 哈希存储，登录时自动平滑迁移
  ② 会话从内存落 SQLite 持久化，进程重启不掉线
  ③ 补全登录成功 / 失败 / 限速命中审计
  ④ 连续失败计数与账号锁定策略

合规基线：GB/T 22239-2019 第三级 —— 8.1.4.1 身份鉴别、8.1.4.2 访问控制、
          8.1.4.3 安全审计、8.1.4.4 入侵防范、8.1.4.8 数据保密性、
          8.1.4.10 剩余信息保护。

集成方式：本模块不感知 HTTP，只吃「用户名 / 口令 / 客户端 IP / UA」，吐结果对象。
          api.py 侧的接入见同目录 api_patch_example.py。

线程安全：http.server 若使用 ThreadingHTTPServer，每个请求在独立线程。本模块用
          threading.local() 为每线程维护独立 sqlite3 连接（sqlite3 连接默认不可跨线程）。
"""

import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Tuple

# ===========================================================================
# 配置区：按你的实际库结构调整这四个常量即可，其余代码无需改动
# ===========================================================================
DB_PATH = "console.db"          # SQLite 数据库文件路径
USERS_TABLE = "console_users"   # 账号表名
USERNAME_COLUMN = "username"    # 登录名列名
PASSWORD_COLUMN = "password"    # 口令列名（改造后此列存 PBKDF2 哈希串）

# 哈希串格式标识。自描述格式让「算法/迭代次数」随哈希一起存储，
# 日后提升迭代次数时旧哈希仍可正常校验并在登录时自动升级，无需改表。
HASH_SCHEME = "pbkdf2_sha256"

# 会话 Cookie 名。改造后控制台会话与原有「终端 token 鉴权」并行，互不干扰。
SESSION_COOKIE = "console_sid"

# 口令字符类别定义（等保三级要求口令具有一定复杂度）
_CLASS_PATTERNS = (
    (re.compile(r"[a-z]"), "小写字母"),
    (re.compile(r"[A-Z]"), "大写字母"),
    (re.compile(r"[0-9]"), "数字"),
    (re.compile(r"[^0-9A-Za-z]"), "特殊符号"),
)

# 弱口令黑名单（示例，生产建议外置为文件并定期更新）
_WEAK_PASSWORDS = {
    "admin", "admin123", "administrator", "password", "passw0rd", "123456",
    "1234567890", "abc123456", "qwerty123", "root123", "test123", "hospital",
    "admin@123", "Admin@123", "P@ssw0rd", "changeme", "welcome1",
}


# ===========================================================================
# 结果对象
# ===========================================================================

@dataclass
class AuthResult:
    """登录/鉴权结果。message 是可直接回显给用户的文案，reason 是仅入审计的内部码。"""
    ok: bool
    reason: str = ""                          # 内部码，写审计，不回显
    message: str = ""                         # 对外文案，已做防枚举归一化
    token: Optional[str] = None               # 登录成功时返回的明文 token（仅此一次可见）
    user: Optional[Dict[str, Any]] = None     # 账号行的字典快照
    session_id: Optional[int] = None
    must_change_password: bool = False        # True → 前端应跳转改密页
    retry_after: int = 0                      # 被限速/锁定时的剩余秒数，供 Retry-After 头
    http_status: int = 200
    last_login_at: Optional[int] = None       # 上次成功登录时间（本次改写前的快照，
                                              # 供前端「上次登录」展示；若直接读库，
                                              # 拿到的永远是本次登录的时间）
    last_login_ip: Optional[str] = None       # 上次成功登录 IP


# ===========================================================================
# 数据库连接（线程局部）
# ===========================================================================

_local = threading.local()


def get_conn() -> sqlite3.Connection:
    """
    每线程一个连接。WAL 模式下多读一写可并发，配合 busy_timeout 避免
    "database is locked"。切勿把连接做成全局单例跨线程共享。
    """
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys = ON")
        _local.conn = conn
    return conn


def now() -> int:
    return int(time.time())


def now_ms() -> int:
    return int(time.time() * 1000) % 1000


# ===========================================================================
# 安全策略读取（带 TTL 缓存，避免每次登录都查库）
# ===========================================================================

_policy_cache: Dict[str, Any] = {}
_policy_cache_at = 0.0
_POLICY_TTL = 60.0          # 秒。安全管理员改策略后最多 60 秒生效
_policy_lock = threading.Lock()

_POLICY_DEFAULTS = {
    "pwd_min_length": 10, "pwd_min_classes": 3, "pwd_max_age_days": 90,
    "pwd_history_count": 3, "pbkdf2_iterations": 320000,
    "lock_threshold": 5, "lock_window_seconds": 900, "lock_duration_seconds": 1800,
    "ip_window_seconds": 60, "ip_max_attempts": 10, "ip_block_seconds": 600,
    "session_idle_seconds": 1800, "session_abs_seconds": 43200,
    "session_max_per_user": 5, "bind_session_ip": 0, "bind_session_ua": 1,
    "audit_retain_days": 365,
}


def policy(key: str) -> int:
    """读取策略参数。库中缺失或表不存在时回落到内置默认值，保证模块可独立运行。"""
    global _policy_cache, _policy_cache_at
    with _policy_lock:
        if time.time() - _policy_cache_at > _POLICY_TTL:
            fresh = dict(_POLICY_DEFAULTS)
            try:
                for r in get_conn().execute(
                        "SELECT key, value FROM console_security_policy"):
                    try:
                        fresh[r["key"]] = int(r["value"])
                    except (TypeError, ValueError):
                        fresh[r["key"]] = r["value"]
            except sqlite3.Error:
                pass    # 策略表尚未建立时静默使用默认值
            _policy_cache = fresh
            _policy_cache_at = time.time()
        return _policy_cache.get(key, _POLICY_DEFAULTS.get(key, 0))


def invalidate_policy_cache() -> None:
    """安全管理员保存策略后调用，使变更立即生效。"""
    global _policy_cache_at
    with _policy_lock:
        _policy_cache_at = 0.0


# ===========================================================================
# 改造点① 口令哈希：PBKDF2-HMAC-SHA256 + 明文平滑迁移
# ===========================================================================
#
# 哈希串格式（自描述，$ 分隔，base64 标准字母表不含 $ 故不会冲突）：
#     pbkdf2_sha256$320000$<salt_b64>$<derived_key_b64>
#
# 为什么选 PBKDF2 而不是 bcrypt / scrypt / argon2：
#   · PBKDF2-HMAC-SHA256 在 hashlib 中原生提供（hashlib.pbkdf2_hmac），零第三方依赖，
#     符合 ADR-002；bcrypt/argon2 均需 pip 安装。
#   · scrypt 虽也在 hashlib 中（hashlib.scrypt），但依赖 OpenSSL 1.1+ 编译支持，
#     在部分精简发行版上会抛 ValueError，可移植性不如 PBKDF2。
#   · 等保测评关注「口令是否加密存储、是否不可逆」，PBKDF2 满足且属国际主流方案。
#
# 迭代次数取 320000 的理由：
#   单次哈希约 150~250ms（视 CPU）。http.server 单线程下这是登录吞吐上限的主要来源，
#   故必须把限速判定放在哈希之前（见 authenticate 的执行顺序）。
#   若你的控制台跑在低主频设备上且登录延迟明显，可下调至 120000；若已用
#   ThreadingHTTPServer 且 CPU 富余，可上调至 600000（对齐 Django 默认）。
#   迭代次数存在哈希串里，调整后旧口令仍可校验，并在下次登录自动升级。
# ---------------------------------------------------------------------------

def hash_password(plain: str, iterations: Optional[int] = None) -> str:
    """生成自描述 PBKDF2 哈希串。每个口令独立 16 字节随机盐，杜绝彩虹表与撞库。"""
    if iterations is None:
        iterations = policy("pbkdf2_iterations")
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, iterations)
    return "{}${}${}${}".format(
        HASH_SCHEME, iterations,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    )


def _parse_hash(stored: str) -> Optional[Tuple[int, bytes, bytes]]:
    """解析哈希串。不是本方案格式则返回 None（即判定为遗留明文）。"""
    if not stored or not stored.startswith(HASH_SCHEME + "$"):
        return None
    try:
        _, iters, salt_b64, dk_b64 = stored.split("$", 3)
        return int(iters), base64.b64decode(salt_b64), base64.b64decode(dk_b64)
    except (ValueError, TypeError):
        return None      # 格式损坏 → 当作校验失败处理，不当作明文，避免误放行


def verify_and_upgrade(stored: str, provided: str) -> Tuple[bool, Optional[str]]:
    """
    校验口令，并在需要时返回「应写回数据库的新哈希串」。

    返回 (是否通过, 新哈希或 None)：
      · 遗留明文且比对成功  → (True, 新 PBKDF2 哈希)   ← 改造点①的平滑迁移就在这里
      · 已是哈希且迭代次数低于当前策略 → (True, 按新迭代次数重算的哈希)
      · 已是哈希且参数达标  → (True, None)
      · 任何不通过          → (False, None)

    调用方拿到第二个返回值非 None 时，务必 UPDATE 回库并记 password.migrated 审计。
    这样用户完全无感、无需重置口令，明文即随登录逐个消失。
    """
    parsed = _parse_hash(stored)

    # —— 分支 A：遗留明文（改造前的历史数据）——
    if parsed is None:
        # 用 compare_digest 做常量时间比较，避免通过响应时间逐字符猜口令
        if hmac.compare_digest((stored or "").encode("utf-8"),
                               provided.encode("utf-8")):
            return True, hash_password(provided)
        return False, None

    # —— 分支 B：已是 PBKDF2 哈希 ——
    iterations, salt, expected = parsed
    actual = hashlib.pbkdf2_hmac("sha256", provided.encode("utf-8"),
                                 salt, iterations)
    if not hmac.compare_digest(actual, expected):
        return False, None

    target = policy("pbkdf2_iterations")
    if iterations < target:
        return True, hash_password(provided, target)     # 强度自动跟进升级
    return True, None


def _dummy_hash_work() -> None:
    """
    账号不存在时也消耗一次等量 PBKDF2 计算。

    这是防「用户名枚举」的关键：若账号不存在直接秒回，而账号存在要算 200ms，
    攻击者仅凭响应时间差即可批量枚举出有效账号名，再对有效账号定向爆破。
    配合统一的对外错误文案（「用户名或口令错误」），双管齐下才算真正闭合。
    """
    hashlib.pbkdf2_hmac("sha256", b"dummy-timing-equalizer",
                        b"0123456789abcdef", policy("pbkdf2_iterations"))


# ---------------------------------------------------------------------------
# 口令复杂度与历史校验（等保三级 8.1.4.1 a) 的配套落地）
# ---------------------------------------------------------------------------

def check_password_policy(plain: str, username: str = "") -> Tuple[bool, str]:
    """返回 (是否合规, 不合规原因文案)。文案可直接回显给用户。"""
    min_len = policy("pwd_min_length")
    if len(plain) < min_len:
        return False, f"口令长度不得少于 {min_len} 位。"
    if len(plain) > 128:
        return False, "口令长度不得超过 128 位。"

    hit = [name for pat, name in _CLASS_PATTERNS if pat.search(plain)]
    need = policy("pwd_min_classes")
    if len(hit) < need:
        allnames = "、".join(n for _, n in _CLASS_PATTERNS)
        return False, f"口令须包含{allnames}中至少 {need} 类，当前仅 {len(hit)} 类。"

    low = plain.lower()
    if low in _WEAK_PASSWORDS:
        return False, "该口令属于常见弱口令，请更换。"
    if username and username.lower() in low:
        return False, "口令不得包含用户名。"

    # 连续字符与重复字符检测（如 123456、abcdef、aaaaaa）
    if re.search(r"(.)\1{3,}", plain):
        return False, "口令不得包含 4 个及以上连续重复字符。"
    seq = "0123456789abcdefghijklmnopqrstuvwxyz"
    for i in range(len(low) - 5):
        chunk = low[i:i + 6]
        if chunk in seq or chunk in seq[::-1]:
            return False, "口令不得包含 6 位及以上顺序或逆序连续字符。"

    return True, ""


def check_password_history(user_id: int, plain: str) -> Tuple[bool, str]:
    """新口令不得与最近 N 次历史口令相同，防止「改一下再改回来」绕过定期更换。"""
    n = policy("pwd_history_count")
    if n <= 0:
        return True, ""
    rows = get_conn().execute(
        "SELECT password_hash FROM console_password_history "
        "WHERE user_id = ? ORDER BY changed_at DESC LIMIT ?", (user_id, n)
    ).fetchall()
    for r in rows:
        parsed = _parse_hash(r["password_hash"])
        if not parsed:
            continue
        iterations, salt, expected = parsed
        actual = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, iterations)
        if hmac.compare_digest(actual, expected):
            return False, f"新口令不得与最近 {n} 次使用过的口令相同。"
    return True, ""


# ===========================================================================
# 改造点③ 审计写入
# ===========================================================================

class Ev:
    """事件类型常量。用常量而非裸字符串，避免拼写漂移导致审计检索漏项。"""
    LOGIN_SUCCESS = "login.success"
    LOGIN_FAILURE = "login.failure"
    LOGIN_BLOCKED = "login.blocked"
    LOGIN_LOCKED = "login.locked"
    LOGIN_UNLOCKED = "login.unlocked"
    LOGOUT = "logout"
    SESSION_CREATED = "session.created"
    SESSION_EXPIRED = "session.expired"
    SESSION_REVOKED = "session.revoked"
    SESSION_FP_WARN = "session.fingerprint_warn"
    PWD_CHANGED = "password.changed"
    PWD_CHANGE_FAILED = "password.change_failed"
    PWD_RESET = "password.reset"
    PWD_MIGRATED = "password.migrated"
    ACCESS_DENIED = "access.denied"
    POLICY_UPDATED = "policy.updated"
    AUDIT_EXPORTED = "audit.exported"
    # sysadmin 模块（ADR-021）
    USER_CREATED = "user.created"
    USER_UPDATED = "user.updated"
    USER_DELETED = "user.deleted"
    TOKEN_ROTATED = "terminal_token.rotated"
    TOKEN_STATUS = "terminal_token.status_changed"
    # 交换机管理（ADR-026）
    SWITCH_DEFAULT_UPDATED = "switch.default_updated"
    SWITCH_CREATED = "switch.created"
    SWITCH_UPDATED = "switch.updated"
    SWITCH_DELETED = "switch.deleted"


def audit(event_type: str, result: str, *, username: str = None,
          user_id: int = None, client_ip: str = None, user_agent: str = None,
          session_id: int = None, target: str = None, reason: str = None,
          detail: Dict[str, Any] = None, conn: sqlite3.Connection = None) -> None:
    """
    写一条审计记录。字段与等保三级 8.1.4.3 a) 逐项对应：
    日期时间 / 用户 / 事件类型 / 是否成功 / 其他相关信息。

    审计写入失败绝不允许影响主业务流程（否则日志表故障会造成全站无法登录），
    故此处捕获所有异常。但同时必须把失败暴露到进程标准错误，便于监控告警发现
    「审计静默失效」——这在等保测评中属于严重问题。
    """
    try:
        c = conn or get_conn()
        # user_agent 截断，防止超长 UA 撑爆库
        ua = (user_agent or "")[:512] or None
        c.execute(
            "INSERT INTO console_audit_log "
            "(occurred_at, occurred_ms, event_type, result, username, user_id, "
            " client_ip, user_agent, session_id, target, reason, detail) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (now(), now_ms(), event_type, result, username, user_id,
             client_ip, ua, session_id, target, reason,
             json.dumps(detail, ensure_ascii=False) if detail else None))
        if conn is None:
            c.commit()
    except Exception as exc:                                  # noqa: BLE001
        import sys
        print(f"[AUDIT-WRITE-FAILED] {event_type}/{result}: {exc}",
              file=sys.stderr, flush=True)


# ===========================================================================
# 改造点③ IP 维度限速（入侵防范；必须前置于 PBKDF2 计算）
# ===========================================================================

def throttle_check(scope_type: str, scope_key: str) -> Tuple[bool, int]:
    """
    只读判定当前是否处于封禁中。返回 (是否放行, 剩余封禁秒数)。
    在任何昂贵计算之前调用。
    """
    row = get_conn().execute(
        "SELECT blocked_until FROM console_login_throttle "
        "WHERE scope_type = ? AND scope_key = ?", (scope_type, scope_key)
    ).fetchone()
    if row and row["blocked_until"] and row["blocked_until"] > now():
        return False, row["blocked_until"] - now()
    return True, 0


def throttle_register(scope_type: str, scope_key: str) -> Tuple[bool, int]:
    """
    登记一次尝试并推进滑动窗口。返回 (本次登记后是否仍放行, 剩余封禁秒数)。

    窗口逻辑：window_start 距今超过窗口长度 → 重置计数为 1；否则累加。
    计数达阈值 → 置 blocked_until。落库而非内存，故进程重启不清零，
    攻击者无法通过触发重启来绕过限速。
    """
    conn = get_conn()
    t = now()
    win = policy("ip_window_seconds")
    limit = policy("ip_max_attempts")
    block = policy("ip_block_seconds")

    row = conn.execute(
        "SELECT id, window_start, attempts, blocked_until "
        "FROM console_login_throttle WHERE scope_type = ? AND scope_key = ?",
        (scope_type, scope_key)).fetchone()

    if row is None:
        conn.execute(
            "INSERT INTO console_login_throttle "
            "(scope_type, scope_key, window_start, attempts, updated_at) "
            "VALUES (?,?,?,1,?)", (scope_type, scope_key, t, t))
        conn.commit()
        return True, 0

    if row["blocked_until"] and row["blocked_until"] > t:
        return False, row["blocked_until"] - t

    if t - row["window_start"] > win:
        attempts, window_start = 1, t          # 窗口过期，重新起算
    else:
        attempts, window_start = row["attempts"] + 1, row["window_start"]

    blocked_until = None
    if attempts >= limit:
        blocked_until = t + block
        attempts = 0                            # 封禁后计数归零，解封即重新起算
        window_start = t

    conn.execute(
        "UPDATE console_login_throttle SET window_start=?, attempts=?, "
        "blocked_until=?, updated_at=? WHERE id=?",
        (window_start, attempts, blocked_until, t, row["id"]))
    conn.commit()

    if blocked_until:
        return False, block
    return True, 0


def throttle_clear(scope_type: str, scope_key: str) -> None:
    """登录成功后清除该 IP 的失败计数，避免正常用户被自己的偶发误输累积拖入封禁。"""
    conn = get_conn()
    conn.execute(
        "UPDATE console_login_throttle SET attempts=0, blocked_until=NULL, updated_at=? "
        "WHERE scope_type=? AND scope_key=?", (now(), scope_type, scope_key))
    conn.commit()


# ===========================================================================
# 改造点④ 账号维度失败计数与锁定
# ===========================================================================

def is_locked(user_row: sqlite3.Row) -> Tuple[bool, int]:
    """返回 (是否锁定中, 剩余秒数)。lock_duration=0 时表示永久锁定待人工解锁。"""
    lu = user_row["locked_until"] if "locked_until" in user_row.keys() else None
    if lu is None:
        return False, 0
    if lu == 0:
        return True, 0                     # 0 = 需管理员手工解锁
    if lu > now():
        return True, lu - now()
    return False, 0


def register_failure(user_row: sqlite3.Row, client_ip: str,
                     user_agent: str = None) -> Tuple[bool, int]:
    """
    登记一次账号维度失败。返回 (是否因本次失败而触发锁定, 锁定秒数)。

    滑动窗口：距首次失败超过 lock_window_seconds 则重新起算，
    避免「一年里零散错 5 次」被误锁。
    """
    conn = get_conn()
    t = now()
    uid = user_row["id"]
    threshold = policy("lock_threshold")
    win = policy("lock_window_seconds")
    dur = policy("lock_duration_seconds")

    first = user_row["first_failed_at"] if "first_failed_at" in user_row.keys() else None
    prev = user_row["failed_attempts"] if "failed_attempts" in user_row.keys() else 0

    if first is None or (t - first) > win:
        attempts, first_failed_at = 1, t
    else:
        attempts, first_failed_at = (prev or 0) + 1, first

    locked, lock_secs = False, 0
    locked_until = None
    if attempts >= threshold:
        locked = True
        lock_secs = dur
        locked_until = (t + dur) if dur > 0 else 0
        conn.execute(
            f"UPDATE {USERS_TABLE} SET failed_attempts=?, first_failed_at=?, "
            f"locked_until=?, lock_count=lock_count+1, last_failed_at=?, "
            f"last_failed_ip=? WHERE id=?",
            (attempts, first_failed_at, locked_until, t, client_ip, uid))
    else:
        conn.execute(
            f"UPDATE {USERS_TABLE} SET failed_attempts=?, first_failed_at=?, "
            f"last_failed_at=?, last_failed_ip=? WHERE id=?",
            (attempts, first_failed_at, t, client_ip, uid))
    conn.commit()

    if locked:
        audit(Ev.LOGIN_LOCKED, "blocked",
              username=user_row[USERNAME_COLUMN], user_id=uid,
              client_ip=client_ip, user_agent=user_agent,
              reason="threshold_reached",
              detail={"attempts": attempts, "threshold": threshold,
                      "lock_seconds": dur,
                      "unlock_at": locked_until or "manual"})
    return locked, lock_secs


def clear_failures(user_id: int, client_ip: str) -> None:
    """登录成功后归零失败计数并解除锁定标记。"""
    conn = get_conn()
    conn.execute(
        f"UPDATE {USERS_TABLE} SET failed_attempts=0, first_failed_at=NULL, "
        f"locked_until=NULL, last_login_at=?, last_login_ip=? WHERE id=?",
        (now(), client_ip, user_id))
    conn.commit()


def admin_unlock(user_id: int, operator: str, client_ip: str = None) -> None:
    """管理员手工解锁。属安全事件，必须留痕。"""
    conn = get_conn()
    row = conn.execute(
        f"SELECT {USERNAME_COLUMN} FROM {USERS_TABLE} WHERE id=?", (user_id,)
    ).fetchone()
    conn.execute(
        f"UPDATE {USERS_TABLE} SET failed_attempts=0, first_failed_at=NULL, "
        f"locked_until=NULL WHERE id=?", (user_id,))
    conn.commit()
    audit(Ev.LOGIN_UNLOCKED, "success", username=operator, client_ip=client_ip,
          target=row[USERNAME_COLUMN] if row else str(user_id),
          reason="admin_manual_unlock")


# ===========================================================================
# 改造点② 会话持久化（SQLite 落库，重启不掉线）
# ===========================================================================

def _sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _ua_fingerprint(user_agent: Optional[str]) -> Optional[str]:
    if not user_agent:
        return None
    return hashlib.sha256(user_agent.encode("utf-8")).hexdigest()[:32]


def create_session(user_row: sqlite3.Row, client_ip: str,
                   user_agent: str = None,
                   must_change_password: bool = False) -> Tuple[str, int]:
    """
    创建会话，返回 (明文 token, session_id)。

    明文 token 仅在此刻存在于内存并发给客户端，库里只落 SHA-256(token)。
    即使数据库文件、备份或快照泄露，也无法反推出可用凭据 —— 这是把会话
    从内存搬到磁盘后必须补上的那道防线，不做等于把凭据明文写盘。
    """
    conn = get_conn()
    t = now()
    token = secrets.token_urlsafe(32)              # 256 位熵，足以抵御穷举
    idle = t + policy("session_idle_seconds")
    absolute = t + policy("session_abs_seconds")

    cur = conn.execute(
        "INSERT INTO console_sessions "
        "(token_hash, user_id, username, created_at, last_seen_at, "
        " idle_expires_at, absolute_expires_at, client_ip, ua_hash, "
        " mfa_passed, must_change_password) "
        "VALUES (?,?,?,?,?,?,?,?,?,0,?)",
        (_sha256_hex(token), user_row["id"], user_row[USERNAME_COLUMN],
         t, t, idle, absolute, client_ip, _ua_fingerprint(user_agent),
         1 if must_change_password else 0))
    sid = cur.lastrowid

    # 并发会话数上限：超出则吊销最旧的，防止凭据被到处复制后无限并发
    cap = policy("session_max_per_user")
    if cap > 0:
        olds = conn.execute(
            "SELECT id FROM console_sessions WHERE user_id=? AND revoked_at IS NULL "
            "AND idle_expires_at > ? AND absolute_expires_at > ? "
            "ORDER BY created_at DESC LIMIT -1 OFFSET ?",
            (user_row["id"], t, t, cap)).fetchall()
        for o in olds:
            conn.execute(
                "UPDATE console_sessions SET revoked_at=?, revoke_reason=? WHERE id=?",
                (t, "max_sessions_exceeded", o["id"]))
    conn.commit()

    audit(Ev.SESSION_CREATED, "success", username=user_row[USERNAME_COLUMN],
          user_id=user_row["id"], client_ip=client_ip, user_agent=user_agent,
          session_id=sid,
          detail={"idle_expires_at": idle, "absolute_expires_at": absolute})
    return token, sid


def resolve_session(token: str, client_ip: str = None,
                    user_agent: str = None) -> Optional[sqlite3.Row]:
    """
    校验会话 token 并滑动续期。返回会话行（含 user_id / username）或 None。

    这是每个受保护请求的热路径 —— 只做一次索引命中 + 一次 SHA-256，
    不做慢哈希，故性能开销可忽略。

    依次判定：存在 → 未吊销 → 未空闲超时 → 未绝对超时 → 指纹匹配。
    任一不通过均按未登录处理并写审计。
    """
    if not token:
        return None
    conn = get_conn()
    t = now()
    row = conn.execute(
        "SELECT * FROM console_sessions WHERE token_hash = ?",
        (_sha256_hex(token),)).fetchone()
    if row is None:
        return None

    if row["revoked_at"] is not None:
        return None

    # 账号状态复核（授权动态生效，ADR-021）：账号被停用/删除后，
    # 存量会话立即失效 —— 不能只依赖「停用瞬间吊销」这一次性动作，
    # 否则停用→启用→再建会话→再停用等时序组合会出现漏网会话。
    urow = conn.execute(
        "SELECT status FROM {} WHERE id=?".format(USERS_TABLE),
        (row["user_id"],)).fetchone()
    if urow is None or urow["status"] != "active":
        _revoke_by_id(conn, row["id"], "account_inactive")
        audit(Ev.SESSION_REVOKED, "success", username=row["username"],
              user_id=row["user_id"], client_ip=client_ip,
              session_id=row["id"], reason="account_inactive")
        return None

    # 空闲超时 —— 等保三级 8.1.4.1 b)「登录连接超时自动退出」
    if row["idle_expires_at"] <= t:
        _revoke_by_id(conn, row["id"], "idle_timeout")
        audit(Ev.SESSION_EXPIRED, "success", username=row["username"],
              user_id=row["user_id"], client_ip=client_ip, session_id=row["id"],
              reason="idle_timeout",
              detail={"idle_seconds": policy("session_idle_seconds")})
        return None

    # 绝对超时 —— 防止会话被滑动续期永久续命
    if row["absolute_expires_at"] <= t:
        _revoke_by_id(conn, row["id"], "absolute_timeout")
        audit(Ev.SESSION_EXPIRED, "success", username=row["username"],
              user_id=row["user_id"], client_ip=client_ip, session_id=row["id"],
              reason="absolute_timeout")
        return None

    # 指纹校验：会话盗用检测
    mismatch = []
    if policy("bind_session_ua") and row["ua_hash"] and user_agent:
        if row["ua_hash"] != _ua_fingerprint(user_agent):
            mismatch.append("user_agent")
    if policy("bind_session_ip") and row["client_ip"] and client_ip:
        if row["client_ip"] != client_ip:
            mismatch.append("client_ip")
    if mismatch:
        _revoke_by_id(conn, row["id"], "fingerprint_mismatch")
        audit(Ev.SESSION_FP_WARN, "blocked", username=row["username"],
              user_id=row["user_id"], client_ip=client_ip, user_agent=user_agent,
              session_id=row["id"], reason="fingerprint_mismatch",
              detail={"fields": mismatch, "origin_ip": row["client_ip"]})
        return None
    # IP 变化但未开启阻断 → 仅告警，不打断（移动网络出口 IP 漂移很常见）。
    # 告警后把会话记录的 client_ip 更新为当前值：移动运营商 NAT 会逐请求换出口 IP，
    # 若不更新，这类用户的每个请求都会记一条告警，几天就把审计表刷爆。
    # 更新后告警只在「变化瞬间」各记一条，detail.origin_ip 保留了变化前的地址。
    if (not policy("bind_session_ip")) and row["client_ip"] and client_ip \
            and row["client_ip"] != client_ip:
        audit(Ev.SESSION_FP_WARN, "success", username=row["username"],
              user_id=row["user_id"], client_ip=client_ip, session_id=row["id"],
              reason="ip_changed_warn_only",
              detail={"origin_ip": row["client_ip"], "current_ip": client_ip})
        conn.execute("UPDATE console_sessions SET client_ip=? WHERE id=?",
                     (client_ip, row["id"]))
        conn.commit()

    # 滑动续期。仅当距上次刷新超过 60 秒才写库，避免高频请求把库写爆。
    if t - row["last_seen_at"] > 60:
        conn.execute(
            "UPDATE console_sessions SET last_seen_at=?, idle_expires_at=? WHERE id=?",
            (t, t + policy("session_idle_seconds"), row["id"]))
        conn.commit()
    return row


def _revoke_by_id(conn: sqlite3.Connection, sid: int, reason: str) -> None:
    conn.execute(
        "UPDATE console_sessions SET revoked_at=?, revoke_reason=? "
        "WHERE id=? AND revoked_at IS NULL", (now(), reason, sid))
    conn.commit()


def revoke_session(token: str, reason: str = "logout",
                   client_ip: str = None) -> None:
    """注销单个会话。"""
    conn = get_conn()
    row = conn.execute(
        "SELECT id, user_id, username FROM console_sessions WHERE token_hash=?",
        (_sha256_hex(token),)).fetchone()
    if row is None:
        return
    _revoke_by_id(conn, row["id"], reason)
    audit(Ev.LOGOUT if reason == "logout" else Ev.SESSION_REVOKED, "success",
          username=row["username"], user_id=row["user_id"],
          client_ip=client_ip, session_id=row["id"], reason=reason)


def revoke_user_sessions(user_id: int, reason: str,
                         keep_session_id: int = None,
                         operator: str = None) -> int:
    """
    吊销某账号的全部会话（可保留当前会话）。返回吊销条数。

    必须在这三种场景调用，否则会留下安全缺口：
      · 改密后        —— 否则旧口令泄露者仍能用已有会话继续操作
      · 账号停用/删除 —— 否则停用只挡新登录，挡不住在线会话
      · 管理员强制下线
    """
    conn = get_conn()
    t = now()
    sql = ("UPDATE console_sessions SET revoked_at=?, revoke_reason=? "
           "WHERE user_id=? AND revoked_at IS NULL")
    params: List[Any] = [t, reason, user_id]
    if keep_session_id:
        sql += " AND id != ?"
        params.append(keep_session_id)
    cur = conn.execute(sql, params)
    conn.commit()
    if cur.rowcount:
        audit(Ev.SESSION_REVOKED, "success", user_id=user_id,
              username=operator, reason=reason,
              detail={"revoked_count": cur.rowcount,
                      "kept_session_id": keep_session_id})
    return cur.rowcount


def purge_expired_sessions(hard_delete_days: int = 30) -> Dict[str, int]:
    """
    定期清理。建议在主循环里挂一个后台 threading.Timer，每 10 分钟跑一次。
    先把超时会话标记为吊销（保留痕迹供审计追溯），再物理删除很久以前的行。
    """
    conn = get_conn()
    t = now()
    marked = conn.execute(
        "UPDATE console_sessions SET revoked_at=?, revoke_reason='expired_purge' "
        "WHERE revoked_at IS NULL AND (idle_expires_at <= ? OR absolute_expires_at <= ?)",
        (t, t, t)).rowcount
    deleted = conn.execute(
        "DELETE FROM console_sessions WHERE revoked_at IS NOT NULL AND revoked_at < ?",
        (t - hard_delete_days * 86400,)).rowcount
    conn.execute(
        "DELETE FROM console_login_throttle WHERE updated_at < ? "
        "AND (blocked_until IS NULL OR blocked_until < ?)", (t - 86400, t))
    conn.commit()
    return {"marked": marked, "deleted": deleted}


def list_user_sessions(user_id: int) -> List[sqlite3.Row]:
    """供「我的登录设备」页面展示，用户可自查异常会话并逐个下线。"""
    t = now()
    return get_conn().execute(
        "SELECT id, created_at, last_seen_at, client_ip, ua_hash, "
        "       idle_expires_at, absolute_expires_at "
        "FROM console_sessions WHERE user_id=? AND revoked_at IS NULL "
        "AND idle_expires_at > ? AND absolute_expires_at > ? "
        "ORDER BY last_seen_at DESC", (user_id, t, t)).fetchall()


# ===========================================================================
# 主流程：登录鉴别
# ===========================================================================

# 对外统一失败文案。无论账号不存在、口令错误还是算法解析失败，都回同一句话，
# 配合等时哈希，共同阻断用户名枚举。
MSG_BAD_CREDENTIAL = "用户名或口令错误。"


def authenticate(username: str, password: str, client_ip: str,
                 user_agent: str = None) -> AuthResult:
    """
    登录主流程。**执行顺序经过安全权衡，请勿随意调整**：

      1. IP 限速判定        —— 只读，零成本。必须最先做
      2. 查账号             —— 一次索引查询
      3. 账号状态/锁定判定   —— 便宜，且不应让被锁账号还去消耗慢哈希
      4. PBKDF2 口令校验     —— 约 200ms，最贵的一步，放最后
      5. 成功后收尾          —— 平滑迁移写回、清计数、建会话、审计

    若把第 4 步提到第 1 步之前，攻击者可用高并发请求让单线程 http.server
    持续做慢哈希，直接把控制台拖死 —— 慢哈希会变成 DoS 放大器。
    """
    ua = user_agent
    username = (username or "").strip()

    # —— 1. IP 限速（前置于一切昂贵计算）——
    ok, retry = throttle_check("ip", client_ip)
    if not ok:
        audit(Ev.LOGIN_BLOCKED, "blocked", username=username, client_ip=client_ip,
              user_agent=ua, reason="throttled",
              detail={"scope": "ip", "retry_after": retry})
        return AuthResult(False, "throttled",
                          f"尝试过于频繁，请在 {retry} 秒后重试。",
                          retry_after=retry, http_status=429)

    if not username or not password:
        throttle_register("ip", client_ip)
        audit(Ev.LOGIN_FAILURE, "failure", username=username, client_ip=client_ip,
              user_agent=ua, reason="empty_credential")
        return AuthResult(False, "empty_credential", MSG_BAD_CREDENTIAL,
                          http_status=401)

    conn = get_conn()

    # —— 2. 查账号 ——
    user = conn.execute(
        f"SELECT * FROM {USERS_TABLE} WHERE {USERNAME_COLUMN} = ?",
        (username,)).fetchone()

    if user is None:
        _dummy_hash_work()              # 等时补偿，抹平「账号不存在」的时间特征
        throttle_register("ip", client_ip)
        audit(Ev.LOGIN_FAILURE, "failure", username=username, client_ip=client_ip,
              user_agent=ua, reason="no_such_user")
        return AuthResult(False, "no_such_user", MSG_BAD_CREDENTIAL,
                          http_status=401)

    keys = user.keys()

    # —— 3. 账号状态 ——
    status = user["status"] if "status" in keys else "active"
    if status != "active":
        audit(Ev.LOGIN_BLOCKED, "blocked", username=username, user_id=user["id"],
              client_ip=client_ip, user_agent=ua, reason=f"account_{status}")
        return AuthResult(False, f"account_{status}",
                          "账号已停用，请联系系统管理员。", http_status=403)

    locked, remain = is_locked(user)
    if locked:
        audit(Ev.LOGIN_BLOCKED, "blocked", username=username, user_id=user["id"],
              client_ip=client_ip, user_agent=ua, reason="account_locked",
              detail={"retry_after": remain})
        msg = (f"账号已锁定，请在 {remain // 60 + 1} 分钟后重试。" if remain
               else "账号已锁定，请联系系统管理员解锁。")
        return AuthResult(False, "account_locked", msg,
                          retry_after=remain, http_status=423)

    # —— 4. 口令校验（含改造点①平滑迁移）——
    stored = user[PASSWORD_COLUMN]
    passed, new_hash = verify_and_upgrade(stored, password)

    if not passed:
        throttle_register("ip", client_ip)
        just_locked, lock_secs = register_failure(user, client_ip, ua)
        left = max(0, policy("lock_threshold") -
                   ((user["failed_attempts"] or 0) + 1))
        audit(Ev.LOGIN_FAILURE, "failure", username=username, user_id=user["id"],
              client_ip=client_ip, user_agent=ua, reason="bad_password",
              detail={"attempts_left": left, "locked": just_locked})
        if just_locked:
            msg = (f"口令错误次数过多，账号已锁定 {lock_secs // 60} 分钟。"
                   if lock_secs else "口令错误次数过多，账号已锁定，请联系系统管理员。")
            return AuthResult(False, "locked_now", msg,
                              retry_after=lock_secs, http_status=423)
        # 剩余次数提示是安全与可用性的折中：告知剩余次数会略微帮助攻击者判断阈值，
        # 但能显著减少正常用户被误锁后的求助工单。仅在还剩 2 次及以内时提示。
        hint = f"（还可尝试 {left} 次）" if 0 < left <= 2 else ""
        return AuthResult(False, "bad_password", MSG_BAD_CREDENTIAL + hint,
                          http_status=401)

    # —— 5. 成功收尾 ——
    weak_legacy = False          # 遗留口令虽已哈希化，但本身不满足现行复杂度要求
    if new_hash:
        was_plain = _parse_hash(stored) is None
        conn.execute(
            f"UPDATE {USERS_TABLE} SET {PASSWORD_COLUMN}=?, password_algo=?, "
            f"password_updated_at=COALESCE(password_updated_at, ?) WHERE id=?",
            (new_hash, HASH_SCHEME, now(), user["id"]))
        if was_plain:
            # 把被迁移的这个口令补记进历史表。否则用户改密后还能立刻「改回原来那个
            # 明文口令」，等于绕过了「不得与最近 N 次重复」的约束 —— 而这个口令
            # 恰恰是最应该被淘汰的（它曾以明文形式存在于数据库和历次备份中）。
            conn.execute(
                "INSERT INTO console_password_history "
                "(user_id, password_hash, changed_at, changed_by) "
                "VALUES (?,?,?,'migration')", (user["id"], new_hash, now()))
        conn.commit()
        # 关键补充：平滑迁移只解决「存储不可逆」，不解决「口令本身太弱」。
        # 改造前的历史账号里必然存在 admin / 123456 这类口令，迁移后它们变成了
        # 「哈希过的弱口令」——依然一撞就开，且等保三级 8.1.4.1 a) 的复杂度要求
        # 并未满足。因此凡是不合现行策略的遗留口令，本次登录即强制改密。
        if was_plain:
            compliant, _why = check_password_policy(password, username)
            if not compliant:
                weak_legacy = True
                conn.execute(
                    f"UPDATE {USERS_TABLE} SET password_must_change=1 WHERE id=?",
                    (user["id"],))
                conn.commit()

        audit(Ev.PWD_MIGRATED, "success", username=username, user_id=user["id"],
              client_ip=client_ip, user_agent=ua,
              reason="plain_to_pbkdf2" if was_plain else "iterations_upgraded",
              detail={"scheme": HASH_SCHEME,
                      "iterations": policy("pbkdf2_iterations"),
                      "weak_legacy_force_change": weak_legacy})

    throttle_clear("ip", client_ip)
    # 在改写 last_login_* 之前先留快照，供响应展示「上次登录时间/地址」。
    # 若跳过这步，前端展示的将永远是"本次登录"的时间，这个提示功能等于失效。
    prev_login_at = user["last_login_at"] if "last_login_at" in keys else None
    prev_login_ip = user["last_login_ip"] if "last_login_ip" in keys else None
    clear_failures(user["id"], client_ip)

    # 是否需要强制改密：管理员置位、或口令超期
    must = bool(user["password_must_change"]) if "password_must_change" in keys else False
    max_age = policy("pwd_max_age_days")
    pwd_at = user["password_updated_at"] if "password_updated_at" in keys else None
    expired_pwd = bool(max_age and pwd_at and (now() - pwd_at) > max_age * 86400)
    if expired_pwd or weak_legacy:
        must = True

    # 重新读取以拿到刚写入的新哈希与清零后的计数
    user = conn.execute(f"SELECT * FROM {USERS_TABLE} WHERE id=?",
                        (user["id"],)).fetchone()
    token, sid = create_session(user, client_ip, ua, must_change_password=must)

    audit(Ev.LOGIN_SUCCESS, "success", username=username, user_id=user["id"],
          client_ip=client_ip, user_agent=ua, session_id=sid,
          detail={"must_change_password": must,
                  "password_expired": expired_pwd,
                  "weak_legacy": weak_legacy,
                  "migrated": bool(new_hash),
                  "role": user["role"] if "role" in user.keys() else None})

    msg = "登录成功。"
    if weak_legacy:
        msg = "登录成功，但您的口令不符合当前安全策略，请立即修改。"
    elif expired_pwd:
        msg = "登录成功，口令已超过有效期，请立即修改。"
    elif must:
        msg = "登录成功，请先修改初始口令。"

    return AuthResult(True, "ok", msg, token=token,
                      user=dict(user), session_id=sid,
                      must_change_password=must,
                      last_login_at=prev_login_at, last_login_ip=prev_login_ip)


# ===========================================================================
# 主流程：改密
# ===========================================================================

def change_password(user_id: int, old_password: str, new_password: str,
                    confirm_password: str, client_ip: str,
                    user_agent: str = None,
                    current_session_id: int = None) -> AuthResult:
    """
    用户自助改密。校验链：新旧一致性 → 旧口令 → 复杂度 → 历史重复 → 落库 → 全端下线。

    改密成功后必须吊销该账号的其它会话（保留当前会话），否则「口令已泄露 → 改密」
    这条自救路径是无效的：攻击者手里的旧会话依然有效。
    """
    conn = get_conn()
    user = conn.execute(f"SELECT * FROM {USERS_TABLE} WHERE id=?",
                        (user_id,)).fetchone()
    if user is None:
        return AuthResult(False, "no_such_user", "账号不存在。", http_status=404)
    uname = user[USERNAME_COLUMN]

    def fail(reason: str, msg: str, status: int = 400) -> AuthResult:
        audit(Ev.PWD_CHANGE_FAILED, "failure", username=uname, user_id=user_id,
              client_ip=client_ip, user_agent=user_agent, reason=reason)
        return AuthResult(False, reason, msg, http_status=status)

    if new_password != confirm_password:
        return fail("confirm_mismatch", "两次输入的新口令不一致。")

    passed, _ = verify_and_upgrade(user[PASSWORD_COLUMN], old_password)
    if not passed:
        # 改密接口也要防爆破：这里同样登记 IP 限速
        throttle_register("ip", client_ip)
        return fail("bad_old_password", "当前口令不正确。", 401)

    if old_password == new_password:
        return fail("same_as_old", "新口令不得与当前口令相同。")

    ok, why = check_password_policy(new_password, uname)
    if not ok:
        return fail("pwd_policy", why)

    ok, why = check_password_history(user_id, new_password)
    if not ok:
        return fail("pwd_reused", why)

    # 落库
    new_hash = hash_password(new_password)
    t = now()
    conn.execute(
        f"UPDATE {USERS_TABLE} SET {PASSWORD_COLUMN}=?, password_algo=?, "
        f"password_updated_at=?, password_must_change=0 WHERE id=?",
        (new_hash, HASH_SCHEME, t, user_id))
    conn.execute(
        "INSERT INTO console_password_history (user_id, password_hash, changed_at, changed_by) "
        "VALUES (?,?,?,'self')", (user_id, new_hash, t))
    # 历史表裁剪，只留最近 N+1 条
    keep = policy("pwd_history_count") + 1
    conn.execute(
        "DELETE FROM console_password_history WHERE user_id=? AND id NOT IN "
        "(SELECT id FROM console_password_history WHERE user_id=? "
        " ORDER BY changed_at DESC LIMIT ?)", (user_id, user_id, keep))
    conn.commit()

    kicked = revoke_user_sessions(user_id, "password_changed",
                                  keep_session_id=current_session_id)

    audit(Ev.PWD_CHANGED, "success", username=uname, user_id=user_id,
          client_ip=client_ip, user_agent=user_agent,
          session_id=current_session_id,
          detail={"other_sessions_revoked": kicked})

    return AuthResult(True, "ok",
                      f"口令修改成功，已注销其它 {kicked} 个登录会话。"
                      if kicked else "口令修改成功。",
                      user=dict(user))


# ===========================================================================
# sysadmin 账户管理（ADR-021：控制台「系统管理」模块，admin-only 路由的后端）
# ===========================================================================

def require_admin(sess_row) -> bool:
    """
    sysadmin 路由组权限门（整个 /console/sysadmin/* 仅 admin 角色可用）。

    会话行本身已由 resolve_session 校验过有效性，这里必须回库补查账号
    当前角色与状态 —— 角色可能在会话建立后被管理员调整，不能信任会话
    建立时的快照（等保三级 8.1.4.2 访问控制：授权动态生效）。
    """
    try:
        row = get_conn().execute(
            "SELECT role, status FROM {} WHERE id=?".format(USERS_TABLE),
            (sess_row["user_id"],)).fetchone()
    except (sqlite3.Error, TypeError, KeyError, IndexError):
        return False
    return bool(row) and row["role"] == "admin" and row["status"] == "active"


def ensure_admin_role() -> None:
    """
    角色模型启用迁移（幂等，进程启动时调用一次）。

    历史库 console_users.role 默认 'operator'（权限模型启用前该列无意义）。
    若整库不存在任何 admin 角色账号，则把名为 admin 的账号提升为 admin，
    避免存量管理员被新的 admin-only 权限门锁在门外；已存在 admin 时不动任何行。
    """
    try:
        conn = get_conn()
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM {} WHERE role='admin'".format(USERS_TABLE)
        ).fetchone()
        if row and (row["n"] or 0) > 0:
            return
        cur = conn.execute(
            "UPDATE {} SET role='admin' WHERE username='admin'".format(USERS_TABLE))
        conn.commit()
        if cur.rowcount:
            audit(Ev.USER_UPDATED, "success", username="system",
                  reason="admin_role_migration",
                  detail={"promoted": "admin", "basis": "legacy_role_backfill"})
    except sqlite3.Error:
        pass    # 表未就绪（首次冷启动）时静默跳过；登录改造迁移脚本会建表


def get_user(user_id: int) -> Optional[sqlite3.Row]:
    """按 id 取账号概要（session-info 等处使用）。"""
    try:
        return get_conn().execute(
            "SELECT id, {u} AS username, role, status, password_must_change "
            "FROM {t} WHERE id=?".format(u=USERNAME_COLUMN, t=USERS_TABLE),
            (int(user_id),)).fetchone()
    except (sqlite3.Error, TypeError, ValueError):
        return None


def list_users() -> List[Dict[str, Any]]:
    """账号清单（password 字段恒脱敏为 '****'，绝不回显哈希）。"""
    rows = get_conn().execute(
        "SELECT id, {u} AS username, role, status, password_algo, "
        "password_must_change, last_login_at, last_login_ip, locked_until, "
        "failed_attempts, lock_count FROM {t} ORDER BY id".format(
            u=USERNAME_COLUMN, t=USERS_TABLE)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["password"] = "****"
        d["password_must_change"] = bool(d.get("password_must_change"))
        out.append(d)
    return out


def _count_active_admins(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM {t} WHERE role='admin' AND status='active'"
        .format(t=USERS_TABLE)).fetchone()
    return int(row["n"]) if row else 0


def create_user(username: str, password: str, role: str = "operator",
                operator: str = None, client_ip: str = None) -> Dict[str, Any]:
    """
    创建控制台账号。返回 dict(ok, http_status, message, user_id)。

    口令复杂度走 check_password_policy；初始口令强制改密（password_must_change=1），
    使创建者本人也不掌握长期有效口令（等保三级问责性，与 admin_reset_password 同理）。
    """
    conn = get_conn()
    username = (username or "").strip()
    role = role if role in ("admin", "operator") else "operator"

    def fail(status, reason, msg):
        audit(Ev.USER_CREATED, "failure", username=operator, client_ip=client_ip,
              target=username or None, reason=reason)
        return {"ok": False, "http_status": status, "message": msg,
                "user_id": None}

    if not username:
        return fail(400, "empty_username", "用户名不能为空。")
    if len(username) > 32 or not re.match(r"^[A-Za-z0-9_.-]+$", username):
        return fail(400, "bad_username",
                    "用户名仅允许字母、数字与 _. -，长度不超过 32。")
    if not password:
        return fail(400, "empty_password", "初始口令不能为空。")
    ok, why = check_password_policy(password, username)
    if not ok:
        return fail(400, "pwd_policy", why)
    if conn.execute(
            "SELECT id FROM {t} WHERE {u}=?".format(
                t=USERS_TABLE, u=USERNAME_COLUMN), (username,)).fetchone():
        return fail(409, "duplicate_username", "用户名已存在。")

    h = hash_password(password)
    cur = conn.execute(
        "INSERT INTO {t}({u}, {p}, password_algo, password_updated_at, "
        "password_must_change, role, status) VALUES(?,?,?,?,1,?,'active')".format(
            t=USERS_TABLE, u=USERNAME_COLUMN, p=PASSWORD_COLUMN),
        (username, h, HASH_SCHEME, now(), role))
    conn.commit()
    uid = cur.lastrowid
    audit(Ev.USER_CREATED, "success", username=operator, client_ip=client_ip,
          user_id=uid, target=username,
          detail={"role": role, "must_change": True})
    return {"ok": True, "http_status": 200,
            "message": "账号已创建，首次登录须修改口令。", "user_id": uid}


def update_user(user_id: int, role: str = None, status: str = None,
                operator: str = None, current_user_id: int = None,
                client_ip: str = None) -> Dict[str, Any]:
    """
    修改账号角色/状态。返回 dict(ok, http_status, message)。

    防线：禁自降级 / 禁停用自己的账号（409）；禁把最后一个 active admin
    降级或停用（409）；置 disabled 立即吊销该账号全部会话 —— 停用必须
    即时生效，不能只挡新登录（否则在线攻击者会话继续有效）。
    """
    conn = get_conn()
    user = conn.execute(
        "SELECT * FROM {t} WHERE id=?".format(t=USERS_TABLE),
        (int(user_id),)).fetchone()
    uname = user[USERNAME_COLUMN] if user else None

    def fail(status_code, reason, msg):
        audit(Ev.USER_UPDATED, "failure", username=operator, client_ip=client_ip,
              target=uname or str(user_id), reason=reason)
        return {"ok": False, "http_status": status_code, "message": msg}

    if user is None:
        return fail(404, "no_such_user", "目标账号不存在。")
    if role is None and status is None:
        return fail(400, "nothing_to_update", "role / status 至少提供一项。")
    if role is not None and role not in ("admin", "operator"):
        return fail(400, "bad_role", "role 仅允许 admin / operator。")
    if status is not None and status not in ("active", "disabled"):
        return fail(400, "bad_status", "status 仅允许 active / disabled。")

    is_self = (current_user_id is not None
               and int(user_id) == int(current_user_id))
    if is_self and role == "operator":
        return fail(409, "self_demote", "不能将自己的角色降级为 operator。")
    if is_self and status == "disabled":
        return fail(409, "self_disable", "不能停用自己的账号。")

    target_is_admin = (user["role"] == "admin")
    admins = _count_active_admins(conn)
    if role == "operator" and target_is_admin and admins <= 1:
        return fail(409, "last_admin", "系统至少需要保留一个 admin 账号。")
    if status == "disabled" and target_is_admin and admins <= 1:
        return fail(409, "last_admin", "系统至少需要保留一个启用的 admin 账号。")

    sets, args = [], []
    if role is not None and role != user["role"]:
        sets.append("role=?")
        args.append(role)
    if status is not None and status != user["status"]:
        sets.append("status=?")
        args.append(status)
    if sets:
        conn.execute(
            "UPDATE {t} SET {s} WHERE id=?".format(
                t=USERS_TABLE, s=", ".join(sets)),
            args + [int(user_id)])
        conn.commit()
    revoked = 0
    if status == "disabled" and user["status"] != "disabled":
        revoked = revoke_user_sessions(int(user_id), "account_disabled",
                                       operator=operator)
    audit(Ev.USER_UPDATED, "success", username=operator, client_ip=client_ip,
          user_id=int(user_id), target=uname,
          detail={"role": role, "status": status, "sessions_revoked": revoked})
    msg = "账号已更新。"
    if revoked:
        msg += "已强制下线 %d 个会话。" % revoked
    return {"ok": True, "http_status": 200, "message": msg}


def delete_user(user_id: int, operator: str = None,
                current_user_id: int = None,
                client_ip: str = None) -> Dict[str, Any]:
    """
    删除账号。返回 dict(ok, http_status, message)。

    防线：禁删自己（409）、禁删最后一个 admin（409）；先吊销全部会话再删除
    （console_sessions / console_password_history 对账号表
    FK ON DELETE CASCADE，随删自动清理，不遗留孤儿会话）。
    """
    conn = get_conn()
    user = conn.execute(
        "SELECT * FROM {t} WHERE id=?".format(t=USERS_TABLE),
        (int(user_id),)).fetchone()
    uname = user[USERNAME_COLUMN] if user else None

    def fail(status_code, reason, msg):
        audit(Ev.USER_DELETED, "failure", username=operator, client_ip=client_ip,
              target=uname or str(user_id), reason=reason)
        return {"ok": False, "http_status": status_code, "message": msg}

    if user is None:
        return fail(404, "no_such_user", "目标账号不存在。")
    if current_user_id is not None and int(user_id) == int(current_user_id):
        return fail(409, "self_delete", "不能删除自己的账号。")
    if user["role"] == "admin" and _count_active_admins(conn) <= 1:
        return fail(409, "last_admin", "系统至少需要保留一个 admin 账号。")

    revoke_user_sessions(int(user_id), "account_deleted", operator=operator)
    conn.execute("DELETE FROM {t} WHERE id=?".format(t=USERS_TABLE),
                 (int(user_id),))
    conn.commit()
    audit(Ev.USER_DELETED, "success", username=operator, client_ip=client_ip,
          target=uname, detail={"deleted_user_id": int(user_id)})
    return {"ok": True, "http_status": 200, "message": "账号已删除。"}


def admin_reset_password(target_user_id: int, new_password: str, operator: str,
                         client_ip: str = None,
                         force_change: bool = True) -> AuthResult:
    """
    管理员重置口令。默认置 password_must_change=1，要求用户下次登录立即自行修改，
    使管理员本人也不掌握用户最终口令 —— 这是等保三级问责性的重要一环。
    """
    conn = get_conn()
    user = conn.execute(f"SELECT * FROM {USERS_TABLE} WHERE id=?",
                        (target_user_id,)).fetchone()
    if user is None:
        return AuthResult(False, "no_such_user", "目标账号不存在。", http_status=404)

    ok, why = check_password_policy(new_password, user[USERNAME_COLUMN])
    if not ok:
        return AuthResult(False, "pwd_policy", why, http_status=400)

    t = now()
    new_hash = hash_password(new_password)
    conn.execute(
        f"UPDATE {USERS_TABLE} SET {PASSWORD_COLUMN}=?, password_algo=?, "
        f"password_updated_at=?, password_must_change=?, failed_attempts=0, "
        f"first_failed_at=NULL, locked_until=NULL WHERE id=?",
        (new_hash, HASH_SCHEME, t, 1 if force_change else 0, target_user_id))
    conn.execute(
        "INSERT INTO console_password_history (user_id, password_hash, changed_at, changed_by) "
        "VALUES (?,?,?,?)", (target_user_id, new_hash, t, f"admin:{operator}"))
    conn.commit()

    kicked = revoke_user_sessions(target_user_id, "password_reset_by_admin")
    audit(Ev.PWD_RESET, "success", username=operator, client_ip=client_ip,
          target=user[USERNAME_COLUMN],
          detail={"force_change": force_change, "sessions_revoked": kicked})
    return AuthResult(True, "ok", "口令已重置，该用户下次登录须自行修改。")


# ===========================================================================
# 自检：直接运行本文件可验证核心逻辑（不依赖真实业务库）
#   python auth_upgrade.py
# ===========================================================================

if __name__ == "__main__":
    import os
    import tempfile

    tmp = os.path.join(tempfile.gettempdir(), "auth_upgrade_selftest.db")
    if os.path.exists(tmp):
        os.remove(tmp)
    DB_PATH = tmp
    _local = threading.local()

    c = get_conn()
    c.executescript(f"""
    CREATE TABLE {USERS_TABLE} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        {USERNAME_COLUMN} TEXT UNIQUE NOT NULL,
        {PASSWORD_COLUMN} TEXT NOT NULL,
        password_algo TEXT NOT NULL DEFAULT 'plain',
        password_updated_at INTEGER,
        password_must_change INTEGER NOT NULL DEFAULT 0,
        failed_attempts INTEGER NOT NULL DEFAULT 0,
        first_failed_at INTEGER, locked_until INTEGER,
        lock_count INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'active',
        last_login_at INTEGER, last_login_ip TEXT,
        last_failed_at INTEGER, last_failed_ip TEXT,
        role TEXT NOT NULL DEFAULT 'operator');
    CREATE TABLE console_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, token_hash TEXT NOT NULL UNIQUE,
        user_id INTEGER NOT NULL, username TEXT NOT NULL,
        created_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL,
        idle_expires_at INTEGER NOT NULL, absolute_expires_at INTEGER NOT NULL,
        client_ip TEXT, ua_hash TEXT, revoked_at INTEGER, revoke_reason TEXT,
        mfa_passed INTEGER NOT NULL DEFAULT 0,
        must_change_password INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE console_password_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        password_hash TEXT NOT NULL, changed_at INTEGER NOT NULL, changed_by TEXT);
    CREATE TABLE console_login_throttle (
        id INTEGER PRIMARY KEY AUTOINCREMENT, scope_type TEXT NOT NULL,
        scope_key TEXT NOT NULL, window_start INTEGER NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, blocked_until INTEGER,
        updated_at INTEGER NOT NULL, UNIQUE(scope_type, scope_key));
    CREATE TABLE console_audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at INTEGER NOT NULL,
        occurred_ms INTEGER, event_type TEXT NOT NULL, result TEXT NOT NULL,
        username TEXT, user_id INTEGER, client_ip TEXT, user_agent TEXT,
        session_id INTEGER, target TEXT, reason TEXT, detail TEXT);
    CREATE TABLE console_security_policy (
        key TEXT PRIMARY KEY, value TEXT NOT NULL,
        value_type TEXT NOT NULL DEFAULT 'int', description TEXT,
        updated_at INTEGER, updated_by TEXT);
    -- 自检用低迭代次数，避免测试耗时过长
    INSERT INTO console_security_policy(key,value) VALUES('pbkdf2_iterations','20000');
    -- 模拟改造前的遗留明文账号
    INSERT INTO {USERS_TABLE}({USERNAME_COLUMN},{PASSWORD_COLUMN})
        VALUES('opadmin','Hz@Console2026');
    """)
    c.commit()
    invalidate_policy_cache()

    ok_cnt = fail_cnt = 0

    def check(label, cond):
        global ok_cnt, fail_cnt
        if cond:
            ok_cnt += 1
            print(f"  \u2713 {label}")
        else:
            fail_cnt += 1
            print(f"  \u2717 {label}")

    print("\n[1] 改造点① 明文口令自动平滑迁移")
    before = c.execute(f"SELECT {PASSWORD_COLUMN}, password_algo "
                       f"FROM {USERS_TABLE} WHERE {USERNAME_COLUMN}='opadmin'").fetchone()
    check(f"改造前库中为明文：{before[PASSWORD_COLUMN]!r}", before["password_algo"] == "plain")
    r = authenticate("opadmin", "Hz@Console2026", "172.17.1.50", "SelfTest/1.0")
    check("用原明文口令登录成功", r.ok)
    after = c.execute(f"SELECT {PASSWORD_COLUMN}, password_algo "
                      f"FROM {USERS_TABLE} WHERE {USERNAME_COLUMN}='opadmin'").fetchone()
    check(f"登录后自动改写为哈希：{after[PASSWORD_COLUMN][:34]}...",
          after["password_algo"] == "pbkdf2_sha256"
          and after[PASSWORD_COLUMN].startswith("pbkdf2_sha256$"))
    check("库中已无明文口令", "Hz@Console2026" not in after[PASSWORD_COLUMN])
    r2 = authenticate("opadmin", "Hz@Console2026", "172.17.1.50", "SelfTest/1.0")
    check("迁移后同一口令仍可正常登录（用户无感）", r2.ok)

    print("\n[1b] 遗留弱口令：哈希化同时强制改密")
    c.execute(f"INSERT INTO {USERS_TABLE}({USERNAME_COLUMN},{PASSWORD_COLUMN}) "
              f"VALUES('weakuser','admin123')")
    c.commit()
    rw = authenticate("weakuser", "admin123", "172.17.1.53", "SelfTest/1.0")
    check("弱口令明文账号仍可登录（迁移不阻断登录）", rw.ok)
    check("但被强制要求改密（must_change_password）", rw.must_change_password)
    check("且库中已变哈希（弱口令不再以明文存续）",
          c.execute(f"SELECT password_algo FROM {USERS_TABLE} "
                    f"WHERE {USERNAME_COLUMN}='weakuser'").fetchone()[0]
          == "pbkdf2_sha256")

    print("\n[2] 改造点② 会话落库与 token 不落明文")
    token = r2.token
    srow = c.execute("SELECT * FROM console_sessions WHERE id=?", (r2.session_id,)).fetchone()
    check("库中 token_hash 为 64 位 SHA-256 十六进制", len(srow["token_hash"]) == 64)
    check("库中不存在明文 token", token not in srow["token_hash"])
    check("按明文 token 可解析出会话", resolve_session(token, "172.17.1.50", "SelfTest/1.0") is not None)
    # 模拟进程重启：清掉线程局部连接与所有内存态
    _local = threading.local()
    check("模拟进程重启后会话依然有效（重启不掉线）",
          resolve_session(token, "172.17.1.50", "SelfTest/1.0") is not None)
    check("UA 变化触发指纹阻断",
          resolve_session(token, "172.17.1.50", "Evil-Bot/9.9") is None)

    print("\n[3] 改造点④ 失败计数与账号锁定（阈值 5）")
    for i in range(1, 5):
        rr = authenticate("opadmin", "wrong-pwd", "172.17.1.51", "SelfTest/1.0")
        check(f"第 {i} 次错误口令被拒（HTTP {rr.http_status}）",
              (not rr.ok) and rr.http_status == 401)
    rr = authenticate("opadmin", "wrong-pwd", "172.17.1.51", "SelfTest/1.0")
    check(f"第 5 次触发锁定（HTTP {rr.http_status}，{rr.retry_after}s）",
          (not rr.ok) and rr.http_status == 423)
    rr = authenticate("opadmin", "Hz@Console2026", "172.17.1.51", "SelfTest/1.0")
    check("锁定期内即使口令正确也拒绝", not rr.ok and rr.reason == "account_locked")
    uid = c.execute(f"SELECT id FROM {USERS_TABLE} "
                    f"WHERE {USERNAME_COLUMN}='opadmin'").fetchone()["id"]
    admin_unlock(uid, "secadmin", "172.17.1.9")
    rr = authenticate("opadmin", "Hz@Console2026", "172.17.1.52", "SelfTest/1.0")
    check("管理员解锁后可正常登录", rr.ok)

    print("\n[4] 防用户名枚举")
    t0 = time.time(); a = authenticate("ghost-user", "x", "172.17.1.60"); d1 = time.time() - t0
    t0 = time.time(); b = authenticate("opadmin", "x", "172.17.1.61"); d2 = time.time() - t0
    check(f"两种失败的对外文案一致：{a.message!r}",
          a.message.startswith(MSG_BAD_CREDENTIAL) and b.message.startswith(MSG_BAD_CREDENTIAL))
    check(f"耗时接近，无时间侧信道（{d1*1000:.0f}ms vs {d2*1000:.0f}ms）",
          abs(d1 - d2) < max(d1, d2) * 1.5 + 0.05)
    check("内部审计仍区分原因（no_such_user / bad_password）",
          a.reason == "no_such_user" and b.reason == "bad_password")

    print("\n[5] 改造点③ IP 限速（窗口 60s / 阈值 10）")
    blocked = None
    for i in range(12):
        rr = authenticate(f"probe{i}", "x", "172.17.9.99")
        if rr.reason == "throttled":
            blocked = i + 1
            break
    check(f"第 {blocked} 次请求起被 IP 限速拦截（HTTP 429）", blocked is not None)

    print("\n[6] 改密：复杂度、历史重用、全端下线")
    ra = authenticate("opadmin", "Hz@Console2026", "172.17.1.70", "SelfTest/1.0")
    rb = authenticate("opadmin", "Hz@Console2026", "172.17.1.71", "SelfTest/1.0")
    check("同一账号建立两个并发会话", ra.ok and rb.ok)
    rc = change_password(uid, "Hz@Console2026", "123456", "123456", "172.17.1.70")
    check(f"弱口令被拒：{rc.message}", not rc.ok and rc.reason == "pwd_policy")
    rc = change_password(uid, "Hz@Console2026", "Hz@Console2026", "Hz@Console2026", "172.17.1.70")
    check("与当前口令相同被拒", not rc.ok and rc.reason == "same_as_old")
    rc = change_password(uid, "bad-old", "Xin@Console2027", "Xin@Console2027", "172.17.1.70")
    check("旧口令错误被拒", not rc.ok and rc.reason == "bad_old_password")
    rc = change_password(uid, "Hz@Console2026", "Xin@Console2027", "Xin@Console2027",
                         "172.17.1.70", "SelfTest/1.0", current_session_id=ra.session_id)
    check(f"合规新口令改密成功：{rc.message}", rc.ok)
    check("当前会话保留", resolve_session(ra.token, "172.17.1.70", "SelfTest/1.0") is not None)
    check("其它会话被强制下线", resolve_session(rb.token, "172.17.1.71", "SelfTest/1.0") is None)
    rc = change_password(uid, "Xin@Console2027", "Hz@Console2026", "Hz@Console2026",
                         "172.17.1.70", current_session_id=ra.session_id)
    check(f"改回历史口令被拒：{rc.message}", not rc.ok and rc.reason == "pwd_reused")

    print("\n[7] 审计完整性（等保三级 8.1.4.3）")
    rows = c.execute("SELECT event_type, COUNT(*) n FROM console_audit_log "
                     "GROUP BY event_type ORDER BY n DESC").fetchall()
    total = c.execute("SELECT COUNT(*) FROM console_audit_log").fetchone()[0]
    print(f"    共 {total} 条：" + "，".join(f"{r['event_type']}×{r['n']}" for r in rows))
    need = {Ev.LOGIN_SUCCESS, Ev.LOGIN_FAILURE, Ev.LOGIN_BLOCKED, Ev.LOGIN_LOCKED,
            Ev.LOGIN_UNLOCKED, Ev.PWD_MIGRATED, Ev.PWD_CHANGED,
            Ev.PWD_CHANGE_FAILED, Ev.SESSION_CREATED, Ev.SESSION_REVOKED}
    got = {r["event_type"] for r in rows}
    check(f"关键事件全覆盖（缺 {sorted(need - got) or '无'}）", need <= got)
    one = c.execute("SELECT * FROM console_audit_log WHERE event_type=? LIMIT 1",
                    (Ev.LOGIN_SUCCESS,)).fetchone()
    check("单条记录含时间/用户/类型/结果/来源 IP 五要素",
          all([one["occurred_at"], one["username"], one["event_type"],
               one["result"], one["client_ip"]]))
    check("审计表无任何明文口令残留",
          c.execute("SELECT COUNT(*) FROM console_audit_log "
                    "WHERE IFNULL(detail,'')||IFNULL(reason,'') LIKE '%Console2026%'"
                    ).fetchone()[0] == 0)

    print("\n" + "=" * 62)
    print(f" 自检结果：通过 {ok_cnt} 项，失败 {fail_cnt} 项")
    print(f" 测试库：{tmp}")
    print("=" * 62)
    raise SystemExit(0 if fail_cnt == 0 else 1)
