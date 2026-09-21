# -*- coding: utf-8 -*-
"""画方准入系统 HTTP API 客户端（ADR-024，IP 冲突检测准入数据源）。

凭据来源：系统管理·第三方接口登记（third_party_apis id=2）——
params_json 含 app_key/app_secret/enctype/version，base_url 为准入平台地址，
enabled=false 或删除即视为未配置（降级不阻断判定）。

接口（v2.0，自签证书 verify=False）：
    POST {base_url}/httpapi/term/get
    body: {appkey, sign, time, nonce, enctype:0, version:"v2.0",
           data:{where:{}, curpage:1, limit:10000}}
    sign = hmac_sha256_hex(appsecret, "appkey={appkey}&nonce={nonce}&time={time}")
    响应：{errno:0, errmsg, data:{total, list:[...]}}

性能：全量单页 ~1MB（实测 1588 台），ipconflict 为低频手动触发——可接受；
模块级 60s 结果缓存防连点。纯标准库（urllib+ssl+hashlib+hmac+random）。"""
import hashlib
import hmac
import json
import random
import ssl
import threading
import time
import urllib.error
import urllib.request

NAD_CACHE_TTL = 300         # 结果缓存秒数（2026-09-19：60→300。画方 1588 台
                            # 需翻 2 页，每次全量拉取代价高；资产定位/第三方
                            # 弹窗等读路径高频复用同一份缓存）
NAD_HTTP_TIMEOUT = 8        # 单请求超时（2026-09-19：10→8）。读路径已改为
                            # stale-while-revalidate（见 nad_terminals_cached），
                            # 请求不再等待网络，故此处只需保证后台刷新自身
                            # 不会长期挂死：2 页最坏 16s，发生在后台线程。
NAD_PAGE_LIMIT = 1000       # 实测单页上限（1588 台需 2 页）
NAD_MAX_PAGES = 50          # 翻页防御上限
NAD_CONFIG_ID = 2           # 第三方接口登记 id（画方准入）

_cache = {"ts": 0.0, "terms": None, "reason": None}


