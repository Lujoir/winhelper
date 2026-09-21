#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""观枢终端平台控制台 · 首页卡片式主界面 E2E 门禁（home-console-dev）。

覆盖（PRD 门禁项）：
- 首页渲染：卡片网格 / 卡片注册机制（10 卡 = 3 参考卡 + 7 stub 占位）
- 导航第一项：首页为第一个菜单且默认激活
- 参考卡数据：终端监控 / 资产管理 / 客户端发布（复用既有端点聚合）
- 空态降级：单卡失败「数据不可用」不阻塞整页；整页失败全部降级
- 宽度自适应：1920 / 1440 / 1080 / 720 四档无横向滚动（STYLE.md 第 9 节）
- 样式基线抽查：卡头字号/颜色、键值行 dashed 底线（docs STYLE 基线）
- 弹窗规范：原生 dialog 出现即失败

前置：本地服务已运行（tools/smoke.py 灌过种子终端 WIN-SMOKE-TOKEN）。
环境变量：ETP_API_BASE（默认 http://127.0.0.1:18090）、
         ETP_CONSOLE_PASSWORD（默认 dev-console）。
"""
import json
import os
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright

BASE = os.environ.get("ETP_API_BASE", "http://127.0.0.1:18090").rstrip("/")
PASSWORD = os.environ.get("ETP_CONSOLE_PASSWORD", "dev-console")

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                          (" | " + detail) if detail and not cond else ""))


def _login_token():
    """直接 API 登录拿 token（用于 route 拦截场景绕过 UI 重登）。"""
    body = json.dumps({"username": "admin", "password": PASSWORD}).encode()
    req = urllib.request.Request(BASE + "/api/v1/console/login", data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())["token"]


def main():
    token = _login_token()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        native_dialogs = []
        page.on("dialog", lambda d: (native_dialogs.append(d.type),
                                     d.dismiss()))

        # ---------- A. 导航与框架 ----------
        print("[A] 导航与框架")
        page.goto(BASE + "/", wait_until="networkidle")
        overlay = page.locator("#loginOverlay")
        overlay.wait_for(state="visible", timeout=5000)
        page.fill("#pwd", PASSWORD)
        page.click("#loginOverlay button.primary")
        overlay.wait_for(state="hidden", timeout=5000)

        tabs = page.locator(".tabbar .tab")
        check("A1 home tab is first nav item",
              tabs.nth(0).get_attribute("data-page") == "home")
        check("A2 home tab active by default",
              "active" in (tabs.nth(0).get_attribute("class") or ""))
        check("A3 page-home active by default",
              "active" in (page.locator("#page-home")
                           .get_attribute("class") or ""))
        check("A4 monitor page not active by default",
              "active" not in (page.locator("#page-monitor")
                               .get_attribute("class") or ""))

        page.wait_for_selector("#homeGrid .home-card", timeout=5000)
        n_cards = page.locator("#homeGrid .home-card").count()
        check("A5 11 cards rendered", n_cards == 11, "got %d" % n_cards)
        ids = page.eval_on_selector_all(
            "#homeGrid .home-card", "els => els.map(e => e.dataset.card)")
        check("A6 card id registry complete",
              set(ids) == {"monitor", "assets", "asset-locate", "release",
                           "nettest", "ai", "dpol", "pc", "kb", "config",
                           "sysadmin"},
              "got %s" % ids)
        check("A7 no pageerror", not errors, "; ".join(errors[:2]))

        # ---------- B. 参考卡数据 ----------
        print("[B] 参考卡数据")
        mon = page.locator("#homeCard-monitor").inner_text()
        check("B1 monitor card total row", "终端总数" in mon, mon[:60])
        check("B2 monitor card online row", "在线" in mon)
        ast = page.locator("#homeCard-assets").inner_text()
        check("B3 assets card rows", "平台资产组" in ast and "安全分组" in ast)
        rel = page.locator("#homeCard-release").inner_text()
        check("B4 release card data-or-empty",
              ("当前版本" in rel) or ("尚未发布客户端版本" in rel))
        check("B5 monitor card action buttons",
              page.locator("#homeCard-monitor button").count() >= 1)

        # ---------- C. 空态 / 降级 ----------
        print("[C] 空态与降级")
        stub_pending = page.locator(
            '#homeCard-nettest .hc-empty').inner_text()
        check("C1 stub card pending state", "卡片接入中" in stub_pending)

        # 单卡失败 → 该卡「数据不可用」，其余卡不受影响
        def _break_monitor(route):
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"ok": True, "ts": 0, "cards": {
                              "monitor": {"ok": False, "error": "x"},
                              "assets": {"ok": True, "platform_groups": 2,
                                         "huorong_groups": 3,
                                         "other_total": 0,
                                         "other_online": 0},
                              "release": {"ok": True, "current_version": None,
                                          "total_releases": 0}}}))
        page.route("**/api/v1/console/home/summary", _break_monitor)
        page.click("text=刷新卡片")
        page.wait_for_timeout(400)
        check("C2 broken card degraded",
              "数据不可用" in page.locator("#homeCard-monitor").inner_text())
        check("C3 other cards unaffected",
              "平台资产组" in page.locator("#homeCard-assets").inner_text())
        page.unroute("**/api/v1/console/home/summary")

        # 整页失败 → 全部降级但卡片骨架仍在
        def _kill_summary(route):
            route.abort()
        page.route("**/api/v1/console/home/summary", _kill_summary)
        page.click("text=刷新卡片")
        page.wait_for_timeout(400)
        check("C4 full outage degrades all cards",
              page.locator("#homeGrid .hc-empty-err").count() >= 11)
        check("C5 page still alive (11 card shells)",
              page.locator("#homeGrid .home-card").count() == 11)
        page.unroute("**/api/v1/console/home/summary")
        page.click("text=刷新卡片")
        page.wait_for_timeout(400)

        # ---------- D. 交互 ----------
        print("[D] 交互")
        page.locator("#homeCard-monitor button",
                     has_text="进入终端监控").first.click()
        page.wait_for_timeout(200)
        check("D1 direct jump to monitor",
              "active" in (page.locator("#page-monitor")
                           .get_attribute("class") or ""))
        page.click('.tab[data-page="home"]')
        page.wait_for_timeout(400)
        check("D2 back to home cards alive",
              page.locator("#homeGrid .home-card").count() == 11
              and "终端总数" in page.locator("#homeCard-monitor").inner_text())

        # ---------- E. 宽度自适应（多档断言） ----------
        print("[E] 宽度自适应")
        for w, h in ((1920, 1080), (1440, 900), (1080, 800), (720, 900)):
            page.set_viewport_size({"width": w, "height": h})
            page.wait_for_timeout(250)
            no_hscroll = page.evaluate(
                "() => document.documentElement.scrollWidth"
                " <= document.documentElement.clientWidth + 1")
            grid_w = page.evaluate(
                "() => document.getElementById('homeGrid').getBoundingClientRect()"
                ".width")
            check("E w%d no horizontal scroll (grid %.0fpx)" % (w, grid_w),
                  no_hscroll and grid_w > 0)

        # ---------- F. 样式基线抽查 ----------
        print("[F] 样式基线")
        page.set_viewport_size({"width": 1440, "height": 900})
        hc_style = page.eval_on_selector(
            "#homeCard-monitor .hc-head h3",
            "e => { const s = getComputedStyle(e);"
            " return {fs: s.fontSize, fw: s.fontWeight, c: s.color}; }")
        check("F1 card head font 13.5px/600",
              hc_style["fs"] == "13.5px" and hc_style["fw"] == "600",
              json.dumps(hc_style))
        check("F2 card head color #4fc3f7",
              hc_style["c"] == "rgb(79, 195, 247)", hc_style["c"])
        row_style = page.eval_on_selector(
            "#homeCard-monitor .hc-row",
            "e => getComputedStyle(e).borderBottomStyle")
        check("F3 kv row dashed underline", row_style == "dashed", row_style)

        # ---------- G. 实现细节零残留 ----------
        print("[G] 实现细节零残留")
        grid_text = page.locator("#homeGrid").inner_text()
        bad_tokens = [t for t in (".js", "summary", "api/", "pending",
                                  "stub", "error") if t in grid_text]
        check("G1 no implementation details in UI copy",
              not bad_tokens, "found %s" % bad_tokens)
        check("G2 no native dialog", not native_dialogs,
              str(native_dialogs))
        check("G3 no pageerror at end", not errors, "; ".join(errors[:2]))

        browser.close()

    print("")
    print("e2e_home: %d passed, %d failed" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("FAILED: " + ", ".join(FAILED))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
