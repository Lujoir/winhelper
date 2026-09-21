# -*- coding: utf-8 -*-
"""观枢终端平台服务端 · 入口。

运行时：python39 venv，纯标准库（ADR-002）。
配置：ETP_CONFIG 指向远端 config.json；本地开发回退 server/config.local.json
（gitignore，不入仓库）；均无则 dev 默认值（仅本机冒烟用）。
服务：ThreadingHTTPServer + 后台保留策略清理线程（每日 03:30 + 启动时，ADR-003）。
"""
import json
import os
import signal
import socket
import ssl
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import api
import client_release
from api import APP_VERSION, ApiContext, ApiError, Response
import desktop_policy
import nad_client
import power_control
import wol
from huorong import HuorongSyncer
from iperf import IperfManager
from secretsbox import SecretsBox
from settings import SettingsStore
from storage import StorageManager
from store import Store

import auth_upgrade as auth

_DEV_CONFIG = {
    "port": 18090,
    "terminal_port": 18443,
    "console_port": 8443,
    # 注意：download_port 不在此设默认值——_DEV_CONFIG 是所有环境的基底
    # （生产 config 只覆盖「存在的键」），在此写 18080 会渗透到生产。
    # 生产用 tls_settings 的默认 80；本地开发请在 server/config.local.json
    # （gitignore）里覆盖为 18080，避免占用系统 80。
    "tls": {"enabled": True, "min_tls_version": "TLSv1_2"},
    "legacy_http": {"enabled": True},
    "terminal_token": "dev-token",
    "console_password": "dev-console",
    "session_ttl_hours": 8,
    "heartbeat_timeout_sec": 180,
    "report_interval": 60,
    "retention_days": {"metrics": 30, "events": 90, "bottlenecks": 90},
    "bottleneck_dedup_min": 10,
    "data_dir": os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")),
}


def log(msg):
    sys.stdout.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    sys.stdout.flush()


def load_config():
    """配置加载顺序：$ETP_CONFIG → server/config.local.json → dev 默认。"""
    candidates = []
    env_cfg = os.environ.get("ETP_CONFIG")
    if env_cfg:
        candidates.append(env_cfg)
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "config.local.json"))
    for path in candidates:
        if path and os.path.isfile(path):
            with open(path, "rb") as fh:
                cfg = json.loads(fh.read().decode("utf-8-sig"))  # 兼容 BOM
            log("config loaded: %s" % path)
            merged = dict(_DEV_CONFIG)
            merged.update(cfg)
            return merged
    log("WARNING: no config file found, using DEV defaults (do not use in production)")
    return dict(_DEV_CONFIG)


# ----------------------------------------------------------------------
# 后台任务：保留策略清理
# ----------------------------------------------------------------------

def retention_worker(ctx, stop_event):
    def run_cleanup():
        try:
            stats = ctx.store.cleanup(ctx.config.get("retention_days") or {})
            if any(stats.values()):
                log("retention cleanup: %s" % stats)
        except Exception:
            log("retention cleanup failed:\n%s" % traceback.format_exc())

    run_cleanup()  # 启动即清一次
    while not stop_event.is_set():
        # 睡到下一个 03:30
        now = time.localtime()
        target = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 3, 30, 0,
                              0, 0, -1))
        if target <= time.time():
            target += 86400
        if stop_event.wait(max(target - time.time(), 1)):
            break
        run_cleanup()


