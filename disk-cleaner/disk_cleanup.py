"""
磁盘清理模块 — C盘智能分析与清理
=================================
安全模型（数据安全优先）:
  🟢 安全级 (safe)    回收站/系统临时/用户临时/更新缓存/浏览器缓存/错误报告等
                      → 支持一键清理与勾选个性化清理
  🟡 谨慎级 (caution) Windows.old / 内存转储
                      → 默认不勾选，用户显式选择后才清理
  🔴 敏感级 (sensitive) 大容量数据文件
                      → 仅扫描定位与给出分析建议，应用内不提供删除入口，推荐手动处理

技术要点:
  - 回收站: WinAPI (SHQueryRecycleBinW / SHEmptyRecycleBinW)，免子进程
  - 扫描:   os.scandir 栈式遍历（快），跳过无权限目录与系统保护目录
  - 任务:   后台线程 + 统一任务管理器（进度/取消/结果轮询）
  - 防护:   清理仅限内置白名单目录；自动跳过自身运行时临时文件(_MEI*)
"""

import os
import re
import time
import uuid
import ctypes
import fnmatch
import heapq
import shutil
import threading
import subprocess
from ctypes import wintypes
from datetime import datetime

DRIVE = (os.environ.get("SystemDrive", "C:") or "C:").rstrip("\\") + "\\"

# ============================================================
# WinAPI: 回收站
# ============================================================

class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("i64Size", ctypes.c_longlong),      # 回收站总字节数
        ("i64NumItems", ctypes.c_longlong),  # 项目数
    ]

_shell32 = ctypes.windll.shell32
_shell32.SHQueryRecycleBinW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(_SHQUERYRBINFO)]
_shell32.SHQueryRecycleBinW.restype = ctypes.HRESULT
_shell32.SHEmptyRecycleBinW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.DWORD]
_shell32.SHEmptyRecycleBinW.restype = ctypes.HRESULT

def recycle_bin_info(drive=DRIVE):
    """查询回收站 (字节, 项目数)。查询失败按 0 处理。"""
    info = _SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(info)
    try:
        if _shell32.SHQueryRecycleBinW(drive, ctypes.byref(info)) == 0:
            return int(info.i64Size), int(info.i64NumItems)
    except Exception:
        pass
    return 0, 0

def empty_recycle_bin(drive=DRIVE):
    """清空回收站 (SHERB_NOCONFIRMATION|NOPROGRESSUI|NOSOUND = 7)。返回(释放字节, 项目数)。"""
    size, count = recycle_bin_info(drive)
    if count <= 0:
        return 0, 0
    hr = _shell32.SHEmptyRecycleBinW(None, drive, 7)
    if hr != 0:
        raise OSError(f"清空回收站失败 (hr=0x{hr & 0xFFFFFFFF:08X})")
    return size, count


# ============================================================
# 分类定义（清理白名单，服务端唯一事实来源）
# specs: [{base, dir_pattern?, file_patterns?}]
# ============================================================

