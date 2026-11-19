"""Mở trang nói bằng micro giả, ghi lại request thật của trang vào window.__log."""
import base64
import json
import sys
import time

sys.path.insert(0, ".")
from ioeauto import api, cdp, cli, tts, vision  # noqa: E402

WORD = sys.argv[1] if len(sys.argv) > 1 else "Surgeon"
OUT = "data/spk_cap"
c = cli._client("testezioetool1", "ditmerobloxvng97@J97")
d = c.post("/ioe-service/v3/TuLuyen/setinfo", {"idExercise": 12272, "round": 0,
          "gameId": 36, "redirectUrl": "https://ioe.vn/hoc-sinh/tu-luyen"})
url = d["urlgame"]
token = url.split("token=")[1].split("&")[0]

wav = tts.speak(WORD, "en-US-AriaNeural")
b64 = base64.b64encode(wav).decode()

MIC = """
(async () => {
  const b64 = "%s";
  const bin = atob(b64); const buf = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  await ctx.audioData;
  const audio = await ctx.decodeAudioData(buf.buffer);
  navigator.mediaDevices.getUserMedia = async () => {
    const src = ctx.createBufferSource();
    src.buffer = audio; src.loop = true;
    const dest = ctx.createMediaStreamDestination();
    src.connect(dest); src.start();
    return dest.stream;
  };
  window.__log = [];
  const grab = async (args) => {
    try {
      const out = {};
      for (const [k, v] of Object.entries(args)) {
        if (typeof v === "string") out[k] = v.slice(0, 200);
        else if (v instanceof File) out[k] = `File(${v.name},${v.size})`;
        else if (v instanceof Blob) out[k] = `Blob(${v.type},${v.size})`;
        else out[k] = v;
      }
      return out;
    } catch (e) { return {}; }
  };
  const of = window.fetch;
  window.fetch = async function (input, init) {
    const rec = {url: String(input && input.url || input), method: (init || {}).method || "GET",
                 body: (init || {}).body ? String((init || {}).body).slice(0, 400) : ""};
    const res = await of.apply(this, arguments);
    try { rec.status = res.status; rec.res = (await res.clone().text()).slice(0, 500); } catch (e) {}
    window.__log.push(rec);
    return res;
  };
  const oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u) { this.__m = m; this.__u = u; return oo.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function (body) {
    const rec = {url: String(this.__u), method: this.__m};
    if (body instanceof FormData) {
      rec.form = {}; const files = [];
      for (const [k, v] of body.entries()) {
        if (v instanceof File || v instanceof Blob) files.push(`${k}:${v.size}`); else rec.form[k] = String(v).slice(0, 120);
      }
      rec.files = files;
    } else if (body) rec.body = String(body).slice(0, 500);
    this.addEventListener("load", () => {
      rec.status = this.status; rec.res = String(this.responseText || "").slice(0, 500);
      window.__log.push(rec);
    });
    return os.apply(this, arguments);
  };
  const stor = (to) => {
    try { sessionStorage.setItem("tokenGame", "%s"); } catch (e) {}
  };
  stor();
  window.__mic_ready = true;
})();
""" % (b64, token)

browser = cdp.Browser(extra_args=["--disable-sync"]).start()
browser.grant("https://ioe.vn", ["audioCapture"])
browser.inject(MIC)
vision.login(browser, "testezioetool1", "ditmerobloxvng97@J97")
browser.goto(url)
time.sleep(4)
print("mic:", browser.eval("!!window.__mic_ready"), "| câu hỏi:",
      browser.eval("(document.body.innerText||'').slice(0,120).replace(/\\n/g,' | ')"))
# OK của modal hướng dẫn
for attempt in range(24):
    if browser.eval("!!document.querySelector('.bg-introduction')"):
        time.sleep(1)
        continue
    ok = browser.eval("(() => { const b=[...document.querySelectorAll('button')]"
                      ".find(x=>/^ok$/i.test(x.innerText.trim())); if(b){b.click(); return 1}; return 0 })()")
    if ok:
        break
    time.sleep(1)
time.sleep(2)
for _ in range(20):
    if not browser.eval("!!document.querySelector('.ant-modal-mask, .bg-introduction')"):
        break
    time.sleep(1)
print("nút:", browser.eval("[...document.querySelectorAll('button')].map(b=>b.innerText.trim())"
                           ".filter(Boolean).join(' / ')"), "| mic:",
      browser.eval("!!document.querySelector('.btn-micro-phone')"))
with open(OUT + "/page.html", "w", encoding="utf-8") as fh:
    fh.write(browser.eval("document.body.outerHTML.slice(0, 20000)"))
click = "(()=>{const b=document.querySelector('.btn-micro-phone'); if(!b) return 0; b.click(); return 1})()"
print("bấm ghi:", browser.eval(click))
time.sleep(3)
for _ in range(20):
    done = browser.eval("(()=>{const b=document.querySelector('.btn-micro-phone');"
                        "if(!b) return 0; b.click(); return 1})()")
    if done:
        break
    time.sleep(0.4)
print("dừng:", done)
time.sleep(8)
log = browser.eval("JSON.stringify(window.__log, null, 1)") or "[]"
with open(OUT + "/log.json", "w", encoding="utf-8") as fh:
    fh.write(log)
print("request:", len(json.loads(log)))
print("lưu", OUT)
print(browser.url_now())
