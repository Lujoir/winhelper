# -*- coding: utf-8 -*-
"""desktop_policy 引擎单测（stdlib unittest；mock transport，无破坏）。"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import desktop_policy as dp  # noqa: E402


class FakeTransport(dp.Transport):
    def __init__(self, responses=None, fail_report_times=0,
                 fail_download=False):
        self.responses = list(responses or [])
        self.calls = {"fetch": [], "download": [], "report": []}
        self.fail_report_times = fail_report_times
        self.fail_download = fail_download

    def fetch_policy(self, revision, monitors_brief):
        self.calls["fetch"].append((revision, monitors_brief))
        if self.responses:
            return self.responses.pop(0)
        return {"ok": True, "unchanged": True, "revision": revision}

    def download_wallpaper(self, wallpaper_id, dest_path, checksum):
        self.calls["download"].append(wallpaper_id)
        if self.fail_download:
            raise OSError("network down")
        with open(dest_path, "wb") as f:
            f.write(b"PNGDATA")

    def report(self, payload):
        self.calls["report"].append(payload)
        if self.fail_report_times > 0:
            self.fail_report_times -= 1
            raise OSError("report fail")


def make_engine(tmp, responses=None, **kw):
    dp.set_log_dir(os.path.join(tmp, "logs"))
    t = FakeTransport(responses=responses, **kw)
    eng = dp.Engine(transport=t,
                    store=dp.StateStore(root=os.path.join(tmp, "data")),
                    poll_sec=1)
    return eng, t


class TestPureFunctions(unittest.TestCase):
    def test_cover_src_rect_equal(self):
        self.assertEqual(dp.cover_src_rect(1920, 1080, 1920, 1080),
                         (0, 0, 1920, 1080))

    def test_cover_src_rect_4k_to_1080p(self):
        sx, sy, sw, sh = dp.cover_src_rect(3840, 2160, 1920, 1080)
        self.assertEqual((sx, sy, sw, sh), (0, 0, 3840, 2160))

    def test_cover_src_rect_crop_center(self):
        sx, sy, sw, sh = dp.cover_src_rect(1000, 2000, 1920, 1080)
        self.assertEqual(sw, 1000)
        self.assertAlmostEqual(sh, 1080 * 1000 / 1920.0, delta=1)
        self.assertEqual(sx, 0)
        self.assertGreater(sy, 0)

    def test_monitors_brief(self):
        ms = [{"index": 0, "width": 2560, "height": 1440, "primary": True},
              {"index": 1, "width": 1680, "height": 1050, "primary": False}]
        self.assertEqual(dp.monitors_brief(ms), "0,2560,1440,1;1,1680,1050,0")

    def test_monitors_signature(self):
        self.assertEqual(
            dp.monitors_signature([{"width": 100, "height": 50, "left": 0,
                                    "top": 0, "primary": True}]),
            "100x50@0,0P")


class TestStateStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dp_test_")
        self.store = dp.StateStore(root=os.path.join(self.tmp, "d"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_state_roundtrip(self):
        st = self.store.load_state()
        self.assertEqual(st["revision"], -1)
        st["revision"] = 7
        self.store.save_state(st)
        self.assertEqual(self.store.load_state()["revision"], 7)

    def test_wallpaper_cache_checksum(self):
        p = self.store.wallpaper_path("12")
        with open(p, "wb") as f:
            f.write(b"PNGDATA")
        good = dp._sha256_file(p)
        self.assertEqual(self.store.wallpaper_cached("12", good), p)
        self.assertIsNone(self.store.wallpaper_cached("12", "bad" * 8))
        self.assertIsNone(self.store.wallpaper_cached("999", None))

# PART2_TESTS


class TestTerminalIdResolution(unittest.TestCase):
    """BRG-064：terminal_id 三级解析（配置→进程内 uplink→hostname 兜底）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dp_tid_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cfg_with(self, cfg_obj, inproc, hosttid):
        t = dp.PlatformTransport(
            config_path=os.path.join(self.tmp, "uplink_config.json"))
        if cfg_obj is not None:
            with open(t.cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg_obj, f)
        t._uplink_tid_inproc = lambda: inproc
        t._hostname_tid = lambda: hosttid
        return t._cfg()

    def test_config_tid_priority(self):
        base, token, tid = self._cfg_with(
            {"server_url": "https://x", "token": "tk",
             "terminal_id": "CUSTOM-1"}, "INPROC", "WIN-H")
        self.assertEqual(tid, "CUSTOM-1")

    def test_inproc_fallback(self):
        base, token, tid = self._cfg_with(
            {"server_url": "https://x", "token": "tk", "terminal_id": ""},
            "INPROC-TID", "WIN-H")
        self.assertEqual(tid, "INPROC-TID")

    def test_hostname_fallback(self):
        base, token, tid = self._cfg_with(
            {"server_url": "https://x", "token": "tk", "terminal_id": ""},
            None, "WIN-HOST")
        self.assertEqual(tid, "WIN-HOST")

    def test_missing_config_uses_chain(self):
        base, token, tid = self._cfg_with(None, None, "WIN-HOST")
        self.assertEqual(tid, "WIN-HOST")
        self.assertEqual((base, token), ("", ""))


