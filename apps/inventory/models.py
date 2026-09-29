"""Stock (§7.5, ADR-007).

* Item: one row per physical serialized piece for its whole life (the legacy system inserted a
  new row per event and reused the code). Its `status` and `branch` say where it is now.
* StockLot: a bulk position (category × karat × branch), e.g. chain by weight. Quantities and
  weights are never stored on the lot; they are the sum of its movements (LotBalance).
* StockMovement: append-only history of every change to an item or lot.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import Document, TenantScopedModel


class ItemType(models.TextChoices):
    GOLD_PIECE = "gold_piece", _("Gold piece")
    SILVER_PIECE = "silver_piece", _("Silver piece")
    DIAMOND_PIECE = "diamond_piece", _("Diamond piece")
    STONE = "stone", _("Stone")
    BULLION = "bullion", _("Bullion")
    COIN = "coin", _("Coin")
    OTHER = "other", _("Other")


class ItemStatus(models.TextChoices):
    IN_STOCK = "in_stock", _("In stock")
    IN_TRANSIT = "in_transit", _("In transit")
    RESERVED = "reserved", _("Reserved")
    SOLD = "sold", _("Sold")
    AT_WORKSHOP = "at_workshop", _("At workshop")
    CONSUMED = "consumed", _("Used in another piece")
    RETURNED_TO_SUPPLIER = "returned_to_supplier", _("Returned to supplier")
    SCRAPPED = "scrapped", _("Scrapped")
    MISSING = "missing", _("Missing")
    VOIDED = "voided", _("Cancelled")


# Physically in the shop and owned: counted in stock value and in stocktakes.
ON_HAND = (ItemStatus.IN_STOCK, ItemStatus.RESERVED)


class Item(TenantScopedModel):
    barcode = models.CharField(max_length=32)
    rfid_epc = models.CharField(max_length=24, blank=True)
    external_code = models.CharField(max_length=64, blank=True)  # supplier's own code
    item_type = models.CharField(max_length=16, choices=ItemType.choices)
    category = models.ForeignKey("catalog.ItemCategory", on_delete=models.PROTECT,
                                 related_name="items")
    karat = models.ForeignKey("catalog.Karat", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="+")
    gross_weight_g = models.DecimalField(max_digits=14, decimal_places=3)
    metal_weight_g = models.DecimalField(max_digits=14, decimal_places=3)
    fine_weight_g = models.DecimalField(max_digits=16, decimal_places=4)
    stone_weight_ct = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    supplier = models.ForeignKey("parties.Party", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=24, choices=ItemStatus.choices,
                              default=ItemStatus.IN_STOCK)
    branch = models.ForeignKey("org.Branch", on_delete=models.PROTECT, related_name="+")
    # Making charge per gram paid to the supplier, in cost_currency, and its total in the
    # company currency (the gold itself is tracked in fine grams, not as money).
    cost_currency = models.ForeignKey("catalog.Currency", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")
    cost_making_rate = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    cost_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    # Sale making charge per gram (company currency) unless a promotion overrides it.
    list_making_rate = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    label_price = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    attributes = models.JSONField(default=dict, blank=True)
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "barcode"],
                                    name="inventory_item_barcode_uniq"),
            models.UniqueConstraint(fields=["tenant", "rfid_epc"], condition=~Q(rfid_epc=""),
                                    name="inventory_item_rfid_uniq"),
            models.CheckConstraint(
                condition=Q(gross_weight_g__gte=0, metal_weight_g__gte=0, fine_weight_g__gte=0,
                            stone_weight_ct__gte=0),
                name="inventory_item_weights_check"),
        ]
        indexes = [
            models.Index(fields=["tenant", "branch", "status", "category"],
                         name="inventory_item_stock_idx"),
            models.Index(fields=["tenant", "external_code"], name="inventory_item_external_idx"),
        ]

    def __str__(self):
        return self.barcode


class StockLot(TenantScopedModel):
    category = models.ForeignKey("catalog.ItemCategory", on_delete=models.PROTECT,
                                 related_name="lots")
    karat = models.ForeignKey("catalog.Karat", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="+")
    branch = models.ForeignKey("org.Branch", on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "category", "karat", "branch"],
                                    nulls_distinct=False, name="inventory_stocklot_key_uniq"),
        ]

    def __str__(self):
        return f"{self.category_id}/{self.karat_id}/{self.branch_id}"


class LotBalance(TenantScopedModel):
    """Projection of a lot's movements, updated in the same transaction; rebuildable."""

    lot = models.OneToOneField(StockLot, on_delete=models.CASCADE, related_name="balance")
    qty = models.IntegerField(default=0)
    gross_weight_g = models.DecimalField(max_digits=16, decimal_places=3, default=0)
    fine_weight_g = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    cost_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "lot"], name="inventory_lotbalance_lot_uniq"),
            models.CheckConstraint(condition=Q(gross_weight_g__gte=0, fine_weight_g__gte=0,
                                               qty__gte=0),
                                   name="inventory_lotbalance_non_negative_check"),
        ]


