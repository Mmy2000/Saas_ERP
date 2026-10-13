"""Stone codes (legacy GHD11 تكويد الحجارة): a coded stone description with a reference price per
carat. Entering a stone with a code fills its kind, origin, shape, colour and clarity, and keeps
the code's price per carat at that moment, which gives the stones' reference value; receiving
from a supplier suggests the stones' cost from it."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from apps.catalog.models import Currency
from apps.core.errors import NotFound, ValidationError
from apps.core.numeric import quantize

from .grades import MANAGE, normalizer
from .models import StoneCode, StoneKind, StoneOrigin, StoneShape


def _error(field: str, message: str) -> ValidationError:
    return ValidationError(message, fields={field: [message]})


@dataclass(frozen=True)
class StoneCodeInput:
    code: str
    price_per_ct: Decimal | str
    currency_code: str
    name: str = ""
    kind: str = StoneKind.DIAMOND
    origin: str = ""
    shape: str = ""
    color: str = ""
    clarity: str = ""
    is_active: bool = True


def save_stone_code(data: StoneCodeInput, *, code_id: int | None = None,
                    actor=None) -> StoneCode:
    if actor is not None:
        actor.require(MANAGE)
    code = data.code.strip()[:20]
    if not code:
        raise _error("code", _("This field is required."))
    if data.kind not in StoneKind.values:
        raise _error("kind", _("Choose the kind of stone."))
    if data.origin not in ("", *StoneOrigin.values):
        raise _error("origin", _("Choose natural or lab-grown."))
    if data.shape not in ("", *StoneShape.values):
        raise _error("shape", _("Choose the shape."))
    try:
        price = quantize(data.price_per_ct or "0", Decimal("0.01"))
    except (TypeError, ValueError, InvalidOperation):
        raise _error("price_per_ct", _("Enter a number.")) from None
    if price < 0:
        raise _error("price_per_ct", _("Must not be negative."))
    currency = Currency.objects.filter(code=data.currency_code, is_active=True).first()
    if currency is None:
        raise _error("currency", _("Unknown currency."))
    normalize = normalizer()
    with transaction.atomic():
        if code_id is None:
            row = StoneCode()
        else:
            row = StoneCode.objects.select_for_update().filter(pk=code_id).first()
            if row is None:
                raise NotFound(_("Not found."))
        if StoneCode.objects.filter(code__iexact=code).exclude(pk=row.pk).exists():
            raise _error("code", _("Already in the list."))
        row.code, row.name = code, data.name.strip()[:80]
        row.kind, row.origin, row.shape = data.kind, data.origin, data.shape
        row.color = normalize("color", data.color.strip()[:20])
        row.clarity = normalize("clarity", data.clarity.strip()[:20])
        row.price_per_ct, row.currency, row.is_active = price, currency, data.is_active
        try:
            with transaction.atomic():
                row.save()
        except IntegrityError:
            raise _error("code", _("Already in the list.")) from None
    return row


def codes_for_entry() -> list[dict]:
    """Active codes with what they fill in, for the stone rows of the entry screens."""
    return [{"code": c.code, "label": str(c), "kind": c.kind, "origin": c.origin,
             "shape": c.shape, "color": c.color, "clarity": c.clarity,
             "price": str(c.price_per_ct), "currency": c.currency.code}
            for c in StoneCode.objects.filter(is_active=True).select_related("currency")]
