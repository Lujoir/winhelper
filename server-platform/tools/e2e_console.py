#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""观枢终端平台控制台 · 无头浏览器 E2E（Playwright）。

前置：本地或远端服务已运行、库中已有冒烟终端数据（tools/smoke.py 产生）。
自播种（2026-09-18 健壮性修复）：详情端点 latest_metrics 只取最近 1 小时
指标（api.py query_metrics now-3600），历史种子过期后 metric/disk 卡片
不渲染导致整套超时——现在运行前自动给 WIN-SMOKE-TOKEN 上报新鲜快照，
任何时刻运行均可复现晨间绿基线。
环境变量：
    ETP_API_BASE          服务基址（默认 http://127.0.0.1:18090）
    ETP_CONSOLE_PASSWORD  控制台口令（默认 dev-console）
    ETP_TERMINAL_TOKEN    终端上报令牌（默认 dev-token）
"""
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright

BASE = os.environ.get("ETP_API_BASE", "http://127.0.0.1:18090").rstrip("/")
PASSWORD = os.environ.get("ETP_CONSOLE_PASSWORD", "dev-console")
TOKEN = os.environ.get("ETP_TERMINAL_TOKEN", "dev-token")
SEED_TID = "WIN-SMOKE-TOKEN"


def _seed_fresh_metrics():
    """给种子终端上报 3 个新鲜快照（健康→高负载→恢复），激活 metric/disk
    卡片与曲线数据。失败不阻断（既有新鲜数据时 E2E 仍可绿）。"""
    snaps = [
        {"cpu": {"percent": 50.0},
         "mem": {"used_percent": 55.0, "available_percent": 45.0},
         "disks": [{"mount": "C:", "used_gb": 120, "total_gb": 200,
                    "percent": 60.0, "busy_percent": 10.0}]},
        {"cpu": {"percent": 92.0},
         "mem": {"used_percent": 94.0, "available_percent": 6.0},
         "disks": [{"mount": "C:", "used_gb": 190, "total_gb": 200,
                    "percent": 95.0, "busy_percent": 30.0}]},
        {"cpu": {"percent": 23.0},
         "mem": {"used_percent": 48.0, "available_percent": 52.0},
         "disks": [{"mount": "C:", "used_gb": 121, "total_gb": 200,
                    "percent": 60.5, "busy_percent": 5.0}]},
    ]
    for snap in snaps:
        snap.setdefault("ts", int(time.time()))
        body = json.dumps(snap).encode("utf-8")
        req = urllib.request.Request(
            BASE + "/api/v1/terminals/" + SEED_TID + "/metrics", data=body,
            headers={"Content-Type": "application/json",
                     "X-ETP-Token": TOKEN}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except urllib.error.HTTPError as e:
            print("  [seed] metrics POST http %s" % e.code)
        except Exception as e:
            print("  [seed] metrics POST failed: %s" % repr(e)[:80])
        time.sleep(0.3)
    print("  [seed] fresh metrics posted for " + SEED_TID)

PASSED = []
FAILED = []
widths = []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                          (" | " + detail) if detail and not cond else ""))


def main():
    _seed_fresh_metrics()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(BASE + "/", wait_until="networkidle")

        # 1. 登录浮层
        overlay = page.locator("#loginOverlay")
        overlay.wait_for(state="visible", timeout=5000)
        check("login overlay visible", overlay.is_visible())
        check("brand title in page", "观枢终端平台｜EyeTerm" in page.content())
        check("login page client download link",
              page.locator("#loginDownloadLink").count() == 1
              and page.locator("#loginDownloadLink").get_attribute("href")
              == "/download/client/setup")

        # 2. 登录
        page.fill("#pwd", PASSWORD)
        page.click("#loginOverlay button.primary")
        overlay.wait_for(state="hidden", timeout=5000)
        check("login success (overlay hidden)", not overlay.is_visible())

        # 2.5 首页为默认页（home-console-dev）：登录后落在首页，
        # 断言首页卡片网格出现后切回终端监控继续既有流程。
        check("default page is home",
              "active" in (page.locator('.tab[data-page="home"]')
                           .get_attribute("class") or ""))
        page.wait_for_selector("#homeGrid .home-card", timeout=5000)
        _hc = page.locator("#homeGrid .home-card")
        check("home cards rendered on login", _hc.count() >= 5,
              "count=%d" % _hc.count())
        # 未接入模块的「卡片接入中」占位卡已移除（2026-09-19 用户反馈：
        # 首页不应出现没有要求增加的空卡）
        check("home has no placeholder cards",
              page.locator("#homeGrid .home-card",
                           has_text="卡片接入中").count() == 0)
        page.locator('.tab[data-page="monitor"]').click()
        page.wait_for_timeout(400)

        # 3. 终端列表出现冒烟终端
        page.wait_for_selector(".t-item", timeout=5000)
        items = page.locator(".t-item").count()
        check("terminal list rendered", items >= 1, "items=%d" % items)

        # 4. 点击终端 → 详情卡片（精确定位种子终端：列表按 last_seen 排序，
        # smoke 重灌后首行可能换人且无新鲜指标，metricCards 断言需确定性）
        page.evaluate("selectTerminal('%s')" % SEED_TID)
        page.wait_for_selector("#infoCard h2 .dot", timeout=5000)
        check("terminal detail rendered",
              page.locator("#infoCard .kv").count() >= 5)
        page.wait_for_selector("#metricCards .m-card", timeout=5000)
        check("metric cards rendered", page.locator("#metricCards .m-card").count() == 3)
        page.wait_for_selector("#diskCards .m-card", timeout=5000)
        check("disk cards rendered", page.locator("#diskCards .m-card").count() >= 1)

        # 5. 曲线 canvas 绘制
        page.wait_for_timeout(800)
        drawn = page.evaluate(
            "() => { const c = document.getElementById('curve');"
            " const ctx = c.getContext('2d');"
            " return ctx.getImageData(0, 0, c.width, c.height).data"
            ".some(v => v > 0); }")
        check("curve canvas painted", drawn)

        # 6. 瓶颈表渲染（冒烟数据含 3+ 条瓶颈）
        page.wait_for_selector("#bnBody tr", timeout=5000)
        bn_rows = page.locator("#bnBody tr").count()
        check("bottleneck table rows", bn_rows >= 3, "rows=%d" % bn_rows)

        # 7. 事件表渲染
        page.wait_for_selector("#evBody tr", timeout=5000)
        check("events table rows", page.locator("#evBody tr").count() >= 1)

        # 8. 确认按钮交互
        btn = page.locator("#bnBody .link-btn").first
        if btn.count() > 0:
            btn.click()
            page.wait_for_timeout(600)
            check("ack action executed",
                  "已确认" in page.locator("#bnBody").inner_text())

        # 8.5 配置清单页（功能1；算力网关卡片已迁移至系统管理页）
        page.locator('.tab[data-page="config"]').click()
        page.wait_for_selector("#wlCidr", timeout=5000)
        check("config page rendered", page.is_visible("#wlCidr"))
        check("whitelist table present", page.locator("#wlBody").count() == 1)
        page.fill("#wlCidr", "192.0.2.0/24")
        page.fill("#wlNote", "e2e-temp")
        page.click("#page-config .inline-form button.primary")
        page.wait_for_selector("#wlBody tr td:has-text('192.0.2.0/24')",
                               timeout=5000)
        check("whitelist entry added via UI",
              page.locator("#wlBody tr td:has-text('192.0.2.0/24')").count() >= 1)
        page.locator("#wlBody tr td:has-text('192.0.2.0/24')").locator(
            "xpath=following-sibling::td[3]//button[contains(., '删除')]").first.click()
        page.wait_for_timeout(600)
        check("whitelist entry deleted via UI",
              page.locator("#wlBody tr td:has-text('192.0.2.0/24')").count() == 0)
        # 回监控页避免影响后续断言
        page.locator('.tab[data-page="monitor"]').click()
        page.wait_for_timeout(400)

        # 8.55 系统管理页（sysadmin，admin-only；算力网关卡片自配置页迁入）
        page.locator('.tab[data-page="sysadmin"]').click()
        page.wait_for_selector("#saUserBody tr", timeout=5000)
        check("sysadmin page rendered", page.is_visible("#llmUrl")
              and page.is_visible("#llmModelFallback")
              and page.locator("#llmTestMsg").count() == 1)
        check("sysadmin user table populated",
              page.locator("#saUserBody tr").count() >= 1)
        check("sysadmin token table present",
              page.locator("#saTokenBody").count() == 1)
        check("sysadmin third-party table present",
              page.locator("#saTpBody").count() == 1)
        check("sysadmin tab visible for admin",
              page.locator('.tab[data-page="sysadmin"]').is_visible())

        # 8.55b 系统管理页 · WoL 双路线设置卡（ADR-044：加载/保存/回读/回退语义）
        page.wait_for_selector("#wolWakeMode", timeout=5000)
        # 渲染竞态加固：select 可见 ≠ 动态注入的 options 已挂载，等 options 就绪再读
        page.wait_for_function(
            "() => document.querySelectorAll('#wolWakeMode option').length >= 2",
            timeout=8000)
        mode_opts = page.locator("#wolWakeMode option").all_inner_texts()
        check("wol card rendered with mode options",
              any("中继模式" in t for t in mode_opts)
              and any("画方准入模式" in t for t in mode_opts)
              and page.is_visible("#wolNadUrl")
              and page.locator("#wolNadKey").get_attribute("type") == "password")
        cur_mode = page.locator("#wolWakeMode").input_value()
        cur_step = page.locator("#wolStepSec").input_value()
        check("wol defaults loaded from GET",
              cur_mode in ("relay", "nad") and cur_step.isdigit(),
              "mode=%s step=%s" % (cur_mode, cur_step))
        # 保存：切 nad + 合法参数 → 回读断言 → 恢复 relay（清场）
        page.select_option("#wolWakeMode", "nad")
        page.fill("#wolStepSec", "90")
        page.evaluate("saSaveWol()")
        page.wait_for_timeout(600)
        check("wol save nad + step=90 persisted",
              page.locator("#wolWakeMode").input_value() == "nad"
              and page.locator("#wolStepSec").input_value() == "90")
        page.select_option("#wolWakeMode", "relay")
        page.fill("#wolStepSec", "120")
        page.evaluate("saSaveWol()")
        page.wait_for_timeout(600)
        check("wol restored to relay mode (clean exit)",
              page.locator("#wolWakeMode").input_value() == "relay"
              and page.locator("#wolStepSec").input_value() == "120")
        # nad key 留空保持不变（掩码显示非空占位或未配置提示）
        check("wol nad key masked state shown",
              page.locator("#wolNadKeyMask").inner_text().strip() != "")

        # 8.56 系统管理页 · VLAN 知识库（ADR-044 收尾：列表/导入/删除/uiConfirm）
        page.wait_for_selector("#vlanKbBody tr", timeout=5000)
        check("vlan kb card + table rendered",
              page.locator("#vlanKbCard h3").inner_text() == "VLAN 知识库"
              and page.locator("#vlanKbBody").count() == 1)
        # 清理历史残留（保证导入断言「新增 2」确定性）
        page.evaluate(
            "() => fetch('/api/v1/console/sysadmin/vlan-kb',"
            "{headers:{'X-ETP-Console-Token': sessionStorage.getItem('etp_console_token') || token}})"
            ".then(function(r){ return r.json(); }).then(function(j){"
            " return Promise.all((j.items || []).filter(function(x){ return x.source === 'e2e'; })"
            ".map(function(x){ return fetch('/api/v1/console/sysadmin/vlan-kb/' + x.id,"
            "{method:'DELETE', headers:{'X-ETP-Console-Token': sessionStorage.getItem('etp_console_token') || token}}); })); })")
        page.wait_for_timeout(500)
        # 文件导入：两条合法（其一需归一）+ 一条非法 cidr（中性示例网段）
        kb_seed = {"items": [
            {"cidr": "192.168.56.0/24", "vlan_id": "56",
             "zone_desc": "E2E·导入测试", "source": "e2e"},
            {"cidr": "10.9.9.7/24", "vlan_id": "99",
             "zone_desc": "E2E·归一化测试", "source": "e2e"},
            {"cidr": "not-a-cidr", "vlan_id": "x",
             "zone_desc": "E2E·非法条目", "source": "e2e"}]}
        kb_file = os.path.join(tempfile.gettempdir(), "etp_e2e_vlan_kb.json")
        with open(kb_file, "w", encoding="utf-8") as fh:
            json.dump(kb_seed, fh, ensure_ascii=False)
        page.set_input_files("#vlanKbFile", kb_file)
        page.wait_for_function(
            "() => document.getElementById('vlanKbMsg').textContent"
            ".indexOf('导入完成') >= 0", timeout=8000)
        kb_msg = page.locator("#vlanKbMsg").inner_text()
        check("vlan kb import success state (added/updated/invalid)",
              "新增 2" in kb_msg and "更新 0" in kb_msg
              and "无效 1" in kb_msg and "not-a-cidr" in kb_msg, kb_msg)
        check("vlan kb imported row listed",
              page.locator("#vlanKbBody tr td:has-text('192.168.56.0/24')")
              .count() >= 1)
        check("vlan kb cidr normalized (10.9.9.7/24 → 10.9.9.0/24)",
              page.locator("#vlanKbBody tr td:has-text('10.9.9.0/24')").count() == 1
              and page.locator("#vlanKbBody tr td:has-text('10.9.9.7/24')")
              .count() == 0)
        # 前端过滤框（区域说明）
        page.fill("#vlanKbFilterZone", "归一化测试")
        page.wait_for_timeout(200)
        check("vlan kb zone filter narrows rows",
              page.locator("#vlanKbBody tr").count() == 1
              and "10.9.9.0/24" in page.locator("#vlanKbBody").inner_text())
        page.fill("#vlanKbFilterZone", "")
        page.fill("#vlanKbFilterCidr", "192.168.56")
        page.wait_for_timeout(200)
        check("vlan kb cidr filter narrows rows",
              page.locator("#vlanKbBody tr").count() == 1
              and "192.168.56.0/24" in page.locator("#vlanKbBody").inner_text())
        page.fill("#vlanKbFilterCidr", "")
        page.wait_for_timeout(200)
        # 删除走 uiConfirm danger（并断言零原生 dialog）
        native_dialogs = []

        def _no_native_dialog(d):
            native_dialogs.append(d.type)
            d.dismiss()

        page.on("dialog", _no_native_dialog)
        page.locator("#vlanKbBody tr td:has-text('192.168.56.0/24')").locator(
            "xpath=following-sibling::td[last()]//button[contains(., '删除')]"
        ).first.click()
        page.wait_for_selector("#uiConfirmOverlay", state="visible",
                               timeout=5000)
        check("vlan kb delete uses uiConfirm danger (no native confirm)",
              "btn-danger" in (page.locator("#uiConfirmOk").get_attribute(
                  "class") or ""))
        page.locator("#uiConfirmOk").click()
        page.wait_for_timeout(800)
        check("vlan kb row deleted via UI",
              page.locator("#vlanKbBody tr td:has-text('192.168.56.0/24')")
              .count() == 0)
        check("vlan kb zero native dialog fired", len(native_dialogs) == 0)
        page.remove_listener("dialog", _no_native_dialog)
        # 清理：删除另一条 E2E 导入条目
        page.locator("#vlanKbBody tr td:has-text('10.9.9.0/24')").locator(
            "xpath=following-sibling::td[last()]//button[contains(., '删除')]"
        ).first.click()
        page.wait_for_selector("#uiConfirmOverlay", state="visible",
                               timeout=5000)
        page.locator("#uiConfirmOk").click()
        page.wait_for_timeout(800)
        check("vlan kb e2e rows cleaned",
              page.locator("#vlanKbBody tr td:has-text('10.9.9.0/24')")
              .count() == 0)

        # 8.57 首页 · 资产定位卡（回归门禁：结果区必须离开 loading 出终态）
        # 背景：2026-09-19 生产出现「点定位后永远转圈」——后端 200 但前端
        # 结果区停留 loading 无终态；此断言要求「响应到达后必须渲染
        # 结果/空态/错误态之一」，杜绝无终态转圈。
        page.locator('.tab[data-page="home"]').click()
        page.wait_for_selector("#alInput", timeout=5000)
        page.fill("#alInput", "192.0.2.77")     # TEST-NET-1 保留地址，必 not_found
        page.click("#alBtn")
        page.wait_for_function(
            "() => { var r = document.getElementById('alResult');"
            " return !!r && !r.querySelector('.hc-loading'); }",
            timeout=20000)
        al_txt = page.locator("#alResult").inner_text()
        check("asset-locate leaves loading state (no infinite spinner)",
              bool(al_txt.strip()), al_txt[:60])
        check("asset-locate unknown target reported (not_found / unavailable)",
              ("未找到" in al_txt) or ("不可用" in al_txt), al_txt[:80])
        page.fill("#alInput", "")

        # 8.58 首页 · 终端性能分析卡（新增：渲染 + 离开加载态 + 摘要字段）
        page.wait_for_selector("#homeCard-perf", timeout=5000)
        check("home perf card rendered",
              page.locator("#homeCard-perf h3").inner_text() == "终端性能分析")
        page.wait_for_function(
            "() => { var c = document.getElementById('homeCard-perf');"
            " return !!c && !c.querySelector('.hc-loading'); }", timeout=8000)
        perf_txt = page.locator("#homeCard-perf .hc-body").inner_text()
        check("home perf card shows metrics or explicit empty state",
              ("指标覆盖" in perf_txt) or ("暂无性能数据" in perf_txt)
              or ("数据不可用" in perf_txt), perf_txt[:80])

        page.locator('.tab[data-page="monitor"]').click()
        page.wait_for_timeout(300)

        # 8.6 资产清单页（功能2）
        page.locator('.tab[data-page="assets"]').click()
        page.wait_for_selector("#assetCards .card", timeout=5000)
        check("assets page rendered", page.is_visible("#astTotal"))
        check("asset card shows hwinfo",
              page.locator("#assetCards .card").count() >= 1)
        # 8.61 资产页批量操作工具栏（2026-09-19 增补，参考火绒「终端管理」）
        #   覆盖：工具栏存在 / 未选时禁用 / 勾选联动计数 / 「更多」三个动作项
        check("asset batch toolbar present",
              page.is_visible("#asToolbar")
              and page.is_visible("#asUpgradeBtn")
              and page.is_visible("#asMoreBtn"))
        check("batch buttons disabled before selection",
              page.locator("#asUpgradeBtn").is_disabled()
              and page.locator("#asMoreBtn").is_disabled())
        _acks = page.locator("#assetCards .as-ck")
        check("asset cards have batch checkbox", _acks.count() >= 1,
              "checkbox=%d" % _acks.count())
        page.locator("#asCkAll").check()
        page.wait_for_timeout(200)
        check("select-all counts and enables batch buttons",
              (not page.locator("#asUpgradeBtn").is_disabled())
              and (not page.locator("#asMoreBtn").is_disabled())
              and page.locator("#asCkCount").inner_text().strip()
                  == str(_acks.count()),
              page.locator("#asCkCount").inner_text())
        page.locator("#asMoreBtn").click()
        page.wait_for_timeout(250)
        check("more menu has restart/shutdown/wake",
              page.locator("#asMoreMenu .as-mi").count() == 3
              and page.locator('#asMoreMenu .as-mi:has-text("重启")').count() == 1
              and page.locator('#asMoreMenu .as-mi:has-text("关机")').count() == 1
              and page.locator('#asMoreMenu .as-mi:has-text("开机")').count() == 1)
        page.locator("body").click(position={"x": 5, "y": 400})
        page.wait_for_timeout(200)
        check("more menu closes on outside click",
              not page.locator("#asMoreMenu").is_visible())
        page.locator("#asCkAll").uncheck()
        page.wait_for_timeout(150)
        check("unselect-all disables batch buttons",
              page.locator("#asUpgradeBtn").is_disabled()
              and page.locator("#asCkCount").inner_text().strip() == "0")
        page.locator('.tab[data-page="monitor"]').click()
        page.wait_for_timeout(300)

        # 8.7 网络测试页（功能3）
        page.locator('.tab[data-page="nettest"]').click()
        page.wait_for_selector("#ntBody", timeout=5000)
        check("nettest page rendered", page.is_visible("#ntType")
              and page.locator("#ntType option").count() == 4)
        page.wait_for_timeout(600)
        check("nettest task table populated",
              page.locator("#ntBody tr").count() >= 1)
        page.locator('.tab[data-page="monitor"]').click()
        page.wait_for_timeout(300)

        # 8.8 AI 分析页（功能4）
        page.locator('.tab[data-page="ai"]').click()
        page.wait_for_selector("#aiBody", timeout=5000)
        check("ai page rendered", page.is_visible("#aiTerminal")
              and page.is_visible("#aiIssue"))
        page.wait_for_timeout(600)
        check("ai history table populated",
              page.locator("#aiBody tr").count() >= 1)
        page.locator('.tab[data-page="monitor"]').click()
        page.wait_for_timeout(300)

        # 8.9 知识库页（kb_store，ADR-022）：列表→新增→编辑→版本历史→回滚
        page.locator('.tab[data-page="kb"]').click()
        page.wait_for_selector("#kbBody tr td b", timeout=5000)   # 等数据渲染（非"加载中"占位）
        check("kb page rendered", page.is_visible("#kbCat")
              and page.locator("#kbBody tr").count() >= 1)
        check("kb seeded route-nodes entry",
              page.locator("#kbBody tr td:has-text('路由表 · 关键节点')").count() >= 1)
        # 新增条目（编辑路径）
        page.click("#page-kb button:has-text('新增条目')")
        page.fill("#kbNewId", "e2e-kb-entry")
        page.fill("#kbTitle", "E2E 测试条目")
        page.fill("#kbCategory", "route_nodes")
        page.fill("#kbContent", '[{"match":"10.99.0.0/16","zone":"E2E区","desc":"测试"}]')
        page.click("#kbEditModal button:has-text('保存')")
        page.wait_for_selector("#kbBody tr td:has-text('E2E 测试条目')", timeout=5000)
        check("kb entry created via UI", True)
        # 编辑（版本迭代 v2）
        page.locator("#kbBody tr td:has-text('E2E 测试条目')").locator(
            "xpath=following-sibling::td[5]//button[contains(., '编辑')]").first.click()
        page.wait_for_selector("#kbEditModal", state="visible", timeout=5000)
        page.fill("#kbContent", '[{"match":"10.99.1.0/24","zone":"E2E区2","desc":"改"}]')
        page.fill("#kbNote", "e2e-edit")
        page.click("#kbEditModal button:has-text('保存')")
        page.wait_for_timeout(800)
        # 版本历史含 2 版并回滚到 v1
        page.locator("#kbBody tr td:has-text('E2E 测试条目')").locator(
            "xpath=following-sibling::td[5]//button[contains(., '版本')]").first.click()
        page.wait_for_selector("#kbVerBody tr", timeout=5000)
        check("kb versions has 2 entries",
              page.locator("#kbVerBody tr").count() == 2)
        # 回滚到最低版本 v1（列表按版本号倒序，last = v1）——uiConfirm 确认
        page.locator("#kbVerBody tr").last.locator("button:has-text('回滚')").click()
        page.wait_for_selector("#uiConfirmOverlay", state="visible",
                               timeout=5000)
        check("kb rollback uiConfirm danger style",
              "btn-danger" in (page.locator("#uiConfirmOk").get_attribute(
                  "class") or "")
              and "确认回滚" in page.locator("#uiConfirmOk").inner_text())
        page.locator("#uiConfirmOk").click()
        page.wait_for_timeout(900)
        check("kb rollback executed (v3)",
              "v3" in page.locator("#kbVerBody").inner_text())
        # 校验回滚后内容回到 v1 数据（经 API 内容核对）
        kb_entry = page.evaluate(
            "fetch('/api/v1/console/kb/e2e-kb-entry', {headers:{'X-ETP-Console-Token': sessionStorage.getItem('etp_console_token') || token}}).then(function(r){ return r.json(); })")
        content = json.dumps(kb_entry.get("entry", {}).get("content", "")
                             if isinstance(kb_entry, dict) else "")
        check("kb rolled back to v1 content",
              "10.99.0.0/16" in content)
        # 清理测试条目（DELETE）
        page.evaluate(
            "fetch('/api/v1/console/kb/e2e-kb-entry', {method:'DELETE', headers:{'X-ETP-Console-Token': sessionStorage.getItem('etp_console_token') || token}})")
        page.wait_for_timeout(400)

        # 8.95 资产页 · 安全分组融合（火绒 P1 UI 融合形态；桩数据驱动）
        # 先收起 kb 段遗留的版本历史弹窗，避免遮挡导航点击
        page.evaluate("if (typeof kbCloseVersions === 'function') kbCloseVersions()")
        page.wait_for_timeout(300)

        # 导航零「终端安全」残留
        check("security tab removed",
              page.locator('.tab[data-page="security"]').count() == 0)
        check("security page dom removed",
              page.locator("#page-security").count() == 0)

        az_sync_calls = {"n": 0}

        def _az_groups_stub():
            groups = []
            for i in range(89):
                name = "防护组" if i == 0 else "分组%02d" % (i + 1)
                if i == 88:
                    name = "i18n:db_groups_name:ungrouped"   # 生产真实键残留样例
                groups.append({"id": i + 1, "name": name,
                               "parent": 0 if i < 13 else ((i - 13) % 13) + 1,
                               "total": 4, "online": 1, "matched": 1})
            groups[0]["total"] = 24
            groups[0]["online"] = 12
            return groups

        def _az_hu(i, win7=False):
            return {"client_id": "HC%04d" % i, "name": "PC-%03d" % i,
                    "computer_name": "PC-%03d" % i,
                    "ip": "10.99.0.%d" % (i + 1),
                    "connect_ip": "10.99.0.%d" % (i + 1),
                    "mac": "AA-BB-CC-%02X-%02X-%02X" % (i, i, i),
                    "group_id": 1, "group_name": "防护组",
                    "online": i % 2 == 0,
                    "os": ("Microsoft Windows 7 专业版" if win7
                           else "Microsoft Windows 10 专业版"),
                    "hr_version": "2.0.19.7",
                    "last_seen": 1760000000 - i, "win7_eol": win7}

        def _az_pl(i):
            return {"terminal_id": "WIN-E2E-%03d" % i,
                    "hostname": "E2E-PC-%03d" % i,
                    "ip": "10.98.0.%d" % (i + 1), "online": True,
                    "client_version": "4.0.0",
                    "os_info": "Windows 10 专业版", "group_id": None,
                    "asset": {"cpu_model": "Intel Core i7-12700",
                              "cpu_cores": 20, "mem_total_mb": 32768,
                              "disk_total_gb": 1024.0,
                              "gpu_info": "NVIDIA RTX 3060", "os_arch": "x64"}}

        def _az_stub(route):
            from urllib.parse import urlparse, parse_qs
            req = route.request
            u = urlparse(req.url)
            path = u.path
            qs = {k: v[0] for k, v in parse_qs(u.query).items()}
            if req.method == "POST" and path.endswith("/huorong/sync"):
                # 首次 409（同步进行中）→ 二次 200（成功），覆盖双路径交互
                az_sync_calls["n"] += 1
                if az_sync_calls["n"] == 1:
                    route.fulfill(status=409, content_type="application/json",
                                  body=json.dumps(
                                      {"ok": False,
                                       "error": "同步进行中，请稍后再试"}))
                else:
                    route.fulfill(status=200, content_type="application/json",
                                  body=json.dumps(
                                      {"ok": True, "result": {
                                          "ok": True, "groups": 89,
                                          "clients": 710, "duration_ms": 120,
                                          "trigger": "manual"}}))
                return
            if path.endswith("/huorong/overview"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(
                                  {"ok": True, "groups_count": 89,
                                   "clients_total": 710, "online": 152,
                                   "online_rate": 0.2141,
                                   "win7_eol_count": 59,
                                   "last_sync": 1760000000}))
                return
            if path.endswith("/assets/groups"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(
                                  {"ok": True, "platform_groups": [],
                                   "huorong_groups": _az_groups_stub(),
                                   "other": {"platform_total": 1,
                                             "platform_online": 1}}))
                return
            if path.endswith("/assets/terminals"):
                gs = qs.get("group_source")
                gid = qs.get("group_id")
                if gs == "huorong" and gid == "other":
                    items = [{"kind": "platform_only", "match_type": None,
                              "platform": _az_pl(99), "huorong": None}]
                    body = {"ok": True, "total": 1, "page": 1,
                            "page_size": 20, "items": items}
                elif gs == "huorong" and gid == "1":
                    items = [
                        {"kind": "matched", "match_type": "mac",
                         "platform": _az_pl(1), "huorong": _az_hu(1, win7=True)},
                        {"kind": "matched", "match_type": "manual",
                         "platform": _az_pl(2), "huorong": _az_hu(2)}]
                    for i in range(3, 25):
                        items.append({"kind": "huorong_only", "match_type": None,
                                      "platform": None, "huorong": _az_hu(i)})
                    q = (qs.get("q") or "").lower()
                    if q:
                        items = [it for it in items
                                 if _az_q_hit(it, q)]
                    total = len(items)
                    pg = int(qs.get("page") or 1)
                    ps = int(qs.get("page_size") or 20)
                    items = items[(pg - 1) * ps: pg * ps]
                    body = {"ok": True, "total": total, "page": pg,
                            "page_size": ps, "items": items}
                else:
                    body = {"ok": True, "total": 0, "page": 1,
                            "page_size": 20, "items": []}
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(body, ensure_ascii=False))
                return
            if req.method == "POST" and (
                    path.endswith("/assets/assign-link")
                    or path.endswith("/assets/assign-group")):
                route.fulfill(status=200, content_type="application/json",
                              body='{"ok":true}')
                return
            route.fulfill(status=404, content_type="application/json",
                          body='{"ok":false,"error":"not found"}')

        def _az_q_hit(it, q):
            def hit(o, fields):
                for f in fields:
                    v = o.get(f) if o else None
                    if v and q in str(v).lower():
                        return True
                return False
            return hit(it.get("platform"), ("hostname", "terminal_id", "ip")) \
                or hit(it.get("huorong"),
                       ("name", "computer_name", "ip", "mac"))

        # 先真打一次 assets/groups 触发服务端镜像同步（建「全部资产」根）
        # —— 必须在桩注册前（桩会拦截该端点）
        page.evaluate(
            "() => fetch('/api/v1/console/assets/groups',"
            " {headers:{'X-ETP-Console-Token':"
            " sessionStorage.getItem('etp_console_token') || token}})"
            ".then(r => r.json())")
        page.wait_for_timeout(600)

        page.route("**/console/huorong/**", _az_stub)
        page.route("**/console/assets/**", _az_stub)

        page.locator('.tab[data-page="assets"]').click()
        page.wait_for_selector("#hrOverview .m-card", timeout=5000)
        page.wait_for_selector("#azTree .hr-grp", timeout=5000)
        page.wait_for_timeout(700)
        check("asset root node present",
              page.locator('#agTree .ag-node:has-text("全部资产")')
              .count() == 1)
        page.locator('#agTree .ag-node:has-text("全部资产")').first.click()
        page.wait_for_timeout(500)
        check("asset root shows all terminals",
              "全部资产" in page.locator("#agGroupTitle").inner_text())
        check("assets overview numbers",
              page.locator("#hrTotal").inner_text() == "710"
              and page.locator("#hrOnline").inner_text().strip().startswith("152")
              and page.locator("#hrGroupsCnt").inner_text() == "89"
              and page.locator("#hrWin7").inner_text() == "59")
        check("assets online rate shown",
              "21.4%" in page.locator("#hrOnline").inner_text())
        check("win7 eol card red",
              "v-err" in (page.locator("#hrWin7").get_attribute("class") or ""))
        check("last sync rendered",
              page.locator("#hrLastSync").inner_text() not in ("-", "未同步"))
        check("huorong tree collapsed default (13 roots + other)",
              page.locator("#azTree .hr-grp").count() == 14,
              str(page.locator("#azTree .hr-grp").count()))
        # i18n 未分组兜底（id=89 深层，展开其父「分组12」后可见）
        page.locator('#azTree .hr-grp[data-gid="11"] .ag-tg').first.click()
        page.wait_for_timeout(300)
        check("i18n ungrouped fallback in tree",
              page.locator('#azTree .hr-grp:has-text("未分组")').count() >= 1)
        page.locator('#azTree .hr-grp[data-gid="11"] .ag-tg').first.click()
        page.wait_for_timeout(300)
        check("other virtual group present",
              page.locator('#azTree .hr-grp[data-gid="other"]').count() == 1)
        # 展开收起交互：顶层防护组 toggle
        page.locator('#azTree .hr-grp[data-gid="1"] .ag-tg').first.click()
        page.wait_for_timeout(300)
        expanded_n = page.locator("#azTree .hr-grp").count()
        check("security tree expand on toggle", expanded_n > 14,
              str(expanded_n))
        page.locator('#azTree .hr-grp[data-gid="1"] .ag-tg').first.click()
        page.wait_for_timeout(300)
        check("security tree collapse on toggle",
              page.locator("#azTree .hr-grp").count() == 14)

        # 选中火绒组（防护组）→ 三类条目
        page.locator('#azTree .hr-grp[data-gid="1"]').click()
        page.wait_for_selector("#azCards .card", timeout=5000)
        page.wait_for_timeout(700)
        check("entry card shown / others hidden",
              page.locator("#azEntryCard").is_visible()
              and not page.locator("#assetCards").is_visible()
              and not page.locator("#agGroupCard").is_visible())
        check("entry title = group name",
              "防护组" in page.locator("#azTitleText").inner_text())
        check("matched badge auto",
              page.locator('#azCards .badge:has-text("自动匹配")').count() == 1)
        check("matched badge manual",
              page.locator('#azCards .badge:has-text("手动关联")').count() == 1)
        check("win7 eol badge in entry card",
              page.locator("#azCards .b-eol").count() == 1)
        check("huorong only empty hint",
              page.locator('#azCards .empty:has-text("终端未接入平台")').count() == 18)
        check("link buttons per kind",
              page.locator('#azCards [data-az-link]:has-text("更换关联终端")').count() == 2
              and page.locator('#azCards [data-az-link]:has-text("指定关联终端")').count() == 18)
        check("unlink buttons on matched only",
              page.locator("#azCards [data-az-unlink]").count() == 2)
        check("regroup buttons on all hr cards",
              page.locator("#azCards [data-az-group]").count() == 20)
        check("entry pager 24/2",
              "共 24 台 · 第 1 / 2 页" in page.locator("#azPager").inner_text(),
              page.locator("#azPager").inner_text())

        # 分页翻页
        page.locator("#azPager .link-btn:has-text('下一页')").click()
        page.wait_for_timeout(600)
        check("entry pager page 2",
              "第 2 / 2 页" in page.locator("#azPager").inner_text()
              and page.locator("#azCards .card").count() == 4,
              page.locator("#azPager").inner_text())
        page.locator("#azPager .link-btn:has-text('上一页')").click()
        page.wait_for_timeout(600)

        # 关键字搜索（huorong 名称）
        page.fill("#azSearch", "PC-003")
        page.locator("#azEntryCard button:has-text('搜索')").click()
        page.wait_for_timeout(600)
        check("entry keyword search",
              "共 1 台" in page.locator("#azPager").inner_text())
        page.fill("#azSearch", "")
        page.locator("#azEntryCard button:has-text('搜索')").click()
        page.wait_for_timeout(600)

        # 手动关联：huorong_only 卡 → 弹窗选平台终端 → 确定
        page.locator('#azCards [data-az-link]:has-text("指定关联终端")').first.click()
        page.wait_for_selector("#azLinkModal", state="visible", timeout=5000)
        page.wait_for_selector('#azLinkList input[name="azLinkPick"]',
                               timeout=5000)
        check("link modal lists platform terminals",
              page.locator('#azLinkList input[name="azLinkPick"]').count() >= 1)
        page.locator('#azLinkList input[name="azLinkPick"]').first.check()
        page.locator("#azLinkModal button:has-text('确定关联')").click()
        page.wait_for_timeout(800)
        check("assign-link done",
              page.locator("#toast").inner_text().strip() == "已关联"
              and not page.locator("#azLinkModal").is_visible(),
              page.locator("#toast").inner_text())

        # 解除关联：matched 卡 → uiConfirm 居中确认 → 确定
        page.locator("#azCards [data-az-unlink]").first.click()
        page.wait_for_selector("#uiConfirmOverlay", state="visible", timeout=5000)
        check("uiConfirm centered dialog",
              "解除关联" in page.locator("#uiConfirmTitle").inner_text())
        page.locator("#uiConfirmOk").click()
        page.wait_for_timeout(800)
        check("unlink done via uiConfirm",
              page.locator("#toast").inner_text().strip() == "已解除关联"
              and not page.locator("#uiConfirmOverlay").is_visible(),
              page.locator("#toast").inner_text())

        # 调整分组：弹窗组树选组器 → 选分组02 → 确定
        page.locator("#azCards [data-az-group]").first.click()
        page.wait_for_selector("#azGroupModal", state="visible", timeout=5000)
        page.wait_for_selector('#azGroupList input[name="azGroupPick"]',
                               timeout=5000)
        check("group picker has follow-source option",
              page.locator('#azGroupList input[name="azGroupPick"][value="0"]')
              .count() == 1)
        page.check('#azGroupList input[name="azGroupPick"][value="2"]')
        page.locator("#azGroupModal button:has-text('确定调整')").click()
        page.wait_for_timeout(800)
        check("assign-group done",
              page.locator("#toast").inner_text().strip() == "已调整分组"
              and not page.locator("#azGroupModal").is_visible(),
              page.locator("#toast").inner_text())

        # 「其他」虚拟组 → 仅平台条目
        page.locator('#azTree .hr-grp[data-gid="other"]').click()
        page.wait_for_timeout(700)
        check("other view platform_only card",
              "其他" in page.locator("#azTitleText").inner_text()
              and page.locator("#azCards .card").count() == 1
              and page.locator("#azCards [data-az-group]").count() == 0,
              page.locator("#azTitleText").inner_text())

        # 选中互斥回归：点平台组树 → 条目卡隐藏 + 平台组卡显示
        page.locator('#agTree .ag-node:has-text("未分组终端")').first.click()
        page.wait_for_timeout(600)
        check("platform group selection mutex",
              page.locator("#agGroupCard").is_visible()
              and not page.locator("#azEntryCard").is_visible(),
              "agGroupCard=%s azEntryCard=%s" % (
                  page.locator("#agGroupCard").is_visible(),
                  page.locator("#azEntryCard").is_visible()))

        # 立即同步 · 路径1：409 → 透传后端文案 + 按钮恢复；路径2：200 → 重载
        page.locator("#hrSyncBtn").click()
        page.wait_for_timeout(600)
        check("sync 409 toast hint",
              page.locator("#toast").inner_text().strip() == "同步进行中，请稍后再试",
              page.locator("#toast").inner_text())
        check("sync 409 button restored",
              page.locator("#hrSyncBtn").is_enabled()
              and page.locator("#hrSyncBtn").inner_text().strip() == "立即同步")
        page.locator("#hrSyncBtn").click()
        page.wait_for_timeout(900)
        check("sync 200 toast result",
              page.locator("#toast").inner_text().strip()
              == "同步完成：分组 89 · 终端 710",
              page.locator("#toast").inner_text())
        check("sync 200 button restored and overview reloaded",
              page.locator("#hrSyncBtn").is_enabled()
              and page.locator("#hrTotal").inner_text() == "710")

        page.unroute("**/console/huorong/**")
        page.unroute("**/console/assets/**")

        # 8.96 桌面管控页（desktop-policy P1；桩数据驱动，ADR-036）
        def _dp_stub(route):
            from urllib.parse import urlparse, parse_qs
            req = route.request
            u = urlparse(req.url)
            path = u.path
            if path.endswith("/desktoppolicy/overview"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(
                                  {"ok": True, "wallpapers": 2,
                                   "policies": 1, "delivered_7d": 3,
                                   "warn_7d": 1, "blocked_7d": 1,
                                   "failed_7d": 4}))
                return
            if path.endswith("/desktoppolicy/wallpapers"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(
                                  {"ok": True, "total": 2, "page": 1,
                                   "page_size": 100, "categories": ["宣传"],
                                   "wallpapers": [
                                       {"id": 1, "name": "主视觉",
                                        "category": "宣传", "width": 1920,
                                        "height": 1080, "aspect": 1.777778,
                                        "size_bytes": 1048576,
                                        "sha256": "a" * 64,
                                        "enabled": 1,
                                        "created_at": 1760000000,
                                        "created_by": "admin"},
                                       {"id": 2, "name": "默认兜底",
                                        "category": "default",
                                        "width": 3840, "height": 2160,
                                        "aspect": 1.777778,
                                        "size_bytes": 2097152,
                                        "sha256": "b" * 64,
                                        "enabled": 1,
                                        "created_at": 1760000000,
                                        "created_by": "admin"}]},
                                  ensure_ascii=False))
                return
            if path.endswith("/desktoppolicy/policies"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(
                                  {"ok": True, "policies": [
                                      {"id": 1, "name": "办公区基准",
                                       "group_id": None,
                                       "group_name": "全部终端",
                                       "payload": {
                                           "desktop_wallpaper": {
                                               "enabled": True,
                                               "mode": "stretch",
                                               "wallpaper_ids": [1]},
                                           "idle_lock": {
                                               "enabled": True,
                                               "minutes": 15}},
                                       "revision": 3, "enabled": 1,
                                       "updated_at": 1760000000,
                                       "updated_by": "admin"}]},
                                  ensure_ascii=False))
                return
            if path.endswith("/desktoppolicy/deliveries"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(
                                  {"ok": True, "total": 2, "page": 1,
                                   "page_size": 20, "deliveries": [
                                       {"id": 1, "policy_id": 1,
                                        "revision": 3,
                                        "terminal_id": "WIN-E2E-001",
                                        "status": "applied",
                                        "error_code": None,
                                        "error_detail": None,
                                        "reported_at": 1760001000,
                                        "created_at": 1760000000},
                                       {"id": 2, "policy_id": 1,
                                        "revision": 3,
                                        "terminal_id": "WIN-E2E-002",
                                        "status": "failed",
                                        "error_code":
                                            "blocked_by_security",
                                        "error_detail": "{\"results\":[]}",
                                        "reported_at": 1760001000,
                                        "created_at": 1760000000}]},
                                  ensure_ascii=False))
                return
            route.fulfill(status=200, content_type="application/json",
                          body='{"ok":true}')

        page.route("**/console/desktoppolicy/**", _dp_stub)
        page.locator('.tab[data-page="dpol"]').click()
        page.wait_for_selector("#dpOverview .m-card", timeout=5000)
        page.wait_for_timeout(700)
        check("dp tab present",
              page.locator('.tab[data-page="dpol"]').count() == 1)
        check("dp overview numbers",
              page.locator("#dpWallpapers").inner_text() == "2"
              and page.locator("#dpPolicies").inner_text() == "1"
              and page.locator("#dpDelivered").inner_text() == "3"
              and page.locator("#dpWarn").inner_text() == "1"
              and page.locator("#dpBlocked").inner_text() == "1"
              and page.locator("#dpFailed").inner_text() == "4")
        check("dp blocked card red",
              "v-err" in (page.locator("#dpBlocked").get_attribute("class")
                          or ""))
        check("dp wallpaper list rendered",
              page.locator("#dpWallBody tr").count() == 2
              and page.locator('#dpWallBody tr:has-text("主视觉")')
              .count() == 1)
        check("dp policy list rendered",
              page.locator("#dpPolicyBody tr").count() == 1
              and "办公区基准" in page.locator("#dpPolicyBody").inner_text()
              and "r3" in page.locator("#dpPolicyBody").inner_text())
        check("dp delivery status badges",
              page.locator('#dpDlvBody .badge:has-text("已生效")').count() == 1
              and page.locator('#dpDlvBody .badge:has-text("失败")')
              .count() == 1)
        check("dp blocked error badge red",
              page.locator('#dpDlvBody .b-eol:has-text("终端安全软件拦截")')
              .count() == 1)

        # 下发详情弹窗（取含 error_detail 的失败行）
        page.locator(
            "#dpDlvBody tr:has-text('终端安全软件拦截') .link-btn"
        ).first.click()
        page.wait_for_selector("#dpDetailModal", state="visible",
                               timeout=5000)
        check("dp detail modal shows json",
              "{" in page.locator("#dpDetailBody").inner_text())
        page.locator("#dpDetailModal button:has-text('关闭')").click()
        page.wait_for_timeout(300)
        page.unroute("**/console/desktoppolicy/**")

        # 8.97 资产卡片 → 终端详情弹窗（复用资产明细渲染 + 机型/心跳间隔/电源快照）
        page.route("**/console/powercontrol/**", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"ok": True, "snapshot": {
                "terminal_id": "WIN-SMOKE", "collected_ts": 1760000000,
                "schema_ver": 1, "capability": "enterprise_configurable",
                "vendor_line": "AC",
                "snapshot": {"machine": {
                    "manufacturer": "Dell Inc.",
                    "model": "OptiPlex 7090",
                    "system_family": "OptiPlex",
                    "capability": "enterprise_configurable"},
                    "rtc": {"alarm_on": False,
                            "after_power_loss": "power_on",
                            "summary": "off"},
                    "schema": 1}},
            }, ensure_ascii=False)))

        # 资产总览卡点击 → 详情弹窗（真后端 smoke 终端含 hwinfo asset）
        page.locator('.tab[data-page="assets"]').click()
        # 8.95 结束时选中了「未分组终端」组，再点一次取消选中回总览
        page.locator('#agTree .ag-node:has-text("未分组终端")').first.click()
        page.wait_for_selector("#assetCards .card[data-tid]", timeout=5000)
        page.locator("#assetCards .card[data-tid]").first.click()
        page.wait_for_selector("#assetModal", state="visible", timeout=5000)
        page.wait_for_timeout(900)
        body_txt = page.locator("#assetModalBody").inner_text()
        check("detail modal opens from card",
              page.locator("#assetModal").is_visible())
        check("detail three sections",
              "终端配置" in body_txt and "硬件状态" in body_txt
              and "本地网络" in body_txt)
        check("detail machine from power snapshot",
              "Dell" in body_txt and "OptiPlex" in body_txt, body_txt[:200])
        check("detail power snapshot card",
              "电源策略快照" in body_txt and "企业可配置" in body_txt)
        check("detail heartbeat interval filled",
              "秒" in (page.locator("[data-ad-hb]").inner_text() or "")
              or "--" in (page.locator("[data-ad-hb]").inner_text() or ""))

        # 缺字段容错：asset=null 终端（桩 detail）→ 降级注册摘要
        page.route("**/console/terminals/DP-ASSETLESS", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"ok": True, "terminal": {
                "terminal_id": "DP-ASSETLESS", "hostname": "无资产终端",
                "online": False, "client_version": "test"},
                "asset": None, "latest_metrics": None})))
        page.evaluate("adOpen('DP-ASSETLESS')")
        page.wait_for_timeout(700)
        body_txt2 = page.locator("#assetModalBody").inner_text()
        check("detail asset-less fallback",
              "终端配置（注册摘要）" in body_txt2
              and "硬件状态" not in body_txt2, body_txt2[:200])
        page.unroute("**/console/terminals/DP-ASSETLESS")
        page.locator("#assetModal button:has-text('关闭')").click()
        page.wait_for_timeout(300)

        # 1080 视口弹窗不溢出
        page.set_viewport_size({"width": 1080, "height": 900})
        page.locator("#assetCards .card[data-tid]").first.click()
        page.wait_for_selector("#assetModal", state="visible", timeout=5000)
        page.wait_for_timeout(400)
        box_w = page.evaluate(
            "() => document.querySelector('#assetModal > div').offsetWidth")
        check("detail modal fits 1080", box_w <= 1080 - 32, str(box_w))
        page.locator("#assetModal button:has-text('关闭')").click()
        page.set_viewport_size({"width": 1280, "height": 800})
        page.unroute("**/console/powercontrol/**")
        page.locator('.tab[data-page="monitor"]').click()

        # 8.98 开关机管控页（ADR-040；桩数据驱动）
        def _pw_stub(route):
            from urllib.parse import urlparse, parse_qs
            req = route.request
            u = urlparse(req.url)
            path = u.path
            if "/snapshots" in path:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"ok": True, "snapshot": {
                                  "terminal_id": "X", "collected_ts":
                                      1760000000,
                                  "capability":
                                      "enterprise_configurable",
                                  "vendor_line": "AC",
                                  "snapshot": {"machine": {
                                      "manufacturer": "Dell"},
                                      "rtc": {"alarm_on": True,
                                              "time": "08:30",
                                              "summary": "daily 08:30"},
                                      "schema": 1}}},
                              ensure_ascii=False))
                return
            if req.method == "POST" and path.endswith(
                    "/policies/dispatch"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(
                                  {"ok": True, "dispatch_id": 9,
                                   "policy_id": "pw-e2e",
                                   "total": 2, "queued_offline": 0}))
                return
            if path.endswith("/manual-configs") and req.method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"ok": True, "configs": [
                                  {"terminal_id": "WIN-MANUAL-E2E",
                                   "boot": {"enabled": True,
                                            "mode": "daily",
                                            "time": "07:50"},
                                   "shutdown": {},
                                   "note": "BIOS 人工配置（E2E 桩）",
                                   "operator": "admin",
                                   "updated_ts": 1760002000}]},
                                  ensure_ascii=False))
                return
            if req.method == "PUT" and "/manual-config" in path:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(
                                  {"ok": True,
                                   "config": {"terminal_id": "WIN-MAN-X",
                                              "boot": {"enabled": True,
                                                       "mode": "daily",
                                                       "time": "07:50"},
                                              "shutdown": {},
                                              "note": "E2E 手动登记",
                                              "operator": "admin",
                                              "updated_ts": 1760003000}},
                                  ensure_ascii=False))
                return
            if path.endswith("/policies/dispatches") and req.method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(
                                  {"ok": True, "dispatches": [
                                      {"id": 9, "policy_id": "pw-e2e",
                                       "operator": "admin",
                                       "payload": {"boot": {"enabled": True}},
                                       "note": "E2E 批次备注",
                                       "created_ts": 1760000000,
                                       "total": 2, "success": 1,
                                       "failed": 0, "rejected": 0,
                                       "queued": 1}]},
                                  ensure_ascii=False))
                return
            if "/policies/dispatches/" in path and req.method == "GET":
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(
                                  {"ok": True, "dispatch": {
                                      "id": 9, "policy_id": "pw-e2e",
                                      "operator": "admin",
                                      "payload": {"boot": {"enabled": True}},
                                      "note": "E2E 批次备注",
                                      "created_ts": 1760000000,
                                      "targets": [
                                          {"terminal_id": "WIN-SMOKE",
                                           "status": "success",
                                           "error_detail": None,
                                           "result": {"steps": {
                                               "bios": {"ok": True}}},
                                           "updated_ts": 1760001000},
                                          {"terminal_id": "WIN-E2E-001",
                                           "status": "offline_queued",
                                           "error_detail": None,
                                           "result": None,
                                           "updated_ts": 1760001000},
                                          {"terminal_id": "WIN-E2E-002",
                                           "status": "failed",
                                           "error_detail":
                                               "bios: BIOS 写入被拒绝",
                                           "result": {"steps": {
                                               "bios": {"ok": False,
                                                        "error": "BIOS 写入被拒绝"}}},
                                           "updated_ts": 1760001000}]}},
                                  ensure_ascii=False))
                return
            # 任务化（ADR-046）：任务列表 / 日历 / 每日列 / 下发桩
            if req.method == "GET" and path.endswith("/tasks"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"ok": True, "tasks": [
                                  {"id": 1, "kind": "boot",
                                   "name": "E2E 开机任务",
                                   "source": "platform",
                                   "origin": "platform",
                                   "target_type": "terminals",
                                   "targets": ["WIN-SMOKE"],
                                   "group_id": None, "repeat": "daily",
                                   "weekdays": "", "once_date": "",
                                   "time_hhmm": "07:30", "enabled": 1,
                                   "method": "auto",
                                   "last_run_date": "", "last_result": "",
                                   "target_summary": "指定终端 1 台"},
                                  {"id": 2, "kind": "shutdown",
                                   "name": "E2E 关机任务",
                                   "source": "platform",
                                   "origin": "platform",
                                   "target_type": "group",
                                   "targets": [], "group_id": 5,
                                   "repeat": "daily", "weekdays": "",
                                   "once_date": "", "time_hhmm": "22:00",
                                   "enabled": 1,
                                   "declared": {"enabled": True,
                                                "mode": "daily",
                                                "time": "22:00"},
                                   "last_run_date": "", "last_result": "",
                                   "target_summary": "办公区 · 2 台"}]},
                                  ensure_ascii=False))
                return
            if req.method == "GET" and path.endswith("/holidays"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"ok": True, "holidays": [
                                  {"date": "2026-10-01", "type": "holiday",
                                   "name": "国庆"}]}, ensure_ascii=False))
                return
            if req.method == "GET" and path.endswith("/holiday-status"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(
                                  {"ok": True, "status": {
                                      "year": 2026, "holiday_count": 0,
                                      "workday_count": 0,
                                      "covered": False}},
                                  ensure_ascii=False))
                return
            if req.method == "GET" and path.endswith("/daily-summary"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(
                                  {"ok": True, "map": {
                                      "WIN-SMOKE-TOKEN": {
                                          "boot": {"time": "07:30",
                                                   "total": 1}}}},
                                  ensure_ascii=False))
                return
            if req.method == "POST" and path.endswith("/dispatch"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(
                                  {"ok": True, "dispatch_id": 9,
                                   "total": 2, "queued_offline": 1},
                                  ensure_ascii=False))
                return
            route.fulfill(status=200, content_type="application/json",
                          body='{"ok":true}')

        page.route("**/console/powercontrol/**", _pw_stub)
        # 8.95 资产段已把每日列缓存置为真实空 map，此处重置以命中本段桩
        page.evaluate("pwDailyCache = null")
        check("pc tab present",
              page.locator('.tab[data-page="pc"]').count() == 1)
        page.locator('.tab[data-page="pc"]').click()
        page.wait_for_selector("#pwBody tr", timeout=5000)
        page.wait_for_timeout(900)
        check("pw terminal list rendered",
              page.locator("#pwBody tr").count() >= 2)
        check("pw capability badge",
              page.locator('#pwBody .badge:has-text("企业可配置")').count()
              >= 1)
        check("pw rtc summary",
              page.locator('#pwBody .badge:has-text("已启用")').count() >= 1)
        # ①② 增量：资产组筛选 + 每日开机列（任务化 ADR-046）
        check("pw group select present",
              page.locator("#pwGroupSel option:has-text('全部资产')")
              .count() == 1)
        check("pw daily boot column filled",
              page.locator('#pwBody td:has-text("07:30")').count() >= 1)
        # ③ 手动登记流：离线/未知行入口 → 弹窗 → 保存（桩回显）→ RTC 列区分手动
        page.locator("#pwBody button:has-text('手动登记')").first.click()
        page.wait_for_selector("#pwmModal", state="visible", timeout=5000)
        page.fill("#pwmNote", "E2E 手动登记")
        page.check("#pwmBootOn")
        page.locator("#pwmBootTime").fill("07:50")
        page.locator("#pwmModal button:has-text('保存登记')").click()
        page.wait_for_timeout(900)
        check("pw manual saved badge in rtc column",
              page.locator('#pwBody .badge:has-text("手动登记")').count() >= 1)
        # 8.985 pc_diag 联调通道（真打 dev 库：发起→pending→JSON 渲染）
        page.locator("#pwBody button:has-text('诊断')").first.click()
        page.wait_for_selector("#pwdModal", state="visible", timeout=5000)
        page.locator("#pwdLaunchBtn").click()
        page.wait_for_timeout(1500)
        diag_txt = page.locator("#pwdBody").inner_text()
        check("pw diag record rendered",
              "待终端拉取" in diag_txt or "已完成" in diag_txt
              or "失败" in diag_txt, diag_txt[:200])
        check("pw diag launch button restored",
              page.locator("#pwdLaunchBtn").is_enabled())
        page.locator("#pwdModal button:has-text('关闭')").click()
        page.wait_for_timeout(300)
        # 8.982 任务卡断言（任务为中心，ADR-046：列表/徽章/日历/向导）
        check("pt task table rendered",
              page.locator("#ptBody tr").count() >= 2)
        check("pt kind badges",
              page.locator('#ptBody .badge:has-text("开机")').count() >= 1
              and page.locator('#ptBody .badge:has-text("关机")').count()
              >= 1)
        check("pt calendar default semantics note",
              "默认周一至周五" in page.locator("#page-pc").inner_text()
              and "可选" in page.locator("#page-pc").inner_text())
        check("pt holiday table rendered",
              page.locator("#ptHolBody tr").count() >= 1)
        # 向导：开合 → 步骤条 → 关机第三方说明/置灰 → 组目标切换
        page.evaluate("ptOpenWizard()")
        page.wait_for_selector("#ptModal", state="visible", timeout=5000)
        check("pt wizard step1 visible",
              page.locator("#ptS1").is_visible()
              and "类型" in page.locator("#ptSteps").inner_text())
        page.locator(
            '#ptModal input[name="ptKind"][value="shutdown"]').check()
        page.evaluate("ptStep(1)")
        page.wait_for_timeout(200)
        check("pt wizard step2 visible", page.locator("#ptS2").is_visible())
        check("pt shutdown third-party note",
              page.locator("#ptSrcShutdownNote").is_visible())
        check("pt third-party source disabled",
              page.locator(
                  '#ptModal input[name="ptSource"][value="huorong"]')
              .is_disabled())
        page.evaluate("ptStep(1)")
        page.wait_for_timeout(200)
        page.locator("#ptTargetType").select_option("group")
        check("pt group select rendered",
              page.locator("#ptGroupSel option").count() >= 1)
        page.evaluate("ptClose()")
        page.wait_for_timeout(200)

        # 8.59 向导 · 第三方资产源开放（ADR-047 批 B）
        page.evaluate("ptOpenWizard()")
        page.wait_for_selector("#ptModal", state="visible", timeout=5000)
        page.locator(
            '#ptModal input[name="ptKind"][value="boot"]').check()
        page.evaluate("ptStep(1)")
        page.wait_for_timeout(250)
        check("pt third-party sources enabled for boot task",
              not page.locator(
                  '#ptModal input[name="ptSource"][value="huorong"]')
              .is_disabled()
              and not page.locator(
                  '#ptModal input[name="ptSource"][value="nad"]')
              .is_disabled())
        page.locator(
            '#ptModal input[name="ptSource"][value="huorong"]').check()
        page.wait_for_timeout(400)
        check("pt huorong source selectable + groups endpoint ok",
              page.evaluate("ptWizSource()") == "huorong")
        page.locator('#ptModal input[name="ptSource"][value="nad"]').check()
        page.wait_for_timeout(300)
        check("pt nad source disables group mode (no stable group id)",
              page.evaluate(
                  "document.querySelector('#ptTargetType option[value=group]')"
                  ".disabled") is True)
        # 端点可达性（诊断式：失败时给出真实 HTTP 状态/形状，便于定位）
        _src_probe = """async (u) => {
            try {
                const r = await fetch('/api/v1' + u, {headers: {
                    'X-ETP-Console-Token':
                        sessionStorage.getItem('etp_console_token') || ''}});
                if (!r.ok) return 'HTTP ' + r.status;
                const j = await r.json();
                return (j && typeof j === 'object') ? 'OK' : 'SHAPE';
            } catch (e) { return 'ERR ' + e.message; }
        }"""
        _nad_ok = page.evaluate(
            _src_probe,
            "/console/powercontrol/src-terminals?source=nad&page_size=5")
        check("pt nad src-terminals endpoint reachable", _nad_ok == "OK",
              str(_nad_ok))
        _hr_ok = page.evaluate(
            _src_probe, "/console/powercontrol/src-groups?source=huorong")
        check("pt huorong src-groups endpoint reachable", _hr_ok == "OK",
              str(_hr_ok))
        page.evaluate("ptClose()")
        page.wait_for_timeout(200)
        # 关机任务下发确认（danger uiConfirm 语义沿既有桩批次链）
        page.locator(
            '#ptBody tr:has-text("E2E 关机任务") button:has-text("下发")'
        ).first.click()
        page.wait_for_selector("#uiConfirmOverlay", state="visible",
                               timeout=5000)
        check("pt dispatch confirm scheduled wording",
              "非立即关机" in page.locator("#uiConfirmText").inner_text(),
              page.locator("#uiConfirmText").inner_text())
        page.locator("#uiConfirmOk").click()
        page.wait_for_timeout(900)
        check("pt dispatch toast",
              page.locator("#toast").inner_text().strip()
              == "已下发：2 台（离线排队 1 台）",
              page.locator("#toast").inner_text())
        check("pw batch list row",
              page.locator("#pwBatchBody tr").count() == 1)
        page.unroute("**/console/powercontrol/**")
        page.locator('.tab[data-page="monitor"]').click()
        page.wait_for_timeout(300)

        # 8.99 终端删除（高危确认 + 级联勾选语义；桩驱动）
        page.locator('.tab[data-page="assets"]').click()
        page.wait_for_selector("#assetCards .card[data-tid]", timeout=5000)
        check("delete entry on asset card",
              page.locator("#assetCards [data-del]").count() >= 1)
        del_reqs = []
        page.route("**/console/terminals/DEL-E2E**", lambda r: (
            del_reqs.append(r.request.url),
            r.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"ok": True, "purged": True}))))
        page.evaluate("tdelOpen('DEL-E2E')")
        page.wait_for_selector("#tdelModal", state="visible", timeout=5000)
        check("delete confirm modal shows tid",
              "DEL-E2E" in page.locator("#tdelInfo").inner_text())
        check("purge checkbox default checked",
              page.locator("#tdelPurge").is_checked())
        page.locator("#tdelModal button:has-text('确认删除')").click()
        page.wait_for_timeout(700)
        check("delete executed with purge",
              page.locator("#toast").inner_text().strip()
              == "终端已删除（含历史数据）"
              and any("purge=1" in u for u in del_reqs),
              page.locator("#toast").inner_text())
        check("delete modal closed",
              not page.locator("#tdelModal").is_visible())
        page.unroute("**/console/terminals/DEL-E2E**")
        page.locator('.tab[data-page="monitor"]').click()
        page.wait_for_timeout(300)

        # 8.99 客户端发布页（ADR-042；桩数据驱动，admin-only）
        def _cr_stub(route):
            from urllib.parse import urlparse
            req = route.request
            path = urlparse(req.url).path
            if req.method == "GET" and path.endswith(
                    "/console/client/releases"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"ok": True, "releases": [
                                  {"id": 2, "version": "4.1.0",
                                   "filename": "EyeTerm_Setup_x64_4.1.0.exe",
                                   "sha256": "b" * 64, "size": 27682406,
                                   "published_at": 1760001000,
                                   "rollback_flag": 0, "note": "当前版本",
                                   "is_current": True},
                                  {"id": 1, "version": "4.0.0",
                                   "filename": "EyeTerm_Setup_x64_4.0.0.exe",
                                   "sha256": "a" * 64, "size": 26319204,
                                   "published_at": 1750000000,
                                   "rollback_flag": 1, "note": "",
                                   "is_current": False}]},
                                  ensure_ascii=False))
                return
            if req.method == "POST" and path.endswith(
                    "/console/client/custom-name"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({
                                  "ok": True, "version": "4.1.0",
                                  "filename": "EyeTerm_Setup_x64_4.1.0_"
                                              "eNqrVipWslLKKCkpsNLXNzQ30jM01zPV"
                                              "MzI0tTK0MLA0UNJRKgHKl-RnGxoZK9UC"
                                              "ABEgC2E_4036e788.exe",
                                  "download_url":
                                      "/download/client/setup?ticket=stub1",
                                  "expires_in": 600}))
                return
            # 更新推送（2026-09-19）：批次列表 / 批次明细 / 下发
            # 注意匹配顺序——batches 分支必须早于 push 分支
            if req.method == "GET" and path.endswith(
                    "/console/client/push/batches"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"ok": True, "batches": [
                                  {"id": 7, "mode": "notify",
                                   "target_version": "4.1.0",
                                   "targets": ["PUSH-E2E-OK", "PUSH-E2E-NG"],
                                   "total": 2, "created_ts": 1760002000,
                                   "operator": "admin", "note": "",
                                   "stats": {"pending": 0, "sent": 0,
                                             "executed": 1, "failed": 1,
                                             "timeout": 0, "total": 2}}]},
                                  ensure_ascii=False))
                return
            if req.method == "GET" and "/console/client/push/batches/" in path:
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"ok": True, "batch": {
                                  "id": 7, "mode": "notify",
                                  "target_version": "4.1.0", "total": 2,
                                  "created_ts": 1760002000, "operator": "admin",
                                  "stats": {},
                                  "items": [
                                      {"terminal_id": "PUSH-E2E-OK",
                                       "ip": "10.0.0.1",
                                       "client_version": "4.0.0",
                                       "status": "executed", "ok": True,
                                       "mode": "notify",
                                       "apply_status": "ready",
                                       "version": "4.1.0",
                                       "note": "ready_await_user"},
                                      {"terminal_id": "PUSH-E2E-NG",
                                       "ip": "10.0.0.2",
                                       "client_version": "4.0.0",
                                       "status": "failed", "ok": False,
                                       "mode": "notify",
                                       "error": "download_failed"}]}},
                                  ensure_ascii=False))
                return
            if req.method == "POST" and path.endswith("/console/client/push"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"ok": True, "batch_id": 8,
                                               "mode": "notify",
                                               "version": "4.1.0",
                                               "total": 2, "online": 1,
                                               "offline": 1}))
                return
            route.fulfill(status=200, content_type="application/json",
                          body='{"ok":true}')

        page.route("**/console/client/**", _cr_stub)
        check("release tab present & visible for admin",
              page.locator('.tab[data-page="release"]').count() == 1
              and page.locator('.tab[data-page="release"]').is_visible())
        page.locator('.tab[data-page="release"]').click()
        page.wait_for_selector("#crBody tr", timeout=5000)
        page.wait_for_timeout(600)
        check("release page form rendered",
              page.is_visible("#crVer") and page.is_visible("#crFile")
              and page.is_visible("#crNote") and page.is_visible("#crServer")
              and page.is_visible("#crToken"))
        check("release list rows rendered",
              page.locator("#crBody tr").count() == 2)
        check("release current badge",
              page.locator('#crBody .badge:has-text("当前版本")').count() == 1)
        check("release rollback badge",
              page.locator('#crBody .badge:has-text("曾回滚")').count() == 1)
        check("release sha256 short display",
              "…" in page.locator("#crBody").inner_text())

        # 8.991 更新推送（客户端发布页 · 主动下发；2026-09-19）
        #   覆盖：终端勾选列表 / 模式选择 / 全量与批量按钮 / 勾选联动计数 /
        #   推送记录 / 批次明细展开。真实下发链路（服务端入队 + 端侧执行）由
        #   tools/test_client_release.py 与端侧 tools/test_uplink_client_update.py
        #   单测覆盖，此处只做控制台契约门禁。
        page.wait_for_timeout(400)
        ck = page.locator("#crPushBody .cr-push-ck")
        check("update push terminal list rendered", ck.count() >= 1,
              "checkbox=%d" % ck.count())
        check("update push mode selector has notify/silent",
              page.locator("#crPushMode option").count() == 2
              and page.locator('#crPushMode option[value="notify"]').count() == 1
              and page.locator('#crPushMode option[value="silent"]').count() == 1)
        check("update push buttons present",
              page.is_visible("#crPushAllBtn")
              and page.is_visible("#crPushSelBtn"))
        check("push-to-selected disabled before checking",
              page.locator("#crPushSelBtn").is_disabled())
        page.locator("#crPushAllCk").check()
        page.wait_for_timeout(150)
        check("push-to-selected enabled & counted after select-all",
              (not page.locator("#crPushSelBtn").is_disabled())
              and ("（%d）" % ck.count())
                  in page.locator("#crPushSelBtn").inner_text(),
              page.locator("#crPushSelBtn").inner_text())
        check("update push history rendered",
              page.locator("#crPushHistBody tr").count() >= 1
              and "#7" in page.locator("#crPushHistBody").inner_text())
        page.locator("#crPushHistBody .link-btn").first.click()
        page.wait_for_selector("#crPushDetail table", timeout=5000)
        _pdtxt = page.locator("#crPushDetail").inner_text()
        check("push batch detail rendered with per-terminal rows",
              ("PUSH-E2E-OK" in _pdtxt) and ("PUSH-E2E-NG" in _pdtxt),
              _pdtxt[:80])
        check("push batch detail shows failure reason",
              "download_failed" in _pdtxt)
        page.locator("#crPushDetail .btn").click()
        page.wait_for_timeout(150)
        check("push batch detail collapsed",
              not page.locator("#crPushDetail").is_visible())

        page.locator("#crGenBtn").click()
        page.wait_for_selector("#crCustomResult", state="visible",
                               timeout=5000)
        fname = page.locator("#crCustomName").input_value()
        check("custom name protocol format",
              re.match(r"^EyeTerm_Setup_x64_4\.1\.0_[A-Za-z0-9_-]+"
                       r"_[0-9a-f]{8}\.exe$", fname) is not None, fname)
        check("custom download url with ticket",
              page.locator("#crCustomUrl").input_value().endswith(
                  "/download/client/setup?ticket=stub1"))
        page.unroute("**/console/client/**")
        page.locator('.tab[data-page="monitor"]').click()
        page.wait_for_timeout(300)

        # 8.995 第三方数据源（资产右键菜单 + 三弹窗，ADR-043；真打降级路径）
        page.route("**/console/thirdparty/**", lambda r: r.continue_())
        page.locator('.tab[data-page="assets"]').click()
        page.wait_for_selector("#assetCards .card[data-tid]", timeout=5000)
        page.wait_for_timeout(500)
        # 直接右键 → IP 在线日志查询（不先开信息弹窗）：预填 = 该终端卡片 IP
        page.locator(
            '#assetCards .card[data-tid="%s"]' % SEED_TID).first.click(
            button="right")
        page.wait_for_selector("#tpMenu", state="visible", timeout=5000)
        check("tp context menu shows three entries",
              page.locator("#tpMenu button:has-text('第三方数据源信息')")
              .count() == 1
              and page.locator("#tpMenu button:has-text('IP 在线日志查询')")
              .count() == 1
              and page.locator("#tpMenu button:has-text('终端隔离')")
              .count() == 1)
        page.locator("#tpMenu button:has-text('IP 在线日志查询')").click()
        page.wait_for_selector("#tpLogModal", state="visible", timeout=5000)
        page.wait_for_timeout(900)
        # 固定使用种子终端（首卡终端随 smoke 重灌漂移；时间线断言需要
        # 有新鲜 metrics 的确定性目标）
        tid_e2e = SEED_TID
        check("tp target card exists",
              page.locator('#assetCards .card[data-tid="%s"]' % SEED_TID)
              .count() >= 1)
        api_ip = page.evaluate(
            "fetch('/api/v1/console/thirdparty/'"
            " + encodeURIComponent('" + tid_e2e + "'),"
            " {headers:{'X-ETP-Console-Token':"
            " sessionStorage.getItem('etp_console_token') || token}})"
            ".then(function(r){ return r.json(); })")
        exp_ip = ((api_ip.get("nad") or {}).get("query") or {}).get("ip") \
            or ((api_ip.get("terminal") or {}).get("ip") or "")
        exp_mac = ((api_ip.get("nad") or {}).get("query") or {}).get("mac") \
            or ""
        filled_ip = page.locator("#tpLogIp").input_value()
        check("tp log prefilled ip from card (direct entry)",
              filled_ip != "" and filled_ip == exp_ip,
              "filled=%r expected=%r" % (filled_ip, exp_ip))
        check("tp log prefilled mac matches api",
              page.locator("#tpLogMac").input_value() == exp_mac,
              "filled=%r expected=%r" % (
                  page.locator("#tpLogMac").input_value(), exp_mac))
        page.locator("#tpLogModal button:has-text('查询')").click()
        page.wait_for_timeout(900)
        log_txt = page.locator("#tpLogBody").inner_text()
        check("tp log skeleton fallback",
              "尚未接入" in log_txt, log_txt[:200])
        # 过渡数据源：平台通信时间线区块（dev 库该终端有冒烟指标数据）
        check("tp log platform timeline rendered",
              "平台通信时间线" in log_txt and "不代表" in log_txt,
              log_txt[:300])
        page.locator("#tpLogModal button:has-text('关闭')").click()
        page.wait_for_timeout(300)
        # 信息弹窗（两区块 + 骨架标记）
        page.locator(
            '#assetCards .card[data-tid="%s"]' % SEED_TID).first.click(
            button="right")
        page.wait_for_selector("#tpMenu", state="visible", timeout=5000)
        page.locator("#tpMenu button:has-text('第三方数据源信息')").click()
        page.wait_for_selector("#tpModal", state="visible", timeout=5000)
        page.wait_for_timeout(900)
        body_txt = page.locator("#tpModalBody").inner_text()
        check("tp modal two sections rendered",
              "火绒安全" in body_txt and "画方准入" in body_txt, body_txt[:200])
        check("tp huorong unlinked fallback",
              "未关联" in body_txt and "已关联" not in body_txt)
        check("tp nad not-configured fallback",
              "不可用" in body_txt or "未配置" in body_txt, body_txt[:300])
        check("tp skeleton marks present",
              "IP 在线日志" in body_txt and "终端隔离" in body_txt)
        # 弹窗内日志按钮（tpLast 快路径）：预填仍一致
        page.locator("#tpModal button:has-text('IP 在线日志查询…')").click()
        page.wait_for_selector("#tpLogModal", state="visible", timeout=5000)
        page.wait_for_timeout(400)
        check("tp log prefilled via tpLast path",
              page.locator("#tpLogIp").input_value() == exp_ip,
              page.locator("#tpLogIp").input_value())
        page.locator("#tpLogModal button:has-text('关闭')").click()
        page.wait_for_timeout(300)
        # 终端隔离：空密码前端拦截 + 错密码 403 + 正确密码 501 降级
        page.locator("#tpModal button:has-text('终端隔离…')").click()
        page.wait_for_selector("#tpIsoModal", state="visible", timeout=5000)
        page.locator("#tpIsoBtn").click()
        page.wait_for_timeout(400)
        check("tp iso empty pwd blocked",
              "请输入" in page.locator("#tpIsoMsg").inner_text())
        page.fill("#tpIsoPwd", "definitely-wrong-pwd")
        page.locator("#tpIsoBtn").click()
        page.wait_for_timeout(1200)
        check("tp iso wrong pwd rejected", "未通过" in
              page.locator("#tpIsoMsg").inner_text()
              or "口令" in page.locator("#tpIsoMsg").inner_text()
              or "错误" in page.locator("#tpIsoMsg").inner_text(),
              page.locator("#tpIsoMsg").inner_text()[:120])
        page.fill("#tpIsoPwd", PASSWORD)
        page.locator("#tpIsoBtn").click()
        page.wait_for_timeout(1500)
        iso_txt = page.locator("#tpIsoMsg").inner_text()
        check("tp iso correct pwd -> nad api pending",
              "待接入" in iso_txt, iso_txt[:160])
        page.locator("#tpIsoModal button:has-text('取消')").click()
        page.wait_for_timeout(300)
        page.locator("#tpModal button:has-text('关闭')").click()
        page.wait_for_timeout(300)
        # 查看隔离终端（骨架降级 toast）
        page.locator("#tpIsoFilterBtn").click()
        page.wait_for_timeout(600)
        check("tp isolated filter toast", "隔离" in
              (page.locator("#toast").inner_text() or ""))

        # 8.996 画方匹配态渲染（桩：责任人/接入位置/枚举可读化）
        def _tp_full_stub(route):
            from urllib.parse import urlparse
            req = route.request
            if urlparse(req.url).path.endswith(
                    "/console/thirdparty/" + tid_e2e):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(
                                  {"ok": True,
                                   "terminal": {"terminal_id": tid_e2e,
                                                "hostname": "TP-FULL",
                                                "ip": "172.17.90.215"},
                                   "huorong": {"linked": False},
                                   "nad": {"available": True,
                                           "reason": None, "matched": True,
                                           "fetched_at": 1760000000,
                                           "query": {"ip": "172.17.90.215",
                                                     "mac": "F4:F1:9E:3C:"
                                                            "69:E4",
                                                     "asset_ip":
                                                         "172.17.90.215",
                                                     "source_ip":
                                                         "172.17.90.215"},
                                           "evidence": [{
                                               "source": "nad",
                                               "oid": "8899",
                                               "name": "心脏彩超室办公电脑",
                                               "ou": "总院/门诊楼/1F",
                                               "ttype": "普通终端",
                                               "manfct": "浪潮",
                                               "model": "",
                                               "online": 1, "block": 0,
                                               "owner": {"name": "赵四",
                                                         "uuid": "19122"},
                                               "owner_name": "赵四",
                                               "owner_uuid": "19122",
                                               "onlts": 1789620000,
                                               "reginfo": {"stat": 2},
                                               "reginfo_stat": 2,
                                               "macs": [{
                                                   "mac": "F4:F1:9E:3C:"
                                                          "69:E4",
                                                   # 2 个 IP：1 个命中检索依据、1 个历史
                                                   "ips": ["172.17.11.26",
                                                           "172.17.90.215"],
                                                   # 3 条端口记录，其中 b/Gi1/0/30
                                                   # 重复一次 → 去重后应为 2 条
                                                   "macports": [
                                                       {"nasoid": "a",
                                                        "nasif": "Gi1/0/36",
                                                        "nasname": "SW0753 "
                                                                   "SW6",
                                                        "manip":
                                                            "192.168.254.6"},
                                                       {"nasoid": "b",
                                                        "nasif": "Gi1/0/30",
                                                        "nasname": "SW0660 "
                                                                   "SW5",
                                                        "manip":
                                                            "192.168.254.5"},
                                                       {"nasoid": "b",
                                                        "nasif": "Gi1/0/30",
                                                        "nasname": "SW0660 "
                                                                   "SW5",
                                                        "manip":
                                                            "192.168.254.5"}]
                                                   }]}]},
                                   "online_log": {"available": False,
                                                  "reason": "stub"},
                                   "isolate": {"available": False,
                                               "reason": "stub"}},
                                  ensure_ascii=False))
                return
            route.continue_()

        page.route("**/console/thirdparty/**", _tp_full_stub)
        page.evaluate("tpClose && tpClose()")   # 幂等关闭（防前段残留）
        page.locator(
            '#assetCards .card[data-tid="%s"]' % SEED_TID).first.click(
            button="right")
        page.wait_for_selector("#tpMenu", state="visible", timeout=5000)
        page.locator("#tpMenu button:has-text('第三方数据源信息')").click()
        page.wait_for_selector("#tpModal", state="visible", timeout=5000)
        page.wait_for_timeout(700)
        full_txt = page.locator("#tpModalBody").inner_text()
        check("tp nad owner rendered",
              "赵四" in full_txt and "19122" in full_txt, full_txt[:200])
        check("tp nad access location rendered",
              "SW0753 SW6" in full_txt and "Gi1/0/36" in full_txt
              and "192.168.254.6" in full_txt
              and "SW0660 SW5" in full_txt, full_txt[:300])
        # 接入位置展示加固（2026-09-19）：去重 + "当前性"标识。
        # 背景：生产实测准入侧 ips 与 macports **非一一对应**（1335 个有端口记录的
        # MAC 中 376 个 len(ips)≠len(macports)，且存在同端口重复记录），故前端
        # 不得按索引臆断对应关系 —— 去重后**唯一**才标「当前接入位置」；多条须
        # 如实标注为含历史的记录集合，并给出核实手段（不虚构确定性）。
        check("tp nad ip chips distinguish query-vs-history",
              "172.17.90.215 · 检索依据" in full_txt
              and "172.17.11.26 · 历史" in full_txt, full_txt[:400])
        check("tp nad access points deduplicated",
              "原始 3 条" in full_txt and "去重后 2 条" in full_txt,
              full_txt[:400])
        check("tp nad multi points labeled as history set",
              "接入点记录 2 条" in full_txt
              and "当前接入位置" not in full_txt, full_txt[:400])
        check("tp nad states no port-level ip attribution",
              "未提供端口级 IP 归属" in full_txt and "深度检测" in full_txt,
              full_txt[:400])
        check("tp nad stat readable mapping",
              "已注册" in full_txt and "状态码 2" not in full_txt,
              full_txt[:300])
        check("tp nad last online row",
              "最近在线" in full_txt)
        page.evaluate("tpClose && tpClose()")   # 关弹窗，防拦截后续右键
        page.unroute("**/console/thirdparty/**", _tp_full_stub)

        # 8.9965 火绒关联卡新增字段（登记信息 5 字段 + 首次上线/上次关机/
        # 本次开机；ADR-033 增补三。桩形态对齐 store.hr_context_block 响应）
        tp_hr_state = {"registered": True}

        def _tp_hr_stub(route):
            from urllib.parse import urlparse
            req = route.request
            if urlparse(req.url).path.endswith(
                    "/console/thirdparty/" + tid_e2e):
                assets = ({"楼层": "3", "具体位置": "门诊楼3F东区",
                           "工号": "1024", "姓名": "张三",
                           "使用科室": "检验科"}
                          if tp_hr_state["registered"] else None)
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps(
                                  {"ok": True,
                                   "terminal": {"terminal_id": tid_e2e,
                                                "hostname": "TP-FULL",
                                                "ip": "172.17.90.215"},
                                   "huorong": {
                                       "linked": True, "match_type": "mac",
                                       "bound_ts": 1760000000,
                                       "group_id": 1,
                                       "group_name": "防护组",
                                       "group_path": "防护组",
                                       "client": {
                                           "client_id": "HR-E2E-01",
                                           "name": "视光-01",
                                           "computer_name": "PC01",
                                           "ip": "172.17.90.215",
                                           "connect_ip": "10.1.1.9",
                                           "mac": "F4:F1:9E:3C:69:E4",
                                           "online": True,
                                           "os": "Windows 10 专业版",
                                           "hr_version": "2.0.7.5",
                                           "last_seen": 1760000000,
                                           "first_seen": 1691459644,
                                           "last_off": 1691126727,
                                           "this_on": 1691126737,
                                           "updated_at": 1760000000},
                                       "assets": assets, "virus": None},
                                   "nad": {"available": False,
                                           "reason": "stub"},
                                   "online_log": {"available": False,
                                                  "reason": "stub"},
                                   "isolate": {"available": False,
                                               "reason": "stub"}},
                                  ensure_ascii=False))
                return
            route.continue_()

        page.route("**/console/thirdparty/**", _tp_hr_stub)

        def _open_tp_modal():
            page.evaluate("tpClose && tpClose()")   # 幂等关闭（防前段残留）
            page.locator(
                '#assetCards .card[data-tid="%s"]' % SEED_TID).first.click(
                button="right")
            page.wait_for_selector("#tpMenu", state="visible", timeout=5000)
            page.locator(
                "#tpMenu button:has-text('第三方数据源信息')").click()
            page.wait_for_selector("#tpModal", state="visible", timeout=5000)
            page.wait_for_timeout(700)

        _open_tp_modal()
        hr_body = page.locator("#tpModalBody").inner_text()
        row_fs = page.locator(
            "#tpModalBody tr:has-text('首次上线')").inner_text()
        check("hr card time rows rendered",
              re.search(r"\d{4}-\d{2}-\d{2}", row_fs) is not None
              and "上次关机" in hr_body and "本次开机" in hr_body
              and "最近连上火绒" in hr_body,
              hr_body[:300])
        row_lc = page.locator(
            "#tpModalBody tr:has-text('使用科室')").inner_text()
        row_fl = page.locator("#tpModalBody tr:has-text('楼层')").inner_text()
        check("hr card registered fields rendered",
              "检验科" in row_lc and "门诊楼3F东区" in hr_body
              and "1024" in hr_body and "张三" in hr_body
              and "3" in row_fl and "未登记" not in hr_body,
              hr_body[:300])
        tp_hr_state["registered"] = False
        _open_tp_modal()
        hr_body2 = page.locator("#tpModalBody").inner_text()
        check("hr card unregistered empty state",
              page.locator(
                  "#tpModalBody td:has-text('未登记')").count() == 5,
              hr_body2[:300])
        page.unroute("**/console/thirdparty/**", _tp_hr_stub)
        page.locator("#tpModal button:has-text('关闭')").click()
        page.locator('.tab[data-page="monitor"]').click()
        page.wait_for_timeout(300)

        # 8.997 4.1.5 批次：power_action / wol_relay / WoL 定时唤醒
        tok = page.evaluate(
            "() => sessionStorage.getItem('etp_console_token')")

        def _api(method, path, body=None):
            body_js = (", body: JSON.stringify(%s)" % json.dumps(body)
                       if body is not None else "")
            js = ("() => fetch('%s/api/v1%s', {method:'%s', headers:{"
                  "'Content-Type':'application/json',"
                  "'X-ETP-Console-Token':'%s','X-ETP-Token':'%s'}%s})"
                  ".then(r => r.text().then(t => [r.status, t]))"
                  % (BASE, path, method, tok, TOKEN, body_js))
            st, txt = page.evaluate(js)
            try:
                return st, json.loads(txt)
            except ValueError:
                return st, {"raw": txt[:200]}

        st, j = _api("GET", "/console/terminals")
        tids = [t["terminal_id"] for t in (j.get("terminals") or [])]
        check("4.1.5 terminals available", bool(tids))
        # 注册在线终端（register 刷新 last_seen）——power-action 校验矩阵
        # 需要先过 400 校验再论 409 离线守卫，离线目标会使 400 用例误得 409
        st, j = _api("POST", "/terminals/register",
                     {"terminal_id": "WIN-E2E-415",
                      "terminal_type": "windows",
                      "hostname": "e2e-415-online",
                      "client_version": "4.1.9"})
        check("4.1.5 register online terminal", st == 200, str(st))
        tid415 = "WIN-E2E-415"

        # WoL 定时唤醒 CRUD 往返
        st, j = _api("POST", "/console/powercontrol/wol/schedules",
                     {"terminal_id": tid415, "time": "7:30"})
        check("4.1.5 wol schedule invalid time 400", st == 400, str(st))
        st, j = _api("POST", "/console/powercontrol/wol/schedules",
                     {"terminal_id": tid415, "time": "23:58",
                      "name": "e2e-415"})
        check("4.1.5 wol schedule create", st == 200 and j.get("ok"),
              "%s %s" % (st, j))
        sid415 = j.get("schedule_id") or (
            (j.get("schedule") or {}).get("id"))
        st, j = _api("GET", "/console/powercontrol/wol/schedules")
        check("4.1.5 wol schedule listed",
              any(s.get("id") == sid415 for s in (j.get("schedules") or [])))
        st, j = _api("PUT", "/console/powercontrol/wol/schedules/%s" % sid415,
                     {"enabled": False})
        check("4.1.5 wol schedule disable",
              st == 200 and j.get("schedule", {}).get("enabled") == 0,
              str(st))
        st, j = _api("DELETE",
                     "/console/powercontrol/wol/schedules/%s" % sid415)
        check("4.1.5 wol schedule delete", st == 200, str(st))

        # 中继选举（读）+ 直发参数校验
        st, j = _api("GET",
                     "/console/powercontrol/wol/relays?terminal_id=" + tid415)
        check("4.1.5 wol relays shape",
              st == 200 and j.get("ok")
              and (j.get("target") or {}).get("broadcasts"),
              "%s %s" % (st, j))
        st, j = _api("POST", "/console/powercontrol/wol/direct",
                     {"terminal_id": tid415, "mac": "zz:bb:cc"})
        check("4.1.5 wol direct bad mac 400", st == 400, str(st))
        st, j = _api("POST", "/console/powercontrol/wol/direct",
                     {"terminal_id": tid415, "mac": "aa-bb-cc-dd-ee-ff",
                      "broadcast": "192.168.1.255"})
        check("4.1.5 wol direct normalized", st == 200
              and j.get("mac") == "AABBCCDDEEFF", "%s %s" % (st, j))

        # power_action 参数校验 + 在线守卫（200 下发 / 409 离线均合规）
        st, j = _api("POST",
                     "/console/powercontrol/terminals/%s/power-action"
                     % tid415, {"action": "format"})
        check("4.1.5 power-action bad action 400", st == 400, str(st))
        st, j = _api("POST",
                     "/console/powercontrol/terminals/%s/power-action"
                     % tid415, {"action": "shutdown", "delay_sec": 99999})
        check("4.1.5 power-action delay range 400", st == 400, str(st))
        st, j = _api("POST",
                     "/console/powercontrol/terminals/%s/power-action"
                     % tid415, {"action": "restart", "delay_sec": 60})
        check("4.1.5 power-action dispatch or offline guard",
              st in (200, 409), str(st))

        # UI：pw 页新卡片与两个弹窗可开合
        page.locator('.tab[data-page="pc"]').click()
        page.wait_for_timeout(700)
        check("4.1.5 pw wol card rendered",
              page.locator("#pwWolBody").count() == 1
              and page.locator("#pwWolAttBody").count() == 1)
        page.evaluate("pwWolAddOpen()")
        page.wait_for_timeout(200)
        check("4.1.5 pwol modal opens", page.locator("#pwolModal").is_visible())
        page.evaluate("pwWolAddClose()")
        page.evaluate("pwWolRelayOpen()")
        page.wait_for_timeout(600)
        check("4.1.5 pwr modal opens", page.locator("#pwrModal").is_visible())
        page.evaluate("pwWolRelayClose()")

        # 9. 宽度自适应门禁（STYLE.md §9：1920/1440/1080 三档 × 六页铺满）
        sec_samples = {}
        for width in (1920, 1440, 1080):
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(350)
            no_hscroll = page.evaluate(
                "() => document.documentElement.scrollWidth"
                " <= document.documentElement.clientWidth")
            check("no horizontal scroll @%d" % width, no_hscroll)
            # AI 页降级断点（≤1200 单列堆叠；>1200 双栏）
            page.locator('.tab[data-page="ai"]').click()
            page.wait_for_timeout(300)
            cols = page.evaluate(
                "() => (getComputedStyle("
                "document.querySelector('.ai-split'))"
                ".gridTemplateColumns || '').split(' ').filter("
                "v => v && v !== 'none').length")
            if width <= 1200:
                check("ai split single column @%d" % width, cols == 1,
                      str(cols))
            else:
                check("ai split two columns @%d" % width, cols == 2,
                      str(cols))
            # 六页主体宽度随视口变化（流式铺满，非固定像素）
            for pg in ("nettest", "config", "kb", "sysadmin", "dpol", "pc"):
                page.locator('.tab[data-page="%s"]' % pg).click()
                page.wait_for_timeout(150)
                sec_w = page.evaluate(
                    "() => document.querySelector('.page.active .section')"
                    ".offsetWidth")
                check("%s section fluid @%d" % (pg, width),
                      0 < sec_w <= width, str(sec_w))
                sec_samples.setdefault(pg, []).append(sec_w)
            page.locator('.tab[data-page="monitor"]').click()
        for pg, ws in sec_samples.items():
            check("%s section width varies" % pg,
                  ws[0] > ws[2] and ws[0] != ws[1], str(ws))

        # 10. 页面 JS 零错误
        check("no page errors", len(errors) == 0, "; ".join(errors[:3]))

        browser.close()

    print("\n=== e2e summary ===")
    print("PASS: %d  FAIL: %d" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("failed: %s" % ", ".join(FAILED))
        sys.exit(1)
    print("ALL E2E TESTS PASSED")


if __name__ == "__main__":
    main()
