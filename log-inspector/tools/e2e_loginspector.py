# -*- coding: utf-8 -*-
"""
Log Inspector E2E（Playwright + pywebview 桩，发布四连门禁）
============================================================
场景1 standalone：file:// 打开 web/loginspector-standalone.html，
      断言全流程（检索→统计/图表/表格→下载任务→分析→知识库→报告导出），pageerror=none。
场景2 主应用：file:// 打开主应用 web/index.html，3 菜单遍历
      （日志诊断/磁盘清理/性能分析），断言 disk/perf 关键函数 typeof=function 保持 +
      新函数 initLogInspectorTab 在列 + 数据填充，pageerror=none。

运行: python tools/e2e_loginspector.py
"""

import json
import os
import sys

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = os.path.dirname(ROOT)                     # 工作区根（主应用所在）
STANDALONE = os.path.join(ROOT, "web", "loginspector-standalone.html")
MAIN_INDEX = os.path.join(WS, "web", "index.html")

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    if cond:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL  {name}  {detail}")


# ============================================================
# pywebview 桩：按路由返回假数据（真实结构）
# ============================================================

def _events(n, offset=0, level_cycle=("error", "warning", "info", "critical")):
    out = []
    for i in range(n):
        lv = level_cycle[(i + offset) % len(level_cycle)]
        lv_name = {"critical": "关键", "error": "错误", "warning": "警告", "info": "信息"}[lv]
        out.append({
            "id": offset + i + 1,
            "event_id": [41, 1000, 1027, 0][i % 4],
            "source": ["Microsoft-Windows-WindowsUpdateClient", "Service Control Manager", "disk", "Kernel-Power"][i % 4],
            "level_name": lv_name,
            "level_class": lv if lv != "critical" else "danger",
            "timestamp": f"2026-09-06 10:{(i % 60):02d}:00",
            "description": f"模拟事件描述 {i}：服务启动失败（disk timeout）。\n" + f"详细堆栈信息行 {i} —— 组件初始化异常，错误代码 0x80070057，请检查依赖服务与配置项是否完整。" * 3,
            "log_type": "系统日志",
            "is_known": i % 4 == 0,
            "known_name": "系统意外重启（Kernel-Power）" if i % 4 == 0 else "",
        })
    return out


