# -*- coding: utf-8 -*-
"""
winhelper.exe 内容核验（诊断工具）
==================================
解包 PyInstaller onefile exe（CArchive + PYZ），核验：
  1. exe 内嵌 web/ 文件与工作区磁盘文件 sha1 是否一致（打包版本漂移取证）
  2. PYZ 中 bridge 模块 import 的 handle_* 名字在 net_service/service 等模块中
     是否真实存在（顶层 import 断链取证——desktop.py 会因 ImportError 拒启）
运行：python disk-cleaner/tools/inspect_exe.py [exe路径]
退出码：0=核验通过 1=发现断链/漂移
"""

import hashlib
import marshal
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "winhelper.exe")

WEB_FILES = ["web/index.html", "web/app.js", "web/disk.js", "web/appdata.js",
             "web/netdoctor.js", "web/perf.js", "web/home.js", "web/loginspector.js",
             "web/style.css"]


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:12]


def collect_names(code, acc):
    for n in code.co_names:
        acc.add(n)
    for k in code.co_consts:
        if hasattr(k, "co_names"):
            collect_names(k, acc)


def main():
    print("exe:", EXE)
    ar = CArchiveReader(EXE)
    toc = ar.toc if isinstance(ar.toc, dict) else {t[0]: t for t in ar.toc}

    # ---- 1. web 资源版本核对 ----
    print("\n[1] web 资源对比（exe 内 vs 工作区磁盘）")
    web_mismatch = []
    for name in toc:
        norm = name.replace("\\", "/")
        if norm in WEB_FILES:
            data = ar.extract(name)
            disk = os.path.join(ROOT, norm)
            exe_h = sha1(data)
            ws_h = sha1(open(disk, "rb").read()) if os.path.exists(disk) else "MISSING"
            flag = "OK " if exe_h == ws_h else "DIFF"
            if exe_h != ws_h:
                web_mismatch.append(norm)
            print("  [%s] %-24s exe=%s workspace=%s" % (flag, norm, exe_h, ws_h))

    # ---- 2. PYZ 顶层 import 断链核对 ----
    print("\n[2] PYZ 模块 import 断链核对")
    pyz_name = next((n for n in toc if "PYZ" in n and n.endswith(".pyz")), None)
    if not pyz_name:
        print("  未找到 PYZ（目录模式？）")
        return 1
    pyz_path = os.path.join(os.environ.get("TEMP", "."), "_inspect_pyz.pyz")
    with open(pyz_path, "wb") as f:
        f.write(ar.extract(pyz_name))
    pz = ZlibArchiveReader(pyz_path)
    ptoc = pz.toc if isinstance(pz.toc, dict) else {t[0]: t for t in pz.toc}

    # 收集各模块顶层定义/引用的名字
    names = {}
    for mod in ("bridge", "net_service", "service", "perf_service",
                "log_service", "home_service", "uplink"):
        if mod not in ptoc:
            print("  [MISS] %-14s 不在 PYZ 中！" % mod)
            names[mod] = set()
            continue
        code = pz.extract(mod)
        if hasattr(code, "co_names"):
            acc = set()
            collect_names(code, acc)
            names[mod] = acc

    # bridge 顶层 from X import (...) 的名字集合 = co_names 与模块名共现，简化：
    # 对 bridge.co_names 中所有 handle_ 开头名字，逐一到对应模块的名字集合里查
    broken = []
    bnames = names.get("bridge", set())
    handles = sorted(n for n in bnames if n.startswith("handle_"))
    # 名字 -> 来源模块映射（按 handle_ 前缀路由到可能的宿主模块，逐一查）
    hosts = ["net_service", "service", "perf_service", "log_service",
             "home_service", "uplink"]
    for h in handles:
        where = [m for m in hosts if h in names.get(m, set())]
        if not where:
            broken.append(h)
            print("  [BROKEN] bridge imports %-36s → 未在任何宿主模块定义！" % h)
    if not broken:
        print("  [OK] bridge 全部 %d 个 handle_* import 在宿主模块中可解析" % len(handles))
    else:
        print("\n  ★ 结论：exe 内 bridge 存在 %d 个断链 import，"
              "desktop.py 启动时将 ImportError 拒启（弹窗退出）" % len(broken))

    print("\n===== RESULT: %s =====" %
          ("BROKEN" if broken else ("WEB-DRIFT: %d 文件" % len(web_mismatch))
           if web_mismatch else "PASS"))
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
