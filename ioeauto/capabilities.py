"""Lớp phòng thủ: dò xem IOE còn hở gì, và tự đổi cách chơi theo cái còn sống.

Thứ tự ưu tiên khi làm một câu (càng xuống càng chậm nhưng càng khó bị vá):

    1. `ans[]` server gửi xuống            - nhanh nhất, chết nếu IOE bỏ đáp án
    2. kho đáp án `data/answers.json`       - sống qua mọi lần đổi API
    3. `answercheck` (oracle)               - chết nếu chấm chuyển hẳn về server
    4. ASR offline + từ điển/ngữ pháp        - chỉ cần đề, không cần server

`probe()` trả về cái nào còn dùng được; `run` dùng nó để bỏ qua bước đã chết
(không gọi 40 lần oracle vô ích) và in ra "capability report".
"""

from __future__ import annotations

import json
import os

from . import games, paths
from .api import ApiError

CACHE = os.path.join(paths.data_dir(), "capabilities.json")


def probe(client=None, exercise: dict | None = None) -> dict:
    """Đo khả năng hiện tại của API. Không cần tài khoản nếu chỉ xem round API."""
    out = {"round_api": False, "answers_in_payload": None, "oracle": None,
           "schedule": None, "exercises": 0, "checked": ""}
    if client is None:
        return out

    try:
        payload = games.round_payload(client)
        out["round_api"] = True
        out["exercises"] = len(payload["games"])
    except ApiError as exc:
        out["round_api"] = False
        out["checked"] = str(exc)[:160]
        return out

    try:
        out["schedule"] = bool(client.schedule())
    except ApiError:
        out["schedule"] = False

    candidates = [exercise] if exercise else [e for e in payload["games"] if e.get("isRank")]
    session = None
    last_error = ""
    for target in candidates[:3]:
        try:
            session = games.Session(client, target["idexercise"], target["gameId"]).open()
            break
        except ApiError as exc:
            last_error = str(exc)
    if session is None:
        out["checked"] = f"setinfo/getinfo lỗi: {last_error}"[:160]
        return out

    out["answers_in_payload"] = any(
        any(a.get("content", "").strip() for a in question.get("ans") or [])
        for question in session.questions
    )
    if not session.questions:
        return out
    question = session.questions[0]
    try:
        # gửi một đáp án sai chắc chắn: oracle còn sống thì trả 0, chết thì lỗi
        session.start()
        points = session.check(question, "__ezioe__", 1)
        out["oracle"] = points == 0
        if not out["oracle"]:
            out["checked"] = "answercheck trả điểm lạ cho đáp án sai"
    except ApiError as exc:
        out["oracle"] = False
        out["checked"] = f"answercheck lỗi: {exc}"[:160]
    out["checked"] = out["checked"] or "ok"
    return out


def advice(caps: dict) -> list[str]:
    """Gợi ý nên bật/tắt gì khi một lớp đã chết."""
    tips = []
    if not caps.get("round_api"):
        tips.append("API vòng lỗi: chạy `run.py endpoints --discover` rồi `run.py doctor`.")
    if caps.get("answers_in_payload") is False:
        tips.append("Server không gửi đáp án nữa: dựa vào kho + oracle + ASR; "
                    "nên chạy `run.py bank export` để giữ kho.")
    if caps.get("oracle") is False:
        tips.append("`answercheck` chết: điểm sẽ tụt, cân nhắc thêm kho đáp án "
                    "(`run.py bank import`) hoặc phương án B (userscript).")
    if caps.get("schedule") is False:
        tips.append("Lịch mở vòng không đọc được: bỏ `wait_for_round` để khỏi ngủ sai giờ.")
    return tips


def save(caps: dict) -> None:
    if not caps.get("at"):
        from datetime import datetime

        caps = {**caps, "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as fh:
        json.dump(caps, fh, ensure_ascii=False, indent=1)


def load() -> dict:
    try:
        with open(CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def fresh(max_hours: float = 48.0) -> dict:
    """Cache còn mới không; cũ quá thì bỏ (IOE vá xong có thể đã hồi)."""
    caps = load()
    stamp = caps.get("at") or ""
    if not stamp:
        return {}
    try:
        from datetime import datetime

        age = (datetime.now() - datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")).total_seconds()
    except ValueError:
        return {}
    return caps if age <= max_hours * 3600 else {}


def report(caps: dict) -> str:
    rows = [
        ("API vòng", caps.get("round_api")),
        ("đáp án trong payload", caps.get("answers_in_payload")),
        ("answercheck (oracle)", caps.get("oracle")),
        ("lịch mở vòng", caps.get("schedule")),
    ]
    lines = [f"  {name}: " + {True: "còn", False: "MẤT", None: "chưa rõ"}[value]
             for name, value in rows]
    return "\n".join(lines)
