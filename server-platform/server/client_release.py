# -*- coding: utf-8 -*-
"""观枢终端平台服务端 · 客户端版本发布管理（ADR-042）。

职责：
- 安装包上传落存储（<data_dir>/storage/client/{version}/{filename}）；
- 版本台账（client_releases）+ 单一 current 指针（client_current，回滚=指回旧版）；
- 终端更新 manifest 数据源（latest_version/sha256/size/download_url）；
- 定制安装包文件名协议（main 定稿，客户端侧按此解析）：
    EyeTerm_Setup_x64_{ver}_{cfg64}_md58.exe
    cfg64 = base64url(zlib(json({"s": server_url, "t": terminal_token})))
            超长截断保 <=160 字符（防御性，常规配置长度不触发）；
    md58  = json 原文 MD5 前 8 位（安装器解析后校验完整性；
            解析失败/校验失败 -> 静默回退手动配置流程）；
- 定制包一次性下载票据（10 分钟有效、用后即焚，内存态；服务重启失效可接受——
  管理员重新生成即可，选择理由见 ADR-042 下载鉴权选型）。
"""
import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import zlib

# 版本号：semver 三段（宽松：允许 4.0.0 / 4.0.0-beta 不接受，纯数字点分）
_VERSION_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){1,3}$")
_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
_MAX_UPLOAD_BYTES = 256 * 1024 * 1024      # 安装包上限 256MB
_CFG64_MAX = 160                            # 协议：cfg64 截断上限
_TICKET_TTL_SEC = 600                       # 一次性下载票据有效期 10 分钟
_TICKET_SWEEP_LIMIT = 256                   # 惰性清理上限（防字典膨胀）

