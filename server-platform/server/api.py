# -*- coding: utf-8 -*-
"""观枢终端平台服务端 · REST API v1 分发、控制台静态服务、HTML 报告。

设计要点（ADR-004/005/008）：
- 终端上行鉴权：X-ETP-Token；控制台鉴权：用户名+口令登录换 token
  （auth_upgrade：PBKDF2 哈希 + SQLite 持久会话 + 失败锁定 + IP 限速 + 审计）。
- 指标上报：对象或数组均可（批量预留）；Content-Encoding: gzip 支持（预留）。
- 指标入库后即过瓶颈规则引擎，命中且超出去重窗口则落 bottlenecks 并回告终端。
- 导出报告：单文件自包含 HTML（全局规范）。
"""
import datetime
import gzip
import io
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from analysis import evaluate
from settings import mask_secret
import client_release
import desktop_policy
import kb_store
import nad_client
import power_control
import wol

# 登录改造（等保三级）：PBKDF2 哈希 + SQLite 持久会话 + 失败锁定 + 审计
import auth_upgrade as auth

# 控制台「首页」聚合路由组（home-console-dev 辖区，独立文件自治演进）
import api_home

auth.DB_PATH = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "console_auth.db"))
os.makedirs(os.path.dirname(auth.DB_PATH), exist_ok=True)

CONSOLE_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "console"))

_MAX_BODY = 8 * 1024 * 1024  # 8MB，防滥用
_MAX_UPLOAD_BODY = 256 * 1024 * 1024  # 安装包上传上限（ADR-042，仅发布端点）

# 批量管控命令白名单（POST /console/terminals/batch-command）：
# 只放行「对终端行为有直接影响」的管控类命令。诊断类（iperf_client /
# net_probe / collect_logs / ai_context / pc_diag）必须走单终端端点，
# 避免批量误伤或在大量终端上并发发起探测造成风暴。
_BATCH_CMD_ALLOWED = ("power_action", "power_action_abort", "client_update")

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}


class ApiError(Exception):
    """带 HTTP 状态码的业务异常。"""

    def __init__(self, status, message):
        Exception.__init__(self, message)
        self.status = status
        self.message = message


class ApiContext(object):
    """全局上下文：存储 + 配置 + 控制台会话 + 运行时配置 + 存储管理。"""

    def __init__(self, store, config, settings=None, storage=None):
        self.store = store
        self.config = config
        self.settings = settings
        self.storage = storage
        self.sessions = {}  # console token -> expiry_ts
        self._login_attempts = {}  # ip -> [timestamps]

    # ---- 控制台会话 ----

    def login(self, password, client_ip):
        if not password or password != self.config.get("console_password"):
            raise ApiError(401, "invalid password")
        now = time.time()
        token = secrets.token_hex(16)
        ttl = int(self.config.get("session_ttl_hours", 8)) * 3600
        self._gc_sessions(now)
        self.sessions[token] = now + ttl
        return {"ok": True, "token": token, "expires_in": ttl}

    def check_console(self, token):
        if not token or token not in self.sessions:
            return False
        if self.sessions[token] < time.time():
            del self.sessions[token]
            return False
        return True

    def check_terminal_token(self, token, client_ip):
        """终端上行鉴权（多 token 模型，ADR-021）：
        config.json 的 terminal_token 或 terminal_tokens 表 status='active'
        命中均放行；表命中时节流更新 last_used_ts（60 秒内不重复写，
        避免高频心跳刷库）。config token 命中不落表（保持向后兼容零开销）。"""
        if not token:
            return False
        if token == self.config.get("terminal_token"):
            return True
        row = self.store.token_get_active(token)
        if row is None:
            return False
        try:
            self.store.token_touch_last_used(row["id"], row.get("last_used_ts"))
        except Exception:
            pass    # 非关键写失败不影响鉴权结论
        return True

    def _gc_sessions(self, now):
        for t in list(self.sessions.keys()):
            if self.sessions[t] < now:
                del self.sessions[t]

    # ---- 登录限速（防暴力尝试，MVP 简单滑动窗口）----

    def login_rate_ok(self, client_ip):
        now = time.time()
        window = 60
        attempts = [t for t in self._login_attempts.get(client_ip, []) if now - t < window]
        self._login_attempts[client_ip] = attempts
        if len(attempts) >= 10:
            return False
        attempts.append(now)
        return True


# ----------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------

def _esc(text):
    if text is None:
        return ""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _fmt_ts(ts):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts)))
    except (TypeError, ValueError):
        return "-"


def _safe_filename(text):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text))[:40] or "unknown"


def _parse_body(headers, body):
    """解析请求体 JSON；支持 gzip（预留）。"""
    if not body:
        return None
    if (headers.get("content-encoding") or "").lower().find("gzip") >= 0:
        try:
            body = gzip.decompress(body)
        except (OSError, gzip.BadGzipFile, ValueError):
            raise ApiError(400, "invalid gzip body")
    if len(body) > _MAX_BODY:
        raise ApiError(413, "body too large")
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ApiError(400, "invalid json body")


def _parse_json_body(body):
    """无 header 上下文时的 JSON 解析（console 分支内部使用）。"""
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ApiError(400, "invalid json body")


def _require(obj, key):
    if not isinstance(obj, dict) or not obj.get(key):
        raise ApiError(400, "missing field: %s" % key)
    return obj.get(key)


def _q_int(query, name, default):
    try:
        return int(query.get(name, default))
    except (TypeError, ValueError):
        return default


def _q_str(query, name):
    v = query.get(name)
    return str(v).strip() if v else ""


def _json_response(status, payload):
    return status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), \
        "application/json; charset=utf-8"


# ----------------------------------------------------------------------
# 主分发
# ----------------------------------------------------------------------

# 服务器版本单一来源（app.py 从此导入；health 端点回显，防多 hardcode 漂移）
APP_VERSION = "1.3.0"


def _scope_allowed(scope, method, path):
    """端口路由白名单（ADR-032）：跨类访问在分发前即 404，不进入鉴权层，
    避免在错误端口泄露路由存在性。health 双口放行（探活/监控）。"""
    if path == "/api/v1/health":
        return True
    if scope == "terminal":
        return (path.startswith("/api/v1/terminals/")
                or path == "/api/v1/ai/analyze"
                or path in ("/api/v1/client/manifest",
                            "/api/v1/client/update-manifest")
                or path.startswith("/download/"))
    if scope == "console":
        if path.startswith("/api/v1/console/"):
            return True
        return method == "GET" and not path.startswith("/api/")
    if scope == "download":
        # 公开客户端下载口（80）：仅下载页与「各平台最新包」下载，
        # 终端/管理路由一律 404，避免在公开端口泄露内部路由存在性
        return (path in ("/", "/download")
                or path.startswith("/download/latest/")
                or path == "/api/v1/download/platforms")
    return True



def dispatch(ctx, method, path, query, headers, body, client_ip, scope=None):
    """返回 (status, payload_bytes, content_type)。

    scope（ADR-032 双 HTTPS 端口路由分离；白名单以 _scope_allowed 为准）：
      None/"all"     — legacy HTTP 端口：全量路由（过渡期并行）。
      "terminal"     — TLS 终端口：/api/v1/health、/api/v1/terminals/*、
                       /api/v1/ai/analyze、/api/v1/client/manifest（含
                       update-manifest 别名）、/download/*（ADR-042 终端
                       更新拉包）；其余一律 404。
      "console"      — TLS 管理口：仅 /api/v1/health、/api/v1/console/*、
                       静态资源（GET）；终端路由一律 404。
    """
    if scope not in (None, "all") and not _scope_allowed(scope, method, path):
        raise ApiError(404, "not found")
    # ---------- 无鉴权 ----------
    if path == "/api/v1/health" and method == "GET":
        return _json_response(200, {"ok": True, "service": "eyeterm-server",
                                    "version": APP_VERSION,
                                    "ts": int(time.time())})

    # ---------- 公开客户端下载（80 端口专属 scope，无需登录）----------
    if scope == "download":
        if path in ("/", "/download") and method == "GET":
            return _download_page()
        if path == "/api/v1/download/platforms" and method == "GET":
            return _json_response(200, {"ok": True,
                                        "platforms": _cr_overview(ctx)})
        if path.startswith("/download/latest/") and method == "GET":
            return _download_latest(ctx, path.rsplit("/", 1)[-1])
        raise ApiError(404, "not found")

    if path == "/api/v1/console/login" and method == "POST":
        data = _parse_body(headers, body) or dict()
        r = auth.authenticate(str(data.get("username") or "admin"),
            str(data.get("password") or ""),
            client_ip, headers.get("User-Agent", ""))
        if not r.ok:
            return _json_response(r.http_status, dict(
                ok=False, msg=r.message, retry_after=r.retry_after or 0))
        return _json_response(200, dict(
            ok=True, token=r.token, msg=r.message,
            must_change_password=r.must_change_password,
            last_login_at=r.last_login_at, last_login_ip=r.last_login_ip,
            role=(r.user or {}).get("role")))

    # ---------- 终端上行（X-ETP-Token + 白名单准入）----------
    if path.startswith("/api/v1/terminals/"):
        if not ctx.check_terminal_token(headers.get("x-etp-token"), client_ip):
            ctx.store.audit(client_ip, path, "auth_reject", "invalid terminal token")
            raise ApiError(401, "invalid terminal token")
        _admission(ctx, method, path, client_ip)
        return _terminal_api(ctx, method, path, query, headers, body,
                             client_ip)

    # ---------- AI 分析（终端侧发起，X-ETP-Token）----------
    if path == "/api/v1/ai/analyze" and method == "POST":
        if not ctx.check_terminal_token(headers.get("x-etp-token"), client_ip):
            ctx.store.audit(client_ip, path, "auth_reject", "invalid terminal token")
            raise ApiError(401, "invalid terminal token")
        data = _parse_body(headers, body) or {}
        tid = str(data.get("terminal_id") or "")
        if not tid or not ctx.store.get_terminal(tid):
            raise ApiError(404, "terminal not found")
        issue = str(data.get("issue_description") or "")
        # ADR-030/031：kind 字段或 issue_description 前缀 → 聚合分支
        kind = str(data.get("kind") or "").strip().lower()
        if not kind and issue.lower().startswith(
                ("ipconflict", "ip_conflict", "ip冲突")):
            kind = "ipconflict"
        if not kind and issue.startswith("路由追踪分析"):
            kind = "routetrace"
        route_ctx = (data.get("context")
                     if kind == "routetrace" and isinstance(
                         data.get("context"), dict) else None)
        return _json_response(200, run_ai_analysis(ctx, tid, issue, "terminal",
                                                   kind=kind or None,
                                                   route_ctx=route_ctx))

    # ---------- 客户端更新 manifest（终端侧拉取，X-ETP-Token，ADR-042）----------
    # 两个路径同一数据源：
    #   /api/v1/client/manifest          — 嵌套形状 {ok, manifest:{...}}（ADR-042 登记）
    #   /api/v1/client/update-manifest   — 扁平形状（TBC-002 对齐别名，power-control
    #                                      updater.check_async 消费；?version= 当前
    #                                      版本由终端侧 semver 比较，服务端忽略）
    if path in ("/api/v1/client/manifest",
                "/api/v1/client/update-manifest") and method == "GET":
        if not ctx.check_terminal_token(headers.get("x-etp-token"), client_ip):
            ctx.store.audit(client_ip, path, "auth_reject",
                            "invalid terminal token")
            raise ApiError(401, "invalid terminal token")
        try:
            m = ctx.cr.manifest()
        except Exception:
            m = None
        if path == "/api/v1/client/update-manifest":
            # 扁平形状：键恒在（无 current 时值 null，消费侧判 latest_version）
            flat = m or {}
            return _json_response(200, {
                "ok": True,
                "latest_version": flat.get("latest_version"),
                "download_url": flat.get("download_url"),
                "sha256": flat.get("sha256"),
                "size": flat.get("size"),
                "release_note": flat.get("release_note")})
        return _json_response(200, {"ok": True, "manifest": m})

    # ---------- 控制台 API（X-ETP-Console-Token，SQLite 持久会话）----------
    if path.startswith("/api/v1/console/"):
        sess = auth.resolve_session(headers.get("x-etp-console-token") or "",
                                    client_ip, headers.get("User-Agent", ""))
        if sess is None:
            raise ApiError(401, "console session invalid, login again")
        # 会话信息（前端按 role 决定「系统管理」导航可见性）
        if path == "/api/v1/console/session-info" and method == "GET":
            u = auth.get_user(int(sess["user_id"]))
            return _json_response(200, dict(
                ok=True, username=sess["username"],
                role=(u["role"] if u else None),
                must_change_password=bool(sess["must_change_password"])))
        # 自助改密（等保三级 8.1.4.1）：成功后吊销其它会话，保留当前会话
        if path == "/api/v1/console/password" and method == "POST":
            data = _parse_body(headers, body) or {}
            r = auth.change_password(
                int(sess["user_id"]),
                str(data.get("old_password") or ""),
                str(data.get("new_password") or ""),
                str(data.get("confirm_password") or ""),
                client_ip, headers.get("User-Agent", ""),
                current_session_id=int(sess["id"]))
            if not r.ok:
                return _json_response(r.http_status,
                                      dict(ok=False, msg=r.message))
            return _json_response(200, dict(ok=True, msg=r.message))
        return _console_api(ctx, method, path, query, headers, body,
                            sess=sess, client_ip=client_ip)

    # ---------- 客户端安装包下载（公开通用包 / 票据定制包，ADR-042）----------
    if path == "/download/client/setup" and method == "GET":
        return _client_download(ctx, query)

    # ---------- 静态资源 ----------
    if method == "GET":
        return _static(path, headers)

    raise ApiError(404, "not found")


def _cr_overview(ctx):
    """各平台发布概览（公开下载页数据源；任何异常降级为空列表）。"""
    cr = getattr(ctx, "cr", None)
    if cr is None:
        return []
    try:
        return cr.platforms_overview()
    except Exception:
        return []


def _download_page():
    """公开下载页（静态 HTML，随 console 目录一并分发）。"""
    path = os.path.join(CONSOLE_DIR, "download.html")
    if not os.path.isfile(path):
        raise ApiError(404, "download page not found")
    with open(path, "rb") as fh:
        return Response(200, fh.read(), "text/html; charset=utf-8")


def _download_latest(ctx, platform):
    """公开下载：指定平台 current 版本安装包（无需登录、无票据）。

    与终端更新共用同一 current 指针，保证「下载页拿到的」与「终端自动更新
    拿到的」永远是同一个版本。"""
    cr = getattr(ctx, "cr", None)
    if cr is None:
        raise ApiError(500, "客户端发布模块未初始化")
    pf = str(platform or "").strip().lower()
    if pf not in client_release.PLATFORMS:
        raise ApiError(404, "not found")
    try:
        row, data = cr.read_current_file(pf)
    except client_release.ClientReleaseError as e:
        raise ApiError(e.http_status, e.message)
    return Response(200, data, "application/octet-stream",
                    'attachment; filename="%s"' % row["filename"])


def _client_download(ctx, query):
    """安装包下载（三种形态，同一份实体，仅换响应文件名）：

    - 无参数        ：通用包（公开；装完需手动填平台地址与 token）
    - ?ticket=      ：定制包·一次性票据（10 分钟，控制台「立即下载」用）
    - ?cfg64=       ：定制包·**长期有效**（2026-09-19 增补；供管理员分发给
      多台终端——原票据 10 分钟一次性，装一台就过期，无法批量分发）

    定制包的"一键连接"原理：响应文件名带 cfg64 段 → 安装器写入
    config_bootstrap.json → 客户端启动 bootstrap.apply_on_startup() 自动
    写 uplink 配置并进入注册，无需人工填表（见根仓 bootstrap.py）。
    服务端不存定制包实体——文件名即配置载体。
    安全说明：cfg64 内含平台地址与终端接入 token，**持有文件名即获授权**，
    与"把定制包分发给谁"的语义一致；故本端点不额外鉴权，但校验可解码。
    """
    cr = getattr(ctx, "cr", None)
    if cr is None:
        raise ApiError(500, "客户端发布模块未初始化")
    cfg64 = _q_str(query, "cfg64").strip()
    if cfg64:
        if not client_release.validate_cfg64(cfg64):
            raise ApiError(404, "定制配置无效")
        cur = cr.get_current()
        if cur is None:
            raise ApiError(404, "尚未发布任何客户端版本")
        row, data = cr.read_file(cur["id"])
        # 反解出 server/token 后按协议重算规范文件名（含 md58 校验段）
        try:
            conf = _decode_cfg64_local(cfg64)
        except Exception:
            raise ApiError(404, "定制配置无效")
        filename = client_release.build_custom_filename(
            conf["server"], conf["token"], cur["version"])
    else:
        ticket = _q_str(query, "ticket")
        if ticket:
            try:
                info = cr.consume_ticket(ticket)
            except client_release.ClientReleaseError as e:
                raise ApiError(e.http_status, e.message)
            row, data = cr.read_file(info["release_id"])
            filename = info["filename"]
        else:
            cur = cr.get_current()
            if cur is None:
                raise ApiError(404, "尚未发布任何客户端版本")
            row, data = cr.read_file(cur["id"])
            filename = cur["filename"]
    return Response(200, data, "application/octet-stream",
                    'attachment; filename="%s"' % filename)


def _decode_cfg64_local(cfg64):
    """cfg64 → {"server","token"}（服务端侧解码，与客户端 bootstrap 同算法）。"""
    import base64 as _b64
    import zlib as _zlib
    s = str(cfg64 or "").strip()
    b64 = s.replace("-", "+").replace("_", "/")
    b64 += "=" * (-len(b64) % 4)
    obj = json.loads(_zlib.decompress(_b64.b64decode(b64)).decode("utf-8"))
    return {"server": str(obj.get("s") or ""), "token": str(obj.get("t") or "")}


# ----------------------------------------------------------------------
# 终端准入（白名单中间件，ADR-013）
# 策略：空名单 = fail-closed（拒绝所有新注册）；已注册终端豁免（存量不踢）。
# ----------------------------------------------------------------------

def _whitelist_match(ctx, ip):
    """client_ip 是否命中任一启用条目（支持单 IP 与 CIDR）。空名单返回 False。"""
    entries = ctx.store.whitelist_list()
    if not entries:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for entry in entries:
        if not entry["enabled"]:
            continue
        try:
            network = ipaddress.ip_network(entry["cidr"], strict=False)
        except ValueError:
            continue
        if addr in network:
            return True
    return False


def _admission(ctx, method, path, client_ip):
    """终端 API 准入：register 强制白名单；其余已注册终端豁免。"""
    parts = path.strip("/").split("/")
    is_register = len(parts) >= 4 and parts[3] == "register"
    if is_register:
        if _whitelist_match(ctx, client_ip):
            return
        ctx.store.audit(client_ip, path, "admission_reject",
                        "register from non-whitelisted source (or empty list)")
        raise ApiError(403, "source not allowed (whitelist)")
    # 心跳/指标/事件/登记：已注册终端豁免；未知终端按白名单判
    tid = parts[3] if len(parts) >= 5 else ""
    if tid and ctx.store.get_terminal(tid):
        return
    if _whitelist_match(ctx, client_ip):
        return
    ctx.store.audit(client_ip, path, "admission_reject",
                    "unregistered terminal from non-whitelisted source")
    raise ApiError(403, "source not allowed (whitelist)")


# ----------------------------------------------------------------------
# 终端 API
# ----------------------------------------------------------------------