def _build_category_defs():
    local = os.environ.get("LOCALAPPDATA", "")
    win_root = os.environ.get("SystemRoot", r"C:\Windows")      # 系统目录(自适应系统盘符)
    programdata = os.environ.get("ProgramData", r"C:\ProgramData")

    def _chromium_profile_caches(user_data):
        """枚举 Chromium 系浏览器全部 Profile 的缓存目录（兼容多 Profile 用户）"""
        ud = os.path.expandvars(user_data)
        specs = []
        if not ud or not os.path.isdir(ud):
            return specs
        try:
            for entry in os.scandir(ud):
                if not entry.is_dir():
                    continue
                # Profile 目录特征: 内含 Preferences 文件
                if not os.path.isfile(os.path.join(entry.path, "Preferences")):
                    continue
                for sub in ("Cache", "Code Cache",
                            os.path.join("Service Worker", "CacheStorage")):
                    specs.append({"base": os.path.join(entry.path, sub)})
        except OSError:
            pass
        return specs

    browser_specs = []
    browser_specs += _chromium_profile_caches(r"%LOCALAPPDATA%\Google\Chrome\User Data")
    browser_specs += _chromium_profile_caches(r"%LOCALAPPDATA%\Microsoft\Edge\User Data")
    browser_specs += _chromium_profile_caches(r"%LOCALAPPDATA%\360Chrome\Chrome\User Data")
    browser_specs += _chromium_profile_caches(r"%LOCALAPPDATA%\Tencent\QQBrowser\User Data")
    firefox_profiles = os.path.join(local, r"Mozilla\Firefox\Profiles")
    browser_specs.append({"base": firefox_profiles, "dir_pattern": "cache2"})

    return {
        "recycle_bin": {
            "name": "回收站", "risk": "safe", "default": True,
            "desc": "已删除文件的暂存区，清空不影响正常文件",
        },
        "system_temp": {
            "name": "系统临时文件", "risk": "safe", "default": True,
            "desc": "Windows 及程序运行产生的临时文件",
            "specs": [{"base": os.path.join(win_root, "Temp")}],
        },
        "user_temp": {
            "name": "用户临时文件", "risk": "safe", "default": True,
            "desc": "当前用户 TEMP 目录（自动保护应用自身运行时文件）",
            "specs": [{"base": os.environ.get("TEMP", "")}],
            "guard_mei": True,
        },
        "win_update_cache": {
            "name": "Windows 更新下载缓存", "risk": "safe", "default": True,
            "desc": "已安装更新的安装包残留，可安全删除",
            "specs": [{"base": os.path.join(win_root, "SoftwareDistribution\\Download")}],
        },
        "delivery_opt": {
            "name": "传递优化缓存", "risk": "safe", "default": True,
            "desc": "Windows 更新 P2P 分发缓存",
            "specs": [{"base": os.path.join(win_root, r"ServiceProfiles\NetworkService\AppData\Local\Microsoft\Windows\DeliveryOptimization\Cache")}],
        },
        "thumbnail_cache": {
            "name": "缩略图缓存", "risk": "safe", "default": True,
            "desc": "资源管理器缩略图缓存，删除后自动重建",
            "specs": [{"base": os.path.join(local, r"Microsoft\Windows\Explorer"),
                       "file_patterns": ["thumbcache*.db", "iconcache*.db"]}],
        },
        "wer_reports": {
            "name": "Windows 错误报告", "risk": "safe", "default": True,
            "desc": "应用崩溃上报队列与历史归档",
            "specs": [
                {"base": os.path.join(local, r"Microsoft\Windows\WER")},
                {"base": os.path.join(programdata, r"Microsoft\Windows\WER")},
            ],
        },
        "shader_cache": {
            "name": "DirectX 着色器缓存", "risk": "safe", "default": True,
            "desc": "游戏/图形应用着色器缓存，删除后首次启动会重新编译",
            "specs": [{"base": os.path.join(local, "D3DSCache")}],
        },
        "browser_cache": {
            "name": "浏览器缓存", "risk": "safe", "default": False,
            "desc": "Chrome / Edge / Firefox 网页缓存——建议手动勾选（浏览器运行中的文件会自动跳过）",
            "specs": browser_specs,
        },
        "system_logs": {
            "name": "系统日志文件", "risk": "safe", "default": True,
            "desc": "Windows 组件日志(CBS/DISM/MoSetup 等)，删除不影响系统运行",
            "specs": [{"base": os.path.join(win_root, "Logs")}],
        },
        "memory_dumps": {
            "name": "内存转储文件", "risk": "caution", "default": False,
            "desc": "蓝屏/崩溃转储，排查系统故障前建议保留",
            "specs": [
                {"base": os.path.join(win_root, "Minidump")},
                {"base": win_root, "file_patterns": ["memory.dmp"]},
            ],
        },
        "upgrade_leftovers": {
            "name": "系统升级残留备份", "risk": "caution", "default": False,
            "desc": "系统升级临时备份($WINDOWS.~BT/Config.MSI)，保留期内可用于回滚，确认系统稳定后可删",
            "specs": [
                {"base": os.path.join(DRIVE, "$WINDOWS.~BT")},
                {"base": os.path.join(DRIVE, "$WINDOWS.~WS")},
                {"base": os.path.join(DRIVE, "Config.MSI")},
            ],
        },
        "windows_old": {
            "name": "旧版 Windows (Windows.old)", "risk": "caution", "default": False,
            "desc": "系统升级备份，确认系统稳定后可删除；若清理失败请使用系统『磁盘清理』工具",
            "specs": [{"base": os.path.join(DRIVE, "Windows.old")}],
        },
    }

CATEGORY_DEFS = _build_category_defs()


# ============================================================
# 通用工具
# ============================================================

def human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def _is_guarded(path):
    """保护 pyinstaller 单文件运行时临时目录，避免清理时误删自身。"""
    p = path.lower()
    return "_mei" in p


def _scan_spec(spec):
    """按 spec 统计 (总字节, 文件数)。仅读不删。"""
    base = spec.get("base", "")
    dir_pattern = spec.get("dir_pattern")
    file_patterns = [p.lower() for p in spec.get("file_patterns", [])]
    if not base or not os.path.isdir(base):
        return 0, 0

    total, count = 0, 0
    stack = [(base, False)]
    while stack:
        d, force = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if _is_guarded(e.path):
                        continue
                    stack.append((e.path, force or (
                        dir_pattern is not None and fnmatch.fnmatch(e.name.lower(), dir_pattern.lower()))))
                elif e.is_file(follow_symlinks=False):
                    matched = force or (
                        (file_patterns and any(fnmatch.fnmatch(e.name.lower(), p) for p in file_patterns))
                        or (not file_patterns and dir_pattern is None)
                    )
                    if matched:
                        total += e.stat(follow_symlinks=False).st_size
                        count += 1
            except OSError:
                continue
    return total, count


