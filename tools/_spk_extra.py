"""Thử hợp đồng bài nói với extra_data = JWT game token (như trang thật)."""
import json
import sys
import time
import urllib.parse

sys.path.insert(0, ".")
from ioeauto import api, cli, games  # noqa: E402

c = cli._client("testezioetool1", "ditmerobloxvng97@J97")
d = c.post("/ioe-service/v3/TuLuyen/setinfo", {"idExercise": 12306, "round": 0,
          "gameId": 36, "redirectUrl": "https://ioe.vn/hoc-sinh/tu-luyen"})
gtok = urllib.parse.parse_qs(urllib.parse.urlparse(d["urlgame"]).query)["token"][0]
info = c._request(api.game_root() + "getinfo", {"api_key": "string", "token": gtok,
        "gameId": 36, "Level": "8", "Round": "1", "deviceId": "string", "sign": "string"})
gt = (info.get("data") or {}).get("token") or ""
game = (info.get("data") or {}).get("game") or {}
if not gt or not game:
    print("không mở được game:", json.dumps(info, ensure_ascii=False)[:200]); sys.exit()
base = {"api_key": "string", "serviceCode": "IOE", "token": gt,
        "examKey": game.get("examKey"), "IPClient": "string", "deviceId": "string",
        "gameId": 36}
print("startgame:", c._request(api.game_root() + "startgame", base).get("code"))
bearer = games.ftech_token()
q = game["question"][0]
text = q["content"]["content"]
wav = games.tts.speak(text, "en-US-AriaNeural") if hasattr(games, "tts") else b""
from ioeauto import tts  # noqa: E402
wav = tts.speak(text, "en-US-AriaNeural")
for label, extra in (("token", gt), ("json {}", "{}")):
    body = json.dumps({"reference_text": text, "english_accent": "en-us",
                       "extra_data": extra}).encode()
    _s, b = games._http(games.FTECH_STREAM, body,
                        {"Content-Type": "application/json", "Origin": "https://ioe.vn",
                         "User-Agent": games.SPEAK_UA, "Authorization": "Bearer " + bearer})
    rid = (json.loads(b).get("data") or {}).get("request_id") if b.startswith(b"{") else ""
    print(f"start({label}):", rid)
    if not rid:
        continue
    vad_body, vad_ct = games._multipart({}, rid + ".wav", wav)
    games._http(games.FTECH_VAD, vad_body, {"Content-Type": vad_ct,
                "User-Agent": games.SPEAK_UA, "Authorization": "Bearer " + bearer})
    stop_body, ctype = games._multipart({"request_id": rid, "reference_text": text,
                                         "extra_data": extra, "english_accent": "en-us"},
                                        rid + ".wav", wav)
    _s, sb = games._http(games.FTECH_STOP, stop_body,
                         {"Content-Type": ctype, "Origin": "https://ioe.vn",
                          "User-Agent": games.SPEAK_UA, "Authorization": "Bearer " + bearer})
    score = ((json.loads(sb).get("data") or {}).get("score_data") or {}).get("accuracy_score")
    res = c._request(api.game_root() + "answercheck",
                     {**base, "ans": {"questId": q["id"], "examCount": 1, "ans": rid,
                                      "position": 1}})
    print(f"  stop({label}) điểm AI {score} | answercheck {res.get('code')} "
          f"point {(res.get('data') or {}).get('point')} | ans echo {(res.get('data') or {}).get('ans')}")
    time.sleep(0.5)
