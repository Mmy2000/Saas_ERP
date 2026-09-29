"""Shop expenses (§7.12): rent, salaries paid in cash, utilities… Legacy `Exp.Fm1` (categories)
and `Fm2` (vouchers, with `tot_cr` rows as reversals, here a void)."""

from __future__ import annotations

from django.db import models
from django.db.models import Q

from apps.core.models import Document, TenantScopedModel


class ExpenseCategory(TenantScopedModel):
    name = models.CharField(max_length=100)
    account = models.ForeignKey("ledger.Account", on_delete=models.PROTECT, related_name="+")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "expense categories"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "name"], name="expenses_category_name_uniq"),
        ]

    def __str__(self):
        return self.name


class ExpenseVoucher(Document):
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name="+")
    # Paid from exactly one of these.
    cash_box = models.ForeignKey("treasury.CashBox", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")
    bank_account = models.ForeignKey("treasury.BankAccount", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+")
    currency = models.ForeignKey("catalog.Currency", on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    functional_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    payee = models.CharField(max_length=200, blank=True)
    reference = models.CharField(max_length=60, blank=True)
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")

    class Meta(Document.Meta):
        ordering = ["-business_date", "-id"]
        constraints = [
            *Document.Meta.constraints,
            models.CheckConstraint(
                condition=(Q(cash_box__isnull=False, bank_account__isnull=True)
                           | Q(cash_box__isnull=True, bank_account__isnull=False)),
                name="expenses_voucher_one_source_check"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="expenses_voucher_amount_check"),
        ]
        indexes = [
            models.Index(fields=["tenant", "branch", "-business_date"], name="expense_date_idx"),
            models.Index(fields=["tenant", "category"], name="expense_category_idx"),
        ]

    def __str__(self):
        return self.number or f"#{self.pk}"

    @property
    def paid_from(self):
        return self.cash_box or self.bank_account
