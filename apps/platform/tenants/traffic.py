"""Per-client request counting and the requests-per-minute limit (platform console → Traffic).

Counters live in PostgreSQL (one row per client per minute, one per client, day and route), so
every worker process sees the same numbers. The limit is a fixed one-minute window: a request
is refused once the minute's count has reached the limit. Two requests checking at the same
instant can both get through, so a client may overshoot by a few requests per minute; that is
fine for protecting the server and avoids a lock on every request.

All writes run outside the request transaction (TrafficMiddleware sits before
TenantResolutionMiddleware), so a rolled-back request is still counted.
"""

from __future__ import annotations

import logging
from datetime import datetime

from django.conf import settings
from django.db import DatabaseError, connection
from django.utils import timezone

from .models import Tenant

logger = logging.getLogger(__name__)


def slow_ms() -> int:
    return settings.TRAFFIC_SLOW_MS


def limit_for(tenant: Tenant) -> int | None:
    """Requests per minute this client may make, or None for no limit."""
    limit = tenant.requests_per_minute
    if limit is None:
        limit = settings.TENANT_REQUESTS_PER_MINUTE
    return limit or None


def current_minute(now: datetime | None = None) -> datetime:
    return (now or timezone.now()).replace(second=0, microsecond=0)


def requests_this_minute(tenant_id: int, minute: datetime) -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT requests FROM tenants_tenanttraffic"
                       " WHERE tenant_id = %s AND minute = %s", [tenant_id, minute])
        row = cursor.fetchone()
    return row[0] if row else 0


def is_over_limit(tenant: Tenant, minute: datetime) -> bool:
    limit = limit_for(tenant)
    return limit is not None and requests_this_minute(tenant.id, minute) >= limit


def record(tenant_id: int, minute: datetime, *, route: str = "", ms: int = 0,
           status: int = 200, throttled: bool = False) -> None:
    """Count one request. Never raises: losing a counter must not fail the request."""
    error = int(status >= 500)
    slow = int(not throttled and ms >= slow_ms())
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO tenants_tenanttraffic
                    (tenant_id, minute, requests, throttled, errors, slow, total_ms, max_ms)
                VALUES (%s, %s, 1, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, minute) DO UPDATE SET
                    requests = tenants_tenanttraffic.requests + 1,
                    throttled = tenants_tenanttraffic.throttled + EXCLUDED.throttled,
                    errors = tenants_tenanttraffic.errors + EXCLUDED.errors,
                    slow = tenants_tenanttraffic.slow + EXCLUDED.slow,
                    total_ms = tenants_tenanttraffic.total_ms + EXCLUDED.total_ms,
                    max_ms = GREATEST(tenants_tenanttraffic.max_ms, EXCLUDED.max_ms)
                """,
                [tenant_id, minute, int(throttled), error, slow,
                 0 if throttled else ms, 0 if throttled else ms])
            if throttled or not route:
                return
            cursor.execute(
                """
                INSERT INTO tenants_tenantroutetraffic
                    (tenant_id, day, route, requests, errors, slow, total_ms, max_ms)
                VALUES (%s, %s, %s, 1, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, day, route) DO UPDATE SET
                    requests = tenants_tenantroutetraffic.requests + 1,
                    errors = tenants_tenantroutetraffic.errors + EXCLUDED.errors,
                    slow = tenants_tenantroutetraffic.slow + EXCLUDED.slow,
                    total_ms = tenants_tenantroutetraffic.total_ms + EXCLUDED.total_ms,
                    max_ms = GREATEST(tenants_tenantroutetraffic.max_ms, EXCLUDED.max_ms)
                """,
                [tenant_id, timezone.localdate(minute), route[:200], error, slow, ms, ms])
    except DatabaseError:
        logger.exception("could not record traffic for tenant %s", tenant_id)
