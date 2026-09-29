"""Expense categories and vouchers (§7.12).

A voucher posts   Dr the category's expense account (company currency)
                  Cr the cash box or bank account it was paid from (the currency paid)
and is cancelled by reversing that entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.models import Currency
from apps.core.errors import DomainError, NotFound, ValidationError
from apps.core.models import DocStatus
from apps.core.numeric import MONEY, quantize
from apps.core.sequences import allocate_number
from apps.ledger.models import Account, AccountType, EntryKind, Subledger
from apps.ledger.services import LineInput as LedgerLine
from apps.ledger.services import functional_commodity, money_commodity, post_entry, reverse_entry
from apps.org.models import Branch
from apps.treasury.holders import (
    TenderKind,
    ensure_cash_available,
    outflow_value,
    resolve_tender,
)

from .models import ExpenseCategory, ExpenseVoucher

DOC_TYPE = "expenses.ExpenseVoucher"
MANAGE = "expenses.category.manage"


def _field_error(field: str, message: str) -> ValidationError:
    return ValidationError(_("Please correct the highlighted fields."), fields={field: [message]})


def expense_accounts():
    """Accounts a category can point at: postable expense accounts without a party."""
    return Account.objects.filter(type=AccountType.EXPENSE, is_postable=True, is_active=True,
                                  subledger=Subledger.NONE).exclude(
        role__in=("cogs_gold", "cogs_diamonds", "metal_loss", "fx_loss", "rounding"))


# --- categories -------------------------------------------------------------------------------

def _category_fields(category: ExpenseCategory, name: str, account_id) -> None:
    name = name.strip()
    if not name:
        raise _field_error("name", _("Required."))
    account = (expense_accounts().filter(pk=account_id).first() if account_id
               else expense_accounts().filter(role="expenses").first())
    if account is None:
        raise _field_error("account", _("Choose an expense account."))
    category.name = name[:100]
    category.account = account


def _save_category(category: ExpenseCategory) -> ExpenseCategory:
    try:
        with transaction.atomic():
            category.save()
    except IntegrityError:
        raise _field_error("name", _("A category with this name already exists.")) from None
    return category


def create_category(name: str, account_id: int | None = None, *, actor=None) -> ExpenseCategory:
    if actor is not None:
        actor.require(MANAGE)
    category = ExpenseCategory()
    _category_fields(category, name, account_id)
    return _save_category(category)


def update_category(category_id: int, name: str, account_id: int | None = None, *,
                    actor=None) -> ExpenseCategory:
    if actor is not None:
        actor.require(MANAGE)
    category = ExpenseCategory.objects.filter(pk=category_id).first()
    if category is None:
        raise NotFound(_("Not found."))
    _category_fields(category, name, account_id)
    return _save_category(category)


def set_category_active(category_id: int, active: bool, *, actor=None) -> ExpenseCategory:
    if actor is not None:
        actor.require(MANAGE)
    category = ExpenseCategory.objects.filter(pk=category_id).first()
    if category is None:
        raise NotFound(_("Not found."))
    category.is_active = active
    category.save(update_fields=["is_active", "updated_at"])
    return category


# --- vouchers ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class ExpenseInput:
    branch_id: int
    category_id: int
    method: str  # cash | bank_transfer
    currency_code: str
    amount: Decimal | str
    cash_box_id: int | None = None
    bank_account_id: int | None = None
    payee: str = ""
    reference: str = ""
    note: str = ""


def post_expense(data: ExpenseInput, *, actor=None) -> ExpenseVoucher:
    with transaction.atomic():
        branch = Branch.objects.filter(pk=data.branch_id, is_active=True).first()
        if branch is None:
            raise _field_error("branch", _("Unknown branch."))
        if actor is not None:
            actor.require("expenses.voucher.create", branch=branch)
        category = ExpenseCategory.objects.select_related("account").filter(
            pk=data.category_id, is_active=True).first()
        if category is None:
            raise _field_error("category", _("Choose a category."))
        if data.method not in (TenderKind.CASH, TenderKind.BANK_TRANSFER):
            raise _field_error("method", _("Choose how it was paid."))
        currency = Currency.objects.filter(code=data.currency_code, is_active=True).first()
        if currency is None:
            raise _field_error("currency", _("Unknown currency."))
        try:
            amount = quantize(data.amount, MONEY)
        except (TypeError, ValueError):
            raise _field_error("amount", _("Enter a number.")) from None
        if amount <= 0:
            raise _field_error("amount", _("Must be greater than zero."))

        holder = resolve_tender(data.method, branch, currency, cash_box_id=data.cash_box_id,
                                bank_account_id=data.bank_account_id)
        if holder.cash_box is not None:
            ensure_cash_available(holder.cash_box, amount, "amount")
        value = outflow_value(holder.account.pk, currency, amount)

        today = timezone.localdate()
        voucher = ExpenseVoucher.objects.create(
            branch=branch, business_date=today, category=category, currency=currency,
            amount=amount, functional_amount=value, cash_box=holder.cash_box,
            bank_account=holder.bank_account, payee=data.payee.strip()[:200],
            reference=data.reference.strip()[:60], note=data.note.strip(),
            created_by=getattr(actor, "user", None))
        voucher.number = allocate_number("EX", branch=branch, fiscal_year=today.year)
        voucher.journal_entry = post_entry(
            branch=branch, business_date=today, kind=EntryKind.AUTO, source_type=DOC_TYPE,
            source_id=voucher.pk, memo=f"{category.name} {voucher.number}",
            lines=[LedgerLine(account=category.account, commodity=functional_commodity(),
                              quantity=value),
                   LedgerLine(account=holder.account, commodity=money_commodity(currency),
                              quantity=-amount, functional_amount=-value)],
        )
        voucher.status = DocStatus.POSTED
        voucher.posted_at = timezone.now()
        voucher.posted_by = getattr(actor, "user", None)
        voucher.save()
    return voucher


def void_expense(voucher_id: int, *, reason: str = "", actor=None) -> ExpenseVoucher:
    with transaction.atomic():
        voucher = (ExpenseVoucher.objects.select_for_update(of=("self",))
                   .select_related("branch").filter(pk=voucher_id).first())
        if voucher is None:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("expenses.void", branch=voucher.branch)
        if voucher.status != DocStatus.POSTED:
            raise DomainError(_("Only posted documents can be cancelled."), code="DOC_NOT_POSTED")
        today = timezone.localdate()
        if voucher.journal_entry_id:
            reverse_entry(voucher.journal_entry_id, business_date=today,
                          memo=_("Cancelled %(number)s") % {"number": voucher.number})
        voucher.status = DocStatus.VOIDED
        voucher.voided_at = timezone.now()
        voucher.voided_by = getattr(actor, "user", None)
        voucher.void_reason = reason.strip()[:300]
        voucher.save()
    return voucher
