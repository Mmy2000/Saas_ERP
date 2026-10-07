"""Selling prices of pieces, changed in bulk with a history, and time-limited offers.

A gold piece sells at the day's gold price plus a making charge per gram (its own, else its
category's: apps.pricing.engine). A price change sets that making charge (or a diamond piece's
label price) on many pieces at once and remembers each piece's old value, so the whole change
can be undone. An offer changes the price only while it runs: nothing on the pieces is touched,
so nothing needs restoring when it ends (legacy GHS61, GHSS41-44, GHS65, GHS68).
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantScopedModel


class PriceTarget(models.TextChoices):
    MAKING = "making", _("Making charge per gram")
    LABEL = "label", _("Label price (diamonds)")


class PriceMethod(models.TextChoices):
    SET = "set", _("Set to")
    ADD = "add", _("Add")
    PERCENT = "percent", _("Change by %")


class PriceChange(TenantScopedModel):
    target = models.CharField(max_length=10, choices=PriceTarget.choices)
    method = models.CharField(max_length=10, choices=PriceMethod.choices)
    value = models.DecimalField(max_digits=18, decimal_places=4)
    filters = models.JSONField(default=dict, blank=True)  # what was chosen, to show it again
    summary = models.CharField(max_length=300, blank=True)  # the filters in words
    note = models.CharField(max_length=200, blank=True)
    item_count = models.PositiveIntegerField(default=0)
    undone_at = models.DateTimeField(null=True, blank=True)
    undone_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.PROTECT, related_name="+")
    undone_count = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["tenant", "-created_at"], name="merch_change_recent_idx")]


class PriceChangeLine(TenantScopedModel):
    change = models.ForeignKey(PriceChange, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+")
    old_value = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    new_value = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["tenant", "change"], name="merch_change_line_idx"),
                   models.Index(fields=["tenant", "item"], name="merch_change_item_idx")]


class OfferKind(models.TextChoices):
    MAKING_PERCENT = "making_percent", _("Discount on the making charge (%)")
    MAKING_RATE = "making_rate", _("Making charge per gram becomes")
    CASHBACK_AMOUNT = "cashback_amount", _("Cashback per piece")
    CASHBACK_PERCENT = "cashback_percent", _("Cashback (% of the price)")


PRICE_KINDS = (OfferKind.MAKING_PERCENT, OfferKind.MAKING_RATE)
CASHBACK_KINDS = (OfferKind.CASHBACK_AMOUNT, OfferKind.CASHBACK_PERCENT)


class Offer(TenantScopedModel):
    """A price or cashback offer on chosen pieces between two dates. Empty lists mean "all"."""

    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=20, choices=OfferKind.choices)
    value = models.DecimalField(max_digits=18, decimal_places=4)
    starts_on = models.DateField()
    ends_on = models.DateField()
    is_active = models.BooleanField(default=True)  # switched off early, kept for the record
    category_ids = models.JSONField(default=list, blank=True)
    karat_ids = models.JSONField(default=list, blank=True)
    supplier_ids = models.JSONField(default=list, blank=True)
    branch_ids = models.JSONField(default=list, blank=True)
    barcodes = models.JSONField(default=list, blank=True)
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        indexes = [models.Index(fields=["tenant", "starts_on", "ends_on"],
                                name="merch_offer_dates_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(ends_on__gte=models.F("starts_on")),
                                   name="merch_offer_dates_check"),
            models.CheckConstraint(condition=Q(value__gt=0), name="merch_offer_value_check"),
        ]

    def __str__(self):
        return self.name
