# -*- coding: utf-8 -*-
"""desktop_policy · 终端桌面管控执行引擎（观枢终端平台｜EyeTerm）。

stdlib-only；策略轮询 → 四类策略本地执行 → 生效自检 → 结果回传；离线兜底不回退。
契约 docs/CONTRACT.md；决策 docs/DECISIONS.md。分部组装：PART1/2/3 锚点分割。
"""
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import os
import random
import re
import struct
import subprocess
import threading
import time
import zlib

APP_DIR_NAME = "desktop_policy"
POLL_DEFAULT_SEC = 120
BACKOFF_SEC = (2, 8, 32)

SPI_GETDESKWALLPAPER = 0x0073
SPI_SETDESKWALLPAPER = 0x0020
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0

CSP_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\PersonalizationCSP"
CSP_VALUES = ("LockScreenImagePath", "LockScreenImageUrl", "LockScreenStatus")

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
advapi32 = ctypes.windll.advapi32

_log_lock = threading.Lock()
_log_dir = None


def data_dir():
    base = os.path.join(os.environ.get("LOCALAPPDATA",
                                       os.path.expanduser("~")),
                        "winhelper", APP_DIR_NAME)
    os.makedirs(base, exist_ok=True)
    return base


def set_log_dir(path):
    global _log_dir
    _log_dir = path
    os.makedirs(path, exist_ok=True)


def log(msg, level="INFO"):
    with _log_lock:
        # 默认目录 = data_dir()/logs（CONTRACT §4，与 handle_dp_logs 读取端一致；
        # BRG-063 修复：主应用运行态未调 set_log_dir 时不再写到 data_dir 根）
        d = _log_dir or os.path.join(data_dir(), "logs")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "dp_%s.log" % time.strftime("%Y%m%d"))
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write("%s [%s] %s\n"
                        % (time.strftime("%Y-%m-%d %H:%M:%S"), level, msg))
        except OSError:
            pass


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------- 显示器枚举
class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD),
                ("rcMonitor", wt.RECT),
                ("rcWork", wt.RECT),
                ("dwFlags", wt.DWORD),
                ("szDevice", wt.WCHAR * 32)]


_MONITORENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC,
                                      ctypes.POINTER(wt.RECT), wt.LPARAM)
_dpi_done = []


def enable_dpi_awareness():
    """per-monitor v2 → shcore → system 三级降级（物理像素坐标前提）。"""
    if _dpi_done:
        return
    _dpi_done.append(True)
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def enum_monitors():
    enable_dpi_awareness()
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

    if not user32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(cb), 0):
        raise RuntimeError("EnumDisplayMonitors 失败")
    monitors.sort(key=lambda m: (not m["primary"], m["top"], m["left"]))
    for i, m in enumerate(monitors):
        m["index"] = i
    return monitors


def virtual_bounds():
    enable_dpi_awareness()
    return (user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))


def monitors_signature(monitors):
    """显示器签名；分辨率变化检测的轮询等效（WM_DISPLAYCHANGE 增强项）。"""
    return ";".join("%dx%d@%d,%d%s" % (m["width"], m["height"], m["left"],
                                       m["top"],
                                       "P" if m["primary"] else "")
                    for m in monitors or [])


def cover_src_rect(src_w, src_h, dst_w, dst_h):
    """cover 裁剪源矩形：等比覆盖后居中裁剪（StretchBlt 源区）。"""
    scale = max(dst_w / float(src_w), dst_h / float(src_h))
    sw = min(src_w, int(round(dst_w / scale)))
    sh = min(src_h, int(round(dst_h / scale)))
    return (src_w - sw) // 2, (src_h - sh) // 2, sw, sh


# PART1_GDIPLUS


class BITMAP(ctypes.Structure):
    _fields_ = [("bmType", wt.LONG), ("bmWidth", wt.LONG),
                ("bmHeight", wt.LONG), ("bmWidthBytes", wt.LONG),
                ("bmPlanes", wt.WORD), ("bmBitsPixel", wt.WORD),
                ("bmBits", ctypes.c_void_p)]


class GdiplusLoader(object):
    """GDI+ flat API（OS 组件）：源图 PNG/JPG/BMP 加载与尺寸读取。"""

    def __init__(self):
        self.ok = False

    def startup(self):
        if self.ok:
            return True
        try:
            dll = ctypes.windll.gdiplus
            si = struct.pack("<IIII", 1, 0, 0, 0)
            token = wt.ULONG()
            if dll.GdiplusStartup(ctypes.byref(token),
                                  ctypes.c_char_p(si), None) == 0:
                self.token = token
                self.dll = dll
                self.ok = True
        except Exception:
            pass
        return self.ok

    def load_hbitmap(self, path):
        gpbitmap = ctypes.c_void_p()
        if self.dll.GdipCreateBitmapFromFile(
                ctypes.c_wchar_p(os.path.abspath(path)),
                ctypes.byref(gpbitmap)) != 0:
            return None
        hbm = ctypes.c_void_p()
        hr = self.dll.GdipCreateHBITMAPFromBitmap(gpbitmap, ctypes.byref(hbm),
                                                  0xFF000000)
        self.dll.GdipDisposeImage(gpbitmap)
        return hbm if hr == 0 and hbm else None

    def hbitmap_size(self, hbm):
        info = BITMAP()
        if gdi32.GetObjectW(hbm, ctypes.sizeof(info),
                            ctypes.byref(info)) == 0:
            return None
        return info.bmWidth, abs(info.bmHeight)

    def shutdown(self):
        if self.ok:
            self.dll.GdiplusShutdown(ctypes.byref(self.token))
            self.ok = False


_gdiplus = GdiplusLoader()


# PART1_STITCH


