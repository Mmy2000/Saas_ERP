"""Pure helpers for party data: phone and national-ID normalization."""

from __future__ import annotations

import re

import phonenumbers


class InvalidPhone(ValueError):
    pass


def normalize_phone(raw: str, region: str) -> str:
    """E.164 form of `raw` ("01012345678" in EG → "+201012345678"), or "" when blank.

    Raises InvalidPhone for input that cannot be a phone number in `region`.
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        number = phonenumbers.parse(raw, region.upper())
    except phonenumbers.NumberParseException as exc:
        raise InvalidPhone(raw) from exc
    if not phonenumbers.is_possible_number(number):
        raise InvalidPhone(raw)
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def normalize_national_id(raw: str) -> str:
    """Strip spaces, dashes and dots; upper-case. Used before encrypting and hashing."""
    return re.sub(r"[\s\-.]", "", raw or "").upper()
