"""Money in and out of a cash box or bank account against any account (legacy Cash GHV11a /
GHV12a "ايداع / سحب" and Bnk GHB13a, which had no other side).

    money in    Dr the box or bank (the currency received)   Cr the chosen account
    money out   Dr the chosen account                         Cr the box or bank

The chosen account is any active account that takes money and has no customer/supplier ledger
(partners, customers and suppliers have Receipts & payments), other than cash boxes, banks and
the accounts the stock and the documents keep. A balance sheet account (a loan, the owner's
current account…) keeps the currency; income and expense accounts are booked in the company
currency. Cancelling reverses the entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.models import Currency
from apps.core.errors import DomainError, NotFound, ValidationError
from apps.core.models import DocStatus
from apps.core.numeric import MONEY, quantize, round_money
from apps.core.sequences import allocate_number
from apps.ledger.accounts import holder_accounts
from apps.ledger.models import Account as LedgerAccount
from apps.ledger.models import AccountType, CommodityScope, EntryKind, Subledger
from apps.ledger.services import LineInput as LedgerLine
from apps.ledger.services import functional_commodity, money_commodity, post_entry, reverse_entry
from apps.org.models import Branch
from apps.pricing.selectors import fx_rate

from .holders import TenderKind, ensure_cash_available, outflow_value, resolve_tender
from .models import MoneyVoucher, VoucherDirection

DOC_TYPE = "treasury.MoneyVoucher"
CREATE = "treasury.voucher.create"
# Kept by the stock, the documents and the closing: never posted to by hand here.
SYSTEM_ROLES = (
    "inventory_gold", "inventory_scrap", "inventory_diamonds", "inventory_at_workshop",
    "inventory_in_production", "branch_clearing", "cheques_received", "cheques_payable",
    "metal_position", "retained_earnings", "cogs_gold", "cogs_diamonds", "metal_gain",
    "metal_loss", "stone_loss", "rounding", "labour_absorbed", "employee_advances",
)
BALANCE_SHEET = (AccountType.ASSET, AccountType.LIABILITY, AccountType.EQUITY)


def _field_error(field: str, message: str) -> ValidationError:
    return ValidationError(_("Please correct the highlighted fields."), fields={field: [message]})


def counter_accounts():
    """Accounts money can come from or go to."""
    return (LedgerAccount.objects.filter(is_postable=True, is_active=True,
                                         subledger=Subledger.NONE)
            .exclude(commodity_scope=CommodityScope.METAL)
            .exclude(role__in=SYSTEM_ROLES).exclude(holder_accounts())
            .select_related("commodity").order_by("code"))


@dataclass(frozen=True)
class VoucherInput:
    branch_id: int
    direction: str  # in | out
    account_id: int
    method: str  # cash | bank_transfer
    currency_code: str
    amount: Decimal | str
    cash_box_id: int | None = None
    bank_account_id: int | None = None
    counterparty: str = ""
    reference: str = ""
    note: str = ""


def post_voucher(data: VoucherInput, *, actor=None) -> MoneyVoucher:
    with transaction.atomic():
        branch = Branch.objects.filter(pk=data.branch_id, is_active=True).first()
        if branch is None:
            raise _field_error("branch", _("Unknown branch."))
        if actor is not None:
            actor.require(CREATE, branch=branch)
        if data.direction not in VoucherDirection.values:
            raise _field_error("direction", _("Choose money in or money out."))
        account = counter_accounts().filter(pk=data.account_id).first()
        if account is None:
            raise _field_error("account", _("Choose the account."))
        if data.method not in (TenderKind.CASH, TenderKind.BANK_TRANSFER):
            raise _field_error("method", _("Choose a cash box or a bank account."))
        currency = Currency.objects.filter(code=data.currency_code, is_active=True).first()
        if currency is None:
            raise _field_error("currency", _("Unknown currency."))
        if account.commodity_id and account.commodity.code != currency.code:
            raise _field_error("account", _("This account takes %(code)s only.")
                               % {"code": account.commodity.code})
        try:
            amount = quantize(data.amount, MONEY)
        except (TypeError, ValueError, ArithmeticError):
            raise _field_error("amount", _("Enter a number.")) from None
        if amount <= 0:
            raise _field_error("amount", _("Must be greater than zero."))

        holder = resolve_tender(data.method, branch, currency, cash_box_id=data.cash_box_id,
                                bank_account_id=data.bank_account_id)
        money = money_commodity(currency)
        sign = 1 if data.direction == VoucherDirection.IN else -1
        if sign > 0:
            value = round_money(amount * fx_rate(currency.code))
        else:
            if holder.cash_box is not None:
                ensure_cash_available(holder.cash_box, amount, "amount")
            value = outflow_value(holder.account.pk, currency, amount)
        if account.commodity_id or account.type in BALANCE_SHEET:
            other = LedgerLine(account=account, commodity=money, quantity=-sign * amount,
                               functional_amount=-sign * value)
        else:
            other = LedgerLine(account=account, commodity=functional_commodity(),
                               quantity=-sign * value)

        today = timezone.localdate()
        user = getattr(actor, "user", None)
        voucher = MoneyVoucher.objects.create(
            branch=branch, business_date=today, direction=data.direction,
            cash_box=holder.cash_box, bank_account=holder.bank_account, account=account,
            currency=currency, amount=amount, functional_amount=value,
            counterparty=data.counterparty.strip()[:200], reference=data.reference.strip()[:60],
            note=data.note.strip(), created_by=user)
        voucher.number = allocate_number("MV", branch=branch, fiscal_year=today.year)
        voucher.journal_entry = post_entry(
            branch=branch, business_date=today, kind=EntryKind.AUTO, source_type=DOC_TYPE,
            source_id=voucher.pk, memo=f"{voucher.get_direction_display()} {voucher.number}",
            lines=[LedgerLine(account=holder.account, commodity=money, quantity=sign * amount,
                              functional_amount=sign * value), other])
        voucher.status, voucher.posted_by = DocStatus.POSTED, user
        voucher.posted_at = timezone.now()
        voucher.save()
    return voucher


def void_voucher(voucher_id: int, *, reason: str = "", actor=None) -> MoneyVoucher:
    with transaction.atomic():
        voucher = (MoneyVoucher.objects.select_for_update(of=("self",))
                   .select_related("branch", "cash_box").filter(pk=voucher_id).first())
        if voucher is None:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("treasury.void", branch=voucher.branch)
        if voucher.status != DocStatus.POSTED:
            raise DomainError(_("Only posted documents can be cancelled."), code="DOC_NOT_POSTED")
        if voucher.direction == VoucherDirection.IN and voucher.cash_box_id:
            ensure_cash_available(voucher.cash_box, voucher.amount, "amount")
        if voucher.journal_entry_id:
            reverse_entry(voucher.journal_entry_id, business_date=timezone.localdate(),
                          memo=_("Cancelled %(number)s") % {"number": voucher.number})
        voucher.status, voucher.voided_at = DocStatus.VOIDED, timezone.now()
        voucher.voided_by, voucher.void_reason = getattr(actor, "user", None), reason.strip()[:300]
        voucher.save()
    return voucher
