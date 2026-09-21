# -*- coding: utf-8 -*-
"""
E2E 仪表盘验证（标准验证手段，见 docs/DECISIONS.md ADR-010）
=============================================================
esprima 只能查语法，抓不住运行时错误（ReferenceError / undefined 属性
读取等）。前端 JS 改动必须用本脚本做真实流程验证后再提交。

前置依赖:
    pip install playwright
    playwright install chromium

用法:
    方式一（服务已手动启动）:
        python app.py
        python tools/e2e_dashboard.py
    方式二（脚本自动起停服务）:
        python tools/e2e_dashboard.py --with-server

验证点:
    [1/5] 关键函数 typeof=function（SyntaxError 会使整个 JS 不执行——2026-09-05 主应用事故）
    [2/5] 进入页面 → initDiskTab 自动启动C盘目录树扫描（仪表盘内嵌 spinner 出现）
    [3/5] 实时进度文本刷新（当前目录/已扫文件数/耗时）
    [4/5] 扫描完成后 #treemap .tm-block 色块自动渲染（>=10 块）
    [5/5] 全程 pageerror 事件必须为 none
配合门禁: tools/js_decl_check.py（esprima AST 重复声明扫描，见 ADR-012）

退出码: 0=通过, 1=失败
注意: 每次执行会对 C 盘做一次全量目录树扫描（约 1-3 分钟）。
"""
import argparse
import os
import subprocess
import sys
import time
import urllib.request

DEFAULT_URL = "http://127.0.0.1:5010/"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def wait_server(url, timeout=30):
    """轮询服务就绪（/api/disk/overview 200）"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/api/disk/overview", timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def run_checks(base_url, scan_timeout_ms):
    from playwright.sync_api import sync_playwright

    ok = True
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base_url, wait_until="domcontentloaded", timeout=30000)

        # [1/5] 关键函数定义检查（重复 const 等语法级错误会使整个 JS 文件不执行）
        fns = ["initDiskTab", "startLargeScan", "pollLargeScan", "startTreeForTreemap",
               "renderTreemap", "squarifyLayout", "tmShowError", "startJunkScan",
               "cleanSelected", "scanInstallers", "startAppdataScan", "adMigrateSelected"]
        missing = page.evaluate(
            "(names) => names.filter(n => typeof window[n] !== 'function')", fns)
        print("[1/5] KEY_FUNCTIONS:",
              "all defined" if not missing else "MISSING: " + ", ".join(missing))
        ok = ok and not missing

        # [2/5] 自动扫描: spinner 应在 initDiskTab 自动触发后出现
        page.wait_for_selector("#tmSpinText", timeout=15000)
        print("[2/5] SPINNER:", page.inner_text("#tmSpinText"))
        start_hidden = page.eval_on_selector("#tmStartBtn", "el => el.style.display") == "none"
        running_flag = page.evaluate("window._treeRunning")
        print("      TM_START_HIDDEN:", start_hidden, "| _treeRunning:", running_flag)
        ok = ok and start_hidden and (running_flag is True)

        # [3/5] 实时进度刷新
        time.sleep(6)
        progress = page.inner_text("#tmSpinText")
        print("[3/5] PROGRESS:", progress)
        ok = ok and ("正在扫描" in progress)

        # [4/5] 扫描完成后色块自动渲染
        page.wait_for_selector("#treemap .tm-block", timeout=scan_timeout_ms)
        blocks = page.eval_on_selector_all("#treemap .tm-block", "els => els.length")
        spin_gone = page.eval_on_selector_all("#tmSpinText", "els => els.length") == 0
        done_flag = page.evaluate("window._treeDone")
        print("[4/5] BLOCKS:", blocks, "| SPINNER_GONE:", spin_gone, "| _treeDone:", done_flag)
        ok = ok and blocks >= 10 and spin_gone and (done_flag is True)

        # [5/5] 全程无 JS 运行时错误
        print("[5/5] PAGE_ERRORS:", errors if errors else "none")
        ok = ok and not errors

        browser.close()
    return ok


def stop_server(proc):
    """Windows 下 Flask debug reloader 是进程树，需 taskkill /T 清理"""
    if proc is None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True)
    else:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


def main():
    ap = argparse.ArgumentParser(description="Disk Cleaner 仪表盘 E2E 验证 (ADR-010)")
    ap.add_argument("--url", default=DEFAULT_URL, help="服务地址（默认 %(default)s）")
    ap.add_argument("--timeout", type=int, default=300,
                    help="扫描完成等待秒数（默认 %(default)s）")
    ap.add_argument("--with-server", action="store_true",
                    help="自动起停 app.py（默认要求服务已手动启动）")
    args = ap.parse_args()

    proc = None
    if args.with_server:
        proc = subprocess.Popen([sys.executable, "app.py"], cwd=PROJECT_ROOT)

    try:
        if not wait_server(args.url):
            print("服务不可达:", args.url)
            return 1
        ok = run_checks(args.url, args.timeout * 1000)
        print("E2E_DASHBOARD_" + ("OK" if ok else "FAILED"))
        return 0 if ok else 1
    finally:
        stop_server(proc)


if __name__ == "__main__":
    sys.exit(main())
