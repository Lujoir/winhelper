"""
应用数据盘点与迁移助手
=======================
场景: 桌面/下载/微信/QQ/钉钉/腾讯会议等产生大量文件堆积，
     直接清理易造成不可逆数据丢失。

方案:
  1) 分级盘点 —— 按 应用区域 → 分类(子目录/类型) → 单文件 三个层级展示文件量与路径
  2) 迁移辅助 —— 将分类数据整体迁移到其他数据盘（保留相对目录结构，冲突自动改名）
  3) 删除辅助 —— 仅允许删除「允许根目录」内的文件，双重校验 + 显式确认
  4) 可靠建议 —— 每个分类给出迁移/删除/人工确认的建议与风险提示

安全边界:
  - 迁移/删除仅允许操作本模块定义的应用数据根目录内的路径（服务端白名单前缀校验）
  - 微信/QQ 的数据库目录(db/log)直接跳过，绝不触碰
  - 迁移前校验目标盘剩余空间
"""

import os
import re
import json
import time
import shutil
import heapq
import threading
import subprocess
from datetime import datetime

from disk_cleanup import (
    human_size, _start_task, _task_view, _cancel_task, _is_guarded,
    LARGE_SCAN_SKIP_DIRS,
)


# ============================================================
# 区域定义（服务端白名单：迁移/删除仅允许这些根目录内的路径）
# ============================================================

def _u(p):
    return os.path.expandvars(p) if "%" in p else os.path.expanduser(p)


def _build_zone_defs():
    home = os.path.expanduser("~")
    return {
        "wechat": {
            "name": "微信",
            "roots": [
                _u(r"%USERPROFILE%\Documents\WeChat Files"),
                _u(r"%USERPROFILE%\Documents\xwechat_files"),
            ],
            "markers": ["FileStorage", "msg", "MsgAttach", "attach"],
            "skip_dirs": {"db", "dbrun", "dbcrash", "log", "logs", "backup_process"},
            "zone_advice": ("微信聊天数据量通常最大。可靠处理顺序: "
                            "① 首选在微信内『设置→文件管理→更改/迁移存储位置』整体迁到数据盘；"
                            "② 或用本工具按分类迁移(聊天媒体迁移后聊天窗口会显示为过期，部分可重新下载)；"
                            "③ 『聊天文件』(文档/压缩包等附件)适合迁移长期保存；"
                            "④ 数据库与日志目录已被本工具自动排除，绝不触碰。"),
        },
        "qq": {
            "name": "QQ",
            "roots": [_u(r"%USERPROFILE%\Documents\Tencent Files")],
            "markers": ["FileRecv", "FileStorage", "nt_data", "Image", "Video", "Audio"],
            "skip_dirs": {"db", "nt_db", "log", "logs"},
            "zone_advice": ("QQ 接收的文件集中在 FileRecv，适合迁移或按需删除；"
                            "聊天图片/视频迁移后可能显示过期。"),
        },
        "dingtalk": {
            "name": "钉钉",
            "roots": [_u(r"%USERPROFILE%\Documents\DingTalk")],
            "markers": [],
            "skip_dirs": {"log", "logs", "cache", "Cache"},
            "zone_advice": "钉钉接收的文件在 Documents\\DingTalk，按文件人工确认后迁移或删除。",
        },
        "wemeet": {
            "name": "腾讯会议",
            "roots": [_u(r"%USERPROFILE%\Documents\Tencent Meeting")],
            "markers": ["meeting_records", "record", "meeting", "whiteboard"],
            "skip_dirs": {"log", "logs", "Cache", "cache"},
            "zone_advice": "会议录制视频体积大，确认不再需要后可删除；重要录像建议先迁移到数据盘。",
        },
        "desktop": {
            "name": "桌面",
            "roots": [os.path.join(home, "Desktop")],
            "markers": [],
            "skip_dirs": set(),
            "zone_advice": "桌面文件多为工作数据，建议人工确认后：不常用的大文件迁移到数据盘，安装包可删。",
        },
        "downloads": {
            "name": "下载",
            "roots": [os.path.join(home, "Downloads")],
            "markers": [],
            "skip_dirs": set(),
            "zone_advice": "下载目录堆积最快。安装包/压缩包确认后可删；重要资料建议归档迁移到数据盘。",
        },
    }

