"""Settling balances with customers, suppliers and trade accounts (§7.8). Legacy: the
receipt/payment/settlement rows of `Cu2`, `Suu2` and `Sh2`, told apart by which number column
was set."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import Document


class SettlementKind(models.TextChoices):
    RECEIPT = "receipt", _("Money received")
    PAYMENT = "payment", _("Money paid")
    METAL_IN = "metal_in", _("Gold received")
    METAL_OUT = "metal_out", _("Gold given")
    CONVERSION = "conversion", _("Gold balance settled in money")


class PartySide(models.TextChoices):
    CUSTOMER = "customer", _("Customer")
    SUPPLIER = "supplier", _("Supplier")
    TRADE_ACCOUNT = "trade_account", _("Trade account")
    WORKSHOP = "workshop", _("Workshop")


class MoneyMethod(models.TextChoices):
    CASH = "cash", _("Cash")
    BANK_TRANSFER = "bank_transfer", _("Bank transfer")
    CARD = "card", _("Card")


class ConversionDirection(models.TextChoices):
    """Which way a gold balance turns into money (legacy "settle in gold value")."""

    WE_OWE_GOLD = "we_owe_gold", _("We owe them gold: pay it in money instead")
    THEY_OWE_GOLD = "they_owe_gold", _("They owe us gold: they pay money instead")


class Settlement(Document):
    kind = models.CharField(max_length=12, choices=SettlementKind.choices)
    side = models.CharField(max_length=16, choices=PartySide.choices)
    party = models.ForeignKey("parties.Party", on_delete=models.PROTECT, related_name="+")
    # Money kinds (receipt, payment) and the money side of a conversion.
    method = models.CharField(max_length=16, choices=MoneyMethod.choices, blank=True)
    currency = models.ForeignKey("catalog.Currency", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    fx_rate = models.DecimalField(max_digits=18, decimal_places=8, default=1)
    functional_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    cash_box = models.ForeignKey("treasury.CashBox", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")
    bank_account = models.ForeignKey("treasury.BankAccount", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+")
    terminal = models.ForeignKey("treasury.CardTerminal", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")
    # Metal kinds (gold in/out as scrap of a karat) and conversions (fine grams).
    karat = models.ForeignKey("catalog.Karat", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="+")
    gross_weight_g = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    fine_weight_g = models.DecimalField(max_digits=16, decimal_places=4, null=True, blank=True)
    direction = models.CharField(max_length=16, choices=ConversionDirection.choices, blank=True)
    price_per_fine_g = models.DecimalField(max_digits=18, decimal_places=4, null=True,
                                           blank=True)
    reference = models.CharField(max_length=60, blank=True)
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")

    class Meta(Document.Meta):
        ordering = ["-business_date", "-id"]
        indexes = [models.Index(fields=["tenant", "branch", "-business_date"],
                                name="settlement_date_idx"),
                   models.Index(fields=["tenant", "party"], name="settlement_party_idx")]

    def __str__(self):
        return self.number or f"#{self.pk}"
