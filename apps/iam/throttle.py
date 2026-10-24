"""Too many wrong passwords: a short lock on the account and on the address (TD-02).

Every sign-in (workspace, console, Django admin, mobile app) goes through `authenticate()`, so
the backends refuse while locked, and failures are counted from Django's `user_login_failed`
signal (apps.iam.signals). The sign-in forms check first to say why.

Counts live in the cache: one window per key, started by the first failure. Production's cache
is a file cache shared by every process on the server; with several servers `CACHE_URL` must
point at a shared one, or each server counts on its own.
"""

from __future__ import annotations

import math
import time

from django.core.cache import cache

WINDOW_SECONDS = 15 * 60
ACCOUNT_LIMIT = 5  # wrong passwords for one account, from anywhere, per window
ADDRESS_LIMIT = 30  # wrong passwords from one address, for any accounts, per window


def _keys(request, username: str) -> list[tuple[str, int]]:
    from .signals import client_ip

    tenant = getattr(request, "tenant", None) if request is not None else None
    scope = tenant.id if tenant is not None else "platform"
    keys = [(f"signin:{scope}:account:{(username or '').strip().lower()[:150]}", ACCOUNT_LIMIT)]
    address = client_ip(request) if request is not None else None
    if address:
        keys.append((f"signin:address:{address}", ADDRESS_LIMIT))
    return keys


def _window(key: str, now: float) -> dict | None:
    state = cache.get(key)
    if state is None or now - state["since"] >= WINDOW_SECONDS:
        return None
    return state


def locked_for(request, username: str) -> int:
    """Seconds until signing in may be tried again (0: not locked)."""
    now = time.time()
    waits = [WINDOW_SECONDS - (now - state["since"])
             for key, limit in _keys(request, username)
             if (state := _window(key, now)) is not None and state["count"] >= limit]
    return math.ceil(max(waits)) if waits else 0


def failed(request, username: str) -> None:
    now = time.time()
    for key, _limit in _keys(request, username):
        state = _window(key, now) or {"count": 0, "since": now}
        state["count"] += 1
        cache.set(key, state, math.ceil(WINDOW_SECONDS - (now - state["since"])) or 1)


def succeeded(request, username: str) -> None:
    """The right password: that account starts afresh (the address keeps its count)."""
    cache.delete(_keys(request, username)[0][0])
