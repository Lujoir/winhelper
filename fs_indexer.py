# -*- coding: utf-8 -*-
r"""
fs_indexer.py — EyeTerm「文件检索」自研轻量索引器（路线 C，2026-09-15）
=======================================================================

定位：SYSTEM 权限常驻进程（P2 经计划任务 schtasks /RU SYSTEM 注册），独立于终端运行。
机制（Windows 公开 API，ctypes 零第三方依赖）：
  1. 首建：FSCTL_ENUM_USN_DATA 一次 IOCTL 枚举各 NTFS 卷全量 MFT 文件记录
     （文件名/父 FRN/属性，纯内核态顺序读，研究文档实测百万文件秒级）
  2. 增量：FSCTL_READ_USN_JOURNAL + 每卷 NextUsn 持久化（1s 轮询阻塞消费循环，秒级实时）
  3. 存储：sqlite ProgramData\EyeTerm\filesearch\index.db（表 files + usn_state），
     path 列写入时物化（父链重建），size/mtime 经 FindFirstFileW 补齐
  4. 非 NTFS 卷（exFAT/FAT32）不在 USN 体系 → 跳过并记录

运行模式：
  python fs_indexer.py --fs-indexer-worker  常驻 worker（4.1.7 A/B 方案共同前置）：
                                            单实例互斥 + 缺卷首建 → 1s 增量消费循环，
                                            日志落盘；计划任务 ONSTART / 安装器 / 客户端
                                            引导注册拉起
  python fs_indexer.py --once               首建（缺卷）+ 各卷消费一轮后退出（测试/快照模式）
  python fs_indexer.py --status

权限：读 MFT/USN 需管理员（或 SYSTEM）——Windows 安全边界，非管理员启动时如实报错退出。
"""

import ctypes
import json
import os
import sqlite3
import sys
import time
from ctypes import wintypes

# 计划任务名（4.1.7 部署机制 A 安装器注册 / B 客户端引导注册共用）
INDEXER_TASK_NAME = "EyeTermFileIndexer"

# ============================================================
# Win32 常量与结构（winioctl.h）
# ============================================================

GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
FILE_SHARE_DELETE = 0x4
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value or -1
ERROR_HANDLE_EOF = 38

# USN journal 失效族（winioctl.h ERROR_JOURNAL_*）——命中即重置重建（ADR-006 自愈）
ERROR_JOURNAL_DELETE_IN_PROGRESS = 1178
ERROR_JOURNAL_ENTRY_DELETED = 1179
ERROR_JOURNAL_NOT_ACTIVE = 1180
ERROR_JOURNAL_DELETED = 1181
_JOURNAL_DEAD_ERRS = frozenset((1178, 1179, 1180, 1181))

_last_ioctl_error = 0   # 最近一次 DeviceIoControl 失败码（诊断透出）

FSCTL_ENUM_USN_DATA = 0x000900B3
FSCTL_READ_USN_JOURNAL = 0x000900BB
FSCTL_QUERY_USN_JOURNAL = 0x000900F4

FILE_ATTRIBUTE_DIRECTORY = 0x10

# USN reason 掩码（消费关注项）
USN_REASON_DATA_OVERWRITE = 0x00000001
USN_REASON_DATA_EXTEND = 0x00000002
USN_REASON_BASIC_INFO_CHANGE = 0x00008000
USN_REASON_FILE_CREATE = 0x00000100
USN_REASON_FILE_DELETE = 0x00000200
USN_REASON_RENAME_OLD_NAME = 0x00002000
USN_REASON_RENAME_NEW_NAME = 0x00004000
USN_CONSUME_MASK = (USN_REASON_DATA_OVERWRITE | USN_REASON_DATA_EXTEND
                    | USN_REASON_BASIC_INFO_CHANGE | USN_REASON_FILE_CREATE
                    | USN_REASON_FILE_DELETE | USN_REASON_RENAME_OLD_NAME
                    | USN_REASON_RENAME_NEW_NAME)


class MFT_ENUM_DATA_V0(ctypes.Structure):
    _fields_ = [("StartFileReferenceNumber", ctypes.c_uint64),
                ("LowUsn", ctypes.c_uint64),
                ("HighUsn", ctypes.c_uint64)]


class MFT_ENUM_DATA_V1(ctypes.Structure):
    """V1（40 字节，多版本字段）——FSCTL_ENUM_USN_DATA 同一控制码按 InputBufferLength
    区分 V0/V1 输入（官方文档允许双结构）；V0 遭遇 INVALID_FUNCTION 时自动切换重试。"""
    _fields_ = [("StartFileReferenceNumber", ctypes.c_uint64),
                ("LowUsn", ctypes.c_uint64),
                ("HighUsn", ctypes.c_uint64),
                ("MinDataVersion", wintypes.DWORD),
                ("MaxDataVersion", wintypes.DWORD)]


