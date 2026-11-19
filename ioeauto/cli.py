"""Command line entry point for ioe-tu-luyen."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import random
import sys
import threading
import time

from . import config as cfgmod
from . import capabilities, cdp, discovery, games, notify, paths, report, schema, userscript, vision
from .api import ApiError, Client, login

ROOT = paths.app_root()
DATA = paths.data_dir()
BANK = os.path.join(DATA, "answers.json")

MIN_QUICK = 20.0

# set by the GUI / Ctrl+C to stop a run between exercises
STOP = threading.Event()


def _sleep(seconds: float) -> None:
    """Sleep that a stop request can cut short."""
    end = time.time() + max(0.0, seconds)
    while not STOP.is_set():
        left = end - time.time()
        if left <= 0:
            return
        time.sleep(min(1.0, left))


# --------------------------------------------------------------------- helpers
def _load_bank() -> dict:
    if not os.path.exists(BANK):
        return {}
    try:
        with open(BANK, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_bank(bank: dict) -> None:
    os.makedirs(DATA, exist_ok=True)
    tmp = BANK + ".tmp"
    with games.BANK_LOCK:                 # parallel accounts share the bank
        text = games.dump_bank(bank)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, BANK)


def _state_path(user: str) -> str:
    safe = "".join(ch for ch in user if ch.isalnum() or ch in "-_") or "session"
    return os.path.join(DATA, f"session_{safe}.json")


def _whoami(client: Client) -> str:
    """Account the stored token belongs to ('' when the token is unusable)."""
    try:
        info = client.post("/user-service/v3/GDPTUser/getuserinfo", {}) or {}
    except ApiError:
        return ""
    return str(info.get("s_username") or "").strip().lower()


def _client(user: str, password: str = "", prompt: bool = True) -> Client:
    """Reuse the saved session of THIS account, refresh it, or log in again.

    The legacy ``data/session.json`` is only adopted when it really belongs to
    `user`, otherwise every new account would inherit the previous one's token
    (and with it the previous account's finished round).
    """
    wanted = user.strip().lower()
    path = _state_path(user)
    legacy = os.path.join(DATA, "session.json")

    for candidate in [path, legacy]:
        client = Client.load(candidate)
        if not client:
            continue
        name = _whoami(client)
        if not name:
            try:
                client.refresh()
                name = _whoami(client)
            except ApiError:
                name = ""
        if name != wanted:
            continue
        client.state_path = path
        client.username = wanted
        client.save()
        if candidate != path and os.path.exists(legacy):
            os.replace(legacy, path)          # migrate the old global session
        return client

    if not password:
        # chạy ngầm (cron/Task Scheduler) không có terminal: lấy từ biến môi trường
        password = os.environ.get("IOE_PASSWORD") or os.environ.get(f"IOE_PASSWORD_{wanted}") or ""
    if not password and not prompt:
        raise ApiError(0, f"{user}: chưa có mật khẩu trong config")
    if not password:
        if not (sys.stdin and sys.stdin.isatty()):
            raise ApiError(0, f"{user}: chưa có mật khẩu, không có terminal để hỏi - bỏ qua")
        # announced on stdout too, otherwise an unattended run just looks frozen
        print(f"cần mật khẩu cho {user} (chưa có phiên dùng được)", flush=True)
        password = getpass.getpass(f"Mật khẩu cho {user}: ")
    client = login(user, password, state_path=path)
    client.username = wanted
    return client


def oracle_alive(config: dict) -> bool:
    """Oracle còn dùng được không: config `oracle: false` tắt tay, probe chết thì tắt."""
    if config.get("oracle") is False:
        return False
    return capabilities.fresh().get("oracle") is not False


def _closure_wait(exc: ApiError) -> float | None:
    """Số giây phải chờ nếu IOE đang đóng Tự luyện (None = lỗi khác)."""
    window = games.closed_window(exc)
    if not window:
        return None
    return max(0.0, window[1] - time.time())


def _fmt(exercise: dict) -> str:
    status = {0: "chưa làm", 1: "đang làm", 2: "đã nộp", 3: "khoá"}.get(exercise["Status"], "?")
    return f"  #{exercise['idexercise']:<7} game {exercise['gameId']:<4} {status:<9} điểm {exercise['mark']}"


def _within_hours(window) -> bool:
    start, end = int(window[0]), int(window[1])
    if start == end:
        return True                      # [0, 0] / [7, 7] = cả ngày
    hour = time.localtime().tm_hour
    return start <= hour < end if start < end else (hour >= start or hour < end)


def _accounts(args, config: dict) -> list[tuple[str, str]]:
    if args.user:
        stored = next((a.get("password", "") for a in config.get("accounts") or []
                       if a.get("user") == args.user), "")
        return [(args.user, args.password or os.environ.get(f"IOE_PASSWORD_{args.user}")
                 or os.environ.get("IOE_PASSWORD") or stored or "")]
    listed = [(a.get("user", ""), a.get("password", "")) for a in config.get("accounts") or []]
    listed = [item for item in listed if item[0]]
    if listed:
        return listed
    user = input("Tài khoản IOE: ").strip()
    return [(user, args.password or "")]


# -------------------------------------------------------------------- commands
def cmd_status(args) -> int:
    client = _client(*(_accounts(args, cfgmod.load())[0]))
    print("Vòng hiện tại:")
    try:
        exercises = games.list_exercises(client)
    except ApiError as exc:
        wait = _closure_wait(exc)
        if wait is None:
            raise
        print(f"  đang đóng: {exc.message}")
        print("  mở lại: " + time.strftime("%d/%m/%Y %H:%M", time.localtime(time.time() + wait)))
        return 0
    for exercise in exercises:
        print(_fmt(exercise))
    pending = [e for e in exercises
               if e.get("isRank") and e["Status"] not in (games.STATUS_FINISHED, games.STATUS_LOCKED)]
    info = games.round_info(client)
    if info:
        left = games.rounds_left(info)
        print(f"  vòng hệ thống {info.get('sysRound')} / tài khoản đã làm tới {info.get('current_round')}"
              f" (tối đa {info.get('sysRoundMax')})" + (f" - còn {left} vòng mở" if left > 0 else ""))
    print(f"  còn {len(pending)} bài phải làm")
    opening = games.next_round_start(client)
    if opening:
        print("  vòng mới mở: " + time.strftime("%d/%m/%Y %H:%M", time.localtime(opening)))
    return 0


def _run_account(user: str, password: str, config: dict, bank: dict, *,
                 only: int | None, single: bool) -> list[str]:
    """Play the rounds of ONE account. Returns the rows for the final summary."""
    rows: list[str] = []
    partial = only is not None
    budget = int(config.get("max_rounds_per_day") or 0)
    if budget and report.rounds_today(user) >= budget:
        print(f"{user}: đã đủ {budget} vòng hôm nay, bỏ qua", flush=True)
        return rows
    client = _client(user, password)
    bil = client.balance() or {}
    print(f"== {user} | RUBY {bil.get('RUBY')} | coin {bil.get('goCoin')}", flush=True)
    try:
        info = games.round_info(client)
    except ApiError as exc:
        wait = _closure_wait(exc)
        if wait is None:
            raise
        when = time.strftime("%H:%M", time.localtime(time.time() + wait))
        print(f"  Tự luyện đang đóng: {exc.message}", flush=True)
        if not single or not config.get("wait_for_round") or wait > 6 * 3600:
            print(f"  mở lại lúc {when}, bỏ qua lượt này", flush=True)
            return rows
        print("  vừa mở lại, vào làm ngay" if wait <= 0 else f"  chờ tới {when} (~{wait / 60:.0f} phút)",
              flush=True)
        notify.send(f"⏳ {user}: IOE đóng Tự luyện, chờ tới {when}")
        _sleep(wait + random.uniform(15, 90))
        info = games.round_info(client)
    if info:
        left = games.rounds_left(info)
        print(f"   vòng hệ thống {info.get('sysRound')} | tk đã làm tới {info.get('current_round')}"
              + (f" | còn {left} vòng mở" if left > 0 else ""), flush=True)
    rounds_done = 0
    confirmed = None
    target = int(config.get("rounds") or 1)

    while rounds_done < target and not STOP.is_set():
        try:
            pending = games.pending_exercises(client)
        except ApiError as exc:
            wait = _closure_wait(exc)
            if wait is None:
                raise
            when = time.strftime("%H:%M", time.localtime(time.time() + wait))
            print(f"  Tự luyện vừa đóng, mở lại lúc {when}", flush=True)
            if not single or not config.get("wait_for_round") or wait > 6 * 3600:
                break
            _sleep(wait + random.uniform(15, 90))
            continue
        if partial:
            # --only: that exercise is played even when it is already submitted
            pending = [e for e in games.list_exercises(client) if e["idexercise"] == only]
        elif not pending:
            # vòng đã làm hết nhưng chưa "Ghi lại kết quả" (lần trước dừng giữa vòng,
            # hoặc chỉ làm 1 bài): phải ghi thì vòng mới được tính và mới mở vòng tiếp
            payload = games.round_payload(client)
            rank = [e for e in payload["games"] if e.get("isRank")]
            # Status 3 = khoá = vòng đã đóng, không "Ghi lại kết quả" được nữa
            if (rank and all(e["Status"] == games.STATUS_FINISHED for e in rank)
                    and payload["info"].get("current_round") != confirmed):
                try:
                    games.confirm_round(client)
                    confirmed = payload["info"].get("current_round")
                    print(f"  {user}: đã ghi kết quả vòng đang chờ", flush=True)
                    report.mark_round(user, True)
                    rounds_done += 1
                    if rounds_done >= target:
                        break
                    pending = games.pending_exercises(client)
                except ApiError as exc:
                    print(f"    không ghi được kết quả: {exc}", flush=True)
            # the account may still have open rounds even when this list is empty
            for _ in range(3):
                if pending:
                    break
                left = games.rounds_left(games.round_info(client))
                if left <= 0:
                    break
                print(f"  còn {left} vòng đang mở, chờ server trả danh sách...", flush=True)
                time.sleep(random.uniform(4, 9))
                pending = games.pending_exercises(client)
        if not pending:
            if partial:
                print(f"không thấy bài {only} trong vòng đang mở")
                break
            opening = games.next_round_start(client)
            when = time.strftime("%d/%m %H:%M", time.localtime(opening)) if opening else ""
            if config.get("dry_run"):
                print("(dry-run) " + (f"vòng mới mở {when}" if when else "không có bài"))
                break
            if not single:
                # nhiều tài khoản: đừng ngủ, tài khoản khác còn việc
                print(f"  {user}: vòng đã xong" + (f", vòng mới mở {when}" if when else ""), flush=True)
                break
            if config.get("wait_for_round") and opening and opening > time.time():
                wait = opening - time.time() + random.uniform(20, 180)
                print(f"... vòng đã xong, chờ vòng mới mở lúc {when} "
                      f"(~{wait / 60:.0f} phút)", flush=True)
                notify.send(f"⏳ {user}: đã xong vòng, chờ vòng mới {when}")
                _sleep(min(wait, 6 * 3600))
                continue
            print(f"Không còn bài để làm" + (f" - vòng mới mở {when}" if when else ""))
            break

        if (config.get("speaking") or {}).get("play"):
            optional = [e for e in games.list_exercises(client)
                        if not e.get("isRank") and e["Status"] not in
                        (games.STATUS_FINISHED, games.STATUS_LOCKED)]
            pending += [e for e in optional if e["idexercise"] not in {p["idexercise"] for p in pending}]
        print(f"=== {user} | vòng {rounds_done + 1} ({len(pending)} bài) ===", flush=True)
        # chỉ bài xếp hạng mới quyết định vòng xong hay chưa
        target_ids = {e["idexercise"] for e in pending if e.get("isRank")}

        def event(kind, exercise, info):
            if kind == "start":
                print(f"[{info}] bài {exercise['idexercise']} (game {exercise['gameId']}) ...", flush=True)
            elif kind == "done":
                data = info or {}
                print(f"    -> {data.get('point', 0)}/{data.get('examScore', 0)} điểm", flush=True)
            elif kind == "skip":
                print(f"    bỏ qua {exercise['idexercise']} (đã nộp)", flush=True)
            elif kind == "failed":
                print(f"    lỗi: {info} - bỏ qua bài này", flush=True)
            elif kind == "unfinished":
                print(f"    còn {info} bài chưa nộp được", flush=True)
            elif kind == "confirm_failed":
                print(f"    không ghi được kết quả: {info}", flush=True)

        remaining = pending
        played: list[dict] = []
        for _pass in range(2):
            try:
                played += games.solve_round(
                    client, bank,
                    only=only,
                    delay=tuple(config["delay"]),
                    pace=tuple(config["pace"]),
                    attempts=int(config.get("attempts") or 3),
                    turbo=bool(config.get("turbo")),
                    dry_run=bool(config.get("dry_run")),
                    speaking=config.get("speaking") or {},
                    include_optional=bool((config.get("speaking") or {}).get("play")),
                    use_oracle=oracle_alive(config),
                    on_event=event,
                )
            except ApiError as exc:
                print(f"    vòng lỗi: {exc}", flush=True)
            _save_bank(bank)
            if config.get("dry_run"):
                remaining = []
                break
            # the server decides whether that round is really finished - and it
            # already shows the NEXT round, so only trust the ids we played.
            remaining = [e for e in games.list_exercises(client)
                         if e["idexercise"] in target_ids
                         and e["Status"] not in (games.STATUS_FINISHED, games.STATUS_LOCKED)]
            if not remaining or _pass or partial:
                break
            print(f"    còn {len(remaining)} bài, làm lại...", flush=True)

        for row in played:
            data = row.get("result") or {}
            if data.get("examScore"):
                rows.append(f"#{row['exercise']['idexercise']}={data.get('point', 0)}")
        if config.get("dry_run"):
            break
        if partial:
            print("  (chỉ làm 1 bài, không tính vòng)", flush=True)
            break
        if remaining:
            print(f"    còn {len(remaining)} bài chưa xong - chưa tính vòng này", flush=True)
            report.mark_round(user, False, f"còn {len(remaining)} bài")
            break
        rounds_done += 1
        info = games.round_info(client)
        report.mark_round(user, True)
        notify.send(f"<b>IOE</b> {user}: xong vòng -> {', '.join(rows[-5:]) or 'n/a'}")
        if info:
            print(f"   -> đã ghi vòng, tk ở vòng {info.get('current_round')}"
                  f"/{info.get('sysRound')}", flush=True)
        if rounds_done < target:
            pause = random.uniform(*config["between"])
            print(f"... nghỉ {pause:.0f}s", flush=True)
            _sleep(pause)

    bil = client.balance() or {}
    print(f"== {user}: RUBY {bil.get('RUBY')} | coin {bil.get('goCoin')}", flush=True)
    return rows


def cmd_run(args) -> int:
    config = cfgmod.load(args.config)
    cfgmod.apply_endpoints(config)
    notify.configure((config.get("notify") or {}).get("telegram"))

    if getattr(args, "profile", None):
        apply_profile(config, args.profile)
    if args.no_sleep:
        config.update(pace=[MIN_QUICK, MIN_QUICK], between=[0, 0], delay=[0, 0])
    if args.turbo:
        config["turbo"] = True
    if args.dry_run:
        config["dry_run"] = True
    if getattr(args, "no_oracle", False):
        config["oracle"] = False
    if getattr(args, "with_speaking", False):
        config.setdefault("speaking", {})["play"] = True
    if args.pace:
        config["pace"] = list(args.pace)
    if args.between:
        config["between"] = list(args.between)
    if args.delay:
        config["delay"] = list(args.delay)
    if args.rounds:
        config["rounds"] = args.rounds
    if getattr(args, "jobs", None):
        config["jobs"] = max(1, args.jobs)
    if getattr(args, "fallback", None):
        config["fallback"] = args.fallback
    if str(config.get("fallback") or "none") == "ai":
        if getattr(args, "force_ai", False):
            print("ép phương án C (trình duyệt thật + AI)")
            return cmd_ai(args)
        if capabilities.fresh().get("round_api") is False and not args.only:
            print("API vòng đang chết -> chuyển phương án C (trình duyệt thật + AI)")
            return cmd_ai(args)
    return _run_api(args, config)


def _run_api(args, config: dict) -> int:
    manual = getattr(args, "only", None) is not None      # --only: chạy tay, bỏ qua khung giờ
    if not manual and not _within_hours(config.get("active_hours") or [0, 24]):
        print("Ngoài khung giờ cho phép, bỏ qua lượt này")
        return 0
    only = args.only if args.only is not None else config.get("only")
    only = int(only) if only else None
    bank = _load_bank()
    accounts = _accounts(args, config)
    single = len(accounts) == 1
    jobs = 1 if only is not None else max(1, int(config.get("jobs") or 1))
    summary_rows: list[str] = []
    STOP.clear()

    def work(account) -> list[str]:
        try:
            return _run_account(account[0], account[1], config, bank, only=only, single=single)
        except ApiError as exc:
            print(f"{account[0]}: lỗi API - {exc}", flush=True)
            return []
        except KeyboardInterrupt:
            STOP.set()
            return []

    if jobs == 1 or len(accounts) <= 1:
        for account in accounts:
            summary_rows += work(account)
    else:
        print(f"chạy song song {min(jobs, len(accounts))} tài khoản", flush=True)
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(jobs, len(accounts))) as pool:
            for rows in pool.map(work, accounts):
                summary_rows += rows
    _save_bank(bank)

    if summary_rows:
        print("Kết quả:", ", ".join(summary_rows))
    weak = report.weak_spots(limit=5)
    if weak:
        print("Bài chưa đạt điểm tối đa:")
        for ex in weak:
            print(f"  {ex.get('user', '')} #{ex.get('idexercise')} game {ex.get('gameId')}: "
                  f"{ex.get('mark')}/{ex.get('max')}")
    return 0


def cmd_report(args) -> int:
    rows = report.summary(limit=args.limit, user=getattr(args, "user", "") or "")
    if not rows:
        print("Chưa có dữ liệu")
        return 0
    print(f"{'ngày':<11}{'giờ':<9}{'user':<18}{'bài':<8}{'game':<6}{'điểm':<9}{'ok':<5}{'giây'}")
    for row in rows:
        print(f"{row['date']:<11}{row['time']:<9}{row['user'][:17]:<18}{row['exercise']:<8}"
              f"{row['game']:<6}{row['mark']:<9}{row['pass']:<5}{row['seconds']}")
    return 0


def cmd_stats(args) -> int:
    rows = report.stats(days=max(1, args.days), user=args.user or "")
    if not rows:
        print("Chưa có dữ liệu trong khoảng này")
        return 0
    print(f"{'ngày':<11}{'vòng':>5}{'bài':>6}{'đạt':>6}{'điểm TB':>9}  biểu đồ")
    total_p = total_m = 0
    for row in rows:
        total_p += row["points"]
        total_m += row["max"]
        ratio = row["ratio"]
        bar = "#" * int(ratio * 24) + "." * (24 - int(ratio * 24))
        print(f"{row['date']:<11}{row['rounds']:>5}{row['exercises']:>6}{row['passed']:>6}"
              f"{ratio * 100:>8.0f}%  {bar}")
    avg = (total_p / total_m * 100) if total_m else 0.0
    print(f"{'tổng':<11}{sum(r['rounds'] for r in rows):>5}{sum(r['exercises'] for r in rows):>6}"
          f"{sum(r['passed'] for r in rows):>6}{avg:>8.0f}%")
    if args.csv:
        import csv

        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["date", "rounds", "exercises", "passed", "points", "max", "seconds"])
            for row in rows:
                writer.writerow([row["date"], row["rounds"], row["exercises"], row["passed"],
                                 row["points"], row["max"], round(row["seconds"], 1)])
        print(f"đã ghi {args.csv}")
    return 0


def cmd_doctor(args) -> int:
    from . import doctor

    config = cfgmod.load(args.config)
    cfgmod.apply_endpoints(config)
    return doctor.run(args.config, check_accounts=not args.no_accounts)


def cmd_schedule(args) -> int:
    from . import scheduler

    return scheduler.install(every=args.every, at=args.at, dry=args.dry_run)


def cmd_unschedule(args) -> int:
    from . import scheduler

    return scheduler.remove(dry=args.dry_run)


def cmd_gui(args) -> int:
    from . import gui

    return gui.run()


def cmd_balance(args) -> int:
    config = cfgmod.load(args.config)
    for user, password in _accounts(args, config):
        client = _client(user, password)
        bil = client.balance() or {}
        print(f"{user}: RUBY {bil.get('RUBY')} | coin {bil.get('goCoin')}")
    return 0


def cmd_reset(args) -> int:
    client = _client(*(_accounts(args, cfgmod.load())[0]))
    games.cancel_round(client)
    print("Đã huỷ kết quả vòng đang mở")
    return 0


def cmd_proxy(args) -> int:
    from . import proxy

    return proxy.run(args.port, args.bank)


def cmd_capabilities(args) -> int:
    """Dò xem IOE còn hở gì (đáp án, oracle, lịch) để chọn lớp fallback."""
    config = cfgmod.load(args.config)
    cfgmod.apply_endpoints(config)
    accounts = _accounts(args, config)
    if not accounts:
        print("chưa có tài khoản nào trong config")
        return 1
    if args.user:
        accounts = [(args.user, args.password or "")]
    else:
        # ưu tiên tài khoản còn bài để dò được sâu
        accounts.sort(key=lambda pair: not pair[1])
        for index, (user, password) in enumerate(accounts):
            try:
                if games.pending_exercises(_client(user, password, prompt=False)):
                    accounts.insert(0, accounts.pop(index))
                    break
            except ApiError:
                continue
    user, password = accounts[0]
    client = _client(user, password, prompt=False)
    caps = capabilities.probe(client)
    capabilities.save(caps)
    print(f"tài khoản dò: {user} | {caps.get('exercises', 0)} bài trong vòng")
    print(capabilities.report(caps))
    if caps.get("checked"):
        print(f"ghi chú: {caps['checked']}")
    caps["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    for tip in capabilities.advice(caps):
        print(f"  ! {tip}")
    return 0


def cmd_endpoints(args) -> int:
    """Xem/ghi đè endpoint; `--discover` tự dò khi IOE đổi API."""
    if args.discover:
        token = ""
        accounts = _accounts(args, config)
        if accounts:
            try:
                token = _client(*accounts[0], prompt=False).token
            except ApiError as exc:
                print(f"(dò không token: {exc})")
        found = discovery.discover(token)
        if found["error"]:
            print(f"lỗi: {found['error']}")
            return 1
        print("thấy: " + (", ".join(sorted(found["found"]["services"].values())) or "không có dịch vụ nào"))
        if not found["endpoints"]:
            print("không dò được endpoint nào")
            return 1
        for key, value in found["endpoints"].items():
            print(f"  {key} = {value}")
        if not args.dry_run:
            print(f"đã ghi {discovery.save(found['endpoints'])}")
        return 0
    current = discovery.load()
    if args.set:
        for item in args.set:
            key, _, value = item.partition("=")
            if not key or not value:
                print(f"bỏ qua {item!r} (cần dạng key=value)")
                continue
            current[key] = value
        print(f"đã ghi {discovery.save(current)}")
        return 0
    if not current:
        print("chưa có endpoint nào bị ghi đè (đang dùng mặc định)")
        return 0
    for key, value in current.items():
        print(f"{key} = {value}")
    return 0


def cmd_schema(args) -> int:
    """So hình dạng response hiện tại với fixture để phát hiện API đổi."""
    config = cfgmod.load(args.config)
    cfgmod.apply_endpoints(config)
    if not schema.names():
        print("chưa có fixture nào (chạy `status`/`run` một lần để tạo)")
        return 1
    accounts = _accounts(args, config)
    client = _client(*accounts[0], prompt=False) if accounts else None
    if client is None:
        for name in schema.names():
            print(name)
        return 0
    problems = 0
    raw = client.post(games.ROUND_PATH, {"serviceCode": "IOE"}) or {}
    drift = schema.drift("round", raw)
    payload = games.round_payload(client)
    print("round:", "ổn" if not drift else "")
    for line in drift:
        print("   ", line)
    problems += len(drift)
    if payload["games"]:
        target = next((e for e in payload["games"] if e.get("isRank")), payload["games"][0])
        try:
            session = games.Session(client, target["idexercise"], target["gameId"]).open()
            for line in session.schema_drift:
                print("getinfo:", line)
            problems += len(session.schema_drift)
        except ApiError as exc:
            print("getinfo lỗi:", exc)
            problems += 1
    print("kết luận:", "schema đổi, xem lại parser" if problems else "schema không đổi")
    return 0 if not problems else 3


def cmd_bank(args) -> int:
    """Xuất/nhập kho đáp án - tài sản không phụ thuộc API."""
    bank = _load_bank()
    if args.action == "stats":
        kinds: dict[str, int] = {}
        for entry in bank.values():
            kinds[str(entry.get("type"))] = kinds.get(str(entry.get("type")), 0) + 1
        print(f"{len(bank)} đáp án")
        for kind, count in sorted(kinds.items()):
            print(f"  type {kind}: {count}")
        return 0
    if args.action == "prune":
        dropped = [k for k, v in bank.items() if not v.get("answer") or int(v.get("point") or 0) <= 0]
        for key in dropped:
            bank.pop(key, None)
        _save_bank(bank)
        print(f"bỏ {len(dropped)} đáp án rỗng/0 điểm, còn {len(bank)}")
        return 0
    if args.action == "show":
        needle = (args.path or "").lower()
        hits = [(k, v) for k, v in bank.items()
                if needle in str(v.get("content", "")).lower()
                or needle in str(v.get("answer", "")).lower()]
        if not hits:
            print(f"không thấy đáp án nào khớp {needle!r}")
            return 0
        for key, entry in hits[:args.limit]:
            print(f"[{entry.get('type')}] {entry.get('content', '')[:90]}")
            print(f"     -> {entry.get('answer')}  ({entry.get('point')} điểm, {entry.get('seen', '')})")
        print(f"{len(hits)} kết quả" + ("" if len(hits) <= args.limit else f", hiện {args.limit}"))
        return 0
    if args.action == "export":
        target = args.path or os.path.join(DATA, "answers_export.json")
        with games.BANK_LOCK:
            text = games.dump_bank(bank)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"đã xuất {len(bank)} đáp án -> {target}")
        return 0
    if args.action == "import":
        if not args.path:
            print("cần đường dẫn file để nhập")
            return 1
        with open(args.path, encoding="utf-8") as fh:
            data = json.load(fh)
        fresh = {k: v for k, v in data.items() if isinstance(v, dict) and v.get("answer")}
        added = 0
        for key, entry in fresh.items():
            if key in bank and bank[key].get("point", 0) >= entry.get("point", 0):
                continue
            bank[key] = entry
            added += 1
        _save_bank(bank)
        print(f"nhập {added} đáp án mới (tổng {len(bank)})")
        return 0
    print("dùng: bank stats | export [file] | import file | prune | show <chữ>")
    return 1


def _vision_conf(config: dict, args) -> dict:
    """Gộp config + cờ dòng lệnh cho phương án AI."""
    vision_conf = dict(config.get("vision") or {})
    for key in ("browser", "profile", "port", "max_steps", "headless"):
        value = getattr(args, key, None)
        if value not in (None, ""):
            vision_conf[key] = value
    return vision_conf


def cmd_ai(args) -> int:
    """Phương án C: mở trình duyệt thật, AI nhìn ảnh chia lưới và bấm."""
    config = cfgmod.load(args.config)
    cfgmod.apply_endpoints(config)
    config["vision"] = _vision_conf(config, args)
    if getattr(args, "login_only", False):
        pass
    elif not config["vision"].get("provider") and not (os.environ.get("EZIOE_AI_KEY")
                                                     or os.environ.get("GEMINI_API_KEY")
                                                     or os.environ.get("ANTHROPIC_API_KEY")):
        print("chưa có khoá AI: đặt EZIOE_AI_KEY (hoặc GEMINI_API_KEY/ANTHROPIC_API_KEY)")
        print("ví dụ: set EZIOE_AI_PROVIDER=gemini & set EZIOE_AI_KEY=...")
        return 2
    accounts = _accounts(args, config)
    if not accounts:
        print("chưa có tài khoản nào trong config")
        return 1
    if args.user:
        accounts = accounts[:1]
    for user, password in accounts:
        if not password:
            password = os.environ.get(f"IOE_PASSWORD_{user}") or os.environ.get("IOE_PASSWORD") or ""
        if not password:
            try:
                password = getpass.getpass(f"mật khẩu cho {user}: ")
            except (EOFError, KeyboardInterrupt):
                print(f"{user}: thiếu mật khẩu, bỏ qua")
                continue
        try:
            _ai_account(user, password, config, args)
        except (cdp.CdpError, vision.VisionError) as exc:
            print(f"{user}: lỗi phương án AI - {exc}", flush=True)
    return 0


def _ai_account(user: str, password: str, config: dict, args) -> int:
    profile = config["vision"].get("profile") or os.path.join(DATA, f"browser_{user}")
    rounds = args.rounds or 1
    done = 0
    finished = 0
    with cdp.Browser(port=int(config["vision"].get("port") or 9222), profile=profile,
                     headless=bool(config["vision"].get("headless")),
                     browser=str(config["vision"].get("browser") or "")) as browser:
        print(f"{user}: mở trình duyệt ({browser.exe})")
        vision.login(browser, user, password)
        if "dang-nhap" in browser.url_now():
            print("  chưa đăng nhập được - đăng nhập tay trong cửa sổ vừa mở rồi chạy lại")
            return 3
        if getattr(args, "login_only", False):
            name = browser.eval("""(() => {
                const el = document.querySelector('.name, .username, .user-name, .account');
                return (el && el.textContent.trim()) || (localStorage.getItem('user') || '');
            })()""")
            print("  đăng nhập OK" + (f" ({str(name)[:40]})" if name else "") + " | " + browser.url_now())
            return 0
        try:
            client = _client(user, password, prompt=False)
        except ApiError as exc:
            print(f"  API không dùng được ({exc}) - chạy hoàn toàn bằng mắt AI")
            client = None
        for _round in range(rounds):
            print(f"  vòng {_round + 1}/{rounds}")
            before = games.round_info(client) if client else {}
            try:
                pending = games.pending_exercises(client) if client else []
            except ApiError:
                pending = []
            if getattr(args, "exercise", None):
                pending = [e for e in pending if e["idexercise"] == args.exercise] or pending
            if not pending:
                browser.goto("https://ioe.vn/hoc-sinh/tu-luyen", 4)
                picked = vision.step(browser, config, "Bấm vào bài tập chưa làm để mở nó.",
                                     "Ưu tiên bài 1-4 (bài bắt buộc).", dry=args.dry_run)
                print(f"  mở bài bằng mắt: {picked['action']} {picked['cell']}")
                result = vision.solve_exercise(browser, config, browser.url_now(),
                                               dry=args.dry_run,
                                               on_log=lambda line: print(line, flush=True))
            else:
                for exercise in pending:
                    if exercise.get("isRank") is False and not getattr(args, "with_speaking", False):
                        continue
                    url = ""
                    if client:
                        try:
                            info = client.post("/ioe-service/v3/TuLuyen/setinfo", {
                                "idExercise": exercise["idexercise"], "round": 0,
                                "gameId": exercise["gameId"],
                                "redirectUrl": "https://ioe.vn/hoc-sinh/tu-luyen"})
                            url = info.get("urlgame") or ""
                        except ApiError as exc:
                            print(f"  setinfo lỗi: {exc}")
                    if not url:
                        browser.goto("https://ioe.vn/hoc-sinh/tu-luyen", 4)
                        vision.step(browser, config, "Bấm vào bài tập chưa làm để mở nó.",
                                    f"Cần mở bài id {exercise['idexercise']}.", dry=args.dry_run)
                        url = browser.url_now()
                    print(f"  bài {exercise['idexercise']} (game {exercise['gameId']})")
                    result = vision.solve_exercise(browser, config, url, game_id=exercise["gameId"],
                                                   dry=args.dry_run,
                                                   on_log=lambda line: print(line, flush=True))
                    if result["done"]:
                        finished += 1
                    else:
                        print("  hết bước mà chưa xong - chạy lại với --max-steps cao hơn")
                    if getattr(args, "dry_run", False):
                        return 0
                    _sleep(random.uniform(3, 8))
            if args.dry_run:
                return 0
            finish = vision.finish_round(browser, config)
            print(f"  bấm lưu kết quả: {finish['action']} {finish.get('cell') or ''}")
            if client and before:
                try:
                    after = games.round_info(client)
                except ApiError:
                    after = {}
                moved = after and (after.get("bilInfo", {}).get("current_round")
                                   != before.get("bilInfo", {}).get("current_round"))
                if moved:
                    print("  vòng đã chuyển sang vòng kế tiếp")
                    done += 1
                else:
                    print("  vòng CHƯA chuyển - kiểm tra lại bằng `run.py status`")
            else:
                done += 1
            _sleep(random.uniform(5, 12))
        print(f"đã làm xong {finished} bài, chốt {done} vòng bằng AI")
        if getattr(args, "keep_open", False):
            print("để cửa sổ mở, bấm Ctrl+C để thoát")
            while True:
                _sleep(5)
    return 0


# ------------------------------------------------------------------ presets
PROFILES = {
    # tên: pace, between, delay, turbo
    "careful": ([180.0, 420.0], [120.0, 480.0], [1.0, 3.0], False),
    "normal": ([60.0, 300.0], [30.0, 180.0], [0.3, 1.0], False),
    "fast": ([30.0, 90.0], [10.0, 40.0], [0.2, 0.6], False),
    "turbo": ([MIN_QUICK, MIN_QUICK], [0.0, 3.0], [0.05, 0.2], True),
}


def apply_profile(config: dict, name: str) -> dict:
    """Đặt nhịp chạy theo bộ có sẵn."""
    if name not in PROFILES:
        raise SystemExit(f"profile lạ: {name} (có: {', '.join(PROFILES)})")
    pace, between, delay, turbo = PROFILES[name]
    config.update(pace=list(pace), between=list(between), delay=list(delay), turbo=turbo)
    return config


MILESTONES = {
    # theo thông báo BTC: vòng 25 trước 02/03 (cấp tỉnh), vòng 30 trước vòng chung kết
    "province": (25, "02/03"),
    "national": (30, "20/03"),
}


def milestone(name: str, config: dict) -> tuple[int, str]:
    custom = (config.get("milestones") or {}).get(name)
    if custom:
        return int(custom[0]), str(custom[1])
    if name not in MILESTONES:
        raise SystemExit(f"mốc lạ: {name} (có: {', '.join(MILESTONES)})")
    return MILESTONES[name]

def cmd_plan(args) -> int:
    """Còn bao nhiêu vòng tới mốc, còn mấy ngày, có kịp không."""
    from . import planner, scheduler

    config = cfgmod.load(args.config)
    cfgmod.apply_endpoints(config)
    target, when = args.target, args.by
    if args.milestone:
        target, when = milestone(args.milestone, config)
    deadline = planner.parse_day(when)
    accounts = _accounts(args, config)
    if not accounts:
        print("chưa có tài khoản nào trong config")
        return 1
    print(f"mốc vòng {target} trước "
          f"{time.strftime('%d/%m/%Y %H:%M', time.localtime(deadline))}")
    worst = 0
    for user, password in accounts:
        try:
            client = _client(user, password, prompt=False)
            info = games.round_info(client)
        except ApiError as exc:
            print(f"{user}: không đọc được vòng - {exc}")
            continue
        current = int(info.get("current_round") or 0)
        opened = int(info.get("sysRound") or 0)
        plan = planner.outline(current, target, deadline, opened)
        level, line = planner.verdict(plan, target)
        mark = {"ok": "ok  ", "warn": "gấp ", "bad": "trễ "}[level]
        print(f"[{mark}] {user}: vòng {current}/{opened} mở - {line}")
        if level != "ok":
            worst = max(worst, 1 if level == "warn" else 2)
        if args.apply and plan["per_day"]:
            config.setdefault("_plan", {})[user] = plan["per_day"]
    if not args.apply:
        if worst:
            print("\nđặt lại nhịp: run.py plan ... --apply   (gỡ hạn chế max_rounds_per_day)")
        return 0

    planned = list((config.get("_plan") or {}).values()) or [1]
    per_day = max(int(value) for value in planned)
    config.pop("_plan", None)
    config["max_rounds_per_day"] = max(per_day, int(config.get("max_rounds_per_day") or 0))
    hours = planner.suggest_hours({"per_day": per_day})
    config["active_hours"] = hours
    cfgmod.save(config, args.config)
    print(f"\nđã ghi: {per_day} vòng/ngày, khung giờ {hours[0]}-{hours[1]}")
    if args.schedule:
        at = args.at or f"{hours[0]:02d}:30"
        scheduler.install(every=args.every, at=at)
        print(f"đã tạo lịch chạy {'mỗi ' + str(args.every) + ' phút' if args.every else 'lúc ' + at}")
    return 0


def cmd_watch(args) -> int:
    """Chờ vòng mới mở rồi tự làm - không cần Task Scheduler."""
    config = cfgmod.load(args.config)
    cfgmod.apply_endpoints(config)
    notify.configure((config.get("notify") or {}).get("telegram"))
    accounts = _accounts(args, config)
    if not accounts:
        print("chưa có tài khoản nào trong config")
        return 1
    print(f"theo dõi {len(accounts)} tài khoản, mỗi {args.poll} phút kiểm tra một lần")
    while not STOP.is_set():
        near = False
        for user, password in accounts:
            try:
                client = _client(user, password, prompt=False)
                pending = games.pending_exercises(client)
                opening = games.next_round_start(client)
            except ApiError as exc:
                print(f"{user}: {exc}", flush=True)
                continue
            if pending:
                print(f"{user}: có {len(pending)} bài -> chạy", flush=True)
                try:
                    _run_account(user, password, config, _load_bank(), only=None, single=True)
                except ApiError as exc:
                    print(f"{user}: lỗi khi chạy - {exc}", flush=True)
                near = True
            elif opening:
                when = time.strftime("%d/%m %H:%M", time.localtime(opening))
                left = max(0, int(opening - time.time()))
                print(f"{user}: chờ vòng mới lúc {when} "
                      f"(còn {left // 3600}h{left % 3600 // 60:02d}m)", flush=True)
                if opening - time.time() <= args.poll * 60:
                    near = True
            else:
                print(f"{user}: không có vòng đang mở, không rõ lịch", flush=True)
        if args.once or STOP.is_set():
            break
        _sleep(min(120, args.poll * 60) if near else args.poll * 60)
    return 0


def cmd_weak(args) -> int:
    """Bài chưa đạt điểm tối đa + dạng bài hay mất điểm."""
    weak = report.weak_spots(user=getattr(args, "user", "") or "", limit=args.limit)
    if not weak:
        print("Không có bài nào dưới điểm tối đa")
        return 0
    kinds: dict[int, list[int]] = {}
    for ex in weak:
        game = ex.get("gameId")
        if game is not None:
            kinds.setdefault(int(game), []).append(int(ex.get("mark") or 0))
    print(f"{'user':<18}{'bài':<8}{'game':<6}{'điểm':<10}{'khi'}")
    for ex in weak:
        mark = f"{ex.get('mark', 0)}/{ex.get('max', 0)}"
        print(f"{str(ex.get('user', '')):<18}{ex.get('idexercise', ''):<8}"
              f"{ex.get('gameId', ''):<6}{mark:<10}{ex.get('at') or ex.get('date') or ''}")
    if kinds:
        print("")
        print("Hay mất điểm nhất:")
        for game, marks in sorted(kinds.items(), key=lambda kv: len(kv[1]), reverse=True)[:5]:
            print(f"  game {game}: {len(marks)} bài, điểm thấp nhất {min(marks)}")
    return 0


def cmd_backup(args) -> int:
    """Nén data/ (kho đáp án, phiên, báo cáo) thành 1 file zip."""
    import zipfile

    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = args.out or os.path.join(DATA, f"backup-{stamp}.zip")
    keep = ("answers.json", "report.json", "config.json", "ioe-fallback.user.js")
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in keep:
            path = os.path.join(DATA, name)
            if os.path.exists(path):
                zf.write(path, name)
        for name in os.listdir(DATA):
            if name.startswith("session_") and name.endswith(".json"):
                zf.write(os.path.join(DATA, name), name)
    size = os.path.getsize(target) / 1024
    print(f"đã sao lưu -> {target} ({size:.0f} KB)")
    backups = sorted(f for f in os.listdir(DATA) if f.startswith("backup-") and f.endswith(".zip"))
    for old in backups[:-max(1, args.keep)]:
        os.unlink(os.path.join(DATA, old))
        print(f"  xoá bản cũ {old}")
    return 0


def cmd_restore(args) -> int:
    """Nhập lại từ file backup (mặc định không ghi đè bằng bản kém hơn)."""
    import zipfile

    if not os.path.exists(args.path):
        print(f"không thấy {args.path}")
        return 1
    with zipfile.ZipFile(args.path) as zf:
        names = zf.namelist()
        if "answers.json" in names:
            incoming = json.loads(zf.read("answers.json").decode("utf-8"))
            if args.replace:
                _save_bank(incoming)
                print(f"thay kho đáp án: {len(incoming)} câu")
            else:
                bank = _load_bank()
                added = 0
                for key, entry in incoming.items():
                    if key in bank and bank[key].get("point", 0) >= entry.get("point", 0):
                        continue
                    bank[key] = entry
                    added += 1
                _save_bank(bank)
                print(f"nhập thêm {added} câu (tổng {len(bank)})")
        for name in names:
            if name == "answers.json":
                continue
            path = os.path.join(DATA, name)
            if os.path.exists(path) and not args.replace:
                continue
            with open(path, "wb") as fh:
                fh.write(zf.read(name))
            print(f"  ghi {name}")
    return 0


def cmd_capture(args) -> int:
    """Bắt request thật của IOE (dùng để mò hợp đồng của bài nói)."""
    config = cfgmod.load(args.config)
    cfgmod.apply_endpoints(config)
    accounts = _accounts(args, config)
    if not accounts:
        print("chưa có tài khoản nào trong config")
        return 1
    user, password = accounts[0]
    out_dir = args.out or os.path.join(DATA, "capture")
    os.makedirs(out_dir, exist_ok=True)
    target = args.match or "/ioe-service/"
    saved = 0
    with cdp.Browser(port=args.port, profile=os.path.join(DATA, f"browser_{user}"),
                     headless=False, browser=args.browser or "") as browser:
        browser.send("Network.enable", {"maxTotalBufferSize": 100_000_000,
                                        "maxResourceBufferSize": 10_000_000})
        if not vision.login(browser, user, password):
            print("chưa đăng nhập được")
            return 3
        browser.goto(args.url or "https://ioe.vn/hoc-sinh/tu-luyen", 4)
        print(f"đang bắt request khớp {target!r}. Làm bài bằng tay trong cửa sổ vừa mở.")
        print(f"xong thì Ctrl+C, hoặc chờ {args.seconds}s.")
        end = time.time() + args.seconds
        while time.time() < end and not STOP.is_set():
            try:
                event = json.loads(browser._read_frame())
            except Exception:                                   # noqa: BLE001
                break
            params = event.get("params") or {}
            request = params.get("request") or {}
            url = str(request.get("url") or "")
            if event.get("method") != "Network.requestWillBeSent" or target not in url:
                continue
            body = request.get("postData") or ""
            if request.get("hasPostData") and not body:
                try:
                    body = browser.send("Network.getRequestPostData",
                                        {"requestId": params["requestId"]}).get("postData", "")
                except cdp.CdpError:
                    body = ""
            payload = {"url": url, "method": request.get("method"),
                       "headers": request.get("headers"), "body": body}
            name = f"{int(time.time())}-{url.rsplit('/', 1)[-1]}.json"
            with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1)
            saved += 1
            print(f"  {saved}. {url.rsplit('/', 1)[-1]} ({len(body)} ký tự)")
    print(f"đã lưu {saved} request vào {out_dir}")
    if saved:
        print("xem rồi chỉnh hợp đồng trong ioeauto/games.py (phần T_SPEAK)")
    return 0


def cmd_userscript(args) -> int:
    """Sinh userscript (phương án B: chạy bằng trình duyệt thật)."""
    bank = _load_bank()
    target = userscript.write(bank, args.out or "")
    print(f"đã ghi {target} ({userscript.known(bank)} đáp án nhúng)")
    print("cài: Tampermonkey -> Create new script -> dán nội dung file -> lưu")
    print("dùng: mở https://ioe.vn/hoc-sinh/tu-luyen -> bấm nút 'Chạy ezioe'")
    return 0


def cmd_config(args) -> int:
    path = cfgmod.write_example()
    print(f"Đã tạo {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ioe",
        description="Tự động làm bài tự luyện IOE (answer engine + ASR + anti-detection).",
    )
    parser.add_argument("-u", "--user", help="tài khoản IOE (bỏ trống = dùng data/config.json)")
    parser.add_argument("-p", "--password", help="mật khẩu IOE")
    parser.add_argument("--config", default=cfgmod.CONFIG, help="đường dẫn file cấu hình")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="xem các bài của vòng đang mở")
    p_status.set_defaults(func=cmd_status)

    p_run = sub.add_parser("run", help="làm bài (mặc định đọc data/config.json)")
    p_run.add_argument("--only", type=int, help="chỉ làm một idExercise")
    p_run.add_argument("--rounds", type=int, help="số vòng liên tiếp")
    p_run.add_argument("--delay", type=float, nargs=2, metavar=("MIN", "MAX"),
                       help="nghỉ ngẫu nhiên giữa các câu (giây)")
    p_run.add_argument("--between", type=float, nargs=2, metavar=("MIN", "MAX"),
                       help="nghỉ ngẫu nhiên giữa các vòng (giây)")
    p_run.add_argument("--pace", type=float, nargs=2, metavar=("MIN", "MAX"),
                       help="thời lượng ngẫu nhiên của mỗi bài, luôn < 10 phút (giây)")
    p_run.add_argument("--turbo", action="store_true", help="làm nhanh (~10-20s/bài)")
    p_run.add_argument("--dry-run", action="store_true", help="xem đề, không nộp bài")
    p_run.add_argument("--no-sleep", action="store_true", help="bỏ hết delay")
    p_run.add_argument("--jobs", type=int, help="số tài khoản chạy song song")
    p_run.add_argument("--no-oracle", action="store_true",
                       help="không gọi answercheck (dùng khi server đã bỏ chấm client)")
    p_run.add_argument("--fallback", choices=["none", "ai"], help="API chết thì chuyển phương án AI")
    p_run.add_argument("--force-ai", action="store_true", help="ép dùng phương án AI ngay")
    p_run.add_argument("--profile", choices=sorted(PROFILES), help="bộ nhịp chạy có sẵn")
    p_run.add_argument("--with-speaking", action="store_true",
                       help="làm cả bài tự chọn (kỹ năng nói, dùng TTS)")
    p_run.set_defaults(func=cmd_run)

    p_report = sub.add_parser("report", help="bảng kết quả các lượt đã chạy")
    p_report.add_argument("--limit", type=int, default=40)
    p_report.add_argument("--user", help="chỉ xem của một tài khoản")
    p_report.set_defaults(func=cmd_report)

    p_stats = sub.add_parser("stats", help="điểm theo ngày (biểu đồ + xuất CSV)")
    p_stats.add_argument("--days", type=int, default=14)
    p_stats.add_argument("--user", help="chỉ tính một tài khoản")
    p_stats.add_argument("--csv", help="ghi bảng ra file CSV")
    p_stats.set_defaults(func=cmd_stats)

    p_doc = sub.add_parser("doctor", help="tự kiểm tra môi trường, phiên, mạng")
    p_doc.add_argument("--no-accounts", action="store_true", help="bỏ qua kiểm tra đăng nhập")
    p_doc.set_defaults(func=cmd_doctor)

    p_sch = sub.add_parser("schedule", help="tạo lịch chạy tự động (Task Scheduler/cron)")
    p_sch.add_argument("--at", default="09:00", help="giờ chạy mỗi ngày HH:MM")
    p_sch.add_argument("--every", type=int, help="chạy mỗi N phút thay vì mỗi ngày")
    p_sch.add_argument("--dry-run", action="store_true", help="chỉ in lệnh, không tạo")
    p_sch.set_defaults(func=cmd_schedule)

    p_uns = sub.add_parser("unschedule", help="xoá lịch đã tạo")
    p_uns.add_argument("--dry-run", action="store_true")
    p_uns.set_defaults(func=cmd_unschedule)

    p_gui = sub.add_parser("gui", help="mở giao diện")
    p_gui.set_defaults(func=cmd_gui)

    p_balance = sub.add_parser("balance", help="xem RUBY / coin")
    p_balance.set_defaults(func=cmd_balance)

    p_reset = sub.add_parser("reset", help="huỷ kết quả vòng đang mở")
    p_reset.set_defaults(func=cmd_reset)

    p_proxy = sub.add_parser("proxy", help="chạy MITM proxy để xem đáp án stream về trình duyệt")
    p_proxy.add_argument("--port", type=int, default=8080)
    p_proxy.add_argument("--bank", action="store_true", help="ghi đáp án bắt được vào data/answers.json")
    p_proxy.set_defaults(func=cmd_proxy)

    p_caps = sub.add_parser("capabilities", help="dò lớp nào còn sống (đáp án/oracle) + gợi ý")
    p_caps.add_argument("--exercise", type=int, help="idExercise để dò (bỏ trống = bài đầu vòng)")
    p_caps.set_defaults(func=cmd_capabilities)

    p_eps = sub.add_parser("endpoints", help="xem/đổi endpoint, tự dò khi IOE đổi API")
    p_eps.add_argument("--discover", action="store_true", help="dò bundle JS của ioe.vn")
    p_eps.add_argument("--set", nargs="*", metavar="KEY=VALUE",
                       help="ghi đè, vd ioe_service=/ioe-service/v4")
    p_eps.add_argument("--dry-run", action="store_true")
    p_eps.set_defaults(func=cmd_endpoints)

    p_schema = sub.add_parser("schema", help="so schema response với fixture (phát hiện API đổi)")
    p_schema.set_defaults(func=cmd_schema)

    p_bank = sub.add_parser("bank", help="xuất/nhập/thống kê kho đáp án")
    p_bank.add_argument("action", choices=["stats", "export", "import", "prune", "show"])
    p_bank.add_argument("path", nargs="?")
    p_bank.add_argument("--limit", type=int, default=20, help="số dòng tối đa khi `bank show`")
    p_bank.set_defaults(func=cmd_bank)

    p_plan = sub.add_parser("plan", help="tính số vòng/ngày tới mốc và hạn chót")
    p_plan.add_argument("--target", type=int, default=25, help="vòng đích (mặc định 25)")
    p_plan.add_argument("--by", default="02/03", help="hạn chót DD/MM hoặc DD/MM/YYYY")
    p_plan.add_argument("--milestone", choices=sorted(MILESTONES),
                        help="dùng mốc có sẵn thay cho --target/--by")
    p_plan.add_argument("--apply", action="store_true", help="ghi max_rounds_per_day + khung giờ")
    p_plan.add_argument("--schedule", action="store_true", help="tạo lịch chạy luôn")
    p_plan.add_argument("--at", help="giờ chạy (mặc định theo khung giờ gợi ý)")
    p_plan.add_argument("--every", type=int, help="chạy mỗi N phút")
    p_plan.set_defaults(func=cmd_plan)

    p_watch = sub.add_parser("watch", help="chờ vòng mới rồi tự làm (không cần Task Scheduler)")
    p_watch.add_argument("--poll", type=int, default=15, help="số phút giữa hai lần kiểm tra")
    p_watch.add_argument("--once", action="store_true", help="kiểm tra một lần rồi thoát")
    p_watch.set_defaults(func=cmd_watch)

    p_weak = sub.add_parser("weak", help="bài chưa đạt điểm tối đa + dạng bài hay sai")
    p_weak.add_argument("--limit", type=int, default=20)
    p_weak.add_argument("--user", help="chỉ xem của một tài khoản")
    p_weak.set_defaults(func=cmd_weak)

    p_backup = sub.add_parser("backup", help="nén data/ thành 1 file zip")
    p_backup.add_argument("--out", help="đường dẫn file zip")
    p_backup.add_argument("--keep", type=int, default=7, help="số bản backup giữ lại")
    p_backup.set_defaults(func=cmd_backup)

    p_restore = sub.add_parser("restore", help="nhập lại từ file backup")
    p_restore.add_argument("path")
    p_restore.add_argument("--replace", action="store_true", help="ghi đè cả file đã có")
    p_restore.set_defaults(func=cmd_restore)

    p_capture = sub.add_parser("capture", help="bắt request thật của IOE (mò hợp đồng bài nói)")
    p_capture.add_argument("--seconds", type=int, default=180)
    p_capture.add_argument("--match", help="chuỗi cần có trong URL (mặc định /ioe-service/)")
    p_capture.add_argument("--url", help="trang mở đầu")
    p_capture.add_argument("--out", help="thư mục lưu request")
    p_capture.add_argument("--browser", help="đường dẫn chrome/edge")
    p_capture.add_argument("--port", type=int, default=9222)
    p_capture.set_defaults(func=cmd_capture)

    p_ai = sub.add_parser("ai", help="phương án C: trình duyệt thật + AI nhìn ảnh bấm")
    p_ai.add_argument("--rounds", type=int, default=1)
    p_ai.add_argument("--dry-run", action="store_true", help="chỉ xem AI chọn gì, không bấm")
    p_ai.add_argument("--browser", help="đường dẫn chrome/edge")
    p_ai.add_argument("--headless", action="store_true")
    p_ai.add_argument("--port", type=int)
    p_ai.add_argument("--profile", help="thư mục profile trình duyệt (giữ đăng nhập)")
    p_ai.add_argument("--max-steps", type=int)
    p_ai.add_argument("--with-speaking", action="store_true")
    p_ai.add_argument("--keep-open", action="store_true", help="giữ cửa sổ sau khi xong")
    p_ai.add_argument("--login-only", action="store_true", help="chỉ đăng nhập rồi thoát (thử CDP)")
    p_ai.add_argument("--exercise", type=int, help="chỉ làm đúng một idExercise")
    p_ai.set_defaults(func=cmd_ai)

    p_user = sub.add_parser("userscript", help="sinh userscript chạy trong trình duyệt (phương án B)")
    p_user.add_argument("--out", help="đường dẫn file .user.js")
    p_user.set_defaults(func=cmd_userscript)

    p_config = sub.add_parser("config", help="tạo data/config.example.json")
    p_config.set_defaults(func=cmd_config)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ApiError as exc:
        print(f"lỗi API: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
