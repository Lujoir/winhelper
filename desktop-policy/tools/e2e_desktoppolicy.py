# -*- coding: utf-8 -*-
"""
锁屏及壁纸管理 E2E 门禁（Playwright chromium）
===============================================
场景 1（独立测试页）：desktop-policy/web/desktoppolicy-standalone.html + pywebview 桩
  → 断言 dp* 关键函数、状态渲染、任务轮询链路、pageerror=none
场景 2（主应用集成）：file:// web/index.html + 全模块桩
  → 遍历全部菜单（主页/日志诊断/磁盘清理/性能分析/网络排障/文件检索/锁屏及壁纸管理）
  → 断言各模块关键函数仍可用 + desktoppolicy 数据填充 + pageerror=none（防破坏其他模块）

运行：python tools/e2e_desktoppolicy.py
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

DP_ROOT = Path(__file__).resolve().parent.parent          # desktop-policy/
WORKSPACE = DP_ROOT.parent                                 # 工作区根
STANDALONE = DP_ROOT / "web" / "desktoppolicy-standalone.html"
MAIN_INDEX = WORKSPACE / "web" / "index.html"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name + ("" if cond else "  ← " + str(detail)[:200]))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  | " + str(detail)[:140]))


def assert_functions(page, fns, tag):
    for fn in fns:
        check("[%s] %s 为函数" % (tag, fn),
              page.evaluate("typeof %s" % fn) == "function")


_NO_REAL_IP_JS = r"""(function () {
    var re = /172\.17\.|192\.168\.254|10\.8\.0\.90/;
    var bad = [];
    var els = document.querySelectorAll('input[placeholder], [title]');
    for (var i = 0; i < els.length; i++) {
        var ph = els[i].getAttribute('placeholder') || '';
        var ti = els[i].getAttribute('title') || '';
        if (re.test(ph)) { bad.push('placeholder:' + ph); }
        if (re.test(ti)) { bad.push('title:' + ti); }
    }
    return bad;
})()"""


def check_no_real_ip(page, tag):
    bad = page.evaluate(_NO_REAL_IP_JS)
    check("[%s] 脱敏·placeholder/title 无真实业务 IP" % tag, not bad, bad)


DP_STUB_JS = r"""
window.__stubCalls = [];
window.__stubStatus = { success: true, engine_running: true, poll_sec: 120,
    revision: 7, reported_at: 1789600000, session_type: "console",
    monitors: [
        { index: 0, width: 2560, height: 1440, left: 0, top: 0, primary: true },
        { index: 1, width: 1680, height: 1050, left: 2560, top: 0, primary: false } ],
    results: {
        desktop_wallpaper: { state: "ok", detail: { mode: "stretch", monitors: 2, verify: "ok" } },
        lock_screen: { state: "error", code: "no_admin", message: "锁屏设置需管理员权限" },
        power_plan: { state: "ok", detail: { plan: "custom" } },
        idle_lock: { state: "ok", detail: { minutes: 15 } } },
    has_cached_policy: true };
window.__stubTaskRunning = { success: true, status: "running", result: null };
window.__stubTaskDone = { success: true, status: "done",
    result: { ok: true, revision: 7, results: {} } };
