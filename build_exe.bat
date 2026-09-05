@echo off
REM ==========================================
REM winhelper.exe 一键构建脚本
REM 输出: dist\winhelper.exe (单文件, 双击即用)
REM ==========================================
chcp 65001 >nul
cd /d "%~dp0"

echo [1/3] 检查打包环境...
python -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo      安装 pyinstaller...
    python -m pip install pyinstaller
)

echo [2/3] 开始打包...
python -m PyInstaller winhelper.spec --noconfirm
if errorlevel 1 (
    echo [X] 打包失败！
    pause
    exit /b 1
)

echo [3/3] 完成！
echo 输出文件: %cd%\dist\winhelper.exe
echo.
echo 提示: 将 dist\winhelper.exe 复制到任意 Windows 终端机即可双击使用，
echo       无需安装 Python、无需配置环境、无需网络。
pause
