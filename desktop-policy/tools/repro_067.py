# -*- coding: utf-8 -*-
"""BRG-067 本机复现（UTF-8 落文件版）：直接调引擎 build_stitch。"""
import os
import sys
import traceback
import io

sys.path.insert(0, r"c:\Users\10604\CodeBuddy\20260522083146")
import desktop_policy as dp  # noqa: E402

OUT = r"c:\Users\10604\CodeBuddy\20260522083146\desktop-policy\repro_out.txt"
cache = os.path.join(os.environ["LOCALAPPDATA"], "winhelper",
                     "desktop_policy", "cache")
src = os.path.join(cache, "1")
lines = []
try:
    monitors = dp.enum_monitors()
    vx, vy, vw, vh = dp.virtual_bounds()
    lines.append("monitors: %r" % [(m["width"], m["height"]) for m in monitors])
    lines.append("virtual: %s %s" % (vw, vh))
    lines.append("src exists: %s" % os.path.exists(src))
    out = dp.build_stitch([src], monitors, vx, vy, vw, vh)
    lines.append("BUILD OK: %s (%s)" % (out,
                 os.path.getsize(out) if os.path.exists(out) else 0))
except Exception:
    lines.append("BUILD FAILED:")
    lines.append(traceback.format_exc())
io.open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("written")
