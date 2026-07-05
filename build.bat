@echo off
chcp 65001 >nul
echo ========================================
echo   GenshinAutoTool 打包脚本
echo ========================================
echo.

REM 检查 PyInstaller 是否安装
pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo [提示] PyInstaller 未安装，正在安装...
    pip install pyinstaller
    if %errorlevel% neq 0 (
        echo [错误] PyInstaller 安装失败，请手动安装: pip install pyinstaller
        pause
        exit /b 1
    )
)

echo [信息] 正在打包...
pyinstaller GenshinAutoTool.spec

if %errorlevel% equ 0 (
    echo.
    echo ========================================
    echo   打包成功！
    echo   输出文件: dist\GenshinAutoTool.exe
    echo ========================================
) else (
    echo.
    echo [错误] 打包失败，请检查错误信息。
)

pause
（内容由AI生成，仅供参考）