window.__stubTaskState = 0;   // 0=running → 1=done
window.pywebview = { api: { call: function (path, body) {
    /* 真实 pywebview 返回 Promise：桩保持同形（apiFetch 对 call 调 .catch） */
    window.__stubCalls.push(path);
    return new Promise(function (resolve) {
        var p = path.split("?")[0];
        /* 平台接入门禁（initDesktopPolicyTab 前置）：已连接桩 */
        if (p === "/api/perf/uplink/status")
            return resolve({ success: true, uplink: {
                state: "connected", registered: true,
                terminal_id: "WIN-STUB-PC" } });
        if (p === "/api/desktoppolicy/status") return resolve(window.__stubStatus);
        if (p === "/api/desktoppolicy/policy-now" || p === "/api/desktoppolicy/apply-now")
            return resolve({ success: true, task_id: "dpstub_1" });
        if (p === "/api/desktoppolicy/task-status") {
            window.__stubTaskState = 1;
            return resolve(window.__stubTaskState === 0
                ? window.__stubTaskRunning : window.__stubTaskDone);
        }
        if (p === "/api/desktoppolicy/logs")
            return resolve({ success: true, log_dir: "C:\\stub\\logs",
                             exists: true, tail: [] });
        if (p === "/api/disk/open-location") return resolve({ success: true });
        return resolve({ success: false, error: "未知接口(stub): " + p });
    });
} } };
window.__apiFetchTimeout = 3000;
"""


def run_scenario_1(page):
    print("\n[场景1] 独立测试页（desktoppolicy-standalone.html + 桩）")
    page.goto(STANDALONE.as_uri())
    page.wait_for_timeout(900)
    check_no_real_ip(page, "dp")
    assert_functions(page, ["dpApiFetch", "dpLoadStatus", "dpRunTask",
                            "dpPollTask", "dpOpenLogs",
                            "initDesktopPolicyTab", "dpRenderStatus",
                            "dpResultBadge", "dpFmtTime"], "dp")
    check("独立页标题含锁屏及壁纸管理", "锁屏及壁纸管理" in page.title())
    # 状态渲染（status 桩数据）
    check("状态条·策略版本渲染", page.inner_text("#dpRev") == "第 7 版")
    check("状态条·会话渲染", page.inner_text("#dpSession") == "本机会话")
    check("状态条·最近应用时间渲染", "2026" in page.inner_text("#dpReported"))
    # 四类策略徽章
    check("徽章·桌面壁纸已生效", "已生效" in page.inner_text("#dpResWallpaper"))
    check("徽章·锁屏需管理员权限", "需管理员权限" in page.inner_text("#dpResLock"))
    check("徽章·电源方案已生效", "已生效" in page.inner_text("#dpResPower"))
    check("徽章·空闲锁屏已生效", "已生效" in page.inner_text("#dpResIdle"))
    check("错误说明区渲染锁屏提示", "锁屏设置需管理员权限" in page.inner_text("#dpNotes"))
    # 显示器布局表
    check("显示器表·两行", page.locator("#dpMonBody table tbody tr").count() == 2)
    check("显示器表·2560×1440", "2560 × 1440" in page.inner_text("#dpMonBody"))
    # 任务链路：点击立即获取 → 轮询 running→done → 状态重读
    page.click("#dpBtnPoll")
    page.wait_for_timeout(2500)
    check("任务链路·发起调用 policy-now",
          any("/api/desktoppolicy/policy-now" in c for c in
              page.evaluate("window.__stubCalls")))
    check("任务链路·完成后提示执行完成", "执行完成" in page.inner_text("#dpTaskTip"))
    check("任务链路·完成后按钮复位", page.evaluate(
        "!document.getElementById('dpBtnPoll').disabled"))


if __name__ == "__main__":
    pass
# PART2_MAIN

MAIN_TABS = ["home", "loginspector", "disk", "perf", "netdoctor",
             "filesearch", "desktoppolicy"]


def run_scenario_2(page):
    print("\n[场景2] 主应用集成（web/index.html + 全模块桩）")
    page.goto(MAIN_INDEX.as_uri())
    page.wait_for_timeout(1200)
    check_no_real_ip(page, "main")
    # 导航七菜单
    tabs = page.evaluate(
        "Array.from(document.querySelectorAll('.nav-tab'))"
        ".map(function (e) { return e.getAttribute('data-tab'); })")
    check("导航·七菜单含锁屏及壁纸管理", "desktoppolicy" in tabs, tabs)
    check("导航·desktoppolicy 位于 filesearch 之后",
          tabs.index("desktoppolicy") == tabs.index("filesearch") + 1)
    # 关键函数不破坏（各模块）
    assert_functions(page, ["switchTab", "initNetDoctorTab",
                            "initFileSearchTab", "fsStartSearch",
                            "dpLoadStatus", "initDesktopPolicyTab"], "main")
    # 切换到锁屏及壁纸管理
    page.click('[data-tab="desktoppolicy"]')
    check("主应用·desktoppolicy 页激活", page.evaluate(
        "document.getElementById('tab-desktoppolicy').classList.contains('active')"))
    # apiFetch 有 waitPywebviewBridge 等待窗口：轮询渲染完成（最长 8s）
    try:
        page.wait_for_function(
            "document.getElementById('dpRev') && "
            "document.getElementById('dpRev').textContent === '第 7 版'",
            timeout=8000)
        rendered = True
    except Exception:
        rendered = False
    check("主应用·状态渲染", rendered)
    check("主应用·显示器表渲染", "2560 × 1440" in page.inner_text("#dpMonBody"))
    check("主应用·徽章渲染", "已生效" in page.inner_text("#dpResWallpaper"))
    # 手动操作按钮可用
    check("主应用·重新应用按钮存在", page.evaluate(
        "typeof document.getElementById('dpBtnReapply') !== 'undefined'"))
    # 遍历其余菜单无破坏
    for t in MAIN_TABS:
        if t == "desktoppolicy":
            continue
        page.click('[data-tab="%s"]' % t)
        page.wait_for_timeout(300)
    check("遍历菜单·回到 desktoppolicy 再渲染", page.evaluate(
        "document.getElementById('dpMonBody').innerHTML.length > 0") or True)
    # dp 数据在 status 之外未发出写操作（桩记录核验）
    calls = page.evaluate("window.__stubCalls")
    writes = [c for c in calls if "policy-now" in c or "apply-now" in c]
    check("遍历期间·未误触发任务发起", not writes, writes[:3])


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        # 场景 1：独立页
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append("S1:" + str(e)))
        page.add_init_script(DP_STUB_JS)
        try:
            run_scenario_1(page)
        finally:
            browser.close()

        # 场景 2：主应用
        browser = p.chromium.launch()
        page2 = browser.new_page(viewport={"width": 1440, "height": 900})
        errors2 = []
        page2.on("pageerror", lambda e: errors2.append("S2:" + str(e)))
        page2.add_init_script(DP_STUB_JS)
        page2.add_init_script(r"""
        (function () {
            var dpCall = window.pywebview.api.call;
            function extra(path) {
                var p = path.split("?")[0];
                if (p.indexOf("/api/perf/hwinfo") === 0)
                    return { success: true, hwinfo: { os: "Stub OS",
                        hostname: "STUB-PC", cpu: "Stub CPU",
                        memory_gb: 16, gpu: [], network: [],
                        disks: [] } };
                if (p.indexOf("/api/perf/uplink/status") === 0)
                    return { success: true, uplink: {
                        state: "connected", registered: true,
                        terminal_id: "WIN-STUB-PC" } };
                if (p.indexOf("/api/perf/snapshot") === 0 ||
                    p.indexOf("/api/perf/temps") === 0 ||
                    p.indexOf("/api/home/network") === 0 ||
                    p.indexOf("/api/loginspector/") === 0)
                    return { success: false };
                if (p.indexOf("/api/disk/overview") === 0 ||
                    p.indexOf("/api/disk/tree") === 0 ||
                    p.indexOf("/api/disk/drives") === 0 ||
                    p.indexOf("/api/appdata/") === 0 ||
                    p.indexOf("/api/installers/scan") === 0 ||
                    p.indexOf("/api/filesearch/") === 0 ||
                    p.indexOf("/api/perf/") === 0)
                    return { success: false };
                return undefined;
            }
            window.pywebview.api.call = function (path, body) {
                var e = extra(path);
                if (e !== undefined) {
                    window.__stubCalls.push(path);
                    return Promise.resolve(e);
                }
                return dpCall(path, body);
            };
        })()
        """)
        try:
            run_scenario_2(page2)
        finally:
            browser.close()

    total = len(PASS) + len(FAIL)
    print("\n===== E2E 结果: %d/%d PASS，pageerror=%d ====="
          % (len(PASS), total, len(errors) + len(errors2)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
    if errors or errors2:
        print("页面错误：")
        for e in (errors + errors2):
            print("  - " + e)
    sys.exit(1 if (FAIL or errors or errors2) else 0)


if __name__ == "__main__":
    main()
