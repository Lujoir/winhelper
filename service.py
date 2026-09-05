"""
服务层 — 业务处理器（桌面模式 JS 桥接的唯一入口）
==================================================
原 B/S Web 模式已移除；本模块承载与传输协议无关的全部业务处理器，
由 bridge.py（pywebview js_api）直接调用。

入参: params dict;  出参: 可JSON序列化的 dict
"""

from disk_cleanup import (
    handle_disk_overview, handle_disk_scan, handle_disk_scan_status,
    handle_disk_scan_cancel, handle_disk_cleanup, handle_disk_open_location,
    handle_disk_tree, handle_disk_drives,
)
from appdata_scan import (
    handle_appdata_scan, handle_appdata_drives,
    handle_appdata_migrate, handle_appdata_delete,
    handle_installer_scan,
)


# ==================================================================
# 故障诊断业务处理器
# ==================================================================

def handle_analyze(params: dict) -> dict:
    """分析指定日志类型的全部数据（仪表盘 + 故障模式 + 已知问题事件）"""
    log_type = params.get("type", "System")
    try:
        max_events = int(params.get("max", 1000))
    except (TypeError, ValueError):
        max_events = 1000

    try:
        from log_reader import (
            get_event_logs, get_system_summary, get_fault_analysis,
            CRITICAL_EVENT_IDS, LOG_SOURCES,
        )

        events = get_event_logs(log_type, max_events=max_events)
        summary = get_system_summary(events)
        faults = get_fault_analysis(events)

        # 标记关键事件（包含已知问题ID）
        critical_events = []
        for ev in events:
            if ev["event_id"] in CRITICAL_EVENT_IDS:
                critical_events.append({
                    "id": ev["id"],
                    "event_id": ev["event_id"],
                    "source": ev["source"],
                    "level_name": ev["level_name"],
                    "level_class": ev["level_class"],
                    "timestamp": ev["timestamp"],
                    "description": ev["description"][:300],
                    "event_name": CRITICAL_EVENT_IDS[ev["event_id"]],
                    "log_type": ev["log_type"],
                })

        # 高严重度故障
        high_risk_faults = [f for f in faults if f["severity"] in ("critical", "error")]

        return {
            "success": True,
            "summary": {
                "total": summary["total"],
                "critical": summary["critical"],
                "error": summary["error"],
                "warning": summary["warning"],
                "info": summary["info"],
                "by_hour_labels": summary["by_hour_labels"],
                "by_hour_values": summary["by_hour_values"],
                "top_events": [{"name": n, "count": c} for n, c in summary["top_events"]],
            },
            "critical_events": critical_events[:50],  # 最多返回50条
            "critical_count": len(critical_events),
            "faults": faults,
            "high_risk_faults": high_risk_faults,
            "high_risk_count": len(high_risk_faults),
            "log_type": LOG_SOURCES.get(log_type, log_type),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_events(params: dict) -> dict:
    """获取原始事件日志（分页 + 级别过滤）"""
    log_type = params.get("type", "System")
    try:
        max_events = int(params.get("max", 500))
        page = max(1, int(params.get("page", 1)))
        per_page = max(1, int(params.get("per_page", 50)))
    except (TypeError, ValueError):
        max_events, page, per_page = 500, 1, 50
    level_filter = params.get("level", "")

    try:
        from log_reader import get_event_logs, CRITICAL_EVENT_IDS

        events = get_event_logs(log_type, max_events=max_events)

        # 级别过滤
        if level_filter:
            level_map = {"critical": 1, "error": 2, "warning": 3, "info": 4}
            target_level = level_map.get(level_filter)
            if target_level is not None:
                events = [e for e in events if e["level"] == target_level]

        total = len(events)
        start = (page - 1) * per_page
        end = start + per_page
        page_events = events[start:end]

        result = []
        for ev in page_events:
            desc = ev["description"]
            result.append({
                "id": ev["id"],
                "event_id": ev["event_id"],
                "source": ev["source"],
                "level_name": ev["level_name"],
                "level_class": ev["level_class"],
                "timestamp": ev["timestamp"],
                "description": desc[:500] if desc else "(无详细描述)",
                "log_type": ev["log_type"],
                "is_known": ev["event_id"] in CRITICAL_EVENT_IDS,
                "known_name": CRITICAL_EVENT_IDS.get(ev["event_id"], ""),
            })

        return {
            "success": True,
            "events": result,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_faults(params: dict) -> dict:
    """获取故障模式分析结果"""
    log_type = params.get("type", "System")
    try:
        max_events = int(params.get("max", 1000))
    except (TypeError, ValueError):
        max_events = 1000

    try:
        from log_reader import get_event_logs, get_fault_analysis

        events = get_event_logs(log_type, max_events=max_events)
        faults = get_fault_analysis(events)
        return {"success": True, "faults": faults}
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_log_types(params: dict) -> dict:
    """获取可用的日志类型"""
    from log_reader import LOG_SOURCES

    return {
        "success": True,
        "types": [{"id": k, "name": v} for k, v in LOG_SOURCES.items()],
    }


def handle_knowledge(params: dict) -> dict:
    """获取故障知识库"""
    from log_reader import FAULT_PATTERNS, CRITICAL_EVENT_IDS

    result = []
    for name, pattern in FAULT_PATTERNS.items():
        result.append({
            "name": name,
            "severity": pattern["severity"],
            "event_ids": [eid for eid in pattern["event_ids"] if eid in CRITICAL_EVENT_IDS],
            "sources": pattern["sources"][:5],
            "suggestions": pattern["suggestions"],
        })
    return {"success": True, "knowledge": result}
