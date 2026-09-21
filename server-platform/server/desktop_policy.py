# -*- coding: utf-8 -*-
"""观枢终端平台服务端 · 终端桌面管控：壁纸资源库 + 四类策略 + 匹配推荐 + 下发记录。

契约：desktop-policy/docs/CONTRACT.md v1（terminal 3 端点 + console 8 端点）。
实现裁定（ADR-036，与 desktop-policy-dev 对齐记录）：
- 壁纸上传：POST /console/desktoppolicy/wallpapers/{name}?category=&mime=，
  body 为图片原始字节（不经 JSON 解析，上限 10MB；替代 multipart，零新依赖）；
- wallpaper_id 统一整数（dp_wallpapers.id）；
- dp_policies.group_id 引用平台 asset_groups.id；终端生效策略 = 其组内
  enabled 且 revision 最大（平局取 id 大）的一条；
- revision 全局单调（publish = max(revision)+1），report 以 (revision,
  terminal_id) 唯一定位下发记录；
- dp_deliveries 行在终端拉取 policy 时 upsert（pending→delivered；已有
  applied/warn/partial/failed 终态不因重复拉取重置）；
- 策略 payload 存配置形状（desktop_wallpaper.wallpaper_ids 池 + mode +
  rotation、lock_screen.wallpaper_id、power_plan、idle_lock）；响应中的
  per_monitor 由服务端按终端显示器简报即时匹配产出（exact →
  aspect_higher_res → default_fallback + 告警）；
- rotation 为策略参数原样下发（P1 不做服务端轮换调度，契约未定义触发机制）；
- 状态机：applied（全 ok 无告警）/ warn（全 ok 但有 default_fallback 或
  blocked_by_security）/ partial（部分 ok）/ failed（全失败）；
  error_code 记首个非 ok 码或告警码（rollback_failed 最高告警）。
"""
import hashlib
import json
import os
import sqlite3
import threading
import time

_MAX_WALLPAPER_BYTES = 10 * 1024 * 1024   # 10MB（Handler 读上限 16MB 之内）
_ASPECT_TOLERANCE = 0.02                  # 宽高比匹配容差（2560x1440≈1920x1080）

_WALLPAPER_MODES = ("fill", "fit", "stretch", "tile", "center")
_POWER_PLANS = ("balanced", "high_performance", "power_saver", "custom")
_POWER_BUTTONS = ("sleep", "shutdown", "hibernate", "none")

DELIVERY_STATES = ("pending", "delivered", "applied", "partial",
                   "failed", "warn")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dp_wallpapers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'default',
  width INTEGER NOT NULL, height INTEGER NOT NULL,
  aspect REAL NOT NULL, size_bytes INTEGER NOT NULL,
  sha256 TEXT NOT NULL UNIQUE, path TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL, created_by TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS idx_dp_wallpapers_cat
  ON dp_wallpapers(category, enabled);
CREATE TABLE IF NOT EXISTS dp_policies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, group_id INTEGER,
  payload TEXT NOT NULL,              -- JSON 配置形状（1.1 policies 减 per_monitor）
  revision INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  updated_at INTEGER NOT NULL, updated_by TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS idx_dp_policies_group
  ON dp_policies(group_id, enabled, revision);
CREATE TABLE IF NOT EXISTS dp_deliveries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  policy_id INTEGER NOT NULL, revision INTEGER NOT NULL,
  terminal_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  error_code TEXT, error_detail TEXT,
  reported_at INTEGER,
  created_at INTEGER NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS idx_dp_delivery_unique
  ON dp_deliveries(policy_id, revision, terminal_id);
CREATE INDEX IF NOT EXISTS idx_dp_delivery_term
  ON dp_deliveries(terminal_id, revision);
