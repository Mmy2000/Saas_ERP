"""Stock position queries."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Count, Sum

from apps.catalog.domain.metal import DEFAULT_REFERENCE_FINENESS
from apps.catalog.models import Karat

from .models import ON_HAND, Item, LotBalance

ZERO = Decimal(0)


@dataclass
class StockLine:
    karat: object
    pieces: int = 0
    piece_weight_g: Decimal = ZERO
    bulk_weight_g: Decimal = ZERO
    fine_weight_g: Decimal = ZERO

    @property
    def total_weight_g(self) -> Decimal:
        return self.piece_weight_g + self.bulk_weight_g

    @property
    def equivalent_21k_g(self) -> Decimal:
        return (self.fine_weight_g * 1000 / DEFAULT_REFERENCE_FINENESS).quantize(Decimal("0.001"))


def stock_by_karat(branch_ids=None) -> list[StockLine]:
    """Pieces in stock plus bulk lots, per karat (gold first)."""
    lines: dict[int | None, StockLine] = {}
    items = Item.objects.filter(status__in=ON_HAND)
    lots = LotBalance.objects.filter(gross_weight_g__gt=0)
    if branch_ids is not None:
        items = items.filter(branch_id__in=branch_ids)
        lots = lots.filter(lot__branch_id__in=branch_ids)
    for row in items.values("karat").annotate(n=Count("id"), g=Sum("gross_weight_g"),
                                              f=Sum("fine_weight_g")):
        line = lines.setdefault(row["karat"], StockLine(karat=None))
        line.pieces += row["n"]
        line.piece_weight_g += row["g"] or ZERO
        line.fine_weight_g += row["f"] or ZERO
    for row in lots.values("lot__karat").annotate(g=Sum("gross_weight_g"), f=Sum("fine_weight_g")):
        line = lines.setdefault(row["lot__karat"], StockLine(karat=None))
        line.bulk_weight_g += row["g"] or ZERO
        line.fine_weight_g += row["f"] or ZERO
    karats = {k.pk: k for k in Karat.objects.select_related("metal").filter(pk__in=lines)}
    for karat_id, line in lines.items():
        line.karat = karats.get(karat_id)
    return sorted(lines.values(), key=lambda s: (
        s.karat is None, getattr(getattr(s.karat, "metal", None), "code", "") != "gold",
        -getattr(s.karat, "code", 0)))


def fine_grams_in_stock(branch_ids=None) -> dict[str, Decimal]:
    """Total fine grams per metal code."""
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for line in stock_by_karat(branch_ids):
        if line.karat is not None:
            totals[line.karat.metal.code] += line.fine_weight_g
    return dict(totals)
