"""Reservations (§7.7): pieces held for a customer against deposits.

reserve   pieces in stock at the branch → reserved; prices recorded from today's board
deposit   Dr cash box / bank / terminal      Cr customer deposits (customer)
complete  a sale to the customer in which the deposits count as a payment (up to the amount
          due; any excess stays with the customer as credit on their account)
cancel    pieces back in stock; deposits paid back from a box / bank account, or credited to
          the customer's account:  Dr customer deposits   Cr box / bank  or  Cr customers
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.models import Currency
from apps.core.errors import DomainError, NotFound, ValidationError
from apps.core.models import DocStatus
from apps.core.numeric import round_money, to_decimal
from apps.core.sequences import allocate_number
from apps.inventory.services import set_reserved
from apps.ledger.models import EntryKind
from apps.ledger.services import LineInput as LedgerLine
from apps.ledger.services import account_for, functional_commodity, money_commodity, post_entry
from apps.pricing.selectors import functional_currency
from apps.pricing.selectors import fx_rate as rate_in_force
from apps.treasury.holders import TenderKind, ensure_cash_available, resolve_tender

from .models import (
    PaymentKind,
    PaymentTerms,
    Reservation,
    ReservationDeposit,
    ReservationLine,
    SalesInvoice,
)
from .services import PaymentInput, SaleInput, SaleLineInput, post_sale, quote_sale

DOC_TYPE = "sales.Reservation"
ZERO = Decimal(0)


class RefundMethod:
    CASH = "cash"
    BANK_TRANSFER = "bank_transfer"
    CUSTOMER_CREDIT = "customer_credit"
    CHOICES = (CASH, BANK_TRANSFER, CUSTOMER_CREDIT)


@dataclass(frozen=True)
class ReservationInput:
    branch_id: int
    customer_id: int | None
    lines: tuple[SaleLineInput, ...]
    price_locked: bool = False
    expires_on: date | None = None
    deposit: PaymentInput | None = None
    note: str = ""


def _open(reservation_id: int) -> Reservation:
    reservation = (Reservation.objects.select_for_update(of=("self",))
                   .select_related("branch", "customer").filter(pk=reservation_id).first())
    if reservation is None:
        raise NotFound(_("Not found."))
    if reservation.state != "open":
        raise DomainError(_("This reservation is not open."), code="SALES_RESERVATION_CLOSED")
    return reservation


def _take_deposit(reservation: Reservation, payment: PaymentInput, *, sign: int = 1,
                  actor=None) -> ReservationDeposit | None:
    """Record money in (sign 1) or paid back (sign -1) through a box / bank / terminal."""
    currency = Currency.objects.filter(code=payment.currency_code, is_active=True).first()
    if currency is None:
        raise ValidationError(_("Unknown currency."), fields={"currency": [_("Unknown currency.")]})
    amount = round_money(to_decimal(payment.amount or "0"))
    if amount <= 0:
        raise ValidationError(_("Must be greater than zero."),
                              fields={"amount": [_("Must be greater than zero.")]})
    if payment.kind == TenderKind.CARD and sign < 0:
        raise ValidationError(_("Cards can only be used to receive money."),
                              fields={"method": [_("Cards can only be used to receive money.")]})
    holder = resolve_tender(payment.kind, reservation.branch, currency,
                            cash_box_id=payment.cash_box_id,
                            bank_account_id=payment.bank_account_id,
                            terminal_id=payment.terminal_id)
    fx = Decimal(1) if currency.code == functional_currency() else rate_in_force(currency.code)
    value = round_money(amount * fx)
    if sign < 0 and holder.cash_box is not None:
        ensure_cash_available(holder.cash_box, amount, "amount")
    today = timezone.localdate()
    deposit = ReservationDeposit(
        reservation=reservation, business_date=today, kind=payment.kind, currency=currency,
        amount=sign * amount, fx_rate=fx, functional_amount=sign * value,
        number=allocate_number("RD", branch=reservation.branch, fiscal_year=today.year),
        created_by=getattr(actor, "user", None), **holder.fields)
    money = money_commodity(currency)
    deposit.journal_entry = post_entry(
        branch=reservation.branch, business_date=today, kind=EntryKind.AUTO,
        source_type=DOC_TYPE, source_id=reservation.pk,
        memo=(_("Deposit %(number)s") if sign > 0 else _("Deposit returned %(number)s"))
        % {"number": deposit.number},
        lines=[LedgerLine(account=holder.account, commodity=money, quantity=sign * amount,
                          functional_amount=None if money.is_functional else sign * value),
               LedgerLine(account=account_for("customer_deposits"),
                          commodity=functional_commodity(), quantity=-sign * value,
                          party=reservation.customer)])
    deposit.save()
    reservation.deposit_amount += sign * value
    return deposit


def create_reservation(data: ReservationInput, *, actor=None) -> Reservation:
    with transaction.atomic():
        if not data.customer_id:
            raise ValidationError(_("A reservation needs a customer."),
                                  fields={"customer": [_("Required.")]})
        if not data.lines:
            raise ValidationError(_("Add at least one piece."),
                                  fields={"lines": [_("Add at least one piece.")]})
        quote = quote_sale(SaleInput(branch_id=data.branch_id, lines=data.lines,
                                     customer_id=data.customer_id,
                                     payment_terms=PaymentTerms.CREDIT), actor=actor)
        if actor is not None:
            actor.require("sales.reservation.create", branch=quote.branch)
        today = timezone.localdate()
        if data.expires_on is not None and data.expires_on < today:
            raise ValidationError(_("The date has passed."),
                                  fields={"expires_on": [_("The date has passed.")]})
        reservation = Reservation.objects.create(
            branch=quote.branch, business_date=today, customer=quote.customer,
            price_locked=data.price_locked, price_board=quote.board, expires_on=data.expires_on,
            quoted_total=quote.total, note=data.note.strip(),
            created_by=getattr(actor, "user", None))
        for item, priced in sorted(zip(quote.items, quote.lines, strict=True),
                                   key=lambda pair: pair[0].pk):
            set_reserved(item.pk, True, branch=quote.branch)
            ReservationLine.objects.create(reservation=reservation, item=item,
                                           discount_rate=priced.discount_rate,
                                           quoted_total=priced.line_total)
        reservation.number = allocate_number("RS", branch=quote.branch, fiscal_year=today.year)
        if data.deposit is not None and data.deposit.amount not in (None, "", "0"):
            _take_deposit(reservation, data.deposit, actor=actor)
        reservation.status = DocStatus.POSTED
        reservation.posted_at = timezone.now()
        reservation.posted_by = getattr(actor, "user", None)
        reservation.save()
    return reservation


def add_deposit(reservation_id: int, payment: PaymentInput, *, actor=None) -> ReservationDeposit:
    with transaction.atomic():
        reservation = _open(reservation_id)
        if actor is not None:
            actor.require("sales.reservation.create", branch=reservation.branch)
        deposit = _take_deposit(reservation, payment, actor=actor)
        reservation.save(update_fields=["deposit_amount", "updated_at"])
    return deposit


def quote_completion(reservation: Reservation, payments=(), *, actor=None):
    """The sale that completing would post: the reserved pieces (at the locked board if the
    price is locked), the deposits first, then `payments` for the rest."""
    lines = tuple(SaleLineInput(item_id=line.item_id, discount_rate=line.discount_rate)
                  for line in reservation.lines.all())
    probe = quote_sale(SaleInput(branch_id=reservation.branch_id, lines=lines,
                                 customer_id=reservation.customer_id,
                                 payment_terms=PaymentTerms.CREDIT,
                                 reservation_id=reservation.pk), actor=actor)
    applied = min(reservation.deposit_amount, probe.due)
    deposit = (PaymentInput(kind=PaymentKind.DEPOSIT, currency_code=functional_currency(),
                            amount=str(applied)),) if applied > 0 else ()
    return lines, deposit + tuple(payments), applied, probe


def complete_reservation(reservation_id: int, *, payments=(),
                         payment_terms: str = PaymentTerms.CASH, actor=None) -> SalesInvoice:
    with transaction.atomic():
        reservation = _open(reservation_id)
        if actor is not None:
            actor.require("sales.reservation.complete", branch=reservation.branch)
        lines, all_payments, applied, _probe = quote_completion(reservation, payments,
                                                                 actor=actor)
        invoice = post_sale(SaleInput(
            branch_id=reservation.branch_id, lines=lines, payments=all_payments,
            payment_terms=payment_terms, customer_id=reservation.customer_id,
            reservation_id=reservation.pk,
            note=_("Reservation %(number)s") % {"number": reservation.number}), actor=actor)
        leftover = reservation.deposit_amount - applied
        if leftover > 0:  # the price fell below the deposits: the rest is the customer's credit
            _credit_customer(reservation, leftover, memo=_("Deposit left over from %(number)s"))
        reservation.sale = invoice
        reservation.completed_at = timezone.now()
        reservation.deposit_amount = ZERO
        reservation.save()
    return invoice


def _credit_customer(reservation: Reservation, amount: Decimal, *, memo: str):
    home = functional_commodity()
    return post_entry(
        branch=reservation.branch, business_date=timezone.localdate(), kind=EntryKind.AUTO,
        source_type=DOC_TYPE, source_id=reservation.pk,
        memo=memo % {"number": reservation.number},
        lines=[LedgerLine(account=account_for("customer_deposits"), commodity=home,
                          quantity=amount, party=reservation.customer),
               LedgerLine(account=account_for("customers"), commodity=home, quantity=-amount,
                          party=reservation.customer)])


def cancel_reservation(reservation_id: int, *, refund_method: str = RefundMethod.CASH,
                       cash_box_id=None, bank_account_id=None, reason: str = "",
                       actor=None) -> Reservation:
    if refund_method not in RefundMethod.CHOICES:
        raise ValidationError(_("Choose how to refund."),
                              fields={"refund_method": [_("Required.")]})
    with transaction.atomic():
        reservation = _open(reservation_id)
        if actor is not None:
            actor.require("sales.reservation.cancel", branch=reservation.branch)
        for line in reservation.lines.order_by("item_id"):
            set_reserved(line.item_id, False, branch=reservation.branch)
        held = reservation.deposit_amount
        if held > 0:
            if refund_method == RefundMethod.CUSTOMER_CREDIT:
                reservation.cancel_entry = _credit_customer(
                    reservation, held, memo=_("Deposit of cancelled %(number)s"))
                reservation.deposit_amount = ZERO
            else:
                _take_deposit(reservation, PaymentInput(
                    kind=refund_method, currency_code=functional_currency(), amount=str(held),
                    cash_box_id=cash_box_id, bank_account_id=bank_account_id), sign=-1,
                    actor=actor)
        reservation.status = DocStatus.VOIDED
        reservation.voided_at = timezone.now()
        reservation.voided_by = getattr(actor, "user", None)
        reservation.void_reason = reason.strip()[:300]
        reservation.save()
    return reservation
