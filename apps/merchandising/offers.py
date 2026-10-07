"""Which offer a gold piece (or a weight of bulk gold) gets on a given day, and what it does.

An offer applies while it runs (from its first to its last day, if switched on) to the pieces in
its scope (categories, karats, suppliers, branches, barcodes; an empty list means all). Of the
price offers that match, the one giving the lowest making charge wins; separately, of the
cashback offers, the largest cashback. The making charge never goes below what it cost: the
pricing engine keeps that floor.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext as _

from apps.core.errors import ValidationError
from apps.core.numeric import MONEY, UNIT_PRICE, quantize

from .models import CASHBACK_KINDS, PRICE_KINDS, Offer, OfferKind

ZERO = Decimal(0)


@dataclass(frozen=True)
class Deal:
    """What the offers do to one line. `making_rate` None: no price offer applies.
    `cashbacks`: the matching cashback offers as (id, name, kind, value)."""

    making_rate: Decimal | None = None
    offer_id: int | None = None
    offer_name: str = ""
    cashbacks: tuple = ()

    def cashback(self, line_total: Decimal, pieces: int) -> tuple[Decimal, int | None, str]:
        """(amount, offer id, offer name) of the largest cashback for this line."""
        best = (ZERO, None, "")
        for offer_id, name, kind, value in self.cashbacks:
            if kind == OfferKind.CASHBACK_PERCENT:
                amount = quantize(line_total * value / 100, MONEY)
            else:
                amount = quantize(value * max(pieces, 1), MONEY)
            amount = min(amount, line_total)
            if amount > best[0]:
                best = (amount, offer_id, name)
        return best


NO_DEAL = Deal()


def running(day: date, branch_id: int | None = None) -> list[Offer]:
    offers = Offer.objects.filter(is_active=True, starts_on__lte=day, ends_on__gte=day)
    result = list(offers.order_by("pk"))
    if branch_id is not None:
        result = [o for o in result if not o.branch_ids or branch_id in o.branch_ids]
    return result


def matches(offer: Offer, *, category_id, karat_id, supplier_id, barcode) -> bool:
    return ((not offer.category_ids or category_id in offer.category_ids)
            and (not offer.karat_ids or karat_id in offer.karat_ids)
            and (not offer.supplier_ids or supplier_id in offer.supplier_ids)
            and (not offer.barcodes or barcode in offer.barcodes))


def offered_rate(offer: Offer, making: Decimal) -> Decimal:
    if offer.kind == OfferKind.MAKING_PERCENT:
        return quantize(making * (1 - offer.value / 100), UNIT_PRICE)
    return min(quantize(offer.value, UNIT_PRICE), making)  # an offer never raises the price


def deal_for(offers: list[Offer], *, making: Decimal, category_id, karat_id, supplier_id=None,
             barcode="") -> Deal:
    found = [o for o in offers if matches(o, category_id=category_id, karat_id=karat_id,
                                          supplier_id=supplier_id, barcode=barcode)]
    price = [(offered_rate(o, making), o) for o in found if o.kind in PRICE_KINDS]
    best_price = min(price, key=lambda pair: (pair[0], pair[1].pk)) if price else None
    cashbacks = tuple((o.pk, o.name, o.kind, o.value) for o in found
                      if o.kind in CASHBACK_KINDS)
    if best_price is None and not cashbacks:
        return NO_DEAL
    return Deal(making_rate=best_price[0] if best_price else None,
                offer_id=best_price[1].pk if best_price else None,
                offer_name=best_price[1].name if best_price else "", cashbacks=cashbacks)


# ---- managing offers --------------------------------------------------------------------------

def _ids(values) -> list[int]:
    return sorted({int(v) for v in values or [] if str(v).strip().isdigit()})


def save_offer(data: dict, *, offer: Offer | None = None, actor=None) -> Offer:
    """Create or edit an offer. `data` holds the form's fields (already type-checked)."""
    if actor is not None:
        actor.require("sales.offer.manage")
    if data["ends_on"] < data["starts_on"]:
        raise ValidationError(_("Please correct the highlighted fields."),
                              fields={"ends_on": [_("The last day is before the first.")]})
    if data["kind"] == OfferKind.MAKING_PERCENT and not ZERO < data["value"] <= 100:
        raise ValidationError(_("Please correct the highlighted fields."),
                              fields={"value": [_("Between 0 and 100%.")]})
    if data["kind"] == OfferKind.CASHBACK_PERCENT and not ZERO < data["value"] <= 50:
        raise ValidationError(_("Please correct the highlighted fields."),
                              fields={"value": [_("Between 0 and 50%.")]})
    barcodes = sorted({b.strip() for b in data.get("barcodes") or [] if b.strip()})
    with transaction.atomic():
        offer = offer or Offer(created_by=getattr(actor, "user", None))
        offer.name = data["name"].strip()[:120]
        offer.kind, offer.value = data["kind"], data["value"]
        offer.starts_on, offer.ends_on = data["starts_on"], data["ends_on"]
        offer.is_active = data.get("is_active", True)
        offer.category_ids, offer.karat_ids = _ids(data.get("category_ids")), _ids(
            data.get("karat_ids"))
        offer.supplier_ids, offer.branch_ids = _ids(data.get("supplier_ids")), _ids(
            data.get("branch_ids"))
        offer.barcodes, offer.note = barcodes[:5000], (data.get("note") or "").strip()[:300]
        offer.updated_by = getattr(actor, "user", None)
        offer.save()
    return offer


def pieces_in_offer(offer: Offer):
    """The on-hand gold pieces an offer covers (for its page)."""
    from apps.inventory.models import ON_HAND, Item

    items = Item.objects.filter(status__in=ON_HAND, karat__isnull=False).exclude(
        category__product_family__in=("diamond", "stone"))
    for field, values in (("category_id__in", offer.category_ids),
                          ("karat_id__in", offer.karat_ids),
                          ("supplier_id__in", offer.supplier_ids),
                          ("branch_id__in", offer.branch_ids), ("barcode__in", offer.barcodes)):
        if values:
            items = items.filter(Q(**{field: values}))
    return items