def _terminal_api(ctx, method, path, query, headers, body, client_ip):
    store = ctx.store
    parts = path.strip("/").split("/")  # api/v1/terminals/register 或
    # api/v1/terminals/{tid}/{action} → 各 4/5 段
    if method == "POST" and len(parts) == 4 and parts[3] == "register":
        data = _parse_body(headers, body) or {}
        terminal_id = _require(data, "terminal_id")
        registered = store.register_terminal(
            terminal_id=terminal_id,
            terminal_type=data.get("terminal_type") or "windows",
            hostname=data.get("hostname") or "",
            os_info=data.get("os_info") or "",
            client_version=data.get("client_version") or "",
            ip=data.get("ip") or client_ip,
            hwinfo=data.get("hwinfo"),
            asset=data.get("asset"))   # TBC-001：schema1 资产明细入库（缺键安全为 None）
        return _json_response(200, {"ok": True, "registered": registered})

    if len(parts) == 7 and method == "POST" and parts[4] == "commands" \
            and parts[6] == "result":
        # POST /terminals/{tid}/commands/{cid}/result — 命令回执
        terminal_id = parts[3]
        if not store.get_terminal(terminal_id):
            raise ApiError(404, "terminal not registered")
        try:
            cid = int(parts[5])
        except ValueError:
            raise ApiError(400, "bad command id")
        data = _parse_body(headers, body) or {}
        ok = bool(data.get("ok"))
        updated = store.complete_command(terminal_id, cid, ok,
                                         data.get("data") or {"error": data.get("error")})
        if not updated:
            raise ApiError(409, "command not in sent state (or unknown)")
        cmd = store.get_command(terminal_id, cid)
        if cmd and ctx.iperf:
            ctx.iperf.complete_from_command(cmd["command"], cmd["args"],
                                            data or {})
        if cmd and cmd.get("command") == "pc_apply_policy":
            pc = getattr(ctx, "pc", None)
            if pc is not None:
                try:
                    pc.on_command_result(
                        terminal_id, cid, ok,
                        (data.get("data") or {}) if isinstance(
                            data, dict) else {})
                except Exception:
                    pass   # 回执状态更新失败不影响命令链路（留 commands 记录）
        if cmd and cmd.get("command") == "pc_diag":
            pc = getattr(ctx, "pc", None)
            if pc is not None:
                try:
                    pc.diag_complete(
                        terminal_id, cid, ok,
                        (data.get("data") or {}) if isinstance(
                            data, dict) else {})
                except Exception:
                    pass   # 存档失败不影响命令链路（留 commands 记录）
        return _json_response(200, {"ok": True})

    # 深度检测任务（ADR-029）：GET /terminals/{tid}/netdoctor/ipconflict-deep/{task_id}
    if len(parts) == 7 and method == "GET" and parts[4] == "netdoctor" \
            and parts[5] == "ipconflict-deep":
        terminal_id = parts[3]
        if not store.get_terminal(terminal_id):
            raise ApiError(404, "terminal not registered")
        row = store.deep_task_get(parts[6])
        if not row or row.get("terminal_id") != terminal_id:
            raise ApiError(404, "task not found")
        return _json_response(200, {"ok": True, "task": row})

    # 桌面管控终端子路由（ADR-036）：
    #   GET  /terminals/{tid}/desktoppolicy/policy?revision=&mi=
    #   GET  /terminals/{tid}/desktoppolicy/wallpaper/{id}  （X-DP-Checksum 头）
    #   POST /terminals/{tid}/desktoppolicy/report
    if len(parts) >= 6 and parts[4] == "desktoppolicy":
        terminal_id = parts[3]
        if not store.get_terminal(terminal_id):
            raise ApiError(404, "terminal not registered")
        dp = getattr(ctx, "dp", None)
        if dp is None:
            raise ApiError(500, "桌面管控模块未初始化")
        if method == "GET" and len(parts) == 6 and parts[5] == "policy":
            return _json_response(200, dp.get_policy_for_terminal(
                terminal_id, query.get("revision"), query.get("mi")))
        if method == "GET" and len(parts) == 7 and parts[5] == "wallpaper":
            try:
                wid = int(parts[6])
            except ValueError:
                raise ApiError(400, "bad wallpaper id")
            try:
                data, mime, sha = dp.get_wallpaper_file(wid)
            except desktop_policy.DpError as e:
                raise ApiError(e.http_status, e.message)
            return Response(200, data, mime,
                            extra_headers={"X-DP-Checksum": sha})
        if method == "POST" and len(parts) == 6 and parts[5] == "report":
            data = _parse_body(headers, body) or {}
            try:
                return _json_response(200, dp.save_report(terminal_id, data))
            except desktop_policy.DpError as e:
                raise ApiError(e.http_status, e.message)

    # 自动开关机终端子路由（power-control P0，ADR-001）：
    #   POST /terminals/{tid}/powercontrol/snapshot（快照存档，X-ETP-Token）
    if len(parts) == 6 and parts[4] == "powercontrol":
        terminal_id = parts[3]
        if not store.get_terminal(terminal_id):
            raise ApiError(404, "terminal not registered")
        if parts[5] == "snapshot" and method == "POST":
            data = _parse_body(headers, body)
            if not isinstance(data, dict):
                raise ApiError(400, "snapshot body required")
            pc = getattr(ctx, "pc", None)
            if pc is None:
                raise ApiError(500, "power control module not initialized")
            try:
                saved = pc.save_snapshot(terminal_id, data)
            except power_control.PowerControlError as e:
                raise ApiError(e.http_status, e.message)
            return _json_response(200, {"ok": True,
                                        "snapshot_id": saved["id"],
                                        "terminal_id": terminal_id})
        # 关机配置上报（架构修正：定时关机终端本地执行；连接时+变更时
        # 各上报一次，中心按版本去重存档，X-ETP-Token）
        #   POST /terminals/{tid}/powercontrol/shutdown-config
        #   {config: {...pc_apply_policy shutdown 形状...}, version, ts}
        if parts[5] == "shutdown-config" and method == "POST":
            pc = getattr(ctx, "pc", None)
            if pc is None:
                raise ApiError(500, "power control module not initialized")
            data = _parse_body(headers, body) or {}
            config = data.get("config")
            if not isinstance(config, dict):
                raise ApiError(400, "config 对象必填")
            res = pc.shutdown_config_save(
                terminal_id, config, str(data.get("version") or ""),
                now=int(data.get("ts") or 0) or None)
            return _json_response(200, {"ok": True, "terminal_id":
                                        terminal_id, **res})
        # 开机任务（任务化 ADR-046 追加：中心任务驱动客户端，X-ETP-Token）
        #   GET /terminals/{tid}/powercontrol/boot-tasks — 命中本终端的
        #   启用任务（组展开与执行引擎同源），按下次触发升序
        if parts[5] == "boot-tasks" and method == "GET":
            pc = getattr(ctx, "pc", None)
            if pc is None:
                raise ApiError(500, "power control module not initialized")
            hits = pc.boot_tasks_for_terminal(store, terminal_id)
            slim = [{k: h.get(k) for k in
                     ("id", "name", "repeat", "weekdays", "once_date",
                      "time_hhmm", "source", "origin", "method",
                      "next_ts", "next_trigger")}
                    for h in hits]
            return _json_response(200, {
                "ok": True, "terminal_id": terminal_id, "tasks": slim,
                "generated_ts": int(time.time())})
        #   POST — 终端个性化开机任务（origin=client_personal，
        #   目标锁定本终端；每终端上限 5 条 fail-closed）
        if parts[5] == "boot-tasks" and method == "POST":
            pc = getattr(ctx, "pc", None)
            if pc is None:
                raise ApiError(500, "power control module not initialized")
            data = _parse_body(headers, body) or {}
            data = dict(data)
            data["kind"] = "boot"
            data["origin"] = "client_personal"
            data["source"] = "platform"
            data["target_type"] = "terminals"
            data["targets"] = [terminal_id]
            try:
                norm = power_control.validate_task_payload(data)
            except power_control.PowerControlError as e:
                raise ApiError(400, str(e))
            if pc.task_count_personal(terminal_id) >= \
                    power_control.PERSONAL_TASK_LIMIT:
                raise ApiError(409, "每终端个性化开机任务上限 %d 条"
                                    % power_control.PERSONAL_TASK_LIMIT)
            name = norm["name"] or pc.task_gen_name("boot",
                                                    norm["time_hhmm"])
            fields = dict(norm)
            fields["name"] = name
            fields["operator"] = "client:" + terminal_id
            task = pc.task_create(fields)
            exp = pc.wol_expand_for_task(pc.task_get(task["id"]), store)
            return _json_response(200, {"ok": True,
                                        "task_id": task["id"],
                                        "task": task, "expand": exp})
        raise ApiError(404, "not found")

    # 终端个性化开机任务维护（ADR-046 追加，X-ETP-Token）：
    #   PUT/DELETE /terminals/{tid}/powercontrol/boot-tasks/{task_id}
    #   仅可操作 origin=client_personal 且目标含本终端的任务
    if len(parts) == 7 and parts[4] == "powercontrol" \
            and parts[5] == "boot-tasks" and parts[6].isdigit():
        terminal_id = parts[3]
        if not store.get_terminal(terminal_id):
            raise ApiError(404, "terminal not registered")
        pc = getattr(ctx, "pc", None)
        if pc is None:
            raise ApiError(500, "power control module not initialized")
        t = pc.task_get(int(parts[6]))
        if not t:
            raise ApiError(404, "task not found")
        if str(t.get("origin") or "") != "client_personal" \
                or terminal_id not in [str(x) for x in
                                       (t.get("targets") or [])]:
            raise ApiError(403, "仅可维护本终端的个性化任务")
        if method == "PUT":
            data = _parse_body(headers, body) or {}
            try:
                norm = power_control.validate_task_payload(data, existing=t)
            except power_control.PowerControlError as e:
                raise ApiError(400, str(e))
            # 归属锁定：个性化任务的 kind/origin/目标/来源不可漂移
            norm["kind"] = "boot"
            norm["origin"] = "client_personal"
            norm["source"] = "platform"
            norm["target_type"] = "terminals"
            norm["group_id"] = None
            norm["targets"] = t.get("targets") or [terminal_id]
            fields = {k: v for k, v in norm.items() if k != "targets"}
            pc.task_update(t["id"], fields)
            pc.task_set_targets(t["id"], "terminals", None,
                                norm["targets"])
            task = pc.task_get(t["id"])
            exp = pc.wol_expand_for_task(task, store)
            pc.task_set_enabled(t["id"], bool(task.get("enabled")))
            return _json_response(200, {"ok": True,
                                        "task": pc.task_get(t["id"]),
                                        "expand": exp})
        if method == "DELETE":
            pc.task_delete(t["id"])
            return _json_response(200, {"ok": True})
        raise ApiError(405, "method not allowed")

    # net-doctor 终端子路由：/terminals/{tid}/netdoctor/{sub}（6 段）
    if len(parts) == 6 and parts[4] == "netdoctor":
        terminal_id = parts[3]
        if not store.get_terminal(terminal_id):
            raise ApiError(404, "terminal not registered")
        sub = parts[5]
        if sub == "ipconflict-deep" and method == "POST":
            # 深度检测编排（异步任务；ADR-029）：
            # resolve→arp→nad→macaddr→conclude，逐步进度经 GET 轮询
            data = _parse_body(headers, body) or {}
            rip = str(_require(data, "ip")).strip()
            rmac = str(_require(data, "mac")).strip()
            try:
                ipaddress.ip_address(rip)
            except ValueError:
                raise ApiError(400, "invalid ip address")
            from deep_engine import mac_key as _dmac_key
            if not _dmac_key(rmac):
                raise ApiError(400, "invalid mac address")
            if not _DEEP_SEMAPHORE.acquire(blocking=False):
                raise ApiError(429, "deep check busy, retry later")
            task_id = "DC-" + secrets.token_hex(4)
            store.deep_task_create(task_id, terminal_id, rip, rmac)
            threading.Thread(
                target=_deep_task_worker,
                args=(ctx, task_id, terminal_id, rip, rmac),
                daemon=True).start()
            return _json_response(200, {"ok": True, "task_id": task_id,
                                        "status": "running"})
        if sub == "ipconflict" and method == "POST":
            data = _parse_body(headers, body) or {}
            rip = str(_require(data, "ip"))
            rmac = str(_require(data, "mac"))
            verdict = store.ipconflict_report(terminal_id, rip, rmac)
            # 准入数据源接入（ADR-024）：画方准入真实登记 + 交换机端口近似；
            # 失败降级 not_configured/error，不阻断平台内判定
            _enrich_ipconflict_admission(verdict, store, rip, rmac)
            return _json_response(200, {"ok": True, "verdict": verdict})
        if sub == "route-nodes" and method == "GET":
            # 数据源统一（ADR-022）：优先 kb_entries category='route_nodes'
            # 最新条目（content 为 JSON 数组 [{match,zone,desc}]），
            # 解析失败/无条目回退 settings netdoctor.route_nodes。
            nodes = _kb_route_nodes(ctx)
            if not nodes:
                raw = ctx.settings.get("netdoctor.route_nodes") if ctx.settings else None
                if raw:
                    try:
                        nodes = json.loads(raw)
                    except ValueError:
                        nodes = []
            if not isinstance(nodes, list):
                nodes = []
            return _json_response(200, {"ok": True, "nodes": nodes,
                                        "source": "kb" if nodes else "settings"})
        if sub == "iperf-server" and method == "POST":
            data = _parse_body(headers, body) or {}
            mode = "udp" if str(data.get("mode") or "").lower() == "udp" else "tcp"
            duration = _q_int(data, "duration_sec", 60)
            spawned = ctx.iperf.spawn_server(terminal_id, mode, duration) \
                if ctx.iperf else None
            if not spawned:
                raise ApiError(503, "iperf server unavailable (no free port)")
            return _json_response(200, {"ok": True, "task_id": spawned[0],
                                        "port": spawned[1]})
        if sub == "iperf-result" and method == "POST":
            data = _parse_body(headers, body) or {}
            row = store.iperf_get(str(data.get("task_id") or ""))
            if not row or row["status"] != "running":
                raise ApiError(404, "task not running")
            store.iperf_finish(str(data.get("task_id")),
                               "done" if data.get("ok") else "failed",
                               data.get("data") or {}, "terminal stress result")
            return _json_response(200, {"ok": True})
        raise ApiError(404, "not found")

    # 终端 AI 智能诊断（ADR-023）：/terminals/{tid}/ai/diagnose（X-ETP-Token，同步 ≤120s）
    if len(parts) == 6 and parts[4] == "ai":
        if parts[5] != "diagnose" or method != "POST":
            raise ApiError(404, "not found")
        terminal_id = parts[3]
        if not store.get_terminal(terminal_id):
            raise ApiError(404, "terminal not registered")
        data = _parse_body(headers, body) or {}
        issue = str(data.get("issue") or "").strip()
        if not issue:
            raise ApiError(400, "issue is required")
        if len(issue) > 2000:
            raise ApiError(400, "issue too long (max 2000 chars)")
        logs = data.get("logs")
        if logs is not None and not isinstance(logs, dict):
            raise ApiError(400, "logs must be an object")
        result = run_terminal_diagnose(ctx, terminal_id, issue, logs or {})
        if not result.get("ok"):
            return _json_response(502, {
                "ok": False, "error": result.get("error") or "llm failed",
                "analysis_id": result.get("analysis_id"),
                "duration_ms": result.get("duration_ms")})
        return _json_response(200, {
            "ok": True, "analysis_id": result["analysis_id"],
            "response_text": result.get("response_text") or "",
            "model": result.get("model") or "",
            "duration_ms": result.get("duration_ms")})

    if len(parts) != 5:
        raise ApiError(404, "not found")
    terminal_id = parts[3]
    action = parts[4]

    if action == "heartbeat" and method == "POST":
        if not store.touch_terminal(terminal_id):
            raise ApiError(404, "terminal not registered")
        store.expire_commands(now=int(time.time()))
        pending = store.take_pending_commands(terminal_id)
        # 客户端更新提示（ADR-042）：捎带最新发布版本，省一次 manifest 请求
        try:
            latest_ver = ctx.cr.current_version()
        except Exception:
            latest_ver = None
        return _json_response(200, {
            "ok": True,
            "interval": int(ctx.config.get("heartbeat_timeout_sec", 180)) / 3,
            "report_interval": ctx.config.get("report_interval", 60),
            "commands": pending,
            "latest_version": latest_ver})

    if action == "metrics" and method == "POST":
        if not store.get_terminal(terminal_id):
            raise ApiError(404, "terminal not registered")
        data = _parse_body(headers, body)
        if not data:
            raise ApiError(400, "empty metrics payload")
        snapshots = data if isinstance(data, list) else [data]
        accepted = 0
        new_bottlenecks = []
        for snap in snapshots:
            if not isinstance(snap, dict):
                continue
            accepted += 1
            _ingest_snapshot(ctx, terminal_id, snap, new_bottlenecks)
        return _json_response(200, {"ok": True, "accepted": accepted,
                                    "bottlenecks": new_bottlenecks})

    if action == "events" and method == "POST":
        if not store.get_terminal(terminal_id):
            raise ApiError(404, "terminal not registered")
        data = _parse_body(headers, body) or {}
        level = data.get("level") or "info"
        if level not in ("info", "warn", "error", "critical"):
            level = "info"
        eid = store.insert_event(
            terminal_id=terminal_id,
            ts=int(data.get("ts") or time.time()),
            level=level,
            category=str(data.get("category") or "general"),
            message=str(data.get("message") or ""),
            detail=data.get("detail"))
        return _json_response(200, {"ok": True, "event_id": eid})

    if action == "uploads" and method == "POST":
        # 终端经 FTP 上传文件后的登记上报（source=api）
        if not store.get_terminal(terminal_id):
            raise ApiError(404, "terminal not registered")
        data = _parse_body(headers, body) or {}
        items = data.get("files") or []
        if not isinstance(items, list):
            raise ApiError(400, "files must be a list")
        ids = []
        for item in items:
            if not isinstance(item, dict) or not item.get("filename"):
                continue
            ids.append(store.insert_upload(
                terminal_id=terminal_id,
                filename=str(item.get("filename")),
                size=item.get("size") or 0,
                sha256=item.get("sha256") or "",
                path=str(item.get("path") or ""),
                source="api", ts=int(item.get("ts") or time.time())))
        return _json_response(200, {"ok": True, "registered": len(ids)})

    raise ApiError(404, "not found")


def _ingest_snapshot(ctx, terminal_id, snapshot, new_bottlenecks):
    """入库 + 瓶颈判定（含去重窗口）。"""
    store = ctx.store
    ts = snapshot.get("ts")
    try:
        ts = int(ts) if ts else int(time.time())
    except (TypeError, ValueError):
        ts = int(time.time())
    store.insert_metric(terminal_id, ts, snapshot)
    hits = evaluate(snapshot)
    if not hits:
        return
    now = int(time.time())
    dedup_sec = int(ctx.config.get("bottleneck_dedup_min", 10)) * 60
    for hit in hits:
        last = store.last_bottleneck_ts(terminal_id, hit["kind"], hit["metric_key"])
        if last is not None and now - last < dedup_sec:
            continue
        bid = store.insert_bottleneck(
            terminal_id=terminal_id, ts=now, kind=hit["kind"],
            metric_key=hit["metric_key"], value=hit["value"],
            threshold=hit["threshold"], level=hit["level"],
            detail={"rule": hit["rule"], "desc": hit["desc"]})
        new_bottlenecks.append({"id": bid, "rule": hit["rule"],
                                "metric_key": hit["metric_key"],
                                "value": hit["value"],
                                "threshold": hit["threshold"],
                                "desc": hit["desc"]})


# ----------------------------------------------------------------------
# 控制台 API
# ----------------------------------------------------------------------