class TestEnsureAutostart(unittest.TestCase):
    """BRG-065：ensure_autostart 幂等 + uplink 就绪等待 + 超时兜底。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dp_auto_")
        dp.set_log_dir(os.path.join(self.tmp, "logs"))
        dp._AUTOSTART_DONE[0] = False
        self.created = []
        self._orig_get = dp.get_engine

    def tearDown(self):
        dp._AUTOSTART_DONE[0] = False
        dp.get_engine = self._orig_get
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _stub_get_engine(self):
        def _get():
            dp._DP_ENGINE = object()  # 占位，避免真实引擎启动
            self.created.append(1)
            return dp._DP_ENGINE
        dp.get_engine = _get

    def test_idempotent(self):
        self._stub_get_engine()
        self.assertTrue(dp.ensure_autostart(wait_uplink=False))
        self.assertFalse(dp.ensure_autostart(wait_uplink=False))
        deadline = time.time() + 3
        while time.time() < deadline and not self.created:
            time.sleep(0.05)
        self.assertEqual(len(self.created), 1)

    def test_waits_until_ready(self):
        self._stub_get_engine()
        state = {"calls": 0}

        def checker():
            state["calls"] += 1
            return state["calls"] >= 2  # 第二次才就绪

        self.assertTrue(dp._wait_uplink_ready(5, 0.05, checker))
        self.assertGreaterEqual(state["calls"], 2)

    def test_timeout_still_starts(self):
        self._stub_get_engine()
        # checker 永不就绪：超时返回 False（引擎仍照常启动，由调用方启动）
        self.assertFalse(dp._wait_uplink_ready(0.1, 0.05, lambda: False))


class TestEngineTick(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dp_eng_")
        self._old_backoff = dp.BACKOFF_SEC
        dp.BACKOFF_SEC = (0.01, 0.01, 0.01)

    def tearDown(self):
        dp.BACKOFF_SEC = self._old_backoff
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_offline_keeps_state(self):
        eng, t = make_engine(self.tmp)
        def _down(rev, brief):
            raise OSError("down")
        t.fetch_policy = _down
        rev_before = eng.store.load_state()["revision"]
        r = eng.tick()
        self.assertFalse(r["ok"])
        self.assertTrue(r["offline"])
        self.assertEqual(eng.store.load_state()["revision"], rev_before)
        self.assertEqual(t.calls["report"], [])

    def test_unchanged_short_circuit(self):
        eng, t = make_engine(self.tmp)
        r = eng.tick()
        self.assertTrue(r["ok"])
        self.assertTrue(r["unchanged"])
        self.assertEqual(t.calls["report"], [])

    def test_empty_policies_reports(self):
        eng, t = make_engine(self.tmp, responses=[
            {"ok": True, "revision": 5, "policies": {}}])
        r = eng.tick()
        self.assertTrue(r["ok"])
        self.assertEqual(r["revision"], 5)
        self.assertEqual(len(t.calls["report"]), 1)
        payload = t.calls["report"][0]
        self.assertEqual(payload["revision"], 5)
        self.assertIn("monitors", payload)
        self.assertIn("session_type", payload)
        self.assertEqual(eng.store.load_state()["revision"], 5)

    def test_report_retry_then_success(self):
        eng, t = make_engine(self.tmp, responses=[
            {"ok": True, "revision": 3, "policies": {}}],
            fail_report_times=2)
        self.assertTrue(eng.tick()["reported"])
        self.assertEqual(len(t.calls["report"]), 3)

    def test_report_exhausted(self):
        eng, t = make_engine(self.tmp, responses=[
            {"ok": True, "revision": 3, "policies": {}}],
            fail_report_times=99)
        self.assertFalse(eng.tick()["reported"])

    def test_rdp_skips_wallpaper(self):
        eng, t = make_engine(self.tmp, responses=[
            {"ok": True, "revision": 9, "policies": {
                "desktop_wallpaper": {"enabled": True,
                                      "per_monitor": []}}}]
        )
        orig = dp.session_type
        dp.session_type = lambda: "rdp"
        try:
            r = eng.tick()
        finally:
            dp.session_type = orig
        self.assertEqual(r["results"]["desktop_wallpaper"]["error"]["code"],
                         "rdp_skipped")
        self.assertNotIn("lock_screen", r["results"])

    def test_idle_lock_invalid_minutes(self):
        eng, t = make_engine(self.tmp, responses=[
            {"ok": True, "revision": 4, "policies": {
                "idle_lock": {"enabled": True, "minutes": 0}}}]
        )
        res = eng.tick()["results"]["idle_lock"]
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "apply_failed")

    def test_wallpaper_download_fail_maps_file_missing(self):
        eng, t = make_engine(self.tmp, responses=[
            {"ok": True, "revision": 6, "policies": {
                "desktop_wallpaper": {"enabled": True,
                                      "per_monitor": [
                                          {"monitor_index": 0,
                                           "wallpaper_id": "77"}]}}}],
            fail_download=True)
        orig = dp.session_type
        dp.session_type = lambda: "console"
        try:
            r = eng.tick()
        finally:
            dp.session_type = orig
        res = r["results"]["desktop_wallpaper"]
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "file_missing")

    def test_sig_change_with_unchanged_reapplies_cached(self):
        # 服务端 unchanged 但显示器签名变化 → 按缓存策略重适配
        eng, t = make_engine(self.tmp, responses=[
            {"ok": True, "revision": 8, "policies": {
                "idle_lock": {"enabled": True, "minutes": 30}}},
            {"ok": True, "unchanged": True, "revision": 8}])
        r1 = eng.tick()
        self.assertIn("idle_lock", r1["results"])
        eng._sig_last = "CHANGED_SIG"  # 模拟分辨率变化
        r2 = eng.tick()
        self.assertNotIn("unchanged", r2)
        self.assertIn("idle_lock", r2["results"])
        self.assertEqual(eng.store.load_state()["revision"], 8)


class TestPowerSnapshotParsing(unittest.TestCase):
    """快照解析器：喂真实格式样例文本（monkeypatch _run_powercfg）。"""

    SAMPLE = (
        "电源方案 GUID: 6708c478-6596-48af-8fac-f5e2f94a17ae  (Lenovo 默认)\n"
        "  子组 GUID: 7516b95f-f776-4464-8c53-06167f40cc99  (显示)\n"
        "    GUID 别名: SUB_VIDEO\n"
        "    电源设置 GUID: 3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e  "
        "(在此时间后关闭显示)\n"
        "      GUID 别名: VIDEOIDLE\n"
        "      当前交流电源设置索引: 0x00000258\n"
        "      当前直流电源设置索引: 0x0000012c\n"
        "\n"
        "    电源设置 GUID: aded5e82-b909-4619-9949-f5d71dac0bcb  "
        "(显示器亮度)\n"
        "      GUID 别名: VIDEONORMALLEVEL\n"
        "      当前交流电源设置索引: 0x00000037\n"
        "      当前直流电源设置索引: 0x00000037\n")

    def test_parse_sample(self):
        orig = dp._run_powercfg
        dp._run_powercfg = lambda *a: (0, self.SAMPLE, "")
        try:
            snap, aliases = dp.query_snapshot("x")
        finally:
            dp._run_powercfg = orig
        self.assertIn("7516b95f-f776-4464-8c53-06167f40cc99", snap)
        vid = "3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e"
        self.assertEqual(snap["7516b95f-f776-4464-8c53-06167f40cc99"][vid],
                         {"ac": 0x258, "dc": 0x12c})
        self.assertEqual(aliases.get(vid), "VIDEOIDLE")
        self.assertEqual(aliases.get(
            "7516b95f-f776-4464-8c53-06167f40cc99"), "SUB_VIDEO")
        sg, st = dp.find_setting(snap, aliases, "VIDEOIDLE")
        self.assertEqual((sg, st),
                         ("7516b95f-f776-4464-8c53-06167f40cc99", vid))


class TestIdleLockRegistry(unittest.TestCase):
    """屏保注册表写入（HKCU 用户级，写前备份写后还原）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dp_idle_")
        dp.set_log_dir(os.path.join(self.tmp, "logs"))
        import winreg
        self.winreg = winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Control Panel\Desktop", 0,
                             winreg.KEY_READ | winreg.KEY_SET_VALUE)
        self.backup = {}
        for name in ("ScreenSaveActive", "ScreenSaveTimeOut",
                     "ScreenSaverIsSecure"):
            try:
                self.backup[name] = winreg.QueryValueEx(key, name)[0]
            except OSError:
                self.backup[name] = None
        winreg.CloseKey(key)

    def tearDown(self):
        key = self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER,
                                  r"Control Panel\Desktop", 0,
                                  self.winreg.KEY_SET_VALUE)
        for name, val in self.backup.items():
            if val is None:
                try:
                    self.winreg.DeleteValue(key, name)
                except OSError:
                    pass
            else:
                self.winreg.SetValueEx(key, name, 0, self.winreg.REG_SZ, val)
        self.winreg.CloseKey(key)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_apply_and_restore(self):
        r = dp.apply_idle_lock({"minutes": 15, "screen_saver_secure": True})
        self.assertTrue(r["ok"])
        self.assertEqual(r["timeout_sec"], 915)
        key = self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER,
                                  r"Control Panel\Desktop", 0,
                                  self.winreg.KEY_READ)
        got = self.winreg.QueryValueEx(key, "ScreenSaveTimeOut")[0]
        secure = self.winreg.QueryValueEx(key, "ScreenSaverIsSecure")[0]
        self.winreg.CloseKey(key)
        self.assertEqual(got, "915")
        self.assertEqual(secure, "1")


if __name__ == "__main__":
    unittest.main(verbosity=1)

