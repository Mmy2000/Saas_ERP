from datetime import date

from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone

from apps.iam.authz import permission_required
from apps.ledger import closing
from apps.ledger.models import Account, Commodity, EntryKind, JournalEntry, PeriodEvent, YearEnd
from apps.ledger.selectors import account_balances, trial_balance
from apps.org.models import Branch


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


@permission_required("ledger.view")
def accounts(request):
    branches = request.actor.branch_ids("ledger.view")
    balances = account_balances(branch_ids=branches)
    rows = []
    for account in Account.objects.order_by("code"):
        depth = 0 if account.parent_id is None else (1 if len(account.code) <= 2 else 2)
        rows.append({"account": account, "depth": depth, "balance": balances.get(account.pk)})
    return render(request, "ledger/accounts.html", {"rows": rows})


@permission_required("ledger.view")
def journal(request):
    entries = (JournalEntry.objects.select_related("branch", "posted_by", "reversed_by")
               .prefetch_related("lines__account", "lines__commodity", "lines__party"))
    branches = request.actor.branch_ids("ledger.view")
    if branches is not None:
        entries = entries.filter(branch_id__in=branches)
    date_from, date_to = _parse_date(request.GET.get("from")), _parse_date(request.GET.get("to"))
    account_id = request.GET.get("account") or ""
    if date_from:
        entries = entries.filter(business_date__gte=date_from)
    if date_to:
        entries = entries.filter(business_date__lte=date_to)
    if account_id.isdigit():
        entries = entries.filter(lines__account_id=int(account_id)).distinct()
    entries = entries.order_by("-business_date", "-id")
    page = Paginator(entries, 20).get_page(request.GET.get("page"))
    return render(request, "ledger/journal.html", {
        "page": page,
        "accounts": Account.objects.filter(is_postable=True).order_by("code"),
        "filters": {"from": date_from, "to": date_to, "account": account_id},
    })


@permission_required("ledger.journal.post")
def journal_new(request):
    allowed = request.actor.branch_ids("ledger.journal.post")
    branches = Branch.objects.filter(is_active=True).order_by("code")
    if allowed is not None:
        branches = branches.filter(pk__in=allowed)
    return render(request, "ledger/journal_form.html", {
        "branches": branches,
        "default_branch": request.membership.default_branch_id,
        "today": timezone.localdate(),
        "accounts": Account.objects.filter(is_postable=True, is_active=True).order_by("code"),
        "commodities": Commodity.objects.select_related("metal"),
    })


@permission_required("ledger.view")
def trial_balance_view(request):
    as_of = _parse_date(request.GET.get("as_of"))
    tb = trial_balance(as_of=as_of, branch_ids=request.actor.branch_ids("ledger.view"))
    return render(request, "ledger/trial_balance.html", {
        "tb": tb, "as_of": as_of, "manual": EntryKind.MANUAL,
        "home_currency": Commodity.objects.filter(is_functional=True).values_list(
            "code", flat=True).first(),
    })


def _home_currency():
    return Commodity.objects.filter(is_functional=True).values_list("code", flat=True).first()


@permission_required("ledger.view")
def periods(request):
    years = closing.years()
    upcoming = closing.next_to_close()
    try:
        year = int(request.GET.get("year", ""))
    except ValueError:
        year = upcoming[0] if upcoming else years[0]
    if year not in years:
        year = years[0]
    return render(request, "ledger/periods.html", {
        "view": closing.year_view(year), "years": years,
        "upcoming": date(*upcoming, 1) if upcoming else None,
        "events": PeriodEvent.objects.select_related("user")[:12],
        "home_currency": _home_currency(),
    })


@permission_required("ledger.view")
def period(request, year, month):
    try:
        closing.month_bounds(year, month)
    except Exception:  # noqa: BLE001 - any bad month is a 404
        raise Http404 from None
    item = closing.months_of(year)[month - 1]
    if item.state in (closing.MonthState.FUTURE, closing.MonthState.BEFORE):
        raise Http404
    upcoming = closing.next_to_close()
    return render(request, "ledger/period.html", {
        "item": item,
        "checks": closing.checklist(year, month, request.actor),
        "upcoming": date(*upcoming, 1) if upcoming else None,
        "year_closed": YearEnd.objects.filter(year=year, reopened_at__isnull=True).exists(),
        "events": PeriodEvent.objects.select_related("user").filter(year=year, month=month)[:10],
        "home_currency": _home_currency(),
    })
