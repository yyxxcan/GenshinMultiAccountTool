#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GenshinAutoScheduler - 原神一条龙定时启动器
===========================================
一个简单的 GUI 程序，用来定时启动 GenshinAutoTool。
"""

import os, sys, json, time, subprocess, threading
from datetime import datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# ============================================================
# 常量
# ============================================================
SCRIPT_DIR = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "scheduler_config.json"

# ============================================================
# 配置管理
# ============================================================
def load_config():
    defaults = {
        "exe_path": "GenshinAutoTool_NoAccounts.exe",
        "schedules": []
    }
    if CONFIG_PATH.is_file():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
        for key, default_val in defaults.items():
            if key not in user_cfg:
                user_cfg[key] = default_val
        return user_cfg
    return defaults

def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

# ============================================================
# 主窗口
# ============================================================
class GenshinAutoScheduler:
    def __init__(self, root):
        self.root = root
        self.root.title("原神一条龙定时启动器")
        self.root.geometry("500x520")
        self.root.minsize(400, 400)
        self.root.configure(bg="#F5F8FC")

        self.cfg = load_config()
        self.schedule_thread = None
        self.running = False

        self._build_ui()
        self._load_schedules()

    def _build_ui(self):
        # ---- 顶部标题 ----
        title_bar = tk.Frame(self.root, bg="#F5F8FC")
        title_bar.pack(side="top", fill="x", padx=20, pady=(15, 5))
        tk.Label(title_bar, text="原神一条龙定时启动器", font=("Microsoft YaHei", 16, "bold"),
                 bg="#F5F8FC", fg="#2C3E50").pack()
        tk.Label(title_bar, text="设置定时任务，自动启动 GenshinAutoTool", font=("Microsoft YaHei", 10),
                 bg="#F5F8FC", fg="#7F8C8D").pack()

        # ---- 控制栏（固定） ----
        ctrl_bar = tk.Frame(self.root, bg="#E8F0FE", height=38)
        ctrl_bar.pack(side="top", fill="x")
        ctrl_bar.pack_propagate(False)

        self.status_var = tk.StringVar(value="就绪")
        tk.Label(ctrl_bar, textvariable=self.status_var, font=("Microsoft YaHei", 9),
                 bg="#E8F0FE", fg="#2C3E50").pack(side="left", padx=12, pady=9)

        self.start_stop_btn = tk.Button(ctrl_bar, text="启动定时器", command=self._toggle_scheduler,
                                        bg="#52C41A", fg="#FFFFFF", activebackground="#389E0D",
                                        relief="flat", font=("Microsoft YaHei", 10), padx=20, pady=2)
        self.start_stop_btn.pack(side="right", padx=12, pady=5)

        # ---- 可滚动内容区 ----
        scroll_container = tk.Frame(self.root, bg="#F5F8FC")
        scroll_container.pack(side="top", fill="both", expand=True, padx=20, pady=(10, 15))

        self.content_canvas = tk.Canvas(scroll_container, bg="#FFFFFF",
                                         highlightthickness=0, relief="flat")
        content_scroll = tk.Scrollbar(scroll_container, orient="vertical",
                                       command=self.content_canvas.yview)
        self.content_canvas.configure(yscrollcommand=content_scroll.set)

        self.content_canvas.pack(side="left", fill="both", expand=True)
        content_scroll.pack(side="right", fill="y")

        self.content_frame = tk.Frame(self.content_canvas, bg="#FFFFFF")
        self.content_canvas.create_window((0, 0), window=self.content_frame, anchor="nw",
                                           tags="content_inner")

        self.content_frame.bind("<Configure>",
            lambda e: self.content_canvas.configure(
                scrollregion=self.content_canvas.bbox("all")))

        def _on_canvas_width(event):
            self.content_canvas.itemconfig("content_inner",
                width=event.width)
        self.content_canvas.bind("<Configure>", _on_canvas_width)

        def _on_mousewheel(event):
            if not self.content_canvas.winfo_exists():
                return
            bbox = self.content_canvas.bbox("all")
            if bbox and bbox[3] > self.content_canvas.winfo_height():
                self.content_canvas.yview_scroll(
                    int(-1 * (event.delta / 120)), "units")
        self.content_canvas.bind("<MouseWheel>", _on_mousewheel)

        # ---- 内容：添加任务表单 ----
        add_section = tk.LabelFrame(self.content_frame, text="添加新定时任务",
                                     font=("Microsoft YaHei", 11, "bold"),
                                     bg="#FFFFFF", fg="#2C3E50",
                                     padx=12, pady=8)
        add_section.pack(fill="x", padx=10, pady=(10, 5))

        # 程序路径
        tk.Label(add_section, text="GenshinAutoTool 程序路径:",
                 bg="#FFFFFF", fg="#2C3E50").pack(anchor="w")
        path_frame = tk.Frame(add_section, bg="#FFFFFF")
        path_frame.pack(fill="x", pady=(2, 8))
        self.exe_var = tk.StringVar(value=self.cfg["exe_path"])
        tk.Entry(path_frame, textvariable=self.exe_var, width=40).pack(
            side="left", fill="x", expand=True)
        tk.Button(path_frame, text="浏览...", command=self._browse_exe,
                  bg="#E0E0E0", fg="#2C3E50", relief="flat", padx=10).pack(
            side="left", padx=(5, 0))

        # 时间选择 + 重复 + 添加按钮 同行
        action_row = tk.Frame(add_section, bg="#FFFFFF")
        action_row.pack(fill="x")

        tk.Label(action_row, text="时:", bg="#FFFFFF", fg="#2C3E50").pack(side="left")
        self.hour_var = tk.StringVar(value="08")
        tk.Spinbox(action_row, from_=0, to=23, width=3, textvariable=self.hour_var,
                   format="%02.0f", state="readonly").pack(side="left", padx=(2, 8))

        tk.Label(action_row, text="分:", bg="#FFFFFF", fg="#2C3E50").pack(side="left")
        self.minute_var = tk.StringVar(value="00")
        tk.Spinbox(action_row, from_=0, to=59, width=3, textvariable=self.minute_var,
                   format="%02.0f", state="readonly").pack(side="left", padx=(2, 12))

        self.repeat_var = tk.BooleanVar(value=True)
        tk.Checkbutton(action_row, text="每天重复", variable=self.repeat_var,
                       bg="#FFFFFF", fg="#2C3E50",
                       selectcolor="#FFFFFF").pack(side="left", padx=(0, 12))

        tk.Button(action_row, text="添加", command=self._add_schedule,
                  bg="#4A90D9", fg="#FFFFFF", activebackground="#3A7BC8",
                  relief="flat", font=("Microsoft YaHei", 10),
                  padx=16, pady=2).pack(side="left")

        # ---- 内容：任务列表 ----
        list_section = tk.LabelFrame(self.content_frame, text="已设置的定时任务",
                                      font=("Microsoft YaHei", 11, "bold"),
                                      bg="#FFFFFF", fg="#2C3E50",
                                      padx=12, pady=8)
        list_section.pack(fill="both", expand=True, padx=10, pady=(5, 10))

        self.listbox = tk.Listbox(list_section,
                                  bg="#FFFFFF", fg="#2C3E50",
                                  selectbackground="#D6E8FA",
                                  selectforeground="#2C3E50",
                                  relief="flat", bd=0,
                                  font=("Microsoft YaHei", 10))
        self.listbox.pack(fill="both", expand=True)

        tk.Button(list_section, text="删除选中", command=self._delete_selected,
                  bg="#E74C3C", fg="#FFFFFF", activebackground="#C0392B",
                  relief="flat", font=("Microsoft YaHei", 9),
                  padx=12, pady=2).pack(pady=(8, 0))

    def _browse_exe(self):
        path = filedialog.askopenfilename(filetypes=[("EXE 文件", "*.exe")])
        if path:
            self.exe_var.set(path)

    def _add_schedule(self):
        exe = self.exe_var.get().strip()
        if not exe:
            messagebox.showerror("错误", "请选择程序路径")
            return

        hour = self.hour_var.get().zfill(2)
        minute = self.minute_var.get().zfill(2)
        repeat = self.repeat_var.get()

        # 检查时间格式
        try:
            int(hour), int(minute)
        except ValueError:
            messagebox.showerror("错误", "时间格式不正确")
            return

        schedule = {
            "time": f"{hour}:{minute}",
            "repeat": repeat,
            "exe": exe
        }

        self.cfg["exe_path"] = exe
        self.cfg["schedules"].append(schedule)
        save_config(self.cfg)

        # 更新列表
        desc = f"{hour}:{minute} - {'每天' if repeat else '仅一次'} - {os.path.basename(exe)}"
        self.listbox.insert(tk.END, desc)
        self.status_var.set(f"已添加任务: {hour}:{minute}")

    def _load_schedules(self):
        self.listbox.delete(0, tk.END)
        for s in self.cfg["schedules"]:
            desc = f"{s['time']} - {'每天' if s.get('repeat', True) else '仅一次'} - {os.path.basename(s['exe'])}"
            self.listbox.insert(tk.END, desc)

    def _delete_selected(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        self.listbox.delete(idx)
        self.cfg["schedules"].pop(idx)
        save_config(self.cfg)
        self.status_var.set("已删除选中任务")

    def _toggle_scheduler(self):
        if not self.running:
            if not self.cfg["schedules"]:
                messagebox.showwarning("警告", "没有设置任何定时任务")
                return
            self.running = True
            self.start_stop_btn.config(text="停止定时器", bg="#E74C3C", activebackground="#C0392B")
            self.status_var.set("定时器运行中...")
            self.schedule_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
            self.schedule_thread.start()
        else:
            self.running = False
            self.start_stop_btn.config(text="启动定时器", bg="#52C41A", activebackground="#389E0D")
            self.status_var.set("定时器已停止")

    def _scheduler_loop(self):
        while self.running:
            now = datetime.now()
            current_time = now.strftime("%H:%M")
            current_date = now.strftime("%Y-%m-%d")

            for schedule in self.cfg["schedules"]:
                if schedule["time"] == current_time:
                    # 检查重复
                    if schedule.get("repeat", True):
                        pass  # 每天执行
                    else:
                        # 仅一次，检查是否已执行过
                        last_run = schedule.get("last_run")
                        if last_run == current_date:
                            continue  # 今天已执行过
                        schedule["last_run"] = current_date
                        save_config(self.cfg)

                    # 启动程序
                    exe_path = schedule["exe"]
                    if os.path.isfile(exe_path):
                        try:
                            subprocess.Popen([exe_path], shell=True)
                            self.root.after(0, lambda: self.status_var.set(f"已启动: {os.path.basename(exe_path)}"))
                        except Exception as e:
                            self.root.after(0, lambda: self.status_var.set(f"启动失败: {e}"))
                    else:
                        self.root.after(0, lambda: self.status_var.set(f"文件不存在: {exe_path}"))

            time.sleep(60)  # 每分钟检查一次

# ============================================================
# 入口
# ============================================================
def main():
    root = tk.Tk()
    app = GenshinAutoScheduler(root)
    root.mainloop()

if __name__ == "__main__":
    main()
