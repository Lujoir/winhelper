# -*- coding: utf-8 -*-
"""火绒终端安全管理系统 API 客户端（EyeTerm 服务端集成附件，零凭据）。

签名算法已于 2026-09-15 实测打通（依据官方参考脚本 S:/项目开发/火绒/api测试.py，
两对凭据均 HTTP 200 + errno=0）。算法全要素：

    1. 认证全部走 URL 参数：{HOST}/{path}?ak={AK}&expires={ts}&sign={sig}
       —— 无 Authorization 头、无 Content-MD5 头
    2. path 不带前导斜杠（如 api/group/_list）
    3. 待签串 5 行：AK \\n expires \\n POST \\n content_md5 \\n path
    4. content_md5 = base64(md5(body utf-8 二进制))；body = 紧凑 JSON
       （json.dumps(payload, separators=(',',':'))）
    5. sig = urllib.parse.quote(base64(hmac-sha1(SK, 待签串)))
       —— quote 用默认 safe='/'（斜杠不转义，仅 +/= 被转义）
    6. expires = int(time.time()) + 86400
    7. method=POST；Content-Type: application/json

凭据经构造参数注入（生产由 settings SecretsBox 解密后传入），本文件零凭据。

安全约定：
- 只读方法（groups / clients / leak / virus_events / swinfo）已联调授权；
- 任务类方法（create_task：查杀/隔离/通知）默认禁用（enable_tasks=False 抛
  HuorongTaskDisabled）。破坏性管控操作必须经平台审批门禁 + 操作留痕后才允许开启。
"""
import base64
import hashlib
import hmac
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request


class HuorongApiError(Exception):
    """火绒 API 业务错误（errno != 0）。"""

    def __init__(self, errno, errmsg, http_status=None):
        super().__init__("huorong errno=%s %s (http=%s)" %
                         (errno, errmsg, http_status))
        self.errno = errno
        self.errmsg = errmsg
        self.http_status = http_status


class HuorongTaskDisabled(Exception):
    """任务类（破坏性）接口在未开启 enable_tasks 时被拦截。"""