def _console_api(ctx, method, path, query, headers=None, body=None,
                 sess=None, client_ip=None):
    store = ctx.store
    parts = path.strip("/").split("/")
    # 首页路由组（单点转发，实现在 api_home.py，home-console-dev 辖区）
    if parts[:4] == ["api", "v1", "console", "home"]:
        return api_home.handle(ctx, method, path, query, headers, body,
                               sess=sess, client_ip=client_ip)
    if len(parts) >= 4 and parts[3] == "kb":
        return _console_kb_api(ctx, method, parts, query, body, sess=sess)
    now = int(time.time())
    hb_timeout = int(ctx.config.get("heartbeat_timeout_sec", 180))

    # ---------- 资产组（树形分组 + 终端绑定，资产管理菜单）----------
    if parts[:4] == ["api", "v1", "console", "asset-groups"]:
        if method == "GET" and len(parts) == 4:
            return _json_response(200, {"ok": True,
                                        "groups": store.asset_group_list()})
        if method == "POST" and len(parts) == 4:
            data = _parse_json_body(body) or {}
            parent = data.get("parent_id")
            try:
                gid = store.asset_group_create(
                    str(data.get("name") or ""),
                    int(parent) if parent not in (None, "", 0) else None)
            except ValueError as exc:
                raise ApiError(400, str(exc))
            return _json_response(200, {"ok": True, "id": gid})
        if len(parts) == 5 and parts[4].isdigit():
            gid = int(parts[4])
            if method in ("POST", "PUT"):
                data = _parse_json_body(body) or {}
                if not store.asset_group_rename(gid, str(data.get("name") or "")):
                    raise ApiError(404, "group not found")
                return _json_response(200, {"ok": True})
            if method == "DELETE":
                result = store.asset_group_delete(gid)
                if result == "missing":
                    raise ApiError(404, "group not found")
                if result == "has_children":
                    raise ApiError(409, "先删除该组下的子目录后再删除本组")
                return _json_response(200, {"ok": True})
        raise ApiError(404, "not found")

    # ---------- 终端绑定/解绑资产组（group_id 为空 = 解绑）----------
    if method == "POST" and len(parts) == 6 and \
            parts[:4] == ["api", "v1", "console", "terminals"] and parts[5] == "group":
        data = _parse_json_body(body) or {}
        raw = data.get("group_id")
        gid = int(raw) if raw not in (None, "", 0) else None
        try:
            store.set_terminal_group(parts[4], gid)
        except ValueError as exc:
            raise ApiError(400, str(exc))
        return _json_response(200, {"ok": True})

    # DELETE /console/terminals/{tid}?purge=1 — 移除终端注册（可选级联清数据）
    if method == "DELETE" and len(parts) == 5 \
            and parts[:4] == ["api", "v1", "console", "terminals"]:
        tid = parts[4]
        try:
            purge = _q_str(query, "purge") in ("1", "true")
        except Exception:
            purge = False
        if not store.delete_terminal(tid, purge=purge):
            raise ApiError(404, "terminal not found")
        auth.audit("terminal.delete", "success",
                   username=(sess["username"] if sess else None),
                   client_ip=client_ip, target=tid,
                   detail={"purge": purge})
        return _json_response(200, {"ok": True, "purged": purge})

    if method == "GET" and parts == ["api", "v1", "console", "terminals"]:
        out = []
        for t in store.list_terminals():
            out.append({
                "terminal_id": t["terminal_id"],
                "terminal_type": t["terminal_type"],
                "hostname": t["hostname"],
                "os_info": t["os_info"],
                "client_version": t["client_version"],
                "ip": t["ip"],
                "first_seen": t["first_seen"],
                "last_seen": t["last_seen"],
                "online": (now - t["last_seen"]) < hb_timeout,
                "cpu_model": t.get("cpu_model") or "",
                "cpu_cores": t.get("cpu_cores"),
                "mem_total_mb": t.get("mem_total_mb"),
                "disk_total_gb": t.get("disk_total_gb"),
                "gpu_info": t.get("gpu_info") or "",
                "os_arch": t.get("os_arch") or "",
                "group_id": t.get("group_id"),
            })
        return _json_response(200, {"ok": True, "terminals": out, "now": now})

    if method == "GET" and len(parts) == 5 and \
            parts[:4] == ["api", "v1", "console", "terminals"]:
        tid = parts[4]
        t = store.get_terminal(tid)
        if not t:
            raise ApiError(404, "terminal not found")
        latest = store.query_metrics(tid, now - 3600)
        latest_snap = latest[-1] if latest else None
        bottlenecks = store.list_bottlenecks(limit=20, terminal_id=tid)
        events = store.list_events(limit=20, terminal_id=tid)
        return _json_response(200, {
            "ok": True,
            "terminal": {
                "terminal_id": t["terminal_id"],
                "terminal_type": t["terminal_type"],
                "hostname": t["hostname"],
                "os_info": t["os_info"],
                "client_version": t["client_version"],
                "ip": t["ip"],
                "first_seen": t["first_seen"],
                "last_seen": t["last_seen"],
                "cpu_model": t.get("cpu_model"),
                "cpu_cores": t.get("cpu_cores"),
                "mem_total_mb": t.get("mem_total_mb"),
                "disk_total_gb": t.get("disk_total_gb"),
                "gpu_info": t.get("gpu_info"),
                "os_arch": t.get("os_arch"),
                "online": (now - t["last_seen"]) < hb_timeout,
            },
            "asset": store.get_terminal_asset(tid),
            "latest_metrics": latest_snap,
            "bottlenecks": bottlenecks,
            "events": events,
        })

    if method == "GET" and len(parts) == 6 and \
            parts[:4] == ["api", "v1", "console", "terminals"] and parts[5] == "metrics":
        tid = parts[4]
        minutes = _q_int(query, "minutes", 60)
        minutes = max(5, min(minutes, 60 * 24 * 7))
        rows = store.query_metrics(tid, now - minutes * 60)
        return _json_response(200, {"ok": True, "minutes": minutes, "points": rows})

    if method == "GET" and parts == ["api", "v1", "console", "events"]:
        limit = _q_int(query, "limit", 100)
        since = _q_int(query, "since_ts", 0) or None
        rows = store.list_events(limit=limit, terminal_id=query.get("terminal_id"),
                                 since_ts=since)
        return _json_response(200, {"ok": True, "events": rows})

    if method == "GET" and parts == ["api", "v1", "console", "bottlenecks"]:
        limit = _q_int(query, "limit", 100)
        since = _q_int(query, "since_ts", 0) or None
        rows = store.list_bottlenecks(limit=limit,
                                      terminal_id=query.get("terminal_id"),
                                      since_ts=since)
        return _json_response(200, {"ok": True, "bottlenecks": rows})

    if method == "POST" and len(parts) == 6 and \
            parts[:4] == ["api", "v1", "console", "bottlenecks"] and \
            parts[5] == "ack":
        try:
            bid = int(parts[4])
        except ValueError:
            raise ApiError(400, "bad bottleneck id")
        if not store.ack_bottleneck(bid):
            raise ApiError(404, "bottleneck not found")
        return _json_response(200, {"ok": True})

    if method == "GET" and len(parts) == 5 and \
            parts[:4] == ["api", "v1", "console", "report"]:
        tid = parts[4]
        hours = _q_int(query, "hours", 24)
        hours = max(1, min(hours, 24 * 30))
        html = build_report_html(ctx, tid, hours)
        fname = "EyeTerm_Report_%s_%s.html" % (_safe_filename(tid),
                                               time.strftime("%Y%m%d"))
        payload = html.encode("utf-8")
        return status_disposition(200, payload,
                                  "text/html; charset=utf-8", fname)

    # ---------- 配置清单（功能1）----------
    if parts[:4] == ["api", "v1", "console", "whitelist"]:
        return _console_whitelist(ctx, method, parts, body)

    if parts[:4] == ["api", "v1", "console", "settings"]:
        if method == "GET":
            return _json_response(200, {"ok": True,
                                        "settings": ctx.settings.all_masked()})
        if method == "POST":
            data = _parse_json_body(body) or {}
            updates = data.get("settings") or {}
            if not isinstance(updates, dict):
                raise ApiError(400, "settings must be an object")
            changed = []
            for key, value in updates.items():
                if not re.match(r"^[a-z_]+\.[a-z_0-9]+$", key):
                    raise ApiError(400, "bad settings key: %s" % key)
                ctx.settings.set(key, "" if value is None else str(value))
                changed.append(key)
            return _json_response(200, {"ok": True, "changed": changed})
        raise ApiError(405, "method not allowed")

    if method == "GET" and parts == ["api", "v1", "console", "storage", "status"]:
        return _json_response(200, {"ok": True,
                                    "mount": ctx.storage.mount_status(),
                                    "ftp": ctx.storage.ftp_status()})

    if method == "POST" and parts == ["api", "v1", "console", "storage", "mount"]:
        return _json_response(200, ctx.storage.exec_mount())

    if method == "POST" and parts == ["api", "v1", "console", "storage", "scan"]:
        return _json_response(200, ctx.storage.scan_uploads())

    if method == "GET" and parts == ["api", "v1", "console", "uploads"]:
        return _json_response(200, {"ok": True,
                                    "uploads": store.list_uploads(_q_int(query, "limit", 50))})

    if method == "POST" and len(parts) == 6 and \
            parts[:4] == ["api", "v1", "console", "terminals"] and \
            parts[5] == "commands":
        # POST /console/terminals/{tid}/commands — 下发命令
        tid = parts[4]
        data = _parse_json_body(body) or {}
        command = str(data.get("command") or "").strip()
        allowed = ("iperf_client", "collect_logs", "ai_context", "net_probe",
                   "pc_apply_policy", "pc_diag", "power_action",
                   "power_action_abort", "wol_relay",
                   # client_update：中心主动推送客户端更新（控制台「客户端发布」
                   # →「更新推送」使用；本通用端点亦放行，便于定点排障）
                   "client_update")
        if command not in allowed:
            raise ApiError(400, "unknown command type (allowed: %s)" % ", ".join(allowed))
        if not store.get_terminal(tid):
            raise ApiError(404, "terminal not found")
        cid = store.enqueue_command(
            tid, command, data.get("args") or {},
            timeout_sec=int(data.get("timeout_sec") or 120),
            source=data.get("source") or "console")
        return _json_response(200, {"ok": True, "command_id": cid})

    # POST /api/v1/console/terminals/batch-command — 批量下发管控命令
    #   （资产管理页工具栏「更多」→ 重启 / 关机 等；2026-09-19 增补）
    # body {targets:[tid...] | "all", command:"power_action"|..., args:{...}}
    # 仅放行**管控类**命令（不放 iperf/collect_logs 等诊断类，避免误用面）；
    # admin-only + 审计；离线终端同样入队（上线后自然领取），与更新推送同语义。
    if method == "POST" and parts == ["api", "v1", "console", "terminals",
                                      "batch-command"]:
        if not auth.require_admin(sess):
            auth.audit(auth.Ev.ACCESS_DENIED, "blocked",
                       username=(sess["username"] if sess is not None else None),
                       client_ip=client_ip,
                       target="/api/v1/console/terminals/batch-command",
                       reason="admin_required")
            raise ApiError(403, "需要管理员权限")
        data = _parse_json_body(body) or {}
        command = str(data.get("command") or "").strip()
        allowed = _BATCH_CMD_ALLOWED
        if command not in allowed:
            raise ApiError(400, "该端点仅支持管控类命令：%s"
                                % " / ".join(allowed))
        raw = data.get("targets")
        if isinstance(raw, str) and raw.strip().lower() in ("all", "*", ""):
            tids = [t["terminal_id"] for t in store.list_terminals()]
        elif isinstance(raw, list):
            tids = []
            for item in raw:
                tid = str(item or "").strip()
                if tid and tid not in tids:
                    tids.append(tid)
        else:
            raise ApiError(400, "targets 必须为 \"all\" 或终端 ID 数组")
        if not tids:
            raise ApiError(400, "没有匹配的终端")
        missing = [t for t in tids if not store.get_terminal(t)]
        if missing:
            raise ApiError(404, "终端不存在：%s%s"
                                % (", ".join(missing[:5]),
                                   " 等 %d 台" % len(missing)
                                   if len(missing) > 5 else ""))
        args = data.get("args") if isinstance(data.get("args"), dict) else {}
        operator = sess["username"] if sess is not None else ""
        now = int(time.time())
        hb_timeout = int(ctx.config.get("heartbeat_timeout_sec", 180))
        reason = str(data.get("reason") or "").strip()[:200]
        online = 0
        for tid in tids:
            store.enqueue_command(tid, command, args,
                                  timeout_sec=_UPDATE_PUSH_TIMEOUT_SEC,
                                  source="console-batch", now=now)
            term = store.get_terminal(tid)
            last_seen = (term["last_seen"] or 0) if term is not None else 0
            if now - last_seen < hb_timeout:
                online += 1
        store.audit(client_ip, "/api/v1/console/terminals/batch-command",
                    "terminal_batch_command",
                    "command=%s total=%d online=%d args=%s by=%s %s"
                    % (command, len(tids), online,
                       json.dumps(args, ensure_ascii=False)[:200], operator,
                       reason))
        return _json_response(200, {
            "ok": True, "command": command, "total": len(tids),
            "online": online, "offline": len(tids) - online})

    # POST /api/v1/console/terminals/batch-wake — 批量开机（WoL 魔术包）
    # body {targets:[tid...] | "all"}
    # 关机态终端收不到任何命令，只能靠网卡唤醒。取目标机 MAC（资产自报 →
    # 火绒 → 画方，见 wol._target_mac 同源口径）后向同网段广播地址直发；
    # 跨网段直发无效（三层设备过滤 directed broadcast，2026-09-18 对照实验），
    # 故响应逐终端回 direct_applicable，供前端提示"跨网段需用中继"。
    if method == "POST" and parts == ["api", "v1", "console", "terminals",
                                      "batch-wake"]:
        if not auth.require_admin(sess):
            auth.audit(auth.Ev.ACCESS_DENIED, "blocked",
                       username=(sess["username"] if sess is not None else None),
                       client_ip=client_ip,
                       target="/api/v1/console/terminals/batch-wake",
                       reason="admin_required")
            raise ApiError(403, "需要管理员权限")
        data = _parse_json_body(body) or {}
        raw = data.get("targets")
        if isinstance(raw, str) and raw.strip().lower() in ("all", "*", ""):
            tids = [t["terminal_id"] for t in store.list_terminals()]
        elif isinstance(raw, list):
            tids = []
            for item in raw:
                tid = str(item or "").strip()
                if tid and tid not in tids:
                    tids.append(tid)
        else:
            raise ApiError(400, "targets 必须为 \"all\" 或终端 ID 数组")
        if not tids:
            raise ApiError(400, "没有匹配的终端")
        items = []
        sent_ok = 0
        for tid in tids:
            row = {"terminal_id": tid, "mac": "", "sent": [],
                   "direct_applicable": True, "error": None}
            try:
                mac = wol.target_mac(store, {"terminal_id": tid})
                row["mac"] = str(mac or "")
                if not row["mac"]:
                    raise ValueError("MAC 未知（终端未上报网卡信息）")
                row["direct_applicable"] = bool(
                    wol.direct_applicable(store, tid))
                bcasts = wol.target_broadcasts(store, tid)
                row["sent"] = wol.direct_send(
                    wol.normalize_mac(row["mac"]), bcasts)
                if row["sent"]:
                    sent_ok += 1
                else:
                    row["error"] = "无可达广播地址"
            except ValueError as e:
                row["error"] = str(e)[:120]
            except Exception as e:
                row["error"] = ("发送异常：%s" % str(e))[:120]
            items.append(row)
        operator = sess["username"] if sess is not None else ""
        store.audit(client_ip, "/api/v1/console/terminals/batch-wake",
                    "terminal_batch_wake",
                    "total=%d sent=%d by=%s" % (len(tids), sent_ok, operator))
        return _json_response(200, {
            "ok": True, "total": len(tids), "sent": sent_ok,
            "items": items})

    if method == "GET" and parts == ["api", "v1", "console", "commands"]:
        limit = _q_int(query, "limit", 100)
        return _json_response(200, {"ok": True,
                                    "commands": store.list_commands(
                                        query.get("terminal_id"), limit)})

    # POST /api/v1/console/terminals/{tid}/diag — 发起只读诊断（pc_diag，
    #   ADR-040 增补联调通道：admin-only + 审计 + 回执存档钩子）
    if method == "POST" and len(parts) == 6 and \
            parts[:4] == ["api", "v1", "console", "terminals"] and \
            parts[5] == "diag":
        if not auth.require_admin(sess):
            auth.audit(auth.Ev.ACCESS_DENIED, "blocked",
                       username=sess["username"] if sess is not None else None,
                       user_id=sess["user_id"] if sess is not None else None,
                       client_ip=client_ip,
                       target="/api/v1/console/terminals/%s/diag" % parts[4],
                       reason="admin_required")
            raise ApiError(403, "需要管理员权限")
        tid = parts[4]
        if not store.get_terminal(tid):
            raise ApiError(404, "terminal not found")
        pc = getattr(ctx, "pc", None)
        if pc is None:
            raise ApiError(500, "自动开关机模块未初始化")
        cid = store.enqueue_command(tid, "pc_diag", {"kind": "pc_diag"},
                                    timeout_sec=600, source="powercontrol")
        did = pc.diag_create(tid, cid, sess["username"] if sess else "")
        auth.audit("powercontrol.diag", "success",
                   username=(sess["username"] if sess else None),
                   client_ip=client_ip, target=tid,
                   detail={"diag_id": did, "command_id": cid})
        return _json_response(200, {"ok": True, "diag_id": did,
                                    "command_id": cid})

    # GET /api/v1/console/terminals/{tid}/diag — 该终端最近诊断记录
    if method == "GET" and len(parts) == 6 and \
            parts[:4] == ["api", "v1", "console", "terminals"] and \
            parts[5] == "diag":
        pc = getattr(ctx, "pc", None)
        if pc is None:
            raise ApiError(500, "自动开关机模块未初始化")
        rows = pc.diag_list(parts[4], limit=10)
        return _json_response(200, {"ok": True, "diags": rows})

    if parts[:4] == ["api", "v1", "console", "ai"]:
        if method == "POST" and parts == ["api", "v1", "console", "ai", "analyze"]:
            data = _parse_json_body(body) or {}
            tid = str(data.get("terminal_id") or "")
            if not tid or not store.get_terminal(tid):
                raise ApiError(404, "terminal not found")
            return _json_response(200, run_ai_analysis(
                ctx, tid, str(data.get("issue_description") or ""), "console"))
        if method == "GET" and parts == ["api", "v1", "console", "ai", "analyses"]:
            limit = _q_int(query, "limit", 50)
            return _json_response(200, {"ok": True,
                                        "analyses": store.ai_list(
                                            limit, query.get("terminal_id"))})
        if method == "GET" and len(parts) == 6 and parts[4] == "analyses" \
                and parts[5].isdigit():
            # 单条详情（含 context_json：终端诊断的 issue 与日志存证，ADR-023）
            row = store.ai_get(int(parts[5]))
            if not row:
                raise ApiError(404, "analysis not found")
            return _json_response(200, {"ok": True, "analysis": row})
        if method == "GET" and len(parts) == 6 and parts[4] == "report" \
                and parts[5].isdigit():
            row = store.ai_get(int(parts[5]))
            if not row:
                raise ApiError(404, "analysis not found")
            html = build_ai_report_html(row)
            fname = "EyeTerm_AI_%s_%d.html" % (_safe_filename(row["terminal_id"]),
                                               row["id"])
            return status_disposition(200, html.encode("utf-8"),
                                      "text/html; charset=utf-8", fname)
        raise ApiError(404, "not found")

    if parts[:4] == ["api", "v1", "console", "nettest"]:
        return _console_nettest(ctx, method, parts, query, body)

    if method == "GET" and parts == ["api", "v1", "console", "audit"]:
        return _json_response(200, {"ok": True,
                                    "audit": store.list_audit(_q_int(query, "limit", 50))})

    # ---------- 火绒终端安全（只读镜像，ADR-033）----------
    if parts[:4] == ["api", "v1", "console", "huorong"]:
        return _console_huorong(ctx, method, parts, query, body,
                                sess, client_ip)

    # ---------- 资产融合（火绒关联引擎 + 统一视图，ADR-033 增补）----------
    if parts[:4] == ["api", "v1", "console", "assets"]:
        return _console_assets(ctx, method, parts, query, body,
                               sess, client_ip)

    # ---------- 第三方数据源信息（资产右键弹窗聚合，ADR-043）----------
    if parts[:4] == ["api", "v1", "console", "thirdparty"]:
        return _console_thirdparty(ctx, method, parts, query, headers, body,
                                   sess, client_ip)

    # ---------- 桌面管控（壁纸资源库 + 四类策略，ADR-036）----------
    if parts[:4] == ["api", "v1", "console", "desktoppolicy"]:
        return _console_desktoppolicy(ctx, method, parts, headers, query,
                                      body, sess, client_ip)

    # ---------- 自动开关机（电源快照存档查询 + 策略下发，power-control P0/P1）----------
    if parts[:4] == ["api", "v1", "console", "powercontrol"]:
        return _console_powercontrol(ctx, method, parts, query, sess, body,
                                     client_ip)

    # ---------- 客户端版本发布（console，admin-only，ADR-042）----------
    if parts[:4] == ["api", "v1", "console", "client"]:
        return _console_client(ctx, method, parts, query, headers, body,
                               sess=sess, client_ip=client_ip)

    # ---------- 系统管理（sysadmin，admin-only，ADR-021）----------
    if parts[:4] == ["api", "v1", "console", "sysadmin"]:
        if sess is None or not auth.require_admin(sess):
            auth.audit(auth.Ev.ACCESS_DENIED, "blocked",
                       username=(sess["username"] if sess is not None else None),
                       user_id=(sess["user_id"] if sess is not None else None),
                       client_ip=client_ip, target=path, reason="admin_required")
            raise ApiError(403, "需要管理员权限")
        return _console_sysadmin(ctx, method, parts, headers, body,
                                 sess, client_ip)

    raise ApiError(404, "not found")


# ----------------------------------------------------------------------
# 火绒终端安全路由组（/api/v1/console/huorong/*，ADR-033）
# 契约冻结：overview / groups / clients / sync（sync 为 admin-only）。
# 全部只读缓存表，绝不透传火绒实时请求；任务类破坏性接口无路由。
# ----------------------------------------------------------------------

def _console_huorong(ctx, method, parts, query, body, sess, client_ip):
    syncer = getattr(ctx, "huorong", None)
    store = ctx.store
    sub = parts[4] if len(parts) >= 5 else ""

    if method == "GET" and len(parts) == 5 and sub == "overview":
        return _json_response(200, dict(ok=True, **store.hr_overview()))

    if method == "GET" and len(parts) == 5 and sub == "groups":
        return _json_response(200, {"ok": True,
                                    "groups": store.hr_groups_list()})

    if method == "GET" and len(parts) == 5 and sub == "clients":
        online_raw = _q_str(query, "online")
        online = int(online_raw) if online_raw in ("0", "1") else None
        gid_raw = _q_str(query, "group_id")
        try:
            group_id = int(gid_raw) if gid_raw else None
        except ValueError:
            raise ApiError(400, "bad group_id")
        data = store.hr_clients_page(
            group_id=group_id, online=online, q=_q_str(query, "q") or None,
            page=max(1, _q_int(query, "page", 1)),
            page_size=max(1, min(_q_int(query, "page_size", 50), 200)))
        return _json_response(200, dict(ok=True, **data))

    if method == "GET" and len(parts) == 5 and sub == "context":
        # 资产右键「第三方数据源信息」弹窗·火绒块（只读缓存，ADR-033）
        tid = _q_str(query, "terminal_id")
        if not tid:
            raise ApiError(400, "missing terminal_id")
        return _json_response(200, {"ok": True,
                                    "huorong": store.hr_context_block(tid)})

    if method == "POST" and len(parts) == 5 and sub == "sync":
        # 管理员鉴权（与 sysadmin 同款防线，operator 一律 403 + 审计）
        if sess is None or not auth.require_admin(sess):
            auth.audit(auth.Ev.ACCESS_DENIED, "blocked",
                       username=(sess["username"] if sess is not None else None),
                       user_id=(sess["user_id"] if sess is not None else None),
                       client_ip=client_ip, target="/api/v1/console/huorong/sync",
                       reason="admin_required")
            raise ApiError(403, "需要管理员权限")
        if syncer is None:
            raise ApiError(400, "火绒同步器未初始化")
        if not syncer.configured():
            raise ApiError(400, "火绒凭据未配置，请在系统设置中注入后重试")
        result = syncer.sync_once(trigger="manual")
        if result.get("busy"):
            return _json_response(409, {"ok": False, "error": "同步进行中，请稍后再试"})
        return _json_response(200, {"ok": True, "result": result})

    raise ApiError(404, "not found")


# ----------------------------------------------------------------------
# 资产融合路由组（/api/v1/console/assets/*，ADR-033 增补）
# 统一视图（火绒 89 组镜像 + 未关联平台终端「其他」组）+ 手动关联/调组。
# 条目三类 kind：matched（平台概览+火绒字段合并）/ huorong_only / platform_only。
# assign 两操作 operator 可用（资产日常操作），audit 留痕。
# ----------------------------------------------------------------------

def _console_assets(ctx, method, parts, query, body, sess, client_ip):
    store = ctx.store
    sub = parts[4] if len(parts) >= 5 else ""
    hb_timeout = int(ctx.config.get("heartbeat_timeout_sec", 180))

    if method == "GET" and len(parts) == 5 and sub == "groups":
        # 火绒分组 → 资产组镜像同步（幂等惰性触发，ADR-041）
        sync_stat = store.asset_group_sync_huorong(
            store.hr_groups_raw(), store.hr_matched_map())
        unlinked = store.hr_platform_unlinked()
        now = int(time.time())
        other_online = sum(1 for r in unlinked
                           if now - (r["last_seen"] or 0) < hb_timeout)
        return _json_response(200, {
            "ok": True,
            "platform_groups": store.asset_group_list(),
            "huorong_groups": store.hr_groups_with_stats(),
            "sync": sync_stat,
            "other": {"platform_total": len(unlinked),
                      "platform_online": other_online}})

    if method == "GET" and len(parts) == 5 and sub == "terminals":
        group_source = _q_str(query, "group_source") or "huorong"
        if group_source not in ("platform", "huorong"):
            raise ApiError(400, "bad group_source")
        page = max(1, _q_int(query, "page", 1))
        page_size = max(1, min(_q_int(query, "page_size", 50), 200))
        items = store.hr_unified_items(
            group_source=group_source, group_id=_q_str(query, "group_id")
            or None, q=_q_str(query, "q") or None, hb_timeout=hb_timeout)
        total = len(items)
        start = (page - 1) * page_size
        return _json_response(200, {"ok": True, "total": total,
                                    "page": page, "page_size": page_size,
                                    "items": items[start:start + page_size]})

    if method == "POST" and len(parts) == 5 and sub == "assign-link":
        data = _parse_json_body(body) or {}
        cid = str(data.get("huorong_client_id") or "").strip()
        if not cid or not store.hr_client_exists(cid):
            raise ApiError(400, "huorong client not found")
        raw_tid = data.get("terminal_id")
        tid = str(raw_tid).strip() if raw_tid not in (None, "") else None
        if tid is None:
            # 解除关联 + 记 ignore 对（下轮自动 pass 不拉回）
            deleted = store.hr_map_delete(cid)
            if deleted and deleted.get("terminal_id"):
                store.hr_ignore_add(cid, deleted["terminal_id"])
            store.audit(client_ip, "/api/v1/console/assets/assign-link",
                        "hr_unlink", "client=%s" % cid)
            return _json_response(200, {"ok": True, "linked": False,
                                        "ignored": bool(deleted)})
        if not store.get_terminal(tid):
            raise ApiError(400, "terminal not found")
        result = store.hr_map_manual_set(cid, tid)
        if result != "ok":
            raise ApiError(409, "目标终端已被其它手动关联占用，请先解除")
        store.hr_ignore_clear(cid, tid)     # 正向指定即解除否决
        store.audit(client_ip, "/api/v1/console/assets/assign-link",
                    "hr_link", "client=%s terminal=%s" % (cid, tid))
        return _json_response(200, {"ok": True, "linked": True,
                                    "match_type": "manual"})

    if method == "POST" and len(parts) == 5 and sub == "assign-group":
        data = _parse_json_body(body) or {}
        cid = str(data.get("client_id") or "").strip()
        if not cid or not store.hr_client_exists(cid):
            raise ApiError(400, "huorong client not found")
        raw = data.get("group_id")
        if raw in (None, 0, "", "0"):
            store.hr_override_set(cid, None)
            native = store.hr_override_reset_native(cid)
            store.audit(client_ip, "/api/v1/console/assets/assign-group",
                        "hr_group_reset", "client=%s" % cid)
            return _json_response(200, {"ok": True, "group_id": native})
        try:
            gid = int(raw)
        except (TypeError, ValueError):
            raise ApiError(400, "bad group_id")
        if not store.hr_group_exists(gid):
            raise ApiError(400, "huorong group not found")
        store.hr_override_set(cid, gid)
        store.hr_override_apply(cid)
        store.audit(client_ip, "/api/v1/console/assets/assign-group",
                    "hr_group", "client=%s group=%d" % (cid, gid))
        return _json_response(200, {"ok": True, "group_id": gid})

    raise ApiError(404, "not found")


# ----------------------------------------------------------------------
# 桌面管控路由组（/api/v1/console/desktoppolicy/*，ADR-036）
# 壁纸资源库（上传 body=图片原始字节）+ 四类策略 CRUD/发布 + 下发记录。
# POST/DELETE 类 admin-only（403+审计）；契约见 desktop-policy CONTRACT.md v1。
# ----------------------------------------------------------------------