def build_stitch(per_monitor_files, monitors, vx, vy, vw, vh):
    """虚拟桌面拼接图：每屏源图 cover 裁剪后 StretchBlt 至对应区域。
    per_monitor_files: 源路径或 None（None 首张可用源兜底）→ stitch.png 路径。"""
    if not _gdiplus.startup():
        raise RuntimeError("gdiplus_init_failed")
    hdc_screen = user32.GetDC(None)
    try:
        mem = gdi32.CreateCompatibleDC(hdc_screen)
        bmi = struct.pack("<IiiHHIIiiII", 40, vw, -vh, 1, 32, BI_RGB,
                          0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        hbm = gdi32.CreateDIBSection(hdc_screen, bmi, DIB_RGB_COLORS,
                                     ctypes.byref(bits), None, 0)
        if not hbm or not bits.value:
            raise RuntimeError("CreateDIBSection 失败")
        old = gdi32.SelectObject(mem, hbm)
        gdi32.PatBlt(mem, 0, 0, vw, vh, 0x00000042)
        fallback = next((p for p in per_monitor_files if p), None)
        for i, m in enumerate(monitors):
            src = per_monitor_files[i] or fallback
            if not src or not os.path.exists(src):
                log("屏%d 无可用源图" % i, "WARN")
                continue
            hbm_src = _gdiplus.load_hbitmap(src)
            if not hbm_src:
                log("源图加载失败 screen=%d" % i, "WARN")
                continue
            size = _gdiplus.hbitmap_size(hbm_src)
            src_dc = gdi32.CreateCompatibleDC(hdc_screen)
            old_src = gdi32.SelectObject(src_dc, hbm_src)
            if size and size[0] > 0:
                sx, sy, sw, sh = cover_src_rect(size[0], size[1],
                                                m["width"], m["height"])
                gdi32.StretchBlt(mem, m["left"] - vx, m["top"] - vy,
                                 m["width"], m["height"],
                                 src_dc, sx, sy, sw, sh, SRCCOPY)
            gdi32.SelectObject(src_dc, old_src)
            gdi32.DeleteDC(src_dc)
            gdi32.DeleteObject(hbm_src)
        out = ctypes.create_string_buffer(vw * vh * 4)
        ctypes.memmove(out, bits.value, vw * vh * 4)
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(hbm)
        gdi32.DeleteDC(mem)
        png_path = os.path.join(data_dir(), "stitch.png")
        _png_encode(out, vw, vh, png_path)
        return png_path
    finally:
        user32.ReleaseDC(None, hdc_screen)


def _png_encode(buf32, w, h, dest):
    """GDI 内存 BGRA(top-down) → PNG(RGB)。"""
    s_stride, d_stride = w * 4, w * 3
    raw = bytearray(h * (d_stride + 1))
    pos = 0
    for y in range(h):
        raw[pos] = 0
        pos += 1
        row = bytearray(d_stride)
        src_off = y * s_stride
        for x in range(w):
            si = src_off + x * 4
            di = x * 3
            row[di] = buf32[si + 2]
            row[di + 1] = buf32[si + 1]
            row[di + 2] = buf32[si]
        raw[pos:pos + d_stride] = row
        pos += d_stride
    _write_png(ihdr=_png_ihdr(w, h), raw=raw, dest=dest)


def _png_ihdr(w, h):
    return struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)


def _write_png(ihdr, raw, dest):
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    with open(dest, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
                chunk(b"IDAT", zlib.compress(bytes(raw), 6)) +
                chunk(b"IEND", b""))


def grab_screen(vx, vy, vw, vh):
    """GDI 抓屏（合成桌面含窗口）→ RGB bytearray（用于生效比对/存档）。"""
    hdc_screen = user32.GetDC(None)
    try:
        mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbm = gdi32.CreateCompatibleBitmap(hdc_screen, vw, vh)
        old = gdi32.SelectObject(mem, hbm)
        ok = gdi32.BitBlt(mem, 0, 0, vw, vh, hdc_screen, vx, vy, SRCCOPY)
        bmi = struct.pack("<IiiHHIIiiII", 40, vw, -vh, 1, 32, BI_RGB,
                          0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(vw * vh * 4)
        got = gdi32.GetDIBits(mem, hbm, 0, vh, buf, bmi, DIB_RGB_COLORS) \
            if ok else 0
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(hbm)
        gdi32.DeleteDC(mem)
        if got != vh:
            raise RuntimeError("抓屏失败")
        src = bytes(buf)
        cv = bytearray(vw * vh * 3)
        si = di = 0
        for _y in range(vh):
            for x in range(vw):
                cv[di] = src[si + 2]
                cv[di + 1] = src[si + 1]
                cv[di + 2] = src[si]
                di += 3
                si += 4
        return cv
    finally:
        user32.ReleaseDC(None, hdc_screen)


def save_png(rgb, w, h, dest):
    raw = bytearray()
    stride = w * 3
    for y in range(h):
        raw.append(0)
        raw += rgb[y * stride:(y + 1) * stride]
    _write_png(ihdr=_png_ihdr(w, h), raw=raw, dest=dest)


# PART2_WALLPAPER


# ------------------------------------------------------------- 壁纸应用
def reg_read_desktop(name):
    hkey = wt.HKEY()
    if advapi32.RegOpenKeyExW(0x80000001, "Control Panel\\Desktop", 0,
                              0x20019, ctypes.byref(hkey)) != 0:
        return None
    buf = ctypes.create_unicode_buffer(64)
    size = wt.DWORD(ctypes.sizeof(buf))
    rc = advapi32.RegQueryValueExW(hkey, name, None, None, buf,
                                   ctypes.byref(size))
    advapi32.RegCloseKey(hkey)
    return buf.value if rc == 0 else None


def reg_write_desktop(name, value):
    hkey = wt.HKEY()
    if advapi32.RegOpenKeyExW(0x80000001, "Control Panel\\Desktop", 0,
                              0x0002, ctypes.byref(hkey)) != 0:
        raise RuntimeError("注册表打开失败")
    advapi32.RegSetValueExW(hkey, name, 0, 1,
                            ctypes.create_unicode_buffer(value),
                            (len(value) + 1) * 2)
    advapi32.RegCloseKey(hkey)


def spi_get_wallpaper():
    user32.SystemParametersInfoW.argtypes = [ctypes.c_uint, ctypes.c_uint,
                                             ctypes.c_void_p, ctypes.c_uint]
    user32.SystemParametersInfoW.restype = ctypes.c_int
    buf = ctypes.create_unicode_buffer(520)
    user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, 520, buf, 0)
    return buf.value


