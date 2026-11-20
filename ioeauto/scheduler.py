"""`run.py schedule` - hand the run to the OS scheduler.

A process cannot survive a reboot, so the durable way to "chạy ngầm" is the
system scheduler: Windows Task Scheduler or cron. Both are driven from here so
the user does not have to write XML or crontab by hand.

    python run.py schedule --at 08:30             # mỗi ngày
    python run.py schedule --every 90             # mỗi 90 phút
    python run.py unschedule
"""

from __future__ import annotations

import os
import subprocess
import sys

TASK = "IOE-Auto"


def _program() -> list[str]:
    """The command to schedule (a frozen .exe schedules itself)."""
    from .paths import app_root

    if getattr(sys, "frozen", False):
        return [sys.executable, "run"]
    return [sys.executable, os.path.join(app_root(), "run.py"), "run"]


def _quote(text: str) -> str:
    return f'"{text}"' if " " in text else text


def _windows_install(every: int | None, at: str, dry: bool) -> int:
    """Ghi một .bat rồi giao cho Task Scheduler: có log, không phụ thuộc PATH.

    Task Scheduler chạy không có terminal; nhét lệnh thẳng vào `/TR` thì dấu
    ngoặc và `>>` rất dễ bị hiểu sai, nên dùng file .bat có sẵn `cd` và `>>`.
    """
    from .paths import app_root, data_dir

    log = os.path.join(data_dir(), "task.log")
    bat = os.path.join(data_dir(), "task.bat")
    NL = chr(13) + chr(10)
    program = _program()
    body = ["@echo off", f'cd /d "{app_root()}"',
            " ".join(_quote(part) for part in program) + f' >> "{log}" 2>&1']
    if not dry:
        with open(bat, "w", encoding="utf-8", newline=NL) as fh:
            fh.write(NL.join(body) + NL)
    args = ["schtasks", "/Create", "/F", "/TN", TASK, "/TR", f'"{bat}"']
    if every:
        args += ["/SC", "MINUTE", "/MO", str(max(1, every))]
    else:
        args += ["/SC", "DAILY", "/ST", at]
    if dry:
        print(NL.join(body))
        print(" ".join(args))
        return 0
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        print((proc.stderr or proc.stdout).strip(), file=sys.stderr)
        print("Gợi ý: chạy lại bằng PowerShell 'Run as administrator'.", file=sys.stderr)
        return proc.returncode
    print(proc.stdout.strip())
    return 0


def _cron_line(every: int | None, at: str, log: str) -> str:
    command = " ".join(_quote(part) for part in _program())
    if every:
        return f"*/{max(1, every)} * * * * {command} >> {_quote(log)} 2>&1"
    hour, minute = (at.split(":") + ["0"])[:2]
    return f"{int(minute)} {int(hour)} * * * {command} >> {_quote(log)} 2>&1"


def _crontab() -> list[str]:
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return [line for line in (proc.stdout or "").splitlines() if line.strip()]


def _posix_install(every: int | None, at: str, dry: bool) -> int:
    from .paths import data_dir

    log = os.path.join(data_dir(), "cron.log")
    line = _cron_line(every, at, log)
    kept = [row for row in _crontab() if TASK not in row]
    if dry:
        print("\n".join(kept + [line]))
        return 0
    if not shutil_which("crontab"):
        print("Không có crontab trên máy này.", file=sys.stderr)
        return 2
    proc = subprocess.run(["crontab", "-"], input="\n".join(kept + [line]) + "\n", text=True)
    if proc.returncode != 0:
        return proc.returncode
    print(f"Đã thêm vào crontab: {line}")
    return 0


def shutil_which(name: str) -> str | None:
    from shutil import which

    return which(name)


def install(every: int | None = None, at: str = "09:00", dry: bool = False) -> int:
    if every is not None:
        every = max(1, int(every))
    if at.count(":") != 1:
        print("--at phải dạng HH:MM", file=sys.stderr)
        return 2
    if os.name == "nt":
        level = _windows_install(every, at, dry)
        if not dry and level == 0:
            print(f'Đã tạo lịch "{TASK}". Xem lại: schtasks /Query /TN {TASK}')
        return level
    return _posix_install(every, at, dry)


def remove(dry: bool = False) -> int:
    if os.name == "nt":
        args = ["schtasks", "/Delete", "/F", "/TN", TASK]
        if dry:
            print(" ".join(args))
            return 0
        proc = subprocess.run(args, capture_output=True, text=True)
        print((proc.stdout or proc.stderr).strip())
        return proc.returncode
    kept = [row for row in _crontab() if TASK not in row]
    if dry:
        print("\n".join(kept))
        return 0
    subprocess.run(["crontab", "-"], input="\n".join(kept) + "\n", text=True)
    print("Đã xoá khỏi crontab")
    return 0


def show() -> int:
    if os.name == "nt":
        proc = subprocess.run(["schtasks", "/Query", "/TN", TASK, "/V", "/FO", "LIST"],
                              capture_output=True, text=True)
        print((proc.stdout or proc.stderr).strip())
        return proc.returncode
    rows = [row for row in _crontab() if TASK in row]
    print("\n".join(rows) or "Chưa có lịch nào")
    return 0