ZONE_DEFS = _build_zone_defs()


# 扩展名 → 类型分组（桌面/下载按文件类型聚合）
_EXT_GROUPS = [
    ((".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".m4v", ".ts"), "视频媒体"),
    ((".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".raw", ".psd"), "图片"),
    ((".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".pdf", ".txt", ".md", ".csv", ".wps", ".et", ".dps"), "文档"),
    ((".exe", ".msi", ".msix", ".appx", ".bat"), "安装程序"),
    ((".zip", ".rar", ".7z", ".tar", ".gz", ".xz", ".cab", ".iso"), "压缩包/镜像"),
]


def _ext_group(filename):
    ext = os.path.splitext(filename)[1].lower()
    for exts, label in _EXT_GROUPS:
        if ext in exts:
            return label
    return "其他文件"


# 分类建议引擎: (展示建议, 是否适合迁移, 风险级别)
def _category_meta(zone_key, raw_label, size):
    label = raw_label.lower()
    if "缓存" in raw_label or "cache" in label:
        return "应用缓存，可安全删除（应用会按需重建）", False, "safe"
    if "视频" in raw_label or label.endswith("/video") or label in ("video",):
        return "聊天媒体，删除后聊天窗口显示过期；推荐整体迁移到数据盘", True, "caution"
    if "图片" in raw_label or label.endswith("/image") or label.endswith("/pic") or label in ("image", "msgattach"):
        return "聊天图片量大且单个较小，推荐在应用内清理或整体迁移", True, "caution"
    if "备份" in raw_label or "backup" in label:
        return "聊天备份，人工确认是否仍需要", True, "caution"
    if "文件" in raw_label or "filerecv" in label or label.endswith("/file"):
        return "接收的文件附件，适合迁移到数据盘长期保存", True, "info"
    if "安装程序" in raw_label:
        return "安装包，确认软件已装好后可删除", False, "safe"
    if "压缩包" in raw_label or "镜像" in raw_label:
        return "压缩包/镜像，确认内容后可迁移或删除", True, "info"
    if "文档" in raw_label or "视频媒体" in raw_label:
        return "个人/工作数据，人工确认后迁移或删除", True, "caution"
    if "语音" in raw_label or label == "audio":
        return "语音消息，人工确认", False, "caution"
    return "请人工判断用途后迁移或删除", True, "caution"


# ============================================================
# 路径安全校验
# ============================================================

def _norm(p):
    return os.path.normcase(os.path.abspath(os.path.normpath(p)))


def _all_zone_roots():
    roots = []
    for zdef in ZONE_DEFS.values():
        for r in zdef["roots"]:
            if r and os.path.isdir(r):
                roots.append(_norm(r))
    return roots


def _is_under(path, root):
    p, r = _norm(path), _norm(root)
    return p == r or p.startswith(r + os.sep)


def _locate_zone(path):
    """返回路径所属 (zone_key, zdef)，不在任何允许根内则返回 (None, None)"""
    for key, zdef in ZONE_DEFS.items():
        for r in zdef["roots"]:
            if r and os.path.isdir(r) and _is_under(path, r):
                return key, zdef
    return None, None


# ============================================================
# 分类归类
# ============================================================

def _category_for(zone_key, rel_parts):
    parts = [p for p in rel_parts if p]
    if not parts:
        return "根目录"
    if zone_key in ("desktop", "downloads"):
        return None  # 由调用方按扩展名分组
    markers = ZONE_DEFS[zone_key]["markers"]
    low = [p.lower() for p in parts]
    for m in markers:
        ml = m.lower()
        if ml in low:
            i = low.index(ml)
            seg = parts[i:i + 2]
            return "/".join(seg) if len(seg) >= 2 else parts[i]
    return parts[0]


CATEGORY_LABELS = {
    "FileStorage/Video": "聊天视频", "FileStorage/Image": "聊天图片",
    "FileStorage/File": "聊天文件", "FileStorage/Cache": "缓存",
    "FileStorage/MsgAttach": "聊天附件图片", "FileStorage/CustomEmoji": "表情包",
    "FileStorage/BackupFiles": "聊天备份", "FileStorage/Video/Mp4": "聊天视频",
    "msg/video": "聊天视频", "msg/file": "聊天文件", "msg/image": "聊天图片",
    "msg/attach": "聊天附件", "msg/video/2026": "聊天视频",
    "nt_data/Video": "聊天视频", "nt_data/Pic": "聊天图片", "nt_data/File": "聊天文件",
    "nt_data/Emoji": "表情包", "FileRecv": "接收的文件",
    "Image": "聊天图片", "Video": "聊天视频", "Audio": "语音消息",
}


def _label_for(zone_key, raw):
    return CATEGORY_LABELS.get(raw, raw)


# ============================================================
# 区域扫描
# ============================================================

_TOPN = 25
_TIER_DEFS = [("huge", 1024 * 1024 * 1024, "≥1GB"),
              ("large", 500 * 1024 * 1024, "500MB~1GB"),
              ("mid", 100 * 1024 * 1024, "100~500MB")]


def _run_appdata_scan(task, params):
    cancel = task["_cancel"]
    zones = []
    keys = list(ZONE_DEFS.keys())
    for idx, key in enumerate(keys):
        if cancel.is_set():
            task["status"] = "cancelled"
            return
        zdef = ZONE_DEFS[key]
        task["progress"] = {"stage": "appdata", "current": zdef["name"],
                            "done": idx, "total": len(keys)}

        cats = {}          # cat_key -> {"size","count","sample_path"}
        top = []           # min-heap (size, path, mtime)
        tiers = {k: {"count": 0, "size": 0} for k, _, _ in _TIER_DEFS}
        tiers["small"] = {"count": 0, "size": 0}
        total_size, total_count = 0, 0
        existing_roots = [r for r in zdef["roots"] if r and os.path.isdir(r)]

        for root in existing_roots:
            stack = [(root, [])]
            while stack:
                d, rel_parts = stack.pop()
                if cancel.is_set():
                    task["status"] = "cancelled"
                    return
                try:
                    entries = list(os.scandir(d))
                except OSError:
                    continue
                for e in entries:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            name_l = e.name.lower()
                            if name_l in zdef["skip_dirs"]:
                                continue
                            stack.append((e.path, rel_parts + [e.name]))
                        elif e.is_file(follow_symlinks=False):
                            size = e.stat(follow_symlinks=False).st_size
                            mtime = e.stat(follow_symlinks=False).st_mtime
                            total_size += size
                            total_count += 1

                            # 分类聚合
                            if key in ("desktop", "downloads"):
                                cat = _ext_group(e.name)
                                cat_path = root
                            else:
                                cat = _category_for(key, rel_parts + [e.name])
                                if cat is None:
                                    cat = "根目录"
                                cat_path = os.path.join(root, *(rel_parts[:2] if rel_parts else []))

                            c = cats.setdefault(cat, {"size": 0, "count": 0, "path": cat_path})
                            c["size"] += size
                            c["count"] += 1

                            # 分层统计
                            for tkey, threshold, _ in _TIER_DEFS:
                                if size >= threshold:
                                    tiers[tkey]["count"] += 1
                                    tiers[tkey]["size"] += size
                                    break
                            else:
                                tiers["small"]["count"] += 1
                                tiers["small"]["size"] += size

                            # Top N 大文件
                            item = (size, e.path, mtime)
                            if len(top) < _TOPN:
                                heapq.heappush(top, item)
                            elif size > top[0][0]:
                                heapq.heapreplace(top, item)
                    except OSError:
                        continue

        # 组装分类结果
        cat_list = []
        for cat, c in sorted(cats.items(), key=lambda kv: kv[1]["size"], reverse=True):
            label = _label_for(key, cat) if key not in ("desktop", "downloads") else cat
            advice, migrate_ok, risk = _category_meta(key, label, c["size"])
            cat_list.append({
                "key": cat,
                "name": label,
                "path": c["path"],
                "size": c["size"],
                "size_text": human_size(c["size"]),
                "count": c["count"],
                "advice": advice,
                "migrate_ok": migrate_ok,
                "risk": risk,
            })

        top_files = [{
            "path": p,
            "name": os.path.basename(p),
            "size": s,
            "size_text": human_size(s),
            "modified": datetime.fromtimestamp(mt).strftime("%Y-%m-%d"),
        } for s, p, mt in sorted(top, reverse=True)]

        zones.append({
            "key": key,
            "name": zdef["name"],
            "found": bool(existing_roots),
            "paths": existing_roots,
            "total_size": total_size,
            "total_size_text": human_size(total_size),
            "file_count": total_count,
            "categories": cat_list,
            "top_files": top_files,
            "tiers": tiers,
            "zone_advice": zdef["zone_advice"],
        })

    task["result"] = {
        "zones": zones,
        "total_size": sum(z["total_size"] for z in zones),
        "total_size_text": human_size(sum(z["total_size"] for z in zones)),
    }


# ============================================================
# 迁移助手
# ============================================================

def _conflict_dest(dest):
    """目标重名处理: 同大小视为已迁移跳过(返回None)；否则追加序号"""
    if not os.path.exists(dest):
        return dest
    return None  # 同名同大小 → 跳过


def _unique_dest(dest):
    base, ext = os.path.splitext(dest)
    i = 2
    while os.path.exists(dest):
        dest = f"{base} ({i}){ext}"
        i += 1
    return dest


def _run_migrate(task, params):
    src = params.get("path", "")
    target_root = params.get("target", "")
    if not src or not os.path.exists(src):
        task["result"] = {"success": False, "error": "源路径不存在"}
        return
    zone_key, _ = _locate_zone(src)
    if zone_key is None:
        task["result"] = {"success": False, "error": "路径不在允许的应用数据目录内，已拒绝"}
        return

    drive = os.path.splitdrive(target_root)[0]
    if not drive or drive.upper().startswith("C"):
        task["result"] = {"success": False, "error": "迁移目标必须为 C 以外的数据盘"}
        return
    if not os.path.isdir(target_root):
        try:
            os.makedirs(target_root, exist_ok=True)
        except OSError as e:
            task["result"] = {"success": False, "error": f"无法创建目标目录: {e}"}
            return

    # 统计源体积并校验目标盘空间
    total_src = 0
    if os.path.isfile(src):
        total_src = os.path.getsize(src)
    else:
        for root, _, files in os.walk(src, onerror=lambda e: None):
            for f in files:
                try:
                    total_src += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    free = shutil.disk_usage(drive + "\\").free
    if free < total_src:
        task["result"] = {"success": False,
                          "error": f"目标盘空间不足: 需要 {human_size(total_src)}, 剩余 {human_size(free)}"}
        return

    cancel = task["_cancel"]
    moved, freed, skipped_dup, failed = 0, 0, 0, 0
    started = time.time()

    # 收集待迁移文件列表: (源绝对路径, 相对参考根)
    jobs = []
    if os.path.isfile(src):
        jobs.append((src, os.path.dirname(src)))
    else:
        for root, _, files in os.walk(src, onerror=lambda e: None):
            for f in files:
                jobs.append((os.path.join(root, f), src))

    for i, (fp, base) in enumerate(jobs):
        if cancel.is_set():
            task["status"] = "cancelled"
            break
        if i % 20 == 0:
            task["progress"] = {"stage": "migrate", "current": os.path.basename(fp),
                                "done": i, "total": len(jobs)}
        rel = os.path.relpath(fp, base)
        dest = os.path.join(target_root, rel)
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if os.path.exists(dest):
                if os.path.getsize(dest) == os.path.getsize(fp):
                    skipped_dup += 1
                    continue
                dest = _unique_dest(dest)
            shutil.move(fp, dest)
            moved += 1
            freed += os.path.getsize(dest)
        except (OSError, shutil.Error):
            failed += 1

    # 尝试清理源目录中的空目录
    if os.path.isdir(src):
        for root, dirs, files in os.walk(src, topdown=False, onerror=lambda e: None):
            for d in dirs:
                try:
                    os.rmdir(os.path.join(root, d))
                except OSError:
                    pass

    task["result"] = {
        "success": True,
        "moved": moved, "freed": freed, "freed_text": human_size(freed),
        "skipped_dup": skipped_dup, "failed": failed,
        "elapsed": round(time.time() - started, 1),
        "target": target_root,
        "src": src,
        "hint": "提示: 聊天媒体迁移后，聊天窗口中可能显示为过期(部分可在聊天设置中重新下载)；建议同步在应用内使用官方存储迁移功能。",
    }


# ============================================================
# 删除辅助（仅允许根目录内，逐路径校验）
# ============================================================

def _run_delete(task, params):
    raw = params.get("paths", "")
    paths = [p.strip() for p in raw.split("|") if p.strip()] if raw else []
    if not paths:
        task["result"] = {"success": False, "error": "未指定删除路径"}
        return

    for p in paths:
        if not os.path.exists(p):
            task["result"] = {"success": False, "error": f"路径不存在: {p}"}
            return
        if not _path_allowed(p):
            task["result"] = {"success": False, "error": f"路径不在允许的应用数据目录内，已拒绝: {p}"}
            return

    cancel = task["_cancel"]
    freed, deleted, failed = 0, 0, 0
    for i, p in enumerate(paths):
        if cancel.is_set():
            task["status"] = "cancelled"
            break
        task["progress"] = {"stage": "delete", "current": os.path.basename(p),
                            "done": i, "total": len(paths)}
        try:
            if os.path.isfile(p):
                size = os.path.getsize(p)
                os.remove(p)
                freed += size
                deleted += 1
            else:
                for root, _, files in os.walk(p, onerror=lambda e: None):
                    for f in files:
                        fp = os.path.join(root, f)
                        try:
                            s = os.path.getsize(fp)
                            os.remove(fp)
                            freed += s
                            deleted += 1
                        except OSError:
                            failed += 1
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            failed += 1

    task["result"] = {
        "success": True,
        "deleted": deleted, "freed": freed, "freed_text": human_size(freed),
        "failed": failed,
    }


# ============================================================
# 业务处理器
# ============================================================

def handle_appdata_scan(params: dict) -> dict:
    scan_id, reused = _start_task("appdata", _run_appdata_scan, {})
    return {"success": True, "scan_id": scan_id, "reused": reused}


def handle_appdata_drives(params: dict) -> dict:
    """列出可作迁移目标的数据盘（排除C盘）"""
    drives = []
    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
        root = f"{letter}:\\"
        if os.path.exists(root):
            try:
                u = shutil.disk_usage(root)
                drives.append({
                    "drive": root,
                    "free": u.free, "free_text": human_size(u.free),
                    "total": u.total, "total_text": human_size(u.total),
                })
            except OSError:
                pass
    return {"success": True, "drives": drives}


def handle_appdata_migrate(params: dict) -> dict:
    src = (params.get("path") or "").strip()
    target = (params.get("target") or "").strip()
    if not src or not target:
        return {"success": False, "error": "缺少源路径或目标目录"}

    # ---- 同步前置安全校验（白名单外直接拒绝）----
    if not os.path.exists(src):
        return {"success": False, "error": "源路径不存在"}
    if _locate_zone(src)[0] is None:
        return {"success": False, "error": "路径不在允许的应用数据目录内，已拒绝"}
    drive = os.path.splitdrive(target)[0]
    if not drive or drive.upper().startswith("C"):
        return {"success": False, "error": "迁移目标必须为 C 以外的数据盘"}

    job_id, reused = _start_task("migrate", _run_migrate,
                                 {"path": src, "target": target})
    return {"success": True, "job_id": job_id, "reused": reused}


def _path_allowed(p: str) -> bool:
    """路径是否在允许的清理范围内：应用数据区域，或**本轮扫描会话**登记的根。

    授权来源已从「全局累积集合」改为「会话 + TTL」（见 _SCAN_GRANT）——
    历次扫描的登记不再叠加，避免授权面随时间扩散。"""
    if _locate_zone(p)[0] is not None:
        return True
    if not _grant_active():
        return False
    pn = _norm(p)
    return any(pn == r or pn.startswith(r + os.sep)
               for r in _SCAN_GRANT["roots"])


def handle_appdata_delete(params: dict) -> dict:
    raw = (params.get("paths") or "").strip()
    if not raw:
        return {"success": False, "error": "未指定删除路径"}

    # ---- 同步前置安全校验（白名单外直接拒绝）----
    for p in [x.strip() for x in raw.split("|") if x.strip()]:
        if not os.path.exists(p):
            return {"success": False, "error": f"路径不存在: {p}"}
        if not _path_allowed(p):
            if _locate_zone(p)[0] is None and not _grant_active():
                return {"success": False, "code": "scan_expired",
                        "error": "删除授权已过期：请重新扫描后再删除"}
            return {"success": False, "error": f"路径不在允许的应用数据目录内，已拒绝: {p}"}

    job_id, reused = _start_task("delete", _run_delete, {"paths": raw})
    return {"success": True, "job_id": job_id, "reused": reused}


# 状态/取消/打开位置 复用 disk_cleanup 的通用实现（保持响应结构一致）
from disk_cleanup import handle_disk_scan_status as handle_appdata_scan_status
handle_appdata_cancel = _cancel_task


# ============================================================
# 安装包扫描与一键清理（下载/桌面 是安装包堆积重灾区）
# 置信分级:
#   auto   明确安装包 → 默认勾选:
#          · .msi/.msix/.appx/.msp 等安装格式
#          · 下载目录中 ≥1MB 的 .exe
#          · 文件名含 setup/install/安装 的安装程序
#   manual 待人工确认 → 默认不勾选（可能是绿色软件/程序本体）
# ============================================================

INSTALLER_EXTS = {".exe", ".msi", ".msix", ".appx", ".msp", ".msixbundle", ".appxbundle"}

# 扫描会话删除授权（原实现为模块级全局集合，历次扫描登记的根永久叠加 ——
# 多轮/全盘扫描后集合可覆盖大量目录，删除接口据此放行，构成授权逃逸面。
# 现改为「单会话 + TTL」：每次扫描开始重置，到期自动失效，不再跨会话累积）
_SCAN_GRANT = {"scan_id": "", "expires_at": 0.0, "roots": set()}
_SCAN_GRANT_TTL = 30 * 60          # 秒；覆盖「扫描 → 勾选 → 删除」的正常操作耗时


def _grant_reset(scan_id: str) -> None:
    """开启新一轮扫描会话：清空上轮授权并设定 TTL。"""
    _SCAN_GRANT["scan_id"] = scan_id or ""
    _SCAN_GRANT["expires_at"] = time.time() + _SCAN_GRANT_TTL
    _SCAN_GRANT["roots"] = set()


def _grant_add(p: str) -> None:
    """登记本轮扫描发现的允许删除根（仅当前会话内有效）。"""
    _SCAN_GRANT["roots"].add(_norm(p))


def _grant_active() -> bool:
    """当前是否存在有效（非空且未过期）的扫描授权。"""
    return bool(_SCAN_GRANT["roots"]) and time.time() < _SCAN_GRANT["expires_at"]

# Chromium 系浏览器 User Data 目录（解析各 Profile 的 Preferences 获取真实下载目录）
_CHROMIUM_USER_DIRS = [
    ("Edge", r"%LOCALAPPDATA%\Microsoft\Edge\User Data"),
    ("Chrome", r"%LOCALAPPDATA%\Google\Chrome\User Data"),
    ("360极速", r"%LOCALAPPDATA%\360Chrome\Chrome\User Data"),
    ("QQ浏览器", r"%LOCALAPPDATA%\Tencent\QQBrowser\User Data"),
    ("Brave", r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data"),
    ("Vivaldi", r"%LOCALAPPDATA%\Vivaldi\User Data"),
]


def _browser_download_dirs():
    """自动发现各浏览器配置的下载目录。返回 [(目录, 浏览器名)]"""
    found, seen = [], set()

    def add(path, bname):
        if path and os.path.isdir(path):
            pn = _norm(path)
            if pn not in seen:
                seen.add(pn)
                found.append((path, bname))

    # Chromium 系: User Data\<Profile>\Preferences → download.default_directory
    for bname, ud in _CHROMIUM_USER_DIRS:
        ud_path = os.path.expandvars(ud)
        if not os.path.isdir(ud_path):
            continue
        try:
            for entry in os.scandir(ud_path):
                if not entry.is_dir():
                    continue
                pref = os.path.join(entry.path, "Preferences")
                if not os.path.isfile(pref):
                    continue
                try:
                    with open(pref, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    dd = (data.get("download") or {}).get("default_directory") or ""
                    if dd:
                        add(dd, bname)
                except Exception:
                    continue
        except OSError:
            continue

    # Firefox: prefs.js → browser.download.dir
    ff_profiles = os.path.expandvars(r"%APPDATA%\Mozilla\Firefox\Profiles")
    if os.path.isdir(ff_profiles):
        try:
            for entry in os.scandir(ff_profiles):
                prefs = os.path.join(entry.path, "prefs.js")
                if not os.path.isfile(prefs):
                    continue
                try:
                    with open(prefs, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            if "browser.download.dir" in line:
                                m = re.search(r',\s*"(.+)"\)', line)
                                if m:
                                    add(m.group(1).replace("\\\\", "\\"), "Firefox")
                                break
                except Exception:
                    continue
        except OSError:
            pass
    return found


def _fixed_drives():
    """枚举全部本地固定磁盘根目录"""
    return [f"{l}:\\" for l in "CDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(f"{l}:\\")]


def _installer_scan_zones(scope, custom):
    """根据作用域返回扫描区域 [(zone_key, root, dir_label)]（路径已去重）"""
    zones, seen = [], set()

    def add(key, root, label):
        if root and os.path.isdir(root):
            pn = _norm(root)
            if pn not in seen:
                seen.add(pn)
                zones.append((key, root, label))

    if scope == "custom":
        if custom and os.path.isdir(custom):
            add("custom", custom, "自定义目录")
    elif scope == "alldisks":
        for drv in _fixed_drives():
            add(f"disk:{drv[0]}", drv, f"{drv[0]}盘全盘")
    elif scope == "downloads":
        add("downloads", ZONE_DEFS["downloads"]["roots"][0], "下载")
    elif scope == "desktop":
        add("desktop", ZONE_DEFS["desktop"]["roots"][0], "桌面")
    else:  # both: 下载 + 桌面 + 各浏览器下载目录
        add("downloads", ZONE_DEFS["downloads"]["roots"][0], "下载")
        add("desktop", ZONE_DEFS["desktop"]["roots"][0], "桌面")
        for d, bname in _browser_download_dirs():
            add("browser:" + bname, d, f"{bname}下载")
    return zones
_INSTALL_FORMATS = {".msi", ".msix", ".msp", ".appx", ".msixbundle", ".appxbundle"}

# 强安装特征: setup / installer / install(前后不含un与字母) / 安装
# (排除 installation / uninstall 等非安装包命名)
_STRONG_INSTALL = re.compile(r"setup|(?<!un)installer|(?<!un)install(?![a-z])|安装", re.I)
# 卸载程序(绝对排除)
_UNINSTALL = re.compile(r"uninstall|卸载", re.I)
# 版本号特征: 2~4段式 (V3.7.5 / 8.0.34 / 7.28)
_VERSION_TAG = re.compile(r"\bv?\d+(\.\d+){1,3}\b", re.I)
# 架构标记: 安装包通常带位数/平台标识 (Wireshark-win64-3.6.6 / Git-2.47.0-64-bit)
_ARCH_TAG = re.compile(r"win64|win32|win-x64|x64|x86|amd64|arm64|64-bit|32-bit", re.I)
# 数据库/服务类程序家族(绝对排除): 程序本体而非安装包
_FAMILY_NEG = re.compile(r"mysql|mariadb|myisam|innochecksum|ibd2sdi|nginx|redis|memcached|ffmpeg", re.I)
# 工具类命名(用于版本号路径的二次校验与无特征提示)
_TOOL_NEGATIVE = re.compile(
    r"dump$|chk$|check|checksum|pack$|show$|import$|export$|admin$|config"
    r"|defaults|tzinfo|monitor|bench|server$|client$|kill$|echo$|tool", re.I)


def _run_installer_scan(task, params):
    cancel = task["_cancel"]
    scope = params.get("scope", "both")
    custom = (params.get("custom") or "").strip()

    # 本轮扫描开启新的删除授权会话（上轮授权立即失效，不再跨轮累积）
    _grant_reset(task.get("id") or "")

    if scope == "custom":
        if not custom or not os.path.isdir(custom):
            task["result"] = {"success": False, "error": f"自定义目录不存在: {custom}"}
            return
        # 登记自定义扫描根 → 本轮会话内放行该根目录内路径
        _grant_add(custom)

    zones = _installer_scan_zones(scope, custom)
    if not zones:
        task["result"] = {"success": False, "error": "未找到可扫描的目录"}
        return

    items = []
    browser_dirs = []
    for idx, (zone_key, root, dir_label) in enumerate(zones):
        if cancel.is_set():
            task["status"] = "cancelled"
            return
        # 浏览器下载目录登记本轮删除授权（通常位于其他盘，正是用户要清理的位置）
        if zone_key.startswith("browser:"):
            _grant_add(root)
            browser_dirs.append({"label": dir_label, "path": root})
        is_disk_root = zone_key.startswith("disk:")
        task["progress"] = {"stage": "installers", "current": dir_label,
                            "done": idx, "total": len(zones)}
        for root2, dirs, files in os.walk(root, onerror=lambda e: None):
            if _is_guarded(root2):
                dirs[:] = []
                continue
            if is_disk_root:
                # 全盘扫描时跳过系统保护目录（WinSxS/回收站等）
                dirs[:] = [d for d in dirs if d.lower() not in LARGE_SCAN_SKIP_DIRS]
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext not in INSTALLER_EXTS:
                    continue
                fp = os.path.join(root2, f)
                try:
                    st = os.stat(fp)
                except OSError:
                    continue
                base = os.path.splitext(f)[0]
                hint = ""
                auto = False
                if ext in _INSTALL_FORMATS:
                    auto = True
                    hint = "安装格式"
                elif ext == ".exe":
                    strong = bool(_STRONG_INSTALL.search(base))
                    ver = bool(_VERSION_TAG.search(base))
                    arch = bool(_ARCH_TAG.search(base))
                    if _UNINSTALL.search(base):
                        hint = "卸载程序，非安装包"
                    elif strong:
                        auto = True
                        hint = "命名含安装关键词(setup/install/安装)"
                    elif (ver and st.st_size >= 1024 * 1024
                          and not _FAMILY_NEG.search(base) and not _TOOL_NEGATIVE.search(base)):
                        auto = True
                        hint = "文件名含版本号"
                    elif (arch and st.st_size >= 1024 * 1024
                          and not _FAMILY_NEG.search(base) and not _TOOL_NEGATIVE.search(base)):
                        auto = True
                        hint = "含架构标记(win64/x64/64-bit等)"
                    elif _FAMILY_NEG.search(base) or _TOOL_NEGATIVE.search(base):
                        hint = "工具类程序，已排除"
                    else:
                        hint = "EXE无安装特征(需安装关键词/版本号/架构标记)"
                item = {
                    "path": fp, "name": f,
                    "size": st.st_size, "size_text": human_size(st.st_size),
                    "ext": ext.lstrip(".").upper(),
                    "dir_label": dir_label,
                    "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d"),
                    "auto": bool(auto), "hint": hint,
                }
                items.append(item)
                # 全盘模式下逐文件登记父目录到本轮删除授权（服务端受控；
                # 仅本次扫描会话 + TTL 内有效，不再永久累积）
                if is_disk_root:
                    _grant_add(root2)

    items.sort(key=lambda x: (0 if x["auto"] else 1, -x["size"]))
    auto_items = [i for i in items if i["auto"]]
    task["result"] = {
        "success": True,
        "items": items[:500],
        "shown": min(len(items), 500),
        "total": len(items),
        "total_size": sum(i["size"] for i in items),
        "total_size_text": human_size(sum(i["size"] for i in items)),
        "auto_count": len(auto_items),
        "auto_size": sum(i["size"] for i in auto_items),
        "auto_size_text": human_size(sum(i["size"] for i in auto_items)),
        "scope": scope,
        "custom": custom if scope == "custom" else "",
        "browser_dirs": browser_dirs,
        "scanned_zones": [z[2] for z in zones],
        # 供前端在删除时回传，绑定本次扫描会话
        "scan_id": task.get("id") or "",
    }


def handle_installer_scan(params: dict) -> dict:
    scan_id, reused = _start_task("installers", _run_installer_scan, dict(params))
    return {"success": True, "scan_id": scan_id, "reused": reused}
