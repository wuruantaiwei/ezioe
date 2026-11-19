"""Dò hợp đồng chấm điểm thật của game nói: eventSpeak + FTech PredictFile."""
import json
import sys
import time
import urllib.parse
import uuid

sys.path.insert(0, ".")
from ioeauto import api, cli, tts  # noqa: E402

SPEAK = "/event-service/v2/eventSpeak/"
FTECH = "/event-service/v2/FTech/"
c = cli._client("testezioetool1", "ditmerobloxvng97@J97")


def show(label, res):
    print(f"{label}: {json.dumps(res, ensure_ascii=False)[:400]}")
    return res


info_game = show("game/setinfo", c.post("/ioe-service/v3/TuLuyen/setinfo", {
    "idExercise": 12272, "round": 0, "gameId": 36,
    "redirectUrl": "https://ioe.vn/hoc-sinh/tu-luyen"}))
url = info_game.get("urlgame") or ""
gtok = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("token", [""])[0]
print("gtok:", gtok[:24])

info = show("eventSpeak/getInfo", c._request(api.game_root() + "getinfo", {
    "api_key": "string", "token": gtok, "gameId": 36, "Level": "8", "Round": "1",
    "deviceId": "string", "sign": "string"}))
data = info.get("data") or {}
token, game = data.get("token") or "", data.get("game") or {}
if not game:
    print("KHÔNG có game -> dừng"); sys.exit()

base = {"api_key": "string", "serviceCode": "IOE", "token": token,
        "examKey": game.get("examKey"), "IPClient": "string", "deviceId": "string"}
show("eventSpeak/startgame", c._request(api.game_root() + "startgame", {**base, "gameId": 36}))

first = game["question"][0]
text = first["content"]["content"]
print("thử câu:", text)


def multipart(fields, name, blob):
    b = "----ez" + uuid.uuid4().hex
    out = b""
    for k, v in fields.items():
        out += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    out += (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{name}"'
            "\r\nContent-Type: audio/wav\r\n\r\n").encode() + blob + f"\r\n--{b}--\r\n".encode()
    return out, f"multipart/form-data; boundary={b}"


# 1) upload + xin key
body, ctype = multipart({"token": token, "serviceCode": "IOE", "reference_text": text},
                        str(first["id"]) + ".wav", tts.speak(text, "en-US-AriaNeural"))
upload = show("fileupload/uploadsingle", c._upload(
    api.API_BASE + "/event-service/v2/fileupload/uploadsingle",
    {"token": c.token, "serviceCode": "IOE"},
    str(first["id"]) + ".wav", tts.speak(text, "en-US-AriaNeural"), bearer=c.token))
print()
up = show("PredictFile", c._upload(api.API_BASE + FTECH + "PredictFile",
                                  {"token": token, "serviceCode": "IOE",
                                   "reference_text": text},
                                  str(first["id"]) + ".wav", tts.speak(text, "en-US-AriaNeural")))

key = up.get("data") if isinstance(up.get("data"), str) else ""
print("key:", key)
for attempt in range(1, 11):
    red = c._request(api.API_BASE + FTECH + "PredictFileRedis",
                     {"token": c.token, "serviceCode": "IOE", "keyRedis": key})
    print(f"  redis {attempt}: {json.dumps(red, ensure_ascii=False)[:220]}")
    if red.get("code") == 0 and red.get("data"):
        break
    time.sleep(0.5)

ans = {"questId": first["id"], "examCount": 1, "ans": key, "position": 1}
show("AnswerCheck(game, key)", c._request(api.game_root() + "answercheck", {**base, "ans": ans}))
show("AnswerCheck(eventSpeak, key)", c._request(api.API_BASE + SPEAK + "AnswerCheck",
                                                {**base, "ans": ans}))
