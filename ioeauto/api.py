"""Low-level IOE / goEdu API client.

Reverse engineered from the public web clients:
  * https://ioe.vn/hoc-sinh  (SPA, api-edu.go.vn/ioe-service/*)
  * https://ioe.vn/lam-bai/<game>/  (cocos games, /ioe-service/v2/game/*)

The platform ships the correct answer down to the client and validates it
client side, and the API gateway does not verify any request signature
(`sign` is always the empty string), so a plain HTTP client can play the whole
game without a browser.
"""

from __future__ import annotations

import base64
import json
import os
import random
import re
import string
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

API_BASE = "https://api-edu.go.vn"
IDENTITY_BASE = "https://identity.go.vn/api"
SERVICE = "/ioe-service/v3"   # đổi bằng `run.py endpoints --discover` khi IOE lên v4
DEFAULT_SERVICE = "/ioe-service/v3"


def set_service(prefix: str) -> None:
    """Đổi hệ phiên bản của mọi đường dẫn API (v3 -> v4, ...)."""
    global SERVICE
    prefix = (prefix or "").rstrip("/")
    if not prefix:
        SERVICE = DEFAULT_SERVICE
    elif prefix.startswith("/"):
        SERVICE = prefix


GAME_SUFFIX = "/ioe-service/v2/game/"
GAME_ROOT = ""            # rỗng = suy ra từ API_BASE, đặt được qua config


def game_root() -> str:
    return GAME_ROOT or (API_BASE + GAME_SUFFIX)


def service_path(path: str) -> str:
    """Thay tiền tố /ioe-service/vN trong một đường dẫn bằng SERVICE hiện tại."""
    if SERVICE == "/ioe-service/v3":
        return path
    return re.sub(r"/ioe-service/v\d+", SERVICE, path, count=1)
IOE_BASE = "https://ioe.vn"

# public OAuth client used by edu.go.vn for the IOE service
CLIENT_ID = "jXEJcp17qKvkZwo"
CLIENT_SECRET = "m5N678JDwZGlpMEbyY"
TURNSTILE_KEY = "0x4AAAAAACVgql_FjVrRDWo8"
API_KEY = "string"  # literally the value shipped in the game configs

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
)


class ApiError(RuntimeError):
    def __init__(self, code: int, message: str, payload: dict | None = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.payload = payload or {}

    @property
    def auth(self) -> bool:
        return self.code in AUTH_ERRORS


# gateway error codes
AUTH_ERRORS = {82, 83, 84, 85, 86, 87}
DEVICE_CONFLICT = 22
NOT_STARTED = 24
PARAM_ERROR = 81
INVALID_TOKEN = 85


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def md5_password(password: str) -> str:
    """edu.go.vn hashes the password client side (non-ASCII -> '?')."""
    import hashlib

    masked = "".join(chr(63) if ord(c) > 127 else c for c in password)
    return hashlib.md5(masked.encode("utf-8")).hexdigest()


def rand_device_id() -> str:
    return "".join(random.choice("0123456789abcdef") for _ in range(32))


def jwt_claims(token: str) -> dict:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


class Client:
    """Session bound to one token + one device id."""

    def __init__(
        self,
        token: str,
        device_id: str,
        ip_client: str = "",
        device_os: str = "Windows 10; Chrome 155.0.0.0",
        state_path: str | None = None,
    ):
        self.token = token
        self.refresh_token = ""
        self.device_id = device_id or rand_device_id()
        self.ip_client = ip_client
        self.device_os = device_os
        self.state_path = state_path
        self.username = ""

    # ------------------------------------------------------------------ http
    def _upload(self, url: str, fields: dict, filename: str, blob: bytes, *,
                timeout: int = 90, bearer: str = "") -> dict:
        """POST multipart (dịch vụ chấm phát âm chỉ nhận multipart)."""
        boundary = "----ezioe" + uuid.uuid4().hex
        body = b""
        for key, value in fields.items():
            head = f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n'
            body += head.encode() + str(value).encode() + b"\r\n"
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                 f'filename="{filename}"\r\nContent-Type: audio/wav\r\n\r\n').encode()
        body += blob + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json", "Origin": IOE_BASE, "Referer": IOE_BASE + "/",
            "User-Agent": UA, "x-device-id": self.device_id, "x-device-os": self.device_os,
            **({"Authorization": "Bearer " + bearer} if bearer else {}),
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            try:
                return json.loads(raw)
            except Exception:
                return {"code": exc.code, "message": raw[:400]}
        except urllib.error.URLError as exc:
            raise ApiError(0, f"network error: {exc.reason}") from exc

    def _request(self, url: str, body: dict | None = None, *, origin: str = IOE_BASE,
                 referer: str = IOE_BASE + "/", timeout: int = 40,
                 with_common: bool = True, bearer: str = "") -> dict:
        payload = dict(body or {})
        if with_common:
            payload.setdefault("ipClient", self.ip_client)
            payload.setdefault("deviceId", self.device_id)
            payload.setdefault("sign", "")
            payload.setdefault("deltatime", 0)
            payload.setdefault("token", self.token)
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Origin": origin,
                "Referer": referer,
                "User-Agent": UA,
                "x-device-id": self.device_id,
                "x-device-os": self.device_os,
                **({"Authorization": "Bearer " + bearer} if bearer else {}),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            try:
                out = json.loads(raw)
            except Exception:
                out = {"code": exc.code, "message": raw[:400]}
            return out
        except urllib.error.URLError as exc:
            raise ApiError(0, f"network error: {exc.reason}") from exc

    def _request_retry(self, url: str, body: dict | None, *, referer: str | None = None,
                       origin: str | None = None, tries: int = 4) -> dict:
        """DNS/connection hiccups should not kill a whole run."""
        last: Exception | None = None
        kwargs = {"referer": referer or IOE_BASE + "/"}
        if origin:
            kwargs["origin"] = origin
        for attempt in range(tries):
            try:
                return self._request(url, body, **kwargs)
            except ApiError as exc:
                last = exc
                if exc.code != 0:
                    raise
                time.sleep(min(30.0, 2.0 * (attempt + 1)))
        raise last if last else ApiError(0, "network error")

    def post(self, path: str, body: dict | None = None, *, referer: str | None = None) -> dict:
        url = path if path.startswith("http") else API_BASE + service_path(path)
        out = self._request_retry(url, body, referer=referer or IOE_BASE + "/")
        code = out.get("code", 0)
        if code in AUTH_ERRORS and self.refresh_token:
            self.refresh()
            out = self._request(url, body, referer=referer or IOE_BASE + "/")
            code = out.get("code", 0)
        if code:
            raise ApiError(code, out.get("message", ""), out)
        return out.get("data")

    # --------------------------------------------------------------- extras
    def balance(self) -> dict:
        """Coin / ruby balance shown in the header of the site."""
        try:
            return self.post("/billing-service/v2/billing/goedu_getbalance", {}) or {}
        except ApiError:
            return {}

    def schedule(self) -> list[dict]:
        """Release calendar. Cosmetic, so a slow answer must not stall a run."""
        try:
            out = self._request(
                API_BASE + service_path("/ioe-service/v3/TuLuyen/getTuLuyenOpenSchedule"),
                {"token": self.token},
                timeout=15,
            )
        except ApiError:
            return []
        return (out.get("data") if out.get("code") == 0 else None) or []

    # ------------------------------------------------------------------ auth
    def refresh(self) -> None:
        out = self._request(
            IDENTITY_BASE + "/auth/refresh-token",
            {"refresh_token": self.refresh_token},
            origin="https://edu.go.vn",
            with_common=False,
        )
        data = out.get("data") or {}
        if out.get("code") != 0 or not data.get("access_token"):
            raise ApiError(out.get("code", 0), out.get("message", "refresh failed"))
        self.token = data["access_token"]
        self.refresh_token = data.get("refresh_token") or self.refresh_token
        self.save()

    def save(self) -> None:
        if not self.state_path:
            return
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "token": self.token,
                    "refresh_token": self.refresh_token,
                    "device_id": self.device_id,
                    "ip_client": self.ip_client,
                    "username": self.username,
                },
                fh,
            )
        os.replace(tmp, self.state_path)

    @classmethod
    def load(cls, state_path: str) -> "Client | None":
        if not os.path.exists(state_path):
            return None
        try:
            with open(state_path, encoding="utf-8") as fh:
                st = json.load(fh)
        except Exception:
            return None
        cli = cls(st.get("token", ""), st.get("device_id", ""), st.get("ip_client", ""),
                  state_path=state_path)
        cli.refresh_token = st.get("refresh_token", "")
        cli.username = st.get("username", "")
        return cli if cli.token else None


