r"""
Log Inspector 服务层（框架无关，ADR-001）
==========================================
业务处理器：检索 / 原始日志导出任务（流式 txt）/ 智能分析 / 报告导出（HTML）/ 知识库。
入参: params dict（值均为字符串，来自 query string）；出参: 可JSON序列化 dict。

铁律（docs/DECISIONS.md）：
  - 原始日志导出 txt（utf-8-sig），分析报告一律 HTML（ADR-003/005）
  - 大日志量流式处理，导出增量 flush，取消立即终止（ADR-002/003）
  - Security 等类别无权限时优雅降级（ADR-004）
  - 路径禁止硬编码盘符，默认 %USERPROFILE%\Downloads（ADR-003）
"""

import datetime
import io
import os
import re
import threading
import uuid

import log_reader


# ============================================================
# 常量
# ============================================================

SEARCH_MAX_EVENTS = 20000     # 检索上限保护（ADR-002）
ANALYZE_MAX_EVENTS = 30000    # 分析上限保护
DESC_TRUNC = 500              # 事件描述返回前端的最大长度

LEVEL_NAME_MAP = {"critical": "关键", "error": "错误", "warning": "警告", "info": "信息"}


# ============================================================
# 参数解析辅助
# ============================================================

def _to_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_types(params):
    """日志类别列表，默认 System；白名单校验"""
    valid = set(log_reader.LOG_SOURCES.keys())
    raw = (params.get("types") or params.get("type") or "System").strip()
    types = [t.strip() for t in raw.split(",") if t.strip()]
    types = [t for t in types if t in valid]
    return types or ["System"]


def _parse_time_range(params):
    """
    解析时间范围（ADR-002）
    :return: (since: datetime|None, until: datetime|None, desc: str)
    """
    now = datetime.datetime.now()
    until = now

    start_raw = (params.get("start") or "").strip()
    end_raw = (params.get("end") or "").strip()

    def _parse_dt(raw, is_end):
        raw = raw.replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.datetime.strptime(raw, fmt)
                if is_end and len(raw) <= 10:
                    dt = dt + datetime.timedelta(days=1) - datetime.timedelta(seconds=1)
                return dt
            except ValueError:
                continue
        return None

    if start_raw:
        since = _parse_dt(start_raw, False)
        if end_raw:
            until = _parse_dt(end_raw, True) or now
        return since, until, f"{since:%Y-%m-%d %H:%M} ~ {until:%Y-%m-%d %H:%M}"

    hours = _to_int(params.get("hours"), 72)
    if hours <= 0:
        hours = 72
    since = now - datetime.timedelta(hours=hours)
    return since, until, f"近 {hours} 小时（{since:%Y-%m-%d %H:%M} ~ {until:%Y-%m-%d %H:%M}）"


def _parse_levels(params):
    """
    级别过滤集合（Windows EventType 数值）；None 表示全部
    critical=1 error=2 warning=3 info=0/4
    """
    raw = (params.get("levels") or "").strip()
    if not raw:
        return None
    levels = set()
    for lv in raw.split(","):
        lv = lv.strip().lower()
        if lv == "info":
            levels.update((0, 4))
        elif lv == "critical":
            levels.add(1)
        elif lv == "error":
            levels.add(2)
        elif lv == "warning":
            levels.add(3)
    return levels or None


def _parse_conditions(params):
    return {
        "levels": _parse_levels(params),
        "source": (params.get("source") or "").strip(),
        "keyword": (params.get("keyword") or "").strip(),
        "event_id": (params.get("event_id") or "").strip(),
    }


def _levels_desc(levels):
    if not levels:
        return "全部"
    names = {1: "关键", 2: "错误", 3: "警告", 0: "信息", 4: "信息"}
    return "、".join(sorted({names.get(v, str(v)) for v in levels}))


