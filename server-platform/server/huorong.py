# -*- coding: utf-8 -*-
"""观枢终端平台服务端 · 火绒终端安全管理系统集成（只读镜像 + 同步器，ADR-033）。

组成：
- HuorongClient：火绒企业版 API 客户端（HTTP POST + JSON，URL 参数签名）。
  签名算法 2026-09-15 以官方参考脚本实测打通（两对凭据 HTTP 200 + errno=0，
  详见 docs/huorong_integration_plan.md §5.3/§7.1）。全要素：
    1. 认证全部走 URL 参数 ?ak=&expires=&sign= —— 无 Authorization/Content-MD5 头
    2. CanonicalizedResource 不带前导斜杠（api/group/_list）
    3. 待签串 5 行：AK \\n expires \\n POST \\n content_md5 \\n resource
    4. content_md5 = base64(md5(body))；body = 紧凑 JSON separators=(',',':')
    5. sign = urllib.parse.quote(base64(hmac-sha1(SK, 待签串)))，quote 默认 safe='/'
    6. expires = int(time.time()) + 86400
- HuorongSyncer：周期同步器（进程内线程 + 失败指数退避 + 互斥锁防重入），
  分组/终端全量镜像进 hr_* 缓存表；控制台只读缓存，绝不透传实时请求。

安全红线：
- 凭据（huorong.ak / huorong.sk）经 settings SecretsBox 加密落库，零硬编码；
- 任务类破坏性接口（查杀/隔离/通知）代码级硬门禁：enable_tasks=False（默认）
  时 create_task 一律抛 HuorongTaskDisabled；本模块不提供任何任务下发路由。
"""
import base64
import hashlib
import hmac
import json
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_PAGE = 200          # 官方 limit 上限 200
DEFAULT_EXPIRES_TTL = 86400
HR_ASSETS_BATCH = 50        # _info2 批量 client_id 上限（保守分批）


class HuorongApiError(Exception):
    """火绒 API 业务错误（errno != 0 / HTTP 层异常）。

    errno 语义：0=成功 1=认证失败 2=参数错误 3=服务端内部 4=API 未授权；
    约定值：-1=HTTP/网络层、-2=TLS 指纹不符。
    """

    def __init__(self, errno, errmsg, http_status=None):
        Exception.__init__(self, "huorong errno=%s %s (http=%s)"
                           % (errno, errmsg, http_status))
        self.errno = errno
        self.errmsg = errmsg
        self.http_status = http_status


class HuorongTaskDisabled(Exception):
    """任务类（破坏性）接口在 enable_tasks=False 时被硬门禁拦截。"""


class HuorongNotConfigured(Exception):
    """settings 未配置 huorong.url/ak/sk，无法构造客户端。"""


