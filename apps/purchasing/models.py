"""Supplier invoices: goods receipt from a supplier (§7.8). Legacy: `Sup.Su2` + `Suu2(xo1)`."""

from __future__ import annotations

from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import Document, TenantScopedModel


class SellerRole(models.TextChoices):
    """Who the goods come from: a supplier, or a trade account (a shop that also sells to you;
    its balance is the same one wholesale sales post to)."""

    SUPPLIER = "supplier", _("Supplier")
    TRADE_ACCOUNT = "trade_account", _("Trade account")


#: Ledger account role holding the balance with each kind of seller.
SELLER_ACCOUNT = {SellerRole.SUPPLIER: "suppliers", SellerRole.TRADE_ACCOUNT: "trade_accounts"}
#: Web page of each kind of seller.
SELLER_PAGE = {SellerRole.SUPPLIER: "supplier-edit", SellerRole.TRADE_ACCOUNT: "trade-account-edit"}


def seller_from_query(params) -> tuple[str, object]:
    """(seller_role, party) chosen by ?from=trade_account&party=ID on a "new" link."""
    from apps.parties.models import Party

    role = params.get("from") if params.get("from") in SellerRole.values else SellerRole.SUPPLIER
    party_id = params.get("party", "")
    party = (Party.objects.filter(pk=int(party_id), roles__role=role, is_active=True).first()
             if party_id.isdigit() else None)
    return role, party


class SellerMixin(models.Model):
    seller_role = models.CharField(max_length=16, choices=SellerRole.choices,
                                   default=SellerRole.SUPPLIER)

    class Meta:
        abstract = True

    @property
    def seller_page(self) -> str:
        return SELLER_PAGE[self.seller_role]


class SupplierInvoice(SellerMixin, Document):
    supplier = models.ForeignKey("parties.Party", on_delete=models.PROTECT, related_name="+")
    supplier_reference = models.CharField(max_length=60, blank=True)  # their invoice number
    # Currency of the making charges (gold itself is owed in fine grams).
    currency = models.ForeignKey("catalog.Currency", on_delete=models.PROTECT, related_name="+")
    # Company-currency units per invoice-currency unit; blank on drafts = use the rate in force
    # on the business date when posting.
    fx_rate = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")

    class Meta(Document.Meta):
        ordering = ["-business_date", "-id"]
        indexes = [models.Index(fields=["tenant", "branch", "-business_date"],
                                name="purch_invoice_date_idx")]

    def __str__(self):
        return self.number or f"draft #{self.pk}"


class SupplierInvoiceLine(TenantScopedModel):
    invoice = models.ForeignKey(SupplierInvoice, on_delete=models.CASCADE, related_name="lines")
    position = models.PositiveSmallIntegerField(default=0)
    category = models.ForeignKey("catalog.ItemCategory", on_delete=models.PROTECT,
                                 related_name="+")
    karat = models.ForeignKey("catalog.Karat", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="+")  # none for watches and other non-metal goods
    qty = models.PositiveIntegerField(default=0)  # pieces (serialized) or count in the lot
    gross_weight_g = models.DecimalField(max_digits=14, decimal_places=3)
    fine_weight_g = models.DecimalField(max_digits=16, decimal_places=4, default=0)
    making_cost_rate = models.DecimalField(max_digits=18, decimal_places=4, default=0)  # per g
    making_cost_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    list_making_rate = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "invoice", "position"],
                                    name="purchasing_line_position_uniq"),
            models.CheckConstraint(condition=Q(gross_weight_g__gt=0, making_cost_rate__gte=0,
                                               list_making_rate__gte=0),
                                   name="purchasing_line_values_check"),
        ]


class SupplierInvoicePiece(TenantScopedModel):
    """One serialized piece on a line: its own weight, and the Item created when posted."""

    line = models.ForeignKey(SupplierInvoiceLine, on_delete=models.CASCADE, related_name="pieces")
    gross_weight_g = models.DecimalField(max_digits=14, decimal_places=3)
    item = models.OneToOneField("inventory.Item", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="+")
    # Diamond pieces and loose stones (apps.diamonds): carats, what the stones cost (invoice
    # currency), the selling price, and each stone's details.
    stone_weight_ct = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    stone_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    label_price = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    stones = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(condition=Q(gross_weight_g__gt=0),
                                   name="purchasing_piece_weight_check"),
        ]
        indexes = [models.Index(fields=["tenant", "line"], name="purchasing_piece_line_idx")]


# --- supplier returns ---------------------------------------------------------------------------

class SupplierReturn(SellerMixin, Document):
    """Goods sent back to a supplier (§7.8). The supplier's gold and money balances go down by
    the fine gold (at today's value) and the making cost paid for it."""

    supplier = models.ForeignKey("parties.Party", on_delete=models.PROTECT, related_name="+")
    total_qty = models.IntegerField(default=0)
    total_gross_weight_g = models.DecimalField(max_digits=16, decimal_places=3, default=0)
    total_fine_weight_g = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    total_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0)  # making cost
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")

    class Meta(Document.Meta):
        ordering = ["-business_date", "-id"]
        indexes = [
            models.Index(fields=["tenant", "branch", "-business_date"],
                         name="supplier_return_date_idx"),
            models.Index(fields=["tenant", "supplier"], name="supplier_return_party_idx"),
        ]

    def __str__(self):
        return self.number or f"#{self.pk}"


