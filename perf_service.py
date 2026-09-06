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

import gc
import hashlib
import io
import json
import os
import random
import shutil
import subprocess

# GUI（无控制台）程序中调用控制台子进程（nvidia-smi/powershell/typeperf）必须隐藏窗口，
# 否则每次调用都会弹出一个 cmd 窗口（温度 2s 轮询 = 不停循环弹窗）
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime

import ctypes

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
    """单次采样（rec 内自维护 io 基线；cpu 基线也在 rec 上；温度取温度轮询缓存）"""
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
        "cpu_temp": _temps_cache.get("cpu_temp"),   # 管理员模式下有值
        "gpu_temp": _temps_cache.get("gpu_temp"),   # 有 NVIDIA GPU 时有值
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
            # 文件名为随机哈希，字典序≠时间序；必须按修改时间取最新
            files.sort(key=lambda f: os.path.getmtime(os.path.join(d, f)))
            path = os.path.join(d, files[-1])
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
    cols = {"cpu": [], "mem_pct": [], "mem_avail": [], "swap_pct": [], "cpu_temp": [], "gpu_temp": []}
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

                ct = o.get("cpu_temp")
                if ct is not None:
                    cols["cpu_temp"].append(ct)
                gt = o.get("gpu_temp")
                if gt is not None:
                    cols["gpu_temp"].append(gt)

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
    if cols["cpu_temp"]:
        stats["cpu_temp_c"] = _stat_line(cols["cpu_temp"], "°C")
    if cols["gpu_temp"]:
        stats["gpu_temp_c"] = _stat_line(cols["gpu_temp"], "°C")
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
    """导出最近（或指定）报告为单文件自包含 HTML，写入记录目录"""
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

    html = _report_html(rep)
    d = rep.get("file") and os.path.dirname(rep["file"]) or _records_dir()
    name = "perf_report_%s.html" % (rep.get("record_id") or datetime.now().strftime("%Y%m%d_%H%M%S"))
    out = os.path.join(d, name)
    try:
        with io.open(out, "w", encoding="utf-8") as f:
            f.write(html)
    except Exception as e:
        return {"success": False, "error": str(e)}
    return {"success": True, "path": out, "html_size": len(html)}


def _stat_row(title, s, fmt="%s"):
    if not s:
        return "| %s | -- | -- | -- | -- |\n" % title
    return "| %s | %s | %s | %s | %s |\n" % (
        title, s["avg"], s["p95"], s["max"], s["min"])


def _html_escape(s):
    import html as _htmllib
    return _htmllib.escape(str(s if s is not None else "--"))


def _html_doc(title, body):
    css = (
        "body{background:#0f1117;color:#e8ebf2;font-family:'Segoe UI','Microsoft YaHei',sans-serif;"
        "margin:0;padding:32px}"
        ".wrap{max-width:920px;margin:0 auto}"
        "h1{font-size:20px;margin:0 0 6px}"
        "h2{font-size:15px;margin:24px 0 10px;border-bottom:1px solid #262b3a;padding-bottom:6px}"
        ".meta{color:#8a93a5;font-size:12.5px;line-height:1.9}"
        ".badge{display:inline-block;font-size:12.5px;border:1px solid #262b3a;"
        "border-radius:20px;padding:2px 12px;vertical-align:middle;margin-left:8px}"
        ".card{background:#171a23;border:1px solid #262b3a;border-left:3px solid #4fc3f7;"
        "border-radius:8px;padding:10px 14px;margin-bottom:10px;font-size:13px;line-height:1.7}"
        ".sug{color:#8a93a5;font-size:12.5px;margin-top:2px}"
        "table{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}"
        "th,td{padding:8px 12px;border-bottom:1px solid #262b3a;text-align:left}"
        "th{color:#8a93a5;font-weight:500;white-space:nowrap}"
        "td{white-space:nowrap}"
        "ol,ul{margin:8px 0 0;padding-left:22px}li{margin:3px 0;font-size:13px}"
        "code{background:#1d212e;border:1px solid #262b3a;border-radius:4px;padding:0 5px;font-size:12px}"
        ".foot{color:#8a93a5;font-size:11.5px;margin-top:24px;border-top:1px solid #262b3a;padding-top:10px}"
    )
    return ('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
            "<title>%s</title><style>%s</style></head><body><div class=\"wrap\">%s</div></body></html>"
            % (_html_escape(title), css, body))


def _report_html(rep):
    """记录分析报告 → 单文件自包含 HTML（深色风格，ADR-012）"""
    a = rep.get("assessment") or {}
    color = {"ok": "#81c784", "watch": "#ffd54f", "upgrade": "#e57373"}.get(a.get("level"), "#aab3c5")
    sev_color = {"ok": "#81c784", "watch": "#ffd54f", "upgrade": "#e57373"}
    h = []
    ap = h.append
    ap('<h1>观枢终端平台｜EyeTerm · 性能分析报告<span class="badge" style="color:%s;border-color:%s">%s</span></h1>'
       % (color, color, _html_escape(a.get("level_text"))))
    ap('<div class="meta">记录ID：%s · 生成时间：%s · 采样间隔：%ss · 记录时长：%ss<br>'
       '数据文件：<code>%s</code></div>'
       % (_html_escape(rep.get("record_id")), _html_escape(rep.get("generated_at")),
          _html_escape(rep.get("interval")), _html_escape(rep.get("elapsed")),
          _html_escape(rep.get("file"))))

    ap('<h2>结论与建议</h2>')
    for it in (a.get("items") or []):
        c = sev_color.get(it.get("severity"), "#aab3c5")
        ap('<div class="card"><span class="badge" style="color:%s;border-color:%s">[%s]</span> '
           '<b>%s</b>：%s<div class="sug">→ %s</div></div>'
           % (c, c, _html_escape(it.get("severity")), _html_escape(it.get("component")),
              _html_escape(it.get("reason")), _html_escape(it.get("suggestion"))))

    ap('<h2>指标统计（avg / p95 / max / min）</h2>')
    ap('<table><tr><th>指标</th><th>avg</th><th>p95</th><th>max</th><th>min</th></tr>')
    st = rep.get("stats") or {}

    def _row(title, s):
        if not s:
            return '<tr><td>%s</td><td colspan="4" style="color:#8a93a5">--</td></tr>' % _html_escape(title)
        return ('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>'
                % (_html_escape(title), s["avg"], s["p95"], s["max"], s["min"]))

    ap(_row("CPU 占用 (%)", st.get("cpu_pct")))
    ap(_row("内存使用率 (%)", st.get("mem_used_pct")))
    ap(_row("可用内存 (GB)", st.get("mem_available_gb")))
    ap(_row("虚拟内存使用 (%)", st.get("swap_pct")))
    if st.get("cpu_temp_c"):
        ap(_row("CPU 温度 (°C)", st.get("cpu_temp_c")))
    if st.get("gpu_temp_c"):
        ap(_row("GPU 温度 (°C)", st.get("gpu_temp_c")))
    for name, ds in (rep.get("disk_stats") or {}).items():
        ap(_row("磁盘 %s 活跃 (%%)" % name, ds.get("busy_pct")))
        ap(_row("磁盘 %s 读 (MB/s)" % name, ds.get("read_mb_s")))
        ap(_row("磁盘 %s 写 (MB/s)" % name, ds.get("write_mb_s")))
        ap(_row("磁盘 %s IOPS" % name, ds.get("iops")))
    ap('</table>')

    ap('<h2>饱和判定（阈值：CPU&gt;%g%%、内存可用&lt;%g%%、磁盘活跃&gt;%g%%）</h2>'
       % (_SAT["cpu_pct"], _SAT["mem_avail_pct"], _SAT["disk_busy_pct"]))
    comps = rep.get("components") or []
    ap('<table><tr><th>#</th><th>组件</th><th>饱和时长(s)</th><th>占比</th><th>p95</th><th>阈值说明</th></tr>')
    for i, c in enumerate(comps, 1):
        p95 = c.get("p95")
        ap('<tr><td>#%d</td><td>%s</td><td>%s</td><td>%.0f%%</td><td>%s %s</td><td>%s</td></tr>'
           % (i, _html_escape(c.get("name")), _html_escape(c.get("saturation_seconds")),
              (c.get("saturation_ratio") or 0) * 100,
              _html_escape(p95 if p95 is not None else "--"), _html_escape(c.get("unit") or ""),
              _html_escape(c.get("threshold_text"))))
    ap('</table>')
    dsk_sat = rep.get("disk_saturation") or {}
    if dsk_sat:
        ap('<div class="meta" style="margin-top:8px">各磁盘饱和时长：' +
           "；".join("%s %ss" % (_html_escape(k), v) for k, v in dsk_sat.items()) + "</div>")
    ap('<h2>瓶颈排序</h2><ol>')
    for c in comps:
        ap('<li>%s（压力占比 %.0f%%）</li>' % (_html_escape(c.get("name")), (c.get("saturation_ratio") or 0) * 100))
    ap('</ol>')
    ap('<div class="foot">本报告由 winhelper 性能分析模块自动生成；单文件自包含 HTML，可直接浏览器打开或打印。</div>')
    return _html_doc("观枢终端平台｜EyeTerm - 性能分析报告 %s" % rep.get("record_id"), "\n".join(h))