def _console_desktoppolicy(ctx, method, parts, headers, query, body,
                           sess, client_ip):
    dp = getattr(ctx, "dp", None)
    if dp is None:
        raise ApiError(500, "桌面管控模块未初始化")
    actor = sess["username"] if sess is not None else "admin"
    rest = parts[4:]

    def _require_admin(action):
        if auth.require_admin(sess):
            return
        auth.audit(auth.Ev.ACCESS_DENIED, "blocked",
                   username=(sess["username"] if sess is not None else None),
                   user_id=(sess["user_id"] if sess is not None else None),
                   client_ip=client_ip,
                   target="/api/v1/console/desktoppolicy/" + action,
                   reason="admin_required")
        raise ApiError(403, "需要管理员权限")

    def _dp(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except desktop_policy.DpError as e:
            raise ApiError(e.http_status, e.message)

    if method == "GET" and rest == ["overview"]:
        return _json_response(200, dp.overview())

    if method == "GET" and rest == ["wallpapers"]:
        return _json_response(200, _dp(dp.list_wallpapers,
                                       _q_str(query, "category") or None,
                                       max(1, _q_int(query, "page", 1)),
                                       max(1, min(_q_int(query, "page_size",
                                                          50), 200))))

    if method == "PUT" and len(rest) == 2 and rest[0] == "wallpapers":
        # v1.1 裁定：raw bytes 上传（body=图片二进制，不经 JSON 解析）
        _require_admin("wallpapers")
        name = urllib.parse.unquote(rest[1]).strip()
        row = _dp(dp.add_wallpaper, body, name,
                  _q_str(query, "category"), _q_str(query, "mime"), actor)
        store = ctx.store
        store.audit(client_ip, "/api/v1/console/desktoppolicy/wallpapers",
                    "dp_wallpaper_created",
                    "id=%s name=%s bytes=%s" % (row["id"], row["name"],
                                                row["size_bytes"]))
        return _json_response(200, {"ok": True, "wallpaper": row})

    if method == "DELETE" and len(rest) == 2 and rest[0] == "wallpapers":
        _require_admin("wallpapers")
        try:
            wid = int(rest[1])
        except ValueError:
            raise ApiError(400, "bad wallpaper id")
        _dp(dp.delete_wallpaper, wid)
        ctx.store.audit(client_ip,
                        "/api/v1/console/desktoppolicy/wallpapers",
                        "dp_wallpaper_deleted", "id=%d" % wid)
        return _json_response(200, {"ok": True})

    if method == "GET" and rest == ["policies"]:
        return _json_response(200, _dp(dp.list_policies))

    if method == "POST" and rest == ["policies"]:
        _require_admin("policies")
        data = _parse_json_body(body) or {}
        raw_pid = data.get("id")
        pid = None
        if raw_pid not in (None, ""):
            try:
                pid = int(raw_pid)
            except (TypeError, ValueError):
                raise ApiError(400, "bad policy id")
        row = _dp(dp.save_policy, data.get("name"), data.get("group_id"),
                  data.get("payload"), actor, policy_id=pid)
        ctx.store.audit(client_ip, "/api/v1/console/desktoppolicy/policies",
                        "dp_policy_saved",
                        "id=%s name=%s" % (row["id"], row["name"]))
        return _json_response(200, {"ok": True, "policy": row})

    if method == "POST" and len(rest) == 3 and rest[0] == "policies" \
            and rest[2] == "publish":
        _require_admin("policies")
        try:
            pid = int(rest[1])
        except ValueError:
            raise ApiError(400, "bad policy id")
        row = _dp(dp.publish_policy, pid, actor)
        ctx.store.audit(client_ip,
                        "/api/v1/console/desktoppolicy/policies/%d/publish"
                        % pid, "dp_policy_published",
                        "id=%d revision=%d" % (pid, row["revision"]))
        return _json_response(200, {"ok": True, "policy": row})

    if method == "GET" and rest == ["deliveries"]:
        return _json_response(200, _dp(dp.list_deliveries,
                                       _q_str(query, "status") or None,
                                       _q_str(query, "error_code") or None,
                                       _q_str(query, "terminal_id") or None,
                                       max(1, _q_int(query, "page", 1)),
                                       max(1, min(_q_int(query, "page_size",
                                                         50), 200))))

    raise ApiError(404, "not found")


# ----------------------------------------------------------------------
# 自动开关机路由组（/api/v1/console/powercontrol/*，power-control P0）
# 电源策略快照存档查询：按终端查最新 / 历史（时间线）。
# ----------------------------------------------------------------------
def _validate_pc_sched(obj, name):
    """开关机策略单侧校验：enabled 必填布尔；启用时按 mode 补全校验。"""
    if not isinstance(obj, dict):
        raise ApiError(400, "%s 必须为对象" % name)
    if not isinstance(obj.get("enabled"), bool):
        raise ApiError(400, "%s.enabled 必须为布尔" % name)
    if not obj.get("enabled"):
        return
    mode = str(obj.get("mode") or "")
    if mode not in ("daily", "weekly", "single", "disabled"):
        raise ApiError(400, "%s.mode 仅支持 daily/weekly/single/disabled" % name)
    if mode == "disabled":
        raise ApiError(400, "%s 启用时 mode 不可为 disabled" % name)
    if not re.match(r"^\d{2}:\d{2}$", str(obj.get("time") or "")):
        raise ApiError(400, "%s.time 必须为 HH:MM" % name)
    if mode == "weekly":
        wd = obj.get("weekdays")
        if (not isinstance(wd, list) or len(wd) != 7
                or any(x not in (0, 1) for x in wd)):
            raise ApiError(400, "%s.weekdays 必须为 7 位 0/1 数组" % name)
    if mode == "single" and not re.match(r"^\d{4}-\d{2}-\d{2}$",
                                         str(obj.get("date") or "")):
        raise ApiError(400, "%s.date 必须为 YYYY-MM-DD" % name)


def _pc_validate_targets(store, norm):
    """任务目标存在性校验（ADR-047 批 B：按资产源分派）。

    - platform：组须存在；终端须在 terminals 表
    - huorong ：组须存在（平台侧安全分组）；终端须在 hr_clients 镜像且
                有 MAC 与 IP——WoL 硬约束，缺一不可
    - nad     ：仅支持指定终端（nad:<oid>），须在 nad_terminals 镜像且可唤醒；
                组模式未开放（NAD 无稳定组 id）"""
    source = str(norm.get("source") or "platform")
    if norm.get("target_type") == "group":
        if source == "nad":
            raise ApiError(400, "画方准入暂不支持按分组下发，请检索选择终端")
        if not any(int(g["id"]) == int(norm.get("group_id") or 0)
                   for g in store.asset_group_list()):
            raise ApiError(404, "asset group not found")
        return
    bad = []
    for x in [str(y) for y in (norm.get("targets") or [])]:
        if source == "huorong":
            cid = x.split(":", 1)[1] if x.startswith("hr:") else x
            row = store.hr_client_get(cid)
            ok = bool(row) and bool(str(row.get("mac") or "").strip()) \
                and bool(str(row.get("ip") or row.get("connect_ip")
                             or "").strip())
        elif source == "nad":
            oid = x.split(":", 1)[1] if x.startswith("nad:") else x
            row = store.nad_terminal_get(oid)
            ok = bool(row) and bool(row.get("wol_capable"))
        else:
            ok = bool(store.get_terminal(x))
        if not ok:
            bad.append(x)
    if bad:
        raise ApiError(404, "%s 目标不可用（不存在或缺 MAC/网段）：%s"
                            % (source, ",".join(bad[:8])))


def _console_powercontrol(ctx, method, parts, query, sess, body=None,
                          client_ip=None):
    pc = getattr(ctx, "pc", None)
    if pc is None:
        raise ApiError(500, "自动开关机模块未初始化")
    rest = parts[4:]

    # GET /api/v1/console/powercontrol/terminals/{tid}/snapshots?latest=1&limit=&since=
    if method == "GET" and len(rest) == 3 and rest[0] == "terminals" \
            and rest[2] == "snapshots":
        tid = rest[1]
        if not ctx.store.get_terminal(tid):
            raise ApiError(404, "terminal not found")
        if _q_str(query, "latest") in ("1", "true"):
            row = pc.latest_snapshot(tid)
            return _json_response(200, {"ok": True, "snapshot": row})
        rows = pc.history_snapshots(tid, limit=_q_int(query, "limit", 50),
                                    since=_q_int(query, "since", 0))
        return _json_response(200, {"ok": True, "total": len(rows),
                                    "snapshots": rows})

    # GET /api/v1/console/powercontrol/terminals/{tid}/manual-config
    #   — 手动维护登记读取（ADR-040 增补：平台侧台账，不下发终端）
    if method == "GET" and len(rest) == 3 and rest[0] == "terminals" \
            and rest[2] == "manual-config":
        if not ctx.store.get_terminal(rest[1]):
            raise ApiError(404, "terminal not found")
        return _json_response(200, {"ok": True,
                                    "config": pc.manual_get(rest[1])})

    # PUT /api/v1/console/powercontrol/terminals/{tid}/manual-config
    #   — 登记/更新（管理员/操作员均可；备注必填；结构复用下发校验）
    if method == "PUT" and len(rest) == 3 and rest[0] == "terminals" \
            and rest[2] == "manual-config":
        tid = rest[1]
        if not ctx.store.get_terminal(tid):
            raise ApiError(404, "terminal not found")
        data = _parse_json_body(body) or {}
        boot = data.get("boot") or {}
        shutdown = data.get("shutdown") or {}
        note = str(data.get("note") or "").strip()
        if len(note) < 2:
            raise ApiError(400, "备注为必填项（请说明人工维护内容）")
        if len(note) > 500:
            raise ApiError(400, "备注最长 500 字")
        boot = boot if isinstance(boot, dict) and boot.get("enabled") else {}
        shutdown = (shutdown if isinstance(shutdown, dict)
                    and shutdown.get("enabled") else {})
        if not boot and not shutdown:
            raise ApiError(400, "定时开机与定时关机至少需启用一项")
        if boot:
            _validate_pc_sched(boot, "boot")
        if shutdown:
            _validate_pc_sched(shutdown, "shutdown")
        operator = sess["username"] if sess else ""
        cfg = pc.manual_upsert(tid, boot, shutdown, note, operator)
        auth.audit("powercontrol.manual_config", "success",
                   username=operator, client_ip=client_ip, target=tid,
                   detail={"ops": sorted(
                       [k for k, v in (("boot", boot),
                                       ("shutdown", shutdown)) if v])})
        return _json_response(200, {"ok": True, "config": cfg})

    # DELETE /api/v1/console/powercontrol/terminals/{tid}/manual-config
    if method == "DELETE" and len(rest) == 3 and rest[0] == "terminals" \
            and rest[2] == "manual-config":
        if not ctx.store.get_terminal(rest[1]):
            raise ApiError(404, "terminal not found")
        deleted = pc.manual_delete(rest[1])
        if deleted:
            auth.audit("powercontrol.manual_config", "success",
                       username=(sess["username"] if sess else None),
                       client_ip=client_ip, target=rest[1],
                       detail={"op": "delete"})
        return _json_response(200, {"ok": True, "deleted": deleted})

    # GET /api/v1/console/powercontrol/manual-configs?limit=
    #   — 手动维护台账列表（RTC 列手动优先展示的数据源）
    if method == "GET" and rest == ["manual-configs"]:
        return _json_response(200, {
            "ok": True,
            "configs": pc.manual_list(limit=_q_int(query, "limit", 200))})

    # GET /api/v1/console/powercontrol/diags?terminal_id=&limit=
    #   — pc_diag 诊断记录列表（只读诊断联调通道，ADR-040 增补）
    if method == "GET" and rest == ["diags"]:
        return _json_response(200, {
            "ok": True,
            "diags": pc.diag_list(
                terminal_id=_q_str(query, "terminal_id") or None,
                limit=_q_int(query, "limit", 50))})

    # GET /api/v1/console/powercontrol/diags/{id} — 诊断原始 JSON 详情
    if method == "GET" and len(rest) == 2 and rest[0] == "diags" \
            and rest[1].isdigit():
        row = pc.diag_get(int(rest[1]))
        if row is None:
            raise ApiError(404, "diag record not found")
        return _json_response(200, {"ok": True, "diag": row})

    # POST /api/v1/console/powercontrol/diags — 保留占位（见 terminals 组 diag 端点）
    # POST /api/v1/console/powercontrol/policies/dispatch — 批量下发定时开关机
    if method == "POST" and rest == ["policies", "dispatch"]:
        data = _parse_json_body(body) or {}
        tids = data.get("terminal_ids")
        if not isinstance(tids, list) or not tids:
            raise ApiError(400, "terminal_ids 必须为非空数组")
        if len(tids) > 100:
            raise ApiError(400, "单批最多 100 台终端")
        tids = [str(x) for x in tids]
        if len(set(tids)) != len(tids):
            raise ApiError(400, "terminal_ids 存在重复")
        for t in tids:
            if not ctx.store.get_terminal(t):
                raise ApiError(404, "terminal not found: %s" % t)
        boot = data.get("boot")
        shutdown = data.get("shutdown")
        payload = {}
        if boot:
            _validate_pc_sched(boot, "boot")
            payload["boot"] = boot
        if shutdown:
            _validate_pc_sched(shutdown, "shutdown")
            payload["shutdown"] = shutdown
        if not payload:
            raise ApiError(400, "boot 与 shutdown 至少需要一项")
        note = str(data.get("note") or "").strip()
        if len(note) > 500:
            raise ApiError(400, "备注最长 500 字")
        policy_id = secrets.token_hex(16)
        args = {"policy_id": policy_id, "op": "apply"}
        args.update(payload)
        did = pc.create_dispatch(policy_id, payload, tids,
                                 sess["username"] if sess else "",
                                 note=note)
        auth.audit("powercontrol.dispatch", "success",
                   username=(sess["username"] if sess else None),
                   client_ip=client_ip, target=str(did),
                   detail={"policy_id": policy_id,
                           "terminals": len(tids),
                           "ops": sorted(payload.keys())})
        queued_offline = 0
        hb_timeout = int(ctx.config.get("heartbeat_timeout_sec", 180))
        now = int(time.time())
        for t in tids:
            term = ctx.store.get_terminal(t) or {}
            offline = (now - (term["last_seen"] if "last_seen" in term.keys()
                              else 0)) >= hb_timeout
            cid = ctx.store.enqueue_command(
                t, "pc_apply_policy", args, timeout_sec=604800,
                source="powercontrol")
            pc.bind_command(did, t, cid, offline)
            if offline:
                queued_offline += 1
        return _json_response(200, {
            "ok": True, "dispatch_id": did, "policy_id": policy_id,
            "total": len(tids), "queued_offline": queued_offline})

    # GET /api/v1/console/powercontrol/policies/dispatches?limit=
    if method == "GET" and rest == ["policies", "dispatches"]:
        rows = pc.dispatch_list(_q_int(query, "limit", 50))
        return _json_response(200, {"ok": True, "dispatches": rows,
                                    "total": len(rows)})

    # GET /api/v1/console/powercontrol/policies/dispatches/{id}
    if method == "GET" and len(rest) == 3 and rest[:2] == \
            ["policies", "dispatches"] and rest[2].isdigit():
        d = pc.dispatch_get(int(rest[2]), store=ctx.store)
        if not d:
            raise ApiError(404, "dispatch not found")
        return _json_response(200, {"ok": True, "dispatch": d})

    # ------------------------------------------------------------------
    # 4.1.5 批次：power_action 立即重启/关机 + wol_relay 中继 + WoL 定时
    # （ADR-044/045；命令契约与终端 power_action.py 定稿一致，服务端
    #  先行校验双保险——终端侧仍会再拒一次）
    # ------------------------------------------------------------------

    def _pc_admin(action):
        if not auth.require_admin(sess):
            auth.audit(auth.Ev.ACCESS_DENIED, "blocked",
                       username=(sess["username"] if sess is not None
                                 else None),
                       user_id=(sess["user_id"] if sess is not None
                                else None),
                       client_ip=client_ip, target=action,
                       reason="admin_required")
            raise ApiError(403, "需要管理员权限")

    def _pc_online(tid, label):
        term = ctx.store.get_terminal(tid)
        if not term:
            raise ApiError(404, "terminal not found")
        hb = int(ctx.config.get("heartbeat_timeout_sec", 180))
        if (int(time.time()) - int(term["last_seen"] or 0)) >= hb:
            raise ApiError(409, "%s 需终端在线（当前离线）" % label)
        return term

    # POST /api/v1/console/powercontrol/terminals/{tid}/power-action
    #   {action:"shutdown"|"restart", delay_sec?:0-3600 缺省 60, force?:缺省 true}
    if method == "POST" and len(rest) == 3 and rest[0] == "terminals" \
            and rest[2] == "power-action":
        _pc_admin("powercontrol.power_action")
        tid = rest[1]
        _pc_online(tid, "立即开关机")
        data = _parse_json_body(body) or {}
        action = str(data.get("action") or "").strip()
        if action not in ("shutdown", "restart"):
            raise ApiError(400, "action 仅支持 shutdown/restart")
        try:
            delay_sec = int(data.get("delay_sec", 60))
        except (TypeError, ValueError):
            raise ApiError(400, "delay_sec 必须为整数")
        if delay_sec < 0 or delay_sec > 3600:
            raise ApiError(400, "delay_sec 超出范围（0-3600 秒）")
        force = data.get("force", True)
        if not isinstance(force, bool):
            force = str(force).lower() in ("true", "1", "yes")
        cid = ctx.store.enqueue_command(
            tid, "power_action",
            {"action": action, "delay_sec": delay_sec, "force": force},
            timeout_sec=120, source="powercontrol")
        auth.audit("powercontrol.power_action", "success",
                   username=(sess["username"] if sess else None),
                   client_ip=client_ip, target=tid,
                   detail={"command_id": cid, "action": action,
                           "delay_sec": delay_sec, "force": force})
        return _json_response(200, {"ok": True, "command_id": cid,
                                    "action": action,
                                    "delay_sec": delay_sec, "force": force})

    # POST /api/v1/console/powercontrol/terminals/{tid}/power-action/abort
    if method == "POST" and len(rest) == 4 and rest[0] == "terminals" \
            and rest[2] == "power-action" and rest[3] == "abort":
        _pc_admin("powercontrol.power_action_abort")
        tid = rest[1]
        _pc_online(tid, "撤销立即开关机")
        cid = ctx.store.enqueue_command(
            tid, "power_action_abort", {}, timeout_sec=120,
            source="powercontrol")
        auth.audit("powercontrol.power_action_abort", "success",
                   username=(sess["username"] if sess else None),
                   client_ip=client_ip, target=tid,
                   detail={"command_id": cid})
        return _json_response(200, {"ok": True, "command_id": cid})

    # POST /api/v1/console/powercontrol/terminals/{tid}/wol-relay
    #   {mac, broadcast, port?} — 由该终端（须在线）代发目标机魔术包
    if method == "POST" and len(rest) == 3 and rest[0] == "terminals" \
            and rest[2] == "wol-relay":
        _pc_admin("powercontrol.wol_relay")
        relay_tid = rest[1]
        _pc_online(relay_tid, "WoL 中继")
        data = _parse_json_body(body) or {}
        try:
            mac = wol.normalize_mac(data.get("mac"))
            bcast = wol.validate_broadcast(data.get("broadcast"))
            port = int(data.get("port", 9))
            if not (1 <= port <= 65535):
                raise ValueError("端口超出范围")
        except (TypeError, ValueError) as e:
            raise ApiError(400, str(e) or "参数校验失败")
        cid = ctx.store.enqueue_command(
            relay_tid, "wol_relay",
            {"mac": mac, "broadcast": bcast, "port": port},
            timeout_sec=120, source="powercontrol")
        auth.audit("powercontrol.wol_relay", "success",
                   username=(sess["username"] if sess else None),
                   client_ip=client_ip, target=relay_tid,
                   detail={"command_id": cid, "mac": mac,
                           "broadcast": bcast, "port": port})
        return _json_response(200, {"ok": True, "command_id": cid,
                                    "mac": mac, "broadcast": bcast,
                                    "port": port})

    # GET /api/v1/console/powercontrol/wol/relays?terminal_id=T
    #   中继选举（读）：同网段在线终端列表 + 目标机 MAC/广播预填
    if method == "GET" and rest == ["wol", "relays"]:
        tid = _q_str(query, "terminal_id") or ""
        term = ctx.store.get_terminal(tid)
        if not term:
            raise ApiError(404, "terminal not found")
        relays = wol.elect_relays(
            ctx.store, tid,
            hb_timeout=int(ctx.config.get("heartbeat_timeout_sec", 180)))
        asset_ip, mac = _terminal_net_from_asset(ctx.store, tid)
        return _json_response(200, {
            "ok": True, "relays": relays,
            "target": {"terminal_id": tid, "mac": mac,
                       "asset_ip": asset_ip,
                       "broadcasts": wol.target_broadcasts(ctx.store, tid)}})

    # POST /api/v1/console/powercontrol/wol/direct
    #   {terminal_id, mac?, broadcast?, port?} — 服务器直发魔术包（即时）
    if method == "POST" and rest == ["wol", "direct"]:
        _pc_admin("powercontrol.wol_direct")
        data = _parse_json_body(body) or {}
        tid = str(data.get("terminal_id") or "")
        if tid and not ctx.store.get_terminal(tid):
            raise ApiError(404, "terminal not found")
        mac = str(data.get("mac") or "")
        if not mac and tid:
            _, mac = _terminal_net_from_asset(ctx.store, tid)
        try:
            mac = wol.normalize_mac(mac)
        except ValueError as e:
            raise ApiError(400, "MAC %s" % e)
        if data.get("broadcast"):
            try:
                bcasts = [wol.validate_broadcast(data.get("broadcast"))]
            except ValueError as e:
                raise ApiError(400, str(e))
        elif tid:
            bcasts = wol.target_broadcasts(ctx.store, tid)
        else:
            bcasts = []
        if not bcasts:
            raise ApiError(400, "缺少广播地址（目标机无已知网段）")
        sent = wol.direct_send(mac, bcasts,
                               ports=(int(data.get("port") or 9),))
        auth.audit("powercontrol.wol_direct", "success",
                   username=(sess["username"] if sess else None),
                   client_ip=client_ip, target=tid or mac,
                   detail={"sent": sent})
        return _json_response(200, {
            "ok": bool(sent), "mac": mac, "sent": sent,
            "note": ("魔术包已发送（%s）；唤醒结果以目标机上线为准"
                     % ("、".join(sent) if sent else "无可达广播"))})

    # GET /api/v1/console/powercontrol/wol/schedules — 定时唤醒台账+最近尝试
    if method == "GET" and rest == ["wol", "schedules"]:
        return _json_response(200, {
            "ok": True, "schedules": pc.wol_schedule_list(),
            "attempts": pc.wol_attempts_list(limit=30)})

    # POST /api/v1/console/powercontrol/wol/schedules
    #   {terminal_id | targets:[tid,...], time:"HH:MM",
    #    name?, mac?, method?:auto|direct|relay}
    # 批量（targets[]）语义：逐目标独立建行（同一时刻、同一方式），
    # 每行各自走完整唤醒链；部分冲突不整批失败。
    if method == "POST" and rest == ["wol", "schedules"]:
        _pc_admin("powercontrol.wol_schedule")
        data = _parse_json_body(body) or {}
        targets = data.get("targets")
        if targets is None:
            targets = [data.get("terminal_id")]
        if not isinstance(targets, list) or not targets:
            raise ApiError(400, "targets 必须为非空数组（或 terminal_id）")
        tids, seen = [], set()
        for x in targets:
            x = str(x or "").strip()
            if x and x not in seen:
                seen.add(x)
                tids.append(x)
        if not tids:
            raise ApiError(400, "targets 为空")
        missing = [x for x in tids if not ctx.store.get_terminal(x)]
        if missing:
            raise ApiError(404, "terminal not found: %s"
                                 % ",".join(missing[:8]))
        t = str(data.get("time") or "")
        if not re.match(r"^\d{2}:\d{2}$", t):
            raise ApiError(400, "time 必须为 HH:MM")
        method_w = str(data.get("method") or "auto")
        if method_w not in ("auto", "direct", "relay"):
            raise ApiError(400, "method 仅支持 auto/direct/relay")
        name = str(data.get("name") or "").strip()
        if len(name) > 60:
            raise ApiError(400, "名称最长 60 字")
        mac = ""
        if data.get("mac"):
            try:
                mac = wol.normalize_mac(data.get("mac"))
            except ValueError as e:
                raise ApiError(400, str(e))
        operator = sess["username"] if sess else ""
        created, conflicts = [], []
        for x in tids:
            try:
                sid = pc.wol_schedule_create(
                    x, mac, t,
                    name=(name if len(tids) == 1
                          else (name + "·" + x if name else x)),
                    method=method_w, operator=operator)
                created.append({"terminal_id": x, "schedule_id": sid})
            except sqlite3.IntegrityError:
                conflicts.append({"terminal_id": x,
                                  "reason": "该终端同一时间已存在唤醒计划"})
        auth.audit("powercontrol.wol_schedule", "success",
                   username=(sess["username"] if sess else None),
                   client_ip=client_ip,
                   target=",".join(tids[:8]),
                   detail={"created": len(created),
                           "conflicts": len(conflicts), "time": t,
                           "method": method_w})
        if len(tids) == 1:
            if conflicts:
                raise ApiError(409, conflicts[0]["reason"])
            sid = created[0]["schedule_id"]
            return _json_response(200, {"ok": True, "schedule_id": sid,
                                        "schedule": pc.wol_schedule_get(sid)})
        return _json_response(200, {"ok": True, "created": created,
                                    "conflicts": conflicts})

    # PUT /api/v1/console/powercontrol/wol/schedules/{id}
    if method == "PUT" and len(rest) == 3 and rest[:2] == ["wol", "schedules"] \
            and rest[2].isdigit():
        _pc_admin("powercontrol.wol_schedule")
        if not pc.wol_schedule_get(int(rest[2])):
            raise ApiError(404, "schedule not found")
        data = _parse_json_body(body) or {}
        fields = {}
        if "enabled" in data:
            fields["enabled"] = 1 if data.get("enabled") else 0
        for k in ("name", "method", "mac", "time_hhmm"):
            if k in data:
                v = data.get(k)
                if k == "method" and str(v) not in ("auto", "direct", "relay"):
                    raise ApiError(400, "method 仅支持 auto/direct/relay")
                if k == "time_hhmm" and not re.match(r"^\d{2}:\d{2}$",
                                                     str(v or "")):
                    raise ApiError(400, "time 必须为 HH:MM")
                if k == "mac" and v:
                    try:
                        v = wol.normalize_mac(v)
                    except ValueError as e:
                        raise ApiError(400, str(e))
                fields[k] = v
        if not pc.wol_schedule_update(int(rest[2]), fields):
            raise ApiError(400, "无可更新字段")
        auth.audit("powercontrol.wol_schedule", "success",
                   username=(sess["username"] if sess else None),
                   client_ip=client_ip, target=rest[2],
                   detail={"op": "update", "fields": sorted(fields)})
        return _json_response(200, {"ok": True,
                                    "schedule": pc.wol_schedule_get(
                                        int(rest[2]))})

    # DELETE /api/v1/console/powercontrol/wol/schedules/{id}
    if method == "DELETE" and len(rest) == 3 \
            and rest[:2] == ["wol", "schedules"] and rest[2].isdigit():
        _pc_admin("powercontrol.wol_schedule_delete")
        if not pc.wol_schedule_delete(int(rest[2])):
            raise ApiError(404, "schedule not found")
        auth.audit("powercontrol.wol_schedule_delete", "success",
                   username=(sess["username"] if sess else None),
                   client_ip=client_ip, target=rest[2],
                   detail={"op": "delete"})
        return _json_response(200, {"ok": True})

    # ------------------------------------------------------------------
    # 开关机任务（ADR-047）：任务为中心统一模型。读 operator / 写 admin。
    # boot 任务保存时目标展开写回 wol_schedules（task_id 关联，diff 保留
    # 运行态）；shutdown 任务为声明配置模板（下发批次 + 回读比对）。
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # 第三方资产源（ADR-047 批 B）：分组树 + 终端检索（向导第 3 步数据源）
    #   铁律：只呈现「可唤醒」终端（有 MAC 且有 IP），不可唤醒者附原因，
    #   由前端禁选——WoL 无 MAC 组不出魔术包、无 IP 派生不出广播地址。
    # ------------------------------------------------------------------

    # GET /src-groups?source=huorong|nad — 分源分组
    if method == "GET" and rest == ["src-groups"]:
        src = (_q_str(query, "source") or "").strip()
        if src not in ("huorong", "nad"):
            raise ApiError(400, "source 仅支持 huorong/nad")
        if src == "huorong":
            out = []
            for g in ctx.store.asset_group_list():
                gsrc = str(g.get("source") or "")
                if gsrc not in ("huorong", "root"):
                    continue
                hr_gid = g.get("huorong_group_id")
                if gsrc == "root":
                    rows = ctx.store.hr_clients_wol_targets(None)
                elif hr_gid:
                    rows = ctx.store.hr_clients_wol_targets(int(hr_gid))
                else:
                    rows = []          # 未映射火绒组 → 无可选终端
                out.append({"id": int(g["id"]),
                            "name": g.get("name") or "",
                            "is_root": gsrc == "root",
                            "mapped": bool(hr_gid) or gsrc == "root",
                            "total": len(rows),
                            "wol_capable": sum(1 for r in rows
                                               if r.get("wol_capable"))})
            return _json_response(200, {"ok": True, "source": src,
                                        "groups": out})
        rows = ctx.store.nad_groups_summary()
        return _json_response(200, {
            "ok": True, "source": src,
            "groups": [{"path": r.get("group_path") or "",
                        "name": r.get("group_path") or "（未分类）",
                        "total": r.get("total") or 0,
                        "wol_capable": r.get("wol_capable") or 0}
                       for r in rows],
            "sync_ts": ctx.store.nad_sync_ts()})

    # GET /src-terminals?source=&q=&group=&page=&page_size=
    #   分源终端列表（可唤醒者可选，其余附原因禁选）
    if method == "GET" and rest == ["src-terminals"]:
        src = (_q_str(query, "source") or "").strip()
        if src not in ("huorong", "nad"):
            raise ApiError(400, "source 仅支持 huorong/nad")
        q = (_q_str(query, "q") or "").strip().lower()
        group = (_q_str(query, "group") or "").strip()
        page = max(1, _q_int(query, "page", 1))
        page_size = max(1, min(_q_int(query, "page_size", 50), 200))

        def _shape(tid, name, gname, mac, ips, online, capable):
            return {"id": tid, "name": name or "", "group": gname or "",
                    "mac": mac or "", "ips": [x for x in ips if x],
                    "online": bool(online),
                    "wol_capable": bool(capable),
                    "reason": "" if capable
                              else "缺 MAC 或网段信息，无法唤醒"}

        if src == "huorong":
            hr_gid, only_root = None, not group
            if group:
                for g in ctx.store.asset_group_list():
                    if str(g["id"]) == group:
                        if str(g.get("source") or "") == "root":
                            only_root = True
                        else:
                            hr_gid = g.get("huorong_group_id")
                        break
            rows = ctx.store.hr_clients_wol_targets(
                None if only_root else (int(hr_gid) if hr_gid else -1))
            if q:
                rows = [r for r in rows if q in (
                    (str(r.get("name") or "") + " "
                     + str(r.get("computer_name") or "") + " "
                     + str(r.get("ip") or "") + " "
                     + str(r.get("mac") or "")).lower())]
            total = len(rows)
            start = (page - 1) * page_size
            items = [_shape("hr:" + str(r["client_id"]),
                            r.get("computer_name") or r.get("name"),
                            r.get("group_name"), r.get("mac"),
                            [str(r.get("ip") or "").strip(),
                             str(r.get("connect_ip") or "").strip()],
                            r.get("online"), r.get("wol_capable"))
                     for r in rows[start:start + page_size]]
            return _json_response(200, {"ok": True, "source": src,
                                        "total": total, "page": page,
                                        "page_size": page_size,
                                        "items": items, "sync_ts": 0})
        res = ctx.store.nad_terminals_page(
            q=q or None, group_path=group or None, only_wol_capable=False,
            page=page, page_size=page_size)
        items = [_shape("nad:" + str(r["oid"]), r.get("name"),
                        r.get("group_path"), r.get("mac"), r.get("ips") or [],
                        r.get("online"), r.get("wol_capable"))
                 for r in res["items"]]
        return _json_response(200, {"ok": True, "source": src,
                                    "total": res["total"], "page": page,
                                    "page_size": page_size, "items": items,
                                    "sync_ts": ctx.store.nad_sync_ts()})

    # GET /tasks?kind=&origin=&q= — 任务列表（附目标摘要；q 匹配名称或
    # 目标终端 tid/IP/主机名/MAC——个性化任务检索口径）
    if method == "GET" and rest == ["tasks"]:
        kind = _q_str(query, "kind")
        origin = _q_str(query, "origin")
        q = (_q_str(query, "q") or "").strip().lower()
        tasks = pc.task_list(
            kind=kind if kind in ("boot", "shutdown") else None)
        groups = {}
        try:
            for g in ctx.store.asset_group_list():
                groups[int(g["id"])] = {"name": g["name"],
                                        "source": g["source"]}
        except Exception:
            groups = {}
        gcount = {}
        term_map = {}
        try:
            for r in ctx.store.list_terminals():
                term_map[r["terminal_id"]] = r
                gid = r.get("group_id")
                if gid is not None:
                    gcount[int(gid)] = gcount.get(int(gid), 0) + 1
        except Exception:
            term_map = {}
        out = []
        for t in tasks:
            if origin and str(t.get("origin") or "platform") != origin:
                continue
            d = dict(t)
            if d.get("target_type") == "group":
                g = groups.get(int(d.get("group_id") or 0)) or {}
                d["target_summary"] = "%s · %d 台" % (
                    g.get("name") or ("组#%s" % d.get("group_id")),
                    gcount.get(int(d.get("group_id") or 0), 0))
                if q and q not in str(d.get("name") or "").lower():
                    continue
            else:
                d["target_summary"] = "指定终端 %d 台" % len(
                    d.get("targets") or [])
                if q:
                    hit = q in str(d.get("name") or "").lower()
                    if not hit:
                        for x in (d.get("targets") or []):
                            tr = term_map.get(x)
                            macs = ""
                            if tr:
                                try:
                                    a = ctx.store.get_terminal_asset(x) \
                                        or {}
                                    macs = " ".join(
                                        str(n.get("mac") or "").lower()
                                        for n in (a.get("network") or [])
                                        if isinstance(n, dict))
                                except Exception:
                                    macs = ""
                            if (q in x.lower()
                                    or (tr and q in str(
                                        tr.get("ip") or "").lower())
                                    or (tr and q in str(
                                        tr.get("hostname") or "").lower())
                                    or (macs and q in macs)):
                                hit = True
                                break
                    if not hit:
                        continue
            out.append(d)
        return _json_response(200, {"ok": True, "tasks": out})

    # POST /tasks — 创建任务（boot: 校验后展开写回 wol_schedules）
    if method == "POST" and rest == ["tasks"]:
        _pc_admin("powercontrol.task")
        data = _parse_json_body(body) or {}
        try:
            norm = power_control.validate_task_payload(data)
        except power_control.PowerControlError as e:
            raise ApiError(400, str(e))
        norm["origin"] = "platform"   # 控制台仅建中心任务（个性化走终端口）
        _pc_validate_targets(ctx.store, norm)
        name = norm["name"] or pc.task_gen_name(norm["kind"],
                                                norm["time_hhmm"])
        fields = dict(norm)
        fields["name"] = name
        fields["operator"] = sess["username"] if sess else ""
        try:
            task = pc.task_create(fields)
        except sqlite3.IntegrityError:
            raise ApiError(409, "同类型下同名任务已存在")
        expand_info, warnings = None, []
        if norm["kind"] == "boot":
            expand_info = pc.wol_expand_for_task(task, ctx.store)
            warnings = ["%s（%s）" % (c["reason"], c["terminal_id"])
                        for c in expand_info.get("conflicts") or []]
        auth.audit("powercontrol.task", "success",
                   username=(sess["username"] if sess else None),
                   client_ip=client_ip, target=str(task["id"]),
                   detail={"op": "create", "kind": norm["kind"],
                           "repeat": norm["repeat"],
                           "time": norm["time_hhmm"],
                           "targets": len(norm["targets"])
                           if norm["target_type"] == "terminals" else "group"})
        resp = {"ok": True, "task": pc.task_get(task["id"])}
        if expand_info is not None:
            resp["expand"] = expand_info
        if warnings:
            resp["warnings"] = warnings
        return _json_response(200, resp)

    # GET /tasks/{id} — 详情（boot: 调度行+最近尝试；shutdown: runs）
    if method == "GET" and len(rest) == 2 and rest[0] == "tasks" \
            and rest[1].isdigit():
        t = pc.task_get(int(rest[1]))
        if not t:
            raise ApiError(404, "task not found")
        d = dict(t)
        if d["kind"] == "boot":
            rows = [r for r in pc.wol_schedule_list()
                    if r.get("task_id") == d["id"]]
            for r in rows:
                r.pop("relay_queue", None)
                r.pop("relay_tried", None)
            d["schedules"] = rows
            attempts = []
            for r in rows[:20]:
                attempts.extend(pc.wol_attempts_list(
                    schedule_id=r["id"], limit=8))
            attempts.sort(key=lambda a: -int(a.get("id") or 0))
            d["attempts"] = attempts[:30]
        else:
            # 关机任务=声明配置模板（本地执行）；比对走 /drift 端点
            d["declared"] = power_control.task_shutdown_config(d)
        return _json_response(200, {"ok": True, "task": d})

    # PUT /tasks/{id} — 编辑（增量合并校验；boot 重展开 diff 保留运行态）
    if method == "PUT" and len(rest) == 2 and rest[0] == "tasks" \
            and rest[1].isdigit():
        _pc_admin("powercontrol.task")
        t = pc.task_get(int(rest[1]))
        if not t:
            raise ApiError(404, "task not found")
        data = _parse_json_body(body) or {}
        try:
            norm = power_control.validate_task_payload(data, existing=t)
        except power_control.PowerControlError as e:
            raise ApiError(400, str(e))
        norm["origin"] = str(t.get("origin") or "platform")  # origin 不可漂移
        _pc_validate_targets(ctx.store, norm)
        fields = {k: v for k, v in norm.items() if k != "targets"}
        if not t.get("name") and not norm["name"]:
            fields["name"] = pc.task_gen_name(norm["kind"],
                                              norm["time_hhmm"])
        pc.task_update(t["id"], fields)
        pc.task_set_targets(t["id"], norm["target_type"],
                            norm["group_id"], norm["targets"])
        expand_info = None
        if t["kind"] == "boot":
            task = pc.task_get(t["id"])
            expand_info = pc.wol_expand_for_task(task, ctx.store)
            pc.task_set_enabled(t["id"], bool(task.get("enabled")))
        auth.audit("powercontrol.task", "success",
                   username=(sess["username"] if sess else None),
                   client_ip=client_ip, target=str(t["id"]),
                   detail={"op": "update",
                           "fields": sorted(
                               set(fields) | {"targets"})})
        resp = {"ok": True, "task": pc.task_get(t["id"])}
        if expand_info is not None:
            resp["expand"] = expand_info
        return _json_response(200, resp)

    # DELETE /tasks/{id} — 删除任务（级联清调度行；runs 保留为历史）
    if method == "DELETE" and len(rest) == 2 and rest[0] == "tasks" \
            and rest[1].isdigit():
        _pc_admin("powercontrol.task_delete")
        if not pc.task_delete(int(rest[1])):
            raise ApiError(404, "task not found")
        auth.audit("powercontrol.task_delete", "success",
                   username=(sess["username"] if sess else None),
                   client_ip=client_ip, target=rest[1],
                   detail={"op": "delete"})
        return _json_response(200, {"ok": True})

    # GET /tasks/{id}/drift — 关机任务声明 vs 各目标上报回读态比对
    if method == "GET" and len(rest) == 3 and rest[0] == "tasks" \
            and rest[1].isdigit() and rest[2] == "drift":
        t = pc.task_get(int(rest[1]))
        if not t:
            raise ApiError(404, "task not found")
        if t["kind"] != "shutdown":
            raise ApiError(400, "仅关机任务支持回读比对")
        return _json_response(200, {
            "ok": True,
            "drift": pc.shutdown_drift_for_task(t, ctx.store)})

    # POST /tasks/{id}/dispatch — 关机任务声明下发（展开目标 →
    # 复用 pc_apply_policy 批次链：在线待拉取 / 离线排队上线补投）
    if method == "POST" and len(rest) == 3 and rest[0] == "tasks" \
            and rest[1].isdigit() and rest[2] == "dispatch":
        _pc_admin("powercontrol.task_dispatch")
        t = pc.task_get(int(rest[1]))
        if not t:
            raise ApiError(404, "task not found")
        if t["kind"] != "shutdown":
            raise ApiError(400, "仅关机任务支持配置下发（开机任务由"
                                "平台调度自动执行，无需下发）")
        tids, note = pc.expand_platform_targets(ctx.store, t)
        if not tids:
            raise ApiError(409, "目标为空，无可下发终端")
        if len(tids) > 100:
            raise ApiError(400, "展开目标 %d 台超出单批上限 100 台"
                                % len(tids))
        shutdown_cfg = power_control.task_shutdown_config(t)
        _validate_pc_sched(shutdown_cfg, "shutdown")
        payload = {"shutdown": shutdown_cfg}
        policy_id = secrets.token_hex(16)
        args = {"policy_id": policy_id, "op": "apply"}
        args.update(payload)
        did = pc.create_dispatch(policy_id, payload, tids,
                                 sess["username"] if sess else "",
                                 note="任务 #%s「%s」下发（%s）"
                                      % (t["id"], t.get("name") or "",
                                         note))
        auth.audit("powercontrol.task_dispatch", "success",
                   username=(sess["username"] if sess else None),
                   client_ip=client_ip, target=str(t["id"]),
                   detail={"op": "dispatch", "dispatch_id": did,
                           "terminals": len(tids),
                           "expand_note": note})
        queued_offline = 0
        hb_timeout = int(ctx.config.get("heartbeat_timeout_sec", 180))
        now = int(time.time())
        for x in tids:
            term = ctx.store.get_terminal(x) or {}
            offline = (now - (term["last_seen"] if "last_seen"
                              in term.keys() else 0)) >= hb_timeout
            cid = ctx.store.enqueue_command(
                x, "pc_apply_policy", args, timeout_sec=604800,
                source="powercontrol")
            pc.bind_command(did, x, cid, offline)
            if offline:
                queued_offline += 1
        return _json_response(200, {
            "ok": True, "dispatch_id": did, "policy_id": policy_id,
            "total": len(tids), "queued_offline": queued_offline,
            "expand_note": note})

    # （ADR-047 架构修正：runs/pc_sched 中心调度方案作废，runs 表与
    #   GET /runs/{id} 路由已撤——关机任务执行历史=声明下发批次
    #   （pc_policy_dispatch/targets）+ 终端回读比对 drift，无 runs 呈现）

    # GET /holidays?year= — 节假日日历（读）
    if method == "GET" and rest == ["holidays"]:
        y = _q_str(query, "year")
        rows = pc.holiday_list(int(y) if y and y.isdigit() else None)
        return _json_response(200, {"ok": True, "holidays": rows})

    # POST /holidays — 单条登记/更新 {date, type, name?}（admin）
    if method == "POST" and rest == ["holidays"]:
        _pc_admin("powercontrol.holiday")
        data = _parse_json_body(body) or {}
        d = str(data.get("date") or "")
        t = str(data.get("type") or "")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
            raise ApiError(400, "date 必须为 YYYY-MM-DD")
        if t not in ("holiday", "workday"):
            raise ApiError(400, "type 仅支持 holiday（休）/workday（调休上班）")
        pc.holiday_upsert(d, t, str(data.get("name") or "")[:60])
        auth.audit("powercontrol.holiday", "success",
                   username=(sess["username"] if sess else None),
                   client_ip=client_ip, target=d,
                   detail={"op": "upsert", "type": t})
        return _json_response(200, {"ok": True})

    # POST /holidays/import — 年度批量导入 {items:[{date,type,name}]}（admin）
    if method == "POST" and rest == ["holidays", "import"]:
        _pc_admin("powercontrol.holiday")
        data = _parse_json_body(body) or {}
        items = data.get("items")
        if not isinstance(items, list) or not items:
            raise ApiError(400, "items 必须为非空数组")
        if len(items) > 500:
            raise ApiError(400, "单次导入上限 500 条")
        res = pc.holiday_import(items)
        auth.audit("powercontrol.holiday", "success",
                   username=(sess["username"] if sess else None),
                   client_ip=client_ip, target="import",
                   detail={"imported": res["imported"],
                           "invalid": res["invalid"]})
        return _json_response(200, dict(ok=True, **res))

    # DELETE /holidays/{date} — 删除（admin）
    if method == "DELETE" and len(rest) == 2 and rest[0] == "holidays":
        _pc_admin("powercontrol.holiday_delete")
        if not pc.holiday_delete(rest[1]):
            raise ApiError(404, "holiday not found")
        auth.audit("powercontrol.holiday_delete", "success",
                   username=(sess["username"] if sess else None),
                   client_ip=client_ip, target=rest[1],
                   detail={"op": "delete"})
        return _json_response(200, {"ok": True})

    # GET /holiday-status — 当年日历覆盖状态（缺失年如实提示）
    if method == "GET" and rest == ["holiday-status"]:
        return _json_response(200, {"ok": True,
                                    "status": pc.holiday_status()})

    # GET /daily-summary — 终端「每日开机 / 每日关机」列数据源
    #   开机=命中该终端的启用中 daily 任务（组展开与执行引擎同源）；
    #   关机=终端上报的本地实际配置（真实态，非中心声明）
    if method == "GET" and rest == ["daily-summary"]:
        out = {}
        now = int(time.time())
        stale_sec = power_control.SHUTDOWN_STALE_SEC
        rep_map = {r["terminal_id"]: r for r in pc.shutdown_config_list()}
        boot_hits = {}
        for t in pc.task_list(kind="boot"):
            if not t.get("enabled") or str(t.get("repeat")) != "daily":
                continue
            tids, _n = pc.expand_platform_targets(ctx.store, t)
            for x in tids:
                boot_hits.setdefault(x, []).append(str(t.get("time_hhmm")
                                                       or ""))
        try:
            rows = ctx.store.list_terminals()
        except Exception:
            rows = []
        for r in rows:
            tid = r["terminal_id"]
            entry = {}
            times = sorted(boot_hits.get(tid) or [])
            if times:
                entry["boot"] = {"time": times[0], "total": len(times)}
            rep = rep_map.get(tid)
            if rep:
                cfg = rep.get("config")
                if isinstance(cfg, dict) and cfg.get("enabled"):
                    entry["shutdown"] = {
                        "time": str(cfg.get("time") or ""),
                        "mode": str(cfg.get("mode") or ""),
                        "stale": (now - int(rep.get("reported_ts") or 0))
                                 > stale_sec}
            if entry:
                out[tid] = entry
        return _json_response(200, {"ok": True, "map": out})

    # GET /term-search?q= — 终端检索（tid/IP/主机名/MAC；向导选择器）
    if method == "GET" and rest == ["term-search"]:
        q = (_q_str(query, "q") or "").strip().lower()
        out = []
        try:
            rows = ctx.store.list_terminals()
        except Exception:
            rows = []
        hb_timeout = int(ctx.config.get("heartbeat_timeout_sec", 180))
        now = int(time.time())
        for r in rows[:500]:
            tid = r["terminal_id"]
            hay = [tid.lower(), str(r.get("hostname") or "").lower(),
                   str(r.get("ip") or "").lower()]
            if q and q not in " ".join(hay):
                try:
                    a = ctx.store.get_terminal_asset(tid) or {}
                    macs = " ".join(str(n.get("mac") or "").lower()
                                    for n in (a.get("network") or [])
                                    if isinstance(n, dict))
                except Exception:
                    macs = ""
                if q not in macs:
                    continue
            ls = int(r.get("last_seen") or 0)
            out.append({"terminal_id": tid,
                        "hostname": r.get("hostname"),
                        "ip": r.get("ip"),
                        "online": bool(ls and (now - ls) < hb_timeout)})
            if len(out) >= 60:
                break
        return _json_response(200, {"ok": True, "terminals": out})

    # GET /terminal-power-config?terminal_id= — 终端详情弹窗数据源
    #   开机配置=命中中心任务（同执行引擎解析）；关机配置=终端上报实际配置
    if method == "GET" and rest == ["terminal-power-config"]:
        tid = _q_str(query, "terminal_id") or ""
        if not ctx.store.get_terminal(tid):
            raise ApiError(404, "terminal not found")
        hits = pc.boot_tasks_for_terminal(ctx.store, tid)
        boot = [{k: h.get(k) for k in
                 ("id", "name", "repeat", "weekdays", "once_date",
                  "time_hhmm", "source", "origin", "method",
                  "next_ts", "next_trigger")}
                for h in hits]
        rep = pc.shutdown_config_get(tid)
        shutdown = None
        if rep:
            shutdown = {"config": rep.get("config") or {},
                        "version": rep.get("version") or "",
                        "reported_ts": rep.get("reported_ts"),
                        "stale": (int(time.time())
                                  - int(rep.get("reported_ts") or 0))
                                 > power_control.SHUTDOWN_STALE_SEC}
        return _json_response(200, {"ok": True, "boot": boot,
                                    "shutdown": shutdown})

    raise ApiError(404, "not found")


# ----------------------------------------------------------------------
# 客户端版本发布路由组（/api/v1/console/client/*，ADR-042）
# 安装包上传 / 版本列表 / 设置 current（回滚=指回旧版）/ 定制名生成+下载票据 /
# 更新推送（2026-09-19 增补：全量或勾选终端批量下发 client_update）。
# 整组 admin-only（定制名含终端 token、推送属管控操作）+ 审计留痕。
# 下载实体读取走 GET /download/client/setup（公开通用包 / 票据定制包）。
# ----------------------------------------------------------------------
_UPDATE_PUSH_TIMEOUT_SEC = 7 * 86400   # 更新推送命令的排队有效期（7 天）。
                                       # 离线终端同样入队，靠 take_pending_commands
                                       # 在上线后自然领取；若沿用默认 120s，
                                       # store.expire_commands 会在终端上线前
                                       # 就把它判为 timeout，指令凭空消失。


def _console_client(ctx, method, parts, query, headers, body,
                    sess=None, client_ip=None):
    cr = getattr(ctx, "cr", None)
    if cr is None:
        raise ApiError(500, "客户端发布模块未初始化")
    store = ctx.store

    def _require_admin(action):
        if not auth.require_admin(sess):
            auth.audit(auth.Ev.ACCESS_DENIED, "blocked",
                       username=(sess["username"] if sess is not None
                                 else None),
                       user_id=(sess["user_id"] if sess is not None
                                else None),
                       client_ip=client_ip,
                       target="/api/v1/console/client/" + action,
                       reason="admin_required")
            raise ApiError(403, "需要管理员权限")

    rest = parts[4:]

    # PUT /api/v1/console/client/releases/{version}?filename=&note=&platform=
    # body = 安装包原始字节（同平台同版本覆盖更新；sha256/size 落库）
    if method == "PUT" and len(rest) == 2 and rest[0] == "releases":
        _require_admin("releases/upload")
        version = urllib.parse.unquote(rest[1]).strip()
        filename = _q_str(query, "filename")
        note = _q_str(query, "note")
        platform = (_q_str(query, "platform") or "windows").strip().lower()
        try:
            row = cr.upload(platform, version, filename, body or b"", note)
        except client_release.ClientReleaseError as e:
            raise ApiError(e.http_status, e.message)
        store.audit(client_ip, "/api/v1/console/client/releases",
                    "cr_release_uploaded",
                    "platform=%s version=%s filename=%s size=%s" % (
                        platform, row["version"], row["filename"],
                        row["size"]))
        return _json_response(200, {"ok": True, "release": row})

    # GET /api/v1/console/client/releases?platform= — 版本列表
    #   （含 current/回滚标记；platform 省略 = 全部平台总览）
    if method == "GET" and rest == ["releases"]:
        _require_admin("releases/list")
        pf = (_q_str(query, "platform") or "").strip().lower() or None
        if pf is not None and pf not in client_release.PLATFORMS:
            raise ApiError(400, "platform 仅支持 %s"
                                % " / ".join(client_release.PLATFORMS))
        return _json_response(200, {"ok": True, "releases": cr.list(pf),
                                    "platforms": list(
                                        client_release.PLATFORMS)})

    # POST /api/v1/console/client/releases/{version}/set-current
    if method == "POST" and len(rest) == 3 and rest[0] == "releases" \
            and rest[2] == "set-current":
        _require_admin("releases/set-current")
        try:
            rid = int(urllib.parse.unquote(rest[1]))
        except ValueError:
            raise ApiError(400, "bad release id")
        try:
            row = cr.set_current(rid)
        except client_release.ClientReleaseError as e:
            raise ApiError(e.http_status, e.message)
        store.audit(client_ip,
                    "/api/v1/console/client/releases/%d/set-current" % rid,
                    "cr_release_published",
                    "version=%s rollback=%s" % (
                        row["version"], row["rollback_flag"]))
        return _json_response(200, {"ok": True, "release": row})

    # POST /api/v1/console/client/custom-name
    # body {"server": 平台地址, "token": 可空=当前 config 终端 token}
    # → 定制文件名（协议 EyeTerm_Setup_x64_{ver}_{cfg64}_md58.exe）
    #   + 一次性下载票据 URL（10 分钟有效，审计不记 token）
    if method == "POST" and rest == ["custom-name"]:
        _require_admin("custom-name")
        data = _parse_json_body(body) or {}
        server = str(data.get("server") or "").strip()
        token = str(data.get("token") or "").strip()
        if not server.lower().startswith(("http://", "https://")):
            raise ApiError(400, "平台地址必须以 http:// 或 https:// 开头")
        if not token:
            token = str(ctx.config.get("terminal_token") or "")
        if not token:
            raise ApiError(400, "终端 Token 未配置")
        cur = cr.get_current()
        if cur is None:
            raise ApiError(400, "尚未发布任何客户端版本，请先上传并发布")
        filename = client_release.build_custom_filename(
            server, token, cur["version"])
        ticket = cr.issue_ticket(cur["id"], filename)
        cfg64 = client_release.custom_cfg64(filename)
        store.audit(client_ip, "/api/v1/console/client/custom-name",
                    "cr_custom_name_issued",
                    "version=%s server=%s" % (cur["version"], server))
        return _json_response(200, {
            "ok": True,
            "version": cur["version"],
            "filename": filename,
            "download_url": "/download/client/setup?ticket=" + ticket,
            "expires_in": 600,
            # 长期分发链接（2026-09-19 增补）：无票据、无有效期，可直接发给
            # 多台终端用户（cfg64 段本身即凭证）。原 ticket 链接 10 分钟一次性，
            # 只够「立即下载」一次，无法批量分发——这是用户实况痛点。
            "long_term_url": "/download/client/setup?cfg64=" + cfg64})

    # POST /api/v1/console/client/push
    # body {"targets": "all" | ["tid",...], "mode": "notify"|"silent",
    #       "note": 可空}
    # → 向目标终端下发 client_update 命令（在心跳拍领取），并登记推送批次。
    #   mode=notify：终端仅下载就绪，仍需本机用户点「立即更新」（默认/安全）；
    #   mode=silent：终端下载就绪后自动静默安装并重启客户端（无人值守）。
    # 离线终端同样入队（7 天有效），上线后自动补执行，响应回 online/offline 计数
    # 供控制台提示「M 台离线，将在其上线后自动执行」。
    if method == "POST" and rest == ["push"]:
        _require_admin("push")
        data = _parse_json_body(body) or {}
        mode = str(data.get("mode") or "notify").strip().lower()
        if mode not in ("notify", "silent"):
            raise ApiError(400, "mode 仅支持 notify（仅提醒）或 silent（静默安装）")
        note = str(data.get("note") or "").strip()[:200]
        raw = data.get("targets")
        if isinstance(raw, str):
            if raw.strip().lower() not in ("all", "*", ""):
                raise ApiError(400, "targets 为字符串时仅支持 \"all\"")
            tids = [t["terminal_id"] for t in store.list_terminals()]
        elif isinstance(raw, list):
            tids = []
            for item in raw:
                tid = str(item or "").strip()
                if tid and tid not in tids:
                    tids.append(tid)
        else:
            raise ApiError(400, "targets 必须为 \"all\" 或终端 ID 数组")
        if not tids:
            raise ApiError(400, "没有匹配的终端")
        missing = [t for t in tids if not store.get_terminal(t)]
        if missing:
            raise ApiError(404, "终端不存在：%s%s"
                                % (", ".join(missing[:5]),
                                   " 等 %d 台" % len(missing)
                                   if len(missing) > 5 else ""))
        cur = cr.get_current()
        if cur is None:
            raise ApiError(400, "尚未发布任何客户端版本，请先上传并发布")
        operator = sess["username"] if sess is not None else ""
        batch_id = cr.push_create(mode, cur["version"], tids,
                                  operator=operator, note=note)
        source = cr.push_source(batch_id)
        now = int(time.time())
        hb_timeout = int(ctx.config.get("heartbeat_timeout_sec", 180))
        online = 0
        for tid in tids:
            store.enqueue_command(tid, "client_update",
                                  {"mode": mode, "version": cur["version"],
                                   "batch_id": batch_id},
                                  timeout_sec=_UPDATE_PUSH_TIMEOUT_SEC,
                                  source=source, now=now)
            term = store.get_terminal(tid)
            last_seen = (term["last_seen"] or 0) if term is not None else 0
            if now - last_seen < hb_timeout:
                online += 1
        store.audit(client_ip, "/api/v1/console/client/push",
                    "cr_update_pushed",
                    "batch=%d mode=%s version=%s total=%d online=%d by=%s"
                    % (batch_id, mode, cur["version"], len(tids), online,
                       operator))
        return _json_response(200, {
            "ok": True, "batch_id": batch_id, "mode": mode,
            "version": cur["version"], "total": len(tids),
            "online": online, "offline": len(tids) - online})

    # GET /api/v1/console/client/push/batches?limit= — 推送批次列表（附状态计数）
    if method == "GET" and rest == ["push", "batches"]:
        _require_admin("push/batches")
        return _json_response(200, {
            "ok": True,
            "batches": cr.list_batches(_q_int(query, "limit", 20))})

    # GET /api/v1/console/client/push/batches/{id} — 批次明细（逐终端执行状态）
    if method == "GET" and len(rest) == 3 and rest[0] == "push" \
            and rest[1] == "batches":
        _require_admin("push/batches/detail")
        try:
            bid = int(rest[2])
        except ValueError:
            raise ApiError(400, "bad batch id")
        batch = cr.batch_get(bid)
        if batch is None:
            raise ApiError(404, "推送批次不存在")
        batch["items"] = cr.batch_detail(bid)
        return _json_response(200, {"ok": True, "batch": batch})

    # DELETE /api/v1/console/client/push/batches/{id} — 删除批次台账
    # （仅清理记录，不撤回已下发命令；命令状态机由 commands 表独立持有）
    if method == "DELETE" and len(rest) == 3 and rest[0] == "push" \
            and rest[1] == "batches":
        _require_admin("push/batches/delete")
        try:
            bid = int(rest[2])
        except ValueError:
            raise ApiError(400, "bad batch id")
        if not cr.push_delete(bid):
            raise ApiError(404, "推送批次不存在")
        store.audit(client_ip, "/api/v1/console/client/push/batches/%d" % bid,
                    "cr_push_batch_deleted", "batch=%d" % bid)
        return _json_response(200, {"ok": True})

    raise ApiError(404, "not found")


# ----------------------------------------------------------------------
# 第三方数据源信息（资产右键弹窗聚合，ADR-043）
# 火绒块=store.hr_context_block（ADR-033 增补二，huorong 模块所有权）；
# 准入块=画方实时证据（60s 缓存，ADR-024 模式，不建持久绑定表）。
# IP 在线日志/终端隔离依赖画方联动接口（规格待接入），先交付降级骨架：
# 参数校验/密码重校验/审计真实生效；online-log 过渡透出平台通信时间线。
# ----------------------------------------------------------------------
_ONLINE_LOG_PENDING = ("画方「IP 在线日志」接口尚未接入（规格索取中）；"
                       "准入层入网时段记录在画方平台，平台侧暂不掌握。"
                       "下方平台通信时间线仅供终端活跃度参考。")
_TP_TIMELINE_DAYS = 7
def _terminal_net_from_asset(store, tid):
    """从资产明细 network[] 提取终端自报网卡 IP/MAC（ADR-043 IP 口径修正）。

    终端表 ip 列 = HTTP 连接源 IP（winhelper register 不自报 ip，服务端
    兜底 client_ip）——NAT/代理场景下非终端本机地址；画方准入登记的是
    终端本机网卡，查询键必须用自报值。多网卡取首个含 IPv4 的网卡
    （与 hr 绑定 MAC 匹配同源提取模式）。"""
    try:
        asset = store.get_terminal_asset(tid) or {}
        for nic in (asset.get("network") or []):
            if not isinstance(nic, dict):
                continue
            mac = str(nic.get("mac") or "")
            ip = str(nic.get("ip") or "")
            if ip or mac:
                return ip, mac
    except Exception:
        pass
    return "", ""


def _thirdparty_nad_block(store, tid, term_row):
    """画方准入块：自报 IP 优先实时查询、MAC 回退、连接源兜底
    （60s 缓存，ADR-024）。响应透出 asset_ip/source_ip 供前端区分展示。"""
    asset_ip, mac = _terminal_net_from_asset(store, tid)
    source_ip = (term_row["ip"] if term_row is not None else "") or ""
    hits = None
    reason = None
    if asset_ip:
        hits, reason = nad_client.nad_find_by_ip(store, asset_ip, full=True)
    if hits is None and mac:
        hits, reason = nad_client.nad_find_by_mac(store, mac, full=True)
    if hits is None and source_ip and source_ip != asset_ip:
        # 兜底：终端 asset 缺失/无 IP 时退回连接源 IP（NAT 下可能查错对象）
        hits, reason = nad_client.nad_find_by_ip(store, source_ip, full=True)
    query = {"ip": asset_ip or source_ip, "mac": mac,
             "asset_ip": asset_ip, "source_ip": source_ip}
    if hits is None:
        return {"available": False, "reason": reason or "error",
                "matched": False, "evidence": [],
                "fetched_at": (int(nad_client.nad_cache_ts()) or None),
                "query": query}
    return {
        "available": True,
        "reason": None,
        "matched": bool(hits),
        "evidence": hits,          # 多网卡多命中全透出，前端主渲染第一条
        "fetched_at": (int(nad_client.nad_cache_ts()) or None),
        "query": query,
    }


def _console_thirdparty(ctx, method, parts, query, headers, body,
                        sess=None, client_ip=None):
    store = ctx.store
    # 隔离终端列表（画方 block 状态源待接入，先降级骨架）
    if (method == "GET" and len(parts) == 5
            and parts[4] == "isolated"):
        return _json_response(200, {
            "ok": True, "available": False, "terminals": [],
            "reason": "画方阻断状态源待接入（终端隔离 ADR-043 骨架）"})

    if len(parts) < 5:
        raise ApiError(404, "not found")
    tid = parts[4]

    # 单终端聚合（第三方数据源信息弹窗，一次请求两区块）
    if method == "GET" and len(parts) == 5:
        t = store.get_terminal(tid)
        if t is None:
            raise ApiError(404, "终端不存在")
        try:
            hr = store.hr_context_block(tid)      # huorong 模块所有权（ADR-033 增补二）
        except Exception:
            hr = {"linked": False, "error": "火绒镜像读取失败"}
        try:
            nad = _thirdparty_nad_block(store, tid, t)
        except Exception:
            nad = {"available": False, "reason": "error:internal",
                   "matched": False, "evidence": []}
        return _json_response(200, {
            "ok": True,
            "terminal": {"terminal_id": tid,
                         "hostname": t["hostname"] or "",
                         "ip": t["ip"] or ""},
            "huorong": hr,
            "nad": nad,
            "online_log": {"available": False,
                           "reason": _ONLINE_LOG_PENDING},
            "isolate": {"available": False,
                        "reason": "画方阻断/解除接口待接入"}})

    # IP 在线日志查询：画方接口规格待接入（available=False 语义不变）；
    # 过渡数据源=平台通信时间线（独立 platform_timeline 区块并列透出）。
    if method == "GET" and len(parts) == 6 and parts[5] == "online-log":
        t = store.get_terminal(tid)
        if t is None:
            raise ApiError(404, "终端不存在")
        ip = _q_str(query, "ip")
        mac = _q_str(query, "mac")
        start = _q_str(query, "start")
        end = _q_str(query, "end")
        if not ip and not mac:
            raise ApiError(400, "IP 与 MAC 至少提供一项")
        try:
            timeline = store.metrics_timeline(tid, days=_TP_TIMELINE_DAYS)
        except Exception:
            timeline = {"available": False, "points": [],
                        "error": "timeline_query_failed"}
        return _json_response(200, {
            "ok": True, "available": False,
            "reason": _ONLINE_LOG_PENDING,
            "query": {"ip": ip, "mac": mac, "start": start, "end": end},
            "platform_timeline": timeline})

    # 终端隔离（高危）：admin-only + 密码重校验 + 审计；画方 API 待接入
    if method == "POST" and len(parts) == 6 and parts[5] == "isolate":
        if not auth.require_admin(sess):
            auth.audit(auth.Ev.ACCESS_DENIED, "blocked",
                       username=sess["username"] if sess is not None else None,
                       user_id=sess["user_id"] if sess is not None else None,
                       client_ip=client_ip,
                       target="/api/v1/console/thirdparty/%s/isolate" % tid,
                       reason="admin_required")
            raise ApiError(403, "需要管理员权限")
        if store.get_terminal(tid) is None:
            raise ApiError(404, "终端不存在")
        data = _parse_json_body(body) or {}
        action = str(data.get("action") or "")
        password = str(data.get("password") or "")
        if action not in ("block", "unblock"):
            raise ApiError(400, "action 必须为 block 或 unblock")
        if not password:
            raise ApiError(400, "需要管理员密码重校验")
        # 密码重校验：复用 authenticate 全链（锁定/限速/审计自动生效）。
        # 语义映射：401 保留给「控制台会话无效」（apiFetch 全局跳登录），
        # 重校验失败用 403（密码未通过）/ 423（账号锁定）/ 429（限速）。
        r = auth.authenticate(str(sess["username"]), password, client_ip,
                              headers.get("User-Agent", ""))
        if not r.ok:
            store.audit(client_ip,
                        "/api/v1/console/thirdparty/%s/isolate" % tid,
                        "tp_isolate_auth_fail",
                        "action=%s terminal=%s operator=%s"
                        % (action, tid, sess["username"]))
            status = r.http_status or 403
            if status not in (423, 429):
                status = 403
            raise ApiError(status, r.message)
        if r.token:
            # 重校验产生的临时会话立即吊销（不留冗余会话）
            auth.revoke_session(r.token, reason="isolate_reauth")
        store.audit(client_ip,
                    "/api/v1/console/thirdparty/%s/isolate" % tid,
                    "tp_isolate_attempt",
                    "action=%s terminal=%s operator=%s"
                    % (action, tid, sess["username"]))
        # 画方阻断/解除 API 待接入：校验与审计已真实生效，操作未执行
        raise ApiError(501, "画方阻断/解除接口待接入，操作未执行")

    raise ApiError(404, "not found")
    """params_json / headers_json 入库前校验：空 → '{}'；dict → 规范化 JSON；
    字符串 → json.loads 校验且必须是对象，否则 400。返回规范化 JSON 字符串。"""
    if value is None or value == "":
        return "{}"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            raise ApiError(400, "%s is not valid json" % field)
        if not isinstance(parsed, dict):
            raise ApiError(400, "%s must be a json object" % field)
        return json.dumps(parsed, ensure_ascii=False)
    raise ApiError(400, "%s must be a json object" % field)


def _llm_test(settings):
    """算力网关连通性测试：GET {url}/models（OpenAI 兼容，/v1 前缀自适应），
    Bearer 鉴权，8s 超时。返回 {ok, status_code, latency_ms, error}；
    URL/Key 未配置 → {ok:false, error:'not_configured'}。"""
    url = (settings.get("llm.url") or "").strip()
    key = settings.get("llm.api_key") or ""
    if not url or not key:
        return {"ok": False, "status_code": None, "latency_ms": None,
                "error": "not_configured"}
    base = url.rstrip("/")
    models_url = base + ("/models" if base.endswith("/v1") else "/v1/models")
    req = urllib.request.Request(models_url)
    req.add_header("Authorization", "Bearer " + key)
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            status = getattr(resp, "status", None) or getattr(resp, "code", 0)
    except urllib.error.HTTPError as exc:
        status = exc.code
    except Exception as exc:
        return {"ok": False, "status_code": None,
                "latency_ms": int((time.time() - started) * 1000),
                "error": str(exc)[:200]}
    return {"ok": 200 <= int(status) < 300, "status_code": int(status),
            "latency_ms": int((time.time() - started) * 1000), "error": ""}


def _audit_vlan_kb(operator, client_ip, action, result, detail=None,
                   target="sysadmin/vlan-kb"):
    """VLAN 知识库写操作审计（ADR-044）：action 区分 upsert/import/delete。"""
    auth.audit("sysadmin.vlan_kb", result, username=operator,
               client_ip=client_ip, target=target, reason=action,
               detail=detail or {})


def _console_sysadmin(ctx, method, parts, headers, body, sess, client_ip):
    store = ctx.store
    rest = parts[4:]                      # ['users'|'llm'|'third-party'|'tokens', ...]
    sub = rest[0] if rest else ""
    operator = sess["username"]
    operator_id = int(sess["user_id"])

    # ---------- 1. 账户管理（console_auth.db console_users）----------
    if sub == "users":
        if method == "GET" and len(rest) == 1:
            return _json_response(200, {"ok": True, "users": auth.list_users()})
        if method == "POST" and len(rest) == 1:
            data = _parse_json_body(body) or {}
            _require(data, "username")
            _require(data, "password")
            r = auth.create_user(
                str(data.get("username") or ""),
                str(data.get("password") or ""),
                str(data.get("role") or "operator"),
                operator=operator, client_ip=client_ip)
            if not r["ok"]:
                raise ApiError(r["http_status"], r["message"])
            return _json_response(200, {"ok": True, "id": r["user_id"],
                                        "msg": r["message"]})
        if len(rest) >= 2 and rest[1].isdigit():
            uid = int(rest[1])
            if method == "PUT" and len(rest) == 2:
                data = _parse_json_body(body) or {}
                r = auth.update_user(
                    uid,
                    role=(str(data["role"]) if "role" in data else None),
                    status=(str(data["status"]) if "status" in data else None),
                    operator=operator, current_user_id=operator_id,
                    client_ip=client_ip)
                if not r["ok"]:
                    raise ApiError(r["http_status"], r["message"])
                return _json_response(200, {"ok": True, "msg": r["message"]})
            if method == "DELETE" and len(rest) == 2:
                r = auth.delete_user(uid, operator=operator,
                                     current_user_id=operator_id,
                                     client_ip=client_ip)
                if not r["ok"]:
                    raise ApiError(r["http_status"], r["message"])
                return _json_response(200, {"ok": True, "msg": r["message"]})
            if method == "POST" and len(rest) == 3 and rest[2] == "reset-password":
                data = _parse_json_body(body) or {}
                _require(data, "new_password")
                r = auth.admin_reset_password(
                    uid, str(data.get("new_password") or ""), operator,
                    client_ip=client_ip, force_change=True)
                if not r.ok:
                    raise ApiError(r.http_status, r.message)
                return _json_response(200, {"ok": True, "msg": r.message})
        raise ApiError(404, "not found")

    # ---------- 2. 算力网关（settings llm.*，key 加密存储）----------
    if sub == "llm":
        s = ctx.settings
        if method == "GET" and len(rest) == 1:
            url = s.get("llm.url") or ""
            key = s.get("llm.api_key") or ""
            return _json_response(200, {"ok": True, "llm": {
                "url": url,
                "model": s.get("llm.model") or "",
                "model_fallback": s.get("llm.model_fallback") or "",
                "api_key_masked": mask_secret(key),
                "configured": bool(url and key),
            }})
        if method == "POST" and len(rest) == 1:
            data = _parse_json_body(body) or {}
            changed = []
            for field, key in (("url", "llm.url"), ("model", "llm.model"),
                               ("model_fallback", "llm.model_fallback")):
                if field in data:
                    s.set(key, str(data.get(field) or "").strip())
                    changed.append(key)
            if data.get("api_key"):      # 留空 = 保持不变（与现有逻辑一致）
                s.set("llm.api_key", str(data.get("api_key")))
                changed.append("llm.api_key")
            return _json_response(200, {"ok": True, "changed": changed})
        if method == "POST" and len(rest) == 2 and rest[1] == "test":
            return _json_response(200, {"ok": True,
                                        "test": _llm_test(ctx.settings)})
        raise ApiError(404, "not found")

    # ---------- 2b. 远程唤醒（wol.* + nad.*，ADR-044 双路线增补）----------
    if sub == "wol":
        s = ctx.settings
        if method == "GET" and len(rest) == 1:
            key = s.get("nad.wol_api_key") or ""
            url = s.get("nad.wol_api_url") or ""
            return _json_response(200, {"ok": True, "wol": {
                "wake_mode": s.get("wol.wake_mode") or "relay",
                "pinned_relay": s.get("wol.pinned_relay") or "",
                "relay_step_sec": s.get("wol.relay_step_sec") or "120",
                "relay_window_sec": s.get("wol.relay_window_sec") or "300",
                "max_inflight": s.get("wol.max_inflight") or "3",
                "nad": {"url": url,
                        "api_key_masked": mask_secret(key),
                        "configured": bool(url)},
            }})
        if method == "POST" and len(rest) == 1:
            data = _parse_json_body(body) or {}
            changed = []
            mode = str(data.get("wake_mode") or "").strip()
            if mode:
                if mode not in ("relay", "nad"):
                    raise ApiError(400, "wake_mode 仅支持 relay/nad")
                s.set("wol.wake_mode", mode)
                changed.append("wol.wake_mode")
            if "pinned_relay" in data:
                pinned = str(data.get("pinned_relay") or "").strip()
                if pinned and not ctx.store.get_terminal(pinned):
                    raise ApiError(404, "pinned_relay terminal not found")
                s.set("wol.pinned_relay", pinned)
                changed.append("wol.pinned_relay")
            for field, key, lo, hi in (
                    ("relay_step_sec", "wol.relay_step_sec", 30, 600),
                    ("relay_window_sec", "wol.relay_window_sec", 60, 1800),
                    ("max_inflight", "wol.max_inflight", 1, 10)):
                if field in data:
                    try:
                        v = int(str(data.get(field)).strip())
                    except (TypeError, ValueError):
                        raise ApiError(400, "%s 必须为整数" % field)
                    if not lo <= v <= hi:
                        raise ApiError(400, "%s 取值 %d-%d"
                                           % (field, lo, hi))
                    s.set(key, str(v))
                    changed.append(key)
            if "nad_url" in data:
                s.set("nad.wol_api_url",
                      str(data.get("nad_url") or "").strip())
                changed.append("nad.wol_api_url")
            if data.get("nad_api_key"):     # 留空 = 保持不变
                s.set("nad.wol_api_key", str(data.get("nad_api_key")))
                changed.append("nad.wol_api_key")
            auth.audit("sysadmin.wol_settings", "success",
                       username=(sess["username"] if sess else None),
                       client_ip=client_ip, target="sysadmin/wol",
                       detail={"changed": changed})
            return _json_response(200, {"ok": True, "changed": changed})
        raise ApiError(404, "not found")

    # ---------- 3. 第三方接口登记 ----------
    if sub == "third-party":
        if method == "GET" and len(rest) == 1:
            return _json_response(200, {"ok": True,
                                        "apis": store.third_party_list()})
        if method == "POST" and len(rest) == 1:
            data = _parse_json_body(body) or {}
            name = str(_require(data, "name")).strip()
            base_url = str(_require(data, "base_url")).strip()
            http_method = str(data.get("method") or "GET").upper()
            if http_method not in ("GET", "POST", "PUT", "DELETE", "HEAD"):
                raise ApiError(400, "method must be GET/POST/PUT/DELETE/HEAD")
            tpid = store.third_party_create(
                name, base_url, http_method,
                _validate_json_obj(data.get("params_json"), "params_json"),
                _validate_json_obj(data.get("headers_json"), "headers_json"),
                str(data.get("note") or ""))
            return _json_response(200, {"ok": True, "id": tpid})
        if len(rest) >= 2 and rest[1].isdigit():
            tpid = int(rest[1])
            if method == "PUT" and len(rest) == 2:
                data = _parse_json_body(body) or {}
                fields = {}
                if "name" in data:
                    name = str(data.get("name") or "").strip()
                    if not name:
                        raise ApiError(400, "name cannot be empty")
                    fields["name"] = name
                if "base_url" in data:
                    base_url = str(data.get("base_url") or "").strip()
                    if not base_url:
                        raise ApiError(400, "base_url cannot be empty")
                    fields["base_url"] = base_url
                if "method" in data:
                    http_method = str(data.get("method") or "").upper()
                    if http_method not in ("GET", "POST", "PUT", "DELETE", "HEAD"):
                        raise ApiError(400,
                                       "method must be GET/POST/PUT/DELETE/HEAD")
                    fields["method"] = http_method
                if "params_json" in data:
                    fields["params_json"] = _validate_json_obj(
                        data.get("params_json"), "params_json")
                if "headers_json" in data:
                    fields["headers_json"] = _validate_json_obj(
                        data.get("headers_json"), "headers_json")
                if "note" in data:
                    fields["note"] = str(data.get("note") or "")
                if not fields:
                    raise ApiError(400, "nothing to update")
                if not store.third_party_update(tpid, fields):
                    raise ApiError(404, "api not found")
                return _json_response(200, {"ok": True})
            if method == "DELETE" and len(rest) == 2:
                if not store.third_party_delete(tpid):
                    raise ApiError(404, "api not found")
                return _json_response(200, {"ok": True})
            if method == "POST" and len(rest) == 3 and rest[2] == "toggle":
                data = _parse_json_body(body) or {}
                if not store.third_party_toggle(tpid, bool(data.get("enabled"))):
                    raise ApiError(404, "api not found")
                return _json_response(200, {"ok": True})
        raise ApiError(404, "not found")

    # ---------- 4. 终端 token 维护 ----------
    if sub == "tokens":
        if method == "GET" and len(rest) == 1:
            return _json_response(200, {"ok": True, "tokens": store.token_list()})
        if method == "POST" and len(rest) == 1:
            data = _parse_json_body(body) or {}
            row = store.token_create(str(data.get("label") or ""))
            auth.audit(auth.Ev.TOKEN_STATUS, "success", username=operator,
                       client_ip=client_ip, target="token#%d" % row["id"],
                       reason="token_created", detail={"label": row["label"]})
            return _json_response(200, {"ok": True, "token": row})
        if len(rest) == 3 and rest[1].isdigit() and \
                rest[2] in ("rotate", "disable", "enable"):
            tid = int(rest[1])
            action = rest[2]
            if action == "rotate":
                new_token = store.token_rotate(tid)
                if new_token is None:
                    raise ApiError(404, "token not found")
                auth.audit(auth.Ev.TOKEN_ROTATED, "success", username=operator,
                           client_ip=client_ip, target="token#%d" % tid,
                           reason="token_rotated")
                return _json_response(200, {"ok": True, "token": new_token})
            status = "disabled" if action == "disable" else "active"
            if not store.token_set_status(tid, status):
                raise ApiError(404, "token not found")
            auth.audit(auth.Ev.TOKEN_STATUS, "success", username=operator,
                       client_ip=client_ip, target="token#%d" % tid,
                       reason="token_" + status)
            return _json_response(200, {"ok": True})
        raise ApiError(404, "not found")

    # ---------- 5. 交换机管理（默认凭据 + 台账，ADR-026）----------
    if sub == "switch-default":
        s = ctx.settings
        if method == "GET" and len(rest) == 1:
            pwd = s.get("switch.default_password") or ""
            return _json_response(200, {"ok": True, "switch_default": {
                "username": s.get("switch.default_username") or "",
                "password_masked": mask_secret(pwd) if pwd else "(未配置)",
                "password_set": bool(pwd),
            }})
        if method == "PUT" and len(rest) == 1:
            data = _parse_json_body(body) or {}
            changed = []
            if "username" in data:
                s.set("switch.default_username",
                      str(data.get("username") or "").strip())
                changed.append("username")
            if data.get("password"):        # 留空 = 保持不变（对齐 llm key 语义）
                s.set("switch.default_password", str(data.get("password")))
                changed.append("password")
            auth.audit(auth.Ev.SWITCH_DEFAULT_UPDATED, "success",
                       username=operator, client_ip=client_ip,
                       reason="switch_default_updated",
                       detail={"changed": changed})   # 不记明文密码
            return _json_response(200, {"ok": True, "changed": changed})
        raise ApiError(404, "not found")

    if sub == "nad-switches" and method == "GET" and len(rest) == 1:
        # NAD macports.manip 聚合导出（ADR-029 台账补录辅助，admin-only）
        inventory = _nad_switch_inventory(store)
        for e in inventory:
            e["in_ledger"] = store.switch_find_by_ip(e["ip"]) is not None
        return _json_response(200, {"ok": True, "switches": inventory,
                                    "total": len(inventory),
                                    "note": "确认后 POST switches/import-nad 批量入台账（凭据用全局缺省，逐台可覆盖）"})
    if sub == "switches":
        if method == "GET" and len(rest) == 1:
            rows = store.switch_list()
            switches = []
            for r in rows:
                d = dict(r)
                has_pwd = bool(d.pop("password_set", 0))
                d["password"] = "****" if has_pwd else ""   # 恒脱敏
                switches.append(d)
            return _json_response(200, {"ok": True, "switches": switches})
        if method == "POST" and len(rest) == 2 and rest[1] == "import-nad":
            # NAD 清单批量入台账（已存在 IP 跳过；凭据=全局缺省口令加密落库）
            inventory = _nad_switch_inventory(store)
            default_user = ctx.settings.get(
                "switch.default_username") or "reader"
            default_pwd = ctx.settings.get("switch.default_password") or ""
            if not default_pwd:
                raise ApiError(400, "switch.default_password not configured")
            created, skipped = [], []
            for item in inventory:
                if store.switch_find_by_ip(item["ip"]):
                    skipped.append(item["ip"])
                    continue
                sid = store.switch_create(
                    item["name"], item["ip"], 22, default_user,
                    ctx.settings.encrypt(default_pwd), "nad-import")
                created.append({"id": sid, "ip": item["ip"],
                                "name": item["name"]})
            auth.audit(auth.Ev.SWITCH_CREATED, "success",
                       username=operator, client_ip=client_ip,
                       target="switches/import-nad",
                       reason="nad_import",
                       detail={"created": len(created),
                               "skipped": len(skipped)})   # 不记密码
            return _json_response(200, {"ok": True, "created": created,
                                        "skipped": skipped})
        if method == "POST" and len(rest) == 1:
            data = _parse_json_body(body) or {}
            name = str(_require(data, "name")).strip()
            ip = str(_require(data, "ip")).strip()
            username = str(_require(data, "username")).strip()
            password = str(data.get("password") or "")
            if not password:
                raise ApiError(400, "password is required")
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                raise ApiError(400, "invalid ip address")
            sid = store.switch_create(
                name, ip, int(data.get("ssh_port") or 22), username,
                ctx.settings.encrypt(password), str(data.get("brand") or ""))
            auth.audit(auth.Ev.SWITCH_CREATED, "success", username=operator,
                       client_ip=client_ip, target="switch#%d" % sid,
                       reason="switch_created",
                       detail={"name": name, "ip": ip})   # 不记明文密码
            return _json_response(200, {"ok": True, "id": sid})
        if len(rest) == 2 and rest[1].isdigit():
            sid = int(rest[1])
            if method == "PUT" and len(rest) == 2:
                if not store.switch_get(sid):
                    raise ApiError(404, "switch not found")
                data = _parse_json_body(body) or {}
                fields = {}
                for key in ("name", "ip", "username", "brand"):
                    if key in data:
                        value = str(data.get(key) or "").strip()
                        if key in ("name", "ip", "username") and not value:
                            raise ApiError(400, "%s cannot be empty" % key)
                        fields[key] = value
                if "ssh_port" in data:
                    fields["ssh_port"] = int(data.get("ssh_port") or 22)
                if "ip" in fields:
                    try:
                        ipaddress.ip_address(fields["ip"])
                    except ValueError:
                        raise ApiError(400, "invalid ip address")
                if data.get("password"):      # 留空 = 保持原值
                    fields["password_enc"] = ctx.settings.encrypt(
                        str(data.get("password")))
                if not fields:
                    raise ApiError(400, "nothing to update")
                store.switch_update(sid, fields)
                auth.audit(auth.Ev.SWITCH_UPDATED, "success", username=operator,
                           client_ip=client_ip, target="switch#%d" % sid,
                           reason="switch_updated",
                           detail={"changed": sorted(fields)})
                return _json_response(200, {"ok": True})
            if method == "DELETE" and len(rest) == 2:
                if not store.switch_delete(sid):
                    raise ApiError(404, "switch not found")
                auth.audit(auth.Ev.SWITCH_DELETED, "success", username=operator,
                           client_ip=client_ip, target="switch#%d" % sid,
                           reason="switch_deleted")
                return _json_response(200, {"ok": True})
        raise ApiError(404, "not found")

    # ---------- 5b. 画方准入终端镜像同步（ADR-047 批 B，admin-only）----------
    if sub == "nad-sync":
        if method != "POST":
            raise ApiError(405, "method not allowed")
        import nad_client
        try:
            res = nad_client.nad_sync_to_store(ctx.store)
        except Exception as exc:
            res = {"ok": False, "total": 0, "capable": 0,
                   "error": str(exc)[:160]}
        auth.audit("sysadmin.nad_sync",
                   "success" if res.get("ok") else "failed",
                   username=(sess["username"] if sess else None),
                   client_ip=client_ip, target="nad",
                   detail={"total": res.get("total"),
                           "capable": res.get("capable"),
                           "error": res.get("error")})
        if not res.get("ok"):
            raise ApiError(400, "画方终端同步失败：%s"
                                % (res.get("error") or "unknown"))
        return _json_response(200, {"ok": True, "total": res["total"],
                                    "capable": res["capable"],
                                    "sync_ts": ctx.store.nad_sync_ts()})

    # ---------- 6. VLAN 知识库（ADR-044 增补：钉钉表格灌入 + 检索梯子 L1 数据源）----------
    if sub == "vlan-kb":
        if method == "GET" and len(rest) == 1:
            # 只读列表，不审计（与 users/llm 等只读端点一致）
            return _json_response(200, {"ok": True,
                                        "items": store.vlan_kb_list()})

        # 单条 upsert（按 cidr 幂等覆盖，服务端归一网段格式）
        if method == "POST" and len(rest) == 1:
            data = _parse_json_body(body) or {}
            try:
                cidr_raw = str(_require(data, "cidr")).strip()
                try:
                    cidr = str(ipaddress.ip_network(cidr_raw, strict=False))
                except ValueError:
                    raise ApiError(400, "cidr 非法：%s" % cidr_raw[:64])
                item = {"cidr": cidr,
                        "vlan_id": str(data.get("vlan_id") or "").strip(),
                        "zone_desc": str(data.get("zone_desc") or "").strip(),
                        "source": str(data.get("source") or "").strip()}
            except ApiError as exc:
                _audit_vlan_kb(operator, client_ip, "upsert", "failed",
                               {"error": exc.message})
                raise
            r = store.vlan_kb_upsert_many([item])
            _audit_vlan_kb(operator, client_ip, "upsert", "success",
                           {"cidr": cidr, "vlan_id": item["vlan_id"],
                            "added": r["added"], "updated": r["updated"]})
            return _json_response(200, {"ok": True, "added": r["added"],
                                        "updated": r["updated"]})

        # 批量导入（表格导出 JSON：{"items": [...]}，同种子重复导入幂等覆盖）
        if method == "POST" and len(rest) == 2 and rest[1] == "import":
            data = _parse_json_body(body) or {}
            items = data.get("items")
            try:
                if not isinstance(items, list):
                    raise ApiError(400, "items 必须为数组")
                if len(items) > 500:
                    raise ApiError(400, "items 超过单次上限 500 条（本次 %d 条）"
                                   % len(items))
                for idx, it in enumerate(items):
                    if not isinstance(it, dict):
                        raise ApiError(400, "items[%d] 必须为对象" % idx)
            except ApiError as exc:
                _audit_vlan_kb(operator, client_ip, "import", "failed",
                               {"error": exc.message})
                raise
            valid, invalid = [], []
            for it in items:
                cidr_raw = str(it.get("cidr") or "").strip()
                try:
                    cidr = str(ipaddress.ip_network(cidr_raw, strict=False))
                except ValueError:
                    invalid.append({"cidr": cidr_raw[:64],
                                    "reason": "cidr 非法，无法归一"})
                    continue
                valid.append({"cidr": cidr,
                              "vlan_id": str(it.get("vlan_id") or "").strip(),
                              "zone_desc": str(it.get("zone_desc") or "").strip(),
                              "source": str(it.get("source") or "").strip()})
            r = store.vlan_kb_upsert_many(valid)
            _audit_vlan_kb(operator, client_ip, "import", "success",
                           {"total": len(items), "added": r["added"],
                            "updated": r["updated"], "invalid": len(invalid)})
            return _json_response(200, {
                "ok": True, "added": r["added"], "updated": r["updated"],
                "invalid": invalid,
                "imported_ts": datetime.datetime.now().isoformat(
                    timespec="seconds")})

        # 删除单条
        if method == "DELETE" and len(rest) == 2 and rest[1].isdigit():
            vid = int(rest[1])
            if not store.vlan_kb_delete(vid):
                _audit_vlan_kb(operator, client_ip, "delete", "failed",
                               {"id": vid, "error": "not found"},
                               target="vlan_kb#%d" % vid)
                raise ApiError(404, "vlan_kb entry not found")
            _audit_vlan_kb(operator, client_ip, "delete", "success",
                           {"id": vid}, target="vlan_kb#%d" % vid)
            return _json_response(200, {"ok": True})

    raise ApiError(404, "not found")


def status_disposition(status, payload, content_type, filename):
    """带下载头的响应（app.py 读取 payload.disposition）。"""
    return Response(status, payload, content_type,
                    'attachment; filename="%s"' % filename)


class Response(object):
    """带附加头的响应包装。"""

    def __init__(self, status, payload, content_type, disposition=None,
                 extra_headers=None):
        self.status = status
        self.payload = payload
        self.content_type = content_type
        self.disposition = disposition
        self.extra_headers = extra_headers or {}


def _console_nettest(ctx, method, parts, query, body):
    store = ctx.store
    if method == "POST" and parts == ["api", "v1", "console", "nettest", "launch"]:
        data = _parse_json_body(body) or {}
        result = ctx.iperf.launch(
            terminal_id=str(data.get("terminal_id") or ""),
            test_type=str(data.get("test_type") or ""),
            duration_sec=int(data.get("duration_sec") or 10),
            source="console")
        status = 200 if result.get("ok") else 400
        return _json_response(status, result)
    if method == "GET" and parts == ["api", "v1", "console", "nettest", "tasks"]:
        limit = _q_int(query, "limit", 50)
        return _json_response(200, {"ok": True,
                                    "tasks": store.iperf_list(
                                        limit, query.get("terminal_id"))})
    if method == "POST" and len(parts) == 6 and parts[4] == "task" and \
            parts[5].startswith("IT-"):
        return _json_response(200, ctx.iperf.cancel(parts[5]))
    if method == "GET" and parts == ["api", "v1", "console", "nettest", "report"]:
        limit = _q_int(query, "limit", 20)
        html = build_nettest_report_html(store, limit)
        fname = "EyeTerm_NetTest_%s.html" % time.strftime("%Y%m%d")
        return status_disposition(200, html.encode("utf-8"),
                                  "text/html; charset=utf-8", fname)
    raise ApiError(404, "not found")


def build_nettest_report_html(store, limit=20):
    tasks = store.iperf_list(limit)
    rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%d</td>"
        "<td><span class='badge b-%s'>%s</span></td><td>%s</td><td>%s</td></tr>" % (
            _esc(t["task_id"]), _esc(t["terminal_id"]), _esc(t["test_type"]),
            int(t["port"]),
            "ok" if t["status"] == "done" else ("warn" if t["status"] == "running"
                                               else "off"),
            _esc(t["status"]), _fmt_ts(t["created_ts"]), _fmt_ts(t["finished_ts"]))
        for t in tasks) or "<tr><td colspan='8'>无任务</td></tr>"
    return ("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            "<title>观枢终端平台｜EyeTerm · 网络测试报告</title>"
            "<style>%s</style></head><body><div class='wrap'>"
            "<h1>观枢终端平台｜EyeTerm · 网络测试报告</h1>"
            "<div class='sub'>EyeTerm Server v1.1 ｜ 生成 %s ｜ 最近 %d 个任务</div>"
            "<div class='card'><h2>任务列表</h2>"
            "<table><tr><th>任务</th><th>终端</th><th>类型</th><th>端口</th>"
            "<th>状态</th><th>状态值</th><th>创建</th><th>结束</th></tr>%s</table></div>"
            "<div class='foot'>观枢终端平台｜EyeTerm 服务端自动生成 · 单文件自包含 HTML</div>"
            "</div></body></html>") % (
        _REPORT_CSS, _fmt_ts(int(time.time())), limit, rows)


