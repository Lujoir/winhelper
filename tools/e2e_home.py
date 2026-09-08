# -*- coding: utf-8 -*-
"""
主页（终端概览）E2E 门禁（Playwright chromium）
================================================
场景 1（主应用默认加载）：file:// web/index.html + pywebview 桩
  → 主页默认激活 → 断言 initHomeTab/hm* 函数、三卡数据填充、pageerror=none
场景 2（菜单往返）：主页 → 日志诊断/磁盘清理/性能分析 → 回主页
  → 断言 disk/perf/loginspector 关键函数仍可用、主页数据保持、pageerror=none

依赖：pip install playwright && playwright install chromium
运行：python tools/e2e_home.py
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent   # 工作区根
MAIN_INDEX = ROOT / "web" / "index.html"

STUB_JS = r"""
window.__stubCalls = [];
window.__stubHwinfo = {
    source: "cim",
    hostname: "STUB-PC",
    os: { caption: "Stub Windows 11 Pro", version: "10.0.26200", build: "26200", text: "Stub Windows 11 Pro" },
    cpu: { name: "Stub CPU i9-13900K", cores: 24, logical: 32, max_mhz: 5400, cur_mhz: 3500 },
    memory: { total: 34359738368, modules: [
        { slot: "DIMM_A1", size: 17179869184, type: "DDR5", speed_mhz: 5600 } ] },
    disks: [
        { name: "PhysicalDrive0", model: "Stub NVMe SSD 1TB", size: 1024203640320, bus: "NVMe", media: "SSD",
          volumes: ["C:\\"], system: true } ],
    gpu: [
        { name: "Stub GeForce RTX 4090", dedicated: true, vram_text: "24 GB", driver: "566.36", resolution: "3840x2160" } ]
};
window.__stubSnapshot = {
    success: true, ts: 1,
    cpu: { percent: 23.5, count: 32, freq_mhz: 3500 },
    memory: { total: 34359738368, used: 12000000000, available: 22000000000, percent: 35.2,
              total_text: "32.0 GB", available_text: "20.5 GB" },
    swap: { total: 1, used: 0, percent: 2.0 },
    disks: [], volumes: [
        { mount: "C:\\", total: 1024203640320, used: 512101820160, free: 512101820160, percent: 50.5 } ]
};
window.__stubTemps = { success: true, admin: false,
    cpu: { available: false, temp_c: null, reason: "not_admin" },
    gpu: { available: true, temp_c: 46.5, reason: null } };
window.__stubUplink = { success: true, uplink: {
    enabled: true, running: true, server_url: "http://172.17.5.215:18090",
    terminal_id: "WIN-STUB-PC", state: "connected", registered: true,
    last_hb_ts: 1788870000, last_error: null, has_token: true,
    executed_count: 1, iperf3_available: true, heartbeat_interval: 30, client_version: "4.0.0" } };
window.__stubNetwork = { success: true, source: "powershell", adapters: [
    { name: "以太网", desc: "Stub Intel I226-V", status: "Up", mac: "AA-BB-CC-DD-EE-FF",
      speed: "1.0 Gbps", ipv4: ["192.168.1.10"], plen: [24], gw: "192.168.1.1",
      dns: ["192.168.1.1", "223.5.5.5"] },
    { name: "WLAN", desc: "Stub Wi-Fi 6E", status: "Down", mac: "11-22-33-44-55-66",
      speed: "--", ipv4: [], plen: [], gw: null, dns: [] } ] };
window.pywebview = { api: {
    call: function (path) {
        window.__stubCalls.push(path);
        return new Promise(function (resolve) {
            if (path.indexOf("/api/perf/hwinfo") === 0) return resolve(window.__stubHwinfo);
            if (path.indexOf("/api/perf/snapshot") === 0) return resolve(window.__stubSnapshot);
            if (path.indexOf("/api/perf/temps") === 0) return resolve(window.__stubTemps);
            if (path.indexOf("/api/perf/uplink/status") === 0) return resolve(window.__stubUplink);
            if (path.indexOf("/api/home/network") === 0) return resolve(window.__stubNetwork);
            return resolve({ success: true });
        });
    } } };
