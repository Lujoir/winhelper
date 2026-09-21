# -*- coding: utf-8 -*-
"""power-control · mock 单测（P0 门禁）。

覆盖：CurrentSetting 解析（RTC 各项/杂项/空值/null）、分线判定、
唤醒定时器解析（中/英）、schtasks CSV 解析与关机任务筛选（夹具用
stub_shutdown_sim.exe 占位，禁真实关机命令串，ADR-018 惯例）、
快速启动判定、快照组装（fake runner）、重试、上报（not_registered/成功）。

运行：python tools/test_power_control.py
"""
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import power_control as pc  # noqa: E402


# ----------------------------------------------------------------------
# 夹具（ThinkCentre M720t 实测 CurrentSetting 样例，ADR-005；
#       关机任务动作串用 stub 占位非真实命令串，ADR-018 惯例）
# ----------------------------------------------------------------------
ENT_ITEMS = [
    "USBPortAccess,Enabled;[Optional:Disabled,Enabled]",      # 杂项
    "After Power Loss,Last State;[Optional:Power Off,Power On,Last State]",
    "WakeOnLAN,Automatic;[Optional:Primary,Automatic,Disabled]",
    "Wake Up on Alarm,Daily Event;"
    "[Optional:Single Event,Daily Event,Weekly Event,Disabled,User Defined]",
    "Alarm Time(HH:MM:SS),[08:00:00]",
    "Alarm Date(MM/DD/YYYY),[01/01/2017][Status:ShowOnly]",
    "Alarm Day of Week,Sunday;[Optional:Sunday,Monday,Tuesday,Wednesday,"
    "Thursday,Friday,Saturday][Status:ShowOnly]",
    "Sunday,Disabled;[Optional:Disabled,Enabled][Status:ShowOnly]",
    "Monday,Disabled;[Optional:Disabled,Enabled][Status:ShowOnly]",
    "Tuesday,Disabled;[Optional:Disabled,Enabled][Status:ShowOnly]",
    "Wednesday,Disabled;[Optional:Disabled,Enabled][Status:ShowOnly]",
    "Thursday,Disabled;[Optional:Disabled,Enabled][Status:ShowOnly]",
    "Friday,Disabled;[Optional:Disabled,Enabled][Status:ShowOnly]",
    "Saturday,Disabled;[Optional:Disabled,Enabled][Status:ShowOnly]",
    "User Defined Alarm Time(HH:MM:SS),[00:00:00][Status:ShowOnly]",
    "Primary Boot Sequence,M.2 Drive:SATA 1:SATA 2:SATA 3:Network 1:USB HDD:"
    "USB CDROM:Other Device;[Excluded from boot order:Network 2:Network 3]",
    None,                        # 全量枚举中的空值项（M720t 实测存在）
    "",
]

WK_A = (
    "[SERVICE] \\Device\\HarddiskVolume4\\Windows\\System32\\svchost.exe "
    "(SystemEventsBroker) 设置的计时器在 18:48:18 过期(位于 2026/9/16 上)。\n"
    "  原因: Windows 将执行“NT TASK\\Microsoft\\Windows\\UpdateOrchestrator"
    "\\Reboot_AC”计划的任务，该任务请求唤醒计算机。\n"
    "[PROCESS] \\Device\\HarddiskVolume3\\Apps\\upd.exe 设置的计时器在 "
    "20:00:00 过期(位于 2026/9/17 上)。\n")

WK_B = (
    "有以下唤醒计时器可用-\n"
    "   计时器 ID [0]: 2026-09-17T07:30:00 [AUTHOR: Microsoft Corporation] "
    "[DESCRIPTION: Windows 更新]\n"
    "      所有者: [SERVICE?] \\Device\\HarddiskVolume3\\Windows\\System32"
    "\\svchost.exe\n"
    "   计时器 ID [1]: 2026-09-17T12:00:00 [AUTHOR: Adobe] [DESCRIPTION: 更新检查]\n"
    "      所有者: [PROCESS?] \\Device\\HarddiskVolume3\\Apps\\upd.exe\n")

WK_EN = (
    "There are wake timers available.\n"
    "   Timer ID [0]: 2026-09-18T03:00:00 [AUTHOR: Microsoft Corporation] "
    "[DESCRIPTION: Maintenance]\n"
    "      Owner: [SERVICE?] stub-service-host\n")

SCH_CSV_ZH = (
    "主机名,任务名,下次运行时间,状态,登录模式,上次运行时间,上次结果,创建者,"
    "要运行的任务,起始位置,备注,计划任务状态,空闲时间,电源管理,运行身份,"
    "如果计划的任务未重新调度则删除该任务,如果任务运行 X 小时 X 分则停止该任务,"
    "计划,计划类型,开始时间,开始日期,结束日期,天,月,重复: 每,重复: 直到: 时间,"
    "重复: 持续时间,重复: 如果仍在运行则停止\r\n"
    "PC,\\NightlyStub,\"2026/9/17 22:00:00\",就绪,交互式/任何用户,\"2026/9/16 22:00:00\",0,"
    "ADMIN,\"stub_shutdown_sim.exe /s (样例占位，非真实命令)\",,,已启用,无,,SYSTEM,"
    "已禁用,72:00:00,每天,每天,\"22:00:00\",\"2026/8/1\",无,每 1 天,无,无,无,已禁用\r\n"
    "PC,\\BackupJob,\"2026/9/17 03:00:00\",就绪,交互式/任何用户,\"2026/9/16 03:00:00\",0,"
    "ADMIN,\"stub_backup.exe /full\",,,已启用,无,,SYSTEM,已禁用,72:00:00,每天,每天,"
    "\"03:00:00\",\"2026/8/1\",无,每 1 天,无,无,无,已禁用\r\n"
    "PC,\\DailyCleanup,无,就绪,仅交互式,无,267011,USER,\"cleanmgr.exe /sagerun:1\",,"
    "磁盘清理,已启用,无,,USER,已禁用,72:00:00,每天,每天,\"12:00:00\",\"2026/1/1\","
    "无,每 1 天,无,无,无,已禁用\r\n")

SCH_CSV_EN_HEADER = (
    "HostName,TaskName,Next Run Time,Status,Logon Mode,Last Run Time,Last Result,"
    "Author,Task To Run,Start In,Comment,Scheduled Task State,Idle Time,"
    "Power Management,Run As User,Delete Task If Not Rescheduled,"
    "Stop Task If Runs X Hours and X Mins,Schedule,Schedule Type,Start Time,"
    "Start Date,End Date,Days,Months,Repeat: Every,Repeat: Until: Time,"
    "Repeat: Until: Duration,Repeat: Stop If Still Running\r\n"
    "PC,\\StubShutdown,\"2026/9/17 22:00:00\",Ready,Interactive only,"
    "\"2026/9/16 22:00:00\",0,ADMIN,\"stub_shutdown_sim.exe (sample stub)\",,,"
    "Enabled,None,,SYSTEM,Disabled,72:00:00,Daily,Daily,\"22:00:00\","
    "\"2026/8/1\",None,Every 1 day,None,None,None,Disabled\r\n")

PCA_ON = ("以下睡眠状态可用:\n    待机 (S3)\n    休眠\n    快速启动\n"
          "以下睡眠状态在此系统上不可用:\n    待机 (S1)\n")
PCA_OFF = ("以下睡眠状态可用:\n    待机 (S3)\n"
           "以下睡眠状态在此系统上不可用:\n    休眠\n    快速启动\n"
           "    待机 (S1)\n")
PCA_EN = ("The following sleep states are available: Standby (S3), Fast Startup\n"
          "The following sleep states are not available on this system: Standby (S1)\n")

MACHINE_JSON = ('[{"Name":"STUB-PC","Manufacturer":"LENOVO",'
                '"Model":"91XX000STUB","SystemFamily":"ThinkCentre M720t"}]')


def fake_runner(script_outputs):
    """构造 fake runner：按首个 token/命令关键词路由到预置输出。
    outputs: {关键词: (rc, out, err)}"""
    def run(args, timeout):
        key = args[0] if args else ""
        joined = " ".join(args)
        for kw, val in script_outputs.items():
            if kw in joined:
                rc, out, err = val
                return rc, out, err
        return 0, "", "no stub for %s" % key
    return run


def ent_runner(**overrides):
    """企业线全链 fake runner（可覆盖各段输出）。"""
    outs = {
        "Win32_ComputerSystem": (0, MACHINE_JSON, ""),
        "Get-CimClass": (0, '[{"ClassName":"Lenovo_BiosSetting"}]', ""),
        "Lenovo_BiosSetting": (0, None, ""),
        "/waketimers": (0, WK_A, ""),
        "schtasks": (0, SCH_CSV_ZH, ""),
        "/a": (0, PCA_ON, ""),
    }
    outs["Lenovo_BiosSetting"] = (0, _ps_array(ENT_ITEMS), "")
    outs.update(overrides)
    return fake_runner(outs)


def _ps_array(items):
    """模拟 PowerShell ConvertTo-Json 数组输出（None→null）。"""
    import json as _json
    return _json.dumps([i for i in items], ensure_ascii=False)