def _aggregate_ipconflict_context(ctx, terminal_id):
    """聚合 IP 冲突四源证据包（ADR-030）。返回 pkg 或 None（无冲突上报）。

    ①ipconflict_reports 窗口内该终端最近上报 IP 的交叉判定
      （suspect_reasons/nic_history/evidence）
    ②NAD 准入资产（nad_find_by_ip：名称/部门/在线/IP/MAC/接入交换机）
    ③最近一次 deep 任务（五步 steps 摘要 + verdict）
    ④数据源状态位"""
    store = ctx.store
    # 该终端最近一次冲突上报（确定分析目标 ip/mac；无上报=无冲突证据）
    cur = store._conn.cursor()
    cur.execute("SELECT ip, mac FROM ipconflict_reports"
                " WHERE terminal_id=? ORDER BY id DESC LIMIT 1",
                (terminal_id,))
    row = cur.fetchone()
    cur.close()
    if not row:
        return None
    ip = row["ip"] if isinstance(row, dict) else row[0]
    mac = row["mac"] if isinstance(row, dict) else row[1]
    pkg = {"ip": ip, "mac": mac, "sources": {}}

    # ① 终端上报交叉判定（只读）
    try:
        pkg["conflict_reports"] = store.ipconflict_lookup(
            ip, terminal_id, mac, window_days=7)
        pkg["sources"]["terminal_reports"] = "ok"
    except Exception as exc:
        pkg["conflict_reports"] = None
        pkg["sources"]["terminal_reports"] = "error:%s" % repr(exc)[:80]

    # ② NAD 准入资产
    try:
        from nad_client import nad_find_by_ip
        blocks, reason = nad_find_by_ip(store, ip)
        if blocks is None:
            pkg["admission"] = None
            pkg["sources"]["admission"] = reason or "not_configured"
        else:
            pkg["admission"] = {"ip": ip, "blocks": blocks}
            pkg["sources"]["admission"] = "connected"
    except Exception as exc:
        pkg["admission"] = None
        pkg["sources"]["admission"] = "error:%s" % repr(exc)[:80]

    # ③ 最近一次深度检测任务（steps 摘要化：仅 step/status/target/note）
    task = store.deep_task_latest(terminal_id)
    if task:
        pkg["deep_task"] = {
            "task_id": task["task_id"], "status": task["status"],
            "ip": task.get("ip"), "mac": task.get("mac"),
            "steps": [{"step": s.get("step"), "name": s.get("name"),
                       "status": s.get("status"), "target": s.get("target"),
                       "note": s.get("note"),
                       "evidence_count": len(s.get("evidence") or [])}
                      for s in task.get("steps") or []],
            "verdict": task.get("verdict")}
        pkg["sources"]["gateway_arp"] = next(
            (s.get("status") for s in pkg["deep_task"]["steps"]
             if s.get("step") == "arp"), "skipped")
        pkg["sources"]["access_mac"] = next(
            (s.get("status") for s in pkg["deep_task"]["steps"]
             if s.get("step") == "macaddr"), "skipped")
    else:
        pkg["deep_task"] = None
        pkg["sources"]["gateway_arp"] = "no_task"
        pkg["sources"]["access_mac"] = "no_task"
    return pkg


