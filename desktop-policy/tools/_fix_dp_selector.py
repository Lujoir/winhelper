# -*- coding: utf-8 -*-
"""一次性：desktoppolicy.js 中 $("..") 调用替换为 dp$("..")（自包含 DOM 辅助）。"""
import io
import re

p = r"c:\Users\10604\CodeBuddy\20260522083146\desktop-policy\web\desktoppolicy.js"
src = io.open(p, encoding="utf-8").read()
n_before = len(re.findall(r'(?<![A-Za-z0-9_$.])\$\("', src))
out = re.sub(r'(?<![A-Za-z0-9_$.])\$\("', 'dp$("', src)
# dp$ 自身定义行保留（function dp$(sel) 已是新名，不受影响）
io.open(p, "w", encoding="utf-8", newline="\n").write(out)
print("replaced:", n_before)
