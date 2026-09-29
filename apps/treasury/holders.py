"""Cash boxes, bank accounts and card terminals: setup, and choosing the one a payment uses.

Every holder gets its own ledger account under the group of its role ("cash", "bank",
"card_receivable"), locked to the holder's currency. Sales, returns, settlements and expenses
never post to a group; they ask `resolve_tender` for the holder and post to its account.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import Max, Q, Sum
from django.utils.translation import gettext as _

from apps.catalog.models import Currency
from apps.core.errors import DomainError, NotFound, ValidationError
from apps.core.numeric import RATE, quantize, round_money
from apps.core.templatetags.ui import num
from apps.ledger.models import Account, AccountType, BalanceProjection, CommodityScope, Nature
from apps.ledger.services import money_commodity
from apps.org.models import Branch
from apps.pricing.selectors import functional_currency
from apps.pricing.selectors import fx_rate as rate_in_force

from .models import BankAccount, BankAccountBranch, CardTerminal, CashBox

MANAGE = "treasury.setup.manage"
ZERO = Decimal(0)


class TenderKind:
    CASH = "cash"
    CARD = "card"
    BANK_TRANSFER = "bank_transfer"


def _field_error(field: str, message: str) -> ValidationError:
    # The message itself, not "correct the highlighted fields": the sales screen shows only it.
    return ValidationError(message, fields={field: [message]})


# --- ledger accounts --------------------------------------------------------------------------

def _holder_account(group_role: str, currency: Currency, name: str) -> Account:
    """A new postable account under the group, e.g. 1101 → 1101001, 1101002…"""
    group = Account.objects.select_for_update().filter(role=group_role).first()
    if group is None:
        raise DomainError(_("No account is set up for “%(role)s”.") % {"role": group_role},
                          code="LEDGER_ROLE_MISSING")
    top = (Account.objects.filter(parent=group, code__startswith=group.code)
           .aggregate(top=Max("code"))["top"])
    seq = int(top[len(group.code):]) + 1 if top and top[len(group.code):].isdigit() else 1
    return Account.objects.create(
        code=f"{group.code}{seq:03d}", name=name[:200], parent=group, type=AccountType.ASSET,
        nature=Nature.DEBIT, commodity_scope=CommodityScope.MONEY,
        commodity=money_commodity(currency), is_postable=True,
    )


def _rename_account(account: Account, name: str) -> None:
    if account.name != name[:200]:
        account.name = name[:200]
        account.save(update_fields=["name", "updated_at"])


def _box_account_name(branch: Branch, currency: Currency, name: str) -> str:
    # Stored, so kept language-neutral: user-entered names and codes only.
    return " · ".join(part for part in (branch.name, name, currency.code) if part)


def balance(account_id: int) -> tuple[Decimal, Decimal]:
    """(quantity, value in the company currency) held on a holder's account right now."""
    totals = BalanceProjection.objects.filter(account_id=account_id).aggregate(
        q=Sum("quantity"), f=Sum("functional_amount"))
    return totals["q"] or ZERO, totals["f"] or ZERO


def outflow_value(account_id: int, currency: Currency, amount: Decimal) -> Decimal:
    """Company-currency value of `amount` leaving a holder: its average carrying value for a
    foreign currency (so moving dollars between boxes makes no gain or loss), the rate in
    force when the holder has none."""
    if currency.code == functional_currency():
        return amount
    quantity, value = balance(account_id)
    if quantity > 0:
        return round_money(value * amount / quantity)
    return round_money(amount * rate_in_force(currency.code))


def ensure_cash_available(box: CashBox, amount: Decimal, field: str) -> None:
    """A drawer cannot pay out money it does not hold."""
    quantity, _value = balance(box.account_id)
    if amount > quantity:
        raise _field_error(field, _("Only %(amount)s %(currency)s in %(box)s.") % {
            "amount": num(quantity, box.currency.minor_units), "currency": box.currency.code,
            "box": box.label})


# --- cash boxes -------------------------------------------------------------------------------

@dataclass(frozen=True)
class CashBoxInput:
    branch_id: int
    currency_code: str
    name: str = ""
    is_default: bool = False


def _currency(code: str) -> Currency:
    currency = Currency.objects.filter(code=code, is_active=True).first()
    if currency is None:
        raise _field_error("currency", _("Unknown currency."))
    return currency


def _make_default(box: CashBox) -> None:
    CashBox.objects.filter(branch=box.branch, currency=box.currency, is_default=True).exclude(
        pk=box.pk).update(is_default=False)
    box.is_default = True


