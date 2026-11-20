"""Đo accuracy của bộ chấm IOE theo từng giọng/tốc độ TTS.

    python tools/bench_speak.py                 # ma trận mặc định
    python tools/bench_speak.py --voice "en-US-AndrewNeural|-10%"
"""
import argparse
import sys

sys.path.insert(0, ".")
from ioeauto import games  # noqa: E402

PHRASES = [
    "Surgeon",
    "Volunteer",
    "Can you lend me the book when you finish reading it?",
    "The government should replace the old transport system with a modern one.",
    "Green technology uses renewable resources that are never depleted.",
]

VOICES = [
    "en-US-AriaNeural",
    "en-US-AriaNeural|-10%",
    "en-US-AriaNeural|-20%",
    "en-US-EmmaMultilingualNeural",
    "en-US-AndrewNeural",
    "en-US-MichelleNeural",
    "en-GB-SoniaNeural",
    "Microsoft Zira Desktop",
]


def score(voice: str) -> tuple[float, list[int]]:
    points = []
    for text in PHRASES:
        question = {"type": 12, "Point": 100, "content": {"content": text}}
        _rid, got = games.speak_answer(question, voice, "")
        points.append(int(got or 0))
    return sum(points) / len(points), points


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--voice", action="append", help="chỉ đo giọng này (lặp được)")
    args = parser.parse_args()
    voices = args.voice or VOICES
    rows = []
    for voice in voices:
        avg, points = score(voice)
        rows.append((avg, voice, points))
        print(f"{voice:40s} TB {avg:5.1f} | {points}", flush=True)
    rows.sort(reverse=True)
    print("\nXếp hạng:")
    for avg, voice, _points in rows[:3]:
        print(f"  {avg:5.1f}  {voice}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
