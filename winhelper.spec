# -*- mode: python ; coding: utf-8 -*-
# =====================================================
# PyInstaller 打包配置 — winhelper.exe (C/S 桌面单文件)
# 构建: python -m PyInstaller winhelper.spec --noconfirm
# 输出: dist/winhelper.exe
# =====================================================

a = Analysis(
    ['desktop.py'],
    pathex=[],
    binaries=[],
    # 前端静态资源随包分发；温度组件（LibreHardwareMonitorLib/HidSharp）随包分发
    datas=[
        ('web', 'web'),
        ('perf-analyzer/libs/*.dll', 'libs'),
    ],
    hiddenimports=[
        # Windows 事件日志
        'win32evtlog',
        'win32evtlogutil',
        'pywintypes',
        'win32timezone',
        # pywebview 桌面后端 (WebView2 / WinForms)
        'webview.platforms.edgechromium',
        'webview.platforms.winforms',
        'webview.platforms.cef',
        'clr_loader',
        'clr_loader.netfx',
        'pythonnet',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='winhelper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # GUI程序, 不显示控制台黑窗
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app.ico' if __import__('os').path.exists('app.ico') else None,
)
