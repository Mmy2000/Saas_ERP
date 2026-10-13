"""Blind counts (legacy GHS45a / GHS45b مطابقة جرد الفرع): the branch's pieces per category (or
main group) and karat, its bulk gold by weight and its cash, typed by someone who does not see
the books. Every check is kept under its own number with what was typed and what the books
held at that moment; a figure that differs is only shown as differing (owners see the real
numbers). Pieces must match exactly, weights to 0.01 g and cash to the whole unit, as in Gweb.

Nothing is posted: a blind count is a control, not an adjustment (that is the stocktake's job).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.models import ItemCategory, Karat, ProductFamily, Tracking
from apps.core.errors import ValidationError
from apps.core.models import DocStatus
from apps.core.sequences import allocate_number
from apps.org.models import Branch

from .models import BlindCount, Item, ItemStatus, LotBalance

ZERO = Decimal(0)
ON_SHELF = (ItemStatus.IN_STOCK, ItemStatus.RESERVED)  # physically in the shop
WEIGHT_STEP, CASH_STEP = Decimal("0.01"), Decimal("1")
PARTS = ("pieces", "weights", "cash")
KARAT_FAMILIES = (ProductFamily.GOLD, ProductFamily.SILVER, ProductFamily.BULLION_COIN,
                  ProductFamily.DIAMOND)


def _field(name: str, message: str) -> ValidationError:
    return ValidationError(message, fields={name: [message]})


def _round(value: Decimal, step: Decimal) -> Decimal:
    return Decimal(value).quantize(step, rounding=ROUND_HALF_UP)


def _families() -> list[str]:
    """Families counted as pieces: gold always; diamond pieces when the feature is on (loose
    stones are counted in their own parcels)."""
    from apps.diamonds.services import enabled

    families = [value for value in ProductFamily.values
                if value not in (ProductFamily.DIAMOND, ProductFamily.STONE)]
    if enabled():
        families.append(ProductFamily.DIAMOND)
    return families


def _root(category: ItemCategory, by_id: dict[int, ItemCategory]) -> ItemCategory:
    while category.parent_id and category.parent_id in by_id:
        category = by_id[category.parent_id]
    return category


@dataclass
class Sheet:
    """What the counter fills in: rows (categories or main groups) × karat columns."""

    by_group: bool
    piece_rows: list[ItemCategory] = field(default_factory=list)
    weight_rows: list[ItemCategory] = field(default_factory=list)
    karats: list[Karat] = field(default_factory=list)
    row_of: dict[int, int] = field(default_factory=dict)  # category id → its row's id
    cash_currency: str = ""
    no_karat: bool = False  # a column for goods without a karat (watches, steel…)

    @property
    def columns(self) -> list[Karat | None]:
        return [*self.karats, *([None] if self.no_karat else [])]


def sheet(*, by_group: bool = False) -> Sheet:
    from apps.pricing.selectors import functional_currency

    categories = list(ItemCategory.objects.filter(is_active=True).order_by("code"))
    by_id = {c.pk: c for c in categories}
    parents = {c.parent_id for c in categories if c.parent_id}
    leaves = [c for c in categories if c.pk not in parents]
    families = set(_families())
    result = Sheet(by_group=by_group, cash_currency=functional_currency(),
                   karats=list(Karat.objects.filter(is_active=True).select_related("metal")
                               .order_by("metal__code", "-code")))
    pieces, weights = {}, {}
    for leaf in leaves:
        if leaf.product_family not in families:
            continue
        row = _root(leaf, by_id) if by_group else leaf
        result.row_of[leaf.pk] = row.pk
        target = pieces if leaf.tracking == Tracking.SERIALIZED else weights
        target.setdefault(row.pk, row)
        if leaf.default_karat_id is None and leaf.product_family not in KARAT_FAMILIES:
            result.no_karat = True
    result.piece_rows = sorted(pieces.values(), key=lambda c: c.code)
    result.weight_rows = sorted(weights.values(), key=lambda c: c.code)
    return result


def books(branch: Branch, layout: Sheet) -> dict:
    """What the books hold in the branch now, keyed like the sheet's cells."""
    from apps.treasury.holders import balance
    from apps.treasury.models import CashBox

    pieces: dict[tuple[int, int | None], int] = defaultdict(int)
    for row in (Item.objects.filter(branch=branch, status__in=ON_SHELF)
                .values("category_id", "karat_id").annotate(n=Count("id"))):
        target = layout.row_of.get(row["category_id"])
        if target is not None:
            pieces[(target, row["karat_id"])] += row["n"]
    weights: dict[tuple[int, int | None], Decimal] = defaultdict(lambda: ZERO)
    for row in (LotBalance.objects.filter(lot__branch=branch)
                .values("lot__category_id", "lot__karat_id")
                .annotate(g=Sum("gross_weight_g"))):
        target = layout.row_of.get(row["lot__category_id"])
        if target is not None and row["g"]:
            weights[(target, row["lot__karat_id"])] += row["g"]
    cash = sum((balance(box.account_id)[0] for box in CashBox.objects.filter(
        branch=branch, currency__code=layout.cash_currency, is_active=True)), ZERO)
    return {"pieces": pieces, "weights": weights, "cash": cash}


