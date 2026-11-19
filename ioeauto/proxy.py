"""MITM proxy mode (requirement #1 of the brief).

Most IOE traffic is still reachable over plain HTTP and the gateway accepts any
client, so a browser can be put behind mitmproxy and the exam JSON observed
directly. This addon:

  * prints every `/ioe-service/v2/game/*` exchange,
  * prints the questions together with the answer list that the server ships to
    the client (`ans` + `orderTrue` = the "client side validation" hole),
  * optionally harvests every answercheck the server scored `point > 0` into
    `data/answers.json`, which `ioe run` then reuses.

Usage:

    pip install mitmproxy
    ioe proxy --bank       # 127.0.0.1:8080, install the mitm.it CA in the browser
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time

from .paths import app_root

ROOT = app_root()
BANK = os.path.join(ROOT, "data", "answers.json")
GAME_MARK = "/ioe-service/v2/game/"

addons = []


def _load_bank() -> dict:
    if not os.path.exists(BANK):
        return {}
    try:
        with open(BANK, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_bank(bank: dict) -> None:
    os.makedirs(os.path.dirname(BANK), exist_ok=True)
    with open(BANK, "w", encoding="utf-8") as fh:
        json.dump(bank, fh, ensure_ascii=False, indent=1, sort_keys=True)


class Addon:
    def __init__(self, store: bool = False, quiet: bool = False):
        self.store = store
        self.quiet = quiet
        self.asked: dict[int, str] = {}
        self.questions: list[dict] = []

    # ------------------------------------------------------------------ hooks
    def request(self, flow) -> None:
        if GAME_MARK not in flow.request.pretty_url:
            return
        action = flow.request.pretty_url.rsplit("/", 1)[-1]
        try:
            body = json.loads(flow.request.content or b"{}")
        except Exception:
            return
        if action == "answercheck":
            answer = (body.get("ans") or {}).get("ans", "")
            self.asked[(body.get("ans") or {}).get("questId")] = answer
            if not self.quiet:
                print(f"[answercheck] {answer!r}")

    def response(self, flow) -> None:
        if GAME_MARK not in flow.request.pretty_url:
            return
        action = flow.request.pretty_url.rsplit("/", 1)[-1]
        try:
            payload = json.loads(flow.response.content or b"{}")
        except Exception:
            return
        if action == "getinfo" and payload.get("code") == 0:
            self.show(payload)
        elif action == "answercheck":
            data = payload.get("data") or {}
            point = int(data.get("point") or 0)
            quest = data.get("questId")
            if point and self.store:
                self.remember(quest, self.asked.get(quest, ""), point)
        elif action == "finishgame":
            data = payload.get("data") or {}
            print(f"[finishgame] {data.get('point')}/{data.get('examScore')} điểm, "
                  f"{data.get('time')}s, pass={data.get('isPass')}")

    # ---------------------------------------------------------------- helpers
    def show(self, payload: dict) -> None:
        game = (payload.get("data") or {}).get("game") or {}
        self.questions = game.get("question") or []
        if self.quiet:
            return
        print(f"\n[exam] {len(self.questions)} câu hỏi - đáp án server gửi xuống client:")
        for question in self.questions:
            words = sorted(
                ((a.get("orderTrue"), a.get("content")) for a in question.get("ans") or []),
                key=lambda item: item[0],
            )
            print(f"  #{question.get('id')} type={question.get('type')} "
                  f"numTChar={question.get('numTChar')}")
            print("    câu:", question.get("content", {}).get("content"))
            desc = (question.get("Description") or {}).get("content")
            if desc:
                print("    ngữ liệu:", desc)
            print("    đáp án:", [word for _, word in words])

    def remember(self, quest: int, answer: str, point: int) -> None:
        question = next((q for q in self.questions if q.get("id") == quest), None)
        if not answer or not question:
            return
        key = hashlib.sha1(
            f"{question['type']}|{question['content']['content']}".encode()
        ).hexdigest()[:16]
        bank = _load_bank()
        bank[key] = {
            "type": question["type"],
            "content": question["content"]["content"],
            "answer": answer,
            "point": point,
            "seen": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        _save_bank(bank)
        print(f"[bank] lưu {answer!r}")


def run(port: int = 8080, store: bool = False) -> int:
    try:
        from mitmproxy.tools.main import mitmdump
    except ImportError:
        print("Cần cài mitmproxy trước: pip install mitmproxy", file=sys.stderr)
        return 2
    os.environ["IOE_STORE"] = "1" if store else "0"
    saved = sys.argv
    sys.argv = [
        "mitmdump",
        "-p", str(port),
        "--set", "flow_detail=0",
        "--scripts", os.path.abspath(__file__),
    ]
    print(f"MITM proxy: 127.0.0.1:{port} (Ctrl+C để dừng)")
    try:
        mitmdump()
    finally:
        sys.argv = saved
    return 0


def load(loader):
    addons.append(Addon(store=os.environ.get("IOE_STORE") == "1"))


if __name__ == "__main__":
    load(None)
