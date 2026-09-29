"""Reference data: currencies, metals, karats and item categories (§7.3)."""

from __future__ import annotations

from django.db import models
from django.db.models import Q
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantScopedModel

#: Default currency names, shown in the viewer's language unless the tenant sets its own.
CURRENCY_NAMES = {
    "EGP": _("Egyptian pound"),
    "USD": _("US dollar"),
    "AED": _("UAE dirham"),
    "SAR": _("Saudi riyal"),
    "EUR": _("Euro"),
}


class Currency(TenantScopedModel):
    """A currency the tenant trades in. Legacy source: `Cod.Fc1` (`cudis`)."""

    code = models.CharField(max_length=3)  # ISO 4217
    name = models.CharField(max_length=60, blank=True)  # blank = translated default
    symbol = models.CharField(max_length=8, blank=True)
    minor_units = models.PositiveSmallIntegerField(default=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "currencies"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "code"], name="catalog_currency_code_uniq"),
            models.CheckConstraint(condition=Q(code__regex=r"^[A-Z]{3}$"),
                                   name="catalog_currency_iso_code_check"),
            models.CheckConstraint(condition=Q(minor_units__lte=4),
                                   name="catalog_currency_minor_units_max_check"),
        ]

    def __str__(self):
        return self.code

    @property
    def label(self) -> str:
        return self.name or str(CURRENCY_NAMES.get(self.code, self.code))


class MetalCode(models.TextChoices):
    GOLD = "gold", _("Gold")
    SILVER = "silver", _("Silver")
    PLATINUM = "platinum", _("Platinum")


class Metal(TenantScopedModel):
    code = models.CharField(max_length=16, choices=MetalCode.choices)
    name = models.CharField(max_length=60)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "code"], name="catalog_metal_code_uniq"),
        ]

    def __str__(self):
        return self.name


class Karat(TenantScopedModel):
    """A purity of a metal. Legacy source: `Cod.co1`, which is just an integer (1 = silver).

    Fineness is tenant data (§8.2): conversions read it from here, never from constants.
    Posted documents store the weights they computed, so a later fineness edit does not
    rewrite history.
    """

    metal = models.ForeignKey(Metal, on_delete=models.PROTECT, related_name="karats")
    code = models.PositiveSmallIntegerField()  # 14/18/21/22/24 for gold, 925 for silver
    fineness = models.DecimalField(max_digits=7, decimal_places=3)  # ‰, e.g. 999.900
    is_reference = models.BooleanField(default=False)  # 21k for gold in Egypt
    display_name = models.CharField(max_length=40, blank=True)  # blank = translated default
    legacy_code = models.PositiveSmallIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["metal__code", "-code"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "metal", "code"],
                                    name="catalog_karat_code_uniq"),
            models.UniqueConstraint(fields=["tenant", "metal"], condition=Q(is_reference=True),
                                    name="catalog_karat_one_reference_uniq"),
            models.CheckConstraint(condition=Q(fineness__gt=0, fineness__lte=1000),
                                   name="catalog_karat_fineness_range_check"),
        ]

    def __str__(self):
        return self.label

    @property
    def label(self) -> str:
        """The tenant's own name if set, else "21K" / "عيار 21" in the viewer's language."""
        if self.display_name:
            return self.display_name
        if self.metal.code == MetalCode.GOLD:
            return gettext("%(code)sK") % {"code": self.code}
        return gettext("%(metal)s %(code)s") % {"metal": self.metal.get_code_display(),
                                                 "code": self.code}


class ProductFamily(models.TextChoices):
    GOLD = "gold", _("Gold")
    SILVER = "silver", _("Silver")
    WATCH = "watch", _("Watches")
    BEADS = "beads", _("Beads")
    STAINLESS = "stainless", _("Stainless steel")
    BULLION_COIN = "bullion_coin", _("Bullion and coins")
    DIAMOND = "diamond", _("Diamonds")
    STONE = "stone", _("Stones")
    OTHER = "other", _("Other")


class Tracking(models.TextChoices):
    SERIALIZED = "serialized", _("Per piece")  # one Item per piece, barcoded (legacy flage=1)
    BULK = "bulk", _("By weight")  # a StockLot counted by weight (legacy flage=2)


MAX_CATEGORY_DEPTH = 3


class ItemCategory(TenantScopedModel):
    """Item group tree, at most three levels. Legacy source: `Sup.Su` (main group) and
    `Sup.Suu` (item group, whose `gco` is the barcode prefix)."""

    code = models.CharField(max_length=32)
    name = models.CharField(max_length=200)
    short_name = models.CharField(max_length=60, blank=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="children")
    depth = models.PositiveSmallIntegerField(default=1, editable=False)
    product_family = models.CharField(max_length=16, choices=ProductFamily.choices)
    tracking = models.CharField(max_length=16, choices=Tracking.choices)
    default_karat = models.ForeignKey(Karat, null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="+")
    # Serialized barcodes are `barcode_prefix × 10⁶ + sequence`, the legacy `gco·10⁶ + n`.
    barcode_prefix = models.PositiveIntegerField(null=True, blank=True)
    commission_rate = models.DecimalField(max_digits=9, decimal_places=6, default=0)  # fraction
    reorder_min_qty = models.PositiveIntegerField(null=True, blank=True)
    reorder_max_qty = models.PositiveIntegerField(null=True, blank=True)
    attributes = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "item categories"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "code"],
                                    name="catalog_itemcategory_code_uniq"),
            models.UniqueConstraint(fields=["tenant", "barcode_prefix"],
                                    name="catalog_itemcategory_barcode_prefix_uniq"),
            models.CheckConstraint(condition=Q(depth__gte=1, depth__lte=MAX_CATEGORY_DEPTH),
                                   name="catalog_itemcategory_depth_range_check"),
            models.CheckConstraint(condition=Q(commission_rate__gte=0, commission_rate__lte=1),
                                   name="catalog_itemcategory_commission_rate_check"),
        ]
        indexes = [models.Index(fields=["tenant", "parent"], name="catalog_itemcat_parent_idx")]

    def __str__(self):
        return f"{self.code} {self.name}"


class CategoryMakingCharge(TenantScopedModel):
    """Default making charge per gram for a category in one currency (legacy `prc`/`pr` in
    EGP and `prc_us`/`pr_us` in USD). Item and promotion prices override these."""

    category = models.ForeignKey(ItemCategory, on_delete=models.CASCADE,
                                 related_name="making_charges")
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    cost_rate_per_g = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    list_rate_per_g = models.DecimalField(max_digits=18, decimal_places=4, default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "category", "currency"],
                                    name="catalog_categorymakingcharge_uniq"),
            models.CheckConstraint(condition=Q(cost_rate_per_g__gte=0, list_rate_per_g__gte=0),
                                   name="catalog_categorymakingcharge_non_negative_check"),
        ]

    def __str__(self):
        return f"{self.category_id}/{self.currency_id}"
