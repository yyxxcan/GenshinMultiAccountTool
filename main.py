#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GenshinAutoTool v5.2 - 原神多账号自动化一条龙 (动态账号版)
============================================================
- 天空蓝简约 GUI
- 动态添加/删除账号，支持胡桃工具箱账号
- BetterGI 一条龙配置自动发现
- 自动处理 ESC 阻塞
- 实时日志 + 进度 + 即停即止
"""

import os, sys, json, time, glob, queue, tempfile, threading, subprocess
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
CONFIG_PATH = SCRIPT_DIR / "config.json"
SCHEDULER_CONFIG_PATH = SCRIPT_DIR / "scheduler_config.json"
LOGS_DIR = SCRIPT_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

BETTERGI_ONEDRAGON_DIR = r"E:\原神\BetterGI\BetterGI\User\OneDragon"
BETTERGI_USER_DIR = r"E:\原神\BetterGI\BetterGI\User"

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

def load_config():
    defaults = {
        "accounts": [],
        "bettergi": {
            "exe": "E:\\原神\\BetterGI\\BetterGI\\BetterGI.exe",
            "config": "E:\\原神\\BetterGI\\BetterGI\\User\\config.json",
        },
        "snap_hutao": {
            "exe": "D:\\原神\\胡桃工具箱\\Repository\\Snap.ContentDelivery\\Snap.Hutao.Remastered.FullTrust.exe",
            "app_id": "E8B6E2B3-D2A0-4435-A81D-2A16AAF405C8_k3erpsn8bwzzy!App",
        },
        "genshin": {
            "exe": "E:\\原神\\Genshin Impact\\Genshin Impact Game\\YuanShen.exe",
            "process_name": "YuanShen.exe",
        },
        "monitor": {
            "max_wait_seconds": 7200,
        },
        "tesseract": {
            "path": "C:/Program Files/Tesseract-OCR",
        },
        "hotkeys": {
            "stop": "ctrl+shift+q",
        },
        "uid": {
            "method": "tesseract",
            "bettergi_group": "",
        },
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
        return user_cfg
    return defaults


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ============================================================
# 定时器配置管理
# ============================================================
def load_scheduler_config():
    defaults = {
        "schedules": []
    }
    if SCHEDULER_CONFIG_PATH.is_file():
        with open(SCHEDULER_CONFIG_PATH, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
        for key, default_val in defaults.items():
            if key not in user_cfg:
                user_cfg[key] = default_val
        return user_cfg
    return defaults


def save_scheduler_config(cfg):
    with open(SCHEDULER_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---- 开机自启动 ----
AUTOSTART_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_VALUE_NAME = "GenshinAutoTool"


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


def discover_onedragon_configs():
    """扫描 OneDragon 目录，返回可用配置名列表"""
    configs = []
    if os.path.isdir(BETTERGI_ONEDRAGON_DIR):
        for f in glob.glob(os.path.join(BETTERGI_ONEDRAGON_DIR, "*.json")):
            configs.append(os.path.splitext(os.path.basename(f))[0])
    return sorted(configs)


def discover_scheduler_groups():
    """扫描 BetterGI ScriptGroup 目录，返回可用配置组名列表"""
    groups_dir = os.path.join(BETTERGI_USER_DIR, "ScriptGroup")
    groups = []
    if os.path.isdir(groups_dir):
        for f in glob.glob(os.path.join(groups_dir, "*.json")):
            groups.append(os.path.splitext(os.path.basename(f))[0])
    return sorted(groups)


def find_proc(name):
    t = name.lower()
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if p.info["name"] and p.info["name"].lower() == t:
                return p
        except Exception:
            pass
    return None


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


def cleanup_all(log_func):
    log_func("全局清理：关闭所有相关进程...")
    for n in GLOBAL_CLEANUP_TARGETS:
        log_func(f"  关闭 {n}...")
        kill_proc(n, graceful=True)
    time.sleep(3)
    ok = True
    for n in GLOBAL_CLEANUP_TARGETS:
        if find_proc(n):
            log_func(f"  [!] {n} 仍在运行")
            ok = False
    if ok:
        log_func("全局清理完成")
    return ok


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


def activate_hutao_window():
    """激活胡桃窗口（MSIX 应用启动后默认不可见），用 EnumWindows 找 WinUIDesktopWin32WindowClass"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    results = []

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    def _enum_callback(hwnd, _lparam):
        try:
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 255)
            if not buf.value or "胡桃" not in buf.value:
                return True
            cls_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls_buf, 255)
            if cls_buf.value != "WinUIDesktopWin32WindowClass":
                return True
            results.append(hwnd)
        except Exception:
            pass
        return True

    user32.EnumWindows(WNDENUMPROC(_enum_callback), 0)

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


def monitor_bettergi_log(log_date_str, timeout_sec, log_func, stop_event):
    """
    监控 BetterGI 日志，检测「任务结束」和 ESC 阻塞。
    一条龙任务包含多个子任务（邮件→脚本→追踪），每个子任务都会写"任务结束"。
    当检测到「一条龙和配置组任务结束」紧接着「任务结束」时，判定整条龙真正完成。
    """
    log_path = f"E:\\原神\\BetterGI\\BetterGI\\log\\better-genshin-impact{log_date_str}.log"
    log_func(f"监控日志: {log_path}")
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
    onedragon_done_line = False  # 检测到"一条龙和配置组任务结束"标志

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

        # 检测 ESC 阻塞
        if esc_cooldown <= 0 and ("请自行退出当前界面（ESC）" in new or "按ESC" in new):
            log_func("[ESC] 检测到阻塞，自动发送 ESC...")
            send_esc_to_genshin()
            esc_cooldown = 15
            time.sleep(2)

        if esc_cooldown > 0:
            esc_cooldown -= 3

        if not new:
            continue

        # -------- 精确完成判定 --------
        # 检测「一条龙和配置组任务结束」标志位
        if "一条龙和配置组任务结束" in new:
            onedragon_done_line = True
            log_func("检测到一条龙配置组完成标志，等待任务结束...")
            # 同一批日志里紧跟"任务结束" → 立即完成
            if "任务结束" in new:
                elapsed = int(time.time() - start_t)
                log_func(f"一条龙任务完成 耗时 {elapsed} 秒，10 秒后结束...")
                time.sleep(10)
                return True
            continue

        # 如果上轮已看到标志，本轮出现"任务结束" → 立即完成
        if onedragon_done_line and "任务结束" in new:
            elapsed = int(time.time() - start_t)
            log_func(f"一条龙任务完成 耗时 {elapsed} 秒，10 秒后结束...")
            time.sleep(10)
            return True

        # -------- 子任务日志（仅供参考，不影响判定） --------
        if "任务结束" in new:
            log_func("检测到子任务结束")
        if "任务启动" in new:
            log_func("检测到新子任务启动")

    log_func(f"任务监控超时（{timeout_sec} 秒）")
    return False