# ============================================================
# 5. 性能检测（压测）——硬约束见 ADR-008/009：每阶段 ≤60s，编排总时长 58s
# ============================================================

_STRESS_STAGE_SECONDS = {"disk": 25, "cpu": 15, "mem": 10, "gpu": 8}
_STRESS_STAGE_ORDER = ("disk", "cpu", "mem", "gpu")
_STRESS_RUNNERS = {}
_stress_tasks = {}
_stress_lock = threading.Lock()


def _scan_recent_errors(minutes, sources):
    """只读扫描 System 事件日志最近 N 分钟的指定来源错误/警告（pywin32 缺失返回 None）"""
    try:
        import win32evtlog  # 可选依赖（主应用已含 pywin32）
    except ImportError:
        return None
    found = []
    try:
        hand = win32evtlog.OpenEventLog(None, "System")
    except Exception:
        return []
    try:
        flags = win32evtlog.EVENTLOG_BACKWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ
        cutoff = time.time() - minutes * 60
        for _ in range(50):  # 每批最多约2000条，足够回溯几分钟
            try:
                evs = win32evtlog.ReadEventLog(hand, flags, 0)
            except Exception:
                break
            if not evs:
                break
            stop = False
            for ev in evs:
                try:
                    t = time.mktime(ev.TimeGenerated.timetuple())
                except Exception:
                    continue
                if t < cutoff:
                    stop = True
                    break
                src = ev.SourceName or ""
                if any(s.lower() in src.lower() for s in sources) and ev.EventType in (1, 2):
                    found.append({
                        "source": src,
                        "event_id": ev.EventID & 0xFFFF,
                        "time": time.strftime("%H:%M:%S", time.localtime(t)),
                    })
            if stop:
                break
    except Exception:
        pass
    finally:
        try:
            win32evtlog.CloseEventLog(hand)
        except Exception:
            pass
    return found


def _looks_dgpu(name):
    """独显命名判定（ADR-009）：Intel 仅 Arc；NVIDIA 系列均独显；AMD 需 RX/R9/Pro/FirePro"""
    n = (name or "").lower()
    if not n:
        return False
    if "intel" in n:
        return "arc" in n
    if "geforce" in n or "gtx" in n or "rtx" in n or "quadro" in n or "tesla" in n or "nvidia" in n:
        return True
    if "radeon" in n:
        return ("rx" in n) or ("r9" in n) or ("pro" in n) or ("firepro" in n)
    return ("arc" in n) or ("firepro" in n)


def _detect_gpus():
    try:
        import wmi  # 可选依赖（主应用已含 wmi==1.5.1）
        return [v.Name for v in wmi.WMI().Win32_VideoController() if v.Name]
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }"],
            capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW)
        if r.returncode == 0:
            return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    except Exception:
        pass
    return []


def _gpu_metrics():
    """GPU 指标：nvidia-smi 优先，降级 typeperf GPU Engine 计数器（多实例求和夹取100）"""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, creationflags=_NO_WINDOW)
        if r.returncode == 0 and r.stdout.strip():
            parts = [p.strip() for p in r.stdout.strip().splitlines()[0].split(",")]
            if len(parts) >= 3:
                return {"util": float(parts[0]), "mem_mb": float(parts[1]),
                        "temp": float(parts[2]), "src": "nvidia-smi"}
    except Exception:
        pass
    try:
        r = subprocess.run(["typeperf", r"\GPU Engine(*)\Utilization Percentage", "-sc", "1"],
                           capture_output=True, text=True, timeout=8, creationflags=_NO_WINDOW)
        if r.returncode == 0 and r.stdout:
            total = 0.0
            for ln in r.stdout.splitlines():
                ln = ln.strip()
                if not ln.startswith('"'):
                    continue
                for cell in ln.split('","'):
                    c = cell.strip('"')
                    try:
                        total += float(c)
                    except ValueError:
                        continue
            if total > 0:
                return {"util": round(min(total, 100.0), 1), "mem_mb": None, "temp": None,
                        "src": "GPU Engine计数器"}
    except Exception:
        pass
    return None


# ---------- 阶段 1：磁盘读写上限 ----------

