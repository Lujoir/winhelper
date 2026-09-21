# -*- coding: utf-8 -*-
"""真实 exe 网络层取证：reload 页面抓全部资源响应状态码 + performance 记录"""
import os
import subprocess
import sys
import time

import requests
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXE = os.path.join(ROOT, sys.argv[1] if len(sys.argv) > 1 else "dist_v3", "winhelper.exe")
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 9333

env = dict(os.environ)
env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--remote-debugging-port=%d" % PORT
proc = subprocess.Popen([EXE], env=env, cwd=ROOT)
print("launched PID=%s" % proc.pid)
try:
    page_url = None
    for i in range(40):
        time.sleep(1)
        try:
            targets = requests.get("http://127.0.0.1:%d/json" % PORT, timeout=2).json()
            pages = [t for t in targets if t.get("type") == "page"]
            if pages:
                page_url = pages[0].get("url")
                print("CDP attached: %s" % page_url)
                break
        except Exception:
            continue
    if not page_url:
        print("FAIL: no CDP")
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:%d" % PORT)
        page = next(pg for pg in browser.contexts[0].pages if pg.url.startswith("http"))
        print("page.url =", page.url)

        # 历史加载的资源记录（页面加载时）
        print("\n[performance entries]（首帧加载时已发生的请求）")
        entries = page.evaluate(
            "performance.getEntriesByType('resource').map(e => e.name + ' status=' + "
            "(e.responseStatus !== undefined ? e.responseStatus : '?') + ' size=' + e.transferSize)")
        for e in entries:
            print("  ", e)

        # reload 抓实时响应
        print("\n[network reload] 逐资源实时状态码")
        seen = []
        page.on("response", lambda r: seen.append((r.url, r.status)))
        page.reload()
        page.wait_for_timeout(4000)
        for url, status in seen:
            mark = "OK " if status == 200 else ">>> FAIL"
            print("  [%s] %s %s" % (mark, status, url[:100]))

        print("\n[reload 后函数态]")
        for fn in ("initDiskTab", "initHomeTab", "switchTab", "apiFetch", "initPerfTab"):
            print("  typeof %-16s = %s" % (fn, page.evaluate("typeof " + fn)))
        browser.close()
finally:
    try:
        proc.kill()
        print("[cleanup] killed")
    except Exception:
        pass
