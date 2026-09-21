# -*- coding: utf-8 -*-
"""临时产物归置：把项目根与各模块的临时输出，集中到 ai-agent/tmp/triage-<日期>/。

背景
----
ADR-005 定了"临时产物用 `_` 前缀 / `*_out*.txt` / `nd_tmp_*`"的约定，但各模块
长期未接入，导致主仓根与 server-platform 下堆积上百个临时文件（实测 138 个）。
本工具是**治理动作**，不是一次性脚本。

两类处置（关键区分）
------------------
**证据类 → ai-agent/evidence/**（长期保留，**不进 tmp**）
    截图、取证数据、现场报告。它们是"当时现场的凭据"，复盘/追责时需要，
    价值高于普通中间产物 —— 放 tmp/ 会被 TTL 机制清掉，那是错的。

**临时类 → ai-agent/tmp/triage-<日期>/**（按 TTL 兜底）
    命令输出重定向、调试脚本、误重定向产物。

**边界（重要）**
  · 只动"未被 git 跟踪"的文件 —— 已入库的归版本控制管，不由本工具处置
  · `docs/` 下的文件**不动**（那是资产目录；其中的临时项应由人工挑拣后入库）
  · 默认 dry-run；`--apply` 才真正移动
  · **移动而非删除**（SOUL 准则六：不确定就保留）

用法
----
    python ai-agent/tools/triage_junk.py            # dry-run，只报告
    python ai-agent/tools/triage_junk.py --apply    # 实际归置
"""
import argparse
import datetime as dt
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAMP = dt.date.today().strftime("%Y%m%d")
TRIAGE_DIR = ROOT / "ai-agent" / "tmp" / ("triage-" + STAMP)
EVIDENCE_DIR = ROOT / "ai-agent" / "evidence" / ("triage-" + STAMP)

# 扫描范围：只扫这些目录（避免误入 node_modules 之类）
SCAN_DIRS = ["", "server-platform", "net-doctor", "file-search",
             "perf-analyzer", "disk-cleaner", "log-inspector",
             "desktop-policy", "power-control", "tools"]

# 证据类：截图 / 取证数据 / 现场报告 —— 移到 evidence/ 长期保留
EVIDENCE_PATTERNS = [
    re.compile(r".*\.(png|jpg|jpeg|webp)$"),
    re.compile(r".*forensic.*\.(json|txt)$"),
    re.compile(r".*_report.*\.(md|json)$"),
]

# 临时类：命令输出 / 调试脚本 / 误重定向产物
JUNK_PATTERNS = [
    re.compile(r"^_.+\.(txt|json|log|py)$"),        # 根级 `_` 前缀
    re.compile(r".*_out\d*\.(txt|json)$"),          # *_out.txt
    re.compile(r"^out_.*\.txt$"),
    re.compile(r"^nd_.*\.txt$"),
    re.compile(r"^nd_tmp_.*\.py$"),
    re.compile(r".*[_-]smoke.*\.txt$"),
    re.compile(r"^app_.*run.*\.txt$"),
    re.compile(r"^deploy.*_out.*\.txt$"),
    re.compile(r"^gen_.*_out.*\.txt$"),
    re.compile(r"^spike\d*_.*\.txt$"),
    re.compile(r"^(auth_check|der_dump|ip_err|ip_out)\.(txt|py)$"),
    re.compile(r"^\.?OpenSSL$"),                     # 误重定向产物
]

# 例外：这些目录下的文件不处置
SKIP_DIRS = {"node_modules", "third_party", "build", "dist", ".git", "installer",
             "ai-agent", "assets", "docs", "vendor"}

# 例外：这些文件名保留
KEEP_NAMES = {".gitignore", ".gitattributes", "requirements.txt", "README.md"}


def tracked_files():
    """git 跟踪的文件集合（posix 相对路径）——不处置它们。"""
    r = subprocess.run(["git", "ls-files", "-z"], cwd=str(ROOT), capture_output=True)
    if r.returncode != 0:
        print("[WARN] git ls-files 失败，将按'未跟踪'全集处理")
        return set()
    return {x for x in r.stdout.decode("utf-8", "replace").split("\0") if x}


def classify(name):
    """返回 'evidence' / 'junk' / None。"""
    if name in KEEP_NAMES:
        return None
    if any(p.search(name) for p in EVIDENCE_PATTERNS):
        return "evidence"
    if any(p.search(name) for p in JUNK_PATTERNS):
        return "junk"
    return None


def show(title, items):
    if not items:
        print("  %s：0 个" % title)
        return
    groups = {}
    for rel in items:
        key = rel.split("/")[0] if "/" in rel else "(根目录)"
        groups.setdefault(key, []).append(rel)
    print("  %s：%d 个" % (title, len(items)))
    for k in sorted(groups, key=lambda x: -len(groups[x])):
        print("    [%s] %d 个" % (k, len(groups[k])))
        for rel in sorted(groups[k])[:3]:
            print("        · %s" % rel)
        if len(groups[k]) > 3:
            print("        ... 另 %d 个" % (len(groups[k]) - 3))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际移动（默认仅 dry-run）")
    args = ap.parse_args()

    tracked = tracked_files()
    print("git 跟踪文件数 = %d" % len(tracked))

    junk, evidence = [], []
    for d in SCAN_DIRS:
        base = ROOT / d if d else ROOT
        if not base.is_dir():
            continue
        for p in sorted(base.iterdir()):
            if p.is_dir():
                continue
            rel = p.relative_to(ROOT).as_posix()
            if rel in tracked:
                continue
            if any(part in SKIP_DIRS for part in Path(rel).parts[:-1]):
                continue
            c = classify(p.name)
            if c == "junk":
                junk.append(rel)
            elif c == "evidence":
                evidence.append(rel)

    print("命中：临时类 %d 个，证据类 %d 个" % (len(junk), len(evidence)))
    show("临时类 → ai-agent/tmp/（TTL 兜底）", junk)
    show("证据类 → ai-agent/evidence/（长期保留）", evidence)

    if not args.apply:
        print("\n[dry-run] 未做任何改动。加 --apply 执行归置。")
        return 0

    moved, failed = 0, 0
    for rel, base_dir in ([(r, TRIAGE_DIR) for r in junk]
                          + [(r, EVIDENCE_DIR) for r in evidence]):
        src = ROOT / rel
        dst = base_dir / rel
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                dst = dst.with_name(dst.name + ".dup")
            shutil.move(str(src), str(dst))
            moved += 1
        except Exception as e:      # noqa: BLE001
            print("  [FAIL] %s : %s" % (rel, e))
            failed += 1

    print("\n[apply] 已移动 %d 个，失败 %d 个" % (moved, failed))
    print("  临时类 -> %s" % TRIAGE_DIR.relative_to(ROOT).as_posix())
    print("  证据类 -> %s" % EVIDENCE_DIR.relative_to(ROOT).as_posix())
    print("（临时类受 ai-agent/.gitignore 排除并由 cleanup.py 按 TTL 兜底；证据类需在")
    print(" ai-agent/.gitignore 中单独保留，勿让 TTL 清掉）")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