def _run_disk_stage(task, cancel, seconds, out, params):
    drive = params.get("drive") or os.environ.get("SystemDrive") or "C:"
    if not drive.endswith("\\"):
        drive += "\\"
    sysd = os.environ.get("SystemDrive") or "C:"
    if not sysd.endswith("\\"):
        sysd += "\\"
    # 系统盘用用户 TEMP 目录（权限稳妥）；其他盘用盘根临时目录（禁止碰用户数据）
    base = tempfile.gettempdir() if os.path.normcase(drive) == os.path.normcase(sysd) else drive
    tmp = tempfile.mkdtemp(prefix="winhelper_perf_", dir=base)
    fpath = os.path.join(tmp, "seq.bin")
    out["drive"] = drive
    write_samples = []
    read_samples = []
    written = 0
    read_total = 0
    try:
        buf = b"\xa5" * (64 * 1024 * 1024)  # 64MB 大缓冲
        half = seconds / 2.0
        # ---- 顺序写 ----
        t0 = time.time()
        last_t = t0
        last_b = 0
        with io.open(fpath, "wb") as f:
            while True:
                if time.time() - t0 >= half or cancel.is_set():
                    break
                f.write(buf)
                f.flush()
                written += len(buf)
                now = time.time()
                if now - last_t >= 0.5:
                    rate = (written - last_b) / (now - last_t) / 1048576.0
                    write_samples.append(rate)
                    task["stage_detail"]["live"] = {"phase": "顺序写", "mb_s": round(rate, 1)}
                    last_t = now
                    last_b = written
        w_elapsed = max(time.time() - t0, 0.001)
        out["write"] = {
            "peak_mb_s": round(max(write_samples), 1) if write_samples else round(written / w_elapsed / 1048576.0, 1),
            "avg_mb_s": round(written / w_elapsed / 1048576.0, 1),
        }
        # ---- 读（顺序回读至 EOF 后随机偏移交替；注明缓存影响）----
        fsize = os.path.getsize(fpath)
        chunk = 32 * 1024 * 1024
        t0 = time.time()
        last_t = t0
        last_b = 0
        with io.open(fpath, "rb") as f:
            while True:
                if time.time() - t0 >= half or cancel.is_set():
                    break
                data = f.read(chunk)
                if not data:
                    try:
                        f.seek(random.randrange(0, max(1, fsize - chunk)))
                    except ValueError:
                        f.seek(0)
                read_total += len(data)
                now = time.time()
                if now - last_t >= 0.5:
                    rate = (read_total - last_b) / (now - last_t) / 1048576.0
                    read_samples.append(rate)
                    task["stage_detail"]["live"] = {"phase": "顺序+随机读", "mb_s": round(rate, 1)}
                    last_t = now
                    last_b = read_total
        r_elapsed = max(time.time() - t0, 0.001)
        out["read"] = {
            "peak_mb_s": round(max(read_samples), 1) if read_samples else round(read_total / r_elapsed / 1048576.0, 1),
            "avg_mb_s": round(read_total / r_elapsed / 1048576.0, 1),
            "note": "读速受OS文件缓存影响，为上限乐观值",
        }
        # ---- 结论（ADR-009 阈值）----
        w = out["write"]["avg_mb_s"]
        r = out["read"]["avg_mb_s"]
        if w >= 200 and r >= 300:
            out["conclusion"] = {"level": "ok", "text": "磁盘性能满足日常使用（SSD 级，写 %d / 读 %d MB/s）" % (w, r),
                                 "suggestion": "无需升级"}
        elif w < 150:
            out["conclusion"] = {"level": "bad",
                                 "text": "磁盘为机械盘水平（顺序写 %d MB/s）" % w,
                                 "suggestion": "建议升级 SSD（系统盘优先）"}
        else:
            out["conclusion"] = {"level": "edge",
                                 "text": "磁盘性能处于边缘水平（写 %d / 读 %d MB/s）" % (w, r),
                                 "suggestion": "轻度使用尚可；重度读写场景建议升级 SSD"}
    finally:
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass
        out["tmp_cleaned"] = not os.path.exists(fpath)


# ---------- 阶段 2：CPU 稳定性 ----------

