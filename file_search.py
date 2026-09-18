# -*- coding: utf-8 -*-
r"""
file_search.py → search_service.py — EyeTerm「文件检索」服务层（路线 C 自研引擎，2026-09-15）
=================================================================================

架构（v2，ADR-004→ADR-005）：**全部 Everything 依赖移除**（HTTP 5700 / 拉起编排 / exe 定位链 /
实例 ini / 保存路径设置），查询改为直读 fs_indexer 维护的 sqlite 索引
（C:\ProgramData\EyeTerm\filesearch\index.db，普通权限只读）。

检索契约（前端不变）：q（含原生 ext: 拼接段）+ sort/ascending（name/size/mtime 正倒序）；
返回 {success, q, total, count, results[{name,path,size,date_modified}]}，上限 200。

零新增 pip 依赖；一切子进程 CREATE_NO_WINDOW。
"""

import ctypes
import os
import re
import sqlite3
import subprocess
import sys

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

INDEXER_TASK_NAME = "EyeTermFileIndexer"   # 与 fs_indexer.INDEXER_TASK_NAME 同名（双份同源约定）

FS_MAX_RESULTS = 200
FS_SORTABLE = {
    "name": "name COLLATE NOCASE",
    "size": "size",
    "mtime": "mtime",
    "date_modified": "mtime",   # 前端列名兼容
    "ext": "LOWER(SUBSTR(name, INSTR(name, '.') + 1))",   # 扩展名字典序（目录固定排最前，见 handle_fs_query）
}
_FS_EXT_RE = re.compile(r"(?:^|\s)ext:([A-Za-z0-9_;]+)", re.IGNORECASE)


def _parse_date_param(v, end_of_day=False):
    """解析日期参数：支持 ISO 日期字符串或 Unix 时间戳。
    end_of_day=True 时日期字符串视为 23:59:59。"""
    if not v:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
            import time
            t = time.strptime(s, "%Y-%m-%d")
            ts = int(time.mktime(t))
            return ts + 86399 if end_of_day else ts
        return int(float(s))
    except Exception:
        return None


def _fs_db_path():
    """索引库路径（与 fs_indexer.db_path() 同源；env FS_INDEX_DB 供测试覆盖）。"""
    base = os.environ.get("FS_INDEX_DB") or os.path.join(
        os.environ.get("ProgramData") or r"C:\ProgramData", "EyeTerm", "filesearch")
    d = base if base.lower().endswith(".db") else os.path.join(base, "index.db")
    return d


def _connect_ro():
    """只读连接（WAL 库只读 uri 安全；busy_timeout 与索引器写并发共存）。"""
    path = _fs_db_path()
    if not os.path.isfile(path):
        return None
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True, timeout=5)
        conn.execute("PRAGMA busy_timeout=3000")
        return conn
    except Exception:
        return None


def _parse_query(q):
    """拆 ext: 筛选段与关键词（前端语法拼接，服务端转为 sqlite 条件）。"""
    exts = []
    m = _FS_EXT_RE.search(q)
    if m:
        exts = [e for e in m.group(1).lower().split(";") if e]
        q = (q[:m.start()] + " " + q[m.end():])
    kw = q.strip()
    return kw, exts


def _like_escape(kw):
    return kw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _is_user_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _schtasks_task_registered(task_name=None):
    """计划任务注册检测（schtasks /Query，返回码判定；任何异常按未注册如实——不虚构）。
    CREATE_NO_WINDOW 铁律。"""
    try:
        p = subprocess.run(
            ["schtasks", "/Query", "/TN", task_name or INDEXER_TASK_NAME],
            capture_output=True, creationflags=_NO_WINDOW, timeout=10)
        return p.returncode == 0
    except Exception:
        return False


def _indexer_unavailable():
    """indexer_not_running 错误的统一三态 hint（4.1.7 文案修复——废除误导性「请稍后重试」）：
    任务已注册但库未就绪=构建中；未注册=未部署（给出部署指引）。"""
    if _schtasks_task_registered():
        return {"error": "indexer_not_running",
                "hint": "索引构建中（首次需数分钟），请稍后重试"}
    return {"error": "indexer_not_running",
            "hint": "检索索引未部署：请重新安装客户端或由管理员部署"}


def _worker_launch_cmd():
    """worker 启动命令串（计划任务 /TR 用，--pc-elevated-worker 同款形态）：
    exe 态 = 自身 exe + flag；python 态 = python + 同目录 fs_indexer.py + flag。"""
    flag = "--fs-indexer-worker"
    if getattr(sys, "frozen", False):
        return '"%s" %s' % (sys.executable, flag)
    cand = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fs_indexer.py")
    return '"%s" "%s" %s' % (sys.executable, cand, flag)


