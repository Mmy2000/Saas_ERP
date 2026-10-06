"""Bank reconciliation (§7.9): match a bank statement against the bank account's ledger lines.

A reconciliation takes the statement's date and closing balance, then the lines that appear on
the statement are ticked. Lines ticked on earlier, completed reconciliations are "cleared"; the
cleared balance plus what is ticked now must equal the statement balance. Charges and interest
the bank booked on its own can be posted from here (they are ticked at once). Completing locks
the ticks; the latest completed reconciliation can be reopened.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.errors import DomainError, NotFound, ValidationError
from apps.core.numeric import MONEY, quantize
from apps.ledger.models import EntryKind, JournalLine
from apps.ledger.services import LineInput, account_for, money_commodity, post_entry
from apps.org.models import Branch

from .models import BankAccount, BankReconciliation, ReconciledLine

PERMISSION = "treasury.reconcile"
ZERO = Decimal(0)


def _field(name: str, message: str) -> ValidationError:
    return ValidationError(message, fields={name: [message]})


def _require(actor) -> None:
    if actor is not None:
        actor.require(PERMISSION)


def _bank(bank_id: int) -> BankAccount:
    bank = BankAccount.objects.select_related("account", "currency").filter(pk=bank_id).first()
    if bank is None:
        raise NotFound(_("Not found."))
    return bank


def _date(value) -> date:
    try:
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    except ValueError:
        raise _field("statement_date", _("Enter the statement date.")) from None


def _amount(value, field: str, *, allow_negative: bool = True) -> Decimal:
    try:
        amount = quantize(value, MONEY)
    except (TypeError, ValueError):
        raise _field(field, _("Enter a number.")) from None
    if not allow_negative and amount <= 0:
        raise _field(field, _("Must be greater than zero."))
    return amount


def last_completed(bank: BankAccount) -> BankReconciliation | None:
    return bank.reconciliations.filter(completed_at__isnull=False).order_by(
        "-statement_date", "-id").first()


def open_reconciliation(bank: BankAccount) -> BankReconciliation | None:
    return bank.reconciliations.filter(completed_at__isnull=True).first()


def candidates(reconciliation: BankReconciliation):
    """The bank account's lines up to the statement date not cleared by an earlier statement."""
    bank = reconciliation.bank_account
    done = ReconciledLine.objects.filter(reconciliation__bank_account=bank,
                                         reconciliation__completed_at__isnull=False)
    return (JournalLine.objects.filter(account_id=bank.account_id,
                                       business_date__lte=reconciliation.statement_date)
            .exclude(pk__in=done.values("journal_line_id"))
            .select_related("entry").order_by("business_date", "id"))


@dataclass
class Summary:
    opening: Decimal  # the previous statement's closing balance
    cleared_before: Decimal  # lines cleared by earlier statements
    ticked: Decimal  # lines ticked on this statement
    statement: Decimal
    book: Decimal  # the account's balance in the books on the statement date

    @property
    def cleared(self) -> Decimal:
        return self.cleared_before + self.ticked

    @property
    def difference(self) -> Decimal:
        return self.statement - self.cleared


def summary(reconciliation: BankReconciliation) -> Summary:
    bank = reconciliation.bank_account
    previous = (bank.reconciliations.filter(completed_at__isnull=False)
                .exclude(pk=reconciliation.pk).order_by("-statement_date", "-id").first())
    ticked = (reconciliation.lines.aggregate(q=Sum("journal_line__quantity"))["q"] or ZERO)
    earlier = bank.reconciliations.filter(completed_at__isnull=False).exclude(pk=reconciliation.pk)
    if reconciliation.is_completed:  # a past statement: only the ones before it
        earlier = earlier.filter(Q(statement_date__lt=reconciliation.statement_date)
                                 | Q(statement_date=reconciliation.statement_date,
                                     pk__lt=reconciliation.pk))
    cleared_before = (ReconciledLine.objects.filter(reconciliation__in=earlier)
                      .aggregate(q=Sum("journal_line__quantity"))["q"] or ZERO)
    book = (JournalLine.objects.filter(account_id=bank.account_id,
                                       business_date__lte=reconciliation.statement_date)
            .aggregate(q=Sum("quantity"))["q"] or ZERO)
    return Summary(opening=previous.statement_balance if previous else ZERO,
                   cleared_before=cleared_before, ticked=ticked,
                   statement=reconciliation.statement_balance, book=book)


def start(bank_id: int, statement_date, statement_balance, *, note: str = "",
          actor=None) -> BankReconciliation:
    """Begin reconciling a statement, or change the date and balance of the one in progress."""
    _require(actor)
    with transaction.atomic():
        bank = _bank(bank_id)
        day = _date(statement_date)
        balance = _amount(statement_balance, "statement_balance")
        previous = last_completed(bank)
        if previous is not None and day < previous.statement_date:
            raise _field("statement_date", _("The last statement reconciled was on %(day)s.")
                         % {"day": previous.statement_date.isoformat()})
        if day > timezone.localdate():
            raise _field("statement_date", _("A statement cannot be dated in the future."))
        reconciliation = open_reconciliation(bank)
        if reconciliation is None:
            try:
                with transaction.atomic():
                    reconciliation = BankReconciliation.objects.create(
                        bank_account=bank, statement_date=day, statement_balance=balance,
                        note=note.strip()[:300], created_by=getattr(actor, "user", None))
            except IntegrityError:
                raise DomainError(_("A reconciliation is already in progress."),
                                  code="RECON_IN_PROGRESS") from None
        else:
            reconciliation.statement_date = day
            reconciliation.statement_balance = balance
            reconciliation.note = note.strip()[:300]
            reconciliation.save()
            # Lines after the new date are no longer on this statement.
            reconciliation.lines.filter(journal_line__business_date__gt=day).delete()
    return reconciliation


