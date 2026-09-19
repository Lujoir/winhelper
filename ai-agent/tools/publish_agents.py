#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI Agent 配置发布器：ai-agent/agents/<env>/ → 功能模块目录。

**为什么需要"发布"这一步**
配置的**源**统一在 `ai-agent/agents/`（集中维护、统一评审），但功能模块需要
**自包含**地持有自己的 Agent 配置（模块目录内可直接读到）。两处各手写一份必然漂移，
所以把"发布"做成**机械操作** + 校验兜底：

    源（唯一维护点）  ──publish──>  模块内副本（只读，勿手改）
            ↑                              │
            └──── verify 比对哈希 ─────────┘

**副本结构** = 发布头（源路径 / 时间 / 内容 SHA256）+ 源文件原文。
校验时剥离发布头比对哈希 —— 任何手改**立即被发现**。

用法：
    python ai-agent/tools/publish_agents.py            # 发布（幂等，内容相同则跳过）
    python ai-agent/tools/publish_agents.py --dry-run  # 预览
    python ai-agent/tools/publish_agents.py --env center   # 只发布一侧
"""
import argparse
import hashlib
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent          # ai-agent/tools
AGENT_ROOT = HERE.parent                        # ai-agent/
PROJECT_ROOT = AGENT_ROOT.parent                # 项目根

# 环境 → (源目录, 发布目标目录)
# 中心服务侧：发布到 server-platform 服务端（独立子仓库）
# Windows 客户端侧：发布到 net-doctor（AI 诊断的承载项目）
TARGETS = {
    "center": {
        "src": AGENT_ROOT / "agents" / "center",
        "dst": PROJECT_ROOT / "server-platform" / "server" / "agents",
        "label": "中心服务端",
    },
    "client": {
        "src": AGENT_ROOT / "agents" / "client",
        "dst": PROJECT_ROOT / "net-doctor" / "agents",
        "label": "Windows 客户端",
    },
}

HEADER_TMPL = """<!--
  ⚠️ 本文件由 ai-agent/tools/publish_agents.py 自动生成，**请勿手改**。
     手改会在下次发布时被覆盖；需要改内容请改源文件后重新发布。

  源文件  : {src}
  发布时间: {ts}
  内容指纹: {digest}   （源文件正文 SHA256 前 16 位，校验用）

  重新发布: python ai-agent/tools/publish_agents.py
  校验一致: python ai-agent/tools/verify_agent_configs.py
-->

"""


def body_digest(text):
    """源文件正文指纹（剥离发布头后再算，保证与副本可比）。"""
    return hashlib.sha256(strip_header(text).encode("utf-8")).hexdigest()[:16]


def strip_header(text):
    """剥离发布头（副本 → 纯正文）。无头则原样返回。"""
    if text.startswith("<!--\n  ⚠️ 本文件由"):
        end = text.find("-->\n\n")
        if end != -1:
            return text[end + len("-->\n\n"):]
    return text


def publish_one(env, src_path, dst_dir, dry_run=False):
    """发布单个配置。返回 'created' | 'updated' | 'unchanged' | 'missing'。"""
    if not src_path.is_file():
        return "missing"
    src_text = src_path.read_text(encoding="utf-8")
    digest = body_digest(src_text)
    dst_path = dst_dir / src_path.name
    ts = time.strftime("%Y-%m-%d %H:%M:%S")

    header = HEADER_TMPL.format(
        src=str(src_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        ts=ts, digest=digest)
    new_text = header + src_text

    exists = dst_path.is_file()
    if exists:
        old_text = dst_path.read_text(encoding="utf-8")
        # 内容一致（含指纹一致）→ 跳过，保持副本时间戳稳定
        if old_text == new_text or (
                strip_header(old_text) == src_text
                and ("内容指纹: %s" % digest) in old_text):
            return "unchanged"

    if not dry_run:
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst_path.write_text(new_text, encoding="utf-8")
    return "updated" if exists else "created"


def main():
    ap = argparse.ArgumentParser(description="AI Agent 配置发布器")
    ap.add_argument("--dry-run", action="store_true", help="只预览不写入")
    ap.add_argument("--env", default="", choices=["", "center", "client"],
                    help="只发布指定环境（默认两者都发）")
    args = ap.parse_args()

    envs = [args.env] if args.env else list(TARGETS.keys())
    total = {"created": 0, "updated": 0, "unchanged": 0, "missing": 0}
    print("=== AI Agent 配置发布%s ===" % ("（dry-run 预览）" if args.dry_run else ""))
    for env in envs:
        cfg = TARGETS[env]
        src_dir, dst_dir = cfg["src"], cfg["dst"]
        rel_dst = str(dst_dir.relative_to(PROJECT_ROOT)).replace("\\", "/")
        print("\n[%s] %s" % (cfg["label"], env))
        print("  源  : %s" % str(src_dir.relative_to(PROJECT_ROOT)).replace("\\", "/"))
        print("  目标: %s" % rel_dst)
        if not src_dir.is_dir():
            print("  ! 源目录不存在，跳过")
            continue
        files = sorted(src_dir.glob("*.agent.md"))
        if not files:
            print("  ! 源目录下无 *.agent.md")
            continue
        for f in files:
            act = publish_one(env, f, dst_dir, args.dry_run)
            total[act] = total.get(act, 0) + 1
            mark = {"created": "+ 新建", "updated": "~ 更新",
                    "unchanged": "= 未变", "missing": "! 缺失"}[act]
            print("  %s  %s" % (mark, f.name))

    print("\n合计：新建 %d / 更新 %d / 未变 %d / 缺失 %d"
          % (total["created"], total["updated"],
             total["unchanged"], total["missing"]))
    if args.dry_run:
        print("（dry-run：未写入任何文件）")
    else:
        print("提示：发布后请跑 python ai-agent/tools/verify_agent_configs.py 校验一致性。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
