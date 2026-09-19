# -*- coding: utf-8 -*-
"""EyeTerm 三大改造客户端侧 · mock 单测（2026-09-17）。

覆盖：安装包文件名解析（协议 EyeTerm_Setup_x64_{ver}_{cfg64}.exe）、cfg64
解码（base64url+zlib）、bootstrap 读取与启用策略（仅当中心未配置时采用、
解析失败零行为变化）、semver 比对、更新状态机、下载（file:// 真链路 +
sha256 校验）、自启键读写往返（HKCU 测试键）。

运行：python tools/test_client_upgrade.py
"""
import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import uuid
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bootstrap as bs  # noqa: E402
import updater as upd  # noqa: E402
import appctl  # noqa: E402


def _make_cfg(server="https://eye.example.cn:18443", token="tok-abc123"):
    return base64.urlsafe_b64encode(
        zlib.compress(json.dumps({"s": server, "t": token}).encode("utf-8"))
    ).decode("ascii").rstrip("=")


class TestExtractCfg64(unittest.TestCase):

    def test_ok(self):
        c = _make_cfg()
        self.assertEqual(bs.extract_cfg64(
            "EyeTerm_Setup_x64_4.1.0_%s.exe" % c), c)

    def test_case_insensitive_and_cfg64_with_underscore(self):
        c = _make_cfg()
        c2 = c[:-2] + "_x"          # base64url 可能含下划线（尾段）
        self.assertEqual(bs.extract_cfg64(
            "eyeterm_setup_x64_4.1.0_%s.EXE" % c2), c2)

    def test_invalid(self):
        for name in ("EyeTerm_Setup_x64_4.1.0.exe", "setup.exe",
                     "Other_Setup_x64_4.1.0_abc.exe", "",
                     "EyeTerm_Setup_x64_4.1.0.exe.txt"):
            self.assertIsNone(bs.extract_cfg64(name))


class TestDecodeCfg(unittest.TestCase):

    def test_ok(self):
        d = bs.decode_cfg(_make_cfg())
        self.assertTrue(d["server"].startswith("https://"))
        self.assertEqual(d["token"], "tok-abc123")

    def test_invalid(self):
        for bad in ("", "not-base64!!", "AAAA",   # AAAA 解 zlib 大概率失败
                    bs.decode_cfg.__name__):
            with self.assertRaises(ValueError):
                bs.decode_cfg(bad if bad != "AAAA" else "AAAA")
        # 缺 token
        payload = base64.urlsafe_b64encode(
            zlib.compress(json.dumps({"s": "https://x"}).encode())).decode()
        with self.assertRaises(ValueError):
            bs.decode_cfg(payload)
        # server 非 url
        payload = base64.urlsafe_b64encode(
            zlib.compress(json.dumps({"s": "ftp://x", "t": "k"}).encode())).decode()
        with self.assertRaises(ValueError):
            bs.decode_cfg(payload)