def _summary_of(events, since=None, until=None):
    """统计概览（级别/来源TOP/时间分布），基于全部匹配事件。

    时间分布采用绝对时间桶（ADR-002）：
      - 跨度 ≤ 48 小时 → 小时桶（MM-DD HH:00）
      - 否则 → 天桶（MM-DD）
    """
    s = {
        "total": len(events),
        "critical": 0, "error": 0, "warning": 0, "info": 0, "unknown": 0,
        "by_hour_labels": [], "by_hour_values": [],
        "top_events": [],
    }
    by_source = {}
    for ev in events:
        ln = ev["level_name"]
        if ln == "关键":
            s["critical"] += 1
        elif ln == "错误":
            s["error"] += 1
        elif ln == "警告":
            s["warning"] += 1
        elif ln == "信息":
            s["info"] += 1
        else:
            s["unknown"] += 1
        by_source[ev["source"]] = by_source.get(ev["source"], 0) + 1
    s["top_events"] = [{"name": n, "count": c}
                       for n, c in sorted(by_source.items(), key=lambda x: x[1], reverse=True)[:10]]

    # 时间分布桶
    if not events:
        s["by_hour_labels"] = ["(无数据)"]
        s["by_hour_values"] = [0]
        return s

    span_end = until or max(e["datetime"] for e in events)
    span_start = since or min(e["datetime"] for e in events)
    span_hours = max(0.0, (span_end - span_start).total_seconds() / 3600.0)

    buckets = {}
    hourly = span_hours <= 48
    for ev in events:
        dt = ev["datetime"]
        key = dt.replace(minute=0, second=0, microsecond=0) if hourly else dt.date()
        buckets[key] = buckets.get(key, 0) + 1

    if hourly:
        cur = span_start.replace(minute=0, second=0, microsecond=0)
        labels, values = [], []
        while cur <= span_end and len(labels) <= 60:
            labels.append(f"{cur:%m-%d %H}:00")
            values.append(buckets.get(cur, 0))
            cur += datetime.timedelta(hours=1)
    else:
        cur = span_start.date()
        labels, values = [], []
        while cur <= span_end.date() and len(labels) <= 60:
            labels.append(f"{cur:%m-%d}")
            values.append(buckets.get(cur, 0))
            cur += datetime.timedelta(days=1)
    s["by_hour_labels"] = labels
    s["by_hour_values"] = values
    return s


def _public_event(ev, seq=None):
    """事件 -> 前端安全结构"""
    return {
        "id": seq if seq is not None else ev["id"],
        "event_id": ev["event_id"],
        "source": ev["source"],
        "level_name": ev["level_name"],
        "level_class": ev["level_label"] if ev["level_class"] == "danger" else ev["level_class"],
        "timestamp": ev["timestamp"],
        "description": (ev["description"] or "(无详细描述)")[:DESC_TRUNC],
        "log_type": ev["log_type"],
        "is_known": ev["event_id"] in log_reader.CRITICAL_EVENT_IDS,
        "known_name": log_reader.CRITICAL_EVENT_IDS.get(ev["event_id"], ""),
    }


# ============================================================
# 1. 权限探测（ADR-004）
# ============================================================

def handle_log_access(params: dict) -> dict:
    """探测各类别日志读取权限（Security 需管理员）"""
    access = {}
    denied = []
    for name in log_reader.LOG_SOURCES:
        r = log_reader.check_log_access(name)
        access[name] = r["ok"]
        if not r["ok"]:
            denied.append({"type": name, "name": log_reader.LOG_SOURCES[name], "error": r["error"]})
    return {"success": True, "access": access, "denied": denied}


# ============================================================
# 2. 检索（过滤 + 分页，ADR-002）
# ============================================================

def _collect_events(params, max_events, cancel_event=None, progress_cb=None):
    """
    按条件跨类别收集匹配事件（倒序合并）
    :return: (events, errors, denied)  单类别失败不影响其他类别（铁律6）
    """
    types = _parse_types(params)
    since, until, _ = _parse_time_range(params)
    conditions = _parse_conditions(params)

    events = []
    errors = []
    denied = []
    quota = max_events
    for t in types:
        acc = log_reader.check_log_access(t)
        if not acc["ok"]:
            denied.append({"type": t, "name": log_reader.LOG_SOURCES[t], "error": acc["error"]})
            continue
        got = 0
        try:
            for ev in log_reader.iter_events(
                t, since=since, until=until,
                cancel_event=cancel_event, progress_cb=progress_cb,
            ):
                if log_reader.match_condition(ev, conditions):
                    events.append(ev)
                    got += 1
                    if got >= quota:
                        break
        except Exception as e:
            errors.append({"type": t, "error": str(e)})
            continue
        quota -= got
        if quota <= 0:
            break
    events.sort(key=lambda e: e["datetime"], reverse=True)
    return events, errors, denied