def _aggregate_routetrace_context(ctx, terminal_id, route_ctx):
    """聚合路由追踪三源证据包（ADR-031）。hops 非法/空返回 None（回退）。"""
    store = ctx.store
    hops = route_ctx.get("hops")
    if not isinstance(hops, list) or not hops:
        return None
    pkg = {"target": str(route_ctx.get("target") or "-"),
           "hops": hops, "sources": {"hops": "terminal_upload"}}
    # 知识库路由表全量（LLM 据此判定各跳区域归属）
    try:
        nodes = _kb_route_nodes(ctx)
        pkg["route_nodes"] = nodes or None
        pkg["sources"]["route_nodes"] = "kb" if nodes else "empty"
    except Exception as exc:
        pkg["route_nodes"] = None
        pkg["sources"]["route_nodes"] = "error:%s" % repr(exc)[:80]
    # 终端基础信息（名称/网段）
    try:
        t = store.get_terminal(terminal_id)
        if t:
            t_ip = str(t.get("ip") or "")
            seg = ""
            try:
                import ipaddress as _ipa
                seg = "%s/%d" % (_ipa.ip_network(
                    t_ip + "/24", strict=False).network_address,
                    24) if t_ip else ""
            except ValueError:
                seg = ""
            pkg["terminal"] = {"hostname": t.get("hostname") or terminal_id,
                               "ip": t_ip, "segment": seg,
                               "os_info": t.get("os_info") or ""}
            pkg["sources"]["terminal"] = "ok"
    except Exception as exc:
        pkg["terminal"] = None
        pkg["sources"]["terminal"] = "error:%s" % repr(exc)[:80]
    return pkg


