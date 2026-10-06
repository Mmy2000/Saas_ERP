"""Closing months and financial years (§7.10).

A closed month takes no postings (post_entry checks every entry's date) and none of its entries
can be reversed, so no document dated in it can be cancelled: its figures stay as they were
when it was closed. Months close in order and reopen in reverse order, with a reason.

Closing a year moves the balance of every income and expense account (money and metal) to
retained earnings with one "closing" entry dated the last day of the year; reopening the year
reverses that entry. A year closes once all its months are closed.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Count, Min, Q, Sum
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.errors import DomainError, PermissionDenied, ValidationError
from apps.core.models import DocStatus
from apps.org.models import Branch

from .models import (
    AccountType,
    EntryKind,
    FiscalPeriod,
    JournalEntry,
    JournalLine,
    PeriodAction,
    PeriodEvent,
    PeriodStatus,
    YearEnd,
)
from .services import LineInput, account_for, functional_commodity, post_entry, reverse_entry

ZERO = Decimal(0)
PROFIT_AND_LOSS = (AccountType.INCOME, AccountType.EXPENSE)


def month_bounds(year: int, month: int) -> tuple[date, date]:
    if not (1 <= month <= 12) or not (2000 <= year <= 2200):
        raise ValidationError(_("Choose a month."), fields={"period": [_("Choose a month.")]})
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _next(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _previous(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _require(actor, code: str) -> None:
    """Closing is for the whole business, so the permission must cover every branch."""
    if actor is None:
        return
    actor.require(code)
    if actor.branch_ids(code) is not None:
        raise PermissionDenied(_("This needs the permission for all branches."),
                               code="PERMISSION_DENIED")


def first_month() -> tuple[int, int] | None:
    """The month of the first posting: months before it have nothing to close."""
    first = JournalEntry.objects.aggregate(first=Min("business_date"))["first"]
    return (first.year, first.month) if first else None


def _closed_starts() -> set[date]:
    return set(FiscalPeriod.objects.filter(status=PeriodStatus.CLOSED)
               .values_list("start_date", flat=True))


def _active_year_end(year: int) -> YearEnd | None:
    return YearEnd.objects.filter(year=year, reopened_at__isnull=True).first()


# --- figures -------------------------------------------------------------------------------------

@dataclass
class Figures:
    income: Decimal = ZERO
    expenses: Decimal = ZERO
    entries: int = 0

    @property
    def profit(self) -> Decimal:
        return self.income - self.expenses


def figures(start: date, end: date) -> Figures:
    """Income, expenses (company currency) and the number of entries between two dates. The
    year-end closing entry is left out, or every closed year would show no profit."""
    lines = JournalLine.objects.filter(business_date__gte=start, business_date__lte=end).exclude(
        entry__kind=EntryKind.CLOSING)
    sums = lines.filter(account__type__in=PROFIT_AND_LOSS).aggregate(
        income=Sum("functional_amount", filter=Q(account__type=AccountType.INCOME)),
        expenses=Sum("functional_amount", filter=Q(account__type=AccountType.EXPENSE)))
    entries = JournalEntry.objects.filter(business_date__gte=start, business_date__lte=end).exclude(
        kind=EntryKind.CLOSING).aggregate(n=Count("id"))["n"]
    return Figures(income=-(sums["income"] or ZERO), expenses=sums["expenses"] or ZERO,
                   entries=entries)


# --- the months of a year ------------------------------------------------------------------------

class MonthState:
    CLOSED = "closed"
    OPEN = "open"          # over, can be closed
    CURRENT = "current"    # still running
    FUTURE = "future"
    BEFORE = "before"      # before the first posting: nothing to close


@dataclass
class Month:
    year: int
    month: int
    start: date
    end: date
    state: str
    period: FiscalPeriod | None
    figures: Figures | None = None
    can_close: bool = False   # the next month to close
    can_reopen: bool = False  # the last closed month

    @property
    def key(self) -> str:
        return f"{self.year}-{self.month:02d}"


def _state(start: date, end: date, closed: set[date], first: tuple[int, int] | None) -> str:
    today = timezone.localdate()
    if start in closed:
        return MonthState.CLOSED
    if start > today:
        return MonthState.FUTURE
    if end >= today:
        return MonthState.CURRENT
    if first is None or (start.year, start.month) < first:
        return MonthState.BEFORE
    return MonthState.OPEN


def next_to_close() -> tuple[int, int] | None:
    """The earliest month after the first posting that is over and still open."""
    first = first_month()
    if first is None:
        return None
    closed = _closed_starts()
    year, month = first
    today = timezone.localdate()
    while True:
        start, end = month_bounds(year, month)
        if end >= today:
            return None
        if start not in closed:
            return year, month
        year, month = _next(year, month)


def last_closed() -> tuple[int, int] | None:
    latest = FiscalPeriod.objects.filter(status=PeriodStatus.CLOSED).order_by("-start_date").first()
    return (latest.start_date.year, latest.start_date.month) if latest else None


def months_of(year: int) -> list[Month]:
    closed = _closed_starts()
    first = first_month()
    periods = {p.start_date: p for p in FiscalPeriod.objects.filter(
        start_date__year=year).select_related("closed_by")}
    upcoming, latest = next_to_close(), last_closed()
    year_closed = _active_year_end(year) is not None
    months = []
    for number in range(1, 13):
        start, end = month_bounds(year, number)
        state = _state(start, end, closed, first)
        item = Month(year, number, start, end, state, periods.get(start))
        if state not in (MonthState.FUTURE, MonthState.BEFORE):
            item.figures = figures(start, end)
        item.can_close = upcoming == (year, number)
        item.can_reopen = latest == (year, number) and not year_closed
        months.append(item)
    return months


def years() -> list[int]:
    """Years from the first posting to this one, newest first."""
    today = timezone.localdate()
    first = first_month()
    start = first[0] if first else today.year
    return list(range(today.year, start - 1, -1))


# --- what is still open in a month ---------------------------------------------------------------

@dataclass
class Check:
    label: str
    count: int
    url: str
    icon: str


def checklist(year: int, month: int, actor=None) -> list[Check]:
    """Work dated in or before the month that is still unfinished. None of it blocks closing,
    but it is worth finishing first: once the month is closed it cannot be dated into it."""
    _start, end = month_bounds(year, month)
    allowed = (lambda code: True) if actor is None else (lambda code: not actor.feature_off(code))
    items = []

    if allowed("purchasing.invoice.view"):
        from apps.purchasing.models import SupplierInvoice

        n = SupplierInvoice.objects.filter(status=DocStatus.DRAFT, business_date__lte=end).count()
        if n:
            items.append(Check(_("Purchases not posted yet"), n,
                               reverse("invoices") + "?status=draft", "receipt"))
    if allowed("inventory.stock.view"):
        from apps.inventory.models import Stocktake, StockTransfer

        n = Stocktake.objects.filter(status=DocStatus.DRAFT, business_date__lte=end).count()
        if n:
            items.append(Check(_("Stocktakes in progress"), n, reverse("stocktakes"),
                               "clipboard-check"))
        n = StockTransfer.objects.filter(status=DocStatus.POSTED, received_at__isnull=True,
                                         business_date__lte=end).count()
        if n:
            items.append(Check(_("Goods sent between branches and not received"), n,
                               reverse("stock-transfers"), "truck"))
    if allowed("treasury.view"):
        from apps.treasury.models import TreasuryDocument

        n = TreasuryDocument.objects.filter(
            status=DocStatus.POSTED, needs_receipt=True, received_at__isnull=True,
            business_date__lte=end).count()
        if n:
            items.append(Check(_("Cash sent between branches and not received"), n,
                               reverse("treasury-documents"), "banknote"))
    if allowed("hr.payroll.view"):
        from apps.hr.models import Employee, PayrollRun

        staff = Employee.objects.filter(is_active=True).filter(
            Q(hired_on__isnull=True) | Q(hired_on__lte=end)).exclude(monthly_salary=0).count()
        paid = PayrollRun.objects.filter(year=year, month=month, status=DocStatus.POSTED).exists()
        if staff and not paid:
            items.append(Check(_("The payroll for this month is not paid"), staff,
                               reverse("payroll-new") + f"?period={year}-{month:02d}", "users"))
    return items


# --- closing and reopening a month ---------------------------------------------------------------

def _event(action: str, year: int, month: int | None, actor, reason: str = "") -> None:
    PeriodEvent.objects.create(action=action, year=year, month=month,
                               user=getattr(actor, "user", None), reason=reason.strip()[:300])


def close_month(year: int, month: int, *, confirm: bool = False, note: str = "",
                actor=None) -> FiscalPeriod:
    """Close a month. `confirm` acknowledges unfinished work listed by checklist()."""
    _require(actor, "ledger.period.close")
    start, end = month_bounds(year, month)
    with transaction.atomic():
        if end >= timezone.localdate():
            raise DomainError(_("A month can be closed once it is over."), code="PERIOD_NOT_OVER")
        if start in _closed_starts():
            raise DomainError(_("This month is already closed."), code="PERIOD_ALREADY_CLOSED")
        upcoming = next_to_close()
        if upcoming is not None and upcoming < (year, month):
            earlier = date(*upcoming, 1)
            raise DomainError(
                _("Close %(month)s first: months are closed in order.")
                % {"month": earlier.strftime("%Y-%m")}, code="PERIOD_EARLIER_OPEN")
        if not confirm and checklist(year, month, actor):
            raise DomainError(_("Some work in this month is unfinished. Finish it, or confirm "
                                "that you want to close the month anyway."),
                              code="PERIOD_HAS_OPEN_ITEMS")
        period, _created = FiscalPeriod.objects.select_for_update().get_or_create(
            start_date=start, defaults={"end_date": end, "name": f"{year}-{month:02d}"})
        period.status = PeriodStatus.CLOSED
        period.closed_at = timezone.now()
        period.closed_by = getattr(actor, "user", None)
        period.save()
        _event(PeriodAction.CLOSED, year, month, actor, note)
    return period


def reopen_month(year: int, month: int, *, reason: str, actor=None) -> FiscalPeriod:
    _require(actor, "ledger.period.reopen")
    start, _end = month_bounds(year, month)
    if not reason.strip():
        raise ValidationError(_("Say why the month is reopened."),
                              fields={"reason": [_("Say why the month is reopened.")]})
    with transaction.atomic():
        period = FiscalPeriod.objects.select_for_update().filter(
            start_date=start, status=PeriodStatus.CLOSED).first()
        if period is None:
            raise DomainError(_("This month is not closed."), code="PERIOD_NOT_CLOSED")
        if _active_year_end(year) is not None:
            raise DomainError(_("The year %(year)s is closed. Reopen the year first.")
                              % {"year": year}, code="PERIOD_YEAR_CLOSED")
        if last_closed() != (year, month):
            raise DomainError(_("Reopen the later closed months first."),
                              code="PERIOD_LATER_CLOSED")
        period.status = PeriodStatus.OPEN
        period.closed_at = None
        period.closed_by = None
        period.save()
        _event(PeriodAction.REOPENED, year, month, actor, reason)
    return period


# --- the financial year --------------------------------------------------------------------------

@dataclass
class YearView:
    year: int
    figures: Figures
    year_end: YearEnd | None
    can_close: bool
    blocker: str = ""
    can_reopen: bool = False
    months: list[Month] = field(default_factory=list)


def year_view(year: int) -> YearView:
    months = months_of(year)
    year_end = _active_year_end(year)
    view = YearView(year=year, figures=figures(date(year, 1, 1), date(year, 12, 31)),
                    year_end=year_end, can_close=False, months=months)
    if year_end is not None:
        later = YearEnd.objects.filter(year__gt=year, reopened_at__isnull=True).exists()
        view.can_reopen = not later
        return view
    view.blocker = _year_blocker(year, months)
    view.can_close = not view.blocker
    return view


def _year_blocker(year: int, months: list[Month]) -> str:
    if date(year, 12, 31) >= timezone.localdate():
        return _("The year can be closed once it is over and all its months are closed.")
    if any(m.state not in (MonthState.CLOSED, MonthState.BEFORE) for m in months):
        return _("Close all the months of the year first.")
    if all(m.state == MonthState.BEFORE for m in months):
        return _("Nothing was posted in this year.")
    first = first_month()
    if first is not None:
        for earlier in range(first[0], year):
            if _active_year_end(earlier) is None:
                return _("Close %(year)s first.") % {"year": earlier}
    return ""


def _closing_lines(year: int) -> list[LineInput]:
    """Lines that bring every income and expense balance of the year to zero, against
    retained earnings, per branch, currency and metal."""
    home = functional_commodity()
    rows = (JournalLine.objects.filter(business_date__year=year,
                                       account__type__in=PROFIT_AND_LOSS)
            .values("account", "commodity", "branch")
            .annotate(q=Sum("quantity"), f=Sum("functional_amount")))
    from .models import Account, Commodity

    accounts = Account.objects.in_bulk({row["account"] for row in rows})
    commodities = Commodity.objects.in_bulk({row["commodity"] for row in rows} | {home.pk})
    branches = Branch.objects.in_bulk({row["branch"] for row in rows})
    lines: list[LineInput] = []
    offset: dict[tuple[int, int], list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])

    memo = _("Closing %(year)s") % {"year": year}

    def book(account, commodity_id: int, branch_id: int, quantity: Decimal,
             value: Decimal) -> tuple[int, Decimal] | None:
        if quantity == 0 and value == 0:
            return None
        if quantity == 0 or commodity_id == home.pk:
            # A value left with no quantity (rounding of metal or currency) is closed in money.
            commodity_id, quantity = home.pk, value
        lines.append(LineInput(account=account, commodity=commodities[commodity_id],
                               quantity=quantity, functional_amount=value,
                               branch=branches[branch_id], memo=memo))
        return commodity_id, quantity

    for row in rows:
        value = -(row["f"] or ZERO)
        booked = book(accounts[row["account"]], row["commodity"], row["branch"],
                      -(row["q"] or ZERO), value)
        if booked:
            bucket = offset[(booked[0], row["branch"])]
            bucket[0] -= booked[1]
            bucket[1] -= value
    retained = account_for("retained_earnings")
    for (commodity_id, branch_id), (quantity, value) in sorted(offset.items()):
        book(retained, commodity_id, branch_id, quantity, value)
    return lines


def close_year(year: int, *, actor=None) -> YearEnd:
    _require(actor, "ledger.period.close")
    with transaction.atomic():
        view = year_view(year)
        if view.year_end is not None:
            raise DomainError(_("This year is already closed."), code="YEAR_ALREADY_CLOSED")
        if view.blocker:
            raise DomainError(view.blocker, code="YEAR_NOT_READY")
        lines = _closing_lines(year)
        if not lines:
            raise DomainError(_("There is no income or expense to close in %(year)s.")
                              % {"year": year}, code="YEAR_NOTHING_TO_CLOSE")
        branch = lines[0].branch
        entry = post_entry(branch=branch, business_date=date(year, 12, 31), lines=lines,
                           kind=EntryKind.CLOSING,
                           memo=_("Closing the year %(year)s") % {"year": year},
                           actor=actor, _closing=True)
        try:
            with transaction.atomic():
                year_end = YearEnd.objects.create(year=year, entry=entry,
                                                  closed_by=getattr(actor, "user", None))
        except IntegrityError:
            raise DomainError(_("This year is already closed."),
                              code="YEAR_ALREADY_CLOSED") from None
        _event(PeriodAction.YEAR_CLOSED, year, None, actor)
    return year_end


def reopen_year(year: int, *, reason: str, actor=None) -> YearEnd:
    _require(actor, "ledger.period.reopen")
    if not reason.strip():
        raise ValidationError(_("Say why the year is reopened."),
                              fields={"reason": [_("Say why the year is reopened.")]})
    with transaction.atomic():
        year_end = YearEnd.objects.select_for_update().filter(
            year=year, reopened_at__isnull=True).first()
        if year_end is None:
            raise DomainError(_("This year is not closed."), code="YEAR_NOT_CLOSED")
        if YearEnd.objects.filter(year__gt=year, reopened_at__isnull=True).exists():
            raise DomainError(_("Reopen the later years first."), code="YEAR_LATER_CLOSED")
        reverse_entry(year_end.entry_id, business_date=date(year, 12, 31),
                      memo=_("Reopening the year %(year)s") % {"year": year}, _closing=True)
        year_end.reopened_at = timezone.now()
        year_end.reopened_by = getattr(actor, "user", None)
        year_end.save()
        _event(PeriodAction.YEAR_REOPENED, year, None, actor, reason)
    return year_end