"""

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name + ("" if cond else "  ← " + str(detail)[:160]))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  | " + str(detail)[:120]))


def run_scenario_1(page):
    print("\n[场景1] 主应用默认加载（主页默认激活）")
    page.goto(MAIN_INDEX.as_uri())
    page.wait_for_timeout(1200)

    check("入口函数存在", page.evaluate("typeof initHomeTab") == "function")
    check("hm 渲染函数存在", page.evaluate("typeof hmRenderNetwork") == "function"
          and page.evaluate("typeof hmRenderUplink") == "function"
          and page.evaluate("typeof hmRenderLive") == "function")
    check("主页为激活 tab", page.evaluate(
        "document.getElementById('tab-home').classList.contains('active')"))

    os_txt = page.inner_text("#homeOsBody")
    check("终端配置·系统信息", "STUB-PC" in os_txt and "Stub Windows 11" in os_txt, os_txt[:80])
    check("终端配置·CPU", "Stub CPU i9-13900K" in os_txt, os_txt[:80])
    up_txt = page.inner_text("#homeUplinkBody")
    check("终端配置·平台对接已连接", "已连接" in up_txt and "WIN-STUB-PC" in up_txt, up_txt[:80])
    check("终端配置·服务地址", "172.17.5.215" in up_txt, up_txt[:80])

    live_txt = page.inner_text("#homeLiveBody")
    check("硬件·CPU使用率", "CPU 使用率" in live_txt and "23.5%" in live_txt, live_txt[:80])
    check("硬件·内存", "35.2%" in live_txt and "32.0 GB" in live_txt, live_txt[:80])
    check("硬件·分区容量", "C:\\" in live_txt and "50.5%" in live_txt, live_txt[:80])
    temps_txt = page.inner_text("#homeTempsBody")
    check("硬件·GPU温度", "46.5 ℃" in temps_txt, temps_txt[:80])
    hw_txt = page.inner_text("#homeHwStatic")
    check("硬件·GPU规格", "Stub GeForce RTX 4090" in hw_txt and "24 GB" in hw_txt, hw_txt[:80])
    check("硬件·磁盘规格", "Stub NVMe SSD 1TB" in hw_txt and "系统盘" in hw_txt, hw_txt[:80])

    net_txt = page.inner_text("#homeNetBody")
    check("网络·网卡状态", "以太网" in net_txt and "已连接" in net_txt, net_txt[:80])
    check("网络·IP掩码", "192.168.1.10/24" in net_txt, net_txt[:80])
    check("网络·网关", "192.168.1.1" in net_txt, net_txt[:80])
    check("网络·DNS", "223.5.5.5" in net_txt, net_txt[:80])
    check("网络·断开网卡", "WLAN" in net_txt and "断开" in net_txt, net_txt[:80])
    check("接口调用次数>=5", len(page.evaluate("window.__stubCalls")) >= 5,
          page.evaluate("window.__stubCalls.join(',')"))


def run_scenario_2(page):
    print("\n[场景2] 菜单往返")
    for tab, fn in (("loginspector", "initLogInspectorTab"),
                    ("disk", "initDiskTab"),
                    ("perf", "initPerfTab")):
        page.click('button[data-tab="%s"]' % tab)
        page.wait_for_timeout(500)
        check("切换到 %s 后函数保持" % tab,
              page.evaluate("typeof %s" % fn) == "function")
    page.click('button[data-tab="home"]')
    page.wait_for_timeout(600)
    check("back-home-active", page.evaluate("document.getElementById('tab-home').classList.contains('active')"))
    check("home-data-kept-net", "\u4ee5\u592a\u7f51" in page.inner_text("#homeNetBody"))
    check("home-data-kept-live", "23.5%" in page.inner_text("#homeLiveBody"))


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(STUB_JS)
        try:
            run_scenario_1(page)
            run_scenario_2(page)
        finally:
            browser.close()
        print("\n[JS errors] pageerror count = %d" % len(errors))
        for e in errors:
            print("  JS ERROR:", e[:200])
        check("no-pageerror", len(errors) == 0, errors)

    total = len(PASS) + len(FAIL)
    print("\n===== RESULT: %d/%d passed =====" % (len(PASS), total))
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
        sys.exit(1)
    print("E2E HOME GATE: PASS")


if __name__ == "__main__":
    main()