# ============================================================
# 大文件扫描
# ============================================================

# 全盘扫描时跳过的系统保护/无意义目录（不进入递归）
LARGE_SCAN_SKIP_DIRS = {
    "$recycle.bin", "system volume information", "winsxs", "windowsapps",
    "config.msi", "recovery", "$windows.~bt", "$windows.~ws", "servicing",
}
# 跳过的系统页文件（锁定且无清理意义）
LARGE_SCAN_SKIP_FILES = {"pagefile.sys", "hiberfil.sys", "swapfile.sys", "dumpstack.log.tmp"}

FILE_TYPE_RULES = [
    ((".vhd", ".vhdx", ".vmdk", ".iso", ".img", ".wim", ".esd", ".gho"),
     "镜像/虚拟磁盘", "若对应系统/虚拟机已不再使用，可手动删除", "warn"),
    ((".bak", ".mdf", ".ndf", ".ldf", ".accdb", ".sqlite", ".db"),
     "数据库/备份", "敏感数据文件，务必人工核对后再处理", "danger"),
    ((".dmp",), "调试转储", "崩溃转储文件，一般可手动删除", "safe"),
    ((".log",), "日志文件", "一般可清理，确认无需留存后处理", "safe"),
    ((".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".m4v"),
     "视频媒体", "个人媒体文件，请人工确认", "info"),
    ((".zip", ".rar", ".7z", ".tar", ".gz", ".xz"),
     "压缩包", "确认内容后手动归档或删除", "info"),
    ((".msi", ".exe"),
     "安装包/程序", "确认是安装包后可删；程序本体请勿删除", "warn"),
    ((".psd", ".ai", ".prproj", ".aep", ".max", ".blend"),
     "设计工程", "工作数据，人工处理", "danger"),
    ((".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv"),
     "文档数据", "工作/个人数据，人工处理", "danger"),
]


def classify_file(path):
    ext = os.path.splitext(path)[1].lower()
    for exts, label, advice, level in FILE_TYPE_RULES:
        if ext in exts:
            return label, advice, level
    return "其他文件", "请人工判断用途后处理", "info"


def _location_hint(path):
    p = path.lower().replace("/", "\\")
    home = os.path.expanduser("~").lower().rstrip("\\")
    if p.startswith(home):
        rest = p[len(home):].lstrip("\\")
        first = rest.split("\\")[0] if rest else ""
        return {
            "downloads": "下载目录", "desktop": "桌面", "documents": "文档目录",
            "pictures": "图片目录", "videos": "视频目录", "music": "音乐目录",
        }.get(first, "个人用户目录")
    if p.startswith("c:\\windows"):
        return "系统目录（谨慎处理）"
    if p.startswith("c:\\program files"):
        return "程序安装目录"
    if p.startswith("c:\\programdata"):
        return "应用数据目录"
    if p.startswith("c:\\users"):
        return "其他用户目录"
    return "磁盘根目录/其他"


def _run_large_scan(task, params):
    root = params.get("root") or DRIVE
    try:
        min_mb = max(1, int(params.get("min_mb", 200)))
    except (TypeError, ValueError):
        min_mb = 200
    top_n = 100
    min_bytes = min_mb * 1024 * 1024

    state = {"files": 0, "skipped_dirs": 0}
    found = []
    started = time.time()
    cancel = task["_cancel"]

    stack = [root]
    last_progress = 0.0
    while stack:
        if cancel.is_set():
            task["status"] = "cancelled"
            return
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            state["skipped_dirs"] += 1
            continue
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name.lower() in LARGE_SCAN_SKIP_DIRS or _is_guarded(e.path):
                        continue
                    stack.append(e.path)
                elif e.is_file(follow_symlinks=False):
                    if e.name.lower() in LARGE_SCAN_SKIP_FILES:
                        continue
                    state["files"] += 1
                    size = e.stat(follow_symlinks=False).st_size
                    if size >= min_bytes:
                        mtime = e.stat(follow_symlinks=False).st_mtime
                        label, advice, level = classify_file(e.path)
                        found.append({
                            "path": e.path,
                            "size": size,
                            "size_text": human_size(size),
                            "type": label,
                            "advice": advice,
                            "level": level,
                            "location": _location_hint(e.path),
                            "modified": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
                        })
            except OSError:
                continue

        # 进度上报（限频：每0.5秒）
        now = time.time()
        if now - last_progress > 0.5:
            last_progress = now
            task["progress"] = {
                "scanned_files": state["files"],
                "found": len(found),
                "current_dir": d,
                "elapsed": round(now - started, 1),
            }

    found.sort(key=lambda x: x["size"], reverse=True)
    task["result"] = {
        "files": found[:top_n],
        "total_found": len(found),
        "shown": min(len(found), top_n),
        "scanned_files": state["files"],
        "skipped_dirs": state["skipped_dirs"],
        "elapsed": round(time.time() - started, 1),
        "root": root,
        "min_mb": min_mb,
    }


# ============================================================
# 目录树容量扫描（全盘/自定义目录，支持海量小文件定位）
# ============================================================

class _TreeCancel(Exception):
    pass


# ============================================================
# 推荐清理引擎（启发式：目录特征匹配 + 文件形态识别）
# ============================================================

# (正则, 类别, 可清理度0~1, 建议)
_RECOMMEND_RULES = [
    (re.compile(r"softwaredistribution", re.I), "Windows更新下载缓存", 0.95,
     "已安装更新的残留安装包，可放心清理"),
    (re.compile(r"(^|[\\/_.-])temp($|[\\/_.-])|(^|[\\/_.-])tmp($|[\\/_.-])", re.I), "临时文件", 0.95,
     "临时目录，可直接清理"),
    (re.compile(r"cache", re.I), "应用缓存", 0.90,
     "应用缓存，清理后应用按需重建"),
    (re.compile(r"deliveryoptimization", re.I), "传递优化缓存", 0.90,
     "Windows更新分发缓存"),
    (re.compile(r"thumbcache|iconcache|\\explorer$", re.I), "缩略图缓存", 0.90,
     "删除后系统自动重建"),
    (re.compile(r"d3dscache|shadercache|glcache|nvidia", re.I), "着色器缓存", 0.90,
     "游戏/图形缓存，首次启动重新编译"),
    (re.compile(r"(^|[\\/_.])logs?($|[\\/_.])", re.I), "日志文件", 0.85,
     "日志类目录，确认无需留存后可清理"),
    (re.compile(r"crash|dumps|minidump|\\wer($|[\\/])", re.I), "崩溃转储/错误报告", 0.85,
     "崩溃报告与转储，可清理"),
    (re.compile(r"(chrome|edge|firefox|opera).*cache", re.I), "浏览器缓存", 0.85,
     "浏览器缓存，运行中文件会占用跳过"),
    (re.compile(r"(\\|_)(pip|npm|yarn|nuget|gradle|maven|cargo|huggingface)(\\|_)", re.I), "开发工具缓存", 0.80,
     "开发包缓存，可清理，下次构建自动重新下载"),
    (re.compile(r"windows\.old|\$windows\.~", re.I), "系统升级备份", 0.70,
     "确认系统运行稳定后再删除"),
    (re.compile(r"\\prefetch", re.I), "预读取数据", 0.50,
     "影响程序启动速度，一般不建议清理"),
]

# 个人数据目录（相对扫描根的任一层级命中即整棵子树排除）
_USER_DATA_DIRS = {"desktop", "documents", "downloads", "pictures",
                   "music", "videos", "desktop", "onedrive", "wechat files",
                   "xwechat_files", "tencent files"}


def _match_recommend(path_lower):
    for pat, cat, conf, advice in _RECOMMEND_RULES:
        if pat.search(path_lower):
            return cat, conf, advice
    return None


def _recommend_top10(tree, root_path, total_size):
    """从内存目录树中启发式选出建议清理的TOP10文件夹。"""
    candidates = []

    def walk(node, rel_parts):
        rel = "\\".join(rel_parts)
        rel_l = rel.lower()
        # 个人数据目录整棵排除
        if any(p.lower() in _USER_DATA_DIRS for p in rel_parts):
            return
        size, files = node["_s"], node["_f"]
        if size > 0 and rel_parts:
            m = _match_recommend(rel_l)
            avg = size / files if files else 0
            small_heavy = files >= 300 and avg <= 256 * 1024
            conf, cat, advice, reasons = None, None, None, []
            if m and size >= 30 * 1024 * 1024:
                cat, conf, advice = m
                reasons.append(f"命中特征「{cat}」")
            if small_heavy and files >= 1000 and size >= 10 * 1024 * 1024:
                if conf is None:
                    conf, cat = 0.55, "海量小文件目录"
                    advice = "海量小文件目录（日志/缓存类特征），清理效率高，定位到文件夹容量即可按需清理"
                else:
                    conf = min(1.0, conf + 0.08)
                reasons.append(f"海量小文件（{files:,} 个文件, 均值 {human_size(avg)}）")
            if conf is not None:
                candidates.append({
                    "path": os.path.join(root_path, *rel_parts),
                    "rel": rel,
                    "size": size, "size_text": human_size(size),
                    "files": files, "dirs": node["_d"],
                    "avg_text": human_size(avg),
                    "small_heavy": small_heavy,
                    "category": cat, "confidence": round(conf, 2),
                    "advice": advice,
                    "score": size * conf,
                })
        for name, child in node["_c"].items():
            walk(child, rel_parts + [name])

    walk(tree, [])

    # 按预期可释放量排序；父子重叠只保留最优项
    candidates.sort(key=lambda c: c["score"], reverse=True)
    selected = []
    for c in candidates:
        p = _norm(c["path"])
        if any(_is_under_rel(p, _norm(s["path"])) or _is_under_rel(_norm(s["path"]), p)
               for s in selected):
            continue
        reasons = []
        if c["category"] != "海量小文件目录":
            reasons.append(f"命中特征「{c['category']}」")
        if c["small_heavy"]:
            reasons.append(f"海量小文件（{c['files']:,} 个文件, 均值 {c['avg_text']}）")
        c["reason"] = " · ".join(reasons) or "目录特征符合可清理类型"
        selected.append(c)
        if len(selected) >= 10:
            break

    for i, s in enumerate(selected, 1):
        s["rank"] = i
        s["confidence_text"] = "高" if s["confidence"] >= 0.85 else ("中" if s["confidence"] >= 0.7 else "低")
    return selected


def _norm(p):
    return os.path.normcase(os.path.abspath(os.path.normpath(p)))


def _is_under_rel(p, root):
    return p == root or p.startswith(root + os.sep)


def _run_tree_scan(task, params):
    """
    递归聚合每个目录的 容量/文件数/子目录数，构建内存目录树。
    - root: 任意本地磁盘根目录或自定义子目录
    - min_mb: 仅收集 >= 该阈值的大文件进 TOP25
    树本体存于 task["_tree"]（不随状态轮询序列化），通过 /api/disk/tree 懒展开。
    """
    root = (params.get("root") or DRIVE).strip()
    if not os.path.isdir(root):
        task["result"] = {"success": False, "error": f"目录不存在: {root}"}
        return
    try:
        min_mb = max(1, int(params.get("min_mb", 100)))
    except (TypeError, ValueError):
        min_mb = 100
    min_bytes = min_mb * 1024 * 1024

    # 全量统计: 不跳过任何目录（含 WinSxS/回收站等系统目录），
    # 仅排除应用自身运行时(_MEI)目录；受权限保护的目录计入 skipped 并保留样本
    cancel = task["_cancel"]
    state = {"files": 0, "skipped": 0, "skipped_samples": [], "dirs": 0, "last": 0.0}
    started = time.time()
    top_heap = []  # (size, path, mtime)

    def build(dir_path):
        if cancel.is_set():
            raise _TreeCancel()
        node = {"_s": 0, "_f": 0, "_d": 0, "_c": {}}
        try:
            entries = list(os.scandir(dir_path))
        except OSError:
            state["skipped"] += 1
            if len(state["skipped_samples"]) < 10:
                state["skipped_samples"].append(dir_path)
            return node
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if _is_guarded(e.path):
                        continue
                    child = build(e.path)
                    node["_c"][e.name] = child
                    node["_s"] += child["_s"]
                    node["_f"] += child["_f"]
                    node["_d"] += child["_d"] + 1
                elif e.is_file(follow_symlinks=False):
                    size = e.stat(follow_symlinks=False).st_size
                    node["_s"] += size
                    node["_f"] += 1
                    state["files"] += 1
                    if size >= min_bytes:
                        item = (size, e.path, e.stat(follow_symlinks=False).st_mtime)
                        if len(top_heap) < 25:
                            heapq.heappush(top_heap, item)
                        elif size > top_heap[0][0]:
                            heapq.heapreplace(top_heap, item)
            except OSError:
                continue
        state["dirs"] += 1
        now = time.time()
        if now - state["last"] > 0.5:
            state["last"] = now
            task["progress"] = {
                "scanned_files": state["files"],
                "dirs": state["dirs"],
                "current_dir": dir_path,
                "elapsed": round(now - started, 1),
            }
        return node

    try:
        tree = build(root)
    except _TreeCancel:
        task["status"] = "cancelled"
        return
    except RecursionError:
        task["result"] = {"success": False, "error": "目录层级过深，扫描中止"}
        return

    top_files = [{
        "path": p, "size": s, "size_text": human_size(s),
        "type": classify_file(p)[0], "advice": classify_file(p)[1],
        "level": classify_file(p)[2], "location": _location_hint(p),
        "modified": datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M"),
    } for s, p, mt in sorted(top_heap, reverse=True)]

    task["_tree"] = tree
    norm_root = os.path.normpath(root)
    recommendations = _recommend_top10(tree, norm_root, tree["_s"])
    task["result"] = {
        "success": True,
        "root_path": norm_root,
        "total_size": tree["_s"], "total_size_text": human_size(tree["_s"]),
        "total_files": tree["_f"], "total_dirs": tree["_d"],
        "scanned_files": state["files"],
        "skipped_dirs": state["skipped"],
        "skipped_samples": state["skipped_samples"],
        "elapsed": round(time.time() - started, 1),
        "min_mb": min_mb,
        "top_files": top_files,
        "recommendations": recommendations,
        "recommend_note": "启发式推荐仅供参考；桌面/文档/下载/微信等个人数据目录已排除，请用『应用数据』页处理。父子目录重叠已去重。",
    }


def _tree_node_for(task, path):
    """在内存树中定位 path 对应节点。path 必须位于扫描根内。"""
    root_path = task["result"]["root_path"]
    node = task.get("_tree")
    if node is None:
        return None, None
    rel = os.path.relpath(os.path.normpath(path), os.path.normpath(root_path))
    if rel in (".", ""):
        return node, ""
    parts = [p for p in rel.split(os.sep) if p and p != ".."]
    if rel.startswith(".."):
        return None, None
    for part in parts:
        node = node["_c"].get(part)
        if node is None:
            return None, None
    return node, rel


def _node_view(name, node, path):
    """序列化单个目录节点（含海量小文件识别）"""
    files = node["_f"]
    avg = node["_s"] / files if files else 0
    # 海量小文件: 文件数>=300 且 平均文件大小<=256KB （日志/缓存类目录特征）
    small_heavy = files >= 300 and avg <= 256 * 1024
    return {
        "name": name,
        "path": path,
        "size": node["_s"],
        "size_text": human_size(node["_s"]),
        "files": files,
        "dirs": node["_d"],
        "avg_text": human_size(avg),
        "small_heavy": small_heavy,
    }


def handle_disk_tree(params: dict) -> dict:
    """懒展开目录树: scan_id + 可选 path（默认扫描根）+ limit"""
    with _lock:
        task = _tasks.get(params.get("scan_id", ""))
    if not task or task["status"] != "done" or task.get("_tree") is None:
        return {"success": False, "error": "扫描任务不存在或未完成"}

    root_path = task["result"]["root_path"]
    path = (params.get("path") or root_path).strip()
    if not os.path.normpath(path).lower().startswith(os.path.normpath(root_path).lower()):
        return {"success": False, "error": "路径超出扫描范围"}
    node, rel = _tree_node_for(task, path)
    if node is None:
        return {"success": False, "error": "节点不存在，请重新扫描"}
    if os.path.isfile(path):
        return {"success": False, "error": "目标是文件，请选择目录"}

    try:
        limit = max(10, min(5000, int(params.get("limit", 2000))))
    except (TypeError, ValueError):
        limit = 2000

    children = []
    for name, child in node["_c"].items():
        children.append(_node_view(
            name, child, os.path.join(path, name) if rel else os.path.join(root_path, name)))
    children.sort(key=lambda c: c["size"], reverse=True)

    # 当前目录下的散落文件（实时读取单层目录，取TOP15）
    loose = []
    try:
        for e in os.scandir(path):
            try:
                if e.is_file(follow_symlinks=False) and e.name.lower() not in LARGE_SCAN_SKIP_FILES:
                    st = e.stat(follow_symlinks=False)
                    loose.append({"name": e.name, "path": e.path,
                                  "size": st.st_size, "size_text": human_size(st.st_size),
                                  "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")})
            except OSError:
                continue
    except OSError:
        pass
    loose.sort(key=lambda x: x["size"], reverse=True)

    cur = _node_view(os.path.basename(root_path) if not rel else os.path.basename(path), node, path)
    return {
        "success": True,
        "current": cur,
        "rel": rel,
        "children": children[:limit],
        "children_total": len(children),
        "loose_files": loose[:15],
    }