# 定制文件名解析：EyeTerm_Setup_x64_{ver}_{cfg64}_md58.exe
_CUSTOM_RE = re.compile(
    r"^EyeTerm_Setup_x64_(?P<ver>\d{1,3}(?:\.\d{1,3}){1,3})_"
    r"(?P<cfg>[A-Za-z0-9_-]{1,168})_(?P<md58>[0-9a-f]{8})\.exe$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS client_releases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  version TEXT NOT NULL UNIQUE,
  filename TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size INTEGER NOT NULL,
  published_at INTEGER NOT NULL,
  rollback_flag INTEGER NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT '',
  created_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS client_current (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  release_id INTEGER NOT NULL,
  updated_ts INTEGER NOT NULL
);
-- 多平台 current 指针（ADR-042 增补）：Windows / Android / Linux 各一。
-- 旧 client_current 受 CHECK(id=1) 限制无法承载多平台，故另立此表；
-- 迁移把旧指针按 platform='windows' 迁入（幂等，见 _migrate_platform）。
CREATE TABLE IF NOT EXISTS client_current_platform (
  platform TEXT PRIMARY KEY,
  release_id INTEGER NOT NULL,
  updated_ts INTEGER NOT NULL
);
-- 更新推送批次（2026-09-19）：控制台按范围（全量/勾选终端）主动下发
-- client_update 命令的批次台账。逐终端执行状态不另存表——由 commands 表
-- 的 source 字段（'client-push:<batch_id>'）关联统计，避免双写不一致。
CREATE TABLE IF NOT EXISTS client_push_batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mode TEXT NOT NULL DEFAULT 'notify',
  target_version TEXT NOT NULL DEFAULT '',
  targets_json TEXT NOT NULL DEFAULT '[]',
  total INTEGER NOT NULL DEFAULT 0,
  created_ts INTEGER NOT NULL,
  operator TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT ''
);
"""

# 支持的平台（顺序即下载页展示顺序）
PLATFORMS = ("windows", "android", "linux")
PLATFORM_LABELS = {"windows": "Windows", "android": "安卓 Android",
                   "linux": "Linux"}


class ClientReleaseError(Exception):
    """发布管理业务错误（message 为简体中文，可直接作为 API error 文案）。"""

    def __init__(self, message, http_status=400):
        Exception.__init__(self, message)
        self.message = message
        self.http_status = http_status


# ----------------------------------------------------------------------
# 定制文件名协议（纯函数，供单测与服务端分发面板；终端侧独立实现同协议）
# ----------------------------------------------------------------------

def build_custom_filename(server_url, token, version):
    """按协议生成定制安装包文件名（派单协议定稿）：
    EyeTerm_Setup_x64_{ver}_{cfg64}_{md58}.exe（md58 = json 原文 MD5 前 8 位）。"""
    payload = json.dumps({"s": str(server_url), "t": str(token)},
                         ensure_ascii=False, separators=(",", ":"))
    raw = payload.encode("utf-8")
    md58 = hashlib.md5(raw).hexdigest()[:8]
    cfg64 = base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii")
    cfg64 = cfg64.rstrip("=")
    if len(cfg64) > _CFG64_MAX:          # 防御性截断（协议约定，常规不触发）
        cfg64 = cfg64[:_CFG64_MAX]
    return "EyeTerm_Setup_x64_%s_%s_%s.exe" % (version, cfg64, md58)


def custom_cfg64(filename):
    """定制文件名 → cfg64 段（非定制名返回空串）。供长期分发链接组装。"""
    m = _CUSTOM_RE.match(str(filename or ""))
    return m.group("cfg") if m else ""


def validate_cfg64(cfg64):
    """校验 cfg64 段可解码且含合法 server/token（服务端侧最小校验）。

    与客户端 bootstrap.decode_cfg 同算法（base64url + zlib + json{"s","t"}）；
    服务端不导入客户端模块，故此处独立实现一份。校验失败返回 False。
    用途：长期定制包下载端点（?cfg64=）的入参把关，避免垃圾串被当作有效凭证。
    """
    s = str(cfg64 or "").strip()
    if not s or len(s) > _CFG64_MAX:
        return False
    b64 = s.replace("-", "+").replace("_", "/")
    b64 += "=" * (-len(b64) % 4)
    try:
        raw = base64.b64decode(b64)
        obj = json.loads(zlib.decompress(raw).decode("utf-8"))
    except Exception:
        return False
    if not isinstance(obj, dict):
        return False
    server = str(obj.get("s") or "").strip()
    token = str(obj.get("t") or "").strip()
    return bool(server.startswith(("http://", "https://")) and token)


def parse_custom_filename(filename):
    """协议侧解析（安装器同款逻辑的服务端参考实现；测试与联调核对用）。

    返回 {"s":..., "t":..., "version":...}；任何解析/校验失败抛
    ClientReleaseError（安装器语义为静默回退手动流程）。"""
    m = _CUSTOM_RE.match(str(filename or ""))
    if not m:
        raise ClientReleaseError("定制文件名格式无效")
    cfg = m.group("cfg")
    pad = "=" * (-len(cfg) % 4)
    try:
        raw = zlib.decompress(base64.urlsafe_b64decode(cfg + pad))
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        raise ClientReleaseError("定制配置解析失败")
    if (not isinstance(payload, dict) or "s" not in payload
            or "t" not in payload):
        raise ClientReleaseError("定制配置字段缺失")
    # md58 完整性校验：基于解压后的 json 原文（与生成侧同一输入口径）
    if hashlib.md5(raw).hexdigest()[:8] != m.group("md58"):
        raise ClientReleaseError("定制配置完整性校验失败")
    return {"s": payload["s"], "t": payload["t"], "version": m.group("ver")}


# ----------------------------------------------------------------------
# 存储
# ----------------------------------------------------------------------

class ClientReleaseStore(object):
    """客户端版本发布存储（自持连接 + 线程锁，power_control 同款形态）。"""

    def __init__(self, db_path, data_dir):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False,
                                     timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._migrate_platform()
        self._storage_dir = os.path.join(data_dir, "storage", "client")
        os.makedirs(self._storage_dir, exist_ok=True)
        self._tickets = {}   # ticket -> {"release_id", "filename", "exp", "used"}
        self._ticket_lock = threading.Lock()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    # -- 内部工具 --

    def _migrate_platform(self):
        """多平台迁移（幂等）：client_releases 补 platform 列，并把唯一约束从
        (version) 改为 (platform, version)；旧单指针 current 迁入新表。

        实现要点：SQLite 无法修改 UNIQUE 约束，必须重建表；重建时按 id 原样
        搬运，保证 client_current.release_id 引用不失效（生产数据零丢失）。"""
        with self._lock:
            cols = set(r["name"] for r in self._conn.execute(
                "PRAGMA table_info(client_releases)").fetchall())
            if "platform" not in cols:
                self._conn.executescript("""
