"""Diamonds and gemstones (switched on per client from the platform console).

A diamond piece is an ordinary barcoded Item (family "diamond") whose stones are described here,
with its stones' cost on the item (stone_cost_amount) and a fixed label price. A loose stone, or
a parcel of stones sold together, is an Item of family "stone" without a karat.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.media import TenantUploadPath
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
    # …and the loose stone it came from, so taking that stone out again removes these rows.
    source = models.ForeignKey("inventory.Item", null=True, blank=True, on_delete=models.SET_NULL,
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
    # Done from a setting job: the piece and stones were with the setter (SettingJob).
    job = models.ForeignKey("SettingJob", null=True, blank=True, on_delete=models.PROTECT,
                            related_name="settings")

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
    removal = models.ForeignKey("StoneRemoval", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="stones")  # taken out of the piece again

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "setting", "stone"],
                                               name="diamonds_setting_stone_uniq")]


class SettingJob(Document):
    """Pieces and loose stones handed to a setter (legacy Install, GHN1–GHN4). While with the
    setter they are out of stock ("at workshop") and their value sits on "goods at
    workshops". Each comes back set into a piece (a StoneSetting of this job) or unchanged."""

    setter = models.ForeignKey("parties.Party", on_delete=models.PROTECT, related_name="+")
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")
    closed_at = models.DateTimeField(null=True, blank=True)  # nothing left with the setter

    class Meta(Document.Meta):
        ordering = ["-business_date", "-id"]
        indexes = [models.Index(fields=["tenant", "branch", "-business_date"],
                                name="diamonds_job_date_idx"),
                   models.Index(fields=["tenant", "setter"], name="diamonds_job_setter_idx")]

    def __str__(self):
        return self.number or f"#{self.pk}"


class JobLineState(models.TextChoices):
    OUT = "out", _("With the setter")
    SET = "set", _("Set")
    RETURNED = "returned", _("Returned")


class SettingJobLine(TenantScopedModel):
    job = models.ForeignKey(SettingJob, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+")
    state = models.CharField(max_length=10, choices=JobLineState.choices,
                             default=JobLineState.OUT)
    metal_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)  # when handed
    # Its category when handed over: a gold mounting set with stones becomes a diamond piece,
    # and goes back to this if the setting is cancelled.
    category = models.ForeignKey("catalog.ItemCategory", null=True, on_delete=models.PROTECT,
                                 related_name="+")
    setting = models.ForeignKey(StoneSetting, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="+")
    return_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+")
    returned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "job", "item"],
                                               name="diamonds_job_item_uniq")]
        indexes = [models.Index(fields=["tenant", "item"], name="diamonds_jobline_item_idx")]


class StoneRemoval(Document):
    """Stones taken out of a piece again (legacy GHN4 "فك"): the loose stones come back to
    stock with their own barcodes and cost, the piece is re-weighed, and a fee for the work
    may go to the setter."""

    piece = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+")
    setting = models.ForeignKey(StoneSetting, on_delete=models.PROTECT, related_name="removals")
    setter = models.ForeignKey("parties.Party", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="+")
    gross_before_g = models.DecimalField(max_digits=14, decimal_places=3)
    gross_after_g = models.DecimalField(max_digits=14, decimal_places=3)
    stones_carat = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    stones_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    fee_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")

    class Meta(Document.Meta):
        ordering = ["-business_date", "-id"]
        indexes = [models.Index(fields=["tenant", "branch", "-business_date"],
                                name="diamonds_removal_date_idx")]

    def __str__(self):
        return self.number or f"#{self.pk}"


class CheckStatus(models.TextChoices):
    VERIFIED = "verified", _("Found at the lab")
    NOT_FOUND = "not_found", _("Not found at the lab")
    ERROR = "error", _("Could not check")


class CertificateCheck(TenantScopedModel):
    """A lab report looked up by its number (legacy GHD52, IgiVerification): what the lab
    answered, and the report's PDF, kept so it is not fetched again."""

    lab = models.CharField(max_length=10, default="IGI")
    report_no = models.CharField(max_length=60)
    status = models.CharField(max_length=10, choices=CheckStatus.choices)
    message = models.CharField(max_length=300, blank=True)
    data = models.JSONField(default=dict, blank=True)
    pdf = models.FileField(upload_to=TenantUploadPath("certificates"), blank=True)
    checked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    checked_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "lab", "report_no"],
                                               name="diamonds_check_report_uniq")]

    def __str__(self):
        return f"{self.lab} {self.report_no}"


class BuyBack(Document):
    """Old diamond jewellery and loose stones bought from a customer or a walk-in (legacy
    GHD40 "شراء ماس كسر"). Unlike Gweb, each one becomes a barcoded piece in stock: its gold
    on gold inventory at the gold part of the price, its stones at the rest."""

    seller = models.ForeignKey("parties.Party", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="+")  # a customer; empty: a walk-in
    seller_name = models.CharField(max_length=200, blank=True)
    seller_phone = models.CharField(max_length=30, blank=True)
    seller_id_encrypted = models.TextField(blank=True)
    payment = models.CharField(max_length=16)  # cash | bank_transfer | account
    cash_box = models.ForeignKey("treasury.CashBox", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")
    bank_account = models.ForeignKey("treasury.BankAccount", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+")
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    gold_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")

    class Meta(Document.Meta):
        ordering = ["-business_date", "-id"]
        indexes = [models.Index(fields=["tenant", "branch", "-business_date"],
                                name="diamonds_buyback_date_idx")]

    def __str__(self):
        return self.number or f"#{self.pk}"

    @property
    def seller_label(self) -> str:
        return self.seller.name if self.seller_id else self.seller_name


class BuyBackLine(TenantScopedModel):
    buy_back = models.ForeignKey(BuyBack, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+")
    price = models.DecimalField(max_digits=18, decimal_places=2)
    gold_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    class Meta:
        indexes = [models.Index(fields=["tenant", "buy_back"], name="diamonds_buyback_line_idx")]
