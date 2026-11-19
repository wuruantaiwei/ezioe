"""Phương án C: mở trình duyệt thật, chụp ảnh, chia lưới toạ độ và để AI chọn.

Khi mọi lớp API chết (bỏ đáp án, chấm server-side, chặn IP, captcha) thì lớp
cuối vẫn giữ đúng hình dạng "người thật ngồi trước máy":

    1. mở đúng bài bằng chính phiên trình duyệt,
    2. chụp ảnh, chia lưới 8x6 có nhãn (A1..H6) cho AI chọn ô,
    3. phóng vào ô đó, chia lưới 5x5 lần hai, AI chọn ô con, bấm tâm ô,
    4. câu viết (dictation/fill) thì bấm vào ô trả lời rồi gõ chữ,
    5. hết câu thì bấm "Nộp bài", xác nhận, sang bài sau.

Mỗi bước đều lưu ảnh vào `data/vision/` để còn dò lại khi AI chọn sai.
Chạy thử không bấm: `run.py ai --dry-run`.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import time

from . import cdp, paths
from .cdp import CdpError

COLS, ROWS = 8, 6          # lưới thô
FINE_COLS, FINE_ROWS = 5, 5  # lưới phóng vào một ô
LABELS = "ABCDEFGH"
TYPE_GAMES = {1, 3}        # game gõ chữ (dictation, fill)
SAVE_DIR = "vision"
MAX_STEPS = 140

PROMPT = """Bạn đang điều khiển trình duyệt để làm một bài tiếng Anh trên IOE.
Ảnh là màn hình hiện tại đã chia thành các ô có nhãn {labels}.
Trả về DUY NHẤT một JSON:
{{"action": "...", "cell": "C4", "text": "", "conf": 0.0, "why": "ngắn gọn"}}

action chọn một trong:
- "click": bấm vào giữa một ô (bắt buộc có "cell") - dùng để chọn đáp án, ghép cặp, kéo thả, bấm nút.
- "type": bấm vào ô trả lời rồi gõ đúng nội dung trong "text".
- "submit": bấm nút nộp bài / xác nhận đã hiện trên màn hình.
- "wait": chờ (audio đang phát, trang đang tải).
- "done": bài đã xong, đang ở màn hình kết quả.

Luật:
- Mỗi lần chỉ chọn MỘT ô. Ở bài ghép cặp hãy bấm lần lượt từng cặp.
- Câu hỏi dạng viết/dictation: "type" và "text" chỉ chứa phần cần điền, không thêm lời giải thích.
- Nếu đề tiếng Anh, text là tiếng Anh đúng chính tả; nếu đề tiếng Việt, text là tiếng Việt.
- Đừng bấm vào nút đã hoàn thành hay ô trống ngoài đề.
{prompt}

