"""Gợi ý đáp án bằng AI (chỉ văn bản) khi bank và từ điển bó tay.

Nguyên tắc: AI không bao giờ được tin tuyệt đối. Nó chỉ *đưa thêm ứng viên*,
oracle của server vẫn là trọng tài — đúng thì lưu vào bank, sai thì mất một
request. Vì vậy bật tính năng này không làm giảm độ chính xác.
"""

from __future__ import annotations

import json
import re

from . import games, vision

MAX_HINTS = 12
WORD_EDGE = ".,;:" + chr(39) + chr(34) + "()"
PROMPT = """Bạn là giáo viên tiếng Anh. Điền một từ vào chỗ trống (đã thay bằng dấu *).

Câu: {sentence}
Từ cần điền dài đúng {length} chữ cái{pos}{shape}.
Chỉ trả JSON: {{"candidates": ["từ1", "từ2", ...]}} — tối đa {limit} từ, xếp từ
khả năng cao nhất trước, mỗi từ đúng {length} chữ cái, chữ thường, không giải thích."""

POS_HINT = {
    "verb": ", là động từ (chia đúng thì của câu)",
    "adjective": ", là tính từ",
    "noun": ", là danh từ",
    "adverb": ", là trạng từ",
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _parse(raw: str, length: int, pattern: str = "") -> list[str]:
    """Đọc JSON (hoặc danh sách dòng) và chỉ giữ từ đúng độ dài."""
    words: list[str] = []
    raw = (raw or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(raw[start:end + 1])
            if isinstance(data, dict):
                words = [str(w) for w in data.get("candidates") or []]
            elif isinstance(data, list):
                words = [str(w) for w in data]
        except ValueError:
            words = []
    if not words:
        words = re.findall(r"[A-Za-z]+", raw)
    out: list[str] = []
    for word in words:
        low = word.strip(WORD_EDGE).lower()
        if len(low) != length or not low.isalpha() or low in out:
            continue
        if pattern and not re.fullmatch(pattern, low):
            continue
        if True:
            out.append(low)
    return out[:MAX_HINTS]


def _slot_kind(question: dict) -> str:
    before, after = games._context(question)
    if not after:
        return ""
    if before.endswith("ly"):
        return "verb"
    if before in {"a", "an", "the", "this", "that", "my", "his", "her", "their", "our", "its"}:
        return "noun"
    return ""


def candidates(question: dict, config: dict, limit: int = MAX_HINTS,
               skip: list[str] | None = None) -> list[str]:
    """Ứng viên do AI nghĩ ra cho một chỗ trống đang bị giấu cả từ."""
    if not (config.get("aihint") or {}).get("enabled", True):
        return []
    sentence, length = games.gap_sentence(question)
    if not length or sentence.count("*") != length:
        return []
    token = str(question["content"]["content"] or "").strip()
    partial = token if (token and "*" in token and " " not in token
                        and set(token) != {"*"}) else ""
    pattern = "".join("[a-z]" if ch == "*" else re.escape(ch) for ch in partial.lower())
    prompt = PROMPT.format(sentence=_clean(sentence), length=length,
                           pos=POS_HINT.get(_slot_kind(question), ""),
                           shape=f", và có dạng {partial.upper()}" if partial else "",
                           limit=max(3, min(limit, MAX_HINTS)))
    try:
        raw = vision.ask_text(prompt, config)
    except Exception:                        # noqa: BLE001 - AI hỏng thì bỏ qua
        return []
    bad = {w.lower() for w in (skip or [])}
    return [w for w in _parse(raw, length, pattern) if w not in bad]


REFINE = """Câu tiếng Anh sau do máy nghe tự động ghi lại, có thể sai chính tả hoặc
thiếu từ. Hãy sửa lại cho đúng chính tả và ngữ pháp, giữ nguyên ý và độ dài xấp xỉ.
Chỉ trả JSON: {{"text": "câu đã sửa"}}.

Câu: {text}"""

FILL = """Bạn là giáo viên tiếng Anh. Điền {count} chỗ trống (đánh dấu *) trong câu,
mỗi chỗ đúng số chữ cái ghi kèm. Chỉ trả JSON:
{{"words": ["từ1", "từ2"]}} theo đúng thứ tự chỗ trống.

Câu: {sentence}
Độ dài từng chỗ: {sizes}"""


def refine(transcript: str, config: dict) -> list[str]:
    """Bản sửa chính tả/ngữ pháp cho câu nghe được (dạng nghe - chép)."""
    text = _clean(transcript)
    if not text or not (config.get("aihint") or {}).get("enabled", True):
        return []
    try:
        raw = vision.ask_text(REFINE.format(text=text), config)
    except Exception:                        # noqa: BLE001
        return []
    start, end = raw.find("{"), raw.rfind("}")
    fixed = ""
    if start >= 0 and end > start:
        try:
            fixed = _clean(str((json.loads(raw[start:end + 1]) or {}).get("text") or ""))
        except ValueError:
            fixed = ""
    fixed = fixed or _clean(raw)
    out = []
    for candidate in (fixed, fixed.rstrip("."), fixed.capitalize()):
        if candidate and candidate.lower() != text.lower() and candidate not in out:
            out.append(candidate)
    return out[:3]


def fill(sentence: str, sizes: list[int], config: dict) -> list[str]:
    """Cả câu đã điền, do AI nghĩ (câu điền nhiều chỗ trống)."""
    if not sizes or not (config.get("aihint") or {}).get("enabled", True):
        return []
    prompt = FILL.format(count=len(sizes), sentence=_clean(sentence), sizes=sizes)
    try:
        raw = vision.ask_text(prompt, config)
    except Exception:                        # noqa: BLE001
        return []
    start, end = raw.find("{"), raw.rfind("}")
    words: list[str] = []
    if start >= 0 and end > start:
        try:
            words = [str(w) for w in (json.loads(raw[start:end + 1]) or {}).get("words") or []]
        except ValueError:
            words = []
    words = [w.strip().lower() for w in words][:len(sizes)]
    if len(words) != len(sizes) or any(len(w) != n for w, n in zip(words, sizes)):
        return []
    return ["|".join(words)]