class TestParseCurrentSetting(unittest.TestCase):
    def test_entry_form1_optional(self):
        e = pc.parse_current_setting_entry(
            "Wake Up on Alarm,Daily Event;"
            "[Optional:Single Event,Daily Event,Weekly Event,Disabled,"
            "User Defined]")
        self.assertEqual(e["item"], "Wake Up on Alarm")
        self.assertEqual(e["value"], "Daily Event")
        self.assertEqual(len(e["optional"]), 5)
        self.assertEqual(e["status"], None)

    def test_entry_form2_bracket_value(self):
        e = pc.parse_current_setting_entry(
            "Alarm Time(HH:MM:SS),[08:00:00]")
        self.assertEqual(e["value"], "08:00:00")
        self.assertEqual(e["status"], None)

    def test_entry_bracket_value_with_status(self):
        e = pc.parse_current_setting_entry(
            "Alarm Date(MM/DD/YYYY),[01/01/2017][Status:ShowOnly]")
        self.assertEqual(e["value"], "01/01/2017")
        self.assertEqual(e["status"], "ShowOnly")

    def test_entry_optional_and_status(self):
        e = pc.parse_current_setting_entry(
            "Alarm Day of Week,Sunday;[Optional:Sunday,Monday,Saturday]"
            "[Status:ShowOnly]")
        self.assertEqual(e["value"], "Sunday")
        self.assertEqual(len(e["optional"]), 3)
        self.assertEqual(e["status"], "ShowOnly")

    def test_entry_excluded_note(self):
        e = pc.parse_current_setting_entry(
            "Primary Boot Sequence,M.2 Drive:SATA 1:Network 1;"
            "[Excluded from boot order:Network 2:Network 3]")
        self.assertEqual(e["value"], "M.2 Drive:SATA 1:Network 1")
        self.assertIn("Excluded from boot order", e["note"] or "")

    def test_entry_empty_skipped(self):
        self.assertIsNone(pc.parse_current_setting_entry(None))
        self.assertIsNone(pc.parse_current_setting_entry(""))
        self.assertIsNone(pc.parse_current_setting_entry("   "))

    def test_enterprise_rtc_full(self):
        r = pc.parse_current_setting(ENT_ITEMS)
        self.assertTrue(r["rtc_present"])
        rtc = r["rtc"]
        self.assertEqual(rtc.get("alarm"), "Daily Event")
        self.assertTrue(rtc.get("alarm_on"))
        self.assertEqual(rtc.get("time"), "08:00:00")
        self.assertEqual(rtc.get("user_time"), "00:00:00")
        self.assertEqual(rtc.get("date"), "01/01/2017")
        self.assertEqual(rtc.get("day"), "Sunday")
        self.assertEqual(rtc.get("after_power_loss"), "Last State")
        self.assertEqual(rtc.get("wake_on_lan"), "Automatic")
        self.assertEqual(rtc.get("cycle_text"), "每天")
        self.assertEqual(rtc.get("summary"), "每天 08:00:00")
        self.assertEqual(len(rtc.get("weekdays") or {}), 7)
        self.assertTrue(all(v == "Disabled"
                            for v in rtc["weekdays"].values()))

    def test_unknown_items_preserved(self):
        r = pc.parse_current_setting(ENT_ITEMS)
        names = [i["item"] for i in r["items"]]
        self.assertIn("USBPortAccess", names)
        self.assertIn("Primary Boot Sequence", names)
        self.assertNotIn("USBPortAccess", r["rtc"])

    def test_alarm_disabled(self):
        r = pc.parse_current_setting([
            "Wake Up on Alarm,Disabled;"
            "[Optional:Single Event,Daily Event,Weekly Event,Disabled]",
            "Alarm Time(HH:MM:SS),[08:00:00]",
        ])
        self.assertFalse(r["rtc"]["alarm_on"])
        self.assertEqual(r["rtc"]["summary"], "已关闭")

    def test_alarm_user_defined(self):
        items = ["Wake Up on Alarm,User Defined;"
                 "[Optional:Single Event,Daily Event,Weekly Event,Disabled,"
                 "User Defined]",
                 "User Defined Alarm Time(HH:MM:SS),[07:15:00]"
                 "[Status:ShowOnly]",
                 "Monday,Enabled;[Optional:Disabled,Enabled][Status:ShowOnly]",
                 "Tuesday,Disabled;[Optional:Disabled,Enabled][Status:ShowOnly]"]
        rtc = pc.parse_current_setting(items)["rtc"]
        self.assertEqual(rtc["cycle_text"], "自定义（按星期）")
        self.assertEqual(rtc["summary"], "指定星期（Monday） 07:15:00")

    def test_alarm_user_defined_none_enabled(self):
        rtc = pc.parse_current_setting([
            "Wake Up on Alarm,User Defined;[Optional:Disabled,User Defined]",
            "User Defined Alarm Time(HH:MM:SS),[07:15:00][Status:ShowOnly]",
        ])["rtc"]
        self.assertEqual(rtc["summary"], "自定义，星期未启用 07:15:00")

    def test_no_rtc(self):
        r = pc.parse_current_setting(["USBPortAccess,Enabled;"
                                      "[Optional:Disabled,Enabled]"])
        self.assertFalse(r["rtc_present"])
        self.assertEqual(r["rtc"], {})

    def test_real_fixture_dump(self):
        """M720t 全量 255 项实测 dump：解析零崩溃 + 关键项命中（ADR-005）。"""
        fixture = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "fixtures", "lenovo_bios_m720t_20260916.txt")
        self.assertTrue(os.path.isfile(fixture), "fixture 缺失: %s" % fixture)
        with open(fixture, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        # 仅取形如 CurrentSetting 的行（ASCII 数据段；跳过标题/空行/中文标签行）
        candidates = [ln.strip() for ln in lines
                      if ln.strip() and "," in ln
                      and not ln.startswith(("[", "=", " ", "\t"))
                      and ln[:20].isascii()]
        r = pc.parse_current_setting(candidates)
        rtc = r["rtc"]
        self.assertGreater(len(r["items"]), 60)
        self.assertEqual(rtc.get("alarm"), "Daily Event")
        self.assertEqual(rtc.get("time"), "08:00:00")
        self.assertEqual(rtc.get("after_power_loss"), "Last State")
        self.assertEqual(rtc.get("wake_on_lan"), "Automatic")
        self.assertEqual(rtc.get("summary"), "每天 08:00:00")


class TestVendorLine(unittest.TestCase):
    def test_lenovo_enterprise(self):
        k, _t, cap, cap_t = pc.classify_vendor_line("LENOVO", True)
        self.assertEqual((k, cap, cap_t),
                         ("lenovo_enterprise", "enterprise_configurable",
                          "企业线可配置"))

    def test_lenovo_consumer(self):
        k, _t, cap, _ct = pc.classify_vendor_line("LENOVO", False)
        self.assertEqual((k, cap), ("lenovo_consumer", "not_supported"))

    def test_other_vendor(self):
        k, _t, cap, _ct = pc.classify_vendor_line("Dell Inc.", False)
        self.assertEqual((k, cap), ("other", "not_supported"))


class TestWakeTimers(unittest.TestCase):
    def test_parse_format_a_m720t(self):
        """M720t 实测格式 A（ADR-005）。"""
        items = pc.parse_waketimers(WK_A)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["type"], "SERVICE")
        self.assertTrue(items[0]["owner"].startswith("\\Device"))
        self.assertEqual(items[0]["wake_time"], "2026/9/16 18:48:18")
        self.assertIn("Reboot_AC", items[0]["reason"])
        self.assertEqual(items[1]["type"], "PROCESS")
        self.assertEqual(items[1]["wake_time"], "2026/9/17 20:00:00")

    def test_parse_format_b_legacy(self):
        items = pc.parse_waketimers(WK_B)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["wake_time"], "2026-09-17T07:30:00")
        self.assertEqual(items[0]["author"], "Microsoft Corporation")
        self.assertEqual(items[0]["description"], "Windows 更新")
        self.assertTrue(items[0]["owner"].startswith("[SERVICE?]"))

    def test_parse_en(self):
        items = pc.parse_waketimers(WK_EN)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["wake_time"], "2026-09-18T03:00:00")
        self.assertTrue(items[0]["owner"].endswith("stub-service-host"))

    def test_empty(self):
        self.assertEqual(pc.parse_waketimers(""), [])


class TestSchtasksCsv(unittest.TestCase):
    def test_parse_zh_header(self):
        found, rows = pc.parse_schtasks_csv(SCH_CSV_ZH)
        self.assertTrue(found)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["name"], "NightlyStub")
        self.assertIn("stub_shutdown_sim.exe", rows[0]["action"])
        self.assertEqual(rows[0]["schedule_type"], "每天")

    def test_parse_en_header(self):
        found, rows = pc.parse_schtasks_csv(SCH_CSV_EN_HEADER)
        self.assertTrue(found)
        self.assertEqual(len(rows), 1)

    def test_filter_shutdown(self):
        _, rows = pc.parse_schtasks_csv(SCH_CSV_ZH)
        hits = pc.filter_shutdown_tasks(rows)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["name"], "NightlyStub")

    def test_filter_by_name(self):
        rows = [{"name": "每日关机任务", "action": "stub_tool.exe",
                 "next_run": "", "status": "就绪", "schedule_type": "每天"}]
        hits = pc.filter_shutdown_tasks(rows)
        self.assertEqual(len(hits), 1)


class TestFastStartup(unittest.TestCase):
    def test_available_zh(self):
        self.assertTrue(pc.parse_powercfg_available(PCA_ON))

    def test_unavailable_zh(self):
        self.assertFalse(pc.parse_powercfg_available(PCA_OFF))

    def test_available_en(self):
        self.assertTrue(pc.parse_powercfg_available(PCA_EN))

    def test_collect_combines_registry(self):
        orig = pc._read_hiberboot
        try:
            pc._read_hiberboot = lambda: (True, 0)
            r = pc.collect_fast_startup(runner=fake_runner(
                {"/a": (0, PCA_ON, "")}))
            self.assertFalse(r["enabled"])       # 注册表 0 优先
            self.assertTrue(r["available"])
            pc._read_hiberboot = lambda: (False, None)
            r2 = pc.collect_fast_startup(runner=fake_runner(
                {"/a": (0, PCA_OFF, "")}))
            self.assertFalse(r2["enabled"])      # 键缺失回退可用性
        finally:
            pc._read_hiberboot = orig