class MovementType(models.TextChoices):
    PURCHASE_RECEIPT = "purchase_receipt", _("Purchase")
    PURCHASE_VOID = "purchase_void", _("Purchase cancelled")
    SALE = "sale", _("Sale")
    SALE_RETURN = "sale_return", _("Customer return")
    TRADE_SALE = "trade_sale", _("Wholesale")
    TRADE_RETURN = "trade_return", _("Wholesale return")
    SUPPLIER_RETURN = "supplier_return", _("Returned to supplier")
    TRANSFER_OUT = "transfer_out", _("Sent to branch")
    TRANSFER_IN = "transfer_in", _("Received from branch")
    WORKSHOP_ISSUE = "workshop_issue", _("Sent to workshop")
    WORKSHOP_RECEIPT = "workshop_receipt", _("Back from workshop")
    PRODUCTION_CONSUME = "production_consume", _("Used in production")
    PRODUCTION_OUTPUT = "production_output", _("Produced")
    STOCKTAKE_ADJUST = "stocktake_adjust", _("Stocktake adjustment")
    SCRAP_IN = "scrap_in", _("Scrap received")
    SCRAP_OUT = "scrap_out", _("Scrap out")
    LOSS = "loss", _("Loss")
    GAIN = "gain", _("Gain")
    OPENING = "opening", _("Opening stock")


class StockMovement(TenantScopedModel):
    """Append-only (a database trigger rejects UPDATE/DELETE). Signed: in > 0, out < 0."""

    branch = models.ForeignKey("org.Branch", on_delete=models.PROTECT, related_name="+")
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT,
                             related_name="movements")
    lot = models.ForeignKey(StockLot, null=True, blank=True, on_delete=models.PROTECT,
                            related_name="movements")
    movement_type = models.CharField(max_length=24, choices=MovementType.choices)
    qty = models.IntegerField()
    gross_weight_g = models.DecimalField(max_digits=14, decimal_places=3)
    fine_weight_g = models.DecimalField(max_digits=16, decimal_places=4)
    cost_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    document_type = models.CharField(max_length=60, blank=True)
    document_id = models.BigIntegerField(null=True, blank=True)
    business_date = models.DateField()
    memo = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-business_date", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(item__isnull=False, lot__isnull=True)
                           | Q(item__isnull=True, lot__isnull=False)),
                name="inventory_movement_item_xor_lot_check"),
        ]
        indexes = [
            models.Index(fields=["tenant", "branch", "business_date"],
                         name="inventory_movement_date_idx"),
            models.Index(fields=["tenant", "document_type", "document_id"],
                         name="inventory_movement_doc_idx"),
        ]


# --- documents ----------------------------------------------------------------------------------

class StockTransfer(Document):
    """Goods sent from one branch to another (§7.5; replaces the legacy br1..br4 relay).

    Posting sends: pieces become in_transit at the destination, bulk weight leaves the source
    lot. The destination receives everything at once; until then the sender may cancel."""

    to_branch = models.ForeignKey("org.Branch", on_delete=models.PROTECT, related_name="+")
    total_qty = models.IntegerField(default=0)
    total_gross_weight_g = models.DecimalField(max_digits=16, decimal_places=3, default=0)
    total_fine_weight_g = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    received_at = models.DateTimeField(null=True, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.PROTECT,
                                    related_name="+")
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")
    receive_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")

    class Meta(Document.Meta):
        ordering = ["-business_date", "-id"]
        indexes = [
            models.Index(fields=["tenant", "branch", "-business_date"],
                         name="stock_transfer_date_idx"),
            models.Index(fields=["tenant", "to_branch"], name="stock_transfer_to_idx",
                         condition=Q(received_at__isnull=True)),
        ]

    def __str__(self):
        return self.number or f"#{self.pk}"

    @property
    def in_transit(self) -> bool:
        return self.status == "posted" and self.received_at is None