class USN_JOURNAL_DATA(ctypes.Structure):
    _fields_ = [("UsnJournalID", ctypes.c_uint64),
                ("FirstUsn", ctypes.c_uint64),
                ("NextUsn", ctypes.c_uint64),
                ("LowestValidUsn", ctypes.c_uint64),
                ("MaxUsn", ctypes.c_uint64),
                ("MaximumSize", ctypes.c_uint64),
                ("AllocationDelta", ctypes.c_uint64)]


class READ_USN_JOURNAL_DATA_V0(ctypes.Structure):
    """winioctl.h 对照：ReturnOnlyOnClose 是 BYTE(1)，Timeout/BytesToWaitFor 是
    DWORDLONG(8)——此前全按 DWORD 定义导致结构体 32 字节（NTFS 要求 40），
    FSCTL_READ_USN_JOURNAL 恒返回 ERROR_INVALID_USER_BUFFER(1784)。"""
    _fields_ = [("StartUsn", ctypes.c_uint64),
                ("ReasonMask", wintypes.DWORD),
                ("ReturnOnlyOnClose", wintypes.BYTE),
                ("Timeout", ctypes.c_uint64),
                ("BytesToWaitFor", ctypes.c_uint64),
                ("UsnJournalID", ctypes.c_uint64)]


assert ctypes.sizeof(READ_USN_JOURNAL_DATA_V0) == 40, "READ_USN_JOURNAL_DATA_V0 布局错误"


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def _open_volume(letter):
    r"""打开卷句柄 \\.\X:（GENERIC_READ——实测 QUERY_USN_JOURNAL 成功配置；
    0 访问句柄上 FSCTL_QUERY_USN_JOURNAL 返回 ERROR_INVALID_FUNCTION(1)，2026-09-15 重测实证）。
    读 MFT/USN 仍需管理员令牌。"""
    return _kernel32.CreateFileW("\\\\.\\%s:" % letter, GENERIC_READ,
                                 FILE_SHARE_READ | FILE_SHARE_WRITE,
                                 None, OPEN_EXISTING, 0, None)


def _device_ioctl(h, code, in_buf, out_size):
    """DeviceIoControl 包装；返回 (ok, bytes_buffer)。失败码记入 _last_ioctl_error。"""
    global _last_ioctl_error
    buf = ctypes.create_string_buffer(out_size)
    ret = wintypes.DWORD(0)
    ok = _kernel32.DeviceIoControl(h, code, in_buf, ctypes.sizeof(in_buf) if in_buf is not None else 0,
                                   buf, out_size, ctypes.byref(ret), None)
    if not ok:
        _last_ioctl_error = ctypes.get_last_error()
    return bool(ok), buf.raw[:ret.value]


def _filetime_to_epoch(ft):
    return int(ft / 10 ** 7 - 11644473600)


def _stat_file(path):
    """FindFirstFileW 取 size/mtime（USN 记录不含这两个字段）。"""
    class WIN32_FIND_DATAW(ctypes.Structure):
        _fields_ = [("dwFileAttributes", wintypes.DWORD),
                    ("ftCreationTime", wintypes.FILETIME),
                    ("ftLastAccessTime", wintypes.FILETIME),
                    ("ftLastWriteTime", wintypes.FILETIME),
                    ("nFileSizeHigh", wintypes.DWORD),
                    ("nFileSizeLow", wintypes.DWORD),
                    ("dwReserved0", wintypes.DWORD),
                    ("dwReserved1", wintypes.DWORD),
                    ("cFileName", wintypes.WCHAR * 260),
                    ("cAlternateFileName", wintypes.WCHAR * 14)]

    fd = WIN32_FIND_DATAW()
    h = _kernel32.FindFirstFileW(path, ctypes.byref(fd))
    if h == INVALID_HANDLE_VALUE or h == -1:
        return None, None
    _kernel32.FindClose(h)
    size = (fd.nFileSizeHigh << 32) | fd.nFileSizeLow
    return size, _filetime_to_epoch(fd.ftLastWriteTime.dwHighDateTime << 32 | fd.ftLastWriteTime.dwLowDateTime)


_FRN_MASK = 0x0000FFFFFFFFFFFF   # 低 48 位 = MFT 记录号；高 16 位为 sequence number——
# 子记录保存的父引用 seq 与父记录自身枚举时的 seq 可能不同步（父目录曾删除重建），
# 全链路 FRN 键必须统一低 48 位，否则物化父链全部 parent_not_found（2026-09-15 铁证实锤）。


