"""Employees, advances, bonuses and deductions, monthly payroll and sales commissions (§7.12).
Legacy: `Emp.Fe1` (employees), `Fe2` (advances), `Fe3` (bonuses / penalties), `Fe4` (payroll),
`Fe5` (commission detail)."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.crypto import decrypt
from apps.core.models import Document, TenantScopedModel


class CommissionBasis(models.TextChoices):
    NONE = "none", _("No commission")
    MAKING_PCT = "making_pct", _("Share of the making charge sold")
    PER_GRAM = "per_gram", _("Amount per gram sold")


class Employee(TenantScopedModel):
    code = models.PositiveIntegerField()
    name = models.CharField(max_length=200)
    job_title = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    national_id_encrypted = models.TextField(blank=True)
    address = models.TextField(blank=True)
    branch = models.ForeignKey("org.Branch", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="+")
    # The login this person sells with: their sales count towards their commission.
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.PROTECT, related_name="+")
    hired_on = models.DateField(null=True, blank=True)
    monthly_salary = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    commission_basis = models.CharField(max_length=12, choices=CommissionBasis.choices,
                                        default=CommissionBasis.NONE)
    # % of the making charge (making_pct) or money per gram (per_gram).
    commission_rate = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "code"], name="hr_employee_code_uniq"),
            models.UniqueConstraint(fields=["tenant", "user"], condition=Q(user__isnull=False),
                                    name="hr_employee_user_uniq"),
            models.CheckConstraint(condition=Q(monthly_salary__gte=0, commission_rate__gte=0),
                                   name="hr_employee_pay_check"),
        ]

    def __str__(self):
        return self.name

    @property
    def national_id(self) -> str:
        return decrypt(self.national_id_encrypted) if self.national_id_encrypted else ""


class EmployeeAdvance(Document):
    """Money paid to an employee ahead of payroll (سلفة); recovered in later payrolls."""

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="advances")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    method = models.CharField(max_length=16)  # cash / bank_transfer
    cash_box = models.ForeignKey("treasury.CashBox", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")
    bank_account = models.ForeignKey("treasury.BankAccount", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+")
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")

    class Meta(Document.Meta):
        ordering = ["-business_date", "-id"]
        indexes = [models.Index(fields=["tenant", "employee"], name="hr_advance_employee_idx")]
        constraints = [
            *Document.Meta.constraints,
            models.CheckConstraint(condition=Q(amount__gt=0), name="hr_advance_amount_check"),
        ]

    def __str__(self):
        return self.number or f"#{self.pk}"


class AdjustmentKind(models.TextChoices):
    BONUS = "bonus", _("Bonus")
    DEDUCTION = "deduction", _("Salary deduction")


class PayAdjustment(TenantScopedModel):
    """A bonus or deduction for one month, applied by that month's payroll."""

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="adjustments")
    kind = models.CharField(max_length=10, choices=AdjustmentKind.choices)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    note = models.CharField(max_length=200, blank=True)
    payroll_line = models.ForeignKey("PayrollLine", null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="adjustments")

    class Meta:
        ordering = ["-year", "-month", "-id"]
        indexes = [models.Index(fields=["tenant", "year", "month"],
                                name="hr_adjustment_period_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0, month__gte=1, month__lte=12),
                                   name="hr_adjustment_values_check"),
        ]


class PayrollRun(Document):
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    method = models.CharField(max_length=16)  # cash / bank_transfer
    cash_box = models.ForeignKey("treasury.CashBox", null=True, blank=True,
                                 on_delete=models.PROTECT, related_name="+")
    bank_account = models.ForeignKey("treasury.BankAccount", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+")
    total_salary = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_commission = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_bonus = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_deduction = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_advance = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_net = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    journal_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True,
                                      on_delete=models.PROTECT, related_name="+")

    class Meta(Document.Meta):
        ordering = ["-year", "-month", "-id"]
        indexes = [models.Index(fields=["tenant", "year", "month"], name="hr_payroll_period_idx")]
        constraints = [
            *Document.Meta.constraints,
            # One paid payroll per month.
            models.UniqueConstraint(fields=["tenant", "year", "month"],
                                    condition=Q(status="posted"), name="hr_payroll_month_uniq"),
        ]

    def __str__(self):
        return self.number or f"#{self.pk}"

    @property
    def period(self) -> str:
        return f"{self.year}-{self.month:02d}"


class PayrollLine(TenantScopedModel):
    run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="lines")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="payslips")
    salary = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    commission_basis = models.CharField(max_length=12, choices=CommissionBasis.choices)
    commission_rate = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    commission_base = models.DecimalField(max_digits=18, decimal_places=3, default=0)  # money/g
    commission = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    bonus = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    deduction = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    advance = models.DecimalField(max_digits=18, decimal_places=2, default=0)  # recovered
    net = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    class Meta:
        ordering = ["employee__code"]
        indexes = [models.Index(fields=["tenant", "run"], name="hr_payroll_line_run_idx"),
                   models.Index(fields=["tenant", "employee"], name="hr_payroll_line_emp_idx")]

    @property
    def gross(self):
        return self.salary + self.commission + self.bonus - self.deduction
