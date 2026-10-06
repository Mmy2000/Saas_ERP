"""Work orders with workshops, and in-house production orders (§7.11, §8.5). Legacy: the issue /
receipt / loss rows of `Shh2`.

A work order is posted when gold is sent out (the workshop then owes that fine gold on its
account) and completed when the goods come back: new pieces and bulk gold into stock, scrap
back into the scrap lot, and the difference written to metal loss (هالك) or gain.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import Document, TenantScopedModel


class WorkOrderKind(models.TextChoices):
    MANUFACTURE = "manufacture", _("Making pieces")
    CASTING = "casting", _("Casting")
    REPAIR = "repair", _("Repair and polishing")


class WorkOrder(Document):
    # Empty for in-house production: the gold stays with us (in production) and our own
    # craftsman makes the goods.
    workshop = models.ForeignKey("parties.Party", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="+")
    craftsman = models.ForeignKey("hr.Employee", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="+")
    kind = models.CharField(max_length=12, choices=WorkOrderKind.choices,
                            default=WorkOrderKind.MANUFACTURE)
    issued_gross_weight_g = models.DecimalField(max_digits=16, decimal_places=3, default=0)
    issued_fine_weight_g = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    # Making cost already in the gold sent out; it moves into what comes back.
    carried_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")

    received_at = models.DateTimeField(null=True, blank=True)
    received_on = models.DateField(null=True, blank=True)  # business date of the receipt
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+")
    received_fine_weight_g = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    loss_fine_weight_g = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    gain_fine_weight_g = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    labour_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    receive_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")
    receipt_note = models.CharField(max_length=300, blank=True)

    class Meta(Document.Meta):
        ordering = ["-business_date", "-id"]
        indexes = [
            models.Index(fields=["tenant", "branch", "-business_date"], name="work_order_date_idx"),
            models.Index(fields=["tenant", "workshop"], name="work_order_workshop_idx"),
        ]

    def __str__(self):
        return self.number or f"#{self.pk}"

    @property
    def in_house(self) -> bool:
        return self.workshop_id is None

    @property
    def at_workshop(self) -> bool:
        """Out and not back yet: at the workshop, or in production in-house."""
        return self.status == "posted" and self.received_at is None

    @property
    def state(self) -> str:
        if self.status == "voided":
            return "cancelled"
        return "received" if self.received_at else "at_workshop"


class WorkOrderIssueLine(TenantScopedModel):
    """A weight of bulk gold or scrap sent out from a lot."""

    order = models.ForeignKey(WorkOrder, on_delete=models.CASCADE, related_name="issue_lines")
    lot = models.ForeignKey("inventory.StockLot", on_delete=models.PROTECT, related_name="+")
    category = models.ForeignKey("catalog.ItemCategory", on_delete=models.PROTECT,
                                 related_name="+")
    karat = models.ForeignKey("catalog.Karat", on_delete=models.PROTECT, related_name="+")
    qty = models.IntegerField(default=0)
    gross_weight_g = models.DecimalField(max_digits=14, decimal_places=3)
    fine_weight_g = models.DecimalField(max_digits=16, decimal_places=4)
    cost_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    metal_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)  # as booked

    class Meta:
        ordering = ["id"]
        constraints = [models.CheckConstraint(condition=Q(gross_weight_g__gt=0),
                                              name="work_order_issue_weight_check")]
        indexes = [models.Index(fields=["tenant", "order"], name="work_order_issue_idx")]


class ReceiptLineType(models.TextChoices):
    PIECES = "pieces", _("New pieces")
    BULK = "bulk", _("Gold by weight")
    SCRAP = "scrap", _("Scrap back")


class WorkOrderReceiptLine(TenantScopedModel):
    """What came back: pieces (each with its own weight), bulk gold, or scrap."""

    order = models.ForeignKey(WorkOrder, on_delete=models.CASCADE, related_name="receipt_lines")
    line_type = models.CharField(max_length=8, choices=ReceiptLineType.choices)
    category = models.ForeignKey("catalog.ItemCategory", on_delete=models.PROTECT,
                                 related_name="+")
    karat = models.ForeignKey("catalog.Karat", on_delete=models.PROTECT, related_name="+")
    qty = models.IntegerField(default=0)
    gross_weight_g = models.DecimalField(max_digits=14, decimal_places=3)
    fine_weight_g = models.DecimalField(max_digits=16, decimal_places=4)
    labour_rate = models.DecimalField(max_digits=18, decimal_places=4, default=0)  # per gram
    labour_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    list_making_rate = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    # Labour plus this line's share of the making cost carried in the gold sent out.
    cost_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    metal_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(condition=Q(gross_weight_g__gt=0, labour_rate__gte=0,
                                               list_making_rate__gte=0),
                                   name="work_order_receipt_values_check"),
        ]
        indexes = [models.Index(fields=["tenant", "order"], name="work_order_receipt_idx")]


class WorkOrderPiece(TenantScopedModel):
    """One new piece on a receipt line, and the Item created for it."""

    line = models.ForeignKey(WorkOrderReceiptLine, on_delete=models.CASCADE,
                             related_name="pieces")
    gross_weight_g = models.DecimalField(max_digits=14, decimal_places=3)
    item = models.OneToOneField("inventory.Item", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="+")

    class Meta:
        ordering = ["id"]
        constraints = [models.CheckConstraint(condition=Q(gross_weight_g__gt=0),
                                              name="work_order_piece_weight_check")]
        indexes = [models.Index(fields=["tenant", "line"], name="work_order_piece_line_idx")]