def _parse_record_at(raw, off, n):
    """解析单条 USN_RECORD（按记录头 MajorVersion 分派：V2=64 位 FRN / V3=128 位 FRN）。
    FRN 键统一低 48 位（_FRN_MASK）。返回 (rec|None, rec_len, major)。"""
    if off + 8 > n:
        return None, 0, 0
    rec_len = int.from_bytes(raw[off:off + 4], "little")
    major = int.from_bytes(raw[off + 4:off + 6], "little")
    minor = int.from_bytes(raw[off + 6:off + 8], "little")
    if rec_len < 8 or off + rec_len > n:
        return None, rec_len, major   # 截断信号
    if major == 3:
        # USN_RECORD_V3：FILE_ID_128（128 位）文件/父引用——取低 64 位再掩低 48 位
        frn = int.from_bytes(raw[off + 8:off + 16], "little") & _FRN_MASK
        parent = int.from_bytes(raw[off + 24:off + 32], "little") & _FRN_MASK
        attrs = int.from_bytes(raw[off + 68:off + 72], "little")
        name_len = int.from_bytes(raw[off + 72:off + 74], "little")
        name_off = int.from_bytes(raw[off + 74:off + 76], "little")
    else:   # V2（64 位布局）
        frn = int.from_bytes(raw[off + 8:off + 16], "little") & _FRN_MASK
        parent = int.from_bytes(raw[off + 16:off + 24], "little") & _FRN_MASK
        attrs = int.from_bytes(raw[off + 52:off + 56], "little")
        name_len = int.from_bytes(raw[off + 56:off + 58], "little")
        name_off = int.from_bytes(raw[off + 58:off + 60], "little")
    name = ""
    if 0 < name_len < rec_len and 0 < name_off < rec_len and name_off + name_len <= rec_len:
        name = raw[off + name_off:off + name_off + name_len].decode("utf-16-le", errors="replace")
    rec = {"frn": frn, "parent": parent, "is_dir": bool(attrs & FILE_ATTRIBUTE_DIRECTORY),
           "name": name, "major": major, "minor": minor}
    return rec, rec_len, major


def parse_usn_records(raw):
    """解析 READ_USN_JOURNAL 记录流（跳过头部 8 字节 NextUsn；V2/V3 逐记录分派）。"""
    out = []
    off = 8
    n = len(raw)
    while off + 8 <= n:
        rec, rec_len, _major = _parse_record_at(raw, off, n)
        if rec is None:
            break
        reason = int.from_bytes(raw[off + 40:off + 44], "little") if rec.get("major") != 3 \
            else int.from_bytes(raw[off + 56:off + 60], "little")
        rec["reason"] = reason
        out.append(rec)
        off += rec_len
    return out


def parse_enum_records(raw):
    """解析 ENUM_USN_DATA 输出（头 8 字节 next_frn + 记录流；V2/V3 逐记录分派）。
    返回 (records, next_frn, safe_frn, truncated, diag)：截断时 safe_frn=最后完整记录 FRN
    （调用方回退重枚举该条，REPLACE 幂等）。diag 含首记录 Major/Minor/rec_len/FRN 采样。"""
    n = len(raw)
    if n < 8:
        return [], 0, 0, False, None
    next_frn = int.from_bytes(raw[0:8], "little")
    out = []
    off = 8
    truncated = False
    safe_frn = 0
    first_diag = None
    while off + 8 <= n:
        rec, rec_len, major = _parse_record_at(raw, off, n)
        if first_diag is None:
            first_diag = {"major": major, "rec_len": rec_len,
                          "frn": (rec or {}).get("frn") if rec else None,
                          "hex": raw[off:off + 60].hex() if off < n else ""}
        if rec is None:
            truncated = True   # 尾部截断：弃用系统 next_frn，回退 safe_frn 重枚举
            break
        if rec["name"]:
            out.append(rec)
        safe_frn = rec["frn"]
        off += rec_len
    if not truncated:
        safe_frn = next_frn
    return out, next_frn, safe_frn, truncated, first_diag


