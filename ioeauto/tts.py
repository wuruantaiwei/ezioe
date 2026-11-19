"""Text to speech for the AI-scored speaking game (Windows SAPI, offline)."""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile

from .paths import data_dir

DATA = data_dir()
CACHE = os.path.join(DATA, "tts")


RATE = 16000   # định dạng bộ chấm AI chấp nhận: 16 kHz mono 16-bit


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
    """WAV bytes for `text` (empty when no TTS engine is available)."""
    if not text.strip():
        return b""
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