MOCK = {
    "/api/loginspector/access": {
        "success": True,
        "access": {"System": True, "Application": True, "Security": False, "Setup": True},
        "denied": [{"type": "Security", "name": "安全日志", "error": "拒绝访问"}],
    },
    "/api/loginspector/search": {
        "success": True,
        "events": _events(12),
        "summary": {
            "total": 137, "critical": 3, "error": 31, "warning": 48, "info": 55, "unknown": 0,
            "by_hour_labels": ["09-06 08:00", "09-06 09:00", "09-06 10:00"],
            "by_hour_values": [30, 55, 52],
            "top_events": [
                {"name": "Microsoft-Windows-WindowsUpdateClient", "count": 60},
                {"name": "Service Control Manager", "count": 40},
                {"name": "disk", "count": 37},
            ],
        },
        "total": 137, "page": 1, "per_page": 50, "total_pages": 3, "truncated": False,
        "sources": ["Microsoft-Windows-WindowsUpdateClient", "Service Control Manager", "disk"],
        "errors": [], "denied": [],
    },
    "/api/loginspector/export-start": {"success": True, "task_id": "e2e123", "reused": False},
    "/api/loginspector/export-status": {"poll": True},
    "/api/loginspector/export-cancel": {"success": True, "cancelled": True},
    "/api/loginspector/analyze": {
        "success": True,
        "conclusion": {"level": "critical", "text": "发现严重故障模式，建议立即处理"},
        "patterns": [
            {
                "name": "磁盘故障", "count": 5, "severity": "critical",
                "evidence": [{"timestamp": "2026-09-06 09:12:00", "event_id": 129,
                              "source": "disk", "level_name": "错误",
                              "description": "重置到设备 Device\\RaidPort0"}],
                "suggestions": ["立即备份重要数据", "运行 chkdsk /f /r 检查修复"],
            },
            {
                "name": "服务异常", "count": 8, "severity": "warning",
                "evidence": [{"timestamp": "2026-09-06 08:40:00", "event_id": 7031,
                              "source": "Service Control Manager", "level_name": "警告",
                              "description": "服务意外终止"}],
                "suggestions": ["检查服务依赖链"],
            },
        ],
        "known_issues": _events(3),
        "known_count": 9,
        "summary": {"total": 137, "critical": 3, "error": 31, "warning": 48, "info": 55,
                    "by_hour_labels": ["09-06 08:00"], "by_hour_values": [137],
                    "top_events": [{"name": "disk", "count": 37}]},
        "conditions": "近 72 小时", "scanned": 137, "truncated": False,
        "errors": [], "denied": [], "generated_at": "2026-09-06 10:30:00",
    },
    "/api/loginspector/report-export": {
        "success": True, "path": "C:\\Users\\tester\\Downloads\\LogAnalysis_20260906_103000.html",
        "html_size": 12345, "filename": "LogAnalysis_20260906_103000.html",
        "dir": "C:\\Users\\tester\\Downloads",
    },
    "/api/loginspector/knowledge": {
        "success": True, "q": "",
        "knowledge": [
            {"name": "磁盘故障", "severity": "critical", "event_ids": [7, 51, 129],
             "sources": ["disk", "Ntfs"], "keywords": ["disk"],
             "suggestions": ["立即备份重要数据，磁盘可能存在物理故障", "运行 chkdsk /f /r"]},
            {"name": "网络故障", "severity": "warning", "event_ids": [1014, 1015],
             "sources": ["DnsApi"], "keywords": ["dns"],
             "suggestions": ["运行网络诊断工具"]},
        ],
    },
    "/api/perf/hwinfo": {
        "success": True,
        "hwinfo": {
            "os": {"caption": "Microsoft Windows 11 专业版", "version": "10.0.26100",
                   "build": "26100", "text": "Microsoft Windows 11 专业版 24H2 (26100.2314)"},
            "hostname": "EYE-TEST-PC",
            "network": [
                {"name": "以太网", "ipv4": "172.17.9.215", "ipv6": "fe80::1", "mac": "AA-BB-CC-DD-EE-FF",
                 "status": "up", "gateway": "172.17.9.1", "dns": "172.17.7.107"},
            ],
            "cpu": {"name": "Intel(R) Core(TM) i7-10700 CPU @ 2.90GHz", "cores": 8, "logical": 16,
                    "max_mhz": 4800, "cur_mhz": 2904},
            "memory": {"total": 34359738368,
                       "modules": [{"slot": "ChannelA-DIMM0", "size": 17179869184, "type": "DDR4", "speed_mhz": 3200},
                                   {"slot": "ChannelB-DIMM0", "size": 17179869184, "type": "DDR4", "speed_mhz": 3200}]},
            "disks": [{"name": "PhysicalDrive0", "model": "Samsung SSD 980 1TB", "size": 1000204886016,
                       "bus": "NVMe", "media": "SSD", "volumes": ["C:\\"], "system": True}],
            "gpu": [{"name": "NVIDIA GeForce RTX 3060", "dedicated": True, "vram_text": "12 GB", "driver": "566.36", "resolution": "2560 x 1440"},
                    {"name": "Intel(R) UHD Graphics 630", "dedicated": False, "vram_text": "共享系统内存", "driver": "31.0.101.5333", "resolution": "--"}],
            "source": "cim",
        },
    },
    "/api/disk/open-location": {"success": True, "path": "C:\\Users\\tester\\Downloads"},
    # disk 桩（主应用场景）
    "/api/disk/overview": {
        "success": True, "drive": "C:", "total": 500 * 2**30, "total_text": "500 GB",
        "used": 320 * 2**30, "used_text": "320 GB", "free": 180 * 2**30, "free_text": "180 GB",
        "used_percent": 64.0,
    },
    "/api/disk/drives": {"success": True, "drives": [{"letter": "C:", "total": 500 * 2**30, "free": 180 * 2**30},
                                                     {"letter": "D:", "total": 1000 * 2**30, "free": 600 * 2**30}]},
    "/api/disk/scan": {"success": True, "scan_id": "scanX", "reused": False},
    "/api/disk/scan-status": {
        "success": True,
        "task": {"id": "scanX", "kind": "junk", "status": "done",
                 "progress": {}, "error": None, "elapsed": 1.2,
                 "result": {"success": True, "categories": [], "total_freed": 0,
                            "total_freed_text": "0 B", "total_deleted": 0, "total_failed": 0}},
    },
    "/api/installers/scan": {"success": True, "scan_id": "insX", "reused": False},
    "/api/appdata/scan": {"success": True, "scan_id": "adX", "reused": False},
    "/api/appdata/drives": {"success": True, "drives": [{"letter": "D:"}]},
    # perf 桩（主应用场景）——结构对齐 perf_service.handle_perf_snapshot / perf.js perfRenderSnapshot
    "/api/perf/snapshot": {
        "success": True,
        "cpu": {"percent": 12.5, "freq_mhz": 2904, "count": 16},
        "memory": {"percent": 46.0, "available_gb": 18.6, "available_text": "18.6 GB"},
        "swap": {"percent": 3.2},
        "disks": [{"name": "PhysicalDrive0", "read_mb_s": 1.2, "write_mb_s": 0.8,
                   "iops": 128, "busy_pct": 5.0}],
        "volumes": [{"mount": "C:\\", "total": 500 * 2**30, "used": 320 * 2**30,
                     "free": 180 * 2**30, "percent": 64.0}],
        "gpu_temp": 45, "cpu_temp": None,
        "ts": "2026-09-06 10:30:00",
    },
    "/api/perf/temps": {"success": True, "admin": False,
                        "cpu": {"available": False, "reason": "need_admin"},
                        "gpu": {"available": True, "temp_c": 45}},
}