def spi_set_wallpaper(path, style="2", tiled=False):
    reg_write_desktop("WallpaperStyle", style)
    reg_write_desktop("TileWallpaper", "1" if tiled else "0")
    user32.SystemParametersInfoW.argtypes = [ctypes.c_uint, ctypes.c_uint,
                                             ctypes.c_void_p, ctypes.c_uint]
    user32.SystemParametersInfoW.restype = ctypes.c_int
    ok = user32.SystemParametersInfoW(
        SPI_SETDESKWALLPAPER, 0,
        ctypes.cast(ctypes.create_unicode_buffer(path), ctypes.c_void_p),
        SPIF_UPDATEINIFILE | SPIF_SENDCHANGE)
    if not ok:
        raise RuntimeError("SPI_SETDESKWALLPAPER 失败")


def transcoded_wallpaper_mtime():
    p = os.path.join(os.environ.get("APPDATA", ""),
                     "Microsoft", "Windows", "Themes",
                     "TranscodedWallpaper")
    try:
        return os.path.getmtime(p)
    except OSError:
        return None


def apply_wallpaper(per_monitor_files, monitors=None, verify=True):
    """应用多屏拼接壁纸 + 自检三件套（ADR-004）。返回 result dict。
    生效判定唯一标准=抓屏回采；SPI 读回/注册表仅记录。"""
    monitors = monitors or enum_monitors()
    vx, vy, vw, vh = virtual_bounds()
    mtime_before = transcoded_wallpaper_mtime()
    reg_before = {"WallpaperStyle": reg_read_desktop("WallpaperStyle"),
                  "Wallpaper": reg_read_desktop("Wallpaper")}
    stitch = build_stitch(per_monitor_files, monitors, vx, vy, vw, vh)
    spi_set_wallpaper(stitch, style="2")
    time.sleep(1.5)  # Explorer 异步转码与重绘
    mtime_after = transcoded_wallpaper_mtime()
    reg_after = {"WallpaperStyle": reg_read_desktop("WallpaperStyle"),
                 "Wallpaper": reg_read_desktop("Wallpaper")}
    result = {
        "mode": "stretch", "monitors": len(monitors),
        "stitch": stitch,
        "reg_before": reg_before, "reg_after": reg_after,
        "spi_readback": spi_get_wallpaper(),
        "transcoded_mtime_advanced": bool(
            mtime_before is not None and mtime_after is not None
            and mtime_after > mtime_before),
        "verify": "not_checked",
    }
    if verify:
        grab = grab_screen(vx, vy, vw, vh)
        verify_path = os.path.join(data_dir(), "last_apply_grab.png")
        save_png(grab, vw, vh, verify_path)
        result["verify_grab"] = verify_path
        # 生效判定：转码推进 或 注册表落位（抓屏含窗口干扰，采样留人眼复核）
        if result["transcoded_mtime_advanced"] or \
                (reg_after["Wallpaper"] and
                 os.path.abspath(reg_after["Wallpaper"]) ==
                 os.path.abspath(stitch)):
            result["verify"] = "ok"
            result["ok"] = True
        else:
            result["verify"] = "rejected"
            result["ok"] = False
            result["error"] = {"code": "blocked_by_security",
                               "message": "壁纸变更未生效（疑似被终端安全"
                                          "软件拦截）"}
    else:
        result["ok"] = True
    log("壁纸应用: verify=%s monitors=%d stitch=%s"
        % (result["verify"], len(monitors), stitch))
    return result


def show_desktop_toggle():
    """Win+D 最小化/恢复全部窗口（人眼复核辅助，不用于自动判定）。"""
    VK_LWIN, VK_D, KEYUP = 0x5B, 0x44, 0x0002
    user32.keybd_event(VK_LWIN, 0, 0, 0)
    user32.keybd_event(VK_D, 0, 0, 0)
    user32.keybd_event(VK_D, 0, KEYUP, 0)
    user32.keybd_event(VK_LWIN, 0, KEYUP, 0)
    time.sleep(1.2)


# PART2_LOCKSCREEN


# ------------------------------------------------------------- 锁屏 CSP
def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def csp_read():
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CSP_KEY, 0,
                             winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
    except OSError:
        return None
    out = {}
    try:
        for name in CSP_VALUES:
            try:
                out[name] = winreg.QueryValueEx(key, name)[0]
            except OSError:
                out[name] = None
    finally:
        key.Close()
    return out


def csp_write(image_path):
    """写 PersonalizationCSP 三值（需提权 token；否则抛 PermissionError）。"""
    import winreg
    key = winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, CSP_KEY, 0,
                             winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY)
    try:
        winreg.SetValueEx(key, "LockScreenImagePath", 0, winreg.REG_SZ,
                          image_path)
        winreg.SetValueEx(key, "LockScreenImageUrl", 0, winreg.REG_SZ,
                          image_path)
        winreg.SetValueEx(key, "LockScreenStatus", 0, winreg.REG_DWORD, 1)
    finally:
        key.Close()
    return csp_read()


def csp_restore(backup):
    import winreg
    key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CSP_KEY, 0,
                         winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY)
    try:
        for name in CSP_VALUES:
            old = backup.get(name) if backup else None
            if old is None:
                try:
                    winreg.DeleteValue(key, name)
                except OSError:
                    pass
            else:
                kind = (winreg.REG_DWORD
                        if name == "LockScreenStatus" else winreg.REG_SZ)
                winreg.SetValueEx(key, name, 0, kind, old)
    finally:
        key.Close()


def apply_lock_screen(image_path, elevated_helper=None):
    """应用锁屏壁纸。elevated_helper: callable(image_path)->None（提权通道，
    P3 计划任务助手；未提供且非管理员时报 no_admin）。返回 result dict。"""
    result = {"policy": "lock_screen"}
    backup = csp_read()
    if is_admin():
        csp_write(image_path)
    elif elevated_helper:
        elevated_helper(image_path)
    else:
        result.update(ok=False, error={"code": "no_admin",
                                       "message": "锁屏设置需管理员权限"})
        return result
    readback = csp_read()
    ok = bool(readback) and readback.get("LockScreenImagePath") \
        == os.path.abspath(image_path) \
        and readback.get("LockScreenStatus") == 1
    result.update(ok=ok, backup=backup, readback=readback)
    if not ok:
        result["error"] = {"code": "apply_failed",
                           "message": "锁屏注册表写入后读回不一致"}
    log("锁屏应用: ok=%s" % ok)
    return result


