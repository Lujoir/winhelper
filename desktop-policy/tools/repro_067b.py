# -*- coding: utf-8 -*-
"""BRG-067 二段复现：_png_encode 小规模直测 + 类型探针。"""
import ctypes
import io
import os
import sys
import traceback

sys.path.insert(0, r"c:\Users\10604\CodeBuddy\20260522083146")
import desktop_policy as dp

OUT = r"c:\Users\10604\CodeBuddy\20260522083146\desktop-policy\repro_out.txt"
lines = []
small = ctypes.create_string_buffer(4 * 4 * 4)
lines.append("buffer type: %r" % type(small))
lines.append("elem type: %r" % type(small[0]))
try:
    dp._png_encode(small, 4, 4,
                   os.path.join(os.path.dirname(OUT), "repro_small.png"))
    lines.append("SMALL _png_encode OK")
except Exception:
    lines.append("SMALL FAILED:")
    lines.append(traceback.format_exc())

io.open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("written")