def _run_cpu_stage(task, cancel, seconds, out, params):
    n = max(1, (psutil.cpu_count() or 2) - 1)
    stop = threading.Event()
    buf = bytes(256 * 1024)  # hashlib >2047B 在 C 层释放 GIL，多线程真满载
    errors = []

    def _burner():
        try:
            sha = hashlib.sha256
            while not stop.is_set():
                sha(buf).hexdigest()
        except Exception as e:  # 负载线程异常视为不稳定信号
            errors.append(str(e))

    threads = [threading.Thread(target=_burner, daemon=True) for _ in range(n)]
    state = {"ct": None, "ct_t": None}
    samples = []
    temp_samples = []
    t0 = time.time()
    for t in threads:
        t.start()
    while True:
        if time.time() - t0 >= seconds or cancel.is_set():
            break
        pct = _cpu_pct_from_state(state)
        if pct is not None:
            samples.append(round(pct, 1))
            tv = _temps_cache.get("cpu_temp")  # 管理员模式下温度轮询在更新
            if tv:
                temp_samples.append(tv)
            live = {"cpu_pct": round(pct, 1), "workers": n}
            if tv:
                live["cpu_temp"] = tv
            task["stage_detail"]["live"] = live
        stop.wait(1.0)
    stop.set()
    for t in threads:
        t.join(timeout=2)
    elapsed = int(time.time() - t0)
    out.update({"workers": n, "duration": elapsed, "worker_errors": errors,
                "cpu_temp_max": (round(max(temp_samples), 1) if temp_samples else None)})
    if samples:
        out["samples"] = {"avg": round(sum(samples) / len(samples), 1), "max": round(max(samples), 1),
                          "min": round(min(samples), 1), "unit": "%", "n": len(samples)}
        over = [s for s in samples if s > 90]
        out["achieved"] = len(over) >= max(1, len(samples) // 2)
    else:
        out["achieved"] = False
    out["status_gaps"] = task.get("_status_gaps", 0)
    out["post_events"] = _scan_recent_errors(5, ("WHEA-Logger", "Kernel-Power")) or []
    stable = (not errors) and bool(samples) and bool(out.get("achieved")) \
        and out["status_gaps"] <= 2 and not out["post_events"]
    avg = out.get("samples", {}).get("avg", 0)
    out["conclusion"] = {
        "level": "stable" if stable else "unstable",
        "text": ("满载 %ds（%d 线程），平均 %.1f%%，系统响应正常，未见硬件级错误" % (elapsed, n, avg)
                 if stable else "CPU 满载期间存在异常（线程错误/响应间隙/WHEA事件/未达标）"),
        "suggestion": ("CPU 满载下系统运行稳定，满足高负载使用" if stable
                       else "建议排查高负载异常：散热、电源计划、驱动与后台程序"),
    }


# ---------- 阶段 3：内存稳定性 ----------

def _run_mem_stage(task, cancel, seconds, out, params):
    vm0 = psutil.virtual_memory()
    total = vm0.total
    before_pct = vm0.percent
    floor = max(1.5 * 1024 ** 3, 0.08 * total)  # 红线：可用内存硬下限
    step = 256 * 1024 * 1024
    touched = bytes(4 * 1024 * 1024)
    blocks = []
    stop_flag = threading.Event()
    peak_pct = before_pct
    err = None
    reached_target = False
    hit_floor = False

    def _watchdog():
        # 临界可用内存 800MB：立即全部释放（ADR-009 安全底线）
        while not stop_flag.is_set() and not cancel.is_set():
            try:
                if psutil.virtual_memory().available < 800 * 1024 * 1024:
                    stop_flag.set()
                    return
            except Exception:
                return
            time.sleep(0.15)

    wt = threading.Thread(target=_watchdog, daemon=True)
    wt.start()
    t0 = time.time()
    try:
        # 渐进分配 + 触摸（真实提交）
        while time.time() - t0 < seconds and not cancel.is_set() and not stop_flag.is_set():
            vm = psutil.virtual_memory()
            peak_pct = max(peak_pct, vm.percent)
            if vm.percent >= 90:
                reached_target = True
                break
            if vm.available - step < floor:
                hit_floor = True
                break
            try:
                ba = bytearray(step)
                for off in range(0, step, len(touched)):
                    ba[off:off + len(touched)] = touched
                blocks.append(ba)
            except MemoryError:
                err = "MemoryError（分配阶段，已安全停止）"
                break
            task["stage_detail"]["live"] = {"mem_pct": vm.percent,
                                            "blocks_gb": round(len(blocks) * step / 1073741824.0, 1)}
        # 维持至阶段结束
        while time.time() - t0 < seconds and not cancel.is_set() and not stop_flag.is_set():
            vm = psutil.virtual_memory()
            peak_pct = max(peak_pct, vm.percent)
            task["stage_detail"]["live"] = {"mem_pct": vm.percent,
                                            "blocks_gb": round(len(blocks) * step / 1073741824.0, 1)}
            time.sleep(0.4)
    except MemoryError as e:
        err = "MemoryError（%s）" % e
    finally:
        blocks.clear()
        gc.collect()
        time.sleep(0.6)
        after_pct = psutil.virtual_memory().percent
    out.update({
        "target_percent": 90, "achieved": reached_target,
        "peak_percent": round(peak_pct, 1), "before_percent": round(before_pct, 1),
        "percent_after_release": round(after_pct, 1),
        "floor_gb": round(floor / 1073741824.0, 2), "hit_floor": hit_floor, "error": err,
    })
    released_ok = after_pct <= before_pct + 5
    stable = (err is None) and released_ok and task.get("_status_gaps", 0) <= 2
    if hit_floor:
        text = "接近安全红线（可用下限 %.1fGB）即停止推进，峰值占用 %.1f%%，释放后回落 %.1f%%" \
               % (floor / 1073741824.0, peak_pct, after_pct)
    elif reached_target:
        text = "系统占用推至 %.1f%% 并维持，释放后回落至 %.1f%%" % (peak_pct, after_pct)
    elif err:
        text = "分配触达系统上限触发保护性停止（MemoryError 已安全捕获，系统未崩溃），峰值 %.1f%%，释放后回落 %.1f%%；90%% 维持测试未完成" \
               % (peak_pct, after_pct)
    else:
        text = "峰值占用 %.1f%%（受阶段时长/安全底线限制未达 90%%），释放后回落 %.1f%%" % (peak_pct, after_pct)
    out["conclusion"] = {
        "level": "stable" if stable else "unstable",
        "text": text + ("，全程无异常" if stable else "，期间出现异常"),
        "suggestion": ("内存高占用下系统运行稳定，满足多任务使用" if stable
                       else "建议排查高内存占用下的稳定性（关闭后台程序/检查内存条）"),
    }


# ---------- 阶段 4：GPU 稳定性 ----------

def _run_gpu_stage(task, cancel, seconds, out, params):
    cards = _detect_gpus()
    out["cards"] = [{"name": c, "dedicated": _looks_dgpu(c)} for c in cards]
    dgpus = [c for c in cards if _looks_dgpu(c)]
    if not dgpus:
        out["status"] = "skipped"
        out["reason"] = "未检测到独立显卡，GPU检测跳过"
        out["conclusion"] = {"level": "skip", "text": "未检测到独立显卡",
                             "suggestion": "核显机器无需 GPU 压测"}
        return
    task["need_webgl"] = True  # 前端轮询到该标志后启动 WebGL 满载渲染
    metrics = []
    metrics_unavailable = False
    t0 = time.time()
    while True:
        if time.time() - t0 >= seconds or cancel.is_set():
            break
        m = _gpu_metrics()
        if m:
            metrics.append(m)
            task["stage_detail"]["live"] = {"gpu_util": m.get("util"),
                                            "gpu_temp": m.get("temp"), "src": m.get("src")}
        else:
            metrics_unavailable = True
            task["stage_detail"]["live"] = {"note": "指标不可用（WebGL负载运行中）"}
        time.sleep(1.0)
    task["need_webgl"] = False
    post = _scan_recent_errors(5, ("nvlddmkm", "Display")) or []
    out.update({"metrics_count": len(metrics), "metrics_unavailable": metrics_unavailable,
                "post_events": post})
    if metrics:
        utils = [m["util"] for m in metrics if m.get("util") is not None]
        temps = [m["temp"] for m in metrics if m.get("temp") is not None]
        out["util_max"] = max(utils) if utils else None
        out["util_avg"] = round(sum(utils) / len(utils), 1) if utils else None
        out["temp_max"] = max(temps) if temps else None
        out["src"] = metrics[0].get("src")
    stable = (not post) and (len(metrics) >= 2 or metrics_unavailable)
    detail = "，峰值占用 %s%%" % out["util_max"] if out.get("util_max") is not None \
        else "（指标不可用）"
    out["conclusion"] = {
        "level": "stable" if stable else "unstable",
        "text": ("满载 %ds，无驱动重置（TDR）事件%s" % (seconds, detail) if stable
                 else "满载期间出现异常（驱动事件/采样中断）"),
        "suggestion": ("GPU 满载下运行稳定" if stable else "建议更新显卡驱动并排查满载异常"),
    }


_STRESS_RUNNERS["disk"] = _run_disk_stage
_STRESS_RUNNERS["cpu"] = _run_cpu_stage
_STRESS_RUNNERS["mem"] = _run_mem_stage
_STRESS_RUNNERS["gpu"] = _run_gpu_stage


def _stress_overall(stages):
    parts = []
    levels = []
    for key in _STRESS_STAGE_ORDER:
        st = stages.get(key)
        if not st:
            continue
        c = st.get("conclusion") or {}
        if c.get("level"):
            levels.append(c["level"])
        label = {"disk": "磁盘", "cpu": "CPU", "mem": "内存", "gpu": "GPU"}[key]
        if st.get("status") == "skipped":
            parts.append("%s：跳过（%s）" % (label, st.get("reason", "")))
        else:
            parts.append("%s：%s" % (label, c.get("text", "")))
    if any(l in ("bad", "unstable") for l in levels):
        overall = {"level": "bad", "text": "存在未达标的部件，建议优化"}
    elif any(l == "edge" for l in levels):
        overall = {"level": "edge", "text": "整体可用，个别部件处于边缘水平"}
    else:
        overall = {"level": "ok", "text": "各部件检测通过，硬件满足使用需求"}
    overall["detail"] = parts
    return overall


def _stress_worker(task):
    try:
        for stage_key, secs in task["plan"]:
            if task["_cancel"].is_set():
                break
            task["current_stage"] = stage_key
            task["_stage_started"] = time.time()
            task["stage_detail"] = {"name": stage_key, "seconds": secs, "live": {}}
            out = {}
            try:
                runner = _STRESS_RUNNERS[stage_key]
                runner(task, task["_cancel"], secs, out, task.get("params", {}).get(stage_key, {}))
                if "status" not in out:
                    out["status"] = "cancelled" if task["_cancel"].is_set() else "done"
            except Exception as e:
                out = {"status": "error", "error": str(e)}
            task["result"]["stages"][stage_key] = out
            task["stage_detail"] = None
            task["current_stage"] = None
        task["result"]["overall"] = _stress_overall(task["result"]["stages"])
        task["status"] = "cancelled" if task["_cancel"].is_set() else "done"
        task["finished_at"] = time.time()
    except Exception as e:
        task["status"] = "error"
        task["error"] = str(e)
    finally:
        task["need_webgl"] = False
        task["stage_detail"] = None


def handle_perf_stress_start(params: dict) -> dict:
    """开始压测：mode=full|disk|cpu|mem|gpu；同任务不幂等（running 时明确拒绝，ADR-008）"""
    if psutil is None:
        return {"success": False, "error": "缺少依赖 psutil，请先 pip install psutil"}
    mode = (params.get("mode") or "full").strip().lower()
    if mode not in ("full",) + _STRESS_STAGE_ORDER:
        return {"success": False, "error": "未知压测模式: %s" % mode}
    with _stress_lock:
        for t in _stress_tasks.values():
            if t["status"] == "running":
                return {"success": False, "error": "已有压测进行中（%s），请先取消或等待完成" % t["mode"]}
        if mode == "full":
            plan = [(k, _STRESS_STAGE_SECONDS[k]) for k in _STRESS_STAGE_ORDER]
        else:
            plan = [(mode, _STRESS_STAGE_SECONDS[mode])]
        sid = uuid.uuid4().hex[:12]
        task = {
            "id": sid, "mode": mode, "plan": plan,
            "total_seconds": sum(s for _, s in plan),
            "status": "running", "started_at": time.time(), "finished_at": None,
            "current_stage": None, "stage_detail": None, "_stage_started": None,
            "result": {"stages": {}}, "error": None, "need_webgl": False,
            "_cancel": threading.Event(), "_status_gaps": 0, "_last_status_ts": time.time(),
            "params": {"disk": {"drive": (params.get("drive") or "").strip()}},
        }
        _stress_tasks[sid] = task
    threading.Thread(target=_stress_worker, args=(task,), daemon=True).start()
    return {"success": True, "stress_id": sid, "mode": mode, "total_seconds": task["total_seconds"],
            "plan": [{"stage": k, "seconds": s} for k, s in plan]}


def _stress_task_view(task, now):
    stage_detail = None
    if task.get("stage_detail"):
        sd = dict(task["stage_detail"])
        st = task.get("_stage_started") or now
        sd["elapsed"] = round(now - st, 1)
        sd["remaining"] = round(max(0.0, sd.get("seconds", 0) - (now - st)), 1)
        stage_detail = sd
    return {
        "id": task["id"], "mode": task["mode"], "status": task["status"],
        "total_seconds": task["total_seconds"],
        "elapsed": round(now - task["started_at"], 1),
        "remaining": (round(max(0.0, task["total_seconds"] - (now - task["started_at"])), 1)
                      if task["status"] == "running" else 0),
        "plan": [{"stage": k, "seconds": s} for k, s in task["plan"]],
        "current_stage": task["current_stage"],
        "stage_detail": stage_detail,
        "need_webgl": task.get("need_webgl", False),
        "result": (task["result"] if task["status"] in ("done", "cancelled", "error") else None),
        "error": task["error"],
    }


def handle_perf_stress_status(params: dict) -> dict:
    """轮询压测状态：当前阶段/剩余秒/实时指标/完成后结果"""
    sid = (params.get("stress_id") or "").strip()
    with _stress_lock:
        task = _stress_tasks.get(sid)
    if task is None:
        return {"success": False, "error": "压测任务不存在或已过期"}
    now = time.time()
    if task["status"] == "running":
        gap = now - task.get("_last_status_ts", now)
        if gap > 5.0:  # 响应间隙：主进程可能被压测拖慢的佐证（ADR-009）
            task["_status_gaps"] = task.get("_status_gaps", 0) + 1
        task["_last_status_ts"] = now
    return {"success": True, "task": _stress_task_view(task, now)}


def handle_perf_stress_cancel(params: dict) -> dict:
    """立即取消：停所有负载并释放（磁盘临时文件在 runner finally 必删）"""
    sid = (params.get("stress_id") or "").strip()
    with _stress_lock:
        task = _stress_tasks.get(sid)
    if task is None:
        return {"success": False, "error": "压测任务不存在"}
    if task["status"] == "running":
        task["_cancel"].set()
        task["need_webgl"] = False
        return {"success": True, "cancelled": True}
    return {"success": True, "cancelled": False}


def _stress_html(task):
    """压测报告 → 单文件自包含 HTML（深色风格，ADR-012）"""
    res = task.get("result") or {}
    ov = res.get("overall") or {}
    color = {"ok": "#81c784", "edge": "#ffd54f", "bad": "#e57373"}.get(ov.get("level"), "#aab3c5")
    lv_color = {"ok": "#81c784", "stable": "#81c784", "edge": "#ffd54f",
                "bad": "#e57373", "unstable": "#e57373", "skip": "#8a93a5"}
    h = []
    ap = h.append
    ap('<h1>观枢终端平台｜EyeTerm · 性能检测报告<span class="badge" style="color:%s;border-color:%s">%s</span></h1>'
       % (color, color, _html_escape(ov.get("text"))))
    ap('<div class="meta">任务ID：%s（模式 %s） · 编排时长：%s 秒（计划） · 状态：%s<br>'
       '生成时间：%s</div>'
       % (_html_escape(task["id"]), _html_escape(task["mode"]),
          _html_escape(task["total_seconds"]), _html_escape(task["status"]),
          _html_escape(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))))
    detail = ov.get("detail") or []
    if detail:
        ap('<h2>综合结论</h2><ul>')
        for d in detail:
            ap('<li>%s</li>' % _html_escape(d))
        ap('</ul>')
    ap('<h2>各阶段结果</h2>')
    labels = {"disk": "磁盘读写上限", "cpu": "CPU 稳定性", "mem": "内存稳定性", "gpu": "GPU 稳定性"}
    for key in _STRESS_STAGE_ORDER:
        st = (res.get("stages") or {}).get(key)
        if not st:
            continue
        c = st.get("conclusion") or {}
        cc = lv_color.get(c.get("level"), "#aab3c5")
        ap('<div class="card" style="border-left-color:%s"><b>%s</b>'
           '<span class="badge" style="color:%s;border-color:%s">%s</span>'
           % (cc, _html_escape(labels[key]), cc, cc, _html_escape(c.get("level") or st.get("status") or "")))
        ap('<ul>')
        if st.get("status") == "skipped":
            ap('<li>%s</li>' % _html_escape(st.get("reason", "跳过")))
        else:
            if st.get("write"):
                ap('<li>顺序写：<b>%s MB/s</b>（峰值 %s MB/s）</li>'
                   % (st["write"].get("avg_mb_s"), st["write"].get("peak_mb_s")))
            if st.get("read"):
                ap('<li>顺序+随机读：<b>%s MB/s</b>（峰值 %s MB/s，%s）</li>'
                   % (st["read"].get("avg_mb_s"), st["read"].get("peak_mb_s"),
                      _html_escape(st["read"].get("note", ""))))
            if st.get("samples"):
                s = st["samples"]
                ap('<li>CPU 占用：avg <b>%s%%</b> / max %s%%（%s 线程，达标 %s）</li>'
                   % (s.get("avg"), s.get("max"), st.get("workers", 0),
                      "是" if st.get("achieved") else "否"))
            if st.get("cpu_temp_max") is not None:
                ap('<li>CPU 温度峰值：%s°C</li>' % st.get("cpu_temp_max"))
            if st.get("peak_percent") is not None:
                ap('<li>内存峰值占用：<b>%s%%</b>（目标 ≥%s%%，释放后 %s%%）</li>'
                   % (st.get("peak_percent"), st.get("target_percent"),
                      st.get("percent_after_release")))
            if st.get("util_max") is not None:
                ap('<li>GPU 峰值占用：<b>%s%%</b>（来源 %s）</li>'
                   % (st.get("util_max"), _html_escape(st.get("src"))))
            if st.get("reason") and st.get("status") != "skipped":
                ap('<li>%s</li>' % _html_escape(st.get("reason")))
        if c.get("text"):
            ap('<li>结论：%s</li>' % _html_escape(c.get("text")))
        if c.get("suggestion"):
            ap('<li>建议：%s</li>' % _html_escape(c.get("suggestion")))
        ap('</ul></div>')
    ap('<div class="foot">报告由 winhelper 性能检测模块自动生成；压测编排硬上限 60s，磁盘测试临时文件已清理。'
       '单文件自包含 HTML，可直接浏览器打开或打印。</div>')
    return _html_doc("观枢终端平台｜EyeTerm - 性能检测报告 %s" % task["id"], "\n".join(h))


