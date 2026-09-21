# -*- coding: utf-8 -*-
"""
主应用磁盘清理页 全断诊断 E2E（Playwright chromium）
======================================================
背景（2026-09-11）：v8.x exe 用户实测磁盘清理页全断——
  C 盘容量概览全「--」、垃圾分析停在初始态、清理按钮灰、安装包表空，
  骨架正常渲染但无任何数据。本脚本在真实浏览器中复现/排除前端运行时层。

桩覆盖磁盘域全部路由（overview/drives/scan/scan-status/installers/appdata/tree），
扫描任务直接返回 done+result，允许轮询链完整走完。

场景：
  1. 默认加载（file:// web/index.html + pywebview 桩）→ 关键函数 typeof=function
  2. 主页 → 点击磁盘清理（用户真实路径）→ initDiskTab 执行证据 + 数据填充
  3. pageerror/console.error 全程为零

运行：python disk-cleaner/tools/e2e_mainapp_disk.py
退出码：0=通过 1=失败
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent.parent   # 工作区根
# 可选参数：指定 index.html 路径（时间旅行 E2E 用，默认当前工作区）
MAIN_INDEX = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "web" / "index.html"

STUB_JS = r"""
window.__stubCalls = [];
window.__stubOverview = { success: true,
    total: 512110190592, used: 329853488332, free: 182256702260,
    total_text: "476.9 GB", used_text: "307.2 GB", free_text: "169.7 GB", used_percent: 64.4 };
window.__stubDrives = { success: true, drives: [
    { drive: "C:\\", total_text: "476.9 GB", used_percent: 64.4, free_text: "169.7 GB" } ] };
window.__stubAdDrives = { success: true, drives: [
    { drive: "D:\\", total_text: "931.5 GB", used_percent: 35.2, free_text: "603.5 GB" } ] };
window.__stubStatus = {
    "stub-junk-1": { success: true, task: { status: "done", progress: { done: 245, total: 245 },
        result: { total_size_text: "2.3 GB", categories: [
            { key: "recycle_bin", name: "回收站", desc: "回收站文件", risk: "safe", count: 15,
              size: 104857600, size_text: "100.0 MB", paths: [], default_checked: true },
            { key: "temp", name: "临时文件", desc: "系统与用户临时目录", risk: "safe", count: 230,
              size: 524288000, size_text: "500.0 MB", paths: ["C:\\Windows\\Temp"], default_checked: true }
        ] } } },
    "stub-tree-1": { success: true, task: { status: "done", progress: { scanned_files: 123456, dirs: 23456, elapsed: 12 },
        result: { success: true, root_path: "C:\\", total_size_text: "200.0 GB",
                  total_files: 123456, total_dirs: 23456, elapsed: 12,
                  skipped_dirs: 0, skipped_samples: [], recommendations: [], large_files: [] } } },
    "stub-ins-1": { success: true, task: { status: "done", progress: {},
        result: { success: true, total: 2, browser_dirs: [], items: [
            { name: "StubSetup.exe", path: "C:\\Users\\Stub\\Downloads\\StubSetup.exe",
              size: 104857600, size_text: "100.0 MB", auto: true, verdict: "installer", from: "download" },
            { name: "stubtool-1.2.3-setup.exe", path: "D:\\stubtool-1.2.3-setup.exe",
              size: 52428800, size_text: "50.0 MB", auto: false, verdict: "installer", from: "scan" }
        ] } } },
    "stub-ad-1": { success: true, task: { status: "done", progress: {},
        result: { success: true, total_size_text: "3.4 GB", zones: [] } } },
    "stub-clean-1": { success: true, task: { status: "done", progress: {},
        result: { success: true, total_freed_text: "1.2 GB", total_deleted: 245, total_failed: 0 } } }
};
window.__stubTree = { success: true, rel: ".",
    children: [
        { name: "Windows", path: "C:\\Windows", size: 53687091200, size_text: "50.0 GB",
          files: 110000, dirs: 5000, is_dir: true },
        { name: "Users", path: "C:\\Users", size: 32212254720, size_text: "30.0 GB",
          files: 80000, dirs: 3000, is_dir: true }
    ] };
window.__stubHwinfo = { success: true, hwinfo: {
    source: "cim", hostname: "STUB-PC",
    os: { caption: "Stub Windows 11 Pro", version: "10.0.26200", build: "26200", text: "Stub Windows 11 Pro" },
    cpu: { name: "Stub CPU i9-13900K", cores: 24, logical: 32, max_mhz: 5400, cur_mhz: 3500 },
    memory: { total: 34359738368, modules: [] },
    disks: [], gpu: [] } };
