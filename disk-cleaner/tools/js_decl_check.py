#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JS 同作用域重复声明检测（esprima AST 作用域扫描）
=================================================
背景（2026-09-05 主应用事故，主应用 commit 5f4eaad 修复）:
  esprima-python 检不出同作用域重复 const/let 声明——
  `function f(){const a=1;const a=2;}` 解析通过（已用最小用例验证）。
  主应用 web/disk.js renderTreemap 中两处 `const atRoot` 使整个 disk.js
  SyntaxError，initDiskTab 未定义，页面静默空白，而 esprima 语法校验绿灯。
本脚本补上该检测，作为 web/ JS 同步与提交前的验证门禁之一（ADR-012）。

用法:
    python tools/js_decl_check.py                # 检查 web/ 下默认 3 个 JS
    python tools/js_decl_check.py a.js b.js      # 检查指定文件
    python tools/js_decl_check.py --selftest     # 工具自检（含事故最小用例）

退出码: 0=通过  1=发现重复声明  2=解析/IO错误
"""
import os
import sys

import esprima

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(PROJECT_ROOT, "web")
DEFAULT_FILES = ["app-lite.js", "disk.js", "appdata.js"]

# 注意: FunctionDeclaration 有专用分支（需登记名字到父作用域做冲突检查），
# 不能放入 FUNC_NODES，否则会被表达式分支拦截跳过登记（2026-09-05 selftest case9 教训）
FUNC_NODES = ("FunctionExpression", "ArrowFunctionExpression")
BLOCK_SCOPES = ("BlockStatement", "ForStatement", "ForInStatement", "ForOfStatement",
                "SwitchStatement")


def _ident_name(idnode):
    if isinstance(idnode, dict) and idnode.get("type") == "Identifier":
        return idnode.get("name")
    return None


def walk(node, scopes, conflicts, path):
    """作用域感知遍历。scopes: 栈，栈顶为当前词法环境 {kind, names{name: kind}}"""
    if isinstance(node, list):
        for item in node:
            walk(item, scopes, conflicts, path)
        return
    if not isinstance(node, dict) or "type" not in node:
        return
    t = node["type"]

    if t in FUNC_NODES:
        body = node.get("body")
        if body and body.get("type") == "BlockStatement":
            scopes.append({"kind": "function", "names": {}})
            walk(body.get("body"), scopes, conflicts, path)
            scopes.pop()
        elif body:  # 箭头函数表达式体
            walk(body, scopes, conflicts, path)
        return

    if t == "VariableDeclaration":
        kind = node.get("kind")
        if kind in ("const", "let"):
            target = scopes[-1]  # 词法声明归最近块/函数/程序作用域
        elif kind == "var":
            # var 归最近函数/程序作用域（var 无块级作用域）
            target = next((s for s in reversed(scopes)
                           if s["kind"] in ("function", "program")), None)
        else:
            target = None
        if target is not None:
            for d in node.get("declarations", []):
                name = _ident_name(d.get("id"))
                if not name:
                    continue
                prev = target["names"].get(name)
                if prev is None:
                    target["names"][name] = kind
                elif kind in ("const", "let") and prev in ("const", "let", "class"):
                    conflicts.append(
                        "%s: %s '%s' 重复声明（已有 %s）— esprima 语法校验检不出" %
                        (path, kind, name, prev))
        return

    if t == "FunctionDeclaration":
        name = _ident_name(node.get("id"))
        if name:
            prev = scopes[-1]["names"].get(name)
            if prev in ("const", "let", "class"):
                conflicts.append("%s: function '%s' 与已有 '%s' 重复声明" %
                                 (path, name, prev))
            elif prev is None:
                scopes[-1]["names"][name] = "fn"
        scopes.append({"kind": "function", "names": {}})
        fn_body = node.get("body") or {}
        walk(fn_body.get("body"), scopes, conflicts, path)
        scopes.pop()
        return

    if t == "ClassDeclaration":
        name = _ident_name(node.get("id"))
        if name:
            prev = scopes[-1]["names"].get(name)
            if prev is not None:
                conflicts.append("%s: class '%s' 与已有 '%s' 重复声明" %
                                 (path, name, prev))
            else:
                scopes[-1]["names"][name] = "class"
        scopes.append({"kind": "class", "names": {}})
        walk(node.get("body"), scopes, conflicts, path)
        scopes.pop()
        return

    if t == "CatchClause":
        scopes.append({"kind": "catch", "names": {}})
        pname = _ident_name(node.get("param"))
        if pname:
            scopes[-1]["names"][pname] = "param"
        body = node.get("body") or {}
        walk(body.get("body"), scopes, conflicts, path)
        scopes.pop()
        return

    if t in BLOCK_SCOPES:
        scopes.append({"kind": "block", "names": {}})
        if t == "BlockStatement":
            walk(node.get("body"), scopes, conflicts, path)
        else:
            for key in ("init", "test", "update", "left", "right",
                        "discriminant", "cases", "body"):
                if key in node:
                    walk(node[key], scopes, conflicts, path)
        scopes.pop()
        return

    # 普通节点: 子节点留在当前作用域
    for key, value in node.items():
        if key in ("loc", "range"):
            continue
        if isinstance(value, (dict, list)):
            walk(value, scopes, conflicts, path)


def check_file(path):
    with open(path, encoding="utf-8") as f:
        code = f.read()
    ast = esprima.parseScript(code, tolerant=False)
    tree = esprima.toDict(ast)
    conflicts = []
    walk(tree, [{"kind": "program", "names": {}}], conflicts, os.path.basename(path))
    return conflicts


# (代码片段, 期望检出冲突数)
SELFTEST_CASES = [
    ("function f(){ const a=1; const a=2; }", 1),   # 2026-09-05 事故最小用例
    ("function f(){ const atRoot = !a; const b = 1; const atRoot = b; }", 1),
    ("const x = 1; let x = 2;", 1),
    ("let a; { let a; }", 0),                        # 不同作用域: 合法
    ("for(let i=0;;){} for(let i=0;;){}", 0),        # for 各自作用域: 合法
    ("function f(){ const atRoot=1; } function g(){ const atRoot=2; }", 0),
    ("function f(){ var a=1; var a=2; }", 0),        # var 重复: 合法
    ("switch(x){ case 1: let v=1; break; case 2: let v=2; break; }", 1),  # case 共享块作用域
    ("const f = 1; function f(){}", 1),              # 函数声明与词法声明冲突
]


def selftest():
    failed = 0
    for i, (code, expect) in enumerate(SELFTEST_CASES, 1):
        ast = esprima.parseScript(code, tolerant=False)
        tree = esprima.toDict(ast)
        conflicts = []
        walk(tree, [{"kind": "program", "names": {}}], conflicts, "case%d" % i)
        status = "OK" if len(conflicts) == expect else "FAIL"
        if status == "FAIL":
            failed += 1
        print("  case%d: expect=%d got=%d [%s] %s" %
              (i, expect, len(conflicts), status,
               conflicts[0] if conflicts else code[:46]))
    return failed == 0


def main():
    args = sys.argv[1:]
    if args == ["--selftest"]:
        print("js_decl_check selftest:")
        ok = selftest()
        print("SELFTEST_" + ("OK" if ok else "FAILED"))
        return 0 if ok else 1

    files = args or [os.path.join(WEB_DIR, f) for f in DEFAULT_FILES]
    total = 0
    exit_code = 0
    for fp in files:
        try:
            conflicts = check_file(fp)
        except Exception as e:
            print("[PARSE_ERROR] %s: %s" % (fp, e))
            exit_code = 2
            continue
        total += len(conflicts)
        if conflicts:
            for c in conflicts:
                print("[CONFLICT] " + c)
        else:
            print("[OK] %s: 无重复声明" % os.path.basename(fp))
    if total:
        exit_code = 1
    print("JS_DECL_CHECK_" + ("OK" if exit_code == 0 else ("FAIL(%d)" % total if exit_code == 1 else "ERROR")))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
