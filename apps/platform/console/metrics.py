"""Usage figures per client, read inside each tenant's own context (RLS stays on: the console
never sees two tenants in one transaction), and rolled up for the platform overview."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db.models import Count, Max, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.core.models import DocStatus
from apps.core.tenancy import tenant_context
from apps.platform.tenants.models import Tenant, TenantStatus

ZERO = Decimal(0)
DAYS = 14


@dataclass
class TenantUsage:
    tenant: Tenant
    display_name: str = ""
    currency: str = ""
    locale: str = ""
    users: int = 0
    branches: int = 0
    customers: int = 0
    pieces_in_stock: int = 0
    documents_30d: int = 0
    sales_30d: int = 0
    sales_amount_30d: Decimal = ZERO
    last_activity: datetime | None = None
    daily: dict[date, int] = field(default_factory=dict)  # postings per day, last DAYS days

    @property
    def idle_days(self) -> int | None:
        if self.last_activity is None:
            return None
        return (timezone.now() - self.last_activity).days

    @property
    def health(self) -> str:
        """active: posted this week; quiet: not this week; idle: a month or more; new: never."""
        days = self.idle_days
        if days is None:
            return "new"
        if days <= 7:
            return "active"
        return "quiet" if days < 30 else "idle"


def tenant_usage(tenant: Tenant) -> TenantUsage:
    from apps.iam.models import Membership, MembershipStatus
    from apps.inventory.models import ON_HAND, Item
    from apps.ledger.models import JournalEntry
    from apps.org.models import Branch, TenantProfile
    from apps.parties.models import PartyRoleType
    from apps.parties.selectors import parties_with_role
    from apps.sales.models import SalesInvoice

    usage = TenantUsage(tenant=tenant)
    since = timezone.localdate() - timedelta(days=30)
    first_day = timezone.localdate() - timedelta(days=DAYS - 1)
    with tenant_context(tenant.id):
        profile = TenantProfile.objects.first()
        if profile is not None:
            usage.display_name = profile.display_name
            usage.currency = profile.functional_currency
            usage.locale = profile.locale
        usage.users = Membership.objects.filter(status=MembershipStatus.ACTIVE).count()
        usage.branches = Branch.objects.filter(is_active=True).count()
        usage.customers = parties_with_role(PartyRoleType.CUSTOMER).filter(is_active=True).count()
        usage.pieces_in_stock = Item.objects.filter(status__in=ON_HAND).count()
        entries = JournalEntry.objects.all()
        usage.documents_30d = entries.filter(business_date__gte=since).count()
        usage.last_activity = entries.aggregate(last=Max("posted_at"))["last"]
        usage.daily = dict(
            entries.filter(posted_at__date__gte=first_day).annotate(day=TruncDate("posted_at"))
            .values("day").annotate(n=Count("id")).values_list("day", "n"))
        sales = SalesInvoice.objects.filter(status=DocStatus.POSTED, business_date__gte=since)
        totals = sales.aggregate(n=Count("id"), total=Sum("total_amount"))
        usage.sales_30d, usage.sales_amount_30d = totals["n"], totals["total"] or ZERO
    return usage


@dataclass
class PlatformOverview:
    tenants: list[TenantUsage]
    by_status: dict[str, int]
    new_30d: int
    users: int
    documents_30d: int
    sales_30d: int
    sales_by_currency: dict[str, Decimal]
    daily: list[dict]  # day, label, postings, height (bar %)
    health: dict[str, int]


def overview() -> PlatformOverview:
    tenants = list(Tenant.objects.order_by("slug"))
    live = [t for t in tenants if t.status in (TenantStatus.ACTIVE, TenantStatus.SUSPENDED)]
    usages = [tenant_usage(t) for t in live]
    by_status: dict[str, int] = defaultdict(int)
    for tenant in tenants:
        by_status[tenant.status] += 1
    sales_by_currency: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for usage in usages:
        if usage.sales_amount_30d:
            sales_by_currency[usage.currency] += usage.sales_amount_30d
    today = timezone.localdate()
    per_day = {today - timedelta(days=n): 0 for n in range(DAYS)}
    for usage in usages:
        for day, count in usage.daily.items():
            if day in per_day:
                per_day[day] += count
    top = max(per_day.values(), default=0)
    from django.utils.formats import date_format

    daily = [{"day": day, "label": date_format(day, "j"), "postings": count,
              "height": int(count / top * 100) if top else 0, "today": day == today}
             for day, count in sorted(per_day.items())]
    health: dict[str, int] = defaultdict(int)
    for usage in usages:
        health[usage.health] += 1
    month_ago = timezone.now() - timedelta(days=30)
    return PlatformOverview(
        tenants=usages, by_status=dict(by_status),
        new_30d=sum(1 for t in tenants if t.created_at >= month_ago),
        users=sum(u.users for u in usages),
        documents_30d=sum(u.documents_30d for u in usages),
        sales_30d=sum(u.sales_30d for u in usages),
        sales_by_currency=dict(sales_by_currency), daily=daily, health=dict(health))