window.__stubNetwork = { success: true, source: "powershell", adapters: [] };
window.pywebview = { api: {
    call: function (path) {
        window.__stubCalls.push(path);
        return new Promise(function (resolve) {
            setTimeout(function () {
                if (path.indexOf("/api/disk/overview") === 0) return resolve(window.__stubOverview);
                if (path.indexOf("/api/disk/drives") === 0) return resolve(window.__stubDrives);
                if (path.indexOf("/api/disk/scan?type=junk") === 0)
                    return resolve({ success: true, scan_id: "stub-junk-1" });
                if (path.indexOf("/api/disk/scan?type=tree") === 0)
                    return resolve({ success: true, scan_id: "stub-tree-1" });
                if (path.indexOf("/api/installers/scan") === 0)
                    return resolve({ success: true, scan_id: "stub-ins-1" });
                if (path.indexOf("/api/disk/cleanup") === 0)
                    return resolve({ success: true, job_id: "stub-clean-1" });
                if (path.indexOf("/api/appdata/scan") === 0)
                    return resolve({ success: true, scan_id: "stub-ad-1" });
                if (path.indexOf("/api/appdata/drives") === 0) return resolve(window.__stubAdDrives);
                if (path.indexOf("/api/disk/scan-status") === 0) {
                    var m = path.match(/scan_id=([^&]+)/);
                    var key = m ? decodeURIComponent(m[1]) : "";
                    return resolve(window.__stubStatus[key] ||
                        { success: true, task: { status: "done", progress: {}, result: null } });
                }
                if (path.indexOf("/api/disk/tree") === 0) return resolve(window.__stubTree);
                return resolve({ success: true });
            }, 5);
        });
    } } };
