#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI Agent 配置校验器 —— 源与发布副本的"防漂移"门禁。

校验七项（任一失败 → 退出码 1）：

  1. **必填字段**      frontmatter 关键字段齐全且非空
  2. **枚举合法**      env ∈ {center, client}；service_target ∈ {operator, enduser}
  3. **段落完整**      模板要求的 11 个段落一个都不能少
  4. **身份可溯**      §1 必须指向 soul/SOUL.md（不得自造身份）
  5. **隐私不空**      §7 隐私边界不得留空（准则五）
  6. **发布一致**      模块内副本存在，且剥离发布头后与源**逐字节相同**
  7. **常量存在**      const_ref 指向的常量在源码中确实存在

设计原则：**宁可报错，不可放过**。校验器若形同虚设，配置就会慢慢腐烂成摆设。

用法：
    python ai-agent/tools/verify_agent_configs.py
    python ai-agent/tools/verify_agent_configs.py -v      # 打印通过项明细
"""
import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT_ROOT = HERE.parent
PROJECT_ROOT = AGENT_ROOT.parent

REQUIRED_FIELDS = ("agent_id", "feature", "env", "module", "owner",
                   "service_target", "version", "status",
                   "code_ref", "const_ref")
ENV_VALUES = ("center", "client")
TARGET_VALUES = ("operator", "enduser")
REQUIRED_SECTIONS = tuple(range(1, 12))     # §1 ~ §11

# env → (发布目标目录, 常量搜索范围)
PUBLISH_DST = {
    "center": (PROJECT_ROOT / "server-platform" / "server" / "agents",
               [PROJECT_ROOT / "server-platform" / "server"]),
    "client": (PROJECT_ROOT / "net-doctor" / "agents",
               [PROJECT_ROOT, PROJECT_ROOT / "net-doctor"]),
}

OK, FAIL = "PASS", "FAIL"
results = []


def check(name, ok, detail=""):
    results.append((OK if ok else FAIL, name, detail))
    return ok


def parse_frontmatter(text):
    """极简 frontmatter 解析（本文件只用到 key: value，不引入 yaml 依赖）。"""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm = {}
    for line in text[3:end].splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.split("#")[0].strip().strip('"').strip("'")
        fm[k.strip()] = v
    return fm


def strip_header(text):
    if text.startswith("<!--\n  ⚠️ 本文件由"):
        end = text.find("-->\n\n")
        if end != -1:
            return text[end + len("-->\n\n"):]
    return text


def const_search_roots(env):
    return PUBLISH_DST[env][1]


def const_exists(const_ref, env):
    """const_ref 的第一个 token 是否作为标识符出现在源码中。"""
    if not const_ref:
        return False, "const_ref 为空"
    token = re.split(r"[\s（(：:/]+", const_ref.strip())[0]
    if not token:
        return False, "无法解析常量名"
    for base in const_search_roots(env):
        if not base.is_dir():
            continue
        for py in base.glob("*.py"):
            try:
                if token in py.read_text(encoding="utf-8", errors="ignore"):
                    return True, "%s ← %s" % (token, py.name)
            except OSError:
                continue
    return False, "源码中未找到标识符 %r" % token


def verify_one(src_path, env, verbose):
    text = src_path.read_text(encoding="utf-8")
    fm = parse_frontmatter(text)
    tag = src_path.name

    # 1. 必填字段
    miss = [k for k in REQUIRED_FIELDS if not fm.get(k)]
    check("[%s] 必填字段齐全" % tag, not miss,
          "" if not miss else "缺: %s" % ", ".join(miss))

    # 2. 枚举合法
    check("[%s] env 取值合法" % tag, fm.get("env") in ENV_VALUES,
          "env=%r（应为 center|client）" % fm.get("env"))
    check("[%s] service_target 取值合法" % tag,
          fm.get("service_target") in TARGET_VALUES,
          "service_target=%r（应为 operator|enduser）" % fm.get("service_target"))
    check("[%s] env 与所在目录一致" % tag, fm.get("env") == env,
          "目录=%s 但 env=%r" % (env, fm.get("env")))

    # 3. 段落完整
    missing_sec = [n for n in REQUIRED_SECTIONS
                   if not re.search(r"^##\s+%d\.\s" % n, text, re.M)]
    check("[%s] 11 个段落齐全" % tag, not missing_sec,
          "" if not missing_sec else "缺段落: %s"
          % ", ".join("§%d" % n for n in missing_sec))

    # 4. 身份可溯 Soul
    check("[%s] 身份指向 soul/SOUL.md" % tag, "soul/SOUL.md" in text,
          "§1 未引用 soul/SOUL.md（身份不得自造）")

    # 5. 隐私边界非空（§7 段落内须有实质内容）
    m = re.search(r"^##\s+7\..*?(?=^##\s+8\.)", text, re.M | re.S)
    sec7 = m.group(0) if m else ""
    check("[%s] §7 隐私边界非空" % tag, len(sec7.strip()) > 120,
          "§7 内容过短（准则五要求逐项填写）")

    # 6. 发布一致
    dst_dir = PUBLISH_DST[env][0]
    dst_path = dst_dir / tag
    if not dst_path.is_file():
        check("[%s] 模块内副本存在" % tag, False,
              "未发布 → 跑 publish_agents.py")
    else:
        dst_text = dst_path.read_text(encoding="utf-8")
        same = strip_header(dst_text) == text
        check("[%s] 副本与源一致" % tag, same,
              "" if same else "副本已被手改或源已变更 → 重新发布")

    # 7. 常量存在
    ok, detail = const_exists(fm.get("const_ref"), env)
    check("[%s] const_ref 在源码存在" % tag, ok, detail)

    if verbose:
        print("    · %s → const_ref=%s" % (tag, fm.get("const_ref")))


def main():
    ap = argparse.ArgumentParser(description="AI Agent 配置校验器")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印明细")
    args = ap.parse_args()

    total = 0
    for env in ("center", "client"):
        src_dir = AGENT_ROOT / "agents" / env
        print("=== %s ===" % ("中心服务端" if env == "center" else "Windows 客户端"))
        if not src_dir.is_dir():
            print("  ! 源目录不存在: %s" % src_dir)
            check("[%s] 源目录存在" % env, False, str(src_dir))
            continue
        files = sorted(src_dir.glob("*.agent.md"))
        if not files:
            print("  ! 无 *.agent.md")
            check("[%s] 存在配置" % env, False, "目录为空")
            continue
        for f in files:
            print("  · %s" % f.name)
            verify_one(f, env, args.verbose)
            total += 1

    fails = [r for r in results if r[0] == FAIL]
    passes = [r for r in results if r[0] == OK]
    print("\n=== 结果 ===")
    for st, name, detail in fails:
        print("  FAIL  %s%s" % (name, ("  —— " + detail) if detail else ""))
    if args.verbose:
        for st, name, detail in passes:
            print("  PASS  %s" % name)
    print("\n配置 %d 份 | 检查项 %d | 通过 %d | 失败 %d"
          % (total, len(results), len(passes), len(fails)))
    if fails:
        print("\n校验未通过。修复后重跑；发布副本不一致请执行：")
        print("  python ai-agent/tools/publish_agents.py")
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