def monitor_config_group(log_date_str, group_name, timeout_sec, log_func, stop_event):
    """
    监控 BetterGI 日志，等待指定配置组执行结束。
    检测到 配置组 "xxx" 执行结束 时返回 True。
    仅用于 --startGroups 的单组执行监控，不处理一条龙完成检测。
    """
    log_path = f"E:\\原神\\BetterGI\\BetterGI\\log\\better-genshin-impact{log_date_str}.log"
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


def wait_proc_appear(name, timeout_sec, log_func, stop_event):
    start_t = time.time()
    last_log = 0
    while time.time() - start_t < timeout_sec:
        if stop_event.is_set():
            return None
        p = find_proc(name)
        if p:
            time.sleep(2)
            return p
        now = time.time()
        if now - last_log > 15:
            log_func(f"等待进程 {name}... ({int(now-start_t)}s/{timeout_sec}s)")
            last_log = now
        time.sleep(3)
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
    """用 EnumWindows 找到胡桃真实窗口（WinUIDesktopWin32WindowClass，非 MSIX 容器壳）"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    results = []

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    def _enum_callback(hwnd, _lparam):
        try:
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 255)
            title = buf.value
            if not title or "胡桃" not in title:
                return True
            cls_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls_buf, 255)
            if cls_buf.value != "WinUIDesktopWin32WindowClass":
                return True
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
        return True

    user32.EnumWindows(WNDENUMPROC(_enum_callback), 0)

    # 过滤掉 MSIX 容器壳（5x5 微小窗口），返回真实窗口
    for r in results:
        if r["width"] > 100 and r["height"] > 100:
            return r
    return None


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
        except: pass
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
                    except: pass
                if not skip:
                    # 评分：ButtonControl 优先，名字直接匹配加分
                    score = 0
                    if ct == "ButtonControl":
                        score += 100
                    try:
                        if ctrl.Name and "启动游戏" in ctrl.Name:
                            score += 50
                    except: pass
                    # 控件越靠近窗口顶部/右侧加分（排除底部溢出控件）
                    score -= ry  # y 越小越好
                    log_func(f"候选: [{ct}] '{ctrl.Name or ''}' ({r.left},{r.top}) {w}x{h} score={score}")
                    results.append((score, ctrl, r))

        if depth > 12:
            return results
        try:
            for child in ctrl.GetChildren():
                results.extend(_collect_candidates(child, depth + 1))
        except: pass
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
        # 第一次点击（可能被弹窗拦截）
        pyautogui.click(cx, cy)
        log_func("已点击「启动游戏」(第1次)")
        time.sleep(1.5)
        # 第二次点击（兜底，防止首次被弹窗消费）
        pyautogui.click(cx, cy)
        log_func("已点击「启动游戏」(第2次)")
        time.sleep(2)
        return True
    except Exception as e:
        log_func(f"UIA点击失败，改用鼠标: {e}")
        pyautogui.click(cx, cy)
        time.sleep(1.5)
        pyautogui.click(cx, cy)
        time.sleep(2)
        log_func("已点击「启动游戏」(鼠标双击)")
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

    def _scout(ctrl):
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
                _scout(child)
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
        return True

    return False


def wait_genshin_ready(log_func, stop_event, timeout_sec=300):
    """等待原神真正进入游戏：窗口出现 → 大小稳定 → 白屏→画面色彩检测"""
    import numpy as np
    from PIL import Image

    if not HAS_GW:
        time.sleep(90)
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

    # 阶段2：窗口大小稳定
    last_size = None
    stable = 0
    while stable < 3 and (time.time() - start_t < timeout_sec):
        if stop_event.is_set():
            return False
        time.sleep(6)
        try:
            for pat in ["原神", "YuanShen", "Genshin Impact"]:
                ws = gw.getWindowsWithTitle(pat)
                if ws:
                    win = ws[0]
                    break
            if not win:
                stable = 0
                continue
            cur = (win.width, win.height)
            if cur[0] < 100:
                continue
            if last_size and cur == last_size:
                stable += 1
                log_func(f"窗口稳定 {stable}/3: {cur[0]}x{cur[1]}")
            else:
                stable = 1
                last_size = cur
        except Exception:
            stable = 0

    if stable < 3:
        return False

    # 阶段3：截图色彩检测
    # 原神流程: 白屏(7元素) → 健康提示 → 白屏(岩元素) → 加载画面 → 游戏
    # 进入游戏的标志：画面长时间稳定、色彩丰富、白色占比低
    log_func("开始截图检测...")
    was_white = False
    white_count = 0
    transition_count = 0
    stable_count = 0
    post_enter_stable = 0  # 初步判定进入后，还需持续确认

    while time.time() - start_t < timeout_sec:
        if stop_event.is_set():
            return False

        try:
            for pat in ["原神", "YuanShen", "Genshin Impact"]:
                ws = gw.getWindowsWithTitle(pat)
                if ws:
                    win = ws[0]
                    break
            if not win or win.width < 100:
                time.sleep(5)
                continue

            l, t, w, h = win.left, win.top, win.width, win.height
            m = int(min(w, h) * 0.08)
            region = (l + m, t + m, w - 2 * m, h - 2 * m)

            img = pyautogui.screenshot(region=region)
            arr = np.array(img, dtype=np.float32)

            diff = np.sqrt(np.sum((arr - 255.0) ** 2, axis=2))
            white_ratio = np.mean(diff < 40)
            color_std = float(np.std(arr))

            if white_ratio > 0.55:
                white_count += 1
                if not was_white and white_count >= 2:
                    was_white = True
                    log_func(f"检测到加载画面 (第{transition_count+1}次, 白色 {white_ratio:.1%})")
            elif was_white and white_count >= 1:
                transition_count += 1
                log_func(f"画面已变化 (第{transition_count}次转换, 白色 {white_ratio:.1%}, 色彩度 {color_std:.0f})")
                if transition_count >= 2:
                    # 第2次转换后还需持续确认：连续5帧非白屏 + 色彩度 > 20 才真正进入
                    post_enter_stable += 1
                    if post_enter_stable >= 5 and color_std > 20:
                        time.sleep(2)
                        log_func("游戏已进入")
                        return True
                was_white = False
                white_count = 0
            elif transition_count >= 1:
                # 第2次白屏可能被跳过，用连续非白屏+画面有内容兜底
                stable_count += 1
                if stable_count >= 8 and color_std > 25:
                    time.sleep(2)
                    log_func("游戏已进入（连续非白屏）")
                    return True
            else:
                pass

        except Exception as e:
            log_func(f"截图异常: {e}")

        time.sleep(1)

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

    # 1. 优先使用 exe 同目录下的便携版 Tesseract（免安装）
    portable_tess = os.path.join(str(SCRIPT_DIR), "tesseract", "tesseract.exe")
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

    debug_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp")
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
                except:
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
                except:
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
                except:
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
    log_path = f"E:\\原神\\BetterGI\\BetterGI\\log\\better-genshin-impact{log_date_str}.log"

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
        proc = subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW)
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
    def __init__(self, accounts, log_queue, stop_event, pause_event):
        super().__init__(daemon=True)
        self.accounts = accounts  # 账号列表 (dict)
        self.log_queue = log_queue
        self.stop_event = stop_event
        self.pause_event = pause_event  # 暂停事件：set=不暂停, clear=暂停
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
        sh_exe = cfg["snap_hutao"]["exe"]
        sh_app_id = cfg["snap_hutao"].get("app_id", "")
        timeout = cfg["monitor"].get("max_wait_seconds", 7200)
        log_date_str = datetime.now().strftime("%Y%m%d")

        if not cleanup_all(self.log):
            self.log("[!] 清理失败，请手动关闭后重试")
            return

        total = len(self.accounts)
        executed = set()  # 用 set 记录已执行的账号名，避免重复
        prev_type = None
        idx = 0
        pending = list(self.accounts)  # 待处理列表，UID不匹配时动态调整

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

            if acc_type == "hutao":
                matched, recognized_uid = self._run_hutao_smart(
                    acc, gi_exe, gi_config, genshin_proc, sh_exe,
                    sh_app_id, timeout, log_date_str, pending,
                )
            else:
                matched, recognized_uid = self._run_direct_smart(
                    acc, gi_exe, gi_config, genshin_proc, timeout, log_date_str,
                    is_last=(len(pending) == 0), prev_hutao=(prev_type == "hutao"),
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

        # 最终清理
        for pn in GLOBAL_CLEANUP_TARGETS:
            if find_proc(pn):
                kill_proc(pn, graceful=False)

        self.log("=" * 50)
        self.log(f"完成。成功: {len(executed)}/{total}  {list(executed)}")
        if self.stop_event.is_set():
            self.log("(已中断)")

    def _run_direct_smart(self, acc, gi_exe, gi_config, genshin_proc, timeout,
                         log_date_str, is_last, prev_hutao):
        """直接 BetterGI 启动（大号/小号）。
        先启动 BetterGI 自动登录，验证 UID 正确后再用 -startOneDragon 重启执行一条龙。
        返回 (成功, 识别到的UID或空)。"""
        name = acc["name"]
        expected_uid = acc.get("uid", "").strip()

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
            time.sleep(5)
            ok = monitor_bettergi_log(log_date_str, timeout, self.log, self.stop_event)
            self.log("一条龙完成，30秒后结束游戏...")
            for _ in range(30):
                if self.stop_event.is_set():
                    break
                time.sleep(1)
            if acc.get("close_bettergi", True):
                kill_proc("BetterGI.exe")
                time.sleep(2)
            if acc.get("close_game", True):
                kill_proc(genshin_proc)
                time.sleep(3)
            return ok, ""

        if find_proc("BetterGI.exe"):
            self.log("关闭 BetterGI...")
            kill_proc("BetterGI.exe")
            time.sleep(2)

        self.log("启动 BetterGI（自动登录，暂不执行一条龙）...")
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

        if not wait_genshin_ready(self.log, self.stop_event):
            self.log("[!] 原神可能未进入游戏，继续尝试...")

        if not self.stop_event.is_set():
            self.log("等待界面渲染...")
            time.sleep(8)

        if expected_uid:
            # 轮询等待 UID 可见（BetterGI 全屏独占模式需时间切换）
            self.log("等待游戏进入开放世界（UID 可见）...")
            matched = False
            recognized = ""
            last_wrong_uid = ""
            wrong_streak = 0
            for poll_idx in range(6):
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
                self.log(f"UID 未检测到，继续等待... ({poll_idx+1}/6)")
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
                                    log_date_str, timeout, self.log, self.stop_event)
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
                        if acc.get("close_bettergi", True):
                            kill_proc("BetterGI.exe")
                            time.sleep(2)
                        if acc.get("close_game", True):
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

        ok = monitor_bettergi_log(log_date_str, timeout, self.log, self.stop_event)

        close_game = acc.get("close_game", True)
        close_bettergi = acc.get("close_bettergi", True)

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
                         sh_app_id, timeout, log_date_str, pending):
        """通过胡桃工具箱启动。
        返回 (成功, 识别到的UID或空)。"""
        name = acc["name"]
        hutao_account = acc.get("hutao_account", name)

        self.log(f"胡桃账号: {hutao_account}")

        for pn in ["BetterGI.exe", genshin_proc,
                   "Snap.Hutao.Remastered.exe",
                   "Snap.Hutao.Remastered.FullTrust.exe"]:
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

        self.log("等待胡桃初始化...")
        time.sleep(5)

        if not verify_and_switch_hutao_account(hutao_account, self.log, self.stop_event):
            self.log("等待 15 秒供手动操作...")
            time.sleep(15)

        # 切完账号后连点两次启动游戏（第一次可能被弹窗拦截）
        game_launched = False
        for click_idx in range(2):
            if self.stop_event.is_set():
                kill_proc("Snap.Hutao.Remastered.exe")
                kill_proc("Snap.Hutao.Remastered.FullTrust.exe")
                return False, ""
            if click_idx > 0:
                self.log("第二次点击启动游戏...")
                time.sleep(2)
            ok = click_hutao_start_game(self.log, self.stop_event)
            if ok:
                game_launched = True

        if not game_launched:
            self.log("[!] 无法点击胡桃启动按钮（uiautomation 未安装或窗口异常）")
            self.log("[!] 请安装: pip install uiautomation")
            kill_proc("Snap.Hutao.Remastered.exe")
            kill_proc("Snap.Hutao.Remastered.FullTrust.exe")
            return False, ""

        self.log("等待原神启动...")
        gs = wait_proc_appear(genshin_proc, 180, self.log, self.stop_event)
        if not gs:
            self.log("[!] 原神未启动")
            kill_proc("Snap.Hutao.Remastered.exe")
            kill_proc("Snap.Hutao.Remastered.FullTrust.exe")
            return False, ""

        self.log(f"原神 PID={gs.pid}")

        if not wait_genshin_ready(self.log, self.stop_event):
            self.log("[!] 原神可能未进入游戏，继续尝试...")

        if not self.stop_event.is_set():
            self.log("胡桃切换后等待游戏稳定...")
            time.sleep(3)
            self.log("等待界面渲染...")
            time.sleep(8)

        # 胡桃模式下由胡桃管理账号，无需验证 UID
        self.log("游戏已进入（胡桃模式，跳过 UID 验证）")

        close_hutao = acc.get("close_hutao", True)
        if close_hutao:
            self.log("关闭胡桃...")
            kill_proc("Snap.Hutao.Remastered.exe")
            kill_proc("Snap.Hutao.Remastered.FullTrust.exe")
            time.sleep(1)

        self.log("等待 BetterGI 附带启动...")
        bg_pid = wait_proc_appear("BetterGI.exe", 60, self.log, self.stop_event)
        if not bg_pid:
            self.log("[!] BetterGI 未出现")
            kill_proc(genshin_proc)
            return False, ""
        self.log(f"BetterGI 已出现 PID={bg_pid.pid}，关闭后重新启动...")
        kill_proc("BetterGI.exe")
        time.sleep(3)

        self.log("启动 BetterGI（-startOneDragon）...")
        pid = start_bettergi_onedragon(gi_exe)
        if not pid:
            self.log("[!] BetterGI 启动失败")
            kill_proc(genshin_proc)
            return False, ""
        self.log(f"BetterGI PID={pid}")

        ok = monitor_bettergi_log(log_date_str, timeout, self.log, self.stop_event)

        close_game = acc.get("close_game", True)
        close_bettergi = acc.get("close_bettergi", True)

        if close_bettergi:
            self.log("关闭 BetterGI...")
            kill_proc("BetterGI.exe")
            time.sleep(2)
        if close_game:
            self.log("关闭原神...")
            kill_proc(genshin_proc)
            time.sleep(3)

        return ok, ""


# ============================================================
# 添加账号对话框
# ============================================================

class AddAccountDialog(tk.Toplevel):
    def __init__(self, parent, edit_account=None):
        super().__init__(parent)
        self.result = None
        self.edit_account = edit_account

        self.title("编辑账号" if edit_account else "添加账号")
        self.geometry("420x680")
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
                        value="hutao").pack(side="left")

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

        # 胡桃账号名
        ttk.Label(self, text="胡桃中的账号名称（仅胡桃启动）", foreground=label_fg,
                  background=COLORS["bg"]).pack(anchor="w", **pad)
        self.hutao_var = tk.StringVar(value=edit.get("hutao_account", "") if edit else "")
        self.hutao_entry = ttk.Entry(self, textvariable=self.hutao_var, width=40)
        self.hutao_entry.pack(fill="x", padx=20, pady=(0, 8))

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

        tk.Checkbutton(chk_frame, text="关闭游戏",
                        variable=self.close_game_var).pack(side="left", padx=(0, 10))
        tk.Checkbutton(chk_frame, text="关闭BetterGI",
                        variable=self.close_bettergi_var).pack(side="left", padx=(0, 10))
        tk.Checkbutton(chk_frame, text="关闭胡桃",
                        variable=self.close_hutao_var).pack(side="left")

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

    def _save(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("提示", "请输入账号名称", parent=self)
            return
        config_name = self.config_var.get()
        if not config_name:
            messagebox.showwarning("提示", "请选择 BetterGI 配置", parent=self)
            return

        acc_type = self.type_var.get()
        hutao_account = self.hutao_var.get().strip() if acc_type == "hutao" else ""

        if acc_type == "hutao" and not hutao_account:
            messagebox.showwarning("提示", "请输入胡桃中的账号名称", parent=self)
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
            "scheduler_groups": self.scheduler_var.get(),
        }
        self.destroy()



# ============================================================
# 快捷键检测弹窗
# ============================================================

class HotkeyDetectDialog(tk.Toplevel):
    """快捷键检测弹窗 - 支持组合键和单键（使用 keyboard 库确保可靠性）"""
    def __init__(self, parent, callback):
        super().__init__(parent)
        self.callback = callback
        self.title("检测快捷键")
        self.geometry("360x200")
        self.resizable(False, False)
        self.configure(bg=COLORS["bg"])
        self.transient(parent)
        self.grab_set()

        self.result = None
        self._finished = False

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

        tk.Label(self, text="按 Esc 取消",
                 font=("Microsoft YaHei", 9), bg=COLORS["bg"],
                 fg=COLORS["text_light"]).pack(pady=(8, 0))

        self._center()

        # 后台线程使用 keyboard 库监听按键组合
        self._thread = threading.Thread(target=self._listen_keyboard, daemon=True)
        self._thread.start()

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

    def _listen_keyboard(self):
        """使用 keyboard.read_hotkey 检测组合键，在后台线程中运行"""
        try:
            import keyboard
        except ImportError:
            self.key_display.config(text="keyboard 库未安装")
            return

        # 先清除可能残留的 hook
        keyboard.unhook_all()

        pressed_keys = set()
        display_lock = threading.Lock()

        def on_key(event):
            if self._finished:
                return
            name = event.name
            if event.event_type == "down":
                # Esc 取消
                if name.lower() in ("esc", "escape"):
                    self._finished = True
                    keyboard.unhook_all()
                    self.after(100, self.destroy)
                    return
                pressed_keys.add(name)
            elif event.event_type == "up":
                # 当非修饰键松开时，视为组合键完成
                if name not in ("ctrl", "shift", "alt", "windows", "left windows", "right windows"):
                    if pressed_keys and not self._finished:
                        self._finished = True
                        keyboard.unhook_all()
                        # 构建 hotkey 字符串
                        parts = []
                        for m in ("ctrl", "shift", "alt", "windows"):
                            if m in pressed_keys or "left " + m in pressed_keys or "right " + m in pressed_keys:
                                parts.append(m)
                        main_keys = [k for k in pressed_keys
                                     if k not in ("ctrl", "shift", "alt", "windows",
                                                  "left windows", "right windows",
                                                  "left ctrl", "right ctrl",
                                                  "left shift", "right shift",
                                                  "left alt", "right alt")]
                        parts.extend(main_keys)
                        self.result = "+".join(parts)
                        self.after(200, self._done)
                        return
            # 更新显示
            with display_lock:
                parts = []
                for m in ("ctrl", "shift", "alt", "windows"):
                    if m in pressed_keys or "left " + m in pressed_keys or "right " + m in pressed_keys:
                        parts.append(m)
                main_keys = [k for k in pressed_keys
                             if k not in ("ctrl", "shift", "alt", "windows",
                                          "left windows", "right windows",
                                          "left ctrl", "right ctrl",
                                          "left shift", "right shift",
                                          "left alt", "right alt")]
                parts.extend(main_keys)
                display = " + ".join(p.upper() if len(p) == 1 else p for p in parts) if parts else "等待按键..."
                self.key_display.config(text=display)

        keyboard.hook(on_key, suppress=False)

    def _done(self):
        if self.result and self.callback:
            self.callback(self.result)
        self.destroy()


# ============================================================
# 软件设置对话框
# ============================================================

class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, cfg):
        super().__init__(parent)
        self.result = False
        self.cfg = cfg

        self.title("软件设置")
        self.geometry("480x600")
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

        # 启动时自动最小化
        self.auto_minimize_var = tk.BooleanVar(value=cfg.get("settings", {}).get("auto_minimize", True))
        tk.Checkbutton(inner, text="启动时自动最小化窗口（避免挡住游戏）",
                        variable=self.auto_minimize_var).pack(anchor="w", padx=20, pady=(0, 10))

        # 任务完成后自动关机
        self.auto_shutdown_var = tk.BooleanVar(value=cfg.get("settings", {}).get("auto_shutdown", False))
        tk.Checkbutton(inner, text="所有任务完成后自动关机（60秒倒计时，可取消）",
                        variable=self.auto_shutdown_var).pack(anchor="w", padx=20, pady=(0, 10))

        # 8. Tesseract 安装目录 (可选)
        self.tesseract_var = tk.StringVar(
            value=cfg.get("tesseract", {}).get("path", "C:/Program Files/Tesseract-OCR"))
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

    def _detect_hotkey(self, target="stop"):
        def on_detected(hotkey_str):
            if target == "pause":
                self.pause_hotkey_var.set(hotkey_str)
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
        self.cfg["monitor"]["max_wait_seconds"] = self.timeout_var.get()
        self.cfg.setdefault("tesseract", {})
        self.cfg["tesseract"]["path"] = self.tesseract_var.get().strip()
        self.cfg.setdefault("hotkeys", {})
        self.cfg["hotkeys"]["stop"] = self.hotkey_var.get().strip()
        self.cfg["hotkeys"]["pause"] = self.pause_hotkey_var.get().strip()
        self.cfg.setdefault("settings", {})
        self.cfg["settings"]["auto_minimize"] = self.auto_minimize_var.get()
        self.cfg["settings"]["auto_shutdown"] = self.auto_shutdown_var.get()
        self.cfg.setdefault("uid", {})
        self.cfg["uid"]["method"] = self.uid_method_var.get()
        self.cfg["uid"]["bettergi_group"] = self.uid_group_var.get().strip()
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
    QUICK_TIMES = [("早8点", "08", "00"), ("午12点", "12", "00"),
                   ("晚8点", "20", "00"), ("凌晨4点", "04", "00")]

    def __init__(self, parent, gui):
        super().__init__(parent)
        self.gui = gui
        self.cfg = load_scheduler_config()

        self.title("定时任务管理")
        self.geometry("640x580")
        self.resizable(True, True)
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

        btn_color = "#52C41A" if not self.gui.scheduler_running else "#E74C3C"
        btn_text = "启动定时器" if not self.gui.scheduler_running else "停止定时器"
        self.toggle_btn = tk.Button(top_bar, text=btn_text, command=self._toggle,
                                    bg=btn_color, fg="#FFFFFF",
                                    activebackground="#389E0D" if not self.gui.scheduler_running else "#C0392B",
                                    relief="flat", font=("Microsoft YaHei", 9),
                                    padx=14, pady=2, cursor="hand2", bd=0)
        self.toggle_btn.pack(side="right", padx=(0, 6))

        self._build()
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

        scroll_canvas.bind("<Enter>",
                           lambda e: scroll_canvas.bind_all("<MouseWheel>", _scroll_mousewheel))
        scroll_canvas.bind("<Leave>",
                           lambda e: scroll_canvas.unbind_all("<MouseWheel>"))

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

        # 快捷时间 + 时分选择同行
        t_row = tk.Frame(add_frame, bg="#FFFFFF")
        t_row.pack(fill="x", padx=10, pady=(0, 4))
        tk.Label(t_row, text="时间:", bg="#FFFFFF", fg=COLORS["text_light"],
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=(0, 4))

        for label, h, m in self.QUICK_TIMES:
            tk.Button(t_row, text=label,
                      command=lambda hh=h, mm=m: self._set_time(hh, mm),
                      bg="#EEF2F7", fg=COLORS["text"], relief="flat",
                      font=("Microsoft YaHei", 8), padx=6, cursor="hand2", bd=0).pack(side="left", padx=(0, 3))

        self.hour_var = tk.StringVar(value="08")
        ttk.Combobox(t_row, textvariable=self.hour_var, width=3,
                     values=[f"{h:02d}" for h in range(24)],
                     state="readonly", font=("Microsoft YaHei", 11)).pack(side="left", padx=(8, 0))
        tk.Label(t_row, text="时", bg="#FFFFFF", fg=COLORS["text"],
                 font=("Microsoft YaHei", 9)).pack(side="left")
        tk.Label(t_row, text=":", bg="#FFFFFF", fg=COLORS["text"],
                 font=("Microsoft YaHei", 11, "bold")).pack(side="left", padx=(2, 2))
        self.minute_var = tk.StringVar(value="00")
        ttk.Combobox(t_row, textvariable=self.minute_var, width=3,
                     values=[f"{m:02d}" for m in range(60)],
                     state="readonly", font=("Microsoft YaHei", 11)).pack(side="left")
        tk.Label(t_row, text="分", bg="#FFFFFF", fg=COLORS["text"],
                 font=("Microsoft YaHei", 9)).pack(side="left")

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
        tk.Label(self.weekday_frame, text="选择星期:", bg="#FFFFFF",
                 fg=COLORS["text_light"], font=("Microsoft YaHei", 9)).pack(side="left")
        self.weekday_vars = []
        for i, name in enumerate(self.WEEKDAY_NAMES):
            var = tk.BooleanVar(value=False)
            self.weekday_vars.append(var)
            tk.Checkbutton(self.weekday_frame, text=name, variable=var).pack(
                side="left", padx=(2, 0))
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
        self.acct_canvas.create_window((0, 0), window=self.acct_inner, anchor="nw")

        def _on_acct_inner_resize(event):
            self.acct_canvas.configure(scrollregion=self.acct_canvas.bbox("all"))
            self.after(50, self._update_acct_scrollbar)
        self.acct_inner.bind("<Configure>", _on_acct_inner_resize)
        self.acct_canvas.bind("<Configure>",
            lambda e: self.after(50, self._update_acct_scrollbar))

        def _acct_mousewheel(event):
            if not self.acct_canvas.winfo_exists():
                return
            bbox = self.acct_canvas.bbox("all")
            if bbox and bbox[3] > self.acct_canvas.winfo_height():
                self.acct_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.acct_canvas.bind("<Enter>", lambda e: self.acct_canvas.bind_all("<MouseWheel>", _acct_mousewheel))
        self.acct_canvas.bind("<Leave>", lambda e: self.acct_canvas.unbind_all("<MouseWheel>"))

        self._acct_vars = {}
        accounts = self.gui.cfg.get("accounts", [])
        if accounts:
            for acc in accounts:
                var = tk.BooleanVar(value=False)
                self._acct_vars[acc["name"]] = var
                tk.Checkbutton(self.acct_inner, text=acc["name"], variable=var).pack(
                    anchor="w", padx=4, pady=1)
        else:
            tk.Label(self.acct_inner, text="（暂无账号）", bg="#FFFFFF",
                     fg=COLORS["text_light"], font=("Microsoft YaHei", 9)).pack(anchor="w", padx=4)

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

        self.task_canvas = tk.Canvas(list_frame, bg="#FFFFFF", highlightthickness=0)
        self.task_scroll = ttk.Scrollbar(list_frame, orient="vertical",
                                         command=self.task_canvas.yview)
        self.task_canvas.configure(yscrollcommand=self.task_scroll.set)
        self.task_canvas.pack(fill="both", expand=True)

        self.task_inner = tk.Frame(self.task_canvas, bg="#FFFFFF")
        self.task_canvas.create_window((0, 0), window=self.task_inner, anchor="nw")

        def _on_task_inner_resize(event):
            self.task_canvas.configure(scrollregion=self.task_canvas.bbox("all"))
            self.after(50, self._update_task_scrollbar)
        self.task_inner.bind("<Configure>", _on_task_inner_resize)
        self.task_canvas.bind("<Configure>",
            lambda e: self.after(50, self._update_task_scrollbar))

        def _task_mousewheel(event):
            if not self.task_canvas.winfo_exists():
                return
            bbox = self.task_canvas.bbox("all")
            if bbox and bbox[3] > self.task_canvas.winfo_height():
                self.task_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.task_canvas.bind("<MouseWheel>", _task_mousewheel)

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
        self.hour_var.set(h)
        self.minute_var.set(m)

    def _on_mode_change(self):
        mode = self.mode_var.get()
        if mode == "once":
            self.date_frame.pack(fill="x", padx=10, pady=(2, 4))
        else:
            self.date_frame.pack_forget()

        if mode == "weekly":
            self.weekday_frame.pack(fill="x", padx=10, pady=(2, 4))
        else:
            self.weekday_frame.pack_forget()

        if mode == "dates":
            self.dates_frame.pack(fill="x", padx=10, pady=(2, 4))
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

    def _format_schedule_desc(self, s):
        accts = ", ".join(s.get("accounts", []))
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
        for w in self.task_inner.winfo_children():
            w.destroy()
        self._task_vars = []

        if not self.cfg["schedules"]:
            tk.Label(self.task_inner, text="（暂无任务）", bg="#FFFFFF",
                     fg=COLORS["text_light"],
                     font=("Microsoft YaHei", 10)).pack(pady=20)
            self.after(50, self._update_task_scrollbar)
            self.after(50, self._update_main_scrollbar)
            return

        for s in self.cfg["schedules"]:
            var = tk.BooleanVar(value=False)
            self._task_vars.append(var)
            row = tk.Frame(self.task_inner, bg="#FFFFFF")
            row.pack(fill="x", pady=1)
            cb = tk.Checkbutton(row, text="", variable=var, bg="#FFFFFF")
            cb.pack(side="left", padx=(6, 4))
            tk.Label(row, text=self._format_schedule_desc(s), bg="#FFFFFF",
                     fg=COLORS["text"], font=("Microsoft YaHei", 10),
                     anchor="w").pack(side="left", fill="x", padx=(0, 4))
        self.after(50, self._update_task_scrollbar)
        self.after(50, self._update_main_scrollbar)

    def _add_schedule(self):
        selected = [name for name, var in self._acct_vars.items() if var.get()]
        if not selected:
            messagebox.showwarning("提示", "请至少选择一个账号", parent=self)
            return

        hour = self.hour_var.get().zfill(2)
        minute = self.minute_var.get().zfill(2)
        try:
            int(hour), int(minute)
        except ValueError:
            messagebox.showerror("错误", "时间格式不正确", parent=self)
            return

        mode = self.mode_var.get()
        schedule = {
            "schedule_type": mode,
            "time": f"{hour}:{minute}",
            "accounts": selected,
            "scheduler_groups": self.scheduler_var.get().strip(),
        }

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

        self._load_list()
        self.status_var.set("已添加")
        self._update_status()

    def _delete_selected(self):
        indices = [i for i, v in enumerate(self._task_vars) if v.get()]
        if not indices:
            return
        if not messagebox.askyesno("确认删除", f"确定删除 {len(indices)} 个任务？", parent=self):
            return
        for idx in sorted(indices, reverse=True):
            self.cfg["schedules"].pop(idx)
        save_scheduler_config(self.cfg)
        self._load_list()
        self.status_var.set(f"已删除 {len(indices)} 个任务")
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
        if not self.gui.scheduler_running:
            if not self.cfg["schedules"]:
                messagebox.showwarning("警告", "没有设置任何定时任务", parent=self)
                return
            self.gui.scheduler_running = True
            self.gui.scheduler_thread = threading.Thread(target=self.gui._scheduler_loop, daemon=True)
            self.gui.scheduler_thread.start()
            self.toggle_btn.config(text="停止定时器", bg="#E74C3C", activebackground="#C0392B")
            self.gui._log("定时器已启动")
            # 缩到托盘，不在任务栏显示
            if self.cfg.get("settings", {}).get("auto_minimize", True):
                self.gui.root.after(300, self.gui._minimize_to_tray)
        else:
            self.gui.scheduler_running = False
            self.toggle_btn.config(text="启动定时器", bg="#52C41A", activebackground="#389E0D")
            self.gui._log("定时器已停止")
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
        if self.gui.scheduler_running:
            self.status_var.set("定时器运行中")
        else:
            n = len(self.cfg["schedules"])
            self.status_var.set(f"已设置 {n} 个任务" if n else "暂无任务")

    def refresh_btn(self):
        if self.gui.scheduler_running:
            self.toggle_btn.config(text="停止定时器", bg="#E74C3C", activebackground="#C0392B")
        else:
            self.toggle_btn.config(text="启动定时器", bg="#52C41A", activebackground="#389E0D")
        self._update_status()


# ============================================================
# GenshinAutoToolGUI - 主界面（从 pyc 反汇编重建）
# ============================================================

class GenshinAutoToolGUI:
    """原神多账号自动化一条龙 v5.1 - 主界面"""

    def __init__(self, root):
        self.root = root
        self.root.title("GenshinAutoTool v5.2")
        self.root.geometry("960x600")
        self.root.minsize(700, 500)
        self.root.configure(bg=COLORS["bg"])

        self.cfg = load_config()
        self.worker = None
        self.log_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()  # 暂停事件：set=运行, clear=暂停
        self.pause_event.set()  # 初始为运行状态
        self.running = False
        self.paused = False

        # 调度器相关（孤儿方法需要）
        self.scheduler_running = False
        self.scheduler_thread = None
        self._shutdown_pending = False

        # 热键相关（孤儿方法需要）
        self._hotkey_stop_registered = None
        self._hotkey_pause_registered = None

        # 系统托盘
        self.tray = None
        self._quitting = False
        if HAS_TRAY:
            self._setup_tray()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._setup_styles()
        self._build_ui()
        self._load_accounts()
        self._poll_log()
        self._register_hotkey()

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
                             text="   原神多账号自动化一条龙",
                             bg=COLORS["primary_dark"],
                             fg=COLORS["text_white"],
                             font=("Microsoft YaHei", 14, "bold"))
        title_lbl.pack(side="left")

        version_lbl = tk.Label(self.title_bar,
                               text=" v5.2 ",
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

        self.title_scheduler_btn = tk.Label(self.title_bar, text="定时计划", cursor="hand2",
                                            bg=COLORS["primary_dark"], fg=COLORS["text_white"],
                                            font=("Microsoft YaHei", 10))
        self.title_scheduler_btn.pack(side="right", padx=1)
        self.title_scheduler_btn.bind("<Button-1>", lambda e: self._open_scheduler())

        self.title_settings_btn = tk.Label(self.title_bar, text="设置", cursor="hand2",
                                           bg=COLORS["primary_dark"], fg=COLORS["text_white"],
                                           font=("Microsoft YaHei", 10))
        self.title_settings_btn.pack(side="right", padx=1)
        self.title_settings_btn.bind("<Button-1>", lambda e: self._open_settings())

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
            type_label = "直接启动" if acc_type == "direct" else "胡桃启动"
            type_color = (COLORS["primary"]
                          if acc_type == "direct"
                          else COLORS["warning"])

            name_lbl = tk.Label(info_frame, text=name,
                                bg=COLORS["panel_bg"],
                                fg=COLORS["text"],
                                font=("Microsoft YaHei", 10, "bold"))
            name_lbl.pack(anchor="w")

            detail = (f"{type_label}  |  配置: "
                      f"{acc.get('config_name', '')}")
            if acc_type == "hutao":
                detail += (f"  |  胡桃: "
                           f"{acc.get('hutao_account', '')}")

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
            self._log("设置已保存")

    def _open_scheduler(self):
        """打开定时计划对话框"""
        SchedulerDialog(self.root, self)

    def _auto_start_scheduler(self):
        """开机自启动：静默启动定时器（不弹警告）"""
        cfg = load_scheduler_config()
        if not cfg.get("schedules"):
            return  # 没有任务，不启动
        self.scheduler_running = True
        self.scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self.scheduler_thread.start()
        # 开机自启 → 最小化到托盘
        self._minimize_to_tray()


    def _scheduler_loop(self):
        """定时器后台线程：每分钟检查一次时间，到点直接启动主程序一条龙
        支持三种模式: daily(每天), weekly(每周), once(一次性)
        """
        last_triggered = {}  # key=任务索引, value=最后触发的日期

        while self.scheduler_running:
            cfg = load_scheduler_config()
            now = datetime.now()
            current_time = now.strftime("%H:%M")
            current_date = now.strftime("%Y-%m-%d")
            current_weekday = now.isoweekday()  # 1=周一, 7=周日

            for i, schedule in enumerate(cfg["schedules"]):
                if schedule["time"] != current_time:
                    continue

                stype = schedule.get("schedule_type", "daily")

                # ---- 一次性模式 ----
                if stype == "once":
                    sched_date = schedule.get("date", "")
                    if sched_date != current_date:
                        continue
                    # 检查是否已执行过
                    if schedule.get("last_run") == current_date:
                        continue
                    schedule["last_run"] = current_date
                    save_scheduler_config(cfg)

                # ---- 每周模式 ----
                elif stype == "weekly":
                    wds = schedule.get("weekdays", [])
                    if current_weekday not in wds:
                        continue
                    if last_triggered.get(i) == current_date:
                        continue

                # ---- 指定日期模式 ----
                elif stype == "dates":
                    sched_dates = schedule.get("dates", [])
                    if current_date not in sched_dates:
                        continue
                    if last_triggered.get(i) == current_date:
                        continue

                # ---- 每天模式 ----
                else:  # daily
                    if last_triggered.get(i) == current_date:
                        continue

                last_triggered[i] = current_date

                accts = schedule.get("accounts", [])
                sched_groups = schedule.get("scheduler_groups", "")

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

                    self._start()

                self.root.after(0, trigger)

            time.sleep(60)

    def _start(self):
        sel = self._get_selected()
        if not sel:
            messagebox.showwarning("提示", "请至少选择一个账号")
            return

        # 检查依赖
        if not HAS_GW or not HAS_PA:
            r = messagebox.askyesno("依赖提示",
                                    "pygetwindow/pyautogui 未安装，"
                                    "胡桃功能将不可用。\n继续？")
            if not r:
                return

        self.running = True
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
        self._log(f"GenshinAutoTool v5.2  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self._log(f"选中: {', '.join(sel)}")
        self._log("=" * 55)

        selected_accounts = [a for a in self.cfg.get("accounts", [])
                             if a["name"] in sel]
        self.worker = WorkerThread(selected_accounts, self.log_queue, self.stop_event, self.pause_event)
        self.worker.start()

        # 自动最小化窗口，避免挡住游戏画面
        if self.cfg.get("settings", {}).get("auto_minimize", True):
            self.root.iconify()

    def _stop(self):
        if not self.running:
            return
        self._log("正在停止...")
        self.stop_event.set()
        self.pause_event.set()  # 取消暂停以便线程能退出
        self.paused = False
        self.status.config(text="正在停止...")
        self.stop_btn.config(state="disabled")
        self.pause_btn.config(state="disabled")
        self.root.deiconify()  # 恢复窗口

    def _pause_toggle(self):
        """暂停/继续切换"""
        if not self.running:
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

    def _resume(self):
        self.paused = False
        self.pause_event.set()  # 恢复工作线程
        self._log("任务继续")
        self.status.config(text="运行中...")
        self.pause_btn.config(text="⏸ 暂停", bg="#F0AD4E",
                              activebackground="#EC971F")

    # ------------------------------------------------------------------
    # 日志 & 轮询
    # ------------------------------------------------------------------

    def _poll_log(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
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
                    self._log(msg)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log)

    def _log(self, msg):
        self.log_area.config(state="normal")
        self.log_area.insert("end", msg + "\n")
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
        self.paused = False
        self.root.deiconify()  # 恢复窗口
        self.start_btn.config(state="normal", bg=COLORS["primary"])
        self.pause_btn.config(state="disabled", bg="#E0E4E8",
                              fg=COLORS["text_light"])
        self.stop_btn.config(state="disabled", bg="#E8E8E8",
                             fg=COLORS["text"])
        self.status.config(text="就绪 | 任务已完成或被中断")
        self._log(f"结束: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

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
        """注册全局停止热键和暂停热键"""
        try:
            import keyboard
            hotkey_stop = self.cfg.get("hotkeys", {}).get("stop", "ctrl+shift+q")
            hotkey_pause = self.cfg.get("hotkeys", {}).get("pause", "ctrl+shift+p")
            keyboard.add_hotkey(hotkey_stop, self._hotkey_stop)
            keyboard.add_hotkey(hotkey_pause, self._hotkey_pause)
            self._hotkey_stop_registered = hotkey_stop
            self._hotkey_pause_registered = hotkey_pause
        except ImportError:
            pass

    def _unregister_hotkey(self):
        """注销全局热键"""
        try:
            import keyboard
            if hasattr(self, '_hotkey_stop_registered') and self._hotkey_stop_registered:
                keyboard.remove_hotkey(self._hotkey_stop_registered)
            if hasattr(self, '_hotkey_pause_registered') and self._hotkey_pause_registered:
                keyboard.remove_hotkey(self._hotkey_pause_registered)
        except Exception:
            pass

    def _hotkey_stop(self):
        """热键触发的停止"""
        if self.running:
            self._log(f"热键停止 ({self._hotkey_stop_registered})")
            self._stop()

    def _hotkey_pause(self):
        """热键触发的暂停/继续"""
        if self.running:
            self._log(f"热键暂停/继续 ({self._hotkey_pause_registered})")
            self._pause_toggle()

    # ------------------------------------------------------------------
    # 系统托盘（pystray）
    # ------------------------------------------------------------------

    def _setup_tray(self):
        """初始化 pystray 托盘图标，并启动后台守护线程"""
        try:
            icon_path = _gen_icon()
            image = Image.open(icon_path) if icon_path and os.path.exists(icon_path) else self._tray_default_image()

            menu = pystray.Menu(
                pystray.MenuItem("显示窗口", self._tray_show, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("开始", self._tray_start),
                pystray.MenuItem("暂停", self._tray_pause, checked=lambda item: self.paused),
                pystray.MenuItem("停止", self._tray_stop),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("定时器", self._tray_scheduler_toggle, checked=lambda item: self.scheduler_running),
                pystray.MenuItem("取消自动关机", self._tray_cancel_shutdown, checked=lambda item: self._shutdown_pending),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("设置", self._tray_settings),
                pystray.MenuItem("退出", self._tray_quit),
            )
            self.tray = pystray.Icon("genshin_onedragon", image, "原神一条龙 v5.2", menu)
            threading.Thread(target=self.tray.run, daemon=True).start()
        except Exception:
            import traceback
            err = traceback.format_exc()
            log_dir = os.path.dirname(os.path.abspath(__file__))
            with open(os.path.join(log_dir, "tray_error.log"), "w", encoding="utf-8") as f:
                f.write(err)
            messagebox.showerror("托盘初始化失败", err[:500])
            self.tray = None

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
        self._quitting = True
        self.scheduler_running = False
        self.running = False
        if self.stop_event:
            self.stop_event.set()
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    def _on_close(self):
        """点 X 关闭 → 缩到托盘，无托盘时直接退出"""
        if self.tray:
            self.root.withdraw()
        else:
            self._tray_quit_in_main()

    def _minimize_to_tray(self):
        if self.tray:
            self.root.withdraw()
        else:
            self.root.iconify()

    def _tray_start(self):
        self.root.after(0, self._start)

    def _tray_pause(self):
        self.root.after(0, self._pause_toggle)

    def _tray_stop(self):
        self.root.after(0, self._stop)

    def _tray_scheduler_toggle(self):
        def _toggle():
            if self.scheduler_running:
                self.scheduler_running = False
                self._log("定时器已停止")
            else:
                self.scheduler_running = True
                self.scheduler_thread = threading.Thread(
                    target=self._scheduler_loop, daemon=True)
                self.scheduler_thread.start()
                self._log("定时器已启动")
        self.root.after(0, _toggle)

    def _tray_cancel_shutdown(self):
        self.root.after(0, self._cancel_shutdown)

    def _tray_settings(self):
        self.root.after(0, self._open_settings)

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


def main():
    # 单实例检查
    import msvcrt
    lock_path = os.path.join(tempfile.gettempdir(), "GenshinAutoTool.lock")
    try:
        _lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_TRUNC)
        msvcrt.locking(_lock_fd, msvcrt.LK_NBLCK, 1)
    except (IOError, OSError):
        messagebox.showwarning("原神一条龙", "程序已在运行中。")
        return

    root = tk.Tk()
    root.withdraw()  # 隐藏小窗口，等界面就绪再显示
    ico = _gen_icon()
    if ico:
        root.iconbitmap(ico)
    app = GenshinAutoToolGUI(root)

    # 开机自启动：自动启动定时器
    if "--auto-start-scheduler" in sys.argv:
        app.root.after(500, app._auto_start_scheduler)

    root.deiconify()
    root.mainloop()


if __name__ == "__main__":
    main()

