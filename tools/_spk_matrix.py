"""Thử các biến thể examCount/position/answercheck để tìm cách chấm đúng nhất."""
import json
import sys
import time
import urllib.parse

sys.path.insert(0, ".")
from ioeauto import api, cli, games, tts  # noqa: E402

c = cli._client("testezioetool1", "ditmerobloxvng97@J97")


def one_round(pos_base: int, exam_count: int, answer_check: bool, label: str) -> None:
    d = c.post("/ioe-service/v3/TuLuyen/setinfo", {
        "idExercise": 12272, "round": 0, "gameId": 36,
        "redirectUrl": "https://ioe.vn/hoc-sinh/tu-luyen"})
    gtok = urllib.parse.parse_qs(urllib.parse.urlparse(d["urlgame"]).query)["token"][0]
    info = c._request(api.game_root() + "getinfo",
                      {"api_key": "string", "token": gtok, "gameId": 36, "Level": "8",
                       "Round": "1", "deviceId": "string", "sign": "string"})
    token, game = info["data"]["token"], info["data"]["game"]
    base = {"api_key": "string", "serviceCode": "IOE", "token": token,
            "examKey": game.get("examKey"), "IPClient": "string", "deviceId": "string"}
    c._request(api.game_root() + "startgame", {**base, "gameId": 36})
    bearer = games.ftech_token()
    answers, scores = [], []
    started = time.time()
    for index, q in enumerate(game["question"]):
        value, score = games.speak_answer(q, "en-US-AriaNeural", bearer)
        scores.append(score)
        pos = index + pos_base
        if answer_check and value:
            try:
                c._request(api.game_root() + "answercheck",
                           {**base, "ans": {"ans": value, "point": q["Point"],
                                            "questId": q["id"], "position": pos,
                                            "examCount": exam_count}})
            except api.ApiError:
                pass
        answers.append({"ans": value, "point": q["Point"], "questId": q["id"],
                        "position": pos, "examCount": exam_count})
        time.sleep(0.2)
    fin = c._request(api.game_root() + "finishgame",
                     {**base, "ans": answers, "totalTime": max(60, int(time.time() - started))})
    print(f"{label}: examScore={(fin.get('data') or {}).get('examScore')} "
          f"ai={min(scores)}-{max(scores)}")


one_round(0, 0, True, "vị trí 0, examCount 0, có answercheck")
one_round(1, 1, False, "không answercheck")
one_round(1, 0, True, "vị trí 1, examCount 0")