class TestRetry(unittest.TestCase):
    def test_retry_then_success(self):
        calls = {"n": 0}

        def run(args, timeout):
            calls["n"] += 1
            if calls["n"] < 3:
                return 1, "", "transient"
            return 0, "ok", ""

        rc, out, _ = pc._run_cmd(["stub"], retries=3, runner=run)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "ok")
        self.assertEqual(calls["n"], 3)

    def test_retry_exhausted(self):
        def run(args, timeout):
            return 1, "", "always fail"

        rc, _o, err = pc._run_cmd(["stub"], retries=3, runner=run)
        self.assertEqual(rc, 1)
        self.assertIn("always fail", err)


class TestCollectSnapshot(unittest.TestCase):
    def test_enterprise_snapshot(self):
        snap = pc.collect_snapshot(runner=ent_runner(), probe_bios=True)
        self.assertEqual(snap["errors"], [])
        self.assertEqual(snap["machine"]["manufacturer"], "LENOVO")
        self.assertEqual(snap["machine"]["system_family"], "ThinkCentre M720t")
        self.assertEqual(snap["machine"]["capability"],
                         "enterprise_configurable")
        self.assertTrue(snap["bios"]["remote_configurable"])
        self.assertEqual(snap["bios"]["rtc"].get("alarm"), "Daily Event")
        self.assertTrue(snap["bios"]["rtc"].get("alarm_on"))
        self.assertEqual(snap["wake_timers"]["count"], 2)
        self.assertEqual(snap["shutdown_tasks"]["count"], 1)
        self.assertTrue(snap["fast_startup"]["available"])
        self.assertFalse(snap["wake_timers"]["need_admin"])

    def test_consumer_snapshot(self):
        run = ent_runner(
            **{"Get-CimClass": (0, "[]", ""),
               "Win32_ComputerSystem": (
                   0, '[{"Name":"STUB-PC","Manufacturer":"LENOVO",'
                      '"Model":"91AY000LCP","SystemFamily":'
                      '"IdeaCentre GeekPro-17IAX"}]', "")})
        snap = pc.collect_snapshot(runner=run, probe_bios=True)
        self.assertEqual(snap["machine"]["vendor_line"], "lenovo_consumer")
        self.assertEqual(snap["machine"]["capability"], "not_supported")
        self.assertFalse(snap["bios"]["remote_configurable"])
        self.assertIn("人工", snap["bios"]["reason"])
        self.assertEqual(snap["bios"]["items"], [])

    def test_section_failure_not_fatal(self):
        def run(args, timeout):
            j = " ".join(args)
            if "Win32_ComputerSystem" in j:
                return 1, "", "wmi broken"
            if "Get-CimClass" in j:
                return 1, "", "probe broken"
            if "schtasks" in j:
                return 1, "", "tasks broken"
            if "/waketimers" in j:
                raise RuntimeError("boom")
            if j.endswith("/a"):
                return 0, PCA_ON, ""
            return 0, "", ""

        snap = pc.collect_snapshot(runner=run)
        self.assertEqual(snap["machine"]["manufacturer"], "")
        self.assertGreaterEqual(len(snap["errors"]), 3)
        self.assertTrue(snap["fast_startup"]["available"])
        self.assertFalse(snap["shutdown_tasks"]["ok"])


class TestReport(unittest.TestCase):
    def test_not_registered(self):
        rep = pc.PlatformReporter(config_path=os.path.join(
            os.environ.get("TEMP", "."), "no_such_uplink_config.json"))
        with unittest.mock.patch.object(pc, "_uplink_tid_inproc",
                                        return_value=None), \
                unittest.mock.patch.object(pc, "_hostname_tid",
                                           return_value=None):
            try:
                rep.report_snapshot({})
                self.fail("should raise")
            except RuntimeError as e:
                self.assertEqual(str(e), "not_registered")

    def test_handler_report_not_registered(self):
        rep = pc.PlatformReporter(config_path=os.path.join(
            os.environ.get("TEMP", "."), "no_such_uplink_config.json"))
        fake_snap = {"schema": 1, "collected_at": "t", "collected_ts": 1}
        orig = (pc.PlatformReporter, pc.collect_snapshot)
        try:
            pc.PlatformReporter = lambda *a, **kw: rep
            pc.collect_snapshot = lambda *a, **kw: fake_snap
            with unittest.mock.patch.object(pc, "_uplink_tid_inproc",
                                            return_value=None), \
                    unittest.mock.patch.object(pc, "_hostname_tid",
                                               return_value=None):
                r = pc.handle_pc_report(None)
            self.assertFalse(r["success"])
            self.assertIn("平台", r["error"])
        finally:
            pc.PlatformReporter, pc.collect_snapshot = orig

    def test_handler_report_ok(self):
        class FakeRep(object):
            def report_snapshot(self, snap):
                assert snap["schema"] == 1
                return {"ok": True, "id": 7}

        fake_snap = {"schema": 1, "collected_at": "t", "collected_ts": 1}
        orig = (pc.PlatformReporter, pc.collect_snapshot)
        try:
            pc.PlatformReporter = lambda *a, **kw: FakeRep()
            pc.collect_snapshot = lambda *a, **kw: fake_snap
            r = pc.handle_pc_report(None)
            self.assertTrue(r["success"])
            self.assertEqual(r["server"]["id"], 7)
        finally:
            pc.PlatformReporter, pc.collect_snapshot = orig


# TestThrottle 已随 4.1.8 页面级自动上报链删除（_should_auto_report 移除）


# ======================================================================
# P1 · BIOS 写入闭环 / 定时关机任务 / 提权 worker / 异步任务（全部 mock）
# ======================================================================

class TestValidateTime(unittest.TestCase):

    def test_ok(self):
        self.assertEqual(pc._validate_hhmm("08:00"), "08:00:00")
        self.assertEqual(pc._validate_hhmm("22:05:30"), "22:05:30")
        self.assertEqual(pc._validate_hhmm(" 9:07 "), "09:07:00")

    def test_bad(self):
        for bad in ("", "8:5", "24:00", "12:60", "abc", "12"):
            with self.assertRaises(ValueError):
                pc._validate_hhmm(bad)


class TestValidateDate(unittest.TestCase):

    def test_ok_and_normalize(self):
        self.assertEqual(pc._validate_mmddyyyy("12/31/2026"), "12/31/2026")
        self.assertEqual(pc._validate_mmddyyyy("1/5/2026"), "01/05/2026")
        self.assertEqual(pc._validate_mmddyyyy("2026-03-01"), "03/01/2026")

    def test_bad(self):
        for bad in ("", "13/01/2026", "02/30/2026", "2026/03/01", "x"):
            with self.assertRaises(ValueError):
                pc._validate_mmddyyyy(bad)


class TestBuildBiosTargets(unittest.TestCase):

    def test_daily(self):
        t, s = pc.build_bios_targets({"mode": "daily", "time": "08:30"})
        self.assertEqual(t, [("Wake Up on Alarm", "Daily Event"),
                             ("Alarm Time(HH:MM:SS)", "08:30:00")])
        self.assertIn("08:30:00", s)

    def test_workday(self):
        t, _ = pc.build_bios_targets({"mode": "workday", "time": "07:00"})
        d = dict(t)
        self.assertEqual(d["Wake Up on Alarm"], "Weekly Event")
        for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"):
            self.assertEqual(d[day], "Enabled")
        self.assertEqual(d["Sunday"], "Disabled")
        self.assertEqual(d["Saturday"], "Disabled")
        self.assertEqual(len(t), 8)

    def test_single(self):
        t, _ = pc.build_bios_targets({"mode": "single", "time": "07:30",
                                      "date": "2026-12-31"})
        d = dict(t)
        self.assertEqual(d["Wake Up on Alarm"], "Single Event")
        self.assertEqual(d["Alarm Date(MM/DD/YYYY)"], "12/31/2026")
        self.assertEqual(d["Alarm Time(HH:MM:SS)"], "07:30:00")

    def test_off(self):
        t, _ = pc.build_bios_targets({"mode": "off"})
        self.assertEqual(t, [("Wake Up on Alarm", "Disabled")])

    def test_invalid(self):
        for cfg in ({"mode": "xx"}, {"mode": "daily"}, {"mode": "daily",
                    "time": "25:00"},
                    {"mode": "single", "time": "08:00"},
                    {"mode": "single", "time": "08:00", "date": "bad"}):
            with self.assertRaises(ValueError):
                pc.build_bios_targets(cfg)


