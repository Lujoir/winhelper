# -*- coding: utf-8 -*-
"""观枢终端平台服务端 · 控制台「首页」聚合路由组（/api/v1/console/home/*）。

归属：home-console-dev（中心首页卡片式主界面模块）。
api.py 仅在 _console_api 头部单点转发接入（2 行），本文件自治演进，
避免单体 API 文件多 agent 并行改动冲突。

端点（v1）：
- GET  /api/v1/console/home/summary — 首页卡片摘要聚合（一次请求喂全部卡片）
- POST /api/v1/console/home/asset-locate — 资产定位检索聚合（admin-only + 审计；
  实现在 asset_locate.py，asset-mgmt-dev 辖区，19694e1）

设计要点：
- 每张卡片独立 try/except：单卡数据失败返回 {"ok": false, "error": ...}，
  前端该卡片降级显示「数据不可用」，不阻塞整页（PRD 硬要求）。
- 鉴权：dispatch 层 /api/v1/console/* 已统一 resolve_session（operator/admin
  均可读），本组不重复鉴权，与 _console_kb_api 同口径。
- summary 为只读 GET，不审计（与控制台其他 GET 端点一致）；asset-locate
  为 POST，admin-only + 审计留痕（口径见 asset_locate.py）。

卡片数据契约 v1（返回体 cards 的键 = 前端卡片注册 id，Phase2 各模块 agent
按此键追加自有字段，向后兼容）：
  monitor — 终端监控：total / online / offline / version_counts
  perf    — 终端性能分析：covered / online / cpu_avg / mem_avg / busy /
            bottleneck_24h（数据源：metrics 表 + bottlenecks 表，
            阈值与 ADR-006 规则引擎一致）
  assets  — 资产管理：platform_groups / huorong_groups / other_total /
            other_online
  release — 客户端发布：current_version / published_at / total_releases
            （operator 可见：仅版本号/时间/数量等非敏感字段，不含
            token/sha256/安装包文件名——admin-only 端点数据的脱敏投影）
  其余卡片 — {"ok": true, "pending": true} 骨架占位（无数据模块 Phase1
             先放占位，Phase2 由对应模块 agent 接入真实摘要）
"""

import time


def handle(ctx, method, path, query, headers=None, body=None,
           sess=None, client_ip=None):
    """入口：/api/v1/console/home/* 路由分发。返回 (status, bytes, ctype)。

    局部导入 api（api.py 顶层 import api_home，反向顶层导入会循环）。"""
    from api import ApiError
    if path == "/api/v1/console/home/summary" and method == "GET":
        return _summary(ctx)
    # 资产定位（首页「资产定位」卡数据供给；admin-only。实现在
    # asset_locate.py，asset-mgmt-dev 辖区；经本文件分发使共享文件
    # api.py 保持零改动）
    if path == "/api/v1/console/home/asset-locate":
        import asset_locate
        return asset_locate.handle(ctx, method, path, query, headers, body,
                                   sess=sess, client_ip=client_ip)
    raise ApiError(404, "not found")


def _summary(ctx):
    from api import _json_response
    now = int(time.time())
    cards = {}
    builders = (
        ("monitor", _card_monitor),
        ("perf", _card_perf),
        ("assets", _card_assets),
        ("asset-locate", _card_asset_locate),
        ("release", _card_release),
        ("nettest", None),
        ("ai", None),
        ("dpol", None),
        ("pc", None),
        ("kb", None),
        ("config", None),
        ("sysadmin", None),
    )
    for card_id, fn in builders:
        if fn is None:
            # Phase2 骨架占位：各模块 agent 接入后替换
            cards[card_id] = {"ok": True, "pending": True}
            continue
        try:
            cards[card_id] = fn(ctx, now)
        except Exception as exc:  # 单卡失败不拖垮整页
            cards[card_id] = {"ok": False, "error": str(exc)[:120]}
    return _json_response(200, {"ok": True, "ts": now, "cards": cards})