def handle_disk_drives(params: dict) -> dict:
    """枚举所有本地固定磁盘（含C盘，供容量树扫描选择）"""
    drives = []
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        root = f"{letter}:\\"
        if os.path.exists(root):
            try:
                u = shutil.disk_usage(root)
                drives.append({
                    "drive": root,
                    "total": u.total, "total_text": human_size(u.total),
                    "free": u.free, "free_text": human_size(u.free),
                    "used_percent": round(u.used / u.total * 100, 1),
                })
            except OSError:
                pass
    return {"success": True, "drives": drives}


# ============================================================
# 垃圾分类扫描
# ============================================================

def _run_junk_scan(task, params):
    cancel = task["_cancel"]
    keys = list(CATEGORY_DEFS.keys())
    results = []
    total_size, total_count = 0, 0

    for i, key in enumerate(keys):
        if cancel.is_set():
            task["status"] = "cancelled"
            return
        cdef = CATEGORY_DEFS[key]
        task["progress"] = {"stage": "scan", "current": cdef["name"],
                            "done": i, "total": len(keys)}

        if key == "recycle_bin":
            size, count = recycle_bin_info(DRIVE)
        else:
            size, count = 0, 0
            for spec in cdef.get("specs", []):
                s, c = _scan_spec(spec)
                size += s
                count += c

        existing_paths = [p for spec in cdef.get("specs", [])
                          for p in [spec.get("base")] if p and os.path.isdir(p)]
        results.append({
            "key": key,
            "name": cdef["name"],
            "desc": cdef["desc"],
            "risk": cdef["risk"],
            "default_checked": cdef["default"],
            "size": size,
            "size_text": human_size(size),
            "count": count,
            "paths": existing_paths,
        })
        total_size += size
        total_count += count

    task["result"] = {
        "categories": results,
        "total_size": total_size,
        "total_size_text": human_size(total_size),
        "total_count": total_count,
    }