# ------------------------------------------------------------- 电源计划
GUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                     r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _run_powercfg(*args):
    proc = subprocess.run(["powercfg", *args], capture_output=True)
    out = proc.stdout or b""
    err = proc.stderr or b""
    text = None
    for enc in ("gbk", "utf-8", "latin-1"):
        try:
            text = out.decode(enc)
            errt = err.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = out.decode("utf-8", "replace")
        errt = err.decode("utf-8", "replace")
    return proc.returncode, text, errt


def get_active_scheme():
    rc, out, err = _run_powercfg("/getactivescheme")
    if rc != 0:
        raise RuntimeError("getactivescheme rc=%d %s" % (rc, err.strip()))
    m = GUID_RE.search(out)
    if not m:
        raise RuntimeError("未能解析活动电源方案 GUID")
    return m.group(0).lower()


def query_snapshot(guid):
    """/q → ({sub:{set:{'ac','dc'}}}, {guid或子组: 别名})。中英文输出兼容。"""
    rc, out, err = _run_powercfg("/q", guid)
    if rc != 0:
        raise RuntimeError("query rc=%d %s" % (rc, err.strip()))
    snap, aliases = {}, {}
    cur_sub = cur_set = None
    pats = {
        "sub": (re.compile(r"子组\s*GUID[:：]\s*([0-9a-fA-F-]{36})"),
                re.compile(r"Subgroup\s+GUID[:：]\s*([0-9a-fA-F-]{36})")),
        "set": (re.compile(r"电源设置\s*GUID[:：]\s*([0-9a-fA-F-]{36})"),
                re.compile(r"Power Setting\s+GUID[:：]\s*([0-9a-fA-F-]{36})")),
        "alias": (re.compile(r"GUID\s*别名[:：]\s*(\S+)"),
                  re.compile(r"GUID\s+Alias[:：]\s*(\S+)")),
        "ac": (re.compile(r"(?:当前交流电源设置索引|Current AC Power Setting "
                          r"Index)[:：]\s*0x([0-9a-fA-F]+)"),),
        "dc": (re.compile(r"(?:当前直流电源设置索引|Current DC Power Setting "
                          r"Index)[:：]\s*0x([0-9a-fA-F]+)"),),
    }
    for line in out.splitlines():
        line = line.strip()
        m = pats["sub"][0].search(line) or pats["sub"][1].search(line)
        if m:
            cur_sub = m.group(1).lower()
            snap.setdefault(cur_sub, {})
            continue
        m = pats["set"][0].search(line) or pats["set"][1].search(line)
        if m:
            cur_set = m.group(1).lower()
            snap.setdefault(cur_sub, {})[cur_set] = {"ac": None, "dc": None}
            continue
        m = pats["alias"][0].search(line) or pats["alias"][1].search(line)
        if m:
            key = cur_set or cur_sub
            if key:
                aliases[key] = m.group(1).upper()
            continue
        m = pats["ac"][0].search(line)
        if m and cur_sub and cur_set:
            snap[cur_sub][cur_set]["ac"] = int(m.group(1), 16)
            continue
        m = pats["dc"][0].search(line)
        if m and cur_sub and cur_set:
            snap[cur_sub][cur_set]["dc"] = int(m.group(1), 16)
    return snap, aliases


def find_setting(snap, aliases, setting_alias):
    for sg, settings in snap.items():
        for st in settings:
            if aliases.get(st) == setting_alias.upper():
                return sg, st
    raise RuntimeError("快照中未找到 %s" % setting_alias)


def powercfg_set_index(guid, sub, setting, value, channel):
    arg = "/setacvalueindex" if channel == "ac" else "/setdcvalueindex"
    rc, _, err = _run_powercfg(arg, guid, sub, setting, str(value))
    if rc != 0:
        raise RuntimeError("%s rc=%d %s" % (arg, rc, err.strip()))


def powercfg_set_active(guid):
    rc, _, err = _run_powercfg("/setactive", guid)
    if rc != 0:
        raise RuntimeError("setactive rc=%d %s" % (rc, err.strip()))


# PART2_POWER_APPLY


# 电源计划预设（SUB_* 组别名 → GUID；settings 走别名定位）
POWER_PRESETS = {
    "balanced": "381b4222-f694-41f0-9685-ff5bb260df2e",
    "high": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
    "saver": "a1841308-3541-4fab-bc81-f71556f20b4a",
}


def apply_power_plan(policy):
    """应用电源计划策略。policy: {plan: balanced|high|saver|custom, custom:{...}}。
    内置计划直接 /setactive；自定义计划快照备份 → 逐项回写 → 失败自动还原。
    返回 result dict（backup_guid/rollback 语义见 CONTRACT.md）。"""
    result = {"policy": "power_plan"}
    plan = str(policy.get("plan") or "balanced")
    active = get_active_scheme()
    result["backup_guid"] = active
    if plan in POWER_PRESETS:
        powercfg_set_active(POWER_PRESETS[plan])
        result.update(ok=True, plan=plan, rollback="not_needed")
        log("电源计划: 切换内置 %s" % plan)
        return result
    # custom：快照 → 修改
    snap, aliases = query_snapshot(active)
    custom = policy.get("custom") or {}
    mapping = [("display_off", "VIDEOIDLE"), ("sleep", "STANDBYIDLE"),
               ("disk_off", "DISKIDLE")]
    try:
        changed = []
        for key, alias in mapping:
            for ch in ("ac", "dc"):
                val = custom.get("%s_%s" % (key, ch))
                if val is None:
                    continue
                try:
                    sg, st = find_setting(snap, aliases, alias)
                except RuntimeError:
                    continue  # 目标系统无此设置项，跳过并保持快照原值
                powercfg_set_index(active, sg, st, int(val), ch)
                changed.append((sg, st, ch))
        powercfg_set_active(active)
        result.update(ok=True, plan="custom", changed=len(changed),
                      rollback="not_needed")
        log("电源计划: 自定义应用 %d 项" % len(changed))
        return result
    except Exception as exc:
        # 自动还原快照
        try:
            for sg, settings in snap.items():
                for st, vals in settings.items():
                    if vals["ac"] is not None:
                        powercfg_set_index(active, sg, st, vals["ac"], "ac")
                    if vals["dc"] is not None:
                        powercfg_set_index(active, sg, st, vals["dc"], "dc")
            powercfg_set_active(active)
            result["rollback"] = "ok"
            result["error"] = {"code": "rollback_ok",
                               "message": "应用失败已还原（%s）" % exc}
        except Exception as exc2:
            result["rollback"] = "failed"
            result["error"] = {"code": "rollback_failed",
                               "message": "还原失败（%s / %s）" % (exc, exc2)}
        result["ok"] = False
        log("电源计划失败: %r rollback=%s" % (exc, result.get("rollback")),
            "ERROR")
        return result


