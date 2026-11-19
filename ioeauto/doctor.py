"""`run.py doctor` - is this machine actually able to run the tool?

Checks the things that silently break unattended runs (bad config, dead
sessions, missing ASR model, unwritable data folder, blocked network) and
prints the fix for each one instead of failing halfway through a round.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import time

OK, WARN, BAD = "ok", "warn", "bad"
_MARK = {OK: "[ok]  ", WARN: "[cần] ", BAD: "[lỗi] "}


class _Check:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, level: str, title: str, hint: str = "") -> None:
        self.rows.append((level, title, hint))

    @property
    def bad(self) -> int:
        return sum(1 for level, _, _ in self.rows if level == BAD)

    @property
    def warn(self) -> int:
        return sum(1 for level, _, _ in self.rows if level == WARN)

    def report(self) -> int:
        for level, title, hint in self.rows:
            print(f"{_MARK[level]} {title}")
            if hint and level != OK:
                print(f"        -> {hint}")
        print()
        if self.bad:
            print(f"{self.bad} lỗi, {self.warn} việc cần làm - chưa chạy được.")
            return 1
        if self.warn:
            print(f"Chạy được, còn {self.warn} việc nên làm cho ổn định.")
            return 0
        print("Mọi thứ ổn.")
        return 0


def _module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def run(config_path: str, *, check_accounts: bool = True) -> int:
    from . import asr, config as cfgmod, discovery, games, schema
    from .capabilities import load as caps_load, advice as caps_advice
    from .cli import DATA, _client, _whoami

    checks = _Check()

    # 1. runtime
    if sys.version_info >= (3, 10):
        checks.add(OK, f"Python {sys.version.split()[0]}")
    else:
        checks.add(BAD, f"Python {sys.version.split()[0]} quá cũ",
                   "cài Python 3.10 trở lên rồi chạy lại")

    # 2. data folder
    try:
        os.makedirs(DATA, exist_ok=True)
        probe = os.path.join(DATA, ".write-test")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("x")
        os.unlink(probe)
        checks.add(OK, f"ghi được vào {DATA}")
    except OSError as exc:
        checks.add(BAD, f"không ghi được vào {DATA}: {exc}",
                   "chạy tool ở thư mục khác hoặc bỏ quyền chỉ-đọc")

    # 3. config
    if os.path.exists(config_path):
        try:
            config = cfgmod.load(config_path)
            accounts = [a for a in config.get("accounts") or [] if a.get("user")]
            if accounts:
                checks.add(OK, f"config đọc được, {len(accounts)} tài khoản")
            else:
                checks.add(WARN, "config chưa có tài khoản nào",
                           'thêm {"user": "...", "password": "..."} vào "accounts"')
        except Exception as exc:                                   # noqa: BLE001
            config, accounts = cfgmod.load(), []
            checks.add(BAD, f"config hỏng: {exc}", "xoá file rồi chạy `run.py config`")
    else:
        config, accounts = cfgmod.load(), []
        checks.add(WARN, f"chưa có {config_path}", "chạy `run.py config` để tạo mẫu")

    # 4. optional parts
    for name, why, level in (
        ("mitmproxy", "chế độ proxy", WARN),
        ("pystray", "biểu tượng khay hệ thống của GUI (tuỳ chọn)", WARN),
        ("PIL", "icon khay hệ thống", WARN),
    ):
        checks.add(OK if _module(name) else level, f"{name}: {'có' if _module(name) else 'thiếu'}",
                   f"pip install {name}" if not _module(name) else "")

    have_asr = _module("vosk") and _module("soundfile")
    if have_asr:
        checks.add(OK, "vosk + soundfile cho bài nghe: có")
    else:
        checks.add(WARN, "thiếu vosk/soundfile - bài nghe sẽ dùng từ điển",
                   "pip install vosk soundfile numpy")
    if os.path.isdir(asr.MODEL_DIR):
        checks.add(OK, "model ASR đã tải")
    elif have_asr:
        checks.add(WARN, "model ASR chưa tải", "chạy `run.py run` một lần, model tự tải")

    # 5. free space
    try:
        free = shutil.disk_usage(DATA).free / 2**30
        level = OK if free > 1 else WARN
        checks.add(level, f"trống {free:.1f} GB", "dọn bớt đĩa" if level != OK else "")
    except OSError:
        pass

    # 6. sessions
    if check_accounts and accounts:
        for account in accounts:
            user = str(account.get("user") or "")
            if not user:
                continue
            try:
                client = _client(user, str(account.get("password") or ""))
                name = _whoami(client)
                if not name:
                    checks.add(BAD, f"{user}: phiên đăng nhập không dùng được",
                               f'điền "password" cho {user} trong config')
                    continue
                try:
                    info = games.round_info(client)
                except Exception as exc:                        # noqa: BLE001
                    window = games.closed_window(exc)
                    if not window:
                        raise
                    checks.add(OK, f"{user}: đăng nhập OK - Tự luyện đang đóng tới "
                                   + time.strftime("%d/%m %H:%M", time.localtime(window[1])))
                    continue
                pending = len(games.pending_exercises(client))
                left = games.rounds_left(info)
                checks.add(OK, f"{user}: đăng nhập OK, {pending} bài phải làm, "
                               f"{left} vòng đang mở")
            except Exception as exc:                                # noqa: BLE001
                checks.add(BAD, f"{user}: {exc}", "kiểm tra lại tài khoản/mật khẩu")

    # 6b. phương án C (AI + trình duyệt)
    from . import cdp, vision

    try:
        exe = cdp.find_browser(str((config.get("vision") or {}).get("browser") or ""))
        checks.add(OK, f"trình duyệt cho phương án AI: {os.path.basename(exe)}")
    except cdp.CdpError as exc:
        checks.add(WARN, "phương án AI: " + str(exc), "cài Edge/Chrome hoặc đặt EZIOE_BROWSER")
    name, key, model, _base = vision.provider(config)
    if key:
        checks.add(OK, f"AI cho phương án C: {name or 'openai'}/{model}")
    else:
        checks.add(WARN, "chưa có khoá AI cho phương án C",
                   "đặt EZIOE_AI_PROVIDER=gemini & EZIOE_AI_KEY=... (hoặc mục vision trong config)")

    # 7. lớp chống vá: endpoint đã bị đổi tay, schema đã trôi, lớp nào đã chết
    endpoints = discovery.load()
    if endpoints:
        checks.add(WARN, "đang ghi đè endpoint: " + ", ".join(f"{k}={v}" for k, v in endpoints.items()),
                   "`run.py endpoints` để xem, `run.py endpoints --discover` để dò lại")
    caps = caps_load()
    if caps:
        dead = [label for key, label in (("round_api", "API vòng"),
                                         ("answers_in_payload", "đáp án trong payload"),
                                         ("oracle", "answercheck"))
                if caps.get(key) is False]
        if dead:
            tips = " | ".join(caps_advice(caps)) or "chạy `run.py capabilities`"
            checks.add(WARN, "lớp đã chết: " + ", ".join(dead), tips)
        else:
            checks.add(OK, "các lớp fallback còn sống" + (f" (dò lúc {caps.get('at')})" if caps.get("at") else ""))

    # 8. clock
    checks.add(OK, "giờ máy: " + time.strftime("%d/%m/%Y %H:%M"))
    if time.localtime().tm_gmtoff if hasattr(time.localtime(), "tm_gmtoff") else False:
        pass
    return checks.report()
