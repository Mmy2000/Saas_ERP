"""The two WhatsApp gateways a client can be given (console → Features), both set up once for
the platform in settings:

* The hosted service ("whatsapp_api"): the official WhatsApp API service sale codes use.
  POST {WHATSAPP_GATEWAY_URL}/whatsapp/send-message/ (a document) or /whatsapp/send-report/ (a
  statement), multipart: phone, name, order_id, order_amount and the PDF as invoice_file, header
  "Authorization: ApiKey <WHATSAPP_GATEWAY_KEY>". It sends its own approved wording: the shop's
  text is not passed on. Answers {"success": true} or {"success": false, "message": …}.

* The shop's own number ("whatsapp_own"): a whatsapp-web.js gateway (Node) that keeps one
  session per client, linked by scanning a QR. WA_GATEWAY_URL, header "X-API-KEY:
  <WA_GATEWAY_KEY>". POST /sessions/<id>/start, GET /sessions/<id>/status → {"status":
  connected | qr | starting | not_started | disconnected}, GET /sessions/<id>/qr → {"qr": a data
  URL image}, POST /send {session, phone, message} and POST /send-document {session, phone,
  message, filename, pdf_base64} → {"ok": true, "id"} or {"ok": false, "error"}. A dropped
  session may take ~52 s to reconnect before it answers, hence the long timeout.

With WHATSAPP_CONSOLE (development and tests) neither is called: messages land in OUTBOX and a
session links at once. Standard library HTTP only. Nothing here raises on a gateway problem:
callers get Result(ok, error) with the reason in words.
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)
SEND_TIMEOUT = 60
CHECK_TIMEOUT = 15
STATES = ("connected", "qr", "starting", "not_started", "disconnected")

OUTBOX: list[dict] = []  # the console's messages, for tests
_CONSOLE_SESSIONS: dict[str, str] = {}


@dataclass(frozen=True)
class Result:
    ok: bool
    error: str = ""
    data: dict = field(default_factory=dict)


def console() -> bool:
    return bool(getattr(settings, "WHATSAPP_CONSOLE", False))


def phone_digits(phone: str) -> str:
    """"+20 100 123 4567" or "01001234567" → "201001234567" (Egypt when it starts with 0)."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = "20" + digits[1:]
    return digits if 10 <= len(digits) <= 15 else ""


def _call(method: str, url: str, *, headers: dict, body: bytes | None = None,
          timeout: int = SEND_TIMEOUT) -> tuple[int, dict]:
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 (configured URL)
            status, raw = response.status, response.read()
    except HTTPError as error:
        status, raw = error.code, error.read()
    except (URLError, TimeoutError, OSError) as error:
        logger.warning("WhatsApp gateway %s unreachable: %s", url, error)
        return 0, {}
    try:
        return status, json.loads(raw or b"{}")
    except ValueError:
        return status, {}


def _problem(status: int, text: str = "") -> str:
    if status == 0:
        return _("The WhatsApp service is not reachable. Try again later or ask support.")
    if status in (401, 403):
        return _("The WhatsApp service refused the platform's key. Ask support.")
    low = (text or "").lower()
    if "not connected" in low or "not_started" in low or "disconnected" in low:
        return _("The shop's WhatsApp number is not linked. Link it again in Settings → "
                 "WhatsApp.")
    if "not registered" in low or ("invalid" in low and "number" in low):
        return _("This number is not on WhatsApp.")
    return text[:300] or _("The WhatsApp service answered with an error (%(status)s).") % {
        "status": status}


# ---- the hosted service ----------------------------------------------------------------------

def hosted_ready() -> bool:
    return console() or bool(settings.WHATSAPP_GATEWAY_URL and settings.WHATSAPP_GATEWAY_KEY)