# ============================================================
# 清理执行
# ============================================================

def _clean_path_tree(base, guard_mei=False, progress_cb=None):
    """删除 base 下全部文件并尽量移除空子目录。返回 (释放字节, 删除数, 失败数)。
    progress_cb(已处理文件数, 已释放字节) 供清理任务实时上报进度。"""
    freed, deleted, failed = 0, 0, 0
    if not base or not os.path.isdir(base):
        return freed, deleted, failed

    processed = 0
    for root, dirs, files in os.walk(base, topdown=True, onerror=lambda e: None):
        if guard_mei and _is_guarded(root):
            dirs[:] = []
            continue
        for f in files:
            fp = os.path.join(root, f)
            if guard_mei and _is_guarded(fp):
                continue
            try:
                size = os.path.getsize(fp)
                os.remove(fp)
                freed += size
                deleted += 1
            except OSError:
                failed += 1
            processed += 1
            if progress_cb and processed % 50 == 0:
                progress_cb(processed, freed)

    if progress_cb:
        progress_cb(processed, freed)

    for root, dirs, files in os.walk(base, topdown=False, onerror=lambda e: None):
        for d in dirs:
            try:
                os.rmdir(os.path.join(root, d))
            except OSError:
                pass
    return freed, deleted, failed


def _run_cleanup(task, params):
    categories = params.get("categories", [])
    unknown = [c for c in categories if c not in CATEGORY_DEFS]
    if unknown:
        task["result"] = {"success": False, "error": f"非法清理项: {unknown}"}
        return

    cancel = task["_cancel"]
    results, total_freed, total_deleted, total_failed = [], 0, 0, 0

    for i, key in enumerate(categories):
        if cancel.is_set():
            task["status"] = "cancelled"
            break
        cdef = CATEGORY_DEFS[key]
        task["progress"] = {"stage": "clean", "current": cdef["name"],
                            "done": i, "total": len(categories)}

        if key == "recycle_bin":
            try:
                freed, deleted = empty_recycle_bin(DRIVE)
                failed = 0
            except OSError as e:
                freed, deleted, failed = 0, 0, 1
                task["last_error"] = str(e)
        else:
            freed, deleted, failed = 0, 0, 0

            # 逐文件实时进度: 已处理文件数 / 已释放字节（跨分类累计）
            def _file_progress(done_n, freed_n,
                               _base_del=total_deleted, _base_freed=total_freed,
                               _idx=i, _tot=len(categories), _name=cdef["name"]):
                task["progress"] = {
                    "stage": "clean", "current": _name,
                    "done": _idx, "total": _tot,
                    "files_done": _base_del + done_n,
                    "freed": _base_freed + freed_n,
                }

            for spec in cdef.get("specs", []):
                f, d, x = _clean_path_tree(spec.get("base", ""),
                                           guard_mei=cdef.get("guard_mei", False),
                                           progress_cb=_file_progress)
                freed += f
                deleted += d
                failed += x

        results.append({
            "key": key, "name": cdef["name"],
            "freed": freed, "freed_text": human_size(freed),
            "deleted": deleted, "failed": failed,
        })
        total_freed += freed
        total_deleted += deleted
        total_failed += failed

    task["result"] = {
        "success": True,
        "categories": results,
        "total_freed": total_freed,
        "total_freed_text": human_size(total_freed),
        "total_deleted": total_deleted,
        "total_failed": total_failed,
        "hint": "部分文件被占用或受权限保护时已自动跳过；浏览器缓存建议关闭浏览器后再清理效果更佳" if total_failed else None,
    }