def handle_fs_indexer_deploy(params=None):
    """索引器部署（4.1.7 方案 B 客户端管理员引导）：注册 SYSTEM 计划任务
    （ONSTART + HIGHEST）并立即 Run 一次（装/部署完即首建，不等重启）。
    管理员会话直接执行；非管理员走 UAC 提权（ShellExecuteW runas，一次拉起
    Create+Run 组合，结果由前端轮询 /status 确认）。幂等：/Create /F 覆盖 + /Run 重复触发无害。"""
    create = ["schtasks", "/Create", "/F", "/TN", INDEXER_TASK_NAME,
              "/TR", _worker_launch_cmd(), "/SC", "ONSTART", "/RL", "HIGHEST"]
    run_now = ["schtasks", "/Run", "/TN", INDEXER_TASK_NAME]
    if _is_user_admin():
        try:
            p1 = subprocess.run(create, capture_output=True, creationflags=_NO_WINDOW, timeout=15)
            if p1.returncode != 0:
                return {"success": False, "already_admin": True,
                        "error": "schtasks_create_failed:%d" % p1.returncode,
                        "detail": (p1.stderr or p1.stdout or b"").decode("utf-8", "replace")[-200:]}
            subprocess.run(run_now, capture_output=True, creationflags=_NO_WINDOW, timeout=15)
            return {"success": True, "already_admin": True,
                    "hint": "索引器已部署并启动，首次构建需数分钟"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    cmdline = '"%s" && "%s"' % (" ".join(create), " ".join(run_now))
    ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", "/C " + cmdline, None, 0)
    if int(ret) <= 32:
        return {"success": False, "elevate_cancelled": True,
                "error": "提权未确认或被拒绝（UAC 已取消）"}
    return {"success": True, "elevated": True,
            "hint": "已发起部署（UAC 需确认），索引构建需数分钟，请稍后"}


def handle_fs_query(params=None):
    """文件检索：params {q, count?, sort?, ascending?}。直读索引库（只读），引擎不可用如实提示。"""
    params = params or {}
    q = str(params.get("q") or "").strip()
    if not q:
        return {"success": False, "error": "empty_query"}
    kw, exts = _parse_query(q)
    if not kw and not exts:
        return {"success": False, "error": "empty_query"}

    conn = _connect_ro()
    if conn is None:
        return dict(success=False, **_indexer_unavailable())
    try:
        try:
            conn.execute("SELECT 1 FROM files LIMIT 1").fetchone()
        except sqlite3.Exception:
            return dict(success=False, **_indexer_unavailable())

        where, args = ["1=1"], []
        if kw:
            where.append("name LIKE ? ESCAPE '\\'")
            args.append("%" + _like_escape(kw) + "%")
        if exts:   # 多选语义=OR（组内 OR：ext:a;b → 以 .a 或 .b 结尾；组间与 kw AND）
            where.append("(" + " OR ".join(["name LIKE ? ESCAPE '\\'"] * len(exts)) + ")")
            args.extend("%." + _like_escape(e) for e in exts)

        # 修改时间区间过滤（mtime_from/mtime_to 为规范参数名；date_from/date_to 兼容别名。
        # 均接受 YYYY-MM-DD 或时间戳；日期串止端按 23:59:59 对齐天边界，闭区间）
        date_from = _parse_date_param(params.get("mtime_from") or params.get("date_from"))
        date_to = _parse_date_param(params.get("mtime_to") or params.get("date_to"), end_of_day=True)
        if date_from is not None:
            where.append("mtime >= ?")
            args.append(date_from)
        if date_to is not None:
            where.append("mtime <= ?")
            args.append(date_to)

        cond = " AND ".join(where)

        sort = str(params.get("sort") or "").strip()
        order_col = FS_SORTABLE.get(sort)
        asc = str(params.get("ascending") or "").strip() != "0"
        if not order_col:   # 默认：名称升序
            order_col, asc, sort = FS_SORTABLE["name"], True, "name"
        if sort == "ext":
            # ext 排序语义（2026-09-16 定案）：目录固定排最前（不随正倒序翻转，组内按名称稳定序）；
            # 文件按扩展名字典序（无扩展名文件的扩展名段=文件名整体，自然参与字典序），
            # 同扩展名按名称稳定序。服务端全量排序，前端不再只排当页 200 条。
            order = (" ORDER BY is_dir DESC, " + order_col + (" ASC" if asc else " DESC")
                     + ", name COLLATE NOCASE ASC")
        else:
            order = " ORDER BY " + order_col + (" ASC" if asc else " DESC")

        limit = FS_MAX_RESULTS
        try:
            limit = max(10, min(FS_MAX_RESULTS, int(params.get("count") or FS_MAX_RESULTS)))
        except (TypeError, ValueError):
            limit = FS_MAX_RESULTS

        total = conn.execute("SELECT COUNT(*) FROM files WHERE " + cond, args).fetchone()[0]
        rows = conn.execute("SELECT name, path, is_dir, size, mtime FROM files WHERE "
                            + cond + order + " LIMIT %d" % limit, args).fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    results = []
    for name, path, is_dir, size, mtime in rows:
        p = path or ""
        if not is_dir and p.endswith("\\" + name):
            # 文件行：物化 path 为完整路径 → 归一为所在目录（前端契约 path=目录列）
            p = p[: -(len(name) + 1)]
        if is_dir:
            # 目录行：物化 path 为自身（含尾反斜杠）→ 归一为父目录（「打开位置」定位其父）
            suffix = "\\" + name + "\\"
            p = p[: -len(suffix)] if p.endswith(suffix) else p.rstrip("\\")
            idx = p.rfind("\\")
            p = (p[: idx + 1] if idx >= 0 else p + "\\")   # 根目录下 → "C:\"
        results.append({"name": name, "path": p, "is_dir": bool(is_dir), "size": size, "date_modified": mtime})
    return {"success": True, "q": q, "total": total, "count": len(results), "results": results}


def handle_fs_status(params=None):
    """状态：索引库就绪情况 + 索引器部署三态判定（4.1.7）。
    state：ready=库有数据；building=计划任务已注册但库未就绪（首建分钟级进行中）；
    not_deployed=任务未注册。partial 卷如实透出。"""
    path = _fs_db_path()
    out = {"success": True, "db_exists": os.path.isfile(path), "db_path": path,
           "file_count": 0, "dir_count": 0, "volumes": [], "partial_volumes": []}
    conn = _connect_ro()
    if conn is not None:
        try:
            out["file_count"] = conn.execute("SELECT COUNT(*) FROM files WHERE is_dir=0").fetchone()[0]
            out["dir_count"] = conn.execute("SELECT COUNT(*) FROM files WHERE is_dir=1").fetchone()[0]
            st = conn.execute("SELECT DISTINCT volume FROM usn_state").fetchall()
            out["volumes"] = [r[0] + ":" for r in st]
            out["partial_volumes"] = [r[0] + ":" for r in
                                      conn.execute("SELECT volume FROM usn_state WHERE partial=1").fetchall()]
        except sqlite3.Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
    out["indexer_task_registered"] = _schtasks_task_registered()
    if out["file_count"] > 0:
        out["state"], out["hint"] = "ready", ""
    elif out["indexer_task_registered"]:
        out["state"] = "building"
        out["hint"] = "索引构建中（首次需数分钟），请稍后重试"
    else:
        out["state"] = "not_deployed"
        out["hint"] = "检索索引未部署：请重新安装客户端或由管理员部署"
    return out


def handle_fs_open_location(params=None):
    """打开位置：explorer /select（与 /api/disk/open-location 同模式）。"""
    path = str((params or {}).get("path") or "").strip()
    if not path or not os.path.exists(path):
        return {"success": False, "error": "path_not_found"}
    try:
        subprocess.Popen(["explorer", "/select,", path], creationflags=_NO_WINDOW)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_fs_stats(params=None):
    """统计：返回索引库中存在的文件扩展名（前 30）与修改时间区间。"""
    conn = _connect_ro()
    if conn is None:
        return dict(success=False, **_indexer_unavailable())
    try:
        conn.execute("SELECT 1 FROM files LIMIT 1").fetchone()
        rows = conn.execute(
            "SELECT LOWER(SUBSTR(name, INSTR(name, '.') + 1)) AS ext, COUNT(*) AS c "
            "FROM files WHERE is_dir=0 AND name LIKE '%.%' "
            "GROUP BY ext ORDER BY c DESC LIMIT 30"
        ).fetchall()
        exts = [{"ext": r[0], "count": r[1]} for r in rows]
        r = conn.execute("SELECT MIN(mtime), MAX(mtime) FROM files WHERE is_dir=0").fetchall()
        return {"success": True, "exts": exts, "date_min": r[0][0], "date_max": r[0][1]}
    except sqlite3.Exception:
        return dict(success=False, **_indexer_unavailable())
    finally:
        try:
            conn.close()
        except Exception:
            pass


FS_ROUTES = {
    "/api/filesearch/query": handle_fs_query,
    "/api/filesearch/status": handle_fs_status,
    "/api/filesearch/stats": handle_fs_stats,
    "/api/filesearch/open-location": handle_fs_open_location,
    "/api/filesearch/indexer-deploy": handle_fs_indexer_deploy,
}


if __name__ == "__main__":
    import json
    print(json.dumps(handle_fs_status({}), ensure_ascii=False, indent=2))
