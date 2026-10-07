"""Changing selling prices of pieces in bulk, and undoing a change.

The making charge a change starts from is what the piece sells at now: its own rate, else its
category's. "Add 10" on a piece that follows its category's 150/g makes it 160/g of its own.
Undo puts each piece back to what it had before, except pieces whose price was changed again
since (they are counted and left alone).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.models import CategoryMakingCharge
from apps.core.errors import DomainError, ValidationError
from apps.core.numeric import MONEY, UNIT_PRICE, quantize
from apps.inventory.models import ON_HAND, Item

from .models import PriceChange, PriceChangeLine, PriceMethod, PriceTarget

ZERO = Decimal(0)
MAX_PIECES = 20000
DIAMOND_FAMILIES = ("diamond", "stone")


@dataclass
class PieceFilter:
    branch_ids: list[int] = field(default_factory=list)
    category_ids: list[int] = field(default_factory=list)
    karat_ids: list[int] = field(default_factory=list)
    supplier_ids: list[int] = field(default_factory=list)
    min_weight: Decimal | None = None
    max_weight: Decimal | None = None
    barcodes: list[str] = field(default_factory=list)  # typed or pasted, one per line

    def as_json(self) -> dict:
        return {"branch_ids": self.branch_ids, "category_ids": self.category_ids,
                "karat_ids": self.karat_ids, "supplier_ids": self.supplier_ids,
                "min_weight": str(self.min_weight) if self.min_weight is not None else None,
                "max_weight": str(self.max_weight) if self.max_weight is not None else None,
                "barcodes": self.barcodes}


def pieces(target: str, rules: PieceFilter, scope=None):
    """The on-hand pieces a change would apply to. `scope`: the branches the user may change."""
    items = Item.objects.filter(status__in=ON_HAND).select_related("category", "karat")
    if target == PriceTarget.LABEL:
        items = items.filter(category__product_family__in=DIAMOND_FAMILIES)
    else:
        items = items.filter(karat__isnull=False).exclude(
            category__product_family__in=DIAMOND_FAMILIES)
    if scope is not None:
        items = items.filter(branch_id__in=scope)
    conditions = {"branch_id__in": rules.branch_ids, "category_id__in": rules.category_ids,
                  "karat_id__in": rules.karat_ids, "supplier_id__in": rules.supplier_ids,
                  "barcode__in": rules.barcodes}
    for name, values in conditions.items():
        if values:
            items = items.filter(Q(**{name: values}))
    if rules.min_weight is not None:
        items = items.filter(gross_weight_g__gte=rules.min_weight)
    if rules.max_weight is not None:
        items = items.filter(gross_weight_g__lte=rules.max_weight)
    return items.order_by("barcode")


def _category_rates(functional_currency: str) -> dict[int, Decimal]:
    return dict(CategoryMakingCharge.objects.filter(currency__code=functional_currency)
                .values_list("category_id", "list_rate_per_g"))


def current_value(item, target: str, category_rates: dict) -> Decimal | None:
    if target == PriceTarget.LABEL:
        return item.label_price
    return item.list_making_rate or category_rates.get(item.category_id, ZERO)


def new_value(current: Decimal | None, method: str, value: Decimal, target: str) -> Decimal:
    base = current or ZERO
    if method == PriceMethod.SET:
        result = value
    elif method == PriceMethod.ADD:
        result = base + value
    else:
        result = base * (1 + value / 100)
    return quantize(max(result, ZERO), MONEY if target == PriceTarget.LABEL else UNIT_PRICE)


def _value(raw, method: str) -> Decimal:
    try:
        value = Decimal(str(raw).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise ValidationError(_("Please correct the highlighted fields."),
                              fields={"value": [_("Enter a number.")]}) from None
    if method == PriceMethod.SET and value < 0:
        raise ValidationError(_("Please correct the highlighted fields."),
                              fields={"value": [_("Cannot be negative.")]})
    if method == PriceMethod.PERCENT and not Decimal(-90) <= value <= Decimal(500):
        raise ValidationError(_("Please correct the highlighted fields."),
                              fields={"value": [_("Between -90% and +500%.")]})
    return value


def preview(target: str, method: str, raw_value, rules: PieceFilter, *, scope=None,
            limit: int = 50) -> dict:
    from apps.pricing.selectors import functional_currency

    if target not in PriceTarget.values or method not in PriceMethod.values:
        raise ValidationError(_("Choose what to change and how."))
    value = _value(raw_value, method)
    items = pieces(target, rules, scope)
    rates = _category_rates(functional_currency())
    sample = []
    for item in items[:limit]:
        old = current_value(item, target, rates)
        sample.append({"id": item.pk, "barcode": item.barcode, "category": item.category.name,
                       "karat": item.karat.label if item.karat else "",
                       "weight": str(item.gross_weight_g),
                       "old": str(old) if old is not None else None,
                       "new": str(new_value(old, method, value, target))})
    return {"count": items.count(), "sample": sample}


def apply_change(target: str, method: str, raw_value, rules: PieceFilter, *, summary: str = "",
                 note: str = "", scope=None, actor=None) -> PriceChange:
    from apps.pricing.selectors import functional_currency

    if actor is not None:
        actor.require("inventory.price.change")
    if target not in PriceTarget.values or method not in PriceMethod.values:
        raise ValidationError(_("Choose what to change and how."))
    value = _value(raw_value, method)
    with transaction.atomic():
        items = list(pieces(target, rules, scope).select_for_update(of=("self",)))
        if not items:
            raise DomainError(_("No piece matches what you chose."), code="MERCH_NO_PIECES")
        if len(items) > MAX_PIECES:
            raise DomainError(_("At most %(n)s pieces in one change: narrow the choice.")
                              % {"n": MAX_PIECES}, code="MERCH_TOO_MANY")
        rates = _category_rates(functional_currency())
        change = PriceChange.objects.create(
            target=target, method=method, value=value, filters=rules.as_json(),
            summary=summary[:300], note=note.strip()[:200], item_count=len(items),
            created_by=getattr(actor, "user", None))
        lines = []
        field = "label_price" if target == PriceTarget.LABEL else "list_making_rate"
        for item in items:
            stored = getattr(item, field)
            result = new_value(current_value(item, target, rates), method, value, target)
            lines.append(PriceChangeLine(change=change, item=item, old_value=stored,
                                         new_value=result))
            setattr(item, field, result)
        PriceChangeLine.objects.bulk_create(lines, batch_size=1000)
        Item.objects.bulk_update(items, [field, "updated_at"], batch_size=1000)
    return change


def set_piece_price(item_id: int, target: str, raw_value, *, note: str = "",
                    actor=None) -> PriceChange:
    """One piece's price, typed in (kept in the same history as bulk changes)."""
    item = Item.objects.filter(pk=item_id, status__in=ON_HAND).first()
    if item is None:
        raise DomainError(_("This piece is not in stock."), code="MERCH_NOT_IN_STOCK")
    if actor is not None:
        actor.require("inventory.price.change", branch=item.branch)
    return apply_change(target, PriceMethod.SET, raw_value, PieceFilter(barcodes=[item.barcode]),
                        summary=item.barcode, note=note, actor=actor)