def _multipart(fields: dict, file_field: str, filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                     f"{value}\r\n".encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
                 f'filename="{filename}"\r\nContent-Type: application/pdf\r\n\r\n'.encode())
    parts.append(content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def hosted_send(*, phone: str, name: str, order_id: str, amount: str, pdf: bytes,
                filename: str, statement: bool = False) -> Result:
    if console():
        OUTBOX.append({"via": "api", "phone": phone, "name": name, "order_id": order_id,
                       "amount": amount, "filename": filename, "statement": statement})
        return Result(True)
    if not hosted_ready():
        return Result(False, _("The WhatsApp service is not set up on this platform."))
    endpoint = "/whatsapp/send-report/" if statement else "/whatsapp/send-message/"
    body, content_type = _multipart(
        {"phone": phone, "name": name, "order_id": order_id, "order_amount": amount},
        "invoice_file", filename, pdf)
    status, data = _call("POST", settings.WHATSAPP_GATEWAY_URL.rstrip("/") + endpoint,
                         body=body, headers={"Authorization": f"ApiKey "
                                             f"{settings.WHATSAPP_GATEWAY_KEY}",
                                             "Content-Type": content_type})
    if status == 200 and data.get("success"):
        return Result(True, data=data)
    return Result(False, _problem(status, str(data.get("message") or data.get("error") or "")))


# ---- the shop's own number -------------------------------------------------------------------

def own_ready() -> bool:
    return console() or bool(settings.WA_GATEWAY_URL and settings.WA_GATEWAY_KEY)


def _own(method: str, path: str, payload: dict | None = None, *,
         timeout: int = CHECK_TIMEOUT) -> tuple[int, dict]:
    headers = {"X-API-KEY": settings.WA_GATEWAY_KEY, "Accept": "application/json"}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode()
    return _call(method, settings.WA_GATEWAY_URL.rstrip("/") + path, headers=headers,
                 body=body, timeout=timeout)


def new_session_id(tenant_slug: str) -> str:
    return f"gweb-{tenant_slug}-{uuid.uuid4().hex[:8]}"


def start(session: str) -> Result:
    if console():
        _CONSOLE_SESSIONS[session] = "qr"
        return Result(True)
    if not own_ready():
        return Result(False, _("Linking a WhatsApp number is not set up on this platform."))
    status, data = _own("POST", f"/sessions/{session}/start", {})
    if 200 <= status < 300:
        return Result(True, data=data)
    return Result(False, _problem(status, str(data.get("error") or "")))


def state(session: str) -> Result:
    """{"status": one of STATES, "qr": a data URL while waiting for the scan, "phone"}."""
    if console():
        status = _CONSOLE_SESSIONS.get(session, "not_started")
        if status == "qr":  # the console "scans" on the second look
            _CONSOLE_SESSIONS[session] = "connected"
            return Result(True, data={"status": "qr", "qr": _CONSOLE_QR})
        return Result(True, data={"status": status, "phone": "201000000000"
                                  if status == "connected" else ""})
    if not own_ready():
        return Result(False, _("Linking a WhatsApp number is not set up on this platform."))
    status, data = _own("GET", f"/sessions/{session}/status")
    if status != 200:
        return Result(False, _problem(status, str(data.get("error") or "")))
    found = data.get("status") if data.get("status") in STATES else "disconnected"
    result = {"status": found, "phone": str(data.get("phone") or data.get("me") or "")}
    if found == "qr":
        _code, qr = _own("GET", f"/sessions/{session}/qr")
        image = str(qr.get("qr") or "")
        result["qr"] = image if image.startswith("data:image/") else ""
    return Result(True, data=result)


def logout(session: str) -> Result:
    """Unlink the number on the gateway (the phone's Linked devices list drops it too)."""
    if console():
        _CONSOLE_SESSIONS.pop(session, None)
        return Result(True)
    if not own_ready():
        return Result(False, _("Linking a WhatsApp number is not set up on this platform."))
    status, data = _own("DELETE", f"/sessions/{session}")
    if 200 <= status < 300 or status == 404:
        return Result(True)
    return Result(False, _problem(status, str(data.get("error") or "")))


def own_send(session: str, *, phone: str, text: str, pdf: bytes | None = None,
             filename: str = "") -> Result:
    if console():
        OUTBOX.append({"via": "own", "session": session, "phone": phone, "text": text,
                       "filename": filename})
        return Result(True)
    if not own_ready():
        return Result(False, _("Linking a WhatsApp number is not set up on this platform."))
    if pdf is None:
        status, data = _own("POST", "/send", {"session": session, "phone": phone,
                                              "message": text}, timeout=SEND_TIMEOUT)
    else:
        status, data = _own("POST", "/send-document", {
            "session": session, "phone": phone, "message": text, "filename": filename,
            "pdf_base64": base64.b64encode(pdf).decode("ascii")}, timeout=SEND_TIMEOUT)
    if 200 <= status < 300 and data.get("ok"):
        return Result(True, data=data)
    return Result(False, _problem(status, str(data.get("error") or "")))


# The console's stand-in "QR": three finder squares and a scatter, so a screenshot reads as one.
def _console_qr() -> str:
    cells = []
    for x, y in ((0, 0), (14, 0), (0, 14)):
        cells.append(f'<rect x="{x}" y="{y}" width="7" height="7"/><rect x="{x + 1}" y="{y + 1}" '
                     f'width="5" height="5" fill="#fff"/><rect x="{x + 2}" y="{y + 2}" width="3" '
                     f'height="3"/>')
    seed = 7
    for y in range(21):
        for x in range(21):
            seed = (seed * 1103515245 + 12345) % 2**31
            inside_finder = (x < 8 and y < 8) or (x > 12 and y < 8) or (x < 8 and y > 12)
            if not inside_finder and seed % 3 == 0:
                cells.append(f'<rect x="{x}" y="{y}" width="1" height="1"/>')
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="-1 -1 23 23" shape-rendering='
           '"crispEdges"><rect x="-1" y="-1" width="23" height="23" fill="#fff"/>'
           + "".join(cells) + "</svg>")
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


_CONSOLE_QR = _console_qr()