def install_stub(page, export_status_sequence):
    """注入 pywebview 桩：window.pywebview.api.call(path) 按路由返回假数据"""
    seq = json.dumps(export_status_sequence)
    stub = """
    (function () {
      const MOCK = %s;
      const SEQ = %s;
      let seqIdx = 0;
      function parse(p) {
        const i = p.indexOf('?');
        const path = i >= 0 ? p.slice(0, i) : p;
        const qs = {};
        if (i >= 0) p.slice(i + 1).split('&').forEach(kv => {
          const [k, v] = kv.split('=');
          qs[decodeURIComponent(k)] = decodeURIComponent(v || '');
        });
        return [path, qs];
      }
      const api = {
        call: function (path) {
          const [p, qs] = parse(path);
          let data;
          if (p === '/api/loginspector/export-status') {
            data = SEQ[Math.min(seqIdx, SEQ.length - 1)];
            seqIdx += 1;
          } else if (p === '/api/loginspector/knowledge') {
            const q = (qs.q || '').toLowerCase();
            let ks = MOCK[p].knowledge;
            if (q) ks = ks.filter(k =>
              k.name.toLowerCase().includes(q) ||
              (k.event_ids || []).some(id => String(id).includes(q)) ||
              (k.keywords || []).some(w => String(w).toLowerCase().includes(q)));
            data = { success: true, q: q, knowledge: ks };
          } else {
            data = MOCK[p];
          }
          if (data === undefined) data = { success: false, error: 'mock-missing:' + p };
          return Promise.resolve(JSON.parse(JSON.stringify(data)));
        }
      };
      Object.defineProperty(window, 'pywebview', {
        value: { api: api }, configurable: true
      });
    })();
    """ % (json.dumps(MOCK, ensure_ascii=False), seq)
    page.add_init_script(stub)


def _goto(page, path):
    page.goto("file:///" + path.replace("\\", "/"))
    page.wait_for_timeout(900)


# ============================================================
# 场景1：standalone
# ============================================================

