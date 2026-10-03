"""Repairs and custom orders (§7.12).

take in   pieces described and weighed; a bag number per branch; an optional deposit
          Dr box / bank / terminal       Cr customer deposits (customer)
send      to a workshop (no entry; the pieces are the customer's, not stock)
ready     weights out recorded; the final charge; the workshop's labour
          Dr repair costs                Cr workshop
deliver   Dr customer deposits (held) + boxes / banks / terminals + customers (on account)
          Cr repair charges              Cr cash (change)
cancel    the deposit goes back from a box / bank account, or onto the customer's account
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.models import Currency, Karat
from apps.core.errors import DomainError, NotFound, ValidationError
from apps.core.models import DocStatus
from apps.core.numeric import MONEY, WEIGHT, quantize, round_money, to_decimal
from apps.core.sequences import allocate_number, allocate_value
from apps.ledger.models import EntryKind
from apps.ledger.services import LineInput as LedgerLine
from apps.ledger.services import account_for, functional_commodity, money_commodity, post_entry
from apps.org.models import Branch
from apps.parties.models import Party, PartyRoleType
from apps.pricing.selectors import functional_currency
from apps.pricing.selectors import fx_rate as rate_in_force
from apps.sales.services import PaymentInput
from apps.treasury.holders import (
    TenderKind,
    default_cash_box,
    ensure_cash_available,
    resolve_tender,
)

from .models import RepairKind, RepairLine, RepairOrder, RepairPayment, RepairPaymentStage

DOC_TYPE = "repairs.RepairOrder"
ZERO = Decimal(0)
TOLERANCE = Decimal("0.01")


class RefundMethod:
    CASH = "cash"
    BANK_TRANSFER = "bank_transfer"
    CUSTOMER_CREDIT = "customer_credit"
    CHOICES = (CASH, BANK_TRANSFER, CUSTOMER_CREDIT)


@dataclass(frozen=True)
class RepairLineInput:
    description: str
    karat_id: int | None = None
    weight_in_g: Decimal | str = "0"
    charge: Decimal | str = "0"  # agreed price; can be set or changed when ready


@dataclass(frozen=True)
class RepairInput:
    branch_id: int
    lines: tuple[RepairLineInput, ...]
    kind: str = RepairKind.REPAIR
    customer_id: int | None = None
    customer_name: str = ""
    customer_phone: str = ""
    promised_on: date | None = None
    deposit: PaymentInput | None = None
    note: str = ""


@dataclass(frozen=True)
class ReadyLineInput:
    line_id: int
    weight_out_g: Decimal | str
    charge: Decimal | str | None = None  # None = keep the agreed price


def _field(name: str, message: str) -> ValidationError:
    return ValidationError(message, fields={name: [message]})


def _line_error(index: int, name: str, message: str) -> ValidationError:
    return ValidationError(message, fields={"lines": {str(index): {name: [message]}}})


def _amount(value, name: str, index: int | None = None) -> Decimal:
    try:
        number = quantize(value or "0", MONEY)
    except (TypeError, ValueError):
        number = None
    if number is None or number < 0:
        message = _("Enter an amount of zero or more.")
        raise _line_error(index, name, message) if index is not None else _field(name, message)
    return number


def _weight(value, name: str, index: int) -> Decimal:
    try:
        number = quantize(value or "0", WEIGHT)
    except (TypeError, ValueError):
        number = None
    if number is None or number < 0:
        raise _line_error(index, name, _("Enter a weight of zero or more."))
    return number


def _locked(order_id: int) -> RepairOrder:
    order = (RepairOrder.objects.select_for_update(of=("self",))
             .select_related("branch", "customer", "workshop").filter(pk=order_id).first())
    if order is None:
        raise NotFound(_("Not found."))
    return order


def _open(order_id: int) -> RepairOrder:
    order = _locked(order_id)
    if not order.is_open:
        raise DomainError(_("This repair is closed."), code="REPAIR_CLOSED")
    return order


# --- money ---------------------------------------------------------------------------------------

def _tender(order: RepairOrder, payment: PaymentInput, *, stage: str, sign: int = 1,
            actor=None) -> tuple[RepairPayment, object]:
    """A payment row and its holder (box / bank account / terminal); not yet saved."""
    currency = Currency.objects.filter(code=payment.currency_code, is_active=True).first()
    if currency is None:
        raise _field("currency", _("Unknown currency."))
    amount = round_money(to_decimal(payment.amount or "0"))
    if amount <= 0:
        raise _field("amount", _("Must be greater than zero."))
    if payment.kind == TenderKind.CARD and sign < 0:
        raise _field("method", _("Cards can only be used to receive money."))
    holder = resolve_tender(payment.kind, order.branch, currency,
                            cash_box_id=payment.cash_box_id,
                            bank_account_id=payment.bank_account_id,
                            terminal_id=payment.terminal_id)
    if sign < 0 and holder.cash_box is not None:
        ensure_cash_available(holder.cash_box, amount, "amount")
    fx = Decimal(1) if currency.code == functional_currency() else rate_in_force(currency.code)
    return RepairPayment(
        order=order, stage=stage, business_date=timezone.localdate(), kind=payment.kind,
        currency=currency, amount=sign * amount, fx_rate=fx,
        functional_amount=sign * round_money(amount * fx),
        created_by=getattr(actor, "user", None), **holder.fields), holder


def _holder_line(row: RepairPayment, holder) -> LedgerLine:
    money = money_commodity(row.currency)
    return LedgerLine(account=holder.account, commodity=money, quantity=row.amount,
                      functional_amount=None if money.is_functional else row.functional_amount)


def _move_deposit(order: RepairOrder, payment: PaymentInput, *, sign: int,
                  actor=None) -> RepairPayment:
    """Money in as a deposit (sign 1), or a deposit paid back (sign -1)."""
    if order.customer_id is None:
        raise _field("customer", _("Deposits are kept for a registered customer. Choose or add "
                                   "the customer first."))
    stage = RepairPaymentStage.DEPOSIT if sign > 0 else RepairPaymentStage.REFUND
    row, holder = _tender(order, payment, stage=stage, sign=sign, actor=actor)
    row.number = allocate_number("RPD", branch=order.branch, fiscal_year=row.business_date.year)
    row.journal_entry = post_entry(
        branch=order.branch, business_date=row.business_date, kind=EntryKind.AUTO,
        source_type=DOC_TYPE, source_id=order.pk,
        memo=(_("Deposit %(number)s") if sign > 0 else _("Deposit returned %(number)s"))
        % {"number": row.number},
        lines=[_holder_line(row, holder),
               LedgerLine(account=account_for("customer_deposits"),
                          commodity=functional_commodity(), quantity=-row.functional_amount,
                          party=order.customer)])
    row.save()
    order.deposit_amount += row.functional_amount
    return row


# --- taking in -----------------------------------------------------------------------------------

def create_repair(data: RepairInput, *, actor=None) -> RepairOrder:
    with transaction.atomic():
        branch = Branch.objects.filter(pk=data.branch_id, is_active=True).first()
        if branch is None:
            raise _field("branch", _("Unknown branch."))
        if actor is not None:
            actor.require("repairs.order.create", branch=branch)
        if data.kind not in RepairKind.values:
            raise _field("kind", _("Choose repair or custom order."))
        customer = None
        if data.customer_id:
            customer = Party.objects.filter(pk=data.customer_id, is_active=True,
                                            roles__role=PartyRoleType.CUSTOMER).first()
            if customer is None:
                raise _field("customer", _("Unknown customer."))
        elif not data.customer_name.strip():
            raise _field("customer", _("Choose the customer or type their name."))
        if not data.lines:
            raise _field("lines", _("Describe at least one piece."))
        today = timezone.localdate()
        if data.promised_on is not None and data.promised_on < today:
            raise _field("promised_on", _("The date has passed."))
        karats = {k.pk: k for k in Karat.objects.filter(
            pk__in=[ln.karat_id for ln in data.lines if ln.karat_id], is_active=True)}

        order = RepairOrder.objects.create(
            branch=branch, business_date=today, kind=data.kind, customer=customer,
            customer_name="" if customer else data.customer_name.strip()[:200],
            customer_phone="" if customer else data.customer_phone.strip()[:30],
            promised_on=data.promised_on, note=data.note.strip(),
            created_by=getattr(actor, "user", None))
        lines = []
        for index, line in enumerate(data.lines):
            if not line.description.strip():
                raise _line_error(index, "description", _("Describe the piece and the work."))
            if line.karat_id and line.karat_id not in karats:
                raise _line_error(index, "karat", _("Unknown karat."))
            lines.append(RepairLine(
                order=order, description=line.description.strip()[:300],
                karat=karats.get(line.karat_id),
                charge_amount=_amount(line.charge, "charge", index),
                weight_in_g=_weight(line.weight_in_g, "weight_in_g", index)))
        RepairLine.objects.bulk_create(lines)

        order.charge_amount = sum((ln.charge_amount for ln in lines), ZERO)
        order.bag_number = allocate_value("repair.bag", branch=branch)
        order.number = allocate_number("RP", branch=branch, fiscal_year=today.year)
        if data.deposit is not None and data.deposit.amount not in (None, "", "0"):
            _move_deposit(order, data.deposit, sign=1, actor=actor)
        order.status = DocStatus.POSTED
        order.posted_at = timezone.now()
        order.posted_by = getattr(actor, "user", None)
        order.save()
    return order


def add_deposit(order_id: int, payment: PaymentInput, *, actor=None) -> RepairPayment:
    with transaction.atomic():
        order = _open(order_id)
        if actor is not None:
            actor.require("repairs.order.create", branch=order.branch)
        row = _move_deposit(order, payment, sign=1, actor=actor)
        order.save(update_fields=["deposit_amount", "updated_at"])
    return row


# --- the work ------------------------------------------------------------------------------------

def send_to_workshop(order_id: int, workshop_id: int, *, actor=None) -> RepairOrder:
    with transaction.atomic():
        order = _open(order_id)
        if actor is not None:
            actor.require("repairs.order.update", branch=order.branch)
        if order.state != "received":
            raise DomainError(_("Only repairs still in the shop can be sent."),
                              code="REPAIR_NOT_IN_SHOP")
        workshop = Party.objects.filter(pk=workshop_id, is_active=True,
                                        roles__role=PartyRoleType.WORKSHOP).first()
        if workshop is None:
            raise _field("workshop", _("Choose the workshop."))
        order.workshop = workshop
        order.sent_on = timezone.localdate()
        order.save()
    return order


def mark_ready(order_id: int, lines: tuple[ReadyLineInput, ...], *, labour_amount="0",
               actor=None) -> RepairOrder:
    """The pieces are back (or done in the shop): weights out, final charges, labour."""
    with transaction.atomic():
        order = _open(order_id)
        if actor is not None:
            actor.require("repairs.order.update", branch=order.branch)
        if order.state not in ("received", "at_workshop"):
            raise DomainError(_("This repair is already ready."), code="REPAIR_READY")
        labour = _amount(labour_amount, "labour_amount")
        if labour and order.workshop_id is None:
            raise _field("labour_amount", _("Only work done by a workshop has a labour charge."))
        saved = {line.pk: line for line in order.lines.all()}
        given = {line.line_id: (index, line) for index, line in enumerate(lines)}
        if set(given) != set(saved):
            raise _field("lines", _("Enter the weight of every piece."))
        for line_id, (index, ready) in given.items():
            line = saved[line_id]
            line.weight_out_g = _weight(ready.weight_out_g, "weight_out_g", index)
            if ready.charge not in (None, ""):
                line.charge_amount = _amount(ready.charge, "charge", index)
            line.save(update_fields=["weight_out_g", "charge_amount", "updated_at"])
        order.charge_amount = sum((ln.charge_amount for ln in saved.values()), ZERO)
        order.labour_amount = labour
        order.ready_on = timezone.localdate()
        if labour:
            home = functional_commodity()
            order.labour_entry = post_entry(
                branch=order.branch, business_date=order.ready_on, kind=EntryKind.AUTO,
                source_type=DOC_TYPE, source_id=order.pk,
                memo=_("Workshop labour for %(number)s") % {"number": order.number},
                lines=[LedgerLine(account=account_for("repair_costs"), commodity=home,
                                  quantity=labour),
                       LedgerLine(account=account_for("workshops"), commodity=home,
                                  quantity=-labour, party=order.workshop)])
        order.save()
    return order


# --- delivery ------------------------------------------------------------------------------------

@dataclass
class DeliveryQuote:
    charge: Decimal
    deposit: Decimal
    due: Decimal  # charge − deposit (negative: the deposit is more than the charge)
    paid: Decimal
    change: Decimal
    balance: Decimal  # left on the customer's account


def quote_delivery(order: RepairOrder, rows: list[RepairPayment],
                   on_account: bool) -> DeliveryQuote:
    paid = sum((row.functional_amount for row in rows), ZERO)
    due = order.charge_amount - order.deposit_amount
    if on_account:
        if order.customer_id is None:
            raise _field("customer", _("Only a registered customer can pay later."))
        if paid - max(due, ZERO) > TOLERANCE:
            raise _field("payments", _("Payments are more than the amount due."))
        return DeliveryQuote(order.charge_amount, order.deposit_amount, due, paid,
                             change=max(-due, ZERO), balance=max(due - paid, ZERO))
    if due - paid > TOLERANCE:
        raise DomainError(_("%(amount)s is still to pay.") % {"amount": due - paid},
                          code="REPAIR_NOT_FULLY_PAID")
    change = max(paid - due, ZERO)
    cash_in = sum((row.functional_amount for row in rows if row.kind == TenderKind.CASH), ZERO)
    if change > cash_in + max(-due, ZERO):
        raise _field("payments", _("Change is given in cash: pay the exact amount by card or "
                                   "transfer."))
    return DeliveryQuote(order.charge_amount, order.deposit_amount, due, paid, change, ZERO)


def deliver(order_id: int, *, payments: tuple[PaymentInput, ...] = (), on_account: bool = False,
            actor=None) -> RepairOrder:
    with transaction.atomic():
        order = _open(order_id)
        if actor is not None:
            actor.require("repairs.order.deliver", branch=order.branch)
        if order.state != "ready":
            raise DomainError(_("Mark the repair ready before delivering it."),
                              code="REPAIR_NOT_READY")
        tendered = [_tender(order, payment, stage=RepairPaymentStage.DELIVERY, actor=actor)
                    for payment in payments if payment.amount not in (None, "", "0")]
        rows = [row for row, _holder in tendered]
        quote = quote_delivery(order, rows, on_account)

        home = functional_commodity()
        lines = [_holder_line(row, holder) for row, holder in tendered]
        if quote.deposit:
            lines.append(LedgerLine(account=account_for("customer_deposits"), commodity=home,
                                    quantity=quote.deposit, party=order.customer))
        if quote.balance:
            lines.append(LedgerLine(account=account_for("customers"), commodity=home,
                                    quantity=quote.balance, party=order.customer))
        if quote.charge:
            lines.append(LedgerLine(account=account_for("repair_income"), commodity=home,
                                    quantity=-quote.charge))
        if quote.change:
            box = next((holder.cash_box for row, holder in tendered
                        if row.kind == TenderKind.CASH and row.currency.code == home.code
                        and holder.cash_box), None) or default_cash_box(order.branch,
                                                                          home.currency)
            if quote.change > sum((row.amount for row, holder in tendered
                                   if holder.cash_box == box), ZERO):
                ensure_cash_available(box, quote.change, "payments")
            lines.append(LedgerLine(account=box.account, commodity=home, quantity=-quote.change))
        if lines:
            order.delivery_entry = post_entry(
                branch=order.branch, business_date=timezone.localdate(), kind=EntryKind.AUTO,
                source_type=DOC_TYPE, source_id=order.pk, lines=lines,
                memo=_("Repair %(number)s delivered") % {"number": order.number})
        for row in rows:
            row.journal_entry = order.delivery_entry
            row.save()
        order.paid_amount = quote.paid
        order.change_amount = quote.change
        order.balance_amount = quote.balance
        order.deposit_amount = ZERO
        order.delivered_at = timezone.now()
        order.delivered_by = getattr(actor, "user", None)
        order.save()
    return order


def cancel_repair(order_id: int, *, refund_method: str = RefundMethod.CASH, cash_box_id=None,
                  bank_account_id=None, reason: str = "", actor=None) -> RepairOrder:
    """Before delivery. Labour already charged by a workshop stays a repair cost."""
    if refund_method not in RefundMethod.CHOICES:
        raise _field("refund_method", _("Choose how to refund."))
    with transaction.atomic():
        order = _open(order_id)
        if actor is not None:
            actor.require("repairs.order.cancel", branch=order.branch)
        held = order.deposit_amount
        if held > 0:
            if refund_method == RefundMethod.CUSTOMER_CREDIT:
                home = functional_commodity()
                order.cancel_entry = post_entry(
                    branch=order.branch, business_date=timezone.localdate(),
                    kind=EntryKind.AUTO, source_type=DOC_TYPE, source_id=order.pk,
                    memo=_("Deposit of cancelled %(number)s") % {"number": order.number},
                    lines=[LedgerLine(account=account_for("customer_deposits"), commodity=home,
                                      quantity=held, party=order.customer),
                           LedgerLine(account=account_for("customers"), commodity=home,
                                      quantity=-held, party=order.customer)])
                order.deposit_amount = ZERO
            else:
                _move_deposit(order, PaymentInput(
                    kind=refund_method, currency_code=functional_currency(), amount=str(held),
                    cash_box_id=cash_box_id, bank_account_id=bank_account_id), sign=-1,
                    actor=actor)
        order.status = DocStatus.VOIDED
        order.voided_at = timezone.now()
        order.voided_by = getattr(actor, "user", None)
        order.void_reason = reason.strip()[:300]
        order.save()
    return order
