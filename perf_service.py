# -*- coding: utf-8 -*-
"""
性能分析服务层（框架无关，与 winhelper 主应用共用 handle_* 约定）
=================================================================
功能：
  1. 实时指标快照：CPU / 内存 / 交换分区 / 各物理磁盘速率与活跃度 / 各卷容量
  2. 长时间记录：后台 daemon 线程按可配置间隔采样，JSONL 增量落盘
  3. 停止后流式分析：统计(avg/p95/max/min) + 饱和持续时长 + 瓶颈排序 + 硬件优化评估
  4. 报告导出 Markdown（写入记录目录）

依赖：唯一运行时依赖 psutil。
路径约束：禁止硬编码盘符，记录目录用 LOCALAPPDATA/TEMP 等环境变量组装。
"""

import io
import json
import os
import subprocess
import threading
import time
import uuid
from datetime import datetime

try:
    import psutil
except ImportError:  # 允许模块导入失败时不崩主进程，handle_* 内会报错提示
    psutil = None


# ============================================================
# 通用工具
# ============================================================

def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _fmt_size(n):
    """字节可读化"""
    if n is None:
        return "--"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0
    return "--"


def _records_dir():
    """记录目录：%LOCALAPPDATA%/winhelper/perf_records（环境变量组装，禁止硬编码盘符）"""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") \
        or os.environ.get("SystemDrive", "C:") + os.sep
    d = os.path.join(base, "winhelper", "perf_records")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = os.environ.get("TEMP") or os.getcwd()
    return d


def _p95(vals):
    """排序线性插值 p95"""
    return _quantile(vals, 0.95)


