# -*- coding: utf-8 -*-
"""模块级记忆访问收口（P1-3）。

定位
----
`ai-agent/memory/README.md` 定义的**防串扰三道闸**中的第二道：

  ① 物理隔离：一个模块一个目录（`memory/<module_id>/`）
  ② **访问收口：本文件的 MemoryScope —— 越界即抛异常**
  ③ 命名空间：条目自带 `module` 字段，跨模块引用必须显式声明 `depends_on`

为什么第 ② 道必须存在
-------------------
只有物理隔离是不够的 —— 只要代码里出现过一次
`open(os.path.join(MEM_ROOT, other_module, "MEMORY.md"))`，
隔离就名存实亡。**收口的意义是让"越界"变成一件做不到的事，而不是一件别做的事**。

故本类的设计原则是：**越界抛异常，而不是返回空**。
返回空会让调用方以为"该模块没记忆"，静默产生错误结论；
抛异常则立刻暴露，且堆栈直指越界那一行。

从既有实现升级而来
----------------
本项目已有 `net_service.py` 的 `ai_history.jsonl`（读/写/追加/删除 + 原子写 + 200 条滚动），
是全项目最接近"记忆"的一处。本类把它**收口为规范形态**：
  · 仍是 JSONL 追加（崩溃安全 + 可回放）
  · 增加物理边界校验（原实现无）
  · 增加轮转能力（原实现只在追加时截断，无归档）

不做的事（边界）
--------------
  · 不做"记忆提炼"（把会话蒸馏为长期 MEMORY.md）—— 那是模型能力，不是存储层职责
  · 不碰网络、不碰凭据
  · 不改动任何既有调用路径（本期为纯新增；接入是单独一步）
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

# module_id 白名单式校验：只允许字母数字下划线连字符（防路径注入）
_MODULE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class MemoryScopeViolation(Exception):
    """越界访问：跨模块、路径逃逸、非法 module_id。"""


class MemoryScope(object):
    """限定在某模块记忆目录内的访问器。

    用法::

        scope = MemoryScope(memory_root, "netdoctor")
        scope.append_session({"ts": int(time.time()), "summary": "..."})
        scope.rotate(keep=200)          # 超阈值轮转，旧条目进 archive/
        state = scope.read_state()
    """

    def __init__(self, memory_root, module_id):
        if not _MODULE_ID_RE.match(str(module_id or "")):
            raise MemoryScopeViolation("非法 module_id: %r" % (module_id,))
        if not memory_root:
            raise MemoryScopeViolation("memory_root 不能为空")

        self.module_id = module_id
        # 基准目录必须 resolve（消除 .. 与符号链接），后续所有路径都以此为界
        self._root = Path(memory_root).expanduser()
        try:
            self._root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise MemoryScopeViolation("记忆根目录不可用: %s" % exc)
        self._root = self._root.resolve()
        self._dir_path = (self._root / module_id).resolve()

        # 边界自检：模块目录必须真在根之下（防 module_id 通过符号链接逃逸）
        if not self._under_root(self._dir_path):
            raise MemoryScopeViolation(
                "模块目录逃逸出记忆根: %s" % self._dir_path)

    # ---------------- 边界 ----------------
    def _under_root(self, path):
        try:
            path.relative_to(self._root)
            return True
        except ValueError:
            return False

    @property
    def dir(self):
        """本模块的记忆目录（唯一可写区域）。"""
        return self._dir_path

    # 允许写入的受限子目录（白名单，避免调用方自造层级）
    ALLOWED_SUBDIRS = ("archive",)

    def path(self, name, subdir=None):
        """取模块目录内的文件路径 —— 文件名不许含分隔符或 `..`。

        这是收口的实际执行点：任何想写别处模块的尝试都会在这里被拦下。
        `subdir` 用于 `archive/` 这类受限子目录，只接受白名单值
        （否则调用方可以借 subdir 绕出模块目录）。
        """
        name = str(name or "")
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            raise MemoryScopeViolation("非法文件名: %r" % (name,))
        base = self._dir_path
        if subdir is not None:
            if subdir not in self.ALLOWED_SUBDIRS:
                raise MemoryScopeViolation("非法子目录: %r" % (subdir,))
            base = (self._dir_path / subdir).resolve()
            if not self._under_root(base):
                raise MemoryScopeViolation("子目录逃逸: %s" % base)
        target = (base / name).resolve()
        if not self._under_root(target):
            raise MemoryScopeViolation("路径逃逸: %s" % target)
        return target

    def ensure_dirs(self):
        """建立规范目录（含 archive/）。"""
        self._dir_path.mkdir(parents=True, exist_ok=True)
        (self._dir_path / "archive").mkdir(parents=True, exist_ok=True)
        return self._dir_path

    # ---------------- SESSION（机写增量） ----------------
    SESSION_NAME = "SESSION.jsonl"

    def read_session(self, limit=200):
        """读会话条目，按 ts 倒序。坏行跳过（不因一行损坏丢全部）。"""
        p = self.path(self.SESSION_NAME)
        out = []
        if not p.exists():
            return out
        try:
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue          # 跳过坏行，保留其余
                    if isinstance(rec, dict) and rec.get("ts"):
                        out.append(rec)
        except OSError:
            return out
        out.sort(key=lambda r: r.get("ts") or 0, reverse=True)
        return out[:limit] if limit else out

    def append_session(self, record):
        """追加一条会话记录（JSONL 追加 = 崩溃安全 + 可回放）。

        写入时强制补 `module` 字段 —— 这是防串扰第 ③ 道闸（命名空间）的落实点：
        条目自带来源，事后混入也能分辨归属。
        """
        if not isinstance(record, dict):
            raise MemoryScopeViolation("记录必须是 dict")
        rec = dict(record)
        rec.setdefault("ts", int(time.time()))
        rec["module"] = self.module_id       # 强制打标，不允许调用方篡改
        self.ensure_dirs()
        p = self.path(self.SESSION_NAME)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def rotate(self, keep=200):
        """轮转：保留最新 keep 条，其余归档到 archive/SESSION-<ts>.jsonl。

        用 os.replace 做同盘原子移动（不复制不删除，宁可留旧也不丢数据）。
        返回 (kept, archived)。
        """
        recs = self.read_session(limit=0)
        if len(recs) <= keep:
            return len(recs), 0
        keep_recs, old_recs = recs[:keep], recs[keep:]
        # 旧条目归档（追加到按日期命名的归档文件）
        self.ensure_dirs()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        arch = self.path("SESSION-%s.jsonl" % stamp, subdir="archive")
        arch.parent.mkdir(parents=True, exist_ok=True)
        with open(arch, "a", encoding="utf-8") as f:
            for r in old_recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        # 重写主文件（原子：先写 tmp 再 replace）
        p = self.path(self.SESSION_NAME)
        tmp = self.path(self.SESSION_NAME + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for r in keep_recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(str(tmp), str(p))
        return len(keep_recs), len(old_recs)

    # ---------------- STATE（机写状态） ----------------
    STATE_NAME = "STATE.json"

    def read_state(self):
        p = self.path(self.STATE_NAME)
        if not p.exists():
            return {}
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def write_state(self, state):
        """原子写状态（写失败保留旧值，不留空文件）。

        —— 这条来自真实事故（ADR-007）：缓存写入抖动导致 5 分钟数据全丢，
        根因是"写失败时把空值写了进去"。此处先写 tmp 再 replace，
        且异常时**不动原文件**。
        """
        if not isinstance(state, dict):
            raise MemoryScopeViolation("state 必须是 dict")
        self.ensure_dirs()
        p = self.path(self.STATE_NAME)
        tmp = self.path(self.STATE_NAME + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(state, ensure_ascii=False, indent=2))
        os.replace(str(tmp), str(p))
        return state


def memory_root_for(app_dir, module_id=None):
    """从应用数据目录推导记忆根：<app_dir>/memory/。

    与既有实现的关系：既有的 `_records_dir()` 落在
    `%LOCALAPPDATA%\\winhelper\\records\\`；记忆层用**独立子目录** `memory/`，
    两者并存不冲突（历史文件是"记录"，记忆是"可迭代的状态"）。
    """
    root = Path(app_dir).expanduser()
    return root / "memory"
