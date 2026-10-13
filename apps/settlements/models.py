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
    TRANSFER = "transfer", _("Balance moved to another account")
    ADJUSTMENT = "adjustment", _("Balance adjustment")


class PartySide(models.TextChoices):
    CUSTOMER = "customer", _("Customer")
    SUPPLIER = "supplier", _("Supplier")
    TRADE_ACCOUNT = "trade_account", _("Trade account")
    WORKSHOP = "workshop", _("Workshop")
    PARTNER = "partner", _("Partner")


class MoneyMethod(models.TextChoices):
    CASH = "cash", _("Cash")
    BANK_TRANSFER = "bank_transfer", _("Bank transfer")
    CARD = "card", _("Card")


class ConversionDirection(models.TextChoices):
    """Which way a gold balance turns into money (legacy "settle in gold value")."""

    WE_OWE_GOLD = "we_owe_gold", _("We owe them gold: pay it in money instead")
    THEY_OWE_GOLD = "they_owe_gold", _("They owe us gold: they pay money instead")


class BalanceAsset(models.TextChoices):
    """What a transfer or an adjustment moves: money in a currency, or gold (fine grams)."""

    MONEY = "money", _("Money")
    GOLD = "gold", _("Gold")


class AdjustmentDirection(models.TextChoices):
    RAISE = "raise", _("Add to what they owe us")
    LOWER = "lower", _("Take off what they owe us")


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
    # Money paid in one currency against a balance kept in another, at a rate agreed on the
    # spot (legacy GHSS33 / GHSS34): `amount` of `currency` takes `balance_amount` of
    # `balance_currency` off (or onto) the party's balance; `balance_rate` = currency units per
    # one balance-currency unit. Empty: the balance is in the currency paid.
    balance_currency = models.ForeignKey("catalog.Currency", null=True, blank=True,
                                         on_delete=models.PROTECT, related_name="+")
    balance_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    balance_rate = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
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
    # Gold received: grams taken off for impurities before it counts (legacy خسية, GHSH15a).
    loss_weight_g = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    fine_weight_g = models.DecimalField(max_digits=16, decimal_places=4, null=True, blank=True)
    direction = models.CharField(max_length=16, choices=ConversionDirection.choices, blank=True)
    price_per_fine_g = models.DecimalField(max_digits=18, decimal_places=4, null=True,
                                           blank=True)
    # Transfers: the balance is taken from `party` and given to `counter_party` (legacy GHC29,
    # GHSS17). Adjustments: `party`'s balance goes up or down against "balance adjustments"
    # (legacy GHC25, GHSS36, GHP17, GHSHH17).
    asset = models.CharField(max_length=8, choices=BalanceAsset.choices, blank=True)
    counter_side = models.CharField(max_length=16, choices=PartySide.choices, blank=True)
    counter_party = models.ForeignKey("parties.Party", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")
    adjustment = models.CharField(max_length=8, choices=AdjustmentDirection.choices, blank=True)
    reference = models.CharField(max_length=60, blank=True)
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")

    class Meta(Document.Meta):
        ordering = ["-business_date", "-id"]
        indexes = [models.Index(fields=["tenant", "branch", "-business_date"],
                                name="settlement_date_idx"),
                   models.Index(fields=["tenant", "party"], name="settlement_party_idx"),
                   models.Index(fields=["tenant", "counter_party"],
                                name="settlement_counter_idx")]

    def __str__(self):
        return self.number or f"#{self.pk}"

    @property
    def net_weight_g(self):
        """What counts: the weight less the loss taken off (gold in and out)."""
        if self.gross_weight_g is None:
            return None
        return self.gross_weight_g - (self.loss_weight_g or 0)