def _quantile(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    if n == 1:
        return s[0]
    idx = q * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return s[lo] * (1.0 - frac) + s[hi] * frac


def _stat_line(vals, unit=""):
    """一组数值 -> {avg,p95,max,min}（保留1位小数）"""
    if not vals:
        return None
    return {
        "avg": round(sum(vals) / len(vals), 1),
        "p95": round(_p95(vals), 1),
        "max": round(max(vals), 1),
        "min": round(min(vals), 1),
        "unit": unit,
        "n": len(vals),
    }


# ============================================================
# 采样原语（cpu_times 自维护 delta，避免 psutil.cpu_percent 全局基准互相干扰）
# ============================================================

def _cpu_pct_from_state(state):
    """
    基于自维护的 cpu_times 快照计算 CPU 总占用%。
    state: dict，保存 "ct"(上次 cpu_times) 与 "ct_t"(上次时间)。
    首帧返回 None（无基准）。
    """
    ct = psutil.cpu_times()
    now = time.time()
    pct = None
    prev = state.get("ct")
    prev_t = state.get("ct_t")
    if prev is not None and prev_t is not None:
        dt = now - prev_t
        if dt > 0:
            total = float(sum(ct))
            ptotal = float(sum(prev))
            idle = float(getattr(ct, "idle", 0.0))
            pidle = float(getattr(prev, "idle", 0.0))
            d_total = total - ptotal
            d_idle = idle - pidle
            if d_total > 0:
                pct = _clamp(100.0 * (d_total - d_idle) / d_total, 0.0, 100.0)
    state["ct"] = ct
    state["ct_t"] = now
    return pct


def _disks_delta_from_state(state):
    """
    计算各物理盘 delta 指标（速率/IOPS/busy%）。
    state: dict，保存 "io"(上次 counters) 与 "io_t"。
    首帧返回空 dict（无基准）。
    """
    now = time.time()
    prev = state.get("io")
    prev_t = state.get("io_t")
    state["io"] = psutil.disk_io_counters(perdisk=True)
    state["io_t"] = now
    out = {}
    if prev is None or prev_t is None:
        return out
    dt = now - prev_t
    if dt <= 0:
        return out
    for name, cur in state["io"].items():
        if not str(name).startswith("PhysicalDrive"):
            continue
        p = prev.get(name)
        if p is None:
            continue
        d_read_b = max(0, cur.read_bytes - p.read_bytes)
        d_write_b = max(0, cur.write_bytes - p.write_bytes)
        d_ops = max(0, (cur.read_count - p.read_count) + (cur.write_count - p.write_count))
        # Windows: read_time/write_time 单位毫秒，读写并行计数可达 200%，夹取 0-100
        d_active_ms = max(0, (cur.read_time - p.read_time) + (cur.write_time - p.write_time))
        out[str(name)] = {
            "read_mb_s": round(d_read_b / dt / 1048576.0, 2),
            "write_mb_s": round(d_write_b / dt / 1048576.0, 2),
            "iops": round(d_ops / dt, 1),
            "busy_pct": round(_clamp(d_active_ms / dt / 10.0, 0.0, 100.0), 1),
        }
    return out


def _volumes():
    """各卷容量（跳过光驱/无权限）"""
    vols = []
    try:
        parts = psutil.disk_partitions(all=False)
    except Exception:
        parts = []
    for p in parts:
        if not p.fstype:
            continue  # 光驱等
        try:
            u = psutil.disk_usage(p.mountpoint)
        except (PermissionError, OSError):
            continue
        vols.append({
            "mount": p.mountpoint,
            "total": u.total, "used": u.used, "free": u.free,
            "percent": round(u.used / u.total * 100, 1) if u.total else None,
        })
    return vols


def _freq_mhz():
    try:
        f = psutil.cpu_freq()
        if f and f.current:
            return round(f.current, 0)
    except Exception:
        pass
    return None


def _memory():
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    return vm, sw


# ============================================================
# 1. 实时快照
# ============================================================

_snap_state = {"ct": None, "ct_t": None, "io": None, "io_t": None, "lock": threading.Lock()}


def handle_perf_snapshot(params: dict) -> dict:
    """实时指标快照（首帧速率类字段为 None，前端显示 --）"""
    if psutil is None:
        return {"success": False, "error": "缺少依赖 psutil，请先 pip install psutil"}
    try:
        with _snap_state["lock"]:
            cpu = _cpu_pct_from_state(_snap_state)
            disks = _disks_delta_from_state(_snap_state)
        vm, sw = _memory()
        vols = _volumes()

        disk_list = [
            {"name": name, **m}
            for name, m in sorted(disks.items())
        ]
        return {
            "success": True,
            "ts": time.time(),
            "cpu": {
                "percent": round(cpu, 1) if cpu is not None else None,
                "count": psutil.cpu_count() or 0,
                "freq_mhz": _freq_mhz(),
            },
            "memory": {
                "total": vm.total, "used": vm.used, "available": vm.available,
                "percent": round(vm.percent, 1),
                "total_text": _fmt_size(vm.total),
                "available_text": _fmt_size(vm.available),
            },
            "swap": {
                "total": sw.total, "used": sw.used,
                "percent": round(sw.percent, 1),
            },
            "disks": disk_list,
            "volumes": vols,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================
# 2. 长时间记录（后台线程 + JSONL 增量落盘）
# ============================================================

_RECORD_INTERVALS = (1, 2, 5, 10)
_records = {}
_records_lock = threading.Lock()
_last_report_ref = {"report": None}


def _record_worker(rec):
    """记录线程：按间隔采样并逐行落盘（异常不终止，坏行容忍在分析侧）"""
    fh = None
    try:
        fh = io.open(rec["file"], "a", encoding="utf-8")
        rec["_fh"] = fh
        while not rec["_stop"].is_set():
            tick = time.time()
            try:
                sample = _sample_once(rec)
                fh.write(json.dumps(sample, ensure_ascii=False) + "\n")
                fh.flush()
                rec["sample_count"] += 1
                rec["last_sample"] = sample
            except Exception:
                rec["error_count"] += 1
            # 可取消等待
            rec["_stop"].wait(max(0.05, rec["interval"] - (time.time() - tick)))
    except Exception as e:
        rec["status"] = "error"
        rec["error"] = str(e)
    finally:
        if rec["status"] == "running":
            rec["status"] = "stopped"
        try:
            if fh:
                fh.close()
        except Exception:
            pass


def _sample_once(rec):
    """单次采样（rec 内自维护 io 基线；cpu 基线也在 rec 上）"""
    with rec["_lock"]:
        cpu = _cpu_pct_from_state(rec["_cpu_state"])
        disks = _disks_delta_from_state(rec["_io_state"])
    vm, sw = _memory()
    return {
        "ts": time.time(),
        "cpu_pct": round(cpu, 1) if cpu is not None else None,
        "mem_pct": round(vm.percent, 1),
        "mem_avail": vm.available,
        "mem_total": vm.total,
        "swap_pct": round(sw.percent, 1),
        "disks": {name: m for name, m in disks.items()},
    }


def handle_perf_record_start(params: dict) -> dict:
    """开始记录：interval ∈ {1,2,5,10} 默认 2；重复开始幂等复用"""
    if psutil is None:
        return {"success": False, "error": "缺少依赖 psutil，请先 pip install psutil"}
    try:
        interval = int(float(params.get("interval") or 2))
    except (TypeError, ValueError):
        interval = 2
    if interval not in _RECORD_INTERVALS:
        interval = 2

    with _records_lock:
        # 幂等：已有运行中记录则复用
        for rec in _records.values():
            if rec["status"] == "running":
                return {
                    "success": True, "reused": True,
                    "record_id": rec["id"], "interval": rec["interval"],
                    "message": "已有记录进行中（间隔 %ds），继续沿用该记录" % rec["interval"],
                }
        rid = uuid.uuid4().hex[:12]
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        d = _records_dir()
        rec = {
            "id": rid, "interval": interval, "status": "running",
            "started_at": time.time(),
            "dir": d,
            "file": os.path.join(d, "perf_%s.jsonl" % ts),
            "sample_count": 0, "error_count": 0, "error": None,
            "last_sample": None,
            "_stop": threading.Event(),
            "_lock": threading.Lock(),
            "_cpu_state": {"ct": None, "ct_t": None},
            "_io_state": {"io": None, "io_t": None},
        }
        _records[rid] = rec
    th = threading.Thread(target=_record_worker, args=(rec,), daemon=True)
    rec["_thread_obj"] = th
    th.start()
    return {"success": True, "record_id": rid, "interval": interval, "reused": False,
            "file": rec["file"]}


def _record_view(rec):
    return {
        "id": rec["id"], "status": rec["status"], "interval": rec["interval"],
        "elapsed": round(time.time() - rec["started_at"], 1),
        "sample_count": rec["sample_count"], "error_count": rec["error_count"],
        "error": rec["error"],
        "file": rec["file"],
        "file_size": (os.path.getsize(rec["file"]) if os.path.exists(rec["file"]) else 0),
        "file_size_text": _fmt_size(os.path.getsize(rec["file"]) if os.path.exists(rec["file"]) else 0),
        "last_sample": rec["last_sample"],
    }


def handle_perf_record_status(params: dict) -> dict:
    """轮询记录状态：时长/样本数/当前瞬时值"""
    rid = (params.get("record_id") or "").strip()
    with _records_lock:
        rec = _records.get(rid)
        if rec is None:
            return {"success": False, "error": "记录不存在或已过期"}
        return {"success": True, "record": _record_view(rec)}


def handle_perf_record_stop(params: dict) -> dict:
    """停止记录并同步流式分析，返回报告"""
    rid = (params.get("record_id") or "").strip()
    with _records_lock:
        rec = _records.get(rid)
    if rec is None:
        return {"success": False, "error": "记录不存在或已过期"}

    if rec["status"] == "running":
        rec["_stop"].set()
        th = rec.get("_thread_obj")
        if th is not None:
            th.join(timeout=5)
        else:
            time.sleep(0.3)  # 线程对象未保存时给一次落盘余量
    if os.path.exists(rec["file"]):
        analysis = _analyze_jsonl(rec["file"], rec["interval"])
    else:
        return {"success": False, "error": "记录文件不存在"}

    report = {
        "record_id": rid,
        "file": rec["file"],
        "interval": rec["interval"],
        "elapsed": round(time.time() - rec["started_at"], 1),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **analysis,
    }
    # 持久化 analysis.json（窗口关闭后仍可查看）
    try:
        with io.open(os.path.join(rec["dir"], "analysis_%s.json" % rid), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    _last_report_ref["report"] = report
    return {"success": True, "record": _record_view(rec), "report": report}


def handle_perf_record_report(params: dict) -> dict:
    """最近一份分析报告（内存优先，回退记录目录 analysis_*.json）"""
    rep = _last_report_ref["report"]
    if rep:
        return {"success": True, "found": True, "report": rep}
    try:
        d = _records_dir()
        files = [f for f in os.listdir(d) if f.startswith("analysis_") and f.endswith(".json")]
        if files:
            path = os.path.join(d, sorted(files)[-1])
            with io.open(path, "r", encoding="utf-8") as f:
                return {"success": True, "found": True, "report": json.load(f)}
    except Exception:
        pass
    return {"success": True, "found": False}


# ============================================================
# 3. 流式分析（坏行跳过；逐行读取不整体载入）
# ============================================================

_SAT = {"cpu_pct": 85.0, "mem_avail_pct": 10.0, "disk_busy_pct": 80.0}
_GAP_BREAK = lambda interval: max(3.0 * interval, 15.0)  # noqa: E731


def _longest_run(pairs, threshold, below, gap):
    """最长连续饱和段时长（秒）。pairs: [(ts, value)]，below=True 表示 value<threshold 为饱和"""
    best = 0.0
    start = None
    last = None
    for ts, v in pairs:
        if v is None:
            continue
        sat = (v < threshold) if below else (v > threshold)
        if sat:
            if start is None or (last is not None and (ts - last) > gap):
                start = ts
            last = ts
            best = max(best, last - start)
        else:
            start = None
            last = None
    return round(best, 1)


def _analyze_jsonl(path, interval):
    """流式分析 JSONL：统计 + 饱和段 + 瓶颈排序 + 硬件评估"""
    cols = {"cpu": [], "mem_pct": [], "mem_avail": [], "swap_pct": []}
    sat_pairs = {"cpu": [], "mem": [], "swap": []}
    per_disk = {}
    bad_lines = 0
    total_lines = 0
    t_first = None
    t_last = None

    try:
        with io.open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                total_lines += 1
                try:
                    o = json.loads(line)
                    ts = float(o["ts"])
                except Exception:
                    bad_lines += 1
                    continue
                if t_first is None:
                    t_first = ts
                t_last = ts

                cpu = o.get("cpu_pct")
                if cpu is not None:
                    cols["cpu"].append(cpu)
                    sat_pairs["cpu"].append((ts, cpu))
                mp = o.get("mem_pct")
                if mp is not None:
                    cols["mem_pct"].append(mp)
                ma = o.get("mem_avail")
                mt = o.get("mem_total")
                if ma is not None and mt:
                    cols["mem_avail"].append(ma)
                    avail_pct = 100.0 * ma / mt
                    sat_pairs["mem"].append((ts, avail_pct))
                sp = o.get("swap_pct")
                if sp is not None:
                    cols["swap_pct"].append(sp)
                    sat_pairs["swap"].append((ts, sp))

                for name, m in (o.get("disks") or {}).items():
                    d = per_disk.setdefault(name, {"busy": [], "read": [], "write": [], "iops": []})
                    if m.get("busy_pct") is not None:
                        d["busy"].append(m["busy_pct"])
                        d.setdefault("_pairs", []).append((ts, m["busy_pct"]))
                    if m.get("read_mb_s") is not None:
                        d["read"].append(m["read_mb_s"])
                    if m.get("write_mb_s") is not None:
                        d["write"].append(m["write_mb_s"])
                    if m.get("iops") is not None:
                        d["iops"].append(m["iops"])
    except FileNotFoundError:
        return {"success": False, "error": "记录文件丢失"}

    duration = (t_last - t_first) if (t_first is not None and t_last is not None) else 0.0
    gap = _GAP_BREAK(interval)

    # ---- 饱和持续时长 ----
    cpu_sat = _longest_run(sat_pairs["cpu"], _SAT["cpu_pct"], below=False, gap=gap)
    mem_sat = _longest_run(sat_pairs["mem"], _SAT["mem_avail_pct"], below=True, gap=gap)
    disk_sats = {}
    for name, d in per_disk.items():
        disk_sats[name] = _longest_run(d.get("_pairs", []), _SAT["disk_busy_pct"], below=False, gap=gap)
    disk_sat_max = max(disk_sats.values()) if disk_sats else 0.0

    # ---- 指标统计 ----
    stats = {
        "cpu_pct": _stat_line(cols["cpu"], "%"),
        "mem_used_pct": _stat_line(cols["mem_pct"], "%"),
        "swap_pct": _stat_line(cols["swap_pct"], "%"),
        "mem_available_gb": None,
    }
    if cols["mem_avail"]:
        gb = [v / 1073741824.0 for v in cols["mem_avail"]]
        stats["mem_available_gb"] = {
            "avg": round(sum(gb) / len(gb), 2),
            "p95": round(_p95(gb), 2),
            "max": round(max(gb), 2),
            "min": round(min(gb), 2),
            "unit": "GB", "n": len(gb),
        }
    disk_stats = {}
    for name, d in per_disk.items():
        disk_stats[name] = {
            "busy_pct": _stat_line(d["busy"], "%"),
            "read_mb_s": _stat_line(d["read"], "MB/s"),
            "write_mb_s": _stat_line(d["write"], "MB/s"),
            "iops": _stat_line(d["iops"], "次/s"),
        }

    # ---- 瓶颈排序（压力分 = 饱和时长占比）----
    ratio = lambda sec: (sec / duration) if duration > 0 else 0.0  # noqa: E731
    components = []
    mem_p95_avail_gb = stats["mem_available_gb"]["p95"] if stats["mem_available_gb"] else None
    cpu_p95 = stats["cpu_pct"]["p95"] if stats["cpu_pct"] else None
    disk_p95_busy = 0.0
    worst_disk_name = None
    for name, d in disk_stats.items():
        if d["busy_pct"] and d["busy_pct"]["p95"] > disk_p95_busy:
            disk_p95_busy = d["busy_pct"]["p95"]
            worst_disk_name = name

    components.append({
        "key": "cpu", "name": "CPU",
        "saturation_seconds": cpu_sat, "saturation_ratio": round(ratio(cpu_sat), 3),
        "p95": cpu_p95, "unit": "%",
        "threshold_text": "CPU > %g%% 视为饱和" % _SAT["cpu_pct"],
    })
    components.append({
        "key": "mem", "name": "内存",
        "saturation_seconds": mem_sat, "saturation_ratio": round(ratio(mem_sat), 3),
        "p95": mem_p95_avail_gb, "unit": "GB(可用)",
        "threshold_text": "可用内存 < %g%% 视为饱和" % _SAT["mem_avail_pct"],
    })
    components.append({
        "key": "disk", "name": "磁盘" + ("（%s）" % worst_disk_name if worst_disk_name else ""),
        "saturation_seconds": disk_sat_max, "saturation_ratio": round(ratio(disk_sat_max), 3),
        "p95": disk_p95_busy if disk_p95_busy else None, "unit": "%busy",
        "threshold_text": "磁盘活跃 > %g%% 视为饱和" % _SAT["disk_busy_pct"],
    })
    # 排序：饱和占比优先，其次 p95 距阈值程度
    def _comp_sort(c):
        if c["key"] == "cpu":
            return (-c["saturation_ratio"], -(cpu_p95 or 0))
        if c["key"] == "mem":
            # 可用越少压力越大：p95 可用越小排越前
            return (-c["saturation_ratio"], -(mem_p95_avail_gb or 0))
        return (-c["saturation_ratio"], -(disk_p95_busy or 0))
    components.sort(key=_comp_sort)

    # ---- 硬件评估结论 ----
    assessment = _assess(components, cpu_sat, mem_sat, disk_sat_max,
                         cpu_p95, mem_p95_avail_gb, disk_p95_busy,
                         stats, worst_disk_name)

    return {
        "duration_seconds": round(duration, 1),
        "sample_lines": total_lines,
        "bad_lines": bad_lines,
        "interval": interval,
        "stats": stats,
        "disk_stats": disk_stats,
        "disk_saturation": disk_sats,
        "saturation_thresholds": dict(_SAT),
        "components": components,
        "assessment": assessment,
    }


def _assess(components, cpu_sat, mem_sat, disk_sat,
            cpu_p95, mem_p95_avail_gb, disk_p95_busy, stats, worst_disk_name):
    """硬件优化评估：是否需要升级 + 哪个部件 + 建议方向"""
    # components 里的 saturation_ratio 已是占比
    def r(k):
        for c in components:
            if c["key"] == k:
                return c["saturation_ratio"]
        return 0.0

    items = []
    level = "ok"
    need = False

    # CPU
    if r("cpu") >= 0.2 or (cpu_p95 is not None and cpu_p95 > _SAT["cpu_pct"]):
        need = True
        items.append({
            "component": "CPU", "severity": "upgrade",
            "reason": "CPU 长期饱和（饱和占比 %.0f%%，p95 %.1f%%）" % (r("cpu") * 100, cpu_p95 or 0),
            "suggestion": "升级更多核心/更高主频 CPU；同步改善散热避免高温降频；临时缓解可减少后台常驻程序",
        })
    elif r("cpu") > 0:
        if level == "ok":
            level = "watch"
        items.append({
            "component": "CPU", "severity": "watch",
            "reason": "CPU 偶发饱和（饱和占比 %.0f%%，p95 %.1f%%）" % (r("cpu") * 100, cpu_p95 or 0),
            "suggestion": "观察高负载场景来源；暂无升级必要，持续监测",
        })

    # 内存
    mem_tight = (mem_p95_avail_gb is not None and mem_p95_avail_gb < 1.0) or r("mem") >= 0.2
    mem_watch = (mem_p95_avail_gb is not None and mem_p95_avail_gb < 2.0) or r("mem") > 0
    if mem_tight:
        need = True
        items.append({
            "component": "内存", "severity": "upgrade",
            "reason": "可用内存长期吃紧（p95 可用 %s GB，饱和占比 %.0f%%）"
                      % (mem_p95_avail_gb, r("mem") * 100),
            "suggestion": "扩容物理内存；临时缓解可关闭后台程序/减少开机自启，避免依赖虚拟内存拖慢系统",
        })
    elif mem_watch:
        if level == "ok":
            level = "watch"
        items.append({
            "component": "内存", "severity": "watch",
            "reason": "内存压力偏高（p95 可用 %s GB）" % mem_p95_avail_gb,
            "suggestion": "关注内存占用最高的进程；暂无升级必要，持续监测",
        })

    # 磁盘
    if r("disk") >= 0.2 or (disk_p95_busy and disk_p95_busy > _SAT["disk_busy_pct"]):
        need = True
        items.append({
            "component": "磁盘", "severity": "upgrade",
            "reason": "磁盘持续高活跃（饱和占比 %.0f%%，p95 busy %.1f%%）" % (r("disk") * 100, disk_p95_busy),
            "suggestion": "若当前为机械硬盘，强烈建议升级 NVMe SSD；若已是 SSD，检查剩余空间（将满会显著拖慢 SSD）并排查后台高频写入",
        })
    elif r("disk") > 0:
        if level == "ok":
            level = "watch"
        items.append({
            "component": "磁盘", "severity": "watch",
            "reason": "磁盘偶发高活跃（饱和占比 %.0f%%，p95 busy %.1f%%）" % (r("disk") * 100, disk_p95_busy),
            "suggestion": "定位高 IO 进程；暂无升级必要，持续监测",
        })

    if not items:
        items.append({
            "component": "综合", "severity": "ok",
            "reason": "记录期间未发现持续饱和（CPU/内存/磁盘均在阈值内）",
            "suggestion": "当前硬件满足使用需求，无需升级",
        })
    if need:
        level = "upgrade"

    order = {"ok": 0, "watch": 1, "upgrade": 2}
    level_text = {"ok": "硬件足够，无需升级", "watch": "基本够用，个别部件需关注",
                  "upgrade": "存在瓶颈，建议升级硬件"}[level]
    return {
        "need_upgrade": need, "level": level, "level_text": level_text,
        "items": items,
        "bottleneck": (components[0]["key"] if level != "ok" else None),
    }


# ============================================================
# 4. 报告导出 Markdown
# ============================================================

def handle_perf_record_export(params: dict) -> dict:
    """导出最近（或指定）报告为 Markdown，写入记录目录"""
    rep = _last_report_ref["report"]
    rid = (params.get("record_id") or "").strip()
    if rid and rep and rep.get("record_id") != rid:
        return {"success": False, "error": "指定记录与最近报告不一致（暂只支持最近一份）"}
    if not rep:
        # 尝试从磁盘恢复
        view = handle_perf_record_report({})
        if view.get("found"):
            rep = view["report"]
    if not rep:
        return {"success": False, "error": "暂无可导出的分析报告"}

    md = _report_markdown(rep)
    d = rep.get("file") and os.path.dirname(rep["file"]) or _records_dir()
    name = "perf_report_%s.md" % (rep.get("record_id") or datetime.now().strftime("%Y%m%d_%H%M%S"))
    out = os.path.join(d, name)
    try:
        with io.open(out, "w", encoding="utf-8") as f:
            f.write(md)
    except Exception as e:
        return {"success": False, "error": str(e)}
    return {"success": True, "path": out, "markdown": md}


def _stat_row(title, s, fmt="%s"):
    if not s:
        return "| %s | -- | -- | -- | -- |\n" % title
    return "| %s | %s | %s | %s | %s |\n" % (
        title, s["avg"], s["p95"], s["max"], s["min"])


def _report_markdown(rep):
    a = rep.get("assessment") or {}
    lines = []
    ap = lines.append
    ap("# 性能记录分析报告")
    ap("")
    ap("- 记录ID：%s" % rep.get("record_id"))
    ap("- 生成时间：%s" % rep.get("generated_at"))
    ap("- 采样间隔：%ss" % rep.get("interval"))
    ap("- 记录时长：%ss" % rep.get("elapsed"))
    ap("- 数据文件：`%s`" % rep.get("file"))
    ap("")
    ap("## 综合结论：%s" % a.get("level_text", "--"))
    ap("")
    for it in (a.get("items") or []):
        ap("- **[%s] %s**：%s → %s" % (it.get("severity"), it.get("component"),
                                        it.get("reason"), it.get("suggestion")))
    ap("")
    ap("## 指标统计（avg/p95/max/min）")
    ap("")
    ap("| 指标 | avg | p95 | max | min |")
    ap("|---|---|---|---|---|")
    st = rep.get("stats") or {}
    ap(_stat_row("CPU 占用 (%)", st.get("cpu_pct")))
    ap(_stat_row("内存使用率 (%)", st.get("mem_used_pct")))
    ap(_stat_row("可用内存 (GB)", st.get("mem_available_gb")))
    ap(_stat_row("虚拟内存使用 (%)", st.get("swap_pct")))
    for name, ds in (rep.get("disk_stats") or {}).items():
        ap(_stat_row("磁盘 %s busy (%%)" % name, ds.get("busy_pct")))
        ap(_stat_row("磁盘 %s 读 (MB/s)" % name, ds.get("read_mb_s")))
        ap(_stat_row("磁盘 %s 写 (MB/s)" % name, ds.get("write_mb_s")))
        ap(_stat_row("磁盘 %s IOPS" % name, ds.get("iops")))
    ap("")
    ap("## 饱和判定（阈值：CPU>%g%%、内存可用<%g%%、磁盘busy>%g%%）"
       % (_SAT["cpu_pct"], _SAT["mem_avail_pct"], _SAT["disk_busy_pct"]))
    ap("")
    comps = rep.get("components") or []
    if comps:
        ap("| 组件 | 饱和时长(s) | 占比 | p95 | 阈值说明 |")
        ap("|---|---|---|---|---|")
        for c in comps:
            ap("| %s | %s | %.0f%% | %s %s | %s |" % (
                c.get("name"), c.get("saturation_seconds"),
                (c.get("saturation_ratio") or 0) * 100,
                c.get("p95") if c.get("p95") is not None else "--", c.get("unit") or "",
                c.get("threshold_text")))
    ap("")
    dsk_sat = rep.get("disk_saturation") or {}
    if dsk_sat:
        ap("### 各磁盘饱和时长（秒）")
        ap("")
        for name, sec in dsk_sat.items():
            ap("- %s：%s" % (name, sec))
        ap("")
    ap("### 瓶颈排序")
    ap("")
    for i, c in enumerate(comps, 1):
        ap("%d. %s（压力占比 %.0f%%）" % (i, c.get("name"), (c.get("saturation_ratio") or 0) * 100))
    ap("")
    ap("> 本报告由 winhelper 性能分析模块自动生成。")
    return "\n".join(lines)


# ============================================================
# 路由注册表（供 bridge.py / 独立运行时使用）
# ============================================================

PERF_ROUTES = {
    "/api/perf/snapshot": handle_perf_snapshot,
    "/api/perf/record-start": handle_perf_record_start,
    "/api/perf/record-status": handle_perf_record_status,
    "/api/perf/record-stop": handle_perf_record_stop,
    "/api/perf/record-report": handle_perf_record_report,
    "/api/perf/record-export": handle_perf_record_export,
}


if __name__ == "__main__":
    # 冒烟：python perf_service.py
    import pprint
    print("== snapshot ==")
    r1 = handle_perf_snapshot({})
    pprint.pprint(r1)
    time.sleep(1.2)
    r1b = handle_perf_snapshot({})
    pprint.pprint(r1b)
    print("== record start ==")
    rs = handle_perf_record_start({"interval": "1"})
    pprint.pprint(rs)
    time.sleep(3.5)
    print("== record status ==")
    pprint.pprint(handle_perf_record_status({"record_id": rs["record_id"]}))
    print("== record stop ==")
    rp = handle_perf_record_stop({"record_id": rs["record_id"]})
    pprint.pprint(rp.get("report", {}).get("assessment"))
    print("== export ==")
    pprint.pprint(handle_perf_record_export({}))
