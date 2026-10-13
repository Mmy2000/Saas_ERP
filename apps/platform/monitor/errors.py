"""Server errors kept for the platform console (Console → Errors).

`ErrorLogHandler` is a logging handler on `django.request` (every 500) and the job worker. It
groups an error by its exception type and the deepest place in our own code it came from, and
counts repeats instead of storing each one. The row is written through the "errorlog" database
alias: a separate connection, because the request's own transaction is rolled back after a 500
(apps.core.tenancy.middleware) and would take the record with it.

A new error, or one that comes back after being marked resolved, can e-mail ERROR_ALERT_EMAILS
(sent from a short-lived thread, so the failing request does not wait for the mail server).
Recording never raises: a broken error log must not hide the original error.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import traceback as tb
from pathlib import Path

from django.conf import settings
from django.db import connections, transaction
from django.utils import timezone

ALIAS = "errorlog"
OURS = str(Path(settings.BASE_DIR) / "apps")


def _alias() -> str:
    return ALIAS if ALIAS in connections.databases else "default"


def _where(exc_tb) -> str:
    """The deepest frame in our own code (else the deepest frame at all)."""
    frames = tb.extract_tb(exc_tb) if exc_tb else []
    ours = [f for f in frames if f.filename.startswith(OURS)]
    frame = (ours or frames or [None])[-1]
    if frame is None:
        return ""
    name = frame.filename
    if name.startswith(str(settings.BASE_DIR)):
        name = name[len(str(settings.BASE_DIR)) + 1:]
    return f"{name}:{frame.lineno} in {frame.name}"[:300]


def fingerprint(exc_type: str, where: str) -> str:
    return hashlib.sha256(f"{exc_type}|{where}".encode()).hexdigest()


def record_error(exc_info, *, request=None) -> None:
    """Count one occurrence of the error in `exc_info` (type, value, traceback)."""
    from .models import ErrorEvent

    exc_class, value, exc_tb = exc_info
    exc_type = f"{exc_class.__module__}.{exc_class.__qualname__}" if exc_class else "Error"
    where = _where(exc_tb)
    now = timezone.now()
    details = {
        "exc_type": exc_type[:200], "message": str(value)[:500], "where": where,
        "last_seen": now, "traceback": "".join(tb.format_exception(*exc_info))[-20000:],
    }
    if request is not None:
        tenant = getattr(request, "tenant", None)
        user = getattr(request, "user", None)
        details.update({
            "last_method": request.method[:10], "last_path": request.get_full_path()[:500],
            "last_host": request.get_host()[:200] if _safe_host(request) else "",
            "last_tenant_id": getattr(tenant, "pk", None),
            "last_user": (getattr(user, "email", "") or getattr(user, "username", "") or ""
                          )[:200] if getattr(user, "is_authenticated", False) else "",
        })
    alias = _alias()
    with transaction.atomic(using=alias):
        event = (ErrorEvent.objects.using(alias).select_for_update()
                 .filter(fingerprint=fingerprint(exc_type, where)).first())
        if event is None:
            event = ErrorEvent(fingerprint=fingerprint(exc_type, where), first_seen=now,
                               count=0)
            alert = True
        else:
            alert = event.resolved_at is not None  # came back after being resolved
        for key, value in details.items():
            setattr(event, key, value)
        event.count += 1
        event.resolved_at = None
        event.save(using=alias)
    if alert:
        _alert(event)


def _safe_host(request) -> bool:
    try:
        request.get_host()
    except Exception:  # noqa: BLE001 - a bad Host header is often why it failed
        return False
    return True


def _alert(event) -> None:
    recipients = getattr(settings, "ERROR_ALERT_EMAILS", [])
    if not recipients:
        return

    def send():
        from django.core.mail import send_mail

        try:
            send_mail(f"[Gweb] {event.exc_type}: {event.message[:120]}",
                      f"{event.where}\n{event.last_method} {event.last_path}\n\n"
                      f"{event.traceback[-6000:]}",
                      None, recipients, fail_silently=True)
        finally:
            connections.close_all()

    threading.Thread(target=send, daemon=True).start()


class ErrorLogHandler(logging.Handler):
    """Logs carrying an exception (`logger.exception`, Django's 500s) go to the error log."""

    def emit(self, record: logging.LogRecord) -> None:
        if not record.exc_info or record.exc_info[0] is None:
            return
        try:
            record_error(record.exc_info, request=getattr(record, "request", None))
        except Exception:  # noqa: BLE001 - never let the error log raise
            pass