def run_standalone(pw):
    print("\n[场景1] standalone 全流程")
    browser = pw.chromium.launch()
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    install_stub(page, [
        {"success": True, "task": {"id": "e2e123", "status": "running",
                                   "progress": {"written": 120, "scanned": 400},
                                   "result": None, "error": None, "elapsed": 0.8}},
        {"success": True, "task": {"id": "e2e123", "status": "done",
                                   "progress": {"written": 137, "scanned": 402},
                                   "result": {"path": "C:\\Users\\tester\\Downloads\\LogExport_20260906_103000.txt",
                                              "filename": "LogExport_20260906_103000.txt",
                                              "count": 137, "size": 20480,
                                              "dir": "C:\\Users\\tester\\Downloads"},
                                   "error": None, "elapsed": 1.6}},
    ])
    _goto(page, STANDALONE)

    # 权限探测置灰
    check("standalone.pageerror.initial", len(errors) == 0, "; ".join(errors))
    sec_disabled = page.evaluate("document.querySelector(\".li-type[value='Security']\").disabled")
    check("standalone.security.denied.disabled", sec_disabled is True)
    tip_visible = page.evaluate("document.getElementById('liDeniedTip').style.display !== 'none'")
    check("standalone.security.denied.tip", tip_visible)

    # 自动检索（init 触发）
    page.wait_for_timeout(600)
    total_badge = page.text_content("#liTotalBadge") or ""
    check("standalone.search.totalBadge", "137" in total_badge, total_badge)
    stats_vals = page.evaluate("Array.from(document.querySelectorAll('#liStats .stat-value')).map(e=>e.textContent)")
    check("standalone.search.stats", len(stats_vals) == 5 and stats_vals[0] == "137", str(stats_vals))
    rows = page.evaluate("document.querySelectorAll('#liBody tr').length")
    check("standalone.search.tableRows", rows == 12, str(rows))
    check("standalone.desc.clamp", page.evaluate(
        "(() => { const d = document.querySelector('#liBody .li-desc'); "
        "return !!d && d.classList.contains('li-desc-clamp') && "
        "getComputedStyle(d).webkitUserSelect === 'text'; })()"), "首条描述收缩态且user-select=text")
    page.click("#liBody .li-desc-toggle")
    check("standalone.desc.expand", page.evaluate(
        "!document.querySelector('#liBody .li-desc').classList.contains('li-desc-clamp')"), "点击展开后描述全文可见")
    page.click("#liBody .li-desc-toggle")
    check("standalone.desc.collapse", page.evaluate(
        "document.querySelector('#liBody .li-desc').classList.contains('li-desc-clamp')"), "再次点击恢复收缩")
    canvas = page.evaluate("typeof Chart === 'function' && !!document.getElementById('liTimeChart')")
    check("standalone.search.chartCanvas", canvas)
    page_info = page.text_content("#liPageInfo") or ""
    check("standalone.search.pageInfo", "第 1/3 页" in page_info, page_info)
    step2 = page.evaluate("document.querySelectorAll('#liFlow .li-step.on').length")
    check("standalone.flow.step2", step2 >= 2, str(step2))

    # 手动检索（条件读取 + 翻页）
    page.select_option("#liTimeRange", "24")
    page.click("#liSearchBtn")
    page.wait_for_timeout(500)
    cond_badge = page.text_content("#liCondBadge") or ""
    check("standalone.condBadge", "近24小时" in cond_badge, cond_badge)

    # 下载任务流程（running → done）
    page.click("#liExportBtn")
    page.wait_for_timeout(1300)
    result_html = page.inner_html("#liExportResult")
    check("standalone.export.result", "LogExport_20260906_103000.txt" in result_html, result_html[:120])
    check("standalone.export.count", "137" in (page.text_content("#liExportText") or ""))
    open_btn = page.evaluate("!!document.querySelector('#liExportResult button[data-p]')")
    check("standalone.export.openBtn", open_btn)
    step3 = page.evaluate("document.querySelectorAll('#liFlow .li-step.on').length")
    check("standalone.flow.step3", step3 >= 3, str(step3))

    # 智能分析
    page.click("#liAnalysisBtn")
    page.wait_for_timeout(600)
    concl = page.inner_html("#liConclusion")
    check("standalone.analyze.conclusion", "严重" in concl and "发现严重故障模式" in concl, concl[:120])
    patterns = page.evaluate("document.querySelectorAll('#liPatterns .fault-card').length")
    check("standalone.analyze.patterns", patterns == 2, str(patterns))
    known_rows = page.evaluate("document.querySelectorAll('#liKnownBody tr').length")
    check("standalone.analyze.knownRows", known_rows == 3, str(known_rows))
    report_btn_visible = page.evaluate("document.getElementById('liReportBtn').style.display !== 'none'")
    check("standalone.analyze.reportBtn", report_btn_visible)
    # 知识库建议区自动填充
    kb_cards = page.evaluate("document.querySelectorAll('#liKnowledgeGrid .knowledge-card').length")
    check("standalone.kb.autoFilled", kb_cards == 2, str(kb_cards))

    # 知识库自由检索
    page.fill("#liKbQuery", "129")
    page.click("text=🔍 检索知识库")
    page.wait_for_timeout(500)
    kb_after = page.evaluate("document.querySelectorAll('#liKnowledgeGrid .knowledge-card').length")
    check("standalone.kb.filter", kb_after == 1, str(kb_after))

    # HTML 报告导出按钮
    page.click("#liReportBtn")
    page.wait_for_timeout(500)
    note = page.inner_html("#liConclusion")
    check("standalone.report.note", "LogAnalysis_20260906_103000.html" in note, note[:160])

    # 本机信息面板
    page.click("#modeBadge")
    page.wait_for_timeout(600)
    panel_visible = page.evaluate("getComputedStyle(document.getElementById('liSysPanel')).display")
    check("standalone.syspanel.visible", panel_visible == "flex", panel_visible)
    os_row = page.inner_html("#liSysPanel .li-sys-body")
    check("standalone.syspanel.os", "Windows 11" in os_row, os_row[:120])
    check("standalone.syspanel.net", "172.17.9.215" in os_row)
    check("standalone.syspanel.gpu", "独显" in os_row)
    check("standalone.syspanel.gpuSpec", "显存" in os_row and "驱动" in os_row, "显卡规格（显存/驱动）展示")
    check("standalone.syspanel.netStatus", "在线" in os_row, "网卡在线状态展示")
    page.click("#liSysPanel", position={"x": 6, "y": 6})
    page.wait_for_timeout(300)
    panel_hidden = page.evaluate("getComputedStyle(document.getElementById('liSysPanel')).display")
    check("standalone.syspanel.close", panel_hidden == "none", panel_hidden)

    check("standalone.pageerror.final", len(errors) == 0, "; ".join(errors))
    browser.close()


