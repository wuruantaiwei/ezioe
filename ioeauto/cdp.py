"""Điều khiển trình duyệt thật qua Chrome DevTools Protocol, không cần thư viện ngoài.

Đây là "tay chân" cho phương án C (AI nhìn ảnh và bấm). Chỉ dùng socket + JSON
thuần: tự bắt tay WebSocket, tự đóng khung frame. Trình duyệt mặc định là Edge
(có sẵn trên Windows), đổi bằng `--browser chrome` hoặc biến `EZIOE_BROWSER`.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import time
import urllib.request

CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0")


class CdpError(RuntimeError):
    pass


def find_browser(explicit: str = "") -> str:
    for option in (explicit, os.environ.get("EZIOE_BROWSER", "")):
        if option and os.path.exists(option):
            return option
        if option and shutil.which(option):
            return shutil.which(option) or option
    for path in CANDIDATES:
        if os.path.exists(path):
            return path
    raise CdpError("không thấy Chrome/Edge - cài Edge hoặc đặt EZIOE_BROWSER=<đường dẫn>")


def _http(port: int, path: str = "/json/version", timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as response:
        return json.loads(response.read())


class Browser:
    """Tab đang điều khiển; dùng như context manager."""

    def __init__(self, port: int = 9222, profile: str = "", headless: bool = False,
                 browser: str = "", width: int = 1280, height: int = 900, url: str = "about:blank",
                 extra_args: list[str] | None = None):
        self.port = port
        self.profile = profile
        self.headless = headless
        self.width = width
        self.height = height
        self.exe = find_browser(browser)
        self.proc: subprocess.Popen | None = None
        self.sock: socket.socket | None = None
        self.url = url
        self.extra_args = list(extra_args or [])
        self._id = 0
        self._events: list[dict] = []

    # ------------------------------------------------------------- lifecycle
    def __enter__(self) -> "Browser":
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.close()

    def start(self) -> "Browser":
        args = [
            self.exe,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile or os.path.join(os.getcwd(), '.browser-profile')}",
            f"--window-size={self.width},{self.height}",
            "--no-first-run", "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--disable-sync", "--no-service-autorun", "--disable-session-crashed-bubble",
            "--disable-features=Translate,OptimizationHints",
            self.url,
        ]
        if self.headless:
            args.insert(1, "--headless=new")
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        target = self._wait_target()
        self._ws(target["webSocketDebuggerUrl"])
        self.send("Page.enable")
        self.send("Runtime.enable")
        return self

    def close(self) -> None:
        try:
            if self.sock:
                self.sock.close()
        finally:
            self.sock = None
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.proc.kill()

    def targets(self) -> list[dict]:
        try:
            return [t for t in _http(self.port, "/json/list", timeout=3) if t.get("type") == "page"]
        except Exception:
            return []

    def _pick(self, pages: list[dict]) -> dict | None:
        """Chọn tab thật của mình, bỏ qua tab nội bộ của trình duyệt."""
        usable = [t for t in pages if not str(t.get("url", "")).startswith(
            ("edge://", "chrome://", "devtools://"))]
        if not usable:
            return None
        origin = self.url.split("/")[2] if self.url.startswith("http") else ""
        for target in usable:
            if origin and origin in str(target.get("url", "")):
                return target
        return usable[-1]

    def attach_newest(self) -> bool:
        """Chuyển sang tab mới nhất (khi bấm link mở tab mới)."""
        newest = self._pick(self.targets())
        if not newest:
            return False
        url = newest.get("webSocketDebuggerUrl")
        if not url:
            return False
        try:
            if self.sock:
                self.sock.close()
        finally:
            self.sock = None
        self._ws(url)
        self.send("Page.enable")
        self.send("Runtime.enable")
        return True

    def _wait_target(self, timeout: float = 30.0) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            try:
                targets = _http(self.port, "/json/list", timeout=2)
            except Exception:
                time.sleep(0.4)
                continue
            picked = self._pick([t for t in targets
                                 if t.get("type") == "page" and "webSocketDebuggerUrl" in t])
            if picked:
                return picked
            time.sleep(0.4)
        raise CdpError("trình duyệt không mở cổng debug (bị chiếm bởi tiến trình khác?)")

    # -------------------------------------------------------------- websocket
    def _ws(self, url: str) -> None:
        rest = url.split("://", 1)[1]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        sock = socket.create_connection((host, int(port or 80)), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(request.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(1)
            if not chunk:
                raise CdpError("bắt tay WebSocket thất bại")
            head += chunk
        if b"101" not in head.split(b"\r\n", 1)[0]:
            raise CdpError("DevTools từ chối WebSocket: " + head[:80].decode("latin-1"))
        sock.settimeout(60)
        self.sock = sock

    def _send_frame(self, text: str) -> None:
        if not self.sock:
            raise CdpError("chưa kết nối")
        data = text.encode()
        mask = os.urandom(4)
        header = bytearray([0x81])
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        header += mask
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _read_frame(self) -> str:
        if not self.sock:
            raise CdpError("chưa kết nối")
        head = self._recv_exact(2)
        length = head[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._recv_exact(8))[0]
        payload = self._recv_exact(length) if length else b""
        return payload.decode("utf-8", "replace")

    def _recv_exact(self, count: int) -> bytes:
        assert self.sock is not None
        buffer = b""
        while len(buffer) < count:
            chunk = self.sock.recv(count - len(buffer))
            if not chunk:
                raise CdpError("kết nối DevTools đóng")
            buffer += chunk
        return buffer

    def send(self, method: str, params: dict | None = None, timeout: float = 60.0) -> dict:
        self._id += 1
        message = self._id
        self._send_frame(json.dumps({"id": message, "method": method, "params": params or {}}))
        end = time.time() + timeout
        while time.time() < end:
            event = json.loads(self._read_frame())
            if event.get("id") == message:
                if "error" in event:
                    raise CdpError(f"{method}: {event['error'].get('message')}")
                return event.get("result") or {}
            self._events.append(event)
        raise CdpError(f"{method}: quá thời gian chờ")

    # ----------------------------------------------------------------- actions
    def goto(self, url: str, wait: float = 2.0) -> None:
        self.send("Page.navigate", {"url": url})
        time.sleep(wait)

    def screenshot(self, clip: dict | None = None, scale: float = 1.0) -> bytes:
        params = {"format": "png"}
        if clip:
            params["clip"] = {**clip, "scale": max(0.2, min(3.0, scale))}
        result = self.send("Page.captureScreenshot", params)
        return base64.b64decode(result["data"])

    def size(self) -> tuple[int, int]:
        metrics = self.send("Page.getLayoutMetrics")["cssLayoutViewport"]
        return int(metrics["clientWidth"]), int(metrics["clientHeight"])

    def click(self, x: float, y: float) -> None:
        base = {"x": x, "y": y, "button": "left", "clickCount": 1,
                "pointerType": "mouse", "buttons": 1}
        self.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        self.send("Input.dispatchMouseEvent", {**base, "type": "mousePressed"})
        time.sleep(0.05)
        self.send("Input.dispatchMouseEvent", {**base, "type": "mouseReleased"})

    def insert_text(self, text: str) -> None:
        self.send("Input.insertText", {"text": text})

    def key(self, name: str) -> None:
        for kind in ("keyDown", "keyUp"):
            self.send("Input.dispatchKeyEvent", {"type": kind, "key": name, "windowsVirtualKeyCode": 13})

    def eval(self, script: str):
        result = self.send("Runtime.evaluate", {"expression": script, "returnByValue": True,
                                                "awaitPromise": True})
        return (result.get("result") or {}).get("value")

    def title(self) -> str:
        return str(self.eval("document.title") or "")

    def url_now(self) -> str:
        return str(self.eval("location.href") or "")

    def user_agent(self, ua: str = UA) -> None:
        self.send("Network.setUserAgentOverride", {"userAgent": ua})
