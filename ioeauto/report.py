"""Run history: what was played, how long it took, where points were lost.

Reads are cheap, writes are serialised: several accounts may run in parallel
(``--jobs``) and they all append here.
"""

from __future__ import annotations

import json
import os
import threading
import time

from .paths import data_dir

DATA = data_dir()
REPORT = os.path.join(DATA, "report.json")

# the file is small, but the GUI, the scheduler and parallel jobs all write it
_LOCK = threading.RLock()
# keep the history bounded: 4000 exercise rows / 120 days is plenty for stats
MAX_RUNS = 4000
MAX_DAYS = 120


def _load() -> dict:
    if not os.path.exists(REPORT):
        return {"runs": []}
    try:
        with open(REPORT, encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("runs", [])
        return data
    except (OSError, ValueError):
        return {"runs": []}


def _save(data: dict) -> None:
    os.makedirs(DATA, exist_ok=True)
    runs = data.get("runs") or []
    if len(runs) > MAX_RUNS:
        runs = runs[-MAX_RUNS:]
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - MAX_DAYS * 86400))
    runs = [r for r in runs if str(r.get("date") or "") >= cutoff]
    data["runs"] = runs
    tmp = REPORT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, REPORT)


def _who(user: str) -> str:
    """Account keys are always compared lower-cased, so "Acc" == "acc"."""
    return (user or "").strip().lower()


def exercises_today() -> int:
    today = time.strftime("%Y-%m-%d")
    with _LOCK:
        return sum(len(run.get("exercises", []))
                   for run in _load()["runs"] if run.get("date") == today)


def rounds_today(user: str = "") -> int:
    """Rounds finished today - per account, so accounts do not block each other."""
    today = time.strftime("%Y-%m-%d")
    who = _who(user)
    with _LOCK:
        return sum(
            1 for run in _load()["runs"]
            if run.get("date") == today and run.get("round_done")
            and (not who or _who(run.get("user")) == who)
        )


def add_exercise(user: str, exercise: dict, result: dict, seconds: float, attempts: int = 1) -> dict:
    entry = {
        "date": time.strftime("%Y-%m-%d"),
        "time": time.strftime("%H:%M:%S"),
        "user": _who(user),
        "idexercise": exercise.get("idexercise"),
        "gameId": exercise.get("gameId"),
        "mark": (result or {}).get("point", 0),
        "max": (result or {}).get("examScore", 0),
        "pass": bool((result or {}).get("isPass")),
        "seconds": round(seconds, 1),
        "attempts": attempts,
    }
    with _LOCK:
        data = _load()
        data["runs"].append({"date": entry["date"], "user": entry["user"],
                             "exercises": [entry], "round_done": False,
                             "started": entry["time"]})
        _save(data)
    return entry


def mark_round(user: str, done: bool, note: str = "") -> None:
    """Flag the pending run of THIS account today (a marker run when it had none).

    Only runs that are not flagged yet are touched, so a failed extra attempt can
    never un-flag an earlier finished round.
    """
    today = time.strftime("%Y-%m-%d")
    who = _who(user)
    with _LOCK:
        data = _load()
        for run in reversed(data["runs"]):
            if (_who(run.get("user")) == who and run.get("date") == today
                    and not run.get("round_done")):
                run["round_done"] = done
                if note:
                    run["note"] = note
                _save(data)
                return
        if done:
            data["runs"].append({"date": today, "time": time.strftime("%H:%M:%S"),
                                 "user": who, "exercises": [], "round_done": True,
                                 "started": "", "note": note})
            _save(data)


def _rows(limit: int, user: str = "") -> list[dict]:
    """Last `limit` exercise rows of `user` (filter first, slice after)."""
    who = _who(user)
    rows = []
    with _LOCK:
        runs = _load()["runs"]
    for run in reversed(runs):
        if who and _who(run.get("user")) != who:
            continue
        for ex in reversed(run.get("exercises", [])):
            rows.append({
                "date": ex.get("date", ""),
                "time": ex.get("time", ""),
                "user": ex.get("user", ""),
                "exercise": ex.get("idexercise"),
                "game": ex.get("gameId"),
                "mark": f"{ex.get('mark', 0)}/{ex.get('max', 0)}",
                "pass": "OK" if ex.get("pass") else "FAIL",
                "seconds": ex.get("seconds", 0),
            })
            if len(rows) >= limit:
                return rows
    return rows


def summary(limit: int = 20, user: str = "") -> list[dict]:
    """Compact ``date | user | game | mark/max | pass | seconds`` rows (newest last)."""
    return list(reversed(_rows(limit, user)))


def weak_spots(user: str = "", limit: int = 20) -> list[dict]:
    """Exercises that did not reach full marks - worth another attempt."""
    out = []
    who = _who(user)
    with _LOCK:
        runs = _load()["runs"]
    for run in runs:
        if who and _who(run.get("user")) != who:
            continue
        for ex in run.get("exercises", []):
            if ex.get("max") and ex.get("mark", 0) < ex["max"]:
                out.append(ex)
    return out[-limit:]


def stats(days: int = 14, user: str = "") -> list[dict]:
    """Per-day aggregation for the score chart (oldest first)."""
    who = _who(user)
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - max(0, days - 1) * 86400))
    days_map: dict[str, dict] = {}
    with _LOCK:
        runs = _load()["runs"]
    for run in runs:
        date = str(run.get("date") or "")
        if date < cutoff or (who and _who(run.get("user")) != who):
            continue
        day = days_map.setdefault(date, {"date": date, "rounds": 0, "exercises": 0,
                                         "passed": 0, "points": 0, "max": 0, "seconds": 0.0})
        if run.get("round_done"):
            day["rounds"] += 1
        for ex in run.get("exercises", []):
            day["exercises"] += 1
            day["points"] += int(ex.get("mark") or 0)
            day["max"] += int(ex.get("max") or 0)
            day["seconds"] += float(ex.get("seconds") or 0)
            if ex.get("pass"):
                day["passed"] += 1
    out = []
    for date in sorted(days_map):
        day = days_map[date]
        day["ratio"] = (day["points"] / day["max"]) if day["max"] else 0.0
        out.append(day)
    return out