# ------------------------------------------------------------- 超时锁屏
def apply_idle_lock(policy):
    """超时锁屏：HKCU 屏保三值（等待秒/Secure/启用）+ 可选空闲主动锁屏。
    policy: {minutes, screen_saver_secure}。屏保值以秒存储（minutes*60+15 缓冲）。"""
    import winreg
    result = {"policy": "idle_lock"}
    minutes = int(policy.get("minutes") or 0)
    secure = bool(policy.get("screen_saver_secure", True))
    if minutes <= 0:
        result.update(ok=False, error={"code": "apply_failed",
                                       "message": "无效的锁屏等待分钟数"})
        return result
    timeout_sec = minutes * 60 + 15  # 屏保先行，主动锁屏兜底
    try:
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                 r"Control Panel\Desktop", 0,
                                 winreg.KEY_SET_VALUE)
        try:
            winreg.SetValueEx(key, "ScreenSaveActive", 0, winreg.REG_SZ, "1")
            winreg.SetValueEx(key, "ScreenSaveTimeOut", 0, winreg.REG_SZ,
                              str(timeout_sec))
            winreg.SetValueEx(key, "ScreenSaverIsSecure", 0, winreg.REG_SZ,
                              "1" if secure else "0")
        finally:
            key.Close()
        result.update(ok=True, minutes=minutes, timeout_sec=timeout_sec,
                      secure=secure)
        log("超时锁屏: %d 分钟 secure=%s" % (minutes, secure))
    except OSError as exc:
        result.update(ok=False, error={"code": "apply_failed",
                                       "message": "屏保注册表写入失败"})
        log("超时锁屏失败: %r" % exc, "ERROR")
    return result


def get_last_input_age_sec():
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(lii)
    if not user32.GetLastInputInfo(ctypes.byref(lii)):
        return None
    now = kernel32.GetTickCount()
    elapsed = (now - lii.dwTime) & 0xFFFFFFFF
    return elapsed / 1000.0


def idle_watch_thread(stop_event, minutes, checker=None):
    """空闲 X 分钟主动锁屏（用户会话内线程；checker 供测试注入）。"""
    if checker is None:
        checker = get_last_input_age_sec
    threshold = minutes * 60.0
    while not stop_event.is_set():
        try:
            age = checker()
            if age is not None and age >= threshold:
                log("空闲 %.0f 秒达到阈值，主动锁屏" % age, "INFO")
                user32.LockWorkStation()
                stop_event.wait(300)  # 锁屏后冷却，避免重复触发
                continue
        except Exception as exc:
            log("空闲监视异常: %r" % exc, "WARN")
        stop_event.wait(30)


# PART3_SESSION


# ------------------------------------------------------------- 会话检测
def session_type():
    """console（本机交互）| rdp（远程/非 console）。失败保守归 rdp。"""
    try:
        pid = kernel32.GetCurrentProcessId()
        proc_sid = wt.DWORD()
        kernel32.ProcessIdToSessionId(pid, ctypes.byref(proc_sid))
        # console 会话
        buf = ctypes.c_void_p()
        count = wt.DWORD()
        if wtsapi32.WTSEnumerateSessionsW(WTS_CURRENT_SERVER_HANDLE, 0, 1,
                                          ctypes.byref(buf),
                                          ctypes.byref(count)):
            class WTS_SESSION_INFOW(ctypes.Structure):
                _fields_ = [("SessionId", wt.DWORD),
                            ("pWinStationName", wt.LPWSTR),
                            ("State", wt.DWORD)]
            entries = ctypes.cast(buf, ctypes.POINTER(WTS_SESSION_INFOW))
            console_sid = None
            for i in range(count.value):
                e = entries[i]
                if e.SessionId == 0xFFFFFFFF or \
                        (e.pWinStationName or "") == "Console":
                    console_sid = e.SessionId
            wtsapi32.WTSFreeMemory(buf)
            if console_sid is not None:
                return "console" if proc_sid.value == console_sid else "rdp"
    except Exception:
        pass
    # 降级：GetSystemMetrics(SM_REMOTESESSION=0x1000)
    try:
        return "rdp" if user32.GetSystemMetrics(0x1000) else "console"
    except Exception:
        return "rdp"


# ------------------------------------------------------------- 传输层
class Transport(object):
    """终端↔服务端传输（CONTRACT.md §1）；可注入 mock。"""

    def fetch_policy(self, revision, monitors_brief):
        raise NotImplementedError

    def download_wallpaper(self, wallpaper_id, dest_path, checksum):
        raise NotImplementedError

    def report(self, payload):
        raise NotImplementedError


