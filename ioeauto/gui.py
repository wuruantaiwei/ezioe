"""`run.py gui` - a small desktop window around the CLI (tkinter, no extra deps).

The window only edits ``data/config.json`` and starts/stops a worker thread that
calls the same code path as the command line, so nothing behaves differently
than a scheduled run. Closing the window can drop it to the tray when
``pystray`` + ``Pillow`` are installed.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import queue
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from . import cli, config as cfgmod, report

TITLE = "IOE tự luyện"


class _Writer:
    """File-like object pushing stdout into the log queue."""

    def __init__(self, log_queue: queue.Queue) -> None:
        self.queue = log_queue

    def write(self, text: str) -> int:
        if text:
            self.queue.put(text)
        return len(text)

    def flush(self) -> None:
        return None


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.path = cfgmod.CONFIG
        self.config = cfgmod.load(self.path)
        self.queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.tray = None

        root.title(TITLE)
        root.geometry("820x620")
        root.minsize(720, 520)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_accounts()
        self._build_settings()
        self._build_actions()
        self._build_log()
        self.refresh_accounts()
        self.root.after(150, self._drain)

    # ------------------------------------------------------------------ layout
    def _build_accounts(self) -> None:
        frame = ttk.LabelFrame(self.root, text="Tài khoản")
        frame.pack(fill="both", expand=True, padx=10, pady=(10, 6))
        columns = ("user", "round", "pending", "note")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", height=6)
        for key, label, width in (("user", "tài khoản", 220), ("round", "vòng", 90),
                                  ("pending", "bài phải làm", 110), ("note", "trạng thái", 330)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Double-1>", lambda _e: self.edit_account())

    def _build_settings(self) -> None:
        frame = ttk.LabelFrame(self.root, text="Thiết lập")
        frame.pack(fill="x", padx=10, pady=6)
        self.vars = {
            "rounds": tk.StringVar(value=str(self.config.get("rounds", 1))),
            "jobs": tk.StringVar(value=str(self.config.get("jobs", 1))),
            "max": tk.StringVar(value=str(self.config.get("max_rounds_per_day", 0))),
        }
        self.turbo = tk.BooleanVar(value=bool(self.config.get("turbo")))
        self.wait_round = tk.BooleanVar(value=bool(self.config.get("wait_for_round", True)))
        self.hours = tk.StringVar(value="{:02d}:{:02d}".format(*self.config.get("active_hours", [0, 24])))

        row = ttk.Frame(frame)
        row.pack(fill="x", padx=8, pady=6)
        for label, key, width in (("Số vòng/acc", "rounds", 6), ("Chạy song song", "jobs", 5),
                                  ("Tối đa vòng/ngày", "max", 5)):
            ttk.Label(row, text=label).pack(side="left")
            ttk.Entry(row, textvariable=self.vars[key], width=width).pack(side="left", padx=(4, 14))
        ttk.Checkbutton(row, text="turbo (nhanh)", variable=self.turbo).pack(side="left")
        ttk.Checkbutton(row, text="chờ vòng mới", variable=self.wait_round).pack(side="left", padx=14)
        ttk.Label(row, text="giờ hoạt động").pack(side="left")
        ttk.Entry(row, textvariable=self.hours, width=6).pack(side="left", padx=4)

    def _build_actions(self) -> None:
        frame = ttk.Frame(self.root)
        frame.pack(fill="x", padx=10)
        for text, command in (
            ("Thêm", self.add_account), ("Sửa", self.edit_account), ("Xoá", self.remove_account),
            ("Kiểm tra", self.refresh_accounts), ("Bắt đầu", self.start), ("Dừng", self.stop),
            ("Điểm theo ngày", self.show_stats), ("Tự kiểm tra", self.show_doctor),
            ("Phương án AI", self.start_ai), ("Lưu", self.save_config),
        ):
            ttk.Button(frame, text=text, command=command).pack(side="left", padx=3, pady=4)
        self.busy = ttk.Label(frame, text="sẵn sàng")
        self.busy.pack(side="right", padx=6)

    def _build_log(self) -> None:
        self.log = scrolledtext.ScrolledText(self.root, height=12, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, padx=10, pady=(6, 10))

    # ------------------------------------------------------------------ config
    def _sync_settings(self) -> None:
        try:
            self.config["rounds"] = max(1, int(self.vars["rounds"].get()))
            self.config["jobs"] = max(1, int(self.vars["jobs"].get()))
            self.config["max_rounds_per_day"] = max(0, int(self.vars["max"].get()))
            start, _, end = self.hours.get().partition(":")
            self.config["active_hours"] = [int(start), int(end or 24)]
        except ValueError:
            raise ValueError("thiết lập phải là số, giờ dạng HH:MM") from None
        self.config["turbo"] = bool(self.turbo.get())
        self.config["wait_for_round"] = bool(self.wait_round.get())

    def save_config(self) -> None:
        try:
            self._sync_settings()
        except ValueError as exc:
            messagebox.showerror(TITLE, str(exc))
            return
        cfgmod.save(self.config, self.path)
        self.write(f"đã lưu {self.path}\n")

    # ----------------------------------------------------------------- accounts
    def add_account(self) -> None:
        self._account_dialog(None)

    def edit_account(self) -> None:
        user = self._selected()
        if user:
            self._account_dialog(user)

    def _selected(self) -> str:
        item = self.tree.focus()
        return self.tree.item(item, "values")[0] if item else ""

    def _account_dialog(self, user: str | None) -> None:
        win = tk.Toplevel(self.root)
        win.title("Tài khoản")
        win.transient(self.root)
        entry_user = tk.StringVar(value=user or "")
        entry_pass = tk.StringVar()
        for label, var, show in (("Tài khoản", entry_user, ""), ("Mật khẩu", entry_pass, "*")):
            row = ttk.Frame(win)
            row.pack(fill="x", padx=10, pady=4)
            ttk.Label(row, text=label, width=10).pack(side="left")
            ttk.Entry(row, textvariable=var, width=32, show=show).pack(side="left")

        def confirm() -> None:
            name = entry_user.get().strip()
            if not name:
                return
            accounts = [a for a in self.config.get("accounts") or [] if a.get("user") != user]
            current = next((a for a in self.config.get("accounts") or []
                            if a.get("user") == name), {})
            accounts.append({"user": name,
                             "password": entry_pass.get() or current.get("password", "")})
            self.config["accounts"] = accounts
            self.save_config()
            self.refresh_accounts()
            win.destroy()

        ttk.Button(win, text="Lưu", command=confirm).pack(pady=10)

    def remove_account(self) -> None:
        user = self._selected()
        if not user or not messagebox.askyesno(TITLE, f"Xoá {user} khỏi danh sách?"):
            return
        self.config["accounts"] = [a for a in self.config.get("accounts") or []
                                   if a.get("user") != user]
        self.save_config()
        self.refresh_accounts()

    def refresh_accounts(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for account in self.config.get("accounts") or []:
            user = account.get("user") or ""
            self.tree.insert("", "end", values=(user, "?", "?", "chưa kiểm tra"))
        threading.Thread(target=self._probe, daemon=True).start()

    def _probe(self) -> None:
        for account in list(self.config.get("accounts") or []):
            user = account.get("user") or ""
            if not user:
                continue
            try:
                client = cli._client(user, str(account.get("password") or ""), prompt=False)
                info = __import__("ioeauto.games", fromlist=["games"]).round_info(client)
                pending = len(__import__("ioeauto.games", fromlist=["games"]).pending_exercises(client))
                note = "sẵn sàng" if not info else "hết bài" if not pending else "có bài"
                rounds = f"{info.get('current_round')}/{info.get('sysRound')}"
            except Exception as exc:                               # noqa: BLE001
                note, pending, rounds = f"lỗi: {exc}", "?", "?"
            self.queue.put(("row", (user, rounds, pending, note)))

    # -------------------------------------------------------------------- run
    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.save_config()
        self.config = cfgmod.load(self.path)
        args = argparse.Namespace(no_sleep=False, turbo=bool(self.config.get("turbo")),
                                  dry_run=False, pace=None, between=None, delay=None,
                                  rounds=None, jobs=None, only=None, user=None, password=None,
                                  config=self.path)
        cli.STOP.clear()
        self.busy.configure(text="đang chạy")
        self.worker = threading.Thread(target=self._work, args=(args,), daemon=True)
        self.worker.start()
        self.root.after(500, self._watch)

    def start_ai(self) -> None:
        """Chạy phương án C (trình duyệt + AI) cho cấu hình hiện tại."""
        if self.worker and self.worker.is_alive():
            return
        self.save_config()
        self.config = cfgmod.load(self.path)
        args = argparse.Namespace(config=self.path, user=None, password=None, rounds=None,
                                  dry_run=False, browser=None, headless=False, port=None,
                                  profile=None, max_steps=None, with_speaking=False,
                                  keep_open=False, login_only=False)
        cli.STOP.clear()
        self.busy.configure(text="phương án AI")
        self.worker = threading.Thread(target=self._work_ai, args=(args,), daemon=True)
        self.worker.start()
        self.root.after(500, self._watch)

    def _work_ai(self, args: argparse.Namespace) -> None:
        writer = _Writer(self.queue)
        try:
            with contextlib.redirect_stdout(writer):
                cli.cmd_ai(args)
        except Exception as exc:                                   # noqa: BLE001
            self.queue.put(("text", f"[gui] lỗi phương án AI: {exc}" + chr(10)))
        finally:
            self.queue.put(("done", ""))

    def _work(self, args: argparse.Namespace) -> None:
        writer = _Writer(self.queue)
        try:
            with contextlib.redirect_stdout(writer):
                cli.cmd_run(args)
        except Exception as exc:                                   # noqa: BLE001
            self.queue.put(("text", f"\n[gui] lỗi: {exc}\n"))
        finally:
            self.queue.put(("done", ""))

    def _watch(self) -> None:
        if self.worker and self.worker.is_alive():
            self.root.after(500, self._watch)
        else:
            self.busy.configure(text="sẵn sàng")
            self.refresh_accounts()

    def stop(self) -> None:
        cli.STOP.set()
        self.write("đang dừng sau bài hiện tại...\n")

    def show_stats(self) -> None:
        rows = report.stats(days=14)
        self.write("\nngày        vòng  bài  điểm TB\n")
        for row in rows:
            self.write(f"{row['date']:<11}{row['rounds']:>4}{row['exercises']:>5}"
                       f"{row['ratio'] * 100:>8.0f}%\n")
        if not rows:
            self.write("chưa có dữ liệu\n")

    def show_doctor(self) -> None:
        from . import doctor

        def work() -> None:
            writer = _Writer(self.queue)
            with contextlib.redirect_stdout(writer):
                doctor.run(self.path)

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------- misc
    def write(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _drain(self) -> None:
        while True:
            try:
                kind, payload = self.queue.get_nowait()
            except queue.Empty:
                break
            if kind == "text":
                self.write(payload)
            elif kind == "row":
                user, rounds, pending, note = payload
                for item in self.tree.get_children():
                    if self.tree.item(item, "values")[0] == user:
                        self.tree.item(item, values=(user, rounds, pending, note))
            elif kind == "done":
                self.write("=== xong ===\n")
        self.root.after(150, self._drain)

    def on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(TITLE, "Đang chạy. Dừng và thoát?"):
                return
            cli.STOP.set()
        if self._tray_available():
            self.root.withdraw()
            self._start_tray()
        else:
            self.root.destroy()

    def _tray_available(self) -> bool:
        try:
            import PIL  # noqa: F401
            import pystray  # noqa: F401
            return True
        except ImportError:
            return False

    def _start_tray(self) -> None:
        import pystray
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (64, 64), "#0b6ea8")
        ImageDraw.Draw(image).text((14, 20), "IOE", fill="white")

        def show(_icon=None, _item=None) -> None:
            self.root.deiconify()

        menu = pystray.Menu(
            pystray.MenuItem("Mở", show, default=True),
            pystray.MenuItem("Bắt đầu", lambda *_: self.start()),
            pystray.MenuItem("Dừng", lambda *_: self.stop()),
            pystray.MenuItem("Thoát", lambda icon, _item: (icon.stop(), self.root.after(0, self.root.destroy))),
        )
        self.tray = pystray.Icon("ioe", image, TITLE, menu)
        threading.Thread(target=self.tray.run, daemon=True).start()


def run() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0
