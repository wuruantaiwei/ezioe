"""Configuration file (``data/config.json``) for unattended runs.

Everything the CLI used to take as flags can live here, so a scheduled run
needs no arguments at all::

    python run.py run
"""

from __future__ import annotations

import copy
import json
import os

from .paths import app_root, data_dir

ROOT = app_root()
DATA = data_dir()
CONFIG = os.path.join(DATA, "config.json")
EXAMPLE = os.path.join(DATA, "config.example.json")

DEFAULTS: dict = {
    "accounts": [],
    "rounds": 1,
    "only": None,
    "pace": [60, 300],
    "between": [30, 180],
    "delay": [0.3, 1.0],
    "attempts": 3,
    "jobs": 1,
    "turbo": False,
    "oracle": True,
    "aihint": {"enabled": True},          # AI gợi ý từ cho câu giấu cả từ

    "fallback": "none",       # "ai" = khi API chết thì mở trình duyệt cho AI bấm
    "vision": {
        "provider": "",       # openai | gemini | anthropic (rỗng = đoán theo biến môi trường)
        "model": "",
        "key_env": "EZIOE_AI_KEY",
        "base": "",
        "max_steps": 140,
        "headless": False,
        "browser": "",
        "port": 9222,
        "profile": "",
        "zoom": True,
    },          # False = không gọi answercheck (khi server bỏ chấm client)
    "dry_run": False,
    "active_hours": [0, 24],
    "max_rounds_per_day": 0,
    "wait_for_round": True,
    "notify": {"telegram": {"token": "", "chat_id": ""}},
    "speaking": {
        "tts": True,
        "voice": "en-US-AriaNeural",   # giọng neural (edge-tts); "" = giọng SAPI mặc định
        "submit": False,
        "play": False,
    },
    "endpoints": {},
}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load(path: str = CONFIG) -> dict:
    if not os.path.exists(path):
        return copy.deepcopy(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as fh:
            return _merge(DEFAULTS, json.load(fh))
    except (OSError, ValueError):
        return copy.deepcopy(DEFAULTS)


def save(config: dict, path: str = CONFIG) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False, indent=1)


def apply_endpoints(config: dict) -> None:
    """Cho phép vá URL từ config hoặc từ `data/endpoints.json` (sống qua mọi lần API đổi)."""
    from . import api, discovery

    endpoints: dict = {}
    endpoints.update(discovery.load())
    endpoints.update(config.get("endpoints") or {})
    if endpoints.get("api"):
        api.API_BASE = endpoints["api"].rstrip("/")
    if endpoints.get("identity"):
        api.IDENTITY_BASE = endpoints["identity"].rstrip("/")
    if endpoints.get("ioe_service"):
        api.set_service(endpoints["ioe_service"])
    if endpoints.get("game"):
        api.GAME_ROOT = endpoints["game"]
    elif endpoints.get("ioe_service"):
        api.GAME_ROOT = api.API_BASE + api.GAME_SUFFIX


def write_example() -> str:
    example = copy.deepcopy(DEFAULTS)
    example["accounts"] = [{"user": "SVTBTranVanBinh", "password": "mat-khau"}]
    example["notify"]["telegram"] = {"token": "123456:ABC-DEF", "chat_id": "123456789"}
    os.makedirs(DATA, exist_ok=True)
    with open(EXAMPLE, "w", encoding="utf-8") as fh:
        json.dump(example, fh, ensure_ascii=False, indent=1)
    return EXAMPLE