class HuorongClient(object):
    """火绒企业版 API 客户端（只读方法联调授权；任务方法默认硬门禁）。"""

    ERRNO_OK, ERRNO_AUTH, ERRNO_PARAM, ERRNO_INTERNAL, ERRNO_UNAUTHORIZED = \
        0, 1, 2, 3, 4

    def __init__(self, base_url, access_key, secret_key,
                 timeout=15, retries=2, tls_fingerprint_sha256=None,
                 enable_tasks=False, expires_ttl_sec=DEFAULT_EXPIRES_TTL,
                 sleep=time.sleep):
        self.base_url = base_url.rstrip("/")
        self.ak = access_key
        self.sk = secret_key
        self.timeout = timeout
        self.retries = retries              # 仅 errno=3 与 HTTP 5xx / 网络类重试
        self.expires_ttl = expires_ttl_sec
        self.enable_tasks = enable_tasks
        self._sleep = sleep
        self._pinned_fp = self._norm_fp(tls_fingerprint_sha256)
        self._ssl_ctx = self._make_ssl_ctx()

    # ---------- TLS ----------
    @staticmethod
    def _norm_fp(fp):
        """指纹归一：去冒号/空白 + 小写（SHA-256 hex）。"""
        if not fp:
            return None
        return "".join(ch for ch in str(fp) if ch not in ": ").lower() or None

    def _make_ssl_ctx(self):
        ctx = ssl.create_default_context()
        # 火绒控制台为 HTTPS 自签：默认不校验（仅隔离内网可接受）。
        # 配置 huorong.tls_fingerprint 后由 _check_pin 对端指纹 pin（fail-closed），
        # 生产启用路径见 ADR-033（SPKI pin 可在后续版本增强）。
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _peer_cert_sha256(self, resp):
        """对端证书 DER SHA-256（hex）；取不到返回 None。"""
        try:
            sock = resp.fp.raw._sock          # http.client 响应 → SSLSocket
            der = sock.getpeercert(binary_form=True)
        except Exception:
            return None
        if not der:
            return None
        return hashlib.sha256(der).hexdigest()

    def _check_pin(self, resp):
        if not self._pinned_fp:
            return
        fp = self._peer_cert_sha256(resp)
        if fp != self._pinned_fp:
            raise HuorongApiError(-2, "tls fingerprint mismatch")

    # ---------- 签名（实测打通算法，金向量见 tools/test_huorong.py） ----------
    @staticmethod
    def content_md5(body_bytes):
        """Content-MD5 = base64(md5(请求体二进制))。"""
        return base64.b64encode(hashlib.md5(body_bytes).digest()).decode("utf-8")

    @staticmethod
    def canonicalized_resource(path):
        """资源路径不带前导斜杠（api/group/_list）。"""
        return (path or "").lstrip("/")

    def _sign(self, expires, method, md5_b64, path_no_slash):
        sts = "\n".join([self.ak, str(expires), method, md5_b64, path_no_slash])
        b64sig = base64.b64encode(
            hmac.new(self.sk.encode("utf-8"), sts.encode("utf-8"),
                     hashlib.sha1).digest()).decode("utf-8")
        # quote 默认 safe='/'：+/= 转义为 %2B/%2F/%3D，斜杠保留（与官方脚本一致）
        return urllib.parse.quote(b64sig)

    def build_signed_url(self, path, body_bytes, expires=None):
        """构造带签名的完整 URL（独立出来便于金向量测试与审计）。"""
        p = self.canonicalized_resource(path)
        expires = int(expires if expires is not None
                      else time.time() + self.expires_ttl)
        sig = self._sign(expires, "POST", self.content_md5(body_bytes), p)
        return "%s/%s?ak=%s&expires=%d&sign=%s" % (
            self.base_url, p, urllib.parse.quote(self.ak, safe=""), expires, sig)

    # ---------- 请求（errno 归一化 + 重试 + 指纹 pin） ----------
    def _retry_wait(self, attempt):
        return min(1.0 * (2 ** attempt), 5.0)

    def _post(self, path, payload=None):
        body = json.dumps(payload or {}, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8")
        url = self.build_signed_url(path, body)
        last = None
        for attempt in range(self.retries + 1):
            try:
                req = urllib.request.Request(url, data=body, method="POST")
                req.add_header("Content-Type", "application/json")
                resp = urllib.request.urlopen(req, timeout=self.timeout,
                                              context=self._ssl_ctx)
                try:
                    self._check_pin(resp)
                    data = json.loads(resp.read().decode("utf-8"))
                finally:
                    try:
                        resp.close()
                    except Exception:
                        pass
                errno = data.get("errno", -1) if isinstance(data, dict) else -1
                if errno == self.ERRNO_OK:
                    return data.get("data", {})
                if errno == self.ERRNO_INTERNAL and attempt < self.retries:
                    self._sleep(self._retry_wait(attempt))
                    continue
                raise HuorongApiError(
                    errno, str(data.get("errmsg", "")) if isinstance(data, dict)
                    else "", getattr(resp, "status", None))
            except HuorongApiError:
                raise
            except urllib.error.HTTPError as exc:
                try:
                    exc.close()
                except Exception:
                    pass
                if exc.code >= 500 and attempt < self.retries:
                    last = exc
                    self._sleep(self._retry_wait(attempt))
                    continue
                raise HuorongApiError(-1, "http %s" % exc.code, exc.code)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                last = exc
                if attempt < self.retries:
                    self._sleep(self._retry_wait(attempt))
                    continue
        raise HuorongApiError(-1, "network: %r" % last, None)

    # ---------- 只读：分组 ----------
    def get_groups(self):
        """全部分组 [{group_id, parent_group, group_name}]（实测 89 组）。"""
        return self._post("/api/group/_list").get("list", [])

    def get_group_info(self, group_id):
        return self._post("/api/group/_info", payload={"group_id": int(group_id)})

    # ---------- 只读：终端 ----------
    def list_clients(self, limit=DEFAULT_PAGE, offset=0):
        """终端基本信息分页（limit 官方上限 200，越界自动收敛）。"""
        limit = max(1, min(int(limit), DEFAULT_PAGE))
        return self._post("/api/clnts/_list",
                          payload={"limit": int(limit), "offset": int(offset)})

    def iter_all_clients(self, page=DEFAULT_PAGE, max_pages=200):
        """全量拉取（自动翻页）。

        分页终止条件（实测响应结构 data.list / data.total，自适应防御）：
        1. data.total 存在 → offset >= total 停；
        2. 无 total → 短页（返回条数 < page）停；
        3. 防御：连续空页停、max_pages 硬上限。
        """
        offset, total, empty_streak = 0, None, 0
        for _ in range(max_pages):
            data = self.list_clients(limit=page, offset=offset)
            rows = data.get("list", []) if isinstance(data, dict) else []
            if isinstance(data, dict) and "total" in data:
                total = data.get("total")
            for row in rows:
                yield row, {"total": total, "offset": offset}
            if not rows:
                empty_streak += 1
                if empty_streak >= 2:
                    return
                continue
            empty_streak = 0
            offset += len(rows)
            if total is not None and offset >= total:
                return
            if len(rows) < page:
                return

    def online_macs(self, limit=DEFAULT_PAGE, offset=0):
        return self._post("/api/clnts/_online",
                          payload={"limit": int(limit), "offset": int(offset)})

    def client_info2(self, clients=None, mac=None, options=None):
        """终端详情 v2（v2.0.6.0+，options: hardware/software/assets/netconf）。"""
        payload = {}
        if clients:
            payload["clients"] = list(clients)
        if mac:
            payload["mac"] = list(mac)
        if options:
            payload["options"] = list(options)
        return self._post("/api/clnts/_info2", payload=payload).get("list", [])

    def leak_clients(self, limit=DEFAULT_PAGE, offset=0):
        """高危漏洞未修复终端（data.all_client / data.risk_client 为全局 KPI）。"""
        return self._post("/api/clnts/_leak",
                          payload={"limit": int(limit), "offset": int(offset)})

    def virus_events(self, type_=2, client_id=None, group_id=None,
                     begin_time=None, end_time=None, limit=DEFAULT_PAGE,
                     offset=0):
        """病毒事件统计。type: 0=按 client_id, 1=按 group_id, 2=全量。"""
        payload = {"type": int(type_), "limit": int(limit), "offset": int(offset)}
        if client_id:
            payload["client_id"] = client_id
        if group_id:
            payload["group_id"] = int(group_id)
        if begin_time:
            payload["begin_time"] = int(begin_time)
        if end_time:
            payload["end_time"] = int(end_time)
        return self._post("/api/clnts/_virus_events", payload=payload)

    def swinfo_search(self, groupby="software.list", ostype="Windows",
                      begin=0, count=100, fuzzy=""):
        """软件统计。groupby: software.list / softwareVer.list / client.list。"""
        return self._post("/api/swinfo/_search", payload={
            "view": {"begin": int(begin), "count": int(count)},
            "fuzzy_query": fuzzy or "",
            "groupby": groupby,
            "ostype": ostype,
        })

    def iter_virus_stats(self, page=DEFAULT_PAGE, max_pages=200):
        """全量病毒事件统计（type=2，每终端 count+处理结果分布；分页同
        iter_all_clients 语义）。官方接口为统计口径，无明细流。"""
        offset, total = 0, None
        for _ in range(max_pages):
            data = self.virus_events(type_=2, limit=page, offset=offset)
            rows = data.get("list", []) if isinstance(data, dict) else []
            if isinstance(data, dict) and "total" in data:
                total = data.get("total")
            for row in rows:
                yield row
            if not rows:
                return
            offset += len(rows)
            if total is not None and offset >= total:
                return
            if len(rows) < page:
                return

    # ---------- 破坏性：任务类（默认硬门禁，ADR-033 红线） ----------
    def create_task(self, type_, param, clients=None, groups=None):
        """创建终端任务（quick/full/custom_scan、netctrl 隔离、message 通知）。

        红线：enable_tasks=False（默认）时一律拦截。未来开启（S6）必须：
        main/用户审批 + 控制台会话鉴权 + 目标白名单确认 + 操作留痕审计，
        禁止任何自动/定时触发；本模块不提供任务下发路由。
        """
        if not self.enable_tasks:
            raise HuorongTaskDisabled(
                "task endpoints are gated; destructive ops require approval")
        payload = {"type": type_, "param": param}
        if clients:
            payload["clients"] = list(clients)
        if groups:
            payload["groups"] = [int(g) for g in groups]
        return self._post("/api/task/_create", payload=payload)


# ----------------------------------------------------------------------
# 镜像行归一（syncer 与单测共用）
# ----------------------------------------------------------------------

def _as_int(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_ts(value):
    """last_connect_time 兼容解析：epoch 数字 / 'YYYY-MM-DD HH:MM:SS' 字符串。"""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    if s.isdigit():
        return int(s)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return int(time.mktime(time.strptime(s, fmt)))
        except ValueError:
            continue
    return None


def _as_online(value):
    return 1 if str(value).strip().lower() in ("1", "true") else 0


def virus_row(row, now=None):
    """病毒统计行归一（字段名宽容解析；真实响应键名以生产首轮校准，
    ADR-033 已知限制）。"""
    if not isinstance(row, dict):
        return None
    def pick(*keys):
        for k in keys:
            if k in row and row[k] not in (None, ""):
                return row[k]
        return 0
    cid = str(pick("client_id", "clientid", "clientId") or "").strip()
    if not cid:
        return None
    def num(*keys):
        try:
            return int(pick(*keys))
        except (TypeError, ValueError):
            return 0
    return {"client_id": cid,
            "total": num("count", "total", "event_count"),
            "success": num("success", "handled_success"),
            "fail": num("fail", "handled_fail"),
            "ignored": num("ignored", "handle_ignored"),
            "trusted": num("trusted"),
            "snapshot_ts": int(now or time.time())}


def assets_row(raw_assets):
    """终端登记信息归一（/api/clnts/_info2 options=["assets"]，v2.0.6.0+）。

    官方响应为 name/value 键值对数组（文档 §3.2.5），登记字段名由火绒
    控制台管理员配置，**无固定 API 字段名**——EXT 纪律：不猜测字段名，
    归一为 {name: value} dict 全量保留原始键，展示层按截图实证白名单
    匹配（楼层/具体位置/工号/姓名/使用科室）。空 name / None / 空串
    值丢弃；非数组输入返回 {}。
    """
    if not isinstance(raw_assets, list):
        return {}
    out = {}
    for item in raw_assets:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        val = item.get("value")
        if val is None:
            continue
        val = str(val).strip()
        if not val:
            continue
        out[name] = val
    return out


def mirror_rows(raw_groups, raw_clients, now):
    """火绒原始分组/终端 → hr_* 表行（分组 total/online 快照由终端聚合）。"""
    groups = []
    for g in raw_groups or []:
        gid = _as_int(g.get("group_id")) if isinstance(g, dict) else None
        if gid is None:
            continue
        groups.append({"group_id": gid,
                       "name": str(g.get("group_name") or ""),
                       "parent": _as_int(g.get("parent_group")),
                       "total": 0, "online": 0, "updated_at": now})
    gmap = dict((g["group_id"], g) for g in groups)
    clients = []
    for c in raw_clients or []:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("client_id") or "").strip()
        if not cid:
            continue
        gid = _as_int(c.get("group_id"))
        online = _as_online(c.get("is_online"))
        clients.append({
            "client_id": cid,
            "name": str(c.get("client_name") or ""),
            "computer_name": str(c.get("computer_name") or ""),
            "ip": str(c.get("local_ip") or ""),       # local_ip 为主（试点结论）
            "connect_ip": str(c.get("connect_ip") or ""),
            "mac": str(c.get("mac") or ""),
            "group_id": gid,
            "native_group_id": gid,   # 火绒原生分组；group_id 可被覆盖生效
            "online": online,
            "os": str(c.get("os_version") or ""),
            "version": str(c.get("version") or ""),
            "last_seen": _parse_ts(c.get("last_connect_time")),
            "first_seen": _parse_ts(c.get("first_appear_time")),
            "last_off": _parse_ts(c.get("last_off_time")),
            "this_on": _parse_ts(c.get("this_on_time")),
            "updated_at": now,
        })
        g = gmap.get(gid)
        if g is not None:
            g["total"] += 1
            g["online"] += online
    return groups, clients


# ----------------------------------------------------------------------
# 同步器（周期线程 + 互斥 + 退避）
# ----------------------------------------------------------------------

class HuorongSyncer(object):
    """火绒镜像同步器：300s 周期（可配）+ 手动触发共用互斥锁。

    - 失败指数退避：interval × 2^连续失败次数，上限 3600s；
    - 认证失败（errno=1）连续 3 次后暂停周期同步（防凭据锁死），
      手动同步成功后自动恢复；
    - 控制台只读缓存表，本类之外不发起任何火绒请求。
    """

    MAX_BACKOFF_SEC = 3600
    AUTH_FAIL_DISABLE = 3

    def __init__(self, store, settings, log=None, client_factory=None,
                 sleep=time.sleep):
        self._store = store
        self._settings = settings
        self._log = log or (lambda msg: None)
        self._client_factory = client_factory
        self._sleep = sleep
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._backoff = 0                  # 连续失败档位（指数退避）
        self._auth_fail_streak = 0
        self._scheduled_disabled = False

    # ---- 配置 ----
    def _conf(self):
        if self._settings is None:
            return {"url": "", "ak": "", "sk": "", "enabled": False,
                    "interval": 300, "fp": ""}
        s = self._settings
        try:
            interval = int(s.get("huorong.sync_interval_sec") or 300)
        except (TypeError, ValueError):
            interval = 300
        return {
            "url": (s.get("huorong.url") or "").strip(),
            "ak": s.get("huorong.ak") or "",
            "sk": s.get("huorong.sk") or "",
            "enabled": str(s.get("huorong.enabled") or "0").strip() == "1",
            "interval": max(60, min(interval, 3600)),
            "fp": (s.get("huorong.tls_fingerprint") or "").strip(),
        }

    def configured(self):
        if self._client_factory is not None:   # 测试注入路径视为已配置
            return True
        c = self._conf()
        return bool(c["url"] and c["ak"] and c["sk"])

    def _make_client(self):
        if self._client_factory is not None:
            return self._client_factory()
        c = self._conf()
        if not (c["url"] and c["ak"] and c["sk"]):
            raise HuorongNotConfigured(
                "huorong.url/ak/sk not configured (settings)")
        return HuorongClient(c["url"], c["ak"], c["sk"],
                             tls_fingerprint_sha256=c["fp"] or None)

    # ---- 同步 ----
    def sync_once(self, trigger="manual"):
        """单轮同步：分组 + 终端全量镜像（互斥，非阻塞获锁）。

        返回 {ok, groups, clients, duration_ms, trigger} 或
        {ok: False, busy: True, error} / {ok: False, error}。
        本方法不抛异常（异常归一为 ok=False 结果）。
        """
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "busy": True, "error": "sync in progress",
                    "trigger": trigger}
        started_ts = int(time.time())
        mono0 = time.time()
        groups_n = clients_n = 0
        matched_n = 0
        error = ""
        ok = False
        record = False
        try:
            try:
                client = self._make_client()
            except HuorongNotConfigured as exc:
                return {"ok": False, "error": str(exc), "trigger": trigger,
                        "groups": 0, "clients": 0, "duration_ms": 0}
            record = True
            try:
                raw_groups = client.get_groups()
                raw_clients = [row for row, _meta
                               in client.iter_all_clients(page=DEFAULT_PAGE)]
            except HuorongApiError as exc:
                error = "huorong errno=%s %s" % (exc.errno, exc.errmsg)
                self._on_sync_error(exc)
                return {"ok": False, "error": error, "trigger": trigger,
                        "groups": 0, "clients": 0,
                        "duration_ms": int((time.time() - mono0) * 1000)}
            except Exception as exc:                      # 兜底：不留半程写库
                error = "sync: %r" % (exc,)
                self._backoff = min(self._backoff + 1, 4)
                return {"ok": False, "error": error, "trigger": trigger,
                        "groups": 0, "clients": 0,
                        "duration_ms": int((time.time() - mono0) * 1000)}
            group_rows, client_rows = mirror_rows(
                raw_groups, raw_clients, started_ts)
            groups_n, clients_n = len(group_rows), len(client_rows)
            self._store.hr_replace_snapshot(group_rows, client_rows)
            override_n = self._store.hr_apply_overrides()
            relink = self._store.hr_relink(now=started_ts)
            matched_n = int(relink.get("total") or 0)
            ok = True
            self._backoff = 0
            self._auth_fail_streak = 0
            if self._scheduled_disabled:
                self._log("huorong sync recovered; scheduled sync resumed")
            self._scheduled_disabled = False
            self._log("huorong relink: mac=%s ip=%s hostname=%s manual=%s"
                      % (relink.get("mac"), relink.get("ip"),
                         relink.get("hostname"), relink.get("manual")))
            # 病毒统计镜像（type=2 全量；失败不阻断主同步，ADR-033 增补）
            virus_state = "ok"
            try:
                vrows = [v for v in (virus_row(r, started_ts)
                                     for r in client.iter_virus_stats(
                                         page=DEFAULT_PAGE))
                         if v is not None]
                self._store.hr_virus_replace_snapshot(vrows, started_ts)
                self._log("huorong virus stats mirrored: %d clients"
                          % len(vrows))
            except Exception as exc:
                virus_state = "failed: %r" % (exc,)
                self._log("huorong virus stats sync failed: %r" % (exc,))
            # 终端登记信息镜像（_info2 options=["assets"] 分批全量；失败
            # 不阻断主同步，同病毒统计模式，ADR-033 增补三）
            assets_state = "ok"
            try:
                assets_map = {}
                ids = [c["client_id"] for c in client_rows]
                for i in range(0, len(ids), HR_ASSETS_BATCH):
                    for item in client.client_info2(
                            clients=ids[i:i + HR_ASSETS_BATCH],
                            options=["assets"]):
                        if not isinstance(item, dict):
                            continue
                        cid2 = str(item.get("client_id") or "").strip()
                        if not cid2:
                            continue
                        amap = assets_row(item.get("assets"))
                        if amap:
                            assets_map[cid2] = amap
                self._store.hr_assets_replace(assets_map, started_ts)
                self._log("huorong assets mirrored: %d clients"
                          % len(assets_map))
            except Exception as exc:
                assets_state = "failed: %r" % (exc,)
                self._log("huorong assets sync failed: %r" % (exc,))
            return {"ok": True, "groups": groups_n, "clients": clients_n,
                    "matched": matched_n, "override_applied": override_n,
                    "relink": relink, "virus_sync": virus_state,
                    "assets_sync": assets_state,
                    "duration_ms": int((time.time() - mono0) * 1000),
                    "trigger": trigger}
        finally:
            if record:
                try:
                    self._store.hr_sync_log_add(
                        started_ts, int(time.time()), 1 if ok else 0, trigger,
                        groups_n, clients_n, matched_n, error)
                except Exception:
                    pass
            self._lock.release()

    def _on_sync_error(self, exc):
        if exc.errno == HuorongClient.ERRNO_AUTH:
            self._auth_fail_streak += 1
            if self._auth_fail_streak >= self.AUTH_FAIL_DISABLE \
                    and not self._scheduled_disabled:
                self._scheduled_disabled = True
                self._log("huorong auth failed %d times; scheduled sync "
                          "disabled until manual recovery"
                          % self._auth_fail_streak)
        else:
            self._backoff = min(self._backoff + 1, 4)

    # ---- 周期线程 ----
    def _next_delay(self, conf):
        base = conf["interval"]
        return min(int(base * (2 ** self._backoff)), self.MAX_BACKOFF_SEC)

    def _loop(self):
        if self._stop.wait(8):                # 启动短延迟，避开主服务装配争抢
            return
        while not self._stop.is_set():
            conf = self._conf()
            if conf["enabled"] and not self._scheduled_disabled \
                    and self.configured():
                try:
                    result = self.sync_once(trigger="scheduled")
                    if result.get("ok"):
                        self._log("huorong sync ok: groups=%s clients=%s"
                                  % (result.get("groups"),
                                     result.get("clients")))
                    elif not result.get("busy"):
                        self._log("huorong sync failed: %s"
                                  % result.get("error"))
                except Exception as exc:      # 双保险：线程永不带异常退出
                    self._log("huorong sync unexpected: %r" % (exc,))
            if self._stop.wait(self._next_delay(conf)):
                return

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="huorong-sync")
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        self._stop.set()