def nad_load_config(store):
    """从第三方接口表 id=2 读取画方准入凭据；缺失/禁用/参数不全返回 None。"""
    try:
        row = store.third_party_get(NAD_CONFIG_ID)
    except Exception:
        return None
    if not row or not row.get("enabled"):
        return None
    try:
        params = json.loads(row.get("params_json") or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(params, dict):
        return None
    app_key = str(params.get("app_key") or "")
    app_secret = str(params.get("app_secret") or "")
    if not app_key or not app_secret:
        return None
    try:
        enctype = int(params.get("enctype") or 0)
    except (TypeError, ValueError):
        enctype = 0
    return {
        "base_url": str(row.get("base_url") or "").rstrip("/"),
        "app_key": app_key,
        "app_secret": app_secret,
        "enctype": enctype,
        "version": str(params.get("version") or "v2.0"),
    }


def nad_sign(app_secret, app_key, nonce, ts):
    """HMAC-SHA256 签名：msg = "appkey={}&nonce={}&time={}"（hex 小写）。"""
    msg = "appkey=%s&nonce=%s&time=%s" % (app_key, nonce, ts)
    return hmac.new(app_secret.encode("utf-8"), msg.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def _norm_page_list(data):
    """data.list 归一为 list（实测为 {"0": {...}} 形态 dict）。"""
    lst = data.get("list") or []
    if isinstance(lst, dict):
        lst = list(lst.values())
    return [t for t in lst if isinstance(t, dict)]


def _norm_ips(value):
    """ips 归一为 [{ip,ipver}] 列表（实测为 {"0": {...}} 形态 dict）。"""
    if isinstance(value, dict):
        value = list(value.values())
    return [i for i in value or [] if isinstance(i, dict)]


def _norm_macs(term):
    """macs 归一为 [{mac, ips:[...], macports:[...]}] 列表。

    实测形态：macs = {"0": {"mac": "AA:BB:..", "ips": {"0": {...}},
    "macports": null | {"0": {...}}}}——macports 与 ips 同为「数字键
    dict」形态（生产 2026-09-17 实证），归一为 list；list/null 原样。
    此前 macports 未归一导致 dict 迭代出键字符串被过滤、接入端口恒空。"""
    raw = term.get("macs") or {}
    if isinstance(raw, dict):
        raw = list(raw.values())
    entries = []
    for mi in raw:
        if not isinstance(mi, dict):
            continue
        ports = mi.get("macports") or []
        if isinstance(ports, dict):
            ports = list(ports.values())
        entries.append({
            "mac": mi.get("mac"),
            "ips": _norm_ips(mi.get("ips")),
            "macports": [p for p in ports if isinstance(p, dict)],
        })
    return entries


def nad_fetch_terms(cfg):
    """拉取准入终端全量（自动翻页：实测单页上限 1000，total=1588 需 2 页）。

    成功返回 list[term]；任何失败抛异常由调用方容错。"""
    terms = []
    curpage = 1
    while True:
        ts = str(int(time.time()))
        nonce = str(random.randint(1000000000, 9999999999))
        payload = {
            "appkey": cfg["app_key"],
            "sign": nad_sign(cfg["app_secret"], cfg["app_key"], nonce, ts),
            "time": ts,
            "nonce": nonce,
            "enctype": cfg.get("enctype", 0),
            "version": cfg.get("version", "v2.0"),
            "data": {"where": {}, "curpage": curpage,
                     "limit": NAD_PAGE_LIMIT},
        }
        req = urllib.request.Request(
            cfg["base_url"] + "/httpapi/term/get",
            data=json.dumps(payload).encode("utf-8"), method="POST")
        req.add_header("Content-Type", "application/json")
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE      # 自签证书（内网准入平台）
        with urllib.request.urlopen(req, timeout=NAD_HTTP_TIMEOUT,
                                    context=ssl_ctx) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if not isinstance(body, dict) or body.get("errno") != 0:
            errno = body.get("errno") if isinstance(body, dict) else "?"
            raise ValueError("nad errno=%s" % errno)
        data = body.get("data") or {}
        page = _norm_page_list(data)
        terms.extend(page)
        total = int(data.get("total") or 0)
        if not page or len(terms) >= total or curpage >= NAD_MAX_PAGES:
            break
        curpage += 1
    return terms


_refresh_lock = threading.Lock()
_refreshing = {"on": False}


def nad_prefetch_async(store):
    """后台预热画方缓存（单飞；供启动早期调用，避免首个读请求等网络）。

    失败静默：预热失败不影响任何既有路径（各读路径仍按各自语义同步拉取）。
    """
    with _refresh_lock:
        if _refreshing["on"]:
            return False
        _refreshing["on"] = True

    def _work():
        now = time.time()
        try:
            cfg = nad_load_config(store)
            if not cfg:
                if _cache["terms"] is None:
                    _cache.update(ts=now, terms=None, reason="not_configured")
                return
            terms = nad_fetch_terms(cfg)
            _cache.update(ts=now, terms=terms, reason=None)
        except Exception:                     # 兜底：预热失败一律静默
            pass
        finally:
            with _refresh_lock:
                _refreshing["on"] = False

    threading.Thread(target=_work, daemon=True, name="nad-prefetch").start()
    return True


def nad_terminals_cached(store, ttl=NAD_CACHE_TTL):
    """全量终端列表（带缓存）。返回 (terms|None, reason|None)；
    未配置 reason='not_configured'，失败 reason='error:...'。

    2026-09-19 调整：
    · 缓存 TTL 60→300s（画方 1588 台需翻 2 页，全量拉取代价高，读路径高频复用）；
    · **失败不覆盖已有好数据** —— 保留旧值继续服务。原实现失败即写空缓存，
      一次网络抖动就会让其后 5 分钟内所有读路径都拿不到画方数据。

    读路径的**超时隔离由调用方负责**：资产定位在 asset_locate._search_nad 用
    线程预算把画方耗时收口在 8s 内（那是唯一有 25s 前端预算约束的场景）；
    IP 冲突分析、第三方资产弹窗等仍按需同步拉取，语义不变。
    """
    now = time.time()
    if _cache["terms"] is not None and now - _cache["ts"] < ttl:
        return _cache["terms"], None
    cfg = nad_load_config(store)
    if not cfg:
        if _cache["terms"] is None:
            _cache.update(ts=now, terms=None, reason="not_configured")
        return None, "not_configured"
    try:
        terms = nad_fetch_terms(cfg)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            ValueError) as exc:
        reason = "error:%s" % exc
        if _cache["terms"] is not None:
            return _cache["terms"], None      # 有旧值：降级用旧值，不写空缓存
        _cache.update(ts=now, terms=None, reason=reason)
        return None, reason
    _cache.update(ts=now, terms=terms, reason=None)
    return terms, None


def nad_cache_invalidate():
    """强制失效缓存（测试/运维用）。"""
    _cache.update(ts=0.0, terms=None, reason=None)


# ----------------------------------------------------------------------
# 终端镜像同步（ADR-047 批 B：开关机任务「画方准入」资产源的数据基础）
#   NAD 平台不提供本地库、终端标识为 oid，故落一份镜像快照到平台库：
#   ① 向导可按归属路径浏览/检索终端；② WoL 目标解析取 MAC 与 IP。
# ----------------------------------------------------------------------

NAD_SYNC_INTERVAL_SEC = 1800     # 镜像同步间隔（30 分钟）


def _term_to_row(term):
    """NAD 原始 term → nad_terminals 行（取首个 MAC + 汇总全部 IP）。

    坑：原始 term 的 macs / ips / macports 均为「数字键 dict」形态
    （实测 {"0": {...}}），必须经 _norm_macs / _norm_ips 归一——直接迭代
    dict 只会得到键字符串，会把 MAC/IP 全部丢掉（2026-09-19 首次上线
    即踩此坑：1543 台终端 wol-capable 全为 0）。"""
    term = term or {}
    mac, ips = "", []
    for m in _norm_macs(term):
        if not mac and m.get("mac"):
            mac = str(m["mac"]).strip()
        for item in (m.get("ips") or []):
            s = str((item or {}).get("ip") or "").strip()
            if s and s not in ips:
                ips.append(s)
    ou = term.get("ou") if isinstance(term.get("ou"), dict) else {}
    return {"oid": str(term.get("oid") or "").strip(),
            "name": str(term.get("name") or "").strip(),
            "mac": mac, "ips": ips,
            "group_path": str(ou.get("namepath") or "").strip(),
            "online": bool(term.get("online")),
            "owner": str(term.get("owner") or "").strip()}


def nad_sync_to_store(store, log=None):
    """拉取 NAD 全量终端并整体替换镜像快照。

    返回 {ok, total, capable, error}；未配置/网络失败**不抛异常**（如实返回），
    保证守护线程与管理端点不因外部依赖中断。"""
    cfg = nad_load_config(store)
    if not cfg:
        return {"ok": False, "total": 0, "capable": 0,
                "error": "not_configured"}
    try:
        terms = nad_fetch_terms(cfg)
    except Exception as exc:
        return {"ok": False, "total": 0, "capable": 0,
                "error": "error:%s" % str(exc)[:120]}
    rows = [_term_to_row(t) for t in (terms or [])]
    rows = [r for r in rows if r["oid"]]
    try:
        store.nad_replace_snapshot(rows)
    except Exception as exc:
        return {"ok": False, "total": 0, "capable": 0,
                "error": "store:%s" % str(exc)[:120]}
    capable = sum(1 for r in rows if r["mac"] and r["ips"])
    # 2026-09-19 修复：同步完成后**用刚拉到的 terms 直接灌内存缓存**，而不是
    # 简单 invalidate。原实现清空缓存 → 紧随其后的资产定位请求又要同步拉约
    # 9 秒，撞上 8 秒预算而丢失画方数据源（实测 status=timeout_budget, n=0）。
    # 同一份数据零额外请求复用，预热与同步兼顾。
    if terms is not None:
        _cache.update(ts=time.time(), terms=terms, reason=None)
    else:
        nad_cache_invalidate()
    return {"ok": True, "total": len(rows), "capable": capable,
            "error": None}


def nad_loop(ctx, stop_event, log=None):
    """画方终端镜像同步守护线程（未配置时静默空转，零外部请求）。"""
    while not stop_event.is_set():
        try:
            res = nad_sync_to_store(ctx.store)
            if log and res.get("ok"):
                log("nad sync ok: %d terminals (%d wol-capable)"
                    % (res["total"], res["capable"]))
        except Exception as exc:            # 兜底：绝不让线程退出
            if log:
                try:
                    log("nad sync error: %s" % str(exc)[:160])
                except Exception:
                    pass
        if stop_event.wait(NAD_SYNC_INTERVAL_SEC):
            break


def _mac_norm(mac):
    return str(mac or "").replace("-", "").replace(":", "").replace(".",
                                                                    "").lower()


def _mac_entry(entry):
    """单个 MAC 条目证据：mac + ips + macports（接入交换机端口）。"""
    return {
        "mac": entry.get("mac"),
        "ips": [i.get("ip") for i in entry.get("ips") or []],
        "macports": [{"nasoid": p.get("nasoid"), "nasif": p.get("nasif"),
                      "nasname": p.get("nasname"), "manip": p.get("manip")}
                     for p in entry.get("macports") or []],
    }


def _evidence_block(term, only_mac=None):
    """准入证据块（ADR-024 规格）：终端摘要 + macs[{mac,ips,macports}]。
    only_mac 非空时仅保留该 MAC 条目。"""
    entries = _norm_macs(term)
    if only_mac:
        entries = [e for e in entries
                   if _mac_norm(e.get("mac")) == _mac_norm(only_mac)]
    ou = term.get("ou")
    return {
        "source": "nad",
        "oid": term.get("oid"),
        "name": term.get("name"),
        "ou": (ou.get("namepath") if isinstance(ou, dict) else ou),
        "ttype": term.get("ttype"),
        "manfct": term.get("manfct"),
        "model": term.get("model"),
        "online": term.get("online"),
        "block": term.get("block"),
        "reginfo": (term.get("reginfo") or {}).get("stat")
        if isinstance(term.get("reginfo"), dict) else None,
        "macs": [_mac_entry(e) for e in entries],
    }


def nad_find_by_ip(store, ip, full=False):
    """按 IP 查准入登记（遍历全部终端 macs.ips）。
    返回 (evidence_blocks|None, reason|None)；None=数据源不可用。
    full=True 返回扩展证据块（reginfo 完整 dict，ADR-043 第三方弹窗）。"""
    terms, reason = nad_terminals_cached(store)
    if terms is None:
        return None, reason
    builder = nad_evidence_full if full else _evidence_block
    hits = []
    for t in terms:
        if not isinstance(t, dict):
            continue
        for entry in _norm_macs(t):
            if any(str(i.get("ip")) == str(ip)
                   for i in entry.get("ips") or []):
                hits.append(builder(t, only_mac=entry.get("mac")))
                break
    return hits, None


def nad_find_by_mac(store, mac, full=False):
    """按 MAC 查准入登记（归一化比较）。返回值语义同 nad_find_by_ip。"""
    terms, reason = nad_terminals_cached(store)
    if terms is None:
        return None, reason
    builder = nad_evidence_full if full else _evidence_block
    target = _mac_norm(mac)
    hits = []
    for t in terms:
        if not isinstance(t, dict):
            continue
        for entry in _norm_macs(t):
            if _mac_norm(entry.get("mac")) == target:
                hits.append(builder(t, only_mac=entry.get("mac")))
                break
    return hits, None


def nad_evidence_full(term, only_mac=None):
    """第三方数据源弹窗扩展证据块（ADR-043）：
    _evidence_block 形状基础上 reginfo 透出**完整 dict**（登记字段原样
    透出，前端防御渲染），另附 reginfo_stat 便捷位；owner（责任人，
    生产实证 {name, uuid:工号}）与 onlts（最后在线 epoch，2026-09-17
    字段路径核验后新增）。ADR-024 的 _evidence_block（ipconflict 在用）
    形状不变。"""
    entries = _norm_macs(term)
    if only_mac:
        entries = [e for e in entries
                   if _mac_norm(e.get("mac")) == _mac_norm(only_mac)]
    ou = term.get("ou")
    reg = term.get("reginfo")
    owner = term.get("owner")
    return {
        "source": "nad",
        "oid": term.get("oid"),
        "name": term.get("name"),
        "ou": (ou.get("namepath") if isinstance(ou, dict) else ou),
        "ttype": term.get("ttype"),
        "manfct": term.get("manfct"),
        "model": term.get("model"),
        "online": term.get("online"),
        "block": term.get("block"),
        "owner": owner if isinstance(owner, dict) else None,
        "owner_name": (owner.get("name")
                       if isinstance(owner, dict) else None),
        "owner_uuid": (owner.get("uuid")
                       if isinstance(owner, dict) else None),
        "onlts": term.get("onlts"),
        "reginfo": reg if isinstance(reg, dict) else None,
        "reginfo_stat": (reg.get("stat")
                         if isinstance(reg, dict) else None),
        "macs": [_mac_entry(e) for e in entries],
    }


def nad_cache_ts():
    """当前结果缓存的写入时点（epoch 秒；无缓存为 0）——前端标注采集时间。"""
    return _cache["ts"]
