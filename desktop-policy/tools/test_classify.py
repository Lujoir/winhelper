# -*- coding: utf-8 -*-
"""BRG-066 分类精化单测：apply_wallpaper 阶段化语义（mock Win32 依赖）。"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import desktop_policy as dp  # noqa: E402


class TestApplyWallpaperClassify(unittest.TestCase):
    MON = [{"index": 0, "device": "D1", "left": 0, "top": 0,
            "width": 100, "height": 50, "primary": True}]

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dp_cls_")
        dp.set_log_dir(os.path.join(self.tmp, "logs"))
        self._orig = {}
        self._patch("enum_monitors", lambda: [dict(m) for m in self.MON])
        self._patch("virtual_bounds", lambda: (0, 0, 100, 50))
        self._patch("build_stitch",
                    lambda f, m, vx, vy, vw, vh: r"C:\stub\stitch.png")
        self._patch("transcoded_wallpaper_mtime", lambda: None)
        self._patch("reg_read_desktop", lambda name: None)
        self._patch("spi_get_wallpaper", lambda: "")
        self._patch("save_png", lambda rgb, w, h, dest: None)

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(dp, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patch(self, name, fn):
        self._orig[name] = getattr(dp, name)
        setattr(dp, name, fn)

# PART2

    def test_build_stitch_fail_is_apply_failed(self):
        def _boom(*a):
            raise RuntimeError("gdiplus_init_failed")
        dp.build_stitch = _boom
        r = dp.apply_wallpaper(["x.png"], verify=True)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["code"], "apply_failed")

    def test_spi_fail_is_apply_failed(self):
        def _boom(path, style="2", tiled=False):
            raise RuntimeError("SPI 失败")
        dp.spi_set_wallpaper = _boom
        r = dp.apply_wallpaper(["x.png"], verify=True)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["code"], "apply_failed")

    def test_spi_ok_but_rejected_is_blocked_by_security(self):
        dp.spi_set_wallpaper = lambda path, style="2", tiled=False: None
        r = dp.apply_wallpaper(["x.png"], verify=True)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["code"], "blocked_by_security")

    def test_readback_exc_after_spi_still_classifies(self):
        # BRG-066 核心：SPI 后读回/抓屏异常不掩盖拦截判定
        dp.spi_set_wallpaper = lambda path, style="2", tiled=False: None

        def _boom():
            raise RuntimeError("读回炸了")
        dp.spi_get_wallpaper = _boom

        def _grab_fail(vx, vy, vw, vh):
            raise RuntimeError("抓屏炸了")
        dp.grab_screen = _grab_fail
        r = dp.apply_wallpaper(["x.png"], verify=True)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["code"], "blocked_by_security")
        self.assertIsNone(r.get("verify_grab"))

    def test_reg_landed_is_ok(self):
        dp.spi_set_wallpaper = lambda path, style="2", tiled=False: None

        def _reg(name):
            return r"C:\stub\stitch.png" if name == "Wallpaper" else "2"
        dp.reg_read_desktop = _reg
        r = dp.apply_wallpaper(["x.png"], verify=True)
        self.assertTrue(r["ok"])


class TestPngEncodeChar_Array(unittest.TestCase):
    """BRG-067 根因回归：c_char_Array 索引返回单字节 bytes，_png_encode 须收敛。"""

    def test_char_array_input(self):
        import ctypes
        buf = ctypes.create_string_buffer(8 * 8 * 4)
        out = os.path.join(tempfile.mkdtemp(prefix="dp_png_"), "t.png")
        dp._png_encode(buf, 8, 8, out)
        with open(out, "rb") as f:
            head = f.read(8)
        self.assertEqual(head, b"\x89PNG\r\n\x1a\n")

    def test_bytes_input_equivalent(self):
        import ctypes
        buf = ctypes.create_string_buffer(8 * 8 * 4)
        d1 = os.path.join(tempfile.mkdtemp(prefix="dp_png_a_"), "a.png")
        dp._png_encode(buf, 8, 8, d1)
        d2 = os.path.join(tempfile.mkdtemp(prefix="dp_png_b_"), "b.png")
        dp._png_encode(bytes(bytearray(buf)), 8, 8, d2)
        with open(d1, "rb") as f1, open(d2, "rb") as f2:
            self.assertEqual(f1.read(), f2.read())


if __name__ == "__main__":
    unittest.main(verbosity=1)
