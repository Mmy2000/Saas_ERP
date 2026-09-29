"""Label templates (§18; legacy `Cod.LabelTemplate*`, `designer.DesignTemplate`).

A template is a label size plus the fields printed on it. Roll media print one label per page
(thermal printers, also as ZPL for Zebra); sheet media lay labels out in a grid on a page
(laser printers). A "split" layout is the jewellery tail tag: two printable wings with the fold
between them.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantScopedModel


class Media(models.TextChoices):
    ROLL = "roll", _("Roll (one label at a time)")
    SHEET = "sheet", _("Sheet (several labels per page)")


class Layout(models.TextChoices):
    SINGLE = "single", _("One area")
    SPLIT = "split", _("Two wings (jewellery tail tag)")


class LabelTemplate(TenantScopedModel):
    name = models.CharField(max_length=100)
    media = models.CharField(max_length=8, choices=Media.choices, default=Media.ROLL)
    layout = models.CharField(max_length=8, choices=Layout.choices, default=Layout.SINGLE)
    width_mm = models.DecimalField(max_digits=6, decimal_places=2)
    height_mm = models.DecimalField(max_digits=6, decimal_places=2)
    tail_mm = models.DecimalField(max_digits=6, decimal_places=2, default=0)  # fold between wings
    fields = models.JSONField(default=list)  # first area / wing, in order
    fields_b = models.JSONField(default=list, blank=True)  # second wing
    font_size_pt = models.DecimalField(max_digits=4, decimal_places=1, default=Decimal("6"))
    barcode_height_mm = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("5"))
    # Sheet media only.
    page_width_mm = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("210"))
    page_height_mm = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("297"))
    columns = models.PositiveSmallIntegerField(default=1)
    rows = models.PositiveSmallIntegerField(default=1)
    margin_top_mm = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    margin_start_mm = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    gap_x_mm = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    gap_y_mm = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-is_default", "name"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "name"], name="printing_label_name_uniq"),
            models.UniqueConstraint(fields=["tenant"], condition=Q(is_default=True),
                                    name="printing_label_one_default_uniq"),
            models.CheckConstraint(condition=Q(width_mm__gt=0, height_mm__gt=0, tail_mm__gte=0,
                                               font_size_pt__gt=0, columns__gte=1, rows__gte=1),
                                   name="printing_label_size_check"),
        ]

    def __str__(self):
        return self.name

    @property
    def area_width_mm(self) -> Decimal:
        """Width of one printable area (a wing, for split tags)."""
        if self.layout == Layout.SPLIT:
            return (self.width_mm - self.tail_mm) / 2
        return self.width_mm

    @property
    def per_page(self) -> int:
        return self.columns * self.rows if self.media == Media.SHEET else 1
