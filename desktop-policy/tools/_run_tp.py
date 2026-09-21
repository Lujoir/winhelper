# -*- coding: utf-8 -*-
import os
import subprocess
os_path = r"c:\Users\10604\CodeBuddy\20260522083146\desktop-policy"
p = subprocess.run(["python", "-m", "unittest", "tools.test_power", "-v"],
                   capture_output=True, cwd=os_path)
import io
io.open(os_path + r"\p.txt", "wb").write(p.stdout + b"\n--STDERR--\n" + p.stderr)
print("done")