Ngữ cảnh thêm: {hint}
"""


class VisionError(RuntimeError):
    pass


# ------------------------------------------------------------------- AI calls
def provider(config: dict) -> tuple[str, str, str, str]:
    vision = config.get("vision") or {}
    name = (vision.get("provider") or os.environ.get("EZIOE_AI_PROVIDER") or "").lower()
    key = (vision.get("key") or os.environ.get(vision.get("key_env") or "EZIOE_AI_KEY")
           or os.environ.get("EZIOE_AI_KEY") or os.environ.get("OPENAI_API_KEY") or "")
    model = (vision.get("model") or os.environ.get("EZIOE_AI_MODEL") or "")
    base = (vision.get("base") or os.environ.get("EZIOE_AI_BASE") or "")
    if not name:
        name = "openai" if key and os.environ.get("EZIOE_AI_BASE", "").startswith("http") else ""
        if not name and os.environ.get("GEMINI_API_KEY"):
            name, key = "gemini", os.environ["GEMINI_API_KEY"]
        if not name and os.environ.get("ANTHROPIC_API_KEY"):
            name, key = "anthropic", os.environ["ANTHROPIC_API_KEY"]
        if not name and key:
            name = "openai"
    model = model or {"openai": "gpt-4o-mini", "gemini": "gemini-2.0-flash",
                      "anthropic": "claude-3-7-sonnet-latest"}.get(name, "")
    base = base or {"openai": "https://api.openai.com/v1",
                    "anthropic": "https://api.anthropic.com/v1",
                    "gemini": "https://generativelanguage.googleapis.com/v1beta"}.get(name, "")
    return name, key, model, base


def _post_json(url: str, body: dict, headers: dict, timeout: int = 90) -> dict:
    import urllib.request

    request = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        raise VisionError(f"AI trả lỗi {exc.code}: {detail}") from exc


def ask(png: bytes, prompt: str, config: dict) -> dict:
    """Gửi ảnh + lưới cho AI, trả dict action/cell/text/conf."""
    name, key, model, base = provider(config)
    if not name or not key:
        raise VisionError("chưa cấu hình AI: đặt EZIOE_AI_KEY (và EZIOE_AI_PROVIDER) "
                          "hoặc điền mục `vision` trong data/config.json")
    data_url = "data:image/png;base64," + base64.b64encode(png).decode()
    if name == "gemini":
        url = f"{base or 'https://generativelanguage.googleapis.com/v1beta'}/models/{model}:generateContent?key={key}"
        body = {"contents": [{"parts": [{"text": prompt},
                                        {"inline_data": {"mime_type": "image/png", "data": data_url.split(",", 1)[1]}}]}],
                "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"}}
        out = _post_json(url, body, {})
        text = (((out.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [{}])[0].get("text", "")
    elif name == "anthropic":
        url = f"{base}/messages"
        body = {"model": model, "max_tokens": 900, "temperature": 0.1,
                "messages": [{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                 "data": data_url.split(",", 1)[1]}},
                    {"type": "text", "text": prompt}]}]}
        out = _post_json(url, body, {"x-api-key": key, "anthropic-version": "2023-06-01"})
        text = "".join(part.get("text", "") for part in out.get("content") or [])
    else:
        url = f"{base}/chat/completions"
        body = {"model": model, "temperature": 0.1, "max_tokens": 900,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}}]}]}
        out = _post_json(url, body, {"Authorization": f"Bearer {key}"})
        message = (out.get("choices") or [{}])[0].get("message") or {}
        # model suy luận (GLM, Qwen3, ...) có thể trả content rỗng và để JSON trong reasoning
        text = message.get("content") or message.get("reasoning_content") or ""
    return parse(text)


def parse(text: str) -> dict:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        raise VisionError(f"AI không trả JSON: {text[:120]!r}")
    try:
        data = json.loads(match.group(0))
    except ValueError as exc:
        raise VisionError(f"JSON hỏng: {match.group(0)[:120]!r}") from exc
    action = str(data.get("action") or "click").lower()
    if action not in {"click", "type", "submit", "wait", "done"}:
        action = "click"
    cell = str(data.get("cell") or "").upper().replace(" ", "")
    if cell and not re.fullmatch(r"[A-Z]\d{1,2}", cell):
        cell = ""
    return {"action": action, "cell": cell, "text": str(data.get("text") or ""),
            "conf": float(data.get("conf") or 0), "why": str(data.get("why") or "")}


# ------------------------------------------------------------------- lưới ảnh
def _open(png: bytes):
    from PIL import Image, ImageDraw

    image = Image.open(io.BytesIO(png)).convert("RGB")
    return image, ImageDraw


def _font(size: int = 16):
    from PIL import ImageFont

    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def annotate(png: bytes, cols: int = COLS, rows: int = ROWS, crop: tuple[int, int, int, int] | None = None) -> tuple[bytes, dict]:
    """Vẽ lưới có nhãn, trả (ảnh PNG, bản đồ nhãn -> (x1,y1,x2,y2) theo toạ độ trang)."""
    image, draw = _open(png)
    width, height = image.size
    x0 = y0 = 0
    if crop:
        x0, y0, x1, y1 = crop
        image = image.crop((x0, y0, x1, y1))
        width, height = image.size
    draw = draw.Draw(image)
    font = _font(max(12, min(20, int(min(width, height) / 26))))
    cells: dict[str, tuple[int, int, int, int]] = {}
    step_x, step_y = width / cols, height / rows
    for row in range(rows):
        for col in range(cols):
            label = f"{LABELS[col]}{row + 1}"
            left, top = int(col * step_x), int(row * step_y)
            right, bottom = int((col + 1) * step_x) - 1, int((row + 1) * step_y) - 1
            cells[label] = (left + x0, top + y0, right + x0, bottom + y0)
            draw.rectangle([left, top, right, bottom], outline=(255, 64, 64), width=1)
            draw.rectangle([left + 1, top + 1, left + 22, top + font.size + 3], fill=(0, 0, 0))
            draw.text((left + 4, top + 2), label, fill=(255, 255, 0), font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue(), cells


def label_map(cols: int = COLS, rows: int = ROWS) -> str:
    return ", ".join(f"{LABELS[col]}{row + 1}" for row in range(rows) for col in range(cols))


# ---------------------------------------------------------------- vòng điều khiển
def _scale(png: bytes, width: int, height: int) -> float:
    from PIL import Image

    real_w, _ = Image.open(io.BytesIO(png)).size
    return (real_w / width) if width else 1.0


def pick(browser: cdp.Browser, config: dict, prompt: str, hint: str = "", *, zoom: bool = True) -> dict:
    """Hai lượt: lưới thô -> lưới mịn, trả {action, x, y, text}."""
    png = browser.screenshot()
    width, height = browser.size()
    factor = _scale(png, width, height)
    coarse_png, cells = annotate(png)
    answer = ask(coarse_png, PROMPT.format(labels="A1..H6", prompt=prompt, hint=hint), config)
    if answer["action"] != "click" or not answer.get("cell"):
        return {**answer, "x": 0, "y": 0}
    box = cells.get(answer["cell"])
    if not box:
        raise VisionError(f"AI chọn ô lạ: {answer['cell']}")
    if not zoom:
        return {**answer, "x": (box[0] + box[2]) / 2 / factor, "y": (box[1] + box[3]) / 2 / factor}
    coarse_crop = (box[0], box[1], box[2] + 1, box[3] + 1)
    fine_png_raw = browser.screenshot(clip={"x": coarse_crop[0], "y": coarse_crop[1],
                                            "width": coarse_crop[2] - coarse_crop[0],
                                            "height": coarse_crop[3] - coarse_crop[1]})
    fine_png, fine_cells = annotate(fine_png_raw, FINE_COLS, FINE_ROWS, crop=coarse_crop)
    second = ask(fine_png, PROMPT.format(labels="A1..E5 (đã phóng vào một ô nhỏ)",
                                         prompt=prompt, hint=hint), config)
    chosen = fine_cells.get(second.get("cell") or "") or box
    return {**answer, "cell": second.get("cell") or answer["cell"],
            "x": (chosen[0] + chosen[2]) / 2 / factor, "y": (chosen[1] + chosen[3]) / 2 / factor}


def step(browser: cdp.Browser, config: dict, prompt: str, hint: str = "", *, dry: bool = False) -> dict:
    """Một bước: xem ảnh, hỏi AI, thực thi. Trả hành động đã làm."""
    save_dir = os.path.join(paths.data_dir(), SAVE_DIR)
    os.makedirs(save_dir, exist_ok=True)
    action = pick(browser, config, prompt, hint)
    if action["action"] in {"click", "submit", "type"} and action.get("cell") and not dry:
        browser.click(action["x"], action["y"])
        time.sleep(0.3)
    if action["action"] == "type" and action.get("text") and not dry:
        browser.insert_text(action["text"])
    with open(os.path.join(save_dir, time.strftime("%H%M%S") + "-" + action["action"] + ".png"), "wb") as fh:
        fh.write(browser.screenshot())
    return action


APP_PAGE = "https://ioe.vn/hoc-sinh/tu-luyen"
LOGIN_PAGE = "https://edu.go.vn/user/dang-nhap"


def logged_in(browser: cdp.Browser, navigate: bool = True) -> bool:
    """Đã vào được khu học sinh chưa.

    ioe.vn chuyển trang đăng nhập sang `edu.go.vn/user/dang-nhap`, nên chỉ cần
    mở trang tự luyện: còn bị đẩy về trang đăng nhập nghĩa là chưa có phiên.
    """
    try:
        if navigate:
            browser.goto(APP_PAGE, 4)
        url = browser.url_now()
    except CdpError:
        return False
    if not url.startswith("http") or "dang-nhap" in url or "dang-ky" in url:
        return False
    return "ioe.vn/hoc-sinh" in url or "edu.go.vn/hoc" in url


SUBMIT_JS = """(() => {
    const re = /Đăng nhập|Tiếp tục|Đồng ý/i;
    const btns = [...document.querySelectorAll("button[type=submit],button,input[type=submit]")]
        .filter((e) => e.getBoundingClientRect().width > 0);
    const btn = btns.find((e) => re.test((e.textContent || "") + (e.value || "")));
    if (btn) { btn.click(); return "button"; }
    const form = document.querySelector("form");
    if (form) { form.requestSubmit ? form.requestSubmit() : form.submit(); return "form"; }
    return "";
})()"""


def submit_login(browser: cdp.Browser) -> str:
    """Bấm đúng nút đăng nhập (Enter trên form của edu.go.vn không ăn)."""
    try:
        return str(browser.eval(SUBMIT_JS) or "")
    except CdpError:
        return ""


def _click_text(browser: cdp.Browser, pattern: str) -> bool:
    return bool(browser.eval("""(() => {
        const re = new RegExp(%s, "i");
        const el = [...document.querySelectorAll("button,a,div[role=button],span")].find(
            (e) => re.test((e.textContent || "").trim()) && e.getBoundingClientRect().width > 0);
        if (!el) return false;
        el.click();
        return true; })()""" % json.dumps(pattern)))


def login(browser: cdp.Browser, user: str, password: str, timeout: float = 45.0) -> bool:
    """Đăng nhập bằng DOM (không cần AI) và xác nhận phiên chạy được thật."""
    if logged_in(browser):
        return True
    # logged_in() đã để trình duyệt ở đúng trang đăng nhập do IOE tự chuyển tới
    if "dang-nhap" not in browser.url_now():
        browser.goto(LOGIN_PAGE, 5)
    if not browser.eval("document.querySelectorAll('input').length"):
        browser.goto("https://ioe.vn/", 3)
        _click_text(browser, "Đăng nhập")
        time.sleep(3)
        browser.attach_newest()
        time.sleep(2)
    point = browser.eval("""(() => {
        const inputs = [...document.querySelectorAll('input')];
        const mail = inputs.find(i => /mail|user|phone|tài khoản/i.test(i.name + i.id + i.placeholder));
        const pass = inputs.find(i => i.type === 'password');
        if (!mail || !pass) return "";
        const r = mail.getBoundingClientRect();
        return JSON.stringify({x: r.x + r.width / 2, y: r.y + r.height / 2}); })()""")
    if not point:
        raise VisionError("không thấy form đăng nhập (đăng nhập tay một lần rồi chạy lại)")
    spot = json.loads(point)
    browser.click(spot["x"], spot["y"])
    browser.insert_text(user)
    browser.key("Tab")
    browser.insert_text(password)
    if not submit_login(browser):
        browser.key("Enter")

    end = time.time() + timeout
    while time.time() < end:
        time.sleep(2)
        if logged_in(browser):
            return True
        url = browser.url_now()
        if "dang-nhap" in url:
            filled = browser.eval(
                "!!document.querySelector('input[type=password]').value")
            if filled:
                submit_login(browser)
            else:
                _click_text(browser, "Đăng nhập")
        elif "oauth" in url:
            _click_text(browser, "Tiếp tục|Đồng ý|Cho phép|Cấp quyền|Chấp nhận")
        elif "dang-nhap" in url and "Lỗi" in (browser.eval("document.body.innerText") or ""):
            break
    return logged_in(browser)


def finish_round(browser: cdp.Browser, config: dict, *, dry: bool = False) -> dict:
    """Bấm 'Lưu kết quả' để chốt vòng (điểm chỉ được ghi sau bước này)."""
    browser.goto("https://ioe.vn/hoc-sinh/tu-luyen", 4)
    action = step(browser, config, "Bấm nút để lưu kết quả vòng và sang vòng tiếp theo.",
                  "Nút thường có chữ 'Lưu kết quả' hoặc 'Hoàn thành'.", dry=dry)
    if not dry and action["action"] != "done":
        time.sleep(3)
        confirmed = browser.eval("""(() => {
            const b = [...document.querySelectorAll('button')].find(e => /Đồng ý|Xác nhận|OK/i.test(e.textContent));
            if (b) { b.click(); return true; } return false; })()""")
        if confirmed:
            time.sleep(2)
    return action


def audio_urls(browser: cdp.Browser) -> list[str]:
    """URL âm thanh đang có trên trang (bài nghe/dictation)."""
    try:
        raw = browser.eval(r"""(() => {
            const found = new Set();
            document.querySelectorAll("audio,source").forEach((el) => el.src && found.add(el.src));
            const html = document.documentElement.innerHTML;
            (html.match(/https?:\/\/[^"' ]+\.(?:mp3|wav|ogg|m4a)/gi) || []).forEach((u) => found.add(u));
            return JSON.stringify([...found].slice(0, 3));
        })()""")
        return json.loads(raw or "[]")
    except (CdpError, ValueError):
        return []


def heard_text(browser: cdp.Browser) -> str:
    """Nghe đề bằng ASR offline, dùng làm gợi ý cho AI gõ đáp án."""
    from . import asr

    if not asr.available():
        return ""
    for url in audio_urls(browser):
        try:
            if url.startswith("blob:"):
                continue
            return asr.transcribe(url, timeout=45)
        except Exception:                                     # noqa: BLE001
            continue
    return ""


def solve_exercise(browser: cdp.Browser, config: dict, url: str, *, game_id: int = 0,
                   dry: bool = False, on_log=print) -> dict:
    """Chơi một bài bằng mắt AI. Trả {steps, done, log}."""
    browser.goto(url, 4)
    prompt = ("Bài đang mở. Hãy làm đúng yêu cầu của đề trên màn hình."
              if game_id not in TYPE_GAMES else
              "Bài tập viết. Đọc đề, xác định đáp án rồi dùng action \"type\" để điền.")
    hint = f"gameId={game_id}. " + ("Bài gõ chữ: luôn dùng type, không click đáp án." if game_id in TYPE_GAMES
                                    else "Bài chọn/ghép: luôn dùng click.")
    if game_id in TYPE_GAMES:
        heard = heard_text(browser)
        if heard:
            hint += f" Nghe được (ASR, có thể sai): {heard[:160]}"
    history: list[dict] = []
    for number in range(1, int((config.get("vision") or {}).get("max_steps") or MAX_STEPS) + 1):
        action = step(browser, config, prompt, hint, dry=dry)
        history.append(action)
        on_log(f"  bước {number}: {action['action']} {action.get('cell') or ''} "
               f"{(action.get('text') or '')[:40]} (tin {float(action.get('conf') or 0):.2f}) "
               f"{(action.get('why') or '')[:50]}")
        if action["action"] == "done":
            return {"steps": number, "done": True, "log": history}
        if action["action"] == "submit" and not dry:
            time.sleep(3)
            if "tu-luyen" in browser.url_now() or browser.eval("!!document.querySelector('.result, .ketqua')"):
                return {"steps": number, "done": True, "log": history}
        pause = 0.6 if action["action"] in {"click", "type"} else 2.0
        time.sleep(pause)
    return {"steps": len(history), "done": False, "log": history}
