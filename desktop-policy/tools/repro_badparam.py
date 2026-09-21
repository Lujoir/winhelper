# -*- coding: utf-8 -*-
"""BRG-068 探针：bad param 时 handle_dp_powercfg_set 的返回。"""
import io
import os
import sys
import traceback

sys.path.insert(0, r"c:\Users\10604\CodeBuddy\20260522083146")
import desktop_policy as dp

lines = []
try:
    r = dp.handle_dp_powercfg_set({"display_off_ac": "abc"})
    lines.append("r = %r" % (r,))
except Exception:
    lines.append("EXC:")
    lines.append(traceback.format_exc())
io.open(r"c:\Users\10604\CodeBuddy\20260522083146\desktop-policy\probe_out.txt",
        "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("done")