def handle_perf_stress_export(params: dict) -> dict:
    """导出最近一次压测报告为单文件自包含 HTML 到记录目录"""
    with _stress_lock:
        done = [t for t in _stress_tasks.values() if t["status"] in ("done", "cancelled")]
    if not done:
        return {"success": False, "error": "暂无可导出的压测结果（先完成一次压测）"}
    task = done[-1]
    html = _stress_html(task)
    out = os.path.join(_records_dir(), "perf_stress_%s.html" % task["id"])
    try:
        with io.open(out, "w", encoding="utf-8") as f:
            f.write(html)
    except Exception as e:
        return {"success": False, "error": str(e)}
    return {"success": True, "path": out, "html_size": len(html)}


# ============================================================
# 6. 硬件规格信息（静态，进程内缓存；PowerShell CIM 一次性子进程 + 降级）
# ============================================================

_hwinfo_cache = {"data": None}
_SMBIOS_MEM_TYPE = {20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4", 34: "DDR5"}

_PS_CIM_SCRIPT = (
    "$ErrorActionPreference='SilentlyContinue';"
    "Write-Output '==CPU==';"
    "Get-CimInstance Win32_Processor | ForEach-Object { '{0}|{1}|{2}|{3}|{4}' -f $_.Name,$_.NumberOfCores,$_.NumberOfLogicalProcessors,$_.MaxClockSpeed,$_.CurrentClockSpeed };"
    "Write-Output '==MEM==';"
    "Get-CimInstance Win32_PhysicalMemory | ForEach-Object { '{0}|{1}|{2}|{3}' -f $_.DeviceLocator,$_.Capacity,$_.SMBIOSMemoryType,$_.ConfiguredClockSpeed };"
    "Write-Output '==DISK==';"
    "Get-CimInstance Win32_DiskDrive | ForEach-Object { '{0}|{1}|{2}|{3}' -f $_.Index,$_.Model,$_.Size,$_.InterfaceType };"
    "Write-Output '==PD==';"
    "Get-PhysicalDisk | ForEach-Object { '{0}|{1}|{2}' -f $_.DeviceId,$_.MediaType,$_.BusType };"
    "Write-Output '==D2P==';"
    "Get-CimInstance Win32_DiskDriveToDiskPartition | ForEach-Object { '{0}=>{1}' -f $_.Antecedent.DeviceID,$_.Dependent.DeviceID };"
    "Write-Output '==P2L==';"
    "Get-CimInstance Win32_LogicalDiskToPartition | ForEach-Object { '{0}=>{1}' -f $_.Antecedent.DeviceID,$_.Dependent.DeviceID };"
    "Write-Output '==GPU==';"
    "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name };"
    "Write-Output '==OS==';"
    "Get-CimInstance Win32_OperatingSystem | ForEach-Object { '{0}|{1}|{2}' -f $_.Caption,$_.Version,$_.BuildNumber };"
    "Write-Output '==NET==';"
    "Get-CimInstance Win32_NetworkAdapterConfiguration -Filter \"IPEnabled=True\" | ForEach-Object { '{0}|{1}|{2}|{3}|{4}|{5}' -f $_.Description,$_.MACAddress,(($_.IPAddress | Where-Object { $_ -notmatch ':' }) -join '/'),(($_.IPAddress | Where-Object { $_ -match ':' }) -join '/'),(($_.DefaultIPGateway) -join '/'),(($_.DNSServerSearchOrder) -join '/') };"
)


def _ps_cim_dump():
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", _PS_CIM_SCRIPT],
            capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW)
        if r.returncode == 0 and r.stdout and "==CPU==" in r.stdout:
            return r.stdout
    except Exception:
        pass
    return None