class PlatformTransport(Transport):
    """平台 HTTPS + X-ETP-Token（语义对齐 uplink）。

    terminal_id 解析顺序（BRG-064 定向，ADR-006）：
    ① uplink_config.json 配置值 → ② 进程内 uplink 内存状态（单进程同源，
    经 handle_uplink_status 公共访问器，含注册成功后的内存 tid）→
    ③ 同源 hostname 兜底（复刻 uplink._default_terminal_id 的 WIN-<host> 规则，
    服务端注册记录默认口径）→ 仍为空则 _request 抛 not_registered（轮询跳过
    并如实上报，不静默失败）。"""

    def __init__(self, config_path=None):
        self.cfg_path = config_path or os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "winhelper", "uplink_config.json")

    def _uplink_tid_inproc(self):
        """进程内直取 uplink 内存 tid（winhelper 单进程内 uplink 线程与引擎同源）；
        框架无关环境（独立单测/E2E）import 失败静默返回 None。"""
        try:
            import uplink
            r = uplink.handle_uplink_status(None) or {}
            u = r.get("uplink") or {}
            return (str(u.get("terminal_id") or "").strip() or None)
        except Exception:
            return None

    def _hostname_tid(self):
        """同源兜底：复刻 uplink._default_terminal_id 规则（服务端注册默认口径）。"""
        try:
            import socket
            host = socket.gethostname() or os.environ.get("COMPUTERNAME") \
                or "UNKNOWN"
        except Exception:
            host = os.environ.get("COMPUTERNAME") or "UNKNOWN"
        import re as _re
        return "WIN-" + _re.sub(r"[^A-Za-z0-9_.\-]", "_", host).strip("_")[:40]

    def _cfg(self):
        base = token = tid = ""
        try:
            with open(self.cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            base = str(cfg.get("server_url") or "")
            token = str(cfg.get("token") or "")
            tid = str(cfg.get("terminal_id") or "").strip()
        except (OSError, ValueError):
            pass
        if not tid:
            tid = self._uplink_tid_inproc() or self._hostname_tid()
        return base, token, tid

    def _request(self, method, path, body=None, binary_dest=None,
                 timeout=30):
        import urllib.request
        base, token, tid = self._cfg()
        if not base or not token or not tid:
            raise RuntimeError("not_registered")
        url = base.rstrip("/") + path.format(tid=tid)
        data = None
        headers = {"X-ETP-Token": token}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
        ctx = _ssl_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            raw = r.read()
        if binary_dest is not None:
            with open(binary_dest, "wb") as f:
                f.write(raw)
            return None
        return json.loads(raw.decode("utf-8"))

    def fetch_policy(self, revision, monitors_brief):
        return self._request(
            "GET", "/api/v1/terminals/{tid}/desktoppolicy/policy"
                   "?revision=%d&mi=%s" % (revision, monitors_brief))

    def download_wallpaper(self, wallpaper_id, dest_path, checksum):
        self._request("GET", "/api/v1/terminals/{tid}/desktoppolicy/wallpaper/"
                      "%s" % wallpaper_id, binary_dest=dest_path, timeout=120)
        if checksum and _sha256_file(dest_path) != checksum:
            try:
                os.remove(dest_path)
            except OSError:
                pass
            raise RuntimeError("file_missing")

    def report(self, payload):
        return self._request("POST",
                             "/api/v1/terminals/{tid}/desktoppolicy/report",
                             body=payload)


def _ssl_context():
    import ssl
    ctx = ssl.create_default_context()
    return ctx


def monitors_brief(monitors):
    return ";".join("%d,%d,%d,%d" % (m["index"], m["width"], m["height"],
                                     1 if m["primary"] else 0)
                    for m in monitors or [])


# PART3_CACHE


# ------------------------------------------------------------- 本地缓存
class StateStore(object):
    """策略缓存（state.json）+ 壁纸文件缓存（cache/，sha256 校验）。"""

    def __init__(self, root=None):
        self.root = root or data_dir()
        self.cache_dir = os.path.join(self.root, "cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.state_path = os.path.join(self.root, "state.json")

    def load_state(self):
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"revision": -1, "policies": None,
                    "last_results": {}, "monitors_sig": ""}

    def save_state(self, state):
        tmp = self.state_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.state_path)
        except OSError as exc:
            log("state 写入失败: %r" % exc, "WARN")

    def wallpaper_path(self, wallpaper_id):
        return os.path.join(self.cache_dir, "%s" % wallpaper_id)

    def wallpaper_cached(self, wallpaper_id, checksum):
        p = self.wallpaper_path(wallpaper_id)
        if not os.path.exists(p):
            return None
        if checksum and _sha256_file(p) != checksum:
            return None
        return p

    def cache_index(self):
        p = os.path.join(self.cache_dir, "index.json")
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}


# ------------------------------------------------------------- 执行编排
class WallpaperFileError(RuntimeError):
    """壁纸文件获取/校验失败（映射 file_missing）。"""


