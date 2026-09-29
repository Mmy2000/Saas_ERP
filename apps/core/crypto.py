"""Column-level encryption and blind indexes for PII such as national IDs (§24).

`encrypt`/`decrypt` use Fernet (AES-128-CBC + HMAC). `blind_index` is an HMAC-SHA256 of the
normalized value, stored next to the ciphertext so exact-match search works without
decrypting. Keys come from settings (FIELD_ENCRYPTION_KEY, BLIND_INDEX_KEY) and must live
outside the database; losing FIELD_ENCRYPTION_KEY makes the encrypted values unreadable.
"""

from __future__ import annotations

import hashlib
import hmac
from functools import lru_cache

from cryptography.fernet import Fernet
from django.conf import settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    return Fernet(settings.FIELD_ENCRYPTION_KEY)


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    if not token:
        return ""
    return _fernet().decrypt(token.encode()).decode()


def blind_index(value: str, *, purpose: str) -> str:
    """Deterministic, keyed hash; `purpose` keeps indexes of different fields unlinkable."""
    if not value:
        return ""
    key = settings.BLIND_INDEX_KEY.encode()
    return hmac.new(key, f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()
