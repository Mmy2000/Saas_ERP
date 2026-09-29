"""Goods moving between branches (§7.5, §15), in two steps like cash (apps.treasury):

send     pieces: in_stock at the source → in_transit, located at the destination
         bulk:   weight leaves the source lot
         ledger: inventory (source branch) → branch clearing (source branch)
receive  pieces: in_transit → in_stock; bulk: weight enters the destination lot
         ledger: branch clearing (destination) → inventory (destination)
cancel   (only while in transit) everything goes back to the source; the send entry is reversed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.models import ItemCategory, Karat
from apps.core.errors import DomainError, NotFound, ValidationError
from apps.core.models import DocStatus
from apps.core.numeric import MONEY, WEIGHT, quantize
from apps.core.sequences import allocate_number
from apps.ledger.models import EntryKind
from apps.ledger.services import post_entry, reverse_entry
from apps.org.models import Branch

from .models import (
    Item,
    ItemStatus,
    LotBalance,
    MovementType,
    StockLot,
    StockTransfer,
    StockTransferLine,
)
from .services import DocRef, change_item_status, lot_for, move_lot
from .valuation import StockPart, metal_value, stock_lines

DOC_TYPE = "inventory.StockTransfer"
ZERO = Decimal(0)


@dataclass(frozen=True)
class TransferLineInput:
    """A piece (barcode or item id) or bulk stock (category, karat, weight, pieces)."""

    barcode: str = ""
    item_id: int | None = None
    category_id: int | None = None
    karat_id: int | None = None
    gross_weight_g: Decimal | str | None = None
    qty: int = 0


@dataclass(frozen=True)
class TransferInput:
    from_branch_id: int
    to_branch_id: int
    lines: tuple[TransferLineInput, ...]
    note: str = ""


def _line_error(index: int, message: str, code: str = "") -> ValidationError:
    return ValidationError(message, code=code or "INVENTORY_TRANSFER_LINE",
                           fields={"lines": {str(index): [message]}})


def _branch(pk, field: str) -> Branch:
    branch = Branch.objects.filter(pk=pk, is_active=True).first() if pk else None
    if branch is None:
        raise ValidationError(_("Unknown branch."), fields={field: [_("Unknown branch.")]})
    return branch


def _piece(index: int, line: TransferLineInput, source: Branch) -> Item:
    items = Item.objects.select_related("category", "karat__metal")
    item = (items.filter(pk=line.item_id).first() if line.item_id
            else items.filter(barcode=line.barcode.strip()).first())
    if item is None:
        raise _line_error(index, _("No piece with barcode %(barcode)s.")
                          % {"barcode": line.barcode}, "INVENTORY_ITEM_NOT_FOUND")
    if item.status != ItemStatus.IN_STOCK or item.branch_id != source.pk:
        raise _line_error(index, _("Piece %(barcode)s is not in stock at %(branch)s.")
                          % {"barcode": item.barcode, "branch": source.name},
                          "INVENTORY_ITEM_UNAVAILABLE")
    return item


def _bulk(index: int, line: TransferLineInput, source: Branch):
    category = ItemCategory.objects.filter(pk=line.category_id).first()
    karat = Karat.objects.select_related("metal").filter(pk=line.karat_id).first() \
        if line.karat_id else None
    lot = StockLot.objects.filter(category=category, karat=karat, branch=source).first() \
        if category else None
    balance = LotBalance.objects.filter(lot=lot).first() if lot else None
    if balance is None or balance.gross_weight_g <= 0:
        raise _line_error(index, _("No bulk stock of this kind at %(branch)s.")
                          % {"branch": source.name}, "INVENTORY_INSUFFICIENT_STOCK")
    try:
        gross = quantize(line.gross_weight_g, WEIGHT)
    except (TypeError, ValueError):
        raise _line_error(index, _("Enter a number.")) from None
    if gross <= 0:
        raise _line_error(index, _("Must be greater than zero."))
    if gross > balance.gross_weight_g or line.qty > balance.qty or line.qty < 0:
        raise _line_error(index, _("Only %(weight)s g (%(qty)s pieces) at %(branch)s.")
                          % {"weight": balance.gross_weight_g, "qty": balance.qty,
                             "branch": source.name}, "INVENTORY_INSUFFICIENT_STOCK")
    cost = quantize(balance.cost_amount * gross / balance.gross_weight_g, MONEY)
    return lot, gross, cost


def send_transfer(data: TransferInput, *, actor=None) -> StockTransfer:
    with transaction.atomic():
        source = _branch(data.from_branch_id, "from_branch")
        if not data.to_branch_id:
            raise ValidationError(_("Choose the branch receiving the goods."),
                                  fields={"to_branch": [_("Required.")]})
        destination = _branch(data.to_branch_id, "to_branch")
        if actor is not None:
            actor.require("inventory.transfer.send", branch=source)
        if source.pk == destination.pk:
            raise ValidationError(_("Choose another branch."),
                                  fields={"to_branch": [_("Choose another branch.")]})
        if not data.lines:
            raise ValidationError(_("Add at least one piece or weight."),
                                  code="INVENTORY_TRANSFER_EMPTY",
                                  fields={"lines": [_("Add at least one piece or weight.")]})

        today = timezone.localdate()
        transfer = StockTransfer.objects.create(
            branch=source, to_branch=destination, business_date=today, note=data.note.strip(),
            created_by=getattr(actor, "user", None))
        doc = DocRef(DOC_TYPE, transfer.pk)

        pieces, seen, lines = [], set(), []
        for index, line in enumerate(data.lines):
            if line.barcode or line.item_id:
                item = _piece(index, line, source)
                if item.pk in seen:
                    raise _line_error(index, _("Piece %(barcode)s is already on this transfer.")
                                      % {"barcode": item.barcode}, "INVENTORY_DUPLICATE_ITEM")
                seen.add(item.pk)
                pieces.append(item)
            else:
                lot, gross, cost = _bulk(index, line, source)
                movement = move_lot(lot, qty=-line.qty, gross_weight_g=-gross, cost_amount=-cost,
                                    movement_type=MovementType.TRANSFER_OUT,
                                    business_date=today, doc=doc)
                fine = -movement.fine_weight_g
                lines.append(StockTransferLine(
                    transfer=transfer, category=lot.category, karat=lot.karat, qty=line.qty,
                    gross_weight_g=gross, fine_weight_g=fine, cost_amount=cost,
                    metal_value=metal_value(lot.karat, fine)))
        for item in sorted(pieces, key=lambda i: i.pk):  # stable lock order (§16.2)
            change_item_status(item.pk, to=ItemStatus.IN_TRANSIT,
                               allowed_from=(ItemStatus.IN_STOCK,), branch=source,
                               move_to=destination, movement_type=MovementType.TRANSFER_OUT,
                               business_date=today, doc=doc)
            lines.append(StockTransferLine(
                transfer=transfer, item=item, category=item.category, karat=item.karat, qty=1,
                gross_weight_g=item.gross_weight_g, fine_weight_g=item.fine_weight_g,
                cost_amount=item.cost_amount,
                metal_value=metal_value(item.karat, item.fine_weight_g)))
        StockTransferLine.objects.bulk_create(lines)

        transfer.total_qty = sum(line.qty for line in lines)
        transfer.total_gross_weight_g = sum((line.gross_weight_g for line in lines), ZERO)
        transfer.total_fine_weight_g = sum((line.fine_weight_g for line in lines), ZERO)
        transfer.number = allocate_number("TF", branch=source, fiscal_year=today.year)
        ledger = stock_lines(_parts(lines), sign=-1, counter_role="branch_clearing",
                             branch=source, values=[line.metal_value for line in lines])
        if ledger:
            transfer.journal_entry = post_entry(
                branch=source, business_date=today, lines=ledger, kind=EntryKind.AUTO,
                source_type=DOC_TYPE, source_id=transfer.pk,
                memo=_("Transfer %(number)s to %(branch)s")
                % {"number": transfer.number, "branch": destination.name})
        transfer.status = DocStatus.POSTED
        transfer.posted_at = timezone.now()
        transfer.posted_by = getattr(actor, "user", None)
        transfer.save()
    return transfer


def _parts(lines) -> list[StockPart]:
    return [StockPart(line.category, line.karat, line.fine_weight_g, line.cost_amount)
            for line in lines]


def _locked(transfer_id: int) -> StockTransfer:
    transfer = (StockTransfer.objects.select_for_update(of=("self",))
                .select_related("branch", "to_branch").filter(pk=transfer_id).first())
    if transfer is None:
        raise NotFound(_("Not found."))
    if not transfer.in_transit:
        raise DomainError(_("This transfer is not in transit."), code="INVENTORY_NOT_IN_TRANSIT")
    return transfer


def _lines(transfer):
    return list(transfer.lines.select_related("item", "category", "karat__metal").order_by(
        "item_id", "id"))


def receive_transfer(transfer_id: int, *, actor=None) -> StockTransfer:
    with transaction.atomic():
        transfer = _locked(transfer_id)
        destination = transfer.to_branch
        if actor is not None:
            actor.require("inventory.transfer.receive", branch=destination)
        today = timezone.localdate()
        doc = DocRef(DOC_TYPE, transfer.pk)
        lines = _lines(transfer)
        for line in lines:
            if line.item_id:
                change_item_status(line.item_id, to=ItemStatus.IN_STOCK,
                                   allowed_from=(ItemStatus.IN_TRANSIT,), branch=destination,
                                   movement_type=MovementType.TRANSFER_IN, business_date=today,
                                   doc=doc, stock_delta=1)
            else:
                move_lot(lot_for(line.category, line.karat, destination), qty=line.qty,
                         gross_weight_g=line.gross_weight_g, cost_amount=line.cost_amount,
                         movement_type=MovementType.TRANSFER_IN, business_date=today, doc=doc)
        ledger = stock_lines(_parts(lines), sign=1, counter_role="branch_clearing",
                             branch=destination, values=[line.metal_value for line in lines])
        if ledger:
            transfer.receive_entry = post_entry(
                branch=destination, business_date=today, lines=ledger, kind=EntryKind.AUTO,
                source_type=DOC_TYPE, source_id=transfer.pk,
                memo=_("Received %(number)s") % {"number": transfer.number})
        transfer.received_at = timezone.now()
        transfer.received_by = getattr(actor, "user", None)
        transfer.save()
    return transfer


def void_transfer(transfer_id: int, *, reason: str = "", actor=None) -> StockTransfer:
    """Call back goods that have not arrived yet."""
    with transaction.atomic():
        transfer = _locked(transfer_id)
        source = transfer.branch
        if actor is not None:
            actor.require("inventory.transfer.void", branch=source)
        today = timezone.localdate()
        doc = DocRef(DOC_TYPE, transfer.pk)
        for line in _lines(transfer):
            if line.item_id:
                change_item_status(line.item_id, to=ItemStatus.IN_STOCK,
                                   allowed_from=(ItemStatus.IN_TRANSIT,),
                                   branch=transfer.to_branch, move_to=source,
                                   movement_type=MovementType.TRANSFER_IN, business_date=today,
                                   doc=doc, stock_delta=1)
            else:
                move_lot(lot_for(line.category, line.karat, source), qty=line.qty,
                         gross_weight_g=line.gross_weight_g, cost_amount=line.cost_amount,
                         movement_type=MovementType.TRANSFER_IN, business_date=today, doc=doc)
        if transfer.journal_entry_id:
            reverse_entry(transfer.journal_entry_id, business_date=today,
                          memo=_("Cancelled %(number)s") % {"number": transfer.number})
        transfer.status = DocStatus.VOIDED
        transfer.voided_at = timezone.now()
        transfer.voided_by = getattr(actor, "user", None)
        transfer.void_reason = reason.strip()[:300]
        transfer.save()
    return transfer
