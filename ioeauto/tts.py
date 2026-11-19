"""Text to speech for the AI-scored speaking game (Windows SAPI, offline)."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
import subprocess
import tempfile

from .paths import data_dir

DATA = data_dir()
CACHE = os.path.join(DATA, "tts")


RATE = 16000   # định dạng bộ chấm AI chấp nhận: 16 kHz mono 16-bit
NEURAL_RE = re.compile(r"^[a-z]{2,3}-[A-Z]{2}-[A-Za-z0-9]+Neural$")
EDGE_VOICES = ("en-US-AriaNeural", "en-US-JennyNeural", "en-US-EmmaMultilingualNeural")


def _cache_key(text: str, voice: str) -> str:
    return hashlib.sha1(f"{voice}|{text}".encode()).hexdigest()[:20]


def _edge(text: str, voice: str) -> bytes:
    """Giọng neural của Microsoft qua edge-tts (cần mạng) -> WAV 16 kHz mono."""
    import asyncio
    import io

    import edge_tts
    import soundfile as sf

    async def collect() -> bytes:
        stream = edge_tts.Communicate(text, voice).stream()
        chunks = b""
        async for part in stream:
            if part["type"] == "audio":
                chunks += part["data"]
        return chunks

    try:
        asyncio.get_running_loop()
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            raw = pool.submit(lambda: asyncio.run(collect())).result(timeout=60)
    except RuntimeError:
        raw = asyncio.run(collect())
    audio, rate = sf.read(io.BytesIO(raw), dtype="int16")
    if getattr(audio, "ndim", 1) > 1:
        audio = audio.mean(axis=1).astype("int16")
    import numpy as np

    if rate != RATE:
        length = int(len(audio) * RATE / rate)
        audio = np.interp(np.linspace(0, len(audio) - 1, length),
                          np.arange(len(audio)), audio).astype("int16")
    out = io.BytesIO()
    sf.write(out, audio, RATE, format="WAV", subtype="PCM_16")
    return out.getvalue()


def _powershell(text: str, path: str, voice: str = "") -> bool:
    script = (
        "[Console]::InputEncoding = [Text.Encoding]::UTF8; "
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        + (f"$s.SelectVoice('{voice.replace(chr(39), chr(39) * 2)}'); " if voice else "")
        + "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo("
        f"{RATE}, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, "
        "[System.Speech.AudioFormat.AudioChannel]::Mono); "
        + f"$s.SetOutputToWaveFile('{path}', $f); "
        f"$s.Speak([Console]::In.ReadToEnd()); $s.Dispose()"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            input=text.encode("utf-8"), capture_output=True, timeout=60,
        )
        return proc.returncode == 0 and os.path.exists(path)
    except (OSError, subprocess.SubprocessError):
        return False


def _espeak(text: str, path: str) -> bool:
    exe = shutil.which("espeak") or shutil.which("espeak-ng")
    if not exe:
        return False
    try:
        subprocess.run([exe, "-w", path, text], check=True, timeout=60, capture_output=True)
        return os.path.exists(path)
    except (OSError, subprocess.SubprocessError):
        return False


def speak(text: str, voice: str = "") -> bytes:
    """WAV bytes for `text` (empty when no TTS engine is available).

    Giọng neural (`en-US-AriaNeural`, ...) dùng edge-tts; giọng SAPI hoặc tên
    khác dùng SAPI/espeak. Kết quả được cache nên mỗi câu chỉ tổng hợp một lần.
    """
    if not text.strip():
        return b""
    if NEURAL_RE.match(voice or ""):
        os.makedirs(CACHE, exist_ok=True)
        stored = os.path.join(CACHE, _cache_key(text, voice) + ".wav")
        if os.path.exists(stored):
            with open(stored, "rb") as fh:
                return fh.read()
        try:
            wav = _edge(text, voice)
        except Exception:                                     # noqa: BLE001
            wav = b""
        if wav:
            with open(stored, "wb") as fh:
                fh.write(wav)
            return wav
    os.makedirs(CACHE, exist_ok=True)
    handle, path = tempfile.mkstemp(suffix=".wav", dir=CACHE)
    os.close(handle)
    try:
        ok = _powershell(text, path, voice) or _espeak(text, path)
        if not ok:
            return b""
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def as_answer(text: str, voice: str = "") -> str:
    """Base64 WAV, the shape the speaking game uploads."""
    data = speak(text, voice)
    return base64.b64encode(data).decode() if data else ""
