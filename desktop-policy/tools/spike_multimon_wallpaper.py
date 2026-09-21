# -*- coding: utf-8 -*-
"""P0 spike A：多屏拼接壁纸可行性实测（观枢终端平台｜EyeTerm · 桌面管控专项）。

验证点（对应 ADR-002）：
1. EnumDisplayMonitors 枚举每屏分辨率/偏移/主屏标识（DPI 感知修正，含负坐标副屏）
2. 虚拟桌面包围盒计算
3. stdlib 拼接图生成（zlib PNG 编码；逐屏测试图案；cover 缩放正确性单测）
4. SPI_SETDESKWALLPAPER + WallpaperStyle=2（拉伸至虚拟桌面）应用
5. GDI 抓屏（BitBlt+GetDIBits）回采比对 → 每屏独立显示/无变形/无黑边自动判定
6. GDI StretchBlt 缩放链路探针（P2 引擎性能路径可行性证据）
7. 全程备份原壁纸与样式，结束自动还原现场

用法：
  python tools/spike_multimon_wallpaper.py              # 完整实测（临时改壁纸并还原）
  python tools/spike_multimon_wallpaper.py --keep       # 保留拼接壁纸供人眼复核
  python tools/spike_multimon_wallpaper.py --enum-only  # 仅枚举显示器，零改动
输出：tools/spike_out/spikeA_report.txt + spikeA_stitch.png + spikeA_grab.png
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import os
import struct
import sys
import zlib

SPI_GETDESKWALLPAPER = 0x0073
SPI_SETDESKWALLPAPER = 0x0020
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
advapi32 = ctypes.windll.advapi32

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spike_out")
_report_lines = []


def log(msg):
    print(msg)
    _report_lines.append(msg)


# ---------------------------------------------------------------- DPI 感知
def enable_dpi_awareness():
    """per-monitor v2 → shcore → system 三级降级，确保坐标为物理像素。"""
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return "per-monitor(shcore)"
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
        return "system"
    except Exception:
        pass
    return "unaware"


# ---------------------------------------------------------------- 显示器枚举
class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD),
                ("rcMonitor", wt.RECT),
                ("rcWork", wt.RECT),
                ("dwFlags", wt.DWORD),
                ("szDevice", wt.WCHAR * 32)]


MONITORENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC,
                                     ctypes.POINTER(wt.RECT), wt.LPARAM)


def enum_monitors():
    monitors = []

    def cb(hmon, hdc, lprect, lparam):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            monitors.append({
                "device": mi.szDevice,
                "left": mi.rcMonitor.left, "top": mi.rcMonitor.top,
                "width": mi.rcMonitor.right - mi.rcMonitor.left,
                "height": mi.rcMonitor.bottom - mi.rcMonitor.top,
                "primary": bool(mi.dwFlags & 1),
            })
        return True

    if not user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0):
        raise RuntimeError("EnumDisplayMonitors 失败")
    # 主屏排前，其余按 (top,left) 稳定排序，保证拼接序号确定
    monitors.sort(key=lambda m: (not m["primary"], m["top"], m["left"]))
    return monitors


def virtual_bounds():
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return vx, vy, vw, vh


# ---------------------------------------------------------------- PNG 画布
class Canvas(object):
    """RGB 像素画布（行级操作，性能足够 spike 与小图单测）。"""

    def __init__(self, w, h, fill=(0, 0, 0)):
        if w <= 0 or h <= 0:
            raise ValueError("invalid canvas size %dx%d" % (w, h))
        self.w = w
        self.h = h
        row = bytes(fill) * w
        self.buf = bytearray(row * h)

    def set_px(self, x, y, color):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.buf[i:i + 3] = bytes(color)

    def fill_rect(self, x, y, w, h, color):
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.w, x + w), min(self.h, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes(color) * (x1 - x0)
        for yy in range(y0, y1):
            i = (yy * self.w + x0) * 3
            self.buf[i:i + (x1 - x0) * 3] = row

    def to_png_bytes(self):
        ihdr = struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0)
        stride = self.w * 3
        raw = bytearray()
        for y in range(self.h):
            raw.append(0)  # filter: none
            raw += self.buf[y * stride:(y + 1) * stride]

        def chunk(tag, data):
            return (struct.pack(">I", len(data)) + tag + data +
                    struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
                chunk(b"IDAT", zlib.compress(bytes(raw), 6)) +
                chunk(b"IEND", b""))

    def get_px(self, x, y):
        i = (y * self.w + x) * 3
        return tuple(self.buf[i:i + 3])


# ---------------------------------------------------------------- 测试图案
SCREEN_COLORS = [(176, 48, 48), (44, 88, 176), (48, 150, 90),
                 (176, 140, 40), (128, 60, 176), (60, 160, 160)]


def draw_test_pattern(canvas, region, idx):
    """屏幕区域内画：专属底色 + 白色内缩边框（黑边/偏移检测）+ 中央横条纹带
    （等宽条纹检测变形/拉伸比例）+ 左上序号色块（镜像/错位检测）。"""
    x, y, w, h = region
    color = SCREEN_COLORS[idx % len(SCREEN_COLORS)]
    canvas.fill_rect(x, y, w, h, color)
    white = (245, 245, 245)
    bw = max(4, min(w, h) // 200)          # 边框宽
    inset = max(6, min(w, h) // 40)        # 边框内缩
    # 上/下/左/右边框
    canvas.fill_rect(x + inset, y + inset, w - 2 * inset, bw, white)
    canvas.fill_rect(x + inset, y + h - inset - bw, w - 2 * inset, bw, white)
    canvas.fill_rect(x + inset, y + inset, bw, h - 2 * inset, white)
    canvas.fill_rect(x + w - inset - bw, y + inset, bw, h - 2 * inset, white)
    # 中央条纹带：8 条等宽横条（白/底色交替），占高 1/3
    band_h = h // 3
    band_y = y + h - band_h - inset * 3
    stripe_h = band_h // 8
    for i in range(8):
        c = white if i % 2 == 0 else color
        canvas.fill_rect(x + inset * 3, band_y + i * stripe_h,
                         w - inset * 6, stripe_h, c)
    # 左上序号色块（黑色块 idx+1 个）
    blk = max(8, min(w, h) // 30)
    for i in range(idx + 1):
        canvas.fill_rect(x + inset * 2 + i * (blk + blk // 2),
                         y + inset * 2, blk, blk, (0, 0, 0))


def cover_crop(src, dst_w, dst_h):
    """cover 缩放裁剪：等比放大至完全覆盖目标再居中裁剪（最近邻）。
    返回新 Canvas；源图必须为渐变图才可做采样断言（见 spike 缩放单测）。"""
    if dst_w <= 0 or dst_h <= 0:
        raise ValueError("bad dst size")
    scale = max(dst_w / src.w, dst_h / src.h)
    sw = max(1, int(round(dst_w / scale)))
    sh = max(1, int(round(dst_h / scale)))
    sx = (src.w - sw) // 2
    sy = (src.h - sh) // 2
    dst = Canvas(dst_w, dst_h)
    for dy in range(dst_h):
        syy = sy + int(dy / scale)
        srow = syy * src.w
        drow = dy * dst_w
        for dx in range(dst_w):
            sxx = sx + int(dx / scale)
            si = (srow + sxx) * 3
            di = (drow + dx) * 3
            dst.buf[di:di + 3] = src.buf[si:si + 3]
    return dst


# ---------------------------------------------------------------- GDI 探针
def gdi_stretchblt_probe():
    """GDI 缩放链路探针：StretchBlt 2x 缩放 + GetDIBits 读回，验证 P2 性能路径。"""
    hdc_screen = user32.GetDC(None)
    try:
        mem = gdi32.CreateCompatibleDC(hdc_screen)
        w, h = 64, 32
        src = Canvas(w, h, (10, 200, 30))
        bmi = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, BI_RGB, 0, 0, 0, 0, 0)
        # 24bpp DIB：行按 4 字节对齐
        stride = ((w * 3 + 3) // 4) * 4
        raw = bytearray()
        for y in range(h):
            row = bytearray()
            for x in range(w):
                r_, g_, b_ = src.get_px(x, y)
                row += bytes((b_, g_, r_))  # DIB 内存序 BGR
            raw += bytes(row) + b"\x00" * (stride - len(row))
        # BITMAPINFO 底朝上：倒序行
        raw_rows = b"".join(raw[r * stride:(r + 1) * stride]
                            for r in range(h - 1, -1, -1))
        bits_ptr = ctypes.c_void_p()
        hbm = gdi32.CreateDIBSection(hdc_screen, bmi, DIB_RGB_COLORS,
                                     ctypes.byref(bits_ptr), None, 0)
        if not hbm or not bits_ptr.value:
            return False, "CreateDIBSection 失败"
        ctypes.memmove(bits_ptr.value, raw_rows, len(raw_rows))
        old = gdi32.SelectObject(mem, hbm)
        dst_dc = gdi32.CreateCompatibleDC(hdc_screen)
        dst_bmi = struct.pack("<IiiHHIIiiII", 40, w * 2, h * 2, 1, 24,
                              BI_RGB, 0, 0, 0, 0, 0)
        dst_bits = ctypes.c_void_p()
        dst_hbm = gdi32.CreateDIBSection(hdc_screen, dst_bmi, DIB_RGB_COLORS,
                                         ctypes.byref(dst_bits), None, 0)
        if not dst_hbm or not dst_bits.value:
            gdi32.SelectObject(mem, old)
            return False, "CreateDIBSection(dst) 失败"
        dst_old = gdi32.SelectObject(dst_dc, dst_hbm)
        ok = gdi32.StretchBlt(dst_dc, 0, 0, w * 2, h * 2, mem, 0, 0, w, h,
                              SRCCOPY)
        gdi32.SelectObject(dst_dc, dst_old)
        gdi32.SelectObject(mem, old)
        # 读回 dst 中心采样：必须在 DeleteObject 之前（DIB 内存随删除失效）
        dst_stride = ((w * 2 * 3 + 3) // 4) * 4
        dst_total = dst_stride * h * 2
        out = ctypes.create_string_buffer(dst_total)
        ctypes.memmove(out, dst_bits.value, dst_total)
        gdi32.DeleteObject(dst_hbm)
        gdi32.DeleteObject(hbm)
        gdi32.DeleteDC(dst_dc)
        gdi32.DeleteDC(mem)
        if not ok:
            return False, "StretchBlt 失败"
        top_row = h * 2 - 1  # 底朝上
        off = top_row * dst_stride + (w) * 3  # 中心像素
        px = bytes(out[off:off + 3])
        b, g, r = int(px[0]), int(px[1]), int(px[2])  # GDI 内存序 BGR
        if (r, g, b) == (10, 200, 30):
            return True, "StretchBlt+GetDIBits 链路可用（中心色一致）"
        return False, "采样不一致 rgb=(%d,%d,%d)" % (r, g, b)
    finally:
        user32.ReleaseDC(None, hdc_screen)


# ---------------------------------------------------------------- 壁纸应用/备份
def reg_query_desktop():
    """读 HKCU\\Control Panel\\Desktop 当前样式（缺省给系统默认口径）。"""
    HKEY_CURRENT_USER = 0x80000001
    KEY_READ = 0x20019
    hkey = wt.HKEY()
    out = {"WallpaperStyle": None, "TileWallpaper": None}
    if advapi32.RegOpenKeyExW(HKEY_CURRENT_USER, "Control Panel\\Desktop", 0,
                              KEY_READ, ctypes.byref(hkey)) == 0:
        for name in out:
            buf = ctypes.create_unicode_buffer(64)
            size = wt.DWORD(ctypes.sizeof(buf))
            if advapi32.RegQueryValueExW(hkey, name, None, None, buf,
                                         ctypes.byref(size)) == 0:
                out[name] = buf.value
        advapi32.RegCloseKey(hkey)
    return out


def reg_set_desktop(name, value):
    HKEY_CURRENT_USER = 0x80000001
    KEY_SET_VALUE = 0x0002
    hkey = wt.HKEY()
    if advapi32.RegOpenKeyExW(HKEY_CURRENT_USER, "Control Panel\\Desktop", 0,
                              KEY_SET_VALUE, ctypes.byref(hkey)) != 0:
        raise RuntimeError("打开注册表 Desktop 键失败")
    try:
        if advapi32.RegSetValueExW(hkey, name, 0, 1,  # REG_SZ
                                   ctypes.create_unicode_buffer(value),
                                   (len(value) + 1) * 2) != 0:
            raise RuntimeError("写注册表值失败: %s" % name)
    finally:
        advapi32.RegCloseKey(hkey)


def get_current_wallpaper():
    buf = ctypes.create_unicode_buffer(520)
    if not user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, 520, buf, 0):
        return ""
    return buf.value


def set_wallpaper(path, style="2", tiled=False):
    reg_set_desktop("WallpaperStyle", style)
    reg_set_desktop("TileWallpaper", "1" if tiled else "0")
    if not user32.SystemParametersInfoW(
            SPI_SETDESKWALLPAPER, 0, ctypes.create_unicode_buffer(path),
            SPIF_UPDATEINIFILE | SPIF_SENDCHANGE):
        raise RuntimeError("SPI_SETDESKWALLPAPER 失败 err=%d"
                           % kernel32.GetLastError())


def wait_wallpaper_settled(vx, vy, vw, vh, timeout=8.0):
    """轮询抓屏，等待壁纸画面稳定（连续两帧同区域采样一致），返回抓屏。
    Explorer 收到广播后异步转码（PNG→缓存）并重绘，需要等待。"""
    import time
    deadline = time.time() + timeout
    prev = None
    grab = None
    while time.time() < deadline:
        time.sleep(0.7)
        grab = grab_virtual_screen(vx, vy, vw, vh)
        # 屏0 中心与四分点采样签名
        sig = []
        for sx, sy in [(vw // 2, vh // 2), (vw // 4, vh // 2),
                       (3 * vw // 4, vh // 2), (vw // 2, vh // 4),
                       (vw // 2, 3 * vh // 4)]:
            sig.append(grab.get_px(sx, sy))
        if prev is not None and prev == sig:
            return grab, True
        prev = sig
    return grab, False


def read_wallpaper_now():
    buf = ctypes.create_unicode_buffer(520)
    if not user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, 520, buf, 0):
        return "<读取失败>"
    return buf.value


def toggle_show_desktop():
    """Win+D：最小化/恢复全部窗口（壁纸验证前清场，验证后恢复现场）。"""
    VK_LWIN = 0x5B
    VK_D = 0x44
    KEYEVENTF_KEYUP = 0x0002
    user32.keybd_event(VK_LWIN, 0, 0, 0)
    user32.keybd_event(VK_D, 0, 0, 0)
    user32.keybd_event(VK_D, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_LWIN, 0, KEYEVENTF_KEYUP, 0)
    import time
    time.sleep(1.2)


# ---------------------------------------------------------------- 抓屏
def grab_virtual_screen(vx, vy, vw, vh):
    hdc_screen = user32.GetDC(None)
    try:
        mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbm = gdi32.CreateCompatibleBitmap(hdc_screen, vw, vh)
        old = gdi32.SelectObject(mem, hbm)
        if not gdi32.BitBlt(mem, 0, 0, vw, vh, hdc_screen, vx, vy, SRCCOPY):
            raise RuntimeError("BitBlt 抓屏失败")
        # 32bpp top-down
        bmi = struct.pack("<IiiHHIIiiII", 40, vw, -vh, 1, 32, BI_RGB,
                          0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(vw * vh * 4)
        got = gdi32.GetDIBits(mem, hbm, 0, vh, buf, bmi, DIB_RGB_COLORS)
        if got != vh:
            raise RuntimeError("GetDIBits 失败 rows=%d" % got)
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(hbm)
        gdi32.DeleteDC(mem)
        cv = Canvas(vw, vh)
        # 32bpp BGRA → 24bpp RGB
        src = bytes(buf)
        di = 0
        si = 0
        for _y in range(vh):
            row = bytearray(vw * 3)
            for x in range(vw):
                row[x * 3] = src[si + 2]
                row[x * 3 + 1] = src[si + 1]
                row[x * 3 + 2] = src[si]
                si += 4
            cv.buf[di:di + vw * 3] = row
            di += vw * 3
        return cv
    finally:
        user32.ReleaseDC(None, hdc_screen)


# ---------------------------------------------------------------- 判定
def color_close(a, b, tol=10):
    return all(abs(a[i] - b[i]) <= tol for i in range(3))


def verify_grab(grab, monitors, vx, vy):
    """逐屏采样判定：四角内缩=底色（无黑边/无偏移）、条纹带交替（无变形）、
    屏间交界两侧颜色符合各自屏底色（独立显示）。"""
    fails = []
    for idx, m in enumerate(monitors):
        x, y = m["left"] - vx, m["top"] - vy
        w, h = m["width"], m["height"]
        color = SCREEN_COLORS[idx % len(SCREEN_COLORS)]
        white = (245, 245, 245)
        inset = max(6, min(w, h) // 40) + 4  # 边框外侧仍是底色
        corners = [(x + inset, y + inset), (x + w - inset, y + inset),
                   (x + inset, y + h - inset), (x + w - inset, y + h - inset),
                   (x + 1, y + 1), (x + w - 2, y + 1),
                   (x + 1, y + h - 2), (x + w - 2, y + h - 2)]
        for cx, cy in corners:
            px = grab.get_px(cx, cy)
            if not color_close(px, color):
                fails.append("屏%d 角点(%d,%d)=%s 期望底色%s（疑似黑边/偏移）"
                             % (idx, cx, cy, px, color))
        # 条纹带：中心横线扫描，交替白/底色均须出现且次数≥6
        band_h = h // 3
        band_y = y + h - band_h - (max(6, min(w, h) // 40)) * 3
        cy = band_y + band_h // 2
        has_white = has_color = False
        for sx in range(x + 10, x + w - 10, max(1, w // 80)):
            px = grab.get_px(sx, cy)
            if color_close(px, white):
                has_white = True
            elif color_close(px, color):
                has_color = True
        if not (has_white and has_color):
            fails.append("屏%d 条纹带扫描未同时发现白/底色条纹（变形或错位）" % idx)
        # 相邻屏独立：右邻屏交界左侧应为本屏底色
        for other in monitors:
            if other is m:
                continue
            ox = other["left"] - vx
            if ox >= x + w:  # 右侧相邻
                for py in range(y + 10, y + h - 10, max(1, h // 40)):
                    left_px = grab.get_px(x + w - 2, py)
                    if not color_close(left_px, color):
                        fails.append("屏%d 右缘(%d,%d)=%s 期望%s（跨界污染）"
                                     % (idx, x + w - 2, py, left_px, color))
                break
    return fails


def scale_unit_test():
    """cover 缩放正确性单测：3840x2160 渐变源 → 1920x1080 目标采样断言。"""
    src_w, src_h = 384, 216  # 1/10 尺寸等价验证（同比例），控制耗时
    src = Canvas(src_w, src_h)

    def grad(x, y):
        return (x * 255 // (src_w - 1), y * 255 // (src_h - 1), 128)

    for y in range(src_h):
        row = bytearray(src_w * 3)
        for x in range(src_w):
            r, g, b = grad(x, y)
            row[x * 3] = r
            row[x * 3 + 1] = g
            row[x * 3 + 2] = b
        src.buf[y * src_w * 3:(y + 1) * src_w * 3] = row
    dst = cover_crop(src, 192, 108)  # 1920x1080 的 1/10
    # cover 后源图等比满高：sw=192,sh=108,无裁剪，采样点直接对应
    checks = [(0, 0), (95, 53), (191, 107), (50, 100)]
    fails = []
    for dx, dy in checks:
        expect = grad(int(dx * 2), int(dy * 2))
        got = dst.get_px(dx, dy)
        if not color_close(got, expect, tol=4):
            fails.append("scale(%d,%d)=%s 期望%s" % (dx, dy, got, expect))
    return fails


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--enum-only", action="store_true")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    dpi = enable_dpi_awareness()
    log("== spike A：多屏拼接壁纸实测 ==")
    log("DPI 感知: %s" % dpi)
    monitors = enum_monitors()
    vx, vy, vw, vh = virtual_bounds()
    log("显示器数: %d  虚拟桌面: origin=(%d,%d) size=%dx%d"
        % (len(monitors), vx, vy, vw, vh))
    for i, m in enumerate(monitors):
        log("  屏%d device=%s %dx%d @(%d,%d) primary=%s"
            % (i, m["device"], m["width"], m["height"], m["left"],
               m["top"], m["primary"]))
    if args.enum_only:
        log("结果: 枚举模式结束（零改动）")
        _save_report()
        return 0

    all_fails = []
    # 1. GDI 探针
    ok, msg = gdi_stretchblt_probe()
    log("[1] GDI StretchBlt 探针: %s（%s）" % ("PASS" if ok else "FAIL", msg))
    if not ok:
        all_fails.append("gdi_probe")

    # 2. 缩放单测
    sf = scale_unit_test()
    log("[2] cover 缩放单测(4K→1080p 等比): %s"
        % ("PASS" if not sf else "FAIL " + "; ".join(sf)))

    # 3. 拼接图生成
    stitch = Canvas(vw, vh)
    for i, m in enumerate(monitors):
        draw_test_pattern(stitch,
                          (m["left"] - vx, m["top"] - vy, m["width"],
                           m["height"]), i)
    stitch_path = os.path.join(OUT_DIR, "spikeA_stitch.png")
    with open(stitch_path, "wb") as f:
        f.write(stitch.to_png_bytes())
    log("[3] 拼接图生成: %dx%d → %s (%.1f KB)"
        % (vw, vh, stitch_path, os.path.getsize(stitch_path) / 1024.0))

    # 4. 备份现场 → 清场（最小化全部窗口）→ 双模式应用（Stretch/Tile 择优）
    old_wp = get_current_wallpaper()
    old_style = reg_query_desktop()
    log("[4] 备份现场: 壁纸=%r style=%r" % (old_wp, old_style))
    grab_path = os.path.join(OUT_DIR, "spikeA_grab.png")
    mode_results = {}
    toggle_show_desktop()  # Win+D 最小化全部窗口：确保采样点落在壁纸上
    for mode, style, tiled in (("stretch", "2", False), ("tile", "0", True)):
        try:
            set_wallpaper(stitch_path, style=style, tiled=tiled)
        except RuntimeError as exc:
            mode_results[mode] = (["apply:" + str(exc)], None)
            continue
        log("    已应用拼接壁纸（模式=%s, WallpaperStyle=%s, Tile=%d）"
            % (mode, style, tiled))
        grab, settled = wait_wallpaper_settled(vx, vy, vw, vh)
        cur = read_wallpaper_now()
        reg_wall = reg_query_desktop()
        log("    SPI 读回=%r（画面稳定=%s）〔判定以抓屏为准〕" % (cur, settled))
        fails = verify_grab(grab, monitors, vx, vy)
        mode_results[mode] = (fails, grab)

    # 择优：任一模式判定 PASS 即机制成立；报告两种模式结果
    ok_mode = next((m for m, (f_, _g) in mode_results.items() if not f_),
                   None)
    chosen = ok_mode or "stretch"
    fails = mode_results[chosen][0]
    grab = mode_results[chosen][1]
    log("[5] 抓屏回采比对（择优模式=%s）: %s → %s"
        % (chosen, "PASS" if not fails else "FAIL", grab_path))
    for m, (f_, _g) in mode_results.items():
        log("    模式%s: %s（%d 项不符）" % (m, "PASS" if not f_ else "FAIL",
                                            len(f_)))
        for f2 in f_[:6]:
            log("      - %s" % f2)
        if len(f_) > 6:
            log("      - ...共 %d 项" % len(f_))
    if grab is not None:
        with open(grab_path, "wb") as f:
            f.write(grab.to_png_bytes())
    all_fails += fails

    # 6. 还原现场（壁纸）→ 恢复窗口布局
    if args.keep:
        log("[6] --keep：保留拼接壁纸，供人眼复核后手动还原")
    else:
        if old_wp:
            # 先恢复原样式再 SPI，避免样式残留
            if old_style["WallpaperStyle"] is not None:
                reg_set_desktop("WallpaperStyle", old_style["WallpaperStyle"])
            if old_style["TileWallpaper"] is not None:
                reg_set_desktop("TileWallpaper", old_style["TileWallpaper"])
            set_wallpaper(old_wp,
                          style=old_style["WallpaperStyle"] or "10")
            log("[6] 现场还原: %r" % old_wp)
        else:
            log("[6] 原壁纸为空，跳过还原（保持拼接壁纸，可 --keep 语义处理）")
    toggle_show_desktop()  # Win+D 恢复原窗口布局

    log("== spike A 结果: %s ==" % ("FAIL" if all_fails else "PASS"))
    _save_report()
    return 1 if all_fails else 0


def _save_report():
    with open(os.path.join(OUT_DIR, "spikeA_report.txt"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(_report_lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
