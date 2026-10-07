"""Metal price boards and exchange rates (§7.6, §8.3).

"Current" is always the latest `effective_at <= now`, never the latest id (the legacy system
used `.last()` by PK). Boards and rates are append-only: publishing a new price creates a new
row, and documents keep a reference to the board they priced from.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.db.models import Q
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.core.errors import DomainError
from apps.core.models import TenantScopedModel


class PriceSide(models.TextChoices):
    SELL = "sell", gettext_lazy("Sell")
    BUY = "buy", gettext_lazy("Buy")
    SCRAP_BUY = "scrap_buy", gettext_lazy("Scrap buy")


class FxRate(TenantScopedModel):
    """Units of the tenant's functional currency per one unit of `currency`.
    Legacy source: `Cod.Fc2` (4 dp; 6 dp on one branch)."""

    currency = models.ForeignKey("catalog.Currency", on_delete=models.PROTECT, related_name="+")
    rate = models.DecimalField(max_digits=18, decimal_places=8)
    effective_at = models.DateTimeField()
    source = models.CharField(max_length=40, default="manual")

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(rate__gt=0), name="pricing_fxrate_positive_check"),
        ]
        indexes = [
            models.Index(fields=["tenant", "currency", "-effective_at"],
                         name="pricing_fxrate_current_idx"),
        ]

    def __str__(self):
        return f"{self.currency_id} {self.rate} @ {self.effective_at:%Y-%m-%d %H:%M}"


class MetalPriceBoard(TenantScopedModel):
    """One published set of per-gram prices. Legacy source: `Cod.gp` (sell `prXX`,
    buy `pr2XX`, silver `pr1`)."""

    effective_at = models.DateTimeField()
    source = models.CharField(max_length=40, default="manual")  # manual | feed:<provider>
    reference_karat = models.ForeignKey("catalog.Karat", on_delete=models.PROTECT,
                                        related_name="+")
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "-effective_at"], name="pricing_board_current_idx"),
        ]

    def __str__(self):
        return f"board #{self.pk} @ {self.effective_at:%Y-%m-%d %H:%M}"

    def price(self, karat, side: str = PriceSide.SELL) -> Decimal:
        """The captured per-gram price for `karat`. Uses prefetched lines when available."""
        karat_id = getattr(karat, "pk", karat)
        line = next((ln for ln in self.lines.all() if ln.karat_id == karat_id), None)
        if line is None:
            raise DomainError(_("%(karat)s has no price on this board.") % {"karat": karat},
                              code="PRICING_KARAT_NOT_ON_BOARD")
        value = {
            PriceSide.SELL: line.sell_price_per_g,
            PriceSide.BUY: line.buy_price_per_g,
            PriceSide.SCRAP_BUY: line.scrap_buy_price_per_g,
        }[PriceSide(side)]
        if value is None:
            raise DomainError(_("This board has no %(side)s price for %(karat)s.")
                              % {"side": PriceSide(side).label, "karat": karat},
                              code="PRICING_SIDE_NOT_ON_BOARD")
        return value


class MetalPriceBoardLine(TenantScopedModel):
    board = models.ForeignKey(MetalPriceBoard, on_delete=models.CASCADE, related_name="lines")
    karat = models.ForeignKey("catalog.Karat", on_delete=models.PROTECT, related_name="+")
    sell_price_per_g = models.DecimalField(max_digits=18, decimal_places=4)
    buy_price_per_g = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    # Legacy "buy" prices (`pr2XX`) are what shops pay for scrap.
    scrap_buy_price_per_g = models.DecimalField(max_digits=18, decimal_places=4, null=True,
                                                blank=True)
    is_derived = models.BooleanField(default=False)  # computed from the reference karat

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "board", "karat"],
                                    name="pricing_boardline_karat_uniq"),
            models.CheckConstraint(
                condition=Q(sell_price_per_g__gt=0)
                & (Q(buy_price_per_g__isnull=True) | Q(buy_price_per_g__gt=0))
                & (Q(scrap_buy_price_per_g__isnull=True) | Q(scrap_buy_price_per_g__gt=0)),
                name="pricing_boardline_positive_check",
            ),
        ]

    def __str__(self):
        return f"{self.karat_id}: {self.sell_price_per_g}"


class PriceFeed(TenantScopedModel):
    """How this shop follows the market gold price (apps.pricing.feed). One row per tenant.

    The market's 21K sell and buy prices (apps.platform.market) are moved by the shop's own
    amounts, rounded, and published as a new board when they differ enough from the current
    one. A jump larger than `max_jump_pct` waits for someone to approve it.
    """

    is_enabled = models.BooleanField(default=False)
    source = models.CharField(max_length=30, blank=True)  # "" = the best available
    # Per gram of 21K, added to the market price (negative to go below it).
    sell_adjust = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    buy_adjust = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    scrap_adjust = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    rounding = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("1"))
    min_change = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("1"))
    max_jump_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("3"))
    follow_silver = models.BooleanField(default=True)
    active_from = models.TimeField(null=True, blank=True)  # shop hours; empty = all day
    active_to = models.TimeField(null=True, blank=True)
    paused_until = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_outcome = models.CharField(max_length=20, blank=True)
    # A change held for approval (too big a jump), as it would be published.
    held = models.JSONField(null=True, blank=True)
    held_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant"], name="pricing_pricefeed_tenant_uniq"),
            models.CheckConstraint(condition=Q(rounding__gte=0, min_change__gte=0,
                                               max_jump_pct__gt=0),
                                   name="pricing_pricefeed_limits_check"),
        ]
