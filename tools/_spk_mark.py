"""Chạy trọn bài nói với extra_data = token, rồi xem MARK thật của bài."""
import json
import sys
import time
import urllib.parse

sys.path.insert(0, ".")
from ioeauto import api, cli, games, tts  # noqa: E402

EX = int(sys.argv[1]) if len(sys.argv) > 1 else 12306
c = cli._client("testezioetool1", "ditmerobloxvng97@J97")
d = c.post("/ioe-service/v3/TuLuyen/setinfo", {"idExercise": EX, "round": 0,
          "gameId": 36, "redirectUrl": "https://ioe.vn/hoc-sinh/tu-luyen"})
gtok = urllib.parse.parse_qs(urllib.parse.urlparse(d["urlgame"]).query)["token"][0]
info = c._request(api.game_root() + "getinfo", {"api_key": "string", "token": gtok,
        "gameId": 36, "Level": "8", "Round": "1", "deviceId": "string", "sign": "string"})
gt = (info.get("data") or {}).get("token") or ""
game = (info.get("data") or {}).get("game") or {}
base = {"api_key": "string", "serviceCode": "IOE", "token": gt,
        "examKey": game.get("examKey"), "IPClient": "string", "deviceId": "string",
        "gameId": 36}
c._request(api.game_root() + "startgame", base)
bearer = games.ftech_token()
answers = []
started = time.time()
for index, q in enumerate(game["question"], 1):
    text = q["content"]["content"]
    body = json.dumps({"reference_text": text, "english_accent": "en-us",
                       "extra_data": gt}).encode()
    _s, b = games._http(games.FTECH_STREAM, body,
                        {"Content-Type": "application/json", "Origin": "https://ioe.vn",
                         "User-Agent": games.SPEAK_UA, "Authorization": "Bearer " + bearer})
    rid = (json.loads(b).get("data") or {}).get("request_id") or ""
    wav = tts.speak(text, "en-US-AriaNeural")
    vad_body, vad_ct = games._multipart({}, rid + ".wav", wav)
    games._http(games.FTECH_VAD, vad_body, {"Content-Type": vad_ct,
                "User-Agent": games.SPEAK_UA, "Authorization": "Bearer " + bearer})
    stop_body, ctype = games._multipart({"request_id": rid, "reference_text": text,
                                         "extra_data": gt, "english_accent": "en-us"},
                                        rid + ".wav", wav)
    _s, sb = games._http(games.FTECH_STOP, stop_body,
                         {"Content-Type": ctype, "Origin": "https://ioe.vn",
                          "User-Agent": games.SPEAK_UA, "Authorization": "Bearer " + bearer})
    score = ((json.loads(sb).get("data") or {}).get("score_data") or {}).get("accuracy_score")
    res = c._request(api.game_root() + "answercheck",
                     {**base, "ans": {"questId": q["id"], "examCount": 1, "ans": rid,
                                      "position": index}})
    answers.append({"ans": rid, "point": q["Point"], "questId": q["id"],
                    "position": index, "examCount": 1})
    print(f"  {index}. {text[:40]!r} AI {score} | ac {(res.get('data') or {}).get('ans') == rid}")
    time.sleep(0.3)
fin = c._request(api.game_root() + "finishgame",
                 {**base, "ans": answers, "totalTime": max(60, int(time.time() - started))})
print("finishgame:", json.dumps(fin.get("data"), ensure_ascii=False)[:200])
for e in games.list_exercises(c):
    if e["idexercise"] == EX:
        print("MARK:", e.get("mark"), "status:", e["Status"])