class SupplierReturnLine(TenantScopedModel):
    """A piece, or a weight of bulk gold from a lot."""

    supplier_return = models.ForeignKey(SupplierReturn, on_delete=models.CASCADE,
                                        related_name="lines")
    item = models.ForeignKey("inventory.Item", null=True, blank=True, on_delete=models.PROTECT,
                             related_name="+")
    lot = models.ForeignKey("inventory.StockLot", null=True, blank=True,
                            on_delete=models.PROTECT, related_name="+")
    category = models.ForeignKey("catalog.ItemCategory", on_delete=models.PROTECT,
                                 related_name="+")
    karat = models.ForeignKey("catalog.Karat", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="+")
    qty = models.IntegerField(default=0)
    gross_weight_g = models.DecimalField(max_digits=14, decimal_places=3)
    fine_weight_g = models.DecimalField(max_digits=16, decimal_places=4)
    cost_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    metal_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)  # as booked

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(condition=(Q(item__isnull=False, lot__isnull=True)
                                              | Q(item__isnull=True, lot__isnull=False)),
                                   name="supplier_return_line_xor_check"),
            models.CheckConstraint(condition=Q(gross_weight_g__gt=0),
                                   name="supplier_return_line_weight_check"),
        ]
        indexes = [models.Index(fields=["tenant", "supplier_return"],
                                name="supplier_return_line_idx")]


# --- scrap gold ---------------------------------------------------------------------------------

class ScrapPayment(models.TextChoices):
    CASH = "cash", _("Cash")
    BANK_TRANSFER = "bank_transfer", _("Bank transfer")
    ACCOUNT = "account", _("On their account")


class ScrapPurchase(Document):
    """Scrap gold bought from a walk-in seller or a customer (§7.8; legacy scrap purchase
    screens). Goes into the branch's scrap stock per karat."""

    seller = models.ForeignKey("parties.Party", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="+")
    # Walk-in sellers: who sold it (kept for anti-money-laundering checks).
    seller_name = models.CharField(max_length=200, blank=True)
    seller_phone = models.CharField(max_length=30, blank=True)
    seller_id_encrypted = models.TextField(blank=True)
    price_board = models.ForeignKey("pricing.MetalPriceBoard", on_delete=models.PROTECT,
                                    related_name="+")
    payment = models.CharField(max_length=16, choices=ScrapPayment.choices)
    cash_box = models.ForeignKey("treasury.CashBox", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")
    bank_account = models.ForeignKey("treasury.BankAccount", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+")
    total_gross_weight_g = models.DecimalField(max_digits=16, decimal_places=3, default=0)
    total_fine_weight_g = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")

    class Meta(Document.Meta):
        ordering = ["-business_date", "-id"]
        indexes = [models.Index(fields=["tenant", "branch", "-business_date"],
                                name="scrap_purchase_date_idx")]

    def __str__(self):
        return self.number or f"#{self.pk}"

    @property
    def seller_label(self) -> str:
        return self.seller.name if self.seller_id else self.seller_name

    @property
    def seller_id_number(self) -> str:
        from apps.core.crypto import decrypt

        return decrypt(self.seller_id_encrypted) if self.seller_id_encrypted else ""


class ScrapPurchaseLine(TenantScopedModel):
    purchase = models.ForeignKey(ScrapPurchase, on_delete=models.CASCADE, related_name="lines")
    karat = models.ForeignKey("catalog.Karat", on_delete=models.PROTECT, related_name="+")
    gross_weight_g = models.DecimalField(max_digits=14, decimal_places=3)
    loss_weight_g = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    net_weight_g = models.DecimalField(max_digits=14, decimal_places=3)
    fine_weight_g = models.DecimalField(max_digits=16, decimal_places=4)
    price_per_g = models.DecimalField(max_digits=18, decimal_places=4)
    price_overridden = models.BooleanField(default=False)
    amount = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(condition=Q(net_weight_g__gt=0, amount__gt=0),
                                   name="scrap_purchase_line_check"),
        ]
        indexes = [models.Index(fields=["tenant", "purchase"], name="scrap_purchase_line_idx")]


class ScrapSale(Document):
    """Scrap gold sold for money to a dealer, a supplier or a customer, out of the branch's
    scrap stock. The difference to what the scrap cost is a metal gain or loss."""

    buyer = models.ForeignKey("parties.Party", on_delete=models.PROTECT, related_name="+")
    payment = models.CharField(max_length=16, choices=ScrapPayment.choices)
    cash_box = models.ForeignKey("treasury.CashBox", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")
    bank_account = models.ForeignKey("treasury.BankAccount", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+")
    total_gross_weight_g = models.DecimalField(max_digits=16, decimal_places=3, default=0)
    total_fine_weight_g = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")

    class Meta(Document.Meta):
        ordering = ["-business_date", "-id"]
        indexes = [models.Index(fields=["tenant", "branch", "-business_date"],
                                name="scrap_sale_date_idx")]

    def __str__(self):
        return self.number or f"#{self.pk}"

    @property
    def gain(self):
        return self.total_amount - self.total_cost


class ScrapSaleLine(TenantScopedModel):
    sale = models.ForeignKey(ScrapSale, on_delete=models.CASCADE, related_name="lines")
    karat = models.ForeignKey("catalog.Karat", on_delete=models.PROTECT, related_name="+")
    gross_weight_g = models.DecimalField(max_digits=14, decimal_places=3)
    fine_weight_g = models.DecimalField(max_digits=16, decimal_places=4)
    price_per_g = models.DecimalField(max_digits=18, decimal_places=4)  # per gram of the karat
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    cost_amount = models.DecimalField(max_digits=18, decimal_places=2)  # what the scrap cost

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(condition=Q(gross_weight_g__gt=0, amount__gt=0),
                                   name="scrap_sale_line_check"),
        ]
        indexes = [models.Index(fields=["tenant", "sale"], name="scrap_sale_line_idx")]