def _mk_bios_runner(pool, bracket_only=False, never_apply=False,
                    require_commit=False, save_na=False, iface="old"):
    """构造 mock runner：
    - 写命令（Invoke-CimMethod SetBiosSetting）：按 bracket_only/never_apply
      决定是否落池；require_commit 模拟「写入 pending、Save 后才落盘」；
    - Save 命令（Lenovo_SaveBiosSetting）：require_commit 时 flush pending
      （save_na=False），save_na=True 返回 NA（类不存在）；
    - 读命令返回 CurrentSetting 列表；
    - iface 模拟写接口形态（ADR-006 附注③）："old" 老接口机型（默认）；
      "new" 新一代接口机型（老类不存在由脚本内 catch 兜住、实例级写入
      生效，输出带 PCVIA=new + PCERR）；"none" 双接口均缺失（PCSET=NA、
      池不更新 → 回读不匹配）。"""
    pending = {}

    def runner(args, timeout=None):
        cmd = args[-1]
        if "Lenovo_SaveBiosSetting" in cmd and "SaveBiosSetting" in cmd:
            if save_na:
                return 0, "PCSAVE=NA\n", ""
            if require_commit:
                pool.update(pending)
                pending.clear()
            return 0, "PCSAVE=0\n", ""
        if "Invoke-CimMethod" in cmd:
            m = re.search(r"request = '([^']*)'", cmd)
            item, raw_value = m.group(1).split(",", 1)
            bracket = raw_value.startswith("[") and raw_value.endswith("]")
            value = raw_value[1:-1] if bracket else raw_value
            if iface == "new":
                if not never_apply:
                    pool[item] = value
                rv = "NA" if never_apply else "0"
                return 0, ("PCOLD=ERR:invalid class Lenovo_SetBiosSetting\n"
                           "PCNEW=%s\nPCSET=%s\nPCVIA=new\n"
                           "PCERR=invalid class Lenovo_SetBiosSetting\n"
                           % (rv, rv)), ""
            if iface == "none":
                return 0, ("PCOLD=ERR:invalid class Lenovo_SetBiosSetting\n"
                           "PCNEW=ERR:invalid class no-instance\n"
                           "PCSET=NA\nPCVIA=new\n"
                           "PCERR=invalid class no-instance\n"), ""
            if never_apply:
                pass
            elif require_commit:
                pending[item] = value
            elif bracket_only:
                if bracket:
                    pool[item] = value
            else:
                pool[item] = value
            return 0, ("PCOLD=0\nPCNEW=skip\nPCSET=0\nPCVIA=old\n"), ""
        entries = ["%s,%s" % (k, v) for k, v in pool.items()]
        return 0, json.dumps(entries), ""
    return runner


class TestBiosApplyTargets(unittest.TestCase):

    def test_plain_first_round_success(self):
        pool = {"Wake Up on Alarm": "Disabled"}
        r = pc.bios_apply_targets([("Wake Up on Alarm", "Daily Event")],
                                  runner=_mk_bios_runner(pool))
        self.assertTrue(r["ok"])
        self.assertEqual(r["failed"], [])
        self.assertEqual(r["attempts"][0]["format"], "plain")
        self.assertEqual(r["readback"].get("Wake Up on Alarm"), "Daily Event")

    def test_bracket_fallback(self):
        pool = {"Alarm Time(HH:MM:SS)": "08:00:00"}
        r = pc.bios_apply_targets([("Alarm Time(HH:MM:SS)", "08:30:00")],
                                  runner=_mk_bios_runner(pool,
                                                         bracket_only=True))
        self.assertTrue(r["ok"])
        # plain → commit → bracket → commit（Save 提交每轮一次，ADR-006 附注）
        self.assertEqual([a["format"] for a in r["attempts"]],
                         ["plain", "commit", "bracket", "commit"])
        self.assertEqual(r["readback"].get("Alarm Time(HH:MM:SS)"), "08:30:00")

    def test_no_silent_success(self):
        pool = {"Wake Up on Alarm": "Disabled"}
        r = pc.bios_apply_targets([("Wake Up on Alarm", "Daily Event")],
                                  runner=_mk_bios_runner(pool,
                                                         never_apply=True))
        self.assertFalse(r["ok"])
        self.assertEqual(len(r["attempts"]), 4)   # plain+commit+bracket+commit
        self.assertEqual(r["failed"][0]["item"], "Wake Up on Alarm")
        self.assertEqual(r["failed"][0]["got"], "Disabled")

    def test_multi_item_mixed(self):
        pool = {"Wake Up on Alarm": "Disabled", "Sunday": "Enabled"}
        r = pc.bios_apply_targets(
            [("Wake Up on Alarm", "Weekly Event"), ("Sunday", "Disabled")],
            runner=_mk_bios_runner(pool))
        self.assertTrue(r["ok"])

    def test_commit_flow_success(self):
        """pending 固件（写入不落盘、Save 提交后落盘）——ADR-006 附注修复路径。"""
        pool = {"Alarm Time(HH:MM:SS)": "08:00:00"}
        r = pc.bios_apply_targets(
            [("Alarm Time(HH:MM:SS)", "09:00:00")],
            runner=_mk_bios_runner(pool, require_commit=True))
        self.assertTrue(r["ok"])
        self.assertEqual(r["readback"].get("Alarm Time(HH:MM:SS)"),
                         "09:00:00")
        commits = [a for a in r["attempts"] if a["format"] == "commit"]
        self.assertEqual(commits[0]["rv"], "committed")

    def test_commit_na_skipped(self):
        """无 Save 类固件：提交探测返回 NA → 跳过，写即落盘照常成功。"""
        pool = {"Alarm Time(HH:MM:SS)": "08:00:00"}
        r = pc.bios_apply_targets(
            [("Alarm Time(HH:MM:SS)", "09:00:00")],
            runner=_mk_bios_runner(pool, save_na=True))
        self.assertTrue(r["ok"])
        commits = [a for a in r["attempts"] if a["format"] == "commit"]
        self.assertEqual(commits[0]["rv"], "no-save-class")

    def test_commit_required_but_na_repro(self):
        """用户症状复现：pending 固件 + 无 Save 类提交 → rv=0 接受但不落盘
        → ok=False 且 failed 明细含实际读回值（绝不静默成功）。"""
        pool = {"Alarm Time(HH:MM:SS)": "08:00:00"}
        r = pc.bios_apply_targets(
            [("Alarm Time(HH:MM:SS)", "09:00:00")],
            runner=_mk_bios_runner(pool, require_commit=True, save_na=True))
        self.assertFalse(r["ok"])
        self.assertEqual(r["failed"][0]["got"], "08:00:00")