"""

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


class DpError(Exception):
    """桌面管控业务错误（message 为简体中文，可直接作为 API error 文案）。"""

    def __init__(self, message, http_status=400):
        Exception.__init__(self, message)
        self.message = message
        self.http_status = http_status


def _image_size(data):
    """解析 PNG / JPEG 像素尺寸（纯 Python 头解析，stdlib-only）。

    返回 (width, height, ext)；不支持格式抛 ValueError。"""
    if data[:8] == _PNG_SIG:
        if len(data) < 24:
            raise ValueError("truncated png")
        w = int.from_bytes(data[16:20], "big")
        h = int.from_bytes(data[20:24], "big")
        if w <= 0 or h <= 0:
            raise ValueError("bad png size")
        return w, h, "png"
    if data[:2] == b"\xff\xd8":
        i = 2
        n = len(data)
        while i + 9 < n:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker == 0xD8 or marker == 0x01 or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            if marker == 0xDA:
                break                       # 进入扫描数据仍未遇 SOF
            seg_len = int.from_bytes(data[i + 2:i + 4], "big")
            if seg_len < 2:
                break
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h = int.from_bytes(data[i + 5:i + 7], "big")
                w = int.from_bytes(data[i + 7:i + 9], "big")
                if w <= 0 or h <= 0:
                    raise ValueError("bad jpeg size")
                return w, h, "jpg"
            i += 2 + seg_len
        raise ValueError("jpeg SOF not found")
    raise ValueError("unsupported image format")


def parse_monitors_brief(mi):
    """解析终端轮询简报 `idx,w,h,primary;...` → [{index,width,height,primary}]。

    宽容解析：坏段跳过（完整显示器信息以 report 为准）。"""
    out = []
    for seg in str(mi or "").split(";"):
        parts = seg.split(",")
        if len(parts) < 3:
            continue
        try:
            out.append({"monitor_index": int(parts[0]),
                        "width": int(parts[1]), "height": int(parts[2]),
                        "primary": bool(int(parts[3])) if len(parts) > 3
                        else False})
        except ValueError:
            continue
    return out


class DesktopPolicyStore(object):
    """桌面管控存储与领域逻辑（自持连接 + 线程锁，kb_store 同款形态）。"""

    def __init__(self, db_path, data_dir):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False,
                                     timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._wallpaper_dir = os.path.join(data_dir, "desktop_policy",
                                           "wallpapers")
        if not os.path.isdir(self._wallpaper_dir):
            os.makedirs(self._wallpaper_dir)

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 壁纸资源库
    # ------------------------------------------------------------------

    def add_wallpaper(self, data, name, category, mime, created_by):
        if not data:
            raise DpError("壁纸内容为空")
        if len(data) > _MAX_WALLPAPER_BYTES:
            raise DpError("壁纸超过大小上限（10MB）")
        try:
            w, h, ext = _image_size(data)
        except ValueError:
            raise DpError("仅支持 PNG / JPEG 格式图片")
        name = str(name or "").strip()
        if not name:
            raise DpError("壁纸名称必填")
        if len(name) > 64:
            raise DpError("壁纸名称过长（≤64 字符）")
        category = str(category or "default").strip() or "default"
        now = int(time.time())
        sha = hashlib.sha256(data).hexdigest()
        with self._lock:
            dup = self._conn.execute(
                "SELECT id FROM dp_wallpapers WHERE sha256=?", (sha,)
            ).fetchone()
            if dup:
                raise DpError("已存在相同内容的壁纸（#%d）" % dup["id"])
            cur = self._conn.execute(
                "INSERT INTO dp_wallpapers(name, category, width, height,"
                " aspect, size_bytes, sha256, path, enabled, created_at,"
                " created_by) VALUES(?,?,?,?,?,?,?,? ,1,?,?)",
                (name, category, w, h, round(w / float(h), 6), len(data),
                 sha, "", now, str(created_by or "")))
            wid = cur.lastrowid
            path = os.path.join(self._wallpaper_dir,
                                "%d.%s" % (wid, ext))
            with open(path, "wb") as f:
                f.write(data)
            self._conn.execute("UPDATE dp_wallpapers SET path=? WHERE id=?",
                               (path, wid))
            self._conn.commit()
        return self.get_wallpaper(wid)

    def get_wallpaper(self, wid):
        row = self._conn.execute(
            "SELECT * FROM dp_wallpapers WHERE id=?", (wid,)).fetchone()
        return dict(row) if row else None

    def get_wallpaper_file(self, wid):
        """返回 (bytes, mime, sha256)；文件丢失抛 DpError(file_missing)。"""
        row = self.get_wallpaper(wid)
        if not row or not row["enabled"]:
            raise DpError("壁纸不存在或已下架", 404)
        try:
            with open(row["path"], "rb") as f:
                data = f.read()
        except OSError:
            raise DpError("壁纸文件缺失", 404)
        mime = "image/png" if row["path"].endswith(".png") else "image/jpeg"
        return data, mime, row["sha256"]

    def list_wallpapers(self, category, page, page_size):
        where, args = "1=1", []
        if category:
            where += " AND category=?"
            args.append(category)
        total = self._conn.execute(
            "SELECT COUNT(*) AS c FROM dp_wallpapers WHERE " + where,
            args).fetchone()["c"]
        rows = self._conn.execute(
            "SELECT * FROM dp_wallpapers WHERE " + where
            + " ORDER BY id DESC LIMIT ? OFFSET ?",
            args + [page_size, (page - 1) * page_size]).fetchall()
        cats = [r["category"] for r in self._conn.execute(
            "SELECT DISTINCT category FROM dp_wallpapers ORDER BY category")]
        return {"ok": True, "total": total, "page": page,
                "page_size": page_size, "categories": cats,
                "wallpapers": [dict(r) for r in rows]}

    def delete_wallpaper(self, wid):
        row = self.get_wallpaper(wid)
        if not row:
            raise DpError("壁纸不存在", 404)
        ref = self._wallpaper_refs(wid)
        if ref:
            raise DpError("壁纸正被策略引用（%s），请先调整对应策略" % ref,
                          409)
        with self._lock:
            self._conn.execute("DELETE FROM dp_wallpapers WHERE id=?",
                               (wid,))
            self._conn.commit()
        try:
            if row["path"] and os.path.isfile(row["path"]):
                os.remove(row["path"])
        except OSError:
            pass
        return True

    def _wallpaper_refs(self, wid):
        """检查壁纸是否被任一策略 payload 引用，返回引用策略名列表。"""
        refs = []
        wid_s = str(wid)
        for row in self._conn.execute(
                "SELECT id, name, payload FROM dp_policies WHERE enabled=1"):
            try:
                p = json.loads(row["payload"])
            except ValueError:
                continue
            dw = p.get("desktop_wallpaper") or {}
            ids = dw.get("wallpaper_ids") or []
            ls = p.get("lock_screen") or {}
            if (not isinstance(ids, list) and False) or \
               wid in [x for x in ids if isinstance(x, int)] or \
               ls.get("wallpaper_id") == wid:
                refs.append("#%d %s" % (row["id"], row["name"]))
        return refs

    # ------------------------------------------------------------------
    # 策略（四类 payload 校验 + CRUD + 发布）
    # ------------------------------------------------------------------

    def validate_payload(self, payload):
        """四类策略结构校验；通过返回规范化 payload（dict）。"""
        if not isinstance(payload, dict) or not payload:
            raise DpError("策略内容不能为空")
        clean = {}
        for key, rule in payload.items():
            if key not in ("desktop_wallpaper", "lock_screen",
                           "power_plan", "idle_lock"):
                raise DpError("未知的策略类型：%s" % key)
            if not isinstance(rule, dict) or \
                    not isinstance(rule.get("enabled"), bool):
                raise DpError("%s 缺少 enabled 布尔值" % key)
            spec = dict(rule)
            if key == "desktop_wallpaper":
                mode = spec.get("mode") or "stretch"
                if mode not in _WALLPAPER_MODES:
                    raise DpError("desktop_wallpaper.mode 取值无效")
                spec["mode"] = mode
                ids = spec.get("wallpaper_ids")
                if not isinstance(ids, list) or not ids or \
                        not all(isinstance(x, int) for x in ids):
                    raise DpError("desktop_wallpaper.wallpaper_ids "
                                  "须为非空整数数组")
                for wid in ids:
                    if not self.get_wallpaper(wid):
                        raise DpError("壁纸 #%d 不存在" % wid)
                rot = spec.get("rotation") or {}
                if not isinstance(rot, dict):
                    raise DpError("rotation 须为对象")
                spec["rotation"] = rot
            elif key == "lock_screen":
                wid = spec.get("wallpaper_id")
                if not isinstance(wid, int) or not self.get_wallpaper(wid):
                    raise DpError("lock_screen.wallpaper_id 不存在")
            elif key == "power_plan":
                plan = spec.get("plan") or "balanced"
                if plan not in _POWER_PLANS:
                    raise DpError("power_plan.plan 取值无效")
                spec["plan"] = plan
                if plan == "custom":
                    custom = spec.get("custom")
                    if not isinstance(custom, dict):
                        raise DpError("plan=custom 时须提供 custom 参数")
                    for f in ("display_off_ac", "display_off_dc",
                              "sleep_ac", "sleep_dc",
                              "disk_off_ac", "disk_off_dc"):
                        v = custom.get(f)
                        if not isinstance(v, int) or not (60 <= v <= 86400):
                            raise DpError("power_plan.custom.%s 须为 "
                                          "60~86400 秒" % f)
                    for f in ("power_button_ac", "power_button_dc"):
                        if custom.get(f) not in _POWER_BUTTONS:
                            raise DpError("power_plan.custom.%s 取值无效" % f)
            elif key == "idle_lock":
                v = spec.get("minutes")
                if not isinstance(v, int) or not (1 <= v <= 1440):
                    raise DpError("idle_lock.minutes 须为 1~1440 分钟")
                spec["screen_saver_secure"] = \
                    bool(spec.get("screen_saver_secure"))
            clean[key] = spec
        return clean

    def save_policy(self, name, group_id, payload, updated_by, policy_id=None):
        name = str(name or "").strip()
        if not name:
            raise DpError("策略名称必填")
        if len(name) > 64:
            raise DpError("策略名称过长（≤64 字符）")
        if group_id is not None:
            try:
                group_id = int(group_id)
            except (TypeError, ValueError):
                raise DpError("group_id 无效")
        clean = self.validate_payload(payload)
        now = int(time.time())
        with self._lock:
            if policy_id is None:
                cur = self._conn.execute(
                    "INSERT INTO dp_policies(name, group_id, payload,"
                    " revision, enabled, updated_at, updated_by)"
                    " VALUES(?,?,?,0,1,?,?)",
                    (name, group_id, json.dumps(clean, ensure_ascii=False),
                     now, str(updated_by or "")))
                pid = cur.lastrowid
            else:
                cur = self._conn.execute(
                    "UPDATE dp_policies SET name=?, group_id=?, payload=?,"
                    " enabled=1, updated_at=?, updated_by=? WHERE id=?",
                    (name, group_id, json.dumps(clean, ensure_ascii=False),
                     now, str(updated_by or ""), policy_id))
                if cur.rowcount != 1:
                    raise DpError("策略不存在", 404)
                pid = policy_id
            self._conn.commit()
        return self.get_policy(pid)

    def get_policy(self, pid):
        row = self._conn.execute(
            "SELECT * FROM dp_policies WHERE id=?", (pid,)).fetchone()
        return self._policy_out(row) if row else None

    @staticmethod
    def _policy_out(row):
        d = dict(row)
        try:
            d["payload"] = json.loads(d["payload"])
        except ValueError:
            d["payload"] = {}
        return d

    def list_policies(self):
        rows = self._conn.execute(
            "SELECT * FROM dp_policies ORDER BY id DESC").fetchall()
        out = []
        for r in rows:
            d = self._policy_out(r)
            g = self._conn.execute(
                "SELECT name FROM asset_groups WHERE id=?",
                (r["group_id"],)).fetchone()
            d["group_name"] = g["name"] if g else (
                "全部终端" if r["group_id"] is None else None)
            out.append(d)
        return {"ok": True, "policies": out}

    def publish_policy(self, pid, updated_by):
        row = self.get_policy(pid)
        if not row:
            raise DpError("策略不存在", 404)
        with self._lock:
            nxt = self._conn.execute(
                "SELECT COALESCE(MAX(revision), 0) AS m FROM dp_policies"
            ).fetchone()["m"] + 1
            self._conn.execute(
                "UPDATE dp_policies SET revision=?, enabled=1,"
                " updated_at=?, updated_by=? WHERE id=?",
                (nxt, int(time.time()), str(updated_by or ""), pid))
            self._conn.commit()
        return self.get_policy(pid)

    def effective_policy(self, group_id):
        """终端生效策略（v1.1 裁定：group_id NULL = 全部终端）：
        1) 绑定组内 enabled 且 revision 最大（平局取 id 大）；
        2) 组内无 → 回退全局策略（group_id IS NULL）；
        3) 均无 → None（终端拉取返回 unchanged）。"""
        for gid in (group_id, None):
            row = self._conn.execute(
                "SELECT * FROM dp_policies WHERE group_id IS ? AND enabled=1"
                " ORDER BY revision DESC, id DESC LIMIT 1",
                (gid,)).fetchone()
            if row:
                return self._policy_out(row)
        return None

    # ------------------------------------------------------------------
    # 匹配推荐（exact → aspect_higher_res → default_fallback）
    # ------------------------------------------------------------------

    @staticmethod
    def match_wallpaper(width, height, cands):
        """在候选壁纸（enabled）中为单个显示器选型 → (wallpaper_id, match)。"""
        exact = [c for c in cands
                 if c["width"] == width and c["height"] == height]
        if exact:
            exact.sort(key=lambda c: (c["width"] * c["height"], -c["id"]))
            return exact[0]["id"], "exact"
        target = width / float(height) if height else 0
        higher = [c for c in cands
                  if c["height"] > 0 and c["width"] >= width
                  and c["height"] >= height
                  and abs(c["aspect"] - target) <= _ASPECT_TOLERANCE]
        if higher:
            higher.sort(key=lambda c: (c["width"] * c["height"], -c["id"]))
            return higher[0]["id"], "aspect_higher_res"
        defaults = [c for c in cands if c["category"] == "default"] or cands
        if defaults:
            defaults.sort(key=lambda c: (c["width"] * c["height"], -c["id"]))
            return defaults[0]["id"], "default_fallback"
        return None, None

    def _enabled_candidates(self):
        rows = self._conn.execute(
            "SELECT id, name, category, width, height, aspect, sha256"
            " FROM dp_wallpapers WHERE enabled=1").fetchall()
        return [dict(r) for r in rows]

    def _build_per_monitor(self, dw, monitors):
        """desktop_wallpaper 配置 + 终端显示器列表 → per_monitor 匹配结果
        （v1.1：item 附 checksum 供终端避免坏缓存）。"""
        pool = self._enabled_candidates()
        by_id = dict((c["id"], c) for c in pool)
        ids = dw.get("wallpaper_ids") or []
        if ids:
            pool = [c for c in pool if c["id"] in ids] or pool
        per = []
        for m in monitors:
            wid, match = self.match_wallpaper(m["width"], m["height"], pool)
            if wid is not None:
                item = {"monitor_index": m["monitor_index"],
                        "wallpaper_id": wid,
                        "match": match or "default_fallback"}
                if wid in by_id:
                    item["checksum"] = by_id[wid]["sha256"]
                per.append(item)
        return per

    # ------------------------------------------------------------------
    # 终端拉取 + 下发记录
    # ------------------------------------------------------------------

    def get_policy_for_terminal(self, terminal_id, last_revision, mi):
        """终端策略拉取：无更新返回 unchanged；有更新返回全量 policies
        并 upsert 下发记录（pending→delivered）。"""
        try:
            last = int(last_revision or 0)
        except (TypeError, ValueError):
            last = 0
        term = self._conn.execute(
            "SELECT group_id FROM terminals WHERE terminal_id=?",
            (terminal_id,)).fetchone()
        gid = term["group_id"] if term else None
        eff = self.effective_policy(gid)
        if not eff or eff["revision"] == last:
            return {"ok": True, "unchanged": True, "revision": last}
        payload = eff["payload"]
        policies = {}
        dw = payload.get("desktop_wallpaper")
        if dw:
            monitors = parse_monitors_brief(mi)
            out = dict(dw)
            out["per_monitor"] = (self._build_per_monitor(dw, monitors)
                                  if monitors else [])
            policies["desktop_wallpaper"] = out
        for key in ("lock_screen", "power_plan", "idle_lock"):
            if payload.get(key):
                policies[key] = payload[key]
        ls = policies.get("lock_screen")
        if ls and isinstance(ls.get("wallpaper_id"), int):
            w = self.get_wallpaper(ls["wallpaper_id"])
            if w:
                ls["checksum"] = w["sha256"]
        self._delivery_pull(eff["id"], eff["revision"], terminal_id)
        return {"ok": True, "revision": eff["revision"],
                "server_time": int(time.time()), "policies": policies}

    def _delivery_pull(self, policy_id, revision, terminal_id):
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM dp_deliveries WHERE policy_id=?"
                " AND revision=? AND terminal_id=?",
                (policy_id, revision, terminal_id)).fetchone()
            now = int(time.time())
            if not row:
                self._conn.execute(
                    "INSERT INTO dp_deliveries(policy_id, revision,"
                    " terminal_id, status, created_at)"
                    " VALUES(?,?,?,'delivered',?)",
                    (policy_id, revision, terminal_id, now))
            elif row["status"] == "pending":
                self._conn.execute(
                    "UPDATE dp_deliveries SET status='delivered'"
                    " WHERE policy_id=? AND revision=? AND terminal_id=?",
                    (policy_id, revision, terminal_id))
            self._conn.commit()

    def save_report(self, terminal_id, data):
        """执行结果回传落库：状态机 + error_code 留痕。"""
        try:
            revision = int(data.get("revision"))
        except (TypeError, ValueError):
            raise DpError("report.revision 无效")
        results = data.get("results")
        if not isinstance(results, list) or not results:
            raise DpError("report.results 不能为空")
        for r in results:
            if not isinstance(r, dict) or not r.get("policy"):
                raise DpError("report.results 元素缺少 policy")
        with self._lock:
            row = self._conn.execute(
                "SELECT id, policy_id, status FROM dp_deliveries"
                " WHERE revision=? AND terminal_id=? ORDER BY id DESC"
                " LIMIT 1", (revision, terminal_id)).fetchone()
            if not row:
                raise DpError("下发记录不存在（revision=%d）" % revision, 404)
            oks = [r for r in results if r.get("ok")]
            warn_codes = ("blocked_by_security",)
            warn_hit = any(
                (not r.get("ok") and (r.get("error") or {}).get("code")
                 in warn_codes)
                or (r.get("ok") and (r.get("detail") or {}).get("match")
                    == "default_fallback")
                for r in results)
            err_entry = next((r for r in results if not r.get("ok")), None)
            err = (err_entry or {}).get("error") or {}
            if not oks:
                status = "failed"
            elif len(oks) == len(results):
                status = "warn" if warn_hit else "applied"
            else:
                status = "partial"
            error_code = (err.get("code") if err_entry else None) or \
                ("default_fallback" if warn_hit and not err_entry else None)
            self._conn.execute(
                "UPDATE dp_deliveries SET status=?, error_code=?,"
                " error_detail=?, reported_at=? WHERE id=?",
                (status, error_code,
                 json.dumps(
                     {"results": results,
                      "monitors": data.get("monitors"),
                      "virtual": data.get("virtual"),
                      "session_type": data.get("session_type")},
                     ensure_ascii=False),
                 int(data.get("reported_at") or time.time()), row["id"]))
            self._conn.commit()
            return {"ok": True, "status": status}

    def list_deliveries(self, status, error_code, terminal_id,
                        page, page_size):
        where, args = "1=1", []
        if status:
            where += " AND status=?"
            args.append(status)
        if error_code:
            where += " AND error_code=?"
            args.append(error_code)
        if terminal_id:
            where += " AND terminal_id=?"
            args.append(terminal_id)
        total = self._conn.execute(
            "SELECT COUNT(*) AS c FROM dp_deliveries WHERE " + where,
            args).fetchone()["c"]
        rows = self._conn.execute(
            "SELECT * FROM dp_deliveries WHERE " + where
            + " ORDER BY id DESC LIMIT ? OFFSET ?",
            args + [page_size, (page - 1) * page_size]).fetchall()
        return {"ok": True, "total": total, "page": page,
                "page_size": page_size,
                "deliveries": [dict(r) for r in rows]}

    def overview(self):
        q = self._conn.execute
        week_ago = int(time.time()) - 7 * 86400
        return {"ok": True,
                "wallpapers": q(
                    "SELECT COUNT(*) AS c FROM dp_wallpapers"
                    " WHERE enabled=1").fetchone()["c"],
                "policies": q(
                    "SELECT COUNT(*) AS c FROM dp_policies"
                    " WHERE enabled=1").fetchone()["c"],
                "delivered_7d": q(
                    "SELECT COUNT(*) AS c FROM dp_deliveries"
                    " WHERE created_at>=? AND status IN"
                    " ('applied','warn')", (week_ago,)).fetchone()["c"],
                "warn_7d": q(
                    "SELECT COUNT(*) AS c FROM dp_deliveries"
                    " WHERE created_at>=? AND status='warn'",
                    (week_ago,)).fetchone()["c"],
                "blocked_7d": q(
                    "SELECT COUNT(*) AS c FROM dp_deliveries"
                    " WHERE created_at>=? AND error_code="
                    "'blocked_by_security'",
                    (week_ago,)).fetchone()["c"],
                "failed_7d": q(
                    "SELECT COUNT(*) AS c FROM dp_deliveries"
                    " WHERE created_at>=? AND status IN ('failed','partial')",
                    (week_ago,)).fetchone()["c"]}


_DP_SINGLETON = None
_DP_LOCK = threading.Lock()


def get_dp(db_path, data_dir):
    """惰性单例工厂（kb_store.get_kb 同款形态）。"""
    global _DP_SINGLETON
    with _DP_LOCK:
        if _DP_SINGLETON is None:
            _DP_SINGLETON = DesktopPolicyStore(db_path, data_dir)
        return _DP_SINGLETON