def run_ai_analysis(ctx, terminal_id, issue, trigger, kind=None,
                    route_ctx=None):
    """聚合上下文 → 调算力平台 → 落库。
    kind="ipconflict" 走四源聚合分支（ADR-030）；
    kind="routetrace" 走路由研判分支（ADR-031）。"""
    from ai import (SYSTEM_PROMPT, FAULT_PATTERNS, build_context,
                    build_system_prompt, llm_chat_chain)
    store = ctx.store
    started = time.time()
    context_text = build_context(store, terminal_id, hours=6)
    if context_text is None:
        raise ApiError(404, "terminal not found")
    context_record = {"text": context_text[:2000]}
    if kind == "ipconflict":
        pkg = _aggregate_ipconflict_context(ctx, terminal_id)
        if pkg is not None:  # 无冲突上报 → 回退一般性分析
            from ai import (IPCONFLICT_SYSTEM_PROMPT,
                            build_ipconflict_context)
            prompt_text, evidence, stats = build_ipconflict_context(
                issue, pkg)
            messages = [
                {"role": "system", "content": build_system_prompt(
                    IPCONFLICT_SYSTEM_PROMPT, FAULT_PATTERNS)},
                {"role": "user", "content": prompt_text},
            ]
            context_record = {"kind": "ipconflict", "ip": pkg.get("ip"),
                              "mac": pkg.get("mac"),
                              "sources": pkg.get("sources"),
                              "sections": evidence, "stats": stats}
        else:
            messages = [
                {"role": "system", "content": build_system_prompt(
                    SYSTEM_PROMPT, FAULT_PATTERNS)},
                {"role": "user",
                 "content": "【运维人员描述的问题】\n%s\n\n【终端诊断上下文】\n%s"
                 % (issue or "（未填写，请综合上下文给出健康评估）",
                    context_text)},
            ]
    elif kind == "routetrace":
        pkg = _aggregate_routetrace_context(ctx, terminal_id, route_ctx or {})
        if pkg is not None:  # hops 缺失 → 回退一般性分析
            from ai import (ROUTETRACE_SYSTEM_PROMPT,
                            build_routetrace_context)
            prompt_text, evidence, stats = build_routetrace_context(
                issue, pkg)
            messages = [
                {"role": "system", "content": build_system_prompt(
                    ROUTETRACE_SYSTEM_PROMPT, FAULT_PATTERNS)},
                {"role": "user", "content": prompt_text},
            ]
            context_record = {"kind": "routetrace",
                              "target": pkg.get("target"),
                              "hops_count": len(pkg.get("hops") or []),
                              "sources": pkg.get("sources"),
                              "sections": evidence, "stats": stats}
        else:
            messages = [
                {"role": "system", "content": build_system_prompt(
                    SYSTEM_PROMPT, FAULT_PATTERNS)},
                {"role": "user",
                 "content": "【运维人员描述的问题】\n%s\n\n【终端诊断上下文】\n%s"
                 % (issue or "（未填写，请综合上下文给出健康评估）",
                    context_text)},
            ]
    else:
        messages = [
            {"role": "system", "content": build_system_prompt(
                SYSTEM_PROMPT, FAULT_PATTERNS)},
            {"role": "user", "content": "【运维人员描述的问题】\n%s\n\n【终端诊断上下文】\n%s"
             % (issue or "（未填写，请综合上下文给出健康评估）", context_text)},
        ]
    # 超时预算（2026-09-19 收紧，ADR-009）：AI 是**增益项**，不得让用户在
    # 控制台干等。原 timeout=60 且 max_retries=1 → 单模型最多跑 2 次，
    # 两模型链最坏可达 **240s**（60×2×2），而控制台 apiFetch **无超时**，
    # 用户会一直卡住（资产定位同类事故：前端报"超时"，用户连确定数据都看不到）。
    # 实测本引擎 duration 约 2.8s（SRV-055），故 10s/模型已留 3.5× 余量；
    # max_retries=0：失败由备选模型兜底，重试=用户双倍等待，对交互式无意义。
    result = llm_chat_chain(ctx.settings.get("llm.url"),
                            ctx.settings.get("llm.api_key"),
                            [ctx.settings.get("llm.model"),
                             ctx.settings.get("llm.model_fallback")],
                            messages, timeout=10, max_retries=0)
    duration = int((time.time() - started) * 1000)
    aid = store.ai_insert(
        terminal_id=terminal_id, ts=int(time.time()), trigger=trigger,
        issue=issue, context=context_record,
        response_text=result.get("content") or "",
        status="ok" if result.get("ok") else "failed",
        error=result.get("error") or "",
        model=result.get("model") or ctx.settings.get("llm.model") or "",
        duration_ms=duration)
    return {"ok": result.get("ok"), "analysis_id": aid,
            "response": result.get("content") or "",
            "model": result.get("model") or "",
            "error": result.get("error") or "",
            "duration_ms": duration}