class TestBiosWriteIfaceFallback(unittest.TestCase):
    """ADR-006 附注③：老接口缺失 → 新一代实例级接口回退（M720t 定案）。"""

    def test_new_interface_machine_succeeds(self):
        """new-interface 机型全链：老类缺失（catch）→ 实例级写入生效。
        old/new 分开记录：old.raw 带 invalid class、new.rv=0（ADR-006 附注④）。"""
        pool = {"Alarm Time(HH:MM:SS)": "08:00:00"}
        r = pc.bios_apply_targets([("Alarm Time(HH:MM:SS)", "09:00:00")],
                                  runner=_mk_bios_runner(pool, iface="new"))
        self.assertTrue(r["ok"])
        w = r["attempts"][0]
        self.assertEqual(w["via"], "new")
        self.assertEqual(w["rv"], 0)
        self.assertEqual(w["err"], "invalid class Lenovo_SetBiosSetting")
        self.assertIsNone(w["old"]["rv"])
        self.assertEqual(w["old"]["raw"],
                         "ERR:invalid class Lenovo_SetBiosSetting")
        self.assertEqual(w["new"]["rv"], 0)
        self.assertEqual(w["new"]["raw"], "0")
        self.assertEqual(r["readback"].get("Alarm Time(HH:MM:SS)"),
                         "09:00:00")

    def test_new_interface_no_save_needed(self):
        """新一代接口无需 Save 提交：commit 探测 NA 跳过照常成功。"""
        pool = {"Wake Up on Alarm": "Disabled"}
        r = pc.bios_apply_targets([("Wake Up on Alarm", "Daily Event")],
                                  runner=_mk_bios_runner(pool, iface="new",
                                                         save_na=True))
        self.assertTrue(r["ok"])
        commits = [a for a in r["attempts"] if a["format"] == "commit"]
        self.assertEqual(commits[0]["rv"], "no-save-class")

    def test_no_write_iface_no_silent_success(self):
        """双接口均缺失：old/new 双双 ERR、PCSET=NA（rv=None）、
        回读不匹配 ok=False——命令从未执行成功不再静默。"""
        pool = {"Alarm Time(HH:MM:SS)": "08:00:00"}
        r = pc.bios_apply_targets([("Alarm Time(HH:MM:SS)", "09:00:00")],
                                  runner=_mk_bios_runner(pool, iface="none"))
        self.assertFalse(r["ok"])
        w = r["attempts"][0]
        self.assertIsNone(w["rv"])
        self.assertIn("no-instance", w["err"])
        self.assertIn("invalid class", w["old"]["raw"])
        self.assertEqual(w["new"]["raw"], "ERR:invalid class no-instance")
        self.assertIsNone(w["new"]["rv"])
        self.assertEqual(r["failed"][0]["got"], "08:00:00")

    def test_old_machine_reports_via_old(self):
        """老接口机型：via=old、old.rv=0、new=skip（未尝试）、err 无异常。"""
        pool = {"Wake Up on Alarm": "Disabled"}
        r = pc.bios_apply_targets([("Wake Up on Alarm", "Daily Event")],
                                  runner=_mk_bios_runner(pool))
        self.assertTrue(r["ok"])
        w = r["attempts"][0]
        self.assertEqual(w["via"], "old")
        self.assertIsNone(w["err"])
        self.assertEqual(w["old"]["rv"], 0)
        self.assertEqual(w["old"]["raw"], "0")
        self.assertEqual(w["new"]["raw"], "skip")
        self.assertIsNone(w["new"]["rv"])

    def test_parse_set_rv_na(self):
        self.assertIsNone(pc._parse_set_rv("PCSET=NA\nPCVIA=new\n"))
        self.assertEqual(pc._parse_set_rv("PCSET=0\nPCVIA=old\n"), 0)
        self.assertIsNone(pc._parse_set_rv(""))

    def test_write_script_fallback_shape(self):
        """写入脚本形态：老/新两处内联同字面量、StartsWith 前缀匹配、
        PCOLD/PCNEW 分开输出 + PCSET/PCVIA/PCERR 兼容三路。"""
        script = pc._ps_bios_write("Alarm Time(HH:MM:SS)", "09:00:00")
        self.assertEqual(script.count("request = 'Alarm Time(HH:MM:SS),09:00:00'"), 2)
        self.assertIn("StartsWith('Alarm Time(HH:MM:SS),')", script)
        self.assertIn("PCOLD=", script)
        self.assertIn("PCNEW=", script)
        self.assertIn("PCSET=", script)
        self.assertIn("PCVIA=", script)
        self.assertIn("PCERR=", script)

    def test_parse_attempt_iface(self):
        out = ("PCOLD=ERR:invalid class\nPCNEW=0\nPCSET=0\nPCVIA=new\n"
               "PCERR=invalid class\n")
        old = pc._parse_attempt_iface(out, "OLD")
        new = pc._parse_attempt_iface(out, "NEW")
        self.assertIsNone(old["rv"])
        self.assertEqual(old["raw"], "ERR:invalid class")
        self.assertEqual(new["rv"], 0)
        self.assertEqual(new["raw"], "0")
        # 缺行 → raw=None 不误判
        missing = pc._parse_attempt_iface("PCSET=NA\n", "NEW")
        self.assertIsNone(missing["raw"])
        self.assertIsNone(missing["rv"])

    def test_diag_wmi_surface(self):
        """diag#3：wmi_surface 穷尽枚举（类名/方法清单/判定依据三项）。"""
        payload = json.dumps({
            "classes": ["Lenovo_BiosSetting", "Lenovo_SetBiosSetting"],
            "methods": {"Lenovo_SetBiosSetting": ["SetBiosSetting"],
                        "Lenovo_BiosSetting": ["GetBiosSelection"],
                        "Lenovo_SaveBiosSetting": None,
                        "Lenovo_BiosSettingInterface": None},
            "old_class_present": True,
            "instance_present": True,
            "inst_has_set_method": False,
        })

        def fake_run(cmd, runner=None, timeout=30):
            s = cmd[-1] if isinstance(cmd, (list, tuple)) else str(cmd)
            if "InputObject $o" in s:
                return 0, payload, ""
            return 0, "PCSC=True", ""

        with unittest.mock.patch.object(pc, "_run_cmd", fake_run), \
             unittest.mock.patch.object(
                 pc, "read_rtc_map", lambda runner=None, timeout=40: {}):
            d = pc.collect_diag()
        ws = d["wmi_surface"]
        self.assertEqual(ws["classes"], ["Lenovo_BiosSetting",
                                         "Lenovo_SetBiosSetting"])
        self.assertEqual(ws["methods"]["Lenovo_BiosSetting"],
                         ["GetBiosSelection"])
        self.assertIsNone(ws["methods"]["Lenovo_SaveBiosSetting"])
        self.assertTrue(ws["old_class_present"])
        self.assertFalse(ws["inst_has_set_method"])

    def test_diag_wmi_surface_parse_fail(self):
        """wmi_surface 输出异常 → 带 error/raw 不阻断其它诊断段。"""
        def fake_run(cmd, runner=None, timeout=30):
            s = cmd[-1] if isinstance(cmd, (list, tuple)) else str(cmd)
            if "InputObject $o" in s:
                return 0, "PCSC=True", ""   # 非 JSON 输出
            return 0, "PCSC=True", ""

        with unittest.mock.patch.object(pc, "_run_cmd", fake_run), \
             unittest.mock.patch.object(
                 pc, "read_rtc_map", lambda runner=None, timeout=40: {}):
            d = pc.collect_diag()
        self.assertIn("error", d["wmi_surface"])
        self.assertEqual(d["wmi_surface"]["raw"], "PCSC=True")
        self.assertTrue(d["save_class"])   # 其它诊断段不受影响


class TestProbeWriteIface(unittest.TestCase):

    def test_probe_forms(self):
        for form in ("old", "new", "none"):
            def runner(args, timeout=None, _f=form):
                return 0, "PCIF=%s\n" % _f, ""
            self.assertEqual(pc.probe_write_iface(runner=runner), form)

    def test_probe_failure_unknown(self):
        def runner(args, timeout=None):
            return 1, "", "boom"
        self.assertEqual(pc.probe_write_iface(runner=runner), "unknown")

    def test_probe_no_output_unknown(self):
        def runner(args, timeout=None):
            return 0, "", ""
        self.assertEqual(pc.probe_write_iface(runner=runner), "unknown")

    def test_probe_lenovo_class_missing_write_none(self):
        """Lenovo_BiosSetting 类缺失 → class_found=False + write_iface=none。"""
        def runner(args, timeout=None):
            cmd = args[-1]
            if "Get-CimClass" in cmd:
                return 0, "[]", ""
            return 0, "", ""
        pr = pc.probe_lenovo_bios(runner=runner)
        self.assertFalse(pr["class_found"])
        self.assertEqual(pr["write_iface"], "none")

    def test_probe_lenovo_new_machine(self):
        """new-interface 机型：类存在 + PCIF=new + 读取正常。"""
        def runner(args, timeout=None):
            cmd = args[-1]
            if "Lenovo_SetBiosSetting" in cmd:   # 写接口形态探测（PCIF）
                return 0, "PCIF=new\n", ""
            if "Get-CimClass" in cmd:            # Lenovo_BiosSetting 类探测
                return 0, '[{"ClassName":"Lenovo_BiosSetting"}]', ""
            if "CurrentSetting" in cmd:          # 实例读取
                return 0, json.dumps(
                    ["Alarm Time(HH:MM:SS),[08:00:00]"]), ""
            return 0, "", ""
        pr = pc.probe_lenovo_bios(runner=runner)
        self.assertTrue(pr["class_found"])
        self.assertEqual(pr["write_iface"], "new")
        self.assertEqual(pr["items_raw"], ["Alarm Time(HH:MM:SS),[08:00:00]"])


class TestBiosBackup(unittest.TestCase):

    def test_save_load_roundtrip(self):
        tmp = os.path.join(tempfile.mkdtemp(prefix="pc_ut_"), "bk.json")
        orig = pc._bios_backup_path
        pc._bios_backup_path = lambda: tmp
        try:
            payload = pc.save_bios_backup({"Wake Up on Alarm": "Daily Event"},
                                          machine={"model": "10SWA03ECD"})
            self.assertFalse(payload["restored"])
            loaded = pc.load_bios_backup()
            self.assertEqual(loaded["items"]["Wake Up on Alarm"],
                             "Daily Event")
            loaded["restored"] = True
            with open(tmp + ".tmp", "w", encoding="utf-8") as f:
                json.dump(loaded, f, ensure_ascii=False)
            os.replace(tmp + ".tmp", tmp)
            self.assertTrue(pc.load_bios_backup()["restored"])
            self.assertEqual(pc.load_bios_backup()["machine"]["model"],
                             "10SWA03ECD")
        finally:
            pc._bios_backup_path = orig

    def test_missing(self):
        orig = pc._bios_backup_path
        pc._bios_backup_path = lambda: os.path.join(
            tempfile.mkdtemp(prefix="pc_ut_"), "none.json")
        try:
            self.assertIsNone(pc.load_bios_backup())
        finally:
            pc._bios_backup_path = orig


class TestShutdownTaskXml(unittest.TestCase):

    def test_daily(self):
        x = pc.build_shutdown_task_xml("daily", "22:00",
                                       now=time.struct_time(
                                           (2026, 9, 16, 10, 0, 0, 2, 0, 0)))
        self.assertIn("<DaysInterval>1</DaysInterval>", x)
        self.assertIn("2026-09-16T22:00:00", x)
        self.assertIn("<Command>shutdown</Command>", x)
        self.assertIn("/s /t 60 /d p:0:0", x)
        self.assertIn("<Enabled>true</Enabled>", x)
        self.assertIn("EyeTerm", x)
        self.assertIn("S-1-5-18", x)

    def test_workday(self):
        x = pc.build_shutdown_task_xml("workday", "18:30",
                                       now=time.struct_time(
                                           (2026, 9, 16, 10, 0, 0, 2, 0, 0)))
        for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"):
            self.assertIn("<%s/>" % d, x)
        self.assertNotIn("<Saturday/>", x)
        self.assertNotIn("<Sunday/>", x)

    def test_single_date(self):
        x = pc.build_shutdown_task_xml("single", "20:00", date="2026-12-31",
                                       now=time.struct_time(
                                           (2026, 9, 16, 10, 0, 0, 2, 0, 0)))
        self.assertIn("<TimeTrigger>", x)
        self.assertIn("2026-12-31T20:00:00", x)

    def test_verify_variant_disabled(self):
        x = pc.build_shutdown_task_xml("daily", "23:00", enabled=False,
                                       args=pc.PC_SHUTDOWN_ARGS_VERIFY)
        self.assertIn("/s /t 90 /d p:0:0", x)
        self.assertIn("<Enabled>false</Enabled>", x)

    def test_invalid(self):
        with self.assertRaises(ValueError):
            pc.build_shutdown_task_xml("weekly", "22:00")
        with self.assertRaises(ValueError):
            pc.build_shutdown_task_xml("single", "22:00")