class TestReadBootstrap(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bs_ut_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cfg64_form(self):
        p = os.path.join(self.tmp, "config_bootstrap.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"cfg64": _make_cfg()}, f)
        d = bs.read_bootstrap(self.tmp)
        self.assertTrue(d["server"].startswith("https://"))

    def test_plain_form(self):
        p = os.path.join(self.tmp, "config_bootstrap.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"server": "http://x:1", "token": "k"}, f)
        self.assertEqual(bs.read_bootstrap(self.tmp)["token"], "k")

    def test_invalid_or_missing(self):
        p = os.path.join(self.tmp, "config_bootstrap.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("not json{")
        self.assertIsNone(bs.read_bootstrap(self.tmp))
        self.assertIsNone(bs.read_bootstrap(tempfile.mkdtemp(prefix="none_")))


class TestApplyOnStartup(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bs_ut_")
        self.cfg_dir = tempfile.mkdtemp(prefix="bs_cfg_")
        self._env = os.environ.get("UPLINK_CONFIG_DIR")
        os.environ["UPLINK_CONFIG_DIR"] = self.cfg_dir
        self._appdir = getattr(bs, "app_dir", None)
        self._patch = unittest.mock.patch.object(bs, "app_dir",
                                                 lambda: self.tmp)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        if self._env is None:
            os.environ.pop("UPLINK_CONFIG_DIR", None)
        else:
            os.environ["UPLINK_CONFIG_DIR"] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.cfg_dir, ignore_errors=True)

    def _write_bootstrap(self, payload):
        with open(os.path.join(self.tmp, "config_bootstrap.json"),
                  "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def test_adopt_when_no_config(self):
        self._write_bootstrap({"cfg64": _make_cfg()})
        self.assertTrue(bs.apply_on_startup())
        import uplink
        cfg = uplink.load_config()
        self.assertTrue(cfg["enabled"])
        self.assertTrue(cfg["server_url"].startswith("https://"))
        self.assertEqual(cfg["token"], "tok-abc123")

    def test_no_overwrite_existing(self):
        import uplink
        cfg = uplink.load_config()
        cfg["server_url"] = "https://already.configured"
        cfg["token"] = "keep"
        cfg["enabled"] = True
        uplink.save_config(cfg)
        self._write_bootstrap({"cfg64": _make_cfg()})
        self.assertFalse(bs.apply_on_startup())
        self.assertEqual(uplink.load_config()["server_url"],
                         "https://already.configured")

    def test_silent_on_garbage(self):
        self._write_bootstrap({"cfg64": "%%%garbage%%%"})
        self.assertFalse(bs.apply_on_startup())
        self.assertFalse(bs.apply_on_startup())   # 文件不清理也持续安全


class TestSemver(unittest.TestCase):

    def test_is_newer(self):
        self.assertTrue(upd.is_newer("4.1.0", "4.0.9"))
        self.assertTrue(upd.is_newer("4.1", "4.0.9"))
        self.assertTrue(upd.is_newer("5.0.0", "4.9.9"))
        self.assertFalse(upd.is_newer("4.0.0", "4.1.0"))
        self.assertFalse(upd.is_newer("4.1.0", "4.1.0"))
        self.assertFalse(upd.is_newer("4.1.0-rc1", "4.1.0"))   # 修饰后缀按 0 剥除
        self.assertFalse(upd.is_newer("garbage", "4.0.0"))
        self.assertFalse(upd.is_newer("4.0.0", ""))


class TestUpdaterState(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="upd_ut_")
        self._patch = unittest.mock.patch.object(upd, "update_dir",
                                                 lambda: self.tmp)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_state_roundtrip(self):
        st = upd._save_state(status="ready", version="4.1.0", file="x.exe")
        self.assertEqual(st["status"], "ready")
        self.assertEqual(upd.load_state()["version"], "4.1.0")

    def test_check_and_fetch_no_manifest(self):
        with unittest.mock.patch.object(upd, "fetch_manifest",
                                        lambda *a, **k: None):
            self.assertEqual(upd.check_and_fetch("4.0.0", "http://x", "t")
                             ["status"], "idle")

    def test_check_and_fetch_not_newer(self):
        with unittest.mock.patch.object(
                upd, "fetch_manifest",
                lambda *a, **k: {"latest_version": "4.0.0",
                                 "download_url": "http://x/pkg.exe"}):
            self.assertEqual(upd.check_and_fetch("4.1.0", "http://x", "t")
                             ["status"], "idle")

    def test_check_and_fetch_ready(self):
        dest = os.path.join(self.tmp, "EyeTerm_Setup_x64_4.1.0.exe")
        with open(dest, "wb") as f:
            f.write(b"installer-bytes")
        import hashlib
        sha = hashlib.sha256(b"installer-bytes").hexdigest()
        with unittest.mock.patch.object(
                upd, "fetch_manifest",
                lambda *a, **k: {"latest_version": "4.1.0",
                                 "download_url": "http://stub/pkg.exe",
                                 "sha256": sha}), \
             unittest.mock.patch.object(
                 upd, "download",
                 lambda url, d, sha256="", progress=None: d):
            st = upd.check_and_fetch("4.0.0", "http://x", "t")
        self.assertEqual(st["status"], "ready")
        self.assertEqual(st["version"], "4.1.0")


class _FakeResp(object):
    """urlopen 桩（fetch_manifest 用 with 上下文 + read）。"""

    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._b


def _urlopen_ok(payload):
    def fake(req, timeout=None):
        return _FakeResp(payload)
    return fake


class TestFetchManifest(unittest.TestCase):
    """TBC-002 定稿：/api/v1/client/update-manifest 扁平形状（ADR-042）。"""

    def test_flat_ok_relative_url(self):
        payload = {"ok": True, "latest_version": "4.1.0",
                   "download_url": "/download/client/setup",
                   "sha256": "a" * 64, "size": 123}
        with unittest.mock.patch.object(upd.urllib.request, "urlopen",
                                        _urlopen_ok(payload)):
            m = upd.fetch_manifest("http://srv:18090", "tok", "4.0.0")
        self.assertEqual(m["latest_version"], "4.1.0")
        self.assertEqual(m["download_url"],
                         "http://srv:18090/download/client/setup")
        self.assertEqual(m["sha256"], "a" * 64)

    def test_absolute_url_kept(self):
        payload = {"latest_version": "4.2.0",
                   "download_url": "http://cdn/pkg.exe"}
        with unittest.mock.patch.object(upd.urllib.request, "urlopen",
                                        _urlopen_ok(payload)):
            m = upd.fetch_manifest("http://srv", "t", "4.1.0")
        self.assertEqual(m["download_url"], "http://cdn/pkg.exe")

    def test_null_current_no_update(self):
        payload = {"ok": True, "latest_version": None, "download_url": None,
                   "sha256": None}
        with unittest.mock.patch.object(upd.urllib.request, "urlopen",
                                        _urlopen_ok(payload)):
            self.assertIsNone(upd.fetch_manifest("http://srv", "tok",
                                                 "4.1.0"))

    def test_401_token_error(self):
        import urllib.error

        def fake(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized",
                                         None, None)
        with unittest.mock.patch.object(upd.urllib.request, "urlopen", fake):
            self.assertIsNone(upd.fetch_manifest("http://srv", "bad",
                                                 "4.0.0"))


class TestDownload(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dl_ut_")
        payload = b"pkg-content-123"
        self.src = os.path.join(self.tmp, "src.bin")
        with open(self.src, "wb") as f:
            f.write(payload)
        self.sha = hashlib.sha256(payload).hexdigest()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _file_url(self):
        return "file:///" + self.src.replace("\\", "/")

    def test_ok_with_sha(self):
        dest = os.path.join(self.tmp, "out.exe")
        upd.download(self._file_url(), dest, sha256=self.sha)
        self.assertTrue(os.path.exists(dest))
        self.assertFalse(os.path.exists(dest + ".part"))

    def test_sha_mismatch_retries(self):
        dest = os.path.join(self.tmp, "out2.exe")
        with self.assertRaises(RuntimeError):
            upd.download(self._file_url(), dest, sha256="deadbeef")
        self.assertFalse(os.path.exists(dest))

    def test_unreachable(self):
        dest = os.path.join(self.tmp, "out3.exe")
        with self.assertRaises(RuntimeError):
            upd.download("http://127.0.0.1:1/nope", dest)


class TestPidAlive(unittest.TestCase):

    def test_current_alive(self):
        self.assertTrue(upd._pid_alive(os.getpid()))

    def test_dead(self):
        # pid 0/负数/超大：按不存在处理（权限不足也按退出处理，updater 语义安全）
        self.assertFalse(upd._pid_alive(-1))


class TestAutostartKey(unittest.TestCase):

    def setUp(self):
        self._orig_key = appctl._RUN_KEY
        self._orig_name = appctl._VALUE_NAME
        appctl._RUN_KEY = r"Software\EyeTermSelfTest\Run"
        appctl._VALUE_NAME = "EyeTerm"

    def tearDown(self):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\EyeTermSelfTest", 0,
                                winreg.KEY_SET_VALUE) as k:
                winreg.DeleteKey(k, "Run")
            import winreg as w2
            with w2.OpenKey(w2.HKEY_CURRENT_USER, "Software", 0,
                            w2.KEY_SET_VALUE) as k:
                w2.DeleteKey(k, "EyeTermSelfTest")
        except OSError:
            pass
        appctl._RUN_KEY = self._orig_key
        appctl._VALUE_NAME = self._orig_name

    def test_roundtrip(self):
        self.assertFalse(appctl.get_autostart()["enabled"])
        r = appctl.set_autostart(True)
        self.assertTrue(r["success"])
        self.assertTrue(appctl.get_autostart()["enabled"])
        r = appctl.set_autostart(False)
        self.assertTrue(r["success"])
        self.assertFalse(appctl.get_autostart()["enabled"])
        # 再删一次（不存在）不报错
        self.assertTrue(appctl.set_autostart(False)["success"])


class TestUplinkDefectB(unittest.TestCase):
    """4.1.3 缺陷 B/F：401 复位 registered + 状态迁移日志。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="upl_ut_")
        self._env = os.environ.get("UPLINK_CONFIG_DIR")
        os.environ["UPLINK_CONFIG_DIR"] = self.tmp
        ws = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if ws not in sys.path:
            sys.path.insert(0, ws)
        import uplink as up
        self.up = up

    def tearDown(self):
        if self._env is None:
            os.environ.pop("UPLINK_CONFIG_DIR", None)
        else:
            os.environ["UPLINK_CONFIG_DIR"] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_401_resets_registered(self):
        up = self.up
        with up._lock:
            up._state["registered"] = True
            up._state["state"] = "connected"
        with unittest.mock.patch.object(
                up, "_post", lambda path, payload, timeout=15:
                (401, {"error": "token invalid"})):
            ok, _ = up._heartbeat_once()
        self.assertFalse(ok)
        with up._lock:
            self.assertFalse(up._state["registered"])
        # 恢复干净状态（避免污染其它用例）
        with up._lock:
            up._state["registered"] = False
            up._state["state"] = "disabled"

    def test_404_resets_registered(self):
        up = self.up
        with up._lock:
            up._state["registered"] = True
        with unittest.mock.patch.object(
                up, "_post", lambda path, payload, timeout=15:
                (404, {"error": "no terminal"})):
            ok, _ = up._heartbeat_once()
        self.assertFalse(ok)
        with up._lock:
            self.assertFalse(up._state["registered"])
        with up._lock:
            up._state["registered"] = False

    def test_state_transition_logged(self):
        up = self.up
        up._set_state(state="connecting")
        up._set_state(state="connected")
        logf = os.path.join(self.tmp, "power-control", "logs")
        logs = [f for f in os.listdir(logf) if f.startswith("ul_")]
        self.assertTrue(logs)
        content = open(os.path.join(logf, logs[0]), encoding="utf-8").read()
        self.assertIn("state: connecting -> connected", content)
        # 噪声键（last_hb_ts）不入日志
        self.assertNotIn("last_hb_ts", content)


class TestPcDiagUplinkFields(unittest.TestCase):

    def test_uplink_fields_merged(self):
        ws = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if ws not in sys.path:
            sys.path.insert(0, ws)
        import uplink as up
        import power_control as pc
        with unittest.mock.patch.object(
                pc, "collect_diag", lambda: {"schema": 1, "log_tail": []}), \
             unittest.mock.patch.object(
                 up, "load_config",
                 lambda: {"heartbeat_interval": 60}):
            with up._lock:
                up._state["state"] = "connected"
                up._state["registered"] = True
            ok, data = up.COMMAND_HANDLERS["pc_diag"]({})
        self.assertTrue(ok)
        # 版本号动态跟随 CLIENT_VERSION（此前硬编码 4.1.7，每次升版都要改测试）
        self.assertEqual(data["client_version"], up.CLIENT_VERSION)
        self.assertEqual(data["uplink"]["state"], "connected")
        self.assertTrue(data["uplink"]["registered"])
        self.assertEqual(data["uplink"]["heartbeat_interval"], 60)


class TestUplinkStatusVersion(unittest.TestCase):
    """main 需求：navbar 版本号数据源——handle_uplink_status 必须带 version 字段。"""

    def test_status_contains_version(self):
        ws = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if ws not in sys.path:
            sys.path.insert(0, ws)
        import uplink as up
        with unittest.mock.patch.object(
                up, "load_config", lambda: {"heartbeat_interval": 60}):
            data = up.handle_uplink_status({})
        self.assertTrue(data["success"])
        ul = data["uplink"]
        self.assertEqual(ul["version"], up.CLIENT_VERSION)
        # 兼容旧消费方字段仍在
        self.assertEqual(ul["client_version"], up.CLIENT_VERSION)


class TestEffectiveHeartbeatInterval(unittest.TestCase):
    """4.1.4 观察修正：自报=实际生效口径（服务端覆盖 > 本地配置 > 默认）。"""

    def _import(self):
        ws = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if ws not in sys.path:
            sys.path.insert(0, ws)
        import uplink as up
        return up

    def test_status_reports_effective_interval(self):
        up = self._import()
        with unittest.mock.patch.object(
                up, "load_config",
                lambda: {"heartbeat_interval": 30}):
            with up._lock:
                up._state["effective_interval"] = 60
            data = up.handle_uplink_status({})
            eff = up._effective_heartbeat_interval()
        self.assertEqual(eff, 60)   # 服务端覆盖值优先
        self.assertEqual(data["uplink"]["heartbeat_interval"], 60)

    def test_fallback_to_config_when_no_override(self):
        up = self._import()
        with unittest.mock.patch.object(
                up, "load_config",
                lambda: {"heartbeat_interval": 30}):
            with up._lock:
                up._state["effective_interval"] = None
            self.assertEqual(up._effective_heartbeat_interval(), 30)

    def test_loop_writes_effective_interval_on_override(self):
        """心跳循环：服务端 interval 覆盖时同步写 effective_interval 状态。"""
        up = self._import()
        src = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "uplink.py"),
            "r", encoding="utf-8").read()
        self.assertIn("delay = srv_interval\n"
                      "                    _set_state("
                      "effective_interval=srv_interval)", src)


class TestPolicyApplyLastApply(unittest.TestCase):
    """4.1.4 观察修正：中心下发路径收尾同步刷新 last_apply 缓存。"""

    def test_apply_policy_records_last_apply(self):
        ws = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if ws not in sys.path:
            sys.path.insert(0, ws)
        import power_control as pc
        recorded = []
        with unittest.mock.patch.object(
                pc, "_record_apply",
                lambda kind, res: recorded.append((kind, res))):
            pc.apply_policy({})
        self.assertEqual(len(recorded), 1)
        kind, res = recorded[0]
        self.assertEqual(kind, "policy_apply")
        self.assertTrue(res["ok"])
        self.assertEqual(res["steps"], {})


class TestPowerAction(unittest.TestCase):
    """UPL power_action / power_action_abort（中心发起重启/关机，2026-09-18）。"""

    def _pa(self):
        ws = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if ws not in sys.path:
            sys.path.insert(0, ws)
        import power_action as pa
        return pa

    def test_argv_shutdown_force(self):
        pa = self._pa()
        self.assertEqual(pa.build_power_action_argv("shutdown", 60, True),
                         ["shutdown", "/s", "/t", "60", "/f"])

    def test_argv_restart_no_force(self):
        pa = self._pa()
        self.assertEqual(pa.build_power_action_argv("restart", 300, False),
                         ["shutdown", "/r", "/t", "300"])

    def test_delay_boundary(self):
        pa = self._pa()
        self.assertEqual(
            pa.build_power_action_argv("shutdown", 0, True)[3], "0")
        self.assertEqual(
            pa.build_power_action_argv("shutdown", 3600, True)[3], "3600")
        for bad in (-1, 3601, "abc", "10.5", None):
            with self.assertRaises((ValueError, TypeError)):
                pa.build_power_action_argv("shutdown", bad, True)

    def test_action_whitelist(self):
        pa = self._pa()
        for bad in ("logoff", "sleep", "", "SHUTDOWN", None):
            with self.assertRaises((ValueError, TypeError)):
                pa.build_power_action_argv(bad, 60, True)

    def test_handle_rejects_invalid(self):
        pa = self._pa()
        calls = []
        ok, data = pa.handle_power_action(
            {"action": "shutdown", "delay_sec": 9999},
            runner=lambda argv, timeout=20:
                calls.append(argv) or (0, "", ""))
        self.assertFalse(ok)
        self.assertIn("参数校验失败", data["error"])
        self.assertEqual(calls, [])          # 校验拒绝 → 未执行任何命令

    def test_handle_executes_and_notifies(self):
        pa = self._pa()
        calls, notified = [], []
        ok, data = pa.handle_power_action(
            {"action": "restart", "delay_sec": 30, "force": False},
            runner=lambda argv, timeout=20:
                calls.append(argv) or (0, "", ""),
            notify=lambda a, d, f: notified.append((a, d, f)))
        self.assertTrue(ok)
        self.assertEqual(calls, [["shutdown", "/r", "/t", "30"]])
        self.assertEqual(notified, [("restart", 30, False)])
        self.assertEqual(data["note"], "30 秒后重启")

    def test_handle_exec_fail_as_reported(self):
        pa = self._pa()
        ok, data = pa.handle_power_action(
            {"action": "shutdown", "delay_sec": 60},
            runner=lambda argv, timeout=20: (5, "", "boom"))
        self.assertFalse(ok)
        self.assertEqual(data["rc"], 5)
        self.assertIn("boom", data["error"])

    def test_abort_success(self):
        pa = self._pa()
        calls = []
        ok, data = pa.handle_power_action_abort(
            {}, runner=lambda argv, timeout=20:
                calls.append(argv) or (0, "", ""))
        self.assertTrue(ok)
        self.assertEqual(calls, [["shutdown", "/a"]])
        self.assertIn("撤销", data["note"])

    def test_abort_no_pending(self):
        pa = self._pa()
        ok, data = pa.handle_power_action_abort(
            {}, runner=lambda argv, timeout=20: (1116, "", ""))
        self.assertFalse(ok)
        self.assertEqual(data["rc"], 1116)
        self.assertIn("没有待执行", data["error"])

    def test_no_local_ui_entry(self):
        """本地 UI 无任何立即关机/重启入口（管控语义：仅中心可发）。"""
        ws = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for js in (os.path.join(ws, "power-control", "web",
                                "powercontrol.js"),
                   os.path.join(ws, "web", "powercontrol.js")):
            src = open(js, encoding="utf-8").read()
            self.assertNotIn("power_action", src)
            self.assertNotIn("立即关机", src)
            self.assertNotIn("立即重启", src)
        html = open(os.path.join(ws, "power-control", "web",
                                 "powercontrol-standalone.html"),
                    encoding="utf-8").read()
        self.assertNotIn("立即关机", html)
        self.assertNotIn("立即重启", html)

    def test_registered_in_uplink(self):
        ws = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if ws not in sys.path:
            sys.path.insert(0, ws)
        import uplink as up
        self.assertIn("power_action", up.COMMAND_HANDLERS)
        self.assertIn("power_action_abort", up.COMMAND_HANDLERS)


class TestWolRelay(unittest.TestCase):
    """UPL wol_relay（WoL 跨网段中继·终端侧，2026-09-18）。"""

    def _pa(self):
        ws = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if ws not in sys.path:
            sys.path.insert(0, ws)
        import power_action as pa
        return pa

    def test_normalize_mac(self):
        pa = self._pa()
        for raw in ("AA:BB:CC:DD:EE:FF", "aa-bb-cc-dd-ee-ff",
                    "aabbccddeeff", "AA BB CC DD EE FF"):
            self.assertEqual(pa.normalize_mac(raw), "AABBCCDDEEFF")
        for bad in ("AABBCCDDEEF", "AABBCCDDEEFG", "", None, "AA:BB:CC"):
            with self.assertRaises(ValueError):
                pa.normalize_mac(bad)

    def test_build_magic_packet(self):
        pa = self._pa()
        pkt = pa.build_magic_packet("AA:BB:CC:DD:EE:FF")
        self.assertEqual(len(pkt), 102)
        self.assertEqual(pkt[:6], b"\xff" * 6)
        self.assertEqual(pkt[6:12], b"\xaa\xbb\xcc\xdd\xee\xff")
        self.assertEqual(pkt[96:102], b"\xaa\xbb\xcc\xdd\xee\xff")

    def test_validate_broadcast(self):
        pa = self._pa()
        for good in ("192.168.1.255", "10.0.0.255", "255.255.255.255"):
            self.assertEqual(pa.validate_broadcast(good), good)
        for bad in ("1.1.1", "256.1.1.1", "abc", None, "192.168.1",
                    "192.168.1.0.1"):
            with self.assertRaises(ValueError):
                pa.validate_broadcast(bad)

    def test_handle_send_and_receipt(self):
        pa = self._pa()
        sent = []
        ok, data = pa.handle_wol_relay(
            {"mac": "AA:BB:CC:DD:EE:FF", "broadcast": "192.168.1.255"},
            sender=lambda pkt, b, p: sent.append((pkt, b, p)))
        self.assertTrue(ok)
        pkt, b, p = sent[0]
        self.assertEqual(len(pkt), 102)
        self.assertEqual(b, "192.168.1.255")
        self.assertEqual(p, 9)          # 缺省 WoL 端口
        self.assertIn("已发送", data["note"])

    def test_handle_custom_port(self):
        pa = self._pa()
        sent = []
        pa.handle_wol_relay(
            {"mac": "aabbccddeeff", "broadcast": "10.0.0.255", "port": 40000},
            sender=lambda pkt, b, p: sent.append((pkt, b, p)))
        self.assertEqual(sent[0][2], 40000)

    def test_handle_rejects_invalid_no_send(self):
        pa = self._pa()
        sent = []
        for bad_args in ({"mac": "XX", "broadcast": "192.168.1.255"},
                         {"mac": "AA:BB:CC:DD:EE:FF", "broadcast": "1.1.1"},
                         {"mac": "AA:BB:CC:DD:EE:FF",
                          "broadcast": "192.168.1.255", "port": 70000},
                         {"broadcast": "192.168.1.255"},
                         {}):
            ok, data = pa.handle_wol_relay(
                bad_args, sender=lambda pkt, b, p: sent.append(1))
            self.assertFalse(ok)
            self.assertIn("参数校验失败", data["error"])
        self.assertEqual(sent, [])      # 校验拒绝 → 零发送

    def test_handle_send_fail_as_reported(self):
        pa = self._pa()
        ok, data = pa.handle_wol_relay(
            {"mac": "AA:BB:CC:DD:EE:FF", "broadcast": "192.168.1.255"},
            sender=lambda pkt, b, p: (_ for _ in ()).throw(OSError("net down")))
        self.assertFalse(ok)
        self.assertIn("net down", data["error"])

    def test_registered_in_uplink(self):
        ws = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if ws not in sys.path:
            sys.path.insert(0, ws)
        import uplink as up
        self.assertIn("wol_relay", up.COMMAND_HANDLERS)


class TestSingleInstance(unittest.TestCase):
    """4.1.6 单实例约束：防重复（命名互斥锁）+ 可接管（--replace）。"""

    def _si(self):
        ws = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if ws not in sys.path:
            sys.path.insert(0, ws)
        import single_instance as si
        return si

    def setUp(self):
        si = self._si()
        # 活实例隔离：本机 winhelper 4.1.6+ 常驻持有正式锁名——单测换用
        # 独立测试名，避免与运行中实例互斥冲突
        si._use_names("Global\\EyeTerm.Test.Mutex.%s" % uuid.uuid4().hex,
                      "Global\\EyeTerm.Test.Event.%s" % uuid.uuid4().hex)
        # 防御：清理上一用例异常中断遗留的锁句柄（模块级容器跨用例共享）
        if si._handle["mutex"]:
            si._KERNEL32.CloseHandle(si._handle["mutex"])
            si._handle["mutex"] = None

    def tearDown(self):
        si = self._si()
        if si._handle["mutex"]:
            si._KERNEL32.CloseHandle(si._handle["mutex"])
            si._handle["mutex"] = None
        si._use_names()

    def test_mutex_double_acquire_same_process(self):
        """同进程复刻双实例：首次拿锁成功、二次 ALREADY_EXISTS 失败。"""
        si = self._si()
        first = si.try_acquire()
        self.assertTrue(first)
        second = si.try_acquire()
        self.assertFalse(second)   # 锁被本进程持有 → 再入失败

    def test_enter_no_replace_when_held(self):
        """防重复：锁被持有 → enter(replace=False) 返回 False 且置前被调。"""
        si = self._si()
        self.assertTrue(si.try_acquire())
        focused = []
        with unittest.mock.patch.object(si, "focus_existing",
                                        lambda: focused.append(1)):
            ok = si.enter(replace=False)
        self.assertFalse(ok)
        self.assertEqual(focused, [1])   # 现有窗口置前被触发

    def test_enter_replace_takes_over(self):
        """可接管：新实例请求替换 → 持锁旧实例释放 → 新实例轮询拿到锁。"""
        si = self._si()
        # 模拟旧实例：持锁 + 监听替换请求（收到即释放锁优雅退出）
        self.assertTrue(si.try_acquire())
        release = threading.Event()
        replaced = []

        def watch():
            h = si._create_or_open_replace_event()
            si._KERNEL32.WaitForSingleObject(h, 5000)
            si._KERNEL32.CloseHandle(h)
            replaced.append(1)
            si._KERNEL32.CloseHandle(si._handle["mutex"])
            si._handle["mutex"] = None
            release.set()

        t = threading.Thread(target=watch, daemon=True)
        t.start()
        time.sleep(0.1)
        try:
            ok = si.enter(replace=True, wait_timeout=10, poll_interval=0.2,
                          _sleep=lambda s: time.sleep(min(s, 0.2)))
        finally:
            t.join(timeout=6)
        self.assertTrue(ok)                    # 新实例接管成功
        self.assertEqual(replaced, [1])        # 旧实例收到替换请求
        self.assertTrue(release.is_set())      # 旧实例已优雅退出

    def test_enter_replace_timeout_no_overlap(self):
        """接管超时：旧实例拒不退出 → replace 如实失败，不叠开。"""
        si = self._si()
        self.assertTrue(si.try_acquire())      # 模拟顽固旧实例（无人释放）
        ok = si.enter(replace=True, wait_timeout=0.5, poll_interval=0.1)
        self.assertFalse(ok)                   # 超时失败，绝不叠开

    def test_enter_replace_idempotent_when_free(self):
        """无旧实例：--replace 幂等，直接拿锁正常启动。"""
        si = self._si()
        ok = si.enter(replace=True, wait_timeout=2, poll_interval=0.1)
        self.assertTrue(ok)

    def test_enter_autostart_no_focus(self):
        """4.1.7：--autostart 撞已运行实例 → 静默退出，不置前不抢焦点。"""
        si = self._si()
        self.assertTrue(si.try_acquire())
        focused = []
        with unittest.mock.patch.object(si, "focus_existing",
                                        lambda: focused.append(1)):
            ok = si.enter(replace=False, focus_on_exists=False)
        self.assertFalse(ok)
        self.assertEqual(focused, [])   # 置前零触发


class TestAutostartSilent(unittest.TestCase):
    """4.1.7 开机自启静默化：旗标判定/Run 键幂等重写/集成断言。"""

    def _dt(self):
        ws = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if ws not in sys.path:
            sys.path.insert(0, ws)
        import desktop as dt
        return dt

    def test_is_autostart_launch(self):
        dt = self._dt()
        self.assertTrue(dt.is_autostart_launch(
            ["winhelper.exe", "--autostart"]))
        self.assertFalse(dt.is_autostart_launch(
            ["winhelper.exe"]))
        self.assertFalse(dt.is_autostart_launch(
            ["winhelper.exe", "--replace"]))   # replace≠autostart

    def _fake_winreg(self, existing):
        import types
        fake = types.SimpleNamespace(
            HKEY_CURRENT_USER=0, KEY_READ=0x20019, KEY_WRITE=0x20006,
            REG_SZ=1)
        key = unittest.mock.MagicMock()
        fake.OpenKey = unittest.mock.MagicMock(return_value=key)
        key.__enter__.return_value = key
        # QueryValueEx/SetValueEx 是 winreg 模块级函数（desktop.py 以
        # winreg.XXX 调用），必须挂 fake 命名空间而非 key 实例
        if existing is None:
            fake.QueryValueEx = unittest.mock.MagicMock(
                side_effect=FileNotFoundError)
        else:
            fake.QueryValueEx = unittest.mock.MagicMock(
                return_value=(existing, fake.REG_SZ))
        fake.SetValueEx = unittest.mock.MagicMock()
        return fake, key

    def _run_ensure(self, existing):
        dt = self._dt()
        fake, key = self._fake_winreg(existing)
        with unittest.mock.patch.dict(
                "sys.modules", {"winreg": fake}), \
             unittest.mock.patch.dict(
                 "sys.modules", {"desktop": dt}):
            ok = dt.ensure_run_key_autostart()
        return ok, fake, key

    def test_ensure_rewrites_old_key(self):
        """旧 Run 键（无旗标）→ 重写为带 --autostart，返回 True。"""
        ok, fake, key = self._run_ensure('"C:\\Program Files\\EyeTerm\\winhelper.exe"')
        self.assertTrue(ok)
        args, _ = fake.SetValueEx.call_args
        self.assertEqual(args[1], "EyeTerm")     # args[0]=键句柄
        self.assertEqual(args[3], fake.REG_SZ)
        self.assertEqual(args[4],
                         '"C:\\Program Files\\EyeTerm\\winhelper.exe" --autostart')

    def test_ensure_skips_flagged_key(self):
        """已含旗标 → 不重写返回 False（幂等）。"""
        ok, fake, key = self._run_ensure(
            '"C:\\app\\winhelper.exe" --autostart')
        self.assertFalse(ok)
        fake.SetValueEx.assert_not_called()

    def test_ensure_skips_missing_key(self):
        """无 EyeTerm 键（用户未勾选自启）→ 不无中生有，返回 False。"""
        ok, fake, key = self._run_ensure(None)
        self.assertFalse(ok)
        fake.SetValueEx.assert_not_called()

    def test_desktop_silent_branch_integrated(self):
        """集成静态断言：静默分支/隐藏窗口/气泡/锁衔接/监听保持。"""
        ws = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        d = open(os.path.join(ws, "desktop.py"), encoding="utf-8").read()
        self.assertIn("focus_on_exists=not autostart", d)
        self.assertIn("hidden=autostart", d)
        self.assertIn('tray.notify(', d)
        self.assertIn("ensure_run_key_autostart()", d)
        self.assertIn("_si2.listen_replace(", d)   # 4.1.6 可接管保持
        tray = open(os.path.join(ws, "tray.py"), encoding="utf-8").read()
        self.assertIn("def notify(", tray)

    def test_desktop_integrated_and_iss_replace(self):
        """集成静态断言：desktop.py 顶层闸门 + main 内监听 + iss --replace。"""
        ws = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        d = open(os.path.join(ws, "desktop.py"), encoding="utf-8").read()
        self.assertIn("_si.enter(replace=", d)
        self.assertIn("_si2.listen_replace(", d)
        self.assertIn("sys.exit(0)", d)
        iss = open(os.path.join(ws, "installer", "EyeTerm.iss"),
                   encoding="utf-8").read()
        self.assertIn("Parameters: --replace", iss)


if __name__ == "__main__":
    unittest.main(verbosity=2)
