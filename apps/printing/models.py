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


class DocLayout(models.TextChoices):
    CLASSIC = "classic", _("Classic")
    MODERN = "modern", _("Modern")
    THERMAL = "thermal", _("Thermal slip (80 mm)")
    BUILDER = "builder", _("My design (designer)")


class Paper(models.TextChoices):
    A4 = "a4", _("A4")
    A5 = "a5", _("A5")
    ROLL80 = "roll80", _("80 mm roll")


class DocLanguage(models.TextChoices):
    WORKSPACE = "", _("The workspace language")
    AR = "ar", _("Arabic")
    EN = "en", _("English")
    BOTH = "both", _("Arabic and English")


class DocumentDesign(TenantScopedModel):
    """How one document type prints for this client (apps.printing.documents). Clients set the
    layout and options; only platform staff write `custom_html` (console)."""

    doc_type = models.CharField(max_length=40)
    # Another document type whose look this one uses ("" = its own design). Columns and
    # details stay this type's own. See apps.printing.render.design_for.
    follows = models.CharField(max_length=40, blank=True, default="")
    layout = models.CharField(max_length=12, choices=DocLayout.choices, default=DocLayout.CLASSIC)
    paper = models.CharField(max_length=8, choices=Paper.choices, default=Paper.A4)
    lang = models.CharField(max_length=4, choices=DocLanguage.choices, blank=True, default="")
    color = models.CharField(max_length=7, blank=True)  # #rrggbb; empty = the workspace accent
    show_logo = models.BooleanField(default=True)
    header_note = models.CharField(max_length=300, blank=True)
    footer_note = models.CharField(max_length=300, blank=True)
    terms = models.TextField(blank=True)
    columns = models.JSONField(default=list, blank=True)  # [] = the type's defaults
    options = models.JSONField(default=dict, blank=True)
    copies = models.PositiveSmallIntegerField(default=1)
    # The drag-and-drop designer (apps.printing.builder): validated blocks and page settings.
    blocks = models.JSONField(default=list, blank=True)
    page = models.JSONField(default=dict, blank=True)
    # Platform staff only (console): a full HTML template over the same `doc` data.
    custom_html = models.TextField(blank=True)
    use_custom = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "doc_type"],
                                               name="printing_documentdesign_type_uniq")]

    def __str__(self):
        return self.doc_type

    @property
    def follows_label(self) -> str:
        from .documents import TYPES

        return str(TYPES[self.follows].label) if self.follows in TYPES else ""

    @property
    def language(self) -> str:
        from django.utils.translation import get_language

        return self.lang or (get_language() or "ar")[:2]

    def enabled_columns(self, doc_type) -> list[str]:
        from .documents import TYPES

        kind = TYPES[doc_type]
        chosen = self.columns or kind.default_columns()
        return [c.key for c in kind.columns if c.fixed or c.key in chosen]

    def option(self, doc_type, key) -> bool:
        from .documents import TYPES

        defaults = {k: on for k, _label, on in TYPES[doc_type].options}
        return bool(self.options.get(key, defaults.get(key, False)))


class DocumentDesignVersion(TenantScopedModel):
    """Earlier custom HTML of a design, kept on every save so staff can go back."""

    design = models.ForeignKey(DocumentDesign, on_delete=models.CASCADE, related_name="versions")
    custom_html = models.TextField()
    saved_by = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["tenant", "design"], name="printing_docver_design_idx")]

    def __str__(self):
        return f"{self.design_id} @ {self.created_at:%Y-%m-%d %H:%M}"
