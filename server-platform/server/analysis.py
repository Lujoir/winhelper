# -*- coding: utf-8 -*-
"""观枢终端平台服务端 · 瓶颈识别规则引擎。

阈值与终端端（winhelper perf）一致（ADR-006）：
  CPU 饱和   cpu.percent            > 85
  内存饱和   mem.available_percent  < 10
  磁盘饱和   磁盘 percent > 80（含 busy_percent > 80，字段为终端端预留）
  换页压力   swap.used_percent      > 50

规则为数据表驱动：新增规则只需在 RULES 追加条目。
evaluate(snapshot) 返回本次命中的瓶颈列表（未做去重，去重由调用方结合
store.last_bottleneck_ts 的窗口完成）。
"""

CPU_THRESHOLD = 85.0
MEM_AVAILABLE_THRESHOLD = 10.0
DISK_THRESHOLD = 80.0
SWAP_THRESHOLD = 50.0


def _num(value):
    """安全转 float，失败返回 None。"""
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_cpu(snapshot):
    cpu = snapshot.get("cpu") or {}
    v = _num(cpu.get("percent"))
    if v is None:
        return []
    return [("cpu.percent", v)]


def _extract_mem_available(snapshot):
    mem = snapshot.get("mem") or {}
    v = _num(mem.get("available_percent"))
    if v is None:
        used = _num(mem.get("used_percent"))
        if used is not None:
            v = 100.0 - used
    if v is None:
        return []
    return [("mem.available_percent", v)]


def _extract_swap(snapshot):
    swap = snapshot.get("swap") or {}
    v = _num(swap.get("used_percent"))
    if v is None:
        return []
    return [("swap.used_percent", v)]


def _extract_disks(snapshot):
    """磁盘规则：每个磁盘产出一条 (metric_key, value)；
    若提供 busy_percent，则额外产出 busy 判定条目（终端端字段预留）。"""
    out = []
    disks = snapshot.get("disks") or []
    if not isinstance(disks, list):
        return out
    for idx, d in enumerate(disks):
        if not isinstance(d, dict):
            continue
        mount = d.get("mount") or ("disk#%d" % idx)
        pct = _num(d.get("percent"))
        if pct is not None:
            out.append(("disk.%s.percent" % mount, pct))
        busy = _num(d.get("busy_percent"))
        if busy is not None:
            out.append(("disk.%s.busy_percent" % mount, busy))
    return out


# 规则表：op ∈ gt/lt；level 为落库等级
RULES = [
    {
        "name": "cpu_saturation",
        "kind": "cpu",
        "extract": _extract_cpu,
        "op": "gt",
        "threshold": CPU_THRESHOLD,
        "level": "warn",
        "desc": "CPU 使用率超阈值（饱和）",
    },
    {
        "name": "mem_saturation",
        "kind": "mem",
        "extract": _extract_mem_available,
        "op": "lt",
        "threshold": MEM_AVAILABLE_THRESHOLD,
        "level": "warn",
        "desc": "内存可用率不足阈值（饱和）",
    },
    {
        "name": "disk_saturation",
        "kind": "disk",
        "extract": _extract_disks,
        "op": "gt",
        "threshold": DISK_THRESHOLD,
        "level": "warn",
        "desc": "磁盘使用率超阈值（饱和）",
    },
    {
        "name": "swap_pressure",
        "kind": "swap",
        "extract": _extract_swap,
        "op": "gt",
        "threshold": SWAP_THRESHOLD,
        "level": "info",
        "desc": "换页空间使用率偏高",
    },
]

_OPS = {
    "gt": lambda v, t: v > t,
    "lt": lambda v, t: v < t,
}


def evaluate(snapshot):
    """对单条指标快照执行全部规则。

    返回列表，元素结构：
    {rule, kind, metric_key, value, threshold, level, desc, detail}
    """
    hits = []
    if not isinstance(snapshot, dict):
        return hits
    for rule in RULES:
        for metric_key, value in rule["extract"](snapshot):
            if value is None:
                continue
            if _OPS[rule["op"]](value, rule["threshold"]):
                hits.append({
                    "rule": rule["name"],
                    "kind": rule["kind"],
                    "metric_key": metric_key,
                    "value": value,
                    "threshold": rule["threshold"],
                    "level": rule["level"],
                    "desc": rule["desc"],
                    "detail": {"op": rule["op"]},
                })
    return hits
