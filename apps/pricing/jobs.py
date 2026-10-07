"""Following the market price (apps.pricing.feed), one job per shop and market move."""

from django.utils.translation import gettext_lazy as _

from apps.platform.jobs.registry import task


@task("pricing.follow_market", label=_("Follow the market gold price"), max_attempts=2)
def follow_market():
    from apps.core.tenancy.context import require_current_tenant_id
    from apps.platform.tenants.features import is_enabled

    from .feed import follow_market as follow

    if is_enabled(require_current_tenant_id(), "price_feed"):
        follow()
