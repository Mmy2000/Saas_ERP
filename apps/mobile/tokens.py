"""Tokens for the mobile app, in the shape it was built for (Gweb's SimpleJWT setup):

* access: a JWT (HS256), 7 minutes, sent as "Authorization: Bearer <access>". The app reads its
  `exp` to refresh just before it runs out. Claims as Gweb's: user_id, usercode, branch, is_staff,
  exp, jti, token_type; plus the client (`tid`) and the device row (`sid`), so a token never works
  on another client's address and a signed-out phone stops at once.
* refresh: an opaque random string, 30 days, kept only as a hash on the device row. Each refresh
  returns a new one and the old one stops working (Gweb rotated and blacklisted the same way).

The JWT is written with the standard library (base64url + HMAC-SHA256), keyed off SECRET_KEY.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import MobileDevice

ACCESS_LIFETIME = timedelta(minutes=7)
REFRESH_LIFETIME = timedelta(days=30)
_HEADER = {"alg": "HS256", "typ": "JWT"}


class TokenError(Exception):
    """A token that is not valid (bad signature, expired, signed out, another client)."""


def _key() -> bytes:
    return hashlib.sha256(f"gweb.mobile.access|{settings.SECRET_KEY}".encode()).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def encode(claims: dict) -> str:
    head = _b64(json.dumps(_HEADER, separators=(",", ":")).encode())
    body = _b64(json.dumps(claims, separators=(",", ":")).encode())
    signature = hmac.new(_key(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64(signature)}"


def decode(token: str) -> dict:
    try:
        head, body, signature = token.split(".")
        expected = hmac.new(_key(), f"{head}.{body}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(signature)):
            raise TokenError("bad signature")
        if json.loads(_unb64(head)).get("alg") != "HS256":
            raise TokenError("bad algorithm")
        claims = json.loads(_unb64(body))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise TokenError("malformed") from exc
    if claims.get("token_type") != "access":
        raise TokenError("not an access token")
    if int(claims.get("exp", 0)) <= int(timezone.now().timestamp()):
        raise TokenError("expired")
    return claims


def _hash(refresh: str) -> str:
    return hashlib.sha256(refresh.encode()).hexdigest()


@dataclass(frozen=True)
class Pair:
    access: str
    refresh: str


def access_for(device: MobileDevice) -> str:
    membership = device.membership
    now = timezone.now()
    branch = membership.default_branch
    return encode({
        "token_type": "access", "exp": int((now + ACCESS_LIFETIME).timestamp()),
        "iat": int(now.timestamp()), "jti": uuid.uuid4().hex, "user_id": membership.user_id,
        "usercode": membership.legacy_usercode or membership.pk,
        "branch": branch.code if branch is not None else None,
        "is_staff": is_owner(membership), "tid": device.tenant_id, "sid": device.pk})


def is_owner(membership) -> bool:
    """Gweb's is_staff (sees everything): a member holding the Owner role's wildcard."""
    from apps.iam.catalog import WILDCARD

    return membership.roles.filter(role__grants__permission=WILDCARD).exists()


def sign_in(membership, *, user_agent: str = "", ip: str | None = None) -> Pair:
    refresh = secrets.token_urlsafe(48)
    device = MobileDevice.objects.create(
        membership=membership, refresh_hash=_hash(refresh),
        refresh_expires_at=timezone.now() + REFRESH_LIFETIME, user_agent=user_agent[:300],
        ip=ip, created_by=membership.user)
    return Pair(access_for(device), refresh)


def _device_for(refresh: str, *, lock: bool = False) -> MobileDevice:
    rows = MobileDevice.objects.select_related("membership__user", "membership__default_branch")
    if lock:
        rows = rows.select_for_update(of=("self",))
    device = rows.filter(refresh_hash=_hash(refresh or "")).first()
    if device is None or not device.active:
        raise TokenError("refresh not valid")
    return device


def rotate(refresh: str) -> Pair:
    """A new access and refresh token; the refresh token given stops working."""
    with transaction.atomic():
        device = _device_for(refresh, lock=True)
        if not device.membership.is_active or not device.membership.user.is_active:
            raise TokenError("member not active")
        fresh = secrets.token_urlsafe(48)
        device.refresh_hash = _hash(fresh)
        device.refresh_expires_at = timezone.now() + REFRESH_LIFETIME
        device.last_seen_at = timezone.now()
        device.save(update_fields=["refresh_hash", "refresh_expires_at", "last_seen_at",
                                   "updated_at"])
    return Pair(access_for(device), fresh)


def sign_out(refresh: str, *, membership) -> bool:
    """False when the token belongs to someone else (Gweb answered 403 then)."""
    try:
        device = _device_for(refresh)
    except TokenError:
        return True  # already signed out: nothing to do
    if device.membership_id != membership.pk:
        return False
    revoke(device)
    return True


def revoke(device: MobileDevice) -> None:
    if device.revoked_at is None:
        device.revoked_at = timezone.now()
        device.save(update_fields=["revoked_at", "updated_at"])


def revoke_all(membership) -> int:
    return MobileDevice.objects.filter(membership=membership, revoked_at__isnull=True).update(
        revoked_at=timezone.now())