def _media_of(model, mediatype, bustype):
    """SSD/HDD 判定链（ADR-010）：Get-PhysicalDisk.MediaType → BusType=NVMe → 模型名启发式 → 未知"""
    mt = (mediatype or "").strip().lower()
    if mt == "ssd":
        return "SSD"
    if mt == "hdd":
        return "HDD"
    if (bustype or "").strip().lower() == "nvme":
        return "SSD"
    m = (model or "").lower()
    if "ssd" in m or "nvme" in m:
        return "SSD"
    return "--"


def _reg_cpu_name():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            name, _ = winreg.QueryValueEx(k, "ProcessorNameString")
            return (name or "").strip()
    except Exception:
        return None


def _parse_cim_sections(text):
    sec = None
    data = {"cpu": [], "mem": [], "disk": [], "pd": [], "d2p": [], "p2l": [],
            "gpu": [], "os": [], "net": []}
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("=="):
            sec = ln.strip("=").lower()
            continue
        if sec in data:
            data[sec].append(ln)
    return data


def _reg_os_info():
    """OS 降级：winreg CurrentVersion（ProductName/DisplayVersion/CurrentBuildNumber）"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
            def _v(name):
                try:
                    val, _ = winreg.QueryValueEx(k, name)
                    return str(val)
                except Exception:
                    return ""
            product = _v("ProductName")
            display = _v("DisplayVersion")
            build = _v("CurrentBuildNumber") or _v("CurrentBuild")
            version = "10.0." + build if build else ""
            text = " ".join(x for x in (product, version) if x)
            return {"caption": product or "--", "version": version or "--",
                    "build": build or "--", "text": text or "--"}
    except Exception:
        return {"caption": "--", "version": "--", "build": "--", "text": "--"}


def _psutil_network():
    """网络降级：psutil net_if_addrs + net_if_stats（网关/DNS 不可得显示 --，ADR-014）"""
    out = []
    if psutil is None:
        return out
    try:
        stats = psutil.net_if_stats()
    except Exception:
        stats = {}
    try:
        addrs = psutil.net_if_addrs()
    except Exception:
        addrs = {}
    import socket as _socket
    for name, alist in addrs.items():
        ln = name.lower()
        if "loopback" in ln or ln in ("lo", "lo0"):
            continue  # 环回排除
        ipv4 = ipv6 = mac = None
        for a in alist:
            try:
                if a.family == _socket.AF_INET and a.address:
                    ipv4 = ipv4 or a.address
                elif a.family == _socket.AF_INET6 and a.address:
                    ipv6 = ipv6 or a.address.split("%")[0]
                elif a.family == _socket.AF_LINK and a.address:
                    mac = mac or a.address
            except Exception:
                continue
        if not ipv4 and not ipv6:
            continue  # 无 IP 的虚拟适配器（蓝牙等）排除
        st = stats.get(name)
        out.append({
            "name": name, "ipv4": ipv4 or "--", "ipv6": ipv6 or "--",
            "mac": mac or "--",
            "status": "up" if (st is None or st.isup) else "down",
            "gateway": "--", "dns": "--",
        })
    return out


def _collect_hwinfo():
    info = {"cpu": {}, "memory": {"total": None, "modules": []},
            "disks": [], "gpu": [], "source": "cim",
            "os": {"caption": "--", "version": "--", "build": "--", "text": "--"},
            "hostname": "--", "network": []}
    raw = _ps_cim_dump()
    if not raw:
        info["source"] = "fallback"
    p = _parse_cim_sections(raw) if raw else {}

    # ---- CPU ----
    if p.get("cpu"):
        f = p["cpu"][0].split("|")
        if f and f[0].strip():
            def _int_or_none(s):
                try:
                    return int(float(s))
                except (TypeError, ValueError):
                    return None
            info["cpu"] = {
                "name": f[0].strip(),
                "cores": _int_or_none(f[1]) if len(f) > 1 else None,
                "logical": _int_or_none(f[2]) if len(f) > 2 else None,
                "max_mhz": _int_or_none(f[3]) if len(f) > 3 else None,
                "cur_mhz": _int_or_none(f[4]) if len(f) > 4 else None,
            }
    if not info["cpu"].get("name"):
        info["cpu"] = {
            "name": _reg_cpu_name() or "--",
            "cores": psutil.cpu_count(logical=False) if psutil else None,
            "logical": psutil.cpu_count() if psutil else None,
            "max_mhz": (round(psutil.cpu_freq().max) if psutil and psutil.cpu_freq() and psutil.cpu_freq().max else None),
            "cur_mhz": _freq_mhz(),
        }

    # ---- 内存 ----
    try:
        if psutil:
            info["memory"]["total"] = psutil.virtual_memory().total
    except Exception:
        pass
    for ln in p.get("mem", []):
        f = ln.split("|")
        if len(f) < 4:
            continue
        try:
            cap = int(float(f[1]))
        except (TypeError, ValueError):
            cap = 0
        try:
            smt = int(float(f[2]))
        except (TypeError, ValueError):
            smt = 0
        try:
            speed = int(float(f[3]))
        except (TypeError, ValueError):
            speed = 0
        info["memory"]["modules"].append({
            "slot": f[0].strip() or "--",
            "size": cap or None,
            "type": _SMBIOS_MEM_TYPE.get(smt, "--"),
            "speed_mhz": speed or None,
        })

    # ---- 磁盘（物理盘维度 + SSD/HDD + 系统盘标注）----
    pd_map = {}
    for ln in p.get("pd", []):
        f = ln.split("|")
        if len(f) >= 3:
            pd_map[f[0].strip()] = (f[1].strip(), f[2].strip())  # (MediaType, BusType)
    part_to_disk = {}
    for ln in p.get("d2p", []):
        if "=>" in ln:
            a, b = ln.split("=>", 1)
            # a: "\\.\PHYSICALDRIVE0"  b: "Disk #0, Partition #1"
            m = a.strip().upper()
            part_to_disk[b.strip()] = m.replace("\\\\.\\PHYSICALDRIVE", "").strip()
    part_to_letters = {}
    for ln in p.get("p2l", []):
        if "=>" in ln:
            a, b = ln.split("=>", 1)
            letter = b.strip().strip('"').upper()
            part_to_letters.setdefault(a.strip(), []).append(letter + "\\")

    disk_rows = p.get("disk", [])
    for ln in disk_rows:
        f = ln.split("|")
        if len(f) < 2:
            continue
        try:
            idx = int(float(f[0]))
        except (TypeError, ValueError):
            continue
        model = f[1].strip()
        try:
            size = int(float(f[2])) if len(f) > 2 and f[2].strip() else None
        except (TypeError, ValueError):
            size = None
        iface = f[3].strip() if len(f) > 3 else ""
        mt, bt = pd_map.get(str(idx), ("", ""))
        vols = []
        for part, dnum in part_to_disk.items():
            if dnum == str(idx):
                vols.extend(part_to_letters.get(part, []))
        media = _media_of(model, mt, bt or iface)
        info["disks"].append({
            "name": "PhysicalDrive%d" % idx, "model": model or "--",
            "size": size, "bus": (bt or iface or "--"),
            "media": media, "volumes": sorted(set(vols)),
            "system": any(v.rstrip("\\").upper() == (os.environ.get("SystemDrive", "C:").upper()) for v in vols),
        })
    if not info["disks"] and psutil:
        # PowerShell 失败降级：psutil 盘名单，规格字段以 "--" 呈现
        try:
            for name in psutil.disk_io_counters(perdisk=True):
                if str(name).startswith("PhysicalDrive"):
                    info["disks"].append({"name": name, "model": "--", "size": None,
                                          "bus": "--", "media": "--", "volumes": [],
                                          "system": None})
        except Exception:
            pass

    # ---- GPU ----
    for n in p.get("gpu", []):
        if n.strip():
            info["gpu"].append({"name": n.strip(), "dedicated": _looks_dgpu(n)})
    if not info["gpu"]:
        info["gpu"] = [{"name": n, "dedicated": _looks_dgpu(n)} for n in _detect_gpus()]

    # ---- OS / hostname / network（ADR-014：与硬件规格同一次缓存一次性获取）----
    if p.get("os"):
        f = p["os"][0].split("|")
        if f and f[0].strip():
            caption = f[0].strip()
            version = f[1].strip() if len(f) > 1 else ""
            build = f[2].strip() if len(f) > 2 else ""
            info["os"] = {"caption": caption, "version": version or "--",
                          "build": build or "--",
                          "text": " ".join(x for x in (caption, version) if x)}
    if info["os"]["caption"] == "--" or not info["os"].get("text"):
        reg_os = _reg_os_info()
        if info["os"].get("caption") in (None, "--", ""):
            info["os"]["caption"] = reg_os["caption"]
        if info["os"].get("version") in (None, "--", ""):
            info["os"]["version"] = reg_os["version"]
        if info["os"].get("build") in (None, "--", ""):
            info["os"]["build"] = reg_os["build"]
        if not info["os"].get("text") or info["os"]["text"] == "--":
            info["os"]["text"] = reg_os["text"]

    try:
        import socket
        info["hostname"] = socket.gethostname() or os.environ.get("COMPUTERNAME") or "--"
    except Exception:
        info["hostname"] = os.environ.get("COMPUTERNAME") or "--"

    net_rows = p.get("net", [])
    for ln in net_rows:
        f = ln.split("|")
        if len(f) < 2 or not f[0].strip():
            continue
        info["network"].append({
            "name": f[0].strip(),
            "mac": f[1].strip() or "--",
            "ipv4": f[2].strip() or "--",
            "ipv6": f[3].strip() or "--",
            "gateway": f[4].strip() or "--",
            "dns": f[5].strip() or "--",
            "status": "up",  # IPEnabled=True 过滤后均为启用
        })
    if not info["network"]:
        info["network"] = _psutil_network()
    return info


def handle_perf_hwinfo(params: dict) -> dict:
    """硬件规格（静态信息，首次获取后进程内缓存）"""
    try:
        if _hwinfo_cache["data"] is None:
            _hwinfo_cache["data"] = _collect_hwinfo()
        return {"success": True, "hwinfo": _hwinfo_cache["data"]}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================
# 7. 温度（能力分级，ADR-011）：GPU nvidia-smi 全员可读；CPU 需管理员 + LibreHardwareMonitorLib
# ============================================================

_lhm_state = {"tried": False, "computer": None, "error": None}
_lhm_lock = threading.Lock()
_temps_cache = {"ts": 0.0, "cpu_temp": None, "gpu_temp": None}  # 供记录/压测附加温度


def _is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _lhm_libs_dir():
    base = getattr(sys, "_MEIPASS", None)
    if base:
        d = os.path.join(base, "libs")
        if os.path.isdir(d):
            return d
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "libs")


def _lhm_computer():
    """LHM Computer 单例（Open 一次复用防句柄泄漏；失败降级不重试爆破）"""
    with _lhm_lock:
        if _lhm_state["tried"]:
            return _lhm_state["computer"]
        _lhm_state["tried"] = True
        try:
            import clr
            libs = _lhm_libs_dir()
            lp = os.path.join(libs, "LibreHardwareMonitorLib.dll")
            if not os.path.exists(lp):
                _lhm_state["error"] = "lhm_dll_missing"
                return None
            hp = os.path.join(libs, "HidSharp.dll")
            if os.path.exists(hp):
                clr.AddReference(hp)
            clr.AddReference(lp)
            from LibreHardwareMonitor import Hardware
            comp = Hardware.Computer()
            comp.IsCpuEnabled = True
            comp.IsGpuEnabled = True
            comp.Open()
            _lhm_state["computer"] = comp
        except Exception as e:
            _lhm_state["error"] = str(e)
        return _lhm_state["computer"]


def _lhm_close():
    with _lhm_lock:
        comp = _lhm_state.get("computer")
        if comp is not None:
            try:
                comp.Close()
            except Exception:
                pass
            _lhm_state["computer"] = None
            _lhm_state["tried"] = False


def _lhm_cpu_temp():
    """返回 (available, info)；MSR 读取有开销，调用方轮询 ≥2s"""
    comp = _lhm_computer()
    if comp is None:
        return False, None
    temps = []
    try:
        for hw in comp.Hardware:
            if "cpu" not in str(hw.HardwareType).lower():
                continue
            try:
                hw.Update()
            except Exception:
                continue
            for s in hw.Sensors:
                try:
                    if int(s.SensorType) == 2 and s.Value is not None:  # SensorType.Temperature
                        temps.append((str(s.Name), float(s.Value)))
                except Exception:
                    continue
            for sub in hw.SubHardware:
                try:
                    sub.Update()
                except Exception:
                    continue
                for s in sub.Sensors:
                    try:
                        if int(s.SensorType) == 2 and s.Value is not None:
                            temps.append((str(s.Name), float(s.Value)))
                    except Exception:
                        continue
    except Exception:
        return False, None
    if not temps:
        return False, None
    package = None
    core_max = None
    for name, v in temps:
        ln = name.lower()
        if "package" in ln and package is None:
            package = v
        if ln.startswith("core") and "max" not in ln:
            core_max = v if core_max is None else max(core_max, v)
    return True, {"package": package, "core_max": core_max,
                  "max": max(v for _, v in temps)}


def _nvidia_temp():
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, creationflags=_NO_WINDOW)
        if r.returncode == 0 and r.stdout.strip():
            parts = [p.strip() for p in r.stdout.strip().splitlines()[0].split(",")]
            if len(parts) >= 2:
                return {"available": True, "temp_c": float(parts[1]),
                        "name": parts[0], "src": "nvidia-smi"}
    except Exception:
        pass
    return {"available": False, "reason": "no_nvidia_gpu"}


def handle_perf_temps(params: dict) -> dict:
    """温度轮询（前端建议 2s，不并入 1s snapshot）；任何失败优雅降级不报 error"""
    try:
        admin = _is_admin()
        gpu = _nvidia_temp()
        cpu = {"available": False, "temp_c": None, "reason": "need_admin"}
        if admin:
            ok, t = _lhm_cpu_temp()
            if ok and t:
                v = t.get("package") or t.get("core_max") or t.get("max")
                cpu = {"available": True, "temp_c": round(v, 1), "detail": t, "reason": None}
            else:
                cpu = {"available": False, "temp_c": None, "reason": "lhm_unavailable"}
        if gpu.get("available") and gpu.get("temp_c") is not None:
            _temps_cache["gpu_temp"] = gpu["temp_c"]
        if cpu.get("available") and cpu.get("temp_c") is not None:
            _temps_cache["cpu_temp"] = cpu["temp_c"]
        _temps_cache["ts"] = time.time()
        return {"success": True, "admin": admin, "cpu": cpu, "gpu": gpu}
    except Exception as e:
        try:
            admin = _is_admin()
        except Exception:
            admin = False
        return {"success": True, "admin": admin,
                "cpu": {"available": False, "temp_c": None, "reason": "internal"},
                "gpu": {"available": False, "reason": "internal"},
                "internal_error": str(e)}


# ---- 以管理员重启（desktop.py 注册 exit hook，避免 bridge→desktop 循环导入）----

_exit_hook = {"fn": None}


def register_exit_hook(fn):
    _exit_hook["fn"] = fn


def _request_exit():
    fn = _exit_hook.get("fn")
    if fn is not None:
        try:
            fn()
            return
        except Exception:
            pass
    os._exit(0)


def handle_perf_restart_admin(params: dict) -> dict:
    """以管理员重启自身：UAC 确认后 ShellExecuteW runas 启动新实例并退出当前实例"""
    try:
        # 进行中的记录先停止落盘（flush 后随新实例恢复展示）
        with _records_lock:
            running = [r for r in _records.values() if r["status"] == "running"]
        for r in running:
            try:
                handle_perf_record_stop({"record_id": r["id"]})
            except Exception:
                pass
        exe = sys.executable
        if getattr(sys, "frozen", False):
            args = ""  # 打包态：winhelper.exe 直启
        else:  # 开发态：python desktop.py
            args = '"%s"' % os.path.join(os.path.dirname(os.path.abspath(__file__)), "desktop.py")
        ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, args, None, 1)
        if ret > 32:
            threading.Timer(0.3, _request_exit).start()  # 给响应返回留时间，再销毁窗口
            return {"success": True, "restarting": True}
        if ret in (5, 1223):  # SE_ERR_ACCESSDENIED / ERROR_CANCELLED：用户在 UAC 点了取消
            return {"success": False, "error": "uac_cancelled"}
        return {"success": False, "error": "shell_error_%s" % ret}
    except Exception as e:
        return {"success": False, "error": str(e)}


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
    "/api/perf/stress-start": handle_perf_stress_start,
    "/api/perf/stress-status": handle_perf_stress_status,
    "/api/perf/stress-cancel": handle_perf_stress_cancel,
    "/api/perf/stress-export": handle_perf_stress_export,
    "/api/perf/hwinfo": handle_perf_hwinfo,
    "/api/perf/temps": handle_perf_temps,
    "/api/perf/restart-admin": handle_perf_restart_admin,
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
