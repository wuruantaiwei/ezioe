"""Kế hoạch chạy theo hạn: bao nhiêu vòng nữa, còn mấy ngày, có kịp không.

IOE chỉ mở thêm một vòng mỗi tuần nên "chạy bao nhiêu vòng/ngày" mới là thứ
quyết định có kịp mốc hay không (vòng 25 trước 02/03, vòng 30 trước vòng chung
kết...). Lệnh `run.py plan` biến mốc + hạn thành con số cụ thể, và tự đặt
`max_rounds_per_day` / lịch chạy khi được yêu cầu.
"""

from __future__ import annotations

import time

DAY = 86400.0


def parse_day(text: str) -> float:
    """`02/03`, `02/03/2027`, `2027-03-02` -> mốc thời gian (cuối ngày)."""
    text = text.strip().replace("-", "/")
    parts = [p for p in text.split("/") if p]
    if len(parts) == 2:
        day, month = (int(p) for p in parts)
        now = time.localtime()
        year = now.tm_year
        stamp = time.mktime((year, month, day, 23, 59, 0, 0, 0, -1))
        if stamp < time.time() - 30 * DAY:            # mốc đã qua thì hiểu là năm sau
            stamp = time.mktime((year + 1, month, day, 23, 59, 0, 0, 0, -1))
        return stamp
    if len(parts) == 3:
        if len(parts[0]) == 4:                 # YYYY-MM-DD
            year, month, day = (int(p) for p in parts)
        else:
            day, month, year = (int(p) for p in parts)
        if year < 100:
            year += 2000
        return time.mktime((year, month, day, 23, 59, 0, 0, 0, -1))
    raise ValueError(f"không hiểu ngày {text!r} (dùng DD/MM hoặc DD/MM/YYYY)")


MAX_ROUND = 35


def outline(current: int, target: int, deadline: float, opened: int,
            now: float | None = None, max_round: int = MAX_ROUND) -> dict:
    """Tính kế hoạch cho một tài khoản.

    Hệ thống mở thêm đúng một vòng mỗi tuần, nên trần thật không phải ``opened``
    hiện tại mà là số vòng sẽ mở được trước hạn (tối đa ``max_round``).
    """
    now = now or time.time()
    left = max(0, target - current)
    days = max(1, int((deadline - now) // DAY) + 1)
    weeks = max(0, int((deadline - now) // (7 * DAY)))
    available = min(max_round, opened + weeks)
    reachable = max(0, min(left, available - current))
    rate = left / days
    return {
        "left": left,
        "available": available,
        "days": days,
        "rate": rate,
        "per_day": max(1, -(-left // days)) if left else 0,
        "reachable": reachable,
        "blocked": left > reachable,
        "late_days": (int((left - reachable) // max(1, rate)) if left > reachable and rate else 0),
    }


def verdict(plan: dict, target: int) -> tuple[str, str]:
    """(mức, câu kết luận) - mức dùng để tô màu / chọn thông báo."""
    if plan["left"] == 0:
        return "ok", f"đã tới vòng {target}"
    if plan["blocked"]:
        return "bad", (f"thiếu {plan['left'] - plan['reachable']} vòng so với số vòng hệ thống "
                       f"sẽ mở được trước hạn (tối đa {plan['available']}) - phải chờ, "
                       f"kịp thì vẫn phải chạy đúng nhịp")
    if plan["rate"] > 1:
        return "warn", f"cần {plan['rate']:.1f} vòng/ngày - phải chạy đều mới kịp"
    return "ok", f"cần {plan['rate']:.2f} vòng/ngày - thoải mái"


def suggest_hours(plan: dict) -> list[int]:
    """Khung giờ gợi ý: nhiều vòng/ngày thì mở rộng cửa sổ chạy."""
    if plan["per_day"] >= 3:
        return [6, 23]
    if plan["per_day"] == 2:
        return [8, 22]
    return [9, 21]
