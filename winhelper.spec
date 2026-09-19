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
    # 前端静态资源随包分发；温度组件（LibreHardwareMonitorLib/HidSharp）随包分发；
    # iperf3 客户端（平台网络测试命令用，uplink 运行时解压至 %TEMP% 执行）随包分发
    datas=[
        ('web', 'web'),
        ('perf-analyzer/libs/*.dll', 'libs'),
        ('perf-analyzer/libs/iperf3/*', 'libs/iperf3'),
        # HTTPS 专项（2026-09-11）：平台自建 CA 随包分发（uplink https 调用验签 +
        # server_ca_fingerprint 双层校验）。资产已是**正式 CA**（CN=EyeTerm Internal CA，
        # SHA256=733c1039…02126010b，与生产 data/certs/ca.crt 一致，有效期至 2036-09-08）；
        # 构建前请运行 python tools/check_ca_asset.py 校验（占位/过期即中止构建）
        ('assets/platform_ca.pem', 'assets'),
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
