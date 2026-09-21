# -*- coding: utf-8 -*-
"""一次性清理：根目录 desktop_policy.py 删除无引用的电源分钟版实现
（被并行会话的 get_current_power_settings/set_current_power_settings 取代）。"""
import io
import re

p = r"c:\Users\10604\CodeBuddy\20260522083146\desktop_policy.py"
src = io.open(p, encoding="utf-8").read()

for name in ("_sec_to_min", "apply_local_power_seconds"):
    pat = re.compile(r"\ndef %s\(.*?(?=\ndef |\nclass |\n# -)" % name, re.S)
    src, n = pat.subn("\n", src, count=1)
    print(name, "removed blocks:", n)

io.open(p, "w", encoding="utf-8", newline="\n").write(src)
