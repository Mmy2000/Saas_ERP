"""Retail sales (§7.7). Legacy: `Cust.Cu2` headers with `Fn6` k=2 rows as lines, payment
tenders as columns (cash EGP/USD, card slots, gold trade-in per karat)."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import Document, TenantScopedModel


class PaymentTerms(models.TextChoices):
    CASH = "cash", _("Cash")
    CREDIT = "credit", _("On account")


class SalesInvoice(Document):
    customer = models.ForeignKey("parties.Party", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="+")
    # Walk-in customers are not parties: their name/phone are kept on the invoice only.
    customer_name = models.CharField(max_length=200, blank=True)
    customer_phone = models.CharField(max_length=30, blank=True)
    payment_terms = models.CharField(max_length=8, choices=PaymentTerms.choices,
                                     default=PaymentTerms.CASH)
    price_board = models.ForeignKey("pricing.MetalPriceBoard", on_delete=models.PROTECT,
                                    related_name="+")
    sold_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                on_delete=models.PROTECT, related_name="+")
    # All amounts in the company currency.
    subtotal_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    trade_in_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    change_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    balance_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")

    class Meta(Document.Meta):
        ordering = ["-business_date", "-id"]
        indexes = [models.Index(fields=["tenant", "branch", "-business_date"],
                                name="sales_invoice_date_idx")]

    def __str__(self):
        return self.number or f"#{self.pk}"

    @property
    def buyer(self) -> str:
        return self.customer.name if self.customer_id else self.customer_name


class SalesInvoiceLine(TenantScopedModel):
    """A sold piece, or a weight of bulk gold from a lot, with every price input captured so
    the invoice can be re-derived later."""

    invoice = models.ForeignKey(SalesInvoice, on_delete=models.CASCADE, related_name="lines")
    position = models.PositiveSmallIntegerField(default=0)
    item = models.ForeignKey("inventory.Item", null=True, blank=True, on_delete=models.PROTECT,
                             related_name="+")
    lot = models.ForeignKey("inventory.StockLot", null=True, blank=True, on_delete=models.PROTECT,
                            related_name="+")
    category = models.ForeignKey("catalog.ItemCategory", null=True, on_delete=models.PROTECT,
                                 related_name="+")
    qty = models.IntegerField(default=1)  # pieces: 1; bulk: pieces counted, may be 0
    karat = models.ForeignKey("catalog.Karat", on_delete=models.PROTECT, related_name="+")
    gross_weight_g = models.DecimalField(max_digits=14, decimal_places=3)
    fine_weight_g = models.DecimalField(max_digits=16, decimal_places=4)
    metal_price_per_g = models.DecimalField(max_digits=18, decimal_places=4)
    making_rate = models.DecimalField(max_digits=18, decimal_places=4)
    discount_rate = models.DecimalField(max_digits=9, decimal_places=6, default=0)
    making_rate_net = models.DecimalField(max_digits=18, decimal_places=4)
    metal_amount = models.DecimalField(max_digits=18, decimal_places=2)
    making_amount = models.DecimalField(max_digits=18, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    line_total = models.DecimalField(max_digits=18, decimal_places=2)
    cost_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)  # making cost
    metal_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)  # for COGS

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "invoice", "position"],
                                    name="sales_line_position_uniq"),
            models.CheckConstraint(condition=Q(discount_rate__gte=0, discount_rate__lte=1),
                                   name="sales_line_discount_check"),
            models.CheckConstraint(condition=(Q(item__isnull=False, lot__isnull=True)
                                              | Q(item__isnull=True, lot__isnull=False)),
                                   name="sales_line_item_xor_lot_check"),
        ]

    @property
    def label(self) -> str:
        """The barcode of a piece; bulk lines have none."""
        return self.item.barcode if self.item_id else ""


class SalesTradeIn(TenantScopedModel):
    """Scrap gold the customer hands over as part-payment (legacy `Cu2.g_w*/g_pay*`)."""

    invoice = models.ForeignKey(SalesInvoice, on_delete=models.CASCADE, related_name="trade_ins")
    karat = models.ForeignKey("catalog.Karat", on_delete=models.PROTECT, related_name="+")
    gross_weight_g = models.DecimalField(max_digits=14, decimal_places=3)
    loss_weight_g = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    net_weight_g = models.DecimalField(max_digits=14, decimal_places=3)
    fine_weight_g = models.DecimalField(max_digits=16, decimal_places=4)
    price_per_g = models.DecimalField(max_digits=18, decimal_places=4)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    price_overridden = models.BooleanField(default=False)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["tenant", "invoice"], name="sales_tradein_invoice_idx")]


class RefundMethod(models.TextChoices):
    CASH = "cash", _("Cash")
    CUSTOMER_CREDIT = "customer_credit", _("Customer's account")


class SalesReturn(Document):
    """Pieces coming back from a posted sale (legacy GHC return screens, `Fn6` k=1/zz=3)."""

    original_invoice = models.ForeignKey(SalesInvoice, on_delete=models.PROTECT,
                                         related_name="returns")
    customer = models.ForeignKey("parties.Party", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="+")
    refund_method = models.CharField(max_length=16, choices=RefundMethod.choices)
    returned_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    deduction_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    refund_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    cash_box = models.ForeignKey("treasury.CashBox", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")  # cash refunds
    reason = models.CharField(max_length=300, blank=True)
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")

    class Meta(Document.Meta):
        ordering = ["-business_date", "-id"]
        indexes = [models.Index(fields=["tenant", "branch", "-business_date"],
                                name="sales_return_date_idx")]

    def __str__(self):
        return self.number or f"#{self.pk}"


class SalesReturnLine(TenantScopedModel):
    sales_return = models.ForeignKey(SalesReturn, on_delete=models.CASCADE, related_name="lines")
    original_line = models.ForeignKey(SalesInvoiceLine, on_delete=models.PROTECT,
                                      related_name="return_lines")

    class Meta:
        indexes = [models.Index(fields=["tenant", "original_line"],
                                name="sales_returnline_orig_idx")]


class PaymentKind(models.TextChoices):
    CASH = "cash", _("Cash")
    CARD = "card", _("Card")
    BANK_TRANSFER = "bank_transfer", _("Bank transfer")
    DEPOSIT = "deposit", _("Reservation deposit")  # only when completing a reservation


class SalesPayment(TenantScopedModel):
    invoice = models.ForeignKey(SalesInvoice, on_delete=models.CASCADE, related_name="payments")
    kind = models.CharField(max_length=16, choices=PaymentKind.choices)
    currency = models.ForeignKey("catalog.Currency", on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    fx_rate = models.DecimalField(max_digits=18, decimal_places=8, default=1)
    functional_amount = models.DecimalField(max_digits=18, decimal_places=2)
    reference = models.CharField(max_length=60, blank=True)  # card slip / transfer reference
    # Where the money went (one of them, by kind).
    cash_box = models.ForeignKey("treasury.CashBox", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")
    bank_account = models.ForeignKey("treasury.BankAccount", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+")
    terminal = models.ForeignKey("treasury.CardTerminal", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["tenant", "invoice"], name="sales_payment_invoice_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="sales_payment_positive_check"),
        ]


class Reservation(Document):
    """Pieces held for a customer against deposits (§7.7; legacy reservation screens).

    Posted = open: the pieces are reserved and deposits can be added. Completing it posts a
    sale in which the deposits count as a payment; cancelling it releases the pieces and pays
    the deposits back (or leaves them on the customer's account). The document status stays
    posted after completion (`sale` is set); cancelling voids it."""

    customer = models.ForeignKey("parties.Party", on_delete=models.PROTECT, related_name="+")
    # Locked: completion prices the pieces on the board in force when they were reserved.
    price_locked = models.BooleanField(default=False)
    price_board = models.ForeignKey("pricing.MetalPriceBoard", on_delete=models.PROTECT,
                                    related_name="+")
    expires_on = models.DateField(null=True, blank=True)
    quoted_total = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    deposit_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)  # held now
    sale = models.OneToOneField(SalesInvoice, null=True, blank=True, on_delete=models.PROTECT,
                                related_name="reservation")
    completed_at = models.DateTimeField(null=True, blank=True)
    cancel_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+")

    class Meta(Document.Meta):
        ordering = ["-business_date", "-id"]
        indexes = [
            models.Index(fields=["tenant", "branch", "-business_date"],
                         name="reservation_date_idx"),
            models.Index(fields=["tenant", "customer"], name="reservation_customer_idx"),
        ]

    def __str__(self):
        return self.number or f"#{self.pk}"

    @property
    def state(self) -> str:
        if self.status == "voided":
            return "cancelled"
        if self.sale_id:
            return "completed"
        return "open"

    @property
    def is_overdue(self) -> bool:
        from django.utils import timezone

        return (self.state == "open" and self.expires_on is not None
                and self.expires_on < timezone.localdate())


class ReservationLine(TenantScopedModel):
    reservation = models.ForeignKey(Reservation, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+")
    discount_rate = models.DecimalField(max_digits=9, decimal_places=6, default=0)
    quoted_total = models.DecimalField(max_digits=18, decimal_places=2)  # when reserved

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "reservation", "item"],
                                    name="reservation_line_item_uniq"),
        ]


class ReservationDeposit(TenantScopedModel):
    """Money received (positive) or paid back (negative) against a reservation."""

    reservation = models.ForeignKey(Reservation, on_delete=models.CASCADE,
                                    related_name="deposits")
    number = models.CharField(max_length=32)
    business_date = models.DateField()
    kind = models.CharField(max_length=16, choices=PaymentKind.choices)
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
        ordering = ["business_date", "id"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "number"],
                                    name="reservation_deposit_no_uniq"),
        ]
        indexes = [models.Index(fields=["tenant", "reservation"], name="reservation_deposit_idx")]


# --- wholesale (trade) --------------------------------------------------------------------------

class SettlementBasis(models.TextChoices):
    """How a trade account pays for goods sold by weight (legacy `Sh2`)."""

    METAL = "metal", _("In gold")  # owes the fine grams, plus the making charge in money
    MONEY = "money", _("In money")  # owes the whole value at today's price


class TradeSale(Document):
    """Goods sold wholesale to another shop by weight plus a making charge per gram (§7.7)."""

    trade_account = models.ForeignKey("parties.Party", on_delete=models.PROTECT,
                                      related_name="+")
    settlement_basis = models.CharField(max_length=8, choices=SettlementBasis.choices)
    price_board = models.ForeignKey("pricing.MetalPriceBoard", on_delete=models.PROTECT,
                                    related_name="+")
    total_qty = models.IntegerField(default=0)
    total_gross_weight_g = models.DecimalField(max_digits=16, decimal_places=3, default=0)
    total_fine_weight_g = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    metal_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    making_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    # Money the trader owes: the making charge (metal basis) or everything (money basis).
    money_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")

    class Meta(Document.Meta):
        ordering = ["-business_date", "-id"]
        indexes = [
            models.Index(fields=["tenant", "branch", "-business_date"],
                         name="trade_sale_date_idx"),
            models.Index(fields=["tenant", "trade_account"], name="trade_sale_party_idx"),
        ]

    def __str__(self):
        return self.number or f"#{self.pk}"

    @property
    def in_metal(self) -> bool:
        return self.settlement_basis == SettlementBasis.METAL


class TradeSaleLine(TenantScopedModel):
    """A piece, or a weight of bulk gold from a lot."""

    trade_sale = models.ForeignKey(TradeSale, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("inventory.Item", null=True, blank=True, on_delete=models.PROTECT,
                             related_name="+")
    lot = models.ForeignKey("inventory.StockLot", null=True, blank=True, on_delete=models.PROTECT,
                            related_name="+")
    category = models.ForeignKey("catalog.ItemCategory", on_delete=models.PROTECT,
                                 related_name="+")
    karat = models.ForeignKey("catalog.Karat", on_delete=models.PROTECT, related_name="+")
    qty = models.IntegerField(default=1)
    gross_weight_g = models.DecimalField(max_digits=14, decimal_places=3)
    fine_weight_g = models.DecimalField(max_digits=16, decimal_places=4)
    metal_price_per_g = models.DecimalField(max_digits=18, decimal_places=4)  # board sell price
    making_rate = models.DecimalField(max_digits=18, decimal_places=4)  # per gross gram
    metal_amount = models.DecimalField(max_digits=18, decimal_places=2)  # gold at today's price
    making_amount = models.DecimalField(max_digits=18, decimal_places=2)
    cost_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)  # making cost
    metal_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)  # as booked

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(condition=(Q(item__isnull=False, lot__isnull=True)
                                              | Q(item__isnull=True, lot__isnull=False)),
                                   name="trade_sale_line_xor_check"),
            models.CheckConstraint(condition=Q(gross_weight_g__gt=0, making_rate__gte=0),
                                   name="trade_sale_line_values_check"),
        ]
        indexes = [models.Index(fields=["tenant", "trade_sale"], name="trade_sale_line_idx")]

    @property
    def label(self) -> str:
        return self.item.barcode if self.item_id else ""


class TradeReturn(Document):
    """Lines of a posted trade sale coming back; the trader's balance goes down by what they
    were charged for them."""

    original_sale = models.ForeignKey(TradeSale, on_delete=models.PROTECT,
                                      related_name="returns")
    trade_account = models.ForeignKey("parties.Party", on_delete=models.PROTECT,
                                      related_name="+")
    total_gross_weight_g = models.DecimalField(max_digits=16, decimal_places=3, default=0)
    total_fine_weight_g = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    money_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    reason = models.CharField(max_length=300, blank=True)
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")

    class Meta(Document.Meta):
        ordering = ["-business_date", "-id"]
        indexes = [
            models.Index(fields=["tenant", "branch", "-business_date"],
                         name="trade_return_date_idx"),
            models.Index(fields=["tenant", "original_sale"], name="trade_return_sale_idx"),
        ]

    def __str__(self):
        return self.number or f"#{self.pk}"


class TradeReturnLine(TenantScopedModel):
    trade_return = models.ForeignKey(TradeReturn, on_delete=models.CASCADE,
                                     related_name="lines")
    original_line = models.ForeignKey(TradeSaleLine, on_delete=models.PROTECT,
                                      related_name="return_lines")

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "trade_return"], name="trade_returnline_idx"),
            models.Index(fields=["tenant", "original_line"], name="trade_returnline_orig_idx"),
        ]
