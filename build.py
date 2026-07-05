#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GenshinAutoTool 单版本打包脚本"""

import shutil, os, sys, subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DIST_DIR = SCRIPT_DIR / "dist"
BUILD_DIR = SCRIPT_DIR / "build"
EXE_NAME = "原神一条龙"
ARCHIVE_NAME = "GenshinAutoTool"
PACKAGE_DIR = DIST_DIR / ARCHIVE_NAME

def run(cmd_args):
    print(f"\n>>> {' '.join(cmd_args)}")
    subprocess.run(cmd_args, check=True)

def main():
    os.chdir(str(SCRIPT_DIR))

    # 清理旧构建产物（忽略被锁定的文件）
    for d in [DIST_DIR, BUILD_DIR]:
        if d.exists():
            try:
                shutil.rmtree(d)
                print(f"[信息] 清理: {d}")
            except PermissionError as e:
                print(f"[警告] 无法清理 {d}: {e}，跳过")

    # 打包
    print("\n========== 打包 ==========")
    run([sys.executable, "-m", "PyInstaller", "--onefile", "--noconsole", "--name", EXE_NAME, "--add-data", "icon.ico;.", "--collect-all", "pystray", "main.py"])

    exe_src = DIST_DIR / f"{EXE_NAME}.exe"
    if not exe_src.exists():
        print(f"[错误] 未找到输出文件: {exe_src}")
        sys.exit(1)

    # 创建发布目录
    PACKAGE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(exe_src, PACKAGE_DIR / f"{EXE_NAME}.exe")
    print(f"[成功] 复制: {EXE_NAME}.exe")

    # 复制文档
    for doc in ["使用说明.md", "使用说明.txt"]:
        src = SCRIPT_DIR / doc
        if src.exists():
            shutil.copy2(src, PACKAGE_DIR / doc)
            print(f"[信息] 复制文档: {doc}")

    # 复制配置文件
    config = SCRIPT_DIR / "config.json"
    if config.exists():
        shutil.copy2(config, PACKAGE_DIR / "config.json")
        print(f"[信息] 复制配置: config.json")
    else:
        print(f"[警告] 未找到 config.json，请手动放置")

    # 复制 Tesseract OCR 便携版
    tess = SCRIPT_DIR / "tesseract-ocr"
    if tess.is_dir():
        dest = PACKAGE_DIR / "tesseract-ocr"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(tess, dest)
        print(f"[信息] 复制: tesseract-ocr")
    else:
        print(f"[警告] 未找到 tesseract-ocr，请手动放置")

    # 检查并复制 BetterGI-UID识别脚本
    uid_script = SCRIPT_DIR / "BetterGI-UID识别脚本"
    if uid_script.is_dir():
        dest = PACKAGE_DIR / "BetterGI-UID识别脚本"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(uid_script, dest)
        print(f"[信息] 复制: BetterGI-UID识别脚本")
    elif uid_script.is_file():
        shutil.copy2(uid_script, PACKAGE_DIR / "BetterGI-UID识别脚本")
        print(f"[信息] 复制: BetterGI-UID识别脚本")

    # 打包 zip
    archive = DIST_DIR / ARCHIVE_NAME
    shutil.make_archive(str(archive), "zip", DIST_DIR, ARCHIVE_NAME)
    print(f"\n[成功] 压缩包: {archive}.zip")

    print("\n" + "=" * 50)
    print("打包完成!")
    print(f"  {PACKAGE_DIR}")
    print(f"  {archive}.zip")
    print("=" * 50)

if __name__ == "__main__":
    main()