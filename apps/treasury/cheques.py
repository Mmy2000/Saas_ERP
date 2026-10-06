"""Cheques received and issued (§7.9). Legacy: the cheque columns of `Cu2` / `Suu2`.

received   Dr cheques received   Cr the party            the cheque is in hand
deposit    (no entry)                                    taken to a bank account to collect
clear      Dr bank account       Cr cheques received     the bank paid it
bounce     Dr the party          Cr cheques received     unpaid: the party owes the money again
           (Cr the bank account instead, when it bounces after clearing)
return     Dr the party          Cr cheques received     handed back to the drawer
endorse    Dr the new holder     Cr cheques received     passed on, e.g. to pay a supplier

issued     Dr the party          Cr cheques payable      given out, drawn on our bank account
clear      Dr cheques payable    Cr bank account         the bank paid it
bounce     Dr cheques payable    Cr the party            not paid: we owe the party again

A cheque recorded by mistake is cancelled while nothing else happened to it (the entry is
reversed). Cheques are in the company currency.
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
from apps.core.numeric import MONEY, quantize
from apps.core.sequences import allocate_number
from apps.ledger.models import EntryKind
from apps.ledger.services import LineInput, account_for, money_commodity, post_entry, reverse_entry
from apps.org.models import Branch
from apps.parties.models import Party
from apps.pricing.selectors import functional_currency

from .holders import TenderKind, resolve_tender
from .models import BankAccount, Cheque, ChequeDirection, ChequeStatus

DOC_TYPE = "treasury.Cheque"
MANAGE = "treasury.cheque.manage"
# The party's account for each side (as in settlements).
PARTY_ACCOUNT = {"customer": "customers", "supplier": "suppliers",
                 "trade_account": "trade_accounts", "workshop": "workshops"}


def _field(name: str, message: str) -> ValidationError:
    return ValidationError(message, fields={name: [message]})


@dataclass(frozen=True)
class ChequeInput:
    direction: str
    side: str
    party_id: int | None
    branch_id: int
    cheque_number: str
    due_date: date | str | None
    amount: Decimal | str | None
    drawn_on: str = ""  # the drawer's bank, for a cheque received
    bank_account_id: int | None = None  # the account an issued cheque is drawn on
    note: str = ""


def _party(side: str, party_id, field: str = "party") -> Party:
    if side not in PARTY_ACCOUNT:
        raise _field("side", _("Choose who the cheque is with."))
    party = Party.objects.filter(pk=party_id, is_active=True, roles__role=side).first()
    if party is None:
        raise _field(field, _("Choose an active account."))
    return party


def _bank(branch: Branch, currency: Currency, bank_account_id) -> BankAccount:
    if not bank_account_id:
        raise _field("bank_account", _("Choose the bank account."))
    return resolve_tender(TenderKind.BANK_TRANSFER, branch, currency,
                          bank_account_id=bank_account_id).bank_account


def _post(cheque: Cheque, debit, credit, *, debit_party=None, credit_party=None, memo: str):
    home = money_commodity(cheque.currency)
    return post_entry(
        branch=cheque.branch, business_date=timezone.localdate(), kind=EntryKind.AUTO,
        source_type=DOC_TYPE, source_id=cheque.pk, memo=memo,
        lines=[LineInput(account=debit, commodity=home, quantity=cheque.amount,
                         party=debit_party),
               LineInput(account=credit, commodity=home, quantity=-cheque.amount,
                         party=credit_party)])


def record_cheque(data: ChequeInput, *, actor=None) -> Cheque:
    if data.direction not in ChequeDirection.values:
        raise _field("direction", _("Choose received or issued."))
    with transaction.atomic():
        branch = Branch.objects.filter(pk=data.branch_id, is_active=True).first()
        if branch is None:
            raise _field("branch", _("Unknown branch."))
        if actor is not None:
            actor.require(MANAGE, branch=branch)
        party = _party(data.side, data.party_id)
        number = data.cheque_number.strip()
        if not number:
            raise _field("cheque_number", _("Enter the cheque number."))
        try:
            due = data.due_date if isinstance(data.due_date, date) else date.fromisoformat(
                str(data.due_date))
        except ValueError:
            raise _field("due_date", _("Enter the date on the cheque.")) from None
        try:
            amount = quantize(data.amount, MONEY)
        except (TypeError, ValueError):
            raise _field("amount", _("Enter a number.")) from None
        if amount <= 0:
            raise _field("amount", _("Must be greater than zero."))
        currency = Currency.objects.get(code=functional_currency())
        received = data.direction == ChequeDirection.RECEIVED
        bank = None if received else _bank(branch, currency, data.bank_account_id)

        today = timezone.localdate()
        cheque = Cheque.objects.create(
            direction=data.direction, side=data.side, party=party, branch=branch,
            business_date=today, cheque_number=number[:40], drawn_on=data.drawn_on.strip()[:100],
            due_date=due, currency=currency, amount=amount, bank_account=bank,
            state=ChequeStatus.IN_HAND if received else ChequeStatus.OUTSTANDING, state_on=today,
            note=data.note.strip(), created_by=getattr(actor, "user", None))
        cheque.number = allocate_number("CHR" if received else "CHI", branch=branch,
                                        fiscal_year=today.year)
        party_account = account_for(PARTY_ACCOUNT[data.side])
        if received:
            entry = _post(cheque, account_for("cheques_received"), party_account,
                          credit_party=party,
                          memo=_("Cheque %(number)s received") % {"number": number})
        else:
            entry = _post(cheque, party_account, account_for("cheques_payable"),
                          debit_party=party,
                          memo=_("Cheque %(number)s issued") % {"number": number})
        cheque.journal_entry = entry
        cheque.status = DocStatus.POSTED
        cheque.posted_at = timezone.now()
        cheque.posted_by = getattr(actor, "user", None)
        cheque.save()
    return cheque


def _locked(cheque_id: int, actor) -> Cheque:
    cheque = (Cheque.objects.select_for_update(of=("self",))
              .select_related("branch", "party", "currency", "bank_account__account")
              .filter(pk=cheque_id).first())
    if cheque is None:
        raise NotFound(_("Not found."))
    if actor is not None:
        actor.require(MANAGE, branch=cheque.branch)
    if cheque.status != DocStatus.POSTED:
        raise DomainError(_("This cheque was cancelled."), code="CHEQUE_CANCELLED")
    return cheque


def _expect(cheque: Cheque, *states) -> None:
    if cheque.state not in states:
        raise DomainError(_("Not possible for a cheque that is %(state)s.")
                          % {"state": cheque.get_state_display().lower()},
                          code="CHEQUE_WRONG_STATE")


def _move(cheque: Cheque, state: str, entry=None, reason: str = "", actor=None) -> Cheque:
    cheque.state = state
    cheque.state_on = timezone.localdate()
    if entry is not None:
        cheque.settle_entry = entry
    if reason.strip():
        cheque.void_reason = reason.strip()[:300]
    cheque.updated_by = getattr(actor, "user", None)
    cheque.save()
    return cheque


def deposit_cheque(cheque_id: int, bank_account_id: int, *, actor=None) -> Cheque:
    """A received cheque goes to the bank to be collected. Nothing is booked until it clears."""
    with transaction.atomic():
        cheque = _locked(cheque_id, actor)
        if cheque.direction != ChequeDirection.RECEIVED:
            raise DomainError(_("Only cheques received are deposited."), code="CHEQUE_ISSUED")
        _expect(cheque, ChequeStatus.IN_HAND)
        cheque.bank_account = _bank(cheque.branch, cheque.currency, bank_account_id)
        return _move(cheque, ChequeStatus.DEPOSITED, actor=actor)


def clear_cheque(cheque_id: int, *, bank_account_id: int | None = None, actor=None) -> Cheque:
    """The bank paid the cheque: into our account (received) or out of it (issued)."""
    with transaction.atomic():
        cheque = _locked(cheque_id, actor)
        number = {"number": cheque.cheque_number}
        if cheque.direction == ChequeDirection.RECEIVED:
            _expect(cheque, ChequeStatus.IN_HAND, ChequeStatus.DEPOSITED)
            if cheque.bank_account is None or bank_account_id:
                cheque.bank_account = _bank(cheque.branch, cheque.currency, bank_account_id)
            entry = _post(cheque, cheque.bank_account.account, account_for("cheques_received"),
                          memo=_("Cheque %(number)s cleared") % number)
        else:
            _expect(cheque, ChequeStatus.OUTSTANDING)
            entry = _post(cheque, account_for("cheques_payable"), cheque.bank_account.account,
                          memo=_("Cheque %(number)s paid by the bank") % number)
        return _move(cheque, ChequeStatus.CLEARED, entry, actor=actor)


def bounce_cheque(cheque_id: int, *, reason: str = "", actor=None) -> Cheque:
    """Unpaid: the debt is back on the party's account."""
    with transaction.atomic():
        cheque = _locked(cheque_id, actor)
        party_account = account_for(PARTY_ACCOUNT[cheque.side])
        number = {"number": cheque.cheque_number}
        memo = _("Cheque %(number)s bounced") % number
        if cheque.direction == ChequeDirection.RECEIVED:
            _expect(cheque, ChequeStatus.IN_HAND, ChequeStatus.DEPOSITED, ChequeStatus.CLEARED)
            # Bounced after clearing: the bank takes the money back out of our account.
            credit = (cheque.bank_account.account if cheque.state == ChequeStatus.CLEARED
                      else account_for("cheques_received"))
            entry = _post(cheque, party_account, credit, debit_party=cheque.party, memo=memo)
        else:
            _expect(cheque, ChequeStatus.OUTSTANDING)
            entry = _post(cheque, account_for("cheques_payable"), party_account,
                          credit_party=cheque.party, memo=memo)
        return _move(cheque, ChequeStatus.BOUNCED, entry, reason, actor)


