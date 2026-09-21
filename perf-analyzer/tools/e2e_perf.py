# -*- coding: utf-8 -*-
"""
Performance Analyzer E2E（Playwright chromium，验证门禁 ADR-006）
================================================================
场景 1（standalone）：file:// 加载 web/perf-standalone.html + pywebview 桩
  → 断言关键函数 typeof=function、实时数据填充、图表实例、记录开始/状态/停止/报告全流程、pageerror=none
场景 2（主应用）：file:// 加载 ../web/index.html + 桩
  → 依次点击全部 6 个菜单 → 断言 disk/appdata/app/perf 关键函数仍全部可用 + 数据填充 + pageerror=none

依赖：pip install playwright && playwright install chromium
运行：python tools/e2e_perf.py
"""

import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent          # perf-analyzer/
WORKSPACE = ROOT.parent                                 # 工作区根
STANDALONE = ROOT / "web" / "perf-standalone.html"
MAIN_INDEX = WORKSPACE / "web" / "index.html"

# ---------------------------------------------------------------------------
# pywebview 桩：add_init_script 注入，页面脚本执行前生效。
# 按路由返回带状态假数据（record-start 后 status 递增、stop 返回完整报告）。
# ---------------------------------------------------------------------------
STUB_JS = r"""
window.__stubCalls = [];
window.__stubState = { recording: false, samples: 0, startedAt: 0, cpuTick: 0 };
window.__stubStress = { mode: null, calls: 0 };
window.__stubTemps = { admin: false };
window.__stubHwinfo = {
    source: "cim",
    cpu: { name: "Stub CPU i9-13900K", cores: 24, logical: 32, max_mhz: 5400, cur_mhz: 3500 },
    memory: { total: 34359738368, modules: [
        { slot: "DIMM_A1", size: 17179869184, type: "DDR5", speed_mhz: 5600 },
        { slot: "DIMM_B1", size: 17179869184, type: "DDR5", speed_mhz: 5600 } ] },
    disks: [
        { name: "PhysicalDrive0", model: "Stub NVMe SSD 1TB", size: 1024203640320, bus: "NVMe", media: "SSD",
          volumes: ["C:\\"], system: true },
        { name: "PhysicalDrive1", model: "Stub HDD 4TB", size: 4000784417280, bus: "SATA", media: "HDD",
          volumes: ["D:\\"], system: false } ],
    gpu: [
        { name: "Stub GeForce RTX 4090", dedicated: true, vram_text: "24 GB", driver: "566.36", resolution: "3840x2160" },
        { name: "Stub UHD Graphics 770", dedicated: false, vram_text: "共享系统内存", driver: "31.0.101.5333", resolution: "--" } ]
};
window.__stubStressResult = function (mode) {
    const stages = {};
    stages.disk = { status: "done", drive: "C:\\",
        write: { peak_mb_s: 1761.1, avg_mb_s: 1712.3 },
        read: { peak_mb_s: 1887.8, avg_mb_s: 1839.5, note: "读速受OS文件缓存影响，为上限乐观值" },
        conclusion: { level: "ok", text: "磁盘性能满足日常使用（SSD 级，写 1712 / 读 1839 MB/s）", suggestion: "无需升级" },
        tmp_cleaned: true };
    stages.cpu = { status: "done", workers: 15, duration: 15, worker_errors: [],
        samples: { avg: 95.3, max: 97.5, min: 93.2, unit: "%", n: 15 },
        achieved: true, status_gaps: 0, post_events: [],
        conclusion: { level: "stable", text: "满载 15s（15 线程），平均 95.3%，系统响应正常，未见硬件级错误", suggestion: "CPU 满载下系统运行稳定，满足高负载使用" } };
    stages.mem = { status: "done", target_percent: 90, achieved: true, peak_percent: 91.2,
        before_percent: 70.0, percent_after_release: 70.6, floor_gb: 2.75, hit_floor: false, error: null,
        conclusion: { level: "stable", text: "系统占用推至 91.2% 并维持，释放后回落至 70.6%，全程无异常", suggestion: "内存高占用下系统运行稳定，满足多任务使用" } };
    stages.gpu = { status: "done", cards: [{ name: "Stub GeForce RTX 4090", dedicated: true }],
        metrics_count: 8, metrics_unavailable: false, post_events: [],
        util_max: 99.0, util_avg: 96.2, temp_max: 71.0, src: "nvidia-smi",
        conclusion: { level: "stable", text: "满载 8s，无驱动重置（TDR）事件，峰值占用 99%", suggestion: "GPU 满载下运行稳定" } };
    return { stages: stages,
        overall: { level: "ok", text: "各部件检测通过，硬件满足使用需求",
                   detail: ["磁盘：磁盘性能满足日常使用（SSD 级）", "CPU：满载稳定", "内存：稳定", "GPU：稳定"] } };
};
window.__stubReport = {
    record_id: "stub123", file: "stub\\perf_stub.jsonl",
    interval: 2, elapsed: 6.0, generated_at: "2026-09-05 17:00:00",
    duration_seconds: 6.0, sample_lines: 3, bad_lines: 0,
    stats: {
        cpu_pct: {avg: 42.0, p95: 88.5, max: 91.0, min: 8.0, unit: "%", n: 3},
        mem_used_pct: {avg: 70.0, p95: 92.0, max: 93.0, min: 69.0, unit: "%", n: 3},
        swap_pct: {avg: 4.0, p95: 5.0, max: 5.0, min: 4.0, unit: "%", n: 3},
        mem_available_gb: {avg: 6.0, p95: 1.2, max: 9.0, min: 0.8, unit: "GB", n: 3}
    },
    disk_stats: {
        PhysicalDrive0: {
            busy_pct: {avg: 55.0, p95: 88.0, max: 95.0, min: 5.0, unit: "%", n: 3},
            read_mb_s: {avg: 12.0, p95: 30.0, max: 40.0, min: 1.0, unit: "MB/s", n: 3},
            write_mb_s: {avg: 5.0, p95: 11.0, max: 15.0, min: 0.5, unit: "MB/s", n: 3},
            iops: {avg: 120.0, p95: 300.0, max: 400.0, min: 10.0, unit: "ci/s", n: 3}
        }
    },
    disk_saturation: { PhysicalDrive0: 2.0 },
    series: { t: [0, 2, 4, 6], cpu: [8, 42, 91, 30], mem: [69, 71, 92, 70], disk_max: [5, 60, 95, 12] },
    saturation_thresholds: {cpu_pct: 85, mem_avail_pct: 10, disk_busy_pct: 80},
    components: [
        {key: "cpu", name: "CPU", saturation_seconds: 2.0, saturation_ratio: 0.33, p95: 88.5, unit: "%", threshold_text: "CPU > 85% 视为饱和"},
        {key: "disk", name: "磁盘（PhysicalDrive0）", saturation_seconds: 2.0, saturation_ratio: 0.33, p95: 88.0, unit: "%busy", threshold_text: "磁盘活跃 > 80% 视为饱和"},
        {key: "mem", name: "内存", saturation_seconds: 1.0, saturation_ratio: 0.17, p95: 1.2, unit: "GB(可用)", threshold_text: "可用内存 < 10% 视为饱和"}
    ],
    assessment: {
        need_upgrade: true, level: "upgrade", level_text: "存在瓶颈，建议升级硬件",
        bottleneck: "cpu",
        items: [
            {component: "CPU", severity: "upgrade", reason: "CPU 长期饱和（饱和占比 33%，p95 88.5%）", suggestion: "升级更多核心/更高主频 CPU"},
            {component: "磁盘", severity: "upgrade", reason: "磁盘持续高活跃（饱和占比 33%，p95 busy 88%）", suggestion: "若当前为机械硬盘，强烈建议升级 NVMe SSD"},
            {component: "内存", severity: "watch", reason: "内存压力偏高（p95 可用 1.2 GB）", suggestion: "关注内存占用最高的进程"}
        ]
    }
};
function __stubSnapshot() {
    const st = window.__stubState;
    st.cpuTick += 1;
    return { success: true, ts: Date.now() / 1000,
        cpu: { percent: 23.4 + (st.cpuTick % 7), count: 8, freq_mhz: 2400 },
        memory: { total: 17179869184, used: 12025908428, available: 5153960755, percent: 70.0,
                  total_text: "16.0 GB", available_text: "4.8 GB" },
        swap: { total: 8589934592, used: 343597383, percent: 4.0 },
        disks: [
            { name: "PhysicalDrive0", read_mb_s: 12.5, write_mb_s: 3.2, iops: 120.5, busy_pct: 45.2 },
            { name: "PhysicalDrive1", read_mb_s: 0.4, write_mb_s: 8.8, iops: 60.1, busy_pct: 22.8 }
        ],
        volumes: [
            { mount: "C:\\", total: 343597383680, used: 295279001600, free: 48318382080, percent: 85.9 },
            { mount: "D:\\", total: 1073741824000, used: 214748364800, free: 858993459200, percent: 20.0 }
        ] };
}
window.__stubUplink = { state: "disabled", saved: 0, registered: 0 };
window.__stubAppConfig = { temperature_interval_sec: 300 };
function __stubUplinkStatus() {
    var u = window.__stubUplink;
    return { success: true, uplink: {
        enabled: u.state !== "disabled", running: u.state !== "disabled",
        server_url: u.saved ? "http://stub-etp.local:18090" : "",
        terminal_id: "WIN-STUB-PC", state: u.state, registered: u.registered > 0,
        last_hb_ts: u.registered ? Date.now() / 1000 : 0,
        last_error: u.state === "error" ? "stub_error" : null,
        has_token: u.saved > 0, executed_count: 2, iperf3_available: true,
        heartbeat_interval: 30, client_version: "4.0.0" } };
}
window.pywebview = { api: { call: async function (path) {
    window.__stubCalls.push(path);
    const u = new URL(path, "http://stub.local/");
    const p = u.pathname;
    const st = window.__stubState;
    if (p === "/api/perf/snapshot") return __stubSnapshot();
    if (p === "/api/perf/record-start") {
        st.recording = true; st.samples = 0; st.startedAt = Date.now();
        return { success: true, record_id: "stub123",
                 interval: parseInt(u.searchParams.get("interval") || "2", 10),
                 reused: false, file: "stub.jsonl" };
    }
    if (p === "/api/perf/record-status") {
        if (st.recording) st.samples += 1;
        return { success: true, record: { id: "stub123", status: st.recording ? "running" : "stopped",
            interval: 2, elapsed: st.recording ? (Date.now() - st.startedAt) / 1000 : 0,
            sample_count: st.samples, error_count: 0, error: null,
            file: "stub.jsonl", file_size: 1024, file_size_text: "1.0 KB",
            last_sample: { ts: Date.now() / 1000, cpu_pct: 33.3, mem_pct: 70.0,
                mem_avail: 5153960755, mem_total: 17179869184, swap_pct: 4.0,
                disks: { PhysicalDrive0: {read_mb_s: 1.0, write_mb_s: 2.0, iops: 30, busy_pct: 41.0} } } } };
    }
    if (p === "/api/perf/record-stop") {
        st.recording = false;
        return { success: true,
            record: { id: "stub123", status: "stopped", interval: 2, elapsed: 6.0, sample_count: 3,
                      error_count: 0, error: null, file: "stub.jsonl", file_size: 1024,
                      file_size_text: "1.0 KB", last_sample: null },
            report: window.__stubReport };
    }
    if (p === "/api/perf/record-report") return { success: true, found: true, report: window.__stubReport };
    if (p === "/api/perf/record-export") return { success: true, path: "stub\\perf_report_stub123.html", html_size: 2048 };
    if (p === "/api/perf/stress-start") {
        const m = u.searchParams.get("mode") || "full";
        window.__stubStress = { mode: m, calls: 0 };
        return { success: true, stress_id: "stubS", mode: m,
                 total_seconds: m === "full" ? 58 : ({disk: 25, cpu: 15, mem: 10, gpu: 8}[m] || 60),
                 plan: [] };
    }
    if (p === "/api/perf/stress-status") {
        const s = window.__stubStress;
        if (!s.mode) return { success: false, error: "no stress task" };
        s.calls += 1;
        const running = s.calls < 3;
        const needWebgl = s.mode === "gpu" && running && s.calls >= 2;
        const plan = s.mode === "full"
            ? [{stage: "disk", seconds: 25}, {stage: "cpu", seconds: 15}, {stage: "mem", seconds: 10}, {stage: "gpu", seconds: 8}]
            : [{stage: s.mode, seconds: 25}];
        if (running) {
            return { success: true, task: { id: "stubS", mode: s.mode, status: "running",
                total_seconds: 58, elapsed: s.calls * 0.5, remaining: 57.5, plan: plan,
                current_stage: s.mode === "full" ? "disk" : s.mode,
                stage_detail: { name: s.mode === "full" ? "disk" : s.mode, seconds: 25,
                                elapsed: 1.0, remaining: 24.0, live: { phase: "顺序写", mb_s: 432.1 } },
                need_webgl: needWebgl, result: null, error: null } };
        }
        return { success: true, task: { id: "stubS", mode: s.mode, status: "done",
            total_seconds: 58, elapsed: 58, remaining: 0, plan: plan,
            current_stage: null, stage_detail: null, need_webgl: false, error: null,
            result: window.__stubStressResult(s.mode) } };
    }
    if (p === "/api/perf/stress-cancel") return { success: true, cancelled: true };
    if (p === "/api/perf/stress-export") return { success: true, path: "stub\\perf_stress_stubS.html", html_size: 2048 };
    if (p === "/api/perf/hwinfo") return { success: true, hwinfo: window.__stubHwinfo };
    if (p === "/api/perf/temps") {
        if (!window.__stubTemps.admin) {
            return { success: true, admin: false,
                cpu: { available: false, temp_c: null, reason: "need_admin" },
                gpu: { available: true, temp_c: 31, name: "Stub RTX 4090", src: "nvidia-smi" } };
        }
        return { success: true, admin: true,
            cpu: { available: true, temp_c: 62.5, detail: { package: 62.5, core_max: 64.0 }, reason: null },
            gpu: { available: true, temp_c: 45, name: "Stub RTX 4090", src: "nvidia-smi" } };
    }
    if (p === "/api/perf/restart-admin") {
        window.__stubTemps.admin = true;  // E2E 由重启动作触发 admin 态，时序确定
        return { success: true, restarting: true };
    }
    if (p === "/api/perf/app-config") {
        const v = parseInt(u.searchParams.get("temperature_interval_sec"), 10);
        if (v >= 30 && v <= 3600) { window.__stubAppConfig.temperature_interval_sec = v; }
        return { success: true, config: { temperature_interval_sec: window.__stubAppConfig.temperature_interval_sec } };
    }
    if (p === "/api/perf/uplink/status") return __stubUplinkStatus();
    if (p === "/api/perf/uplink/save") {
        const en = (u.searchParams.get("enabled") || "true") === "true";
        const u2 = window.__stubUplink;
        if (en && !u2.saved && !(u.searchParams.get("token") || "")) {
            return { success: false, error: "missing_server_or_token" };
        }
        u2.saved += 1;
        u2.state = en ? "connected" : "disabled";
        if (en) u2.registered += 1;
        return __stubUplinkStatus();
    }
    if (p === "/api/perf/uplink/register") {
        const u3 = window.__stubUplink;
        if (!u3.saved) return { success: false, error: "missing_server_or_token" };
        u3.registered += 1;
        return __stubUplinkStatus();
    }
    if (p === "/api/disk/open-location") return { success: true, path: "stub\\perf_report_stub123.md" };
    if (p === "/api/analyze") return { success: true, log_type: "System",
        summary: { total: 100, critical: 1, error: 2, warning: 3, info: 94,
                   by_hour_labels: ["00", "01", "02"], by_hour_values: [1, 2, 3],
                   top_events: [{ name: "StubSource", count: 10 }] },
        critical_count: 0, critical_events: [], faults: [] };
    if (p === "/api/events") return { success: true, total: 0, page: 1, per_page: 50, total_pages: 0, events: [] };
    if (p === "/api/knowledge") return { success: true, knowledge: [
        { name: "桩数据知识条目", severity: "error", event_ids: [41], description: "stub", suggestions: ["无需处理"] } ] };
    if (p === "/api/disk/overview") return { success: true, drive: "C:\\", total: 100,
        total_text: "100 GB", used: 50, used_text: "50 GB", free: 50, free_text: "50 GB", used_percent: 50 };
    if (p === "/api/disk/drives") return { success: true,
        drives: [{ drive: "C:\\", used_percent: 50, free_text: "50 GB" }] };
    if (p === "/api/disk/scan") return { success: true, scan_id: "stub_scan", reused: false };
    if (p === "/api/disk/scan-status") return { success: true, task: { id: "stub_scan", kind: "tree",
        status: "running", progress: { scanned_files: 100, dirs: 10, elapsed: 1, current_dir: "C:\\stub" },
        result: null, error: null, elapsed: 1.0 } };
    return { success: false, error: "stub: 未模拟的路由 " + p };
}}};
"""


