# -*- coding: utf-8 -*-
"""esprima 语法初筛（勿用 ?. 可选链；esprima 检不出 dup const，靠 li 前缀规避）"""
import io
import os
import sys

import esprima

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGETS = [
    os.path.join(ROOT, "web", "loginspector.js"),
    os.path.join(os.path.dirname(ROOT), "web", "loginspector.js"),
]

ok = True
for path in TARGETS:
    if not os.path.exists(path):
        continue
    src = io.open(path, encoding="utf-8").read()
    if "?." in src:
        print(f"FAIL 可选链 ?.: {path}")
        ok = False
        continue
    try:
        esprima.parseScript(src, tolerant=False)
        print(f"OK   {os.path.basename(os.path.dirname(os.path.dirname(path)))}/{os.path.basename(path)}")
    except Exception as e:
        print(f"FAIL {path}: {e}")
        ok = False

sys.exit(0 if ok else 1)
