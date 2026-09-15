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

import os
import re
import sqlite3
import subprocess

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

FS_MAX_RESULTS = 200
FS_SORTABLE = {"name": "name COLLATE NOCASE", "size": "size", "mtime": "mtime"}
_FS_EXT_RE = re.compile(r"(?:^|\s)ext:([A-Za-z0-9_;]+)", re.IGNORECASE)


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
    """拆 ext: 筛选段与关键词（Everything 原生语法前端拼接，服务端转为 sqlite 条件）。"""
    exts = []
    m = _FS_EXT_RE.search(q)
    if m:
        exts = [e for e in m.group(1).lower().split(";") if e]
        q = (q[:m.start()] + " " + q[m.end():])
    kw = q.strip()
    return kw, exts


def _like_escape(kw):
    return kw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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
        return {"success": False, "error": "indexer_not_running",
                "hint": "文件索引未就绪：索引器首次运行需要数分钟，请稍后重试"}
    try:
        try:
            conn.execute("SELECT 1 FROM files LIMIT 1").fetchone()
        except sqlite3.Exception:
            return {"success": False, "error": "indexer_not_running",
                    "hint": "文件索引未就绪：索引器首次运行需要数分钟，请稍后重试"}

        where, args = ["1=1"], []
        if kw:
            where.append("name LIKE ? ESCAPE '\\'")
            args.append("%" + _like_escape(kw) + "%")
        if exts:   # 多选语义=OR（组内 OR：ext:a;b → 以 .a 或 .b 结尾；组间与 kw AND）
            where.append("(" + " OR ".join(["name LIKE ? ESCAPE '\\'"] * len(exts)) + ")")
            args.extend("%." + _like_escape(e) for e in exts)
        cond = " AND ".join(where)

        sort = str(params.get("sort") or "").strip()
        order_col = FS_SORTABLE.get(sort)
        asc = str(params.get("ascending") or "").strip() != "0"
        if not order_col:   # 默认：名称升序（与 Everything 默认一致）
            order_col, asc = FS_SORTABLE["name"], True
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
        results.append({"name": name, "path": p, "size": size, "date_modified": mtime})
    return {"success": True, "q": q, "total": total, "count": len(results), "results": results}


def handle_fs_status(params=None):
    """状态：索引库就绪情况（设置卡/指引依据）。"""
    path = _fs_db_path()
    out = {"success": True, "db_exists": os.path.isfile(path), "db_path": path,
           "file_count": 0, "dir_count": 0, "volumes": []}
    conn = _connect_ro()
    if conn is not None:
        try:
            out["file_count"] = conn.execute("SELECT COUNT(*) FROM files WHERE is_dir=0").fetchone()[0]
            out["dir_count"] = conn.execute("SELECT COUNT(*) FROM files WHERE is_dir=1").fetchone()[0]
            out["volumes"] = [r[0] + ":" for r in
                              conn.execute("SELECT DISTINCT volume FROM usn_state").fetchall()]
        except sqlite3.Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
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


FS_ROUTES = {
    "/api/filesearch/query": handle_fs_query,
    "/api/filesearch/status": handle_fs_status,
    "/api/filesearch/open-location": handle_fs_open_location,
}


if __name__ == "__main__":
    import json
    print(json.dumps(handle_fs_status({}), ensure_ascii=False, indent=2))
