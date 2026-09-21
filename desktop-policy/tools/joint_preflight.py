# -*- coding: utf-8 -*-
"""desktop-policy 联调预检（只读零副作用）。

对真实平台端点做契约形状校验：连通性 → policy 拉取（revision/mi）→
可选壁纸下载校验（--download id sha256）。不发 report、不应用策略、
不改终端状态；发现形状偏差即打印差异供双端核对。

运行：python tools/joint_preflight.py [--download ID SHA256]
退出码：0=全绿；1=有失败项。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 进程内 uplink 直取（BRG-064）需要 workspace 根在 path 上（uplink.py 所在）
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

import desktop_policy as dp  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name +
          ("" if cond else "  | " + str(detail)[:180]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", nargs=2, metavar=("ID", "SHA256"),
                    default=None)
    args = ap.parse_args()

    t = dp.PlatformTransport()
    base, token, tid = t._cfg()
    check("uplink 配置就绪（server_url/token/terminal_id）",
          bool(base and token and tid),
          "base=%s tid=%s token=%s" % (bool(base), tid or "-", bool(token)))
    if not (base and token and tid):
        return 1
    print("  目标平台: %s  终端: %s" % (base, tid))

    monitors = dp.enum_monitors()
    print("== 1. policy 拉取（revision=-1 首拉）==")
    try:
        resp = t.fetch_policy(-1, dp.monitors_brief(monitors))
    except Exception as exc:
        check("policy 拉取连通", False, repr(exc))
        return 1
    check("policy 拉取连通（HTTP+JSON）", isinstance(resp, dict), resp)
    if not isinstance(resp, dict):
        return 1
    if resp.get("unchanged"):
        check("policy·unchanged 形状", "revision" in resp, resp)
        print("  （服务端无已发布策略：先在 console 发布一条策略后重跑）")
    else:
        check("policy·revision 整数", isinstance(resp.get("revision"), int),
              resp.get("revision"))
        pol = resp.get("policies") or {}
        check("policy·policies 为对象", isinstance(pol, dict), type(pol))
        for key in ("desktop_wallpaper", "lock_screen", "power_plan",
                    "idle_lock"):
            item = pol.get(key)
            if item is None:
                continue
            check("policy·%s.enabled 存在" % key,
                  isinstance(item, dict) and "enabled" in item, item)
        wp = pol.get("desktop_wallpaper") or {}
        for m in wp.get("per_monitor") or []:
            check("policy·per_monitor item 整数 id+match",
                  isinstance(m.get("wallpaper_id"), int)
                  and m.get("match") in ("exact", "aspect_higher_res",
                                         "default_fallback"), m)
            break
        check("policy·checksum 字段（如有）为 64 位 hex",
              all(len(str(m.get("checksum") or "")) in (0, 64)
                  for m in wp.get("per_monitor") or []))
    print("== 2. report 端点连通（占位 ping，不入正式状态机——revision=-1 不匹配任何发布）==")
    # 不发 report：契约状态机按 (revision, terminal_id) 落库，占位会造成脏行。
    check("report·跳过真实发送（保持零副作用）", True)

    if args.download:
        wid, sha = args.download
        dest = dp.data_dir() + os.sep + "cache" + os.sep + str(wid)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            t.download_wallpaper(wid, dest, sha)
            check("wallpaper·下载+sha256 校验", True, dest)
        except Exception as exc:
            check("wallpaper·下载+sha256 校验", False, repr(exc))

    print("\n===== 预检结果: %d/%d PASS =====" % (len(PASS), len(PASS) + len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
