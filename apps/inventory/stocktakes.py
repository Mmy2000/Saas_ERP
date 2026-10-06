"""Stocktake (جرد, §7.5): count one branch's stock and apply the differences.

start   snapshot the pieces in stock at the branch (optionally one category / karat) as
        expected lines, and the bulk lots in scope with their weights
scan    a barcode: an expected piece is counted; anything else is recorded as not expected here
weigh   a bulk lot: its counted weight (and pieces)
post    expected pieces never scanned and still in stock here become missing (metal loss);
        a missing piece scanned here comes back into stock (metal gain); bulk lots are adjusted
        to the counted weight. Pieces sold or moved during the count are left alone.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.models import ItemCategory, Karat
from apps.core.errors import DomainError, NotFound, ValidationError
from apps.core.models import DocStatus
from apps.core.numeric import MONEY, WEIGHT, quantize
from apps.core.sequences import allocate_number
from apps.ledger.models import EntryKind
from apps.ledger.services import post_entry
from apps.org.models import Branch

from .models import (
    ON_HAND,
    Item,
    ItemStatus,
    LotBalance,
    MovementType,
    Stocktake,
    StocktakeLine,
    StocktakeLotLine,
    StocktakeResult,
)
from .services import DocRef, change_item_status, move_lot
from .valuation import StockPart, stock_lines

DOC_TYPE = "inventory.Stocktake"
ZERO = Decimal(0)


def _open(stocktake_id: int, *, lock: bool = False) -> Stocktake:
    queryset = Stocktake.objects.select_related("branch", "category", "karat")
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    stocktake = queryset.filter(pk=stocktake_id).first()
    if stocktake is None:
        raise NotFound(_("Not found."))
    if stocktake.status != DocStatus.DRAFT:
        raise DomainError(_("This stocktake is closed."), code="STOCKTAKE_CLOSED")
    return stocktake


def start_stocktake(branch_id: int, *, category_id: int | None = None,
                    karat_id: int | None = None, note: str = "", actor=None) -> Stocktake:
    branch = Branch.objects.filter(pk=branch_id, is_active=True).first()
    if branch is None:
        raise ValidationError(_("Unknown branch."), fields={"branch": [_("Unknown branch.")]})
    if actor is not None:
        actor.require("inventory.stocktake.start", branch=branch)
    category = ItemCategory.objects.filter(pk=category_id).first() if category_id else None
    karat = Karat.objects.filter(pk=karat_id).first() if karat_id else None
    try:
        with transaction.atomic():
            today = timezone.localdate()
            stocktake = Stocktake.objects.create(
                branch=branch, category=category, karat=karat, business_date=today,
                note=note.strip(), created_by=getattr(actor, "user", None),
                number=allocate_number("SK", branch=branch, fiscal_year=today.year))
            items = Item.objects.filter(branch=branch, status__in=ON_HAND)
            lots = LotBalance.objects.filter(lot__branch=branch).filter(
                Q(gross_weight_g__gt=0) | Q(qty__gt=0)).select_related("lot")
            if category is not None:
                items, lots = items.filter(category=category), lots.filter(lot__category=category)
            if karat is not None:
                items, lots = items.filter(karat=karat), lots.filter(lot__karat=karat)
            StocktakeLine.objects.bulk_create([
                StocktakeLine(stocktake=stocktake, item_id=pk, barcode=barcode)
                for pk, barcode in items.order_by("barcode").values_list("pk", "barcode")])
            StocktakeLotLine.objects.bulk_create([
                StocktakeLotLine(stocktake=stocktake, lot=balance.lot, expected_qty=balance.qty,
                                 expected_gross_weight_g=balance.gross_weight_g)
                for balance in lots.order_by("lot__category__code", "lot__karat__code")])
    except IntegrityError:
        raise DomainError(_("A stocktake is already open at this branch."),
                          code="STOCKTAKE_ALREADY_OPEN") from None
    return stocktake


@dataclass
class ScanResult:
    line: StocktakeLine
    outcome: str  # counted | already | unexpected


def scan(stocktake_id: int, barcode: str, *, actor=None) -> ScanResult:
    code = barcode.strip()
    if not code:
        raise ValidationError(_("Scan or type a barcode."),
                              fields={"barcode": [_("Required.")]})
    with transaction.atomic():
        stocktake = _open(stocktake_id)
        if actor is not None:
            actor.require("inventory.stocktake.count", branch=stocktake.branch)
        item = Item.objects.filter(Q(barcode=code) | Q(rfid_epc=code)).first()
        key = item.barcode if item is not None else code
        line = (StocktakeLine.objects.select_for_update(of=("self",))
                .select_related("item__category", "item__karat")
                .filter(stocktake=stocktake, barcode=key).first())
        if line is not None and line.result != StocktakeResult.PENDING:
            return ScanResult(line, "already")
        now, user = timezone.now(), getattr(actor, "user", None)
        if line is not None:
            line.result, line.counted_at, line.counted_by = StocktakeResult.COUNTED, now, user
            line.save(update_fields=["result", "counted_at", "counted_by", "updated_at"])
            return ScanResult(line, "counted")
        line = StocktakeLine.objects.create(
            stocktake=stocktake, item=item, barcode=key, expected=False,
            result=StocktakeResult.UNEXPECTED, counted_at=now, counted_by=user)
        return ScanResult(line, "unexpected")


def undo_scan(stocktake_id: int, line_id: int, *, actor=None) -> None:
    """A mistaken scan: an expected piece goes back to not counted, an unexpected one away."""
    with transaction.atomic():
        stocktake = _open(stocktake_id)
        if actor is not None:
            actor.require("inventory.stocktake.count", branch=stocktake.branch)
        line = StocktakeLine.objects.filter(stocktake=stocktake, pk=line_id).first()
        if line is None:
            raise NotFound(_("Not found."))
        if not line.expected:
            line.delete()
            return
        line.result, line.counted_at, line.counted_by = StocktakeResult.PENDING, None, None
        line.save(update_fields=["result", "counted_at", "counted_by", "updated_at"])


def weigh_lot(stocktake_id: int, lot_line_id: int, gross_weight_g, qty: int | None = None, *,
              actor=None) -> StocktakeLotLine:
    with transaction.atomic():
        stocktake = _open(stocktake_id)
        if actor is not None:
            actor.require("inventory.stocktake.count", branch=stocktake.branch)
        line = StocktakeLotLine.objects.filter(stocktake=stocktake, pk=lot_line_id).first()
        if line is None:
            raise NotFound(_("Not found."))
        try:
            gross = quantize(gross_weight_g, WEIGHT)
        except (TypeError, ValueError):
            raise ValidationError(_("Enter a number."),
                                  fields={"gross_weight_g": [_("Enter a number.")]}) from None
        if gross < 0 or (qty is not None and qty < 0):
            raise ValidationError(_("Cannot be negative."),
                                  fields={"gross_weight_g": [_("Cannot be negative.")]})
        line.counted_gross_weight_g = gross
        line.counted_qty = qty if qty is not None else line.expected_qty
        line.save(update_fields=["counted_gross_weight_g", "counted_qty", "updated_at"])
    return line


@dataclass
class StocktakeSummary:
    expected: int
    counted: int
    pending: int
    missing: int
    unexpected: int
    left: int
    lots_weighed: int
    lots: int

    @property
    def progress(self) -> int:
        return round(100 * self.counted / self.expected) if self.expected else 100


def summary(stocktake: Stocktake) -> StocktakeSummary:
    results = Counter(stocktake.lines.values_list("result", flat=True))
    expected = stocktake.lines.filter(expected=True).count()
    lot_lines = stocktake.lot_lines.all()
    return StocktakeSummary(
        expected=expected, counted=results[StocktakeResult.COUNTED],
        pending=results[StocktakeResult.PENDING], missing=results[StocktakeResult.MISSING],
        unexpected=results[StocktakeResult.UNEXPECTED], left=results[StocktakeResult.LEFT],
        lots_weighed=lot_lines.filter(counted_gross_weight_g__isnull=False).count(),
        lots=lot_lines.count())


def post_stocktake(stocktake_id: int, *, actor=None) -> Stocktake:
    with transaction.atomic():
        stocktake = _open(stocktake_id, lock=True)
        branch = stocktake.branch
        if actor is not None:
            actor.require("inventory.stocktake.post", branch=branch)
        today = timezone.localdate()
        doc = DocRef(DOC_TYPE, stocktake.pk)
        lost, found = [], []

        pending = stocktake.lines.filter(result=StocktakeResult.PENDING).order_by("item_id")
        for line in pending.select_related("item"):
            item = Item.objects.select_for_update(of=("self",)).select_related(
                "category", "karat__metal").get(pk=line.item_id)
            if item.status in ON_HAND and item.branch_id == branch.pk:
                change_item_status(item.pk, to=ItemStatus.MISSING, allowed_from=ON_HAND,
                                   branch=branch,
                                   movement_type=MovementType.STOCKTAKE_ADJUST,
                                   business_date=today, doc=doc)
                line.result, line.adjusted = StocktakeResult.MISSING, True
                lost.append(StockPart(item.category, item.karat, item.fine_weight_g,
                                      item.cost_amount, item.stone_cost_amount))
            else:  # sold or sent away while counting
                line.result = StocktakeResult.LEFT
            line.save(update_fields=["result", "adjusted", "updated_at"])

        unexpected = stocktake.lines.filter(result=StocktakeResult.UNEXPECTED,
                                            item__status=ItemStatus.MISSING).order_by("item_id")
        for line in unexpected:
            item = change_item_status(line.item_id, to=ItemStatus.IN_STOCK,
                                      allowed_from=(ItemStatus.MISSING,), move_to=branch,
                                      movement_type=MovementType.STOCKTAKE_ADJUST,
                                      business_date=today, doc=doc, stock_delta=1)
            line.adjusted = True
            line.save(update_fields=["adjusted", "updated_at"])
            item = Item.objects.select_related("category", "karat__metal").get(pk=item.pk)
            found.append(StockPart(item.category, item.karat, item.fine_weight_g,
                                   item.cost_amount, item.stone_cost_amount))

        for line in stocktake.lot_lines.filter(counted_gross_weight_g__isnull=False).select_related(
                "lot__category", "lot__karat__metal").order_by("lot_id"):
            balance = LotBalance.objects.get(lot=line.lot)  # current, not the snapshot
            delta = line.counted_gross_weight_g - balance.gross_weight_g
            delta_qty = (line.counted_qty or 0) - balance.qty
            if not delta and not delta_qty:
                continue
            cost = (quantize(balance.cost_amount * delta / balance.gross_weight_g, MONEY)
                    if balance.gross_weight_g else ZERO)
            movement = move_lot(line.lot, qty=delta_qty, gross_weight_g=delta, cost_amount=cost,
                                movement_type=MovementType.STOCKTAKE_ADJUST,
                                business_date=today, doc=doc)
            line.adjustment_g = delta
            line.save(update_fields=["adjustment_g", "updated_at"])
            part = StockPart(line.lot.category, line.lot.karat, abs(movement.fine_weight_g),
                             abs(cost))
            (found if delta > 0 else lost).append(part)

        ledger = (stock_lines(lost, sign=-1, counter_role="metal_loss", branch=branch)
                  + stock_lines(found, sign=1, counter_role="metal_gain", branch=branch))
        if ledger:
            stocktake.journal_entry = post_entry(
                branch=branch, business_date=today, lines=ledger, kind=EntryKind.AUTO,
                source_type=DOC_TYPE, source_id=stocktake.pk,
                memo=_("Stocktake %(number)s") % {"number": stocktake.number})
        stocktake.status = DocStatus.POSTED
        stocktake.posted_at = timezone.now()
        stocktake.posted_by = getattr(actor, "user", None)
        stocktake.save()
    return stocktake


def cancel_stocktake(stocktake_id: int, *, reason: str = "", actor=None) -> Stocktake:
    with transaction.atomic():
        stocktake = _open(stocktake_id, lock=True)
        if actor is not None:
            actor.require("inventory.stocktake.start", branch=stocktake.branch)
        stocktake.status = DocStatus.VOIDED
        stocktake.voided_at = timezone.now()
        stocktake.voided_by = getattr(actor, "user", None)
        stocktake.void_reason = reason.strip()[:300]
        stocktake.save()
    return stocktake
