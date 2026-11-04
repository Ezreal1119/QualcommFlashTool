#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import shlex
import threading
import subprocess
from shutil import which

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

ADB_PATH = os.environ.get(
    "ADB_PATH",
    "/Users/patrickxu/Library/Android/sdk/platform-tools/adb"
)

DEFAULT_EDL_DIR_CANDIDATES = [
    os.path.expanduser("~/edl-venv/bin"),
    "/usr/local/bin",
    "/opt/homebrew/bin",
    "/usr/bin",
]


def run_command(cmd, live_cb=None, cwd=None, shell=False):
    """
    Run a command and stream stdout to live_cb.
    Returns the process return code.
    """
    env = os.environ.copy()
    # 打包环境常见缺 PATH，把常见位置都补上
    extra_paths = [
        "/usr/local/bin",
        "/opt/homebrew/bin",
        "/Users/patrickxu/Library/Android/sdk/platform-tools",
    ]
    env["PATH"] = ":".join([env.get("PATH", ""), *extra_paths])

    try:
        proc = subprocess.Popen(
            cmd if shell else (cmd if isinstance(cmd, (list, tuple)) else shlex.split(cmd)),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=cwd,
            shell=shell,
            env=env,
        )
    except Exception as e:
        if live_cb:
            live_cb(f"[ERROR] {e}\n")
        return 1

    if live_cb and proc.stdout:
        for line in proc.stdout:
            live_cb(line)
    return proc.wait()