def public_ip() -> str:
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=10) as r:
            return r.read().decode().strip()
    except Exception:
        return ""


def login(username: str, password: str, *, device_id: str = "",
          state_path: str | None = None) -> Client:
    """Native IOE login through the goEdu identity provider.

    The UI only sends every request body once and never signs it; the whole
    OIDC dance can therefore be reproduced with plain requests.
    """
    device_id = device_id or rand_device_id()
    ip = public_ip()
    os_name = "Windows 10; Chrome 155.0.0.0"

    redirect_uri = IOE_BASE + "/hoc-sinh/tu-luyen"
    encoded_redirect = urllib.parse.quote(redirect_uri, safe="")
    verifier = b64u(os.urandom(32))
    challenge = b64u(__import__("hashlib").sha256(verifier.encode()).digest())

    ticks = str(int(time.time() * 10_000_000) + 621_355_968_000_000_000)
    state = b64u(json.dumps(
        {"id": None, "uri": encoded_redirect, "st": ticks, "rt": "login"}
    ).encode())

    body = {
        "ipClient": ip,
        "deviceId": device_id,
        "deviceOs": os_name,
        "deviceOsVersion": "10",
        "serviceCode": "IOE",
        "api_key": CLIENT_ID,
        "redirectUrl": "string",
        "deltatime": 0,
        "username": username.strip(),
        "password": md5_password(password),
        "captchaToken": "",
        "publicKey": TURNSTILE_KEY,
        "oidcParams": {
            "client_id": CLIENT_ID,
            "redirect_uri": encoded_redirect,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        "remember": False,
    }

    req = urllib.request.Request(
        IDENTITY_BASE + "/auth/login",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Origin": "https://edu.go.vn",
            "Referer": "https://edu.go.vn/user/dang-nhap",
            "User-Agent": UA,
            "x-device-id": device_id,
            "x-device-os": os_name,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            out = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        out = json.loads(exc.read().decode(errors="replace") or "{}")
        raise ApiError(out.get("status", 0), out.get("detail", "login failed")) from exc

    if out.get("code") != 0:
        raise ApiError(out.get("code", 0), out.get("message", "login failed"), out)

    data = out["data"]
    cli = Client(data["access_token"], device_id, ip, state_path=state_path)
    cli.refresh_token = data.get("refresh_token", "")
    cli.username = username
    claims = jwt_claims(data["access_token"])
    if claims.get("dvId"):
        # the gateway binds the session to the device record it created
        cli.device_id = claims["dvId"]
    cli.save()
    return cli
