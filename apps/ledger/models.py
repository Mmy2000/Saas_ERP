"""Multi-commodity double-entry ledger (§7.10, ADR-005).

Money and metal are both commodities. Every balance in the system (customers, suppliers,
cash, stock value, gold on hand) is a sum of journal lines. Invariants, checked by
PostingService and again by a deferred constraint trigger at commit:

1. Σ functional_amount = 0 per entry;
2. Σ quantity = 0 per entry per metal commodity (metal never appears from nowhere);
3. lines only on postable, active accounts, with an allowed commodity, in an open period.

Posted entries and lines are append-only (a trigger rejects UPDATE/DELETE); corrections are
reversal entries.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantScopedModel

from .chart import NAMES


class CommodityKind(models.TextChoices):
    MONEY = "money", _("Money")
    METAL = "metal", _("Metal (fine grams)")


class Commodity(TenantScopedModel):
    code = models.CharField(max_length=8)  # EGP, USD, XAU, XAG
    kind = models.CharField(max_length=8, choices=CommodityKind.choices)
    currency = models.ForeignKey("catalog.Currency", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")
    metal = models.ForeignKey("catalog.Metal", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="+")
    decimal_places = models.PositiveSmallIntegerField(default=2)
    is_functional = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_functional", "kind", "code"]
        verbose_name_plural = "commodities"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "code"], name="ledger_commodity_code_uniq"),
            models.UniqueConstraint(fields=["tenant"], condition=Q(is_functional=True),
                                    name="ledger_commodity_one_functional_uniq"),
            models.CheckConstraint(
                condition=(Q(kind="money", currency__isnull=False, metal__isnull=True)
                           | Q(kind="metal", metal__isnull=False, currency__isnull=True)),
                name="ledger_commodity_kind_check",
            ),
        ]

    def __str__(self):
        return self.code

    @property
    def label(self) -> str:
        if self.kind == CommodityKind.MONEY:
            return self.code
        return str(_("%(metal)s (fine g)") % {"metal": self.metal.get_code_display()})


class AccountType(models.TextChoices):
    ASSET = "asset", _("Asset")
    LIABILITY = "liability", _("Liability")
    EQUITY = "equity", _("Equity")
    INCOME = "income", _("Income")
    EXPENSE = "expense", _("Expense")


class Nature(models.TextChoices):
    DEBIT = "debit", _("Debit")
    CREDIT = "credit", _("Credit")


class Subledger(models.TextChoices):
    NONE = "none", _("None")
    PARTY = "party", _("Party")


class CommodityScope(models.TextChoices):
    ANY = "any", _("Money and metal")
    MONEY = "money", _("Money only")
    METAL = "metal", _("Metal only")


class Account(TenantScopedModel):
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=200, blank=True)  # blank = translated template name
    template_key = models.CharField(max_length=20, blank=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="children")
    type = models.CharField(max_length=12, choices=AccountType.choices)
    nature = models.CharField(max_length=8, choices=Nature.choices)
    subledger = models.CharField(max_length=12, choices=Subledger.choices,
                                 default=Subledger.NONE)
    commodity_scope = models.CharField(max_length=8, choices=CommodityScope.choices,
                                       default=CommodityScope.ANY)
    is_postable = models.BooleanField(default=True)
    role = models.CharField(max_length=40, blank=True)  # posting role, e.g. "customers"
    # Set for accounts that hold exactly one commodity (a cash box, a bank account).
    commodity = models.ForeignKey("Commodity", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="+")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "code"], name="ledger_account_code_uniq"),
            models.UniqueConstraint(fields=["tenant", "role"], condition=~Q(role=""),
                                    name="ledger_account_role_uniq"),
        ]

    def __str__(self):
        return f"{self.code} {self.label}"

    @property
    def label(self) -> str:
        if self.name:
            return self.name
        return str(NAMES.get(self.template_key, self.code))

    def allows(self, commodity: Commodity) -> bool:
        if self.commodity_id is not None:
            return self.commodity_id == commodity.pk
        return self.commodity_scope == CommodityScope.ANY or self.commodity_scope == commodity.kind


class EntryKind(models.TextChoices):
    AUTO = "auto", _("Automatic")
    MANUAL = "manual", _("Manual")
    OPENING = "opening", _("Opening balance")
    REVERSAL = "reversal", _("Reversal")
    CLOSING = "closing", _("Year-end closing")


class JournalEntry(TenantScopedModel):
    number = models.CharField(max_length=32)
    branch = models.ForeignKey("org.Branch", on_delete=models.PROTECT, related_name="+")
    business_date = models.DateField()
    kind = models.CharField(max_length=10, choices=EntryKind.choices)
    memo = models.CharField(max_length=300, blank=True)
    source_type = models.CharField(max_length=60, blank=True)  # e.g. "sales.SalesInvoice"
    source_id = models.BigIntegerField(null=True, blank=True)
    reverses = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="reversed_by")
    posted_at = models.DateTimeField(default=timezone.now)
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-business_date", "-id"]
        verbose_name_plural = "journal entries"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "number"], name="ledger_entry_number_uniq"),
        ]
        indexes = [
            models.Index(fields=["tenant", "business_date"], name="ledger_entry_date_idx"),
            models.Index(fields=["tenant", "source_type", "source_id"],
                         name="ledger_entry_source_idx"),
        ]

    def __str__(self):
        return self.number


class JournalLine(TenantScopedModel):
    entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="lines")
    commodity = models.ForeignKey(Commodity, on_delete=models.PROTECT, related_name="+")
    # Signed: debit positive, credit negative.
    quantity = models.DecimalField(max_digits=20, decimal_places=6)
    functional_amount = models.DecimalField(max_digits=20, decimal_places=6)
    party = models.ForeignKey("parties.Party", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="+")
    branch = models.ForeignKey("org.Branch", on_delete=models.PROTECT, related_name="+")
    business_date = models.DateField()  # copy of the entry's, for date-range indexes
    memo = models.CharField(max_length=300, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "account", "commodity", "business_date"],
                         name="ledger_line_account_idx"),
            models.Index(fields=["tenant", "party", "business_date"],
                         name="ledger_line_party_idx"),
        ]

    @property
    def debit(self):
        return self.quantity if self.quantity > 0 else None

    @property
    def credit(self):
        return -self.quantity if self.quantity < 0 else None


class BalanceProjection(TenantScopedModel):
    """Running balance per (account, commodity, branch, party), maintained in the posting
    transaction. Always rebuildable from the lines (rebuild_balances)."""

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="+")
    commodity = models.ForeignKey(Commodity, on_delete=models.CASCADE, related_name="+")
    branch = models.ForeignKey("org.Branch", on_delete=models.CASCADE, related_name="+")
    party = models.ForeignKey("parties.Party", null=True, blank=True, on_delete=models.CASCADE,
                              related_name="+")
    quantity = models.DecimalField(max_digits=20, decimal_places=6, default=0)
    functional_amount = models.DecimalField(max_digits=20, decimal_places=6, default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "account", "commodity", "branch", "party"],
                nulls_distinct=False, name="ledger_balance_key_uniq",
            ),
        ]


class PeriodStatus(models.TextChoices):
    OPEN = "open", _("Open for posting")
    CLOSED = "closed", _("Closed")


class FiscalPeriod(TenantScopedModel):
    name = models.CharField(max_length=60)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=8, choices=PeriodStatus.choices,
                              default=PeriodStatus.OPEN)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "start_date"],
                                    name="ledger_period_start_uniq"),
            models.CheckConstraint(condition=Q(end_date__gte=models.F("start_date")),
                                   name="ledger_period_dates_check"),
        ]

    def __str__(self):
        return self.name


class PeriodAction(models.TextChoices):
    CLOSED = "closed", _("Month closed")
    REOPENED = "reopened", _("Month reopened")
    YEAR_CLOSED = "year_closed", _("Year closed")
    YEAR_REOPENED = "year_reopened", _("Year reopened")


class PeriodEvent(TenantScopedModel):
    """Who closed or reopened which month or year, when and why (append-only history)."""

    action = models.CharField(max_length=16, choices=PeriodAction.choices)
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField(null=True, blank=True)  # empty for a whole year
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.PROTECT, related_name="+")
    at = models.DateTimeField(default=timezone.now)
    reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-at", "-id"]
        indexes = [models.Index(fields=["tenant", "-at"], name="ledger_period_event_idx")]


class YearEnd(TenantScopedModel):
    """A closed financial year: its income and expense balances moved to retained earnings by
    `entry`. Reopening reverses that entry (kept for the history)."""

    year = models.PositiveSmallIntegerField()
    entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, related_name="+")
    closed_at = models.DateTimeField(default=timezone.now)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.PROTECT, related_name="+")
    reopened_at = models.DateTimeField(null=True, blank=True)
    reopened_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-year", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "year"],
                                    condition=Q(reopened_at__isnull=True),
                                    name="ledger_year_end_open_uniq"),
        ]
