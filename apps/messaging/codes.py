"""Sending one-time codes to a customer's phone (sale confirmation), set up once for the whole
platform in settings, tried in the order of CODE_CHANNELS until one accepts:

* "whatsapp": the WhatsApp gateway Gweb uses (POST {url}{endpoint}, header
  "Authorization: ApiKey <key>", JSON phone / otp_code / amount / currency / customer_name /
  employee_name). WHATSAPP_GATEWAY_URL, WHATSAPP_GATEWAY_KEY, WHATSAPP_GATEWAY_ENDPOINT.
* "sms": a plain HTTP SMS provider (form fields mobile / message / sender, a bearer key).
  SMS_API_URL, SMS_API_KEY, SMS_SENDER.
* "console": development and tests; the message is logged and kept in OUTBOX.

Standard library HTTP only, like the market price readers. Never raises: the caller gets the
channel that took the message, or why none did.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)
TIMEOUT = 15
OUTBOX: list[CodeMessage] = []  # the console channel's messages, for tests


@dataclass(frozen=True)
class CodeMessage:
    phone: str  # E.164, "+201012345678"
    code: str
    text: str  # the whole message, in the shop's language
    amount: Decimal
    currency: str
    customer_name: str = ""
    employee_name: str = ""


@dataclass(frozen=True)
class Sent:
    ok: bool
    channel: str = ""
    error: str = ""


def _post(url: str, body: bytes, headers: dict) -> tuple[int, dict]:
    request = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 (configured URL)
            status, raw = response.status, response.read()
    except HTTPError as error:
        status, raw = error.code, error.read()
    try:
        return status, json.loads(raw or b"{}")
    except ValueError:
        return status, {}


def _whatsapp(message: CodeMessage) -> Sent:
    url, key = settings.WHATSAPP_GATEWAY_URL, settings.WHATSAPP_GATEWAY_KEY
    if not (url and key):
        return Sent(False, "whatsapp", "not set up")
    payload = {"phone": message.phone, "otp_code": message.code, "amount": str(message.amount),
               "currency": message.currency, "customer_name": message.customer_name,
               "employee_name": message.employee_name}
    status, body = _post(f"{url.rstrip('/')}{settings.WHATSAPP_GATEWAY_ENDPOINT}",
                         json.dumps(payload).encode(),
                         {"Authorization": f"ApiKey {key}", "Content-Type": "application/json"})
    if status == 200 and body.get("success", True):
        return Sent(True, "whatsapp")
    return Sent(False, "whatsapp", str(body.get("message") or body.get("error") or status))


def _sms(message: CodeMessage) -> Sent:
    url, key = settings.SMS_API_URL, settings.SMS_API_KEY
    if not (url and key):
        return Sent(False, "sms", "not set up")
    payload = {"mobile": message.phone, "message": message.text, "sender": settings.SMS_SENDER}
    status, body = _post(url, urlencode(payload).encode(),
                         {"Authorization": f"Bearer {key}",
                          "Content-Type": "application/x-www-form-urlencoded"})
    if status == 200:
        return Sent(True, "sms")
    return Sent(False, "sms", str(body.get("message") or status))


def _console(message: CodeMessage) -> Sent:
    OUTBOX.append(message)
    logger.warning("Code for %s: %s", message.phone, message.text)
    return Sent(True, "console")


CHANNELS = {"whatsapp": _whatsapp, "sms": _sms, "console": _console}


def configured() -> bool:
    """Whether any channel can send (the console counts: development)."""
    for name in settings.CODE_CHANNELS:
        if name == "console":
            return True
        if name == "whatsapp" and settings.WHATSAPP_GATEWAY_URL and settings.WHATSAPP_GATEWAY_KEY:
            return True
        if name == "sms" and settings.SMS_API_URL and settings.SMS_API_KEY:
            return True
    return False


def send_code(message: CodeMessage) -> Sent:
    errors = []
    for name in settings.CODE_CHANNELS:
        channel = CHANNELS.get(name)
        if channel is None:
            continue
        try:
            sent = channel(message)
        except (URLError, TimeoutError, OSError) as error:
            sent = Sent(False, name, str(error))
        if sent.ok:
            return sent
        errors.append(f"{name}: {sent.error}")
        logger.warning("Code not sent by %s: %s", name, sent.error)
    return Sent(False, "", "; ".join(errors) or _("No way to send codes is set up."))
