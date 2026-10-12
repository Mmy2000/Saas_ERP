from datetime import date

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.errors import DomainError, ValidationError
from apps.iam.authz import permission_required
from apps.ledger import closing
from apps.ledger.accounts import (
    MANAGE,
    AccountInput,
    create_account,
    delete_account,
    depth_of,
    holder_account_ids,
    parent_choices,
    suggest_code,
    update_account,
    why_locked,
    why_not_deletable,
)
from apps.ledger.models import (
    Account,
    Commodity,
    EntryKind,
    JournalEntry,
    JournalLine,
    PeriodEvent,
    YearEnd,
)
from apps.ledger.selectors import account_balances, trial_balance
from apps.org.models import Branch

from .forms import ONE_CURRENCY, EditAccountForm, NewAccountForm


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


@permission_required("ledger.view")
def accounts(request):
    branches = request.actor.branch_ids("ledger.view")
    balances = account_balances(branch_ids=branches)
    every = list(Account.objects.order_by("code"))
    depths = depth_of(every)
    show_inactive = request.GET.get("inactive") == "1"
    rows = [{"account": account, "depth": min(depths[account.pk], 4),
             "balance": balances.get(account.pk)}
            for account in every if account.is_active or show_inactive]
    return render(request, "ledger/accounts.html", {
        "rows": rows, "show_inactive": show_inactive,
        "inactive_count": sum(1 for a in every if not a.is_active),
        "holders": holder_account_ids(),
        "groups": set(parent_choices().values_list("pk", flat=True))})


def _show(form, exc: ValidationError) -> None:
    for key, problems in (exc.fields or {}).items():
        for problem in problems if isinstance(problems, list) else [problems]:
            form.add_error(key if key in form.fields else None, str(problem))
    if not exc.fields:
        form.add_error(None, str(exc))


@permission_required(MANAGE)
def account_new(request):
    groups = list(parent_choices())
    initial = {"nature": "debit"}
    wanted = request.GET.get("parent") or ""
    parent = next((g for g in groups if str(g.pk) == wanted), None)
    if parent is not None:
        initial.update(parent=parent.pk, nature=parent.nature, code=suggest_code(parent))
    form = NewAccountForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        one_currency = d["commodity_scope"] == ONE_CURRENCY
        if one_currency and not d["currency"]:
            form.add_error("currency", _("Choose the currency."))
        else:
            try:
                account = create_account(AccountInput(
                    parent_id=d["parent"], code=d["code"], name=d["name"],
                    is_group=d["is_group"], nature=d["nature"],
                    commodity_scope="money" if one_currency else d["commodity_scope"],
                    currency_code=d["currency"] if one_currency else ""), actor=request.actor)
            except ValidationError as exc:
                _show(form, exc)
            else:
                messages.success(request, _("%(name)s added.") % {"name": str(account)})
                return redirect("accounts")
    return render(request, "ledger/account_form.html", {
        "form": form, "account": None,
        "groups": [{"pk": g.pk, "code": suggest_code(g), "nature": g.nature} for g in groups]})


@permission_required(MANAGE)
def account_edit(request, pk):
    account = Account.objects.select_related("parent", "commodity").filter(pk=pk).first()
    if account is None:
        raise Http404
    if request.method == "POST" and request.POST.get("delete"):
        try:
            delete_account(account.pk, actor=request.actor)
        except DomainError as exc:
            messages.error(request, str(exc))
            return redirect("account-edit", pk=account.pk)
        messages.success(request, _("%(name)s deleted.") % {"name": str(account)})
        return redirect("accounts")
    form = EditAccountForm(request.POST or None, standard=bool(account.template_key),
                           initial={"name": account.name, "is_active": account.is_active})
    if request.method == "POST" and form.is_valid():
        try:
            update_account(account.pk, name=form.cleaned_data["name"],
                           is_active=form.cleaned_data["is_active"], actor=request.actor)
        except ValidationError as exc:
            _show(form, exc)
        else:
            messages.success(request, _("Saved."))
            return redirect("accounts")
    return render(request, "ledger/account_form.html", {
        "form": form, "account": account, "locked": why_locked(account),
        "undeletable": why_not_deletable(account),
        "used": JournalLine.objects.filter(account=account).exists()})


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