def handle_log_search(params: dict) -> dict:
    """系统日志检索：服务端过滤 + 分页 + 统计"""
    try:
        page = max(1, _to_int(params.get("page"), 1))
        per_page = min(200, max(5, _to_int(params.get("per_page"), 50)))
        events, errors, denied = _collect_events(params, SEARCH_MAX_EVENTS)
        _since, _until, _ = _parse_time_range(params)
        summary = _summary_of(events, _since, _until)

        total = len(events)
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, total_pages)
        start = (page - 1) * per_page
        page_events = events[start:start + per_page]

        # 来源下拉候选（全部来源按计数排序）
        sources = [t["name"] for t in summary["top_events"]]

        return {
            "success": True,
            "events": [_public_event(ev, start + i + 1) for i, ev in enumerate(page_events)],
            "summary": summary,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "truncated": total >= SEARCH_MAX_EVENTS,
            "sources": sources,
            "errors": errors,
            "denied": denied,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================
# 3. 原始日志导出任务（流式 txt，ADR-003）
# ============================================================

_export_tasks = {}
_export_lock = threading.Lock()

SEP_LINE = "=" * 68
EVT_LINE = "-" * 68


def _export_out_dir():
    d = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    out = os.path.join(d, "Downloads")
    if not os.path.isdir(out):
        out = d
    return out


def _export_header(params, types, since, until, time_desc):
    conditions = _parse_conditions(params)
    src = conditions["source"] or "全部"
    kw = conditions["keyword"] or "（无）"
    eid = conditions["event_id"] or "（无）"
    type_names = "、".join(f"{t}（{log_reader.LOG_SOURCES[t]}）" for t in types)
    lines = [
        SEP_LINE,
        "Windows 事件日志导出 · 观枢终端平台｜EyeTerm",
        SEP_LINE,
        f"导出时间   : {datetime.datetime.now():%Y-%m-%d %H:%M:%S}",
        f"日志类别   : {type_names}",
        f"时间范围   : {time_desc}",
        f"级别筛选   : {_levels_desc(conditions['levels'])}",
        f"来源筛选   : {src}",
        f"关键字     : {kw}",
        f"事件ID     : {eid}",
        "备注       : 每个事件一块，含 时间/级别/来源/事件ID/描述；文件编码 UTF-8（BOM）",
        SEP_LINE,
        "",
    ]
    return "\n".join(lines)


def _format_event_block(ev):
    # 统一使用标准时间格式（TimeGenerated.Format() 输出随本地化变化，不可靠）
    try:
        ts = f"{ev['datetime']:%Y-%m-%d %H:%M:%S}"
    except Exception:
        ts = ev["timestamp"] or ""
    lines = [
        EVT_LINE,
        f"[{ts}] [{ev['level_name']}] [{ev['source']}] [事件ID {ev['event_id']}]",
    ]
    desc = ev["description"] or "(无详细描述)"
    lines.append(desc.rstrip())
    strings = ev.get("strings") or []
    if strings:
        lines.append("参数: " + " | ".join(s.replace("\n", " ")[:200] for s in strings[:6]))
    lines.append(EVT_LINE)
    lines.append("")
    return "\n".join(lines)


def _start_export(params):
    with _export_lock:
        for t in _export_tasks.values():
            if t["status"] == "running":
                return t["id"], True
    task_id = uuid.uuid4().hex[:12]
    task = {
        "id": task_id, "kind": "log-export", "status": "running",
        "progress": {"written": 0, "scanned": 0},
        "result": None, "error": None,
        "started": datetime.datetime.now(),
        "_cancel": threading.Event(),
    }
    _export_tasks[task_id] = task
    threading.Thread(target=_run_export, args=(task, dict(params)), daemon=True).start()
    return task_id, False


def _run_export(task, params):
    out_dir = _export_out_dir()
    stamp = task["started"].strftime("%Y%m%d_%H%M%S")
    final_path = os.path.join(out_dir, f"LogExport_{stamp}.txt")
    tmp_path = final_path + ".part"

    types = _parse_types(params)
    since, until, time_desc = _parse_time_range(params)
    conditions = _parse_conditions(params)

    written = 0
    scanned_total = [0]

    def _progress(scanned):
        scanned_total[0] = scanned
        task["progress"] = {"written": written, "scanned": scanned}

    fh = None
    try:
        fh = io.open(tmp_path, "w", encoding="utf-8-sig", newline="\r\n")
        fh.write(_export_header(params, types, since, until, time_desc))
        fh.write("")

        for t in types:
            acc = log_reader.check_log_access(t)
            if not acc["ok"]:
                fh.write(f"【类别 {t} 不可读: {acc['error']}】\n\n")
                continue
            try:
                for ev in log_reader.iter_events(
                    t, since=since, until=until,
                    cancel_event=task["_cancel"], progress_cb=_progress,
                ):
                    if log_reader.match_condition(ev, conditions):
                        fh.write(_format_event_block(ev))
                        written += 1
                        if written % 50 == 0:
                            fh.flush()
                            task["progress"] = {"written": written, "scanned": scanned_total[0]}
            except Exception as e:
                fh.write(f"【类别 {t} 读取中断: {e}】\n\n")

        fh.flush()
        fh.close()
        fh = None

        if task["_cancel"].is_set():
            task["status"] = "cancelled"
            task["result"] = {"written": written}
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return

        os.replace(tmp_path, final_path)
        task["status"] = "done"
        task["result"] = {
            "path": final_path,
            "filename": os.path.basename(final_path),
            "count": written,
            "size": os.path.getsize(final_path),
            "dir": out_dir,
        }
    except Exception as e:
        task["status"] = "error"
        task["error"] = str(e)
        try:
            if fh:
                fh.close()
        except Exception:
            pass
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


def handle_log_export_start(params: dict) -> dict:
    """启动导出后台任务（同检索条件，无分页；默认近3天全量系统日志）"""
    try:
        task_id, reused = _start_export(params)
        return {"success": True, "task_id": task_id, "reused": reused}
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_log_export_status(params: dict) -> dict:
    """轮询导出任务状态"""
    task = _export_tasks.get(params.get("task_id", ""))
    if not task:
        return {"success": False, "error": "任务不存在或已过期"}
    elapsed = (datetime.datetime.now() - task["started"]).total_seconds()
    return {
        "success": True,
        "task": {
            "id": task["id"], "status": task["status"],
            "progress": task["progress"], "result": task["result"],
            "error": task["error"], "elapsed": round(elapsed, 1),
        },
    }


def handle_log_export_cancel(params: dict) -> dict:
    task = _export_tasks.get(params.get("task_id", ""))
    if task and task["status"] == "running":
        task["_cancel"].set()
        return {"success": True, "cancelled": True}
    return {"success": True, "cancelled": False}


# ============================================================
# 4. 智能分析（ADR-007）
# ============================================================

_analysis_lock = threading.Lock()
_last_analysis = None   # 最近一次分析结果缓存（供报告导出复用）


def handle_log_analyze(params: dict) -> dict:
    """智能分析：结论徽章 + 故障模式（含证据与建议）+ 已知问题事件 + 统计"""
    global _last_analysis
    try:
        events, errors, denied = _collect_events(params, ANALYZE_MAX_EVENTS)
        _since, _until, _ = _parse_time_range(params)
        summary = _summary_of(events, _since, _until)
        faults = log_reader.get_fault_analysis(events, with_evidence=True, max_evidence=5)

        critical_events = []
        for ev in events:
            if ev["event_id"] in log_reader.CRITICAL_EVENT_IDS:
                critical_events.append(_public_event(ev))
                if len(critical_events) >= 50:
                    break

        # 结论级别判定（ADR-007）
        sev_rank = {"critical": 0, "error": 1, "warning": 2, "info": 3}
        worst = "info"
        for f in faults:
            if sev_rank.get(f["severity"], 9) < sev_rank.get(worst, 9):
                worst = f["severity"]
        conclusion_text = {
            "critical": "发现严重故障模式，建议立即处理",
            "error": "发现错误级故障模式，建议尽快排查",
            "warning": "发现警告级故障模式，建议关注",
            "info": "未检测到故障模式，系统状态良好",
        }[worst]

        result = {
            "success": True,
            "conclusion": {"level": worst, "text": conclusion_text},
            "patterns": faults,
            "known_issues": critical_events,
            "known_count": sum(1 for ev in events if ev["event_id"] in log_reader.CRITICAL_EVENT_IDS),
            "summary": summary,
            "conditions": _parse_time_range(params)[2],
            "scanned": len(events),
            "truncated": len(events) >= ANALYZE_MAX_EVENTS,
            "errors": errors,
            "denied": denied,
            "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        with _analysis_lock:
            _last_analysis = {"params": dict(params), "result": result}
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================
# 5. 分析报告导出（单文件自包含 HTML，ADR-005）
# ============================================================

def _html_escape(s):
    import html as _htmllib
    return _htmllib.escape(str(s if s is not None else "--"))


def _html_doc(title, body):
    css = (
        "body{background:#0f1117;color:#e8ebf2;font-family:'Segoe UI','Microsoft YaHei',sans-serif;"
        "margin:0;padding:32px}"
        ".wrap{max-width:960px;margin:0 auto}"
        "h1{font-size:20px;margin:0 0 6px}"
        "h2{font-size:16px;margin:26px 0 10px;border-bottom:1px solid #262b3a;padding-bottom:6px}"
        ".meta{font-size:12px;color:#8a93a5;line-height:1.7;margin-bottom:8px}"
        ".brand{font-size:12px;color:#4fc3f7;letter-spacing:1px;margin-bottom:14px}"
        ".badge{display:inline-block;border:1px solid;border-radius:20px;padding:3px 12px;"
        "font-size:13px;margin-bottom:10px}"
        ".card{background:#161a24;border:1px solid #262b3a;border-radius:10px;padding:12px 14px;"
        "margin-bottom:10px;font-size:13px;line-height:1.7}"
        "table{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:6px}"
        "th,td{border:1px solid #262b3a;padding:6px 9px;text-align:left}"
        "th{background:#1a1f2c;color:#aab3c5}"
        "td{color:#cdd3df}"
        "ul{margin:6px 0;padding-left:20px}li{margin:3px 0}"
        ".foot{margin-top:26px;font-size:11px;color:#5f6577}"
        "code{background:#1a1f2c;padding:1px 5px;border-radius:4px;font-size:12px}"
    )
    return ('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
            f"<title>{_html_escape(title)}</title><style>{css}</style></head>"
            f'<body><div class="wrap">{body}</div></body></html>')


_SEV_COLOR = {"critical": "#ef5350", "error": "#ff8a65", "warning": "#ffd54f", "info": "#81c784"}
_SEV_LABEL = {"critical": "严重", "error": "错误", "warning": "警告", "info": "信息"}


def _report_html(rep):
    """分析结果 → 单文件自包含 HTML（深色风格，ADR-005）"""
    h = []
    ap = h.append
    level = rep["conclusion"]["level"]
    color = _SEV_COLOR.get(level, "#aab3c5")
    ap('<div class="brand">观枢终端平台｜EyeTerm</div>')
    ap("<h1>系统日志智能分析报告</h1>")
    ap(f'<div class="badge" style="color:{color};border-color:{color}">'
       f'结论：{_html_escape(rep["conclusion"]["text"])}</div>')
    ap('<div class="meta">'
       f'生成时间：{_html_escape(rep.get("generated_at"))} · '
       f'检索条件：{_html_escape(rep.get("conditions"))} · '
       f'扫描事件：{_html_escape(rep.get("scanned"))} 条</div>')

    s = rep.get("summary") or {}
    ap("<h2>统计概览</h2>")
    ap('<table><tr><th>事件总数</th><th>关键</th><th>错误</th><th>警告</th><th>信息</th></tr>')
    ap(f'<tr><td>{s.get("total", 0)}</td><td>{s.get("critical", 0)}</td>'
       f'<td>{s.get("error", 0)}</td><td>{s.get("warning", 0)}</td>'
       f'<td>{s.get("info", 0)}</td></tr></table>')

    ap("<h2>故障模式（含处理建议）</h2>")
    patterns = rep.get("patterns") or []
    if not patterns:
        ap('<div class="card">未检测到故障模式，系统状态良好。</div>')
    for p in patterns:
        c = _SEV_COLOR.get(p["severity"], "#aab3c5")
        ap(f'<div class="card"><span class="badge" style="color:{c};border-color:{c}">'
           f'[{_SEV_LABEL.get(p["severity"], p["severity"])}]</span> '
           f'<b>{_html_escape(p["name"])}</b> — 匹配 {_html_escape(p["count"])} 次')
        ev = p.get("evidence") or []
        if ev:
            ap("<ul>")
            for e in ev:
                ap(f'<li><code>{_html_escape(e["timestamp"])}</code> '
                   f'[{_html_escape(e["source"])} / ID {_html_escape(e["event_id"])}] '
                   f'{_html_escape((e["description"] or "").replace("\n", " ")[:120])}</li>')
            ap("</ul>")
        ap("<ul>")
        for sg in p.get("suggestions") or []:
            ap(f"<li>{_html_escape(sg)}</li>")
        ap("</ul></div>")

    ki = rep.get("known_issues") or []
    ap(f"<h2>已知问题事件（共 {_html_escape(rep.get('known_count', 0))} 条，展示前 {len(ki)} 条）</h2>")
    if ki:
        ap("<table><tr><th>时间</th><th>事件ID</th><th>名称</th><th>来源</th><th>级别</th><th>描述</th></tr>")
        for e in ki[:50]:
            ap(f'<tr><td>{_html_escape(e["timestamp"])}</td><td>{_html_escape(e["event_id"])}</td>'
               f'<td>{_html_escape(e["known_name"])}</td><td>{_html_escape(e["source"])}</td>'
               f'<td>{_html_escape(e["level_name"])}</td>'
               f'<td>{_html_escape((e["description"] or "").replace("\n", " ")[:100])}</td></tr>')
        ap("</table>")
    else:
        ap('<div class="card">无已知问题事件。</div>')

    if rep.get("denied"):
        names = "、".join(d["name"] for d in rep["denied"])
        ap(f'<div class="meta" style="margin-top:10px">注意：类别 {names} 因权限不足未纳入分析（需管理员权限）。</div>')

    ap('<div class="foot">本报告由 观枢终端平台｜EyeTerm 日志诊断模块自动生成；'
       "单文件自包含 HTML，可直接浏览器打开或打印。</div>")
    return _html_doc("观枢终端平台｜EyeTerm - 日志智能分析报告", "\n".join(h))


def handle_log_report_export(params: dict) -> dict:
    """导出最近一次分析报告为 HTML；无缓存时重新分析"""
    with _analysis_lock:
        cached = _last_analysis
    rep = None
    if cached and cached.get("result") and cached["result"].get("success"):
        rep = cached["result"]
    else:
        fresh = handle_log_analyze(params)
        if fresh.get("success"):
            rep = fresh
        else:
            return {"success": False, "error": fresh.get("error", "分析失败")}
    html = _report_html(rep)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(_export_out_dir(), f"LogAnalysis_{stamp}.html")
    try:
        with io.open(out, "w", encoding="utf-8") as f:
            f.write(html)
    except Exception as e:
        return {"success": False, "error": str(e)}
    return {"success": True, "path": out, "html_size": len(html),
            "filename": os.path.basename(out), "dir": os.path.dirname(out)}


# ============================================================
# 6. 知识库（ADR-007）
# ============================================================

def _knowledge_list():
    result = []
    for name, pattern in log_reader.FAULT_PATTERNS.items():
        result.append({
            "name": name,
            "severity": pattern["severity"],
            "event_ids": list(pattern["event_ids"])[:12],
            "sources": pattern["sources"][:5],
            "keywords": pattern["keywords"][:6],
            "suggestions": pattern["suggestions"],
        })
    return result


def handle_log_knowledge(params: dict) -> dict:
    """知识库：FAULT_PATTERNS 结构化 + 自由检索（q 按名称/事件ID/关键字/来源/建议过滤）"""
    q = (params.get("q") or "").strip().lower()
    knowledge = _knowledge_list()
    if q:
        def _hit(k):
            if q in k["name"].lower():
                return True
            if any(q == str(eid) or q in str(eid) for eid in k["event_ids"]):
                return True
            if any(q in s.lower() for s in k["sources"]):
                return True
            if any(q in kw.lower() for kw in k["keywords"]):
                return True
            if any(q in sg.lower() for sg in k["suggestions"]):
                return True
            return False
        knowledge = [k for k in knowledge if _hit(k)]
    return {"success": True, "knowledge": knowledge, "q": q}
