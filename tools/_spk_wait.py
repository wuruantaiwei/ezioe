import json, sys, time
sys.path.insert(0, ".")
from ioeauto import api, cli, tts
FTECH = "/event-service/v2/FTech/"
c = cli._client("testezioetool1", "ditmerobloxvng97@J97")
text = "Surgeon"
wav = tts.speak(text, "en-US-AriaNeural")
up = c._upload(api.API_BASE + "/event-service/v2/fileupload/uploadsingle",
               {"accesstoken": c.token, "serviceCode": "IOE"}, "a.wav", wav)
url = up.get("data") or ""
print("s3:", url[:70], "| dài", len(wav))
for label, fields in (("file=url", {"token": c.token, "serviceCode": "IOE",
                                    "reference_text": text, "file": url}),):
    res = c._upload(api.API_BASE + FTECH + "PredictFile", fields, "a.wav", wav)
    key = res.get("data") if isinstance(res.get("data"), str) else ""
    print(label, res.get("code"), res.get("message"), key[-24:])
    for _ in range(10):
        red = c._request(api.API_BASE + FTECH + "PredictFileRedis",
                         {"token": c.token, "serviceCode": "IOE", "keyRedis": key})
        if red.get("code") == 0:
            print(" REDIS OK:", json.dumps(red.get("data"), ensure_ascii=False)[:300]); sys.exit()
        time.sleep(1)
    print(" redis:", red.get("code"), red.get("message"))
