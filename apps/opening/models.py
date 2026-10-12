"""Opening balances: what a business brings with it on the day it starts on the system (§7.10).

One document per branch and cut-over date holds every balance carried in: what each customer,
supplier, trader, workshop and partner owes or is owed (in money, or in gold by karat), what
each cash box and bank account holds, what the other branches owe this one or are owed by it,
bulk stock by weight (scrap, bullion…) and barcoded pieces (typed in, or imported from Excel).
Posting writes the stock and one ledger entry (kind "opening") against the Opening balances
equity account; cancelling reverses both.
Legacy: the many "رصيد افتتاحي" screens (GHC20, GHSS25, GHSH24, GHP14, GHB26, GHBR34, GHS69…).
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import Document, TenantScopedModel
from apps.parties.models import PartyRoleType


class OpeningBalance(Document):
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")

    class Meta(Document.Meta):
        indexes = [
            models.Index(fields=["tenant", "branch", "-business_date"], name="opening_date_idx"),
        ]

    def __str__(self):
        return self.number or f"OB draft #{self.pk}"


class LineKind(models.TextChoices):
    PARTY = "party", _("Customer, supplier or partner")
    HOLDER = "holder", _("Cash box or bank")
    STOCK = "stock", _("Stock by weight")
    PIECE = "piece", _("Piece")
    BRANCH = "branch", _("Another branch")


class OpeningLine(TenantScopedModel):
    """One balance carried in. Party amounts are signed: positive = they owe us."""

    opening = models.ForeignKey(OpeningBalance, on_delete=models.CASCADE, related_name="lines")
    kind = models.CharField(max_length=8, choices=LineKind.choices)
    # party
    party = models.ForeignKey("parties.Party", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="+")
    role = models.CharField(max_length=20, blank=True, choices=PartyRoleType.choices)
    # another branch (what it owes this branch, or is owed by it: legacy Bran GHBR34)
    other_branch = models.ForeignKey("org.Branch", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+")
    # cash box or bank account
    cash_box = models.ForeignKey("treasury.CashBox", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")
    bank_account = models.ForeignKey("treasury.BankAccount", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+")
    # money (party, holder) or gold (party: weight of a karat; stock and pieces)
    currency = models.ForeignKey("catalog.Currency", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    karat = models.ForeignKey("catalog.Karat", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="+")
    weight_g = models.DecimalField(max_digits=16, decimal_places=3, default=0)
    fine_weight_g = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    functional_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    # stock by weight, and pieces
    category = models.ForeignKey("catalog.ItemCategory", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")
    qty = models.IntegerField(default=0)
    cost_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    lot = models.ForeignKey("inventory.StockLot", null=True, blank=True,
                            on_delete=models.PROTECT, related_name="+")
    item = models.ForeignKey("inventory.Item", null=True, blank=True, on_delete=models.PROTECT,
                             related_name="+")
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        indexes = [models.Index(fields=["tenant", "opening"], name="opening_line_doc_idx")]
