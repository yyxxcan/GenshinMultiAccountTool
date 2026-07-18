#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GenshinMultiAccountTool v5.5 - 原神多账号辅助工具 v5.5 (动态账号版)
============================================================
- 天空蓝简约 GUI
- 动态添加/删除账号，支持胡桃工具箱账号
- BetterGI 一条龙配置自动发现
- 自动处理 ESC 阻塞
- 实时日志 + 进度 + 即停即止
"""

import os, sys, json, re, time, glob, queue, tempfile, threading, subprocess, ctypes
from ctypes import wintypes
from datetime import datetime, timedelta
from pathlib import Path

import psutil

try:
    import pygetwindow as gw
    HAS_GW = True
except ImportError:
    HAS_GW = False

try:
    import pyautogui
    HAS_PA = True
except ImportError:
    HAS_PA = False

try:
    import uiautomation as auto
    HAS_UIA = True
except ImportError:
    HAS_UIA = False

import numpy as np

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, scrolledtext

try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

# ============================================================
# 路径常量
# ============================================================
SCRIPT_DIR = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
# 嵌入了资源的 PyInstaller 打包：资源文件在临时解压目录 sys._MEIPASS 中
RESOURCE_DIR = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else SCRIPT_DIR
CONFIG_PATH = SCRIPT_DIR / "config.json"
SCHEDULER_CONFIG_PATH = SCRIPT_DIR / "scheduler_config.json"
CHECKIN_SCHEDULE_PATH = SCRIPT_DIR / "checkin_config.json"
TEYVAT_GUIDE_APP_ID_DEFAULT = "27581BTMuli.tauri-genshin_t86f1j5fs8b3t!TEYVATGUIDE"


def get_teyvatguide_app_id():
    """读取 TeyvatGuide 的 AppUserModelId，优先取 config 中的配置，否则用内置默认值"""
    cfg = load_config()
    return cfg.get("teyvatguide", {}).get("app_id", "") or TEYVAT_GUIDE_APP_ID_DEFAULT
LOGS_DIR = SCRIPT_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# 以下路径不再硬编码，由 discover_xxx 函数从 config.json 的 bettergi.config 动态推导
# BETTERGI_USER_DIR = os.path.dirname(cfg["bettergi"]["config"])
# BETTERGI_ONEDRAGON_DIR = os.path.join(BETTERGI_USER_DIR, "OneDragon")

# ============================================================
# Win32 全局热键基础设施（RegisterHotKey + 后台消息泵）
# 使用系统级热键，确保窗口最小化/后台/托盘时均有效
# ============================================================
import ctypes
from ctypes import wintypes

# Win32 常量
_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_WIN = 0x0008
_WM_HOTKEY = 0x0312

# 虚拟键码映射（来自 keyboard 库的键名 → VK_CODE）
_KEY_TO_VK = {
    # 字母键
    **{chr(i): i - 32 for i in range(ord('A'), ord('Z') + 1)},  # 'a'→VK_A(0x41)
    **{chr(i): i - 32 for i in range(ord('a'), ord('z') + 1)},
    # 数字键
    **{str(i): 0x30 + i for i in range(10)},
    # 功能键
    **{f"f{i}": 0x6F + i for i in range(1, 25)},
    # 特殊键
    "space": 0x20, "spacebar": 0x20,
    "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "backspace": 0x08, "esc": 0x1B, "escape": 0x1B,
    "delete": 0x2E, "del": 0x2E,
    "insert": 0x2D, "ins": 0x2D,
    "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pgup": 0x21,
    "pagedown": 0x22, "pgdn": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "printscreen": 0x2C, "prtsc": 0x2C,
    "pause": 0x13, "scrolllock": 0x91,
    "numlock": 0x90, "capslock": 0x14,
    "apps": 0x5D, "menu": 0x5D,
    # 符号键
    "`": 0xC0, "~": 0xC0, "-": 0xBD, "_": 0xBD,
    "=": 0xBB, "+": 0xBB, "[": 0xDB, "{": 0xDB,
    "]": 0xDD, "}": 0xDD, "\\": 0xDC, "|": 0xDC,
    ";": 0xBA, ":": 0xBA, "'": 0xDE, '"': 0xDE,
    ",": 0xBC, "<": 0xBC, ".": 0xBE, ">": 0xBE,
    "/": 0xBF, "?": 0xBF,
    # 小键盘
    "numpad0": 0x60, "numpad1": 0x61, "numpad2": 0x62, "numpad3": 0x63,
    "numpad4": 0x64, "numpad5": 0x65, "numpad6": 0x66, "numpad7": 0x67,
    "numpad8": 0x68, "numpad9": 0x69,
    "numpad*": 0x6A, "numpad+": 0x6B,
    "numpad-": 0x6D, "numpad.": 0x6E, "numpad/": 0x6F,
    "multiply": 0x6A, "add": 0x6B, "subtract": 0x6D, "decimal": 0x6E, "divide": 0x6F,
}

def _parse_hotkey_str(hotkey_str):
    """解析 'ctrl+shift+q' → (modifiers_bitmask, vk_code)
    
    返回 (mods, vk)，若字符串为空或无法解析则返回 (None, None)。
    mods 为 MOD_CONTROL|MOD_SHIFT|MOD_ALT|MOD_WIN 的组合。
    """
    if not hotkey_str or not hotkey_str.strip():
        return None, None
    
    parts = [p.strip().lower() for p in hotkey_str.split("+")]
    mods = 0
    main_key = None
    
    for p in parts:
        if not p:
            continue
        if p in ("ctrl", "control"):
            mods |= _MOD_CONTROL
        elif p in ("shift",):
            mods |= _MOD_SHIFT
        elif p in ("alt",):
            mods |= _MOD_ALT
        elif p in ("win", "windows", "cmd", "command"):
            mods |= _MOD_WIN
        else:
            main_key = p
    
    if main_key is None:
        return None, None
    
    vk = _KEY_TO_VK.get(main_key)
    if vk is None and len(main_key) == 1:
        # 单字符兜底：用 ord 大写
        vk = ord(main_key.upper())
    
    if vk is None:
        return None, None
    
    return mods, vk


def _vk_to_keyname(vk_code):
    """将虚拟键码转为可读键名（反向映射）"""
    for name, vk in _KEY_TO_VK.items():
        if vk == vk_code:
            # 优先返回简短名称
            if len(name) <= 2:
                return name.upper() if len(name) == 1 else name
            return name
    return f"VK_{vk_code}"


def _mods_to_str(mods):
    """将修饰符位掩码转为 'ctrl+shift+alt' 格式"""
    parts = []
    if mods & _MOD_CONTROL:
        parts.append("ctrl")
    if mods & _MOD_SHIFT:
        parts.append("shift")
    if mods & _MOD_ALT:
        parts.append("alt")
    if mods & _MOD_WIN:
        parts.append("win")
    return "+".join(parts)

# 全局托盘引用，供 atexit 兜底清理
_global_tray = None

GLOBAL_CLEANUP_TARGETS = [
    "BetterGI.exe",
    "GenshinImpact.exe",
    "YuanShen.exe",
    "Snap.Hutao.Remastered.FullTrust.exe",
]

# ============================================================
# 配色方案 - 天空蓝
# ============================================================
COLORS = {
    "bg":           "#F0F6FC",
    "panel_bg":     "#FFFFFF",
    "primary":      "#5B9BD5",
    "primary_hover":"#4A8BC5",
    "primary_dark": "#3A7CC3",
    "accent":       "#5B9BD5",
    "success":      "#52C41A",
    "danger":       "#E74C3C",
    "warning":      "#F39C12",
    "text":         "#2C3E50",
    "text_light":   "#7F8C8D",
    "text_white":   "#FFFFFF",
    "log_bg":       "#F7FAFD",
    "log_fg":       "#4A6A8A",
    "log_accent":   "#89B4FA",
    "border":       "#D5E3F0",
    "sel_bg":       "#EBF3FA",
}

# ============================================================
# 工具函数
# ============================================================

_config_cache = None
_config_cache_mtime = 0

def load_config():
    global _config_cache, _config_cache_mtime
    try:
        mtime = os.path.getmtime(CONFIG_PATH)
    except OSError:
        mtime = 0
    if _config_cache is not None and mtime == _config_cache_mtime:
        return json.loads(json.dumps(_config_cache))

    defaults = {
        "accounts": [],
        "bettergi": {
            "exe": "",
            "config": "",
        },
        "snap_hutao": {
            "exe": "",
            "app_id": "E8B6E2B3-D2A0-4435-A81D-2A16AAF405C8_k3erpsn8bwzzy!App",
        },
        "genshin": {
            "exe": "",
            "process_name": "YuanShen.exe",
        },
        "monitor": {
            "max_wait_seconds": 7200,
        },
        "tesseract": {
            "path": "",
        },
        "hotkeys": {
            "stop": "ctrl+shift+q",
            "pause": "ctrl+shift+p",
            "start": "",
        },
        "uid": {
            "method": "tesseract",
            "bettergi_group": "",
            "main_world_detect_group": "",
        },
        "settings": {
            "auto_minimize": True,
            "minimize_on_close": True,
            "auto_shutdown": False,
            "launch_apps_enabled": False,
            "launch_apps_after_all": [],
            "stop_closes_all_processes": True,
            "checkin_close_app": False,
            "close_teyvatguide": False,
            "close_teyvatguide_after_all": True,
        },
        "tg_checkin_before_all": False,
        "checkin_method": "teyvatguide",
        "checkin_hutao_accounts": [],
    }

    if CONFIG_PATH.is_file():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
        # 深度合并：用户配置覆盖默认值，缺失键补默认
        for key, default_val in defaults.items():
            if key not in user_cfg:
                user_cfg[key] = default_val
            elif isinstance(default_val, dict) and isinstance(user_cfg.get(key), dict):
                for sub_key, sub_val in default_val.items():
                    if sub_key not in user_cfg[key]:
                        user_cfg[key][sub_key] = sub_val
        _config_cache = json.loads(json.dumps(user_cfg))
        _config_cache_mtime = mtime
        return user_cfg
    _config_cache = json.loads(json.dumps(defaults))
    _config_cache_mtime = mtime
    return defaults


def save_config(cfg):
    global _config_cache, _config_cache_mtime
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    try:
        _config_cache = json.loads(json.dumps(cfg))
        _config_cache_mtime = os.path.getmtime(CONFIG_PATH)
    except OSError:
        _config_cache_mtime = 0


# ============================================================
# 定时器配置管理
# ============================================================
_scheduler_cache = None
_scheduler_cache_mtime = 0

def load_scheduler_config():
    global _scheduler_cache, _scheduler_cache_mtime
    try:
        mtime = os.path.getmtime(SCHEDULER_CONFIG_PATH)
    except OSError:
        mtime = 0
    if _scheduler_cache is not None and mtime == _scheduler_cache_mtime:
        return json.loads(json.dumps(_scheduler_cache))

    defaults = {
        "schedules": []
    }
    if SCHEDULER_CONFIG_PATH.is_file():
        with open(SCHEDULER_CONFIG_PATH, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
        for key, default_val in defaults.items():
            if key not in user_cfg:
                user_cfg[key] = default_val
        _scheduler_cache = json.loads(json.dumps(user_cfg))
        _scheduler_cache_mtime = mtime
        return user_cfg
    _scheduler_cache = json.loads(json.dumps(defaults))
    _scheduler_cache_mtime = mtime
    return defaults


def save_scheduler_config(cfg):
    with open(SCHEDULER_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def load_checkin_schedule():
    """加载定时签到配置。返回 {"checkins": [], "enabled": False}"""
    if not os.path.exists(CHECKIN_SCHEDULE_PATH):
        return {"checkins": [], "enabled": False}
    try:
        with open(CHECKIN_SCHEDULE_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    cfg.setdefault("checkins", [])
    cfg.setdefault("enabled", False)
    return cfg


def save_checkin_schedule(cfg):
    """保存定时签到配置"""
    with open(CHECKIN_SCHEDULE_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---- 开机自启动 ----
AUTOSTART_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_VALUE_NAME = "GenshinMultiAccountTool"


def _get_autostart_cmd():
    """获取开机自启动命令行"""
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}" --auto-start-scheduler'
    pythonw = sys.executable.replace("python.exe", "pythonw.exe")
    return f'"{pythonw}" "{SCRIPT_DIR / "main.py"}" --auto-start-scheduler'


def is_autostart_enabled():
    """检查注册表中是否已设置开机自启动"""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY, 0, winreg.KEY_READ)
        val, _ = winreg.QueryValueEx(key, AUTOSTART_VALUE_NAME)
        winreg.CloseKey(key)
        return val == _get_autostart_cmd()
    except (FileNotFoundError, OSError):
        return False


def enable_autostart():
    """写入注册表实现开机自启动"""
    import winreg
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY, 0, winreg.KEY_SET_VALUE)
    winreg.SetValueEx(key, AUTOSTART_VALUE_NAME, 0, winreg.REG_SZ, _get_autostart_cmd())
    winreg.CloseKey(key)


def disable_autostart():
    """从注册表移除开机自启动"""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, AUTOSTART_VALUE_NAME)
        winreg.CloseKey(key)
    except (FileNotFoundError, OSError):
        pass


def _get_bettergi_user_dir():
    """从 config.json 的 bettergi.config 路径推导 BetterGI User 目录"""
    cfg = load_config()
    bc = cfg.get("bettergi", {}).get("config", "")
    if not bc or bc.startswith("请选择"):
        return ""
    d = os.path.dirname(bc)
    return d if os.path.isdir(d) else ""


def _get_bettergi_log_dir():
    """从 config.json 的 bettergi 路径推导 BetterGI 日志目录"""
    cfg = load_config()
    gi_exe = cfg.get("bettergi", {}).get("exe", "")
    if not gi_exe or not os.path.isfile(gi_exe):
        return ""
    log_dir = os.path.join(os.path.dirname(gi_exe), "log")
    return log_dir if os.path.isdir(log_dir) else ""


def discover_onedragon_configs():
    """扫描 OneDragon 目录，返回可用配置名列表"""
    configs = []
    user_dir = _get_bettergi_user_dir()
    if user_dir:
        od_dir = os.path.join(user_dir, "OneDragon")
        if os.path.isdir(od_dir):
            for f in glob.glob(os.path.join(od_dir, "*.json")):
                configs.append(os.path.splitext(os.path.basename(f))[0])
    return sorted(configs)


def discover_scheduler_groups():
    """扫描 BetterGI ScriptGroup 目录，返回可用配置组名列表"""
    user_dir = _get_bettergi_user_dir()
    if not user_dir:
        return []
    groups_dir = os.path.join(user_dir, "ScriptGroup")
    groups = []
    if os.path.isdir(groups_dir):
        for f in glob.glob(os.path.join(groups_dir, "*.json")):
            groups.append(os.path.splitext(os.path.basename(f))[0])
    return sorted(groups)


_proc_cache = {}
_proc_cache_time = 0

def find_proc(name):
    global _proc_cache, _proc_cache_time
    now = time.time()
    if now - _proc_cache_time < 1.0:
        return _proc_cache.get(name.lower(), None)
    _proc_cache_time = now
    _proc_cache = {}
    t = name.lower()
    result = None
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if p.info["name"] and p.info["name"].lower() == t:
                result = p
                break
        except Exception:
            pass
    _proc_cache[t] = result
    return result


def kill_proc(name, graceful=True, timeout=10):
    procs = []
    t = name.lower()
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if p.info["name"] and p.info["name"].lower() == t:
                procs.append(p)
        except Exception:
            pass
    if not procs:
        return True
    for p in procs:
        try:
            p.terminate() if graceful else p.kill()
        except Exception:
            pass
    if graceful:
        try:
            psutil.wait_procs(procs, timeout=timeout)
        except Exception:
            pass
        for p in procs:
            try:
                if p.is_running():
                    p.kill()
            except Exception:
                pass
    time.sleep(2)
    return find_proc(name) is None


def cleanup_all(log_func, stop_event=None):
    """全局清理：无条件关闭所有相关进程。
    stop_event: 可选 threading.Event，设置后中断后续清理并返回 False"""
    log_func("全局清理：关闭相关进程...")
    for n in GLOBAL_CLEANUP_TARGETS:
        if stop_event and stop_event.is_set():
            log_func("用户停止，跳过后续清理")
            return False
        log_func(f"  关闭 {n}...")
        kill_proc(n, graceful=True)
    # 等待每个进程退出（每进程最多 2 秒）
    for n in GLOBAL_CLEANUP_TARGETS:
        t0 = time.time()
        while time.time() - t0 < 2:
            if not find_proc(n):
                break
            time.sleep(0.3)
    ok = True
    for n in GLOBAL_CLEANUP_TARGETS:
        if find_proc(n):
            log_func(f"  [!] {n} 仍在运行")
            ok = False
    if ok:
        log_func("全局清理完成")
        log_func("刷新系统托盘...")
        refresh_system_tray()
        log_func("托盘刷新完成")
    return ok


def refresh_system_tray():
    """清除系统托盘残留图标（已关闭进程的僵尸图标）。
    通过 SendMessage 发送 WM_MOUSEMOVE 到托盘窗口，不移动物理光标。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    WM_MOUSEMOVE = 0x0200

    # 定位托盘图标工具栏
    hwnd = user32.FindWindowW("Shell_TrayWnd", None)
    if not hwnd:
        return
    hwnd = user32.FindWindowExW(hwnd, 0, "TrayNotifyWnd", None)
    if not hwnd:
        return
    hwnd = user32.FindWindowExW(hwnd, 0, "SysPager", None)
    if not hwnd:
        return
    hwnd = user32.FindWindowExW(hwnd, 0, "ToolbarWindow32", None)
    if not hwnd:
        return

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    rw = rect.right - rect.left
    rh = rect.bottom - rect.top

    # SendMessage WM_MOUSEMOVE 扫过托盘工具栏，触发 Windows 检查图标有效性
    for x in range(0, rw, 4):
        lparam = ((rh // 2) << 16) | (x & 0xFFFF)
        user32.SendMessageW(hwnd, WM_MOUSEMOVE, 0, lparam)
    # 再反向扫一次确保所有图标都被命中
    for x in range(rw - 1, -1, -4):
        lparam = ((rh // 2) << 16) | (x & 0xFFFF)
        user32.SendMessageW(hwnd, WM_MOUSEMOVE, 0, lparam)

    time.sleep(0.1)


def _cleanup_tray_on_exit():
    """atexit 兜底：程序异常退出时释放托盘对象并清除残留。"""
    global _global_tray
    try:
        if _global_tray is not None:
            _global_tray.stop()
            _global_tray = None
    except Exception:
        pass
    try:
        refresh_system_tray()
    except Exception:
        pass


def modify_bettergi_config(config_path, target_name):
    with open(config_path, "r", encoding="utf-8") as f:
        d = json.load(f)
    old = d.get("selectedOneDragonFlowConfigName", "")
    d["selectedOneDragonFlowConfigName"] = target_name
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return old


def start_bettergi(gi_exe):
    """启动 BetterGI 并点击「启动」按钮（自动登录原神，不执行一条龙）。"""
    if not os.path.isfile(gi_exe):
        return None
    d = os.path.dirname(gi_exe)
    p = subprocess.Popen(
        [gi_exe, "start"],
        cwd=d, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return p.pid


def start_bettergi_onedragon(gi_exe):
    """启动 BetterGI 并立即执行一条龙。"""
    if not os.path.isfile(gi_exe):
        return None
    d = os.path.dirname(gi_exe)
    p = subprocess.Popen(
        [gi_exe, "-startOneDragon"],
        cwd=d, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return p.pid


def start_exe(path):
    if not os.path.isfile(path):
        return None
    d = os.path.dirname(path)
    return subprocess.Popen(
        [path], cwd=d, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def start_msix_app(app_id):
    """通过 shell:AppsFolder 启动 MSIX 打包应用"""
    try:
        subprocess.Popen(
            ["powershell", "-Command",
             f"Start-Process 'shell:AppsFolder\\{app_id}'"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except Exception as e:
        print(f"[!] MSIX 启动失败: {e}")
        return False


# ==================== 共享窗口遍历辅助函数 ====================
def _walk_top_windows():
    """生成器：遍历所有顶层窗口，yield hwnd。避免各处重复 _enum_callback 样板。"""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    hwnds = []

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    def _callback(hwnd, _lparam):
        hwnds.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(_callback), 0)
    for hwnd in hwnds:
        yield hwnd


def _get_window_text(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, 255)
    return buf.value


def _get_class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(hwnd, buf, 255)
    return buf.value


def _is_window_visible(hwnd):
    return ctypes.windll.user32.IsWindowVisible(hwnd)


def _get_proc_name_by_hwnd(hwnd):
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    hProc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not hProc:
        return ""
    buf = ctypes.create_unicode_buffer(260)
    kernel32.QueryFullProcessImageNameW(hProc, 0, buf, ctypes.byref(ctypes.c_ulong(260)))
    kernel32.CloseHandle(hProc)
    return buf.value.rsplit("\\", 1)[-1]
# ==================== 共享窗口遍历辅助函数 结束 ====================


def activate_hutao_window():
    """激活胡桃窗口（MSIX 应用启动后默认不可见），用 EnumWindows 找 WinUIDesktopWin32WindowClass"""
    import ctypes
    user32 = ctypes.windll.user32
    results = []

    for hwnd in _walk_top_windows():
        try:
            title = _get_window_text(hwnd)
            if not title or "胡桃" not in title:
                continue
            if _get_class_name(hwnd) != "WinUIDesktopWin32WindowClass":
                continue
            results.append(hwnd)
        except Exception:
            pass

    for hwnd in results:
        if user32.IsWindow(hwnd):
            SW_RESTORE = 9
            user32.ShowWindow(hwnd, SW_RESTORE)
            time.sleep(0.5)
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.5)
            user32.BringWindowToTop(hwnd)
            return hwnd
    return None


def find_and_activate_window_by_title(title_keyword):
    """按标题关键词查找窗口，找到后 ShowWindow + SetForeground + BringWindowToTop。
    返回 hwnd 或 None。"""
    import ctypes
    user32 = ctypes.windll.user32
    results = []

    for hwnd in _walk_top_windows():
        if title_keyword in _get_window_text(hwnd):
            results.append(hwnd)

    for hwnd in results:
        if user32.IsWindow(hwnd):
            SW_RESTORE = 9
            user32.ShowWindow(hwnd, SW_RESTORE)
            time.sleep(0.3)
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.3)
            user32.BringWindowToTop(hwnd)
            return hwnd
    return None


def find_and_activate_window_by_process(proc_name):
    """按进程名查找顶层可见窗口，找到后 ShowWindow + SetForeground + BringWindowToTop。
    返回 hwnd 或 None。适用于窗口标题不包含进程关键字的 Tauri 等应用。"""
    import ctypes
    user32 = ctypes.windll.user32
    results = []

    for hwnd in _walk_top_windows():
        try:
            if not _is_window_visible(hwnd):
                continue
            exe_name = _get_proc_name_by_hwnd(hwnd)
            if exe_name.lower() == proc_name.lower():
                results.append(hwnd)
        except Exception:
            pass

    for hwnd in results:
        if user32.IsWindow(hwnd):
            SW_RESTORE = 9
            user32.ShowWindow(hwnd, SW_RESTORE)
            time.sleep(0.3)
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.3)
            user32.BringWindowToTop(hwnd)
            return hwnd
    return None


def minimize_window_by_title(title_keyword):
    """按标题关键词查找窗口，用 WM_SYSCOMMAND/SC_MINIMIZE 最小化
    （ShowWindow 对全屏游戏无效），失败则回退到 Win+D 显示桌面。
    返回 hwnd 或 None。"""
    import ctypes
    user32 = ctypes.windll.user32
    results = []

    for hwnd in _walk_top_windows():
        if title_keyword in _get_window_text(hwnd):
            results.append(hwnd)

    for hwnd in results:
        if user32.IsWindow(hwnd):
            WM_SYSCOMMAND = 0x0112
            SC_MINIMIZE = 0xF020
            user32.SendMessageW(hwnd, WM_SYSCOMMAND, SC_MINIMIZE, 0)
            time.sleep(0.5)
            if user32.IsIconic(hwnd):
                return hwnd
            break

    VK_LWIN = 0x5B
    VK_D = 0x44
    KEYEVENTF_KEYUP = 0x0002
    user32.keybd_event(VK_LWIN, 0, 0, 0)
    user32.keybd_event(VK_D, 0, 0, 0)
    time.sleep(0.3)
    user32.keybd_event(VK_D, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_LWIN, 0, KEYEVENTF_KEYUP, 0)
    return results[0] if results else None


def minimize_window_by_process(proc_name):
    """按进程名查找顶层可见窗口，用 WM_SYSCOMMAND/SC_MINIMIZE 最小化。
    适用于窗口标题不包含进程关键字的 Tauri 等应用。返回 hwnd 或 None。"""
    import ctypes
    user32 = ctypes.windll.user32
    results = []

    for hwnd in _walk_top_windows():
        try:
            if not _is_window_visible(hwnd):
                continue
            exe_name = _get_proc_name_by_hwnd(hwnd)
            if exe_name.lower() == proc_name.lower():
                results.append(hwnd)
        except Exception:
            pass

    for hwnd in results:
        if user32.IsWindow(hwnd):
            WM_SYSCOMMAND = 0x0112
            SC_MINIMIZE = 0xF020
            user32.SendMessageW(hwnd, WM_SYSCOMMAND, SC_MINIMIZE, 0)
            time.sleep(0.5)
            if user32.IsIconic(hwnd):
                return hwnd
            break

    return results[0] if results else None


def minimize_genshin_window():
    """最小化原神游戏窗口，返回是否成功。"""
    for pat in ["原神", "YuanShen", "Genshin Impact"]:
        hwnd = minimize_window_by_title(pat)
        if hwnd:
            return True
    return False


def send_esc_to_genshin():
    """发送 ESC 按键到原神窗口"""
    if not HAS_GW or not HAS_PA:
        return
    for pat in ["原神", "YuanShen", "Genshin Impact"]:
        ws = gw.getWindowsWithTitle(pat)
        if ws:
            try:
                ws[0].activate()
                time.sleep(0.3)
            except Exception:
                pass
            break
    try:
        pyautogui.press("esc")
    except Exception:
        pass


def monitor_bettergi_log(log_date_str, timeout_sec, log_func, stop_event, genshin_proc=None):
    """
    监控 BetterGI 日志，检测「任务结束」和 ESC 阻塞。
    一条龙任务包含多个子任务（邮件→脚本→追踪），每个子任务都会写"任务结束"。
    当检测到「一条龙和配置组任务结束」紧接着「任务结束」时，判定整条龙真正完成。
    此外，如果 BetterGI 关闭了原神（进程消失），也视为一条龙完成。
    """
    log_dir = _get_bettergi_log_dir()
    log_path = os.path.join(log_dir, f"better-genshin-impact{log_date_str}.log") if log_dir else ""
    log_func(f"监控日志: {log_path}")
    start_t = time.time()

    while not os.path.isfile(log_path):
        if time.time() - start_t > 30 or stop_event.is_set():
            return False
        time.sleep(2)

    try:
        f = open(log_path, "r", encoding="utf-8", errors="ignore")
        f.seek(0, 2)
    except Exception as e:
        log_func(f"无法打开日志: {e}")
        return False

    esc_cooldown = 0
    onedragon_done_line = False

    try:
        while time.time() - start_t < timeout_sec:
            if stop_event.is_set():
                return False
            time.sleep(3)

            try:
                new = f.read()
            except Exception:
                continue

            # 检测 ESC 阻塞
            if esc_cooldown <= 0 and ("请自行退出当前界面（ESC）" in new or "按ESC" in new):
                log_func("[ESC] 检测到阻塞，自动发送 ESC...")
                send_esc_to_genshin()
                esc_cooldown = 15
                time.sleep(2)

            if esc_cooldown > 0:
                esc_cooldown -= 3

            if not new:
                # 无新日志时检测游戏进程是否已退出（BetterGI 可能关闭了游戏）
                if genshin_proc and not find_proc(genshin_proc):
                    elapsed = int(time.time() - start_t)
                    log_func(f"原神进程已退出，判定一条龙完成 耗时 {elapsed} 秒，5 秒后结束...")
                    stop_event.wait(5)
                    return True
                continue

            # -------- 精确完成判定 --------
            # 检测「一条龙和配置组任务结束」标志位
            if "一条龙和配置组任务结束" in new:
                onedragon_done_line = True
                log_func("检测到一条龙配置组完成标志，等待任务结束...")
                # 同一批日志里紧跟"任务结束" → 立即完成
                if "任务结束" in new:
                    elapsed = int(time.time() - start_t)
                    log_func(f"一条龙任务完成 耗时 {elapsed} 秒，5 秒后结束...")
                    stop_event.wait(5)
                    return True
                continue

            # 如果上轮已看到标志，本轮出现"任务结束" → 立即完成
            if onedragon_done_line and "任务结束" in new:
                elapsed = int(time.time() - start_t)
                log_func(f"一条龙任务完成 耗时 {elapsed} 秒，5 秒后结束...")
                stop_event.wait(5)
                return True

            # -------- 子任务日志（仅供参考，不影响判定） --------
            if "任务结束" in new:
                log_func("检测到子任务结束")
            if "任务启动" in new:
                log_func("检测到新子任务启动")

        log_func(f"任务监控超时（{timeout_sec} 秒）")
        return False
    finally:
        f.close()


def monitor_config_group(log_date_str, group_name, timeout_sec, log_func, stop_event):
    """
    监控 BetterGI 日志，等待指定配置组执行结束。
    检测到 配置组 "xxx" 执行结束 时返回 True。
    仅用于 --startGroups 的单组执行监控，不处理一条龙完成检测。
    """
    log_dir = _get_bettergi_log_dir()
    log_path = os.path.join(log_dir, f"better-genshin-impact{log_date_str}.log") if log_dir else ""
    log_func(f"监控配置组日志: {log_path}")
    start_t = time.time()

    while not os.path.isfile(log_path):
        if time.time() - start_t > 30 or stop_event.is_set():
            return False
        time.sleep(2)

    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            f.seek(0, 2)
            pos = f.tell()
    except Exception as e:
        log_func(f"无法打开日志: {e}")
        return False

    esc_cooldown = 0
    target_line = f'配置组 "{group_name}" 执行结束'

    while time.time() - start_t < timeout_sec:
        if stop_event.is_set():
            return False
        time.sleep(3)

        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(pos)
                new = f.read()
                pos = f.tell()
        except Exception:
            continue

        if esc_cooldown <= 0 and ("请自行退出当前界面（ESC）" in new or "按ESC" in new):
            log_func("[ESC] 检测到阻塞，自动发送 ESC...")
            send_esc_to_genshin()
            esc_cooldown = 15
            time.sleep(2)

        if esc_cooldown > 0:
            esc_cooldown -= 3

        if target_line in new:
            elapsed = int(time.time() - start_t)
            log_func(f"配置组 {group_name} 执行完成 耗时 {elapsed} 秒")
            return True

    log_func(f"配置组监控超时（{timeout_sec} 秒）")
    return False


def monitor_main_world_entered(log_date_str, timeout_sec, log_func, stop_event):
    """
    监控 BetterGI 日志，等待「检测主界面」脚本输出「已进入游戏主界面」。
    此方案完全依赖 BetterGI 内部的主界面检测，不再自写截图检测。
    返回 True 表示成功进入游戏主界面。
    """
    log_dir = _get_bettergi_log_dir()
    log_path = os.path.join(log_dir, f"better-genshin-impact{log_date_str}.log") if log_dir else ""
    log_func(f"监控 BetterGI 主界面检测: {log_path}")
    start_t = time.time()

    while not os.path.isfile(log_path):
        if time.time() - start_t > 30 or stop_event.is_set():
            return False
        time.sleep(2)

    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            f.seek(0, 2)
            pos = f.tell()
    except Exception as e:
        log_func(f"无法打开日志: {e}")
        return False

    keyword = "========== 已进入游戏主界面 =========="

    while time.time() - start_t < timeout_sec:
        if stop_event.is_set():
            return False
        time.sleep(2)

        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(pos)
                new = f.read()
                pos = f.tell()
        except Exception:
            continue

        if keyword in new:
            elapsed = int(time.time() - start_t)
            log_func(f"BetterGI 检测到已进入游戏主界面 耗时 {elapsed} 秒")
            return True

    log_func(f"主界面检测超时（{timeout_sec} 秒）")
    return False


def wait_proc_appear(name, timeout_sec, log_func, stop_event):
    start_t = time.time()
    last_log = 0
    fast_steps = 20  # 前 20 次用 0.5s 步进 = 前 10 秒快速轮询
    step_count = 0
    while time.time() - start_t < timeout_sec:
        if stop_event.is_set():
            return None
        p = find_proc(name)
        if p:
            time.sleep(2)
            return p
        step_count += 1
        now = time.time()
        if now - last_log > 15:
            log_func(f"等待进程 {name}... ({int(now-start_t)}s/{timeout_sec}s)")
            last_log = now
        sleep_time = 0.5 if step_count <= fast_steps else 2.0
        time.sleep(sleep_time)
    return None


def wait_proc_gone(name, timeout_sec, log_func, stop_event):
    start_t = time.time()
    while time.time() - start_t < timeout_sec:
        if stop_event.is_set():
            return False
        if not find_proc(name):
            return True
        time.sleep(3)
    return False


def find_hutao_window():
    """找到胡桃真实窗口（WinUIDesktopWin32WindowClass，非 MSIX 容器壳）"""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    results = []

    for hwnd in _walk_top_windows():
        try:
            title = _get_window_text(hwnd)
            if not title or "胡桃" not in title:
                continue
            if _get_class_name(hwnd) != "WinUIDesktopWin32WindowClass":
                continue
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            results.append({
                "hwnd": hwnd,
                "left": rect.left,
                "top": rect.top,
                "width": rect.right - rect.left,
                "height": rect.bottom - rect.top,
            })
        except Exception:
            pass

    for r in results:
        if r["width"] > 100 and r["height"] > 100:
            return r
    return None


def close_window_by_title(title_keyword, log_func=None):
    """按标题关键词查找窗口，发送 WM_CLOSE 关闭。返回关闭的窗口数。"""
    import ctypes
    user32 = ctypes.windll.user32
    WM_CLOSE = 0x0010
    closed = 0
    results = []

    for hwnd in _walk_top_windows():
        if title_keyword in _get_window_text(hwnd):
            results.append(hwnd)

    for hwnd in results:
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        closed += 1

    if log_func and closed > 0:
        log_func(f"已关闭 {closed} 个「{title_keyword}」窗口")
    return closed


def click_hutao_start_game(log_func, stop_event):
    """先点左边导航栏「启动游戏」切换到启动页，再点右上角真正启动按钮"""
    if not HAS_UIA:
        log_func("[!] uiautomation 未安装")
        return False

    log_func("查找胡桃窗口...")
    win = None
    for _ in range(20):
        if stop_event.is_set():
            return False
        win = find_hutao_window()
        if win:
            break
        time.sleep(1)

    if not win:
        log_func("[!] 未找到胡桃窗口")
        return False

    import uiautomation as uia
    hwnd_ctrl = uia.ControlFromHandle(win["hwnd"])

    # 先尝试关闭可能残留的确认弹窗
    _dismiss_confirm_popup(win, log_func)

    # 第一步：找到左边导航栏的「启动游戏」ListItemControl 并点击
    log_func("点击左侧导航「启动游戏」...")

    def _find_nav_item(ctrl):
        """递归找含「启动游戏」的 ListItemControl"""
        try:
            if ctrl.ControlTypeName == "ListItemControl" and ctrl.Name == "启动游戏":
                return ctrl
        except Exception:
            pass
        for child in ctrl.GetChildren():
            res = _find_nav_item(child)
            if res:
                return res
        return None

    nav_item = _find_nav_item(hwnd_ctrl)
    if nav_item:
        log_func("找到导航项，点击...")
        try:
            nav_item.GetInvokePattern().Invoke()
        except Exception:
            nav_item.Click()
        time.sleep(3)
        log_func("已切换到启动游戏页面")
    else:
        log_func("未找到导航项，假设已在启动页")
        time.sleep(1)

    # 第二步：在启动页上找右侧「启动游戏」按钮
    log_func("寻找「启动游戏」按钮...")

    def _has_text_descendant(ctrl, keyword):
        try:
            if ctrl.Name and keyword in ctrl.Name:
                return True
        except Exception as e:
            log_func(f"[UIA] 遍历异常: {e}")
        try:
            for child in ctrl.GetChildren():
                if _has_text_descendant(child, keyword):
                    return True
        except: pass
        return False

    CANDIDATE_TYPES = ("ButtonControl", "HyperlinkControl", "GroupControl")
    win_bottom = win["top"] + win["height"]

    def _collect_candidates(ctrl, depth=0):
        """收集所有符合条件的候选控件"""
        results = []
        try:
            ct = ctrl.ControlTypeName
        except Exception:
            ct = ""

        if ct in CANDIDATE_TYPES:
            r = ctrl.BoundingRectangle
            w, h = r.width(), r.height()
            rx = r.left - win["left"]
            ry = r.top - win["top"]
            # 必须在窗口内且右半区
            if w < 60 or h < 20:
                pass
            elif rx < win["width"] * 0.3:
                pass
            elif r.top > win_bottom or r.top + h < win["top"]:
                pass  # 完全在窗口外
            elif _has_text_descendant(ctrl, "启动游戏"):
                # 排除导航栏
                parent = ctrl.GetParentControl()
                skip = False
                if parent:
                    try:
                        if parent.ControlTypeName == "ListItemControl":
                            skip = True
                    except Exception as e:
                        log_func(f"[UIA] 遍历异常: {e}")
                if not skip:
                    # 评分：ButtonControl 优先，名字直接匹配加分
                    score = 0
                    if ct == "ButtonControl":
                        score += 100
                    try:
                        if ctrl.Name and "启动游戏" in ctrl.Name:
                            score += 50
                    except Exception as e:
                        log_func(f"[UIA] 遍历异常: {e}")
                    # 控件越靠近窗口顶部/右侧加分（排除底部溢出控件）
                    score -= ry  # y 越小越好
                    log_func(f"候选: [{ct}] '{ctrl.Name or ''}' ({r.left},{r.top}) {w}x{h} score={score}")
                    results.append((score, ctrl, r))

        if depth > 12:
            return results
        try:
            for child in ctrl.GetChildren():
                results.extend(_collect_candidates(child, depth + 1))
        except Exception as e:
            log_func(f"[UIA] 遍历异常: {e}")
        return results

    candidates = _collect_candidates(hwnd_ctrl)
    candidates.sort(key=lambda x: x[0], reverse=True)

    btn = None
    if candidates:
        btn = candidates[0][1]
        r = candidates[0][2]
        log_func(f"选中: [{btn.ControlTypeName}] ({r.left},{r.top}) {r.width()}x{r.height()}")

    if not btn:
        # 可能是页面还没渲染完，等 2 秒重新获取 UIA 树再试
        time.sleep(2)
        log_func("重新扫描 UIA 树...")
        hwnd_ctrl = uia.ControlFromHandle(win["hwnd"])
        candidates = _collect_candidates(hwnd_ctrl)
        candidates.sort(key=lambda x: x[0], reverse=True)
        if candidates:
            btn = candidates[0][1]

    if not btn:
        log_func("[!] 未找到「启动游戏」按钮")
        return False

    r = btn.BoundingRectangle
    cx = r.left + r.width() // 2
    cy = r.top + r.height() // 2
    log_func(f"找到按钮: ({r.left}, {r.top}) {r.width()}x{r.height()} 中心({cx},{cy})")

    # UIA 点击可能静默失败，用 pyautogui 在屏幕坐标上点击做兜底
    old_failsafe = pyautogui.FAILSAFE
    pyautogui.FAILSAFE = False
    try:
        try:
            btn.GetInvokePattern().Invoke()
        except Exception:
            btn.Click()
        time.sleep(0.5)
        pyautogui.click(cx, cy)
        log_func("已点击「启动游戏」")
        time.sleep(1.5)
        return True
    except Exception as e:
        log_func(f"UIA点击失败，改用鼠标: {e}")
        pyautogui.click(cx, cy)
        time.sleep(1.5)
        log_func("已点击「启动游戏」(鼠标)")
        return True
    finally:
        pyautogui.FAILSAFE = old_failsafe


def _find_hutao_current_account(win, hwnd_ctrl):
    """找到当前登录的账号名 TextControl（左侧底部，'用户'下方）"""
    import uiautomation as uia

    sidebar_items = {
        "用户", "设置", "反馈中心", "胡桃通行证", "插件管理",
        "主页", "工具", "周期", "启动游戏", "祈愿记录",
        "成就管理", "实时便笺", "我的角色", "养成计划",
        "深境螺旋", "幻想真境剧诗", "幽境危战",
    }

    candidates = []

    def _scout(ctrl, depth=0):
        if depth > 10:
            return
        try:
            ct = ctrl.ControlTypeName
            name = ctrl.Name or ""
            if ct in ("TextControl",) and len(name) >= 2:
                r = ctrl.BoundingRectangle
                rx = r.left - win["left"]
                ry = r.top - win["top"]
                # 左侧底部：rel_x < 250, rel_y > 650（窗口下半区）
                if rx < 250 and ry > 650 and name not in sidebar_items and "胡桃" not in name:
                    candidates.append((ry, rx, name, ctrl))
        except Exception:
            pass
        try:
            for child in ctrl.GetChildren():
                _scout(child, depth + 1)
        except Exception:
            pass

    _scout(hwnd_ctrl)
    if not candidates:
        return None

    # 取最下面的那个（最大的 rel_y）即当前账号名
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][3], candidates[0][2]


def _dismiss_confirm_popup(win, log_func):
    """检测胡桃切换账号后的确认弹窗并点击确认按钮关闭"""
    import uiautomation as uia
    try:
        hwnd_ctrl = uia.ControlFromHandle(win["hwnd"])

        def _find_confirm_button(ctrl, depth=0):
            try:
                ct = ctrl.ControlTypeName
                name = ctrl.Name or ""
            except Exception:
                return None
            if depth > 12:
                return None
            # 查找 ButtonControl 且名字含"确认"/"确定"/"是"
            if ct == "ButtonControl" and name:
                for kw in ["确认", "确定", "是"]:
                    if kw in name:
                        r = ctrl.BoundingRectangle
                        if r.width() > 20 and r.height() > 10:
                            return ctrl
            for child in ctrl.GetChildren():
                res = _find_confirm_button(child, depth + 1)
                if res:
                    return res
            return None

        btn = _find_confirm_button(hwnd_ctrl)
        if btn:
            log_func(f"找到确认按钮「{btn.Name}」，点击关闭弹窗")
            try:
                btn.GetInvokePattern().Invoke()
            except Exception:
                btn.Click()
            time.sleep(1)
        else:
            log_func("未发现确认弹窗")
    except Exception as e:
        log_func(f"弹窗检测异常: {e}")


def _click_hutao_account_popup(win, target_name, log_func):
    """在账号弹出列表中查找并点击目标账号"""
    import uiautomation as uia

    time.sleep(2)

    # 重新获取窗口控件树（弹窗已出现）
    hwnd_ctrl = uia.ControlFromHandle(win["hwnd"])

    def _search_target(ctrl, depth=0):
        try:
            ct = ctrl.ControlTypeName
            name = ctrl.Name or ""
        except Exception:
            return None

        if depth > 15:
            return None

        # 找到名字匹配的 TextControl，点它的父控件
        if ct == "TextControl" and name and target_name in name:
            r = ctrl.BoundingRectangle
            if r.width() > 20:
                # 父控件通常是 ListItemControl / ButtonControl
                parent = ctrl.GetParentControl()
                if parent:
                    try:
                        pct = parent.ControlTypeName
                        pr = parent.BoundingRectangle
                        if pr.width() > 30 and pr.height() > 20:
                            return parent
                    except Exception:
                        pass
                return ctrl

        try:
            for child in ctrl.GetChildren():
                res = _search_target(child, depth + 1)
                if res:
                    return res
        except Exception:
            pass
        return None

    target = _search_target(hwnd_ctrl)
    if not target:
        log_func(f"[!] 未在列表中找到「{target_name}」")
        return False

    try:
        target.GetInvokePattern().Invoke()
    except Exception:
        target.Click()

    time.sleep(2)

    # 处理可能出现的确认弹窗：查找"确认"/"确定"按钮并点击
    log_func("检查确认弹窗...")
    _dismiss_confirm_popup(win, log_func)

    return True


def verify_and_switch_hutao_account(target_name, log_func, stop_event):
    """
    验证并切换胡桃账号（UIA）。
    点击左侧底部当前账号名 → 弹出列表 → 点目标账号。
    """
    if not HAS_UIA:
        log_func("[!] UIA 未安装，跳过切换")
        return True

    import uiautomation as uia

    win = find_hutao_window()
    if not win:
        log_func("[!] 未找到胡桃窗口")
        return True

    hwnd_ctrl = uia.ControlFromHandle(win["hwnd"])

    result = _find_hutao_current_account(win, hwnd_ctrl)
    if not result:
        log_func("[!] 未找到当前账号名，跳过切换")
        return True

    acct_ctrl, current_name = result
    log_func(f"当前账号: {current_name}")

    if target_name in current_name or current_name in target_name:
        log_func(f"账号「{target_name}」已选中")
        return True

    # 点击当前账号名，弹出切换列表
    log_func("打开账号列表...")
    try:
        acct_ctrl.GetInvokePattern().Invoke()
    except Exception:
        acct_ctrl.Click()

    time.sleep(1.5)

    # 在弹窗中找目标账号并点击
    log_func(f"查找「{target_name}」...")
    ok = _click_hutao_account_popup(win, target_name, log_func)
    if ok:
        log_func(f"已切换至「{target_name}」")
        # 关弹窗 + 点空白取消聚焦
        try:
            time.sleep(0.5)
            _dismiss_confirm_popup(win, log_func)
            time.sleep(0.5)
            # 点击窗口空白区域取消聚焦（中心偏右避开侧栏）
            rect = hwnd_ctrl.BoundingRectangle
            cx = rect.left + int(rect.width() * 0.7)
            cy = rect.top + int(rect.height() * 0.5)
            uia.Click(cx, cy)
            time.sleep(0.5)
        except Exception:
            pass
        return True

    return False


def wait_genshin_ready(log_func, stop_event, timeout_sec=300, on_window_appear=None,
                       loading_button_template=None, on_white_detected=None):
    """等待原神真正进入游戏：窗口出现 → 大小稳定 → 白屏→画面色彩检测
    
    on_window_appear: 可选回调，窗口出现（白屏瞬间）时触发，适合在此启动 BetterGI 等
    loading_button_template: 大门加载画面右上角关闭按钮模板，用于区分加载画面与游戏世界"""
    import numpy as np
    from PIL import Image

    # 预加载关闭按钮模板
    _template_arr = None
    if loading_button_template and os.path.isfile(loading_button_template):
        try:
            from PIL import Image as PILImage
            _template_arr = np.array(PILImage.open(loading_button_template).convert("RGB"), dtype=np.float32)
            log_func(f"已加载关闭按钮模板 ({_template_arr.shape[1]}x{_template_arr.shape[0]})")
        except Exception:
            pass

    def _check_loading_button(screen_arr):
        """检测大门加载画面特有的关闭按钮（模糊匹配），存在→仍在加载，不存在→已进入游戏"""
        if _template_arr is None:
            return False
        try:
            th, tw = _template_arr.shape[:2]
            h, w = screen_arr.shape[:2]
            # 只搜左下 1/4 区域（底 1/3 × 左 1/3）
            y0, y1 = max(1, 2 * h // 3), h
            x0, x1 = 0, max(1, w // 3)
            roi = screen_arr[y0:y1, x0:x1].astype(np.float32)
            rh, rw = roi.shape[:2]
            if rh < th or rw < tw:
                return False
            from numpy.lib.stride_tricks import sliding_window_view
            windows = sliding_window_view(roi, (th, tw, 3)).squeeze()
            # windows shape: (rh-th+1, rw-tw+1, th, tw, 3)
            diff = windows - _template_arr
            mse = np.mean(diff ** 2, axis=(2, 3, 4))
            return np.min(mse) < 500
        except Exception:
            return False

    if not HAS_GW:
        log_func("pygetwindow 不可用，使用进程检测方式等待游戏窗口...")
        for i in range(30):
            if stop_event.is_set():
                return False
            try:
                result = subprocess.run(
                    ['tasklist', '/fi', 'imagename eq YuanShen.exe', '/fo', 'csv', '/nh'],
                    capture_output=True, text=True, timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                if 'YuanShen.exe' in result.stdout or 'GenshinImpact.exe' in result.stdout:
                    log_func("检测到游戏进程，等待窗口稳定...")
                    time.sleep(5)
                    return True
            except Exception:
                pass
            stop_event.wait(3)
        log_func("等待游戏超时（90秒）")
        return True

    start_t = time.time()
    win = None

    # 阶段1：等待窗口出现
    while time.time() - start_t < timeout_sec:
        if stop_event.is_set():
            return False
        for pat in ["原神", "YuanShen", "Genshin Impact"]:
            ws = gw.getWindowsWithTitle(pat)
            if ws:
                win = ws[0]
                break
        if win and win.width > 100:
            break
        time.sleep(5)

    if not win or win.width < 100:
        return False

    log_func(f"原神窗口已出现: {win.width}x{win.height}")

    if on_window_appear:
        try:
            on_window_appear()
        except Exception:
            pass

    # 阶段2：窗口大小稳定（1次确认即可）
    last_size = None
    stable = 0
    while stable < 1 and (time.time() - start_t < timeout_sec):
        if stop_event.is_set():
            return False
        time.sleep(6)
        try:
            for pat in ["原神", "YuanShen", "Genshin Impact"]:
                ws = gw.getWindowsWithTitle(pat)
                if ws:
                    # 排除 BetterGI 窗口
                    win = None
                    for w in ws:
                        t = w.title.lower()
                        if "bettergi" in t or "更好的原神" in w.title:
                            continue
                        win = w
                        break
                    if win:
                        break
            if not win:
                stable = 0
                continue
            cur = (win.width, win.height)
            if cur[0] < 100:
                continue
            if last_size and cur == last_size:
                stable += 1
                log_func(f"窗口稳定: {cur[0]}x{cur[1]}")
            else:
                stable = 1
                last_size = cur
        except Exception:
            stable = 0

    if stable < 1:
        return False

    # 阶段3：截图色彩检测
    # 原神加载流程大体是: 白屏 → 复杂画面 → 白屏 → 复杂画面 → 游戏
    # 实际会有抖动和偏差：连续白屏、连续复杂、跳过一次白屏、持续复杂不变 等
    # 策略：用"白→彩"相位转换次数捕获整体流程，不要求严格交替
    # 去抖动(debouce)：连续 N 帧同色才确认相位切换，过滤单帧噪点
    DEBOUNCE = 2
    # 详细日志（调试用，默认关闭）
    # has_template = bool(loading_button_template and os.path.isfile(loading_button_template))
    # log_func(f"开始截图检测... (模板={'已加载' if has_template else '无'}, 去抖动={DEBOUNCE}帧, 阈值=0.55)")
    log_func("开始截图检测...")
    current_phase = None        # 已确认的相位: "white" / "color"
    phase_frame_count = 0       # 当前相位已确认的持续帧数
    white_to_color_count = 0    # 白→彩 转换次数
    pending_phase = None        # 去抖动暂存
    pending_count = 0
    _white_triggered = False    # on_white_detected 只触发一次

    while time.time() - start_t < timeout_sec:
        if stop_event.is_set():
            return False

        try:
            for pat in ["原神", "YuanShen", "Genshin Impact"]:
                ws = gw.getWindowsWithTitle(pat)
                if ws:
                    # 排除 BetterGI 窗口（标题含"更好的原神"/"BetterGI"）
                    win = None
                    for w in ws:
                        t = w.title.lower()
                        if "bettergi" in t or "更好的原神" in w.title:
                            continue
                        win = w
                        break
                    if win:
                        break
            if not win or win.width < 100:
                time.sleep(5)
                continue

            l, t, w, h = win.left, win.top, win.width, win.height
            m = int(min(w, h) * 0.08)
            region = (l + m, t + m, w - 2 * m, h - 2 * m)

            img = pyautogui.screenshot(region=region)
            arr = np.array(img, dtype=np.float32)

            # 过滤截图抓帧失败产生的全黑/近黑帧（DirectX 渲染间隙的假帧）
            if np.mean(arr) < 10:
                log_func("[跳过] 疑似截图黑帧，忽略")
                time.sleep(0.3)
                continue

            diff = np.sqrt(np.sum((arr - 255.0) ** 2, axis=2))
            white_ratio = np.mean(diff < 40)
            color_std = float(np.std(arr))

            # 迟滞阈值：当前相位用不同门槛，避免个别帧误判导致相位反复横跳
            if current_phase is None:
                is_white = white_ratio > 0.55
            elif current_phase == "white":
                is_white = white_ratio > 0.3   # 已是白屏，需大幅下降才认为进入彩色
            else:
                is_white = white_ratio > 0.7   # 已是彩色，需大幅上升才认为回到白屏
            raw_phase = "white" if is_white else "color"

            # # 原始帧数据日志（调试用，默认关闭）
            # log_func(f"[帧] 白像素比={white_ratio:.3f} 色彩度={color_std:.0f} → raw={raw_phase} "
            #          f"pending={pending_phase}#{pending_count} current={current_phase}#{phase_frame_count}")

            # --- 去抖动：连续 DEBOUNCE 帧同色才确认相位切换 ---
            if raw_phase == pending_phase:
                pending_count += 1
            else:
                pending_phase = raw_phase
                pending_count = 1

            if pending_count < DEBOUNCE:
                time.sleep(0.3)
                continue  # 未确认，跳过本帧判定

            # 已确认，更新相位
            if pending_phase != current_phase:
                # log_func(f"[相位切换] {current_phase} → {pending_phase} (持续{pending_count}帧确认)")
                if pending_phase == "color" and current_phase == "white":
                    white_to_color_count += 1
                    # log_func(f"第{white_to_color_count}次白→画面转换 (色彩度 {color_std:.0f})")
                elif pending_phase == "white" and not _white_triggered:
                    # 首次确认白屏，触发回调（用于启动 BetterGI）
                    _white_triggered = True
                    if on_white_detected:
                        try:
                            on_white_detected()
                        except Exception as e:
                            log_func(f"on_white_detected 回调异常: {e}")
                current_phase = pending_phase
                phase_frame_count = pending_count
            else:
                phase_frame_count = pending_count

            # --- 进入判定（仅彩色相位） ---
            if not is_white:
                if white_to_color_count >= 2:
                    # log_func(f"[判定] w→c={white_to_color_count}, 稳定色帧={phase_frame_count}, 开始评估是否进入游戏")
                    if phase_frame_count >= 12:
                        btn_detected = _check_loading_button(arr)
                        # log_func(f"[加载按钮检测] 结果={'存在' if btn_detected else '不存'}")
                        if btn_detected:
                            log_func("检测到加载画面关闭按钮，继续等待...")
                            phase_frame_count = 0
                        else:
                            # log_func("[截图后等待2秒...]")
                            time.sleep(2)
                            log_func("游戏已进入")
                            return True

        except Exception as e:
            log_func(f"截图异常: {e}")

        time.sleep(0.3)

    log_func("[!] 截图检测超时")
    return False


def get_bettergi_groups():
    """扫描 BetterGI 调度组目录，返回所有组名列表。"""
    cfg = load_config()
    gi_config = cfg["bettergi"]["config"]
    group_dir = os.path.join(os.path.dirname(gi_config), "ScriptGroup")
    if not os.path.isdir(group_dir):
        return []
    groups = []
    for f in os.listdir(group_dir):
        if f.endswith(".json"):
            groups.append(f[:-5])  # 去掉 .json 后缀
    return sorted(groups)


def ocr_genshin_uid_raw(log_func, stop_event, max_retries=5):
    """截图原神右下角 UID 区域，OCR 识别原始数字字符串。
    返回识别到的 UID 字符串（9位数字），失败返回空字符串。"""
    if not HAS_GW and not HAS_PA:
        return ""

    try:
        import pytesseract
    except ImportError:
        log_func("[!] pytesseract 未安装，跳过 UID 识别")
        return ""

    import numpy as np
    from PIL import Image, ImageOps
    try:
        import win32gui
        import win32ui
        import win32con
        HAS_W32 = True
    except ImportError:
        HAS_W32 = False

    # 1. 优先使用便携版 Tesseract（exe 同目录或嵌入资源中的 tesseract-ocr）
    # 在 --onefile 模式下 tesseract-ocr 不会被嵌入，所以也检查 SCRIPT_DIR
    portable_tess = os.path.join(str(RESOURCE_DIR), "tesseract-ocr", "tesseract.exe")
    if not os.path.isfile(portable_tess):
        portable_tess = os.path.join(str(SCRIPT_DIR), "tesseract-ocr", "tesseract.exe")
    if os.path.isfile(portable_tess):
        pytesseract.pytesseract.tesseract_cmd = portable_tess
    else:
        # 2. 回退到 config.json 中配置的路径
        cfg = load_config()
        t_path = cfg.get("tesseract", {}).get("path", "")
        if t_path:
            tess_exe = os.path.join(t_path, "tesseract.exe")
            if os.path.isfile(tess_exe):
                pytesseract.pytesseract.tesseract_cmd = tess_exe

    debug_dir = os.path.join(str(SCRIPT_DIR), "temp")
    os.makedirs(debug_dir, exist_ok=True)

    for attempt in range(max_retries):
        if stop_event.is_set():
            return ""

        try:
            # 查找窗口
            hwnd = None
            if HAS_W32:
                def _find_hwnd(h, _):
                    nonlocal hwnd
                    if hwnd:
                        return
                    t = win32gui.GetWindowText(h)
                    # 排除 BetterGI 窗口（标题含"更好的原神"）
                    if "bettergi" in t.lower() or "更好的原神" in t:
                        return
                    for pat in ["原神", "YuanShen", "Genshin Impact"]:
                        if pat.lower() in t.lower():
                            hwnd = h
                            return
                win32gui.EnumWindows(_find_hwnd, None)

            if HAS_W32 and hwnd and win32gui.IsWindowVisible(hwnd):
                r = win32gui.GetWindowRect(hwnd)
                win_w, win_h = r[2] - r[0], r[3] - r[1]
            else:
                # 回退 pygetwindow（排除 BetterGI）
                for pat in ["原神", "YuanShen", "Genshin Impact"]:
                    ws = gw.getWindowsWithTitle(pat)
                    ws = [w for w in ws if "bettergi" not in w.title.lower() and "更好的原神" not in w.title]
                    if ws:
                        win = ws[0]
                        break
                else:
                    time.sleep(3)
                    continue
                r = (win.left, win.top, win.left + win.width, win.top + win.height)
                win_w, win_h = win.width, win.height

            if win_w < 100:
                time.sleep(5)
                continue

            uid_l = int(win_w * 0.77)
            uid_t = int(win_h * 0.970)
            uid_w = max(150, int(win_w * 0.21))
            uid_h = max(25, int(win_h * 0.030))

            # PrintWindow (PW_RENDERFULLCONTENT) 是唯一可靠的方式。
            # 全屏独占 DirectX 模式下 pyautogui/BitBlt 返回白屏/黑屏，不浪费时间降级。
            img = None
            source = ""

            if HAS_W32 and hwnd:
                try:
                    import ctypes
                    PW_RENDERFULLCONTENT = 2
                    wDC = win32gui.GetWindowDC(hwnd)
                    dcObj = win32ui.CreateDCFromHandle(wDC)
                    cDC = dcObj.CreateCompatibleDC()
                    bmp = win32ui.CreateBitmap()
                    bmp.CreateCompatibleBitmap(dcObj, win_w, win_h)
                    cDC.SelectObject(bmp)
                    result = ctypes.windll.user32.PrintWindow(hwnd, cDC.GetSafeHdc(), PW_RENDERFULLCONTENT)
                    if result:
                        bmpinfo = bmp.GetInfo()
                        bmpbits = bmp.GetBitmapBits(True)
                        full_img = Image.frombuffer(
                            "RGB", (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
                            bmpbits, "raw", "BGRX", 0, 1
                        )
                        # 裁剪 UID 区域
                        img = full_img.crop((uid_l, uid_t, uid_l + uid_w, uid_t + uid_h))
                        arr_check = np.array(img.convert("L"), dtype=np.uint8)
                        pw_mean = float(np.mean(arr_check))
                        pw_max = float(arr_check.max())
                        # 放宽阈值：UID 区域含半透明背景，均值范围宽于纯黑/纯白
                        if pw_max > 40 and pw_mean < 250:
                            source = "PrintWindow"
                        else:
                            img = None
                    win32gui.DeleteObject(bmp.GetHandle())
                    cDC.DeleteDC()
                    dcObj.DeleteDC()
                    win32gui.ReleaseDC(hwnd, wDC)
                except Exception:
                    pass

            if img is None:
                log_func(f"DEBUG: PrintWindow 截图无效 (尝试 {attempt+1}/{max_retries})，等待...")
                time.sleep(3)
                continue

            log_func(f"DEBUG: 截图来源={source}")
            if attempt == 0:
                try:
                    img.save(os.path.join(debug_dir, "uid_raw.png"))
                    # 全窗口截图也保存
                    full = pyautogui.screenshot(region=(r[0], r[1], win_w, win_h))
                    full.save(os.path.join(debug_dir, "uid_fullwin.png"))
                    log_func(f"DEBUG: 窗口=({r[0]},{r[1]}) {win_w}x{win_h} UID区域=({uid_l},{uid_t}) {uid_w}x{uid_h}")
                except Exception:
                    pass

            # 预处理：灰度 → 提取亮区文字（UID 是白色文字）
            gray = img.convert("L")
            arr = np.array(gray, dtype=np.uint8)

            # 投票法 OCR：多阈值二值化提取亮像素 → 反转黑字白底 → OCR → 投票
            all_texts = {}
            for t in range(100, 235, 10):  # 宽范围覆盖不同亮度背景下的白色 UID
                bin_arr = ((arr > t).astype(np.uint8)) * 255
                black_pct = np.sum(bin_arr < 128) / bin_arr.size
                if black_pct < 0.005 or black_pct > 0.85:
                    continue
                try:
                    # 反转为黑字白底（Tesseract 默认偏好），不做膨胀以防数字粘连
                    inverted = 255 - bin_arr
                    pimg = Image.fromarray(inverted)
                    pimg = pimg.resize((pimg.width * 3, pimg.height * 3), Image.LANCZOS)
                    for psm in ["7", "8", "13", "6"]:
                        txt = pytesseract.image_to_string(
                            pimg, config=f'--psm {psm} -c tessedit_char_whitelist=0123456789').strip()
                        if txt:
                            all_texts[txt] = all_texts.get(txt, 0) + 1
                except Exception:
                    pass

            # 投票选最佳结果
            best_text = ""
            best_count = 0
            for txt, count in all_texts.items():
                if count > best_count:
                    best_count = count
                    best_text = txt

            if best_text:
                digits_only = ''.join(c for c in best_text if c.isdigit())
                if len(digits_only) >= 5:
                    log_func(f"UID OCR 识别: '{digits_only}' (投票{best_count})")
                    return digits_only

            # 方法B: 如果投票法失败，尝试传统反转二值化
            proc_arr = ((arr < 120).astype(np.uint8)) * 255  # 暗像素=背景→白色
            proc_img = Image.fromarray(proc_arr)
            proc_img = proc_img.resize((proc_img.width * 3, proc_img.height * 3), Image.LANCZOS)

            if attempt == 0:
                try:
                    proc_img.save(os.path.join(debug_dir, "uid_proc.png"))
                    img.save(os.path.join(debug_dir, "uid_raw.png"))
                except Exception:
                    pass

            for psm in ["7", "13", "6"]:
                text = pytesseract.image_to_string(
                    proc_img, config=f'--psm {psm} -c tessedit_char_whitelist=0123456789').strip()
                digits_only = ''.join(c for c in text if c.isdigit())
                if len(digits_only) >= 5:
                    log_func(f"UID OCR 识别: '{digits_only}'")
                    return digits_only

            log_func(f"UID OCR 识别: '' (无效，重试 {attempt+1}/{max_retries})")
            time.sleep(1)

        except Exception as e:
            log_func(f"UID 识别异常: {e}")
            time.sleep(1)

    log_func(f"[!] UID OCR: {max_retries} 次尝试均未识别到有效数字")
    return ""


def ocr_genshin_uid_bettergi(group_name, log_func, stop_event, max_retries=3):
    """使用 BetterGI 调度组启动 RecognizeUid 脚本识别 UID。
    启动 BetterGI --startGroups <group_name>，监控日志中 "UID 识别成功" 行，
    提取 9 位数字后 kill 掉 BetterGI 并返回。失败返回空字符串。"""
    if not group_name:
        log_func("[!] UID 调度组名未配置")
        return ""

    cfg = load_config()
    gi_exe = cfg["bettergi"]["exe"]
    if not os.path.isfile(gi_exe):
        log_func(f"[!] BetterGI 不存在: {gi_exe}")
        return ""

    log_date_str = datetime.now().strftime("%Y%m%d")
    log_dir = _get_bettergi_log_dir()
    log_path = os.path.join(log_dir, f"better-genshin-impact{log_date_str}.log") if log_dir else ""

    for attempt in range(max_retries):
        if stop_event.is_set():
            return ""

        # 确保没有残留 BetterGI
        if find_proc("BetterGI.exe"):
            kill_proc("BetterGI.exe")
            time.sleep(3)

        log_func(f"启动 BetterGI 调度组 [{group_name}] 识别 UID (尝试 {attempt+1}/{max_retries})...")
        pid = start_bettergi_with_args(gi_exe, ["--startGroups", group_name])
        if not pid:
            log_func("[!] BetterGI 启动失败")
            continue

        # 等待日志文件出现
        start_t = time.time()
        while not os.path.isfile(log_path):
            if time.time() - start_t > 30 or stop_event.is_set():
                kill_proc("BetterGI.exe")
                return ""
            time.sleep(2)

        # 等待 BetterGI 退出（--startGroups 执行完调度组后 BetterGI 会自动退出）
        # 先等待调度组执行完（最多 120 秒）
        wait_start = time.time()
        uid_found = ""
        last_pos = 0

        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(0, 2)
                last_pos = f.tell()
        except Exception as e:
            log_func(f"无法打开日志: {e}")
            kill_proc("BetterGI.exe")
            continue

        while time.time() - wait_start < 120:
            if stop_event.is_set():
                kill_proc("BetterGI.exe")
                return ""

            time.sleep(3)

            # 检查 BetterGI 是否已退出
            if not find_proc("BetterGI.exe"):
                log_func("BetterGI 已退出")

                # 最后一次读取日志剩余内容
                try:
                    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                        f.seek(last_pos)
                        tail = f.read()
                except Exception:
                    tail = ""

                if "UID 识别成功" in tail:
                    import re
                    lines = tail.splitlines()
                    hit = False
                    for i, line in enumerate(lines):
                        if "UID 识别成功" in line:
                            # 扫描本行及后续 5 行找 9 位 UID
                            for j in range(i, min(i + 6, len(lines))):
                                nums = re.findall(r'\d{9}', lines[j])
                                if nums:
                                    uid_found = nums[0]
                                    log_func(f"UID 识别成功(日志): {uid_found}")
                                    hit = True
                                    break
                            if hit:
                                break
                break

            # 读新增的日志行
            try:
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    f.seek(last_pos)
                    new = f.read()
                    last_pos = f.tell()
            except Exception:
                continue

            if "UID 识别成功" in new:
                import re
                lines = new.splitlines()
                hit = False
                for i, line in enumerate(lines):
                    if "UID 识别成功" in line:
                        for j in range(i, min(i + 6, len(lines))):
                            nums = re.findall(r'\d{9}', lines[j])
                            if nums:
                                uid_found = nums[0]
                                log_func(f"UID 识别成功: {uid_found}")
                                hit = True
                                break
                        if hit:
                            break
                if hit:
                    break

            if "任务结束" in new or "任务启动" in new:
                log_func(f"调度组执行中... ({(int(time.time() - wait_start))}s)")

        # 确保 BetterGI 已退出
        if find_proc("BetterGI.exe"):
            kill_proc("BetterGI.exe")
            time.sleep(2)

        if uid_found:
            return uid_found

        log_func(f"尝试 {attempt+1} 未识别到 UID")

    log_func(f"[!] BetterGI UID 识别: {max_retries} 次尝试均未识别到有效数字")
    return ""


def start_bettergi_with_args(gi_exe, args):
    """启动 BetterGI 并附加命令行参数，返回 PID 或 None。"""
    try:
        cmd = [gi_exe] + args
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        return proc.pid
    except Exception as e:
        return None


def verify_genshin_uid(expected_uid, log_func, stop_event, max_retries=5):
    """截图原神右下角 UID 区域，OCR 识别并与期望值比对。
    根据配置 uid.method 自动选择 tesseract 或 bettergi 方式。
    返回 (是否匹配, 识别到的UID字符串)。"""
    if not expected_uid:
        return True, ""

    cfg = load_config()
    uid_method = cfg.get("uid", {}).get("method", "tesseract")

    if uid_method == "bettergi":
        group_name = cfg.get("uid", {}).get("bettergi_group", "")
        recognized = ocr_genshin_uid_bettergi(group_name, log_func, stop_event, max_retries)
    else:
        recognized = ocr_genshin_uid_raw(log_func, stop_event, max_retries)
    if not recognized:
        log_func(f"[!] UID 识别失败，期望 {expected_uid}")
        return False, ""

    if expected_uid in recognized or recognized == expected_uid:
        log_func(f"UID 验证通过: {expected_uid}")
        return True, recognized

    log_func(f"[!] UID 不匹配: 识别={recognized}, 期望={expected_uid}")
    return False, recognized


# ============================================================
# 工作线程
# ============================================================

class WorkerThread(threading.Thread):
    def __init__(self, accounts, log_queue, stop_event, pause_event, exec_lock=None):
        super().__init__(daemon=True)
        self.accounts = accounts  # 账号列表 (dict)
        self.log_queue = log_queue
        self.stop_event = stop_event
        self.pause_event = pause_event  # 暂停事件：set=不暂停, clear=暂停
        self.exec_lock = exec_lock  # 任务互斥锁
        self.cfg = load_config()

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_queue.put(f"[{ts}] {msg}")

    def run(self):
        # 工作线程需要初始化 COM 才能使用 UIA
        import ctypes
        ctypes.windll.ole32.CoInitialize(0)
        try:
            self._run_impl()
        except Exception as e:
            self.log(f"[!] 错误: {e}")
            import traceback
            self.log(traceback.format_exc())
        finally:
            ctypes.windll.ole32.CoUninitialize()
            self.log_queue.put("__DONE__")
            if self.exec_lock is not None:
                try:
                    self.exec_lock.release()
                except RuntimeError:
                    pass  # 锁未被持有或已释放

    def _find_account_by_uid(self, recognized_uid):
        """在所有账号中查找匹配 UID 的账号，返回账号字典或 None"""
        if not recognized_uid or len(recognized_uid) < 5:
            return None
        for a in self.accounts:
            uid = a.get("uid", "").strip()
            if uid == recognized_uid:
                return a
            # 部分匹配：仅当识别结果包含足够多位数时生效（≥7位且是被包含关系）
            if len(recognized_uid) >= 7 and (recognized_uid in uid or uid in recognized_uid):
                return a
        return None

    def _run_impl(self):
        cfg = self.cfg
        gi_exe = cfg["bettergi"]["exe"]
        gi_config = cfg["bettergi"]["config"]
        genshin_proc = cfg["genshin"]["process_name"]
        genshin_proc_name = genshin_proc  # 保存进程名，scheduler 分支可能覆盖 genshin_proc
        sh_exe = cfg["snap_hutao"]["exe"]
        sh_app_id = cfg["snap_hutao"].get("app_id", "")
        timeout = cfg["monitor"].get("max_wait_seconds", 7200)
        log_date_str = datetime.now().strftime("%Y%m%d")

        if self.stop_event.is_set():
            return

        # TeyvatGuide 全局签到（所有任务开始前执行一次）
        if cfg.get("tg_checkin_before_all", False):
            self.log("=== TeyvatGuide 全局签到开始 ===")
            _run_checkin(self.log, self.stop_event)
            self.log("=== TeyvatGuide 全局签到完成 ===")

        if not cleanup_all(self.log, self.stop_event):
            self.log("[!] 清理失败，请手动关闭后重试")
            return

        total = len(self.accounts)
        executed = set()
        prev_type = None
        idx = 0
        pending = list(self.accounts)

        # ---- 需求4：扫描胡桃任务数量，决定是否保持胡桃运行 ----
        hutao_count = sum(1 for a in self.accounts if a.get("type", "direct") == "hutao")
        keep_hutao_alive = hutao_count >= 2
        remaining_hutao = hutao_count
        if keep_hutao_alive:
            self.log(f"[优化] 检测到 {hutao_count} 个胡桃任务，任务间不关闭胡桃工具箱")

        # ---- 扫描 TG CDP 任务数量，决定是否保持 TeyvatGuide 运行 ----
        tgcdp_count = sum(1 for a in self.accounts if a.get("type", "direct") == "tg_cdp")
        keep_tg_alive = tgcdp_count >= 2
        remaining_tg = tgcdp_count
        if keep_tg_alive:
            self.log(f"[优化] 检测到 {tgcdp_count} 个 TG CDP 任务，任务间不关闭 TeyvatGuide")

        while pending:
            if self.stop_event.is_set():
                self.log("用户停止")
                break

            # 暂停等待
            while not self.stop_event.is_set():
                if self.pause_event.wait(timeout=0.5):
                    break  # 恢复执行
            if self.stop_event.is_set():
                self.log("用户停止")
                break

            acc = pending.pop(0)
            idx += 1
            name = acc["name"]
            acc_type = acc.get("type", "direct")
            config_name = acc["config_name"]

            self.log("=" * 50)
            self.log(f"[{idx}/{total}] {name}  |  {config_name}")
            self.log("=" * 50)
            self.log_queue.put(f"__PROGRESS__{idx}")
            self.log_queue.put(f"__STATUS__[{idx}/{total}] {name} 处理中...")

            # 切换配置
            old_cfg = modify_bettergi_config(gi_config, config_name)
            self.log(f"配置: {old_cfg} -> {config_name}")

            # ---- 启动方式交接规则 ----
            # 游戏关闭由批次循环的 force_close_game 参数统一控制
            # 此处只需处理 TG/Hutao 相关的进程清理（非游戏进程）
            game_alive = find_proc(genshin_proc_name) is not None
            if prev_type is not None:
                if prev_type == "tg_cdp" or acc_type == "tg_cdp":
                    # TG→* 或 *→TG：如果游戏还活着则杀掉（belt and suspenders）
                    if game_alive:
                        self.log(f"[过渡] {prev_type}→{acc_type}，关闭游戏进程...")
                        kill_proc(genshin_proc_name)
                        time.sleep(2)
                        game_alive = False
                elif prev_type == "hutao" or acc_type == "hutao":
                    # 涉及胡桃的过渡：如果游戏还活着则杀掉
                    if game_alive:
                        self.log(f"[过渡] {prev_type}→{acc_type}，关闭游戏进程...")
                        kill_proc(genshin_proc_name)
                        time.sleep(2)
                        game_alive = False

            # 检测游戏是否仍在运行（上一个直接启动任务保留了游戏进程）
            game_still_running = (prev_type == "direct" and acc_type == "direct" and game_alive)
            if game_still_running:
                self.log("[过渡] 直接启动→直接启动，游戏已在运行中，跳过进入检测...")

            # 根据下一个任务类型决定是否强制关闭/保留游戏
            # 规则：直接→直接保留；同类型胡桃/TG→胡桃/TG关闭；不同类型关闭；最后一个任务看全局设置
            settings = cfg.get("settings", {})
            if len(pending) > 0:
                next_type = pending[0].get("type", "direct")
                if acc_type == "direct" and next_type == "direct":
                    force_close_game = False
                    self.log("[过渡] 下一个任务也是直接启动，强制保留游戏进程")
                else:
                    force_close_game = True
                    if acc_type == "hutao" and next_type == "hutao":
                        self.log("[过渡] 胡桃→胡桃，强制关闭游戏进程")
                    elif acc_type == "tg_cdp" and next_type == "tg_cdp":
                        self.log("[过渡] TG→TG，强制关闭游戏进程")
                    elif acc_type != next_type:
                        self.log(f"[过渡] {acc_type}→{next_type}，强制关闭游戏进程")
            else:
                # 最后一个任务：全局设置优先级最高
                force_close_game = settings.get("close_game_after_all", True)
                if force_close_game:
                    self.log("[最后一个任务] 全局设置要求关闭游戏")
                else:
                    self.log("[最后一个任务] 全局设置要求保留游戏")

            # BetterGI 和 TeyvatGuide 关闭策略：中间任务总是关闭，最后一个任务看全局设置
            if len(pending) > 0:
                force_close_bettergi = True
                force_close_teyvatguide = True
            else:
                force_close_bettergi = settings.get("close_bettergi_after_all", False)
                force_close_teyvatguide = settings.get("close_teyvatguide_after_all", True)
                if force_close_bettergi:
                    self.log("[最后一个任务] 全局设置要求关闭 BetterGI")
                else:
                    self.log("[最后一个任务] 全局设置要求保留 BetterGI")
                if force_close_teyvatguide:
                    self.log("[最后一个任务] 全局设置要求关闭 TeyvatGuide")
                else:
                    self.log("[最后一个任务] 全局设置要求保留 TeyvatGuide")

            if acc_type == "hutao":
                # 胡桃→胡桃且 keep_hutao_alive：跳过杀掉胡桃进程
                skip_hutao_kill = (keep_hutao_alive and prev_type == "hutao")
                matched, recognized_uid = self._run_hutao_smart(
                    acc, gi_exe, gi_config, genshin_proc_name, sh_exe,
                    sh_app_id, timeout, log_date_str, pending,
                    skip_hutao_kill=skip_hutao_kill,
                    keep_hutao_alive=keep_hutao_alive,
                    remaining_hutao=remaining_hutao,
                    force_close_game=force_close_game,
                    force_close_bettergi=force_close_bettergi,
                )
            elif acc_type == "tg_cdp":
                skip_tg_init = keep_tg_alive and prev_type == "tg_cdp"
                matched, recognized_uid = self._run_tg_cdp_smart(
                    acc, gi_exe, gi_config, genshin_proc_name, timeout, log_date_str,
                    is_last=(len(pending) == 0),
                    keep_tg_alive=keep_tg_alive,
                    remaining_tg=remaining_tg,
                    skip_tg_init=skip_tg_init,
                    force_close_game=force_close_game,
                    force_close_bettergi=force_close_bettergi,
                    force_close_teyvatguide=force_close_teyvatguide,
                )
            else:
                matched, recognized_uid = self._run_direct_smart(
                    acc, gi_exe, gi_config, genshin_proc_name, timeout, log_date_str,
                    is_last=(len(pending) == 0), prev_hutao=(prev_type == "hutao"),
                    skip_ready_check=game_still_running,
                    force_close_game=force_close_game,
                    force_close_bettergi=force_close_bettergi,
                )

            if matched:
                executed.add(name)
            elif recognized_uid:
                # UID 不匹配 → 尝试找到正确账号
                match_acc = self._find_account_by_uid(recognized_uid)
                if match_acc and match_acc["name"] not in executed:
                    self.log(f"[!] UID {recognized_uid} 匹配账号「{match_acc['name']}」，切换执行...")
                    # 把当前账号放回待处理（可能下次还需要）
                    pending.insert(0, acc)
                    # 把匹配的账号提到最前面
                    if match_acc in pending:
                        pending.remove(match_acc)
                    pending.insert(0, match_acc)
                    # 不要关闭游戏，下一个迭代会重新进入
                    continue
                else:
                    self.log(f"[!] UID {recognized_uid} 未匹配任何账号或已执行，跳过")

            prev_type = acc_type
            if acc_type == "hutao":
                remaining_hutao -= 1
            if acc_type == "tg_cdp":
                remaining_tg -= 1

        # 最终清理：根据是否手动停止采用不同的策略
        settings = self.cfg.get("settings", {})
        is_manual_stop = self.stop_event.is_set()

        if is_manual_stop:
            # 手动停止：由「手动停止时关闭所有进程」开关控制
            if settings.get("stop_closes_all_processes", True):
                self.log("手动停止：关闭所有进程...")
                kill_proc(genshin_proc_name, graceful=False)
                time.sleep(2)
                kill_proc("BetterGI.exe", graceful=False)
                time.sleep(2)
                for pn in ["Snap.Hutao.Remastered.exe",
                           "Snap.Hutao.Remastered.FullTrust.exe"]:
                    kill_proc(pn, graceful=False)
                    time.sleep(1)
                kill_proc("TeyvatGuide.exe", graceful=False)
                time.sleep(1)
            else:
                self.log("手动停止：设置要求保留所有进程，不关闭")
        else:
            # 正常完成：由三个独立开关控制
            close_game_after_all = settings.get("close_game_after_all", True)
            close_bettergi_after_all = settings.get("close_bettergi_after_all", False)
            close_hutao_after_all = settings.get("close_hutao_after_all", True)

            if close_game_after_all and find_proc(genshin_proc_name):
                self.log("关闭原神游戏进程...")
                kill_proc(genshin_proc_name, graceful=False)
                time.sleep(2)

            if close_bettergi_after_all and find_proc("BetterGI.exe"):
                self.log("关闭 BetterGI 进程...")
                kill_proc("BetterGI.exe", graceful=False)
                time.sleep(2)

            if close_hutao_after_all:
                for pn in ["Snap.Hutao.Remastered.exe",
                           "Snap.Hutao.Remastered.FullTrust.exe"]:
                    self.log(f"关闭胡桃工具箱 ({pn})...")
                    # 直接 kill_proc（内部 psutil 枚举，绕过 find_proc 1秒缓存）
                    kill_proc(pn, graceful=False)
                    time.sleep(1)

            close_teyvatguide_after_all = settings.get("close_teyvatguide_after_all", True)
            if close_teyvatguide_after_all and find_proc("TeyvatGuide.exe"):
                self.log("关闭 TeyvatGuide 进程...")
                kill_proc("TeyvatGuide.exe", graceful=False)
                time.sleep(1)

        self.log("=" * 50)
        self.log(f"完成。成功: {len(executed)}/{total}  {list(executed)}")
        if self.stop_event.is_set():
            self.log("(已中断)")

    def _run_direct_smart(self, acc, gi_exe, gi_config, genshin_proc, timeout,
                         log_date_str, is_last, prev_hutao, skip_ready_check=False,
                         force_close_game=None, force_close_bettergi=None):
        """直接 BetterGI 启动（大号/小号）。
        先启动 BetterGI 自动登录，验证 UID 正确后再用 -startOneDragon 重启执行一条龙。
        返回 (成功, 识别到的UID或空)。
        
        skip_ready_check: 当为 True 时，表示游戏已经在运行中（上一个直接启动任务保留了进程），
                          跳过 wait_genshin_ready 截图检测，直接进入 UID 验证。"""
        cfg = load_config()
        genshin_proc_name = genshin_proc  # 保存进程名，scheduler 分支可能覆盖 genshin_proc
        name = acc["name"]
        expected_uid = acc.get("uid", "").strip()

        # 任务前签到（胡桃签到）
        if acc.get("checkin_before_task", False):
            hutao_name = acc.get("hutao_account", "").strip()
            if hutao_name:
                # 衔接场景下游戏全屏会遮挡胡桃窗口，先最小化游戏
                game_minimized = False
                if skip_ready_check:
                    self.log("最小化游戏窗口，避免遮挡胡桃签到...")
                    game_minimized = minimize_genshin_window()
                    time.sleep(1)
                self.log(f"=== 胡桃签到: {hutao_name} ===")
                _run_hutao_checkin(self.log, self.stop_event, [hutao_name])
                # 签到完成后恢复游戏窗口
                if game_minimized:
                    find_and_activate_window_by_title("原神")
                    time.sleep(0.5)
            else:
                self.log("跳过胡桃签到：未配置胡桃账号名")

        # 快速路径：无 UID 配置，直接启动 OneDragon，跳过验证阶段
        if not expected_uid:
            self.log("无 UID 配置，直接启动 BetterGI 执行一条龙...")
            if find_proc("BetterGI.exe"):
                kill_proc("BetterGI.exe")
                time.sleep(2)
            pid = start_bettergi_onedragon(gi_exe)
            if not pid:
                self.log("[!] BetterGI 启动失败")
                return False, ""
            self.log(f"BetterGI PID={pid}")
            gs = wait_proc_appear(genshin_proc, 180, self.log, self.stop_event)
            if not gs:
                self.log("[!] 原神未启动")
                kill_proc("BetterGI.exe")
                return False, ""
            self.log(f"原神 PID={gs.pid}")
            # 关闭所有可能遮挡游戏的窗口
            minimize_window_by_process("TeyvatGuide.exe")
            if acc.get("checkin_before_task", False) and acc.get("hutao_account", "").strip():
                close_window_by_title("胡桃", self.log)
            time.sleep(5)
            ok = monitor_bettergi_log(log_date_str, timeout, self.log, self.stop_event, genshin_proc_name)
            self.log("一条龙完成，30秒后结束游戏...")
            for _ in range(30):
                if self.stop_event.is_set():
                    break
                time.sleep(1)
            if acc.get("close_bettergi", True):
                kill_proc("BetterGI.exe")
                time.sleep(2)
            do_close = force_close_game if force_close_game is not None else acc.get("close_game", True)
            if do_close:
                kill_proc(genshin_proc)
                time.sleep(3)
            return ok, ""

        if not skip_ready_check:
            if find_proc("BetterGI.exe"):
                self.log("关闭 BetterGI...")
                kill_proc("BetterGI.exe")
                time.sleep(2)

            self.log("启动 BetterGI（自动登录，暂不执行一条龙）...")
            mw_group = cfg.get("uid", {}).get("main_world_detect_group", "")
            if mw_group:
                self.log(f"使用调度组 [{mw_group}] 检测主界面...")
                pid = start_bettergi_with_args(gi_exe, ["--startGroups", mw_group])
            else:
                pid = start_bettergi(gi_exe)
            if not pid:
                self.log("[!] BetterGI 启动失败")
                return False, ""
            self.log(f"BetterGI PID={pid}")
            time.sleep(5)

            # 等待原神进入游戏并验证 UID
            gs = wait_proc_appear(genshin_proc, 180, self.log, self.stop_event)
            if not gs:
                self.log("[!] 原神未启动")
                kill_proc("BetterGI.exe")
                return False, ""
            self.log(f"原神 PID={gs.pid}")

            # 关闭所有可能遮挡游戏的窗口
            minimize_window_by_process("TeyvatGuide.exe")
            if acc.get("checkin_before_task", False) and acc.get("hutao_account", "").strip():
                close_window_by_title("胡桃", self.log)

            # 检测进入游戏主界面 / 开放世界
            mw_group = cfg.get("uid", {}).get("main_world_detect_group", "")
            if mw_group:
                # BetterGI 调度组检测
                self.log(f"使用调度组 [{mw_group}] 检测主界面...")
                detect_ok = monitor_main_world_entered(log_date_str, 300, self.log, self.stop_event)
                if not detect_ok:
                    self.log("[!] 原神可能未进入游戏，继续尝试...")
            else:
                # 截图检测（fallback）
                if not wait_genshin_ready(self.log, self.stop_event):
                    self.log("[!] 原神可能未进入游戏，继续尝试...")
                if not self.stop_event.is_set():
                    self.log("等待界面渲染...")
                    time.sleep(8)
        else:
            self.log("游戏已在运行中，跳过启动和进入检测...")
            time.sleep(3)

        if expected_uid:
            # 轮询等待 UID 可见（BetterGI 全屏独占模式需时间切换）
            max_polls = 3 if skip_ready_check else 6
            if skip_ready_check:
                self.log("游戏已在运行中，快速验证 UID...")
            else:
                self.log("等待游戏进入开放世界（UID 可见）...")
            matched = False
            recognized = ""
            last_wrong_uid = ""
            wrong_streak = 0
            for poll_idx in range(max_polls):
                if self.stop_event.is_set():
                    return False, ""
                time.sleep(8)
                matched, recognized = verify_genshin_uid(
                    expected_uid, self.log, self.stop_event, max_retries=3)
                if matched:
                    break
                if recognized and recognized in expected_uid:
                    # 部分匹配（截断）也算成功
                    self.log(f"UID 部分匹配: 识别={recognized}，期望={expected_uid}")
                    matched = True
                    break
                # 快速失败：连续 2 次识别到同一错误 UID（≥9位完整UID），立即触发切换
                if recognized and recognized != last_wrong_uid:
                    last_wrong_uid = recognized
                    wrong_streak = 1
                elif recognized and recognized == last_wrong_uid:
                    wrong_streak += 1
                    if wrong_streak >= 2 and len(recognized) >= 9:
                        self.log(f"连续 {wrong_streak} 次识别到 UID={recognized}，判定账号错误，立即切换")
                        break
                self.log(f"UID 未检测到，继续等待... ({poll_idx+1}/{max_polls})")
            if not matched:
                self.log(f"[!] UID 不匹配: 识别={recognized}，尝试切换账号...")
                kill_proc("BetterGI.exe")
                time.sleep(3)

                scheduler_groups = acc.get("scheduler_groups", "").strip()
                if scheduler_groups:
                    self.log(f"调度器配置组: {scheduler_groups}")
                    # 确保原神仍在运行
                    if not find_proc("YuanShen.exe"):
                        self.log("[!] 原神进程已退出，重新启动...")
                        kill_proc("BetterGI.exe")
                        time.sleep(2)
                        pid_gs = start_bettergi(gi_exe)
                        if pid_gs:
                            genshin_proc = wait_proc_appear(
                                "YuanShen.exe", timeout=120,
                                log_func=self.log, stop_event=self.stop_event)
                            if not genshin_proc:
                                self.log("[!] 原神启动超时")
                                kill_proc("BetterGI.exe")
                                return False, recognized
                            self.log(f"原神已启动 PID={genshin_proc}")
                            time.sleep(3)
                            kill_proc("BetterGI.exe")
                            time.sleep(2)
                        else:
                            self.log("[!] BetterGI 启动失败")
                            return False, recognized

                    # Phase 1: 执行调度组脚本（--startGroups 只接受配置组名，不能混入 -startOneDragon）
                    self.log(f"启动 BetterGI 执行调度组: {scheduler_groups}...")
                    pid_s = start_bettergi_with_args(gi_exe, ["--startGroups", scheduler_groups])
                    if pid_s:
                        self.log(f"BetterGI PID={pid_s}")
                        group_ok = monitor_config_group(
                            log_date_str, scheduler_groups, timeout, self.log, self.stop_event)
                        if group_ok:
                            self.log(f"调度组 {scheduler_groups} 执行完成")
                            kill_proc("BetterGI.exe")
                            time.sleep(3)

                            # Phase 2: 执行一条龙
                            self.log("启动 BetterGI 执行一条龙...")
                            pid_s2 = start_bettergi_onedragon(gi_exe)
                            if pid_s2:
                                self.log(f"BetterGI PID={pid_s2}")
                                scheduler_ok = monitor_bettergi_log(
                                    log_date_str, timeout, self.log, self.stop_event, genshin_proc_name)
                                if scheduler_ok:
                                    self.log("调度器+一条龙任务完成")
                                else:
                                    self.log("[!] 一条龙任务超时")
                            else:
                                self.log("[!] BetterGI 一条龙启动失败")
                                scheduler_ok = False
                        else:
                            self.log("[!] 调度组执行超时或失败")
                            scheduler_ok = False
                        # 清理 + 返回
                        close_bgi = force_close_bettergi if force_close_bettergi is not None else acc.get("close_bettergi", True)
                        if close_bgi:
                            kill_proc("BetterGI.exe")
                            time.sleep(2)
                        do_close = force_close_game if force_close_game is not None else acc.get("close_game", True)
                        if do_close:
                            kill_proc(genshin_proc)
                            time.sleep(3)
                        return scheduler_ok, ""
                    else:
                        self.log("[!] BetterGI 启动失败")
                        kill_proc(genshin_proc)
                        return False, recognized
                else:
                    # 无调度组，读取 OneDragon 配置检查是否有切换任务
                    config_name = acc["config_name"]
                    od_dir = os.path.join(os.path.dirname(gi_config), "OneDragon")
                    od_conf_path = os.path.join(od_dir, f"{config_name}.json")
                    has_switch = False
                    if os.path.isfile(od_conf_path):
                        try:
                            with open(od_conf_path, "r", encoding="utf-8") as f:
                                od = json.load(f)
                            tasks = od.get("TaskEnabledList", {})
                            has_switch = any(
                                k for k in tasks if k.startswith("切换") and tasks[k])
                        except Exception:
                            pass

                    if has_switch:
                        self.log("检测到切换账号任务，重启 BetterGI 执行切换...")
                        pid2 = start_bettergi_onedragon(gi_exe)
                        if pid2:
                            switched = False
                            for poll_idx in range(8):
                                if self.stop_event.is_set():
                                    kill_proc("BetterGI.exe")
                                    kill_proc(genshin_proc)
                                    return False, ""
                                time.sleep(10)
                                matched2, rec2 = verify_genshin_uid(
                                    expected_uid, self.log, self.stop_event, max_retries=3)
                                if matched2:
                                    switched = True
                                    self.log("账号切换成功!")
                                    break
                                if rec2 and rec2 in expected_uid:
                                    switched = True
                                    self.log(f"UID 部分匹配，切换成功: {rec2}")
                                    break
                                self.log(f"等待切换... ({poll_idx + 1}/8)")
                            if not switched:
                                self.log("[!] 账号切换失败")
                                kill_proc("BetterGI.exe")
                                kill_proc(genshin_proc)
                                return False, recognized
                            # Kill BetterGI again, will restart below with original config
                            kill_proc("BetterGI.exe")
                            time.sleep(3)
                            # Restart BetterGI with original config for the main task
                            self.log("重新启动 BetterGI 执行主任务...")
                            pid3 = start_bettergi_onedragon(gi_exe)
                            if not pid3:
                                self.log("[!] BetterGI 重启失败")
                                kill_proc(genshin_proc)
                                return False, ""
                            time.sleep(3)
                        else:
                            self.log("[!] BetterGI 重启失败")
                            kill_proc(genshin_proc)
                            return False, recognized
                    else:
                        self.log("未配置切换任务，跳过此账号")
                        kill_proc(genshin_proc)
                        return False, recognized
            else:
                # UID 直接匹配，BetterGI 当前是无 -startOneDragon 模式，需重启
                self.log("UID 验证通过，重启 BetterGI 执行一条龙...")
                kill_proc("BetterGI.exe")
                time.sleep(3)
                pid_onedragon = start_bettergi_onedragon(gi_exe)
                if not pid_onedragon:
                    self.log("[!] BetterGI 重启失败")
                    kill_proc(genshin_proc)
                    return False, ""
                self.log(f"BetterGI PID={pid_onedragon}")
                time.sleep(5)

        ok = monitor_bettergi_log(log_date_str, timeout, self.log, self.stop_event, genshin_proc)

        close_game = force_close_game if force_close_game is not None else acc.get("close_game", True)
        close_bettergi = force_close_bettergi if force_close_bettergi is not None else acc.get("close_bettergi", True)

        if close_bettergi:
            self.log("关闭 BetterGI...")
            kill_proc("BetterGI.exe")
            time.sleep(2)
        if close_game:
            self.log("关闭原神...")
            kill_proc(genshin_proc)
            time.sleep(3)

        return ok, ""

    def _run_hutao_smart(self, acc, gi_exe, gi_config, genshin_proc, sh_exe,
                         sh_app_id, timeout, log_date_str, pending,
                         skip_hutao_kill=False, keep_hutao_alive=False,
                         remaining_hutao=0, force_close_game=None,
                         force_close_bettergi=None):
        """通过胡桃工具箱启动。
        返回 (成功, 识别到的UID或空)。
        
        skip_hutao_kill: 为 True 时不杀掉已运行的胡桃进程（2+胡桃批量模式）
        keep_hutao_alive: 是否保持胡桃运行（任务结束后不关闭）
        remaining_hutao: 剩余胡桃任务数（含当前）"""
        cfg = load_config()
        name = acc["name"]
        hutao_account = acc.get("hutao_account", name)

        self.log(f"米游社名称: {hutao_account}")

        # 任务前签到（仅胡桃签到，TeyvatGuide 由全局统一处理）
        if acc.get("checkin_before_task", False):
            hutao_name = acc.get("hutao_account", "").strip()
            if hutao_name:
                self.log(f"=== 胡桃签到: {hutao_name} ===")
                _run_hutao_checkin(self.log, self.stop_event, [hutao_name])
            else:
                self.log("跳过胡桃签到：未配置胡桃账号名")

        # 清理进程：跳过胡桃进程当 skip_hutao_kill 为 True
        for pn in ["BetterGI.exe", genshin_proc,
                   "Snap.Hutao.Remastered.exe",
                   "Snap.Hutao.Remastered.FullTrust.exe"]:
            if skip_hutao_kill and "Hutao" in pn:
                continue  # 保留胡桃进程
            if find_proc(pn):
                kill_proc(pn)
                time.sleep(2)

        self.log("启动胡桃工具箱...")
        if sh_app_id:
            if not start_msix_app(sh_app_id):
                self.log("[!] 胡桃启动失败")
                return False, ""
        else:
            sh_proc = start_exe(sh_exe)
            if not sh_proc:
                self.log("[!] 胡桃启动失败")
                return False, ""

        self.log("等待胡桃窗口...")
        hw = None
        for _ in range(15):
            if self.stop_event.is_set():
                return False, ""
            if sh_app_id:
                activate_hutao_window()
            hw = find_hutao_window()
            if hw:
                break
            time.sleep(2)

        if not hw:
            self.log("[!] 胡桃窗口未出现")
            kill_proc("Snap.Hutao.Remastered.exe")
            kill_proc("Snap.Hutao.Remastered.FullTrust.exe")
            return False, ""

        if not skip_hutao_kill:
            self.log("等待胡桃初始化...")
            time.sleep(5)
        else:
            self.log("胡桃已在运行，跳过初始化等待...")

        if not verify_and_switch_hutao_account(hutao_account, self.log, self.stop_event):
            self.log("等待 15 秒供手动操作...")
            time.sleep(15)

        # 点击启动游戏，检查进程是否出现，失败则重试
        game_launched = False
        for click_idx in range(2):
            if self.stop_event.is_set():
                kill_proc("Snap.Hutao.Remastered.exe")
                kill_proc("Snap.Hutao.Remastered.FullTrust.exe")
                return False, ""
            ok = click_hutao_start_game(self.log, self.stop_event)
            if ok:
                time.sleep(3)
                gs = find_proc(genshin_proc)
                if gs:
                    self.log(f"原神 PID={gs.pid}")
                    game_launched = True
                    break
                if click_idx == 0:
                    self.log("游戏未启动，重试点击...")

        if not game_launched:
            self.log("[!] 无法点击胡桃启动按钮（uiautomation 未安装或窗口异常）")
            self.log("[!] 请安装: pip install uiautomation")
            kill_proc("Snap.Hutao.Remastered.exe")
            kill_proc("Snap.Hutao.Remastered.FullTrust.exe")
            return False, ""

        # 游戏已启动，关闭所有可能遮挡的窗口
        self.log("最小化胡桃和TG窗口...")
        close_window_by_title("胡桃", self.log)
        minimize_window_by_process("TeyvatGuide.exe")

        # 启动 BetterGI 自动登录 + 主界面检测
        mw_group = cfg.get("uid", {}).get("main_world_detect_group", "")
        if mw_group:
            # BetterGI 调度组检测
            self.log(f"启动 BetterGI 自动登录，使用调度组 [{mw_group}] 检测主界面...")
            pid = start_bettergi_with_args(gi_exe, ["start", "--startGroups", mw_group])
            if not pid:
                self.log("[!] BetterGI 启动失败")
                kill_proc(genshin_proc)
                kill_proc("Snap.Hutao.Remastered.exe")
                kill_proc("Snap.Hutao.Remastered.FullTrust.exe")
                return False, ""
            self.log(f"BetterGI PID={pid}")

            detect_ok = monitor_main_world_entered(log_date_str, 300, self.log, self.stop_event)
            if not detect_ok:
                self.log("[!] 原神可能未进入游戏，继续尝试...")
        else:
            # 截图检测（fallback）：检测到白屏时再启动 BetterGI
            def _on_white_start_bettergi_hutao():
                self.log("检测到白屏，启动 BetterGI 自动登录...")
                pid = start_bettergi(gi_exe)
                if not pid:
                    self.log("[!] BetterGI 启动失败")
                else:
                    self.log(f"BetterGI PID={pid}")
            if not wait_genshin_ready(self.log, self.stop_event,
                                      on_white_detected=_on_white_start_bettergi_hutao):
                self.log("[!] 原神可能未进入游戏，继续尝试...")
            if not self.stop_event.is_set():
                time.sleep(8)

        # 胡桃模式下由胡桃管理账号，无需验证 UID
        self.log("游戏已进入（胡桃模式，跳过 UID 验证）")

        # 关闭胡桃逻辑：批量模式下非最后一个任务时不关闭
        close_hutao = acc.get("close_hutao", True)
        if keep_hutao_alive and remaining_hutao > 1:
            close_hutao = False  # 还有后续胡桃任务，保留胡桃运行
            self.log(f"保留胡桃运行（剩余 {remaining_hutao - 1} 个胡桃任务）")
        if close_hutao:
            self.log("关闭胡桃窗口...")
            close_window_by_title("胡桃", self.log)
            time.sleep(1)

        # 关闭 BetterGI 后重新以一条龙启动
        self.log("关闭 BetterGI 后重启执行一条龙...")
        kill_proc("BetterGI.exe")
        time.sleep(3)

        self.log("启动 BetterGI（-startOneDragon）...")
        pid = start_bettergi_onedragon(gi_exe)
        if not pid:
            self.log("[!] BetterGI 启动失败")
            kill_proc(genshin_proc)
            return False, ""
        self.log(f"BetterGI PID={pid}")

        ok = monitor_bettergi_log(log_date_str, timeout, self.log, self.stop_event, genshin_proc)

        close_game = force_close_game if force_close_game is not None else acc.get("close_game", True)
        close_bettergi = force_close_bettergi if force_close_bettergi is not None else acc.get("close_bettergi", True)

        if close_bettergi:
            self.log("关闭 BetterGI...")
            kill_proc("BetterGI.exe")
            time.sleep(2)
        if close_game:
            self.log("关闭原神...")
            kill_proc(genshin_proc)
            time.sleep(3)

        return ok, ""

    def _run_tg_cdp_smart(self, acc, gi_exe, gi_config, genshin_proc, timeout,
                          log_date_str, is_last, keep_tg_alive=False, remaining_tg=0,
                          skip_tg_init=False, force_close_game=None,
                          force_close_bettergi=None,
                          force_close_teyvatguide=None):
        """通过 TeyvatGuide CDP 切换账号并启动游戏，后续接 BetterGI 一条龙。

        流程：启动/连接 TeyvatGuide → 切换账号（按 UID 匹配）→ 点击启动
        → 等待游戏进入 → 启动 BetterGI 自动登录 → 重启执行一条龙 → 监控日志。
        返回 (成功, 识别到的UID或空)。
        """
        cfg = load_config()
        import requests as _requests

        try:
            import websocket
        except ImportError:
            self.log("[TG] [!] websocket-client 未安装，请执行: pip install websocket-client")
            return False, ""

        name = acc["name"]
        expected_uid = acc.get("uid", "").strip()

        # 任务前签到（胡桃签到）
        if acc.get("checkin_before_task", False):
            hutao_name = acc.get("hutao_account", "").strip()
            if hutao_name:
                self.log(f"=== 胡桃签到: {hutao_name} ===")
                _run_hutao_checkin(self.log, self.stop_event, [hutao_name])
            else:
                self.log("跳过胡桃签到：未配置胡桃账号名")

        if not expected_uid:
            self.log(f"[!] 账号「{name}」未配置 UID，TG CDP 启动需要 UID")
            return False, ""

        self.log(f"=== TG CDP 启动: {name} (UID: {expected_uid}) ===")

        # ---- 启动 TeyvatGuide（防重复）----
        if find_proc("TeyvatGuide.exe"):
            self.log("[TG] TeyvatGuide 已在运行，跳过启动")
            hwnd = find_and_activate_window_by_process("TeyvatGuide.exe")
            if hwnd:
                self.log("[TG] TeyvatGuide 窗口已置顶")
            else:
                self.log("[TG] 未找到 TeyvatGuide 窗口（可能已最小化到托盘）")
        else:
            self.log("[TG] 启动 TeyvatGuide...")
            tg_app_id = get_teyvatguide_app_id()
            try:
                subprocess.Popen(
                    ["explorer.exe", f"shell:AppsFolder\\{tg_app_id}"],
                    shell=True,
                )
            except Exception as e:
                self.log(f"[TG] [!] 启动 TeyvatGuide 失败: {e}")
                return False, ""

        if self.stop_event.is_set():
            return False, ""

        # ---- 等待 CDP 端口 9222 就绪 ----
        self.log("[TG] 等待 CDP 调试端口 (127.0.0.1:9222) 就绪（最多 120 秒）...")
        for attempt in range(60):
            if self.stop_event.is_set():
                return False, ""
            try:
                resp = _requests.get("http://127.0.0.1:9222/json/version", timeout=2)
                if resp.status_code == 200:
                    self.log(f"[TG] CDP 端口就绪 (第 {attempt+1} 次尝试)")
                    break
            except Exception:
                pass
            time.sleep(2)
        else:
            self.log("[TG] [!] CDP 端口 9222 超时未就绪（120 秒）")
            return False, ""

        # 等待 TeyvatGuide 完全加载（WebView2 初始化需要时间）
        if not skip_tg_init:
            self.log("[TG] 等待界面加载...")
            time.sleep(5)
        else:
            self.log("[TG] TeyvatGuide 已在运行，跳过界面加载等待...")

        if self.stop_event.is_set():
            return False, ""

        # ---- 查找 TeyvatGuide 页面 CDP 目标 ----
        self.log("[TG] 查找 TeyvatGuide CDP 目标...")
        ws_url = None
        for retry in range(10):
            if self.stop_event.is_set():
                return False, ""
            try:
                targets = _requests.get("http://127.0.0.1:9222/json", timeout=5).json()
                for t in targets:
                    if t.get("type") == "page" and "tauri.localhost" in t.get("url", ""):
                        ws_url = t["webSocketDebuggerUrl"]
                        self.log(f"[TG] 找到 TeyvatGuide 页面: {t['url']}")
                        break
                if ws_url:
                    break
                self.log(f"[TG] 未找到 TeyvatGuide 页面 (retry={retry+1}/10)")
            except Exception as e:
                self.log(f"[TG] 查询 CDP 目标异常: {e} (retry={retry+1}/10)")
            time.sleep(2)
        else:
            self.log("[TG] [!] 未找到 TeyvatGuide 页面，请确认 TeyvatGuide 已启动")
            return False, ""

        # ---- 连接 WebSocket ----
        self.log("[TG] 连接 CDP WebSocket...")
        try:
            ws = websocket.create_connection(ws_url, timeout=15, enable_multithread=True)
        except Exception as e:
            self.log(f"[TG] [!] WebSocket 连接失败: {e}")
            return False, ""

        def _cdp_call(method, params=None, timeout_s=20):
            """发送 CDP 命令并等待响应。返回 result 对象或 None。"""
            msg_id = int(time.time() * 1000) % 999999
            msg = json.dumps({"id": msg_id, "method": method, "params": params or {}})
            try:
                ws.send(msg)
            except Exception as e:
                self.log(f"[TG] [!] CDP 发送失败: {e}")
                return None
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                if self.stop_event.is_set():
                    return None
                try:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        break
                    ws.settimeout(remaining)
                    raw = ws.recv()
                    resp = json.loads(raw)
                    if resp.get("id") == msg_id:
                        if "error" in resp:
                            err = resp["error"]
                            self.log(f"[TG] [!] CDP 错误 {method}: {err.get('message', err)}")
                            return None
                        result = resp.get("result", {})
                        return result.get("value") if "value" in result else result
                except Exception:
                    break
            self.log(f"[TG] [!] CDP {method} 超时 (id={msg_id})")
            return None

        def _cdp_eval(expression, timeout_s=20):
            """执行 JavaScript 并返回结果值。"""
            val = _cdp_call("Runtime.evaluate", {"expression": expression}, timeout_s=timeout_s)
            return val

        try:
            # 启用 Runtime 域
            self.log("[TG] 启用 CDP Runtime 域...")
            res = _cdp_call("Runtime.enable")
            if res is None:
                self.log("[TG] [!] Runtime.enable 失败")
                ws.close()
                return False, ""

            # 排空初始化事件（简单 sleep + 排空）
            time.sleep(1.5)
            ws.settimeout(0.5)
            for _ in range(10):
                try:
                    ws.recv()
                except Exception:
                    break
            ws.settimeout(20)

            # ---- Step 0: 检查当前账号是否已是目标账号 ----
            miyoushe_name = acc.get("hutao_account", name)
            self.log(f"[TG] 米游社名称: {miyoushe_name}")
            current_same = False
            if miyoushe_name:
                result = _cdp_eval(f"""
(function() {{
    var items = document.querySelectorAll('.v-list-item');
    for (var i = 0; i < items.length; i++) {{
        var title = items[i].querySelector('.v-list-item-title');
        if (title && title.textContent.trim() === '添加账号' && i > 0) {{
            var prev = items[i - 1].querySelector('.v-list-item-title');
            return prev ? prev.textContent.trim() : '';
        }}
    }}
    return '';
}})()
""")
                current_name = str(result).strip().strip("'").strip('"') if result else ""
                if current_name:
                    self.log(f"[TG] 当前侧边栏账号: {current_name}")
                    if current_name == miyoushe_name:
                        self.log(f"[TG] 当前账号已是目标账号「{miyoushe_name}」，跳过切换")
                        current_same = True
                    else:
                        self.log(f"[TG] 当前账号「{current_name}」≠ 目标「{miyoushe_name}」，执行切换")
                else:
                    self.log("[TG] 未能读取当前账号名，执行切换")

            if not current_same:
                # ---- Step 1: 点击侧边栏「切换账号」 ----
                self.log("[TG] 点击「切换账号」...")
                result = _cdp_eval("""
(function() {
    var items = document.querySelectorAll('.v-list-item-title');
    for (var i = 0; i < items.length; i++) {
        if (items[i].textContent.trim() === '切换账号') {
            items[i].closest('.v-list-item').click();
            return 'clicked';
        }
    }
    return 'not_found';
})()
""")
                self.log(f"[TG] 切换账号结果: {result}")
                time.sleep(1)

                # ---- Step 2: 在弹出 overlay 中按 UID 查找并点击账号 ----
                self.log(f"[TG] 搜索 UID: {expected_uid}...")
                result = _cdp_eval(f"""
(function() {{
    var subtitles = document.querySelectorAll('.v-list-item-subtitle');
    for (var i = 0; i < subtitles.length; i++) {{
        if (subtitles[i].textContent.indexOf('{expected_uid}') !== -1) {{
            subtitles[i].closest('.v-list-item').click();
            return 'clicked_' + subtitles[i].textContent.trim();
        }}
    }}
    return 'not_found';
}})()
""")
                self.log(f"[TG] 账号选择结果: {result}")
                if result and "not_found" in str(result):
                    self.log(f"[TG] [!] 未找到 UID {expected_uid} 对应的账号")
                    ws.close()
                    return False, ""
                time.sleep(1.5)

            # ---- Step 3: 点击侧边栏账号名打开菜单 ----
            self.log("[TG] 打开账号操作菜单...")
            result = _cdp_eval("""
(function() {
    // 账户名始终在「添加账号」前一项
    var items = document.querySelectorAll('.v-list-item');
    for (var i = 0; i < items.length; i++) {
        var title = items[i].querySelector('.v-list-item-title');
        if (title && title.textContent.trim() === '添加账号' && i > 0) {
            items[i - 1].click();
            var prev = items[i - 1].querySelector('.v-list-item-title');
            return 'clicked_' + (prev ? prev.textContent.trim() : '?');
        }
    }
    return 'not_found';
})()
""")
            self.log(f"[TG] 打开菜单结果: {result}")
            time.sleep(1)

            # ---- Step 4: 点击菜单中的「启动」 ----
            self.log("[TG] 点击「启动」...")
            result = _cdp_eval("""
(function() {
    var items = document.querySelectorAll('.v-list-item-title');
    for (var i = 0; i < items.length; i++) {
        if (items[i].textContent.trim() === '启动') {
            items[i].closest('.v-list-item').click();
            return 'clicked';
        }
    }
    return 'not_found';
})()
""")
            self.log(f"[TG] 启动结果: {result}")
            time.sleep(1)

            ws.close()

            # ---- 等待游戏进程出现 ----
            self.log("[TG] 等待原神启动...")
            gs = wait_proc_appear(genshin_proc, 180, self.log, self.stop_event)
            if not gs:
                self.log("[TG] [!] 原神未启动")
                return False, ""
            self.log(f"[TG] 原神 PID={gs.pid}")

            # 游戏已启动，最小化 TeyvatGuide 避免遮挡
            minimize_window_by_process("TeyvatGuide.exe")
            if acc.get("checkin_before_task", False) and acc.get("hutao_account", "").strip():
                close_window_by_title("胡桃", self.log)

            # 启动 BetterGI 自动登录 + 主界面检测
            mw_group = cfg.get("uid", {}).get("main_world_detect_group", "")
            if mw_group:
                # BetterGI 调度组检测
                self.log(f"[TG] 启动 BetterGI 自动登录，使用调度组 [{mw_group}] 检测主界面...")
                pid = start_bettergi_with_args(gi_exe, ["start", "--startGroups", mw_group])
                if not pid:
                    self.log("[!] BetterGI 启动失败")
                    kill_proc(genshin_proc)
                    return False, ""
                self.log(f"BetterGI PID={pid}")

                detect_ok = monitor_main_world_entered(log_date_str, 300, self.log, self.stop_event)
                if not detect_ok:
                    self.log("[!] 原神可能未进入游戏，继续尝试...")
            else:
                # 截图检测（fallback）：检测到白屏时再启动 BetterGI
                def _on_white_start_bettergi_tg():
                    self.log("[TG] 检测到白屏，启动 BetterGI 自动登录...")
                    pid = start_bettergi(gi_exe)
                    if not pid:
                        self.log("[!] BetterGI 启动失败")
                    else:
                        self.log(f"BetterGI PID={pid}")
                if not wait_genshin_ready(self.log, self.stop_event,
                                          on_white_detected=_on_white_start_bettergi_tg):
                    self.log("[!] 原神可能未进入游戏，继续尝试...")
                if not self.stop_event.is_set():
                    time.sleep(8)

            # TG CDP 已按 UID 切换账号，跳过 UID 验证
            self.log("游戏已进入（TG CDP 模式，跳过 UID 验证）")

            # 关闭 BetterGI 后重新以一条龙模式启动
            kill_proc("BetterGI.exe")
            time.sleep(3)
            self.log("重启 BetterGI 执行一条龙...")
            pid_onedragon = start_bettergi_onedragon(gi_exe)
            if not pid_onedragon:
                self.log("[!] BetterGI 一条龙启动失败")
                kill_proc(genshin_proc)
                return False, ""
            self.log(f"BetterGI PID={pid_onedragon}")

            ok = monitor_bettergi_log(log_date_str, timeout, self.log, self.stop_event, genshin_proc)

            close_game = force_close_game if force_close_game is not None else acc.get("close_game", True)
            close_bettergi = force_close_bettergi if force_close_bettergi is not None else acc.get("close_bettergi", True)
            close_teyvatguide = force_close_teyvatguide if force_close_teyvatguide is not None else acc.get("close_teyvatguide", True)
            if keep_tg_alive and remaining_tg > 1:
                close_teyvatguide = False
                self.log(f"保留 TeyvatGuide 运行（剩余 {remaining_tg - 1} 个 TG CDP 任务）")
            if close_bettergi:
                kill_proc("BetterGI.exe")
                time.sleep(2)
            if close_game:
                kill_proc(genshin_proc)
                time.sleep(3)
            if close_teyvatguide:
                self.log("关闭 TeyvatGuide...")
                close_window_by_title("TeyvatGuide", self.log)

            return ok, expected_uid

        except Exception as e:
            self.log(f"[TG] [!] CDP 操作异常: {e}")
            try:
                ws.close()
            except Exception:
                pass
            return False, ""



# ============================================================
# 添加账号对话框
# ============================================================

class AddAccountDialog(tk.Toplevel):
    def __init__(self, parent, edit_account=None):
        super().__init__(parent)
        # 清除 Tkinter 默认图标
        self.tk.call("wm", "iconbitmap", self._w, "-default", _gen_blank_ico())
        self.iconbitmap(_gen_blank_ico())
        self.result = None
        self.edit_account = edit_account

        self.title("编辑账号" if edit_account else "添加账号")
        self.geometry("420x710")
        self.resizable(False, False)
        self.configure(bg=COLORS["bg"])
        self.transient(parent)
        self.grab_set()

        self._build(edit_account)
        self._center()

    def _center(self):
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        pw = self.master.winfo_width()
        ph = self.master.winfo_height()
        px = self.master.winfo_rootx()
        py = self.master.winfo_rooty()
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"+{x}+{y}")

    def _build(self, edit):
        pad = {"padx": 20, "pady": 5}
        label_fg = COLORS["text"]

        # 名称
        ttk.Label(self, text="账号名称", foreground=label_fg,
                  background=COLORS["bg"]).pack(anchor="w", **pad)
        self.name_var = tk.StringVar(value=edit["name"] if edit else "")
        self.name_entry = ttk.Entry(self, textvariable=self.name_var, width=40)
        self.name_entry.pack(fill="x", padx=20, pady=(0, 8))

        # 类型
        ttk.Label(self, text="启动方式", foreground=label_fg,
                  background=COLORS["bg"]).pack(anchor="w", **pad)
        type_frame = tk.Frame(self, bg=COLORS["bg"])
        type_frame.pack(fill="x", padx=20, pady=(0, 8))
        self.type_var = tk.StringVar(value=edit.get("type", "direct") if edit else "direct")
        ttk.Radiobutton(type_frame, text="直接启动 (BetterGI)", variable=self.type_var,
                        value="direct").pack(side="left", padx=(0, 15))
        ttk.Radiobutton(type_frame, text="胡桃启动", variable=self.type_var,
                        value="hutao").pack(side="left", padx=(0, 15))
        ttk.Radiobutton(type_frame, text="TeyvatGuide", variable=self.type_var,
                        value="tg_cdp").pack(side="left")

        # 配置选择
        ttk.Label(self, text="BetterGI 一条龙配置", foreground=label_fg,
                  background=COLORS["bg"]).pack(anchor="w", **pad)
        configs = discover_onedragon_configs()
        self.config_var = tk.StringVar(value=edit.get("config_name", "") if edit else "")
        self.config_combo = ttk.Combobox(self, textvariable=self.config_var,
                                         values=["（无）"] + configs, state="readonly", width=37)
        self.config_combo.pack(fill="x", padx=20, pady=(0, 8))
        if configs and not self.config_var.get():
            self.config_combo.current(0)

        # 调度器配置组（UID 不匹配时执行）
        ttk.Label(self, text="调度器配置组（UID不匹配时执行）", foreground=label_fg,
                  background=COLORS["bg"]).pack(anchor="w", **pad)
        groups = discover_scheduler_groups()
        saved = edit.get("scheduler_groups", "") if edit else ""
        self.scheduler_var = tk.StringVar(value=saved)
        self.scheduler_combo = ttk.Combobox(self, textvariable=self.scheduler_var,
                                            values=["（无）"] + groups, state="readonly", width=37)
        self.scheduler_combo.pack(fill="x", padx=20, pady=(0, 8))

        # 米游社名称
        ttk.Label(self, text="米游社名称（胡桃/TG共用）", foreground=label_fg,
                  background=COLORS["bg"]).pack(anchor="w", **pad)
        self.hutao_var = tk.StringVar(value=edit.get("hutao_account", "") if edit else "")
        self.hutao_entry = ttk.Entry(self, textvariable=self.hutao_var, width=40)
        self.hutao_entry.pack(fill="x", padx=20, pady=(0, 8))

        # 任务前签到勾选
        self.checkin_var = tk.BooleanVar(value=edit.get("checkin_before_task", False) if edit else False)
        self.checkin_cb = tk.Checkbutton(self, text="执行任务前先用胡桃签到\n（需填写「米游社名称」）", variable=self.checkin_var)
        self.checkin_cb.pack(anchor="w", padx=20, pady=(8, 0))

        self.checkin_var.trace_add("write", self._on_checkin_toggled)

        # 游戏UID
        ttk.Label(self, text="游戏UID（用于验证账号）", foreground=label_fg,
                  background=COLORS["bg"]).pack(anchor="w", **pad)
        self.uid_var = tk.StringVar(value=edit.get("uid", "") if edit else "")
        self.uid_entry = ttk.Entry(self, textvariable=self.uid_var, width=40)
        self.uid_entry.pack(fill="x", padx=20, pady=(0, 8))

        # 进程清理选项
        ttk.Label(self, text="任务完成后关闭软件", foreground=label_fg,
                  background=COLORS["bg"]).pack(anchor="w", **pad)
        chk_frame = tk.Frame(self, bg=COLORS["bg"])
        chk_frame.pack(fill="x", padx=20, pady=(0, 12))

        self.close_game_var = tk.BooleanVar(
            value=edit.get("close_game", True) if edit else True)
        self.close_bettergi_var = tk.BooleanVar(
            value=edit.get("close_bettergi", True) if edit else True)
        self.close_hutao_var = tk.BooleanVar(
            value=edit.get("close_hutao", True) if edit else True)
        self.close_teyvatguide_var = tk.BooleanVar(
            value=edit.get("close_teyvatguide", True) if edit else True)

        row1 = tk.Frame(chk_frame, bg=COLORS["bg"])
        row1.pack(fill="x")
        tk.Checkbutton(row1, text="关闭游戏",
                        variable=self.close_game_var).pack(side="left", padx=(0, 10))
        tk.Checkbutton(row1, text="关闭BetterGI",
                        variable=self.close_bettergi_var).pack(side="left")

        row2 = tk.Frame(chk_frame, bg=COLORS["bg"])
        row2.pack(fill="x")
        tk.Checkbutton(row2, text="关闭胡桃",
                        variable=self.close_hutao_var).pack(side="left", padx=(0, 10))
        tk.Checkbutton(row2, text="关闭 TeyvatGuide",
                        variable=self.close_teyvatguide_var).pack(side="left")

        # 按钮
        btn_frame = tk.Frame(self, bg=COLORS["bg"])
        btn_frame.pack(pady=(5, 15))

        save_btn = tk.Button(btn_frame, text="保存", command=self._save,
                             bg=COLORS["primary"], fg=COLORS["text_white"],
                             activebackground=COLORS["primary_hover"],
                             activeforeground=COLORS["text_white"],
                             relief="flat", font=("Microsoft YaHei", 10),
                             padx=20, pady=3, cursor="hand2", bd=0)
        save_btn.pack(side="left", padx=5)

        cancel_btn = tk.Button(btn_frame, text="取消", command=self.destroy,
                               bg="#E0E0E0", fg=COLORS["text"],
                               relief="flat", font=("Microsoft YaHei", 10),
                               padx=20, pady=3, cursor="hand2", bd=0)
        cancel_btn.pack(side="left", padx=5)

    def _on_checkin_toggled(self, *args):
        pass

    def _save(self):
        # 勾选签到 → 必须填米游社名称
        if self.checkin_var.get() and not self.hutao_var.get().strip():
            messagebox.showwarning("无法保存", "您勾选了「执行任务前先用胡桃签到」，请务必填写「米游社名称」，签到需要用它切换账号。")
            return

        acc_type = self.type_var.get()

        # 直接启动 / TG CDP 启动 → 必须填 UID
        if acc_type in ("direct", "tg_cdp") and not self.uid_var.get().strip():
            messagebox.showwarning("无法保存", "该启动方式必须填写「账号UID」，否则无法识别账号。")
            return

        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("提示", "请输入账号名称", parent=self)
            return
        config_name = self.config_var.get()
        if not config_name:
            messagebox.showwarning("提示", "请选择 BetterGI 配置", parent=self)
            return

        hutao_account = self.hutao_var.get().strip()

        if acc_type == "hutao" and not hutao_account:
            messagebox.showwarning("提示", "请输入胡桃中的账号名称", parent=self)
            return

        if acc_type == "tg_cdp" and not hutao_account:
            messagebox.showwarning("提示", "TG CDP 启动方式必须填写「米游社名称」，用于在 TeyvatGuide 中切换账号。", parent=self)
            return

        self.result = {
            "name": name,
            "type": acc_type,
            "config_name": config_name,
            "hutao_account": hutao_account,
            "uid": self.uid_var.get().strip(),
            "close_game": self.close_game_var.get(),
            "close_bettergi": self.close_bettergi_var.get(),
            "close_hutao": self.close_hutao_var.get(),
            "close_teyvatguide": self.close_teyvatguide_var.get(),
            "scheduler_groups": self.scheduler_var.get(),
            "checkin_before_task": self.checkin_var.get(),
        }
        self.destroy()



# ============================================================
# 快捷键检测弹窗
# ============================================================

class HotkeyDetectDialog(tk.Toplevel):
    """快捷键检测弹窗 - 纯 tkinter 实现，无外部依赖"""
    def __init__(self, parent, callback):
        super().__init__(parent)
        # 清除 Tkinter 默认图标
        self.tk.call("wm", "iconbitmap", self._w, "-default", _gen_blank_ico())
        self.iconbitmap(_gen_blank_ico())
        self.callback = callback
        self.title("检测快捷键")
        self.geometry("360x200")
        self.resizable(False, False)
        self.configure(bg=COLORS["bg"])
        self.transient(parent)
        self.grab_set()

        self.result = None
        self._finished = False
        self._pressed = set()
        self._mod_names = {"Control_L": "ctrl", "Control_R": "ctrl",
                           "Shift_L": "shift", "Shift_R": "shift",
                           "Alt_L": "alt", "Alt_R": "alt",
                           "Meta_L": "win", "Meta_R": "win",
                           "Win_L": "win", "Win_R": "win"}

        tk.Label(self, text="请按下您想要设置的快捷键",
                 font=("Microsoft YaHei", 13), bg=COLORS["bg"],
                 fg=COLORS["text"]).pack(pady=(20, 5))

        tk.Label(self, text="（支持组合键，如 Ctrl+Shift+Q / F8 / `）",
                 font=("Microsoft YaHei", 9), bg=COLORS["bg"],
                 fg=COLORS["text_light"]).pack(pady=(0, 10))

        self.key_display = tk.Label(self, text="等待按键...",
                                    font=("Microsoft YaHei", 20, "bold"),
                                    bg=COLORS["bg"], fg=COLORS["primary"])
        self.key_display.pack(pady=5)

        clear_btn = tk.Button(self, text="清空（不绑定）", command=self._on_clear,
                              bg="#95A5A6", fg="white", relief="flat",
                              font=("Microsoft YaHei", 9), padx=12, pady=3,
                              cursor="hand2", bd=0)
        clear_btn.pack(pady=(0, 5))

        tk.Label(self, text="按 Esc 取消  |  松开组合键确定",
                 font=("Microsoft YaHei", 9), bg=COLORS["bg"],
                 fg=COLORS["text_light"]).pack(pady=(0, 0))

        self._center()

        # 绑定全部按键事件到顶层窗口
        self.bind_all("<KeyPress>", self._on_key_press)
        self.bind_all("<KeyRelease>", self._on_key_release)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def _center(self):
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        pw = self.master.winfo_width()
        ph = self.master.winfo_height()
        px = self.master.winfo_rootx()
        py = self.master.winfo_rooty()
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"+{x}+{y}")

    def _on_clear(self):
        """清空快捷键（设置为空）"""
        self.result = ""
        self._finished = True
        self._unbind_all()
        self._done()

    def _on_cancel(self):
        self._unbind_all()
        self.destroy()

    def _unbind_all(self):
        try:
            self.unbind_all("<KeyPress>")
            self.unbind_all("<KeyRelease>")
        except Exception:
            pass

    def _on_key_press(self, event):
        if self._finished:
            return "break"
        keysym = event.keysym
        # Esc 取消
        if keysym == "Escape":
            self._finished = True
            self._unbind_all()
            self.after(100, self.destroy)
            return "break"
        self._pressed.add(keysym)
        self._update_display()
        return "break"

    def _on_key_release(self, event):
        if self._finished:
            return "break"
        keysym = event.keysym
        # 非修饰键松开 → 组合键完成
        if keysym not in ("Control_L", "Control_R", "Shift_L", "Shift_R",
                          "Alt_L", "Alt_R", "Meta_L", "Meta_R",
                          "Win_L", "Win_R"):
            if self._pressed and not self._finished:
                self._finished = True
                self._unbind_all()
                self.result = self._build_hotkey_str()
                self.after(150, self._done)
                return "break"
        return "break"

    def _build_hotkey_str(self):
        """从 pressed 集合构建 'ctrl+shift+q' 格式字符串"""
        mods = []
        main_keys = []
        for k in self._pressed:
            if k in self._mod_names:
                mods.append(self._mod_names[k])
            else:
                main_keys.append(k.lower())
        # 去重修饰符
        seen = set()
        mods_unique = []
        for m in mods:
            if m not in seen:
                seen.add(m)
                mods_unique.append(m)
        return "+".join(mods_unique + main_keys)

    def _update_display(self):
        if not self._pressed:
            self.key_display.config(text="等待按键...")
            return
        parts = []
        for k in self._pressed:
            name = self._mod_names.get(k, k)
            parts.append(name.upper() if len(name) == 1 else name)
        self.key_display.config(text=" + ".join(parts))

    def _done(self):
        if self.callback is not None and self._finished:
            self.callback(self.result)
        self.destroy()


# ============================================================
# 软件设置对话框
# ============================================================

class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, cfg):
        super().__init__(parent)
        # 清除 Tkinter 默认图标
        self.tk.call("wm", "iconbitmap", self._w, "-default", _gen_blank_ico())
        self.iconbitmap(_gen_blank_ico())
        self.result = False
        self.cfg = cfg

        self.title("软件设置")
        self.geometry("580x650")
        self.resizable(True, True)
        self.configure(bg=COLORS["bg"])
        self.transient(parent)
        self.grab_set()

        self._build()
        self._center()

        # 窗口大小变化时更新滚动条
        self.bind("<Configure>", lambda e: self._update_scrollbar())

    def _center(self):
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        pw = self.master.winfo_width()
        ph = self.master.winfo_height()
        px = self.master.winfo_rootx()
        py = self.master.winfo_rooty()
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"+{x}+{y}")

    def _update_scrollbar(self):
        """根据内容高度决定是否显示滚动条"""
        self.update_idletasks()
        bbox = self._scroll_canvas.bbox("all")
        if bbox is None:
            return
        content_h = bbox[3]
        visible_h = self._scroll_canvas.winfo_height()
        if visible_h <= 0:
            return
        if content_h > visible_h:
            self._scrollbar.pack(side="right", fill="y")
        else:
            self._scrollbar.pack_forget()

    def _build(self):
        cfg = self.cfg
        pad = {"padx": 20, "pady": (6, 2)}
        label_fg = COLORS["text"]
        entry_width = 38

        # ---- 滚动区域 ----
        self._scroll_canvas = tk.Canvas(self, bg=COLORS["bg"], highlightthickness=0)
        self._scrollbar = ttk.Scrollbar(self, orient="vertical", command=self._scroll_canvas.yview)
        self._scroll_canvas.configure(yscrollcommand=self._scrollbar.set)

        self._scroll_canvas.pack(side="left", fill="both", expand=True)
        # 初始隐藏滚动条

        self.inner = tk.Frame(self._scroll_canvas, bg=COLORS["bg"])
        inner_id = self._scroll_canvas.create_window((0, 0), window=self.inner, anchor="nw", tags="inner")

        def _on_inner_configure(event):
            self._scroll_canvas.configure(scrollregion=self._scroll_canvas.bbox("all"))
        self.inner.bind("<Configure>", _on_inner_configure)

        def _on_canvas_configure(event):
            self._scroll_canvas.itemconfig("inner", width=event.width)
        self._scroll_canvas.bind("<Configure>", _on_canvas_configure)

        # 鼠标滚轮
        def _on_mousewheel(event):
            if not self._scroll_canvas.winfo_exists():
                return
            bbox = self._scroll_canvas.bbox("all")
            if not bbox:
                return
            content_h = bbox[3] - bbox[1]
            visible_h = self._scroll_canvas.winfo_height()
            if content_h > visible_h:
                self._scroll_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self._scroll_canvas.bind("<Enter>", lambda e: self._scroll_canvas.bind_all("<MouseWheel>", _on_mousewheel))
        self._scroll_canvas.bind("<Leave>", lambda e: self._scroll_canvas.unbind_all("<MouseWheel>"))

        inner = self.inner  # 别名，后续代码都用到 inner

        def _file_row(label_text, var, default_val, browse_cmd, extra_cfg=None):
            ttk.Label(inner, text=label_text, foreground=label_fg,
                      background=COLORS["bg"]).pack(anchor="w", **pad)
            row = tk.Frame(inner, bg=COLORS["bg"])
            row.pack(fill="x", padx=20, pady=(0, 10))
            entry = ttk.Entry(row, textvariable=var, width=entry_width)
            entry.pack(side="left", fill="x", expand=True)
            btn = tk.Button(row, text="浏览...", command=browse_cmd,
                            bg="#F0F0F0", fg=COLORS["text"],
                            relief="flat", font=("Microsoft YaHei", 9),
                            padx=10, cursor="hand2", bd=0)
            btn.pack(side="left", padx=(5, 0))

        # "路径自动识别"分组框：一键自动识别按钮 + 扫描结果状态标签
        detect_frame = tk.LabelFrame(inner, text="路径自动识别",
                                     bg=COLORS["border"], fg=COLORS["text"],
                                     font=("Microsoft YaHei", 9),
                                     padx=10, pady=8, bd=1, relief="groove")
        detect_frame.pack(fill="x", padx=12, pady=(4, 12))

        tk.Button(detect_frame, text="一键自动识别",
                  command=self._auto_detect_paths,
                  bg="#F0F0F0", fg=COLORS["text"],
                  relief="flat", font=("Microsoft YaHei", 9),
                  padx=10, cursor="hand2", bd=0).pack(anchor="center", pady=(4, 6))

        # 扫描结果状态标签（初始为空）
        self.status_label = tk.Label(detect_frame, text="", bg=COLORS["border"],
                                     fg=COLORS["text_light"],
                                     font=("Microsoft YaHei", 9),
                                     justify="center", anchor="center")
        self.status_label.pack(fill="x", pady=(0, 4))

        # 1. BetterGI 可执行文件
        self.bettergi_exe_var = tk.StringVar(value=cfg["bettergi"]["exe"])
        _file_row("BetterGI 可执行文件 (.exe)", self.bettergi_exe_var,
                  cfg["bettergi"]["exe"],
                  lambda: self._browse_file(self.bettergi_exe_var, [("EXE文件", "*.exe")]))

        # 2. BetterGI 配置文件
        self.bettergi_config_var = tk.StringVar(value=cfg["bettergi"]["config"])
        _file_row("BetterGI 配置文件 (config.json)", self.bettergi_config_var,
                  cfg["bettergi"]["config"],
                  lambda: self._browse_file(self.bettergi_config_var, [("JSON文件", "*.json")]))

        # 3. 原神可执行文件
        self.genshin_exe_var = tk.StringVar(value=cfg["genshin"]["exe"])
        _file_row("原神可执行文件 (.exe)", self.genshin_exe_var,
                  cfg["genshin"]["exe"],
                  lambda: self._browse_file(self.genshin_exe_var, [("EXE文件", "*.exe")]))

        # 4. 原神进程名
        ttk.Label(inner, text="原神进程名", foreground=label_fg,
                  background=COLORS["bg"]).pack(anchor="w", **pad)
        self.genshin_proc_var = tk.StringVar(value=cfg["genshin"]["process_name"])
        ttk.Entry(inner, textvariable=self.genshin_proc_var, width=entry_width).pack(
            fill="x", padx=20, pady=(0, 10))

        # 5. 胡桃工具箱路径 (可选)
        self.hutao_exe_var = tk.StringVar(value=cfg["snap_hutao"]["exe"])
        _file_row("胡桃工具箱路径 (.exe, 可选)", self.hutao_exe_var,
                  cfg["snap_hutao"]["exe"],
                  lambda: self._browse_file(self.hutao_exe_var, [("EXE文件", "*.exe")]))

        # 6. 胡桃 AppID (可选)
        ttk.Label(inner, text="胡桃 AppID (MSIX, 可选)", foreground=label_fg,
                  background=COLORS["bg"]).pack(anchor="w", **pad)
        self.hutao_appid_var = tk.StringVar(value=cfg["snap_hutao"].get("app_id", ""))
        ttk.Entry(inner, textvariable=self.hutao_appid_var, width=entry_width).pack(
            fill="x", padx=20, pady=(0, 10))

        # 6b. TeyvatGuide AppID (可选，为空则用内置默认值)
        ttk.Label(inner, text="TeyvatGuide AppID (MSIX, 可选)", foreground=label_fg,
                  background=COLORS["bg"]).pack(anchor="w", **pad)
        self.teyvatguide_appid_var = tk.StringVar(value=cfg.get("teyvatguide", {}).get("app_id", ""))
        tg_row = tk.Frame(inner, bg=COLORS["bg"])
        tg_row.pack(fill="x", padx=20, pady=(0, 10))
        ttk.Entry(tg_row, textvariable=self.teyvatguide_appid_var, width=entry_width).pack(
            side="left", fill="x", expand=True)
        tk.Label(tg_row, text="  留空则使用内置默认值",
                 bg=COLORS["bg"], fg=COLORS["text_light"],
                 font=("Microsoft YaHei", 8)).pack(side="left")

        # 7. 一条龙超时时间 (秒)
        ttk.Label(inner, text="一条龙超时时间 (秒)", foreground=label_fg,
                  background=COLORS["bg"]).pack(anchor="w", **pad)
        self.timeout_var = tk.IntVar(value=cfg["monitor"]["max_wait_seconds"])
        ttk.Entry(inner, textvariable=self.timeout_var, width=entry_width).pack(
            fill="x", padx=20, pady=(0, 10))

        # 停止快捷键
        ttk.Label(inner, text="全局停止快捷键", foreground=label_fg,
                  background=COLORS["bg"]).pack(anchor="w", **pad)
        self.hotkey_var = tk.StringVar(value=cfg.get("hotkeys", {}).get("stop", "ctrl+shift+q"))
        row = tk.Frame(inner, bg=COLORS["bg"])
        row.pack(fill="x", padx=20, pady=(0, 10))
        entry = ttk.Entry(row, textvariable=self.hotkey_var, width=entry_width - 10, state="readonly")
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Button-1>", lambda e: self._detect_hotkey("stop"))
        tk.Button(row, text="点击设置", command=lambda: self._detect_hotkey("stop"),
                  bg=COLORS["primary"], fg=COLORS["text_white"],
                  relief="flat", font=("Microsoft YaHei", 10, "bold"),
                  padx=16, pady=4, cursor="hand2", bd=0).pack(side="left", padx=(8, 0))
        tk.Label(inner, text="  支持组合键，如 Ctrl+Shift+Q、F8、`",
                 bg=COLORS["bg"], fg=COLORS["text_light"],
                 font=("Microsoft YaHei", 8)).pack(anchor="w", padx=20, pady=(0, 10))

        # 暂停快捷键
        ttk.Label(inner, text="全局暂停/继续快捷键", foreground=label_fg,
                  background=COLORS["bg"]).pack(anchor="w", **pad)
        self.pause_hotkey_var = tk.StringVar(value=cfg.get("hotkeys", {}).get("pause", "ctrl+shift+p"))
        row2 = tk.Frame(inner, bg=COLORS["bg"])
        row2.pack(fill="x", padx=20, pady=(0, 10))
        entry2 = ttk.Entry(row2, textvariable=self.pause_hotkey_var, width=entry_width - 10, state="readonly")
        entry2.pack(side="left", fill="x", expand=True)
        entry2.bind("<Button-1>", lambda e: self._detect_hotkey("pause"))
        tk.Button(row2, text="点击设置", command=lambda: self._detect_hotkey("pause"),
                  bg=COLORS["primary"], fg=COLORS["text_white"],
                  relief="flat", font=("Microsoft YaHei", 10, "bold"),
                  padx=16, pady=4, cursor="hand2", bd=0).pack(side="left", padx=(8, 0))
        tk.Label(inner, text="  支持组合键，如 Ctrl+Shift+P",
                 bg=COLORS["bg"], fg=COLORS["text_light"],
                 font=("Microsoft YaHei", 8)).pack(anchor="w", padx=20, pady=(0, 10))

        # 开始任务快捷键
        ttk.Label(inner, text="全局开始任务快捷键", foreground=label_fg,
                  background=COLORS["bg"]).pack(anchor="w", **pad)
        self.start_hotkey_var = tk.StringVar(value=cfg.get("hotkeys", {}).get("start", ""))
        row3 = tk.Frame(inner, bg=COLORS["bg"])
        row3.pack(fill="x", padx=20, pady=(0, 10))
        entry3 = ttk.Entry(row3, textvariable=self.start_hotkey_var, width=entry_width - 10, state="readonly")
        entry3.pack(side="left", fill="x", expand=True)
        entry3.bind("<Button-1>", lambda e: self._detect_hotkey("start"))
        tk.Button(row3, text="点击设置", command=lambda: self._detect_hotkey("start"),
                  bg=COLORS["primary"], fg=COLORS["text_white"],
                  relief="flat", font=("Microsoft YaHei", 10, "bold"),
                  padx=16, pady=4, cursor="hand2", bd=0).pack(side="left", padx=(8, 0))
        tk.Label(inner, text="  留空则不绑定，支持组合键，如 Ctrl+Shift+S",
                 bg=COLORS["bg"], fg=COLORS["text_light"],
                 font=("Microsoft YaHei", 8)).pack(anchor="w", padx=20, pady=(0, 10))

        # 启动时自动最小化
        self.auto_minimize_var = tk.BooleanVar(value=cfg.get("settings", {}).get("auto_minimize", True))
        tk.Checkbutton(inner, text="启动时自动最小化窗口（避免挡住游戏）",
                        variable=self.auto_minimize_var).pack(anchor="w", padx=20, pady=(0, 10))

        # 关闭窗口时最小化到托盘
        self.minimize_on_close_var = tk.BooleanVar(value=cfg.get("settings", {}).get("minimize_on_close", True))
        tk.Checkbutton(inner, text="关闭窗口时最小化到系统托盘",
                        variable=self.minimize_on_close_var).pack(anchor="w", padx=20, pady=(0, 10))

        # 任务完成后自动关机
        self.auto_shutdown_var = tk.BooleanVar(value=cfg.get("settings", {}).get("auto_shutdown", False))
        tk.Checkbutton(inner, text="所有任务完成后自动关机（60秒倒计时，可取消）",
                        variable=self.auto_shutdown_var).pack(anchor="w", padx=20, pady=(0, 10))

        # 所有任务完成后关闭游戏
        self.close_game_after_all_var = tk.BooleanVar(
            value=cfg.get("settings", {}).get("close_game_after_all", True))
        tk.Checkbutton(inner, text="所有任务完成后关闭原神游戏进程",
                        variable=self.close_game_after_all_var).pack(anchor="w", padx=20, pady=(0, 10))

        # 所有任务完成后关闭 BetterGI
        self.close_bettergi_after_all_var = tk.BooleanVar(
            value=cfg.get("settings", {}).get("close_bettergi_after_all", False))
        tk.Checkbutton(inner, text="所有任务完成后关闭 BetterGI 进程",
                        variable=self.close_bettergi_after_all_var).pack(anchor="w", padx=20, pady=(0, 10))

        # 所有任务完成后关闭胡桃工具箱
        self.close_hutao_after_all_var = tk.BooleanVar(
            value=cfg.get("settings", {}).get("close_hutao_after_all", True))
        tk.Checkbutton(inner, text="所有任务完成后关闭胡桃工具箱",
                        variable=self.close_hutao_after_all_var).pack(anchor="w", padx=20, pady=(0, 10))

        # 所有任务完成后关闭 TeyvatGuide
        self.close_teyvatguide_after_all_var = tk.BooleanVar(
            value=cfg.get("settings", {}).get("close_teyvatguide_after_all", True))
        tk.Checkbutton(inner, text="所有任务完成后关闭 TeyvatGuide",
                        variable=self.close_teyvatguide_after_all_var).pack(anchor="w", padx=20, pady=(0, 10))

        # 手动停止时关闭所有进程（独立于正常完成的三个开关）
        self.stop_closes_all_var = tk.BooleanVar(
            value=cfg.get("settings", {}).get("stop_closes_all_processes", True))
        tk.Checkbutton(inner, text="手动停止时关闭所有进程（游戏/BetterGI/胡桃/TeyvatGuide）",
                        variable=self.stop_closes_all_var).pack(anchor="w", padx=20, pady=(0, 10))

        # 签到完成后自动关闭软件
        self.checkin_close_app_var = tk.BooleanVar(
            value=cfg.get("settings", {}).get("checkin_close_app", False))
        tk.Checkbutton(inner, text="签到任务完成后自动关闭签到软件（定时签到/一键签到）",
                        variable=self.checkin_close_app_var).pack(anchor="w", padx=20, pady=(0, 10))

        # ---- 任务完成后启动软件 ----
        launch_frame = tk.LabelFrame(inner, text="任务完成后启动软件",
                                      font=("Microsoft YaHei", 11, "bold"),
                                      bg="#FFFFFF", fg=COLORS["text"],
                                      padx=12, pady=8)
        launch_frame.pack(fill="x", padx=20, pady=(10, 5))

        self.launch_apps_enabled_var = tk.BooleanVar(
            value=cfg.get("settings", {}).get("launch_apps_enabled", False))
        tk.Checkbutton(launch_frame, text="所有任务完成后启动以下软件",
                        variable=self.launch_apps_enabled_var,
                        bg="#FFFFFF", fg=COLORS["text"],
                        activebackground="#FFFFFF",
                        selectcolor="#FFFFFF").pack(anchor="w")

        # 软件路径列表
        list_frame = tk.Frame(launch_frame, bg="#FFFFFF")
        list_frame.pack(fill="x", pady=(5, 0))

        self.launch_apps_listbox = tk.Listbox(list_frame,
                                               bg="#F7FAFD", fg=COLORS["text"],
                                               selectbackground=COLORS["sel_bg"],
                                               selectforeground=COLORS["text"],
                                               relief="flat", bd=0,
                                               font=("Microsoft YaHei", 9),
                                               height=5)
        self.launch_apps_listbox.pack(fill="x", expand=True)

        btn_row = tk.Frame(launch_frame, bg="#FFFFFF")
        btn_row.pack(fill="x", pady=(5, 0))

        tk.Button(btn_row, text="添加软件路径", command=self._add_launch_app,
                  bg=COLORS["primary"], fg=COLORS["text_white"],
                  activebackground=COLORS["primary_hover"],
                  relief="flat", font=("Microsoft YaHei", 9),
                  padx=12, pady=2, cursor="hand2", bd=0).pack(side="left")
        tk.Button(btn_row, text="删除选中", command=self._del_launch_app,
                  bg=COLORS["danger"], fg=COLORS["text_white"],
                  activebackground="#C0392B",
                  relief="flat", font=("Microsoft YaHei", 9),
                  padx=12, pady=2, cursor="hand2", bd=0).pack(side="left", padx=(8, 0))

        # 加载已有路径
        self._load_launch_apps()

        # 8. Tesseract 安装目录 (可选)
        self.tesseract_var = tk.StringVar(
            value=cfg.get("tesseract", {}).get("path", ""))
        ttk.Label(inner, text="Tesseract OCR 目录 (可选)", foreground=label_fg,
                  background=COLORS["bg"]).pack(anchor="w", **pad)
        row = tk.Frame(inner, bg=COLORS["bg"])
        row.pack(fill="x", padx=20, pady=(0, 10))
        ttk.Entry(row, textvariable=self.tesseract_var, width=entry_width).pack(
            side="left", fill="x", expand=True)
        tk.Button(row, text="浏览...",
                  command=lambda: self._browse_dir(self.tesseract_var),
                  bg="#F0F0F0", fg=COLORS["text"],
                  relief="flat", font=("Microsoft YaHei", 9),
                  padx=10, cursor="hand2", bd=0).pack(side="left", padx=(5, 0))

        # 9. UID 识别方式
        uid_cfg = cfg.get("uid", {})
        uid_method = uid_cfg.get("method", "tesseract")
        uid_group = uid_cfg.get("bettergi_group", "")

        ttk.Label(inner, text="UID 识别方式", foreground=label_fg,
                  background=COLORS["bg"]).pack(anchor="w", **pad)

        self.uid_method_var = tk.StringVar(value=uid_method)
        radio_frame = tk.Frame(inner, bg=COLORS["bg"])
        radio_frame.pack(fill="x", padx=20, pady=(0, 5))
        tk.Radiobutton(radio_frame, text="Tesseract OCR（多阈值投票法）",
                       variable=self.uid_method_var, value="tesseract",
                       bg=COLORS["bg"], fg=COLORS["text"],
                       activebackground=COLORS["bg"], activeforeground=COLORS["primary"],
                       selectcolor=COLORS["bg"],
                       font=("Microsoft YaHei", 9),
                       command=self._on_uid_method_change).pack(anchor="w")
        tk.Radiobutton(radio_frame, text="BetterGI 调度组（更准）",
                       variable=self.uid_method_var, value="bettergi",
                       bg=COLORS["bg"], fg=COLORS["text"],
                       activebackground=COLORS["bg"], activeforeground=COLORS["primary"],
                       selectcolor=COLORS["bg"],
                       font=("Microsoft YaHei", 9),
                       command=self._on_uid_method_change).pack(anchor="w")

        # BetterGI 调度组（下拉列表，仅 bettergi 可见）
        self.uid_group_frame = tk.Frame(inner, bg=COLORS["bg"])
        self.uid_group_frame.pack(fill="x", padx=20, pady=(0, 5))
        ttk.Label(self.uid_group_frame, text="调度组名称",
                  foreground=label_fg, background=COLORS["bg"]).pack(anchor="w")
        self.uid_group_var = tk.StringVar(value=uid_group)
        groups = get_bettergi_groups()
        if uid_group and uid_group not in groups:
            groups.insert(0, uid_group)  # 保留已保存的值在列表中
        self.uid_group_combo = ttk.Combobox(self.uid_group_frame,
                                            textvariable=self.uid_group_var,
                                            values=["（无）"] + groups, state="readonly")
        self.uid_group_combo.pack(fill="x", pady=(2, 0))
        if uid_method != "bettergi":
            self.uid_group_frame.pack_forget()

        # 进入主界面检测方式
        ttk.Label(inner, text="进入主界面检测方式", foreground=label_fg,
                  background=COLORS["bg"]).pack(anchor="w", **pad)

        mw_detect_group = cfg.get("uid", {}).get("main_world_detect_group", "")

        self.main_world_detect_frame = tk.Frame(inner, bg=COLORS["bg"])
        self.main_world_detect_frame.pack(fill="x", padx=20, pady=(2, 5))
        ttk.Label(self.main_world_detect_frame, text="BetterGI 调度组（选「截图检测」则不使用 BetterGI）",
                  foreground=label_fg, background=COLORS["bg"]).pack(anchor="w")
        self.main_world_detect_var = tk.StringVar(
            value=mw_detect_group if mw_detect_group else "（截图检测）")
        mw_groups = get_bettergi_groups()
        mw_options = ["（截图检测）"] + mw_groups
        if mw_detect_group and mw_detect_group not in mw_groups:
            mw_options.insert(1, mw_detect_group)
        self.mw_detect_combo = ttk.Combobox(self.main_world_detect_frame,
                                            textvariable=self.main_world_detect_var,
                                            values=mw_options, state="readonly")
        self.mw_detect_combo.pack(fill="x", pady=(2, 0))

        # TeyvatGuide 全局签到
        checkin_frame = ttk.LabelFrame(inner, text="任务前签到")
        checkin_frame.pack(fill="x", padx=4, pady=(12, 0))

        self.tg_checkin_var = tk.BooleanVar(value=cfg.get("tg_checkin_before_all", False))
        cb_tg = tk.Checkbutton(checkin_frame, text="开始所有任务前执行 TeyvatGuide 签到（一键签到全部账号）", variable=self.tg_checkin_var)
        cb_tg.pack(anchor="w", padx=10, pady=4)

        # 签到方式（一键签到按钮使用）
        method_frame = ttk.LabelFrame(inner, text="一键签到方式")
        method_frame.pack(fill="x", padx=4, pady=(12, 0))

        self.checkin_method_var = tk.StringVar(value=cfg.get("checkin_method", "teyvatguide"))
        rb_tg = ttk.Radiobutton(method_frame, text="TeyvatGuide 签到（CDP 一键全部）", variable=self.checkin_method_var, value="teyvatguide")
        rb_tg.pack(anchor="w", padx=10, pady=2)
        rb_ht = ttk.Radiobutton(method_frame, text="胡桃工具箱签到（UIA 逐个切号）", variable=self.checkin_method_var, value="hutao")
        rb_ht.pack(anchor="w", padx=10, pady=2)

        # 手动添加账号
        add_frame = ttk.Frame(method_frame)
        add_frame.pack(fill="x", padx=10, pady=(6, 0))

        self.hutao_add_var = tk.StringVar()
        add_entry = ttk.Entry(add_frame, textvariable=self.hutao_add_var, width=18)
        add_entry.pack(side="left", padx=(0, 4))
        add_btn = ttk.Button(add_frame, text="添加", command=self._add_hutao_account)
        add_btn.pack(side="left")

        # 胡桃签到账号选择列表
        self.hutao_accounts_frame = ttk.Frame(method_frame)
        self.hutao_accounts_frame.pack(fill="both", expand=True, padx=10, pady=(6, 4))

        canvas = tk.Canvas(self.hutao_accounts_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.hutao_accounts_frame, orient="vertical", command=canvas.yview)
        self.hutao_accounts_inner = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=self.hutao_accounts_inner, anchor="nw")
        canvas.pack(side="left", fill="both", expand=True)

        def _update_hutao_sett_scroll(*args):
            canvas.update_idletasks()
            bbox = canvas.bbox("all")
            if bbox:
                content_h = bbox[3] - bbox[1]
                canvas_h = canvas.winfo_height()
                if content_h > canvas_h:
                    scrollbar.pack(side="right", fill="y")
                    canvas.configure(yscrollcommand=scrollbar.set)
                    canvas.configure(scrollregion=bbox)
                else:
                    scrollbar.pack_forget()
                    canvas.configure(yscrollcommand="")

        self.hutao_accounts_inner.bind("<Configure>", _update_hutao_sett_scroll)
        canvas.bind("<Configure>", _update_hutao_sett_scroll)
        self._hutao_sett_update_scroll = _update_hutao_sett_scroll

        self.hutao_check_vars = {}
        self.hutao_row_frames = {}
        # 从已有账号自动填充
        auto_names = set()
        for acc in cfg.get("accounts", []):
            name = acc.get("hutao_account", "").strip()
            if name:
                auto_names.add(name)

        # 从之前保存的手动列表恢复
        saved = set(cfg.get("checkin_hutao_accounts", []))
        all_names = auto_names | saved
        for name in sorted(all_names):
            is_auto = name in auto_names

            row = ttk.Frame(self.hutao_accounts_inner)
            row.pack(fill="x")
            self.hutao_row_frames[name] = row

            var = tk.BooleanVar(value=name in (saved if saved else auto_names))
            self.hutao_check_vars[name] = var

            cb = tk.Checkbutton(row, text=name, variable=var)
            cb.pack(side="left")

            if not is_auto:
                del_btn = tk.Button(row, text="✕", font=("", 7), relief="flat",
                                   bd=0, padx=3, pady=0, fg="#888",
                                   command=lambda n=name: self._del_hutao_account(n))
                del_btn.pack(side="right")

        def _on_checkin_method_changed(*args):
            if self.checkin_method_var.get() == "hutao":
                self.hutao_accounts_frame.pack(fill="both", expand=True, padx=10, pady=(6, 4))
            else:
                self.hutao_accounts_frame.pack_forget()

        self.checkin_method_var.trace_add("write", _on_checkin_method_changed)
        if self.checkin_method_var.get() != "hutao":
            self.hutao_accounts_frame.pack_forget()

        # 底部按钮
        btn_frame = tk.Frame(inner, bg=COLORS["bg"])
        btn_frame.pack(pady=(15, 15))

        tk.Button(btn_frame, text="保存", command=self._save,
                  bg=COLORS["primary"], fg=COLORS["text_white"],
                  activebackground=COLORS["primary_hover"],
                  activeforeground=COLORS["text_white"],
                  relief="flat", font=("Microsoft YaHei", 10),
                  padx=20, pady=3, cursor="hand2", bd=0).pack(side="left", padx=5)

        tk.Button(btn_frame, text="取消", command=self.destroy,
                  bg="#E0E0E0", fg=COLORS["text"],
                  relief="flat", font=("Microsoft YaHei", 10),
                  padx=20, pady=3, cursor="hand2", bd=0).pack(side="left", padx=5)

        # 初始滚动条检测
        self.after(100, self._update_scrollbar)

    def _add_hutao_account(self):
        name = self.hutao_add_var.get().strip()
        if not name:
            return
        if name in self.hutao_check_vars:
            messagebox.showinfo("提示", f"账号 '{name}' 已在列表中")
            return

        row = ttk.Frame(self.hutao_accounts_inner)
        row.pack(fill="x", before=self.hutao_accounts_inner.winfo_children()[0] if self.hutao_accounts_inner.winfo_children() else None)
        self.hutao_row_frames[name] = row

        var = tk.BooleanVar(value=True)
        self.hutao_check_vars[name] = var

        cb = tk.Checkbutton(row, text=name, variable=var)
        cb.pack(side="left")

        del_btn = tk.Button(row, text="✕", font=("", 7), relief="flat",
                           bd=0, padx=3, pady=0, fg="#888",
                           command=lambda n=name: self._del_hutao_account(n))
        del_btn.pack(side="right")

        self.hutao_add_var.set("")
        self._hutao_sett_update_scroll()

    def _del_hutao_account(self, name):
        if name not in self.hutao_check_vars:
            return
        self.hutao_row_frames[name].destroy()
        del self.hutao_check_vars[name]
        del self.hutao_row_frames[name]
        self._hutao_sett_update_scroll()

    def _browse_file(self, var, filetypes):
        from tkinter import filedialog
        path = filedialog.askopenfilename(filetypes=filetypes)
        if path:
            var.set(path)

    def _browse_dir(self, var):
        from tkinter import filedialog
        path = filedialog.askdirectory()
        if path:
            var.set(path)

    def _auto_detect_paths(self):
        """一键自动识别游戏和工具路径，已有值的输入框不覆盖"""
        import winreg

        # 清空上次结果
        self.status_label.config(text="")

        def _search_exe(keywords, search_dirs):
            """在指定目录列表中递归搜索文件名匹配所有关键词的 exe 文件"""
            results = []
            for base_dir in search_dirs:
                if not os.path.isdir(base_dir):
                    continue
                # 使用 glob 递归搜索所有 exe
                pattern = os.path.join(base_dir, "**", "*.exe")
                for path in glob.glob(pattern, recursive=True):
                    fname = os.path.basename(path).lower()
                    if all(kw.lower() in fname for kw in keywords):
                        results.append(path)
            return results

        def _resolve_shortcut(lnk_path):
            """解析 Windows 快捷方式 (.lnk)，返回目标 exe 路径"""
            try:
                # 使用 WScript.Shell COM 对象解析 .lnk
                ps_cmd = (
                    "$wsh = New-Object -ComObject WScript.Shell; "
                    "$lnk = $wsh.CreateShortcut('" + lnk_path + "'); "
                    "Write-Output $lnk.TargetPath"
                )
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps_cmd],
                    capture_output=True, text=True, timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                )
                target = result.stdout.strip()
                if target and os.path.isfile(target) and target.lower().endswith(".exe"):
                    return target
            except Exception:
                pass
            return None

        def _search_exe_and_shortcuts(keywords, search_dirs):
            """在目录列表中搜索 exe，未找到则尝试解析 .lnk 快捷方式"""
            results = _search_exe(keywords, search_dirs)
            if results:
                return results
            # exe 未找到，尝试 .lnk 快捷方式
            for base_dir in search_dirs:
                if not os.path.isdir(base_dir):
                    continue
                try:
                    for item in os.listdir(base_dir):
                        if not item.lower().endswith(".lnk"):
                            continue
                        if all(kw.lower() in item.lower() for kw in keywords):
                            target = _resolve_shortcut(os.path.join(base_dir, item))
                            if target:
                                results.append(target)
                except Exception:
                    continue
            return results

        # 收集扫描结果
        status_lines = []
        found_count = 0

        # 环境变量 & 桌面路径
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        appdata = os.environ.get("APPDATA", "")
        user_desktop = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
        public_desktop = "C:\\Users\\Public\\Desktop"

        # ---- 1. BetterGI.exe ----
        if self.bettergi_exe_var.get().strip():
            status_lines.append("- BetterGI 可执行文件 (已有值，跳过)")
        else:
            search_dirs = []
            if local_appdata:
                search_dirs.append(os.path.join(local_appdata, "Programs", "BetterGI"))
            if appdata:
                search_dirs.append(os.path.join(appdata, "BetterGI"))
            for d in [user_desktop, public_desktop]:
                if os.path.isdir(d):
                    search_dirs.append(d)

            results = _search_exe_and_shortcuts(["bettergi"], search_dirs)
            if results:
                bettergi_exe = results[0]
                self.bettergi_exe_var.set(bettergi_exe)
                status_lines.append("✓ BetterGI 可执行文件 已识别")
                found_count += 1

                # ---- 2. BetterGI config.json（与 exe 同目录） ----
                if self.bettergi_config_var.get().strip():
                    status_lines.append("- BetterGI 配置文件 (已有值，跳过)")
                else:
                    config_path = os.path.join(os.path.dirname(bettergi_exe), "config.json")
                    if os.path.isfile(config_path):
                        self.bettergi_config_var.set(config_path)
                        status_lines.append("✓ BetterGI 配置文件 已识别")
                        found_count += 1
                    else:
                        status_lines.append("✗ BetterGI 配置文件 未找到")
            else:
                status_lines.append("✗ BetterGI 可执行文件 未找到")
                # BetterGI.exe 没找到，config 也无法定位
                status_lines.append("✗ BetterGI 配置文件 未找到")

        # ---- 3. 原神 YuanShen.exe ----
        if self.genshin_exe_var.get().strip():
            status_lines.append("- 原神可执行文件 (已有值，跳过)")
        else:
            genshin_path = None

            # 3a. 注册表 Uninstall 信息
            try:
                uninstall_keys = [
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\原神",
                    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\原神",
                ]
                for uk in uninstall_keys:
                    try:
                        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, uk)
                        for field in ("InstallLocation", "DisplayIcon"):
                            try:
                                val, _ = winreg.QueryValueEx(key, field)
                                if val:
                                    candidate = (
                                        val if val.lower().endswith(".exe")
                                        else os.path.join(val, "YuanShen.exe")
                                    )
                                    if os.path.isfile(candidate):
                                        genshin_path = candidate
                                        break
                            except Exception:
                                continue
                        winreg.CloseKey(key)
                        if genshin_path:
                            break
                    except Exception:
                        continue
            except Exception:
                pass

            # 3b. 常见安装路径（多盘符搜索）
            if not genshin_path:
                for drive in ("C:\\", "D:\\", "E:\\", "F:\\", "G:\\"):
                    candidate = os.path.join(
                        drive, "Genshin Impact", "Genshin Impact Game", "YuanShen.exe")
                    if os.path.isfile(candidate):
                        genshin_path = candidate
                        break

            if genshin_path:
                self.genshin_exe_var.set(genshin_path)
                status_lines.append("✓ 原神可执行文件 已识别")
                found_count += 1
            else:
                status_lines.append("✗ 原神可执行文件 未找到")

        # ---- 4. 胡桃工具箱 Snap.Hutao.exe ----
        if self.hutao_exe_var.get().strip():
            status_lines.append("- 胡桃工具箱路径 (已有值，跳过)")
        else:
            search_dirs = []
            if local_appdata:
                search_dirs.append(os.path.join(local_appdata, "Programs", "Snap Hutao"))
                # 考虑到某些版本直接在 Programs 下
                search_dirs.append(os.path.join(local_appdata, "Programs"))
            for d in [user_desktop, public_desktop]:
                if os.path.isdir(d):
                    search_dirs.append(d)

            results = _search_exe_and_shortcuts(["snap", "hutao"], search_dirs)
            if not results:
                results = _search_exe_and_shortcuts(["hutao"], search_dirs)
            if results:
                self.hutao_exe_var.set(results[0])
                status_lines.append("✓ 胡桃工具箱路径 已识别")
                found_count += 1
            else:
                status_lines.append("✗ 胡桃工具箱路径 未找到")

        # ---- 5. Tesseract OCR ----
        if self.tesseract_var.get().strip():
            status_lines.append("- Tesseract OCR 目录 (已有值，跳过)")
        else:
            tess_candidates = [
                "C:\\Program Files\\Tesseract-OCR\\tesseract.exe",
                "C:\\Program Files (x86)\\Tesseract-OCR\\tesseract.exe",
            ]
            found = False
            for tp in tess_candidates:
                if os.path.isfile(tp):
                    self.tesseract_var.set(tp)
                    found = True
                    break
            if found:
                status_lines.append("✓ Tesseract OCR 目录 已识别")
                found_count += 1
            else:
                status_lines.append("✗ Tesseract OCR 目录 未找到")

        # ---- 6. 胡桃工具箱 AppID（MSIX） ----
        if self.hutao_appid_var.get().strip():
            status_lines.append("- 胡桃工具箱 AppID (已有值，跳过)")
        else:
            try:
                result = subprocess.run(
                    ["powershell", "-Command",
                     "Get-StartApps | Where-Object { $_.Name -like '*胡桃*' -or $_.Name -like '*Hutao*' } | Select-Object -ExpandProperty AppId"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                )
                appid = result.stdout.strip()
                if appid:
                    self.hutao_appid_var.set(appid)
                    status_lines.append("✓ 胡桃工具箱 AppID 已识别")
                    found_count += 1
                else:
                    status_lines.append("✗ 胡桃工具箱 AppID 未找到")
            except Exception:
                status_lines.append("✗ 胡桃工具箱 AppID 未找到")

        # ---- 7. TeyvatGuide AppID（MSIX） ----
        if self.teyvatguide_appid_var.get().strip():
            status_lines.append("- TeyvatGuide AppID (已有值，跳过)")
        else:
            try:
                result = subprocess.run(
                    ["powershell", "-Command",
                     "Get-StartApps | Where-Object { $_.Name -like '*TeyvatGuide*' -or $_.Name -like '*Teyvat*' } | Select-Object -ExpandProperty AppId"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                )
                appid = result.stdout.strip()
                if appid:
                    self.teyvatguide_appid_var.set(appid)
                    status_lines.append("✓ TeyvatGuide AppID 已识别")
                    found_count += 1
                else:
                    status_lines.append("✗ TeyvatGuide AppID 未找到")
            except Exception:
                status_lines.append("✗ TeyvatGuide AppID 未找到")

        # 汇总：如果全部跳过，显示提示；否则追加统计
        if found_count == 0 and all("跳过" in line for line in status_lines):
            status_lines.append("所有路径已设置，无需自动识别")
        elif found_count > 0:
            status_lines.append(
                f"已自动填入 {found_count} 项路径，其余请手动设置")

        self.status_label.config(text="\n".join(status_lines))

    def _add_launch_app(self):
        """添加启动软件路径"""
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="选择可执行文件",
            filetypes=[("EXE 文件", "*.exe"), ("所有文件", "*.*")])
        if path:
            self.launch_apps_listbox.insert(tk.END, path)

    def _del_launch_app(self):
        """删除选中的启动软件路径"""
        sel = self.launch_apps_listbox.curselection()
        if sel:
            self.launch_apps_listbox.delete(sel[0])

    def _load_launch_apps(self):
        """从配置加载启动软件路径列表"""
        apps = self.cfg.get("settings", {}).get("launch_apps_after_all", [])
        for app in apps:
            self.launch_apps_listbox.insert(tk.END, app)

    def _detect_hotkey(self, target="stop"):
        def on_detected(hotkey_str):
            if target == "pause":
                self.pause_hotkey_var.set(hotkey_str)
            elif target == "start":
                self.start_hotkey_var.set(hotkey_str)
            else:
                self.hotkey_var.set(hotkey_str)
        HotkeyDetectDialog(self, on_detected)

    def _on_uid_method_change(self):
        """切换 UID 识别方式时显示/隐藏调度组名称输入框"""
        if self.uid_method_var.get() == "bettergi":
            self.uid_group_frame.pack(fill="x", padx=20, pady=(0, 5))
        else:
            self.uid_group_frame.pack_forget()

    def _save(self):
        self.cfg["bettergi"]["exe"] = self.bettergi_exe_var.get().strip()
        self.cfg["bettergi"]["config"] = self.bettergi_config_var.get().strip()
        self.cfg["genshin"]["exe"] = self.genshin_exe_var.get().strip()
        self.cfg["genshin"]["process_name"] = self.genshin_proc_var.get().strip()
        self.cfg["snap_hutao"]["exe"] = self.hutao_exe_var.get().strip()
        self.cfg["snap_hutao"]["app_id"] = self.hutao_appid_var.get().strip()
        self.cfg.setdefault("teyvatguide", {})
        self.cfg["teyvatguide"]["app_id"] = self.teyvatguide_appid_var.get().strip()
        self.cfg["monitor"]["max_wait_seconds"] = self.timeout_var.get()
        self.cfg.setdefault("tesseract", {})
        self.cfg["tesseract"]["path"] = self.tesseract_var.get().strip()
        self.cfg.setdefault("hotkeys", {})
        self.cfg["hotkeys"]["stop"] = self.hotkey_var.get().strip()
        self.cfg["hotkeys"]["pause"] = self.pause_hotkey_var.get().strip()
        self.cfg["hotkeys"]["start"] = self.start_hotkey_var.get().strip()
        self.cfg.setdefault("settings", {})
        self.cfg["settings"]["auto_minimize"] = self.auto_minimize_var.get()
        self.cfg["settings"]["minimize_on_close"] = self.minimize_on_close_var.get()
        self.cfg["settings"]["auto_shutdown"] = self.auto_shutdown_var.get()
        self.cfg["settings"]["close_game_after_all"] = self.close_game_after_all_var.get()
        self.cfg["settings"]["close_bettergi_after_all"] = self.close_bettergi_after_all_var.get()
        self.cfg["settings"]["close_hutao_after_all"] = self.close_hutao_after_all_var.get()
        self.cfg["settings"]["close_teyvatguide_after_all"] = self.close_teyvatguide_after_all_var.get()
        self.cfg["settings"]["stop_closes_all_processes"] = self.stop_closes_all_var.get()
        self.cfg["settings"]["checkin_close_app"] = self.checkin_close_app_var.get()
        self.cfg["settings"]["launch_apps_enabled"] = self.launch_apps_enabled_var.get()
        self.cfg["settings"]["launch_apps_after_all"] = list(self.launch_apps_listbox.get(0, tk.END))
        self.cfg.setdefault("uid", {})
        self.cfg["uid"]["method"] = self.uid_method_var.get()
        self.cfg["uid"]["bettergi_group"] = self.uid_group_var.get().strip()
        mw_detect_val = self.main_world_detect_var.get().strip()
        self.cfg["uid"]["main_world_detect_group"] = "" if mw_detect_val == "（截图检测）" else mw_detect_val
        self.cfg["tg_checkin_before_all"] = self.tg_checkin_var.get()
        self.cfg["checkin_method"] = self.checkin_method_var.get()
        if self.checkin_method_var.get() == "hutao":
            self.cfg["checkin_hutao_accounts"] = [name for name, var in self.hutao_check_vars.items() if var.get()]
        save_config(self.cfg)
        self.result = True
        self.destroy()


# ============================================================
# 简易日历选择弹窗
# ============================================================

class CalendarDialog(tk.Toplevel):
    """日历选择弹窗，支持单选和多选"""
    MONTH_NAMES = ["一月", "二月", "三月", "四月", "五月", "六月",
                   "七月", "八月", "九月", "十月", "十一月", "十二月"]
    WEEKDAY_HEADER = ["一", "二", "三", "四", "五", "六", "日"]

    def __init__(self, parent, multi_select=False, selected_dates=None,
                 title="选择日期", callback=None):
        super().__init__(parent)
        # 清除 Tkinter 默认图标
        self.tk.call("wm", "iconbitmap", self._w, "-default", _gen_blank_ico())
        self.iconbitmap(_gen_blank_ico())
        self.multi_select = multi_select
        self.selected = set(selected_dates or [])
        self.callback = callback

        self.title(title)
        self.resizable(False, False)
        self.configure(bg="#FFFFFF")
        self.transient(parent)
        self.grab_set()

        today = datetime.now()
        self._year = today.year
        self._month = today.month

        self._build()
        self._draw_calendar()
        self._center()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _center(self):
        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()
        pw = self.master.winfo_width()
        ph = self.master.winfo_height()
        px = self.master.winfo_rootx()
        py = self.master.winfo_rooty()
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"+{x}+{y}")

    def _on_close(self):
        if self.callback:
            if self.multi_select:
                self.callback(sorted(self.selected))
            else:
                self.callback(None)
        self.destroy()

    def _build(self):
        # 导航栏
        nav = tk.Frame(self, bg="#FFFFFF")
        nav.pack(fill="x", padx=8, pady=(8, 4))

        self.prev_btn = tk.Button(nav, text="◀", command=self._prev_month,
                                   relief="flat", bg="#FFFFFF",
                                   fg=COLORS["primary"],
                                   font=("Microsoft YaHei", 10),
                                   cursor="hand2", bd=0)
        self.prev_btn.pack(side="left")
        self.month_label = tk.Label(nav, bg="#FFFFFF", fg=COLORS["text"],
                                     font=("Microsoft YaHei", 11, "bold"))
        self.month_label.pack(side="left", expand=True)
        self.next_btn = tk.Button(nav, text="▶", command=self._next_month,
                                   relief="flat", bg="#FFFFFF",
                                   fg=COLORS["primary"],
                                   font=("Microsoft YaHei", 10),
                                   cursor="hand2", bd=0)
        self.next_btn.pack(side="right")

        # 星期头
        wd_frame = tk.Frame(self, bg="#F0F4F8")
        wd_frame.pack(fill="x", padx=8)
        for wd in self.WEEKDAY_HEADER:
            tk.Label(wd_frame, text=wd, bg="#F0F4F8",
                     fg=COLORS["text_light"], font=("Microsoft YaHei", 9),
                     width=4).pack(side="left", padx=1, pady=3)

        # 日期格子容器
        self.grid_frame = tk.Frame(self, bg="#FFFFFF")
        self.grid_frame.pack(fill="both", padx=8, pady=(2, 4))

        # 底部
        bottom = tk.Frame(self, bg="#FFFFFF")
        bottom.pack(fill="x", padx=8, pady=(0, 8))

        if self.multi_select:
            self.confirm_btn = tk.Button(
                bottom, text=f"确定 ({len(self.selected)} 天)",
                bg=COLORS["primary"], fg=COLORS["text_white"],
                relief="flat", font=("Microsoft YaHei", 9),
                padx=16, pady=3, cursor="hand2", bd=0,
                command=self._confirm)
            self.confirm_btn.pack(side="right")
            # 清除按钮
            tk.Button(bottom, text="清除", command=self._clear_all,
                      bg="#EEF2F7", fg=COLORS["text"], relief="flat",
                      font=("Microsoft YaHei", 9), padx=12, pady=3,
                      cursor="hand2", bd=0).pack(side="right", padx=(0, 6))

        tk.Button(bottom, text="取消", command=self._on_close,
                  bg="#EEF2F7", fg=COLORS["text"], relief="flat",
                  font=("Microsoft YaHei", 9), padx=12, pady=3,
                  cursor="hand2", bd=0).pack(side="right", padx=(0, 6))

    def _draw_calendar(self):
        for w in self.grid_frame.winfo_children():
            w.destroy()

        self.month_label.config(
            text=f"{self._year}年 {self.MONTH_NAMES[self._month - 1]}")

        import calendar
        cal = calendar.monthcalendar(self._year, self._month)
        today = datetime.now().strftime("%Y-%m-%d")

        for week_idx, week in enumerate(cal):
            row_frame = tk.Frame(self.grid_frame, bg="#FFFFFF")
            row_frame.pack(fill="x")
            for day_idx, day in enumerate(week):
                if day == 0:
                    lbl = tk.Label(row_frame, text="", bg="#FFFFFF",
                                    width=4, height=1)
                    lbl.pack(side="left", padx=1, pady=1)
                else:
                    date_str = f"{self._year}-{self._month:02d}-{day:02d}"
                    is_today = (date_str == today)
                    is_sel = (date_str in self.selected)

                    bg = "#FFFFFF"
                    fg = COLORS["text"]
                    if is_sel:
                        bg = COLORS["primary"]
                        fg = COLORS["text_white"]
                    elif is_today:
                        bg = "#E8F4FD"
                        fg = COLORS["primary"]

                    lbl = tk.Label(row_frame, text=str(day), bg=bg, fg=fg,
                                    font=("Microsoft YaHei", 9),
                                    width=4, height=1, cursor="hand2")
                    lbl.pack(side="left", padx=1, pady=1)

                    if self.multi_select:
                        lbl.bind("<Button-1>",
                                 lambda e, ds=date_str: self._toggle_date(ds))
                    else:
                        lbl.bind("<Button-1>",
                                 lambda e, ds=date_str: self._pick_single(ds))

    def _prev_month(self):
        if self._month == 1:
            self._month = 12
            self._year -= 1
        else:
            self._month -= 1
        self._draw_calendar()

    def _next_month(self):
        if self._month == 12:
            self._month = 1
            self._year += 1
        else:
            self._month += 1
        self._draw_calendar()

    def _toggle_date(self, date_str):
        if date_str in self.selected:
            self.selected.discard(date_str)
        else:
            self.selected.add(date_str)
        self.confirm_btn.config(text=f"确定 ({len(self.selected)} 天)")
        self._draw_calendar()

    def _pick_single(self, date_str):
        if self.callback:
            self.callback(date_str)
        self.destroy()

    def _confirm(self):
        if self.callback:
            self.callback(sorted(self.selected))
        self.destroy()

    def _clear_all(self):
        self.selected.clear()
        self.confirm_btn.config(text="确定 (0 天)")
        self._draw_calendar()


# ============================================================
# 定时任务管理对话框
# ============================================================

class SchedulerDialog(tk.Toplevel):
    WEEKDAY_NAMES = ["一", "二", "三", "四", "五", "六", "日"]
    QUICK_TIMES = [("凌晨0", "00", "00"), ("凌晨2", "02", "00"), ("凌晨4", "04", "00"),
                   ("早6点", "06", "00"), ("早8点", "08", "00"), ("早10点", "10", "00"),
                   ("午12点", "12", "00"), ("下2点", "14", "00"), ("下4点", "16", "00"),
                   ("晚6点", "18", "00"), ("晚8点", "20", "00"), ("晚10点", "22", "00")]

    def __init__(self, parent, gui):
        super().__init__(parent)
        # 清除 Tkinter 默认图标
        self.tk.call("wm", "iconbitmap", self._w, "-default", _gen_blank_ico())
        self.iconbitmap(_gen_blank_ico())
        self.gui = gui
        self.cfg = load_scheduler_config()

        self.title("定时任务管理")
        self.minsize(500, 300)
        self.resizable(True, True)
        self.geometry("650x580")
        self.configure(bg=COLORS["bg"])
        self.transient(parent)
        self.grab_set()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # 顶部栏（固定在顶部，先 pack 确保正确层级）
        top_bar = tk.Frame(self, bg=COLORS["primary_dark"])
        top_bar.pack(fill="x")

        tk.Label(top_bar, text="  定时任务管理", bg=COLORS["primary_dark"],
                 fg=COLORS["text_white"], font=("Microsoft YaHei", 10, "bold")).pack(side="left", pady=6)

        self.autostart_var = tk.BooleanVar(value=is_autostart_enabled())
        tk.Checkbutton(top_bar, text="开机自启", variable=self.autostart_var,
                       command=self._toggle_autostart).pack(side="right", padx=(0, 10))

        self.auto_shutdown_var = tk.BooleanVar(
            value=self.gui.cfg.get("settings", {}).get("auto_shutdown", False))
        tk.Checkbutton(top_bar, text="完成自动关机", variable=self.auto_shutdown_var,
                       command=self._toggle_auto_shutdown).pack(
            side="right", padx=(0, 10))

        self.toggle_btn = tk.Button(top_bar, text="定时器", command=self._toggle,
                                    bg="#52C41A", fg="#FFFFFF",
                                    activebackground="#389E0D",
                                    relief="flat", font=("Microsoft YaHei", 9),
                                    padx=14, pady=2, cursor="hand2", bd=0)
        self.toggle_btn.pack(side="right", padx=(0, 6))

        self._build()
        self._center()
        self.refresh_btn()

    def _center(self):
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        pw = self.master.winfo_width()
        ph = self.master.winfo_height()
        px = self.master.winfo_rootx()
        py = self.master.winfo_rooty()
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"+{x}+{y}")

        # 初始触发滚动条检测
        self.after(100, self._update_main_scrollbar)

    def _on_close(self):
        self.destroy()

    def _build(self):
        """单栏布局：固定顶部栏 → 下方可滚动内容"""
        # --- 可滚动主容器 ---
        self.main_scroll_canvas = tk.Canvas(self, bg=COLORS["bg"], highlightthickness=0)
        self.main_scrollbar = ttk.Scrollbar(self, orient="vertical",
                                           command=self.main_scroll_canvas.yview)
        self.main_scroll_canvas.configure(yscrollcommand=self.main_scrollbar.set)
        self.main_scroll_canvas.pack(side="left", fill="both", expand=True)
        # 滚动条初始隐藏，由 _update_main_scrollbar 控制显隐

        scroll_canvas = self.main_scroll_canvas  # 局部别名，兼容后续闭包
        content = tk.Frame(scroll_canvas, bg=COLORS["bg"])
        win_id = scroll_canvas.create_window((0, 0), window=content, anchor="nw")

        content.bind("<Configure>",
                     lambda e: scroll_canvas.configure(
                         scrollregion=scroll_canvas.bbox("all")))

        def _on_canvas_configure(event):
            scroll_canvas.itemconfig(win_id, width=event.width)
            self.after(50, self._update_main_scrollbar)

        scroll_canvas.bind("<Configure>", _on_canvas_configure)

        def _scroll_mousewheel(event):
            if scroll_canvas.winfo_exists():
                bbox = scroll_canvas.bbox("all")
                if bbox and bbox[3] > scroll_canvas.winfo_height():
                    scroll_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        content.bind("<MouseWheel>", _scroll_mousewheel)
        scroll_canvas.bind("<MouseWheel>", _scroll_mousewheel)
        self.bind("<MouseWheel>", _scroll_mousewheel)  # 窗口级兜底

        # --- 添加任务区域 ---
        add_frame = tk.Frame(content, bg="#FFFFFF", relief="flat", bd=1)
        add_frame.pack(fill="x", padx=12, pady=(12, 6))

        tk.Label(add_frame, text="添加定时任务", font=("Microsoft YaHei", 12, "bold"),
                 bg="#FFFFFF", fg=COLORS["text"]).pack(anchor="w", padx=10, pady=(6, 6))

        # 模式 + 时间同行
        mt_row = tk.Frame(add_frame, bg="#FFFFFF")
        mt_row.pack(fill="x", padx=10, pady=(0, 6))

        tk.Label(mt_row, text="执行模式:", bg="#FFFFFF", fg=COLORS["text_light"],
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=(0, 6))
        self.mode_var = tk.StringVar(value="daily")
        for text, val in [("每天", "daily"), ("每周", "weekly"),
                          ("一次性", "once"), ("指定日期", "dates")]:
            tk.Radiobutton(mt_row, text=text, value=val, variable=self.mode_var,
                           bg="#FFFFFF", fg=COLORS["text"], selectcolor="#FFFFFF",
                           command=self._on_mode_change,
                           font=("Microsoft YaHei", 9)).pack(side="left", padx=(0, 4))

        # 快捷时间（两排）
        q1 = tk.Frame(add_frame, bg="#FFFFFF")
        q1.pack(fill="x", padx=10, pady=(4, 3))
        q2 = tk.Frame(add_frame, bg="#FFFFFF")
        q2.pack(fill="x", padx=10, pady=(0, 2))
        for i, (label, h, m) in enumerate(self.QUICK_TIMES):
            parent = q1 if i < 6 else q2
            tk.Button(parent, text=label,
                      command=lambda hh=h, mm=m: self._set_time(hh, mm),
                      bg="#EEF2F7", fg=COLORS["text"], relief="flat",
                      font=("Microsoft YaHei", 8), padx=6, cursor="hand2", bd=0).pack(side="left", padx=(0, 5))

        # 时间输入 + 微调同行
        t_row = tk.Frame(add_frame, bg="#FFFFFF")
        t_row.pack(fill="x", padx=10, pady=(2, 4))

        # 小时区
        tk.Button(t_row, text="◀", command=lambda: self._adj_hour(-1),
                  bg="#EEF2F7", fg=COLORS["text"], relief="flat",
                  font=("Microsoft YaHei", 8), padx=3, cursor="hand2", bd=0,
                  width=2).pack(side="left", padx=(0, 2))
        self.hour_var = tk.StringVar(value="08")
        ttk.Combobox(t_row, textvariable=self.hour_var, width=3,
                     values=[f"{h:02d}" for h in range(24)],
                     state="normal", font=("Microsoft YaHei", 11)).pack(side="left")
        tk.Button(t_row, text="▶", command=lambda: self._adj_hour(1),
                  bg="#EEF2F7", fg=COLORS["text"], relief="flat",
                  font=("Microsoft YaHei", 8), padx=3, cursor="hand2", bd=0,
                  width=2).pack(side="left")
        tk.Label(t_row, text="时", bg="#FFFFFF", fg=COLORS["text"],
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=(2, 8))

        # 分钟区
        tk.Button(t_row, text="◀", command=lambda: self._adj_minutes(-1),
                  bg="#EEF2F7", fg=COLORS["text"], relief="flat",
                  font=("Microsoft YaHei", 8), padx=3, cursor="hand2", bd=0,
                  width=2).pack(side="left", padx=(0, 2))
        self.minute_var = tk.StringVar(value="00")
        ttk.Combobox(t_row, textvariable=self.minute_var, width=3,
                     values=[f"{m:02d}" for m in range(60)],
                     state="normal", font=("Microsoft YaHei", 11)).pack(side="left")
        tk.Button(t_row, text="▶", command=lambda: self._adj_minutes(1),
                  bg="#EEF2F7", fg=COLORS["text"], relief="flat",
                  font=("Microsoft YaHei", 8), padx=3, cursor="hand2", bd=0,
                  width=2).pack(side="left")
        tk.Label(t_row, text="分", bg="#FFFFFF", fg=COLORS["text"],
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=(2, 6))

        # 分钟快速加减
        tk.Button(t_row, text="-5", command=lambda: self._adj_minutes(-5),
                  bg="#FDE8E8", fg=COLORS["text"], relief="flat",
                  font=("Microsoft YaHei", 7), padx=2, cursor="hand2", bd=0,
                  width=2).pack(side="left", padx=(0, 2))
        tk.Button(t_row, text="+5", command=lambda: self._adj_minutes(5),
                  bg="#E8F5E9", fg=COLORS["text"], relief="flat",
                  font=("Microsoft YaHei", 7), padx=2, cursor="hand2", bd=0,
                  width=2).pack(side="left", padx=(0, 10))

        # 分钟跳转
        for m, name in [(0, "整点"), (15, "一刻"), (30, "半点"), (45, "三刻")]:
            tk.Button(t_row, text=name, command=lambda mm=m: self._set_minute(mm),
                      bg="#E8F5E9" if m else "#EEF2F7", fg=COLORS["text"],
                      relief="flat", font=("Microsoft YaHei", 8), padx=5,
                      cursor="hand2", bd=0).pack(side="left", padx=(0, 5))

        self._trace_suppress = False
        self._prev_hour = self.hour_var.get()
        self._prev_minute = self.minute_var.get()
        self.hour_var.trace_add("write", self._validate_hour)
        self.minute_var.trace_add("write", self._validate_minute)

        # 日期（一次性模式）
        self.date_frame = tk.Frame(add_frame, bg="#FFFFFF")
        tk.Label(self.date_frame, text="执行日期:", bg="#FFFFFF",
                 fg=COLORS["text_light"], font=("Microsoft YaHei", 9)).pack(side="left")
        self.date_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        self.date_label = tk.Label(self.date_frame, textvariable=self.date_var,
                                    bg="#FFFFFF", fg=COLORS["primary"],
                                    font=("Microsoft YaHei", 9, "underline"),
                                    cursor="hand2")
        self.date_label.pack(side="left", padx=(4, 4))
        self.date_label.bind("<Button-1>", lambda e: self._pick_date())
        self.date_frame.pack(fill="x", padx=10, pady=(2, 4))
        self.date_frame.pack_forget()

        # 星期（每周模式）
        self.weekday_frame = tk.Frame(add_frame, bg="#FFFFFF")

        wd_row1 = tk.Frame(self.weekday_frame, bg="#FFFFFF")
        wd_row1.pack(fill="x")
        tk.Label(wd_row1, text="选择星期:", bg="#FFFFFF",
                 fg=COLORS["text_light"], font=("Microsoft YaHei", 9)).pack(side="left")
        self.weekday_vars = []
        for i, name in enumerate(self.WEEKDAY_NAMES):
            var = tk.BooleanVar(value=False)
            self.weekday_vars.append(var)
            tk.Checkbutton(wd_row1, text=name, variable=var,
                           bg="#FFFFFF", activebackground="#FFFFFF",
                           selectcolor="#FFFFFF", font=("Microsoft YaHei", 9)).pack(
                side="left", padx=(2, 0))

        wd_row2 = tk.Frame(self.weekday_frame, bg="#FFFFFF")
        wd_row2.pack(fill="x", pady=(2, 0))
        for text, cmd, bg in [("全不选", self._weekday_none, "#EEF2F7"),
                               ("全选", self._weekday_all, "#EEF2F7"),
                               ("工作日", self._weekday_workday, "#E8F5E9"),
                               ("周末", self._weekday_weekend, "#FFF3E0")]:
            tk.Button(wd_row2, text=text, command=cmd,
                      bg=bg, fg=COLORS["text"], relief="flat",
                      font=("Microsoft YaHei", 8), padx=6, cursor="hand2", bd=0).pack(
                side="left", padx=(0, 6))

        self.weekday_frame.pack(fill="x", padx=10, pady=(2, 4))
        self.weekday_frame.pack_forget()

        # 指定日期（多选模式）
        self.dates_frame = tk.Frame(add_frame, bg="#FFFFFF")
        tk.Label(self.dates_frame, text="已选日期:", bg="#FFFFFF",
                 fg=COLORS["text_light"], font=("Microsoft YaHei", 9)).pack(side="left")
        self.dates_var = tk.StringVar(value="")
        self.dates_label = tk.Label(self.dates_frame, textvariable=self.dates_var,
                                     bg="#FFFFFF", fg=COLORS["primary"],
                                     font=("Microsoft YaHei", 9),
                                     wraplength=350, anchor="w")
        self.dates_label.pack(side="left", padx=(4, 4), fill="x", expand=True)
        tk.Button(self.dates_frame, text="选择日期", command=self._pick_dates,
                  bg="#EEF2F7", fg=COLORS["text"], relief="flat",
                  font=("Microsoft YaHei", 8), padx=8, cursor="hand2", bd=0).pack(
            side="right")
        self.dates_frame.pack(fill="x", padx=10, pady=(2, 4))
        self.dates_frame.pack_forget()
        self._selected_dates = []  # list of "YYYY-MM-DD"

        # 账号选择（可滚动区域）
        acct_label_row = tk.Frame(add_frame, bg="#FFFFFF")
        acct_label_row.pack(fill="x", padx=10, pady=(2, 0))
        self._mode_anchor = acct_label_row  # 模式相关控件锚点，保证 pack 位置
        tk.Label(acct_label_row, text="账号:", bg="#FFFFFF", fg=COLORS["text_light"],
                 font=("Microsoft YaHei", 9)).pack(anchor="w")

        acct_container = tk.Frame(add_frame, bg="#FFFFFF", highlightthickness=1,
                                  highlightbackground="#E0E4E8")
        acct_container.pack(fill="x", padx=10, pady=(2, 4))

        self.acct_canvas = tk.Canvas(acct_container, bg="#FFFFFF",
                                     highlightthickness=0, height=200)
        self.acct_scroll = ttk.Scrollbar(acct_container, orient="vertical",
                                         command=self.acct_canvas.yview)
        self.acct_canvas.configure(yscrollcommand=self.acct_scroll.set)
        self.acct_canvas.pack(fill="both", expand=True)

        self.acct_inner = tk.Frame(self.acct_canvas, bg="#FFFFFF")
        self.acct_canvas.create_window((0, 0), window=self.acct_inner,
                                       anchor="nw", tags="acct_inner_win")

        def _on_acct_inner_resize(event):
            self.acct_canvas.configure(scrollregion=self.acct_canvas.bbox("all"))
            self.after(50, self._update_acct_scrollbar)
        self.acct_inner.bind("<Configure>", _on_acct_inner_resize)
        self.acct_canvas.bind("<Configure>",
            lambda e: (self.acct_canvas.itemconfig("acct_inner_win",
                                                   width=e.width),
                       self.after(50, self._update_acct_scrollbar)))

        def _acct_mousewheel(event):
            if not self.acct_canvas.winfo_exists():
                return
            bbox = self.acct_canvas.bbox("all")
            if bbox and bbox[3] > self.acct_canvas.winfo_height():
                self.acct_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.acct_canvas.bind("<Enter>", lambda e: self.acct_canvas.bind_all("<MouseWheel>", _acct_mousewheel))
        self.acct_canvas.bind("<Leave>", lambda e: self.acct_canvas.unbind_all("<MouseWheel>"))

        self._acct_vars = {}
        self._acct_items = []  # (name, var, cb) 用于刷新标签
        accounts = self.gui.cfg.get("accounts", [])
        self._acct_rows = []  # 拖拽排序用
        if accounts:
            for i, acc in enumerate(accounts):
                name = acc["name"]
                var = tk.BooleanVar(value=False)
                self._acct_vars[name] = var

                row = tk.Frame(self.acct_inner, bg="#FFFFFF", cursor="hand2")
                row.pack(fill="x", padx=4, pady=(1, 0))

                # 勾选标记 Label
                check_label = tk.Label(row, text="○", width=2, bg="#FFFFFF",
                                       fg=COLORS["primary"], font=("", 12))
                check_label.pack(side="left", padx=(8, 4))

                # 账号名 Label
                name_label = tk.Label(row, text=name, bg="#FFFFFF", anchor="w",
                                      font=("Microsoft YaHei", 9))
                name_label.pack(side="left", padx=(4, 0))

                # 填充空白区域
                fill_label = tk.Label(row, text="", bg="#FFFFFF", cursor="hand2")
                fill_label.pack(side="left", fill="both", expand=True)

                var.trace_add("write", lambda *a: self._refresh_acct_labels())

                self._acct_items.append((name, var, check_label, name_label))

                # 点击释放时切换勾选（仅在未拖拽时生效）
                def make_click_handler(_var=var, _cl=check_label):
                    def _on_release(event):
                        if not self._acct_drag_data.get("dragging", False):
                            _var.set(not _var.get())
                            _cl.config(text="✓" if _var.get() else "○")
                            self._refresh_acct_labels()
                    return _on_release

                # 按下列队拖拽起始
                def make_drag_handler(_idx=i):
                    def _handler(event):
                        self._acct_drag_start(event, _idx)
                    return _handler

                for w in (row, check_label, name_label, fill_label):
                    w.bind("<Button-1>", make_drag_handler())
                    w.bind("<ButtonRelease-1>", make_click_handler())

                self._acct_rows.append(row)

                # 行间分隔线
                tk.Frame(self.acct_inner, bg="#E1E8F0", height=1).pack(
                    fill="x", padx=10, pady=1)

            # 拖拽指示线（初始隐藏）
            self._acct_drag_line = tk.Frame(self.acct_inner, bg=COLORS["primary"], height=2)
            self._acct_drag_data = {"source_idx": -1, "dragging": False}
            self._refresh_acct_labels()
        else:
            tk.Label(self.acct_inner, text="（暂无账号）", bg="#FFFFFF",
                     fg=COLORS["text_light"], font=("Microsoft YaHei", 9)).pack(anchor="w", padx=4)

        # 胡桃签到勾选
        self.hutao_checkin_var = tk.BooleanVar(value=False)
        self.hutao_checkin_var.trace_add("write", self._on_hutao_checkin_toggled)
        hutao_checkin_cb = ttk.Checkbutton(add_frame,
            text="启动后在胡桃工具箱进行米游社签到", variable=self.hutao_checkin_var)
        hutao_checkin_cb.pack(fill="x", padx=10, pady=(8, 0))

        # 调度组 + 添加按钮同行
        bot_row = tk.Frame(add_frame, bg="#FFFFFF")
        bot_row.pack(fill="x", padx=10, pady=(2, 6))

        tk.Label(bot_row, text="调度组:", bg="#FFFFFF", fg=COLORS["text_light"],
                 font=("Microsoft YaHei", 9)).pack(side="left")
        self.scheduler_var = tk.StringVar(value="")
        groups = ["（无）"] + discover_scheduler_groups()
        self.scheduler_combo = ttk.Combobox(bot_row, textvariable=self.scheduler_var, width=16,
                                            values=groups, font=("Microsoft YaHei", 9))
        self.scheduler_combo.pack(side="left", padx=(2, 4))
        tk.Label(bot_row, text="(共用)", bg="#FFFFFF", fg=COLORS["text_light"],
                 font=("Microsoft YaHei", 8)).pack(side="left", padx=(0, 6))

        tk.Button(bot_row, text="添加任务", command=self._add_schedule,
                  bg=COLORS["primary"], fg=COLORS["text_white"],
                  activebackground=COLORS["primary_hover"],
                  relief="flat", font=("Microsoft YaHei", 9, "bold"),
                  padx=14, pady=3, cursor="hand2", bd=0).pack(side="right")

        # --- 已设任务区域 ---
        list_header = tk.Frame(content, bg=COLORS["bg"])
        list_header.pack(fill="x", padx=12, pady=(2, 0))
        tk.Label(list_header, text="已设任务", font=("Microsoft YaHei", 12, "bold"),
                 bg=COLORS["bg"], fg=COLORS["text"]).pack(side="left")
        tk.Button(list_header, text="全选", command=self._toggle_select_all,
                  bg="#EEF2F7", fg=COLORS["text"], relief="flat",
                  font=("Microsoft YaHei", 8), padx=8, cursor="hand2", bd=0).pack(
            side="right", padx=(0, 4))
        tk.Button(list_header, text="删除选中", command=self._delete_selected,
                  bg="#E74C3C", fg="#FFFFFF", activebackground="#C0392B",
                  relief="flat", font=("Microsoft YaHei", 9),
                  padx=10, pady=2, cursor="hand2", bd=0).pack(side="right")

        list_frame = tk.Frame(content, bg="#FFFFFF", relief="solid", bd=1,
                              highlightbackground="#E1E8F0", highlightthickness=1)
        list_frame.pack(fill="x", padx=12, pady=(2, 6))
        list_frame.configure(height=200)
        list_frame.pack_propagate(False)

        self.task_canvas = tk.Canvas(list_frame, bg="#FFFFFF", highlightthickness=0, width=580)
        self.task_scroll = ttk.Scrollbar(list_frame, orient="vertical",
                                         command=self.task_canvas.yview)
        self.task_canvas.configure(yscrollcommand=self.task_scroll.set)
        self.task_canvas.pack(fill="both", expand=True)

        self.task_inner = tk.Frame(self.task_canvas, bg="#FFFFFF")
        self._task_window_id = self.task_canvas.create_window(
            (0, 0), window=self.task_inner, anchor="nw")

        def _on_task_inner_resize(event):
            self.task_canvas.configure(scrollregion=self.task_canvas.bbox("all"))
            self.after(50, self._update_task_scrollbar)
        self.task_inner.bind("<Configure>", _on_task_inner_resize)

        def _on_task_canvas_configure(event):
            self.task_canvas.itemconfig(self._task_window_id, width=event.width)
            self.after(50, self._update_task_scrollbar)
        self.task_canvas.bind("<Configure>", _on_task_canvas_configure)

        def _task_mousewheel(event):
            """任务列表滚轮：到达边界时传播到外层主滚动区"""
            if not self.task_canvas.winfo_exists():
                return
            bbox = self.task_canvas.bbox("all")
            if bbox and bbox[3] > self.task_canvas.winfo_height():
                delta = -event.delta / 120
                cur = self.task_canvas.yview()
                at_top = cur[0] <= 0.001 and delta > 0
                at_bottom = cur[1] >= 0.999 and delta < 0
                if not (at_top or at_bottom):
                    self.task_canvas.yview_scroll(int(delta), "units")
                    return "break"
            # 传播到外层主滚动区
            if hasattr(self, 'main_scroll_canvas') and self.main_scroll_canvas.winfo_exists():
                mbbox = self.main_scroll_canvas.bbox("all")
                if mbbox and mbbox[3] > self.main_scroll_canvas.winfo_height():
                    self.main_scroll_canvas.yview_scroll(
                        int(-event.delta / 120), "units")
        self.task_canvas.bind("<MouseWheel>", _task_mousewheel)
        self.task_inner.bind("<MouseWheel>", _task_mousewheel)

        def _on_canvas_enter(event):
            self.task_canvas.focus_set()
        self.task_canvas.bind("<Enter>", _on_canvas_enter)

        self._task_vars = []
        self._load_list()

        self.after(100, self._update_acct_scrollbar)
        self.after(100, self._update_task_scrollbar)

        # --- 底部状态栏 ---
        bottom = tk.Frame(content, bg=COLORS["bg"])
        bottom.pack(fill="x", side="bottom", padx=12, pady=(0, 6))
        self.status_var = tk.StringVar(value="就绪")
        tk.Label(bottom, textvariable=self.status_var, font=("Microsoft YaHei", 9),
                 bg=COLORS["bg"], fg=COLORS["text_light"]).pack(side="left")

        self._on_mode_change()
        self._update_status()

    def _update_acct_scrollbar(self):
        """账号选择区域：内容超出时显示滚动条"""
        if not self.acct_canvas.winfo_exists():
            return
        bbox = self.acct_canvas.bbox("all")
        if bbox and bbox[3] > self.acct_canvas.winfo_height():
            self.acct_scroll.pack(side="right", fill="y")
            self.acct_canvas.pack(side="left", fill="both", expand=True)
        else:
            self.acct_scroll.pack_forget()
            self.acct_canvas.pack(fill="both", expand=True)

    def _update_main_scrollbar(self):
        """主滚动区域：内容超出时显示滚动条（与设置窗口风格一致）"""
        if not hasattr(self, "main_scroll_canvas") or not self.main_scroll_canvas.winfo_exists():
            return
        self.update_idletasks()
        bbox = self.main_scroll_canvas.bbox("all")
        if bbox is None:
            return
        content_h = bbox[3]
        visible_h = self.main_scroll_canvas.winfo_height()
        if visible_h <= 0:
            return
        if content_h > visible_h:
            self.main_scrollbar.pack(side="right", fill="y")
            self.main_scroll_canvas.pack(side="left", fill="both", expand=True)
        else:
            self.main_scrollbar.pack_forget()
            self.main_scroll_canvas.pack(side="left", fill="both", expand=True)

    def _update_task_scrollbar(self):
        """已设任务区域：内容超出时显示滚动条"""
        if not self.task_canvas.winfo_exists():
            return
        bbox = self.task_canvas.bbox("all")
        if bbox and bbox[3] > self.task_canvas.winfo_height():
            self.task_scroll.pack(side="right", fill="y")
            self.task_canvas.pack(side="left", fill="both", expand=True)
        else:
            self.task_scroll.pack_forget()
            self.task_canvas.pack(fill="both", expand=True)

    def _set_time(self, h, m):
        self._trace_suppress = True
        self.hour_var.set(h)
        self.minute_var.set(m)
        self._trace_suppress = False

    def _validate_hour(self, *args):
        if self._trace_suppress:
            return
        val = self.hour_var.get()
        if not val:
            self._prev_hour = ""
            return
        if not val.isdigit() or int(val) > 23 or len(val) > 2:
            self._trace_suppress = True
            self.hour_var.set(self._prev_hour)
            self._trace_suppress = False
        else:
            self._prev_hour = val

    def _validate_minute(self, *args):
        if self._trace_suppress:
            return
        val = self.minute_var.get()
        if not val:
            self._prev_minute = ""
            return
        if not val.isdigit() or int(val) > 59 or len(val) > 2:
            self._trace_suppress = True
            self.minute_var.set(self._prev_minute)
            self._trace_suppress = False
        else:
            self._prev_minute = val

    def _adj_hour(self, delta):
        h = int(self.hour_var.get() or 8)
        m = int(self.minute_var.get() or 0)
        total = (h + delta) * 60 + m
        total %= 24 * 60
        self._set_time(f"{total // 60:02d}", f"{total % 60:02d}")

    def _adj_minutes(self, delta):
        h = int(self.hour_var.get() or 8)
        m = int(self.minute_var.get() or 0)
        total = h * 60 + m + delta
        total %= 24 * 60
        self._set_time(f"{total // 60:02d}", f"{total % 60:02d}")

    def _set_minute(self, m):
        h = self.hour_var.get() or "08"
        self._set_time(h, f"{m:02d}")

    def _weekday_all(self):
        for v in self.weekday_vars:
            v.set(True)

    def _weekday_none(self):
        for v in self.weekday_vars:
            v.set(False)

    def _weekday_workday(self):
        for i, v in enumerate(self.weekday_vars):
            v.set(i < 5)  # 周一至周五 (index 0-4)

    def _weekday_weekend(self):
        for i, v in enumerate(self.weekday_vars):
            v.set(i >= 5)  # 周六日 (index 5-6)

    def _on_mode_change(self):
        mode = self.mode_var.get()
        if mode == "once":
            self.date_frame.pack(fill="x", padx=10, pady=(2, 4),
                                 before=self._mode_anchor)
        else:
            self.date_frame.pack_forget()

        if mode == "weekly":
            self.weekday_frame.pack(fill="x", padx=10, pady=(2, 4),
                                    before=self._mode_anchor)
        else:
            self.weekday_frame.pack_forget()

        if mode == "dates":
            self.dates_frame.pack(fill="x", padx=10, pady=(2, 4),
                                  before=self._mode_anchor)
        else:
            self.dates_frame.pack_forget()

    def _pick_date(self):
        """打开日历选择单个日期"""
        dlg = CalendarDialog(self, multi_select=False,
                             selected_dates=[self.date_var.get()],
                             title="选择执行日期",
                             callback=self._on_date_picked)
        self.wait_window(dlg)

    def _on_date_picked(self, date_str):
        if date_str:
            self.date_var.set(date_str)

    def _pick_dates(self):
        """打开日历多选日期"""
        dlg = CalendarDialog(self, multi_select=True,
                             selected_dates=self._selected_dates,
                             title="选择执行日期（可多选）",
                             callback=self._on_dates_picked)
        self.wait_window(dlg)

    def _on_dates_picked(self, dates):
        self._selected_dates = sorted(dates or [])
        self.dates_var.set(", ".join(self._selected_dates)
                           if self._selected_dates else "")

    def _select_all_accts(self):
        for v in self._acct_vars.values():
            v.set(True)

    def _deselect_all_accts(self):
        for v in self._acct_vars.values():
            v.set(False)

    def _acct_drag_start(self, event, source_idx):
        """拖拽开始"""
        self._acct_drag_data["source_idx"] = source_idx
        self._acct_drag_data["dragging"] = False
        self._acct_drag_data["start_y"] = event.y_root
        self.bind_all("<B1-Motion>", self._acct_drag_motion)
        self.bind_all("<ButtonRelease-1>", self._acct_drag_end)

    def _acct_drag_motion(self, event):
        """拖拽移动：显示蓝色指示线"""
        if not self._acct_drag_data.get("dragging"):
            dy = event.y_root - self._acct_drag_data.get("start_y", 0)
            if abs(dy) < 5:
                return
            self._acct_drag_data["dragging"] = True

        y_in_inner = event.y_root - self.acct_inner.winfo_rooty()
        y_in_inner += self.acct_canvas.canvasy(0)  # 补偿滚动偏移

        target = self._acct_drag_data["source_idx"]
        for i, row in enumerate(self._acct_rows):
            if not row.winfo_exists():
                continue
            mid_y = row.winfo_y() + row.winfo_height() / 2
            if y_in_inner < mid_y:
                target = i
                break
        else:
            target = len(self._acct_rows)

        if target != self._acct_drag_data.get("target_idx"):
            self._acct_drag_data["target_idx"] = target
            self._show_acct_drag_line(target)

    def _show_acct_drag_line(self, target_idx):
        """在目标位置显示蓝色指示线"""
        self._acct_drag_line.place_forget()
        if target_idx >= len(self._acct_rows):
            last_row = self._acct_rows[-1]
            if last_row.winfo_exists():
                y = last_row.winfo_y() + last_row.winfo_height()
        else:
            row = self._acct_rows[target_idx]
            if row.winfo_exists():
                y = row.winfo_y()
        self._acct_drag_line.place(x=0, y=y, relwidth=1.0)
        self._acct_drag_line.lift()

    def _acct_drag_end(self, event):
        """拖拽结束：执行排序并刷新"""
        self.unbind_all("<B1-Motion>")
        self.unbind_all("<ButtonRelease-1>")
        self._acct_drag_line.place_forget()
        source = self._acct_drag_data.get("source_idx", -1)
        target = self._acct_drag_data.get("target_idx", -1)
        self._acct_drag_data["source_idx"] = -1
        self._acct_drag_data["dragging"] = False
        self._acct_drag_data.pop("target_idx", None)

        if source < 0 or target < 0 or source == target:
            return

        accounts = self.gui.cfg.get("accounts", [])
        if source >= len(accounts):
            return
        acc = accounts.pop(source)
        if target > source:
            target -= 1
        accounts.insert(target, acc)
        save_config(self.gui.cfg)
        self._rebuild_acct_list()

    def _refresh_acct_labels(self):
        """根据勾选状态刷新账号行标签：仅勾选的显示序号"""
        if not self._acct_items:
            return
        seq = 1
        for item in self._acct_items:
            name, var = item[0], item[1]
            name_label = item[3]
            if len(item) >= 3:
                check_label = item[2]
                if var.get():
                    check_label.config(text="✓")
                    name_label.config(text=f"  {seq}. {name}")
                    seq += 1
                else:
                    check_label.config(text="○")
                    name_label.config(text=f"    {name}")
            elif var.get():
                name_label.config(text=f"  {seq}. {name}")
                seq += 1
            else:
                name_label.config(text=f"    {name}")

    def _rebuild_acct_list(self):
        """重建账号列表 UI（保留勾选状态）"""
        for w in self.acct_inner.winfo_children():
            w.destroy()
        self._acct_rows = []
        self._acct_items = []
        accounts = self.gui.cfg.get("accounts", [])
        if accounts:
            for i, acc in enumerate(accounts):
                name = acc["name"]
                var = self._acct_vars.get(name)
                if var is None:
                    var = tk.BooleanVar(value=False)
                    self._acct_vars[name] = var
                    var.trace_add("write", lambda *a: self._refresh_acct_labels())

                row = tk.Frame(self.acct_inner, bg="#FFFFFF", cursor="hand2")
                row.pack(fill="x", padx=4, pady=(1, 0))

                # 勾选标记 Label
                check_label = tk.Label(row, text="✓" if var.get() else "○",
                                       width=2, bg="#FFFFFF", fg=COLORS["primary"],
                                       font=("", 12))
                check_label.pack(side="left", padx=(8, 4))

                # 账号名 Label
                name_label = tk.Label(row, text=name, bg="#FFFFFF", anchor="w",
                                      font=("Microsoft YaHei", 9))
                name_label.pack(side="left", padx=(4, 0))

                # 填充空白区域，确保右侧空白也可点击
                fill_label = tk.Label(row, text="", bg="#FFFFFF", cursor="hand2")
                fill_label.pack(side="left", fill="both", expand=True)

                # 点击释放时切换勾选（仅在未拖拽时生效）
                def make_click_handler(_var=var, _cl=check_label):
                    def _on_release(event):
                        if not self._acct_drag_data.get("dragging", False):
                            _var.set(not _var.get())
                            _cl.config(text="✓" if _var.get() else "○")
                            self._refresh_acct_labels()
                    return _on_release

                self._acct_items.append((name, var, check_label, name_label))

                # 按下列队拖拽起始
                def make_drag_handler(_idx=i):
                    def _handler(event):
                        self._acct_drag_start(event, _idx)
                    return _handler

                for w in (row, check_label, name_label, fill_label):
                    w.bind("<Button-1>", make_drag_handler())
                    w.bind("<ButtonRelease-1>", make_click_handler())

                self._acct_rows.append(row)

                tk.Frame(self.acct_inner, bg="#E1E8F0", height=1).pack(
                    fill="x", padx=10, pady=1)

            self._acct_drag_line = tk.Frame(self.acct_inner, bg=COLORS["primary"], height=2)
            self._acct_drag_data = {"source_idx": -1, "dragging": False}
            self._refresh_acct_labels()
        else:
            tk.Label(self.acct_inner, text="（暂无账号）", bg="#FFFFFF",
                     fg=COLORS["text_light"], font=("Microsoft YaHei", 9)).pack(anchor="w", padx=4)

        self.after(50, self._update_acct_scrollbar)

    def _format_schedule_desc(self, s):
        accts = "    ".join(f"{i+1}. {name}" for i, name in enumerate(s.get("accounts", [])))
        grp = s.get("scheduler_groups", "")
        stype = s.get("schedule_type", "daily")
        time_str = s.get("time", "??:??")

        if stype == "once":
            date_str = s.get("date", "????-??-??")
            desc = f"{date_str} {time_str}  一次性"
        elif stype == "weekly":
            wd_indices = s.get("weekdays", [])
            wd_names = "".join(self.WEEKDAY_NAMES[i - 1] for i in wd_indices if 1 <= i <= 7)
            desc = f"每周{wd_names} {time_str}"
        elif stype == "dates":
            dates_list = s.get("dates", [])
            dates_str = ", ".join(dates_list) if dates_list else ""
            desc = f"指定日期 {dates_str} {time_str}"
        else:
            desc = f"每天 {time_str}"

        desc += f"  [{accts}]"
        if grp:
            desc += f"  组:{grp}"
        return desc

    def _load_list(self):
        # 重新加载配置以同步托盘等外部变更
        self.cfg = load_scheduler_config()
        self._task_vars = []
        for w in self.task_inner.winfo_children():
            w.destroy()

        if not self.cfg["schedules"]:
            tk.Label(self.task_inner, text="（暂无任务）", bg="#FFFFFF",
                     fg=COLORS["text_light"],
                     font=("Microsoft YaHei", 10)).pack(pady=20)
            self.after(50, self._update_task_scrollbar)
            self.after(50, self._update_main_scrollbar)
            return

        for i, s in enumerate(self.cfg["schedules"]):
            var = tk.BooleanVar(value=False)
            self._task_vars.append(var)
            row = tk.Frame(self.task_inner, bg="#FFFFFF")
            row.pack(fill="x", pady=1)
            row.grid_columnconfigure(1, weight=1)

            cb = tk.Checkbutton(row, variable=var, bg="#FFFFFF",
                                activebackground="#FFFFFF")
            cb.grid(row=0, column=0, padx=(6, 4), sticky="w")

            desc_label = tk.Label(row, text=self._format_schedule_desc(s), anchor="w",
                                  bg="#FFFFFF", fg=COLORS["text"],
                                  font=("Microsoft YaHei", 10))
            desc_label.grid(row=0, column=1, padx=(4, 8), sticky="w")

            # 启停开关按钮（行尾，固定宽度容器确保对齐）
            idx = i  # capture for closure
            enabled = s.get("enabled", True)
            btn_text = "停用" if enabled else "启用"
            btn_color = "#E74C3C" if enabled else "#52C41A"
            btn_active = "#C0392B" if enabled else "#389E0D"

            def make_toggle_callback(idx=idx):
                def toggle_enabled():
                    new_state = not self.cfg["schedules"][idx].get("enabled", True)
                    self.cfg["schedules"][idx]["enabled"] = new_state
                    save_scheduler_config(self.cfg)
                    # 手动启用单个任务时，若线程未运行则自动启动
                    if new_state and self.gui.scheduler_event.is_set():
                        self.gui.scheduler_event.clear()
                        self.gui.scheduler_thread = threading.Thread(
                            target=self.gui._scheduler_loop, daemon=True)
                        self.gui.scheduler_thread.start()
                        self.gui._log("定时器已启动（任务被手动启用）")
                    # 所有任务都停用后自动停止线程
                    if not new_state:
                        all_disabled = all(not sch.get("enabled", True)
                                           for sch in self.cfg["schedules"])
                        if all_disabled:
                            self.gui.scheduler_event.set()
                            self.gui._log("定时器已停止（所有任务均已停用）")
                    self._load_list()
                    self._update_status()
                    self.refresh_btn()
                    if self.gui.tray:
                        self.gui._rebuild_tray_menu()
                return toggle_enabled

            btn = tk.Button(row, text=btn_text, command=make_toggle_callback(),
                            bg=btn_color, fg="#FFFFFF",
                            activebackground=btn_active,
                            relief="flat", font=("Microsoft YaHei", 9),
                            width=5, cursor="hand2", bd=0)
            btn.grid(row=0, column=2, padx=(0, 2), sticky="ne")

            # 任务间分隔线（最后一项不加）
            if i < len(self.cfg["schedules"]) - 1:
                tk.Frame(self.task_inner, height=1, bg="#E1E8F0").pack(
                    fill="x", padx=10, pady=2)

        self.after(50, self._update_task_scrollbar)
        self.after(50, self._update_main_scrollbar)

    def _add_schedule(self):
        # 重新加载配置以同步托盘等外部变更
        self.cfg = load_scheduler_config()
        selected = [name for name, var in self._acct_vars.items() if var.get()]
        if not selected:
            messagebox.showwarning("提示", "请至少选择一个账号", parent=self)
            return

        hour = (self.hour_var.get() or "08").zfill(2)
        minute = (self.minute_var.get() or "00").zfill(2)
        if not (re.match(r"^\d{2}$", hour) and 0 <= int(hour) <= 23 and
                re.match(r"^\d{2}$", minute) and 0 <= int(minute) <= 59):
            messagebox.showwarning("提示",
                "时间格式错误，请输入 00-23 的小时和 00-59 的分钟", parent=self)
            return

        mode = self.mode_var.get()
        schedule = {
            "schedule_type": mode,
            "time": f"{hour}:{minute}",
            "accounts": selected,
            "scheduler_groups": self.scheduler_var.get().strip(),
            "enabled": True,
        }
        import uuid
        schedule["id"] = uuid.uuid4().hex[:8]
        schedule["hutao_checkin"] = self.hutao_checkin_var.get()
        schedule["hutao_accounts"] = self._get_hutao_checkin_accounts() if self.hutao_checkin_var.get() else []

        if mode == "once":
            date_str = self.date_var.get().strip()
            try:
                datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                messagebox.showerror("错误", "日期格式不正确，请使用 YYYY-MM-DD", parent=self)
                return
            schedule["date"] = date_str

        elif mode == "weekly":
            wds = [i + 1 for i, v in enumerate(self.weekday_vars) if v.get()]
            if not wds:
                messagebox.showwarning("提示", "请至少选择一个星期", parent=self)
                return
            schedule["weekdays"] = wds

        elif mode == "dates":
            if not self._selected_dates:
                messagebox.showwarning("提示", "请至少选择一个日期", parent=self)
                return
            schedule["dates"] = self._selected_dates[:]

        for existing in self.cfg["schedules"]:
            if (existing.get("schedule_type") == mode and
                existing.get("time") == schedule["time"] and
                existing.get("accounts") == selected and
                existing.get("date") == schedule.get("date") and
                existing.get("weekdays") == schedule.get("weekdays")):
                messagebox.showwarning("提示", "已存在相同的定时任务", parent=self)
                return

        self.cfg["schedules"].append(schedule)
        save_scheduler_config(self.cfg)

        # 添加任务后自动启动调度器（如果有启用任务且调度器未运行）
        any_enabled = any(s.get("enabled", True) for s in self.cfg["schedules"])
        if any_enabled and self.gui.scheduler_event.is_set():
            self.gui.scheduler_event.clear()
            self.gui.scheduler_thread = threading.Thread(
                target=self.gui._scheduler_loop, daemon=True)
            self.gui.scheduler_thread.start()
            self.gui._log("定时器已自动启动")

        self._load_list()
        self.status_var.set("已添加")
        self._update_status()
        self.refresh_btn()
        if self.gui.tray:
            self.gui._rebuild_tray_menu()

    def _get_selected_accounts(self):
        """返回当前勾选的账号名列表"""
        return [name for name, var in self._acct_vars.items() if var.get()]

    def _get_hutao_checkin_accounts(self):
        """获取勾选胡桃签到时应签到的 hutao_account 列表"""
        selected = self._get_selected_accounts()
        cfg = load_config()
        result = []
        for name in selected:
            acc = next((a for a in cfg.get("accounts", []) if a.get("name") == name), None)
            if acc and acc.get("type") == "hutao" and acc.get("hutao_account", "").strip():
                result.append(acc["hutao_account"].strip())
        return result

    def _on_hutao_checkin_toggled(self, *args):
        if self.hutao_checkin_var.get():
            selected = self._get_selected_accounts()
            cfg = load_config()
            missing = []
            for name in selected:
                acc = next((a for a in cfg.get("accounts", []) if a.get("name") == name), None)
                if acc and acc.get("type") == "hutao" and not acc.get("hutao_account", "").strip():
                    missing.append(name)
            if missing:
                messagebox.showwarning("提示",
                    f"以下账号未配置米游社名称，请先到账号管理中填写：\n{', '.join(missing)}")

    def _delete_selected(self):
        # 收集勾选任务的 ID（基于当前快照）
        checked_ids = set()
        for i, v in enumerate(self._task_vars):
            if v.get() and i < len(self.cfg["schedules"]):
                sid = self.cfg["schedules"][i].get("id")
                if sid:
                    checked_ids.add(sid)
        if not checked_ids:
            return
        if not messagebox.askyesno("确认删除", f"确定删除 {len(checked_ids)} 个任务？", parent=self):
            return
        # 重新加载配置以同步托盘等外部变更，然后用 ID 精确删除
        self.cfg = load_scheduler_config()
        self.cfg["schedules"] = [s for s in self.cfg["schedules"]
                                 if s.get("id") not in checked_ids]
        save_scheduler_config(self.cfg)
        self._load_list()
        any_enabled = any(s.get("enabled", True) for s in self.cfg["schedules"])
        if not any_enabled and not self.gui.scheduler_event.is_set():
            self.gui.scheduler_event.set()
            self.gui._log("定时器已停止（所有任务已删除或停用）")
            if self.gui.tray:
                self.gui._rebuild_tray_menu()
        self.refresh_btn()
        self.status_var.set(f"已删除 {len(checked_ids)} 个任务")
        self._update_status()

    def _toggle_select_all(self):
        """全选 / 取消全选"""
        if not self._task_vars:
            return
        all_checked = all(v.get() for v in self._task_vars)
        new_val = not all_checked
        for v in self._task_vars:
            v.set(new_val)

    def _toggle(self):
        # 重新加载配置以同步托盘等外部变更
        self.cfg = load_scheduler_config()
        all_enabled = all(s.get("enabled", True) for s in self.cfg["schedules"])
        if self.gui.scheduler_event.is_set() or not all_enabled:
            if not self.cfg["schedules"]:
                messagebox.showwarning("警告", "没有设置任何定时任务", parent=self)
                return
            # 一键启用全部任务
            for s in self.cfg["schedules"]:
                s["enabled"] = True
            save_scheduler_config(self.cfg)
            # 先停止旧线程（如果存在）
            if not self.gui.scheduler_event.is_set():
                self.gui.scheduler_event.set()
                # 等待旧线程退出，最多 2 秒
                if self.gui.scheduler_thread and self.gui.scheduler_thread.is_alive():
                    self.gui.scheduler_thread.join(timeout=2)
            self.gui.scheduler_event.clear()
            self.gui.scheduler_thread = threading.Thread(target=self.gui._scheduler_loop, daemon=True)
            self.gui.scheduler_thread.start()
            self.gui._log("定时器已启动（一键启用全部任务）")
            self._load_list()
            self.refresh_btn()
            # 缩到托盘，不在任务栏显示
            if self.cfg.get("settings", {}).get("auto_minimize", True):
                self.gui.root.after(300, self.gui._minimize_to_tray)
            if self.gui.tray:
                self.gui._rebuild_tray_menu()
        else:
            self.gui.scheduler_event.set()
            self.gui._log("定时器已停止")
            self._load_list()
            self.refresh_btn()
            if self.gui.tray:
                self.gui._rebuild_tray_menu()
        self._update_status()

    def _toggle_autostart(self):
        if self.autostart_var.get():
            enable_autostart()
            self.status_var.set("已开启开机自启动")
        else:
            disable_autostart()
            self.status_var.set("已关闭开机自启动")

    def _toggle_auto_shutdown(self):
        self.gui.cfg.setdefault("settings", {})
        self.gui.cfg["settings"]["auto_shutdown"] = self.auto_shutdown_var.get()
        save_config(self.gui.cfg)
        status = "开启" if self.auto_shutdown_var.get() else "关闭"
        self.status_var.set(f"已{status}自动关机")

    def _update_status(self):
        # 重新加载配置以同步托盘等外部变更
        self.cfg = load_scheduler_config()
        total = len(self.cfg["schedules"])
        enabled = sum(1 for s in self.cfg["schedules"] if s.get("enabled", True))
        if not self.gui.scheduler_event.is_set():
            self.status_var.set(f"运行中 | {enabled}/{total} 个任务已启用")
        else:
            self.status_var.set(f"已停止 | {enabled}/{total} 个任务已启用" if total else "暂无任务")

    def refresh_btn(self):
        # 重新加载配置以同步托盘等外部变更
        self.cfg = load_scheduler_config()
        all_enabled = all(s.get("enabled", True) for s in self.cfg["schedules"])
        running = not self.gui.scheduler_event.is_set()
        if running and all_enabled:
            self.toggle_btn.config(text="停止定时器", bg="#E74C3C", activebackground="#C0392B")
        elif running and not all_enabled:
            self.toggle_btn.config(text="启用全部任务", bg="#FA8C16", activebackground="#D46B08")
        else:
            self.toggle_btn.config(text="一键启动全部", bg="#52C41A", activebackground="#389E0D")
        self._update_status()


# ============================================================
# TeyvatGuide 签到核心函数（CDP WebSocket 注入方案）
# ============================================================

def _run_checkin(log_func=None, stop_event=None, pause_event=None):
    """通过 TeyvatGuide 执行多账号米游社签到（CDP WebSocket 注入方案）。

    流程：启动 TeyvatGuide → 等待 CDP 端口 → 连接 WebSocket →
          通过 Runtime.evaluate 导航到实用脚本 → 点击一键执行全部账号

    参数:
        log_func: 日志回调函数
        stop_event: threading.Event，set 时中断签到流程并返回 False
        pause_event: threading.Event，set=运行 / clear=暂停
    返回 True/False。
    """
    def log(msg):
        if log_func:
            log_func(msg)

    def _should_stop():
        return stop_event and stop_event.is_set()

    def _sleep(seconds):
        """可中断的 sleep：每 0.5 秒检查 stop_event 和 pause_event"""
        for _ in range(int(seconds * 2)):
            if _should_stop():
                return True
            if pause_event:
                pause_event.wait(0.5)
            time.sleep(0.5)
        return False

    # ---- CDP 辅助函数 ----
    try:
        import websocket
    except ImportError:
        log("[签到] [!] websocket-client 未安装，请执行: pip install websocket-client")
        return False

    try:
        import requests as _requests
    except ImportError:
        log("[签到] [!] requests 未安装，请执行: pip install requests")
        return False

    def _cdp_get_targets(timeout=10):
        """获取所有 CDP 调试目标"""
        resp = _requests.get("http://127.0.0.1:9222/json", timeout=timeout)
        return resp.json()

    def _cdp_send(ws_conn, method, params=None, timeout=20):
        """发送 CDP 命令并等待匹配 id 的响应。"""
        msg_id = int(time.time() * 1000) % 999999
        msg = json.dumps({"id": msg_id, "method": method, "params": params or {}})
        try:
            ws_conn.send(msg)
        except Exception as e:
            log(f"[签到] [!] CDP 发送失败: {e}")
            return None

        deadline = time.time() + timeout
        while time.time() < deadline:
            if _should_stop():
                return None
            try:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                ws_conn.settimeout(remaining)
                raw = ws_conn.recv()
                resp = json.loads(raw)
                if resp.get("id") == msg_id:
                    return resp.get("result")
            except websocket.WebSocketTimeoutException:
                break
            except OSError:
                break
            except Exception as e:
                log(f"[签到] [!] CDP 接收异常: {e}")
                break
        return None

    def _cdp_evaluate(ws_conn, expression, timeout=20):
        """执行 JavaScript 表达式并返回结果。"""
        result = _cdp_send(ws_conn, "Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
        }, timeout=timeout)
        if result is None:
            return None
        return result.get("result", {}).get("value")

    def _drain_events(ws_conn, duration=1.0):
        """排空 WebSocket 缓冲中的残留事件。"""
        deadline = time.time() + duration
        try:
            ws_conn.settimeout(0.3)
            while time.time() < deadline:
                try:
                    ws_conn.recv()
                except websocket.WebSocketTimeoutException:
                    break
                except Exception:
                    break
        except Exception:
            pass

    def _cdp_collect_console(ws_conn, duration=10.0):
        """收集指定时间内 Runtime.consoleAPICalled 事件中的控制台消息。"""
        messages = []
        deadline = time.time() + duration
        try:
            ws_conn.settimeout(0.5)
        except Exception:
            pass
        while time.time() < deadline:
            if _should_stop():
                break
            try:
                remaining = max(0.1, deadline - time.time())
                ws_conn.settimeout(min(remaining, 1.0))
                raw = ws_conn.recv()
                msg = json.loads(raw)
                if msg.get("method") == "Runtime.consoleAPICalled":
                    args = msg.get("params", {}).get("args", [])
                    texts = []
                    for a in (args or []):
                        if a.get("type") == "string":
                            texts.append(a.get("value", ""))
                        else:
                            desc = a.get("description", "")
                            if desc:
                                texts.append(desc)
                    if texts:
                        messages.append(" ".join(texts))
            except websocket.WebSocketTimeoutException:
                continue
            except OSError:
                break
            except Exception:
                continue
        return messages

    # ============================================================
    # 主流程
    # ============================================================
    # ---- 启动 TeyvatGuide（防重复）----
    if find_proc("TeyvatGuide.exe"):
        log("[签到] TeyvatGuide 已在运行，跳过启动")
        hwnd = find_and_activate_window_by_process("TeyvatGuide.exe")
        if hwnd:
            log("[签到] TeyvatGuide 窗口已置顶")
        else:
            log("[签到] 未找到 TeyvatGuide 窗口（可能已最小化到托盘）")
    else:
        log("[签到] 正在启动 TeyvatGuide...")
        tg_app_id = get_teyvatguide_app_id()
        try:
            subprocess.Popen(
                ["explorer.exe", f"shell:AppsFolder\\{tg_app_id}"],
                shell=True,
            )
        except Exception as e:
            log(f"[签到] [!] 启动 TeyvatGuide 失败: {e}")
            return False

    if _should_stop():
        log("[签到] 用户取消，签到终止")
        return False

    # ---- 等待 CDP 端口 9222 就绪 ----
    log("[签到] 等待 CDP 调试端口 (127.0.0.1:9222) 就绪（最多 120 秒）...")
    cdp_ready = False
    for attempt in range(60):
        if _should_stop():
            log("[签到] 用户取消，签到终止")
            return False
        try:
            resp = _requests.get("http://127.0.0.1:9222/json/version", timeout=2)
            if resp.status_code == 200:
                log(f"[签到] CDP 端口就绪 (第 {attempt+1} 次尝试)")
                cdp_ready = True
                break
        except Exception:
            pass
        _sleep(2)

    if not cdp_ready:
        log("[签到] [!] CDP 端口 9222 超时未就绪（120 秒）")
        log("[签到] 请确认已设置环境变量: WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222 --remote-allow-origins=*")
        log("[签到] 或以管理员身份运行: setx WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS \"--remote-debugging-port=9222 --remote-allow-origins=*\"")
        return False

    # 等待 TeyvatGuide 完全加载（WebView2 初始化需要时间）
    log("[签到] 等待 TeyvatGuide 界面加载...")
    _sleep(5)

    if _should_stop():
        log("[签到] 用户取消，签到终止")
        return False

    # ---- 查找 TeyvatGuide 页面 ----
    log("[签到] 查找 TeyvatGuide CDP 目标页面...")
    ws_url = None
    for retry in range(10):
        if _should_stop():
            return False
        try:
            targets = _cdp_get_targets(timeout=5)
            for t in targets:
                if t.get("type") == "page" and "tauri.localhost" in t.get("url", ""):
                    ws_url = t["webSocketDebuggerUrl"]
                    log(f"[签到] 找到 TeyvatGuide 页面: {t['url']}")
                    break
            if ws_url:
                break
            log(f"[签到] 未找到 TeyvatGuide 页面 (retry={retry+1}/10), CDP目标数={len(targets)}")
        except Exception as e:
            log(f"[签到] 查询 CDP 目标异常: {e} (retry={retry+1}/10)")
        _sleep(2)
    else:
        log("[签到] [!] 未找到 TeyvatGuide 页面，请确认 TeyvatGuide 已启动并处于前台")
        return False

    # ---- 连接 WebSocket ----
    log("[签到] 连接 CDP WebSocket...")
    try:
        ws = websocket.create_connection(ws_url, timeout=15, enable_multithread=True)
    except Exception as e:
        log(f"[签到] [!] WebSocket 连接失败: {e}")
        return False

    try:
        # 启用 Runtime 域
        log("[签到] 启用 CDP Runtime 域...")
        if _cdp_send(ws, "Runtime.enable") is None:
            log("[签到] [!] Runtime.enable 失败")
            ws.close()
            return False

        # 排空初始化事件
        _drain_events(ws, 1.5)

        # ---- Step 1: 导航到「实用脚本」 ----
        log("[签到] 导航到「实用脚本」页面...")
        _cdp_evaluate(ws, 'window.location.href = "/user/scripts"', timeout=10)

        # 等待 SPA 路由切换 + 页面渲染
        for _ in range(15):  # 最多等 30 秒
            if _should_stop():
                ws.close()
                return False
            _sleep(2)
            url_val = _cdp_evaluate(ws, "window.location.href", timeout=5)
            if url_val and "/user/scripts" in str(url_val):
                log(f"[签到] 已到达: {url_val}")
                break
        else:
            log("[签到] [!] 导航到实用脚本页面超时")
            ws.close()
            return False

        if _should_stop():
            ws.close()
            return False

        # ---- Step 2: 点击「一键执行全部账号」 ----
        log("[签到] 查找「一键执行全部账号」按钮...")

        click_js = """(function() {
    var buttons = document.querySelectorAll('button');
    for (var i = 0; i < buttons.length; i++) {
        var text = (buttons[i].textContent || '').trim();
        if (text === '一键执行全部账号') {
            buttons[i].click();
            return JSON.stringify({clicked: true, text: text});
        }
    }
    return JSON.stringify({clicked: false, buttonCount: buttons.length});
})()"""

        click_result = _cdp_evaluate(ws, click_js, timeout=20)
        log(f"[签到] 点击按钮结果: {click_result}")

        if click_result:
            try:
                click_data = json.loads(click_result)
                if not click_data.get("clicked"):
                    log(f"[签到] [!] 未找到「一键执行全部账号」按钮 (找到 {click_data.get('buttonCount', 0)} 个按钮)")
                    ws.close()
                    return False
            except json.JSONDecodeError:
                pass

        log("[签到] 已点击「一键执行全部账号」")

        if _should_stop():
            ws.close()
            return False

        # ---- Step 3: 监控控制台确认签到已触发 ----
        log("[签到] 监控签到执行进度 (最多 15 秒)...")
        console_msgs = _cdp_collect_console(ws, 15.0)

        signin_related = 0
        for msg in console_msgs:
            if "[TGHttps]" in msg:
                signin_related += 1

        if signin_related > 0:
            log(f"[签到] 检测到 {signin_related} 条签到 API 请求，签到完成")
        elif len(console_msgs) > 0:
            log(f"[签到] 检测到 {len(console_msgs)} 条控制台输出，签到已触发")
        else:
            log("[签到] 签到已触发")

        ws.close()
        return True

    except Exception as e:
        log(f"[签到] [!] CDP 操作异常: {e}")
        import traceback
        log(f"[签到] 详细: {traceback.format_exc()}")
        try:
            ws.close()
        except Exception:
            pass
        return False


# ============================================================
# 胡桃工具箱签到（UIA 方式）
# ============================================================

def _run_hutao_checkin(log_func=None, stop_event=None, accounts=None, pause_event=None):
    """胡桃工具箱签到（UIA 方式）：逐个账号切换并签到"""
    import time
    import uiautomation as auto
    import pythoncom
    pythoncom.CoInitialize()

    def log(msg):
        if log_func:
            log_func(msg)

    def _should_stop():
        return stop_event and stop_event.is_set()

    def _sleep(seconds):
        """可中断的 sleep：每 0.5 秒检查 stop_event 和 pause_event"""
        for _ in range(int(seconds * 2)):
            if _should_stop():
                return True
            if pause_event:
                pause_event.wait(0.5)
            time.sleep(0.5)
        return False

    def _find_checkin_button(window):
        """在当前窗口中精确查找 name='签到' 的 ButtonControl，返回 (control, is_enabled) 或 None"""
        try:
            # 先定位内容区 PaneControl (DesktopChildSiteBridge)
            content_pane = None
            for child in window.GetChildren():
                if child.ClassName == "Microsoft.UI.Content.DesktopChildSiteBridge":
                    content_pane = child
                    break
            search_root = content_pane if content_pane else window
            for control, depth in auto.WalkControl(search_root, maxDepth=12):
                if control.ControlTypeName == "ButtonControl":
                    name = control.Name or ""
                    if name == "签到":
                        is_enabled = True
                        try:
                            is_enabled = control.IsEnabled
                        except Exception:
                            pass
                        return control, is_enabled
        except Exception as e:
            log(f"  [UIA] _find_checkin_button 异常: {e}")
        return None

    def _click_home_button(window):
        """在左侧边栏中找 name='主页' 的项并点击，返回是否成功"""
        try:
            # 先定位内容区 PaneControl (DesktopChildSiteBridge)
            content_pane = None
            for child in window.GetChildren():
                if child.ClassName == "Microsoft.UI.Content.DesktopChildSiteBridge":
                    content_pane = child
                    break
            if content_pane:
                # 从内容区搜索主页按钮
                for control, depth in auto.WalkControl(content_pane, maxDepth=10):
                    name = control.Name or ""
                    if name == "主页":
                        control.Click()
                        return True
        except Exception as e:
            log(f"  [UIA] _click_home_button 异常: {e}")
        log("  调试: 未找到主页按钮，打印 UIA 树前 3 层...")
        try:
            search_root = content_pane if content_pane else window
            for control, d in auto.WalkControl(search_root, maxDepth=4):
                if d > 3:
                    continue
                ct = control.ControlTypeName
                name = control.Name or ""
                if name.strip():
                    indent = "  " * d
                    log(f"  {indent}[{ct}] name='{name}'")
        except Exception as e:
            log(f"  [UIA] _click_home_button 异常: {e}")
        return False

    if not accounts:
        log("胡桃签到: 无账号需要签到")
        pythoncom.CoUninitialize()
        return False

    log(f"=== 胡桃签到开始，共 {len(accounts)} 个账号 ===")

    # 1. 启动/查找胡桃窗口
    hutao_win = find_hutao_window()
    need_launch = not hutao_win

    if need_launch:
        log("胡桃未运行，正在启动...")
        cfg = load_config()
        hutao_app_id = cfg.get("snap_hutao", {}).get("app_id", "")
        hutao_exe = cfg.get("snap_hutao", {}).get("exe", "")
        if hutao_app_id:
            start_msix_app(hutao_app_id)
        elif hutao_exe:
            start_exe(hutao_exe)
        else:
            log("错误: 未配置胡桃启动路径")
            pythoncom.CoUninitialize()
            return False

        # 等待窗口出现
        for i in range(15):
            if _should_stop():
                log("签到已停止")
                pythoncom.CoUninitialize()
                return False
            time.sleep(1)
            hutao_win = find_hutao_window()
            if hutao_win:
                break

        if not hutao_win:
            log("错误: 胡桃启动超时")
            pythoncom.CoUninitialize()
            return False

        log("等待胡桃初始化...")
        if _sleep(5):
            log("签到已停止")
            pythoncom.CoUninitialize()
            return False
    else:
        log("胡桃已在运行")

    activate_hutao_window()
    if _sleep(1):
        log("签到已停止")
        pythoncom.CoUninitialize()
        return False

    # 2. 逐个账号签到
    success_count = 0
    fail_count = 0

    for i, target_account in enumerate(accounts):
        if _should_stop():
            log("签到已停止")
            break

        log(f"[{i+1}/{len(accounts)}] 签到账号: {target_account}")

        # 3a. 检查当前账号，必要时切换
        current_name = None
        acct_ctrl = None
        hutao_win = find_hutao_window()
        if hutao_win:
            hwnd_ctrl = auto.ControlFromHandle(hutao_win["hwnd"])
            result = _find_hutao_current_account(hutao_win, hwnd_ctrl)
            if result:
                acct_ctrl, current_name = result
        log(f"  当前账号: {current_name}")

        if current_name != target_account:
            log(f"  正在切换账号...")
            # 点击当前账号名，弹出切换列表
            if acct_ctrl:
                try:
                    acct_ctrl.GetInvokePattern().Invoke()
                except Exception:
                    acct_ctrl.Click()
                _sleep(1.5)
            success = _click_hutao_account_popup(hutao_win, target_account, log)
            if not success:
                log(f"  错误: 未找到账号 '{target_account}' 在弹出列表中")
                fail_count += 1
                continue

            # 关弹窗 → 回主页
            hutao_win2 = find_hutao_window()
            if hutao_win2:
                if _sleep(1):
                    log("签到已停止")
                    break
                # 点击窗口空白区域关弹窗（中心偏右避开侧栏）
                window = auto.ControlFromHandle(hutao_win2["hwnd"])
                rect = window.BoundingRectangle
                cx = rect.left + int(rect.width() * 0.7)
                cy = rect.top + int(rect.height() * 0.5)
                auto.Click(cx, cy)
                if _sleep(0.5):
                    log("签到已停止")
                    break
                # 回到主页
                if not _click_home_button(window):
                    log("  警告: 无法确认在主页")
                if _sleep(1):
                    log("签到已停止")
                    break

            # 验证切换结果
            hutao_win = find_hutao_window()
            if hutao_win:
                hwnd_ctrl = auto.ControlFromHandle(hutao_win["hwnd"])
                result = _find_hutao_current_account(hutao_win, hwnd_ctrl)
                current_account = result[1] if result else None
            else:
                current_account = None
            if current_account != target_account:
                log(f"  警告: 切换后账号为 '{current_account}'，期望 '{target_account}'")

        # 3b. 等待页面刷新（WebView2 渲染有延迟）
        if _sleep(2):
            log("签到已停止")
            break

        # 3c. 查找签到按钮
        hutao_win = find_hutao_window()
        if not hutao_win:
            log("  错误: 胡桃窗口丢失")
            fail_count += 1
            continue

        window = auto.ControlFromHandle(hutao_win["hwnd"])

        # 确保在主页
        if not _click_home_button(window):
            log("  警告: 无法确认在主页，直接搜索签到按钮...")
        window = auto.ControlFromHandle(hutao_win["hwnd"])
        if _sleep(3):
            log("签到已停止")
            break

        sign_result = _find_checkin_button(window)
        if not sign_result:
            log(f"  未找到签到按钮，可能不在主页或界面异常")
            fail_count += 1
            continue

        sign_btn, is_enabled = sign_result
        log(f"  找到签到按钮, IsEnabled={is_enabled}")

        if not is_enabled:
            log(f"  已签到（按钮灰色不可点击），跳过")
            success_count += 1
            continue

        # 点击签到按钮
        try:
            invoke = sign_btn.GetInvokePattern()
            invoke.Invoke()
            log("  已点击签到按钮（Invoke），等待服务器响应...")
        except Exception:
            try:
                rect = sign_btn.BoundingRectangle
                cx = rect.left + rect.width() // 2
                cy = rect.top + rect.height() // 2
                auto.Click(cx, cy)
                log("  已点击签到按钮（物理坐标），等待服务器响应...")
            except Exception as e:
                log(f"  点击签到按钮失败: {e}")
                fail_count += 1
                continue

        # 等待服务器处理
        if _sleep(3):
            log("签到已停止")
            break

        # 重新获取窗口句柄，避免 UIA 树过期
        hutao_win = find_hutao_window()
        if not hutao_win:
            log("  无法验证签到结果，胡桃窗口丢失")
            fail_count += 1
            continue
        window = auto.ControlFromHandle(hutao_win["hwnd"])

        # 验证签到结果：按钮应变为不可点击
        sign_result2 = _find_checkin_button(window)
        if sign_result2:
            _, is_enabled2 = sign_result2
            if not is_enabled2:
                log(f"  签到成功（按钮已变灰）")
                success_count += 1
            else:
                log(f"  签到可能失败（按钮仍可点击），计入失败")
                fail_count += 1
        else:
            log(f"  无法验证签到结果，按钮已消失")
            fail_count += 1

        if _sleep(1):
            log("签到已停止")
            break

    log(f"=== 胡桃签到结束: 成功 {success_count}/{len(accounts)}, 失败 {fail_count} ===")
    pythoncom.CoUninitialize()
    return success_count > 0


# ============================================================
# CheckinScheduleDialog - 定时签到设置对话框
# ============================================================

class CheckinScheduleDialog:
    """定时签到设置弹窗：时间选择 + 已有签到任务管理"""

    def __init__(self, parent, gui):
        self.gui = gui
        self.dlg = tk.Toplevel(parent)
        # 清除 Tkinter 默认图标
        self.dlg.tk.call("wm", "iconbitmap", self.dlg._w, "-default", _gen_blank_ico())
        self.dlg.iconbitmap(_gen_blank_ico())
        self.dlg.title("定时签到")
        self.dlg.geometry("550x600")
        self.dlg.minsize(450, 400)
        self.dlg.resizable(True, True)
        self.dlg.configure(bg=COLORS["bg"])
        self.dlg.transient(parent)
        self.dlg.grab_set()

        # 外层 Canvas + 自适应滚动条
        self.outer_canvas = tk.Canvas(self.dlg, bg=COLORS["bg"],
                                       highlightthickness=0, bd=0)
        self.outer_scrollbar = ttk.Scrollbar(self.dlg, orient="vertical",
                                              command=self.outer_canvas.yview)

        self.content = tk.Frame(self.outer_canvas, bg=COLORS["bg"])
        self.outer_canvas.create_window((0, 0), window=self.content,
                                         anchor="nw", tags="content")

        def _update_outer_scroll(*args):
            self.outer_canvas.update_idletasks()
            bbox = self.outer_canvas.bbox("all")
            if bbox:
                content_h = bbox[3] - bbox[1]
                canvas_h = self.outer_canvas.winfo_height()
                if content_h > canvas_h:
                    self.outer_scrollbar.pack(side="right", fill="y")
                    self.outer_canvas.configure(yscrollcommand=self.outer_scrollbar.set)
                    self.outer_canvas.configure(scrollregion=bbox)
                else:
                    self.outer_scrollbar.pack_forget()
                    self.outer_canvas.configure(yscrollcommand="")

        self.content.bind("<Configure>", _update_outer_scroll)
        self.outer_canvas.bind("<Configure>", _update_outer_scroll)

        self.outer_canvas.pack(side="left", fill="both", expand=True)

        def _on_mousewheel(event):
            bbox = self.outer_canvas.bbox("all")
            if not bbox:
                return
            content_h = bbox[3] - bbox[1]
            visible_h = self.outer_canvas.winfo_height()
            if content_h > visible_h:
                self.outer_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.outer_canvas.bind("<MouseWheel>", _on_mousewheel)
        self.outer_canvas.bind("<Enter>",
            lambda e: self.outer_canvas.bind_all("<MouseWheel>", _on_mousewheel))
        self.outer_canvas.bind("<Leave>",
            lambda e: self.outer_canvas.unbind_all("<MouseWheel>"))

        self._build()

    def _build(self):
        # === 签到任务设置 LabelFrame ===
        outer = tk.LabelFrame(self.content, text="签到任务设置", bg=COLORS["bg"],
                               fg=COLORS["text"], font=("Microsoft YaHei", 10, "bold"))
        outer.pack(fill="x", padx=20, pady=(12, 0))

        # 签到方式 RadioButton
        self.checkin_type_var = tk.StringVar(value="teyvatguide")
        self.checkin_type_var.trace_add("write", self._on_checkin_type_changed)
        rb1 = tk.Radiobutton(outer, text="TeyvatGuide 签到（CDP 方式，自动一键全部账号）",
                             variable=self.checkin_type_var, value="teyvatguide",
                             bg=COLORS["bg"])
        rb1.pack(anchor="w", padx=10, pady=(8, 1))
        rb2 = tk.Radiobutton(outer, text="胡桃工具箱签到（UIA 方式，逐个账号切换签到）",
                             variable=self.checkin_type_var, value="hutao",
                             bg=COLORS["bg"])
        rb2.pack(anchor="w", padx=10, pady=(1, 4))

        # 胡桃账号选择区域（默认隐藏）
        self.hutao_accounts_frame = tk.LabelFrame(outer, text="签到账号（米游社名称）",
                                                   bg=COLORS["bg"],
                                                   fg=COLORS["text"],
                                                   font=("Microsoft YaHei", 9, "bold"))

        # 手动添加账号
        add_frame = tk.Frame(self.hutao_accounts_frame, bg=COLORS["bg"])
        add_frame.pack(fill="x", padx=10, pady=(4, 0))

        self.dlg.hutao_add_var = tk.StringVar()
        add_entry = tk.Entry(add_frame,
                             textvariable=self.dlg.hutao_add_var,
                             font=("Microsoft YaHei", 9), width=18)
        add_entry.pack(side="left", padx=(0, 4))
        add_btn = tk.Button(add_frame, text="添加",
                            command=self._dlg_add_hutao_account,
                            bg=COLORS["primary"], fg=COLORS["text_white"],
                            font=("Microsoft YaHei", 9), padx=8,
                            relief="flat", cursor="hand2", bd=0)
        add_btn.pack(side="left")

        # 账号勾选列表（带滚动条）
        list_frame = tk.Frame(self.hutao_accounts_frame, bg=COLORS["bg"])
        list_frame.pack(fill="both", expand=True, padx=10, pady=(4, 4))

        canvas = tk.Canvas(list_frame, bg=COLORS["bg"],
                           highlightthickness=0, bd=0)
        scrollbar = tk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=COLORS["bg"])
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.pack(side="left", fill="both", expand=True)

        def _update_hutao_scroll(*args):
            canvas.update_idletasks()
            bbox = canvas.bbox("all")
            if bbox:
                content_h = bbox[3] - bbox[1]
                canvas_h = canvas.winfo_height()
                if content_h > canvas_h:
                    scrollbar.pack(side="right", fill="y")
                    canvas.configure(yscrollcommand=scrollbar.set)
                    canvas.configure(scrollregion=bbox)
                else:
                    scrollbar.pack_forget()
                    canvas.configure(yscrollcommand="")

        inner.bind("<Configure>", _update_hutao_scroll)
        canvas.bind("<Configure>", _update_hutao_scroll)

        self.dlg.hutao_check_vars = {}
        self.dlg.hutao_row_frames = {}
        self.dlg.hutao_canvas = canvas
        self.dlg.hutao_inner = inner
        self.dlg.hutao_update_scroll = _update_hutao_scroll

        # 从已有账号自动填充（所有有 hutao_account 的账号，不限类型、默认全选）
        auto_names = set()
        for acc in load_config().get("accounts", []):
            name = acc.get("hutao_account", "").strip()
            if name:
                auto_names.add(name)

        for name in sorted(auto_names):
            self._dlg_make_hutao_row(name, is_auto=True, checked=True)

        # 时间选择行
        time_frame = tk.Frame(outer, bg=COLORS["bg"])
        time_frame.pack(fill="x", padx=10, pady=(8, 8))

        self.hour_var = tk.StringVar(value="08")
        self.minute_var = tk.StringVar(value="00")
        self._trace_suppress = False
        self._prev_hour = self.hour_var.get()
        self._prev_minute = self.minute_var.get()

        tk.Label(time_frame, text="执行时间：", bg=COLORS["bg"],
                 fg=COLORS["text"], font=("Microsoft YaHei", 10)).pack(side="left")

        # 小时区
        tk.Button(time_frame, text="◀", command=lambda: self._adj_hour(-1),
                  bg="#EEF2F7", fg=COLORS["text"], relief="flat",
                  font=("Microsoft YaHei", 8), padx=3, cursor="hand2", bd=0,
                  width=2).pack(side="left", padx=(4, 1))
        ttk.Combobox(time_frame, textvariable=self.hour_var, width=3,
                     values=[f"{h:02d}" for h in range(24)],
                     state="normal", font=("Microsoft YaHei", 11)).pack(side="left")
        tk.Button(time_frame, text="▶", command=lambda: self._adj_hour(1),
                  bg="#EEF2F7", fg=COLORS["text"], relief="flat",
                  font=("Microsoft YaHei", 8), padx=3, cursor="hand2", bd=0,
                  width=2).pack(side="left")
        tk.Label(time_frame, text="时", bg=COLORS["bg"],
                 fg=COLORS["text"], font=("Microsoft YaHei", 9)).pack(side="left", padx=(2, 8))

        # 分钟区
        tk.Button(time_frame, text="◀", command=lambda: self._adj_minutes(-1),
                  bg="#EEF2F7", fg=COLORS["text"], relief="flat",
                  font=("Microsoft YaHei", 8), padx=3, cursor="hand2", bd=0,
                  width=2).pack(side="left", padx=(0, 1))
        ttk.Combobox(time_frame, textvariable=self.minute_var, width=3,
                     values=[f"{m:02d}" for m in range(60)],
                     state="normal", font=("Microsoft YaHei", 11)).pack(side="left")
        tk.Button(time_frame, text="▶", command=lambda: self._adj_minutes(1),
                  bg="#EEF2F7", fg=COLORS["text"], relief="flat",
                  font=("Microsoft YaHei", 8), padx=3, cursor="hand2", bd=0,
                  width=2).pack(side="left")
        tk.Label(time_frame, text="分", bg=COLORS["bg"],
                 fg=COLORS["text"], font=("Microsoft YaHei", 9)).pack(side="left")

        self.hour_var.trace_add("write", self._validate_hour)
        self.minute_var.trace_add("write", self._validate_minute)

        # 添加按钮
        self.btn_frame = tk.Frame(outer, bg=COLORS["bg"])
        self.btn_frame.pack(pady=(8, 10))

        tk.Button(self.btn_frame, text="添加定时签到", command=self._add_schedule,
                  bg=COLORS["primary"], fg=COLORS["text_white"],
                  font=("Microsoft YaHei", 10), padx=20, pady=5,
                  relief="flat", cursor="hand2", bd=0).pack()

        # === 已有签到任务 ===
        tk.Label(self.content, text="已有的签到任务：", bg=COLORS["bg"],
                 fg=COLORS["text"], font=("Microsoft YaHei", 9, "bold")).pack(
                     anchor="w", padx=20, pady=(10, 4))

        # 滚动列表区域
        list_frame = tk.Frame(self.content, bg=COLORS["bg"])
        list_frame.pack(fill="both", expand=True, padx=20, pady=(0, 8))

        self.checkin_canvas = tk.Canvas(list_frame, bg="#FFFFFF", highlightthickness=0)
        self.checkin_scroll = ttk.Scrollbar(list_frame, orient="vertical",
                                             command=self.checkin_canvas.yview)
        self.checkin_canvas.configure(yscrollcommand=self.checkin_scroll.set)
        self.checkin_canvas.pack(side="left", fill="both", expand=True)

        self.checkin_inner = tk.Frame(self.checkin_canvas, bg="#FFFFFF")
        self._checkin_window_id = self.checkin_canvas.create_window(
            (0, 0), window=self.checkin_inner, anchor="nw")

        def _on_checkin_inner_resize(event):
            self.checkin_canvas.configure(scrollregion=self.checkin_canvas.bbox("all"))
            self.dlg.after(50, self._update_checkin_scrollbar)
        self.checkin_inner.bind("<Configure>", _on_checkin_inner_resize)

        def _on_checkin_canvas_configure(event):
            self.checkin_canvas.itemconfig(self._checkin_window_id, width=event.width)
            self.dlg.after(50, self._update_checkin_scrollbar)
        self.checkin_canvas.bind("<Configure>", _on_checkin_canvas_configure)

        def _checkin_mousewheel(event):
            if not self.checkin_canvas.winfo_exists():
                return
            bbox = self.checkin_canvas.bbox("all")
            if bbox and bbox[3] > self.checkin_canvas.winfo_height():
                self.checkin_canvas.yview_scroll(int(-event.delta / 120), "units")
        self.checkin_canvas.bind("<MouseWheel>", _checkin_mousewheel)
        self.checkin_inner.bind("<MouseWheel>", _checkin_mousewheel)

        self._load_list()

    def _set_time(self, h, m):
        self._trace_suppress = True
        self.hour_var.set(h)
        self.minute_var.set(m)
        self._trace_suppress = False

    def _validate_hour(self, *args):
        if self._trace_suppress:
            return
        val = self.hour_var.get()
        if not val:
            self._prev_hour = ""
            return
        if not val.isdigit() or int(val) > 23 or len(val) > 2:
            self._trace_suppress = True
            self.hour_var.set(self._prev_hour)
            self._trace_suppress = False
        else:
            self._prev_hour = val

    def _validate_minute(self, *args):
        if self._trace_suppress:
            return
        val = self.minute_var.get()
        if not val:
            self._prev_minute = ""
            return
        if not val.isdigit() or int(val) > 59 or len(val) > 2:
            self._trace_suppress = True
            self.minute_var.set(self._prev_minute)
            self._trace_suppress = False
        else:
            self._prev_minute = val

    def _adj_hour(self, delta):
        try:
            h = int(self.hour_var.get())
        except ValueError:
            h = 0
        h = (h + delta) % 24
        self._trace_suppress = True
        self.hour_var.set(f"{h:02d}")
        self._trace_suppress = False
        self._prev_hour = f"{h:02d}"

    def _adj_minutes(self, delta):
        try:
            m = int(self.minute_var.get())
        except ValueError:
            m = 0
        m = (m + delta) % 60
        self._trace_suppress = True
        self.minute_var.set(f"{m:02d}")
        self._trace_suppress = False
        self._prev_minute = f"{m:02d}"

    def _add_schedule(self):
        try:
            h = int(self.hour_var.get())
            m = int(self.minute_var.get())
        except ValueError:
            messagebox.showwarning("提示", "请输入正确的时间")
            return
        if not (0 <= h <= 23 and 0 <= m <= 59):
            messagebox.showwarning("提示", "请输入正确的时间")
            return

        time_str = f"{h:02d}:{m:02d}"

        # 检查重复
        cfg = load_checkin_schedule()
        for s in cfg["checkins"]:
            if s["time"] == time_str:
                messagebox.showwarning("提示", f"已存在 {time_str} 的定时签到任务")
                return

        # 生成唯一 ID
        import uuid
        task_id = f"checkin_{uuid.uuid4().hex[:8]}"

        new_task = {
            "id": task_id,
            "time": time_str,
            "schedule_type": "daily",
            "enabled": True,
            "label": f"每日签到 {time_str}",
            "date": "",
            "weekdays": [],
            "dates": [],
            "checkin_type": self.checkin_type_var.get(),
            "hutao_accounts": self._get_selected_hutao_accounts(),
        }

        # 胡桃签到需至少选一个账号
        if new_task["checkin_type"] == "hutao" and not new_task["hutao_accounts"]:
            messagebox.showwarning("提示", "请至少选择一个签到账号")
            return

        cfg["checkins"].append(new_task)
        save_checkin_schedule(cfg)
        self.gui._log(f"[签到] 已添加定时签到: 每天 {time_str}")

        # 启动独立签到调度器
        self.gui.start_checkin_scheduler()

        self._load_list()
        if self.gui.tray:
            self.gui.root.after(0, self.gui._rebuild_tray_menu)

    def _delete_schedule(self, entry_id):
        """删除指定签到条目（通过 id 直接定位）"""
        cfg = load_checkin_schedule()
        before_count = len(cfg["checkins"])
        cfg["checkins"] = [c for c in cfg["checkins"] if c.get("id") != entry_id]
        after_count = len(cfg["checkins"])
        if after_count < before_count:
            save_checkin_schedule(cfg)
            self.gui._log(f"[签到] 已删除定时签到条目")
        self._load_list()
        if self.gui.tray:
            self.gui.root.after(0, self.gui._rebuild_tray_menu)

    def _update_checkin_scrollbar(self):
        """签到任务列表：内容超出时显示滚动条"""
        if not self.checkin_canvas.winfo_exists():
            return
        bbox = self.checkin_canvas.bbox("all")
        if bbox and bbox[3] > self.checkin_canvas.winfo_height():
            self.checkin_scroll.pack(side="right", fill="y")
            self.checkin_canvas.pack(side="left", fill="both", expand=True)
        else:
            self.checkin_scroll.pack_forget()
            self.checkin_canvas.pack(fill="both", expand=True)

    def _load_list(self):
        """加载签到任务列表（frame 列表 + 启停按钮）"""
        for w in self.checkin_inner.winfo_children():
            w.destroy()

        cfg = load_checkin_schedule()
        checkins = cfg.get("checkins", [])

        if not checkins:
            tk.Label(self.checkin_inner, text="（暂无签到任务）", bg="#FFFFFF",
                     fg=COLORS["text_light"],
                     font=("Microsoft YaHei", 10)).pack(pady=20)
            self.dlg.after(50, self._update_checkin_scrollbar)
            return

        for i, c in enumerate(checkins):
            time_str = c.get("time", "??:??")
            checkin_type = c.get("checkin_type", "teyvatguide")
            type_label = "胡桃" if checkin_type == "hutao" else "TG"
            enabled = c.get("enabled", True)
            entry_id = c.get("id", "")

            row = tk.Frame(self.checkin_inner, bg="#FFFFFF")
            row.pack(fill="x", pady=1)

            desc_label = tk.Label(row, text=f"每天 {time_str} ({type_label})",
                                  anchor="w", bg="#FFFFFF", fg=COLORS["text"],
                                  font=("Microsoft YaHei", 10))
            desc_label.pack(side="left", padx=(8, 4), fill="x", expand=True)

            # 删除按钮
            del_btn = tk.Button(row, text="删除",
                                command=lambda eid=entry_id: self._delete_schedule(eid),
                                bg=COLORS["danger"], fg=COLORS["text_white"],
                                font=("Microsoft YaHei", 9), padx=8,
                                relief="flat", cursor="hand2", bd=0)
            del_btn.pack(side="right", padx=(4, 2), pady=3)

            # 启停按钮
            btn_text = "停用" if enabled else "启用"
            btn_color = "#E74C3C" if enabled else "#52C41A"
            btn_active = "#C0392B" if enabled else "#389E0D"

            def make_toggle(idx=i):
                def toggle():
                    cfg2 = load_checkin_schedule()
                    chk = cfg2["checkins"]
                    if idx < len(chk):
                        new_state = not chk[idx].get("enabled", True)
                        chk[idx]["enabled"] = new_state
                        save_checkin_schedule(cfg2)
                        if new_state:
                            self.gui.start_checkin_scheduler()
                            self.gui._log(f"[签到] 任务已启用: {chk[idx].get('time', '??:??')}")
                        else:
                            # 检查是否所有任务都已停用
                            cfg3 = load_checkin_schedule()
                            if not any(c2.get("enabled", True) for c2 in cfg3.get("checkins", [])):
                                self.gui.stop_checkin_scheduler()
                            self.gui._log(f"[签到] 任务已停用: {chk[idx].get('time', '??:??')}")
                    self._load_list()
                    if self.gui.tray:
                        self.gui.root.after(0, self.gui._rebuild_tray_menu)
                return toggle

            toggle_btn = tk.Button(row, text=btn_text, command=make_toggle(),
                                   bg=btn_color, fg="#FFFFFF",
                                   activebackground=btn_active,
                                   relief="flat", font=("Microsoft YaHei", 9),
                                   width=5, cursor="hand2", bd=0)
            toggle_btn.pack(side="right", padx=(2, 8), pady=3)

            # 分隔线
            if i < len(checkins) - 1:
                tk.Frame(self.checkin_inner, height=1, bg="#E1E8F0").pack(
                    fill="x", padx=10, pady=2)

        self.dlg.after(50, self._update_checkin_scrollbar)

    def _dlg_make_hutao_row(self, name, is_auto=False, checked=True):
        """在列表内创建一行胡桃账号"""
        inner = self.dlg.hutao_inner
        row = tk.Frame(inner, bg=COLORS["bg"])
        row.pack(fill="x", anchor="w")
        self.dlg.hutao_row_frames[name] = row

        var = tk.BooleanVar(value=checked)
        self.dlg.hutao_check_vars[name] = var

        cb = tk.Checkbutton(row, text=name, variable=var,
                            bg=COLORS["bg"])
        cb.pack(side="left", anchor="w")

        if not is_auto:
            del_btn = tk.Button(row, text="✕",
                                command=lambda n=name: self._dlg_del_hutao_account(n),
                                bg=COLORS["bg"], fg=COLORS["danger"],
                                font=("Microsoft YaHei", 9),
                                relief="flat", cursor="hand2", bd=0, padx=4)
            del_btn.pack(side="right", padx=(0, 4))

        self.dlg.hutao_update_scroll()

    def _dlg_add_hutao_account(self):
        """手动添加胡桃账号到列表"""
        name = self.dlg.hutao_add_var.get().strip()
        if not name:
            return
        if name in self.dlg.hutao_check_vars:
            messagebox.showwarning("提示", f"账号「{name}」已存在")
            return
        self.dlg.hutao_add_var.set("")
        self._dlg_make_hutao_row(name, is_auto=False, checked=True)

    def _dlg_del_hutao_account(self, name):
        """删除手动添加的胡桃账号行"""
        row = self.dlg.hutao_row_frames.get(name)
        if row is None:
            return
        row.destroy()
        del self.dlg.hutao_row_frames[name]
        del self.dlg.hutao_check_vars[name]
        self.dlg.hutao_update_scroll()

    def _get_hutao_account_options(self):
        """从 config.json 获取所有可用的胡桃账号名（不限类型）"""
        cfg = load_config()
        result = []
        for acc in cfg.get("accounts", []):
            name = acc.get("hutao_account", "").strip()
            if name:
                result.append(name)
        return result

    def _get_selected_hutao_accounts(self):
        return [name for name, var in self.dlg.hutao_check_vars.items() if var.get()]

    def _on_checkin_type_changed(self, *args):
        if self.checkin_type_var.get() == "hutao":
            self.hutao_accounts_frame.pack(fill="both", expand=True, pady=(4, 0),
                                           before=self.btn_frame)
        else:
            self.hutao_accounts_frame.pack_forget()


# ============================================================
# GenshinMultiAccountToolGUI - 主界面（从 pyc 反汇编重建）
# ============================================================

class GenshinMultiAccountToolGUI:
    """原神多账号辅助工具 v5.5 - 主界面"""

    def __init__(self, root):
        self.root = root
        self.root.title("GenshinMultiAccountTool v5.5")
        self.root.geometry("960x600")
        self.root.minsize(700, 500)
        self.root.configure(bg=COLORS["bg"])

        self.cfg = load_config()
        self.worker = None
        self.log_queue = queue.Queue()
        self._log_poll_interval = 100  # ms，空闲时动态增加到 500
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()  # 暂停事件：set=运行, clear=暂停
        self.pause_event.set()  # 初始为运行状态
        self.running = False
        self.paused = False

        # 调度器相关（孤儿方法需要）
        self.scheduler_event = threading.Event()  # set=停止, clear=运行
        self.scheduler_thread = None
        self._shutdown_pending = False

        # 签到线程控制
        self.checkin_stop_event = None  # 签到线程的停止信号
        self.checkin_thread = None

        # 独立定时签到调度器
        self.checkin_scheduler_event = threading.Event()  # set=停止, clear=运行
        self.checkin_scheduler_thread = None
        self.checkin_scheduler_enabled = False  # 总开关

        # 任务执行互斥锁：定时计划与定时签到不可同时运行
        self.task_exec_lock = threading.Lock()

        # 热键相关
        self._hotkey_ids = {}       # hotkey_name → atom_id
        self._hotkey_atoms = {}     # atom_id → hotkey_name
        self._hotkey_thread = None
        self._hotkey_ready = threading.Event()
        self._hotkey_reload = threading.Event()
        self._pending_hotkeys = []

        # 系统托盘
        self.tray = None
        self._quitting = False
        self._hotkey_stop_event = threading.Event()
        if HAS_TRAY:
            self._setup_tray()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._setup_styles()
        self._build_ui()
        self._load_accounts()
        self._poll_log()
        self._register_hotkey()

        # 自动启动独立签到调度器（若配置中 enabled 为 True）
        if load_checkin_schedule().get("enabled", False):
            self.start_checkin_scheduler()

    def _setup_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        # 全局背景
        style.configure(".", background=COLORS["bg"],
                        foreground=COLORS["text"],
                        font=("Microsoft YaHei", 9))

        style.configure("TFrame", background=COLORS["bg"])
        style.configure("TLabelframe", background=COLORS["bg"],
                        foreground=COLORS["text"],
                        bordercolor=COLORS["border"],
                        relief="flat")
        style.configure("TLabelframe.Label", background=COLORS["bg"],
                        foreground=COLORS["text"],
                        font=("Microsoft YaHei", 9, "bold"))
        style.configure("TLabel", background=COLORS["bg"],
                        foreground=COLORS["text"])

        # 扁平按钮：浅蓝主色
        style.configure("TButton",
                        background=COLORS["primary"],
                        foreground=COLORS["text_white"],
                        borderwidth=0,
                        focusthickness=0,
                        padding=(12, 5),
                        font=("Microsoft YaHei", 9))
        style.map("TButton",
                  background=[("active", COLORS["primary_hover"]),
                              ("disabled", "#B0C8E0")],
                  foreground=[("disabled", "#E8E8E8")])

        style.configure("TCheckbutton", background=COLORS["panel_bg"],
                        foreground=COLORS["text"])
        style.configure("TRadiobutton", background=COLORS["panel_bg"],
                        foreground=COLORS["text"])

        # 滚动条：细条样式，统一应用于所有区域
        style.configure("Vertical.TScrollbar",
                        background=COLORS["bg"],
                        troughcolor=COLORS["panel_bg"],
                        arrowcolor=COLORS["primary"],
                        bordercolor=COLORS["border"],
                        gripcount=0,
                        arrowsize=10,
                        relief="flat",
                        borderwidth=1)
        style.configure("Horizontal.TScrollbar",
                        background=COLORS["bg"],
                        troughcolor=COLORS["panel_bg"],
                        arrowcolor=COLORS["primary"],
                        bordercolor=COLORS["border"],
                        gripcount=0,
                        arrowsize=10,
                        relief="flat",
                        borderwidth=1)

        # 进度条
        style.configure("TProgressbar",
                        background=COLORS["primary"],
                        troughcolor=COLORS["border"],
                        borderwidth=0,
                        thickness=8)

    def _build_ui(self):
        # 顶部标题栏
        self.title_bar = tk.Frame(self.root, bg=COLORS["primary_dark"])
        self.title_bar.pack(fill="x")
        self.title_bar.pack_propagate(False)
        self.title_bar.configure(height=50)

        title_lbl = tk.Label(self.title_bar,
                             text="   原神多账号辅助工具",
                             bg=COLORS["primary_dark"],
                             fg=COLORS["text_white"],
                             font=("Microsoft YaHei", 14, "bold"))
        title_lbl.pack(side="left")

        version_lbl = tk.Label(self.title_bar,
                               text=" v5.5 ",
                               bg=COLORS["primary_dark"],
                               fg="#A0C8E8",
                               font=("Microsoft YaHei", 9))
        version_lbl.pack(side="right", padx=(0, 2))

        # 标题栏右侧按钮：置顶 | 定时计划 | 设置
        self.topmost_btn = tk.Label(self.title_bar, text="📌", cursor="hand2",
                                    bg=COLORS["primary_dark"], fg=COLORS["text_white"],
                                    font=("Microsoft YaHei", 11))
        self.topmost_btn.pack(side="right", padx=1)
        self.topmost_btn.bind("<Button-1>", lambda e: self._toggle_title_topmost())
        self._topmost_active = False

        self.title_scheduler_btn = tk.Label(self.title_bar, text="定时任务", cursor="hand2",
                                            bg=COLORS["primary_dark"], fg=COLORS["text_white"],
                                            font=("Microsoft YaHei", 10))
        self.title_scheduler_btn.pack(side="right", padx=1)
        self.title_scheduler_btn.bind("<Button-1>", lambda e: self._open_scheduler())

        self.title_settings_btn = tk.Label(self.title_bar, text="设置", cursor="hand2",
                                           bg=COLORS["primary_dark"], fg=COLORS["text_white"],
                                           font=("Microsoft YaHei", 10))
        self.title_settings_btn.pack(side="right", padx=1)
        self.title_settings_btn.bind("<Button-1>", lambda e: self._open_settings())

        self.title_checkin_schedule_btn = tk.Label(self.title_bar, text="定时签到", cursor="hand2",
                                                    bg=COLORS["primary_dark"], fg=COLORS["text_white"],
                                                    font=("Microsoft YaHei", 10))
        self.title_checkin_schedule_btn.pack(side="right", padx=1)
        self.title_checkin_schedule_btn.bind("<Button-1>", lambda e: self._open_checkin_schedule())

        self.title_checkin_btn = tk.Label(self.title_bar, text="一键签到", cursor="hand2",
                                           bg=COLORS["primary_dark"], fg=COLORS["text_white"],
                                           font=("Microsoft YaHei", 10))
        self.title_checkin_btn.pack(side="right", padx=1)
        self.title_checkin_btn.bind("<Button-1>", lambda e: self._do_checkin())

        # 底部状态栏（先于主区域打包，缩小窗口不会被隐藏）
        status_bar = tk.Frame(self.root, bg=COLORS["primary_dark"])
        status_bar.pack(fill="x", side="bottom")

        self.status = tk.Label(status_bar, text="就绪",
                               bg=COLORS["primary_dark"],
                               fg=COLORS["text_white"],
                               font=("Microsoft YaHei", 9))
        self.status.pack(side="left", padx=10, pady=4)

        tip = tk.Label(status_bar,
                       text="Ctrl+Shift+Q 停止  |  Ctrl+Shift+P 暂停/继续  |  "
                            "Esc/数字键1 绑定窗口",
                       bg=COLORS["primary_dark"],
                       fg="#A0C8E8",
                       font=("Microsoft YaHei", 8))
        tip.pack(side="right", padx=10, pady=4)

        # 主区域
        main_frame = tk.Frame(self.root, bg=COLORS["bg"])
        main_frame.pack(fill="both", expand=True, padx=8, pady=8)

        # 底部控制栏（放在 main_frame 层面，始终可见）
        bottom_bar = tk.Frame(main_frame, bg=COLORS["bg"])
        bottom_bar.pack(side="bottom", fill="x")

        # 添加/删除账号按钮
        self.add_btn = tk.Button(bottom_bar, text="+ 添加账号",
                                 bg=COLORS["success"],
                                 fg=COLORS["text_white"],
                                 font=("Microsoft YaHei", 9),
                                 relief="flat",
                                 activebackground="#45B016",
                                 cursor="hand2",
                                 command=self._add_account)
        self.add_btn.pack(side="left", padx=(0, 3))

        self.del_btn = tk.Button(bottom_bar, text=" 删 除 ",
                                 bg=COLORS["danger"],
                                 fg=COLORS["text_white"],
                                 font=("Microsoft YaHei", 9),
                                 relief="flat",
                                 activebackground="#C0392B",
                                 cursor="hand2",
                                 command=self._delete_account)
        self.del_btn.pack(side="left", padx=(3, 12))

        self.start_btn = tk.Button(bottom_bar, text="▶ 开始",
                                   bg=COLORS["primary"],
                                   fg=COLORS["text_white"],
                                   font=("Microsoft YaHei", 10, "bold"),
                                   relief="flat",
                                   activebackground=COLORS["primary_hover"],
                                   cursor="hand2",
                                   width=10, command=self._start)
        self.start_btn.pack(side="left", padx=(0, 6))

        self.pause_btn = tk.Button(bottom_bar, text="⏸ 暂停",
                                   bg="#E0E4E8",
                                   fg=COLORS["text_light"],
                                   font=("Microsoft YaHei", 10, "bold"),
                                   relief="flat",
                                   width=10, command=self._pause_toggle,
                                   state="disabled")
        self.pause_btn.pack(side="left", padx=6)

        self.stop_btn = tk.Button(bottom_bar, text="■ 停止",
                                  bg="#E0E4E8",
                                  fg=COLORS["text_light"],
                                  font=("Microsoft YaHei", 10, "bold"),
                                  relief="flat",
                                  width=10, command=self._stop,
                                  state="disabled")
        self.stop_btn.pack(side="left", padx=6)

        # 取消关机按钮（初始隐藏）
        self.cancel_shutdown_btn = tk.Button(bottom_bar, text="✕ 取消关机",
                                             bg="#E74C3C",
                                             fg=COLORS["text_white"],
                                             font=("Microsoft YaHei", 10, "bold"),
                                             relief="flat",
                                             width=12, command=self._cancel_shutdown,
                                             cursor="hand2")

        # 进度条
        self.progress_label = tk.Label(bottom_bar, text="0/0",
                                       bg=COLORS["bg"],
                                       fg=COLORS["text_light"],
                                       font=("Microsoft YaHei", 8))
        self.progress_label.pack(side="left", padx=(5, 0))
        self.progress = ttk.Progressbar(bottom_bar, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=12)

        # ---------- 左侧面板：账号列表 ----------
        left_panel = tk.Frame(main_frame, bg=COLORS["panel_bg"],
                              highlightthickness=1,
                              highlightbackground=COLORS["border"],
                              padx=1, pady=1, width=380)
        left_panel.pack(side="left", fill="both", padx=(0, 8))
        left_panel.pack_propagate(False)  # 固定宽度不随内容撑大

        self.left_header = tk.Frame(left_panel, bg=COLORS["primary"])
        self.left_header.pack(fill="x")
        tk.Label(self.left_header, text=" 账号列表",
                 bg=COLORS["primary"], fg=COLORS["text_white"],
                 font=("Microsoft YaHei", 10, "bold")).pack(
            side="left", pady=5)

        list_container = tk.Frame(left_panel, bg=COLORS["panel_bg"])
        list_container.pack(fill="both", expand=True)

        self.account_canvas = tk.Canvas(list_container,
                                        bg=COLORS["panel_bg"],
                                        highlightthickness=0)
        self.account_scroll = ttk.Scrollbar(list_container,
                                           orient="vertical",
                                           command=self.account_canvas.yview)
        self.account_canvas.configure(
            yscrollcommand=self.account_scroll.set)

        self.account_scroll_visible = False
        self.account_canvas.pack(side="left", fill="both", expand=True)

        self.account_frame = tk.Frame(self.account_canvas,
                                      bg=COLORS["panel_bg"])
        self.account_canvas.create_window((0, 0),
                                          window=self.account_frame,
                                          anchor="nw",
                                          tags="account_inner")

        self.account_frame.bind(
            "<Configure>",
            lambda e: self._update_account_scrollbar())

        def _on_mousewheel(event):
            if not self.account_canvas.winfo_exists():
                return
            bbox = self.account_canvas.bbox("all")
            if bbox and bbox[3] > self.account_canvas.winfo_height():
                self.account_canvas.yview_scroll(
                    int(-1 * (event.delta / 120)), "units")
        self.account_canvas.bind("<MouseWheel>", _on_mousewheel)

        # ---------- 右侧面板：日志 + 控制 ----------
        right_panel = tk.Frame(main_frame, bg=COLORS["bg"])
        right_panel.pack(side="right", fill="both", expand=True)

        # 日志区域
        self.log_frame = tk.Frame(right_panel, bg=COLORS["log_bg"],
                                  highlightthickness=1,
                                  highlightbackground=COLORS["border"])
        self.log_frame.pack(fill="both", expand=True)

        self.log_area = tk.Text(
            self.log_frame,
            bg=COLORS["log_bg"],
            fg=COLORS["log_fg"],
            insertbackground=COLORS["primary"],
            font=("Consolas", 9),
            state="disabled",
            wrap="word")
        self.log_area.pack(side="left", fill="both", expand=True,
                           padx=4, pady=4)

        # 日志滚动条（初始隐藏）
        self.log_scroll = ttk.Scrollbar(self.log_frame,
                                       orient="vertical",
                                       command=self._on_log_scroll)
        self.log_area.configure(yscrollcommand=self.log_scroll.set)
        self.log_scroll_visible = False

    def _load_accounts(self):
        """刷新账号列表 UI"""
        # 销毁旧控件
        for w in self.account_frame.winfo_children():
            w.destroy()

        self.account_vars = {}
        self.account_widgets = {}

        accounts = self.cfg.get("accounts", [])
        if not accounts:
            empty = tk.Label(self.account_frame,
                             text="暂无账号\n点击「+ 添加账号」开始",
                             bg=COLORS["panel_bg"],
                             fg=COLORS["text_light"],
                             font=("Microsoft YaHei", 10))
            empty.pack(pady=20)
            return

        last_sel = self.cfg.get("last_selected", [])

        # 列表顶部分隔线
        tk.Frame(self.account_frame, bg=COLORS["border"], height=1).pack(
            fill="x", padx=8)

        for idx, acc in enumerate(accounts):
            name = acc.get("name", "")
            var = tk.BooleanVar(value=(name in last_sel))

            def _on_var_changed(*_args, _name=name):
                self._save_selection()

            var.trace_add("write", _on_var_changed)
            self.account_vars[name] = var

            row = tk.Frame(self.account_frame, bg=COLORS["panel_bg"])
            row.pack(fill="x", pady=1)

            cb = tk.Checkbutton(row, text="", variable=var)
            cb.pack(side="left", padx=(3, 0))

            info_frame = tk.Frame(row, bg=COLORS["panel_bg"])
            info_frame.pack(side="left", fill="x", padx=3, pady=3)

            acc_type = acc.get("type", "direct")
            if acc_type == "direct":
                type_label = "直接启动"
                type_color = COLORS["primary"]
            elif acc_type == "tg_cdp":
                type_label = "TeyvatGuide"
                type_color = COLORS["success"]
            else:
                type_label = "胡桃启动"
                type_color = COLORS["warning"]

            name_lbl = tk.Label(info_frame, text=name,
                                bg=COLORS["panel_bg"],
                                fg=COLORS["text"],
                                font=("Microsoft YaHei", 10, "bold"))
            name_lbl.pack(anchor="w")

            detail = (f"{type_label}  |  配置: "
                      f"{acc.get('config_name', '')}")
            if acc_type == "hutao":
                detail += (f"  |  米游社: "
                           f"{acc.get('hutao_account', '')}")
            elif acc_type == "tg_cdp":
                detail += f"  |  UID: {acc.get('uid', '')}"

            detail_lbl = tk.Label(info_frame, text=detail,
                                  bg=COLORS["panel_bg"],
                                  fg=COLORS["text_light"],
                                  font=("Microsoft YaHei", 8))
            detail_lbl.pack(anchor="w")

            # 拖拽排序：所有子控件绑定点击开始拖拽（移动和释放由 bind_all 全局捕获）
            for w in (row, cb, info_frame, name_lbl, detail_lbl):
                w.bind("<Button-1>", lambda e, i=idx: self._drag_start(e, i))

            # 行内编辑按钮
            edit_row_btn = tk.Button(row, text="编辑", command=lambda a=acc: self._edit_account_by_acc(a),
                                     bg="#EEF2F7", fg=COLORS["text"], relief="flat",
                                     font=("Microsoft YaHei", 8), padx=6,
                                     activebackground="#D0D8E4", cursor="hand2", bd=0)
            edit_row_btn.pack(side="right", padx=(0, 4), pady=2)

            self.account_widgets[name] = row

            # 行间分隔线
            sep = tk.Frame(self.account_frame, bg=COLORS["border"], height=1)
            sep.pack(fill="x", padx=8, pady=(2, 0))

        # 拖拽指示线（初始隐藏）
        self._drag_line = tk.Frame(self.account_frame, bg=COLORS["primary"],
                                    height=2)
        self._drag_data = {"source_idx": -1, "dragging": False}

    def _get_selected(self):
        return [n for n, v in self.account_vars.items() if v.get()]

    def _save_selection(self):
        sel = self._get_selected()
        self.cfg["last_selected"] = sel
        save_config(self.cfg)

    def _add_account(self):
        dialog = AddAccountDialog(self.root)
        self.root.wait_window(dialog)
        if dialog.result is not None:
            self.cfg.setdefault("accounts", []).append(
                dialog.result)
            save_config(self.cfg)
            self._load_accounts()
            self._log(f"[+] 已添加账号: {dialog.result['name']}")

    def _edit_account(self):
        sel = self._get_selected()
        if len(sel) > 1:
            messagebox.showinfo("提示", "请先选择一个账号再编辑")
            return

        name = sel[0] if sel else None
        if name is None:
            return

        acc = next((a for a in self.cfg.get("accounts", [])
                    if a.get("name") == name), None)
        if acc is None:
            return

        dialog = AddAccountDialog(self.root, edit_account=acc)
        self.root.wait_window(dialog)
        if dialog.result is not None:
            idx = next(i for i, a in enumerate(self.cfg["accounts"])
                       if a["name"] == name)
            self.cfg["accounts"][idx] = dialog.result
            save_config(self.cfg)
            self._load_accounts()
            self._log(f"[*] 已更新: {dialog.result['name']}")

    def _edit_account_by_acc(self, acc):
        """行内编辑按钮：直接传入 account dict，无需先勾选"""
        dialog = AddAccountDialog(self.root, edit_account=acc)
        self.root.wait_window(dialog)
        if dialog.result is not None:
            idx = next(i for i, a in enumerate(self.cfg["accounts"])
                       if a["name"] == acc["name"])
            self.cfg["accounts"][idx] = dialog.result
            save_config(self.cfg)
            self._load_accounts()
            self._log(f"[*] 已更新: {dialog.result['name']}")

    def _delete_account(self):
        sel = self._get_selected()
        if not sel:
            messagebox.showinfo("提示", "请先选择要删除的账号")
            return

        names = ", ".join(sel)
        if not messagebox.askyesno("确认删除",
                                   f"确定删除以下账号？\n{names}"):
            return

        self.cfg["accounts"] = [
            a for a in self.cfg["accounts"]
            if a["name"] not in sel
        ]
        save_config(self.cfg)
        self._load_accounts()
        self._log(f"[-] 已删除: {names}")

    def _drag_start(self, event, source_idx):
        """拖拽开始：记录来源索引，绑定全局移动/释放事件"""
        self._drag_data["source_idx"] = source_idx
        self._drag_data["dragging"] = False
        self._drag_data["start_y"] = event.y_root
        self.root.bind_all("<B1-Motion>", self._drag_motion)
        self.root.bind_all("<ButtonRelease-1>", self._drag_end)

    def _drag_motion(self, event):
        """拖拽移动：显示插入指示线"""
        dy = event.y_root - self._drag_data.get("start_y", 0)
        if not self._drag_data.get("dragging"):
            if abs(dy) < 5:
                return
            self._drag_data["dragging"] = True

        # 计算鼠标在 account_frame 内的 y 坐标
        y_in_frame = event.y_root - self.account_frame.winfo_rooty()
        # 推算目标插入位置（遍历每行判断鼠标在哪两行之间）
        accounts = self.cfg.get("accounts", [])
        target = self._drag_data["source_idx"]
        cumulative_y = 0
        for i, name in enumerate(self.account_widgets):
            w = self.account_widgets[name]
            h = w.winfo_height()
            mid_y = cumulative_y + h / 2
            if y_in_frame < mid_y:
                target = i
                break
            cumulative_y += h
        else:
            target = len(accounts)

        if target != self._drag_data.get("target_idx"):
            self._drag_data["target_idx"] = target
            self._show_drag_line(target)

    def _show_drag_line(self, target_idx):
        """在目标位置显示蓝色指示线"""
        accounts = self.cfg.get("accounts", [])
        if target_idx >= len(accounts):
            # 插入到末尾：对齐最后一个 widget 底部
            last_name = accounts[-1]["name"]
            last_w = self.account_widgets.get(last_name)
            if last_w:
                y = (last_w.winfo_y() + last_w.winfo_height()
                     - self.account_frame.winfo_y())
        else:
            name = accounts[target_idx]["name"]
            w = self.account_widgets.get(name)
            if w:
                y = w.winfo_y() - self.account_frame.winfo_y()
            else:
                y = 0
        self._drag_line.place_forget()
        self._drag_line.place(x=0, y=y, relwidth=1.0)
        self._drag_line.lift()

    def _drag_end(self, event):
        """拖拽结束：解绑全局事件，执行排序并刷新"""
        self.root.unbind_all("<B1-Motion>")
        self.root.unbind_all("<ButtonRelease-1>")
        self._drag_line.place_forget()
        source = self._drag_data.get("source_idx", -1)
        target = self._drag_data.get("target_idx", -1)
        self._drag_data["source_idx"] = -1
        self._drag_data["dragging"] = False
        self._drag_data.pop("target_idx", None)

        if source < 0 or target < 0 or source == target:
            return

        accounts = self.cfg.get("accounts", [])
        if source >= len(accounts):
            return
        acc = accounts.pop(source)
        # 如果 target > source，pop 后索引自动前移，target 需要减 1
        if target > source:
            target -= 1
        accounts.insert(target, acc)
        save_config(self.cfg)
        self._load_accounts()

    def _select_all(self):
        for v in self.account_vars.values():
            v.set(True)

    def _deselect_all(self):
        for v in self.account_vars.values():
            v.set(False)

    def _open_settings(self):
        dlg = SettingsDialog(self.root, self.cfg)
        self.root.wait_window(dlg)
        if dlg.result is not None:
            self.cfg = load_config()
            self._register_hotkey()
            self._log("设置已保存")

    def _open_scheduler(self):
        """打开定时计划对话框"""
        self.scheduler_dialog = SchedulerDialog(self.root, self)

    def _do_checkin(self):
        """一键签到：在后台线程中执行签到流程"""
        if not HAS_UIA:
            messagebox.showinfo("提示",
                "签到功能需要 uiautomation 库，请先安装：\npip install uiautomation")
            return

        # 若已有签到线程在运行，先停止旧的
        if self.checkin_stop_event:
            self.checkin_stop_event.set()
            self._log("[签到] 中断之前的签到线程")
        self.checkin_stop_event = threading.Event()

        cfg = load_config()
        def _checkin_wrapper(target_func, *args):
            self.root.after(0, self._minimize_to_tray)
            try:
                target_func(*args)
            finally:
                self.checkin_stop_event = None
                self.root.after(0, lambda: (self.root.deiconify(), self.root.lift(), self.root.focus_force()))
                self.root.after(0, lambda: self.pause_btn.config(state="disabled", bg="#E0E4E8",
                                      fg=COLORS["text_light"]))
                self.root.after(0, lambda: self.stop_btn.config(state="disabled", bg="#E8E8E8",
                                     fg=COLORS["text"]))
                if load_config().get("settings", {}).get("checkin_close_app", False):
                    self._log("[签到] checkin_close_app=True, 正在关闭签到软件...")
                    ck_method = load_config().get("checkin_method", "teyvatguide")
                    if ck_method == "hutao":
                        kill_proc("Snap.Hutao.Remastered.exe")
                        kill_proc("Snap.Hutao.Remastered.FullTrust.exe")
                    else:
                        kill_proc("TeyvatGuide.exe")

        if cfg.get("checkin_method", "teyvatguide") == "hutao":
            hutao_names = cfg.get("checkin_hutao_accounts", [])
            if hutao_names:
                self.checkin_thread = threading.Thread(
                    target=_checkin_wrapper,
                    args=(_run_hutao_checkin, self._log, self.checkin_stop_event, hutao_names, self.pause_event),
                    daemon=True)
            else:
                self._log("没有勾选任何米游社账号，无法执行签到")
                return
        else:
            self.checkin_thread = threading.Thread(
                target=_checkin_wrapper,
                args=(_run_checkin, self._log, self.checkin_stop_event, self.pause_event),
                daemon=True)
        self.pause_btn.config(state="normal", bg="#F0AD4E",
                              fg=COLORS["text_white"],
                              activebackground="#EC971F")
        self.stop_btn.config(state="normal", bg=COLORS["danger"],
                             fg=COLORS["text_white"],
                             activebackground="#C0392B")
        self.checkin_thread.start()

    def _open_checkin_schedule(self):
        """打开定时签到设置对话框"""
        self.checkin_dialog = CheckinScheduleDialog(self.root, self)

    def _auto_start_scheduler(self):
        """开机自启动：静默启动定时器（不弹警告）"""
        cfg = load_scheduler_config()
        if not cfg.get("schedules"):
            return  # 没有任务，不启动
        if not self.scheduler_event.is_set():
            return  # 调度器已在运行
        self.scheduler_event.clear()
        self.scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self.scheduler_thread.start()
        # 开机自启 → 最小化到托盘
        self._minimize_to_tray()


    def _scheduler_loop(self):
        """定时器后台线程：精确触发 + 补偿 + 30秒轮询兜底

        计算距离下一次定时触发还有多少秒，精确等待到点触发。
        每次循环记录 last_check 时间，若检测到时间跳跃 > 90秒
        （系统休眠/挂起恢复），扫描所有调度补偿执行错过的触发。
        保留 30 秒轮询作为兜底。
        """
        last_triggered = {}  # key=schedule id, value=最后触发的日期字符串
        fallback_interval = 30  # 兜底轮询间隔
        last_check = datetime.now()

        while not self.scheduler_event.is_set():
            cfg = load_scheduler_config()
            now = datetime.now()

            # ---- 检测时间跳跃（系统休眠恢复），补偿错过触发 ----
            gap = (now - last_check).total_seconds()
            if gap > 90:
                self._compensate_missed(last_check, now, cfg, last_triggered)

            # 找到下一次触发时间（所有调度中最早的）
            next_trigger = self._find_next_trigger(now, cfg, last_triggered)

            if next_trigger is not None:
                wait_seconds = (next_trigger - datetime.now()).total_seconds()
                if wait_seconds > 0:
                    actual_wait = min(wait_seconds, fallback_interval)
                    if self.scheduler_event.wait(actual_wait):
                        break  # shutdown 信号
                    now = datetime.now()

                # 检查是否到达/错过了触发时间
                if now >= next_trigger:
                    self._check_and_trigger(now, cfg, last_triggered)
            else:
                # 没有可触发的调度，兜底等待
                if self.scheduler_event.wait(fallback_interval):
                    break

            last_check = datetime.now()

    def _checkin_scheduler_loop(self):
        """定时签到后台线程：精确触发 + 30秒轮询兜底

        独立于定时器调度器，仅负责触发签到（_run_checkin）。
        每次循环读 load_checkin_schedule()，若总开关 enabled 为 False
        则短暂等待后继续；否则遍历启用的签到条目，找到最近触发时间精确等待。
        检测到时间跳跃 > 90秒（系统休眠恢复）时做补偿。
        """
        last_check = time.time()
        while not self.checkin_scheduler_event.is_set():
            try:
                cfg = load_checkin_schedule()
                if not cfg.get("enabled", False):
                    self.checkin_scheduler_event.wait(1.0)
                    continue

                checkins = [c for c in cfg.get("checkins", []) if c.get("enabled", True)]
                if not checkins:
                    self.checkin_scheduler_event.wait(5.0)
                    continue

                now = time.time()
                gap = now - last_check
                if gap > 90:
                    last_check = now  # 时间跳跃，补偿

                # 找最近的下一个触发时间
                _now = datetime.now()
                next_trigger = None
                for c in checkins:
                    t = self._schedule_next_time(_now, c, 0, {})
                    if t is None:
                        continue
                    if next_trigger is None or t < next_trigger:
                        next_trigger = t

                if next_trigger is None:
                    self.checkin_scheduler_event.wait(30.0)
                    continue

                wait_seconds = max(0, (next_trigger - datetime.now()).total_seconds())
                if wait_seconds > 0:
                    self.checkin_scheduler_event.wait(min(wait_seconds, 30.0))
                    if self.checkin_scheduler_event.is_set():
                        break
                    continue

                # 触发签到 - 找出当前时间已到的所有条目
                _now2 = datetime.now()
                triggered_entries = []
                for c in checkins:
                    t = self._schedule_next_time(_now2, c, 0, {})
                    if t is not None and t <= _now2:
                        triggered_entries.append(c)

                if not triggered_entries:
                    # 兜底：用 next_trigger 最近的条目
                    for c in checkins:
                        t = self._schedule_next_time(_now2, c, 0, {})
                        if t is not None and (next_trigger is None or t <= next_trigger):
                            triggered_entries = [c]
                            break

                if not triggered_entries:
                    last_check = time.time()
                    self.checkin_scheduler_event.wait(2.0)
                    continue

                # 获取互斥锁，若有定时计划任务正在执行则等待
                self.task_exec_lock.acquire()

                checkin_threads = []
                for entry in triggered_entries:
                    checkin_type = entry.get("checkin_type", "teyvatguide")
                    if checkin_type == "hutao":
                        self._log(f"[定时签到] 触发胡桃签到")
                        t = threading.Thread(
                            target=_run_hutao_checkin,
                            args=(self._log, self.checkin_scheduler_event, entry.get("hutao_accounts", [])),
                            daemon=True
                        )
                    else:
                        self._log(f"[定时签到] 触发 TeyvatGuide 签到")
                        t = threading.Thread(
                            target=_run_checkin,
                            args=(self._log, self.checkin_scheduler_event),
                            daemon=True
                        )
                    t.start()
                    checkin_threads.append(t)

                for t in checkin_threads:
                    t.join(timeout=300)

                self.root.after(0, lambda: self.pause_btn.config(state="disabled", bg="#E0E4E8",
                                                                        fg=COLORS["text_light"]))
                self.root.after(0, lambda: self.stop_btn.config(state="disabled", bg="#E8E8E8",
                                                                 fg=COLORS["text"]))

                if load_config().get("settings", {}).get("checkin_close_app", False):
                    ck_method = load_config().get("checkin_method", "teyvatguide")
                    target = "胡桃" if ck_method == "hutao" else "TeyvatGuide"
                    self._log(f"[定时签到] 签到完成，自动关闭{target}")
                    if ck_method == "hutao":
                        kill_proc("Snap.Hutao.Remastered.exe")
                        kill_proc("Snap.Hutao.Remastered.FullTrust.exe")
                    else:
                        kill_proc("TeyvatGuide.exe")
                    self.task_exec_lock.release()
                    return

                self.task_exec_lock.release()

                last_check = time.time()
                self.checkin_scheduler_event.wait(2.0)  # 短暂休息避免重复触发
            except Exception:
                self.checkin_scheduler_event.wait(5.0)

    def start_checkin_scheduler(self):
        """启动独立签到调度器，遵循 Event 安全协议"""
        # 先停止旧线程
        if not self.checkin_scheduler_event.is_set():
            self.checkin_scheduler_event.set()
            if self.checkin_scheduler_thread and self.checkin_scheduler_thread.is_alive():
                self.checkin_scheduler_thread.join(timeout=2)
        self.checkin_scheduler_event.clear()
        self.checkin_scheduler_enabled = True
        self.checkin_scheduler_thread = threading.Thread(
            target=self._checkin_scheduler_loop, daemon=True
        )
        self.checkin_scheduler_thread.start()
        cfg = load_checkin_schedule()
        cfg["enabled"] = True
        save_checkin_schedule(cfg)

    def stop_checkin_scheduler(self):
        """停止独立签到调度器"""
        self.checkin_scheduler_event.set()
        self.checkin_scheduler_enabled = False
        cfg = load_checkin_schedule()
        cfg["enabled"] = False
        save_checkin_schedule(cfg)
        self._log("[定时签到] 调度器已停止")

    def _toggle_checkin_scheduler(self, enable):
        """托盘菜单回调：切换签到总开关"""
        if enable:
            self.start_checkin_scheduler()
        else:
            self.stop_checkin_scheduler()
        self._pending_rebuild = True
        self.root.after(0, self._refresh_scheduler_ui)

    def _toggle_checkin_entry(self, entry):
        """托盘菜单回调：切换单个签到条目的启用状态"""
        def toggle(icon, item):
            cfg = load_checkin_schedule()
            for c in cfg["checkins"]:
                if c.get("id") == entry.get("id"):
                    c["enabled"] = not c.get("enabled", True)
                    break
            save_checkin_schedule(cfg)
            # 根据是否还有启用的条目自动启停签到调度器
            if any(c.get("enabled", True) for c in cfg["checkins"]):
                if self.checkin_scheduler_event.is_set():
                    self.start_checkin_scheduler()
            else:
                self.stop_checkin_scheduler()
            self._pending_rebuild = True
            self.root.after(0, self._refresh_scheduler_ui)
        return toggle

    def _find_next_trigger(self, now, cfg, last_triggered):
        """计算所有调度中下一次触发时间（最早的），返回 datetime 或 None"""
        next_t = None
        for i, s in enumerate(cfg["schedules"]):
            if not s.get("enabled", True):
                continue
            t = self._schedule_next_time(now, s, i, last_triggered)
            if t and (next_t is None or t < next_t):
                next_t = t
        return next_t

    @staticmethod
    def _schedule_next_time(now, s, idx, last_triggered):
        """计算单个调度的下一次触发时间，返回 datetime 或 None"""
        stype = s.get("schedule_type", "daily")
        try:
            h, m = map(int, s["time"].split(":"))
        except (ValueError, AttributeError):
            return None
        trigger_today = now.replace(hour=h, minute=m, second=0, microsecond=0)
        sched_id = s.get("id", str(idx))

        if stype == "daily":
            if trigger_today <= now:
                trigger_today += timedelta(days=1)
            return trigger_today

        if stype == "weekly":
            wds = s.get("weekdays", [])
            if not wds:
                return None
            for days_ahead in range(8):
                check = now + timedelta(days=days_ahead)
                check_trigger = check.replace(hour=h, minute=m, second=0, microsecond=0)
                if check.isoweekday() in wds and check_trigger > now:
                    return check_trigger
            return None

        if stype == "once":
            sched_date = s.get("date", "")
            if sched_date:
                try:
                    dt = datetime.strptime(f"{sched_date} {s['time']}", "%Y-%m-%d %H:%M")
                    if dt > now:
                        return dt
                except ValueError:
                    pass
            return None

        if stype == "dates":
            dates = s.get("dates", [])
            for d in sorted(dates):
                try:
                    dt = datetime.strptime(f"{d} {s['time']}", "%Y-%m-%d %H:%M")
                    if dt > now:
                        return dt
                except ValueError:
                    continue
            return None

        return None

    def _check_and_trigger(self, now, cfg, last_triggered):
        """检查当前时间是否匹配任何调度，匹配则触发（补偿错过的情况）"""
        current_time = now.strftime("%H:%M")
        current_date = now.strftime("%Y-%m-%d")
        current_weekday = now.isoweekday()

        for i, schedule in enumerate(cfg["schedules"]):
            if not schedule.get("enabled", True):
                continue
            if schedule["time"] != current_time:
                continue

            stype = schedule.get("schedule_type", "daily")

            # ---- 一次性模式 ----
            if stype == "once":
                sched_date = schedule.get("date", "")
                if sched_date != current_date:
                    continue
                if schedule.get("last_run") == current_date:
                    continue
                schedule["last_run"] = current_date
                save_scheduler_config(cfg)

            # ---- 每周模式 ----
            elif stype == "weekly":
                wds = schedule.get("weekdays", [])
                if current_weekday not in wds:
                    continue
                if last_triggered.get(schedule.get("id", str(i))) == current_date:
                    continue

            # ---- 指定日期模式 ----
            elif stype == "dates":
                sched_dates = schedule.get("dates", [])
                if current_date not in sched_dates:
                    continue
                if last_triggered.get(schedule.get("id", str(i))) == current_date:
                    continue

            # ---- 每天模式 ----
            else:  # daily
                if last_triggered.get(schedule.get("id", str(i))) == current_date:
                    continue

            last_triggered[schedule.get("id", str(i))] = current_date

            accts = schedule.get("accounts", [])
            sched_groups = schedule.get("scheduler_groups", "")
            hutao_checkin = schedule.get("hutao_checkin", False)
            hutao_accounts = schedule.get("hutao_accounts", [])

            def trigger(accts=accts, grp=sched_groups):
                self._log(f"[定时器] 到点 {current_time}，自动执行: {', '.join(accts)}")

                if self.running:
                    self._log("[定时器] 当前正在执行中，跳过")
                    return

                for name, var in self.account_vars.items():
                    var.set(name in accts)

                if grp:
                    for acc in self.cfg.get("accounts", []):
                        if acc["name"] in accts:
                            acc["scheduler_groups"] = grp
                    save_config(self.cfg)

                # 获取互斥锁，若有签到任务正在执行则等待
                self.task_exec_lock.acquire()
                self._start()

                # 胡桃签到（若勾选）—— 在任务启动后并行执行
                if hutao_checkin and hutao_accounts:
                    self._log(f"[定时器] 附加胡桃签到: {', '.join(hutao_accounts)}")
                    threading.Thread(
                        target=_run_hutao_checkin,
                        args=(self._log, None, hutao_accounts),
                        daemon=True
                    ).start()

            self.root.after(0, trigger)

    def _compensate_missed(self, last_check, now, cfg, last_triggered):
        """扫描所有调度，补偿因系统休眠而错过的触发。

        对每个启用的 daily / weekly / once / dates 调度：
        计算其今天的触发时间，若落入 [last_check, now] 区间且尚未
        触发过，则立即补偿执行。
        """
        current_date = now.strftime("%Y-%m-%d")
        current_weekday = now.isoweekday()

        for i, schedule in enumerate(cfg["schedules"]):
            if not schedule.get("enabled", True):
                continue

            sched_id = schedule.get("id", str(i))
            stype = schedule.get("schedule_type", "daily")

            try:
                h, m = map(int, schedule["time"].split(":"))
            except (ValueError, AttributeError):
                continue

            trigger_today = now.replace(hour=h, minute=m, second=0, microsecond=0)

            # 检查触发时间是否落入休眠窗口
            if not (last_check <= trigger_today <= now):
                continue

            # ---- 去重检查 ----
            if stype == "daily":
                if last_triggered.get(sched_id) == current_date:
                    continue

            elif stype == "weekly":
                if current_weekday not in schedule.get("weekdays", []):
                    continue
                if last_triggered.get(sched_id) == current_date:
                    continue

            elif stype == "once":
                if schedule.get("date", "") != current_date:
                    continue
                if last_triggered.get(sched_id) == current_date:
                    continue

            elif stype == "dates":
                if current_date not in schedule.get("dates", []):
                    continue
                if last_triggered.get(sched_id) == current_date:
                    continue
            else:
                continue

            last_triggered[sched_id] = current_date

            accts = schedule.get("accounts", [])
            sched_groups = schedule.get("scheduler_groups", "")
            trigger_time = schedule["time"]
            hutao_checkin = schedule.get("hutao_checkin", False)
            hutao_accounts = schedule.get("hutao_accounts", [])

            def compensate_trigger(accts=accts, grp=sched_groups, t=trigger_time):
                self._log(f"[定时器] 休眠补偿触发 {t}，自动执行: {', '.join(accts)}")

                if self.running:
                    self._log("[定时器] 当前正在执行中，跳过")
                    return

                for name, var in self.account_vars.items():
                    var.set(name in accts)

                if grp:
                    for acc in self.cfg.get("accounts", []):
                        if acc["name"] in accts:
                            acc["scheduler_groups"] = grp
                    save_config(self.cfg)

                self._start()

                if hutao_checkin and hutao_accounts:
                    self._log(f"[定时器] 休眠补偿附加胡桃签到: {', '.join(hutao_accounts)}")
                    threading.Thread(
                        target=_run_hutao_checkin,
                        args=(self._log, None, hutao_accounts),
                        daemon=True
                    ).start()

            self.root.after(0, compensate_trigger)

    def _start(self):
        sel = self._get_selected()
        if not sel:
            messagebox.showwarning("提示", "请至少选择一个账号")
            return

        # 检查 BetterGI 路径
        gi_exe = self.cfg.get("bettergi", {}).get("exe", "")
        if not gi_exe or not os.path.isfile(gi_exe):
            messagebox.showwarning("提示", "请先在设置中配置 BetterGI 可执行文件路径")
            return

        # 检查依赖
        if not HAS_GW or not HAS_PA:
            r = messagebox.askyesno("依赖提示",
                                    "pygetwindow/pyautogui 未安装，"
                                    "胡桃功能将不可用。\n继续？")
            if not r:
                return

        self.running = True
        self.root.after(200, self._rebuild_tray_menu)
        self.paused = False
        self.stop_event.clear()
        self.pause_event.set()  # 确保开始时不暂停
        self.start_btn.config(state="disabled", bg="#A0C4E0")
        self.pause_btn.config(state="normal", bg="#F0AD4E",
                              fg=COLORS["text_white"],
                              activebackground="#EC971F")
        self.stop_btn.config(state="normal", bg=COLORS["danger"],
                             fg=COLORS["text_white"],
                             activebackground="#C0392B")

        total = len(sel)
        self.progress["maximum"] = total
        self.progress["value"] = 0
        self.progress_label.config(text=f"0/{total}")
        self.status.config(text=f"执行 {', '.join(sel)}...")

        self._clear_log()
        self._log("=" * 55)
        self._log(f"GenshinMultiAccountTool v5.5  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self._log(f"选中: {', '.join(sel)}")
        self._log("=" * 55)

        selected_accounts = [a for a in self.cfg.get("accounts", [])
                             if a["name"] in sel]
        self.worker = WorkerThread(selected_accounts, self.log_queue, self.stop_event, self.pause_event,
                                    exec_lock=self.task_exec_lock)
        self.worker.start()

        # 自动最小化窗口，避免挡住游戏画面
        if self.cfg.get("settings", {}).get("auto_minimize", True):
            self.root.iconify()

    def _stop(self):
        if not self.running and not self.checkin_stop_event:
            return
        self._log("正在停止...")
        self.stop_event.set()
        self.pause_event.set()  # 取消暂停以便线程能退出
        # 同时中断签到线程
        if self.checkin_stop_event:
            self.checkin_stop_event.set()
        self.paused = False
        self.status.config(text="正在停止...")
        self.stop_btn.config(state="disabled")
        self.pause_btn.config(state="disabled")
        self.root.deiconify()  # 恢复窗口
        self.root.lift()
        self.root.focus_force()

        # 需求3：停止时异步停止 BetterGI 进程（避免阻塞 UI）
        if find_proc("BetterGI.exe"):
            self._log("停止 BetterGI 进程...")
            threading.Thread(
                target=lambda: kill_proc("BetterGI.exe", graceful=False),
                daemon=True
            ).start()

    def _pause_toggle(self):
        """暂停/继续切换"""
        if not self.running and not self.checkin_stop_event:
            return
        if self.paused:
            self._resume()
        else:
            self._pause()

    def _pause(self):
        self.paused = True
        self.pause_event.clear()  # 阻塞工作线程
        self._log("任务已暂停")
        self.status.config(text="已暂停")
        self.pause_btn.config(text="▶ 继续", bg="#52C41A",
                              activebackground="#389E0D")

        # 需求2：暂停时同步暂停 BetterGI（发送 F11 热键）
        self._toggle_bettergi_pause()

    def _resume(self):
        self.paused = False
        self.pause_event.set()  # 恢复工作线程
        self._log("任务继续")
        self.status.config(text="运行中...")
        self.pause_btn.config(text="⏸ 暂停", bg="#F0AD4E",
                              activebackground="#EC971F")

        # 需求2：恢复时同步恢复 BetterGI（发送相同热键）
        self._toggle_bettergi_pause()

    def _toggle_bettergi_pause(self):
        """向 BetterGI 窗口发送暂停/恢复热键（F11）。
        通过激活 BetterGI 窗口后发送按键，然后尝试恢复焦点到原神。"""
        if not HAS_GW or not HAS_PA:
            return
        try:
            windows = gw.getWindowsWithTitle("BetterGI")
            if not windows:
                return
            bg_window = windows[0]
            # 尝试恢复 BetterGI 窗口（可能已最小化）
            try:
                bg_window.restore()
            except Exception:
                pass
            bg_window.activate()
            time.sleep(0.15)
            pyautogui.press("f11")
            self._log("已同步 BetterGI 暂停/恢复状态")
            time.sleep(0.1)
            # 尝试恢复焦点到原神
            genshin_wins = (gw.getWindowsWithTitle("原神") or
                            gw.getWindowsWithTitle("Genshin Impact"))
            if genshin_wins:
                try:
                    genshin_wins[0].activate()
                except Exception:
                    pass
        except Exception as e:
            self._log(f"[!] BetterGI 暂停同步失败: {e}")

    # ------------------------------------------------------------------
    # 日志 & 轮询
    # ------------------------------------------------------------------

    def _poll_log(self):
        log_lines = []
        for _ in range(50):
            try:
                msg = self.log_queue.get_nowait()
            except queue.Empty:
                break
            if msg == "__DONE__":
                self._on_done()
            elif msg.startswith("__PROGRESS__"):
                val = int(msg.split("__")[2])
                self.progress["value"] = val
                self.progress_label.config(
                    text=f"{val}/{self.progress['maximum']}")
            elif msg.startswith("__STATUS__"):
                self.status.config(text=msg.split("__", 2)[2])
            else:
                ts = datetime.now().strftime("%H:%M:%S")
                log_lines.append(f"[{ts}] {msg}\n")
        if log_lines:
            self._log_batch("".join(log_lines))
        self._log_poll_interval = 100 if log_lines else 500
        self.root.after(self._log_poll_interval, self._poll_log)

    def _log(self, msg):
        """单行日志（外部直接调用时使用，如 _clear_log 等非队列场景）"""
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_batch(f"[{ts}] {msg}\n")

    def _log_batch(self, text):
        """批量写入日志文本，仅触发一次滚动条更新"""
        self.log_area.config(state="normal")
        self.log_area.insert("end", text)
        # 超过 2000 行则截断前一半，防 UI 膨胀
        total_lines = int(self.log_area.index("end-1c").split(".")[0])
        if total_lines > 2000:
            self.log_area.delete("1.0", f"{total_lines // 2}.0")
        self.log_area.see("end")
        self.log_area.config(state="disabled")
        self._update_log_scrollbar()

    def _clear_log(self):
        self.log_area.config(state="normal")
        self.log_area.delete("1.0", "end")
        self.log_area.config(state="disabled")
        self._update_log_scrollbar()

    def _toggle_title_topmost(self):
        self._topmost_active = not self._topmost_active
        self.root.attributes("-topmost", self._topmost_active)
        self.topmost_btn.configure(
            bg="#4A90D9" if self._topmost_active else COLORS["primary_dark"])

    def _on_log_scroll(self, *args):
        """Scrollbar 回调，同时更新可见性"""
        self.log_area.yview(*args)
        self._update_log_scrollbar()

    def _update_log_scrollbar(self):
        """根据内容高度决定是否显示滚动条"""
        self.log_area.update_idletasks()
        bbox = self.log_area.bbox("end-1c")
        if bbox is None:
            return
        text_h = bbox[1] + bbox[3]
        visible_h = self.log_area.winfo_height()
        if visible_h <= 0:
            return
        if text_h > visible_h and not self.log_scroll_visible:
            self.log_scroll.pack(side="right", fill="y", padx=(0, 1), pady=1)
            self.log_scroll_visible = True
        elif text_h <= visible_h and self.log_scroll_visible:
            self.log_scroll.pack_forget()
            self.log_scroll_visible = False

    def _update_account_scrollbar(self):
        """根据账号列表内容高度决定是否显示滚动条，并同步内部frame宽度"""
        self.account_canvas.configure(scrollregion=self.account_canvas.bbox("all"))
        self.account_canvas.update_idletasks()
        
        # 同步内部 frame 宽度
        canvas_w = self.account_canvas.winfo_width()
        if canvas_w > 1:
            self.account_canvas.itemconfig("account_inner", width=canvas_w)
        
        bbox = self.account_canvas.bbox("all")
        if bbox is None:
            return
        content_h = bbox[3]
        visible_h = self.account_canvas.winfo_height()
        if visible_h <= 0:
            return
        if content_h > visible_h and not self.account_scroll_visible:
            self.account_scroll.pack(side="right", fill="y")
            self.account_scroll_visible = True
        elif content_h <= visible_h and self.account_scroll_visible:
            self.account_scroll.pack_forget()
            self.account_scroll_visible = False

    def _on_done(self):
        self.running = False
        self.root.after(200, self._rebuild_tray_menu)
        self.paused = False
        self.root.deiconify()  # 恢复窗口
        self.root.lift()
        self.root.focus_force()
        self.start_btn.config(state="normal", bg=COLORS["primary"])
        self.pause_btn.config(state="disabled", bg="#E0E4E8",
                              fg=COLORS["text_light"])
        self.stop_btn.config(state="disabled", bg="#E8E8E8",
                             fg=COLORS["text"])
        self.status.config(text="就绪 | 任务已完成或被中断")
        self._log(f"结束: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # 任务完成后启动软件
        settings = self.cfg.get("settings", {})
        launch_apps_enabled = settings.get("launch_apps_enabled", False)
        launch_apps = settings.get("launch_apps_after_all", [])
        if launch_apps_enabled and launch_apps and not self.stop_event.is_set():
            self._log("启动任务完成后软件...")
            for app_path in launch_apps:
                app_path = app_path.strip()
                if app_path and os.path.isfile(app_path):
                    try:
                        subprocess.Popen([app_path], shell=True)
                        self._log(f"  已启动: {os.path.basename(app_path)}")
                    except Exception as e:
                        self._log(f"  [!] 启动失败: {os.path.basename(app_path)} - {e}")
                else:
                    self._log(f"  [!] 文件不存在: {app_path}")

        # 自动关机检查
        auto_shutdown = self.cfg.get("settings", {}).get("auto_shutdown", False)
        if auto_shutdown and not self.stop_event.is_set():
            self._log("任务全部完成，1分钟后自动关机...")
            self._log("点击「取消关机」按钮或运行 shutdown /a 可取消")
            try:
                subprocess.run(
                    ["shutdown", "/s", "/t", "60",
                     "/c", "原神一条龙全部完成，电脑将在1分钟后关机"],
                    shell=True, check=False)
                # 显示取消关机按钮
                self.cancel_shutdown_btn.pack(side="left", padx=(6, 0))
                self._shutdown_pending = True
                self.status.config(text="关机倒计时 60 秒...")
            except Exception as e:
                self._log(f"[!] 关机命令失败: {e}")

    def _cancel_shutdown(self):
        """取消自动关机"""
        try:
            subprocess.run(["shutdown", "/a"], shell=True, check=False)
        except Exception:
            pass
        self.cancel_shutdown_btn.pack_forget()
        self._shutdown_pending = False
        self._log("自动关机已取消")
        self.status.config(text="就绪 | 任务已完成")

    def _register_hotkey(self):
        """使用 Win32 RegisterHotKey(NULL) 在后台线程注册全局热键（窗口最小化/后台/托盘均有效）。

        先通知泵线程注销所有旧热键，再根据配置重新注册 stop / pause / start。
        空字符串的快捷键自动跳过（不绑定）。
        """
        hotkeys = self.cfg.get("hotkeys", {})
        
        # 收集要注册的热键
        pending = []
        for name, key_str, callback in [
            ("stop", hotkeys.get("stop", ""), self._hotkey_stop),
            ("pause", hotkeys.get("pause", ""), self._hotkey_pause),
            ("start", hotkeys.get("start", ""), self._hotkey_start),
        ]:
            if not key_str:
                continue
            mods, vk = _parse_hotkey_str(key_str)
            if vk is None:
                self._log(f"警告: 快捷键 '{key_str}' 无法解析，已跳过")
                continue
            pending.append((name, key_str, mods, vk, callback))
        
        # 将待注册列表交给泵线程（或启动新线程）
        self._pending_hotkeys = pending
        
        if not pending:
            self._unregister_hotkey()
            return
        
        if self._hotkey_thread is None or not self._hotkey_thread.is_alive():
            self._hotkey_ready.clear()
            self._hotkey_thread = threading.Thread(target=self._hotkey_pump, daemon=True)
            self._hotkey_thread.start()
            if not self._hotkey_ready.wait(timeout=3.0):
                self._log("警告: 热键后台线程启动超时")
        else:
            # 通知已有线程重新加载
            self._hotkey_reload.set()

    def _unregister_hotkey(self):
        """通知后台泵线程注销所有热键并退出"""
        self._hotkey_reload.set()
        self._pending_hotkeys = []
        # 旧热键由泵线程在收到 reload 信号时清理

    def _hotkey_pump(self):
        """后台线程：RegisterHotKey(NULL) + GetMessage 消息泵。

        热键注册到 NULL 句柄 → WM_HOTKEY 投递到本线程队列，
        本线程通过 PeekMessage 取出并 via after_idle 投递到主线程。
        """
        user32 = ctypes.windll.user32
        msg = wintypes.MSG()
        local_ids = {}    # atom_id → (name, callback)
        self._hotkey_reload.clear()
        self._hotkey_ready.set()
        
        while not self._quitting:
            # 处理重载请求
            if self._hotkey_reload.is_set():
                self._hotkey_reload.clear()
                # 注销所有旧热键
                for aid in list(local_ids.keys()):
                    try:
                        user32.UnregisterHotKey(None, aid)
                    except Exception:
                        pass
                local_ids.clear()
                
                # 注册新热键
                aid = 1
                for name, key_str, mods, vk, callback in self._pending_hotkeys:
                    try:
                        result = user32.RegisterHotKey(None, aid, mods, vk)
                        if result:
                            local_ids[aid] = (name, callback)
                            aid += 1
                        else:
                            err = ctypes.get_last_error()
                            self.root.after_idle(
                                lambda k=key_str, e=err: self._log(
                                    f"警告: 注册热键 '{k}' 失败 (错误码 {e})，可能已被其他程序占用"))
                    except Exception as ex:
                        self.root.after_idle(
                            lambda k=key_str, ex=ex: self._log(
                                f"警告: 注册热键 '{k}' 异常: {ex}"))
                
                # 更新主线程可见的热键映射（用于外部查询）
                self._hotkey_atoms = dict(local_ids)
                self._hotkey_ids = {name: aid for aid, (name, _) in local_ids.items()}
                self._hotkey_ready.set()
                
                if not local_ids:
                    break  # 没有热键要监听，退出泵线程
            
            # 泵消息（50ms 超时，不阻塞退出检查）
            if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE
                if msg.message == _WM_HOTKEY:
                    entry = local_ids.get(msg.wParam)
                    if entry:
                        _name, callback = entry
                        self.root.after_idle(callback)
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            else:
                self._hotkey_stop_event.wait(0.05)

    def _hotkey_stop(self):
        """热键触发的停止"""
        if self.running or self.checkin_stop_event:
            hotkey_str = self.cfg.get("hotkeys", {}).get("stop", "")
            self._log(f"热键停止 ({hotkey_str})")
            self._stop()

    def _hotkey_pause(self):
        """热键触发的暂停/继续"""
        if self.running:
            hotkey_str = self.cfg.get("hotkeys", {}).get("pause", "")
            self._log(f"热键暂停/继续 ({hotkey_str})")
            self._pause_toggle()

    def _hotkey_start(self):
        """热键触发的开始任务"""
        if not self.running:
            hotkey_str = self.cfg.get("hotkeys", {}).get("start", "")
            self._log(f"热键开始任务 ({hotkey_str})")
            self._start()

    # ------------------------------------------------------------------
    # 系统托盘（pystray）
    # ------------------------------------------------------------------

    def _setup_tray(self):
        """初始化 pystray 托盘图标，并启动后台守护线程"""
        try:
            # 优先加载 icon.ico，失败则用代码生成的默认图标
            image = None
            icon_path = _gen_icon()
            if icon_path and os.path.exists(icon_path):
                try:
                    image = Image.open(icon_path)
                except Exception:
                    pass
            if image is None:
                image = self._tray_default_image()

            checkin_menu, checkin_has_enabled = self._build_checkin_submenu()
            menu = pystray.Menu(
                pystray.MenuItem("显示窗口", self._tray_show_window, default=True),
                pystray.MenuItem("开始任务", self._tray_start),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("定时任务", self._build_scheduler_submenu(),
                    checked=lambda item: any(s.get("enabled", True)
                        for s in load_scheduler_config().get("schedules", []))),
                pystray.MenuItem("定时签到", checkin_menu,
                    checked=lambda item, he=checkin_has_enabled: he),
                pystray.MenuItem("取消自动关机", self._tray_cancel_shutdown, checked=lambda item: self._shutdown_pending),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("设置", self._tray_settings),
                pystray.MenuItem("退出", self._tray_quit),
            )
            self.tray = pystray.Icon("genshin_onedragon", image, "GenshinMultiAccountTool v5.5", menu)
            global _global_tray
            _global_tray = self.tray
            threading.Thread(target=self.tray.run, daemon=True).start()
            self.root.after(100, self._poll_pending_action)
        except Exception:
            import traceback
            err = traceback.format_exc()
            if getattr(sys, 'frozen', False):
                log_dir = os.path.dirname(sys.executable)
            else:
                log_dir = os.path.dirname(os.path.abspath(__file__))
            with open(os.path.join(log_dir, "tray_error.log"), "w", encoding="utf-8") as f:
                f.write(err)
            messagebox.showerror("托盘初始化失败", err[:500])
            self.tray = None
            _global_tray = None

    @staticmethod
    def _tray_default_image():
        """生成默认托盘图标（蓝绿圆 + 白星）"""
        sz = 32
        img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse([2, 2, sz - 2, sz - 2], fill=(65, 180, 210, 255))
        d.ellipse([3, 3, sz - 3, sz - 3], fill=(50, 160, 195, 255))
        m = sz // 2
        r_outer = (sz // 2) - 4
        r_inner = 3
        d.polygon([
            (m, m - r_outer), (m + r_inner, m),
            (m + r_outer, m), (m, m + r_inner),
            (m, m + r_outer), (m - r_inner, m),
            (m - r_outer, m), (m, m - r_inner),
        ], fill=(255, 255, 255, 230))
        return img

    def _tray_show(self):
        self.root.after(0, self._tray_show_in_main)

    def _tray_show_in_main(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _tray_quit(self):
        self.root.after(0, self._tray_quit_in_main)

    def _tray_quit_in_main(self):
        """退出程序：先注销托盘图标，再销毁窗口，避免幽灵图标。"""
        global _global_tray
        self._quitting = True
        self._unregister_hotkey()
        self.scheduler_event.set()
        self.checkin_scheduler_event.set()
        if self.checkin_stop_event:
            self.checkin_stop_event.set()
        self.running = False
        if self.stop_event:
            self.stop_event.set()
        try:
            if self.tray:
                self.tray.stop()
        finally:
            self.tray = None
            _global_tray = None
            try:
                self.root.destroy()
            finally:
                # 兜底清除残留
                try:
                    refresh_system_tray()
                except Exception:
                    pass

    def _on_close(self):
        """点 X 关闭 → 根据设置决定缩到托盘或退出"""
        cfg = load_config()
        minimize_on_close = cfg.get("settings", {}).get("minimize_on_close", True)
        if self.tray and minimize_on_close:
            self.root.withdraw()
        else:
            self._tray_quit_in_main()

    def _minimize_to_tray(self):
        if self.tray:
            self.root.withdraw()
        else:
            self.root.iconify()

    def _poll_pending_action(self):
        """主线程轮询：消费 pystray 线程设置的标志位，安全执行 tkinter 操作"""
        action = getattr(self, '_pending_action', None)
        if action == "start":
            self._pending_action = None
            self._start()
            self.root.after(200, self._rebuild_tray_menu)
        elif action == "pause":
            self._pending_action = None
            self._pause_toggle()
            self.root.after(200, self._rebuild_tray_menu)
        elif action == "stop":
            self._pending_action = None
            self._stop()
            self.root.after(200, self._rebuild_tray_menu)
        elif action == "show_window":
            self._pending_action = None
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        elif action == "settings":
            self._pending_action = None
            self._open_settings()
        if getattr(self, '_pending_rebuild', False):
            self._pending_rebuild = False
            self._rebuild_tray_menu()
        self.root.after(100, self._poll_pending_action)

    def _tray_start(self, icon=None, item=None):
        if self.running:
            return
        self._pending_action = "start"

    def _tray_pause(self, icon=None, item=None):
        if not self.running:
            return
        self._pending_action = "pause"

    def _tray_stop(self, icon=None, item=None):
        if not self.running:
            return
        self._pending_action = "stop"

    def _tray_show_window(self, icon=None, item=None):
        self._pending_action = "show_window"

    def _refresh_scheduler_ui(self):
        """刷新定时计划对话框和签到对话框 UI（供托盘回调调用）"""
        if hasattr(self, 'scheduler_dialog') and self.scheduler_dialog and self.scheduler_dialog.winfo_exists():
            self.scheduler_dialog._load_list()
            self.scheduler_dialog.refresh_btn()
        if hasattr(self, 'checkin_dialog') and self.checkin_dialog and self.checkin_dialog.dlg.winfo_exists():
            self.checkin_dialog._load_list()

    def _tray_scheduler_toggle(self):
        def _toggle():
            if not self.scheduler_event.is_set():
                self.scheduler_event.set()
                self._log("定时器已停止")
            else:
                cfg = load_scheduler_config()
                if not cfg.get("schedules"):
                    self._log("[定时器] 没有设置任何定时任务")
                    return
                # 一键启用全部任务
                for s in cfg["schedules"]:
                    s["enabled"] = True
                save_scheduler_config(cfg)
                self.scheduler_event.clear()
                self.scheduler_thread = threading.Thread(
                    target=self._scheduler_loop, daemon=True)
                self.scheduler_thread.start()
                self._log("定时器已启动（一键启用全部任务）")
            self._refresh_scheduler_ui()
        self.root.after(0, _toggle)

    def _tray_cancel_shutdown(self):
        self.root.after(0, self._cancel_shutdown)

    def _tray_settings(self, icon=None, item=None):
        self._pending_action = "settings"

    # ------------------------------------------------------------------
    # 托盘 - 定时器子菜单
    # ------------------------------------------------------------------

    def _format_schedule_desc_short(self, s):
        """简短描述定时任务（用于托盘子菜单），不超过 20 字"""
        accts = ", ".join(s.get("accounts", []))
        time_str = s.get("time", "??:??")
        stype = s.get("schedule_type", "daily")
        if stype == "daily":
            desc = f"每天{time_str}"
        elif stype == "weekly":
            wds = s.get("weekdays", [])
            wd_map = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "日"}
            wd_names = "".join(wd_map.get(i, "") for i in wds)
            desc = f"每周{wd_names} {time_str}"
        elif stype == "once":
            date_str = s.get("date", "")
            desc = f"{date_str} {time_str}"
        elif stype == "dates":
            desc = f"指定日期 {time_str}"
        else:
            desc = f"{time_str}"
        if accts:
            desc = f"{accts} {desc}"
        return desc

    def _make_tray_task_toggle(self, schedule):
        """返回回调：切换单个定时任务的启用状态并重建托盘菜单"""
        sched_time = schedule["time"]
        sched_type = schedule.get("schedule_type", "daily")
        sched_accounts = schedule.get("accounts", [])

        def toggle(icon, item):
            # 只修改数据，不碰菜单（避免在 pystray 回调中触发 WinError 87）
            cfg = load_scheduler_config()
            for s in cfg["schedules"]:
                if (s["time"] == sched_time and
                    s.get("schedule_type") == sched_type and
                    s.get("accounts") == sched_accounts):
                    s["enabled"] = not s.get("enabled", True)
                    break
            save_scheduler_config(cfg)
            has_enabled = any(s.get("enabled", True) for s in cfg["schedules"])
            if has_enabled and self.scheduler_event.is_set():
                self.scheduler_event.clear()
                self.scheduler_thread = threading.Thread(
                    target=self._scheduler_loop, daemon=True)
                self.scheduler_thread.start()
                self.root.after(0, lambda: self._log("定时器已启动（托盘启用任务）"))
            elif not has_enabled and not self.scheduler_event.is_set():
                self.scheduler_event.set()
                self.root.after(0, lambda: self._log("定时器已停止（所有任务均已停用）"))
            self._pending_rebuild = True
            self.root.after(0, self._refresh_scheduler_ui)

        return toggle

    def _tray_start_all(self, icon, item):
        """一键启用全部定时任务并启动线程"""
        cfg = load_scheduler_config()
        if not cfg.get("schedules"):
            return
        for s in cfg["schedules"]:
            s["enabled"] = True
        save_scheduler_config(cfg)
        # 先停止旧线程（如果存在）
        if not self.scheduler_event.is_set():
            self.scheduler_event.set()
            if self.scheduler_thread and self.scheduler_thread.is_alive():
                self.scheduler_thread.join(timeout=2)
        self.scheduler_event.clear()
        self.scheduler_thread = threading.Thread(
            target=self._scheduler_loop, daemon=True)
        self.scheduler_thread.start()
        self.root.after(0, lambda: self._log("定时器已启动（一键启用全部任务）"))
        self._pending_rebuild = True
        self.root.after(0, self._refresh_scheduler_ui)

    def _tray_stop_all(self, icon, item):
        """全部停用定时任务并停止线程"""
        cfg = load_scheduler_config()
        for s in cfg.get("schedules", []):
            s["enabled"] = False
        save_scheduler_config(cfg)
        self.scheduler_event.set()
        self.root.after(0, lambda: self._log("定时器已停止（全部任务已停用）"))
        self._pending_rebuild = True
        self.root.after(0, self._refresh_scheduler_ui)

    def _tray_start_all_checkin(self, icon, item):
        cfg = load_checkin_schedule()
        for c in cfg.get("checkins", []):
            c["enabled"] = True
        save_checkin_schedule(cfg)
        cfg["enabled"] = True
        save_checkin_schedule(cfg)
        if not self.checkin_scheduler_event or not self.checkin_scheduler_thread or not self.checkin_scheduler_thread.is_alive():
            self.start_checkin_scheduler()
        self.root.after(0, lambda: self._log("定时签到已全部启用"))
        self._pending_rebuild = True
        self.root.after(0, self._refresh_scheduler_ui)

    def _tray_stop_all_checkin(self, icon, item):
        cfg = load_checkin_schedule()
        for c in cfg.get("checkins", []):
            c["enabled"] = False
        save_checkin_schedule(cfg)
        self.root.after(0, lambda: self._log("定时签到已全部停用"))
        self._pending_rebuild = True
        self.root.after(0, self._refresh_scheduler_ui)

    def _build_scheduler_submenu(self):
        """动态构建定时器子菜单（每次调用时根据最新状态生成）"""
        cfg = load_scheduler_config()
        schedules = cfg.get("schedules", [])
        items = []
        for s in schedules:
            if s.get("task_type") == "checkin":
                continue
            desc = self._format_schedule_desc_short(s)
            enabled = s.get("enabled", True)
            items.append(pystray.MenuItem(
                desc,
                self._make_tray_task_toggle(s),
                checked=lambda item, en=enabled: en
            ))
        if items:
            items.append(pystray.Menu.SEPARATOR)
        all_enabled = all(s.get("enabled", True) for s in schedules) if schedules else False
        has_enabled = any(s.get("enabled", True) for s in schedules) if schedules else False
        scheduler_running = not self.scheduler_event.is_set()
        items.append(pystray.MenuItem(
            "一键启动全部定时器", self._tray_start_all,
            checked=lambda item, ae=all_enabled, sr=scheduler_running: ae and sr))
        if scheduler_running and has_enabled:
            items.append(pystray.MenuItem(
                "一键关闭全部定时器", self._tray_stop_all))
        return pystray.Menu(*items) if items else pystray.Menu(
            pystray.MenuItem("（暂无任务）", None, enabled=False)
        )

    def _build_checkin_submenu(self):
        """动态构建定时签到子菜单（每次调用时根据最新状态生成）"""
        cfg = load_checkin_schedule()
        checkins = cfg.get("checkins", [])
        items = []
        for c in checkins:
            c_enabled = c.get("enabled", True)
            time_str = c.get("time", "??:??")
            label = c.get("label", f"签到 {time_str}")
            items.append(pystray.MenuItem(
                label,
                self._toggle_checkin_entry(c),
                checked=lambda item, en=c_enabled: en
            ))
        if items:
            items.append(pystray.Menu.SEPARATOR)
        has_enabled = any(c.get("enabled", True) for c in checkins) if checkins else False
        all_enabled = all(c.get("enabled", True) for c in checkins) if checkins else False
        checkin_running = not self.checkin_scheduler_event.is_set()
        items.append(pystray.MenuItem(
            "一键启用全部签到", self._tray_start_all_checkin,
            checked=lambda item, ae=all_enabled: ae))
        if checkin_running and has_enabled:
            items.append(pystray.MenuItem(
                "一键停用全部签到", self._tray_stop_all_checkin))
        return pystray.Menu(*items) if items else pystray.Menu(
            pystray.MenuItem("（暂无签到任务）", None, enabled=False)
        ), has_enabled

    def _rebuild_tray_menu(self):
        """重建托盘菜单（仅在主循环空闲时调用，不在 pystray 回调中调用）"""
        if not self.tray:
            return
        items = []
        # 显示窗口作为默认动作
        items.append(pystray.MenuItem("显示窗口", self._tray_show_window, default=True))
        # 开始/暂停/停止 — 根据 self.running 动态显示
        if self.running:
            items.append(pystray.MenuItem("暂停任务", self._tray_pause, checked=lambda item: self.paused))
            items.append(pystray.MenuItem("停止任务", self._tray_stop))
        else:
            items.append(pystray.MenuItem("开始任务", self._tray_start))
        checkin_menu, checkin_has_enabled = self._build_checkin_submenu()
        items.extend([
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("定时器", self._build_scheduler_submenu(),
                checked=lambda item: any(s.get("enabled", True)
                    for s in load_scheduler_config().get("schedules", [])) and not self.scheduler_event.is_set()),
            pystray.MenuItem("定时签到", checkin_menu,
                checked=lambda item, he=checkin_has_enabled: he),
            pystray.MenuItem("取消自动关机", self._tray_cancel_shutdown,
                             checked=lambda item: self._shutdown_pending),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("设置", self._tray_settings),
            pystray.MenuItem("退出", self._tray_quit),
        ])
        self.tray.menu = pystray.Menu(*items)


# ============================================================
# 入口
# ============================================================

def _gen_icon():
    """生成原神风格图标，返回 .ico 路径"""
    import os as _os, sys as _sys
    if getattr(_sys, 'frozen', False):
        icon_path = _os.path.join(_sys._MEIPASS, "icon.ico")
    else:
        icon_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "icon.ico")
    if _os.path.exists(icon_path):
        return icon_path  # 已存在则跳过
    try:
        from PIL import Image, ImageDraw
        sz = 64
        img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        # 蓝绿色圆形背景（原神主色调）
        d.ellipse([4, 4, sz - 4, sz - 4], fill=(65, 180, 210, 255))
        d.ellipse([6, 6, sz - 6, sz - 6], fill=(50, 160, 195, 255))
        # 白色四芒星（类似原石/星辉图案）
        m = sz // 2
        r_outer = (sz // 2) - 8
        r_inner = 6
        # 上点
        top = (m, m - r_outer)
        # 右点
        right = (m + r_outer, m)
        # 下点
        bottom = (m, m + r_outer)
        # 左点
        left = (m - r_outer, m)
        # 内缩点（星形凹陷）
        ti = (m, m - r_inner)
        ri = (m + r_inner, m)
        bi = (m, m + r_inner)
        li = (m - r_inner, m)
        d.polygon([top, ri, right, bi, bottom, li, left, ti], fill=(255, 255, 255, 240))
        img.save(icon_path, format="ICO", sizes=[(sz, sz), (32, 32), (16, 16)])
        return icon_path
    except ImportError:
        return None


def _gen_blank_ico():
    """透明 ICO（16x16 + 32x32），用于子窗口去除 Tkinter 默认图标"""
    import tempfile
    blank_path = os.path.join(tempfile.gettempdir(), "_gm_blank.ico")
    if not os.path.exists(blank_path):
        from PIL import Image
        img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        img.save(blank_path, format="ICO", sizes=[(16, 16), (32, 32)])
    return blank_path


def main():
    # 单实例检查
    import msvcrt
    lock_path = os.path.join(tempfile.gettempdir(), "GenshinMultiAccountTool.lock")
    try:
        _lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_TRUNC)
        msvcrt.locking(_lock_fd, msvcrt.LK_NBLCK, 1)
    except (IOError, OSError):
        messagebox.showwarning("原神一条龙", "程序已在运行中。")
        return

    # 启动时清除上次强杀程序遗留的无效托盘图标
    try:
        refresh_system_tray()
    except Exception:
        pass

    # 注册退出兜底：确保托盘对象一定被释放
    import atexit as _atexit
    _atexit.register(_cleanup_tray_on_exit)

    root = tk.Tk()
    root.withdraw()  # 隐藏小窗口，等界面就绪再显示
    ico = _gen_icon()
    if ico:
        root.iconbitmap(ico)
    app = GenshinMultiAccountToolGUI(root)

    # 开机自启动：自动启动定时器，不显示主窗口
    if "--auto-start-scheduler" in sys.argv:
        app.root.after(500, app._auto_start_scheduler)
    else:
        root.deiconify()
        root.lift()
        root.focus_force()

    root.mainloop()


if __name__ == "__main__":
    main()

