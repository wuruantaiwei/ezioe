"""Chạy trọn bài nói bằng API: TTS -> vad -> stop_stream -> answercheck -> finishgame."""
import json
import sys
import time
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, ".")
from ioeauto import api, cli, tts  # noqa: E402

c = cli._client("testezioetool1", "ditmerobloxvng97@J97")
d = c.post("/ioe-service/v3/TuLuyen/setinfo", {
    "idExercise": 12272, "round": 0, "gameId": 36,
    "redirectUrl": "https://ioe.vn/hoc-sinh/tu-luyen"})
gtok = urllib.parse.parse_qs(urllib.parse.urlparse(d["urlgame"]).query)["token"][0]
info = c._request(api.game_root() + "getinfo",
                  {"api_key": "string", "token": gtok, "gameId": 36, "Level": "8",
                   "Round": "1", "deviceId": "string", "sign": "string"})
token = info["data"]["token"]
game = info["data"]["game"]
base = {"api_key": "string", "serviceCode": "IOE", "token": token,
        "examKey": game.get("examKey"), "IPClient": "string", "deviceId": "string"}
print("startgame:", c._request(api.game_root() + "startgame", {**base, "gameId": 36}).get("code"))


def http(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()[:200]


def mp(fields, name, blob):
    b = "----ez" + uuid.uuid4().hex
    out = b""
    for k, v in fields.items():
        out += f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    out += (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\""
            "\r\nContent-Type: audio/wav\r\n\r\n").encode() + blob + f"\r\n--{b}--\r\n".encode()
    return out, f"multipart/form-data; boundary={b}"


def ftok():
    _s, b = http("https://fcloud-api-gateway.ftech.ai/auth/sdk/init",
                 json.dumps({"app_id": "100034",
                             "secret_key": "264a5ead3a63c4f33f5e337d192fb605"}).encode(),
                 {"Content-Type": "application/json", "Origin": "https://ioe.vn",
                  "User-Agent": "Mozilla/5.0"})
    return json.loads(b)["data"]["token"]


started = time.time()
answers = []
bearer = ftok()
for index, q in enumerate(game["question"], 1):
    word = q["content"]["content"]
    wav = tts.speak(word)
    _s, b = http("https://fcloud-streaming-socket-api.ai.ftech.ai/ioe/start_stream_v3",
                 json.dumps({"reference_text": word, "english_accent": "en-us",
                             "extra_data": ""}).encode(),
                 {"Content-Type": "application/json", "Origin": "https://ioe.vn",
                  "User-Agent": "Mozilla/5.0", "Authorization": "Bearer " + bearer})
    rid = json.loads(b)["data"]["request_id"]
    vad_body, vad_ct = mp({}, rid + ".wav", wav)
    _sv, bv = http("https://vad-api.ai.ftech.ai/vad-file", vad_body,
                   {"Content-Type": vad_ct, "Origin": "https://ioe.vn",
                    "User-Agent": "Mozilla/5.0", "Authorization": "Bearer " + bearer})
    if json.loads(bv).get("is_silence"):
        print(f"  câu {index}: im lặng, vẫn gửi")
    body, ctype = mp({"request_id": rid, "reference_text": word, "extra_data": "{}",
                      "english_accent": "en-us"}, rid + ".wav", wav)
    _s, b = http("https://fcloud-streaming-socket-api.ai.ftech.ai/ioe/stop_stream_v3", body,
                 {"Content-Type": ctype, "Origin": "https://ioe.vn",
                  "User-Agent": "Mozilla/5.0", "Authorization": "Bearer " + bearer})
    payload = json.loads(b)
    score = ((payload.get("data") or {}).get("score_data") or {}).get("accuracy_score")
    res = c._request(api.game_root() + "answercheck",
                     {**base, "ans": {"questId": q["id"], "examCount": index,
                                      "ans": rid, "position": index}})
    answers.append({"questId": q["id"], "examCount": index, "ans": rid, "position": index})
    print(f"  câu {index}: {word!r} điểm AI {score} | answercheck {res.get('code')} "
          f"point {(res.get('data') or {}).get('point')}")
    time.sleep(0.4)

seconds = max(60, int(time.time() - started))
fin = c._request(api.game_root() + "finishgame", {**base, "ans": answers, "totalTime": seconds})
print("finishgame:", fin.get("code"), json.dumps(fin.get("data"), ensure_ascii=False)[:300])
