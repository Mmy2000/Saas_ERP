"""The monthly report e-mail: last month's key figures against the month before, the profit
and loss, balance sheet and sales analysis as PDFs, and whether the month is closed yet.

Each person gets only what their own roles let them see (permission and branch scope), so the
same e-mail can go to the owner and to a branch manager.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db.models import Count, Sum
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from apps.core.models import DocStatus

# (report code, extra filter values) in the order they are attached.
PACK = (
    ("profit_loss", {"columns": "previous"}),
    ("balance_sheet", {}),
    ("sales", {"group": "category"}),
)


def last_month(today: date | None = None) -> tuple[int, int]:
    today = today or timezone.localdate()
    return (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)


def _change(now: Decimal, before: Decimal):
    return (now - before) * 100 / abs(before) if before else None


def _readable(actor) -> list[str]:
    from apps.reports.registry import REPORTS

    return [code for code, _extra in PACK
            if code in REPORTS and actor.can(REPORTS[code].permission())]


def _invoices(actor, start: date, end: date) -> dict | None:
    if not actor.can("sales.invoice.view"):
        return None
    from apps.sales.models import SalesInvoice

    invoices = SalesInvoice.objects.filter(status=DocStatus.POSTED,
                                           business_date__range=(start, end))
    branches = actor.branch_ids("sales.invoice.view")
    if branches is not None:
        invoices = invoices.filter(branch_id__in=branches)
    return invoices.aggregate(count=Count("id"), total=Sum("total_amount"))


def pack_for(membership, year: int, month: int) -> dict | None:
    """What one person's e-mail says for the month, or None when they may see none of it."""
    from apps.iam.authz import build_actor
    from apps.ledger.closing import month_bounds
    from apps.ledger.models import FiscalPeriod, PeriodStatus
    from apps.reports.registry import REPORTS
    from apps.reports.statements import pnl_summary, previous_period

    from .jobs import workspace_url
    from .services import company_name

    actor = build_actor(membership)
    start, end = month_bounds(year, month)
    before_start, before_end = previous_period(start, end)
    figures = []
    pnl_permission = REPORTS["profit_loss"].permission()
    if actor.can(pnl_permission):
        scope = actor.branch_ids(pnl_permission)
        now, before = pnl_summary(start, end, scope), pnl_summary(before_start, before_end, scope)
        for key, label in (("sales", _("Sales")), ("gross", _("Gross profit")),
                           ("net", _("Net profit"))):
            figures.append({"label": label, "now": now[key], "before": before[key],
                            "change": _change(now[key], before[key])})
    invoices = _invoices(actor, start, end)
    previous_invoices = _invoices(actor, before_start, before_end)
    reports = _readable(actor)
    if not figures and not reports and not (invoices and invoices["count"]):
        return None

    base = workspace_url()
    closing = None
    if actor.can("ledger.period.close") and actor.branch_ids("ledger.period.close") is None:
        closed = FiscalPeriod.objects.filter(start_date=start,
                                             status=PeriodStatus.CLOSED).exists()
        closing = {"closed": closed, "url": base + reverse("period", args=[year, month])}
    return {
        "month": date_format(start, "F Y"), "before_month": date_format(before_start, "F Y"),
        "figures": figures, "invoices": invoices, "previous_invoices": previous_invoices,
        "reports": [str(REPORTS[code].title) for code in reports], "closing": closing,
        "company": company_name(), "home": base + "/",
        "name": membership.user.display_name or membership.username,
    }


def pack_files(message) -> list[tuple[bytes, str]]:
    """The PDFs of a monthly report message, made for its reader at sending time."""
    from apps.iam.authz import build_actor
    from apps.iam.models import Membership, MembershipStatus
    from apps.ledger.closing import month_bounds
    from apps.reports.registry import REPORTS
    from apps.reports.views import report_file

    params = message.params or {}
    membership = Membership.objects.filter(pk=params.get("membership"),
                                           status=MembershipStatus.ACTIVE).first()
    if membership is None:
        raise LookupError("The reader is no longer a member of this workspace.")
    actor = build_actor(membership)
    start, end = month_bounds(params["year"], params["month"])
    files = []
    for code in _readable(actor):
        query = ({"date": end.isoformat()} if code == "balance_sheet"
                 else {"date_from": start.isoformat(), "date_to": end.isoformat()})
        query.update(dict(PACK)[code])
        files.append(report_file(code, query, actor.branch_ids(REPORTS[code].permission())))
    return files