def _locked(reconciliation_id: int) -> BankReconciliation:
    reconciliation = (BankReconciliation.objects.select_for_update(of=("self",))
                      .select_related("bank_account__account").filter(pk=reconciliation_id)
                      .first())
    if reconciliation is None:
        raise NotFound(_("Not found."))
    return reconciliation


def _in_progress(reconciliation_id: int) -> BankReconciliation:
    reconciliation = _locked(reconciliation_id)
    if reconciliation.is_completed:
        raise DomainError(_("This reconciliation is completed."), code="RECON_COMPLETED")
    return reconciliation


def tick(reconciliation_id: int, line_ids, *, actor=None) -> BankReconciliation:
    """Replace the lines ticked as on the statement."""
    _require(actor)
    with transaction.atomic():
        reconciliation = _in_progress(reconciliation_id)
        wanted = {int(pk) for pk in line_ids}
        allowed = set(candidates(reconciliation).filter(pk__in=wanted).values_list("pk",
                                                                                   flat=True))
        if wanted - allowed:
            raise _field("lines", _("Some of these lines cannot be ticked on this statement."))
        current = set(reconciliation.lines.values_list("journal_line_id", flat=True))
        reconciliation.lines.filter(journal_line_id__in=current - wanted).delete()
        ReconciledLine.objects.bulk_create([
            ReconciledLine(reconciliation=reconciliation, journal_line_id=pk)
            for pk in sorted(wanted - current)])
    return reconciliation


def add_bank_item(reconciliation_id: int, *, kind: str, amount, memo: str = "",
                  actor=None) -> BankReconciliation:
    """Book a charge or interest found on the statement, dated the statement date, and tick
    it. `kind`: "charge" (money out) or "interest" (money in)."""
    _require(actor)
    if kind not in ("charge", "interest"):
        raise _field("kind", _("Choose a bank charge or interest."))
    with transaction.atomic():
        reconciliation = _in_progress(reconciliation_id)
        bank = reconciliation.bank_account
        value = _amount(amount, "amount", allow_negative=False)
        branch = Branch.objects.filter(is_active=True).order_by("code").first()
        commodity = money_commodity(bank.currency)
        sign = 1 if kind == "interest" else -1
        other = account_for("bank_interest" if kind == "interest" else "bank_charges")
        default = _("Bank interest") if kind == "interest" else _("Bank charges")
        functional = None
        if not commodity.is_functional:
            from apps.pricing.selectors import fx_rate

            functional = (value * fx_rate(bank.currency.code)).quantize(Decimal("0.01"))
        entry = post_entry(
            branch=branch, business_date=reconciliation.statement_date, kind=EntryKind.AUTO,
            memo=(memo.strip() or default)[:300],
            lines=[LineInput(account=bank.account, commodity=commodity, quantity=sign * value,
                             functional_amount=None if functional is None else sign * functional),
                   LineInput(account=other, commodity=commodity, quantity=-sign * value,
                             functional_amount=None if functional is None else -sign * functional)],
            actor=None)
        line = entry.lines.get(account=bank.account)
        ReconciledLine.objects.create(reconciliation=reconciliation, journal_line=line)
    return reconciliation


def complete(reconciliation_id: int, *, actor=None) -> BankReconciliation:
    _require(actor)
    with transaction.atomic():
        reconciliation = _in_progress(reconciliation_id)
        figures = summary(reconciliation)
        if figures.difference != 0:
            raise DomainError(
                _("The ticked lines do not match the statement: %(difference)s left over.")
                % {"difference": figures.difference}, code="RECON_DIFFERENCE")
        reconciliation.completed_at = timezone.now()
        reconciliation.completed_by = getattr(actor, "user", None)
        reconciliation.save()
    return reconciliation


def reopen(reconciliation_id: int, *, actor=None) -> BankReconciliation:
    """Undo the latest completed reconciliation of an account, to correct it."""
    _require(actor)
    with transaction.atomic():
        reconciliation = _locked(reconciliation_id)
        bank = reconciliation.bank_account
        if not reconciliation.is_completed or last_completed(bank) != reconciliation:
            raise DomainError(_("Only the latest completed reconciliation can be reopened."),
                              code="RECON_NOT_LATEST")
        if open_reconciliation(bank) is not None:
            raise DomainError(_("Finish or delete the reconciliation in progress first."),
                              code="RECON_IN_PROGRESS")
        reconciliation.completed_at = None
        reconciliation.completed_by = None
        reconciliation.save()
    return reconciliation


def discard(reconciliation_id: int, *, actor=None) -> None:
    """Throw away a reconciliation in progress (its ticks; entries it booked stay)."""
    _require(actor)
    with transaction.atomic():
        reconciliation = _in_progress(reconciliation_id)
        reconciliation.lines.all().delete()
        reconciliation.delete()