class Engine(object):
    """桌面管控引擎：轮询 → 下载 → 执行 → 自检 → 回传（离线兜底）。"""

    def __init__(self, transport=None, store=None, poll_sec=None,
                 elevated_helper=None, idle_checker=None):
        self.transport = transport or PlatformTransport()
        self.store = store or StateStore()
        self.poll_sec = int(poll_sec or POLL_DEFAULT_SEC)
        self.elevated_helper = elevated_helper
        self.idle_checker = idle_checker
        self.last_poll_error = None      # 最近一次轮询失败原因（如实上报状态）
        self.last_poll_ok_ts = None      # 最近一次轮询成功时间
        self._stop = threading.Event()
        self._threads = []
        self._idle_stop = None
        self._sig_last = None

    # -- 单轮 --
    def tick(self):
        state = self.store.load_state()
        monitors = enum_monitors()
        sig = monitors_signature(monitors)
        sig_changed = (self._sig_last is not None and sig != self._sig_last)
        self._sig_last = sig
        try:
            resp = self.transport.fetch_policy(
                state.get("revision", -1), monitors_brief(monitors))
        except Exception as exc:
            self.last_poll_error = str(exc)
            log("策略拉取失败（离线兜底，保持现有配置）: %r" % exc, "WARN")
            return {"ok": False, "offline": True}
        if not isinstance(resp, dict):
            self.last_poll_error = "policy 响应非对象"
            return {"ok": False, "offline": True}
        self.last_poll_ok_ts = time.time()
        self.last_poll_error = None
        if resp.get("unchanged") and not sig_changed:
            return {"ok": True, "unchanged": True}
        if resp.get("unchanged"):
            # 显示器签名变化且服务端无更新 → 按上次缓存策略重适配（不回退）
            policies = state.get("policies") or {}
            revision = state.get("revision", -1)
        else:
            policies = resp.get("policies") or {}
            revision = int(resp.get("revision") or 0)
        results = self.apply_policies(policies, resp)
        state.update({"revision": revision, "policies": policies,
                      "last_results": results, "monitors_sig": sig,
                      "reported_at": int(time.time())})
        self.store.save_state(state)
        report_payload = {
            "revision": revision,
            "reported_at": int(time.time()),
            "monitors": [{"index": m["index"], "device": m["device"],
                          "width": m["width"], "height": m["height"],
                          "primary": m["primary"],
                          "offset_x": m["left"], "offset_y": m["top"]}
                         for m in monitors],
            "virtual": {"x": 0, "y": 0, "width": 0, "height": 0},
            "session_type": session_type(),
            "results": [results[k] for k in
                        ("desktop_wallpaper", "lock_screen", "power_plan",
                         "idle_lock") if k in results],
        }
        vx, vy, vw, vh = virtual_bounds()
        report_payload["virtual"] = {"x": vx, "y": vy, "width": vw,
                                     "height": vh}
        delivered = self._report_with_retry(report_payload)
        return {"ok": True, "revision": revision, "results": results,
                "reported": delivered}

    def _report_with_retry(self, payload):
        for i, wait in enumerate(BACKOFF_SEC):
            try:
                self.transport.report(payload)
                return True
            except Exception as exc:
                log("结果回传失败(%d/%d): %r" % (i + 1, len(BACKOFF_SEC), exc),
                    "WARN")
                if not self._stop.wait(wait):
                    continue
                break
        return False

    # -- 策略执行 --
    def _wallpaper_files(self, wp_policy):
        """按 per_monitor 下发解析壁纸文件（缓存命中或下载）。返回路径列表。"""
        per = wp_policy.get("per_monitor") or []
        paths = []
        for item in per:
            wid = str(item.get("wallpaper_id") or "")
            checksum = item.get("checksum")
            path = self.store.wallpaper_cached(wid, checksum)
            if not path:
                path = self.store.wallpaper_path(wid)
                for i, wait in enumerate(BACKOFF_SEC):
                    try:
                        self.transport.download_wallpaper(wid, path, checksum)
                        break
                    except Exception as exc:
                        log("壁纸下载失败(%d/%d) id=%s: %r"
                            % (i + 1, len(BACKOFF_SEC), wid, exc), "WARN")
                        if i == len(BACKOFF_SEC) - 1:
                            raise WallpaperFileError("file_missing")
                        self._stop.wait(wait)
            paths.append(path)
        return paths

    def apply_policies(self, policies, resp):
        results = {}
        st = session_type()
        wp = policies.get("desktop_wallpaper") or {}
        if wp.get("enabled"):
            if st != "console":
                results["desktop_wallpaper"] = {
                    "policy": "desktop_wallpaper", "ok": True,
                    "skipped": "rdp_skipped",
                    "error": {"code": "rdp_skipped",
                              "message": "远程会话跳过本地壁纸"}}
            else:
                try:
                    files = self._wallpaper_files(wp)
                    results["desktop_wallpaper"] = apply_wallpaper(files)
                    r = results["desktop_wallpaper"]
                    r["policy"] = "desktop_wallpaper"
                    if not r.get("ok"):
                        for i, wait in enumerate(BACKOFF_SEC):
                            r2 = apply_wallpaper(files)
                            r2["policy"] = "desktop_wallpaper"
                            results["desktop_wallpaper"] = r2
                            if r2.get("ok"):
                                break
                            if i < len(BACKOFF_SEC) - 1:
                                self._stop.wait(wait)
                except WallpaperFileError:
                    results["desktop_wallpaper"] = {
                        "policy": "desktop_wallpaper", "ok": False,
                        "error": {"code": "file_missing",
                                  "message": "壁纸文件获取失败"}}
                except Exception as exc:
                    results["desktop_wallpaper"] = {
                        "policy": "desktop_wallpaper", "ok": False,
                        "error": {"code": "apply_failed",
                                  "message": "壁纸应用失败（%s）" % exc}}
        ls = policies.get("lock_screen") or {}
        if ls.get("enabled"):
            wid = str(ls.get("wallpaper_id") or "")
            try:
                path = self.store.wallpaper_cached(wid, None) or \
                    self.store.wallpaper_path(wid)
                if not os.path.exists(path):
                    self.transport.download_wallpaper(wid, path, None)
                results["lock_screen"] = apply_lock_screen(
                    path, elevated_helper=self.elevated_helper)
            except WallpaperFileError:
                results["lock_screen"] = {
                    "policy": "lock_screen", "ok": False,
                    "error": {"code": "file_missing",
                              "message": "锁屏壁纸获取失败"}}
            except Exception as exc:
                results["lock_screen"] = {
                    "policy": "lock_screen", "ok": False,
                    "error": {"code": "apply_failed",
                              "message": "锁屏设置失败（%s）" % exc}}
        pp = policies.get("power_plan") or {}
        if pp.get("enabled"):
            results["power_plan"] = apply_power_plan(pp)
        il = policies.get("idle_lock") or {}
        if il.get("enabled"):
            results["idle_lock"] = apply_idle_lock(il)
            self._ensure_idle_watch(il)
        else:
            self._stop_idle_watch()
        return results

    # -- 空闲锁屏线程 --
    def _ensure_idle_watch(self, il_policy):
        minutes = int(il_policy.get("minutes") or 0)
        if minutes <= 0:
            return
        if self._idle_stop is not None:
            return
        self._idle_stop = threading.Event()
        t = threading.Thread(target=idle_watch_thread,
                             args=(self._idle_stop, minutes,
                                   self.idle_checker),
                             name="dp-idle-watch", daemon=True)
        t.start()
        self._threads.append(t)

    def _stop_idle_watch(self):
        if self._idle_stop is not None:
            self._idle_stop.set()
            self._idle_stop = None

    # -- 常驻循环 --
    def run_forever(self):
        log("引擎启动（轮询 %ds）" % self.poll_sec)
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                log("轮询异常: %r" % exc, "ERROR")
            jitter = random.uniform(0.85, 1.15)
            self._stop.wait(self.poll_sec * jitter)

    def start(self):
        t = threading.Thread(target=self.run_forever, name="dp-engine",
                             daemon=True)
        t.start()
        self._threads.append(t)
        return t

    def stop(self):
        self._stop.set()
        self._stop_idle_watch()


