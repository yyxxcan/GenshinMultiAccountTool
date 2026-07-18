# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('BetterGI-UID识别脚本', 'BetterGI-UID识别脚本'), ('BetterGI-主界面检测脚本', 'BetterGI-主界面检测脚本'), ('logo.png', '.'), ('icon.ico', '.')]
binaries = []
hiddenimports = ['numpy', 'psutil', 'pyautogui', 'pygetwindow', 'pytesseract', 'uiautomation', 'PIL._imaging', 'PIL._tkinter_finder', 'PIL.Image', 'PIL.ImageDraw', 'websocket', 'requests']
tmp_ret = collect_all('pystray')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='GenshinMultiAccountTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
)
