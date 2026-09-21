# -*- coding: utf-8 -*-
"""从 exe 提取内嵌 web 文件到指定目录（取证用）"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from PyInstaller.archive.readers import CArchiveReader  # noqa: E402

exe, outdir = sys.argv[1], sys.argv[2]
ar = CArchiveReader(exe)
toc = ar.toc if isinstance(ar.toc, dict) else {t[0]: t for t in ar.toc}
for name in toc:
    norm = name.replace("\\", "/")
    if norm.startswith("web/"):
        dst = os.path.join(outdir, norm.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(ar.extract(name))
        print("extracted", norm, os.path.getsize(dst))