# ============================================================
# 场景2：主应用 3 菜单遍历
# ============================================================

FUNC_CHECKS = [
    # (函数名, 来源模块)
    ("initLogInspectorTab", "loginspector"),
    ("liSearch", "loginspector"),
    ("liStartExport", "loginspector"),
    ("liRunAnalysis", "loginspector"),
    ("liFilterKnowledge", "loginspector"),
    ("liShowSystemPanel", "loginspector"),
    ("apiFetch", "app.js 传输层"),
    ("initCollapsibleCards", "app.js"),
    ("toggleAllCards", "app.js"),
    ("escapeHtml", "app.js 公共工具"),
    ("showError", "app.js 公共工具"),
    ("initDiskTab", "disk.js"),
    ("startJunkScan", "disk.js"),
    ("cleanSelected", "disk.js"),
    ("scanInstallers", "disk.js"),
    ("startLargeScan", "disk.js"),
    ("cancelLargeScan", "disk.js"),
    ("renderTreemap", "disk.js"),
    ("treemapUp", "disk.js"),
    ("startAppdataScan", "appdata.js"),
    ("adMigrateSelected", "appdata.js"),
    ("initPerfTab", "perf.js"),
    ("perfStartRecord", "perf.js"),
    ("perfStopRecord", "perf.js"),
    ("startPerfStress", "perf.js"),
    ("cancelPerfStress", "perf.js"),
]

DEAD_FUNCS = [
    "reloadData", "updateDashboard", "drawTimeChart", "drawSourceChart",
    "loadLogs", "renderLogsTable", "loadAnalysis", "renderFaults",
    "loadKnowledge", "getSeverityLabel",
]