"""

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name + ("" if cond else "  ← " + str(detail)[:160]))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  | " + str(detail)[:120]))


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors, console_errs, native_dialogs = [], [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: console_errs.append("[%s] %s" % (m.type, m.text))
                if m.type == "error" else None)
        page.on("dialog", lambda d: (native_dialogs.append(d.type), d.dismiss()))
        page.add_init_script(STUB_JS)

        # 场景1：默认加载
        print("\n[场景1] 默认加载")
        page.goto(MAIN_INDEX.as_uri())
        page.wait_for_timeout(1500)
        for fn in ("initDiskTab", "initAppdataTab", "loadDiskOverview", "startJunkScan",
                   "scanInstallers", "startLargeScan", "renderTreemap", "loadTreeDrives"):
            check("%s typeof=function" % fn, page.evaluate("typeof %s" % fn) == "function")

        # 场景2：主页 → 磁盘清理（用户真实路径）
        print("\n[场景2] 主页 → 磁盘清理")
        page.click('button[data-tab="disk"]')
        page.wait_for_timeout(2500)   # 轮询 700-800ms 一轮，留足两轮

        check("tab-disk 激活", page.evaluate(
            "document.getElementById('tab-disk').classList.contains('active')"))
        calls = page.evaluate("window.__stubCalls.join(' | ')")
        check("initDiskTab 执行证据: /api/disk/overview 已调用",
              "/api/disk/overview" in calls, calls[:300])
        check("initDiskTab 执行证据: /api/disk/drives 已调用", "/api/disk/drives" in calls)
        check("initDiskTab 执行证据: junk 扫描已启动", "/api/disk/scan?type=junk" in calls)
        check("initDiskTab 执行证据: 安装包扫描已启动", "/api/installers/scan" in calls)
        check("initDiskTab 执行证据: 树扫描已启动", "/api/disk/scan?type=tree" in calls)

        # —— 用户症状逐项断言 ——
        check("概览·总容量非「--」", page.inner_text("#diskTotal") == "476.9 GB",
              page.inner_text("#diskTotal"))
        check("概览·已用非「--」", page.inner_text("#diskUsed") == "307.2 GB",
              page.inner_text("#diskUsed"))
        check("概览·可用非「--」", page.inner_text("#diskFree") == "169.7 GB",
              page.inner_text("#diskFree"))
        check("概览·使用率", "64.4%" in page.inner_text("#diskUsedPercent"),
              page.inner_text("#diskUsedPercent"))
        check("垃圾分析·分类卡已渲染", page.locator("#junkGrid .junk-card").count() >= 1,
              page.inner_text("#junkGrid")[:120])
        check("垃圾分析·清理按钮已解禁", page.evaluate(
            "document.getElementById('junkCleanSelBtn').disabled === false"))
        check("安装包·表已渲染（含 StubSetup.exe）", "StubSetup.exe" in page.content())
        check("树扫描·目录行已渲染（Windows）", "Windows" in page.inner_text("#treeBody"),
              page.inner_text("#treeBody")[:120])
        check("仪表盘·treemap 色块已渲染", page.locator("#treemap .tm-block").count() >= 1,
              page.inner_text("#treemap")[:120])
        check("迁移·目标盘已加载（D:\\）",
              "D:\\" in page.evaluate("document.getElementById('adDriveSelect').innerHTML"))

        # 场景3：uiConfirm 门禁（废除原生 confirm/alert，2026-09-14 第二批派单）
        print("\n[场景3] uiConfirm 门禁")
        check("uiConfirm 组件存在", page.evaluate("typeof uiConfirm") == "function")

        # 3a. 清理主流程：点击「清理勾选项」→ uiConfirm 弹出（居中/danger/文案净化）→ 确定 → cleanup 调用
        page.click("#junkCleanSelBtn")
        page.wait_for_selector(".ui-confirm-mask", timeout=3000)
        box = page.locator(".ui-confirm-card").bounding_box()
        vp = page.viewport_size
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        check("弹窗水平居中（中心≈窗体中心）", abs(cx - vp["width"] / 2) <= 2,
              "card_cx=%.1f vp_cx=%.1f" % (cx, vp["width"] / 2))
        check("弹窗垂直居中（中心≈窗体中心）", abs(cy - vp["height"] / 2) <= 2,
              "card_cy=%.1f vp_cy=%.1f" % (cy, vp["height"] / 2))
        check("danger 红键（破坏性操作）", page.locator(".ui-confirm-ok-danger").count() == 1)
        card_txt = page.inner_text(".ui-confirm-card")
        check("弹窗标题语义化（磁盘清理确认）", "磁盘清理确认" in card_txt, card_txt[:80])
        check("弹窗文案零来源信息（127.0.0.1）", "127.0.0.1" not in card_txt)
        check("弹窗文案零端口（:数字）", not __import__("re").search(r":\d{4,5}\b", card_txt),
              card_txt[:80])
        page.click(".ui-confirm-ok-danger")
        page.wait_for_timeout(600)
        check("确定后遮罩关闭", page.locator(".ui-confirm-mask").count() == 0)
        calls2 = page.evaluate("window.__stubCalls.join(' | ')")
        check("清理主流程走通（/api/disk/cleanup 已调用）", "/api/disk/cleanup" in calls2, calls2[-200:])

        # 3b. 取消路径（ESC）：appdata 删除确认 → ESC → 不执行删除
        # 注意：adDeleteFiles 是 async，evaluate 会 await 其 Promise（等弹窗关闭）造成死锁，
        # 必须用同步 IIFE fire-and-forget 触发
        page.evaluate("(() => { adDeleteFiles(['C:\\\\stub\\\\f1'], 1, '1 MB'); })()")
        page.wait_for_selector(".ui-confirm-mask", timeout=3000)
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        check("ESC 取消后遮罩关闭", page.locator(".ui-confirm-mask").count() == 0)
        calls3 = page.evaluate("window.__stubCalls.join(' | ')")
        check("取消后未执行删除（无 /api/appdata/delete）", "/api/appdata/delete" not in calls3)

        # 3c. 单按钮提示模式（alert 语义）：cancelText="" → 唯一按钮 → 确定 resolve true
        r = page.evaluate(
            "(async () => { const p = uiConfirm({title:'提示', message:'单按钮模式', cancelText:''});"
            " await new Promise(res => setTimeout(res, 150));"
            " const btns = document.querySelectorAll('.ui-confirm-card .ui-confirm-btn');"
            " const single = btns.length === 1;"
            " btns[btns.length - 1].click();"
            " return { single: single, resolved: await p }; })()")
        check("alert 语义单按钮模式 + resolve(true)", r["single"] and r["resolved"] is True, str(r))

        # 3d. 全程零原生对话框
        check("原生 dialog 计数 = 0", len(native_dialogs) == 0, native_dialogs)

        browser.close()

    print("\n[JS errors] pageerror=%d console.error=%d native_dialog=%d"
          % (len(errors), len(console_errs), len(native_dialogs)))
    for e in errors:
        print("  PAGEERROR:", e[:250])
    for e in console_errs[:10]:
        print("  CONSOLE ERR:", e[:250])
    check("no-pageerror", len(errors) == 0, errors)
    check("no-console-error", len(console_errs) == 0, console_errs)

    total = len(PASS) + len(FAIL)
    print("\n===== RESULT: %d/%d passed =====" % (len(PASS), total))
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
        sys.exit(1)
    print("E2E DISK GATE: PASS")


if __name__ == "__main__":
    main()
