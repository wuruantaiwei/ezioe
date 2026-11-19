"""Tự dò endpoint mới khi IOE nâng cấp (v3 -> v4, đổi host, đổi đường dẫn).

Cách làm: thử từng version của từng service, version nào trả JSON (không phải
404) là version còn sống. Ghi kết quả vào `data/endpoints.json`;
`config.apply_endpoints()` đọc file đó nên chỉ cần dò lại là tool chạy tiếp,
không phải sửa code.

    python run.py endpoints --discover      # dò và ghi file
    python run.py endpoints                 # xem đang dùng gì
    python run.py endpoints --set ioe_service=/ioe-service/v4
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from . import paths

DEFAULT_HOST = "https://api-edu.go.vn"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
# service -> (đường dẫn mẫu để thử, body)
PROBES = {
    "ioe-service": ("/ioe-service/{v}/TuLuyen/getlistGameAndResultMax", {"serviceCode": "IOE"}),
    "game": ("/ioe-service/{v}/game/getinfo", {}),
    "billing-service": ("/billing-service/{v}/billing/goedu_getbalance", {}),
}
HOSTS = ["https://api-edu.go.vn", "https://identity.go.vn"]
PAGES = ["https://ioe.vn/hoc-sinh/tu-luyen", "https://ioe.vn/"]
SERVICE_RE = re.compile(r"/([a-z]+-service)/(v\d+)")
HOST_RE = re.compile(r"https://([a-z0-9.-]*(?:api|identity)[a-z0-9.-]*\.go\.vn)")


def _post(host: str, path: str, body: dict, token: str = "", timeout: int = 12) -> tuple[int, str]:
    payload = dict(body)
    if token:
        payload["token"] = token
    request = urllib.request.Request(
        host + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Origin": "https://ioe.vn", "User-Agent": UA},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(120).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception as exc:
        return 0, str(exc)


def _alive(host: str, path: str, body: dict, token: str) -> bool:
    status, text = _post(host, path, body, token)
    return status == 200 and "code" in text


def _scan_bundles() -> dict[str, str]:
    """Dò phụ từ bundle JS: bắt được cả service mới mà chưa biết đường dẫn mẫu."""
    blob = ""
    for page in PAGES:
        request = urllib.request.Request(page, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                blob = response.read().decode("utf-8", "replace")
        except Exception:
            continue
        for src in re.findall(r'src="([^"]+\.js[^"]*)"', blob):
            url = src if src.startswith("http") else "https://ioe.vn" + ("" if src.startswith("/") else "/") + src
            try:
                request = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(request, timeout=20) as response:
                    blob += response.read().decode("utf-8", "replace")
            except Exception:
                continue
    found = {}
    for service, version in SERVICE_RE.findall(blob):
        if service not in found or int(version[1:]) > int(found[service][1:]):
            found[service] = version
    return found


def discover(token: str = "", versions: int = 6, timeout: int = 12) -> dict:
    """Trả {'endpoints': {...}, 'found': {...}, 'error': str}."""
    found: dict[str, str] = {}
    for service, (template, body) in PROBES.items():
        for number in range(1, versions + 1):
            version = f"v{number}"
            if _alive(DEFAULT_HOST, template.format(v=version), body, token):
                found[service] = version
        if service == "ioe-service" and "ioe-service" not in found:
            hint = _scan_bundles()
            for guess in range(1, versions + 1):
                version = f"v{guess}"
                if _alive(DEFAULT_HOST, template.format(v=version), body, token):
                    found[service] = version
                    break
    if not found:
        hint = _scan_bundles()
        return {"endpoints": {}, "found": {"services": hint, "hosts": []},
                "error": "không version nào trả JSON (mạng hoặc API đổi hẳn)"}

    hosts = [host for host in HOSTS if _alive(host, "/", {}, "")]
    endpoints: dict[str, str] = {"api": DEFAULT_HOST}
    if found.get("ioe-service"):
        endpoints["ioe_service"] = f"/ioe-service/{found['ioe-service']}"
    if found.get("game"):
        endpoints["game"] = f"{DEFAULT_HOST}/ioe-service/{found['game']}/game/"
    if found.get("billing-service"):
        endpoints["billing_service"] = f"/billing-service/{found['billing-service']}"
    return {"endpoints": endpoints, "found": {"services": found, "hosts": hosts}, "error": ""}


def path() -> str:
    return os.path.join(paths.data_dir(), "endpoints.json")


def load() -> dict:
    try:
        with open(path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save(endpoints: dict) -> str:
    current = load()
    current.update({k: v for k, v in endpoints.items() if v})
    os.makedirs(os.path.dirname(path()), exist_ok=True)
    with open(path(), "w", encoding="utf-8") as fh:
        json.dump(current, fh, ensure_ascii=False, indent=1)
    return path()