@dataclass(frozen=True)
class BlindCountInput:
    branch_id: int
    by_group: bool = False
    parts: tuple[str, ...] = PARTS
    # {(row id, karat id or None): "12"}; a cell left empty counts as nothing there.
    pieces: dict = field(default_factory=dict)
    weights: dict = field(default_factory=dict)
    cash: str | Decimal | None = None
    note: str = ""


def _number(value, name: str, step: Decimal, whole: bool = False) -> Decimal:
    text = str(value if value is not None else "").strip().replace(",", "")
    if not text:
        return ZERO
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        raise _field(name, _("Enter a number.")) from None
    if not number.is_finite() or number < 0 or (whole and number != number.to_integral()):
        raise _field(name, _("Enter a whole number, zero or more.") if whole
                     else _("Must not be negative."))
    return number if whole else _round(number, step)


def record(data: BlindCountInput, *, actor=None) -> BlindCount:
    parts = tuple(p for p in PARTS if p in data.parts)
    if not parts:
        raise _field("parts", _("Choose what to count."))
    with transaction.atomic():
        branch = Branch.objects.filter(pk=data.branch_id, is_active=True).first()
        if branch is None:
            raise _field("branch", _("Unknown branch."))
        if actor is not None:
            actor.require("inventory.blind_count.create", branch=branch)
        layout = sheet(by_group=data.by_group)
        held = books(branch, layout)
        differences = 0
        karat_of = {k.pk: k for k in layout.karats}

        def cells(rows, typed, actual, kind, step):
            nonlocal differences
            kept = []
            seen = set()
            for row in rows:
                for karat in layout.columns:
                    key = (row.pk, karat.pk if karat else None)
                    real = actual.get(key, 0)
                    value = typed.get(key)
                    counted = _number(value, f"{kind}-{row.pk}-{key[1] or 0}", step,
                                      whole=kind == "p")
                    real = Decimal(real) if kind == "p" else _round(real, step)
                    if not counted and not real:
                        continue
                    seen.add(key)
                    ok = counted == real
                    differences += not ok
                    kept.append({"category": row.pk, "name": row.name, "karat": key[1],
                                 "label": karat_of[key[1]].label if key[1] else "",
                                 "counted": str(counted), "actual": str(real), "ok": ok})
            # Stock in a karat no longer active still counts against the row.
            for key, real in actual.items():
                if key not in seen and key[0] in {r.pk for r in rows} and real:
                    differences += 1
                    kept.append({"category": key[0], "name": next(
                        r.name for r in rows if r.pk == key[0]), "karat": key[1],
                        "label": "", "counted": "0", "actual": str(real), "ok": False})
            return kept

        count = BlindCount(branch=branch, business_date=timezone.localdate(),
                           by_group=data.by_group, note=data.note.strip()[:500],
                           created_by=getattr(actor, "user", None))
        if "pieces" in parts:
            count.pieces = cells(layout.piece_rows, data.pieces, held["pieces"], "p", None)
        if "weights" in parts:
            count.weights = cells(layout.weight_rows, data.weights, held["weights"], "w",
                                  WEIGHT_STEP)
        if "cash" in parts:
            count.cash_currency = layout.cash_currency
            count.cash_counted = _number(data.cash, "cash", CASH_STEP)
            count.cash_actual = _round(held["cash"], CASH_STEP)
            differences += count.cash_counted != count.cash_actual
        count.differences = differences
        count.status = DocStatus.POSTED
        count.posted_at = timezone.now()
        count.posted_by = getattr(actor, "user", None)
        count.save()
        count.number = allocate_number("BC", branch=branch, fiscal_year=count.business_date.year)
        count.save(update_fields=["number"])
    return count