def undo_change(change_id: int, *, actor=None) -> PriceChange:
    if actor is not None:
        actor.require("inventory.price.change")
    with transaction.atomic():
        change = PriceChange.objects.select_for_update().filter(pk=change_id).first()
        if change is None:
            raise DomainError(_("Not found."), code="NOT_FOUND")
        if change.undone_at is not None:
            raise DomainError(_("This change was already undone."), code="MERCH_ALREADY_UNDONE")
        field = "label_price" if change.target == PriceTarget.LABEL else "list_making_rate"
        lines = list(change.lines.select_related("item"))
        later = set(PriceChangeLine.objects.filter(
            item_id__in=[ln.item_id for ln in lines], change__created_at__gt=change.created_at,
            change__undone_at__isnull=True).values_list("item_id", flat=True))
        restored = []
        for line in lines:
            if line.item_id in later or getattr(line.item, field) != line.new_value:
                continue  # changed again since: leave it
            setattr(line.item, field, line.old_value if line.old_value is not None
                    else (None if field == "label_price" else ZERO))
            restored.append(line.item)
        Item.objects.bulk_update(restored, [field, "updated_at"], batch_size=1000)
        change.undone_at, change.undone_count = timezone.now(), len(restored)
        change.undone_by = getattr(actor, "user", None)
        change.save(update_fields=["undone_at", "undone_count", "undone_by", "updated_at"])
    return change
