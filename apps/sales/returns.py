"""Customer returns against a posted sale (§7.7).

Posting a return, in one transaction:
* the returned pieces go back into stock at the sale's branch;
* revenue and cost of goods for those pieces are reversed;
* the customer is refunded in cash, or credited on their account (credit sales, deposits);
* an optional deduction (e.g. part of the making charge) stays as income.
A piece can be returned once; cancelling the return makes it returnable again.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.errors import DomainError, NotFound, ValidationError
from apps.core.models import DocStatus
from apps.core.numeric import round_money, to_decimal
from apps.core.sequences import allocate_number
from apps.inventory.models import ItemStatus, MovementType
from apps.inventory.services import DocRef, change_item_status, move_lot
from apps.ledger.models import EntryKind
from apps.ledger.services import LineInput as LedgerLine
from apps.ledger.services import (
    account_for,
    functional_commodity,
    metal_commodity,
    post_entry,
    reverse_entry,
)
from apps.treasury.holders import TenderKind, ensure_cash_available, resolve_tender

from .models import RefundMethod, SalesInvoice, SalesReturn, SalesReturnLine

DOC_TYPE = "sales.SalesReturn"
ZERO = Decimal(0)


def returned_line_ids(invoice: SalesInvoice) -> set[int]:
    """Lines of `invoice` already taken back by a return that is not cancelled."""
    return set(SalesReturnLine.objects.filter(
        original_line__invoice=invoice, sales_return__status=DocStatus.POSTED,
    ).values_list("original_line_id", flat=True))


def _ledger_lines(sales_return: SalesReturn, lines) -> list[LedgerLine]:
    home = functional_commodity()
    result: list[LedgerLine] = []

    def add(role, commodity, quantity, functional=None, party=None):
        if quantity:
            result.append(LedgerLine(account=account_for(role), commodity=commodity,
                                     quantity=quantity, functional_amount=functional,
                                     party=party))

    add("sales_gold", home, sum((line.metal_amount for line in lines), ZERO))
    add("sales_making", home, sum((line.making_amount for line in lines), ZERO))
    add("sales_diamonds", home, sum((line.stones_amount for line in lines), ZERO))
    add("sales_making", home, -sales_return.deduction_amount)
    if sales_return.refund_method == RefundMethod.CASH:
        if sales_return.refund_amount:
            result.append(LedgerLine(account=sales_return.cash_box.account, commodity=home,
                                     quantity=-sales_return.refund_amount))
    else:
        add("customers", home, -sales_return.refund_amount, party=sales_return.customer)

    back: dict[str, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    for line in lines:
        if line.karat is None:  # a loose stone
            continue
        back[line.karat.metal.code][0] += line.fine_weight_g
        back[line.karat.metal.code][1] += line.metal_value
    for metal_code, (fine, value) in sorted(back.items()):
        commodity = metal_commodity(metal_code)
        add("inventory_gold", commodity, fine, value)
        add("cogs_gold", commodity, -fine, -value)
    cost = sum((line.cost_amount for line in lines), ZERO)
    add("inventory_gold", home, cost)
    add("cogs_gold", home, -cost)
    stones = sum((line.stone_cost_amount for line in lines), ZERO)
    add("inventory_diamonds", home, stones)
    add("cogs_diamonds", home, -stones)
    return result


def create_return(invoice_id: int, line_ids: list[int], *, refund_method: str,
                  deduction_amount="0", cash_box_id: int | None = None, reason: str = "",
                  actor=None) -> SalesReturn:
    with transaction.atomic():
        invoice = (SalesInvoice.objects.select_for_update(of=("self",))
                   .select_related("branch", "customer")
                   .filter(pk=invoice_id).first())
        if invoice is None:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("sales.return.create", branch=invoice.branch)
        if invoice.status != DocStatus.POSTED:
            raise DomainError(_("Only posted sales can take returns."), code="DOC_NOT_POSTED")
        if refund_method not in RefundMethod.values:
            raise ValidationError(_("Choose how to refund."),
                                  fields={"refund_method": [_("Required.")]})
        if refund_method == RefundMethod.CUSTOMER_CREDIT and invoice.customer_id is None:
            raise DomainError(_("A walk-in sale can only be refunded in cash."),
                              code="SALES_RETURN_NEEDS_CUSTOMER")

        wanted = set(line_ids)
        lines = list(invoice.lines.select_related("karat__metal", "item", "lot__karat")
                     .filter(pk__in=wanted).order_by("item_id", "lot_id"))
        if not lines or len(lines) != len(wanted):
            raise ValidationError(_("Choose the pieces being returned."),
                                  fields={"lines": [_("Choose at least one piece.")]})
        already = returned_line_ids(invoice) & wanted
        if already:
            raise DomainError(_("Some of these pieces were already returned."),
                              code="SALES_ALREADY_RETURNED")

        returned = sum((line.line_total for line in lines), ZERO)
        deduction = round_money(to_decimal(deduction_amount or "0"))
        if deduction < 0 or deduction > returned:
            raise ValidationError(_("The deduction must be between zero and the returned value."),
                                  fields={"deduction_amount": [_("Out of range.")]})

        cash_box = None
        if refund_method == RefundMethod.CASH:
            home = functional_commodity().currency
            cash_box = resolve_tender(TenderKind.CASH, invoice.branch, home,
                                      cash_box_id=cash_box_id).cash_box
            ensure_cash_available(cash_box, returned - deduction, "cash_box")

        today = timezone.localdate()
        sales_return = SalesReturn.objects.create(cash_box=cash_box,
            branch=invoice.branch, business_date=today, original_invoice=invoice,
            customer=invoice.customer, refund_method=refund_method, returned_amount=returned,
            deduction_amount=deduction, refund_amount=returned - deduction,
            reason=reason.strip()[:300], created_by=getattr(actor, "user", None),
        )
        doc = DocRef(DOC_TYPE, sales_return.pk)
        for line in lines:
            if line.lot_id:  # bulk gold goes back into its lot
                move_lot(line.lot, qty=line.qty, gross_weight_g=line.gross_weight_g,
                         cost_amount=line.cost_amount, movement_type=MovementType.SALE_RETURN,
                         business_date=today, doc=doc)
                continue
            change_item_status(line.item_id, to=ItemStatus.IN_STOCK,
                               allowed_from=(ItemStatus.SOLD,),
                               movement_type=MovementType.SALE_RETURN, business_date=today,
                               doc=doc, branch=invoice.branch, stock_delta=1)
        SalesReturnLine.objects.bulk_create(
            [SalesReturnLine(sales_return=sales_return, original_line=line) for line in lines])

        sales_return.number = allocate_number("SR", branch=invoice.branch,
                                              fiscal_year=today.year)
        sales_return.journal_entry = post_entry(
            branch=invoice.branch, business_date=today,
            lines=_ledger_lines(sales_return, lines), kind=EntryKind.AUTO,
            source_type=DOC_TYPE, source_id=sales_return.pk,
            memo=_("Return %(number)s of sale %(sale)s")
            % {"number": sales_return.number, "sale": invoice.number},
        )
        sales_return.status = DocStatus.POSTED
        sales_return.posted_at = timezone.now()
        sales_return.posted_by = getattr(actor, "user", None)
        sales_return.save()
    return sales_return


def void_return(return_id: int, *, reason: str = "", actor=None) -> SalesReturn:
    """Undo a return: the pieces count as sold again and the refund entry is reversed."""
    with transaction.atomic():
        sales_return = (SalesReturn.objects.select_for_update().select_related("branch")
                        .filter(pk=return_id).first())
        if sales_return is None:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("sales.return.void", branch=sales_return.branch)
        if sales_return.status != DocStatus.POSTED:
            raise DomainError(_("Only posted returns can be cancelled."), code="DOC_NOT_POSTED")
        today = timezone.localdate()
        doc = DocRef(DOC_TYPE, sales_return.pk)
        for line in sales_return.lines.select_related("original_line__lot__karat").order_by(
                "original_line__item_id", "original_line__lot_id"):
            sold = line.original_line
            if sold.lot_id:
                move_lot(sold.lot, qty=-sold.qty, gross_weight_g=-sold.gross_weight_g,
                         cost_amount=-sold.cost_amount, movement_type=MovementType.SALE,
                         business_date=today, doc=doc)
                continue
            change_item_status(line.original_line.item_id, to=ItemStatus.SOLD,
                               allowed_from=(ItemStatus.IN_STOCK,),
                               movement_type=MovementType.SALE, business_date=today, doc=doc,
                               branch=sales_return.branch)
        if sales_return.journal_entry_id:
            reverse_entry(sales_return.journal_entry_id, business_date=today,
                          memo=_("Cancelled return %(number)s") % {"number": sales_return.number})
        sales_return.status = DocStatus.VOIDED
        sales_return.voided_at = timezone.now()
        sales_return.voided_by = getattr(actor, "user", None)
        sales_return.void_reason = reason.strip()[:300]
        sales_return.save()
    return sales_return
