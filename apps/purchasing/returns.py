"""Goods returned to a supplier (§7.8).

post    pieces in stock → returned_to_supplier; bulk weight leaves its lot
        Dr the supplier (fine gold at today's value, and the making cost paid)
        Cr gold inventory
cancel  (while the pieces are still marked returned) everything back; the entry is reversed.
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
from apps.inventory.models import Item, ItemStatus, LotBalance, MovementType, StockLot
from apps.inventory.services import SCRAP_CATEGORY_CODE, DocRef, change_item_status, move_lot
from apps.inventory.valuation import StockPart, metal_value, stock_lines
from apps.ledger.models import EntryKind
from apps.ledger.services import post_entry, reverse_entry
from apps.org.models import Branch
from apps.parties.models import Party

from .models import SELLER_ACCOUNT, SellerRole, SupplierReturn, SupplierReturnLine

DOC_TYPE = "purchasing.SupplierReturn"
ZERO = Decimal(0)


@dataclass(frozen=True)
class ReturnLineInput:
    """A piece (barcode or item id) or bulk gold (category, karat, weight, pieces)."""

    barcode: str = ""
    item_id: int | None = None
    category_id: int | None = None
    karat_id: int | None = None
    gross_weight_g: Decimal | str | None = None
    qty: int = 0


@dataclass(frozen=True)
class SupplierReturnInput:
    branch_id: int
    supplier_id: int | None
    lines: tuple[ReturnLineInput, ...]
    note: str = ""
    seller_role: str = SellerRole.SUPPLIER


def _line_error(index: int, message: str) -> ValidationError:
    return ValidationError(message, fields={"lines": {str(index): [message]}})


def _piece(index, line, branch) -> Item:
    items = Item.objects.select_related("category", "karat__metal")
    item = (items.filter(pk=line.item_id).first() if line.item_id
            else items.filter(barcode=line.barcode.strip()).first())
    if item is None:
        raise _line_error(index, _("No piece with barcode %(barcode)s.")
                          % {"barcode": line.barcode})
    if item.status != ItemStatus.IN_STOCK or item.branch_id != branch.pk:
        raise _line_error(index, _("Piece %(barcode)s is not in stock at %(branch)s.")
                          % {"barcode": item.barcode, "branch": branch.name})
    return item


def _bulk(index, line, branch):
    category = ItemCategory.objects.filter(pk=line.category_id).exclude(
        code=SCRAP_CATEGORY_CODE).first()
    karat = Karat.objects.select_related("metal").filter(pk=line.karat_id).first()
    lot = (StockLot.objects.select_related("category", "karat__metal")
           .filter(category=category, karat=karat, branch=branch).first() if category else None)
    balance = LotBalance.objects.filter(lot=lot).first() if lot else None
    try:
        gross = quantize(line.gross_weight_g, WEIGHT)
    except (TypeError, ValueError):
        raise _line_error(index, _("Enter a number.")) from None
    if balance is None or gross <= 0 or gross > balance.gross_weight_g or line.qty > balance.qty:
        available = balance.gross_weight_g if balance else ZERO
        raise _line_error(index, _("Only %(weight)s g of this at %(branch)s.")
                          % {"weight": available, "branch": branch.name})
    cost = quantize(balance.cost_amount * gross / balance.gross_weight_g, MONEY)
    return lot, gross, cost


def post_supplier_return(data: SupplierReturnInput, *, actor=None) -> SupplierReturn:
    with transaction.atomic():
        branch = Branch.objects.filter(pk=data.branch_id, is_active=True).first()
        if branch is None:
            raise ValidationError(_("Unknown branch."), fields={"branch": [_("Unknown branch.")]})
        if actor is not None:
            actor.require("purchasing.return.create", branch=branch)
        role = data.seller_role if data.seller_role in SellerRole.values else None
        supplier = Party.objects.filter(pk=data.supplier_id, is_active=True,
                                        roles__role=role).first() \
            if data.supplier_id and role else None
        if supplier is None:
            message = (_("Choose the trade account.") if role == SellerRole.TRADE_ACCOUNT
                       else _("Choose the supplier."))
            raise ValidationError(message, fields={"supplier": [_("Required.")]})
        if not data.lines:
            raise ValidationError(_("Add at least one piece or weight."),
                                  fields={"lines": [_("Add at least one piece or weight.")]})

        today = timezone.localdate()
        doc = SupplierReturn.objects.create(branch=branch, supplier=supplier, seller_role=role,
                                            business_date=today, note=data.note.strip(),
                                            created_by=getattr(actor, "user", None))
        ref = DocRef(DOC_TYPE, doc.pk)
        pieces, seen, lines = [], set(), []
        for index, line in enumerate(data.lines):
            if line.barcode or line.item_id:
                item = _piece(index, line, branch)
                if item.pk in seen:
                    raise _line_error(index, _("Piece %(barcode)s is already on this return.")
                                      % {"barcode": item.barcode})
                seen.add(item.pk)
                pieces.append(item)
                continue
            lot, gross, cost = _bulk(index, line, branch)
            movement = move_lot(lot, qty=-line.qty, gross_weight_g=-gross, cost_amount=-cost,
                                movement_type=MovementType.SUPPLIER_RETURN, business_date=today,
                                doc=ref)
            fine = -movement.fine_weight_g
            lines.append(SupplierReturnLine(
                supplier_return=doc, lot=lot, category=lot.category, karat=lot.karat,
                qty=line.qty, gross_weight_g=gross, fine_weight_g=fine, cost_amount=cost,
                metal_value=metal_value(lot.karat, fine)))
        for item in sorted(pieces, key=lambda i: i.pk):
            change_item_status(item.pk, to=ItemStatus.RETURNED_TO_SUPPLIER,
                               allowed_from=(ItemStatus.IN_STOCK,), branch=branch,
                               movement_type=MovementType.SUPPLIER_RETURN, business_date=today,
                               doc=ref)
            lines.append(SupplierReturnLine(
                supplier_return=doc, item=item, category=item.category, karat=item.karat, qty=1,
                gross_weight_g=item.gross_weight_g, fine_weight_g=item.fine_weight_g,
                cost_amount=item.cost_amount,
                metal_value=metal_value(item.karat, item.fine_weight_g)))
        SupplierReturnLine.objects.bulk_create(lines)

        doc.total_qty = sum(line.qty for line in lines)
        doc.total_gross_weight_g = sum((line.gross_weight_g for line in lines), ZERO)
        doc.total_fine_weight_g = sum((line.fine_weight_g for line in lines), ZERO)
        doc.total_cost = sum((line.cost_amount for line in lines), ZERO)
        doc.number = allocate_number("PR", branch=branch, fiscal_year=today.year)
        ledger = stock_lines(
            [StockPart(line.category, line.karat, line.fine_weight_g, line.cost_amount,
                       line.item.stone_cost_amount if line.item_id else 0)
             for line in lines], sign=-1, counter_role=SELLER_ACCOUNT[role], branch=branch,
            values=[line.metal_value for line in lines], party=supplier)
        if ledger:
            doc.journal_entry = post_entry(
                branch=branch, business_date=today, lines=ledger, kind=EntryKind.AUTO,
                source_type=DOC_TYPE, source_id=doc.pk,
                memo=_("Return to supplier %(number)s") % {"number": doc.number})
        doc.status = DocStatus.POSTED
        doc.posted_at = timezone.now()
        doc.posted_by = getattr(actor, "user", None)
        doc.save()
    return doc


def void_supplier_return(return_id: int, *, reason: str = "", actor=None) -> SupplierReturn:
    with transaction.atomic():
        doc = (SupplierReturn.objects.select_for_update(of=("self",)).select_related("branch")
               .filter(pk=return_id).first())
        if doc is None:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("purchasing.return.void", branch=doc.branch)
        if doc.status != DocStatus.POSTED:
            raise DomainError(_("Only posted documents can be cancelled."), code="DOC_NOT_POSTED")
        today = timezone.localdate()
        ref = DocRef(DOC_TYPE, doc.pk)
        for line in doc.lines.select_related("lot__karat").order_by("item_id", "lot_id"):
            if line.lot_id:
                move_lot(line.lot, qty=line.qty, gross_weight_g=line.gross_weight_g,
                         cost_amount=line.cost_amount,
                         movement_type=MovementType.SUPPLIER_RETURN, business_date=today,
                         doc=ref)
            else:
                change_item_status(line.item_id, to=ItemStatus.IN_STOCK,
                                   allowed_from=(ItemStatus.RETURNED_TO_SUPPLIER,),
                                   movement_type=MovementType.SUPPLIER_RETURN,
                                   business_date=today, doc=ref, stock_delta=1,
                                   move_to=doc.branch)
        if doc.journal_entry_id:
            reverse_entry(doc.journal_entry_id, business_date=today,
                          memo=_("Cancelled %(number)s") % {"number": doc.number})
        doc.status = DocStatus.VOIDED
        doc.voided_at = timezone.now()
        doc.voided_by = getattr(actor, "user", None)
        doc.void_reason = reason.strip()[:300]
        doc.save()
    return doc
