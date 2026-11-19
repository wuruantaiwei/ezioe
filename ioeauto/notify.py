"""Optional Telegram notifications (progress on the phone)."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

_state: dict = {"token": "", "chat_id": "", "enabled": False}


def configure(telegram: dict | None) -> None:
    telegram = telegram or {}
    _state["token"] = str(telegram.get("token") or "")
    _state["chat_id"] = str(telegram.get("chat_id") or "")
    _state["enabled"] = bool(_state["token"] and _state["chat_id"])


def enabled() -> bool:
    return bool(_state["enabled"])


def send(text: str, timeout: int = 15) -> bool:
    """Fire-and-forget message; failures never break a run."""
    if not enabled():
        return False
    url = f"https://api.telegram.org/bot{_state['token']}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": _state["chat_id"],
        "text": text[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=timeout) as resp:
            json.loads(resp.read().decode() or "{}")
        return True
    except Exception:
        return False
