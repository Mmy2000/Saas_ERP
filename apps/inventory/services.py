"""Stock changes (§7.5, §16.1). Documents call these inside their posting transaction; nothing
else writes items, lots or movements. Items are locked (SELECT … FOR UPDATE) before a status
change, lot balances are updated with row-locking UPDATEs, in a stable order."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Sum
from django.utils.translation import gettext as _

from apps.catalog.domain.metal import fine_weight
from apps.catalog.models import ProductFamily
from apps.core.errors import DomainError
from apps.core.numeric import GRAMS_PER_CARAT, MONEY, WEIGHT, quantize, to_decimal
from apps.core.sequences import allocate_value

from .models import Item, ItemStatus, ItemType, LotBalance, MovementType, StockLot, StockMovement

ZERO = Decimal(0)

FAMILY_TO_ITEM_TYPE = {
    ProductFamily.GOLD: ItemType.GOLD_PIECE,
    ProductFamily.SILVER: ItemType.SILVER_PIECE,
    ProductFamily.DIAMOND: ItemType.DIAMOND_PIECE,
    ProductFamily.STONE: ItemType.STONE,
    ProductFamily.BULLION_COIN: ItemType.BULLION,
}


@dataclass(frozen=True)
class DocRef:
    """Which document caused a movement, e.g. DocRef("purchasing.SupplierInvoice", 12)."""

    type: str
    id: int | None


def next_barcode(category) -> str:
    """Legacy-compatible `prefix × 10⁶ + n` when the category has a barcode prefix; otherwise a
    tenant-wide running number starting with 9."""
    if category.barcode_prefix:
        value = allocate_value(f"barcode.{category.pk}")
        return str(category.barcode_prefix * 10**6 + value)
    return f"9{allocate_value('barcode'):08d}"


def _fine(weight: Decimal, karat) -> Decimal:
    return fine_weight(weight, karat.fineness) if karat is not None else ZERO


def create_item(*, category, karat, gross_weight_g, branch, business_date: date, doc: DocRef,
                supplier=None, stone_weight_ct="0", cost_currency=None, cost_making_rate="0",
                cost_amount="0", list_making_rate="0", external_code: str = "",
                movement_type: str = MovementType.PURCHASE_RECEIPT, actor=None,
                stone_cost_amount="0", label_price=None) -> Item:
    gross = quantize(gross_weight_g, WEIGHT)
    if gross <= 0:
        raise DomainError(_("A piece must weigh more than zero."), code="INVENTORY_ZERO_WEIGHT")
    stones_g = to_decimal(stone_weight_ct) * GRAMS_PER_CARAT
    metal = quantize(max(gross - stones_g, ZERO), WEIGHT)
    fine = _fine(metal, karat)
    cost = quantize(cost_amount, MONEY)
    item = Item.objects.create(
        barcode=next_barcode(category), item_type=FAMILY_TO_ITEM_TYPE.get(
            category.product_family, ItemType.OTHER),
        category=category, karat=karat, gross_weight_g=gross, metal_weight_g=metal,
        fine_weight_g=fine, stone_weight_ct=to_decimal(stone_weight_ct), supplier=supplier,
        status=ItemStatus.IN_STOCK, branch=branch, cost_currency=cost_currency,
        cost_making_rate=to_decimal(cost_making_rate), cost_amount=cost,
        list_making_rate=to_decimal(list_making_rate), external_code=external_code,
        stone_cost_amount=quantize(stone_cost_amount or "0", MONEY),
        label_price=quantize(label_price, MONEY) if label_price not in (None, "") else None,
        created_by=getattr(actor, "user", None),
    )
    StockMovement.objects.create(
        branch=branch, item=item, movement_type=movement_type, qty=1, gross_weight_g=gross,
        fine_weight_g=fine, cost_amount=cost, document_type=doc.type, document_id=doc.id,
        business_date=business_date,
    )
    return item


def change_item_status(item_id: int, *, to: str, allowed_from: tuple[str, ...],
                       movement_type: str, business_date: date, doc: DocRef, branch=None,
                       stock_delta: int = -1, move_to=None) -> Item:
    """Move one piece between states (sale, return, transfer…), recording the movement.
    `branch` pins where the piece must currently be; `stock_delta` is -1 when it leaves stock,
    +1 when it comes back; `move_to` relocates it (transfers). The movement is recorded at the
    branch the piece leaves (out) or arrives at (in)."""
    item = Item.objects.select_for_update().get(pk=item_id)
    if item.status not in allowed_from or (branch is not None and item.branch_id != branch.pk):
        raise DomainError(
            _("Piece %(barcode)s is not available (%(status)s).")
            % {"barcode": item.barcode, "status": item.get_status_display()},
            code="INVENTORY_ITEM_UNAVAILABLE")
    left_from = item.branch
    item.status = to
    if move_to is not None:
        item.branch = move_to
    item.save(update_fields=["status", "branch", "updated_at"])
    sign = 1 if stock_delta > 0 else -1
    StockMovement.objects.create(
        branch=item.branch if stock_delta > 0 else left_from, item=item,
        movement_type=movement_type, qty=stock_delta,
        gross_weight_g=sign * item.gross_weight_g, fine_weight_g=sign * item.fine_weight_g,
        cost_amount=sign * item.cost_amount, document_type=doc.type, document_id=doc.id,
        business_date=business_date,
    )
    return item


def set_reserved(item_id: int, reserved: bool, *, branch) -> Item:
    """Hold a piece for a customer, or release it. Not a stock movement: it stays on hand."""
    item = Item.objects.select_for_update().get(pk=item_id)
    before, after = ((ItemStatus.IN_STOCK, ItemStatus.RESERVED) if reserved
                     else (ItemStatus.RESERVED, ItemStatus.IN_STOCK))
    if item.status != before or item.branch_id != branch.pk:
        raise DomainError(
            _("Piece %(barcode)s is not available (%(status)s).")
            % {"barcode": item.barcode, "status": item.get_status_display()},
            code="INVENTORY_ITEM_UNAVAILABLE")
    item.status = after
    item.save(update_fields=["status", "updated_at"])
    return item


SCRAP_CATEGORY_CODE = "SCRAP"


def scrap_category():
    """The bulk category that holds scrap gold received (trade-ins, settlements), per karat."""
    from apps.catalog.models import ItemCategory, Tracking

    category, _created = ItemCategory.objects.get_or_create(
        code=SCRAP_CATEGORY_CODE,
        defaults={"name": _("Scrap gold"), "product_family": ProductFamily.GOLD,
                  "tracking": Tracking.BULK},
    )
    return category


def lot_for(category, karat, branch) -> StockLot:
    lot, _created = StockLot.objects.get_or_create(category=category, karat=karat, branch=branch)
    LotBalance.objects.get_or_create(lot=lot)
    return lot


def move_lot(lot: StockLot, *, qty: int, gross_weight_g, cost_amount="0",
             movement_type: str, business_date: date, doc: DocRef) -> StockMovement:
    """Add (positive) or take (negative) bulk stock. Refuses to go below zero."""
    gross = quantize(gross_weight_g, WEIGHT)
    fine = fine_weight(abs(gross), lot.karat.fineness) if lot.karat else ZERO
    fine = fine if gross >= 0 else -fine
    cost = quantize(cost_amount, MONEY)
    balance = LotBalance.objects.select_for_update().get(lot=lot)
    if balance.gross_weight_g + gross < 0 or balance.qty + qty < 0:
        raise DomainError(
            _("Not enough stock: %(available)s g available.")
            % {"available": balance.gross_weight_g}, code="INVENTORY_INSUFFICIENT_STOCK")
    LotBalance.objects.filter(pk=balance.pk).update(
        qty=F("qty") + qty, gross_weight_g=F("gross_weight_g") + gross,
        fine_weight_g=F("fine_weight_g") + fine, cost_amount=F("cost_amount") + cost)
    return StockMovement.objects.create(
        branch=lot.branch, lot=lot, movement_type=movement_type, qty=qty, gross_weight_g=gross,
        fine_weight_g=fine, cost_amount=cost, document_type=doc.type, document_id=doc.id,
        business_date=business_date,
    )


def rebuild_lot_balances() -> int:
    """Recompute every lot balance of the current tenant from its movements."""
    with transaction.atomic():
        totals = {row["lot"]: row for row in StockMovement.objects.filter(lot__isnull=False)
                  .values("lot").annotate(q=Sum("qty"), g=Sum("gross_weight_g"),
                                          f=Sum("fine_weight_g"), c=Sum("cost_amount"))}
        count = 0
        for balance in LotBalance.objects.select_for_update():
            row = totals.get(balance.lot_id, {})
            balance.qty = row.get("q") or 0
            balance.gross_weight_g = row.get("g") or ZERO
            balance.fine_weight_g = row.get("f") or ZERO
            balance.cost_amount = row.get("c") or ZERO
            balance.save(update_fields=["qty", "gross_weight_g", "fine_weight_g", "cost_amount",
                                        "updated_at"])
            count += 1
        return count