def _enrich_ipconflict_admission(verdict, store, ip, mac):
    """IP 冲突 verdict 注入画方准入证据（ADR-024）。

    - sources.admission_log：connected / not_configured / error:...
    - sources.core_switch_state：上报 MAC 在准入库且有交换机端口 → nas_connected；
      有登记无端口 → registered_no_port；上报 MAC 不在准入库 → not_connected
    - 同 IP 不同 MAC 升级：准入登记 MAC 与上报 MAC 不一致 → conflict_suspect=true，
      evidence 追加 {source:'nad', name, ou, ttype, manfct/model, online, block,
      reginfo, macs:[{mac,ips,macports}]}（准入侧登记 MAC 在块内）
    - 上报 MAC 命中准入库 → verdict.admission 附证据块"""
    from nad_client import nad_find_by_ip, _mac_norm
    try:
        hits, reason = nad_find_by_ip(store, ip)
    except Exception as exc:                      # 防御：增强逻辑绝不阻断主流程
        hits, reason = None, "error:%s" % exc
    if hits is None:
        verdict["sources"]["admission_log"] = reason or "not_configured"
        return
    verdict["sources"]["admission_log"] = "connected"
    verdict["admission_hit"] = bool(hits)
    target = _mac_norm(mac)
    same = [h for h in hits
            if any(_mac_norm(m.get("mac")) == target for m in h.get("macs") or [])]
    others = [h for h in hits if h not in same]
    # 上报 MAC 自身的交换机端口近似（macports 有接入交换机记录即视为已接入）
    if same:
        has_port = any(m.get("macports") for h in same
                       for m in h.get("macs") or [])
        verdict["sources"]["core_switch_state"] = ("nas_connected" if has_port
                                                   else "registered_no_port")
        verdict["admission"] = same[0]
    else:
        verdict["sources"]["core_switch_state"] = "not_connected"
    if others:
        verdict["conflict_suspect"] = True
        verdict.setdefault("evidence", []).extend(others)
        verdict["admission_registered_macs"] = [
            m.get("mac") for h in others for m in h.get("macs") or []]
        # ADR-028：suspect_reasons 统一归口（store 层 platform 侧原因 +
        # nad 层准入登记不一致原因，供控制台/终端侧解释判定依据）
        reasons = verdict.setdefault("suspect_reasons", [])
        if "nad_registered_mismatch" not in reasons:
            reasons.append("nad_registered_mismatch")


# --------------------------------------------------------------------------
# IP 冲突深度检测（ADR-029）：异步编排 + NAD 接入交换机清单导出
# --------------------------------------------------------------------------
_DEEP_SEMAPHORE = threading.Semaphore(2)   # 全局并发上限（SSH 逐设备串行）


def _deep_task_worker(ctx, task_id, terminal_id, ip, mac):
    """后台编排线程：逐步进度实时回写，结束置 done/failed。"""
    from deep_engine import run_deep_check

    def on_step(tid, steps):
        try:
            ctx.store.deep_task_update(tid, steps=steps)
        except Exception:
            pass  # 进度回写失败不阻断编排（最终态兜底回写）

    try:
        steps, verdict = run_deep_check(
            ctx.store, _kb_route_nodes(ctx), ctx.settings,
            terminal_id, ip, mac, on_step=on_step, task_id=task_id)
        ctx.store.deep_task_update(task_id, steps=steps, verdict=verdict,
                                   status="done")
    except Exception as exc:
        try:
            ctx.store.deep_task_update(task_id, status="failed",
                                       error=repr(exc)[:300])
        except Exception:
            pass
    finally:
        _DEEP_SEMAPHORE.release()


def _nad_switch_inventory(store):
    """从 NAD macports.manip 聚合接入交换机清单（台账批量补录数据源）。

    返回 [{ip, name, port_records, sample_nasif}]（按 IP 排序）。"""
    from nad_client import nad_terminals_cached
    terms, reason = nad_terminals_cached(store)
    if terms is None:
        raise ApiError(503, "nad unavailable: %s" % (reason or "unknown"))
    inv = {}
    for t in terms or []:
        macs = t.get("macs") or []
        if isinstance(macs, dict):
            macs = list(macs.values())
        for m in macs or []:
            ports = m.get("macports") or []
            if isinstance(ports, dict):
                ports = list(ports.values())
            for p in ports or []:
                manip = str(p.get("manip") or "").strip()
                if not manip:
                    continue
                entry = inv.setdefault(manip, {
                    "ip": manip,
                    "name": str(p.get("nasname") or ("SW-" + manip)),
                    "port_records": 0,
                    "sample_nasif": str(p.get("nasif") or "")})
                entry["port_records"] += 1
    return sorted(inv.values(), key=lambda e: e["ip"])


def run_terminal_diagnose(ctx, terminal_id, issue, logs):
    """终端 AI 智能诊断（ADR-023/ADR-027）：终端推送日志包+问题概述 → LLM 链出结论。

    与 run_ai_analysis 差异：上下文来自终端上报的六类日志（非服务端聚合）；
    走同一 llm_chat_chain 主备降级链。**超时预算（2026-09-19 收紧，ADR-009）**：
    timeout=25×max_retries=0 → 双模型最坏约 50s，满足终端 HTTP 等待 ≤120s 契约，
    同时覆盖实测 11.3s（SRV-070）与极端 30.3s（analysis_id=12）两类耗时。
    原 timeout=45 最坏 90s —— 虽在契约内，但对"人正等着看结果"的场景过长。
    落库 trigger=terminal_diagnose，
    context_json 存 issue + 各类日志 ≤32KB 结构感知存证（JSON 类可再解析）+
    logs_stats（每类原始/保留条数与是否截断，ADR-027）。"""
    from ai import (DIAG_SYSTEM_PROMPT, build_diagnose_context,
                    build_system_prompt, llm_chat_chain)
    store = ctx.store
    started = time.time()
    prompt_text, evidence, evidence_stats = build_diagnose_context(issue, logs or {})
    messages = [
        {"role": "system", "content": build_system_prompt(DIAG_SYSTEM_PROMPT)},
        {"role": "user", "content": prompt_text},
    ]
    result = llm_chat_chain(ctx.settings.get("llm.url"),
                            ctx.settings.get("llm.api_key"),
                            [ctx.settings.get("llm.model"),
                             ctx.settings.get("llm.model_fallback")],
                            messages, timeout=25, max_retries=0)
    duration = int((time.time() - started) * 1000)
    aid = store.ai_insert(
        terminal_id=terminal_id, ts=int(time.time()),
        trigger="terminal_diagnose", issue=issue,
        context={"issue": issue, "logs": evidence,
                 "logs_stats": evidence_stats},
        response_text=result.get("content") or "",
        status="ok" if result.get("ok") else "failed",
        error=result.get("error") or "",
        model=result.get("model") or ctx.settings.get("llm.model") or "",
        duration_ms=duration)
    return {"ok": bool(result.get("ok")), "analysis_id": aid,
            "response_text": result.get("content") or "",
            "model": result.get("model") or "",
            "error": result.get("error") or "",
            "duration_ms": duration}


def build_ai_report_html(row):
    resp = (row["response_text"] or "").replace("&", "&amp;") \
        .replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
    return ("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            "<title>观枢终端平台｜EyeTerm · AI 分析报告</title>"
            "<style>%s</style></head><body><div class='wrap'>"
            "<h1>观枢终端平台｜EyeTerm · AI 智能分析报告</h1>"
            "<div class='sub'>EyeTerm Server v1.1 ｜ 生成 %s</div>"
            "<div class='card'><h2>元信息</h2>"
            "<span class='kv'>终端<b>%s</b></span>"
            "<span class='kv'>触发<b>%s</b></span>"
            "<span class='kv'>状态<b>%s</b></span>"
            "<span class='kv'>模型<b>%s</b></span>"
            "<span class='kv'>耗时<b>%s ms</b></span></div>"
            "<div class='card'><h2>问题描述</h2><p>%s</p></div>"
            "<div class='card'><h2>AI 分析结论</h2><p>%s</p></div>"
            "<div class='foot'>观枢终端平台｜EyeTerm 服务端自动生成 · 单文件自包含 HTML</div>"
            "</div></body></html>") % (
        _REPORT_CSS, _fmt_ts(row["ts"]), _esc(row["terminal_id"]),
        _esc(row["trigger"]), _esc(row["status"]), _esc(row["model"]),
        row["duration_ms"], _esc(row["issue"]) or "-", resp or "(空)")


def _console_whitelist(ctx, method, parts, body):
    store = ctx.store
    if method == "GET" and len(parts) == 4:
        return _json_response(200, {
            "ok": True, "whitelist": store.whitelist_list(),
            "policy": "empty-list rejects new registrations; "
                      "registered terminals exempt"})
    if method == "POST" and len(parts) == 4:
        data = _parse_json_body(body) or {}
        cidr = str(data.get("cidr") or "").strip()
        note = str(data.get("note") or "")
        try:
            ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            raise ApiError(400, "invalid CIDR or IP")
        wid = store.whitelist_add(cidr, note)
        if wid is None:
            raise ApiError(409, "entry already exists")
        return _json_response(200, {"ok": True, "id": wid})
    if len(parts) == 5 and parts[4].isdigit():
        entry_id = int(parts[4])
        if method == "DELETE":
            if not store.whitelist_delete(entry_id):
                raise ApiError(404, "entry not found")
            return _json_response(200, {"ok": True})
        if method == "POST":
            data = _parse_json_body(body) or {}
            enabled = bool(data.get("enabled"))
            if not store.whitelist_toggle(entry_id, enabled):
                raise ApiError(404, "entry not found")
            return _json_response(200, {"ok": True})
    raise ApiError(404, "not found")


# ----------------------------------------------------------------------
# 静态文件
# ----------------------------------------------------------------------

def _static(path, headers=None):
    """静态资源：ETag/Last-Modified 协商缓存（no-cache + 304 revalidate，
    避免 index.html 更新后浏览器仍使用无验证器的启发式缓存旧页面）。"""
    if path in ("/", "/index.html"):
        rel = "index.html"
    elif path == "/favicon.ico":
        return Response(204, b"", "image/x-icon")
    else:
        rel = path.lstrip("/")
    full = os.path.normpath(os.path.join(CONSOLE_DIR, rel))
    if not full.startswith(CONSOLE_DIR):
        raise ApiError(403, "forbidden")
    if not os.path.isfile(full):
        raise ApiError(404, "not found")
    st = os.stat(full)
    ext = os.path.splitext(full)[1].lower()
    ctype = _CONTENT_TYPES.get(ext, "application/octet-stream")
    etag = 'W/"%s-%s"' % (int(st.st_mtime), st.st_size)
    inm = (headers or {}).get("if-none-match")
    if inm and etag in [v.strip() for v in inm.split(",")]:
        return Response(304, b"", ctype,
                        extra_headers={"ETag": etag, "Cache-Control": "no-cache"})
    with open(full, "rb") as fh:
        payload = fh.read()
    return Response(200, payload, ctype,
                    extra_headers={"ETag": etag,
                                   "Last-Modified": time.strftime(
                                       "%a, %d %b %Y %H:%M:%S GMT",
                                       time.gmtime(st.st_mtime))})


# ----------------------------------------------------------------------
# HTML 报告（ADR-008：导出报告一律 HTML，单文件自包含深色风）
# ----------------------------------------------------------------------

_REPORT_CSS = """
body{margin:0;background:#0d1117;color:#c9d1d9;font:14px/1.6 'Segoe UI',
 'Microsoft YaHei',sans-serif}
.wrap{max-width:960px;margin:0 auto;padding:32px 24px}
h1{font-size:22px;color:#e6edf3;margin:0 0 4px}
.sub{color:#8b949e;font-size:13px;margin-bottom:24px}
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;
 padding:18px 20px;margin-bottom:18px}
h2{font-size:16px;color:#79c0ff;margin:0 0 12px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid #21262d}
th{color:#8b949e;font-weight:600}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px}
.b-warn{background:#3d2e00;color:#f0b72f}
.b-info{background:#0c2d6b;color:#79c0ff}
.b-ok{background:#0f2e1d;color:#3fb950}
.b-on{background:#0f2e1d;color:#3fb950}
.b-off{background:#3d1a1a;color:#f85149}
.kv{display:inline-block;margin-right:24px;color:#8b949e}
.kv b{color:#e6edf3;margin-left:6px}
svg text{fill:#8b949e;font-size:10px}
.poly-cpu{fill:none;stroke:#f0883e;stroke-width:2}
.poly-mem{fill:none;stroke:#58a6ff;stroke-width:2}
.legend span{margin-right:18px;font-size:12px;color:#8b949e}
.dot{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px}
.d-cpu{background:#f0883e}.d-mem{background:#58a6ff}
.foot{color:#484f58;font-size:12px;margin-top:28px;text-align:center}
"""


def _svg_curve(points, width=880, height=180):
    """手绘 SVG 曲线：CPU（橙）/内存可用率（蓝）。points 为升序指标行。"""
    if not points:
        return "<p style='color:#8b949e'>时间范围内无指标数据</p>"
    xs = [p["ts"] for p in points]
    x0, x1 = xs[0], xs[-1]
    span = max(x1 - x0, 1)
    pad, w, h = 34, width, height

    def xy(ts, val):
        x = pad + (ts - x0) * 1.0 / span * (w - pad - 10)
        y = h - 24 - max(0.0, min(100.0, val)) / 100.0 * (h - 40)
        return x, y

    def poly(key):
        seg = [(xy(p["ts"], p[key])) for p in points if p.get(key) is not None]
        if len(seg) < 2:
            return ""
        return "<polyline class='poly-%s' points='%s'/>" % (
            "cpu" if key == "cpu_percent" else "mem",
            " ".join("%.1f,%.1f" % pt for pt in seg))

    t0 = _fmt_ts(x0)[5:16]
    t1 = _fmt_ts(x1)[5:16]
    return ("{svg_open} width='{w}' height='{h}'>"
            "<text x='{pad}' y='14'>100</text><text x='{pad}' y='{h2}'>0</text>"
            "<text x='6' y='{h3}' transform='rotate(-90 6 {h3})'>%</text>"
            "{cpu}{mem}"
            "<text x='{w2}' y='{h4}'>{t0} ~ {t1}</text></svg>").format(
        svg_open="<svg xmlns='http://www.w3.org/2000/svg'", w=w, h=h, pad=pad,
        h2=h - 20, h3=18, cpu=poly("cpu_percent"), mem=poly("mem_available_percent"),
        w2=w - 150, h4=h - 4, t0=t0, t1=t1)


def build_report_html(ctx, terminal_id, hours):
    store = ctx.store
    now = int(time.time())
    since = now - hours * 3600
    t = store.get_terminal(terminal_id)
    if not t:
        raise ApiError(404, "terminal not found")
    points = store.query_metrics(terminal_id, since)
    bottlenecks = store.list_bottlenecks(limit=200, terminal_id=terminal_id,
                                         since_ts=since)
    events = store.list_events(limit=200, terminal_id=terminal_id, since_ts=since)

    hb_timeout = int(ctx.config.get("heartbeat_timeout_sec", 180))
    online = (now - t["last_seen"]) < hb_timeout

    # 摘要统计
    cpus = [p["cpu_percent"] for p in points if p.get("cpu_percent") is not None]
    mems = [p["mem_available_percent"] for p in points
            if p.get("mem_available_percent") is not None]
    avg = lambda v: (sum(v) / len(v)) if v else None
    cpu_avg, cpu_max = avg(cpus), (max(cpus) if cpus else None)
    mem_avg, mem_min = avg(mems), (min(mems) if mems else None)
    disks = points[-1]["disks"] if points else []

    disk_rows = "".join(
        "<tr><td>%s</td><td>%.1f%%</td><td>%s</td></tr>" % (
            _esc(d.get("mount", "-")),
            float(d.get("percent") or 0),
            "<span class='badge b-warn'>饱和</span>"
            if (d.get("percent") or 0) > 80 else
            "<span class='badge b-ok'>正常</span>")
        for d in disks) or "<tr><td colspan='3'>无数据</td></tr>"

    bn_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%.1f</td><td>%.1f</td>"
        "<td><span class='badge b-%s'>%s</span></td><td>%s</td></tr>" % (
            _esc(b["kind"]), _esc(b["metric_key"]),
            float(b["value"] or 0), float(b["threshold"] or 0),
            "warn" if b["level"] == "warn" else "info",
            _esc(b["level"]), _fmt_ts(b["ts"]))
        for b in bottlenecks) or "<tr><td colspan='6'>无瓶颈记录</td></tr>"

    ev_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            _fmt_ts(e["ts"]), _esc(e["level"]), _esc(e["category"]),
            _esc(e["message"]))
        for e in events) or "<tr><td colspan='4'>无事件</td></tr>"

    return ("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            "<title>观枢终端平台｜EyeTerm · 终端性能报告</title>"
            "<style>%s</style></head><body><div class='wrap'>"
            "<h1>观枢终端平台｜EyeTerm · 终端性能报告</h1>"
            "<div class='sub'>EyeTerm Server v1.0 ｜ 报告生成 %s ｜ 统计窗口近 %d 小时</div>"

            "<div class='card'><h2>终端信息</h2>"
            "<span class='kv'>终端<b>%s</b></span>"
            "<span class='kv'>类型<b>%s</b></span>"
            "<span class='kv'>在线<b><span class='badge b-%s'>%s</span></b></span>"
            "<span class='kv'>系统<b>%s</b></span>"
            "<span class='kv'>客户端<b>%s</b></span>"
            "<span class='kv'>最近心跳<b>%s</b></span></div>"

            "<div class='card'><h2>指标摘要</h2>"
            "<span class='kv'>CPU 均值<b>%s</b></span>"
            "<span class='kv'>CPU 峰值<b>%s</b></span>"
            "<span class='kv'>内存可用均值<b>%s</b></span>"
            "<span class='kv'>内存可用最低<b>%s</b></span>"
            "<div class='legend' style='margin-top:10px'>"
            "<span><i class='dot d-cpu'></i>CPU 使用率</span>"
            "<span><i class='dot d-mem'></i>内存可用率</span></div>"
            "<div>%s</div></div>"

            "<div class='card'><h2>磁盘现状（最近一次快照）</h2>"
            "<table><tr><th>挂载点</th><th>使用率</th><th>判定</th></tr>%s</table></div>"

            "<div class='card'><h2>瓶颈记录（%d 条）</h2>"
            "<table><tr><th>类型</th><th>指标</th><th>当前值</th><th>阈值</th>"
            "<th>等级</th><th>时间</th></tr>%s</table></div>"

            "<div class='card'><h2>事件记录（%d 条）</h2>"
            "<table><tr><th>时间</th><th>等级</th><th>分类</th><th>内容</th></tr>%s</table></div>"

            "<div class='foot'>观枢终端平台｜EyeTerm 服务端自动生成 · 本文件为单文件自包含 HTML</div>"
            "</div></body></html>") % (
        _REPORT_CSS,
        _fmt_ts(now), hours,
        _esc(t["terminal_id"]), _esc(t["terminal_type"]),
        "on" if online else "off", "在线" if online else "离线",
        _esc(t["os_info"]), _esc(t["client_version"]), _fmt_ts(t["last_seen"]),
        _pct(cpu_avg), _pct(cpu_max), _pct(mem_avg), _pct(mem_min),
        _svg_curve(points),
        disk_rows,
        len(bottlenecks), bn_rows,
        len(events), ev_rows)


def _kb_route_nodes(ctx):
    '''知识库路由表读取（ADR-022）：kb_entries 中 category='route_nodes'
    最新条目的 content → JSON 数组 [{match,zone,desc}]；解析失败/无条目返回 []。'''
    try:
        kb = kb_store.get_kb(ctx.store.db_path)
        rows = kb.route_table(category="route_nodes")
        if not rows:
            return []
        entry = kb.get(rows[0]["kb_id"])
        content = str((entry or {}).get("content") or "")
        if not content:
            return []
        parsed = json.loads(content)
        if not isinstance(parsed, list):
            return []
        return [n for n in parsed if isinstance(n, dict) and n.get("match")]
    except Exception:
        return []


def _pct(v):
    if v is None:
        return "-"
    return "%.1f%%" % float(v)



def _console_kb_api(ctx, method, parts, query, body, sess=None):
    ''' 运维知识库：路由表/CRUD/版本迭代（5 版）/回滚/维护记录（ADR-022）。
    操作人一律取当前会话用户名（不信任客户端传入）。'''
    kb = kb_store.get_kb(ctx.store.db_path)
    data = _parse_json_body(body) or dict()
    rest = parts[4:]
    kid = str(rest[0]) if rest else ''
    author = str(sess["username"]) if sess is not None else 'admin'
    if method == 'GET' and not rest:
        category = _q_str(query, 'category')
        q = _q_str(query, 'q')
        return _json_response(200, dict(ok=True, routes=kb.route_table(category, q),
                                        categories=kb.categories()))
    if method == 'POST' and not rest:
        kb_id, ver = kb.create(str(data.get('kb_id') or '').strip(),
                               str(data.get('category') or '-'),
                               str(data.get('title') or '-'),
                               str(data.get('content') or ''),
                               author, str(data.get('note') or ''))
        if kb_id is None:
            raise ApiError(409, 'kb_id already exists')
        return _json_response(200, dict(ok=True, kb_id=kb_id, version=ver))
    if not rest:
        raise ApiError(404, 'not found')
    sub = rest[1] if len(rest) > 1 else ''
    if sub == '':
        if method == 'GET':
            e = kb.get(kid)
            if not e:
                raise ApiError(404, 'kb entry not found')
            e['history'] = kb.history(kid)
            return _json_response(200, dict(ok=True, entry=e))
        if method == 'PUT':
            cur_e = kb.get(kid)
            if not cur_e:
                raise ApiError(404, 'kb entry not found')
            # 字段缺省 = 保持原值（title 未传沿用原标题，content 未传沿用原内容）
            content = data.get('content')
            ver = kb.update(kid,
                            str(data.get('title') or cur_e['title']),
                            (data.get('category') if data.get('category') is not None
                             else None),
                            cur_e['content'] if content is None else str(content),
                            author, str(data.get('note') or ''))
            if ver is None:
                raise ApiError(404, 'kb entry not found')
            return _json_response(200, dict(ok=True, version=ver))
        if method == 'DELETE':
            deleted = kb.delete(kid, author)
            return _json_response(200, dict(ok=True, deleted=deleted))
        raise ApiError(404, 'not found')
    if sub == 'versions' and method == 'GET' and len(rest) == 2:
        return _json_response(200, dict(ok=True, versions=kb.versions(kid)))
    if sub == 'versions' and method == 'GET' and len(rest) == 3 and rest[2].isdigit():
        v = kb.get_version(kid, int(rest[2]))
        if not v:
            raise ApiError(404, 'version not found')
        return _json_response(200, dict(ok=True, version=v))
    if sub == 'rollback' and method == 'POST' and len(rest) == 2:
        ver = kb.rollback(kid, int(data.get('version') or 0), author)
        if ver is None:
            raise ApiError(404, 'version not found')
        return _json_response(200, dict(ok=True, version=ver))
    if sub == 'history' and method == 'GET':
        return _json_response(200, dict(ok=True, history=kb.history(kid)))
    raise ApiError(404, 'not found')