def run_main_app(pw):
    print("\n[场景2] 主应用 3 菜单遍历")
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    install_stub(page, [
        {"success": True, "task": {"id": "e2e123", "status": "done",
                                   "progress": {"written": 137, "scanned": 402},
                                   "result": {"path": "C:\\Users\\tester\\Downloads\\LogExport_x.txt",
                                              "filename": "LogExport_x.txt", "count": 137,
                                              "size": 20480, "dir": "C:\\Users\\tester\\Downloads"},
                                   "error": None, "elapsed": 1.0}},
    ])
    _goto(page, MAIN_INDEX)
    page.wait_for_timeout(700)

    # 导航形态：3 按钮
    tabs = page.evaluate("Array.from(document.querySelectorAll('.nav-tab')).map(e=>e.dataset.tab)")
    check("main.nav.3tabs", tabs == ["loginspector", "disk", "perf"], str(tabs))
    check("main.nav.defaultActive", page.evaluate("document.getElementById('tab-loginspector').classList.contains('active')"))
    dead_selector_gone = page.evaluate("!document.getElementById('logTypeSelector')")
    check("main.nav.logTypeSelector.removed", dead_selector_gone)
    badge_text = (page.text_content("#modeBadge") or "").strip()
    check("main.modeBadge.windows", badge_text == "Windows", badge_text)

    # 函数清单 typeof=function
    missing = []
    for fn, _src in FUNC_CHECKS:
        t = page.evaluate(f"typeof {fn}")
        if t != "function":
            missing.append(f"{fn}={t}")
    check("main.funcs.allPresent", not missing, ", ".join(missing))

    # 旧 tab 专属函数已删除
    alive = []
    for fn in DEAD_FUNCS:
        t = page.evaluate(f"typeof {fn}")
        if t == "function":
            alive.append(fn)
    check("main.deadFuncs.removed", not alive, ", ".join(alive))

    # ---- 日志诊断菜单 ----
    page.click("[data-tab='loginspector']")
    page.wait_for_timeout(800)
    rows = page.evaluate("document.querySelectorAll('#liBody tr').length")
    check("main.loginspector.tableFilled", rows == 12, str(rows))
    stats0 = page.evaluate("document.querySelector('#liStats .stat-value').textContent")
    check("main.loginspector.stats", stats0 == "137", stats0)
    page.click("#liExportBtn")
    page.wait_for_timeout(900)
    check("main.loginspector.export", "已生成" in page.inner_html("#liExportResult"))
    page.click("#liAnalysisBtn")
    page.wait_for_timeout(600)
    check("main.loginspector.analyze", "fault-card" in page.inner_html("#liPatterns"))

    # 本机信息面板（主应用 modeBadge）
    page.click("#modeBadge")
    page.wait_for_timeout(600)
    check("main.syspanel", page.evaluate(
        "getComputedStyle(document.getElementById('liSysPanel')).display") == "flex")
    page.click("#liSysPanel", position={"x": 6, "y": 6})

    # ---- 磁盘清理菜单 ----
    page.click("[data-tab='disk']")
    page.wait_for_timeout(900)
    disk_total = (page.text_content("#diskTotal") or "").strip()
    check("main.disk.overviewFilled", disk_total not in ("", "--"), disk_total)
    junk_grid = page.evaluate("document.querySelectorAll('#junkGrid .junk-card').length")
    check("main.disk.junkRendered", junk_grid >= 0, str(junk_grid))

    # ---- 性能分析菜单 ----
    page.click("[data-tab='perf']")
    page.wait_for_timeout(900)
    cpu_val = (page.text_content("#perfCpuVal") or "").strip()
    check("main.perf.snapshotFilled", cpu_val not in ("", "--"), cpu_val)
    perf_active = page.evaluate("document.getElementById('tab-perf').classList.contains('active')")
    check("main.perf.tabActive", perf_active)

    check("main.pageerror.final", len(errors) == 0, "; ".join(errors))
    browser.close()


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else "all"   # all | standalone | main
    if only in ("all", "standalone") and not os.path.exists(STANDALONE):
        print(f"缺少 standalone 页面: {STANDALONE}")
        sys.exit(2)
    if only in ("all", "main") and not os.path.exists(MAIN_INDEX):
        print(f"缺少主应用页面: {MAIN_INDEX}")
        sys.exit(2)
    with sync_playwright() as pw:
        if only in ("all", "standalone"):
            run_standalone(pw)
        if only in ("all", "main"):
            run_main_app(pw)
    print("\n================ 汇总 ================")
    print(f"通过 {len(PASSED)} / 失败 {len(FAILED)}")
    if FAILED:
        for f in FAILED:
            print("  FAIL:", f)
        sys.exit(1)
    print("E2E 全部通过")


if __name__ == "__main__":
    main()
