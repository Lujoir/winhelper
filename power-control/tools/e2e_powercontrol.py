# -*- coding: utf-8 -*-
"""
自动开关机 E2E 门禁（Playwright chromium）
=========================================
场景 1（独立测试页）：power-control/web/powercontrol-standalone.html + pywebview 桩
  → 断言 pc* 关键函数、企业线/消费线双分支渲染、上报链路、pageerror=none
  → 宽度三档 1920/1440/1080 无横向滚动（STYLE.md §9）
场景 2（主应用集成）：file:// web/index.html + 全模块兜底桩
  → 遍历全部 8 菜单断言激活与关键函数保持（防破坏其他模块）
  → 自动开关机菜单数据填充 + 菜单位置（锁屏及壁纸管理之后）+ pageerror=none
  → powercontrol tab 宽度三档断言

依赖：pip install playwright && playwright install chromium
运行：python tools/e2e_powercontrol.py
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PC_ROOT = Path(__file__).resolve().parent.parent          # power-control/
WORKSPACE = PC_ROOT.parent                                # 工作区根
STANDALONE = PC_ROOT / "web" / "powercontrol-standalone.html"
MAIN_INDEX = WORKSPACE / "web" / "index.html"

_results = []


def check(name, ok, detail=""):
    _results.append(bool(ok))
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name,
                         ("  <- " + str(detail)[:200]) if detail and not ok
                         else ""))


def check_no_real_ip_assets(page, tag):
    # 业务 IP 段口径与 net-doctor 门禁一致（192.168.1.x 通用示例不算业务 IP）
    bad = page.evaluate("""(function () {
        var re = /172\\.17\\.|192\\.168\\.254|10\\.8\\.0\\.90/;
        var bad = [];
        var els = document.querySelectorAll('input[placeholder], [title]');
        for (var i = 0; i < els.length; i++) {
            var ph = els[i].getAttribute('placeholder') || '';
            var ti = els[i].getAttribute('title') || '';
            if (re.test(ph)) { bad.push('placeholder:' + ph); }
            if (re.test(ti)) { bad.push('title:' + ti); }
        }
        return bad;
    })()""")
    check("[%s] 脱敏·placeholder/title 无真实业务 IP" % tag, not bad, bad)


def check_no_real_ip_sources():
    import re as _re
    pat = _re.compile(r"172\.17\.|192\.168\.254|10\.8\.0\.90")
    for f in (PC_ROOT / "web" / "powercontrol.js", STANDALONE):
        hits = [ln for ln in f.read_text(encoding="utf-8").splitlines()
                if pat.search(ln)]
        check("脱敏·源文件无真实 IP（%s）" % f.name, not hits, hits[:3])


def check_no_local_power_entry(page, tag):
    """4.1.5 管控语义门禁：本地 UI 无任何立即关机/重启入口（仅中心可发，
    power_action UPL 命令不对 UI 暴露）。"""
    src_js = (PC_ROOT / "web" / "powercontrol.js").read_text(encoding="utf-8")
    src_html = STANDALONE.read_text(encoding="utf-8")
    dom = page.evaluate(
        "document.body ? document.body.innerHTML : ''")
    for label in ("立即关机", "立即重启"):
        check("管控·%s 无「%s」入口（源码）" % (tag, label),
              label not in src_js and label not in src_html)
    check("管控·%s 无 power_action UI 调用（源码）" % tag,
          "power_action" not in src_js)
    check("管控·%s 无 wol_relay UI 调用（源码）" % tag,
          "wol_relay" not in src_js and "wol_relay" not in src_html)
    check("管控·%s DOM 无关机入口（渲染态）" % tag,
          "立即关机" not in dom and "立即重启" not in dom)


def pc_confirm_modal(page):
    """点击 pcConfirm/uiConfirm 自绘弹窗的确认键（2026-09-17 起无原生 confirm）"""
    page.wait_for_selector(".ui-confirm-card", timeout=4000)
    page.click(".ui-confirm-card .ui-confirm-btn.btn-primary")


# ----------------------------------------------------------------------
# 桩：快照夹具（结构对齐真实采集返回，值取 M720t 实测/本机实测）
# ----------------------------------------------------------------------
PC_STUB_JS = r"""
window.__stubCalls = [];
window.__stubPcMode = "enterprise";
window.__stubTaskPolls = {};   /* task_id -> 轮询次数（首两次 running，此后 done） */
window.__stubPcTasks = {};     /* task_id -> {ok, summary} */
window.__stubNextTaskId = 0;
/* 4.1.8 追加：中心任务卡（门控/列表/个性化创建/删除）
   数据形状按契约定稿（server-platform 批 A，commit 99f4079）：
   中心按 next_trigger 升序返回，携带 next_ts(epoch 秒)/calendar_fallback */