# ----------------------------------------------------------------------
# HTTP Handler
# ----------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    # 技术栈指纹弱化（ADR-018）：响应头不暴露应用版本与 Python 版本
    server_version = "EyeTerm"
    protocol_version = "HTTP/1.1"
    ctx = None  # 由 main 注入 ApiContext
    stop_event = None
    scope = None  # 端口路由域（ADR-032）：None/"all"/"terminal"/"console"

    def version_string(self):
        return "EyeTerm"

    def log_message(self, fmt, *args):
        log("%s - %s" % (self.address_string(), fmt % args))

    # -- 工具 --

    def _headers_dict(self):
        out = {}
        for k, v in self.headers.items():
            out[k.lower()] = v
        return out

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return b""
        # 安装包上传端点单独放宽（ADR-042：客户端发布上传 256MB 上限），
        # 其余端点维持全局限制（防滥用）
        if self.path.startswith("/api/v1/console/client/releases/"):
            limit = api._MAX_UPLOAD_BODY
        else:
            limit = api._MAX_BODY * 2
        if length > limit:
            raise ApiError(413, "body too large")
        return self.rfile.read(length)

    def _send(self, status, payload, content_type, disposition=None,
              cache="no-cache", extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()
        if payload and self.command != "HEAD":
            self.wfile.write(payload)

    def _handle(self, method):
        parsed = urlparse(self.path)
        path = parsed.path
        query = dict((k, v[0]) for k, v in parse_qs(parsed.query).items())
        try:
            client_ip = self.client_address[0]
            body = self._read_body() if method in ("POST", "PUT") else b""
            result = api.dispatch(self.ctx, method, path, query,
                                  self._headers_dict(), body, client_ip,
                                  scope=getattr(self, "scope", None))
            if isinstance(result, Response):
                self._send(result.status, result.payload, result.content_type,
                           result.disposition,
                           extra_headers=result.extra_headers)
            else:
                status, payload, ctype = result
                self._send(status, payload, ctype)
        except ApiError as exc:
            payload = json.dumps({"ok": False, "error": exc.message},
                                 ensure_ascii=False).encode("utf-8")
            self._send(exc.status, payload, "application/json; charset=utf-8")
        except (BrokenPipeError, ConnectionResetError):
            pass  # 客户端提前断开
        except Exception:
            log("internal error on %s %s:\n%s"
                % (method, path, traceback.format_exc()))
            try:
                payload = json.dumps({"ok": False, "error": "internal error"},
                                     ensure_ascii=False).encode("utf-8")
                self._send(500, payload, "application/json; charset=utf-8")
            except Exception:
                pass

    def do_GET(self):
        self._handle("GET")

    def do_HEAD(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_PUT(self):
        self._handle("PUT")


# ----------------------------------------------------------------------
# TLS（ADR-032：双 HTTPS 监听 + 自建 CA 证书）
# ----------------------------------------------------------------------

def tls_settings(config, data_dir):
    """解析 TLS/端口配置。旧 config 无新键时取默认（TLS 启用、legacy 保留）。

    cert 未显式配置时优先 fullchain.crt（服务端必须发送完整链：OpenSSL 1.1.1
    客户端对仅叶证书 + 自签根的组合会报 unable to get local issuer）。"""
    tls = dict(config.get("tls") or {})
    certs_dir = tls.get("certs_dir") or os.path.join(data_dir, "certs")
    fullchain = os.path.join(certs_dir, "fullchain.crt")
    if tls.get("cert"):
        cert = tls["cert"]
    elif os.path.isfile(fullchain):
        cert = fullchain
    else:
        cert = os.path.join(certs_dir, "server.crt")
    key = tls.get("key") or os.path.join(certs_dir, "server.key")
    min_ver = str(tls.get("min_tls_version") or "TLSv1_2")
    min_version = {"TLSv1_2": ssl.TLSVersion.TLSv1_2,
                   "TLSv1_3": ssl.TLSVersion.TLSv1_3}.get(min_ver,
                                                          ssl.TLSVersion.TLSv1_2)
    return {
        "enabled": bool(tls.get("enabled", True)),
        "cert": cert,
        "key": key,
        "min_version": min_version,
        "min_tls_version": min_ver,
        "terminal_port": int(config.get("terminal_port", 18443)),
        "console_port": int(config.get("console_port", 8443)),
        # 公开下载口（ADR-042 增补）：浏览器打开 http://<host>/ 即客户端下载页
        "download_port": int(config.get("download_port", 80)),
        "legacy_enabled": bool((config.get("legacy_http") or {}).get("enabled", True)),
        "legacy_port": int(config.get("port", 18090)),
    }


def build_tls_context(cert_path, key_path, min_version):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = min_version
    ctx.options |= ssl.OP_NO_COMPRESSION  # CRIME 防护
    ctx.load_cert_chain(cert_path, key_path)
    return ctx


def cert_not_after(cert_path):
    """证书 notAfter（UTC epoch 秒）；极简 DER 解析，失败返回 None（防御性）。

    X.509：Certificate SEQ → tbsCertificate SEQ → [version][serial][signature]
    [issuer][validity SEQ → notBefore, notAfter]。兼容 PEM/DER、UTCTime/
    GeneralizedTime。仅用于过期监控展示，不参与 TLS 校验决策。"""
    try:
        with open(cert_path, "rb") as fh:
            raw = fh.read()
        der = (ssl.PEM_cert_to_DER_cert(raw.decode("ascii"))
               if b"-----BEGIN" in raw else raw)
    except Exception:
        return None

    def children(buf):
        i, n = 0, len(buf)
        while i < n:
            tag = buf[i]
            i += 1
            ln = buf[i]
            i += 1
            if ln & 0x80:
                k = ln & 0x7F
                ln = int.from_bytes(buf[i:i + k], "big")
                i += k
            yield tag, buf[i:i + ln]
            i += ln

    try:
        # der = Certificate TLV 编码。content 的第一个孩子才是 tbsCertificate：
        # Certificate content = tbs TLV + signatureAlgorithm TLV + signature TLV
        cert_content = next(children(der))[1]
        tbs = next(children(cert_content))[1]
        kids = list(children(tbs))
        idx = 0 if kids[0][0] != 0xA0 else 1  # 可选 [0] EXPLICIT version
        validity = kids[idx + 3][1]
        not_after = list(children(validity))[1][1].decode("ascii")
        if not_after.endswith("Z"):
            not_after = not_after[:-1]
        if len(not_after) == 12:  # UTCTime YYMMDDHHMMSS
            yy = int(not_after[:2])
            year = 2000 + yy if yy < 50 else 1900 + yy
            rest = not_after[2:]
        else:  # GeneralizedTime YYYYMMDDHHMMSS
            year, rest = int(not_after[:4]), not_after[4:]
        import calendar
        return calendar.timegm((year, int(rest[0:2]), int(rest[2:4]),
                                int(rest[4:6]), int(rest[6:8]), int(rest[8:10]),
                                0, 0, 0))
    except Exception:
        return None


# ----------------------------------------------------------------------
# main
# ----------------------------------------------------------------------

def main():
    config = load_config()
    data_dir = config.get("data_dir") or _DEV_CONFIG["data_dir"]
    if not os.path.isdir(data_dir):
        os.makedirs(data_dir)
    db_path = os.path.join(data_dir, "eyeterm.db")
    log("data dir: %s" % data_dir)

    # 控制台鉴别库与业务库同目录，便于备份与 S7 剩余信息保护处置
    auth.DB_PATH = os.path.join(data_dir, "console_auth.db")
    log("console auth db: %s" % auth.DB_PATH)
    # 角色模型启用迁移（幂等）：历史库 role 全为 operator 时把 admin 提升为
    # admin，避免 sysadmin 路由组（admin-only）上线即锁死存量管理员
    auth.ensure_admin_role()

    store = Store(db_path, config_token=config.get("terminal_token"))
    box = SecretsBox(os.path.join(data_dir, "keys", "machine.key"))
    settings = SettingsStore(store, box)
    storage = StorageManager(store, settings)
    ctx = ApiContext(store, config, settings=settings, storage=storage)
    ctx.iperf = IperfManager(store, settings)
    # 火绒镜像同步（ADR-033）：未配置凭据/未启用时线程空转等待，零请求
    ctx.huorong = HuorongSyncer(store, settings, log=log)
    ctx.huorong.start()
    # 桌面管控（ADR-036）：壁纸资源库 + 四类策略 + 匹配推荐 + 下发记录
    ctx.dp = desktop_policy.get_dp(db_path, data_dir)
    # 自动开关机（power-control P0）：电源策略快照存档与查询
    ctx.pc = power_control.get_pc(db_path)
    # 任务化迁移（ADR-046）：存量 wol_schedules 无缝挂入任务模型——
    # 只建任务并回填 task_id，不删不重建调度行，wol_tick 行为不变
    #（部署时序红线：M720t 生产计划明早 07:30 不空窗）
    try:
        _mig = ctx.pc.migrate_legacy_schedules()
        if _mig.get("migrated"):
            log("power tasks: migrated %s legacy wol schedule(s)"
                % _mig["migrated"])
    except Exception:
        log("power tasks migration error:\n%s" % traceback.format_exc())
    # 客户端版本发布（ADR-042）：安装包存储 + 版本台账 + 下载票据
    ctx.cr = client_release.get_cr(db_path, data_dir)
    Handler.ctx = ctx

    stop_event = threading.Event()
    worker = threading.Thread(target=retention_worker, args=(ctx, stop_event))
    worker.daemon = True
    worker.start()

    # WoL 定时唤醒调度（第二段产品化，ADR-044）：到期直发 → 观察窗 →
    # 同网段中继升级 → 结论落档；wol_enabled=false 可整体关闭
    if config.get("wol_enabled", True):
        wol_thread = threading.Thread(target=wol.wol_loop,
                                      args=(ctx, stop_event, log))
        wol_thread.daemon = True
        wol_thread.start()
        log("wol scheduler: on (tick %ds, wake wait %ds)"
            % (wol.TICK_SEC, wol.WAKE_WAIT_SEC))

    # 画方准入终端镜像同步（ADR-047 批 B：开关机任务第三方资产源）：
    # 未配置凭据时静默空转、零外部请求
    nad_thread = threading.Thread(target=nad_client.nad_loop,
                                 args=(ctx, stop_event, log))
    nad_thread.daemon = True
    nad_thread.start()
    # 画方列表**启动预热**（2026-09-19 修复）：画方 1588 台需翻 2 页，冷缓存
    # 首次拉取要数秒；若等到首个读请求才拉，资产定位会撞上控制台前端 25s 预算
    # （用户实况报「定位超时」）。此处启动即后台预热，此后长期命中缓存。
    # 失败静默：预热失败不影响任何既有路径。
    try:
        nad_client.nad_prefetch_async(ctx.store)
    except Exception:
        pass

    # ---- 监听装配（ADR-032）：TLS 终端口 + TLS 管理口 + legacy HTTP ----
    ts = tls_settings(config, data_dir)
    tls_ctx = None
    if ts["enabled"]:
        if os.path.isfile(ts["cert"]) and os.path.isfile(ts["key"]):
            try:
                tls_ctx = build_tls_context(ts["cert"], ts["key"],
                                            ts["min_version"])
                na = cert_not_after(ts["cert"])
                days = (int((na - time.time()) / 86400) if na else None)
                log("TLS cert loaded: %s (expires in %s days)"
                    % (ts["cert"], days if days is not None else "?"))
            except Exception:
                log("TLS context build FAILED:\n%s" % traceback.format_exc())
        else:
            log("WARNING: TLS enabled but cert/key missing (%s / %s); "
                "start legacy HTTP only" % (ts["cert"], ts["key"]))

    servers = []  # (httpd, 描述)

    def scoped(scope):
        return type("Handler_" + str(scope), (Handler,), {"scope": scope})

    if ts["legacy_enabled"]:
        httpd = ThreadingHTTPServer(("0.0.0.0", ts["legacy_port"]), Handler)
        httpd.daemon_threads = True
        servers.append((httpd, "legacy-http:%d (full routes)" % ts["legacy_port"]))
    if tls_ctx is not None:
        for scope, port_key in (("terminal", "terminal_port"),
                                ("console", "console_port")):
            httpd = ThreadingHTTPServer(("0.0.0.0", ts[port_key]),
                                        scoped(scope))
            httpd.daemon_threads = True
            httpd.socket = tls_ctx.wrap_socket(httpd.socket, server_side=True)
            servers.append((httpd, "tls-%s:%d" % (scope, ts[port_key])))
    # 公开客户端下载口（HTTP，默认 80）：无需 TLS——安装包分发本身是公开
    # 信息，直接浏览器打开 http://<host>/ 即可下载，降低终端用户使用门槛
    dl_port = int(ts.get("download_port") or 0)
    if dl_port:
        httpd = ThreadingHTTPServer(("0.0.0.0", dl_port), scoped("download"))
        httpd.daemon_threads = True
        servers.append((httpd, "download-http:%d (public)" % dl_port))
    if not servers:
        die_no_listener()

    def shutdown(signum, frame):
        log("signal %s received, shutting down..." % signum)
        stop_event.set()
        for httpd, _ in servers:
            threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    log("EyeTerm Server v%s starting listeners:" % APP_VERSION)
    for _, desc in servers:
        log("  listening: 0.0.0.0:%s" % desc)
    try:
        threads = []
        for httpd, _ in servers:
            t = threading.Thread(target=httpd.serve_forever,
                                 kwargs={"poll_interval": 0.5})
            t.daemon = False
            t.start()
            threads.append(t)
        while any(t.is_alive() for t in threads):
            time.sleep(0.5)
    finally:
        stop_event.set()
        ctx.huorong.stop()
        for httpd, _ in servers:
            httpd.server_close()
        store.close()
        log("server stopped")


def die_no_listener():
    log("FATAL: no listener configured (TLS disabled/failed AND legacy_http disabled)")
    sys.exit(1)


if __name__ == "__main__":
    main()
