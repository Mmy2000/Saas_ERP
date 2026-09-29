"""Treasury documents (§7.9): posting and cancelling.

* transfer         Dr destination        Cr source            (same currency; deposit, withdrawal,
                                                                bank to bank, box to box)
  to a box in another branch, in two steps through branch clearing (§15):
                   send:    Dr clearing (source branch)       Cr source box
                   receive: Dr destination box                Cr clearing (destination branch)
* exchange         Dr box bought into    Cr box sold from     (the difference to the carrying
                                                                value is an exchange gain/loss)
* card settlement  Dr bank (net)  Dr card fees (fee)  Cr terminal (gross)

Foreign money leaving a holder is valued at its average carrying value (holders.outflow_value).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.errors import DomainError, NotFound, ValidationError
from apps.core.models import DocStatus
from apps.core.numeric import FX_RATE, MONEY, quantize, round_money
from apps.core.sequences import allocate_number
from apps.ledger.models import EntryKind
from apps.ledger.services import LineInput as LedgerLine
from apps.ledger.services import (
    account_for,
    functional_commodity,
    money_commodity,
    post_entry,
    reverse_entry,
)
from apps.org.models import Branch
from apps.pricing.selectors import functional_currency

from .holders import balance, banks_usable_at, ensure_cash_available, outflow_value
from .models import BankAccount, CardTerminal, CashBox, TreasuryDocument, TreasuryKind

DOC_TYPE = "treasury.TreasuryDocument"
PREFIXES = {TreasuryKind.TRANSFER: "TR", TreasuryKind.EXCHANGE: "FX",
            TreasuryKind.CARD_SETTLEMENT: "CS"}
PERMISSIONS = {TreasuryKind.TRANSFER: "treasury.transfer.create",
               TreasuryKind.EXCHANGE: "treasury.exchange.create",
               TreasuryKind.CARD_SETTLEMENT: "treasury.card_settlement.create"}
ZERO = Decimal(0)


@dataclass(frozen=True)
class TreasuryInput:
    kind: str
    source_box_id: int | None = None
    source_bank_id: int | None = None
    source_terminal_id: int | None = None
    dest_box_id: int | None = None
    dest_bank_id: int | None = None
    branch_id: int | None = None  # for documents between bank accounts / from a terminal
    amount: Decimal | str | None = None
    dest_amount: Decimal | str | None = None  # exchanges
    fee_amount: Decimal | str | None = None  # card settlements; default: terminal fee rate
    reference: str = ""
    note: str = ""


def _field_error(field: str, message: str) -> ValidationError:
    return ValidationError(_("Please correct the highlighted fields."), fields={field: [message]})


def _money(value, field: str, *, allow_zero: bool = False) -> Decimal:
    try:
        number = quantize(value, MONEY)
    except (TypeError, ValueError):
        raise _field_error(field, _("Enter a number.")) from None
    if number < 0 or (number == 0 and not allow_zero):
        raise _field_error(field, _("Must be greater than zero."))
    return number


def _active(model, pk, field, **filters):
    if not pk:
        return None
    holder = model.objects.select_related("account").filter(pk=pk, is_active=True,
                                                             **filters).first()
    if holder is None:
        raise _field_error(field, _("Not available."))
    return holder


def _branch(pk) -> Branch:
    branch = Branch.objects.filter(pk=pk, is_active=True).first() if pk else None
    if branch is None:
        raise _field_error("branch", _("Unknown branch."))
    return branch


def _usable_bank(bank: BankAccount | None, branch: Branch, field: str) -> None:
    if bank is not None and not banks_usable_at(branch).filter(pk=bank.pk).exists():
        raise _field_error(field, _("Not available at this branch in this currency."))


def _prepare(data: TreasuryInput, actor) -> TreasuryDocument:
    if data.kind not in TreasuryKind.values:
        raise _field_error("kind", _("Choose what is being recorded."))
    source_box = _active(CashBox, data.source_box_id, "source")
    source_bank = _active(BankAccount, data.source_bank_id, "source")
    terminal = _active(CardTerminal, data.source_terminal_id, "source")
    dest_box = _active(CashBox, data.dest_box_id, "destination")
    dest_bank = _active(BankAccount, data.dest_bank_id, "destination")

    doc = TreasuryDocument(kind=data.kind, source_box=source_box, source_bank=source_bank,
                           source_terminal=terminal, dest_box=dest_box, dest_bank=dest_bank,
                           reference=data.reference.strip()[:60], note=data.note.strip(),
                           created_by=getattr(actor, "user", None))
    sources = [h for h in (source_box, source_bank, terminal) if h is not None]
    destinations = [h for h in (dest_box, dest_bank) if h is not None]
    if len(sources) != 1:
        raise _field_error("source", _("Choose where the money comes from."))

    if data.kind == TreasuryKind.CARD_SETTLEMENT:  # paid into the terminal's bank account
        if terminal is None:
            raise _field_error("source", _("Choose the card terminal."))
        doc.dest_box, doc.dest_bank = None, terminal.bank_account
        doc.branch = terminal.branch or _branch(data.branch_id)
        doc.to_branch = doc.branch
        doc.currency = doc.dest_currency = terminal.bank_account.currency
        doc.amount = _money(data.amount, "amount")
        outstanding, _value = balance(terminal.account_id)
        if doc.amount > outstanding:
            raise _field_error("amount", _("More than the terminal's unsettled %(amount)s.")
                               % {"amount": outstanding})
        doc.fee_amount = (round_money(doc.amount * terminal.fee_rate)
                          if data.fee_amount in (None, "") else
                          _money(data.fee_amount, "fee_amount", allow_zero=True))
        if doc.fee_amount >= doc.amount:
            raise _field_error("fee_amount", _("The fee must be less than the amount."))
        doc.dest_amount = doc.amount - doc.fee_amount
        return doc

    if terminal is not None:
        raise _field_error("source", _("Card terminals are settled with a card settlement."))
    if len(destinations) != 1:
        raise _field_error("destination", _("Choose where the money goes."))
    source, destination = sources[0], destinations[0]
    if source.account_id == destination.account_id:
        raise _field_error("destination", _("Choose a different destination."))
    doc.currency = source.currency
    doc.dest_currency = destination.currency
    doc.amount = _money(data.amount, "amount")
    if source_box is not None:
        ensure_cash_available(source_box, doc.amount, "amount")

    # Branch: a box belongs to one; a bank-to-bank transfer is booked where the actor says.
    doc.branch = (source_box.branch if source_box else
                  dest_box.branch if dest_box else _branch(data.branch_id))
    doc.to_branch = dest_box.branch if dest_box else doc.branch
    _usable_bank(source_bank, doc.branch, "source")
    _usable_bank(dest_bank, doc.to_branch, "destination")

    if data.kind == TreasuryKind.TRANSFER:
        if doc.currency.pk != doc.dest_currency.pk:
            raise _field_error("destination",
                               _("Use a currency exchange to change money into another currency."))
        doc.dest_amount = doc.amount
        doc.needs_receipt = bool(source_box and dest_box
                                 and source_box.branch_id != dest_box.branch_id)
    else:  # exchange: between two boxes of one branch
        if not (source_box and dest_box) or source_box.branch_id != dest_box.branch_id:
            raise _field_error("destination",
                               _("Exchange between two cash boxes of the same branch."))
        if doc.currency.pk == doc.dest_currency.pk:
            raise _field_error("destination", _("Choose a box in another currency."))
        doc.dest_amount = _money(data.dest_amount, "dest_amount")
        doc.rate = quantize(doc.dest_amount / doc.amount, FX_RATE)
    return doc


def _post_lines(doc: TreasuryDocument) -> list[LedgerLine]:
    source_account = doc.source.account
    value = outflow_value(source_account.pk, doc.currency, doc.amount)
    doc.functional_amount = value
    source_money = money_commodity(doc.currency)
    lines = [LedgerLine(account=source_account, commodity=source_money, quantity=-doc.amount,
                        functional_amount=-value)]

    if doc.kind == TreasuryKind.CARD_SETTLEMENT:
        net_value = value if not doc.fee_amount else round_money(
            value * doc.dest_amount / doc.amount)
        lines.append(LedgerLine(account=doc.dest_bank.account, commodity=source_money,
                                quantity=doc.dest_amount, functional_amount=net_value))
        if doc.fee_amount:
            home = functional_commodity()
            lines.append(LedgerLine(account=account_for("card_fees"), commodity=home,
                                    quantity=value - net_value))
        return lines

    if doc.needs_receipt:
        lines.append(LedgerLine(account=account_for("branch_clearing"), commodity=source_money,
                                quantity=doc.amount, functional_amount=value))
        return lines

    dest_money = money_commodity(doc.dest_currency)
    if doc.kind == TreasuryKind.EXCHANGE:
        home = functional_currency()
        # The side in the company currency fixes the value; between two foreign currencies the
        # money bought is worth what the money sold was carried at.
        bought = (doc.dest_amount if doc.dest_currency.code == home
                  else doc.amount if doc.currency.code == home else value)
        lines.append(LedgerLine(account=doc.destination.account, commodity=dest_money,
                                quantity=doc.dest_amount, functional_amount=bought))
        difference = bought - value
        if difference:
            role = "fx_gain" if difference > 0 else "fx_loss"
            lines.append(LedgerLine(account=account_for(role), commodity=functional_commodity(),
                                    quantity=-difference))
        return lines

    lines.append(LedgerLine(account=doc.destination.account, commodity=dest_money,
                            quantity=doc.dest_amount, functional_amount=value,
                            branch=doc.to_branch))
    return lines


def _memo(doc: TreasuryDocument) -> str:
    return f"{doc.title} {doc.number}"


def post_treasury_document(data: TreasuryInput, *, actor=None) -> TreasuryDocument:
    with transaction.atomic():
        doc = _prepare(data, actor)
        if actor is not None:
            actor.require(PERMISSIONS[doc.kind], branch=doc.branch)
        today = timezone.localdate()
        doc.business_date = today
        lines = _post_lines(doc)
        doc.save()
        doc.number = allocate_number(PREFIXES[doc.kind], branch=doc.branch,
                                     fiscal_year=today.year)
        doc.journal_entry = post_entry(branch=doc.branch, business_date=today, lines=lines,
                                       kind=EntryKind.AUTO, source_type=DOC_TYPE,
                                       source_id=doc.pk, memo=_memo(doc))
        doc.status = DocStatus.POSTED
        doc.posted_at = timezone.now()
        doc.posted_by = getattr(actor, "user", None)
        doc.save()
    return doc


def _locked(doc_id: int) -> TreasuryDocument:
    doc = (TreasuryDocument.objects.select_for_update(of=("self",))
           .select_related("branch", "to_branch", "currency", "dest_box__account")
           .filter(pk=doc_id).first())
    if doc is None:
        raise NotFound(_("Not found."))
    return doc


def receive_transfer(doc_id: int, *, actor=None) -> TreasuryDocument:
    """The destination branch confirms the cash arrived."""
    with transaction.atomic():
        doc = _locked(doc_id)
        if actor is not None:
            actor.require("treasury.transfer.receive", branch=doc.to_branch)
        if not doc.in_transit:
            raise DomainError(_("This transfer is not waiting to be received."),
                              code="TREASURY_NOT_IN_TRANSIT")
        if not doc.dest_box.is_active:
            raise DomainError(_("The receiving cash box is inactive."),
                              code="TREASURY_HOLDER_INACTIVE")
        today = timezone.localdate()
        money = money_commodity(doc.currency)
        doc.receive_entry = post_entry(
            branch=doc.to_branch, business_date=today, kind=EntryKind.AUTO,
            source_type=DOC_TYPE, source_id=doc.pk,
            memo=_("Received %(number)s") % {"number": doc.number},
            lines=[LedgerLine(account=doc.dest_box.account, commodity=money,
                              quantity=doc.amount, functional_amount=doc.functional_amount),
                   LedgerLine(account=account_for("branch_clearing"), commodity=money,
                              quantity=-doc.amount, functional_amount=-doc.functional_amount)],
        )
        doc.received_at = timezone.now()
        doc.received_by = getattr(actor, "user", None)
        doc.save()
    return doc


def void_treasury_document(doc_id: int, *, reason: str = "", actor=None) -> TreasuryDocument:
    with transaction.atomic():
        doc = _locked(doc_id)
        if actor is not None:
            actor.require("treasury.void", branch=doc.branch)
        if doc.status != DocStatus.POSTED:
            raise DomainError(_("Only posted documents can be cancelled."), code="DOC_NOT_POSTED")
        today = timezone.localdate()
        memo = _("Cancelled %(number)s") % {"number": doc.number}
        for entry_id in (doc.receive_entry_id, doc.journal_entry_id):
            if entry_id:
                reverse_entry(entry_id, business_date=today, memo=memo)
        doc.status = DocStatus.VOIDED
        doc.voided_at = timezone.now()
        doc.voided_by = getattr(actor, "user", None)
        doc.void_reason = reason.strip()[:300]
        doc.save()
    return doc
