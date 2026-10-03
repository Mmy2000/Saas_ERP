"""Traffic figures for the console: requests, server time, errors and refusals per client over a
window, and the busiest routes. Platform tables only (no tenant context needed)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from django.db.models import F, Max, Q, QuerySet, Sum
from django.db.models.functions import TruncHour
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _

from apps.platform.tenants import traffic
from apps.platform.tenants.models import (
    Tenant,
    TenantRouteTraffic,
    TenantStatus,
    TenantTraffic,
)

# key: (label, length, bucket seconds)
WINDOWS = {
    "1h": (_("Last hour"), timedelta(hours=1), 60),
    "24h": (_("Last 24 hours"), timedelta(hours=24), 3600),
    "7d": (_("Last 7 days"), timedelta(days=7), 6 * 3600),
}
DEFAULT_WINDOW = "24h"
NEAR_LIMIT = 0.8  # a peak minute at 80% of the limit is "near the limit"
SUMS = ("requests", "throttled", "errors", "slow", "total_ms")


def window(key: str) -> str:
    return key if key in WINDOWS else DEFAULT_WINDOW


@dataclass
class Totals:
    requests: int = 0
    throttled: int = 0
    errors: int = 0
    slow: int = 0
    total_ms: int = 0
    max_ms: int = 0
    peak: int = 0  # busiest minute

    @property
    def served(self) -> int:
        return self.requests - self.throttled

    @property
    def avg_ms(self) -> int:
        return round(self.total_ms / self.served) if self.served else 0

    @property
    def server_seconds(self) -> int:
        return round(self.total_ms / 1000)


@dataclass
class ClientTraffic(Totals):
    tenant: Tenant | None = None
    limit: int | None = None
    this_minute: int = 0
    share: int = 0  # % of all clients' server time

    @property
    def peak_pct(self) -> int | None:
        return round(self.peak / self.limit * 100) if self.limit else None

    @property
    def enabled(self) -> bool:
        return self.tenant is not None and self.tenant.status == TenantStatus.ACTIVE

    @property
    def switchable(self) -> bool:
        return self.tenant is not None and self.tenant.status in (TenantStatus.ACTIVE,
                                                                  TenantStatus.SUSPENDED)

    @property
    def state(self) -> str:
        """disabled: the client is suspended; throttled: refused requests in the window;
        near: a minute close to the limit; slow: one served request in ten or more was slow;
        ok otherwise."""
        if not self.enabled:
            return "disabled"
        if self.throttled:
            return "throttled"
        if self.limit and self.peak >= self.limit * NEAR_LIMIT:
            return "near"
        if self.served and self.slow * 10 >= self.served:
            return "slow"
        return "ok"


@dataclass
class Report:
    key: str
    label: str
    start: datetime
    totals: Totals
    clients: list[ClientTraffic]
    chart: list[dict] = field(default_factory=list)


def _start_and_buckets(key: str) -> tuple[datetime, int, int]:
    _label, length, bucket = WINDOWS[key]
    count = int(length.total_seconds() // bucket)
    now = timezone.now().timestamp()
    last = int(now // bucket * bucket)
    first = last - (count - 1) * bucket
    return datetime.fromtimestamp(first, tz=UTC), bucket, count


def _chart(rows: QuerySet, start: datetime, bucket: int, count: int) -> list[dict]:
    """Requests per bucket, as bar heights. Hour (or coarser) buckets are summed in SQL."""
    rows = rows.annotate(at=TruncHour("minute", tzinfo=UTC) if bucket >= 3600 else F("minute"))
    per = rows.values("at").annotate(requests=Sum("requests"), throttled=Sum("throttled"),
                                     total_ms=Sum("total_ms"))
    first = int(start.timestamp())
    buckets = [{"requests": 0, "throttled": 0, "total_ms": 0} for _ in range(count)]
    for row in per:
        index = (int(row["at"].timestamp()) - first) // bucket
        if 0 <= index < count:
            for name in ("requests", "throttled", "total_ms"):
                buckets[index][name] += row[name]
    top = max((b["requests"] for b in buckets), default=0)
    step = max(1, count // 12)  # about a dozen axis labels
    fmt = "D H:i" if bucket >= 6 * 3600 else "H:i"
    chart = []
    for index, b in enumerate(buckets):
        at = timezone.localtime(datetime.fromtimestamp(first + index * bucket, tz=UTC))
        served = b["requests"] - b["throttled"]
        chart.append({
            "at": at, "requests": b["requests"], "throttled": b["throttled"],
            "avg_ms": round(b["total_ms"] / served) if served else 0,
            "height": round(b["requests"] / top * 100) if top else 0,
            "label": date_format(at, fmt) if index % step == 0 else "",
            "last": index == count - 1,
        })
    return chart


# Aliases are prefixed: an alias named like its column would shadow it for the next aggregate.
AGGREGATES = {**{f"t_{name}": Sum(name) for name in SUMS}, "t_max_ms": Max("max_ms"),
              "t_peak": Max("requests")}


def _totals(target: Totals, row: dict) -> None:
    for name in (*SUMS, "max_ms", "peak"):
        setattr(target, name, row[f"t_{name}"] or 0)


def report(key: str, tenant: Tenant | None = None) -> Report:
    key = window(key)
    start, bucket, count = _start_and_buckets(key)
    rows = TenantTraffic.objects.filter(minute__gte=start)
    if tenant is not None:
        rows = rows.filter(tenant=tenant)

    totals = Totals()
    _totals(totals, rows.aggregate(**AGGREGATES))

    minute = traffic.current_minute()
    now = dict(TenantTraffic.objects.filter(minute=minute).values_list("tenant_id", "requests"))
    per_tenant = {row["tenant_id"]: row for row in
                  rows.values("tenant_id").annotate(**AGGREGATES)}
    tenants = [tenant] if tenant is not None else Tenant.objects.filter(
        Q(pk__in=per_tenant) | Q(status__in=(TenantStatus.ACTIVE, TenantStatus.SUSPENDED))
    ).order_by("name")
    clients = []
    for t in tenants:
        client = ClientTraffic(tenant=t, limit=traffic.limit_for(t), this_minute=now.get(t.pk, 0))
        if t.pk in per_tenant:
            _totals(client, per_tenant[t.pk])
        client.share = round(client.total_ms / totals.total_ms * 100) if totals.total_ms else 0
        clients.append(client)
    clients.sort(key=lambda c: (-c.total_ms, -c.requests))
    return Report(key=key, label=str(WINDOWS[key][0]), start=start, totals=totals,
                  clients=clients, chart=_chart(rows, start, bucket, count))


def routes(tenant: Tenant, start: datetime, limit: int = 15) -> list[dict]:
    """The routes that used the most server time since `start`'s day."""
    rows = (TenantRouteTraffic.objects.filter(tenant=tenant, day__gte=timezone.localdate(start))
            .values("route").annotate(requests=Sum("requests"), errors=Sum("errors"),
                                      slow=Sum("slow"), total_ms=Sum("total_ms"),
                                      max_ms=Max("max_ms"))
            .order_by("-total_ms")[:limit])
    result = []
    for row in rows:
        method, _sep, path = row["route"].partition(" ")
        result.append({**row, "method": method, "path": path,
                       "avg_ms": round(row["total_ms"] / row["requests"]) if row["requests"]
                       else 0})
    return result