ERRORS = []  # 模块级失败收集


def check(cond, msg):
    tag = "PASS" if cond else "FAIL"
    print("  [%s] %s" % (tag, msg))
    if not cond:
        ERRORS.append(msg)


UI_CONFIRM_LOG = []   # uiConfirm 交互记录（废除原生弹窗门禁计数）

UI_CONFIRM_JS = """() => {
    const c = document.querySelector('.ui-confirm-card');
    if (!c) return null;
    const r = c.getBoundingClientRect();
    return { cx: r.left + r.width / 2, cy: r.top + r.height / 2,
             w: window.innerWidth, h: window.innerHeight, txt: c.textContent,
             okCls: (c.querySelector('.ui-confirm-foot button:last-child') || {}).className || "" };
}"""


def ui_confirm_interact(page, expect_danger=False, ok=True, tag=""):
    """uiConfirm 交互（2026-09-14 废除原生 confirm/alert 专项门禁）：
    等待出现 → 断言居中于窗体中心 + 标题/内容零来源端口信息 + danger 键 → 点击。"""
    page.wait_for_selector(".ui-confirm-card", timeout=5000)
    info = page.evaluate(UI_CONFIRM_JS)
    UI_CONFIRM_LOG.append(tag or "unnamed")
    label = "uiConfirm[%s]" % (tag or "unnamed")
    check(info is not None, label + " 弹窗出现")
    if not info:
        return
    check(abs(info["cx"] - info["w"] / 2) <= 2 and abs(info["cy"] - info["h"] / 2) <= 2,
          label + " 居中于窗体中心（卡中心 %d,%d vs 窗体 %d,%d）"
          % (round(info["cx"]), round(info["cy"]), info["w"] // 2, info["h"] // 2))
    txt = info["txt"] or ""
    bad = [w for w in ("127.0.0.1", "localhost") if w in txt]
    bad += [":" + m for m in re.findall(r":\d{2,5}", txt)]   # host:port 样式来源信息
    check(not bad, label + " 标题/内容零来源与端口信息（残留 %s）" % (bad or "无"))
    if expect_danger:
        check("ui-confirm-ok-danger" in (info["okCls"] or ""),
              label + " danger 确认键红色样式")
    else:
        check("ui-confirm-ok-danger" not in (info["okCls"] or ""),
              label + " 非 danger 确认键常规样式")
    page.click(".ui-confirm-card .ui-confirm-foot button:last-child" if ok
               else ".ui-confirm-card .ui-confirm-foot button:first-child")


def check_no_native_dialogs():
    """门禁④：web/ 源码原生 confirm(/alert( 调用零残留。
    允许：组件定义文件（app.js / standalone 内联副本）注释行。
    2026-09-14 第二批收口：disk.js/appdata.js 白名单已移除——web/ 全域零残留。"""
    pat = re.compile(r"(?<![.\w])(confirm|alert)\s*\(")
    targets = sorted((WORKSPACE / "web").glob("*.js"))
    targets.append(WORKSPACE / "web" / "index.html")
    targets.append(ROOT / "web" / "perf-standalone.html")
    hits = []
    for f in targets:
        if not f.exists():
            continue
        src = f.read_text(encoding="utf-8", errors="replace")
        for i, ln in enumerate(src.splitlines(), 1):
            if not pat.search(ln):
                continue
            stripped = ln.strip()
            if stripped.startswith(("//", "*", "/*", "<!--")):
                continue   # 组件定义文件内的约定注释
            hits.append("%s:%d %s" % (f.name, i, stripped[:70]))
    check(not hits, "web/ 源码原生 confirm/alert 调用全域零残留（白名单仅组件注释行）"
          if not hits else "web/ 原生对话框残留 %d 处" % len(hits))
    if hits:
        for h in hits:
            print("    -", h)


def new_page(browser, url, accept_confirm=False):
    ctx = browser.new_context()
    page = ctx.new_page()
    page_errors = []
    dialogs = []

    def _on_err(e):
        stack = getattr(e, "stack", "") or ""
        page_errors.append(str(e) + ("\n---stack---\n" + stack if stack else ""))

    def _on_dialog(d):
        dialogs.append(d.message)
        if accept_confirm:
            d.accept()
        else:
            d.dismiss()

    page.on("pageerror", _on_err)
    page.on("dialog", _on_dialog)
    page.add_init_script(STUB_JS)
    page.goto(url)
    return ctx, page, page_errors, dialogs


# ---------------------------------------------------------------------------
# 场景 1：standalone 全流程
# ---------------------------------------------------------------------------

def e2e_standalone(browser):
    print("== 场景 1：perf-standalone 全流程 ==")
    ctx, page, page_errors, dialogs = new_page(browser, STANDALONE.as_uri(), accept_confirm=True)
    try:
        page.wait_for_timeout(800)

        fns = page.evaluate(
            "() => ['initPerfTab','perfTick','perfStartRecord','perfStopRecord',"
            "'renderPerfReport','perfExportReport','perfOpenReportLocation',"
            "'startPerfStress','cancelPerfStress','perfStressExport','renderStressResult',"
            "'perfStartWebGL','perfStopWebGL','perfLoadHwInfo','renderHwInfo',"
            "'perfStartTempsPolling','perfTempsTick','renderPerfTemps','restartPerfAdmin',"
            "'perfLoadUplink','renderPerfUplink','savePerfUplink','registerPerfUplink','uiConfirm','perfNotify']"
            ".map(f => [f, typeof window[f]])")
        for name, t in fns:
            check(t == "function", "关键函数 %s typeof==function（实际 %s）" % (name, t))

        page.wait_for_function(
            "() => document.getElementById('perfCpuVal') && document.getElementById('perfCpuVal').textContent !== '--'",
            timeout=10000)
        check(True, "实时 CPU 数据填充（%s）" % page.text_content("#perfCpuVal"))

        page.wait_for_function(
            "() => document.getElementById('perfMemVal') && document.getElementById('perfMemVal').textContent !== '--'",
            timeout=5000)
        check(True, "实时内存数据填充（%s）" % page.text_content("#perfMemVal"))

        page.wait_for_function(
            "() => window.perfState && perfState.chartCpuMem && perfState.chartDisk", timeout=5000)
        check(True, "Chart.js 图表实例创建（CPU/内存图 + 磁盘图）")

        page.wait_for_function(
            "() => document.querySelectorAll('#perfDisksBody tr td:first-child').length >= 2", timeout=5000)
        check(True, "物理磁盘明细表填充（2 块物理盘）")

        page.wait_for_function(
            "() => document.querySelectorAll('#perfVolsBody tr td:first-child').length >= 2", timeout=5000)
        check(True, "卷容量表填充（2 个卷）")

        # 记录开始 → 状态轮询
        page.click("#perfRecStartBtn")
        page.wait_for_function(
            "() => document.getElementById('perfRecBadge').textContent === '记录中'", timeout=5000)
        check(True, "开始记录：状态徽章变为「记录中」")
        page.wait_for_function(
            "() => parseInt(document.getElementById('perfRecSamples').textContent, 10) > 0", timeout=5000)
        check(True, "记录状态轮询：样本数递增（%s）" % page.text_content("#perfRecSamples"))
        check(page.text_content("#perfRecCpu") != "--", "记录瞬时值显示 CPU %s" % page.text_content("#perfRecCpu"))

        # 停止 → 报告
        page.click("#perfRecStopBtn")
        page.wait_for_selector(".perf-report", timeout=8000)
        check(True, "停止记录：报告卡片渲染")
        page.wait_for_function(
            "() => document.querySelector('.perf-report-level') && document.querySelector('.perf-report-level').textContent.indexOf('建议升级') >= 0",
            timeout=3000)
        check(True, "报告结论渲染（upgrade 级别文本）")
        page.wait_for_function(
            "() => document.querySelectorAll('.perf-report-item').length >= 3", timeout=3000)
        check(True, "报告结论条目渲染（CPU/磁盘/内存 3 条）")
        page.wait_for_function(
            "() => document.querySelectorAll('.perf-sat-item').length >= 3", timeout=3000)
        check(True, "瓶颈排序卡片渲染（3 组件）")

        # 导出按钮存在
        check(page.query_selector("text=导出 HTML 报告") is not None, "导出 HTML 按钮存在")

        # ===== uiConfirm 组件行为补充（第二批收口：ESC 取消 / 单按钮模式）=====
        esc_r = page.evaluate("""() => new Promise(res => {
            const p = uiConfirm({ title: 'ESC 取消测试', message: '按 ESC 关闭' });
            setTimeout(() => {
                document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
                p.then(v => res({ resolved: v, maskGone: !document.querySelector('.ui-confirm-mask') }));
            }, 50);
        })""")
        check(esc_r is not None and esc_r["resolved"] is False and esc_r["maskGone"] is True,
              "uiConfirm ESC 取消：Promise resolve false + 遮罩移除（实际 %s）" % (esc_r,))
        one_r = page.evaluate("""() => new Promise(res => {
            const p = uiConfirm({ title: '单按钮提示', message: '仅确定', cancelText: '' });
            setTimeout(() => {
                const foot = document.querySelector('.ui-confirm-foot');
                const n = foot ? foot.querySelectorAll('button').length : 0;
                const ok = document.querySelector('.ui-confirm-foot button:last-child');
                if (ok) ok.click();
                p.then(v => res({ buttons: n, resolved: v }));
            }, 50);
        })""")
        check(one_r is not None and one_r["buttons"] == 1 and one_r["resolved"] is True,
              "uiConfirm 单按钮提示模式（cancelText 空 → 仅确定键，resolve true，实际 %s）" % (one_r,))

        # ===== 压测：全面检测（uiConfirm 确认 → 进度 → 结果卡片）=====
        page.click('button[data-stress-mode="full"]')
        ui_confirm_interact(page, expect_danger=True, tag="压测-全面检测")
        page.wait_for_function(
            "() => document.getElementById('perfStressProgress') && "
            "document.getElementById('perfStressProgress').style.display !== 'none'", timeout=6000)
        check(True, "压测启动：confirm 通过后进度区出现")
        page.wait_for_function(
            "() => document.getElementById('perfStressStageText').textContent.indexOf('磁盘') >= 0", timeout=5000)
        check(True, "压测进度：当前阶段显示（%s）" % page.text_content("#perfStressStageText"))
        page.wait_for_timeout(3000)
        page.wait_for_selector("#perfStressResultBox .perf-report", timeout=8000)
        check(True, "压测完成：结果卡片渲染")
        page.wait_for_function(
            "() => document.querySelectorAll('#perfStressResultBox .hw-group').length >= 4", timeout=3000)
        check(True, "压测结果：磁盘/CPU/内存/GPU 四个阶段卡片渲染")
        page.wait_for_function(
            "() => document.querySelector('#perfStressResultBox .perf-report-level').textContent.indexOf('满足使用需求') >= 0",
            timeout=3000)
        check(True, "压测综合结论渲染（%s）" % page.text_content("#perfStressResultBox .perf-report-level"))
        check(page.query_selector("text=导出 HTML 报告") is not None, "压测导出 HTML 按钮存在")

        # ===== 硬件规格 =====
        page.wait_for_function(
            "() => document.getElementById('perfHwInfoBox') && "
            "document.getElementById('perfHwInfoBox').style.display !== 'none'", timeout=6000)
        check(True, "硬件规格卡片填充")
        page.wait_for_function(
            "() => document.querySelectorAll('#perfHwInfoBox .hw-group').length >= 4", timeout=3000)
        check(True, "硬件规格四组展示（CPU/内存/磁盘/GPU）")
        check(page.query_selector("#perfHwInfoBox .hw-badge-sys") is not None, "系统盘徽章渲染")
        check(page.text_content("#perfHwInfoBox").find("DDR5") >= 0, "内存条规格（DDR5）展示")
        check(page.text_content("#perfHwInfoBox").find("显存 24 GB") >= 0, "显卡显存规格展示")
        check(page.evaluate("!!(window.Chart && Chart.getChart && Chart.getChart(document.getElementById('perfVolsCanvas')))"), "卷容量可视化图表渲染")
        check(page.evaluate("!!(window.Chart && Chart.getChart && Chart.getChart(document.getElementById('perfReportChart')))"), "记录综合运行状态曲线渲染")

        # ===== 平台接入（uplink，v4）：卡片渲染 + 保存启用流程 =====
        page.wait_for_function(
            "() => document.getElementById('perfUplinkStatus') && "
            "document.getElementById('perfUplinkStatus').textContent.indexOf('读取中') < 0", timeout=6000)
        check(True, "平台接入卡片状态渲染（初始 disabled）")
        check(page.text_content("#perfUplinkBadge").find("未启用") >= 0, "平台接入徽章初始「未启用」")
        check(page.text_content("#perfUplinkStatus").find("WIN-STUB-PC") >= 0, "平台接入终端ID显示")
        page.fill("#perfUplinkServer", "http://stub-etp.local:18090")
        page.fill("#perfUplinkToken", "stub-token-abc")
        page.click("#perfUplinkSaveBtn")
        page.wait_for_function(
            "() => document.getElementById('perfUplinkBadge').textContent.indexOf('已连接') >= 0", timeout=6000)
        check(True, "保存并启用后徽章变「已连接」")
        check(page.text_content("#perfUplinkStatus").find("已注册") >= 0, "启用后注册态显示")
        token_val = page.evaluate("() => document.getElementById('perfUplinkToken').value")
        check("保存后 Token 输入框清空（不回显）", token_val == "")
        page.click("#perfUplinkDisableBtn")
        page.wait_for_function(
            "() => document.getElementById('perfUplinkBadge').textContent.indexOf('未启用') >= 0", timeout=6000)
        check(True, "停用接入后徽章回「未启用」")
        check(page.evaluate("() => window.__stubUplink.saved >= 2"), "桩收到 2 次 save（启用+停用）")
        check(True, "关闭设置弹窗（不遮挡后续菜单交互）")

        # ===== 压测 GPU 单项：need_webgl 驱动 WebGL 启停 =====
        page.click('button[data-stress-mode="gpu"]')
        ui_confirm_interact(page, expect_danger=True, tag="压测-GPU")
        page.wait_for_function("() => perfState.webglActive === true", timeout=6000)
        check(True, "GPU 压测：need_webgl 驱动 WebGL 负载启动")
        page.wait_for_function("() => perfState.webglActive === false", timeout=10000)
        check(True, "压测完成：WebGL 负载自动停止")
        page.wait_for_function(
            "() => document.querySelectorAll('#perfStressResultBox .hw-group').length >= 1 && "
            "document.getElementById('perfStressResultBox').textContent.indexOf('GPU') >= 0", timeout=5000)
        check(True, "GPU 单项结果卡片渲染")

        # ===== 温度能力分级（两态：need_admin 占位 → admin 显示值+徽章）=====
        page.wait_for_function(
            "() => document.getElementById('perfCpuTempVal') && "
            "document.getElementById('perfCpuTempVal').textContent.indexOf('需管理员模式') >= 0", timeout=6000)
        check(True, "温度降级态：CPU 温度占位「需管理员模式」")
        check(page.query_selector("#perfCpuTempExtra button") is not None, "「以管理员重启」按钮出现")
        check(page.text_content("#perfGpuTempVal").find("31") >= 0 or
              page.text_content("#perfGpuTempVal").find("45") >= 0, "GPU 温度可读（%s）" % page.text_content("#perfGpuTempVal"))
        # 重启按钮 uiConfirm 流程（桩返回 restarting，绝不真提权）
        page.click("#perfCpuTempExtra button")
        ui_confirm_interact(page, expect_danger=False, tag="管理员重启")
        page.wait_for_function(
            "() => document.getElementById('perfCpuTempVal').textContent.indexOf('重启中') >= 0", timeout=5000)
        check(True, "管理员重启：uiConfirm 通过后显示重启中")
        # 温度轮询默认间隔可配置（300s），桩场景临时调快以便观察 admin 态翻转
        page.evaluate(
            "() => { window.perfState.tempsIntervalMs = 500;"
            " window.perfStopTempsPolling(); window.perfStartTempsPolling(); }")
        # admin 态（桩第 2+ 次轮询翻转）
        page.wait_for_function(
            "() => document.getElementById('perfCpuTempVal').textContent.indexOf('°C') >= 0", timeout=8000)
        check(True, "温度 admin 态：CPU 温度实时显示（%s）" % page.text_content("#perfCpuTempVal"))
        page.wait_for_function(
            "() => document.getElementById('perfCpuTempExtra').textContent.indexOf('管理员模式') >= 0", timeout=3000)
        check(True, "「管理员模式」徽章显示")

        check(len(UI_CONFIRM_LOG) == 3, "uiConfirm 交互 3 次（两次压测+一次重启，实际 %d：%s）"
              % (len(UI_CONFIRM_LOG), UI_CONFIRM_LOG))
        check(len(dialogs) == 0, "无原生 dialog（confirm/alert 已全替换，实际 %s）"
              % (dialogs or "无"))
        check(len(page_errors) == 0, "pageerror == none（%s）" % (page_errors or "无"))
    finally:
        ctx.close()


# ---------------------------------------------------------------------------
# 场景 2：主应用遍历 6 菜单
# ---------------------------------------------------------------------------

MAIN_TABS = ["loginspector", "disk", "perf"]  # 2026-09-06 菜单合并后：日志诊断/磁盘清理/性能分析


def e2e_main(browser):
    print("== 场景 2：主应用 3 菜单遍历 ==")
    ctx, page, page_errors, dialogs = new_page(browser, MAIN_INDEX.as_uri())
    try:
        page.wait_for_timeout(1500)

        fns = page.evaluate(
            "() => ['switchTab',"  # app.js
            "'initDiskTab','startJunkScan','startLargeScan',"                # disk.js
            "'startAppdataScan','adMigrateSelected',"                        # appdata.js
            "'initPerfTab','perfStartRecord','perfStopRecord','renderPerfReport','perfExportReport',"  # perf.js v1
            "'startPerfStress','cancelPerfStress','renderStressResult','perfStressExport','renderHwInfo',"  # perf.js v2
            "'perfLoadUplink','renderPerfUplink','savePerfUplink','registerPerfUplink',"  # perf.js v4 uplink
            "'uiConfirm']"  # 2026-09-14 全局对话框组件（app.js）
            ".map(f => [f, typeof window[f]])")
        for name, t in fns:
            check(t == "function", "关键函数 %s typeof==function（实际 %s）" % (name, t))

        # 依次点击 3 个菜单
        for tab in MAIN_TABS:
            page.click('button[data-tab="%s"]' % tab)
            page.wait_for_timeout(500)
            check(page.evaluate(
                "() => document.getElementById('tab-%s').classList.contains('active')" % tab),
                "切换到菜单 %s 并激活" % tab)

        # perf 数据填充（perf 轮询在 perf tab active 后进行）
        page.wait_for_function(
            "() => document.getElementById('perfCpuVal') && document.getElementById('perfCpuVal').textContent !== '--'",
            timeout=10000)
        check(True, "性能分析菜单实时数据填充")

        # 硬件规格（进入 perf 菜单一次性加载）
        page.wait_for_function(
            "() => document.getElementById('perfHwInfoBox') && "
            "document.querySelectorAll('#perfHwInfoBox .hw-group').length >= 4", timeout=8000)
        check(True, "硬件规格卡片填充（4 组）")

        # disk 数据填充（initDiskTab 已在切到 disk 时触发）
        page.wait_for_function(
            "() => document.getElementById('diskTotal') && document.getElementById('diskTotal').textContent !== '--'",
            timeout=5000)
        check(True, "磁盘清理菜单概览数据填充")

        # 菜单按钮数量（2026-09-10 net-doctor 集成：主页/日志诊断/磁盘清理/性能分析/网络排障）
        check(page.evaluate("() => document.querySelectorAll('.nav-tab').length") == 5,
              "导航共 5 个菜单（主页/日志诊断/磁盘清理/性能分析/网络排障）")

        # ===== 设置弹窗卡片化（外壳重构：settings-card 布局 + 文案清理 + 独立保存按钮）=====
        page.click("#appSettingsBtn")
        page.wait_for_function(
            "() => document.getElementById('appSettingsOverlay').style.display === 'flex'", timeout=5000)
        check(True, "设置弹窗打开（齿轮按钮 → overlay 可见）")
        cards = page.evaluate("() => document.querySelectorAll('.app-settings-body .settings-card').length")
        check(cards >= 3, "设置卡片 ≥3 张（中心平台接入/性能分析/ndSettingsHost 容器，实际 %d）" % cards)
        pad = page.evaluate(
            "() => { const s = getComputedStyle(document.querySelector('.app-settings-body'));"
            " return ['Top','Right','Bottom','Left'].map(k => parseFloat(s['padding' + k])); }")
        check(min(pad) >= 16, "app-settings-body 内边距 ≥16px（实际 %s）" % pad)
        card_style = page.evaluate(
            "() => { const s = getComputedStyle(document.querySelector('.settings-card'));"
            " return { br: s.borderTopLeftRadius, bw: parseFloat(s.borderTopWidth),"
            " pt: parseFloat(s.paddingTop), mb: parseFloat(s.marginBottom) }; }")
        check(card_style["br"] == "10px" and card_style["bw"] == 1 and 14 <= card_style["pt"] <= 16,
              "settings-card 样式规格（圆角 10px/边框 1px/内边距 14~16px/间距 12px，实际 %s）" % card_style)
        for btn in ["perfUplinkSaveBtn", "perfSaveAppConfigBtn", "ndSettingsSaveBtn"]:
            page.wait_for_function(
                "() => { const b = document.getElementById('%s');"
                " return b && b.getBoundingClientRect().width > 0; }" % btn, timeout=4000)
        check(True, "三张卡保存按钮独立存在（保存并启用/性能保存/网络监测保存）")
        tv = page.evaluate("() => document.getElementById('tempsIntervalInput').value")
        check(tv == "300", "温度采样间隔回显配置值（桩 temperature_interval_sec=300，实际 %s）" % tv)
        dirty = page.evaluate(
            "() => { const body = document.querySelector('.app-settings-body').cloneNode(true);"
            " const nd = body.querySelector('#ndSettingsHost'); if (nd) nd.remove();"
            " return body.textContent; }")
        bad = [w for w in ["app_config.json", "uplink_config.json", "netdoctor.", "LOCALAPPDATA"]
               if w.lower() in dirty.lower()]
        check(not bad, "设置弹窗静态文案零实现细节（残留 %s）" % (bad or "无"))
        page.click(".app-settings-close")
        page.wait_for_function(
            "() => document.getElementById('appSettingsOverlay').style.display === 'none'", timeout=3000)
        check(True, "关闭设置弹窗")

        # 离开 perf 菜单后轮询自动停止（ADR-007）
        page.click('button[data-tab="loginspector"]')  # dashboard 已并入日志诊断
        page.wait_for_timeout(1500)
        check(page.evaluate("() => window.perfState && perfState.pollTimer === null"),
              "离开性能分析菜单后轮询自动暂停")

        check(len(dialogs) == 0, "无 alert 弹窗（%s）" % (dialogs or "无"))
        check(len(page_errors) == 0, "pageerror == none（%s）" % (page_errors or "无"))
    finally:
        ctx.close()


def main():
    if not STANDALONE.exists():
        print("缺少 standalone 页面:", STANDALONE)
        return 2
    if not MAIN_INDEX.exists():
        print("缺少主应用页面:", MAIN_INDEX)
        return 2
    # 门禁第四道（ADR-013）：GUI 无控制台程序子进程必须 CREATE_NO_WINDOW，
    # smoke（控制台）与 E2E（桩）都看不到弹窗，必须静态兜底
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from check_subprocess_window import check_paths
    probs = check_paths([str(ROOT / "perf_service.py"), str(ROOT / "uplink.py")])
    if probs:
        for pr in probs:
            print("[SUBPROC-FAIL] %s" % pr)
        print("E2E 中止：先修复子进程窗口问题（详见 ADR-013）")
        return 3
    print("[SUBPROC-PASS] 子进程窗口检查通过")
    # 门禁④（2026-09-14 废除原生弹窗专项）：web/ 源码 confirm(/alert( 零残留
    check_no_native_dialogs()
    if ERRORS:
        print("E2E 中止：源码存在原生对话框残留（详见上方 FAIL）")
        return 3
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            e2e_standalone(browser)
            e2e_main(browser)
        finally:
            browser.close()
    print("=" * 50)
    if ERRORS:
        print("E2E 失败：%d 项" % len(ERRORS))
        for e in ERRORS:
            print("  -", e)
        return 1
    print("E2E 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
