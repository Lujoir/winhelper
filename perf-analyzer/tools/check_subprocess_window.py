# -*- coding: utf-8 -*-
"""
门禁第四道：GUI 无控制台程序子进程窗口检查（tools/check_subprocess_window.py）
================================================================
背景事故（2026-09-06，热修 perf-analyzer 819e806 / 主应用 0db7c48）：
windowed exe（console=False）中 subprocess 调用控制台程序（nvidia-smi/powershell/
typeperf）每次都会生成可见 cmd 窗口——温度 2s 轮询 = 不停循环弹窗。
为什么动态门禁没拦住：smoke 在控制台跑、E2E 用桩，两种验证都看不到弹窗，
必须静态检查兜底（发布四连：esprima → js_decl_check → e2e → 本检查）。

规则：目标文件中所有 subprocess.run/Popen/call/check_output/check_call 调用
必须携带 creationflags=_NO_WINDOW（模块级 _NO_WINDOW = getattr(subprocess,
"CREATE_NO_WINDOW", 0) 必须定义）；白名单：explorer 等有意开窗调用（调用源码
片段含 "explorer" 即豁免，如 disk_cleanup 的打开位置）。

用法：
  python tools/check_subprocess_window.py             # 默认检查 perf_service.py
  python tools/check_subprocess_window.py a.py b.py   # 检查指定文件
  python tools/check_subprocess_window.py --selftest  # 自检（缺失/白名单/正常三用例）
"""

import ast
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_FILES = [os.path.join(ROOT, "perf_service.py")]
_WINDOWED_FUNCS = {"run", "Popen", "call", "check_output", "check_call"}
WHITELIST_KEYWORDS = ("explorer",)  # 有意开窗（资源管理器定位）豁免


def _check_source(path, source):
    problems = []
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return ["%s: 语法解析失败 line %s: %s" % (path, e.lineno, e.msg)]
    has_no_window_def = False
    from_imports = set()  # from subprocess import run/Popen/... 的裸名
    lines = source.splitlines()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                from_imports.add(alias.asname or alias.name)
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "_NO_WINDOW":
                    has_no_window_def = True
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        # subprocess.run(...) 形式
        hit = False
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                and f.value.id == "subprocess" and f.attr in _WINDOWED_FUNCS:
            hit = True
        # from subprocess import run; run(...) 形式
        elif isinstance(f, ast.Name) and f.id in from_imports:
            hit = True
        if not hit:
            continue

        # 白名单：有意开窗调用（调用片段含 explorer 等关键词）
        lo = max(0, (getattr(node, "lineno", 1)) - 3)
        hi = min(len(lines), (getattr(node, "end_lineno", node.lineno) or node.lineno) + 2)
        seg = "\n".join(lines[lo:hi]).lower()
        if any(k in seg for k in WHITELIST_KEYWORDS):
            continue

        if not any(kw.arg == "creationflags" for kw in node.keywords):
            problems.append("%s:%d subprocess.%s 缺少 creationflags=_NO_WINDOW"
                            % (path, node.lineno, getattr(f, "attr", None) or getattr(f, "id", "?")))

    if not has_no_window_def:
        problems.append('%s: 未定义模块级 _NO_WINDOW 常量'
                        '（_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)）' % path)
    return problems


def check_paths(paths):
    """返回问题列表（空 = 通过）"""
    problems = []
    for p in paths:
        try:
            src = io.open(p, encoding="utf-8").read()
        except Exception as e:
            problems.append("%s: 读取失败 %s" % (p, e))
            continue
        problems.extend(_check_source(p, src))
    return problems


def _selftest():
    good = (
        "import subprocess\n"
        '_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)\n'
        "def f():\n"
        '    subprocess.run(["nvidia-smi", "-q"], capture_output=True, creationflags=_NO_WINDOW)\n'
        "def g():\n"
        '    subprocess.Popen(["explorer", "/select,C:/x"])  # 有意开窗：资源管理器定位\n'
    )
    bad = (
        "import subprocess\n"
        "def f():\n"
        '    subprocess.run(["nvidia-smi", "-q"], capture_output=True)\n'
    )
    fd_g, p_g = tempfile.mkstemp(suffix=".py")
    fd_b, p_b = tempfile.mkstemp(suffix=".py")
    try:
        with io.open(p_g, "w", encoding="utf-8") as f:
            f.write(good)
        with io.open(p_b, "w", encoding="utf-8") as f:
            f.write(bad)
        assert not check_paths([p_g]), "selftest: 正常用例误报"
        probs = check_paths([p_b])
        assert any("creationflags" in p for p in probs), "selftest: 缺失用例未检出"
        print("SELFTEST_OK（白名单豁免 / 缺失检出）")
        return 0
    finally:
        os.close(fd_g)
        os.close(fd_b)
        try:
            os.remove(p_g)
            os.remove(p_b)
        except OSError:
            pass


def main(argv):
    if "--selftest" in argv:
        return _selftest()
    paths = argv or DEFAULT_FILES
    problems = check_paths(paths)
    if problems:
        for p in problems:
            print("[SUBPROC-FAIL] %s" % p)
        print("共 %d 处：GUI 无控制台程序中一切子进程必须 creationflags=_NO_WINDOW"
              "（有意开窗的 explorer 类除外，ADR-013）" % len(problems))
        return 1
    print("[SUBPROC-PASS] 子进程窗口检查通过（%d 个文件）" % len(paths))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