def create_cash_box(data: CashBoxInput, *, actor=None) -> CashBox:
    with transaction.atomic():
        branch = Branch.objects.filter(pk=data.branch_id, is_active=True).first()
        if branch is None:
            raise _field_error("branch", _("Unknown branch."))
        if actor is not None:
            actor.require(MANAGE, branch=branch)
        currency = _currency(data.currency_code)
        name = data.name.strip()
        has_default = CashBox.objects.filter(branch=branch, currency=currency,
                                             is_default=True).exists()
        box = CashBox(branch=branch, currency=currency, name=name,
                      account=_holder_account("cash", currency,
                                              _box_account_name(branch, currency, name)))
        if data.is_default or not has_default:
            _make_default(box)
        box.save()
    return box


def update_cash_box(box_id: int, *, name: str, is_default: bool, actor=None) -> CashBox:
    """Name and default flag only: a box keeps its branch and currency (its history is there)."""
    with transaction.atomic():
        box = CashBox.objects.select_related("branch", "currency", "account").filter(
            pk=box_id).first()
        if box is None:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require(MANAGE, branch=box.branch)
        box.name = name.strip()[:100]
        if is_default:
            _make_default(box)
        box.save()
        _rename_account(box.account, _box_account_name(box.branch, box.currency, box.name))
    return box


def default_cash_box(branch: Branch, currency: Currency) -> CashBox:
    """The branch's box for a currency, opened on first use (cash in a drawer needs a box)."""
    box = CashBox.objects.filter(branch=branch, currency=currency, is_default=True,
                                 is_active=True).first()
    if box is None:
        box = create_cash_box(CashBoxInput(branch_id=branch.pk, currency_code=currency.code,
                                           is_default=True))
    return box


# --- bank accounts ----------------------------------------------------------------------------

@dataclass(frozen=True)
class BankAccountInput:
    name: str
    currency_code: str = ""
    bank_name: str = ""
    account_number: str = ""
    iban: str = ""
    branch_ids: tuple[int, ...] | None = None  # None = every branch


def _set_bank_branches(bank: BankAccount, branch_ids) -> None:
    BankAccountBranch.objects.filter(bank_account=bank).delete()
    bank.all_branches = branch_ids is None
    if branch_ids is not None:
        branches = list(Branch.objects.filter(pk__in=set(branch_ids)))
        if not branches:
            raise _field_error("branches", _("Choose at least one branch."))
        BankAccountBranch.objects.bulk_create(
            [BankAccountBranch(bank_account=bank, branch=b) for b in branches])


def _bank_fields(bank: BankAccount, data: BankAccountInput) -> None:
    name = data.name.strip()
    if not name:
        raise _field_error("name", _("Required."))
    bank.name = name[:100]
    bank.bank_name = data.bank_name.strip()[:100]
    bank.account_number = data.account_number.strip()[:60]
    bank.iban = data.iban.replace(" ", "").upper()[:40]


def create_bank_account(data: BankAccountInput, *, actor=None) -> BankAccount:
    if actor is not None:
        actor.require(MANAGE)
    with transaction.atomic():
        currency = _currency(data.currency_code)
        bank = BankAccount(currency=currency)
        _bank_fields(bank, data)
        bank.account = _holder_account("bank", currency, f"{bank.name} · {currency.code}")
        bank.save()
        _set_bank_branches(bank, data.branch_ids)
        bank.save()
    return bank


def update_bank_account(bank_id: int, data: BankAccountInput, *, actor=None) -> BankAccount:
    if actor is not None:
        actor.require(MANAGE)
    with transaction.atomic():
        bank = BankAccount.objects.select_related("currency", "account").filter(pk=bank_id).first()
        if bank is None:
            raise NotFound(_("Not found."))
        _bank_fields(bank, data)
        _set_bank_branches(bank, data.branch_ids)
        bank.save()
        _rename_account(bank.account, f"{bank.name} · {bank.currency.code}")
    return bank


def banks_usable_at(branch: Branch):
    return BankAccount.objects.filter(is_active=True).filter(
        Q(all_branches=True) | Q(branch_links__branch=branch)).distinct()


# --- card terminals ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TerminalInput:
    name: str
    bank_account_id: int
    branch_id: int | None = None
    fee_rate: Decimal | str = "0"


def _terminal_fields(terminal: CardTerminal, data: TerminalInput) -> None:
    name = data.name.strip()
    if not name:
        raise _field_error("name", _("Required."))
    bank = BankAccount.objects.filter(pk=data.bank_account_id, is_active=True).first()
    if bank is None:
        raise _field_error("bank_account", _("Choose the bank account it pays into."))
    if terminal.pk and terminal.bank_account.currency_id != bank.currency_id:
        raise _field_error("bank_account", _("The bank account must stay in the same currency."))
    branch = None
    if data.branch_id:
        branch = Branch.objects.filter(pk=data.branch_id, is_active=True).first()
        if branch is None:
            raise _field_error("branch", _("Unknown branch."))
    try:
        fee = quantize(data.fee_rate or "0", RATE)
    except (TypeError, ValueError):
        raise _field_error("fee_rate", _("Enter a number.")) from None
    if not ZERO <= fee < 1:
        raise _field_error("fee_rate", _("Out of range."))
    terminal.name = name[:100]
    terminal.bank_account = bank
    terminal.branch = branch
    terminal.fee_rate = fee


