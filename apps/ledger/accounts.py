"""Editing the chart of accounts (legacy Acc GHG11-GHG15: budget items, general ledger,
sub-ledgers and accounts, a fixed five-level tree).

Here the tree has any depth: an account or a group goes under any active group, and its code
starts with the group's code. The standard accounts the postings rely on (those with a role)
can be renamed but not switched off or deleted; cash boxes, bank accounts and card terminals
are managed in Treasury. An account can be switched off when nothing is left on it, and deleted
only if it was added here and never used.

Migrations that add a standard account later must find it by role and take a free code: a
client may already have used the code for an account of its own.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext as _

from apps.catalog.models import Currency
from apps.core.errors import DomainError, NotFound, ValidationError

from .models import (
    Account,
    BalanceProjection,
    CommodityScope,
    JournalLine,
    Nature,
    Subledger,
)
from .services import money_commodity

MANAGE = "ledger.accounts.manage"
# Groups whose accounts belong to a cash box, bank account or card terminal (Treasury).
HOLDER_GROUPS = ("cash", "bank", "card_receivable")
MAX_CODE = 20


@dataclass(frozen=True)
class AccountInput:
    parent_id: int
    code: str
    name: str
    is_group: bool = False
    nature: str = ""  # debit | credit; blank = the parent's
    commodity_scope: str = CommodityScope.MONEY
    currency_code: str = ""  # one currency only (a loan in dollars…)


def _error(field: str, message: str) -> ValidationError:
    return ValidationError(_("Please correct the highlighted fields."), fields={field: [message]})


def holder_accounts() -> Q:
    """Matches the accounts of cash boxes, bank accounts and card terminals (lazily)."""
    from apps.treasury.models import BankAccount, CardTerminal, CashBox

    return (Q(parent__role__in=HOLDER_GROUPS)
            | Q(pk__in=CashBox.objects.values("account_id"))
            | Q(pk__in=BankAccount.objects.values("account_id"))
            | Q(pk__in=CardTerminal.objects.values("account_id")))


def holder_account_ids() -> set[int]:
    return set(Account.objects.filter(holder_accounts()).values_list("pk", flat=True))


def parent_choices():
    """Groups a new account can go under."""
    return (Account.objects.filter(is_postable=False, is_active=True)
            .exclude(role__in=HOLDER_GROUPS).order_by("code"))


def suggest_code(parent: Account) -> str:
    """The next free code under `parent`: one after the highest child, or the first of a new
    level (5 → 5101, 52 → 5201, 5201 → 520101)."""
    taken = set(Account.objects.values_list("code", flat=True))
    children = [c for c in Account.objects.filter(parent=parent).values_list("code", flat=True)
                if c.isdigit() and c.startswith(parent.code) and len(c) > len(parent.code)]
    if children:
        width = max(len(c) for c in children)
        number = max(int(c) for c in children if len(c) == width) + 1
    else:
        width = len(parent.code) + (3 if len(parent.code) == 1 else 2)
        number = int(parent.code.ljust(width, "0")) + 1
    while True:
        code = str(number).zfill(width)
        if not code.startswith(parent.code) or len(code) > MAX_CODE:
            width += 2  # this level is full: open a deeper one
            number = int(parent.code.ljust(width, "0")) + 1
            continue
        if code not in taken:
            return code
        number += 1


def create_account(data: AccountInput, *, actor=None) -> Account:
    if actor is not None:
        actor.require(MANAGE)
    with transaction.atomic():
        parent = parent_choices().select_for_update().filter(pk=data.parent_id).first()
        if parent is None:
            raise _error("parent", _("Choose the group it goes under."))
        code = (data.code or "").strip() or suggest_code(parent)
        if not code.isdigit() or len(code) > MAX_CODE:
            raise _error("code", _("Digits only, at most %(n)s.") % {"n": MAX_CODE})
        if not code.startswith(parent.code) or len(code) <= len(parent.code):
            raise _error("code", _("Must start with the group's code %(code)s.")
                         % {"code": parent.code})
        if Account.objects.filter(code=code).exists():
            raise _error("code", _("Another account already has this code."))
        name = data.name.strip()
        if not name:
            raise _error("name", _("Required."))
        nature = data.nature or parent.nature
        if nature not in Nature.values:
            raise _error("nature", _("Choose debit or credit."))
        account = Account(code=code, name=name[:200], parent=parent, type=parent.type,
                          nature=nature, subledger=Subledger.NONE, is_postable=not data.is_group)
        if data.is_group:
            account.commodity_scope = CommodityScope.ANY
        else:
            if data.currency_code:
                currency = Currency.objects.filter(code=data.currency_code,
                                                   is_active=True).first()
                if currency is None:
                    raise _error("currency", _("Unknown currency."))
                account.commodity = money_commodity(currency)
                account.commodity_scope = CommodityScope.MONEY
            elif data.commodity_scope in CommodityScope.values:
                account.commodity_scope = data.commodity_scope
            else:
                raise _error("commodity_scope", _("Choose what it takes."))
        account.save()
    return account


def _has_balance(account: Account) -> bool:
    return BalanceProjection.objects.filter(account=account).filter(
        ~Q(quantity=0) | ~Q(functional_amount=0)).exists()


def why_locked(account: Account) -> str:
    """Why `account` cannot be switched off, or ""."""
    from apps.expenses.models import ExpenseCategory

    if account.role:
        return _("The system posts to this account: it stays on.")
    if Account.objects.filter(holder_accounts(), pk=account.pk).exists():
        return _("This is a cash box, bank account or card terminal: manage it in Treasury.")
    if account.children.filter(is_active=True).exists():
        return _("Switch off the accounts under it first.")
    if _has_balance(account):
        return _("There is still a balance on it.")
    if ExpenseCategory.objects.filter(account=account, is_active=True).exists():
        return _("An expense category uses it.")
    return ""


def why_not_deletable(account: Account) -> str:
    """Why `account` cannot be deleted, or ""."""
    from apps.expenses.models import ExpenseCategory

    if account.role or account.template_key:
        return _("A standard account cannot be deleted; it can be renamed.")
    if Account.objects.filter(holder_accounts(), pk=account.pk).exists():
        return _("This is a cash box, bank account or card terminal: manage it in Treasury.")
    if account.children.exists():
        return _("Accounts are filed under it.")
    if JournalLine.objects.filter(account=account).exists():
        return _("Entries were posted to it: switch it off instead.")
    if ExpenseCategory.objects.filter(account=account).exists():
        return _("An expense category uses it.")
    return ""


def update_account(account_id: int, *, name: str, is_active: bool, actor=None) -> Account:
    """Rename (blank = the standard name, for a standard account) and switch on or off."""
    if actor is not None:
        actor.require(MANAGE)
    with transaction.atomic():
        account = Account.objects.select_for_update().filter(pk=account_id).first()
        if account is None:
            raise NotFound(_("Not found."))
        name = name.strip()
        if not name and not account.template_key:
            raise _error("name", _("Required."))
        if account.is_active and not is_active:
            reason = why_locked(account)
            if reason:
                raise _error("is_active", reason)
        if is_active and not account.is_active and account.parent_id \
                and not account.parent.is_active:
            raise _error("is_active", _("Switch the group it is under back on first."))
        account.name, account.is_active = name[:200], is_active
        account.save(update_fields=["name", "is_active", "updated_at"])
    return account


def delete_account(account_id: int, *, actor=None) -> None:
    if actor is not None:
        actor.require(MANAGE)
    with transaction.atomic():
        account = Account.objects.select_for_update().filter(pk=account_id).first()
        if account is None:
            raise NotFound(_("Not found."))
        reason = why_not_deletable(account)
        if reason:
            raise DomainError(reason, code="LEDGER_ACCOUNT_IN_USE")
        account.delete()


def depth_of(accounts) -> dict[int, int]:
    """How deep each account sits in the tree (0 = a top group)."""
    parents = {a.pk: a.parent_id for a in accounts}
    depths: dict[int, int] = {}

    def depth(pk):
        if pk not in depths:
            parent = parents.get(pk)
            depths[pk] = 0 if parent is None or parent not in parents else depth(parent) + 1
        return depths[pk]

    for pk in parents:
        depth(pk)
    return depths