class Row:
    def __init__(self, parent, remove_cb, raw_path="", patch_path=""):
        self.frame = ttk.Frame(parent)

        # Full paths kept here; entry shows only basenames
        self.raw_full = raw_path if raw_path else ""
        self.patch_full = patch_path if patch_path else ""

        self.raw_var = tk.StringVar(value=os.path.basename(self.raw_full) if self.raw_full else "")
        self.patch_var = tk.StringVar(value=os.path.basename(self.patch_full) if self.patch_full else "")

        ttk.Label(self.frame, text="rawprogram:").grid(row=0, column=0, padx=4, pady=2, sticky="e")
        self.raw_entry = ttk.Entry(self.frame, textvariable=self.raw_var, width=24, state="readonly")
        self.raw_entry.grid(row=0, column=1, padx=4, pady=2, sticky="we")
        ttk.Button(self.frame, text="Browse", command=self.browse_raw).grid(row=0, column=2, padx=4, pady=2)

        ttk.Label(self.frame, text="patch:").grid(row=0, column=3, padx=4, pady=2, sticky="e")
        self.patch_entry = ttk.Entry(self.frame, textvariable=self.patch_var, width=24, state="readonly")
        self.patch_entry.grid(row=0, column=4, padx=4, pady=2, sticky="we")
        ttk.Button(self.frame, text="Browse", command=self.browse_patch).grid(row=0, column=5, padx=4, pady=2)

        ttk.Button(self.frame, text="Remove", command=lambda: remove_cb(self)).grid(row=0, column=6, padx=6)

        # ---- Status label (NEW) ----
        self.status_var = tk.StringVar(value="PENDING")
        self.status_label = ttk.Label(self.frame, textvariable=self.status_var, foreground="#888888")
        self.status_label.grid(row=0, column=7, padx=8, sticky="w")

    def grid(self, **kw):
        self.frame.grid(**kw)

    def destroy(self):
        self.frame.destroy()

    def set_status(self, status: str):
        # Map status to color
        status = (status or "").upper()
        color_map = {
            "PENDING": "#888888",
            "RUNNING": "#CC8A00",  # amber
            "SUCCESS": "#068006",  # green
            "FAIL": "#C30010",     # red
            "WARN": "#B36B00",     # dark amber
        }
        self.status_var.set(status)
        try:
            self.status_label.configure(foreground=color_map.get(status, "#888888"))
        except tk.TclError:
            pass

    def browse_raw(self):
        # Allow any *.xml, but enforce name contains "rawprogram"
        p = filedialog.askopenfilename(
            title="Select rawprogram*.xml",
            filetypes=[("XML files", "*.xml"), ("All files", "*")],
        )
        if not p:
            return
        name = os.path.basename(p).lower()
        if "rawprogram" not in name or not p.lower().endswith(".xml"):
            messagebox.showerror("Invalid file", "Please select an XML whose name contains 'rawprogram'.")
            return
        self.raw_full = p
        self.raw_var.set(os.path.basename(p))
        self.set_status("PENDING")

    def browse_patch(self):
        # Allow any *.xml, but enforce name contains "patch"
        p = filedialog.askopenfilename(
            title="Select patch*.xml",
            filetypes=[("XML files", "*.xml"), ("All files", "*")],
        )
        if not p:
            return
        name = os.path.basename(p).lower()
        if "patch" not in name or not p.lower().endswith(".xml"):
            messagebox.showerror("Invalid file", "Please select an XML whose name contains 'patch'.")
            return
        self.patch_full = p
        self.patch_var.set(os.path.basename(p))
        self.set_status("PENDING")

    def valid(self):
        return os.path.isfile(self.raw_full) and os.path.isfile(self.patch_full)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("QualComm Flash Tool - Patrick Xu")
        self.geometry("1180x760")
        self.after(0, self._center_on_screen)
        self._proc = None
        self.rows = []

        # --- Top bar ---
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=8)

        ttk.Label(top, text="EDL directory:").grid(row=0, column=0, sticky="e")
        self.edl_dir_var = tk.StringVar(value=self._guess_edl_dir())
        self.edl_dir_entry = ttk.Entry(top, textvariable=self.edl_dir_var, width=70, state="disabled")
        self.edl_dir_entry.grid(row=0, column=1, sticky="we", padx=6)
        ttk.Button(top, text="Browse", command=self.browse_edl_dir).grid(row=0, column=2, padx=4)

        ttk.Label(top, text="Firehose ELF:").grid(row=1, column=0, sticky="e", pady=4)
        self.loader_var = tk.StringVar()
        self.loader_full = ""
        self.loader_entry = ttk.Entry(top, textvariable=self.loader_var, width=70, state="readonly")
        self.loader_entry.grid(row=1, column=1, sticky="we", padx=6)
        ttk.Button(top, text="3. Browse", command=self.browse_loader).grid(row=1, column=2, padx=4)

        ttk.Label(top, text="Package:").grid(row=2, column=0, sticky="e", pady=2)
        self.pkg_var = tk.StringVar(value="")
        self.pkg_label = ttk.Label(top, textvariable=self.pkg_var, anchor="w")
        self.pkg_label.grid(row=2, column=1, sticky="we", padx=6, columnspan=1)

        ttk.Label(top, text="Memory:").grid(row=0, column=3, padx=(20, 4), sticky="e")
        self.mem_var = tk.StringVar(value="ufs")
        self.mem_combo = ttk.Combobox(top, textvariable=self.mem_var, values=["ufs", "emmc"], width=10,
                                      state="readonly")
        self.mem_combo.grid(row=0, column=4, sticky="w")

        # --- Action buttons ---
        actions = ttk.Frame(self)
        actions.pack(fill="x", padx=10, pady=(0, 6))
        ttk.Button(actions, text="1. Enter EDL Mode", command=self.adb_reboot_edl).pack(side="left")
        ttk.Button(actions, text="2. Check EDL Status", command=self.check_usb_ioreg).pack(side="left", padx=6)
        ttk.Button(actions, text="ADB Devices", command=self.adb_devices).pack(side="right")
        # ttk.Button(actions, text="Verify Memory", command=self.verify_memory).pack(side="left", padx=6)
        # ttk.Button(actions, text="Reset", command=self.edl_reset).pack(side="left", padx=6)

        # --- Rawprogram / Patch section ---
        mid = ttk.LabelFrame(self, text="Rawprogram/Patch Pairs")
        mid.pack(fill="both", expand=False, padx=10, pady=6)
        self.rows_container = ttk.Frame(mid)
        self.rows_container.pack(fill="x", padx=6, pady=6)
        controls = ttk.Frame(mid)
        controls.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(controls, text="+ Add Row", command=self.add_row).pack(side="left")

        self.add_row()

        # --- Run bar ---
        runbar = ttk.Frame(self)
        runbar.pack(fill="x", padx=10, pady=6)
        self.run_btn = ttk.Button(runbar, text="4. Start Flash", command=self.start)
        self.run_btn.pack(side="left")
        # self.stop_btn = ttk.Button(runbar, text="Stop", command=self.stop, state="disabled")
        # self.stop_btn.pack(side="left", padx=6)

        # --- Log ---
        self.log = ScrolledText(self, height=22)
        self.log.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        # self._log("Ready.\n")

    def _center_on_screen(self):
        # 先让 Tk 计算好实际尺寸（尤其是 macOS/Retina 下很重要）
        self.update_idletasks()
        w = self.winfo_width() or 1180
        h = self.winfo_height() or 760
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ---------- helpers ----------
    def _append_log(self, s: str):
        try:
            self.log.insert("end", s)
            self.log.see("end")
        except tk.TclError:
            pass

    def _log(self, s: str):
        # make logging responsive/thread-safe
        try:
            self.log.after(0, self._append_log, s)
        except tk.TclError:
            pass

    def _guess_edl_dir(self):
        for d in DEFAULT_EDL_DIR_CANDIDATES:
            edl_path = os.path.join(d, "edl")
            if os.path.isfile(edl_path) and os.access(edl_path, os.X_OK):
                return d
        w = which("edl")
        if w:
            return os.path.dirname(w)
        return DEFAULT_EDL_DIR_CANDIDATES[0]

    def edl_exec(self):
        """
        Return the EDL command prefix as a list.
        Uses 'sudo -n' so it doesn't block for a password (works with your sudoers rule).
        """
        edl_bin = os.path.join(os.path.expanduser(self.edl_dir_var.get().strip()), "edl")
        return ["sudo", "-n", edl_bin]

    def browse_edl_dir(self):
        p = filedialog.askdirectory(title="Select folder that contains 'edl' executable")
        if p:
            self.edl_dir_var.set(p)

    def browse_loader(self):
        # 允许任意 *.elf，但要求文件名包含 ddr
        p = filedialog.askopenfilename(
            title="Select firehose DDR ELF",
            filetypes=[("ELF files", "*.elf"), ("All files", "*")],
        )
        if not p:
            return
        base = os.path.basename(p).lower()
        if "ddr" not in base or not p.lower().endswith(".elf"):
            messagebox.showerror("Invalid file", "Please select a DDR firehose ELF (filename must contain 'ddr').")
            return

        # 1) 显示 ELF 文件名
        self.loader_full = p
        self.loader_var.set(os.path.basename(p))

        # 2) 在 UI 的 Package 行显示父目录名
        pkg_name = os.path.basename(os.path.dirname(p))
        self.pkg_var.set(pkg_name)  # 只在 UI 显示，不写到 console

        # 3) 自动按该目录填充 rawprogram/patch
        self.populate_rows_from_folder(os.path.dirname(p))

        # 4) 自动识别存储类型：仅在 console 输出 mem type，并自动切换下拉框
        threading.Thread(target=self._auto_detect_memory, daemon=True).start()

    def _auto_detect_memory(self):
        """选 ELF 后自动探测存储类型；仅在 console 输出结果，并自动切换下拉框。"""
        if not self.loader_full:
            return

        edl_cmd = self.edl_exec() + ["printgpt", "--loader", self.loader_full]

        detected = None
        try:
            proc = subprocess.Popen(edl_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            if proc.stdout:
                for line in proc.stdout:
                    low = line.strip().lower()
                    # 常见关键字：mem_type / MemoryName
                    if "mem_type" in low or "memoryname" in low:
                        if "ufs" in low:
                            detected = "ufs"
                            break
                        if "emmc" in low or "e-mmc" in low:
                            detected = "emmc"
                            break
            proc.wait()
        except Exception as e:
            self._log(f"[AUTO] mem_type detect error: {e}\n")
            return

        if detected:
            # 用主线程更新下拉框
            if self.mem_var.get().lower() != detected:
                self.after(0, self.mem_var.set, detected)
            self._log(f"[AUTO] mem_type: {detected.upper()}\n")
        else:
            self._log("[AUTO] mem_type: UNKNOWN\n")

    def add_row(self, raw="", patch=""):
        row = Row(self.rows_container, self.remove_row, raw, patch)
        row.grid(row=len(self.rows), column=0, sticky="we", pady=2)
        self.rows.append(row)

    def remove_row(self, row):
        row.destroy()
        self.rows.remove(row)
        for idx, r in enumerate(self.rows):
            r.grid(row=idx, column=0, sticky="we", pady=2)

    # ---------- action buttons ----------
    def adb_reboot_edl(self):
        threading.Thread(
            target=lambda: run_command(f"{shlex.quote(ADB_PATH)} reboot edl", live_cb=self._log, shell=True),
            daemon=True,
        ).start()

    def check_usb_ioreg(self):
        def _worker():
            cmd = 'ioreg -p IOUSB -l | grep -i "36872"'
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, shell=True)
                found = False
                if proc.stdout:
                    for line in proc.stdout:
                        if "idProduct" in line and "36872" in line:
                            found = True
                            break
                proc.wait()
                if found:
                    self._log("EDL mode Activated Successfully!\n")
                else:
                    self._log("No EDL device detected.\n")
            except Exception as e:
                self._log(f"[ERROR] {e}\n")

        threading.Thread(target=_worker, daemon=True).start()

    def adb_devices(self):
        if not os.path.isfile(ADB_PATH):
            self._log(f"[ERROR] adb not found at: {ADB_PATH}\n"
                      f"Set env ADB_PATH or install platform-tools.\n")
            return
        # 直接把 adb devices 的输出流到日志
        threading.Thread(
            target=lambda: run_command(f"{shlex.quote(ADB_PATH)} devices", live_cb=self._log, shell=True),
            daemon=True,
        ).start()
    # def verify_memory(self):
    #     if not self.loader_full:
    #         self._log("[Verify] No ELF set.\n")
    #         return
    #     edl_cmd = self.edl_exec() + ["printgpt", "--loader", self.loader_full]
    #     cmd = " ".join(shlex.quote(x) for x in edl_cmd) + ' | egrep -i "mem_type|MemoryName"'
    #     threading.Thread(
    #         target=lambda: run_command(cmd, live_cb=self._log, shell=True),
    #         daemon=True,
    #     ).start()

    def edl_reset(self):
        cmd = self.edl_exec() + ["reset"]
        threading.Thread(
            target=lambda: run_command(cmd, live_cb=self._log, shell=False),
            daemon=True,
        ).start()

    # ---------- flashing ----------
    def start(self):
        edl_prefix = self.edl_exec()
        edl_bin = edl_prefix[-1]
        loader = self.loader_full
        mem = (self.mem_var.get() or "").lower()

        if not (os.path.isfile(edl_bin) and os.access(edl_bin, os.X_OK)):
            messagebox.showerror("Error", f"'edl' not executable or not found at:\n{edl_bin}")
            return
        if not os.path.isfile(loader):
            messagebox.showerror("Error", f"ELF not found:\n{loader}")
            return
        if mem not in ("ufs", "emmc"):
            messagebox.showerror("Error", "Memory must be one of: ufs, emmc")
            return

        # 包含 row 对象，用于设置状态
        pairs = [(r, r.raw_full, r.patch_full) for r in self.rows if r.valid()]
        if not pairs:
            messagebox.showerror("Error", "No valid rawprogram/patch pairs.")
            return

        # 开始前把每行先置为 PENDING
        for r, _, _ in pairs:
            r.set_status("PENDING")

        self.run_btn.config(state="disabled")
        # self.stop_btn.config(state="normal")

        sector_choice = "Auto"
        t = threading.Thread(
            target=self._run_pairs,
            args=(edl_prefix, loader, mem, pairs),
            daemon=True,
        )
        t.start()

    # def stop(self):
    #     if self._proc and self._proc.poll() is None:
    #         try:
    #             self._proc.terminate()
    #             self._log("[INFO] Termination requested...\n")
    #         except Exception as e:
    #             self._log(f"[ERROR] {e}\n")

    # ---- 状态设置（NEW）----
    def _set_row_status(self, row_obj: Row, status: str):
        try:
            self.after(0, row_obj.set_status, status)
        except Exception as e:
            self._log(f"[WARN] set_status failed: {e}\n")

    def _build_sectorsize_args(self, sector_choice: str):
        if sector_choice in ("4096", "512"):
            return [f"--sectorsize=={sector_choice}"]
        return []

    def _looks_like_gpt_patch_failure(self, collected_lines):
        hay = "\n".join(collected_lines).lower()
        # Heuristics: presence of patch XML echo OR GPT header keywords
        keys = ["<patch", "lastuseablelba", "backup header", "partition array", "gpt"]
        return any(k in hay for k in keys)

    # 保留：未使用，但不改动你的其它代码
    def _run_fixgpt(self, edl_prefix, loader, mem, sector_choice):
        cmd = edl_prefix + ["fixgpt", "--loader", loader, "--memory", mem] + self._build_sectorsize_args(sector_choice)
        self._log(f"[AUTO] Running: {' '.join(shlex.quote(x) for x in cmd)}\n")
        try:
            rc = run_command(cmd, live_cb=self._log, shell=False)
        except Exception as e:
            self._log(f"[AUTO] fixgpt exception: {e}\n")
            return 1
        return rc

    def _run_pairs(self, edl_prefix, loader, mem, pairs):
        for idx, (row_obj, raw, patch) in enumerate(pairs, start=1):
            self._set_row_status(row_obj, "RUNNING")
            workdir = os.path.dirname(os.path.abspath(raw))

            raw_name = os.path.basename(raw)
            patch_name = os.path.basename(patch)
            loader_name = os.path.basename(loader)

            cmd = edl_prefix + [
                "qfil",
                raw_name,
                patch_name,
                ".",
                "--loader", loader_name,
                "--memory", mem
            ]

            self._log(
                f"\n=== [{idx}/{len(pairs)}] {' '.join(shlex.quote(x) for x in cmd)}\n"
                f"[cwd] {workdir}\n"
            )

            collected = []
            rc = 1
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd=workdir,
                )
                for line in self._proc.stdout:
                    collected.append(line.rstrip("\n"))
                    if len(collected) > 4000:
                        collected = collected[-2000:]
                    self._log(line)
                rc = self._proc.wait()
            except Exception as e:
                self._log(f"[ERROR] {e}\n")
                rc = 1
            finally:
                self._proc = None

            if rc == 0:
                self._log("[OK] exit code 0\n")
                self._set_row_status(row_obj, "SUCCESS")
                continue

            # 只要是 GPT/patch 的打印，直接当作成功继续
            if self._looks_like_gpt_patch_failure(collected):
                self._log("[INFO] qfil printed GPT/patch messages. Treating as normal.\n")
                self._set_row_status(row_obj, "SUCCESS")
                continue

            # 其余错误：标 FAIL 并停止后续
            self._log("[FAIL] exit code non-zero.\n")
            self._set_row_status(row_obj, "FAIL")
            break

        self.run_btn.config(state="normal")
        self._log("[AUTO] Flash finished. Sending reset...\n")
        cmd = self.edl_exec() + ["reset"]
        threading.Thread(
            target=lambda: run_command(cmd, live_cb=self._log, shell=False),
            daemon=True,
        ).start()
        # self.stop_btn.config(state="disabled")
    # ---------- auto-populate ----------
    def populate_rows_from_folder(self, folder: str):
        # Clear old rows
        for r in self.rows[:]:
            self.remove_row(r)

        try:
            entries = os.listdir(folder)
        except Exception as e:
            self._log(f"[INFO] Cannot list folder: {e}\n")
            self.add_row()
            return

        # rawprogram{i}.xml or rawprogram_unsparse{i}.xml
        rx = re.compile(r"^rawprogram(?:_unsparse)?(\d+)\.xml$", re.IGNORECASE)
        indices = sorted({int(m.group(1)) for name in entries if (m := rx.match(name))})

        if not indices:
            self._log("[INFO] No rawprogram*.xml found, add manually.\n")
            self.add_row()
            return

        max_index = max(indices)
        for i in range(max_index + 1):
            raw_a = os.path.join(folder, f"rawprogram{i}.xml")
            raw_b = os.path.join(folder, f"rawprogram_unsparse{i}.xml")
            raw = os.path.basename(raw_a) if os.path.isfile(raw_a) else (os.path.basename(raw_b) if os.path.isfile(raw_b) else "")

            patch_p = os.path.join(folder, f"patch{i}.xml")
            patch = os.path.basename(patch_p) if os.path.isfile(patch_p) else ""

            # 这里仍然保存 full path（便于其他功能），但显示/运行时会用 cwd+basename
            raw_full = os.path.join(folder, raw) if raw else ""
            patch_full = os.path.join(folder, patch) if patch else ""
            self.add_row(raw_full, patch_full)


if __name__ == "__main__":
    try:
        App().mainloop()
    except KeyboardInterrupt:
        pass