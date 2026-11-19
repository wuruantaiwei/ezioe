"""Đo chất lượng sinh đáp án trên đề thật (không nộp bài).

    python tools/bench_answers.py -u testezioetool1 --exercises 3

Mỗi câu được chấm thử bằng `answercheck` (oracle) để biết đáp án đúng nằm ở
vị trí thứ mấy trong danh sách ứng viên. So sánh hai cách xếp hạng:

  * ``grammar`` - cách của tool (ngữ pháp + tần suất),
  * ``freq``    - chỉ theo tần suất, dùng làm mốc.

Càng nhiều "hit@1..@10" và càng ít lần gọi oracle càng tốt.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ioeauto import cli, games  # noqa: E402

TOP = 40


def _rank(session, question, position, values) -> int | None:
    for index, value in enumerate(values[:TOP], 1):
        if not value:
            continue
        if session.check(question, value, position) > 0:
            return index
    return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-u", "--user", required=True)
    parser.add_argument("-p", "--password", default="")
    parser.add_argument("--exercises", type=int, default=3)
    args = parser.parse_args(argv)

    client = cli._client(args.user, args.password)
    exercises = [e for e in games.list_exercises(client) if e.get("isRank")][:args.exercises]
    rows: list[tuple[int | None, int | None, int]] = []
    failed: list[tuple[dict, list[str]]] = []

    for exercise in exercises:
        session = games.Session(client, exercise["idexercise"], exercise["gameId"]).open()
        session.start()
        for position, question in enumerate(session.questions, 1):
            values = games.candidates(question, pool=session.pool)
            size = question.get("numTChar") or len(games._masked_token(
                question["content"]["content"]))
            grammar = games._ranked_slots(values, size or 1,
                                          question["content"]["content"], games._context(question))
            freq = sorted(dict.fromkeys(values), key=lambda word: games._freq(word))
            best = _rank(session, question, position, grammar)
            rows.append((best, _rank(session, question, position, freq), len(values)))
            if best is None:
                failed.append((question, grammar[:8]))
        print(f"  xong bài {exercise['idexercise']}", flush=True)

    def summary(name: str, column: int) -> None:
        hits = [row[column] for row in rows]
        found = [hit for hit in hits if hit]
        total = len(hits)
        bands = {k: sum(1 for hit in found if hit <= k) for k in (1, 3, 5, 10, 20, TOP)}
        mean = sum(found) / len(found) if found else 0
        print(f"{name:<8} khớp: " + "  ".join(f"@{k}={bands[k] / total:.0%}" for k in bands)
              + f"  | hụt={total - len(found)}/{total}  tb vị trí={mean:.1f}")

    print(f"\n{len(rows)} câu")
    summary("grammar", 0)
    summary("freq", 1)
    for question, top in failed:
        print()
        print(f"  HỤT type={question['type']} | {question['content']['content'][:110]}")
        print(f"       desc: {str((question.get('Description') or {}).get('content'))[:110]}")
        print(f"       thử: {top}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