class TestSchtasksElevatedOp(unittest.TestCase):

    def test_ok(self):
        with unittest.mock.patch.object(
                pc, "_run_cmd", return_value=(0, "PCST=0\n", "")):
            r = pc._schtasks_elevated_op("schtasks_delete", {})
        self.assertTrue(r["ok"])

    def test_fail(self):
        with unittest.mock.patch.object(
                pc, "_run_cmd", return_value=(1, "PCST=1\n", "err")):
            r = pc._schtasks_elevated_op("schtasks_toggle", {"enable": False})
        self.assertFalse(r["ok"])
        self.assertIn("detail", r)

    def test_unknown(self):
        self.assertFalse(pc._schtasks_elevated_op("nope", {})["ok"])


class TestElevatedWorkerEntry(unittest.TestCase):

    def _run_op(self, op, spec, **patches):
        tmpdir = tempfile.mkdtemp(prefix="pc_ut_")
        opfile = os.path.join(tmpdir, "op.json")
        with open(opfile, "w", encoding="utf-8") as f:
            json.dump(spec, f)
        sentinels = []
        try:
            for target, val in patches.items():
                sentinels.append((target, getattr(pc, target)))
                setattr(pc, target, val)
            rc = pc.elevated_worker_entry([op, opfile])
        finally:
            for target, old in reversed(sentinels):
                setattr(pc, target, old)
        with open(opfile + ".result", "r", encoding="utf-8") as f:
            return rc, json.load(f)

    def test_bios_apply_ok(self):
        rc, res = self._run_op(
            "bios_apply", {"targets": [["A", "B"]]},
            bios_apply_targets=lambda t, runner=None: {"ok": True})
        self.assertEqual(rc, 0)
        self.assertTrue(res["ok"])

    def test_bios_apply_fail(self):
        rc, res = self._run_op(
            "bios_apply", {"targets": [["A", "B"]]},
            bios_apply_targets=lambda t, runner=None: {
                "ok": False, "failed": [{"item": "A"}]})
        self.assertEqual(rc, 1)
        self.assertFalse(res["ok"])

    def test_unknown_op(self):
        rc, res = self._run_op("nope", {})
        self.assertEqual(rc, 1)
        self.assertIn("error", res)

    def test_schtasks_delete(self):
        with unittest.mock.patch.object(
                pc, "_run_cmd", return_value=(0, "PCST=0\n", "")):
            tmpdir = tempfile.mkdtemp(prefix="pc_ut_")
            opfile = os.path.join(tmpdir, "op.json")
            with open(opfile, "w", encoding="utf-8") as f:
                json.dump({}, f)
            rc = pc.elevated_worker_entry(["schtasks_delete", opfile])
            with open(opfile + ".result", "r", encoding="utf-8") as f:
                res = json.load(f)
        self.assertEqual(rc, 0)
        self.assertTrue(res["ok"])


class TestSelfLaunchParams(unittest.TestCase):
    """ShellExecuteW 语义：file 恒为 sys.executable；exe 态（sys.frozen=True）
    params 仅含 flag+op+opfile；python 态 params 需前缀脚本路径。"""

    def test_exe_mode(self):
        with unittest.mock.patch.object(pc.sys, "frozen", True, create=True), \
             unittest.mock.patch.object(pc.sys, "executable",
                                        r"C:\app\winhelper.exe"):
            p = pc._self_launch_params([pc.ELEVATED_FLAG, "bios_apply", "x"])
        self.assertIn(pc.ELEVATED_FLAG, p)
        self.assertIn("bios_apply", p)
        self.assertNotIn("power_control.py", p)

    def test_python_mode(self):
        with unittest.mock.patch.object(pc.sys, "frozen", False, create=True), \
             unittest.mock.patch.object(pc.sys, "executable",
                                        r"C:\Python312\python.exe"):
            p = pc._self_launch_params([pc.ELEVATED_FLAG, "bios_apply", "x"])
        self.assertIn("power_control.py", p)
        self.assertIn(pc.ELEVATED_FLAG, p)


