"""Diamonds and gemstones (switched on per client from the platform console).

A diamond piece is an ordinary barcoded Item (family "diamond") whose stones are described here,
with its stones' cost on the item (stone_cost_amount) and a fixed label price. A loose stone, or
a parcel of stones sold together, is an Item of family "stone" without a karat.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import Document, TenantScopedModel


class StoneKind(models.TextChoices):
    DIAMOND = "diamond", _("Diamond")
    RUBY = "ruby", _("Ruby")
    EMERALD = "emerald", _("Emerald (gem)")
    SAPPHIRE = "sapphire", _("Sapphire")
    PEARL = "pearl", _("Pearl")
    OTHER = "other", _("Other stone")


class StoneShape(models.TextChoices):
    ROUND = "round", _("Round")
    PRINCESS = "princess", _("Princess")
    OVAL = "oval", _("Oval")
    PEAR = "pear", _("Pear")
    MARQUISE = "marquise", _("Marquise")
    EMERALD_CUT = "emerald_cut", _("Emerald cut")
    CUSHION = "cushion", _("Cushion")
    HEART = "heart", _("Heart")
    BAGUETTE = "baguette", _("Baguette")
    OTHER = "other", _("Other shape")


class ItemStone(TenantScopedModel):
    """A stone, or a group of identical stones, in a piece (or a loose stone/parcel)."""

    item = models.ForeignKey("inventory.Item", on_delete=models.CASCADE, related_name="stones")
    kind = models.CharField(max_length=12, choices=StoneKind.choices, default=StoneKind.DIAMOND)
    shape = models.CharField(max_length=12, choices=StoneShape.choices, blank=True)
    count = models.PositiveIntegerField(default=1)
    carat = models.DecimalField(max_digits=10, decimal_places=3)  # all `count` stones together
    color = models.CharField(max_length=20, blank=True)  # D…Z for diamonds
    clarity = models.CharField(max_length=20, blank=True)  # IF, VVS1… for diamonds
    cut = models.CharField(max_length=20, blank=True)  # Excellent, Very good…
    lab = models.CharField(max_length=40, blank=True)  # GIA, IGI, HRD…
    certificate_no = models.CharField(max_length=60, blank=True)
    note = models.CharField(max_length=200, blank=True)
    # Set from a loose stone by a stone setting (cancelling the setting removes it).
    setting = models.ForeignKey("StoneSetting", null=True, blank=True, on_delete=models.CASCADE,
                                related_name="+")

    class Meta:
        ordering = ["-carat", "id"]
        constraints = [models.CheckConstraint(condition=Q(carat__gt=0, count__gte=1),
                                              name="diamonds_stone_values_check")]
        indexes = [models.Index(fields=["tenant", "item"], name="diamonds_stone_item_idx"),
                   models.Index(fields=["tenant", "certificate_no"],
                                name="diamonds_stone_cert_idx")]


class StoneSetting(Document):
    """Loose stones set into a piece, by a setter (a workshop) or in our own shop. The stones
    leave stock ("used in another piece"); their details and cost move onto the piece, which
    is re-weighed; the setter's labour is added to the piece's cost."""

    piece = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+")
    setter = models.ForeignKey("parties.Party", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="+")  # empty: set in our own shop
    gross_before_g = models.DecimalField(max_digits=14, decimal_places=3)
    gross_after_g = models.DecimalField(max_digits=14, decimal_places=3)
    stones_carat = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    stones_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    labour_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")

    class Meta(Document.Meta):
        ordering = ["-business_date", "-id"]
        indexes = [models.Index(fields=["tenant", "branch", "-business_date"],
                                name="diamonds_setting_date_idx")]

    def __str__(self):
        return self.number or f"#{self.pk}"


class StoneSettingStone(TenantScopedModel):
    setting = models.ForeignKey(StoneSetting, on_delete=models.CASCADE, related_name="stones")
    stone = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+")
    carat = models.DecimalField(max_digits=12, decimal_places=3)
    cost = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "setting", "stone"],
                                               name="diamonds_setting_stone_uniq")]