ALTER TABLE client_releases RENAME TO client_releases_old;
CREATE TABLE client_releases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL DEFAULT 'windows',
  version TEXT NOT NULL,
  filename TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size INTEGER NOT NULL,
  published_at INTEGER NOT NULL,
  rollback_flag INTEGER NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT '',
  created_ts INTEGER NOT NULL,
  UNIQUE(platform, version)
);
INSERT INTO client_releases(id, platform, version, filename, sha256, size,
  published_at, rollback_flag, note, created_ts)
  SELECT id, 'windows', version, filename, sha256, size, published_at,
         rollback_flag, note, created_ts FROM client_releases_old;
DROP TABLE client_releases_old;
""")
                self._conn.commit()
            old = self._conn.execute(
                "SELECT release_id, updated_ts FROM client_current"
                " WHERE id=1").fetchone()
            if old is not None:
                self._conn.execute(
                    "INSERT INTO client_current_platform(platform, release_id,"
                    " updated_ts) VALUES('windows',?,?)"
                    " ON CONFLICT(platform) DO NOTHING",
                    (old["release_id"], old["updated_ts"]))
                self._conn.commit()

    def _file_path(self, platform, version, filename):
        """安装包物理路径。

        Windows 沿用历史布局 <client>/{version}/（零迁移）；其余平台用
        <client>/{platform}/{version}/，避免不同平台的同名版本互相覆盖。"""
        v = str(version)
        f = str(filename)
        if not re.match(r"^[A-Za-z0-9.]+$", v) or ".." in v:
            raise ClientReleaseError("版本号格式无效")
        if not _FILENAME_RE.match(f) or ".." in f:
            raise ClientReleaseError("文件名格式无效")
        if str(platform) == "windows":
            return os.path.join(self._storage_dir, v, f)
        return os.path.join(self._storage_dir, str(platform), v, f)

    def _row_dict(self, row, current_id=None):
        d = dict(row)
        d["is_current"] = bool(current_id is not None
                               and d["id"] == current_id)
        return d

    # -- 上传 / 查询 / 发布 --

    def upload(self, platform, version, filename, data, note=""):
        """上传安装包（同平台同版本覆盖更新）；返回记录 dict。"""
        platform = str(platform or "windows").strip().lower()
        if platform not in PLATFORMS:
            raise ClientReleaseError(
                "平台仅支持 %s" % " / ".join(PLATFORMS))
        version = str(version or "").strip()
        filename = str(filename or "").strip()
        note = str(note or "").strip()
        if not _VERSION_RE.match(version):
            raise ClientReleaseError("版本号必须为数字点分格式（如 4.0.0）")
        if not filename or not _FILENAME_RE.match(filename):
            raise ClientReleaseError("文件名格式无效")
        if not data:
            raise ClientReleaseError("上传内容为空")
        if len(data) > _MAX_UPLOAD_BYTES:
            raise ClientReleaseError("安装包超出大小上限")
        now = int(time.time())
        sha256 = hashlib.sha256(data).hexdigest()
        with self._lock:
            path = self._file_path(platform, version, filename)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            row = self._conn.execute(
                "SELECT id, filename FROM client_releases"
                " WHERE platform=? AND version=?",
                (platform, version)).fetchone()
            if row is not None and row["filename"] != filename:
                old = self._file_path(platform, version, row["filename"])
                try:
                    if os.path.isfile(old):
                        os.remove(old)
                except OSError:
                    pass    # 旧文件清理失败不阻断新版本落库
            with open(path, "wb") as fh:
                fh.write(data)
            if row is not None:
                self._conn.execute(
                    "UPDATE client_releases SET filename=?, sha256=?, size=?,"
                    " published_at=?, note=? WHERE id=?",
                    (filename, sha256, len(data), now, note, row["id"]))
                rid = row["id"]
            else:
                cur = self._conn.execute(
                    "INSERT INTO client_releases(platform, version, filename,"
                    " sha256, size, published_at, rollback_flag, note,"
                    " created_ts) VALUES (?,?,?,?,?,?,0,?,?)",
                    (platform, version, filename, sha256, len(data), now,
                     note, now))
                rid = cur.lastrowid
            self._conn.commit()
            out = dict(self._conn.execute(
                "SELECT * FROM client_releases WHERE id=?", (rid,)).fetchone())
        return out

    def list(self, platform=None):
        """版本列表（published_at 倒序），附 current 标记与回滚标记。

        platform=None 返回全部平台（控制台总览）；指定则只返回该平台。"""
        with self._lock:
            cur_map = {r["platform"]: r["release_id"]
                       for r in self._conn.execute(
                           "SELECT platform, release_id"
                           " FROM client_current_platform")}
            if platform:
                rows = self._conn.execute(
                    "SELECT * FROM client_releases WHERE platform=?"
                    " ORDER BY published_at DESC, id DESC",
                    (str(platform),)).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM client_releases ORDER BY published_at DESC,"
                    " id DESC").fetchall()
        return [self._row_dict(r, cur_map.get(r["platform"])) for r in rows]

    def get(self, release_id):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM client_releases WHERE id=?",
                (int(release_id),)).fetchone()
        return dict(row) if row is not None else None

    def get_current(self, platform="windows"):
        """指定平台的当前发布版本（无则返回 None）。

        兼容：新表尚无该平台记录时，windows 回退读旧单指针（历史部署形态）。"""
        with self._lock:
            cur_row = self._conn.execute(
                "SELECT release_id FROM client_current_platform"
                " WHERE platform=?", (str(platform),)).fetchone()
            if cur_row is None and str(platform) == "windows":
                cur_row = self._conn.execute(
                    "SELECT release_id FROM client_current"
                    " WHERE id=1").fetchone()
            if cur_row is None:
                return None
            row = self._conn.execute(
                "SELECT * FROM client_releases WHERE id=?",
                (cur_row["release_id"],)).fetchone()
        return dict(row) if row is not None else None

    def current_version(self, platform="windows"):
        cur = self.get_current(platform)
        return cur["version"] if cur else None

    def set_current(self, release_id):
        """设置 current（回滚=指回旧版）；按记录所属平台独立设指针。"""
        row = self.get(release_id)
        if row is None:
            raise ClientReleaseError("版本记录不存在", 404)
        platform = str(row.get("platform") or "windows")
        now = int(time.time())
        with self._lock:
            # 「更新版本」判定限定同平台：published_at 更晚，平局（同秒上传）
            # 时 id 更大者视为更新（上传顺序即版本顺序）
            newer = self._conn.execute(
                "SELECT COUNT(*) AS n FROM client_releases WHERE id<>?"
                " AND platform=?"
                " AND (published_at>? OR (published_at=? AND id>?))",
                (row["id"], platform, row["published_at"],
                 row["published_at"], row["id"])).fetchone()["n"]
            rollback = 1 if newer > 0 else 0
            self._conn.execute(
                "INSERT INTO client_current_platform(platform, release_id,"
                " updated_ts) VALUES (?,?,?) ON CONFLICT(platform) DO UPDATE"
                " SET release_id=excluded.release_id,"
                " updated_ts=excluded.updated_ts",
                (platform, row["id"], now))
            self._conn.execute(
                "UPDATE client_releases SET rollback_flag=? WHERE id=?",
                (rollback, row["id"]))
            self._conn.commit()
            out = dict(self._conn.execute(
                "SELECT * FROM client_releases WHERE id=?",
                (row["id"],)).fetchone())
        return out

    def read_file(self, release_id):
        """按记录读安装包字节（不存在抛 404 语义错误）。"""
        row = self.get(release_id)
        if row is None:
            raise ClientReleaseError("版本记录不存在", 404)
        path = self._file_path(row.get("platform") or "windows",
                               row["version"], row["filename"])
        if not os.path.isfile(path):
            raise ClientReleaseError("安装包文件缺失", 404)
        with open(path, "rb") as fh:
            return row, fh.read()

    def read_current_file(self, platform):
        """读指定平台 current 版本的安装包（公开下载用）。

        返回 (row, data)；该平台未发布或文件缺失 → ClientReleaseError(404)。"""
        cur = self.get_current(platform)
        if cur is None:
            raise ClientReleaseError("该平台尚未发布客户端", 404)
        return self.read_file(cur["id"])

    def platforms_overview(self):
        """各平台最新发布概览（公开下载页数据源，不含任何敏感字段）。"""
        out = []
        for pf in PLATFORMS:
            cur = self.get_current(pf) or {}
            out.append({
                "platform": pf,
                "label": PLATFORM_LABELS.get(pf, pf),
                "available": bool(cur),
                "version": cur.get("version") or "",
                "filename": cur.get("filename") or "",
                "size": cur.get("size") or 0,
                "sha256": cur.get("sha256") or "",
                "published_at": cur.get("published_at") or 0,
                "release_note": cur.get("note") or "",
            })
        return out

    def manifest(self):
        """终端更新 manifest（无 current 返回 None）。"""
        cur = self.get_current()
        if cur is None:
            return None
        return {
            "latest_version": cur["version"],
            "download_url": "/download/client/setup",
            "sha256": cur["sha256"],
            "size": cur["size"],
            "release_note": cur["note"],
        }

    # -- 一次性下载票据 --

    def issue_ticket(self, release_id, custom_filename, ttl=_TICKET_TTL_SEC):
        """为定制包签发一次性下载票据（内存态，10 分钟有效）。"""
        row = self.get(release_id)
        if row is None:
            raise ClientReleaseError("版本记录不存在", 404)
        ticket = secrets.token_urlsafe(24)
        now = time.time()
        with self._ticket_lock:
            expired = [k for k, v in self._tickets.items() if v["exp"] < now]
            for k in expired[:_TICKET_SWEEP_LIMIT]:
                self._tickets.pop(k, None)
            self._tickets[ticket] = {
                "release_id": int(release_id),
                "filename": str(custom_filename),
                "exp": now + int(ttl),
                "used": False,
            }
        return ticket

    def consume_ticket(self, ticket):
        """消费票据：一次性 + 有效期校验；成功返回
        {"release_id", "filename"}（filename 为定制名，用于下载响应头）。"""
        with self._ticket_lock:
            info = self._tickets.pop(str(ticket or ""), None)
        if info is None:
            raise ClientReleaseError("下载票据无效或已使用", 404)
        if info["exp"] < time.time():
            raise ClientReleaseError("下载票据已过期，请重新生成", 410)
        return {"release_id": info["release_id"],
                "filename": info["filename"]}

    # -- 更新推送（中心主动下发 client_update；2026-09-19）--
    #
    # 设计取舍：批次只记「谁在何时对哪些终端下发了什么模式」，逐终端的
    # pending/sent/executed/failed/timeout **不在本表冗余**——commands 表
    # 已是那份真值（source='client-push:<batch_id>'），统计靠聚合查询得。
    # 这样命令状态机只有一处写入方（store.enqueue_command/complete_command），
    # 不存在两表不同步的问题。

    def push_source(self, batch_id):
        """批次 → commands.source 标记（逐终端命令凭此归属批次）。"""
        return "client-push:%d" % int(batch_id)

    def push_create(self, mode, target_version, targets, operator="", note=""):
        """登记推送批次并返回 batch_id（逐终端入队由调用方完成）。"""
        now = int(time.time())
        tlist = list(targets or [])
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO client_push_batches(mode, target_version,"
                " targets_json, total, created_ts, operator, note)"
                " VALUES(?,?,?,?,?,?,?)",
                (str(mode), str(target_version or ""),
                 json.dumps(tlist, ensure_ascii=False), len(tlist), now,
                 str(operator or ""), str(note or "")))
            self._conn.commit()
            bid = cur.lastrowid
            cur.close()
        return bid

    def push_delete(self, batch_id):
        """删除批次台账（**仅清理记录**，不撤回已下发命令——命令状态机由
        commands 表独立持有）。返回是否命中。"""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM client_push_batches WHERE id=?", (int(batch_id),))
            self._conn.commit()
            n = cur.rowcount
            cur.close()
        return n > 0

    def _batch_stats(self, batch_id):
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM commands WHERE source=?"
                " GROUP BY status",
                (self.push_source(batch_id),)).fetchall()
        st = {"pending": 0, "sent": 0, "executed": 0, "failed": 0, "timeout": 0}
        for r in rows:
            if r["status"] in st:
                st[r["status"]] = int(r["n"])
        st["total"] = sum(st.values())
        return st

    def _batch_dict(self, row):
        d = dict(row)
        try:
            d["targets"] = json.loads(d.pop("targets_json") or "[]")
        except Exception:
            d["targets"] = []
        d["stats"] = self._batch_stats(d["id"])
        return d

    def batch_get(self, batch_id):
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM client_push_batches WHERE id=?",
                (int(batch_id),)).fetchone()
        return self._batch_dict(r) if r is not None else None

    def list_batches(self, limit=20):
        """推送批次（倒序），附逐状态计数。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM client_push_batches ORDER BY id DESC LIMIT ?",
                (int(limit),)).fetchall()
        return [self._batch_dict(r) for r in rows]

    def batch_detail(self, batch_id, limit=500):
        """批次明细：逐终端命令状态 + 回执摘要 + 终端版本/在线情况。

        回执形状：终端 _post_result 提交 {"ok":bool,"data":{...}}，data 内层
        为 client_update handler 的 out（mode/status/version/note/error）。"""
        src = self.push_source(batch_id)
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.id AS command_id, c.terminal_id, c.status,"
                " c.created_ts, c.sent_ts, c.done_ts, c.result_json,"
                " t.ip, t.client_version, t.last_seen"
                " FROM commands c"
                " LEFT JOIN terminals t ON t.terminal_id=c.terminal_id"
                " WHERE c.source=? ORDER BY c.id ASC LIMIT ?",
                (src, int(limit))).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            try:
                res = json.loads(d.pop("result_json") or "{}")
            except Exception:
                res = {}
            if not isinstance(res, dict):
                res = {}
            # 回执存储形状：命令回执端点（api.py 的 /commands/{cid}/result）把终端的
            # {"ok":..,"data":{..}} 拆开落库——result_json 里就是**内层 data**。
            # 故此处直接取字段，不要再套一层 data（2026-09-19 真机实测修正：
            # 原实现多套一层导致控制台批次明细字段全空）。
            # ok 以命令终态为准：executed=成功，其余（failed/timeout）=未成功。
            d["ok"] = (d["status"] == "executed")
            d["mode"] = res.get("mode")
            d["apply_status"] = res.get("status")
            d["version"] = res.get("version")
            d["note"] = res.get("note")
            d["error"] = res.get("error")
            items.append(d)
        return items


_SINGLE = {}


def get_cr(db_path, data_dir):
    """进程级单例（desktop_policy.get_dp 同款形态）。"""
    key = os.path.normpath(os.path.abspath(db_path))
    inst = _SINGLE.get(key)
    if inst is None:
        inst = ClientReleaseStore(db_path, data_dir)
        _SINGLE[key] = inst
    return inst