def return_cheque(cheque_id: int, *, reason: str = "", actor=None) -> Cheque:
    """A received cheque handed back to the drawer (replaced, or paid another way)."""
    with transaction.atomic():
        cheque = _locked(cheque_id, actor)
        if cheque.direction != ChequeDirection.RECEIVED:
            raise DomainError(_("Only cheques received can be handed back."),
                              code="CHEQUE_ISSUED")
        _expect(cheque, ChequeStatus.IN_HAND)
        entry = _post(cheque, account_for(PARTY_ACCOUNT[cheque.side]),
                      account_for("cheques_received"), debit_party=cheque.party,
                      memo=_("Cheque %(number)s handed back") % {"number": cheque.cheque_number})
        return _move(cheque, ChequeStatus.RETURNED, entry, reason, actor)


def endorse_cheque(cheque_id: int, *, side: str, party_id: int, actor=None) -> Cheque:
    """Pass a received cheque on, e.g. to pay a supplier: their balance goes down by it."""
    with transaction.atomic():
        cheque = _locked(cheque_id, actor)
        if cheque.direction != ChequeDirection.RECEIVED:
            raise DomainError(_("Only cheques received can be endorsed."), code="CHEQUE_ISSUED")
        _expect(cheque, ChequeStatus.IN_HAND)
        holder = _party(side, party_id)
        if holder.pk == cheque.party_id and side == cheque.side:
            raise _field("party", _("Choose someone other than the drawer."))
        entry = _post(cheque, account_for(PARTY_ACCOUNT[side]), account_for("cheques_received"),
                      debit_party=holder,
                      memo=_("Cheque %(number)s endorsed to %(name)s")
                      % {"number": cheque.cheque_number, "name": holder.name})
        cheque.endorsed_side, cheque.endorsed_to = side, holder
        return _move(cheque, ChequeStatus.ENDORSED, entry, actor=actor)


def cancel_cheque(cheque_id: int, *, reason: str = "", actor=None) -> Cheque:
    """Recorded by mistake: undo it, while nothing else has happened to it."""
    with transaction.atomic():
        cheque = _locked(cheque_id, actor)
        _expect(cheque, ChequeStatus.IN_HAND, ChequeStatus.DEPOSITED, ChequeStatus.OUTSTANDING)
        reverse_entry(cheque.journal_entry_id, business_date=timezone.localdate(),
                      memo=_("Cancelled %(number)s") % {"number": cheque.number})
        cheque.status = DocStatus.VOIDED
        cheque.voided_at = timezone.now()
        cheque.voided_by = getattr(actor, "user", None)
        cheque.void_reason = reason.strip()[:300]
        cheque.save()
    return cheque