# ============================================================
# 后台任务管理器（扫描/清理共用：进度、取消、轮询）
# ============================================================

_tasks = {}
_lock = threading.Lock()


def _start_task(kind, runner, params):
    # 同类任务进行中则复用（幂等）
    with _lock:
        for t in _tasks.values():
            if t["kind"] == kind and t["status"] == "running":
                return t["id"], True

    task_id = uuid.uuid4().hex[:12]
    task = {
        "id": task_id, "kind": kind, "status": "running",
        "progress": {}, "result": None, "error": None,
        "started": time.time(), "_cancel": threading.Event(),
    }
    _tasks[task_id] = task

    def _worker():
        try:
            runner(task, params)
            if task["status"] == "running":
                task["status"] = "done"
        except Exception as e:
            task["status"] = "error"
            task["error"] = str(e)

    threading.Thread(target=_worker, daemon=True).start()
    return task_id, False


def _task_view(task_id):
    with _lock:
        task = _tasks.get(task_id)
        if not task:
            return None
        return {
            "id": task["id"], "kind": task["kind"], "status": task["status"],
            "progress": task["progress"], "result": task["result"],
            "error": task["error"], "elapsed": round(time.time() - task["started"], 1),
        }


def _cancel_task(task_id):
    with _lock:
        task = _tasks.get(task_id)
        if task and task["status"] == "running":
            task["_cancel"].set()
            return True
    return False


