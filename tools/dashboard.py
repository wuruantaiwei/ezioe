"""Dựng bảng điều khiển HTML từ data/report.json (chỉ để xem, không sửa gì).

    python tools/dashboard.py            # -> data/dashboard.html
"""
import collections
import json
import os
import sys

sys.path.insert(0, ".")
from ioeauto.paths import data_dir  # noqa: E402

NAMES = {
    36: "Nói (AI chấm)",
    50: "Trắc nghiệm", 51: "Nối cặp", 52: "Nghe - điền từ", 55: "Sắp xếp câu",
    56: "Điền từ (giấu cả từ)", 58: "Đọc hiểu", 59: "Điền từ vào câu",
    60: "Nghe - chọn tranh", 61: "Điền khuyết", 62: "Chính tả", 63: "Nghe - chép",
    64: "Ghép câu", 92: "Viết lại câu", 93: "Từ vựng", 98: "Ngữ pháp",
}


def load() -> list[dict]:
    path = os.path.join(data_dir(), "report.json")
    with open(path, encoding="utf-8") as fh:
        report = json.load(fh)
    return [row for run in report.get("runs") or [] for row in run.get("exercises") or []]


def load_runs() -> list[dict]:
    path = os.path.join(data_dir(), "report.json")
    with open(path, encoding="utf-8") as fh:
        return (json.load(fh).get("runs") or [])


def build(rows: list[dict]) -> str:
    rounds = sum(1 for run in load_runs() if run.get("round_done"))
    passed = sum(1 for r in rows if r.get("pass"))
    # mỗi bài chỉ tính ĐIỂM CAO NHẤT của nó (chạy lại để cải thiện không bị trừ)
    best: dict[tuple, float] = {}
    for row in rows:
        if not row.get("max"):
            continue
        key = (row["user"], row["idexercise"])
        score = 100.0 * row["mark"] / row["max"]
        best[key] = max(best.get(key, 0.0), score)
    by_game = collections.defaultdict(list)
    for (user, idexercise), score in best.items():
        game = next(r["gameId"] for r in rows
                    if r["user"] == user and r["idexercise"] == idexercise)
        by_game[game].append(score)
    games = sorted(by_game.items(), key=lambda kv: sum(kv[1]) / len(kv[1]))
    by_day = collections.defaultdict(lambda: [0, 0.0])
    for row in rows:
        by_day[row["date"]][0] += 1
        if row.get("max"):
            by_day[row["date"]][1] += 100.0 * row["mark"] / row["max"]
    days = sorted(by_day.items())
    avg = sum(by_game[g] and sum(by_game[g]) / len(by_game[g]) for g, _ in games) / max(1, len(games))
    bank = len(json.load(open(os.path.join(data_dir(), "answers.json"), encoding="utf-8")))

    bars = "".join(
        f'<div class="row"><span class="name">{NAMES.get(g, "game " + str(g))}</span>'
        f'<span class="bar"><i style="width:{sum(v) / len(v):.0f}%"></i></span>'
        f'<span class="val">{sum(v) / len(v):.0f}%</span>'
        f'<span class="cnt">{len(v)} bài</span></div>'
        for g, v in games)
    daybars = "".join(
        f'<div class="day"><b>{d[5:]}</b><span class="bar"><i style="width:'
        f'{min(100, n / max(x[0] for x in by_day.values()) * 100):.0f}%"></i></span>'
        f'<span class="val">{n} bài</span></div>' for d, (n, _s) in days)
    return f"""<!doctype html><html lang="vi"><meta charset="utf-8">
<title>ezioe - bảng điều khiển</title>
<style>
 body{{font:15px/1.5 var(--font-sans,system-ui,Segoe UI,sans-serif);color:var(--foreground);margin:0}}
 h1{{font-size:22px;margin:0 0 4px}} .sub{{color:var(--muted-foreground);margin-bottom:28px}}
 .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;margin-bottom:28px}}
 .card{{background:var(--card);color:var(--card-foreground);border:1px solid var(--border);border-radius:var(--radius,14px);padding:16px}}
 .card b{{display:block;font-size:26px}} .card span{{color:var(--muted-foreground);font-size:13px}}
 .row,.day{{display:flex;align-items:center;gap:12px;margin:6px 0}}
 .name{{width:210px;color:var(--foreground)}} .val{{width:52px;text-align:right;color:var(--success,var(--accent-foreground))}}
 .cnt{{width:64px;color:var(--muted-foreground);font-size:12px;text-align:right}}
 .bar{{flex:1;height:10px;background:var(--secondary);border-radius:6px;overflow:hidden}}
 .bar i{{display:block;height:100%;background:linear-gradient(90deg,var(--chart-1,var(--primary)),var(--chart-3,var(--success)))}}
 h2{{font-size:13px;color:var(--muted-foreground);margin:26px 0 10px;text-transform:uppercase;letter-spacing:.06em}}
</style>
<h1>ezioe · bảng điều khiển</h1>
<div class="sub">{len({r['user'] for r in rows})} tài khoản · {len(rows)} bài · data/report.json</div>
<div class="cards">
 <div class="card"><b>{rounds}</b><span>lượt ghi vòng</span></div>
 <div class="card"><b>{len(rows)}</b><span>bài đã chơi</span></div>
 <div class="card"><b>{100 * passed / max(1, len(rows)):.0f}%</b><span>tỉ lệ đạt</span></div>
 <div class="card"><b>{avg:.0f}%</b><span>điểm trung bình</span></div>
 <div class="card"><b>{bank}</b><span>đáp án trong bank</span></div>
</div>
<h2>Điểm theo dạng bài (thấp trước)</h2>
{bars}
<h2>Khối lượng theo ngày</h2>
{daybars}
</html>"""


def main() -> int:
    rows = load()
    out = os.path.join(data_dir(), "dashboard.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(build(rows))
    print(len(rows), "bài ->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