def _hb_timeout(ctx):
    try:
        return int(ctx.config.get("heartbeat_timeout_sec", 180))
    except (TypeError, ValueError):
        return 180


# ----------------------------------------------------------------------
# 卡片摘要实现（只读；新增卡片在此追加 _card_xxx 并登记到 builders）
# ----------------------------------------------------------------------

def _card_monitor(ctx, now):
    """终端监控：在线/离线/总数 + 客户端版本分布。"""
    rows = ctx.store.list_terminals()
    hb = _hb_timeout(ctx)
    online = 0
    versions = {}
    for t in rows:
        if (now - (t.get("last_seen") or 0)) < hb:
            online += 1
        v = t.get("client_version") or "-"
        versions[v] = versions.get(v, 0) + 1
    total = len(rows)
    return {"ok": True, "total": total, "online": online,
            "offline": total - online, "version_counts": versions}


def _card_assets(ctx, now):
    """资产管理：平台资产组 / 火绒镜像组 / 未关联平台终端统计。"""
    store = ctx.store
    hb = _hb_timeout(ctx)
    groups = store.asset_group_list()
    hr_groups = store.hr_groups_with_stats()
    unlinked = store.hr_platform_unlinked()
    other_online = sum(1 for r in unlinked
                       if now - (r.get("last_seen") or 0) < hb)
    return {"ok": True,
            "platform_groups": len(groups),
            "huorong_groups": len(hr_groups),
            "other_total": len(unlinked),
            "other_online": other_online}


def _card_asset_locate(ctx, now):
    """资产定位：轻量占位摘要（检索交互由卡片自取数，POST
    /api/v1/console/home/asset-locate，实现在 asset_locate.py，
    asset-mgmt-dev 辖区；本摘要仅为框架数据流占位）。"""
    return {"ok": True}


def _card_release(ctx, now):
    """客户端发布：当前版本摘要（operator 可见的脱敏投影）。"""
    cr = getattr(ctx, "cr", None)
    if cr is None:
        return {"ok": True, "current_version": None, "total_releases": 0}
    cur = cr.get_current()
    try:
        total = len(cr.list())
    except Exception:
        total = 0
    if not cur:
        return {"ok": True, "current_version": None,
                "total_releases": total}
    return {"ok": True, "current_version": cur.get("version"),
            "published_at": cur.get("published_at"),
            "release_note": cur.get("note") or "",
            "total_releases": total}


def _card_perf(ctx, now):
    """终端性能分析：全平台资源负载概况 + 近 24h 瓶颈事件数。

    数据源：metrics 表（终端心跳同拍上报的 CPU/内存/交换分区）+
    bottlenecks 表（ADR-006 规则引擎落档）。
    阈值口径与规则引擎严格一致（CPU > 85%、内存 > 90%），避免同一平台
    两处口径打架；详细曲线与逐台明细仍在「终端监控」页查看。"""
    store = ctx.store
    hb = _hb_timeout(ctx)
    rows = store.latest_metrics_all(since_ts=now - 24 * 3600)
    cpu_vals = [r["cpu_percent"] for r in rows
                if r.get("cpu_percent") is not None]
    mem_vals = [r["mem_percent"] for r in rows
                if r.get("mem_percent") is not None]
    cpu_avg = round(sum(cpu_vals) / len(cpu_vals), 1) if cpu_vals else None
    mem_avg = round(sum(mem_vals) / len(mem_vals), 1) if mem_vals else None
    busy = sum(1 for r in rows
               if (r.get("cpu_percent") or 0) > 85
               or (r.get("mem_percent") or 0) > 90)
    try:
        bn_24h = len(store.list_bottlenecks(limit=500,
                                            since_ts=now - 24 * 3600))
    except Exception:
        bn_24h = 0
    online = sum(1 for t in store.list_terminals()
                 if (now - (t.get("last_seen") or 0)) < hb)
    return {"ok": True, "covered": len(rows), "online": online,
            "cpu_avg": cpu_avg, "mem_avg": mem_avg,
            "busy": busy, "bottleneck_24h": bn_24h}
