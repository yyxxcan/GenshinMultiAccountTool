#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GenshinAutoTool 双版本打包脚本"""

import shutil, os, sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
TEMPLATE_PATH = SCRIPT_DIR / "config_template.json"
DIST_DIR = SCRIPT_DIR / "dist"
BUILD_DIR = SCRIPT_DIR / "build"

# 保留当前原始配置的备份
ORIGINAL_CONFIG = CONFIG_PATH.read_text(encoding="utf-8") if CONFIG_PATH.exists() else "{}"

def run(cmd):
    print(f"\n>>> {cmd}")
    ret = os.system(cmd)
    if ret != 0:
        print(f"[错误] 命令失败，退出码 {ret}")
        sys.exit(1)

def build_version(suffix: str, use_template: bool):
    """打包一个版本"""
    if use_template:
        # 替换为无账号模板
        shutil.copy2(TEMPLATE_PATH, CONFIG_PATH)
        print(f"[信息] 已替换为无账号模板配置")

    exe_name = f"GenshinAutoTool_{suffix}"
    run(f'pyinstaller --onefile --noconsole --name "{exe_name}" main.py')

    exe_path = DIST_DIR / f"{exe_name}.exe"
    if exe_path.exists():
        print(f"[成功] 生成: {exe_path}")
        # 将模板配置文件复制到 dist 同目录供用户参考
        template_dest = DIST_DIR / f"config_template_{suffix}.json"
        shutil.copy2(TEMPLATE_PATH, template_dest)
        print(f"[信息] 模板配置: {template_dest}")
    else:
        print(f"[错误] 未找到输出文件: {exe_path}")
        sys.exit(1)

def main():
    os.chdir(str(SCRIPT_DIR))

    # 清理旧构建产物
    for d in [DIST_DIR, BUILD_DIR]:
        if d.exists():
            shutil.rmtree(d)
            print(f"[信息] 清理: {d}")

    try:
        # 1. 打包带账号版本
        print("\n========== 版本 1: 带账号记录 ==========")
        build_version("WithAccounts", use_template=False)

        # 2. 打包无账号版本
        print("\n========== 版本 2: 无账号记录 ==========")
        build_version("NoAccounts", use_template=True)

    finally:
        # 恢复原始配置
        CONFIG_PATH.write_text(ORIGINAL_CONFIG, encoding="utf-8")
        print(f"\n[信息] 已恢复原始 config.json")

    # 打包完成
    print("\n" + "=" * 50)
    print("打包完成！输出文件:")
    for f in sorted(DIST_DIR.glob("*.exe")):
        print(f"  {f.name}")
    print("=" * 50)

if __name__ == "__main__":
    main()
