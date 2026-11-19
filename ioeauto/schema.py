"""Bắt thay đổi schema: IOE đổi field là tool biết ngay thay vì chết lặng.

Mỗi lần lấy dữ liệu quan trọng (vòng tự luyện, getinfo), ta lưu *hình dạng*
(các đường dẫn key) vào `data/fixtures/`. Lần sau đọc lại mà thiếu key cũ /
mọc key lạ thì `drift()` báo, `doctor` hiện cảnh báo, `run` vẫn cố chạy tiếp.

Không lưu nội dung bài làm (chỉ key + độ dài) để file nhỏ và không lộ đề.
"""

from __future__ import annotations

import json
import os

from . import paths

MAX_KEYS = 400


def _dir() -> str:
    path = os.path.join(paths.data_dir(), "fixtures")
    os.makedirs(path, exist_ok=True)
    return path


def shape(value, prefix: str = "", out: list | None = None, depth: int = 0) -> list[str]:
    """Tập đường dẫn key của một payload, dừng ở MAX_KEYS để khỏi phình."""
    out = [] if out is None else out
    if len(out) >= MAX_KEYS or depth > 6:
        return out
    if isinstance(value, dict):
        for key in sorted(value):
            path = f"{prefix}.{key}" if prefix else str(key)
            out.append(path)
            shape(value[key], path, out, depth + 1)
    elif isinstance(value, list):
        path = f"{prefix}[]"
        out.append(path)
        for item in value[:3]:
            shape(item, path, out, depth + 1)
    return out


def _path(name: str) -> str:
    return os.path.join(_dir(), f"{name}.json")


def record(name: str, payload) -> list[str]:
    """Lưu hình dạng mới nhất; trả về danh sách khác biệt so với lần trước."""
    keys = shape(payload)
    old = {}
    try:
        with open(_path(name), encoding="utf-8") as fh:
            old = json.load(fh)
    except (OSError, ValueError):
        pass
    diff = compare(old.get("keys") or [], keys) if old.get("keys") else []
    if diff or not old:
        with open(_path(name), "w", encoding="utf-8") as fh:
            json.dump({"keys": keys, "at": _now()}, fh, ensure_ascii=False, indent=1)
    return diff


def compare(old: list[str], new: list[str]) -> list[str]:
    old_set, new_set = set(old), set(new)
    missing = sorted(old_set - new_set)
    added = sorted(new_set - old_set)
    out = [f"- mất: {key}" for key in missing]
    out += [f"+ lạ: {key}" for key in added]
    return out


def drift(name: str, payload) -> list[str]:
    """So payload hiện tại với fixture đã lưu (không ghi đè)."""
    try:
        with open(_path(name), encoding="utf-8") as fh:
            old = json.load(fh).get("keys") or []
    except (OSError, ValueError):
        return []
    return compare(old, shape(payload))


def names() -> list[str]:
    try:
        return sorted(f[:-5] for f in os.listdir(_dir()) if f.endswith(".json"))
    except OSError:
        return []


def _now() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
