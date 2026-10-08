"""Reading the sign-in history: the browser and system in plain words, and the filters."""

from __future__ import annotations

import re
from datetime import date, timedelta

from django.utils import timezone

from .models import SignInEvent, SignInOutcome

# Order matters: Edge and Opera say "Chrome" too, Chrome says "Safari".
_BROWSERS = [("Edg/", "Edge"), ("OPR/", "Opera"), ("SamsungBrowser", "Samsung Internet"),
             ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("CriOS", "Chrome"),
             ("Safari/", "Safari")]
_SYSTEMS = [("Windows", "Windows"), ("iPhone", "iPhone"), ("iPad", "iPad"),
            ("Android", "Android"), ("Mac OS X", "macOS"), ("CrOS", "ChromeOS"),
            ("Linux", "Linux")]


def device(user_agent: str) -> str:
    """"Chrome · Windows", or what the browser sent when it is something else (a script)."""
    if not user_agent:
        return ""
    browser = next((name for mark, name in _BROWSERS if mark in user_agent), "")
    system = next((name for mark, name in _SYSTEMS if mark in user_agent), "")
    if not browser and not system:
        return re.sub(r"\s+", " ", user_agent)[:60]
    return " · ".join(part for part in (browser, system) if part)


def _date(value: str):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def search(params) -> tuple:
    events = SignInEvent.objects.select_related("membership__user")
    who, outcome, ip = params.get("user", ""), params.get("outcome", ""), params.get("ip", "")
    date_from, date_to = _date(params.get("from", "")), _date(params.get("to", ""))
    if who.isdigit():
        events = events.filter(membership_id=int(who))
    if outcome in SignInOutcome.values:
        events = events.filter(outcome=outcome)
    if ip.strip():
        events = events.filter(ip__startswith=ip.strip()[:45])
    if date_from:
        events = events.filter(at__date__gte=date_from)
    if date_to:
        events = events.filter(at__date__lte=date_to)
    filters = {"user": who, "outcome": outcome, "ip": ip.strip(), "from": date_from,
               "to": date_to}
    return events.order_by("-at", "-id"), filters


def failed_recently(hours: int = 24) -> int:
    return SignInEvent.objects.filter(outcome=SignInOutcome.FAILED,
                                      at__gte=timezone.now() - timedelta(hours=hours)).count()
