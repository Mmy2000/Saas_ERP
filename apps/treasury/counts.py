"""Cash counts (§7.9): count a cash box (usually when the day closes), compare with the books,
and book the difference so the box holds what was counted.

shortage   Dr cash over and short     Cr the cash box
surplus    Dr the cash box            Cr cash over and short

Counting by denomination is optional: the notes and coins counted are kept with the count.
Only the latest count of a box can be cancelled (its entry is reversed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.errors import DomainError, NotFound, ValidationError
from apps.core.models import DocStatus
from apps.core.numeric import MONEY, quantize, round_money
from apps.core.sequences import allocate_number
from apps.ledger.models import EntryKind
from apps.ledger.services import LineInput, account_for, money_commodity, post_entry, reverse_entry
from apps.pricing.selectors import functional_currency
from apps.pricing.selectors import fx_rate as rate_in_force

from .holders import balance, outflow_value
from .models import CashBox, CashCount

DOC_TYPE = "treasury.CashCount"
ZERO = Decimal(0)

# Notes and coins offered on the count screen, largest first. Other currencies are counted as a
# total.
DENOMINATIONS = {
    "EGP": ("200", "100", "50", "20", "10", "5", "1", "0.5", "0.25"),
    "USD": ("100", "50", "20", "10", "5", "2", "1"),
    "EUR": ("500", "200", "100", "50", "20", "10", "5", "2", "1"),
    "SAR": ("500", "200", "100", "50", "10", "5", "1"),
    "AED": ("1000", "500", "200", "100", "50", "20", "10", "5", "1"),
    "KWD": ("20", "10", "5", "1", "0.5", "0.25"),
    "GBP": ("50", "20", "10", "5", "2", "1"),
}


def _field(name: str, message: str) -> ValidationError:
    return ValidationError(message, fields={name: [message]})


@dataclass(frozen=True)
class CountInput:
    cash_box_id: int
    counted: Decimal | str | None = None  # the total, when not counted by denomination
    denominations: dict[str, int] = field(default_factory=dict)
    note: str = ""


def _total(currency_code: str, denominations: dict) -> tuple[Decimal, dict[str, int]]:
    allowed = DENOMINATIONS.get(currency_code, ())
    kept, total = {}, ZERO
    for key, count in denominations.items():
        if key not in allowed:
            raise _field("denominations", _("Unknown note or coin: %(value)s.") % {"value": key})
        try:
            pieces = int(count or 0)
        except (TypeError, ValueError):
            raise _field("denominations", _("Enter how many of each.")) from None
        if pieces < 0:
            raise _field("denominations", _("Must not be negative."))
        if pieces:
            kept[key] = pieces
            total += Decimal(key) * pieces
    return quantize(total, MONEY), kept


def record_count(data: CountInput, *, actor=None) -> CashCount:
    with transaction.atomic():
        box = (CashBox.objects.select_for_update(of=("self",))
               .select_related("branch", "currency", "account")
               .filter(pk=data.cash_box_id, is_active=True).first())
        if box is None:
            raise _field("cash_box", _("Choose an active cash box."))
        if actor is not None:
            actor.require("treasury.count.create", branch=box.branch)
        denominations: dict[str, int] = {}
        if data.denominations:
            counted, denominations = _total(box.currency.code, data.denominations)
        else:
            try:
                counted = quantize(data.counted, MONEY)
            except (TypeError, ValueError, InvalidOperation):
                raise _field("counted", _("Enter the amount counted.")) from None
        if counted < 0:
            raise _field("counted", _("Must not be negative."))
        expected = quantize(balance(box.account_id)[0], MONEY)
        difference = counted - expected
        if difference and not data.note.strip():
            raise _field("note", _("Say what explains the difference."))

        today = timezone.localdate()
        count = CashCount.objects.create(
            branch=box.branch, cash_box=box, business_date=today, expected=expected,
            counted=counted, difference=difference, denominations=denominations,
            note=data.note.strip(), created_by=getattr(actor, "user", None))
        count.number = allocate_number("CC", branch=box.branch, fiscal_year=today.year)
        if difference:
            amount = abs(difference)
            if box.currency.code == functional_currency():
                value = amount
            elif difference < 0:  # money leaves the box at what it was carried at
                value = outflow_value(box.account_id, box.currency, amount)
            else:
                value = round_money(amount * rate_in_force(box.currency.code))
            sign = 1 if difference > 0 else -1
            commodity = money_commodity(box.currency)
            count.journal_entry = post_entry(
                branch=box.branch, business_date=today, kind=EntryKind.AUTO,
                source_type=DOC_TYPE, source_id=count.pk,
                memo=(_("Cash surplus at %(box)s") if sign > 0 else _("Cash shortage at %(box)s"))
                % {"box": box.label},
                lines=[LineInput(account=box.account, commodity=commodity, quantity=sign * amount,
                                 functional_amount=sign * value),
                       LineInput(account=account_for("cash_over_short"), commodity=commodity,
                                 quantity=-sign * amount, functional_amount=-sign * value)])
        count.status = DocStatus.POSTED
        count.posted_at = timezone.now()
        count.posted_by = getattr(actor, "user", None)
        count.save()
    return count


def void_count(count_id: int, *, reason: str = "", actor=None) -> CashCount:
    with transaction.atomic():
        count = (CashCount.objects.select_for_update(of=("self",))
                 .select_related("cash_box", "branch").filter(pk=count_id).first())
        if count is None:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("treasury.count.void", branch=count.branch)
        if count.status != DocStatus.POSTED:
            raise DomainError(_("Only posted documents can be cancelled."), code="DOC_NOT_POSTED")
        latest = (CashCount.objects.filter(cash_box=count.cash_box, status=DocStatus.POSTED)
                  .order_by("-posted_at", "-id").first())
        if latest != count:
            raise DomainError(_("Only the latest count of a cash box can be cancelled."),
                              code="COUNT_NOT_LATEST")
        if count.journal_entry_id:
            reverse_entry(count.journal_entry_id, business_date=timezone.localdate(),
                          memo=_("Cancelled %(number)s") % {"number": count.number})
        count.status = DocStatus.VOIDED
        count.voided_at = timezone.now()
        count.voided_by = getattr(actor, "user", None)
        count.void_reason = reason.strip()[:300]
        count.save()
    return count
