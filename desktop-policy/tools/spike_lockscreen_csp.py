# -*- coding: utf-8 -*-
"""P0 spike B：PersonalizationCSP 锁屏壁纸实测（观枢终端平台｜EyeTerm · 桌面管控专项）。

验证点（对应 ADR-002）：
1. 当前进程权限形态实测（IsUserAnAdmin + HKLM 实际可写性）
2. HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\PersonalizationCSP
   三值写入：LockScreenImagePath / LockScreenImageUrl (REG_SZ) +
   LockScreenStatus (REG_DWORD=1)，读回一致断言
3. 原值备份 → 验证后还原（原无键值则删除）
4. 锁屏视觉生效：注册表层面自动判定；界面生效标注需人眼 Win+L 复核
   （--lock 参数可选触发 LockWorkStation 立即锁定）

权限说明：HKLM 写入需管理员。无管理员时执行只读检测并输出提升指引，退出码 2。
用法：
  python tools/spike_lockscreen_csp.py           # 写入→读回→还原（需管理员）
  python tools/spike_lockscreen_csp.py --lock    # 验证后触发锁屏供人眼确认
  python tools/spike_lockscreen_csp.py --keep    # 保留锁屏设置不还原（人眼复核后手动还原）
"""
import argparse
import ctypes
import os
import struct
import sys

import winreg

CSP_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\PersonalizationCSP"
VALUE_IMAGE_PATH = "LockScreenImagePath"
VALUE_IMAGE_URL = "LockScreenImageUrl"
VALUE_STATUS = "LockScreenStatus"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spike_out")
_report_lines = []


def log(msg):
    print(msg)
    _report_lines.append(msg)


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def read_csp_values():
    out = {}
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CSP_KEY, 0,
                             winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
    except OSError:
        return None  # 键不存在
    try:
        for name in (VALUE_IMAGE_PATH, VALUE_IMAGE_URL):
            try:
                out[name], _ = winreg.QueryValueEx(key, name)
            except OSError:
                out[name] = None
        try:
            v, _ = winreg.QueryValueEx(key, VALUE_STATUS)
            out[VALUE_STATUS] = v
        except OSError:
            out[VALUE_STATUS] = None
    finally:
        key.Close()
    return out


def write_csp_values(image_path):
    key = winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, CSP_KEY, 0,
                             winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY)
    try:
        winreg.SetValueEx(key, VALUE_IMAGE_PATH, 0, winreg.REG_SZ, image_path)
        winreg.SetValueEx(key, VALUE_IMAGE_URL, 0, winreg.REG_SZ, image_path)
        winreg.SetValueEx(key, VALUE_STATUS, 0, winreg.REG_DWORD, 1)
    finally:
        key.Close()


def restore_csp_values(backup):
    key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CSP_KEY, 0,
                         winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY)
    try:
        for name in (VALUE_IMAGE_PATH, VALUE_IMAGE_URL, VALUE_STATUS):
            old = backup.get(name) if backup else None
            if old is None:
                try:
                    winreg.DeleteValue(key, name)
                except OSError:
                    pass
            else:
                kind = (winreg.REG_DWORD
                        if name == VALUE_STATUS else winreg.REG_SZ)
                winreg.SetValueEx(key, name, 0, kind, old)
    finally:
        key.Close()