class TestPcTasks(unittest.TestCase):

    def test_create_get_finish(self):
        tid = pc._pc_task_create("bios_apply")
        self.assertTrue(tid.startswith("PCT-"))
        self.assertEqual(pc._pc_task_get(tid)["status"], "running")
        pc._pc_task_finish(tid, {"ok": True})
        t = pc._pc_task_get(tid)
        self.assertEqual(t["status"], "done")
        self.assertEqual(t["result"]["ok"], True)

    def test_get_missing(self):
        self.assertIsNone(pc._pc_task_get("PCT-NONE"))

    def test_spawn_async(self):
        done = []
        tid = pc._pc_spawn_task("t", lambda: done.append(1) or {"ok": True})
        for _ in range(40):
            if pc._pc_task_get(tid)["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(pc._pc_task_get(tid)["status"], "done")


class TestP1Handlers(unittest.TestCase):

    def test_bios_apply_invalid_params(self):
        r = pc.handle_pc_bios_apply(None, {"mode": "xx"})
        self.assertFalse(r["success"])
        r = pc.handle_pc_bios_apply(None, {"mode": "daily", "time": "9:9"})
        self.assertFalse(r["success"])

    def test_bios_apply_ok_async(self):
        with unittest.mock.patch.object(
                pc, "_job_bios_apply",
                lambda cfg: {"ok": True, "summary": "每天 08:00"}):
            r = pc.handle_pc_bios_apply(None, {"mode": "daily",
                                               "time": "08:00"})
        self.assertTrue(r["success"])
        self.assertTrue(r["task_id"].startswith("PCT-"))
        self.assertIn("08:00", r["summary"])
        for _ in range(60):
            t = pc._pc_task_get(r["task_id"])
            if t["status"] != "running":
                break
            time.sleep(0.05)
        self.assertTrue(t["result"]["ok"])

    def test_shutdown_set_invalid(self):
        self.assertFalse(pc.handle_pc_shutdown_set(
            None, {"mode": "weekly", "time": "22:00"})["success"])
        self.assertFalse(pc.handle_pc_shutdown_set(
            None, {"mode": "daily", "time": "bad"})["success"])
        self.assertFalse(pc.handle_pc_shutdown_set(
            None, {"mode": "single", "time": "22:00"})["success"])

    def test_shutdown_set_ok(self):
        with unittest.mock.patch.object(
                pc, "_job_shutdown_set", lambda cfg: {"ok": True}):
            r = pc.handle_pc_shutdown_set(None, {"mode": "workday",
                                                 "time": "18:30"})
        self.assertTrue(r["success"])
        for _ in range(60):
            t = pc._pc_task_get(r["task_id"])
            if t["status"] != "running":
                break
            time.sleep(0.05)
        self.assertTrue(t["result"]["ok"])

    def test_restore_without_backup(self):
        orig = pc.load_bios_backup
        pc.load_bios_backup = lambda: None
        try:
            self.assertFalse(pc.handle_pc_bios_restore(None, {})["success"])
        finally:
            pc.load_bios_backup = orig

    def test_task_status_missing(self):
        self.assertFalse(pc.handle_pc_task_status(
            {"task_id": "PCT-NONE"})["success"])


class TestReportState(unittest.TestCase):
    """4.1.8：页面级上报状态链（_save/_load_report_state 与 snapshot 的
    auto_report/report_state 字段）已随「仅读本地」改造删除——原两用例
    （roundtrip/snapshot_carries_state）同步移除。"""

    def test_snapshot_readonly_no_report_fields(self):
        with unittest.mock.patch.object(
                pc, "collect_snapshot", lambda: {"schema": 1}):
            r = pc.handle_pc_snapshot()
        self.assertTrue(r["success"])
        self.assertNotIn("auto_report", r)
        self.assertNotIn("report_state", r)


class TestHumanSetReport(unittest.TestCase):

    def test_human_set_flag(self):
        captured = {}
        orig_snap = pc.collect_snapshot
        orig_rep = pc.PlatformReporter.report_snapshot
        orig_mark = pc._mark_reported

        def fake_report(self, snapshot):
            captured.update(snapshot)
            return {"ok": True}

        pc.collect_snapshot = lambda: {"schema": 1, "bios": {}}
        pc.PlatformReporter.report_snapshot = fake_report
        pc._mark_reported = lambda: None
        try:
            r = pc.handle_pc_report(None, {"human_set": True})
        finally:
            pc.collect_snapshot = orig_snap
            pc.PlatformReporter.report_snapshot = orig_rep
            pc._mark_reported = orig_mark
        self.assertTrue(r["success"])
        self.assertTrue(captured["bios"]["human_set_flag"])
        self.assertIn("human_set_at", captured["bios"])


class TestBuildBiosTargetsWeekly(unittest.TestCase):
    """协议 weekly+weekdays（7 位周一~周日）通用构造（pc_apply_policy 用）。"""

    def test_all_on(self):
        t, s = pc.build_bios_targets({"mode": "weekly",
                                      "weekdays": [1] * 7, "time": "07:00"})
        d = dict(t)
        self.assertEqual(d["Wake Up on Alarm"], "Weekly Event")
        for day in pc._WD_NAMES:
            self.assertEqual(d[day], "Enabled")
        self.assertIn("周一", s)

    def test_workday_equiv(self):
        a, _ = pc.build_bios_targets({"mode": "workday", "time": "07:00"})
        b, _ = pc.build_bios_targets({"mode": "weekly",
                                      "weekdays": [1, 1, 1, 1, 1, 0, 0],
                                      "time": "07:00"})
        self.assertEqual(a, b)

    def test_invalid(self):
        for cfg in ({"mode": "weekly", "weekdays": [1, 1, 1], "time": "07:00"},
                    {"mode": "weekly", "weekdays": [0] * 7, "time": "07:00"},
                    {"mode": "weekly", "weekdays": [2] * 7, "time": "07:00"}):
            with self.assertRaises(ValueError):
                pc.build_bios_targets(cfg)


class TestPolicyBootToConfig(unittest.TestCase):

    def test_mapping(self):
        self.assertEqual(pc._policy_boot_to_config(
            {"enabled": False, "mode": "daily"}), {"mode": "off"})
        self.assertEqual(pc._policy_boot_to_config(
            {"mode": "disabled"}), {"mode": "off"})
        self.assertEqual(pc._policy_boot_to_config(
            {"mode": "daily", "time": "08:00"}),
            {"mode": "daily", "time": "08:00"})
        self.assertEqual(pc._policy_boot_to_config(
            {"mode": "weekly", "weekdays": [1, 1, 1, 1, 1, 0, 0],
             "time": "07:00"}),
            {"mode": "weekly", "weekdays": [1, 1, 1, 1, 1, 0, 0],
             "time": "07:00"})
        with self.assertRaises(ValueError):
            pc._policy_boot_to_config({"mode": "monthly", "time": "08:00"})


class TestApplyPolicy(unittest.TestCase):

    def _patches(self, capability, bios_ok=True, sd_ok=True,
                 policy_path=None):
        """返回 patch 上下文字典（apply_policy 外部依赖全部 mock）。"""
        return {
            "probe_lenovo_bios": lambda runner=None, timeout=30: {
                "class_found": capability == "enterprise"},
            "collect_machine": lambda runner=None, timeout=30: {"model": "M"},
            "read_rtc_map": lambda runner=None, timeout=40: {
                "Wake Up on Alarm": "Disabled"},
            "save_bios_backup": lambda items, machine=None: {},
            "bios_apply_targets": lambda targets, runner=None, timeout=40: {
                "ok": bios_ok, "attempts": [],
                "failed": [] if bios_ok else [{"item": "x"}],
                "readback": {}},
            "_job_shutdown_set": lambda cfg, wait_timeout=240: {
                "ok": sd_ok, "summary": "每天 22:00"},
            "_job_shutdown_remove": lambda: {
                "ok": sd_ok, "error": None if sd_ok else "stub"},
            "_policy_state_path": lambda: policy_path,
        }

    def _run(self, args, patches):
        olds = {}
        for k, v in patches.items():
            olds[k] = getattr(pc, k)
            setattr(pc, k, v)
        try:
            return pc.apply_policy(args)
        finally:
            for k, v in olds.items():
                setattr(pc, k, v)

    def test_consumer_boot_rejected_shutdown_ok(self):
        tmp = os.path.join(tempfile.mkdtemp(prefix="pc_ut_"), "ps.json")
        r = self._run(
            {"policy_id": "P-abc12345xyz", "op": "apply",
             "boot": {"enabled": True, "mode": "daily", "time": "08:00"},
             "shutdown": {"enabled": True, "mode": "daily", "time": "22:00"}},
            self._patches("not_supported", policy_path=tmp))
        self.assertFalse(r["ok"])
        self.assertEqual(r["capability"], "not_supported")
        self.assertFalse(r["steps"]["bios"]["ok"])
        self.assertIn("capability", r["steps"]["bios"]["error"])
        self.assertTrue(r["steps"]["shutdown_task"]["ok"])
        self.assertEqual(r["steps"]["shutdown_task"]["task_name"],
                         pc.PC_SHUTDOWN_TASK)

    def test_enterprise_full_ok(self):
        saved = {}
        r = self._run(
            {"policy_id": "P-12345678abcd", "op": "apply",
             "boot": {"enabled": True, "mode": "weekly",
                      "weekdays": [1, 1, 1, 1, 1, 0, 0], "time": "07:30"},
             "shutdown": {"enabled": True, "mode": "daily", "time": "22:00"}},
            dict(self._patches("enterprise", policy_path=os.path.join(
                tempfile.mkdtemp(prefix="pc_ut_"), "ps.json")),
                **{"_save_policy_state":
                   lambda pid, s: saved.update({"policy_id": pid,
                                                "summary": s})}))
        self.assertTrue(r["ok"])
        self.assertEqual(r["capability"], "enterprise")
        self.assertTrue(r["steps"]["bios"]["ok"])
        self.assertIn("07:30", r["steps"]["bios"]["summary"])
        self.assertEqual(saved["policy_id"], "P-12345678abcd")
        self.assertIn("定时开机", saved["summary"])
        self.assertIn("定时关机", saved["summary"])

    def test_boot_missing_no_bios_step(self):
        r = self._run(
            {"policy_id": "P-1", "op": "apply",
             "shutdown": {"enabled": False}},
            self._patches("enterprise", policy_path=os.path.join(
                tempfile.mkdtemp(prefix="pc_ut_"), "ps.json")))
        self.assertNotIn("bios", r["steps"])
        self.assertIsNone(r["capability"])
        self.assertTrue(r["steps"]["shutdown_task"]["ok"])

    def test_bios_apply_failed_no_silent(self):
        r = self._run(
            {"policy_id": "P-2", "op": "apply",
             "boot": {"enabled": True, "mode": "daily", "time": "08:00"}},
            self._patches("enterprise", bios_ok=False, policy_path=os.path.join(
                tempfile.mkdtemp(prefix="pc_ut_"), "ps.json")))
        self.assertFalse(r["ok"])
        self.assertIn("apply_failed", r["steps"]["bios"]["error"])


class TestUplinkPcApplyPolicy(unittest.TestCase):

    def test_registered_and_dispatch(self):
        ws_root = os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
        if ws_root not in sys.path:
            sys.path.insert(0, ws_root)
        import uplink as up
        self.assertIn("pc_apply_policy", up.COMMAND_HANDLERS)
        with unittest.mock.patch.object(
                pc, "apply_policy",
                lambda args: {"policy_id": args.get("policy_id"),
                              "op": "apply", "ok": True, "steps": {},
                              "capability": "enterprise"}):
            ok, data = up.COMMAND_HANDLERS["pc_apply_policy"](
                {"policy_id": "P-xyz", "op": "apply"})
        self.assertTrue(ok)
        self.assertEqual(data["policy_id"], "P-xyz")

    def test_exception_isolated(self):
        ws_root = os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
        if ws_root not in sys.path:
            sys.path.insert(0, ws_root)
        import uplink as up

        def boom(args):
            raise RuntimeError("stub boom")

        with unittest.mock.patch.object(pc, "apply_policy", boom):
            ok, data = up.COMMAND_HANDLERS["pc_apply_policy"](
                {"policy_id": "P-err"})
        self.assertFalse(ok)
        self.assertIn("stub boom", data["error"])


class TestCollectDiag(unittest.TestCase):
    """pc_diag 只读诊断采集器（mock 探测命令与缓存）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pc_diag_")
        self._patch = unittest.mock.patch.object(
            pc, "data_dir", lambda: self.tmp)
        self._patch.start()
        pc._LAST_APPLY.update({"data": None, "kind": None, "ts": 0})

    def tearDown(self):
        self._patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_shape_with_mocked_probes(self):
        with unittest.mock.patch.object(
                pc, "_run_cmd",
                lambda cmd, runner=None, timeout=30:
                (0, "PCSC=True", "")), \
             unittest.mock.patch.object(
                 pc, "read_rtc_map",
                 lambda runner=None, timeout=40: {
                     "Wake Up on Alarm": "Daily Event"}):
            d = pc.collect_diag()
        self.assertEqual(d["schema"], 1)
        self.assertTrue(d["save_class"])
        self.assertEqual(d["rtc_readback"].get("Wake Up on Alarm"),
                         "Daily Event")
        self.assertIsNone(d["last_apply"])   # 无 apply 缓存 → 无该键内容
        self.assertIsInstance(d["log_tail"], list)

    def test_last_apply_recorded(self):
        pc._record_apply("bios_apply", {"ok": False, "attempts": [
            {"item": "Alarm Time(HH:MM:SS)", "format": "plain", "rc": 0,
             "rv": 0},
            {"item": "__commit__", "format": "commit", "rv": "committed"}],
            "failed": [{"got": "08:00:00"}]})
        d = pc.collect_diag()
        self.assertEqual(d["last_apply"]["kind"], "bios_apply")
        self.assertEqual(d["last_apply"]["result"]["failed"][0]["got"],
                         "08:00:00")
        commits = [a for a in d["last_apply"]["result"]["attempts"]
                   if a["format"] == "commit"]
        self.assertEqual(commits[0]["rv"], "committed")

    def test_log_tail_parsed_source(self):
        os.makedirs(os.path.join(self.tmp, "logs"), exist_ok=True)
        logf = os.path.join(
            self.tmp, "logs",
            time.strftime("pc_%Y%m%d.log", time.localtime()))
        with open(logf, "w", encoding="utf-8") as f:
            f.write("BIOS 应用失败: 回读校验不符 | attempts="
                    + json.dumps([{"item": "Alarm Time(HH:MM:SS)",
                                   "format": "plain", "rc": 0, "rv": 0},
                                  {"item": "__commit__", "format": "commit",
                                   "rv": "no-save-class"}],
                                  ensure_ascii=False) + "\n")
        d = pc.collect_diag()
        tail = "\n".join(d["log_tail"])
        self.assertIn("__commit__", tail)
        self.assertIn("no-save-class", tail)   # 定案依据：commit note 在日志行内


class TestCenterTasks(unittest.TestCase):
    """4.1.8 页改追加：中心开机任务通道（_center_call + 两 handler）。

    PlatformReporter 以 fake 类注入（不经真实网络）；HTTPError 构造
    携带 {"error": ...} 响应体验证错误透传。"""

    @staticmethod
    def _fake_reporter(behavior):
        class _Fake(object):
            def __init__(self, *a, **k):
                pass

            def _request(self, method, path, body=None, timeout=30):
                return behavior(method, path, body)
        return _Fake

    def test_tasks_ok(self):
        beh = lambda m, p, b: {"tasks": [
            {"name": "A", "kind": "boot", "repeat": "daily",
             "time_hhmm": "08:30", "enabled": 1},
            {"name": "B", "kind": "boot", "repeat": "once",
             "once_date": "2099-01-01", "time_hhmm": "07:00", "enabled": 1},
            "junk"], "quota": {"used": 2, "limit": 5}}
        with unittest.mock.patch.object(
                pc, "PlatformReporter", self._fake_reporter(beh)):
            r = pc.handle_pc_center_tasks()
        self.assertTrue(r["success"])
        self.assertEqual(len(r["tasks"]), 2)
        self.assertEqual(r["quota"]["limit"], 5)

    def test_tasks_not_connected(self):
        def beh(m, p, b):
            raise RuntimeError("not_registered")
        with unittest.mock.patch.object(
                pc, "PlatformReporter", self._fake_reporter(beh)):
            r = pc.handle_pc_center_tasks()
        self.assertFalse(r["success"])
        self.assertEqual(r["error"], "not_connected")

    def test_tasks_http_error_body_extracted(self):
        import io
        import urllib.error
        err = urllib.error.HTTPError(
            "http://stub", 409, "Conflict", {},
            io.BytesIO('{"error": "已达个性化任务数量上限"}'.encode("utf-8")))
        def beh(m, p, b):
            raise err
        with unittest.mock.patch.object(
                pc, "PlatformReporter", self._fake_reporter(beh)):
            r = pc.handle_pc_center_tasks()
        self.assertFalse(r["success"])
        self.assertIn("上限", r["error"])

    def test_create_validations(self):
        cases = [
            ({"repeat": "holiday", "time": "08:00"}, "模式"),
            ({"repeat": "daily"}, "时刻"),
            ({"repeat": "daily", "time": "25:00"}, "范围"),
            ({"repeat": "weekly", "time": "08:00", "weekdays": [0]*7}, "勾选"),
            ({"repeat": "once", "time": "08:00"}, "日期"),
        ]
        for data, kw in cases:
            r = pc.handle_pc_center_task_create(None, data)
            self.assertFalse(r["success"], data)
            self.assertIn(kw, r["error"], data)

    def test_create_payload_normalized(self):
        captured = {}
        def beh(m, p, b):
            captured["method"], captured["path"], captured["body"] = m, p, b
            return {"task": {"id": 9}}
        with unittest.mock.patch.object(
                pc, "PlatformReporter", self._fake_reporter(beh)):
            r = pc.handle_pc_center_task_create(None, {
                "repeat": "weekly", "time": "8:05",
                "weekdays": [1, 1, 1, 1, 1, 0, 0],
                "remark": "已在 BIOS 手工设置"})
        self.assertTrue(r["success"])
        self.assertEqual(captured["method"], "POST")
        self.assertIn("/powercontrol/boot-tasks", captured["path"])
        body = captured["body"]
        self.assertEqual(body["origin"], "client_personal")
        self.assertEqual(body["time_hhmm"], "08:05")
        self.assertEqual(body["weekdays"], "1111100")
        self.assertEqual(body["name"], "个性化开机 08:05")
        self.assertIn("BIOS", body["remark"])

    def test_create_over_limit_passthrough(self):
        import io
        import urllib.error
        err = urllib.error.HTTPError(
            "http://stub", 409, "Conflict", {},
            io.BytesIO('{"error": "已达个性化任务数量上限（5）"}'.encode("utf-8")))
        def beh(m, p, b):
            raise err
        with unittest.mock.patch.object(
                pc, "PlatformReporter", self._fake_reporter(beh)):
            r = pc.handle_pc_center_task_create(
                None, {"repeat": "daily", "time": "08:00"})
        self.assertFalse(r["success"])
        self.assertIn("上限", r["error"])

    def test_delete_validations(self):
        self.assertFalse(pc.handle_pc_center_task_delete({})["success"])
        self.assertFalse(
            pc.handle_pc_center_task_delete({"task_id": "abc"})["success"])

    def test_delete_ok_path_and_error_passthrough(self):
        captured = {}
        def beh(m, p, b):
            captured["method"], captured["path"] = m, p
            return {"ok": True}
        with unittest.mock.patch.object(
                pc, "PlatformReporter", self._fake_reporter(beh)):
            r = pc.handle_pc_center_task_delete({"task_id": "42"})
        self.assertTrue(r["success"])
        self.assertEqual(captured["method"], "DELETE")
        self.assertTrue(captured["path"].endswith("/boot-tasks/42"))
        import io
        import urllib.error
        err = urllib.error.HTTPError(
            "http://stub", 403, "Forbidden", {},
            io.BytesIO('{"error": "仅个性化任务归属人可操作"}'.encode("utf-8")))
        def beh403(m, p, b):
            raise err
        with unittest.mock.patch.object(
                pc, "PlatformReporter", self._fake_reporter(beh403)):
            r = pc.handle_pc_center_task_delete({"task_id": "42"})
        self.assertFalse(r["success"])
        self.assertIn("归属", r["error"])


class TestShutdownConfigReport(unittest.TestCase):
    """4.1.8 终端侧追加：定时关机本地配置存档 + 版本化静默上报。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="pc_ut_sdcfg_")
        self._p = unittest.mock.patch.object(pc, "data_dir",
                                             lambda: self._tmp)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_save_version_increment_and_hash(self):
        a = pc.save_shutdown_config(True, "daily", "22:00:00", "")
        self.assertEqual(a["config_version"], 1)
        self.assertTrue(a["report_pending"])
        b = pc.save_shutdown_config(True, "daily", "22:00:00", "")
        self.assertEqual(b["config_version"], 2)
        self.assertEqual(a["config_hash"], b["config_hash"])  # 语义相同 → 同摘要
        c = pc.save_shutdown_config(False)
        self.assertEqual(c["config_version"], 3)
        self.assertNotEqual(b["config_hash"], c["config_hash"])

    def test_load_missing(self):
        self.assertEqual(pc.load_shutdown_config(), {})

    def test_payload_shape(self):
        pc.save_shutdown_config(True, "workday", "18:30:00", "")
        p = pc._build_sd_config_payload(pc.load_shutdown_config())
        self.assertEqual(p["kind"], "shutdown_config")
        self.assertEqual(p["schema"], 1)
        self.assertEqual(p["mode"], "workday")
        self.assertEqual(p["time"], "18:30:00")
        self.assertEqual(p["task_name"], pc.PC_SHUTDOWN_TASK)
        self.assertIn("config_version", p)
        self.assertIn("config_hash", p)

    def test_report_sync_ok_clears_pending(self):
        pc.save_shutdown_config(True, "daily", "22:00:00", "")
        calls = []
        def fake_center(method, path, body=None, timeout=30):
            calls.append((method, path, body))
            return {"ok": True}
        with unittest.mock.patch.object(pc, "_center_call", fake_center):
            ok, err = pc.report_shutdown_config_sync("change")
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(calls[0][0], "POST")
        self.assertIn("/powercontrol/shutdown-config", calls[0][1])
        self.assertEqual(calls[0][2]["config_version"], 1)
        self.assertFalse(pc.load_shutdown_config()["report_pending"])

    def test_report_sync_fail_keeps_pending(self):
        pc.save_shutdown_config(True, "daily", "22:00:00", "")
        def fake_center(method, path, body=None, timeout=30):
            raise pc.CenterApiError("not_connected")
        with unittest.mock.patch.object(pc, "_center_call", fake_center):
            with self.assertRaises(pc.CenterApiError):
                pc.report_shutdown_config_sync("connected")
        self.assertTrue(pc.load_shutdown_config()["report_pending"])

    def test_job_hooks_persist_and_report(self):
        reasons = []
        with unittest.mock.patch.object(
                pc, "run_elevated",
                lambda op, spec, wait_timeout=240: {"ok": True}), \
             unittest.mock.patch.object(
                 pc, "_report_shutdown_config_async",
                 lambda reason="": reasons.append(reason)):
            r1 = pc._job_shutdown_set({"mode": "daily", "time": "22:00"})
            self.assertTrue(r1["ok"])
            cfg = pc.load_shutdown_config()
            self.assertTrue(cfg["enabled"])
            self.assertEqual(cfg["mode"], "daily")
            self.assertEqual(cfg["time"], "22:00:00")
            r2 = pc._job_shutdown_toggle(False)
            self.assertTrue(r2["ok"])
            cfg = pc.load_shutdown_config()
            self.assertFalse(cfg["enabled"])
            self.assertEqual(cfg["mode"], "daily")  # 启停保留模式字段
            r3 = pc._job_shutdown_remove()
            self.assertTrue(r3["ok"])
            cfg = pc.load_shutdown_config()
            self.assertFalse(cfg["enabled"])
        self.assertEqual(reasons, ["change", "change", "change"])
        self.assertEqual(cfg["config_version"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
