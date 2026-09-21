# -*- coding: utf-8 -*-
"""
真实 exe 活体取证（WebView2 CDP 直连）
======================================
启动 dist_v3/winhelper.exe（独立实例，不干扰用户正在运行的 dist_new 实例），
通过 WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS 开 CDP 端口，Playwright 直连：
  1. 真实页面 pageerror/console 现场抓取
  2. 直接调用真实 IPC：await window.pywebview.api.call('/api/disk/overview')
     —— 返回 {success:false,error} 即抓到后端真凶
  3. 点击磁盘清理 tab → 真实数据填充断言
退出时杀掉本脚本启动的实例。
运行：python disk-cleaner/tools/e2e_live_exe.py
"""

import json
import os
import subprocess
import sys
import time

import requests
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXE = os.path.join(ROOT, "dist_v3", "winhelper.exe")
PORT = 9333

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name + ("" if cond else "  ← " + str(detail)[:200]))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  | " + str(detail)[:160]))


def main():
    env = dict(os.environ)
    env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--remote-debugging-port=%d" % PORT
    proc = subprocess.Popen([EXE], env=env, cwd=ROOT)
    print("launched PID=%s %s" % (proc.pid, EXE))
    try:
        ws = None
        for i in range(40):
            time.sleep(1)
            try:
                r = requests.get("http://127.0.0.1:%d/json" % PORT, timeout=2)
                targets = r.json()
                pages = [t for t in targets if t.get("type") == "page"
                         and "winhelper" not in t.get("url", "devtools")]
                if pages:
                    ws = pages[0]["webSocketDebuggerUrl"]
                    print("CDP attached after %ds: %s" % (i + 1, pages[0].get("url", "")[:60]))
                    break
            except Exception:
                continue
        if not ws:
            print("FAIL: CDP endpoint not reachable on :%d" % PORT)
            return 1

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp("http://127.0.0.1:%d" % PORT)
            ctx = browser.contexts[0]
            page = next(pg for pg in ctx.pages if pg.url.startswith("file") or "index.html" in pg.url)
            errors, cerrs = [], []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: cerrs.append("[%s] %s" % (m.type, m.text))
                    if m.type == "error" else None)
            page.wait_for_timeout(2000)

            print("\n[1] 真实 exe 页面现场")
            check("页面已加载", "index.html" in page.url, page.url[:80])
            check("initDiskTab typeof=function",
                  page.evaluate("typeof initDiskTab") == "function")

            print("\n[2] 真实 IPC 直测（ApiBridge.call）")
            for route in ("/api/disk/overview", "/api/disk/drives",
                          "/api/disk/scan?type=junk", "/api/installers/scan?scope=all-drives"):
                res = page.evaluate("async () => await window.pywebview.api.call(%s)" % json.dumps(route))
                ok = isinstance(res, dict) and res.get("success")
                check("IPC %s → success" % route.split("?")[0], ok,
                      json.dumps(res, ensure_ascii=False)[:200] if res else "None")

            print("\n[3] 真实 UI：点击磁盘清理")
            page.click('button[data-tab="disk"]')
            page.wait_for_timeout(4000)
            check("tab-disk 激活", page.evaluate(
                "document.getElementById('tab-disk').classList.contains('active')"))
            total_txt = page.inner_text("#diskTotal")
            check("C盘总容量非「--」", total_txt not in ("", "--"), total_txt)
            junk_cards = page.locator("#junkGrid .junk-card").count()
            check("垃圾分类卡渲染", junk_cards >= 1, "count=%d" % junk_cards)

            print("\n[4] 错误现场")
            print("pageerror=%d console.error=%d" % (len(errors), len(cerrs)))
            for e in errors[:8]:
                print("  PAGEERROR:", e[:250])
            for e in cerrs[:8]:
                print("  CONSOLE ERR:", e[:250])

            browser.close()
    finally:
        try:
            proc.kill()
            print("\n[cleanup] killed PID=%s" % proc.pid)
        except Exception:
            pass

    total = len(PASS) + len(FAIL)
    print("\n===== RESULT: %d/%d passed =====" % (len(PASS), total))
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
        return 1
    print("LIVE EXE GATE: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