class StockTransferLine(TenantScopedModel):
    """A piece (item) or a weight of bulk stock (category × karat)."""

    transfer = models.ForeignKey(StockTransfer, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT,
                             related_name="+")
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
            models.CheckConstraint(condition=Q(gross_weight_g__gt=0, qty__gte=0),
                                   name="stock_transfer_line_check"),
        ]
        indexes = [models.Index(fields=["tenant", "transfer"], name="stock_transfer_line_idx")]


class StocktakeResult(models.TextChoices):
    PENDING = "pending", _("Not counted yet")
    COUNTED = "counted", _("Counted")
    MISSING = "missing", _("Missing")
    UNEXPECTED = "unexpected", _("Not expected here")
    LEFT = "left", _("Left during the count")


class Stocktake(Document):
    """A count of one branch's stock (§7.5), optionally limited to a category and/or karat.
    Draft = counting; posted = differences applied; voided = abandoned."""

    category = models.ForeignKey("catalog.ItemCategory", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")
    karat = models.ForeignKey("catalog.Karat", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="+")
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")

    class Meta(Document.Meta):
        ordering = ["-business_date", "-id"]
        constraints = [
            *Document.Meta.constraints,
            # One count at a time per branch.
            models.UniqueConstraint(fields=["tenant", "branch"], condition=Q(status="draft"),
                                    name="stocktake_one_open_uniq"),
        ]
        indexes = [models.Index(fields=["tenant", "branch", "-business_date"],
                                name="stocktake_date_idx")]

    def __str__(self):
        return self.number or f"#{self.pk}"


class StocktakeLine(TenantScopedModel):
    stocktake = models.ForeignKey(Stocktake, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT,
                             related_name="+")  # null: a scanned code no piece has
    barcode = models.CharField(max_length=64)
    expected = models.BooleanField(default=True)
    result = models.CharField(max_length=12, choices=StocktakeResult.choices,
                              default=StocktakeResult.PENDING)
    counted_at = models.DateTimeField(null=True, blank=True)
    counted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.PROTECT,
                                   related_name="+")
    adjusted = models.BooleanField(default=False)  # posting changed the piece's stock status

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "stocktake", "barcode"],
                                    name="stocktake_line_barcode_uniq"),
        ]
        indexes = [models.Index(fields=["tenant", "stocktake", "result"],
                                name="stocktake_line_result_idx")]


class StocktakeLotLine(TenantScopedModel):
    """Bulk stock is weighed, not scanned: expected at the start, counted, adjusted on posting."""

    stocktake = models.ForeignKey(Stocktake, on_delete=models.CASCADE, related_name="lot_lines")
    lot = models.ForeignKey(StockLot, on_delete=models.PROTECT, related_name="+")
    expected_qty = models.IntegerField(default=0)
    expected_gross_weight_g = models.DecimalField(max_digits=16, decimal_places=3)
    counted_qty = models.IntegerField(null=True, blank=True)
    counted_gross_weight_g = models.DecimalField(max_digits=16, decimal_places=3, null=True,
                                                 blank=True)
    adjustment_g = models.DecimalField(max_digits=16, decimal_places=3, default=0)  # on posting

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "stocktake", "lot"],
                                    name="stocktake_lot_uniq"),
            models.CheckConstraint(condition=Q(counted_gross_weight_g__gte=0)
                                   | Q(counted_gross_weight_g__isnull=True),
                                   name="stocktake_lot_counted_check"),
        ]

    @property
    def variance_g(self):
        if self.counted_gross_weight_g is None:
            return None
        return self.counted_gross_weight_g - self.expected_gross_weight_g
