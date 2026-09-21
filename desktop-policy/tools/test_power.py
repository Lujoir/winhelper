# -*- coding: utf-8 -*-
"""本机电源状态读取/修改单测（BRG-068；mock powercfg 层）。"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import desktop_policy as dp  # noqa: E402

SCHEME = "6708c478-6596-48af-8fac-f5e2f94a17ae"
SUB_VIDEO = "7516b95f-f776-4464-8c53-06167f40cc99"
VIDEOIDLE = "3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e"
SUB_SLEEP = "238c9fa8-0aad-41ed-83f4-97be242c8f20"
STANDBYIDLE = "29f6c1db-86da-48c5-9fdb-f2b67b1f44da"


class _FakeTransport(dp.Transport):
    def fetch_policy(self, revision, brief):
        return {"ok": True, "unchanged": True, "revision": revision}

    def download_wallpaper(self, wid, dest, checksum):
        raise OSError("smoke")

    def report(self, payload):
        return {"success": True}


class PowerHarness(object):
    """mock powercfg 层：快照注入 + 命令记录 + 值记忆（模拟系统真实行为）。"""

    def __init__(self, values):
        self.values = values
        self.calls = []

    def patch(self):
        self._orig = {
            "get_active_scheme": dp.get_active_scheme,
            "query_snapshot": dp.query_snapshot,
            "_run_powercfg": dp._run_powercfg,
            "powercfg_set_index": dp.powercfg_set_index,
            "powercfg_set_active": dp.powercfg_set_active,
        }

        def fake_get_active():
            return SCHEME

        def fake_query(guid):
            snap = {}
            aliases = {}
            amap = {("display_off", "VIDEOIDLE"): VIDEOIDLE,
                    ("sleep", "STANDBYIDLE"): STANDBYIDLE}
            for (key, alias), st in amap.items():
                v = self.values.get(key)
                if v is None:
                    continue
                sg = (SUB_VIDEO if key == "display_off" else SUB_SLEEP)
                snap.setdefault(sg, {})[st] = {"ac": v["ac"], "dc": v["dc"]}
                aliases[st] = alias
            return snap, aliases

        def fake_set_index(guid, sg, st, value, channel):
            self.calls.append(("set_index", sg, st, value, channel))
            for key, stid in (("display_off", VIDEOIDLE),
                              ("sleep", STANDBYIDLE)):
                if st == stid:
                    self.values[key][channel] = value

        def fake_set_active(guid):
            self.calls.append(("set_active", guid))

        dp.get_active_scheme = fake_get_active
        dp.query_snapshot = fake_query
        dp.powercfg_set_index = fake_set_index
        dp.powercfg_set_active = fake_set_active

        def fake_run(*args):
            if args and args[0] == "/getactivescheme":
                return 0, "电源方案 GUID: %s  (测试方案)\r\n" % SCHEME, ""
            return 0, "", ""

        dp._run_powercfg = fake_run

    def unpatch(self):
        for k, v in self._orig.items():
            setattr(dp, k, v)

# PART2


class TestGetCurrentPowerSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dp_pw_")
        dp.set_log_dir(os.path.join(self.tmp, "logs"))
        self.h = PowerHarness({
            "display_off": {"ac": 600, "dc": 300},
            "sleep": {"ac": 0, "dc": 1800},
        })
        self.h.patch()

    def tearDown(self):
        self.h.unpatch()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_shape(self):
        out = dp.get_current_power_settings()
        self.assertEqual(out["scheme"]["name"], "测试方案")
        self.assertEqual(out["display_off"], {"ac_sec": 600, "dc_sec": 300})
        self.assertEqual(out["sleep"], {"ac_sec": 0, "dc_sec": 1800})

    def test_missing_group_minus1(self):
        self.h.values.pop("sleep")
        out = dp.get_current_power_settings()
        self.assertEqual(out["sleep"]["ac_sec"], -1)


class TestSetCurrentPowerSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dp_pws_")
        dp.set_log_dir(os.path.join(self.tmp, "logs"))
        self.h = PowerHarness({
            "display_off": {"ac": 600, "dc": 600},
            "sleep": {"ac": 0, "dc": 0},
        })
        self.h.patch()

    def tearDown(self):
        self.h.unpatch()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_modify_and_readback_ok(self):
        r = dp.set_current_power_settings({
            "display_off_ac": 300, "display_off_dc": 300,
            "sleep_ac": 1800, "sleep_dc": 0})
        self.assertTrue(r["ok"])
        sets = [c for c in self.h.calls if c[0] == "set_index"]
        self.assertEqual(len(sets), 4)
        self.assertIn(("set_active", SCHEME), self.h.calls)
        self.assertEqual(sorted(r["changed"]),
                         ["display_off_ac", "display_off_dc",
                          "sleep_ac", "sleep_dc"])

    def test_readback_mismatch_is_apply_failed(self):
        orig = dp.powercfg_set_index

        def no_effect(guid, sg, st, value, channel):
            self.h.calls.append(("set_index", sg, st, value, channel))
        dp.powercfg_set_index = no_effect
        try:
            r = dp.set_current_power_settings({"display_off_ac": 300})
        finally:
            dp.powercfg_set_index = orig
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["code"], "apply_failed")
        self.assertIn("回读", r["error"]["message"])

    def test_partial_negative_param(self):
        r = dp.set_current_power_settings({"display_off_ac": -5})
        self.assertFalse(r["ok"])
        self.assertIn("不能为负数", r["error"]["message"])


class TestPowerCfgHandlers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dp_hdl_")
        dp.set_log_dir(os.path.join(self.tmp, "logs"))
        self.h = PowerHarness({
            "display_off": {"ac": 600, "dc": 600},
            "sleep": {"ac": 0, "dc": 0},
        })
        self.h.patch()
        dp._DP_ENGINE = dp.Engine(transport=_FakeTransport(), poll_sec=3600)

    def tearDown(self):
        self.h.unpatch()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_read_handler_shape(self):
        r = dp.handle_dp_powercfg_read({})
        self.assertTrue(r["success"])
        self.assertEqual(r["scheme"]["name"], "测试方案")
        self.assertEqual(r["display_off"]["ac_sec"], 600)
        self.assertEqual(r["sleep"]["ac_sec"], 0)

    def test_set_handler_sync_result(self):
        r = dp.handle_dp_powercfg_set({"display_off_ac": 300,
                                       "display_off_dc": 300,
                                       "sleep_ac": 900, "sleep_dc": 0})
        self.assertTrue(r["success"])
        self.assertTrue(r["result"]["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