# 清理任务互斥
_cleanup_mutex = threading.Lock()
# H9（安全改造 R1）：当前运行中的清理分类 —— 供并发请求如实透出「正在执行什么」
_cleanup_current = {"categories": []}


# ============================================================
# 业务处理器（Web路由 与 桌面JS桥接 共用）
# ============================================================

def handle_disk_overview(params: dict) -> dict:
    """C盘容量概览"""
    try:
        usage = shutil.disk_usage(DRIVE)
        return {
            "success": True,
            "drive": DRIVE,
            "total": usage.total, "total_text": human_size(usage.total),
            "used": usage.used, "used_text": human_size(usage.used),
            "free": usage.free, "free_text": human_size(usage.free),
            "used_percent": round(usage.used / usage.total * 100, 1),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_disk_scan(params: dict) -> dict:
    """启动后台扫描: type=junk|large|tree"""
    scan_type = params.get("type", "junk")
    if scan_type == "junk":
        scan_id, reused = _start_task("junk", _run_junk_scan, {})
        return {"success": True, "scan_id": scan_id, "reused": reused}
    elif scan_type == "large":
        p = {"root": params.get("root") or DRIVE,
             "min_mb": params.get("min_mb", 200)}
        scan_id, reused = _start_task("large", _run_large_scan, p)
        return {"success": True, "scan_id": scan_id, "reused": reused}
    elif scan_type == "tree":
        root = (params.get("root") or DRIVE).strip()
        if not os.path.isdir(root):
            return {"success": False, "error": f"目录不存在: {root}"}
        p = {"root": root, "min_mb": params.get("min_mb", 100)}
        scan_id, reused = _start_task("tree", _run_tree_scan, p)
        return {"success": True, "scan_id": scan_id, "reused": reused}
    return {"success": False, "error": f"未知扫描类型: {scan_type}"}


def handle_disk_scan_status(params: dict) -> dict:
    """轮询任务状态与结果"""
    view = _task_view(params.get("scan_id", ""))
    if view is None:
        return {"success": False, "error": "任务不存在或已过期"}
    return {"success": True, "task": view}


def handle_disk_scan_cancel(params: dict) -> dict:
    ok = _cancel_task(params.get("scan_id", ""))
    return {"success": True, "cancelled": ok}


def handle_disk_cleanup(params: dict) -> dict:
    """启动后台清理: categories=逗号分隔的分类key（仅接受服务端白名单，同步前置校验）

    H9（安全改造 R1）：并发请求改为**显式拒绝并如实透出** —— 原实现在已有同类
    任务时会静默复用旧 job、忽略本次 categories，却仍把新的 categories 回显给
    调用方，前端误以为已生效。现在复用场景返回明确错误码 `cleanup_busy` 与
    正在运行的分类。
    """
    raw = (params.get("categories") or "").strip()
    if not raw:
        return {"success": False, "error": "未指定清理项"}
    categories = [c.strip() for c in raw.split(",") if c.strip()]

    # 安全前置校验：拒绝一切白名单之外的清理项
    unknown = [c for c in categories if c not in CATEGORY_DEFS]
    if unknown:
        return {"success": False, "error": f"非法清理项: {unknown}"}

    if not _cleanup_mutex.acquire(blocking=False):
        return {"success": False, "code": "cleanup_busy",
                "running_categories": list(_cleanup_current["categories"]),
                "requested_categories": categories,
                "error": "已有清理任务正在进行中，本次请求未执行"
                         "（请等待完成或取消后重试）"}
    _cleanup_mutex.release()

    def runner(task, p):
        _cleanup_mutex.acquire()
        _cleanup_current["categories"] = list(p.get("categories") or [])
        try:
            _run_cleanup(task, p)
        finally:
            _cleanup_current["categories"] = []
            _cleanup_mutex.release()

    job_id, reused = _start_task("cleanup", runner, {"categories": categories})
    if reused:
        # 同类任务在跑：本次未生效，如实透出（不再静默丢弃并误导性回显新分类）
        return {"success": False, "code": "cleanup_busy", "job_id": job_id,
                "reused": True,
                "running_categories": list(_cleanup_current["categories"]),
                "requested_categories": categories,
                "error": "已有清理任务正在进行中，本次请求未执行"}
    return {"success": True, "job_id": job_id, "categories": categories}


def handle_disk_open_location(params: dict) -> dict:
    """在资源管理器中定位文件/目录（大文件手动处理的辅助入口）"""
    path = (params.get("path") or "").strip()
    if not path or not os.path.exists(path):
        return {"success": False, "error": "路径不存在"}
    try:
        subprocess.Popen(["explorer", "/select," + os.path.normpath(path)])
        return {"success": True, "path": path}
    except Exception as e:
        return {"success": False, "error": str(e)}
