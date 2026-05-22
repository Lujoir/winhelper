"""
Windows 系统故障分析应用
通过对 Windows 系统日志的全面解析，精准分析可能的故障原因
"""

import json
import datetime
from flask import Flask, render_template, request, jsonify

from log_reader import (
    get_event_logs, get_system_summary, get_fault_analysis,
    CRITICAL_EVENT_IDS, FAULT_PATTERNS, LOG_SOURCES,
)

app = Flask(__name__)
app.config["SECRET_KEY"] = "windows-fault-analyzer-secret-key-2026"


@app.route("/")
def index():
    """首页 - 仪表盘"""
    return render_template("dashboard.html")


@app.route("/api/analyze")
def api_analyze():
    """分析指定日志类型的全部数据"""
    log_type = request.args.get("type", "System")
    max_events = int(request.args.get("max", 1000))

    try:
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

        return jsonify({
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
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
        })


@app.route("/api/events")
def api_events():
    """获取原始事件日志"""
    log_type = request.args.get("type", "System")
    max_events = int(request.args.get("max", 500))
    level_filter = request.args.get("level", "")
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))

    try:
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
            is_known = ev["event_id"] in CRITICAL_EVENT_IDS
            known_name = CRITICAL_EVENT_IDS.get(ev["event_id"], "")

            result.append({
                "id": ev["id"],
                "event_id": ev["event_id"],
                "source": ev["source"],
                "level_name": ev["level_name"],
                "level_class": ev["level_class"],
                "timestamp": ev["timestamp"],
                "description": desc[:500] if desc else "(无详细描述)",
                "log_type": ev["log_type"],
                "is_known": is_known,
                "known_name": known_name,
            })

        return jsonify({
            "success": True,
            "events": result,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/faults")
def api_faults():
    """获取故障模式分析结果"""
    log_type = request.args.get("type", "System")
    max_events = int(request.args.get("max", 1000))

    try:
        events = get_event_logs(log_type, max_events=max_events)
        faults = get_fault_analysis(events)

        return jsonify({
            "success": True,
            "faults": faults,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/log-types")
def api_log_types():
    """获取可用的日志类型"""
    return jsonify({
        "success": True,
        "types": [{"id": k, "name": v} for k, v in LOG_SOURCES.items()],
    })


@app.route("/api/knowledge")
def api_knowledge():
    """获取故障知识库"""
    result = []
    for name, pattern in FAULT_PATTERNS.items():
        result.append({
            "name": name,
            "severity": pattern["severity"],
            "event_ids": [eid for eid in pattern["event_ids"] if eid in CRITICAL_EVENT_IDS],
            "sources": pattern["sources"][:5],
            "suggestions": pattern["suggestions"],
        })
    return jsonify({"success": True, "knowledge": result})


if __name__ == "__main__":
    import webbrowser
    import threading

    def open_browser():
        webbrowser.open("http://127.0.0.1:5000")

    threading.Timer(1.5, open_browser).start()
    app.run(host="127.0.0.1", port=5000, debug=True, threaded=True)