def create_terminal(data: TerminalInput, *, actor=None) -> CardTerminal:
    if actor is not None:
        actor.require(MANAGE)
    with transaction.atomic():
        terminal = CardTerminal()
        _terminal_fields(terminal, data)
        currency = terminal.bank_account.currency
        terminal.account = _holder_account("card_receivable", currency,
                                           f"{terminal.name} · {currency.code}")
        terminal.save()
    return terminal


def update_terminal(terminal_id: int, data: TerminalInput, *, actor=None) -> CardTerminal:
    if actor is not None:
        actor.require(MANAGE)
    with transaction.atomic():
        terminal = (CardTerminal.objects.select_related("bank_account__currency", "account")
                    .filter(pk=terminal_id).first())
        if terminal is None:
            raise NotFound(_("Not found."))
        _terminal_fields(terminal, data)
        terminal.save()
        _rename_account(terminal.account, f"{terminal.name} · {terminal.currency.code}")
    return terminal


def terminals_usable_at(branch: Branch):
    return CardTerminal.objects.filter(is_active=True).filter(
        Q(branch__isnull=True) | Q(branch=branch))


# --- activation -------------------------------------------------------------------------------

HOLDER_MODELS = {"box": CashBox, "bank": BankAccount, "terminal": CardTerminal}


def set_holder_active(kind: str, holder_id: int, active: bool, *, actor=None):
    model = HOLDER_MODELS[kind]
    with transaction.atomic():
        holder = model.objects.select_related("account").filter(pk=holder_id).first()
        if holder is None:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require(MANAGE, branch=getattr(holder, "branch", None))
        if not active:
            quantity, _value = balance(holder.account_id)
            if quantity != 0:
                raise DomainError(_("Move its balance out before deactivating it."),
                                  code="TREASURY_HOLDER_NOT_EMPTY")
            if kind == "bank" and holder.terminals.filter(is_active=True).exists():
                raise DomainError(_("Deactivate the card terminals paying into it first."),
                                  code="TREASURY_BANK_HAS_TERMINALS")
        holder.is_active = active
        if kind == "box" and not active:
            holder.is_default = False
        holder.save()
        holder.account.is_active = active
        holder.account.save(update_fields=["is_active", "updated_at"])
    return holder


# --- tenders ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Holder:
    """The box, bank account or terminal a payment went through."""

    cash_box: CashBox | None = None
    bank_account: BankAccount | None = None
    terminal: CardTerminal | None = None

    @property
    def account(self) -> Account:
        return (self.cash_box or self.bank_account or self.terminal).account

    @property
    def fields(self) -> dict:
        return {"cash_box": self.cash_box, "bank_account": self.bank_account,
                "terminal": self.terminal}


def _pick(candidates, chosen_id, field, *, missing, several):
    candidates = list(candidates)
    if chosen_id:
        match = next((c for c in candidates if c.pk == int(chosen_id)), None)
        if match is None:
            raise _field_error(field, _("Not available at this branch in this currency."))
        return match
    if not candidates:
        raise DomainError(missing, code="TREASURY_NO_HOLDER")
    if len(candidates) > 1:
        raise _field_error(field, several)
    return candidates[0]


def resolve_tender(kind: str, branch: Branch, currency: Currency, *, cash_box_id=None,
                   bank_account_id=None, terminal_id=None, field_prefix: str = "") -> Holder:
    """The holder a payment of `kind` uses: the one chosen, else the only candidate (the
    branch's default box for cash). Raises if a choice is needed or impossible."""
    if kind == TenderKind.CASH:
        if cash_box_id:
            box = CashBox.objects.filter(pk=cash_box_id, branch=branch, currency=currency,
                                         is_active=True).select_related("account").first()
            if box is None:
                raise _field_error(f"{field_prefix}cash_box",
                                   _("Not available at this branch in this currency."))
            return Holder(cash_box=box)
        return Holder(cash_box=default_cash_box(branch, currency))
    if kind == TenderKind.CARD:
        terminal = _pick(
            terminals_usable_at(branch).filter(bank_account__currency=currency)
            .select_related("account", "bank_account"),
            terminal_id, f"{field_prefix}terminal",
            missing=_("No card terminal is set up for this branch in %(currency)s.")
            % {"currency": currency.code},
            several=_("Choose the card terminal."))
        return Holder(terminal=terminal)
    if kind == TenderKind.BANK_TRANSFER:
        bank = _pick(
            banks_usable_at(branch).filter(currency=currency).select_related("account"),
            bank_account_id, f"{field_prefix}bank_account",
            missing=_("No bank account is set up for this branch in %(currency)s.")
            % {"currency": currency.code},
            several=_("Choose the bank account."))
        return Holder(bank_account=bank)
    raise _field_error(f"{field_prefix}method", _("Choose how the money moves."))
