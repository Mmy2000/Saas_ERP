"""Features: modules switched on or off per client from the platform console.

A feature owns a set of permissions (by prefix). When it is off for a client, those permissions
are treated as not granted for everyone in that workspace, Owners included (`Actor.can`), so
the sidebar, the command palette, buttons, dashboard blocks, pages and API endpoints that ask
for them all disappear or refuse at once, with no per-screen code.

Whether a feature is on for a client:
    1. the client's own setting (console → client → Features), if there is one;
    2. else its plan's choice (console → Plans or Features), if one was saved;
    3. else on.

Adding a module: give it its own permission codes (register_permissions) and add a Feature
here listing their prefix. It then shows up in the console on its own.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

# A feature with no saved choice for a plan is on, so adding a module changes nothing for
# existing clients until it is switched off for a plan or a client in the console.
CACHE_SECONDS = 300


@dataclass(frozen=True)
class Feature:
    key: str
    label: object
    description: object
    group: object
    icon: str
    permissions: tuple[str, ...]  # permission code prefixes this feature owns

    def owns(self, code: str) -> bool:
        return any(code == p or code.startswith(p) for p in self.permissions)


SALES, GOODS, MONEY, TOOLS = _("Sales"), _("Goods"), _("Money"), _("Tools")

FEATURES: list[Feature] = [
    Feature("reservations", _("Reservations"),
            _("Hold pieces for customers, with deposits and a price lock."),
            SALES, "bookmark", ("sales.reservation.",)),
    Feature("wholesale", _("Wholesale and trade accounts"),
            _("Sell and buy by weight with traders; one balance in gold and money."),
            SALES, "scale", ("sales.trade.", "parties.trade_account.")),
    Feature("repairs", _("Repairs and custom orders"),
            _("Take in customers' pieces, bag numbers, deposits and delivery."),
            SALES, "wrench", ("repairs.",)),
    Feature("manufacturing", _("Workshops and work orders"),
            _("Send gold to workshops, receive new pieces, loss and labour."),
            GOODS, "send", ("manufacturing.", "parties.workshop.")),
    Feature("scrap", _("Scrap gold"),
            _("Buy scrap from walk-ins and customers, sell it to dealers."),
            GOODS, "coins", ("purchasing.scrap.",)),
    Feature("supplier_returns", _("Returns to suppliers"),
            _("Send pieces and bulk weight back to suppliers."),
            GOODS, "undo-2", ("purchasing.return.",)),
    Feature("transfers", _("Branch transfers"),
            _("Move goods between branches, in transit until received."),
            GOODS, "truck", ("inventory.transfer.",)),
    Feature("stocktake", _("Stocktake"),
            _("Count pieces and weigh bulk stock, then post the differences."),
            GOODS, "clipboard-check", ("inventory.stocktake.",)),
    Feature("labels", _("Barcode labels"),
            _("Label templates and printing, including Zebra printers."),
            GOODS, "printer", ("printing.", "inventory.item.print_label")),
    Feature("treasury", _("Cash boxes and banks"),
            _("Boxes, bank accounts, card terminals, transfers and currency exchange."),
            MONEY, "wallet", ("treasury.",)),
    Feature("expenses", _("Expenses"),
            _("Expense categories and vouchers paid from a box or bank."),
            MONEY, "receipt-text", ("expenses.",)),
    Feature("multi_currency", _("Exchange rates"),
            _("Publish exchange rates for selling and paying in other currencies."),
            MONEY, "arrow-left-right", ("pricing.fx.",)),
    Feature("accounting", _("Accounting"),
            _("Journal, trial balance and the chart of accounts."),
            MONEY, "book-open", ("ledger.",)),
    Feature("reports", _("Reports"),
            _("Daily summary, gold balances, sales analysis and expenses, with export."),
            TOOLS, "chart-column", ("reports.",)),
]
BY_KEY = {feature.key: feature for feature in FEATURES}


def _cache_key(tenant_id: int) -> str:
    return f"features:{tenant_id}"


def forget(tenant_id: int | None = None) -> None:
    """Drop cached states: one client's, or every client's (a plan default changed)."""
    if tenant_id is not None:
        cache.delete(_cache_key(tenant_id))
        return
    from .models import Tenant

    cache.delete_many([_cache_key(pk) for pk in Tenant.objects.values_list("pk", flat=True)])


def plan_defaults() -> dict[str, dict[str, bool]]:
    """{plan: {feature key: on?}} with the console's saved choices over the code defaults."""
    from .models import Plan, PlanFeature

    table = {code: {f.key: True for f in FEATURES}
             for code in Plan.objects.values_list("code", flat=True)}
    for row in PlanFeature.objects.filter(key__in=BY_KEY):
        table.setdefault(row.plan_id, {})[row.key] = row.enabled
    return table


@dataclass(frozen=True)
class FeatureState:
    feature: Feature
    enabled: bool
    plan_default: bool
    custom: bool  # the client's own setting overrides the plan


def states(tenant) -> list[FeatureState]:
    from .models import TenantFeature

    defaults = plan_defaults().get(tenant.plan_id, {})
    own = dict(TenantFeature.objects.filter(tenant=tenant).values_list("key", "enabled"))
    return [FeatureState(f, own.get(f.key, defaults.get(f.key, True)),
                         defaults.get(f.key, True), f.key in own) for f in FEATURES]


def enabled_keys(tenant_id: int) -> frozenset[str]:
    keys = cache.get(_cache_key(tenant_id))
    if keys is None:
        from .models import Tenant

        tenant = Tenant.objects.filter(pk=tenant_id).first()
        keys = frozenset(s.feature.key for s in states(tenant) if s.enabled) if tenant else \
            frozenset()
        cache.set(_cache_key(tenant_id), keys, CACHE_SECONDS)
    return keys


def is_enabled(tenant_id: int, key: str) -> bool:
    return key in enabled_keys(tenant_id)


def disabled_feature_for(code: str, tenant_id: int | None) -> Feature | None:
    """The switched-off feature that owns permission `code` for this client, if any."""
    if tenant_id is None:
        return None
    enabled = enabled_keys(tenant_id)
    for feature in FEATURES:
        if feature.key not in enabled and feature.owns(code):
            return feature
    return None


def disabled_permissions(tenant_id: int | None) -> frozenset[str]:
    """Every registered permission code owned by a feature that is off for this client."""
    if tenant_id is None:
        return frozenset()
    from apps.iam.catalog import permissions

    off = [f for f in FEATURES if f.key not in enabled_keys(tenant_id)]
    return frozenset(code for code in permissions() if any(f.owns(code) for f in off))