# PART3_END


# ============================================================= 主应用桥接层
# （net_service 同模式：handle_dp_* 即时返回；耗时执行走后台任务 + task-status 轮询）
_DP_TASKS = {}
_DP_TASK_SEQ = [0]
_DP_TASK_LOCK = threading.Lock()
_DP_ENGINE = None
_DP_ENGINE_LOCK = threading.Lock()


def get_engine():
    """引擎单例（主应用启动后首次访问时创建并启动常驻轮询）。"""
    global _DP_ENGINE
    with _DP_ENGINE_LOCK:
        if _DP_ENGINE is None:
            _DP_ENGINE = Engine()
            _DP_ENGINE.start()
            log("桥接层创建引擎单例")
        return _DP_ENGINE


def _dp_start_task(fn):
    """启动一次性后台任务，返回 task_id（结果存 _DP_TASKS）。"""
    with _DP_TASK_LOCK:
        _DP_TASK_SEQ[0] += 1
        task_id = "dp%d_%d" % (int(time.time()), _DP_TASK_SEQ[0])
        _DP_TASKS[task_id] = {"status": "running", "result": None,
                              "started": time.time()}
    def _run():
        try:
            result = fn()
            with _DP_TASK_LOCK:
                _DP_TASKS[task_id].update(status="done", result=result)
        except Exception as exc:
            log("后台任务异常 task=%s: %r" % (task_id, exc), "ERROR")
            with _DP_TASK_LOCK:
                _DP_TASKS[task_id].update(
                    status="error",
                    result={"ok": False,
                            "error": {"code": "apply_failed",
                                      "message": str(exc)}})
    threading.Thread(target=_run, name="dp-task", daemon=True).start()
    return {"success": True, "task_id": task_id}


def handle_dp_status(params):
    """GET /api/desktoppolicy/status：引擎状态 + 显示器布局 + 最近执行结果。"""
    eng = get_engine()
    state = eng.store.load_state()
    monitors = enum_monitors()
    results = state.get("last_results") or {}
    base, _token, tid = eng.transport._cfg() \
        if isinstance(eng.transport, PlatformTransport) else ("", "", None)
    return {
        "success": True,
        "engine_running": True,
        "poll_sec": eng.poll_sec,
        "terminal_id_ready": bool(tid),
        "terminal_id": tid,
        "last_poll_error": eng.last_poll_error,
        "last_poll_ok_ts": eng.last_poll_ok_ts,
        "revision": state.get("revision", -1),
        "reported_at": state.get("reported_at"),
        "session_type": session_type(),
        "monitors": [{"index": m["index"], "width": m["width"],
                      "height": m["height"], "left": m["left"],
                      "top": m["top"], "primary": m["primary"]}
                     for m in monitors],
        "results": {
            "desktop_wallpaper": _summarize(results.get("desktop_wallpaper")),
            "lock_screen": _summarize(results.get("lock_screen")),
            "power_plan": _summarize(results.get("power_plan")),
            "idle_lock": _summarize(results.get("idle_lock")),
        },
        "has_cached_policy": bool(state.get("policies")),
    }


def _summarize(r):
    if not r:
        return {"state": "never"}
    if r.get("error"):
        return {"state": "error", "code": r["error"].get("code"),
                "message": r["error"].get("message", "")}
    if r.get("skipped"):
        return {"state": "skipped", "code": r["skipped"]}
    return {"state": "ok", "detail": r}


def handle_dp_policy_now(params):
    """POST /api/desktoppolicy/policy-now：立即拉取策略并应用（后台任务）。"""
    eng = get_engine()
    return _dp_start_task(eng.tick)


def handle_dp_apply_now(params):
    """POST /api/desktoppolicy/apply-now：离线重应用当前缓存策略（不联网）。"""
    eng = get_engine()
    def _run():
        state = eng.store.load_state()
        policies = state.get("policies")
        if not policies:
            return {"ok": False, "error": {"code": "apply_failed",
                                           "message": "暂无已缓存策略"}}
        results = eng.apply_policies(policies, None)
        state["last_results"] = results
        state["reported_at"] = int(time.time())
        eng.store.save_state(state)
        return {"ok": True, "results": results}
    return _dp_start_task(_run)


def handle_dp_task_status(params):
    """GET /api/desktoppolicy/task-status?task_id=：后台任务结果轮询。"""
    task_id = str(params.get("task_id") or "")
    with _DP_TASK_LOCK:
        task = _DP_TASKS.get(task_id)
        if task is None:
            return {"success": False, "error": "任务不存在"}
        out = {"success": True, "status": task["status"],
               "result": task["result"]}
    # 任务清理：done/error 且发起超 600s（10 分钟）后移除（BRG-062：注释与代码对齐）
    with _DP_TASK_LOCK:
        stale = [k for k, v in _DP_TASKS.items()
                 if v["status"] in ("done", "error")
                 and time.time() - v["started"] > 600]
        for k in stale:
            _DP_TASKS.pop(k, None)
    return out


def handle_dp_logs(params):
    """GET /api/desktoppolicy/logs：日志目录与最近日志尾部。"""
    d = os.path.join(data_dir(), "logs")
    tail = []
    path = os.path.join(d, "dp_%s.log" % time.strftime("%Y%m%d"))
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            tail = [l.rstrip("\n") for l in lines[-60:]]
        except OSError:
            pass
    return {"success": True, "log_dir": d, "log_file": path,
            "exists": os.path.exists(path), "tail": tail}