# ============================================================
# 存储
# ============================================================

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    volume TEXT NOT NULL,
    frn INTEGER NOT NULL,
    parent_frn INTEGER,
    name TEXT NOT NULL,
    path TEXT,
    is_dir INTEGER DEFAULT 0,
    size INTEGER,
    mtime INTEGER,
    PRIMARY KEY (volume, frn)
);
CREATE INDEX IF NOT EXISTS idx_files_name ON files(name);
CREATE INDEX IF NOT EXISTS idx_files_parent ON files(volume, parent_frn);
CREATE TABLE IF NOT EXISTS usn_state (
    volume TEXT PRIMARY KEY,
    journal_id INTEGER,
    next_usn INTEGER,
    partial INTEGER DEFAULT 0
);
"""


def _migrate(conn):
    """旧库兼容：usn_state 无 partial 列时补列（2026-09-15 partial 语义新增）。"""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(usn_state)").fetchall()]
        if "partial" not in cols:
            conn.execute("ALTER TABLE usn_state ADD COLUMN partial INTEGER DEFAULT 0")
            conn.commit()
    except Exception:
        pass


def db_path():
    base = os.environ.get("FS_INDEX_DB") or os.path.join(
        os.environ.get("ProgramData") or r"C:\ProgramData", "EyeTerm", "filesearch")
    d = base if base.lower().endswith(".db") else os.path.join(base, "index.db")
    os.makedirs(os.path.dirname(d), exist_ok=True)
    return d


def _log(msg):
    """轻量双写日志（4.1.7 worker 证据链）：控制台 + 索引目录 indexer.log 追加；
    落盘失败静默（日志不可用不阻断索引职责）。"""
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        d = os.path.dirname(db_path())
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "indexer.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _acquire_single_instance_lock():
    """命名互斥单实例保护（Global\\EyeTermFileIndexer，4.1.7）：
    创建成功且非已存在 → True（句柄进程存活期持有即持锁）；已存在/失败 → False。"""
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ERROR_ALREADY_EXISTS = 183
        h = k32.CreateMutexW(None, False, "Global\\EyeTermFileIndexer")
        if not h:
            return False
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            k32.CloseHandle(h)
            return False
        return True
    except Exception:
        return False   # 互斥不可用时宁可退出（防双实例写库），不冒险继续


def _connect():
    conn = sqlite3.connect(db_path(), timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def discover_volumes():
    """枚举 A..Z 盘符：驱动器类型 + 文件系统一次性看清（GetDriveTypeW/GetVolumeInformationW）。
    索引白名单 = 固定盘(DRIVE_FIXED=3) 且 NTFS；其余如实记录跳过原因（exFAT 无 MFT 枚举、
    可移动/网络/虚拟卷行为不可靠——2026-09-15 D/F/G 零记录与 S: 幽灵卷铁证后收紧）。"""
    out, skipped = [], []
    for i in range(65, 91):
        letter = chr(i)
        root = "%s:\\" % letter
        if not os.path.exists(root):
            continue
        dtype = _kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
        fs_name = ctypes.create_unicode_buffer(64)
        ok = _kernel32.GetVolumeInformationW(ctypes.c_wchar_p(root), None, 0, None, None, None, fs_name, 64)
        fs = (fs_name.value or "").upper() if ok else "UNKNOWN"
        info = {"volume": letter + ":", "drive_type": dtype, "fs": fs}
        if dtype != 3:
            info["skip"] = "not_fixed_drive"
            skipped.append(info)
        elif not fs.startswith("NTFS"):
            info["skip"] = "not_ntfs"
            skipped.append(info)
        else:
            out.append(letter)
    return out, skipped


def ntfs_volumes():
    """兼容旧调用：返回 (letters, skipped)。"""
    letters, skipped = discover_volumes()
    return letters, skipped


def _materialize_paths(conn, letter, recs):
    """父链物化 path（首建：全卷记录在内存，一遍记忆化）。
    返回 (rows, drops)——drops 为丢弃原因直方图（parent_not_found/name_invalid/
    self_parent/depth_exceeded），随卷 report 上报定案。"""
    root_frn = 5
    paths = {root_frn: letter + ":\\"}
    rows = []
    drops = {"parent_not_found": 0, "name_invalid": 0, "self_parent": 0, "depth_exceeded": 0}
    pending = []
    for r in recs:
        name = r.get("name") or ""
        if not name or "\\" in name or "/" in name:
            drops["name_invalid"] += 1
            continue
        if r["frn"] == r["parent"]:
            drops["self_parent"] += 1
            continue
        pending.append(r)
    for _pass in range(256):   # 父链深度保护
        stuck = []
        for r in pending:
            p = paths.get(r["parent"])
            if p is None:
                stuck.append(r)
                continue
            full = p + r["name"] + ("\\" if r["is_dir"] else "")
            paths[r["frn"]] = full
            rows.append((letter, r["frn"], r["parent"], r["name"], full,
                         1 if r["is_dir"] else 0))
        pending = stuck
        if not pending:
            break
    for _r in pending:
        drops["parent_not_found"] += 1   # 父链断裂（MFT 元文件/深度超限孤儿等）不入库
    if pending:
        drops["depth_exceeded"] = 0      # 深度保护未触发；剩余一律归 parent_not_found
    return rows, drops


def _persist_volume(conn, letter, recs, drops_out=None):
    """去重 → 父链物化 → 入库 → size/mtime 补齐。返回入库行数（int 契约稳定，
    物化 drop 直方图经 drops_out 字典带回——2026-09-15 热修教训：返回契约不破坏）。"""
    if recs:
        dedup = {}
        for r in recs:
            dedup[r["frn"]] = r
        recs = list(dedup.values())
    conn.execute("DELETE FROM files WHERE volume=?", (letter,))
    rows, drops = _materialize_paths(conn, letter, recs)
    if drops_out is not None:
        drops_out.update(drops)
    conn.executemany("INSERT OR REPLACE INTO files(volume,frn,parent_frn,name,path,is_dir) "
                     "VALUES(?,?,?,?,?,?)", rows)
    conn.commit()
    upd = []
    if rows:
        cur = conn.execute("SELECT frn, path FROM files WHERE volume=? AND is_dir=0 AND size IS NULL", (letter,))
        todo = cur.fetchall()
        for frn, path in todo:
            size, mtime = _stat_file(path)
            if size is not None:
                upd.append((size, mtime, letter, frn))
        conn.executemany("UPDATE files SET size=?, mtime=? WHERE volume=? AND frn=?", upd)
        conn.commit()
    return len(rows)


def build_volume(conn, letter):
    """首建：ENUM_USN_DATA 全量枚举 → 物化入库 → FindFirstFile 补 size/mtime。
    2026-09-15 第二轮修复（实测铁证驱动）：
    ①硬错误（1392）时对已收记录做**部分成功入库**（partial，24.4 万条成果不弃；partial 不落
      usn_state——下次 run 重建该卷，索引覆盖语义诚实）；
    ②解析器 V2/V3 逐记录分派（假设 A：部分卷返回 128 位 FRN 的 V3 记录）；
    ③首轮「有数据但 0 解析」自动切 V1 输入结构重试（假设 B）；
    ④report 带采样诊断（bytes_returned/首记录 Major/Minor/rec_len/FRN/hex60）定案假设。"""
    h = _open_volume(letter)
    if h == INVALID_HANDLE_VALUE or h == -1:
        return {"ok": False, "error": "open_volume_failed", "err": ctypes.get_last_error()}
    qok, jq = _device_ioctl(h, FSCTL_QUERY_USN_JOURNAL, None, ctypes.sizeof(USN_JOURNAL_DATA))
    if not qok:
        _kernel32.CloseHandle(h)
        return {"ok": False, "error": "query_journal_failed", "err": _last_ioctl_error}
    j = USN_JOURNAL_DATA.from_buffer_copy(jq)
    journal_id, next_usn = j.UsnJournalID, j.NextUsn

    recs = []
    err = 0
    rounds = 0
    truncated_rounds = 0
    parsed_total = 0
    drops = {}
    diag = {"samples": []}
    switched_v1 = False
    in_v0 = True
    m = MFT_ENUM_DATA_V0(0, 0, 0x7FFFFFFFFFFFFFFF)
    last_start = 0
    last_safe = 0
    hard_failed = False
    retry_1392_done = False
    skip_1392 = 0            # 1392 坏区段跳跃步长（指数退避，成功后复位）
    skipped_segs = 0         # 跳段次数（诊断透出）
    skipped_frn = 0          # 累计跳过 FRN 跨度（诊断透出）
    done = False
    for _round in range(20000):
        rounds = _round + 1
        ok, raw = _device_ioctl(h, FSCTL_ENUM_USN_DATA, m, 1 << 20)
        if not ok:
            err = _last_ioctl_error
            if err == ERROR_HANDLE_EOF:
                done = True
                break
            if err == 1 and in_v0 and not recs:
                in_v0 = False
                switched_v1 = True
                m = MFT_ENUM_DATA_V1(0, 0, 0x7FFFFFFFFFFFFFFF, 0, 2)
                last_start = 0
                continue
            if err == 1392 and last_start:
                # C 卷实测（2026-09-16）：MFT 坏区段使 ENUM 在固定位置稳定 1392（两次重建
                # 同一断点 parsed 均为 244,537）——先回退重试一轮，仍失败则指数跳跃跳段续扫
                # （业界标准做法：坏段内少数文件放弃，游标可落、增量恢复，卷不再 partial 死状态）。
                if not retry_1392_done:
                    retry_1392_done = True
                    m.StartFileReferenceNumber = last_safe
                    last_start = last_safe
                    continue
                skip_1392 = (skip_1392 * 2) if skip_1392 else 1
                if skip_1392 > (1 << 26):
                    hard_failed = True
                    break   # 跳跃上限（6700 万 FRN）仍失败才放弃走 partial
                nxt = last_start + skip_1392
                skipped_segs += 1
                skipped_frn += skip_1392
                m.StartFileReferenceNumber = nxt
                last_start = nxt
                continue
            hard_failed = True
            break   # 部分成功：已收记录走 partial 入库
        if len(raw) <= 8:
            done = True
            break
        batch, next_frn, safe_frn, truncated, first_diag = parse_enum_records(raw)
        parsed_total += len(batch)
        if batch or truncated:
            skip_1392 = 0   # 枚举恢复推进，步长复位
        if rounds <= 2 and first_diag:
            d = dict(first_diag)
            d["bytes"] = len(raw)
            diag["samples"].append(d)
        if not batch and not truncated:
            if rounds <= 1 and in_v0 and not switched_v1:
                switched_v1 = True
                m = MFT_ENUM_DATA_V1(0, 0, 0x7FFFFFFFFFFFFFFF, 0, 2)   # 假设 B：V1 输入结构
                last_start = 0
                continue
            done = True
            break
        recs.extend(batch)
        if truncated:
            truncated_rounds += 1
            if not safe_frn or safe_frn == last_start:
                done = True
                break   # 回退点无进展防死循环（已收记录保留）
            m.StartFileReferenceNumber = safe_frn
            last_start = safe_frn
            last_safe = safe_frn
            continue
        if not next_frn or next_frn == last_start:
            done = True
            break
        m.StartFileReferenceNumber = next_frn
        last_start = next_frn
        last_safe = next_frn
    _kernel32.CloseHandle(h)

    drops = {}
    rows = _persist_volume(conn, letter, recs, drops_out=drops)   # 部分成功语义：硬错误也入库已收记录
    if rows:
        if hard_failed:
            # partial 卷：游标不落（下次 run 重建重试），但状态行落地供 UI 如实展示「部分索引」
            conn.execute("INSERT OR REPLACE INTO usn_state(volume,journal_id,next_usn,partial) "
                         "VALUES(?,?,NULL,1)", (letter, journal_id))
        else:
            conn.execute("INSERT OR REPLACE INTO usn_state(volume,journal_id,next_usn,partial) "
                         "VALUES(?,?,?,0)", (letter, journal_id, next_usn))
        conn.commit()
    base = {"records": rows, "parsed": parsed_total, "drops": drops,
            "rounds": rounds, "truncated_rounds": truncated_rounds,
            "skipped_segments": skipped_segs, "skipped_frn": skipped_frn,
            "diag": diag, "switched_v1": switched_v1}
    if hard_failed:
        return dict(base, ok=False, partial=True, error="enum_failed", err=err,
                    variant="V0" if in_v0 else "V1")
    return dict(base, ok=True, empty=not rows)


def _reset_volume_state(conn, letter, reason):
    """journal 失效自愈（ADR-006）：清该卷游标与全部索引行（半新半旧不可留），
    由调用方当场重建。journal 失效重建是标准恢复路径（非异常），INFO 级留痕。"""
    conn.execute("DELETE FROM usn_state WHERE volume=?", (letter,))
    conn.execute("DELETE FROM files WHERE volume=?", (letter,))
    conn.commit()
    print("[fs_indexer] 日志已重置，重建索引（卷 %s:）（%s）" % (letter, reason), flush=True)


def consume_volume(conn, letter, max_batches=64):
    """增量消费一轮：READ_USN_JOURNAL 到当前尾；返回处理条数。
    partial 卷（游标未落）静默跳过（设计内常态，2026-09-15 派修②）。
    journal 失效族（1178/1179/1180/1181 或 journal_id 变化）→ 自动重置该卷并
    返回 rebuild 信号（ADR-006），调用方当场重建，单轮完成自愈闭环。"""
    row = conn.execute("SELECT journal_id, next_usn, partial FROM usn_state WHERE volume=?", (letter,)).fetchone()
    if not row:
        return {"ok": False, "error": "no_state"}
    journal_id, next_usn, partial = (list(row) + [0])[:3]
    if partial or next_usn is None:
        return {"ok": True, "applied": 0, "skipped": "partial"}
    h = _open_volume(letter)
    if h == INVALID_HANDLE_VALUE or h == -1:
        return {"ok": False, "error": "open_volume_failed"}
    qok, jq = _device_ioctl(h, FSCTL_QUERY_USN_JOURNAL, None, ctypes.sizeof(USN_JOURNAL_DATA))
    if not qok:
        err = _last_ioctl_error
        _kernel32.CloseHandle(h)
        if err in _JOURNAL_DEAD_ERRS:
            _reset_volume_state(conn, letter, "query_journal err=%d" % err)
            return {"ok": False, "error": "journal_reset", "rebuild": True, "src": "query", "err": err}
        return {"ok": False, "error": "journal_invalid", "err": err}   # 需重建
    j = USN_JOURNAL_DATA.from_buffer_copy(jq)
    if j.UsnJournalID != journal_id:
        _kernel32.CloseHandle(h)
        _reset_volume_state(conn, letter, "journal_id_changed %s->%s" % (journal_id, j.UsnJournalID))
        return {"ok": False, "error": "journal_reset", "rebuild": True, "src": "id_changed"}

    total = 0
    rd = READ_USN_JOURNAL_DATA_V0(next_usn, USN_CONSUME_MASK, 0, 0, 0, journal_id)
    for _ in range(max_batches):
        ok, raw = _device_ioctl(h, FSCTL_READ_USN_JOURNAL, rd, 1 << 20)
        if not ok:
            err = _last_ioctl_error
            _kernel32.CloseHandle(h)
            if err == ERROR_HANDLE_EOF:
                break   # 已消费到 journal 尾（正常）
            if err in _JOURNAL_DEAD_ERRS:
                _reset_volume_state(conn, letter, "read_journal err=%d" % err)
                return {"ok": False, "error": "journal_reset", "rebuild": True, "src": "read", "err": err}
            return {"ok": False, "error": "read_journal_failed", "err": err}
        if len(raw) <= 8:
            break   # 无新记录
        recs = parse_usn_records(raw)
        if not recs:
            break
        total += _apply_batch(conn, letter, recs)
        rd.StartUsn = int.from_bytes(raw[0:8], "little")
        if rd.StartUsn <= next_usn:
            break
        next_usn = rd.StartUsn
    _kernel32.CloseHandle(h)
    if total:
        conn.execute("UPDATE usn_state SET next_usn=? WHERE volume=?", (next_usn, letter))
        conn.commit()
    return {"ok": True, "applied": total}


def _apply_batch(conn, letter, recs):
    """一批 USN 记录：按 frn 聚合 reason（RENAME_OLD+NEW 同 frn 合并为改名）。"""
    agg = {}
    order = []
    for r in recs:
        k = r["frn"]
        if k not in agg:
            agg[k] = {"reasons": 0, "rec": r}
            order.append(k)
        agg[k]["reasons"] |= r["reason"]
        if r["reason"] & (USN_REASON_FILE_CREATE | USN_REASON_RENAME_NEW_NAME):
            agg[k]["rec"] = r   # 新名/新记录为准
    applied = 0
    for frn in order:
        a = agg[frn]
        reasons = a["reasons"]
        rec = a["rec"]
        is_new = reasons & (USN_REASON_FILE_CREATE | USN_REASON_RENAME_NEW_NAME)
        is_gone = reasons & (USN_REASON_FILE_DELETE | USN_REASON_RENAME_OLD_NAME)
        if is_new:
            parent_path = _parent_path(conn, letter, rec["parent"])
            if parent_path is None:
                continue   # 父未索引（罕见，等待下轮）
            full = parent_path + rec["name"] + ("\\" if rec["is_dir"] else "")
            size, mtime = (None, None)
            if not rec["is_dir"]:
                size, mtime = _stat_file(full)
            conn.execute("INSERT OR REPLACE INTO files(volume,frn,parent_frn,name,path,is_dir,size,mtime) "
                         "VALUES(?,?,?,?,?,?,?,?)",
                         (letter, frn, rec["parent"], rec["name"], full,
                          1 if rec["is_dir"] else 0, size, mtime))
            applied += 1
            if rec["is_dir"]:
                _refresh_subtree_paths(conn, letter, frn, full)
        elif is_gone:
            applied += _delete_subtree(conn, letter, frn)
        elif reasons & (USN_REASON_DATA_OVERWRITE | USN_REASON_DATA_EXTEND | USN_REASON_BASIC_INFO_CHANGE):
            row = conn.execute("SELECT path FROM files WHERE volume=? AND frn=?", (letter, frn)).fetchone()
            if row and row[0] and not os.path.isdir(row[0]):
                size, mtime = _stat_file(row[0])
                if size is not None:
                    conn.execute("UPDATE files SET size=?, mtime=? WHERE volume=? AND frn=?",
                                 (size, mtime, letter, frn))
                    applied += 1
    return applied


def _parent_path(conn, letter, parent_frn):
    if parent_frn == 5:
        return letter + ":\\"
    row = conn.execute("SELECT path FROM files WHERE volume=? AND frn=?", (letter, parent_frn)).fetchone()
    if row and row[0]:
        p = row[0]
        return p if p.endswith("\\") else p + "\\"
    if parent_frn == 5:
        return letter + ":\\"
    return None


def _refresh_subtree_paths(conn, letter, dir_frn, dir_path):
    """目录新建/改名后刷新子树 path（物化列连带更新；增量场景子树通常为空或小）。"""
    frontier = [(dir_frn, dir_path)]
    for _ in range(32):   # 深度保护
        if not frontier:
            break
        nxt = []
        for frn, base in frontier:
            b = base if base.endswith("\\") else base + "\\"
            rows = conn.execute("SELECT frn,name,is_dir FROM files WHERE volume=? AND parent_frn=?",
                                (letter, frn)).fetchall()
            for cfrn, cname, cis in rows:
                full = b + cname + ("\\" if cis else "")
                conn.execute("UPDATE files SET path=? WHERE volume=? AND frn=?", (full, letter, cfrn))
                if cis:
                    nxt.append((cfrn, full))


def _delete_subtree(conn, letter, frn):
    """删除文件/目录（目录递归删子孙）。"""
    ids = [frn]
    total = 0
    for _ in range(64):
        if not ids:
            break
        marks = ",".join("?" for _ in ids)
        conn.execute("DELETE FROM files WHERE volume=? AND frn IN (%s)" % marks, [letter] + ids)
        total += len(ids)
        rows = conn.execute("SELECT frn FROM files WHERE volume=? AND parent_frn IN (%s)" % marks,
                            [letter] + ids).fetchall()
        ids = [r[0] for r in rows]
    return total


# ============================================================
# 编排
# ============================================================

def run_once(conn):
    """首建缺卷 + 各卷消费一轮（--once / 测试快照）。
    journal 失效自愈（ADR-006）：consume 返回 rebuild=True 时当场重建（build_volume
    幂等，partial 语义保护）并再消费一轮——单轮完成自愈闭环。
    partial 卷（2026-09-16 修复）：硬失败分支落了 usn_state 行（next_usn=NULL,partial=1），
    重建判定必须要求游标有效（next_usn IS NOT NULL），否则 partial 卷成死状态永不重建
    （修复前 C 卷实证：--once 返回 skipped:partial，新建文件永不进索引）。"""
    vols, skipped = discover_volumes()
    report = {"volumes": [], "skipped": skipped}
    for letter in vols:
        has_state = conn.execute(
            "SELECT 1 FROM usn_state WHERE volume=? AND next_usn IS NOT NULL",
            (letter,)).fetchone()
        if not has_state:
            r = build_volume(conn, letter)
            report["volumes"].append({"volume": letter + ":", "mode": "build", **r})
            if not r.get("ok") or r.get("empty"):
                continue   # 失败或零记录（异常信号）不 consume，report 如实
        r = consume_volume(conn, letter)
        report["volumes"].append({"volume": letter + ":", "mode": "consume", **r})
        if r.get("rebuild"):
            rb = build_volume(conn, letter)
            report["volumes"].append({"volume": letter + ":", "mode": "journal_reset_rebuild", **rb})
            if rb.get("ok") and not rb.get("empty"):
                r2 = consume_volume(conn, letter)
                report["volumes"].append({"volume": letter + ":", "mode": "consume", **r2})
    return report


def run_worker():
    """常驻 worker（--fs-indexer-worker，4.1.7 A/B 方案共同前置）：
    单实例互斥 → 首建全量（分钟级）→ 1s USN 增量消费循环；日志落盘
    （启动/首建完成/异常）。崩溃自动退出由计划任务 ONSTART / 安装器 /
    客户端引导「立即运行」语义兜底重启。"""
    if not _require_admin():
        _log("worker 需要 admin/SYSTEM 权限（读 MFT/USN 安全边界），退出")
        sys.exit(2)
    if not _acquire_single_instance_lock():
        _log("另一索引器实例已在运行（命名互斥命中），退出")
        return
    _log("worker 启动，db=%s" % db_path())
    conn = _connect()
    conn.executescript(_SCHEMA)
    _migrate(conn)
    while True:
        try:
            report = run_once(conn)
            for v in report.get("volumes", []):
                if v.get("mode") == "build" and v.get("ok") and not v.get("empty"):
                    _log("首建完成（卷 %s）records=%s"
                         % (v.get("volume"), v.get("records")))
        except Exception as e:   # 常驻循环异常不退出（记日志，下轮重试）
            _log("loop error: %s" % e)
        time.sleep(1.0)


def run_daemon():
    """兼容旧入口（等价 run_worker）。"""
    run_worker()


def wait_until_indexed(conn, keyword, timeout=90.0, poll=1.0):
    """轮询等待关键词可检索（smoke/编排用）：sleep 间隔轮询 + 硬超时 graceful 返回。
    返回 (hit: bool, elapsed: float)——绝不热轮询打满 CPU（2026-09-15 卡死教训）。"""
    import search_service as ss   # 局部导入避免循环
    deadline = time.time() + timeout
    while True:
        r = ss.handle_fs_query({"q": keyword})
        if r.get("success") and r.get("count"):
            return True, time.time() - (deadline - timeout)
        if time.time() >= deadline:
            return False, timeout
        time.sleep(poll)


def status(conn):
    vols, skipped = discover_volumes()
    out = {"db": db_path(), "volumes": [v + ":" for v in vols], "skipped": skipped}
    try:
        out["file_count"] = conn.execute("SELECT COUNT(*) FROM files WHERE is_dir=0").fetchone()[0]
        out["dir_count"] = conn.execute("SELECT COUNT(*) FROM files WHERE is_dir=1").fetchone()[0]
        st = conn.execute("SELECT volume, next_usn, partial FROM usn_state").fetchall()
        out["usn_state"] = {r[0]: r[1] for r in st}
        out["partial_volumes"] = [r[0] + ":" for r in st if r[2]]
    except Exception as e:
        out["error"] = str(e)
    return out


def _require_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def main():
    if "--fs-indexer-worker" in sys.argv:
        run_worker()   # worker 自含权限检查（SYSTEM 计划任务/管理员拉起）
        return
    if not _require_admin():
        print("fs_indexer 需要管理员权限运行（读 MFT/USN 为 Windows 安全边界）。")
        sys.exit(2)
    conn = _connect()
    conn.executescript(_SCHEMA)
    _migrate(conn)
    if "--status" in sys.argv:
        print(json.dumps(status(conn), ensure_ascii=True, indent=2))
    elif "--once" in sys.argv:
        print(json.dumps(run_once(conn), ensure_ascii=True, indent=2))
    else:
        run_worker()


if __name__ == "__main__":
    main()
