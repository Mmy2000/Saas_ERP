"""Reading a report from IGI (the gem lab), the way Gweb's Dim/igi.py did.

The report's PDF is public at IGI_PDF_URL ("https://pdf.igi.org/{name}.pdf"): found when the
answer is a PDF, not found on 404. Report numbers longer than ten characters are also tried
cut to ten (IGI prints a longer number than it files under). The report's data, from
IGI_API_URL with an optional key, is a bonus: that service is usually behind a bot wall, and a
refusal there changes nothing. Standard library HTTP only, never raises.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings

logger = logging.getLogger(__name__)
TIMEOUT = 20
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like "
                   "Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "application/pdf,application/json;q=0.9,*/*;q=0.8",
    "Referer": "https://www.igi.org/",
}


@dataclass
class Report:
    status: str  # verified | not_found | error
    message: str = ""
    pdf: bytes | None = None
    data: dict = field(default_factory=dict)


def _get(url: str, headers: dict | None = None) -> tuple[int, bytes, dict]:
    request = Request(url, headers={**HEADERS, **(headers or {})})
    try:
        with urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 (configured URL)
            return response.status, response.read(), dict(response.headers)
    except HTTPError as error:
        return error.code, error.read() or b"", dict(error.headers or {})


def _names(report_no: str) -> list[str]:
    clean = "".join(ch for ch in report_no if ch.isalnum())
    names = [clean] if clean else []
    if len(clean) > 10:
        names.append(clean[:10])
    return names


def _data(report_no: str) -> dict:
    url = getattr(settings, "IGI_API_URL", "")
    if not url:
        return {}
    headers = {}
    if getattr(settings, "IGI_API_KEY", ""):
        headers["Ocp-Apim-Subscription-Key"] = settings.IGI_API_KEY
    try:
        status, body, _headers = _get(f"{url}?{urlencode({'Printno': report_no})}", headers)
        if status != 200:
            return {}
        payload = json.loads(body or b"{}")
    except (URLError, TimeoutError, OSError, ValueError):
        return {}
    record = payload[0] if isinstance(payload, list) and payload else payload
    if not isinstance(record, dict):
        return {}
    number = str(record.get("REPORT NUMBER") or record.get("PRINTNO") or "")
    return record if number and number.replace(" ", "") in _names(report_no) else {}


def fetch(report_no: str) -> Report:
    names = _names(report_no)
    if not names:
        return Report("error", "No report number.")
    template = getattr(settings, "IGI_PDF_URL", "https://pdf.igi.org/{name}.pdf")
    last = ""
    for name in names:
        try:
            status, body, _headers = _get(template.format(name=name))
        except (URLError, TimeoutError, OSError) as error:
            logger.warning("IGI unreachable: %s", error)
            return Report("error", str(error))
        if status == 200 and body.startswith(b"%PDF"):
            return Report("verified", "", body, _data(name))
        last = "not found" if status == 404 else f"HTTP {status}"
    if last == "not found":
        return Report("not_found", last)
    return Report("error", last)
