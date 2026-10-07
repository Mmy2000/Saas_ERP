"""Polling the market sources, and telling the shops that follow the market."""

from datetime import time

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from apps.platform.jobs.registry import daily, enqueue, every, task


@task("market.poll", label=_("Read market gold prices"), per_tenant=False, max_attempts=1)
def poll_market():
    from apps.platform.tenants.models import Tenant, TenantStatus

    from .service import poll

    changed = poll()
    if not changed:
        return
    # Each shop applies its own rules (apps.pricing.feed); one job per shop and price move.
    stamp = "-".join(sorted(q.digest[:12] for q in changed))
    for tenant_id in Tenant.objects.filter(status=TenantStatus.ACTIVE).values_list("pk",
                                                                                   flat=True):
        enqueue("pricing.follow_market", tenant_id=tenant_id, dedupe_key=f"market:{stamp}")


@task("market.prune", label=_("Clean up old market prices"), per_tenant=False, max_attempts=2)
def prune_market():
    from .service import prune

    prune()


every("market.poll", minutes=settings.MARKET_POLL_MINUTES)
daily("market.prune", at=time(3, 30))