def make_test_image(path, w, h):
    """生成锁屏测试图：深青底 + 白色等宽竖条纹（人眼复核辨识度高）。
    行级操作 + zlib PNG 编码（stdlib）。"""
    import zlib
    color = (24, 96, 110)
    white = (235, 245, 248)
    stripe = max(4, w // 96)
    stride = w * 3
    buf = bytearray(stride * h)
    for y in range(h):
        row = bytearray()
        seg = (bytes(color) * stripe + bytes(white) * stripe)
        full = seg * (w // (2 * stripe)) + bytes(color) * (w % (2 * stripe))
        row = full[:w * 3]
        buf[y * stride:(y + 1) * stride] = row
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += buf[y * stride:(y + 1) * stride]
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
                chunk(b"IDAT", zlib.compress(bytes(raw), 6)) +
                chunk(b"IEND", b""))
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lock", action="store_true",
                    help="验证后触发锁屏（LockWorkStation）供人眼确认")
    ap.add_argument("--keep", action="store_true",
                    help="保留锁屏设置不还原（人工复核后手动还原）")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)

    log("== spike B：PersonalizationCSP 锁屏实测 ==")
    admin = is_admin()
    log("[1] 权限形态: 当前进程 %s（IsUserAnAdmin=%s）"
        % ("管理员" if admin else "普通权限", admin))
    backup = read_csp_values()
    log("[2] 现有 CSP 值: %s" % (backup if backup is not None else "键不存在"))

    if not admin:
        log("结论: FAIL(待复测)——HKLM 写入需管理员，请以管理员身份重跑：")
        log('  Start-Process powershell -Verb RunAs -ArgumentList '
            '"python %s --lock"' % os.path.abspath(__file__))
        _save_report()
        return 2

    all_fails = []
    # 测试图：主屏分辨率，放 ProgramData（SYSTEM/用户均可读）
    try:
        user32 = ctypes.windll.user32
        w = user32.GetSystemMetrics(0)
        h = user32.GetSystemMetrics(1)
        if w <= 0 or h <= 0:
            w, h = 1920, 1080
        img_dir = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                               "EyeTermDesktopPolicy")
        os.makedirs(img_dir, exist_ok=True)
        img_path = make_test_image(os.path.join(img_dir,
                                                "lockscreen_spike.png"),
                                   min(w, 3840), min(h, 2160))
        log("[3] 测试图生成: %s (%dx%d, %.1f KB)"
            % (img_path, w, h, os.path.getsize(img_path) / 1024.0))
    except Exception as exc:
        log("[3] 测试图生成失败: %r" % exc)
        _save_report()
        return 1

    # 写入 → 读回
    try:
        write_csp_values(img_path)
        log("[4] CSP 三值写入成功")
    except OSError as exc:
        log("[4] CSP 写入失败: %r（权限形态=%s）" % (exc, admin))
        _save_report()
        return 1
    readback = read_csp_values()
    ok_path = readback and readback.get(VALUE_IMAGE_PATH) == img_path
    ok_url = readback and readback.get(VALUE_IMAGE_URL) == img_path
    ok_status = readback and readback.get(VALUE_STATUS) == 1
    log("[5] 读回断言: path=%s url=%s status=%s"
        % ("PASS" if ok_path else "FAIL",
           "PASS" if ok_url else "FAIL",
           "PASS" if ok_status else "FAIL"))
    if not (ok_path and ok_url and ok_status):
        all_fails.append("csp_readback")
    else:
        log("    生效机制：下次锁屏（Win+L）界面即显示该图；"
            "界面级生效需人眼复核（spike_out/lockscreen_spike.png 为期望图）")

    # 还原 / 保留
    if args.keep:
        log("[6] --keep：保留 CSP 设置（人工复核后可删除该三值还原）")
    else:
        try:
            restore_csp_values(backup if backup is not None else {})
            after = read_csp_values()
            # 干净判定：键不存在 / 空字典 / 三值均无（键壳残留亦视为干净）
            if backup is None:
                clean = (not after or all(v is None for v in after.values()))
            else:
                clean = (after == backup)
            log("[6] 现场还原: %s（还原后=%s）"
                % ("PASS" if clean else "FAIL", after))
            if not clean:
                all_fails.append("restore")
        except OSError as exc:
            log("[6] 还原失败: %r" % exc)
            all_fails.append("restore_exc")

    if args.lock and not args.keep:
        log("[7] 触发锁屏（LockWorkStation）——解锁回来查看报告")
        ctypes.windll.user32.LockWorkStation()

    log("== spike B 结果: %s ==" % ("FAIL" if all_fails else "PASS"))
    _save_report()
    return 1 if all_fails else 0


def _save_report():
    with open(os.path.join(OUT_DIR, "spikeB_report.txt"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(_report_lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