window.__stubUplinkState = "disconnected";
window.__stubCenterFail = false;
window.__stubCenterLimit = false;
window.__stubCenterCreatePayload = null;
window.__stubCenterDeleteIds = [];
window.__stubTs = function (hh, mm) {
    var d = new Date();
    d.setDate(d.getDate() + 1);
    d.setHours(hh, mm, 0, 0);
    return Math.floor(d.getTime() / 1000);
};
window.__stubDstr = function () {
    var d = new Date();
    d.setDate(d.getDate() + 1);
    var p2 = function (x) { return ("0" + x).slice(-2); };
    return (d.getMonth() + 1) + "-" + p2(d.getDate());
};
window.__stubCenterTasks = [
    { task_id: 2, name: "个性化早班开机", kind: "boot",
      source: "client_personal", origin: "client_personal",
      repeat: "daily", time_hhmm: "07:30",
      next_trigger: window.__stubDstr() + " 07:30",
      next_ts: window.__stubTs(7, 30), enabled: 1 },
    { task_id: 1, name: "全局晚班开机", kind: "boot", source: "platform",
      origin: "platform", repeat: "daily", time_hhmm: "08:30",
      next_trigger: window.__stubDstr() + " 08:30",
      next_ts: window.__stubTs(8, 30), enabled: 1 },
    { task_id: 3, name: "节假日值班开机", kind: "boot", source: "platform",
      origin: "platform", repeat: "holiday", time_hhmm: "09:00",
      calendar_fallback: true,
      next_trigger: window.__stubDstr() + " 09:00",
      next_ts: window.__stubTs(9, 0), enabled: 1 } ];
window.__stubPcEnt = { success: true, snapshot: {
    schema: 1, collected_at: "2026-09-16 18:30:00", collected_ts: 1789554600,
    machine: { hostname: "STUB-PC", manufacturer: "LENOVO", model: "10SWA03ECD",
        system_family: "ThinkCentre M720t-D234",
        vendor_line: "lenovo_enterprise", vendor_line_text: "联想（企业线接口可用）",
        capability: "enterprise_configurable", capability_text: "企业线可配置" },
    bios: { remote_configurable: true, wmi_class_found: true,
        reason: "检测到企业线 BIOS 配置接口，可读取自动开机项", items: [],
        rtc: { alarm: "Daily Event", alarm_on: true, time: "08:00:00",
            user_time: "00:00:00", date: "01/01/2017", day: "Sunday",
            weekdays: { Sunday: "Disabled", Monday: "Disabled", Tuesday: "Disabled",
                Wednesday: "Disabled", Thursday: "Disabled", Friday: "Disabled",
                Saturday: "Disabled" },
            after_power_loss: "Last State", wake_on_lan: "Automatic",
            cycle_text: "每天", summary: "每天 08:00:00" } },
    wake_timers: { ok: true, need_admin: false, count: 2, items: [
        { type: "SERVICE",
          owner: "svchost.exe (SystemEventsBroker)",
          wake_time: "2026/9/16 18:48:18",
          reason: "Windows 将执行系统维护计划任务，该任务请求唤醒计算机。",
          author: "", description: "" },
        { type: "PROCESS", owner: "upd.exe", wake_time: "2026/9/17 20:00:00",
          reason: "", author: "", description: "更新检查" } ] },
    shutdown_tasks: { ok: true, count: 1, items: [
        { name: "NightlyStub", next_run: "2026/9/17 22:00:00", status: "就绪",
          action: "stub_shutdown_sim.exe (样例占位，非真实命令)",
          schedule_type: "每天" } ] },
    fast_startup: { registry_present: true, hiberboot_enabled: 1,
        available: true, enabled: true, note: "系统支持快速启动" },
    errors: [] } };
window.__stubPcEnt.report_state = { last_ok: true,
    last_at: "2026-09-16 20:00:00", last_error: null };
window.__stubPcCon = { success: true, snapshot: {
    schema: 1, collected_at: "2026-09-16 18:31:00", collected_ts: 1789554660,
    machine: { hostname: "STUB-PC", manufacturer: "LENOVO", model: "91AY000LCP",
        system_family: "IdeaCentre GeekPro-17IAX",
        vendor_line: "lenovo_consumer", vendor_line_text: "联想（未提供企业线接口）",
        capability: "not_supported", capability_text: "不支持远程配置" },
    bios: { remote_configurable: false, wmi_class_found: false,
        reason: "本机 BIOS 未提供企业线远程配置接口，如需定时开机请在开机时进入 BIOS 菜单人工设置",
        items: [], rtc: {} },
    wake_timers: { ok: true, need_admin: true, count: 0, items: [],
        error: "需管理员权限查看唤醒定时器（本机只读，未提权）" },
    shutdown_tasks: { ok: true, count: 0, items: [] },
    fast_startup: { registry_present: true, hiberboot_enabled: 1,
        available: true, enabled: true, note: "系统支持快速启动" },
    errors: [] } };