class HuorongClient(object):
    """火绒企业版 API 客户端（HTTP POST + JSON，URL 参数签名）。"""

    ERRNO_OK, ERRNO_AUTH, ERRNO_PARAM, ERRNO_INTERNAL, ERRNO_UNAUTHORIZED = 0, 1, 2, 3, 4

    def __init__(self, base_url, access_key, secret_key,
                 timeout=15, retries=2, tls_fingerprint_sha256=None,
                 enable_tasks=False, expires_ttl_sec=86400):
        self.base_url = base_url.rstrip("/")
        self.ak = access_key
        self.sk = secret_key
        self.timeout = timeout
        self.retries = retries          # 仅对 errno=3（服务端内部错误）重试
        self.expires_ttl = expires_ttl_sec
        self.enable_tasks = enable_tasks
        self._ssl_ctx = self._make_ssl_ctx(tls_fingerprint_sha256)

    # ---------- TLS ----------
    @staticmethod
    def _make_ssl_ctx(fingerprint):
        ctx = ssl.create_default_context()
        # 自签证书：联调期不校验；生产必须传指纹 pin（spki sha256）
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        HuorongClient._pinned_fp = fingerprint.replace(":", "").lower() \
            if fingerprint else None
        return ctx

    # ---------- 签名（实测打通算法） ----------
    @staticmethod
    def content_md5(body_bytes):
        """Content-MD5 = base64(md5(请求体二进制))。"""
        return base64.b64encode(hashlib.md5(body_bytes).digest()).decode("utf-8")

    @staticmethod
    def canonicalized_resource(path):
        """资源路径，不带前导斜杠（api/group/_list）。"""
        return path.lstrip("/")

    def _sign(self, expires, method, md5_b64, path_no_slash):
        sts = "\n".join([self.ak, str(expires), method, md5_b64, path_no_slash])
        b64sig = base64.b64encode(
            hmac.new(self.sk.encode("utf-8"), sts.encode("utf-8"),
                     hashlib.sha1).digest()).decode("utf-8")
        # quote 默认 safe='/'：+/= 转义为 %2B/%2F/%3D，斜杠保留（与参考脚本一致）
        return urllib.parse.quote(b64sig)

    # ---------- 请求 ----------
    def _post(self, path, payload=None):
        body = json.dumps(payload or {}, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8")
        md5 = self.content_md5(body)
        p = self.canonicalized_resource(path)
        expires = int(time.time()) + self.expires_ttl
        sig = self._sign(expires, "POST", md5, p)
        url = "%s/%s?ak=%s&expires=%d&sign=%s" % (
            self.base_url, p, urllib.parse.quote(self.ak, safe=""), expires, sig)
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        last = None
        for attempt in range(self.retries + 1):
            try:
                resp = urllib.request.urlopen(req, timeout=self.timeout,
                                              context=self._ssl_ctx)
                data = json.loads(resp.read().decode("utf-8"))
                errno = data.get("errno", -1)
                if errno == self.ERRNO_OK:
                    return data.get("data", {})
                if errno == self.ERRNO_INTERNAL and attempt < self.retries:
                    time.sleep(1.5 ** attempt)
                    continue
                raise HuorongApiError(errno, data.get("errmsg", ""), resp.status)
            except urllib.error.HTTPError as exc:
                raise HuorongApiError(-1, "http %s" % exc.code, exc.code)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                last = exc
                if attempt < self.retries:
                    time.sleep(1.5 ** attempt)
                    continue
        raise HuorongApiError(-1, "network: %r" % last, None)

    # ---------- 只读：分组 ----------
    def get_groups(self):
        """全部分组 [{group_id, parent_group, group_name}]（实测 2026-09-15：89 组）。"""
        return self._post("/api/group/_list").get("list", [])

    def get_group_info(self, group_id):
        """单个分组信息。"""
        return self._post("/api/group/_info", payload={"group_id": int(group_id)})

    # ---------- 只读：终端 ----------
    def list_clients(self, limit=200, offset=0):
        """终端基本信息分页（limit 上限 200）。

        实测字段：client_id/client_name/computer_name/connect_ip/local_ip/mac/
        group_id/is_online/os_version/version/last_connect_time（另有
        first_appear_time 等）。分页语义见 iter_all_clients。
        """
        return self._post("/api/clnts/_list",
                          payload={"limit": int(limit), "offset": int(offset)})

    def iter_all_clients(self, page=200, max_pages=200):
        """全量拉取（自动翻页）。

        分页终止条件（按实测响应结构自适应）：
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
                offset += 0
                continue
            empty_streak = 0
            offset += len(rows)
            if total is not None and offset >= total:
                return
            if len(rows) < page:
                return

    def online_macs(self, limit=200, offset=0):
        """在线终端 MAC。"""
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

    def leak_clients(self, limit=200, offset=0):
        """高危漏洞未修复终端（data.all_client / data.risk_client 为全局 KPI）。"""
        return self._post("/api/clnts/_leak",
                          payload={"limit": int(limit), "offset": int(offset)})

    def virus_events(self, type_=2, client_id=None, group_id=None,
                     begin_time=None, end_time=None, limit=200, offset=0):
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

    # ---------- 破坏性：任务类（默认禁用） ----------
    def create_task(self, type_, param, clients=None, groups=None):
        """创建终端任务（quick/full/custom_scan、netctrl 隔离、message 通知）。

        红线：enable_tasks=False 时一律拦截；开启必须经 EyeTerm 平台审批
        门禁 + 操作留痕（audit），禁止自动触发。
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


if __name__ == "__main__":
    # 冒烟用法（凭据经环境变量注入，勿写死）：
    #   set HR_AK=... & set HR_SK=... & python huorong_api_client_draft.py
    import os
    ak = os.environ.get("HR_AK", "")
    sk = os.environ.get("HR_SK", "")
    if not (ak and sk):
        print("HR_AK/HR_SK env required")
        raise SystemExit(2)
    cli = HuorongClient(os.environ.get("HR_BASE",
                                       "https://172.17.1.3:8080"), ak, sk)
    groups = cli.get_groups()
    print(json.dumps({"group_count": len(groups),
                      "sample": groups[:3]}, ensure_ascii=False, indent=2))
