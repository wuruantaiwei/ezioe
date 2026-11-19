"""Offline speech-to-text for the listening games.

The listening exercises stream the recording from a public mp3 URL, so the
answer can simply be *heard*: the audio is transcribed locally with Vosk
(no API key, no browser) and the transcript is used as the answer, or aligned
against the masked sentence to get the missing word.

Model: https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
extracted into ``data/asr/`` — see ``ensure_model()``.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import threading
import urllib.request
import zipfile

from .paths import data_dir

DATA = data_dir()
ASR_DIR = os.path.join(DATA, "asr")
MODEL_DIR = os.path.join(ASR_DIR, "vosk-model-small-en-us-0.15")
MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
CACHE = os.path.join(DATA, "asr_cache.json")

logging.getLogger("vosk").setLevel(logging.ERROR)

_model = None
_cache: dict[str, str] | None = None
_CACHE_LOCK = threading.Lock()


def available() -> bool:
    return os.path.isdir(MODEL_DIR)


def ensure_model() -> bool:
    """Download the small English model once (~40 MB)."""
    if available():
        return True
    try:
        os.makedirs(ASR_DIR, exist_ok=True)
        with urllib.request.urlopen(MODEL_URL, timeout=180) as resp:
            blob = resp.read()
        zipfile.ZipFile(io.BytesIO(blob)).extractall(ASR_DIR)
    except Exception:
        return False
    return available()


def _load_cache() -> dict[str, str]:
    global _cache
    with _CACHE_LOCK:
        if _cache is None:
            try:
                with open(CACHE, encoding="utf-8") as fh:
                    _cache = json.load(fh)
            except Exception:
                _cache = {}
        return _cache


def _save_cache() -> None:
    with _CACHE_LOCK:
        if _cache is None:
            return
        try:
            tmp = CACHE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(_cache, fh, ensure_ascii=False)
            os.replace(tmp, CACHE)
        except OSError:
            pass


def _model_instance():
    global _model
    if _model is None:
        import vosk

        try:
            vosk.SetLogLevel(-1)
        except Exception:
            pass
        _model = vosk.Model(MODEL_DIR)
    return _model


def transcribe(url: str, timeout: int = 60) -> str:
    """Words the recording says, lower case, single spaced."""
    cache = _load_cache()
    if url in cache:
        return cache[url]
    if not available():
        return ""
    try:
        import soundfile as sf
        import vosk

        raw = urllib.request.urlopen(url, timeout=timeout).read()
        pcm, rate = sf.read(io.BytesIO(raw), dtype="int16")
        if getattr(pcm, "ndim", 1) > 1:
            pcm = pcm[:, 0]
        pcm = _resample(pcm, rate, 16000)
        rec = vosk.KaldiRecognizer(_model_instance(), 16000)
        rec.AcceptWaveform(pcm.tobytes())
        text = (json.loads(rec.FinalResult()) or {}).get("text", "")
    except Exception:
        return ""
    text = re.sub(r"\s+", " ", text).strip().lower()
    _load_cache()[url] = text
    _save_cache()
    return text


def _resample(pcm, rate: int, target: int = 16000):
    """Vosk only decodes 16 kHz, the CDN serves various rates."""
    if rate == target:
        return pcm
    import numpy as np

    data = np.frombuffer(pcm.tobytes(), dtype=np.int16)
    length = int(len(data) * target / rate)
    index = np.linspace(0, len(data) - 1, length)
    out = np.interp(index, np.arange(len(data)), data).astype(np.int16)
    return out


def masked_answers(question: dict, transcript: str) -> list[str]:
    """Words of the transcript that fit the ``****`` slot of the sentence.

    Race the transcript against the sentence: the candidates whose neighbours
    line up with the sentence's neighbours win.
    """
    content = question["content"]["content"]
    tokens = content.split()
    slot = next((i for i, t in enumerate(tokens) if "*" in t), None)
    if slot is None or not transcript:
        return []
    left = [t.strip(".,!?;:()\"'").lower() for t in tokens[max(0, slot - 3):slot]]
    right = [t.strip(".,!?;:()\"'").lower() for t in tokens[slot + 1:slot + 4]]
    token = tokens[slot].strip(".,!?;:()\"'")
    token = token.replace("_", "*")
    pattern = re.compile(
        "^" + "".join("[a-z]" if ch in "*_" else re.escape(ch.lower()) for ch in token) + "$"
    )
    holes = [i for i, ch in enumerate(token) if ch in "*_"]
    whole = len(token) == len(holes)
    words = [w.strip(".,!?;:()\"'").lower() for w in transcript.split()]
    scored: list[tuple[float, str, str]] = []
    for index, word in enumerate(words):
        if not pattern.match(word):
            continue
        score = 0.0
        if left:
            score += 2 * _matches(words[max(0, index - len(left)):index], left)
        if right:
            score += 2 * _matches(words[index + 1:index + 1 + len(right)], right)
        answer = word if whole else "".join(word[i] for i in holes)
        scored.append((-score, answer, word))
    scored.sort()
    out: list[str] = []
    for _, answer, word in scored:
        for value in (answer, word):
            if value and value not in out:
                out.append(value)
    return out


def _matches(got: list[str], want: list[str]) -> float:
    total = 0.0
    for got_word, want_word in zip(got, want):
        if got_word == want_word:
            total += 1
        elif want_word and got_word.startswith(want_word[:4]):
            total += 0.5
    return total


def _fits(got: list[str], want: list[str]) -> bool:
    """Compare a short window of words, tolerating ASR slips."""
    pairs = list(zip(got, want))
    if not pairs:
        return True
    ok = 0
    for got_word, want_word in pairs:
        got_word = got_word.strip(".,!?;:").lower()
        want_word = want_word.strip(".,!?;:").lower()
        if not want_word or got_word == want_word or got_word.startswith(want_word[:4]):
            ok += 1
    return ok >= max(1, len(pairs) // 2)


def sound_words(transcript: str) -> list[str]:
    return [w for w in transcript.split() if w.isalpha()]


def score_against(sentence: str, transcript: str) -> float:
    """How many words of `sentence` show up in order inside `transcript`."""
    words = [w.strip(".,!?;:()\"'").lower() for w in transcript.split()]
    target = [w.strip(".,!?;:()\"'").lower() for w in sentence.split() if w]
    score = 0.0
    position = 0
    for want in target:
        for index in range(position, len(words)):
            if words[index] == want or (want and words[index].startswith(want[:4])):
                score += 1
                position = index + 1
                break
    return score / max(1, len(target))


def rank_options(question: dict, options: list[str], transcript: str) -> list[str]:
    """Order the choices by how well they match what is heard."""
    content = question["content"]["content"]
    scored = []
    for index, option in enumerate(options):
        sentence = re.sub(r"[*_]{2,}", option, content) if re.search(r"[*_]{2,}", content) else option
        scored.append((-score_against(sentence, transcript), index, option))
    scored.sort()
    return [option for _, _, option in scored]
