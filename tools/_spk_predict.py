"""PredictFile multipart: thử accesstoken + reference_text + file, rồi poll Redis."""
import json, sys, time
sys.path.insert(0, ".")
from ioeauto import api, cli, tts

FTECH = "/event-service/v2/FTech/"
c = cli._client("testezioetool1", "ditmerobloxvng97@J97")
text = "Surgeon"
wav = tts.speak(text, "en-US-AriaNeural")
variants = [
    ("accesstoken", {"accesstoken": c.token, "serviceCode": "IOE", "reference_text": text}),
    ("acc+quest", {"accesstoken": c.token, "serviceCode": "IOE", "reference_text": text,
                   "questId": "1585106"}),
    ("token=acc", {"token": c.token, "serviceCode": "IOE", "reference_text": text}),
]
for label, fields in variants:
    res = c._upload(api.API_BASE + FTECH + "PredictFile", fields, "a.wav", wav)
    key = res.get("data") if isinstance(res.get("data"), str) else ""
    print(f"{label}: {res.get('code')} {res.get('message')} key={key[-26:]}")
    if not key:
        continue
    for _ in range(10):
        red = c._request(api.API_BASE + FTECH + "PredictFileRedis",
                         {"token": c.token, "serviceCode": "IOE", "keyRedis": key})
        if red.get("code") == 0:
            print("  REDIS OK:", json.dumps(red.get("data"), ensure_ascii=False)[:300]); sys.exit()
        time.sleep(0.5)
    print("  redis:", red.get("code"), red.get("message"))