window.pywebview = { api: {
    call: function (path, body) {
        window.__stubCalls.push(path);
        return new Promise(function (resolve) {
            var p = path.split("?")[0];
            /* 三大改造②③：客户端控制路由 */
            if (p.indexOf("/api/app/autostart") === 0
                    && p.indexOf("autostart-set") < 0) {
                return resolve({ success: true,
                    enabled: !!window.__stubAutostart });
            }
            if (p.indexOf("/api/app/autostart-set") === 0) {
                try { var ab = body ? JSON.parse(body) : {}; }
                catch (e) { var ab = {}; }
                window.__stubAutostart = !!ab.enabled;
                return resolve({ success: true, enabled: !!ab.enabled });
            }
            if (p.indexOf("/api/app/update-status") === 0) {
                return resolve({ success: true, update: {
                    status: window.__stubUpdReady ? "ready" : "idle",
                    version: window.__stubUpdReady ? "4.1.0" : null,
                    error: null } });
            }
            if (p.indexOf("/api/app/update-apply") === 0) {
                window.__stubUpdApplyCalls =
                    (window.__stubUpdApplyCalls || 0) + 1;
                return resolve({ success: true, exiting: true });
            }
            /* 4.1.8 追加：平台接入状态（中心任务卡连接门控） */
            if (p.indexOf("/api/perf/uplink/status") === 0) {
                return resolve({ success: true, uplink: {
                    state: window.__stubUplinkState,
                    registered: window.__stubUplinkState === "connected",
                    terminal_id: "WIN-STUB" } });
            }
            /* 4.1.8 追加：中心开机任务（列表/个性化创建） */
            if (p.indexOf("/api/powercontrol/center-tasks") === 0) {
                if (window.__stubUplinkState !== "connected") {
                    return resolve({ success: false, error: "not_connected" });
                }
                if (window.__stubCenterFail) {
                    return resolve({ success: false,
                                     error: "stub center failure" });
                }
                return resolve({ success: true,
                    tasks: JSON.parse(JSON.stringify(
                        window.__stubCenterTasks)),
                    quota: { used: 1, limit: 5 } });
            }
            if (p.indexOf("/api/powercontrol/center-task-create") === 0) {
                try {
                    window.__stubCenterCreatePayload =
                        body ? JSON.parse(body) : null;
                } catch (e) { window.__stubCenterCreatePayload = null; }
                if (window.__stubCenterLimit) {
                    return resolve({ success: false,
                        error: "已达个性化任务数量上限（5）" });
                }
                var cb = window.__stubCenterCreatePayload || {};
                var nt = {
                    task_id: 100 + window.__stubCenterTasks.length,
                    name: cb.name || "个性化开机", kind: "boot",
                    source: "client_personal", origin: "client_personal",
                    repeat: cb.repeat || "daily",
                    time_hhmm: cb.time || "08:00",
                    weekdays: Array.isArray(cb.weekdays)
                        ? cb.weekdays.join("") : undefined,
                    once_date: cb.once_date || "",
                    next_trigger: window.__stubDstr() + " " +
                        (cb.time || "08:00"),
                    next_ts: window.__stubTs(8, 5), enabled: 1 };
                window.__stubCenterTasks.push(nt);
                return resolve({ success: true, task: nt });
            }
            if (p.indexOf("/api/powercontrol/center-task-delete") === 0) {
                var dm = path.match(/task_id=([^&]+)/);
                var did = dm ? decodeURIComponent(dm[1]) : "";
                window.__stubCenterDeleteIds.push(did);
                var idx = -1;
                for (var di = 0; di < window.__stubCenterTasks.length; di++) {
                    if (String(window.__stubCenterTasks[di].task_id) === did) {
                        idx = di; break;
                    }
                }
                if (idx < 0) {
                    return resolve({ success: false, error: "任务不存在" });
                }
                window.__stubCenterTasks.splice(idx, 1);
                return resolve({ success: true });
            }
            if (p.indexOf("/api/powercontrol/snapshot") === 0) {
                if (window.__stubPcMode === "error") {
                    return resolve({ success: false, error: "stub snapshot failure" });
                }
                return resolve(window.__stubPcMode === "consumer"
                    ? window.__stubPcCon : window.__stubPcEnt);
            }
            if (p.indexOf("/api/powercontrol/report") === 0) {
                window.__stubReportCalls = (window.__stubReportCalls || 0) + 1;
                try { var rb = body ? JSON.parse(body) : null; }
                catch (e) { var rb = null; }
                if (rb && rb.human_set) {
                    window.__stubPcCon.snapshot.bios.human_set_flag = true;
                    window.__stubPcCon.snapshot.bios.human_set_at =
                        "2026-09-16 20:30:00";
                }
                return resolve({ success: true, reported: true,
                    server: { ok: true, snapshot_id: 7 } });
            }
            if (path.indexOf("/api/powercontrol/task-status") === 0) {
                var m = path.match(/task_id=([^&]+)/);
                var tid = m ? m[1] : "?";
                window.__stubTaskPolls[tid] =
                    (window.__stubTaskPolls[tid] || 0) + 1;
                var r = window.__stubPcTasks[tid] || { ok: true };
                if (window.__stubTaskPolls[tid] <= 2) {
                    return resolve({ success: true, task:
                        { task_id: tid, kind: "stub", status: "running",
                          result: null } });
                }
                return resolve({ success: true, task:
                    { task_id: tid, kind: "stub",
                      status: r.ok ? "done" : "error", result: r } });
            }
            if (p.indexOf("/api/powercontrol/bios-apply") === 0
                    || p.indexOf("/api/powercontrol/bios-restore") === 0
                    || p.indexOf("/api/powercontrol/shutdown-set") === 0
                    || p.indexOf("/api/powercontrol/shutdown-remove") === 0
                    || p.indexOf("/api/powercontrol/shutdown-toggle") === 0) {
                try { window.__stubLastBiosPayload = body ? JSON.parse(body) : null; }
                catch (e) { window.__stubLastBiosPayload = null; }
                if (window.__stubPcOpFail) {
                    return resolve({ success: false, error: "stub op fail" });
                }
                var tid2 = "PCT-" + (++window.__stubNextTaskId);
                window.__stubPcTasks[tid2] = {
                    ok: !window.__stubPcOpResultFail,
                    error: window.__stubPcOpResultFail
                        ? "回读校验不符：「Wake Up on Alarm」写入未生效：读回为「Disabled」（目标「Weekly Event」）"
                        : null,
                    summary: "每天 08:30:00" };
                return resolve({ success: true, task_id: tid2,
                    summary: "每天 08:30:00" });
            }
            /* 主应用场景兜底：其它模块一律 success:false（安全终止渲染） */
            return resolve({ success: false });
        });
    }
} };
"""

MAIN_STUB_EXTRA = r"""
/* 主应用场景：与 PC_STUB_JS 同用（PC 桩已兜底全部路由），此处无新增 */
"""


def assert_functions(page, fns, tag):
    for fn in fns:
        check("[%s] %s 为函数" % (tag, fn),
              page.evaluate("typeof %s" % fn) == "function")


def assert_no_horizontal_scroll(page, tag, w):
    check("%s·W%d 无横向滚动" % (tag, w), page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
        " && document.body.scrollWidth <= document.body.clientWidth + 1"))


def width_sweep(page, tag):
    for w in (1920, 1440, 1080):
        page.set_viewport_size({"width": w, "height": 960})
        page.wait_for_timeout(250)
        assert_no_horizontal_scroll(page, tag, w)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.wait_for_timeout(200)


def run_scenario_1(page):
    print("\n[场景1] 独立测试页（powercontrol-standalone.html + 桩）")
    page.goto(STANDALONE.as_uri())
    page.wait_for_timeout(800)
    check_no_real_ip_sources()
    check_no_real_ip_assets(page, "pc")
    check_no_local_power_entry(page, "独立页")
    check("Rename·独立页标题含自动开关机", "自动开关机" in page.title())

    assert_functions(page, ["pcLoadSnapshot", "pcRenderSnapshot",
                            "pcRenderBios"], "pc")
    page.wait_for_function(
        "document.getElementById('pcBiosBody').textContent.indexOf('定时开机') >= 0"
        " && document.getElementById('pcBiosBody').textContent.indexOf('已开启') >= 0",
        timeout=6000)
    # 企业线分支
    check("企业线·机型徽章", "ThinkCentre M720t-D234" ==
          page.inner_text("#pcMachineBadge"))
    check("企业线·能力徽章", page.inner_text("#pcCapBadge") == "企业线可配置")
    check("企业线·计划摘要", "每天 08:00:00" in page.inner_text("#pcBiosBody"))
    check("企业线·周期行", "每天" in page.inner_text("#pcBiosBody"))
    check("企业线·网络唤醒行", "Automatic" in page.inner_text("#pcBiosBody"))
    check("企业线·无人工指引", "BIOS 设置" not in page.inner_text("#pcBiosBody"))
    # 2026-09-17 三卡结构调整：唤醒定时器/快速启动卡移除、关机任务表并入定时关机卡
    # 2026-09-18（4.1.8）：平台存档卡删除，页面回归三卡（本机快照/BIOS 定时开机/定时关机）
    check("结构·唤醒定时器卡已移除", page.evaluate(
        "document.getElementById('pcWakeBody') === null"))
    check("结构·快速启动卡已移除", page.evaluate(
        "document.getElementById('pcFastBody') === null"))
    check("结构·关机任务独立卡已移除", page.evaluate(
        "document.getElementById('pcTasksBody') === null"))
    check("结构·平台存档卡已删除", page.evaluate(
        "(function(){ var els = document.querySelectorAll('.section-card');"
        " for (var i = 0; i < els.length; i++) {"
        "  if (els[i].textContent.indexOf('平台存档') >= 0) return false; }"
        " return true; })()"))
    check("结构·三卡计数（不含子卡）", page.evaluate(
        "document.querySelectorAll('.section-card').length") == 3)
    check("结构·状态栏无平台存档", page.evaluate(
        "document.body.textContent.indexOf('平台存档') < 0"))
    check("关机任务·并入小节表 1 行", page.evaluate(
        "document.querySelectorAll('#pcSdTasksBody tbody tr').length") == 1)
    check("文案·实现细节零残留", page.evaluate(
        "(function(){ var t = document.body.textContent.toLowerCase();"
        " return t.indexOf('wmi') < 0 && t.indexOf('uplink_config') < 0"
        " && t.indexOf('lenovo_biossetting') < 0 && t.indexOf('app_config') < 0; })()"))

    # 手动上报链路（4.1.8 删除）：页面级自动同步/「上报平台」按钮已随平台
    # 存档卡移除；唯一人工动作为 BIOS 卡「登记到平台」（消费线分支）。

    # ---- 4.1.8 追加 · 定时开机（中心任务）卡：结构 + 未连接门控降级 ----
    check("结构·旧BIOS配置卡已移除", page.evaluate(
        "document.getElementById('pcBiosCfgCard') === null"
        " && document.getElementById('pcBootMode') === null"))
    check("结构·中心任务卡存在", page.evaluate(
        "!!document.getElementById('pcCenterCard')"
        " && !!document.getElementById('pcCenterBody')"))
    page.wait_for_function(
        "document.getElementById('pcCenterBody').textContent"
        ".indexOf('连接中心后开放定时开机管理') >= 0", timeout=6000)
    check("门控·未连接置灰说明", True)
    check("门控·人工指引一行（BIOS 手工设置）", page.evaluate(
        "document.getElementById('pcCenterBody').textContent"
        ".indexOf('RTC Alarm') >= 0"))
    check("门控·连接徽章（未连接）", page.inner_text(
        "#pcCenterConnBadge") == "中心未连接")
    check("门控·表单不可用（未渲染）", page.evaluate(
        "document.getElementById('pcCenterMode') === null"))

    # ---- 中心任务卡：连接后生效任务列表（排序/来源/配额）----
    page.evaluate("window.__stubUplinkState = 'connected'")
    page.click("#pcBtnCenterRefresh")
    page.wait_for_function(
        "document.querySelectorAll('#pcCenterListBody tbody tr').length === 3",
        timeout=6000)
    check("中心·连接徽章（已连接）", page.inner_text(
        "#pcCenterConnBadge") == "中心已连接")
    check("中心·排序采用中心 next_ts（最早在最上）", page.evaluate(
        "(function(){ var rows ="
        " document.querySelectorAll('#pcCenterListBody tbody tr');"
        " return rows[0].cells[1].textContent.indexOf('个性化早班开机') >= 0"
        " && rows[1].cells[1].textContent.indexOf('全局晚班开机') >= 0"
        " && rows[2].cells[1].textContent.indexOf('节假日值班开机') >= 0; })()"))
    check("中心·配额行展示", "1 / 5" in page.inner_text("#pcCenterBody"))
    check("中心·来源徽章（个性化）", page.evaluate(
        "(function(){ var rows ="
        " document.querySelectorAll('#pcCenterListBody tbody tr');"
        " return rows[0].cells[4].textContent.indexOf('个性化') >= 0; })()"))
    check("中心·下次触发采用中心文本", page.evaluate(
        "(function(){ var rows ="
        " document.querySelectorAll('#pcCenterListBody tbody tr');"
        " return rows[0].cells[5].textContent.indexOf('07:30') >= 0; })()"))
    check("中心·日历近似标记（calendar_fallback）", page.evaluate(
        "(function(){ var rows ="
        " document.querySelectorAll('#pcCenterListBody tbody tr');"
        " return rows[2].cells[5].textContent.indexOf('≈') >= 0; })()"))
    check("中心·删除入口仅在个性化行", page.evaluate(
        "(function(){ var rows ="
        " document.querySelectorAll('#pcCenterListBody tbody tr');"
        " return rows[0].cells[6].querySelector('button') !== null"
        " && rows[2].cells[6].querySelector('button') === null; })()"))

    # ---- 个性化任务创建（weekly 勾选 → 提交 → payload → 列表刷新）----
    page.select_option("#pcCenterMode", "weekly")
    check("中心·表单联动·每周勾选组", page.evaluate(
        "document.getElementById('pcCenterWeekRow').style.display !== 'none'"))
    page.evaluate("(function(){ var b = document.querySelectorAll('.pc-cwd');"
                  " for (var i = 0; i < 5; i++) { b[i].checked = true; } })()")
    page.fill("#pcCenterTime", "08:05")
    page.fill("#pcCenterRemark", "已在 BIOS 手工设置，此为登记")
    page.click("#pcBtnCenterCreate")
    pc_confirm_modal(page)
    page.wait_for_function(
        "document.getElementById('pcCenterTip') &&"
        " document.getElementById('pcCenterTip').textContent"
        ".indexOf('已提交') >= 0", timeout=8000)
    check("中心·创建payload（repeat/weekdays/时刻/备注；origin 由引擎盖章见单测）",
          page.evaluate(
        "(function(){ var p = window.__stubCenterCreatePayload || {};"
        " return p.repeat === 'weekly' && p.time === '08:05'"
        " && p.weekdays.join(',') === '1,1,1,1,1,0,0'"
        " && (p.remark || '').indexOf('BIOS') >= 0; })()"))
    check("中心·创建后列表刷新", page.evaluate(
        "document.querySelectorAll('#pcCenterListBody tbody tr').length === 4"))

    # ---- 超限如实提示（重渲染后表单为空，重填再提交）+ 拉取失败诚实可见 ----
    page.evaluate("window.__stubCenterLimit = true")
    page.select_option("#pcCenterMode", "weekly")
    page.evaluate("(function(){ var b = document.querySelectorAll('.pc-cwd');"
                  " for (var i = 0; i < 5; i++) { b[i].checked = true; } })()")
    page.fill("#pcCenterTime", "08:05")
    page.click("#pcBtnCenterCreate")
    pc_confirm_modal(page)
    page.wait_for_function(
        "document.getElementById('pcCenterTip').textContent"
        ".indexOf('上限') >= 0", timeout=8000)
    check("中心·超限如实提示", True)
    page.evaluate("window.__stubCenterLimit = false")
    page.evaluate("window.__stubCenterFail = true")
    page.click("#pcBtnCenterRefresh")
    page.wait_for_function(
        "document.getElementById('pcCenterBody').textContent"
        ".indexOf('任务列表获取失败') >= 0", timeout=6000)
    check("中心·拉取失败可见且表单保留", page.evaluate(
        "!!document.getElementById('pcCenterMode')"))
    page.evaluate("window.__stubCenterFail = false")
    page.click("#pcBtnCenterRefresh")
    page.wait_for_function(
        "document.querySelectorAll('#pcCenterListBody tbody tr').length === 4",
        timeout=6000)

    # ---- 个性化任务删除（danger 确认 → DELETE → 列表刷新）----
    page.click("#pcCenterListBody tbody tr:first-child button")
    pc_confirm_modal(page)
    page.wait_for_function(
        "document.getElementById('pcCenterTip') &&"
        " document.getElementById('pcCenterTip').textContent"
        ".indexOf('已删除') >= 0", timeout=8000)
    check("中心·删除流程（确认→DELETE→刷新）", page.evaluate(
        "(function(){ return window.__stubCenterDeleteIds.indexOf('2') >= 0"
        " && document.querySelectorAll('#pcCenterListBody tbody tr')"
        ".length === 3; })()"))

    # ---- P1 · 定时关机卡（保存/停用/删除流程）----
    page.fill("#pcSdTime", "22:00")
    page.click("#pcBtnSdSave")
    pc_confirm_modal(page)
    page.wait_for_function(
        "document.getElementById('pcSdTip').textContent.indexOf('已保存并启用') >= 0",
        timeout=10000)
    check("P1·关机保存流程完成", True)
    page.click("#pcBtnSdToggle")
    page.wait_for_function(
        "document.getElementById('pcSdTip').textContent.indexOf('已启用') >= 0",
        timeout=10000)
    check("P1·关机启停流程完成", True)
    page.click("#pcBtnSdRemove")
    pc_confirm_modal(page)
    page.wait_for_function(
        "document.getElementById('pcSdTip').textContent.indexOf('已删除') >= 0",
        timeout=10000)
    check("P1·关机删除流程完成", True)

    # 消费线分支（切换桩重载；4.1.8 紧凑指引单行）
    page.evaluate("window.__stubPcMode = 'consumer'")
    page.click("#pcBtnRefresh")
    page.wait_for_function(
        "document.getElementById('pcBiosBody').textContent.indexOf('RTC Alarm') >= 0",
        timeout=6000)
    check("消费线·能力徽章", page.inner_text("#pcCapBadge") == "不支持远程配置")
    check("消费线·紧凑指引（一行核心）", page.evaluate(
        "(function(){ var b = document.getElementById('pcBiosBody');"
        " return b.textContent.indexOf('如需定时开机') >= 0"
        " && b.textContent.indexOf('RTC Alarm') >= 0"
        " && b.querySelectorAll('.nd-hint').length <= 2; })()"))
    check("消费线·登记按钮已并入个性化维护（快照卡无按钮）", page.evaluate(
        "document.querySelectorAll('#pcBiosBody button').length === 0"))
    check("消费线·关机任务空态", "未发现关机类计划任务" in
          page.inner_text("#pcSdTasksBody"))

    # 错误分支
    page.evaluate("window.__stubPcMode = 'error'")
    page.click("#pcBtnRefresh")
    page.wait_for_function(
        "document.getElementById('pcErr').textContent.indexOf('读取失败') >= 0",
        timeout=6000)
    check("错误分支·提示可见", True)

    # 宽度三档 + 主体大断点
    page.evaluate("window.__stubPcMode = 'enterprise'")
    page.click("#pcBtnRefresh")
    page.wait_for_timeout(500)
    width_sweep(page, "独立页")
    page.set_viewport_size({"width": 1920, "height": 960})
    page.wait_for_timeout(250)
    check("独立页·主体上限大断点 1600px（禁写死窄值）", page.evaluate(
        "getComputedStyle(document.querySelector('.main-content')).maxWidth") == "1600px")


def run_scenario_2(page):
    print("\n[场景2] 主应用集成（web/index.html + 全模块兜底桩）遍历全部菜单")
    page.goto(MAIN_INDEX.as_uri())
    page.wait_for_timeout(1500)
    check_no_real_ip_assets(page, "main")
    check_no_local_power_entry(page, "主应用")

    assert_functions(page, ["switchTab", "initHomeTab", "initLogInspectorTab",
                            "initDiskTab", "initPerfTab", "initNetDoctorTab",
                            "initFileSearchTab", "initDesktopPolicyTab",
                            "initPowerControlTab", "pcLoadSnapshot",
                            "pcRenderSnapshot"], "main")
    # 菜单位置：自动开关机紧跟锁屏及壁纸管理之后（派单要求）
    check("主应用·菜单位置（锁屏及壁纸管理之后）", page.evaluate(
        "(function(){ var tabs = document.querySelectorAll('.nav-tab');"
        " var seq = []; for (var i = 0; i < tabs.length; i++)"
        " { seq.push(tabs[i].getAttribute('data-tab')); }"
        " return seq.indexOf('powercontrol') === seq.indexOf('desktoppolicy') + 1; })()"))
    check("主应用·导航文案自动开关机", page.evaluate(
        "document.querySelector('button[data-tab=powercontrol]').textContent.indexOf('自动开关机') >= 0"))

    tabs = ["loginspector", "disk", "perf", "netdoctor", "filesearch",
            "desktoppolicy", "powercontrol"]
    for tab in tabs:
        # desktoppolicy 导航默认隐藏（平台连接后显示），隐藏时经 switchTab 直调；
        # 未接入平台时 desktoppolicy 门控回跳主页（既有保护行为，见 desktoppolicy.js
        # initDesktopPolicyTab），故对该 tab 断言「被门控回跳」而非「保持激活」。
        visible = page.evaluate(
            "(function(){ var b = document.querySelector("
            "'button[data-tab=\"%s\"]'); return !!b && b.offsetParent !== null; })()"
            % tab)
        if visible:
            page.click('button[data-tab="%s"]' % tab)
        else:
            page.evaluate("switchTab('%s')" % tab)
        page.wait_for_timeout(600)
        if tab == "desktoppolicy":
            check("desktoppolicy·未接入平台门控回跳主页", page.evaluate(
                "document.getElementById('tab-home').classList.contains('active')"
                " && !document.getElementById('tab-desktoppolicy')"
                ".classList.contains('active')"))
            continue
        check("切换 %s 后激活" % tab, page.evaluate(
            "document.getElementById('tab-%s').classList.contains('active')" % tab))

    # 自动开关机数据填充（企业线夹具）
    page.wait_for_function(
        "document.getElementById('pcBiosBody').textContent.indexOf('已开启') >= 0",
        timeout=6000)
    check("主应用·机型徽章", "ThinkCentre M720t-D234" ==
          page.inner_text("#pcMachineBadge"))
    check("主应用·能力徽章", page.inner_text("#pcCapBadge") == "企业线可配置")
    check("主应用·计划摘要", "每天 08:00:00" in page.inner_text("#pcBiosBody"))
    check("主应用·三卡结构（唤醒/快速启动/任务独立卡移除）", page.evaluate(
        "(function(){ return document.getElementById('pcWakeBody') === null"
        " && document.getElementById('pcFastBody') === null"
        " && document.getElementById('pcTasksBody') === null; })()"))
    check("主应用·关机任务并入小节表 1 行", page.evaluate(
        "document.querySelectorAll('#pcSdTasksBody tbody tr').length") == 1)
    check("主应用·平台当前任务徽章（未创建）", "未创建" in
          page.inner_text("#pcSdCurrent"))

    # ---- 4.1.8 追加 · 中心任务卡：旧配置卡移除 + 未连接门控 ----
    check("主应用·旧BIOS配置卡已移除", page.evaluate(
        "document.getElementById('pcBiosCfgCard') === null"
        " && document.getElementById('pcBootMode') === null"))
    page.wait_for_function(
        "document.getElementById('pcCenterBody') &&"
        " document.getElementById('pcCenterBody').textContent"
        ".indexOf('连接中心后开放定时开机管理') >= 0", timeout=6000)
    check("主应用·中心任务卡未连接门控", True)
    page.evaluate("window.__stubPcMode = 'consumer'")
    page.click("#pcBtnRefresh")
    page.wait_for_function(
        "document.getElementById('pcBiosBody').textContent.indexOf('RTC Alarm') >= 0",
        timeout=6000)
    check("主应用·紧凑指引单行可见（4.1.8）", page.evaluate(
        "(function(){ var b = document.getElementById('pcBiosBody');"
        " return b.textContent.indexOf('如需定时开机') >= 0"
        " && b.querySelectorAll('.nd-hint').length <= 2; })()"))
    check("主应用·登记按钮已并入个性化维护（快照卡无按钮）", page.evaluate(
        "document.querySelectorAll('#pcBiosBody button').length === 0"))
    page.evaluate("window.__stubPcMode = 'enterprise'")
    page.click("#pcBtnRefresh")
    page.wait_for_function(
        "document.getElementById('pcBiosBody').textContent.indexOf('已开启') >= 0",
        timeout=6000)
    check("文案·实现细节零残留", page.evaluate(
        "(function(){ var t = document.getElementById('tab-powercontrol').textContent;"
        " return t.indexOf('wmi') < 0 && t.toLowerCase().indexOf('wmi') < 0"
        " && t.indexOf('uplink_config') < 0 && t.indexOf('Lenovo_BiosSetting') < 0; })()"))
    check("样式·卡头字号=13.5px 基线", page.evaluate(
        "(function(){ var h = document.querySelector('#tab-powercontrol .nd-styled h3');"
        " return !!h && getComputedStyle(h).fontSize === '13.5px'; })()"))
    check("样式·卡体padding=15px 17px 基线", page.evaluate(
        "(function(){ var b = document.querySelector('#tab-powercontrol .nd-styled .card-body');"
        " return !!b && getComputedStyle(b).paddingLeft === '17px'"
        " && getComputedStyle(b).paddingTop === '15px'; })()"))

    # 其它模块关键函数在遍历后仍保持（防破坏）
    for fn in ("initDiskTab", "initNetDoctorTab", "initPerfTab",
               "initDesktopPolicyTab"):
        check("防破坏·%s 保持" % fn,
              page.evaluate("typeof %s" % fn) == "function")

    # ---- 三大改造②③：自启设置卡 + 更新提示条 ----
    assert_functions(page, ["initAppUi", "acLoadAutostart", "ubPoll",
                            "ubApplyNow"], "appui")
    check("改造②·自启卡存在于设置弹窗", page.evaluate(
        "!!document.getElementById('acAutostartBadge')"))
    page.evaluate("acLoadAutostart()")
    page.wait_for_function(
        "document.getElementById('acAutostartBadge').textContent === '未开启'",
        timeout=4000)
    check("改造②·自启状态渲染（未开启）", True)
    page.evaluate("acToggleAutostart()")
    page.wait_for_function(
        "document.getElementById('acAutostartBadge').textContent === '已开启'",
        timeout=4000)
    check("改造②·自启开关往返（已开启）",
          page.evaluate("window.__stubAutostart") is True)
    check("改造③·提示条默认隐藏", page.evaluate(
        "document.getElementById('ubBanner').style.display === 'none'"))
    page.evaluate("window.__stubUpdReady = true")
    page.evaluate("ubPoll()")
    page.wait_for_function(
        "document.getElementById('ubBanner').style.display === 'flex'"
        " && document.getElementById('ubVer').textContent === '4.1.0'",
        timeout=4000)
    check("改造③·新版本提示条展示", True)
    page.evaluate("ubDismiss()")
    check("改造③·稍后收纳（本会话不再弹）", page.evaluate(
        "document.getElementById('ubBanner').style.display === 'none'"
        " && sessionStorage.getItem('ubDismissedVer') === '4.1.0'"))
    page.evaluate("ubPoll()")
    page.wait_for_timeout(300)
    check("改造③·稍后同版本不再弹", page.evaluate(
        "document.getElementById('ubBanner').style.display === 'none'"))
    page.evaluate("sessionStorage.removeItem('ubDismissedVer')")
    page.evaluate("ubPoll()")
    page.wait_for_function(
        "document.getElementById('ubBanner').style.display === 'flex'",
        timeout=4000)
    page.click("#ubApplyBtn")
    page.wait_for_function(
        "document.getElementById('ubTip').textContent.indexOf('正在退出并安装') >= 0",
        timeout=4000)
    check("改造③·立即更新链路（apply→退出提示）",
          page.evaluate("window.__stubUpdApplyCalls") == 1)
    page.evaluate("window.__stubUpdReady = false")
    page.evaluate("ubPoll()")
    page.wait_for_timeout(300)

    # powercontrol tab 宽度三档
    width_sweep(page, "主应用·powercontrol")


def main():
    with sync_playwright() as p:
        # 场景 1：独立页
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors1 = []
        page.on("pageerror", lambda e: errors1.append("S1:" + str(e)))
        # 2026-09-17：原生 dialog 已废除（pcConfirm 自绘弹窗）；再出现即记为失败
        page.on("dialog", lambda d: (errors1.append("S1: native dialog: " + d.type),
                                     d.dismiss()))
        page.add_init_script(PC_STUB_JS)
        try:
            run_scenario_1(page)
        finally:
            browser.close()

        # 场景 2：主应用
        browser = p.chromium.launch()
        page2 = browser.new_page(viewport={"width": 1440, "height": 900})
        errors2 = []
        page2.on("pageerror", lambda e: errors2.append("S2:" + str(e)))
        page2.on("dialog", lambda d: (errors2.append("S2: native dialog: " + d.type),
                                      d.dismiss()))
        page2.add_init_script(PC_STUB_JS)
        page2.add_init_script(MAIN_STUB_EXTRA)
        try:
            run_scenario_2(page2)
        finally:
            browser.close()

    check("pageerror=0（独立页）", not errors1, errors1[:3])
    check("pageerror=0（主应用）", not errors2, errors2[:3])

    n_pass = sum(1 for r in _results if r)
    n_total = len(_results)
    print("\n== E2E 完成：%d/%d 通过 ==" % (n_pass, n_total))
    return 0 if n_pass == n_total else 1


if __name__ == "__main__":
    sys.exit(main())
