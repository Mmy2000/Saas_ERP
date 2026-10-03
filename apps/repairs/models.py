"""Repairs and custom orders (§7.12). Legacy: `Fx1` (header, payments) and `Fx2` (lines).

The customer's pieces are not stock: only money is booked. Deposits are held for the
customer; the workshop's labour is a repair cost; the charge is income when delivered.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import Document, TenantScopedModel


class RepairKind(models.TextChoices):
    REPAIR = "repair", _("Repair")
    CUSTOM = "custom", _("Custom order")


class RepairOrder(Document):
    kind = models.CharField(max_length=8, choices=RepairKind.choices, default=RepairKind.REPAIR)
    customer = models.ForeignKey("parties.Party", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")
    customer_name = models.CharField(max_length=200, blank=True)  # walk-ins
    customer_phone = models.CharField(max_length=30, blank=True)
    bag_number = models.PositiveIntegerField(default=0)  # tag on the bag, per branch
    promised_on = models.DateField(null=True, blank=True)

    workshop = models.ForeignKey("parties.Party", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")
    sent_on = models.DateField(null=True, blank=True)
    ready_on = models.DateField(null=True, blank=True)
    labour_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    labour_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+")

    charge_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    deposit_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)  # held
    delivered_at = models.DateTimeField(null=True, blank=True)
    delivered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+")
    paid_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)  # at delivery
    change_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    balance_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)  # on account
    delivery_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                       on_delete=models.PROTECT, related_name="+")
    cancel_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+")

    class Meta(Document.Meta):
        ordering = ["-business_date", "-id"]
        indexes = [
            models.Index(fields=["tenant", "branch", "-business_date"], name="repair_date_idx"),
            models.Index(fields=["tenant", "customer"], name="repair_customer_idx"),
            models.Index(fields=["tenant", "branch", "bag_number"], name="repair_bag_idx"),
        ]

    def __str__(self):
        return self.number or f"#{self.pk}"

    @property
    def state(self) -> str:
        if self.status == "voided":
            return "cancelled"
        if self.delivered_at:
            return "delivered"
        if self.ready_on:
            return "ready"
        if self.sent_on:
            return "at_workshop"
        return "received"

    @property
    def is_open(self) -> bool:
        return self.status == "posted" and self.delivered_at is None

    @property
    def is_overdue(self) -> bool:
        from django.utils import timezone

        return (self.is_open and self.promised_on is not None
                and self.promised_on < timezone.localdate())

    @property
    def who(self) -> str:
        return self.customer.name if self.customer_id else self.customer_name

    @property
    def due_amount(self) -> object:
        return self.charge_amount - self.deposit_amount


class RepairLine(TenantScopedModel):
    order = models.ForeignKey(RepairOrder, on_delete=models.CASCADE, related_name="lines")
    description = models.CharField(max_length=300)
    karat = models.ForeignKey("catalog.Karat", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="+")
    weight_in_g = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    weight_out_g = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    charge_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    class Meta:
        ordering = ["id"]
        constraints = [models.CheckConstraint(
            condition=Q(weight_in_g__gte=0, charge_amount__gte=0),
            name="repair_line_values_check")]
        indexes = [models.Index(fields=["tenant", "order"], name="repair_line_order_idx")]

    @property
    def loss_g(self):
        return None if self.weight_out_g is None else self.weight_in_g - self.weight_out_g


class RepairPaymentStage(models.TextChoices):
    DEPOSIT = "deposit", _("Deposit")
    REFUND = "refund", _("Deposit returned")
    DELIVERY = "delivery", _("Payment on delivery")


class RepairPayment(TenantScopedModel):
    """Money through a box / bank account / terminal: a deposit, its refund, or a payment
    taken on delivery."""

    order = models.ForeignKey(RepairOrder, on_delete=models.CASCADE, related_name="payments")
    stage = models.CharField(max_length=8, choices=RepairPaymentStage.choices)
    number = models.CharField(max_length=32, blank=True)  # deposits and refunds
    business_date = models.DateField()
    kind = models.CharField(max_length=16)  # cash / card / bank_transfer
    currency = models.ForeignKey("catalog.Currency", on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    fx_rate = models.DecimalField(max_digits=18, decimal_places=8, default=1)
    functional_amount = models.DecimalField(max_digits=18, decimal_places=2)
    cash_box = models.ForeignKey("treasury.CashBox", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")
    bank_account = models.ForeignKey("treasury.BankAccount", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+")
    terminal = models.ForeignKey("treasury.CardTerminal", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["tenant", "order"], name="repair_payment_order_idx")]
