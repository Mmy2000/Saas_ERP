"""Employees, advances, adjustments, commissions and the monthly payroll (§7.12).

advance   Dr employee advances              Cr cash box / bank account
repaid    Dr cash box / bank account        Cr employee advances
settled   Dr balance adjustments            Cr employee advances   (in the employee's favour)
          or the mirror image                                     (against the employee)
payroll   Dr salaries (salary + bonuses − deductions)
          Dr sales commissions
          Cr employee advances (what is recovered this month)
          Cr cash box / bank account (net pay)
Cancelling either reverses its entry. Outstanding advances are always worked out from the
advances and the posted payrolls, so cancelling a payroll makes its recoveries due again.

Commission: on the sales rung up with the employee's login in the month, less what was
returned in the month: a % of the making charge, or an amount per gram of gold.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Max, Q, Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.models import Currency
from apps.core.crypto import encrypt
from apps.core.errors import Conflict, DomainError, NotFound, PermissionDenied, ValidationError
from apps.core.models import DocStatus
from apps.core.numeric import MONEY, UNIT_PRICE, quantize, round_money
from apps.core.sequences import allocate_number
from apps.ledger.models import EntryKind
from apps.ledger.services import LineInput as LedgerLine
from apps.ledger.services import account_for, functional_commodity, post_entry, reverse_entry
from apps.org.models import Branch
from apps.pricing.selectors import functional_currency
from apps.treasury.holders import TenderKind, ensure_cash_available, resolve_tender

from .models import (
    ADVANCE_SIGN,
    AdjustmentKind,
    AdvanceKind,
    CommissionBasis,
    Employee,
    EmployeeAdvance,
    PayAdjustment,
    PayrollLine,
    PayrollRun,
)

ADVANCE_DOC = "hr.EmployeeAdvance"
PAYROLL_DOC = "hr.PayrollRun"
ZERO = Decimal(0)
METHODS = (TenderKind.CASH, TenderKind.BANK_TRANSFER)


def _field(name: str, message: str) -> ValidationError:
    return ValidationError(message, fields={name: [message]})


def _amount(value, name: str, *, positive: bool = False, step=MONEY) -> Decimal:
    try:
        number = quantize(value if value not in (None, "") else "0", step)
    except (TypeError, ValueError):
        raise _field(name, _("Enter a number.")) from None
    if number < 0 or (positive and number == 0):
        raise _field(name, _("Must be greater than zero.") if positive
                     else _("Must not be negative."))
    return number


def period_bounds(year: int, month: int) -> tuple[date, date]:
    if not (1 <= month <= 12) or year < 2000:
        raise _field("period", _("Choose a month."))
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


# --- employees -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class EmployeeInput:
    name: str
    job_title: str = ""
    phone: str = ""
    national_id: str | None = None  # None = leave unchanged; "" = clear
    address: str = ""
    branch_id: int | None = None
    user_id: int | None = None
    hired_on: date | None = None
    monthly_salary: Decimal | str = "0"
    commission_basis: str = CommissionBasis.NONE
    commission_rate: Decimal | str = "0"
    is_active: bool = True
    notes: str = ""


# --- who may see and change whom ---------------------------------------------------------------
# An employee belongs to their branch: a role limited to some branches sees and changes only
# those branches' employees (with their advances, adjustments and payslips). An employee with
# no branch belongs to the whole company, as does a monthly payroll (it pays everyone): those
# need the permission for every branch.

def visible_employees(actor, code: str = "hr.employee.view"):
    branches = actor.branch_ids(code) if actor is not None else None
    queryset = Employee.objects.all()
    return queryset if branches is None else queryset.filter(branch_id__in=branches)


def require_all_branches(actor, code: str) -> None:
    if actor is None:
        return
    actor.require(code)
    if actor.branch_ids(code) is not None:
        raise PermissionDenied(_("This needs the permission for every branch."),
                               code="PERMISSION_DENIED")


def require_for_employee(actor, code: str, branch_id: int | None) -> None:
    """`code` over an employee of `branch_id` (None: the whole company's)."""
    if actor is None:
        return
    if branch_id is None:
        require_all_branches(actor, code)
    else:
        actor.require(code, branch=branch_id)


def _apply(employee: Employee, data: EmployeeInput) -> None:
    from apps.iam.models import Membership

    name = data.name.strip()
    if not name:
        raise _field("name", _("Required."))
    if data.commission_basis not in CommissionBasis.values:
        raise _field("commission_basis", _("Choose how commission is worked out."))
    branch = None
    if data.branch_id:
        branch = Branch.objects.filter(pk=data.branch_id).first()
        if branch is None:
            raise _field("branch", _("Unknown branch."))
    user = None
    if data.user_id:
        membership = Membership.objects.select_related("user").filter(
            user_id=data.user_id).first()
        if membership is None:
            raise _field("user", _("This login is not a member of the workspace."))
        user = membership.user
    rate = _amount(data.commission_rate, "commission_rate", step=UNIT_PRICE)
    if data.commission_basis == CommissionBasis.MAKING_PCT and rate > 100:
        raise _field("commission_rate", _("A share cannot be more than 100%."))
    employee.name = name[:200]
    employee.job_title = data.job_title.strip()[:100]
    employee.phone = data.phone.strip()[:30]
    if data.national_id is not None:
        value = data.national_id.strip()
        employee.national_id_encrypted = encrypt(value) if value else ""
    employee.address = data.address.strip()
    employee.branch = branch
    employee.user = user
    employee.hired_on = data.hired_on
    employee.monthly_salary = _amount(data.monthly_salary, "monthly_salary")
    employee.commission_basis = data.commission_basis
    employee.commission_rate = rate if data.commission_basis != CommissionBasis.NONE else ZERO
    employee.is_active = data.is_active
    employee.notes = data.notes.strip()


def _save(employee: Employee) -> Employee:
    try:
        with transaction.atomic():
            employee.save()
    except IntegrityError:
        raise _field("user", _("Another employee already uses this login.")) from None
    return employee


def create_employee(data: EmployeeInput, *, actor=None) -> Employee:
    require_for_employee(actor, "hr.employee.manage", data.branch_id)
    with transaction.atomic():
        employee = Employee(created_by=getattr(actor, "user", None))
        _apply(employee, data)
        employee.code = (Employee.objects.aggregate(top=Max("code"))["top"] or 0) + 1
        return _save(employee)


def update_employee(employee_id: int, data: EmployeeInput, *, actor=None) -> Employee:
    with transaction.atomic():
        employee = Employee.objects.select_for_update().filter(pk=employee_id).first()
        if employee is None:
            raise NotFound(_("Not found."))
        # Both where they are and where they would be moved to.
        require_for_employee(actor, "hr.employee.manage", employee.branch_id)
        require_for_employee(actor, "hr.employee.manage", data.branch_id)
        _apply(employee, data)
        return _save(employee)


# --- advances and adjustments --------------------------------------------------------------------

def outstanding_advance(employee: Employee) -> Decimal:
    """Advances (and settlements against them) less repayments, settlements in their favour
    and what payrolls took back."""
    posted = EmployeeAdvance.objects.filter(employee=employee, status=DocStatus.POSTED)
    up = [k for k, s in ADVANCE_SIGN.items() if s > 0]
    totals = posted.aggregate(up=Sum("amount", filter=Q(kind__in=up)),
                              down=Sum("amount", filter=~Q(kind__in=up)))
    given = (totals["up"] or ZERO) - (totals["down"] or ZERO)
    recovered = PayrollLine.objects.filter(employee=employee,
                                           run__status=DocStatus.POSTED).aggregate(
        s=Sum("advance"))["s"] or ZERO
    return given - recovered


def _holder(branch, method: str, cash_box_id, bank_account_id):
    if method not in METHODS:
        raise _field("method", _("Choose cash or bank transfer."))
    currency = Currency.objects.get(code=functional_currency())
    return resolve_tender(method, branch, currency, cash_box_id=cash_box_id,
                          bank_account_id=bank_account_id)


def _paid_from(holder) -> dict:
    """The box / bank account fields (pay goes out by cash or transfer, never a card)."""
    return {k: v for k, v in holder.fields.items() if k in ("cash_box", "bank_account")}


def give_advance(employee_id: int, amount, *, branch_id: int, method: str = TenderKind.CASH,
                 cash_box_id=None, bank_account_id=None, note: str = "",
                 actor=None) -> EmployeeAdvance:
    with transaction.atomic():
        branch = Branch.objects.filter(pk=branch_id, is_active=True).first()
        if branch is None:
            raise _field("branch", _("Unknown branch."))
        if actor is not None:
            actor.require("hr.advance.create", branch=branch)
        employee = Employee.objects.filter(pk=employee_id, is_active=True).first()
        if employee is None:
            raise _field("employee", _("Choose an active employee."))
        require_for_employee(actor, "hr.advance.create", employee.branch_id)
        value = _amount(amount, "amount", positive=True)
        holder = _holder(branch, method, cash_box_id, bank_account_id)
        if holder.cash_box is not None:
            ensure_cash_available(holder.cash_box, value, "amount")
        today = timezone.localdate()
        advance = EmployeeAdvance.objects.create(
            branch=branch, business_date=today, employee=employee, amount=value, method=method,
            note=note.strip(), created_by=getattr(actor, "user", None), **_paid_from(holder))
        advance.number = allocate_number("EA", branch=branch, fiscal_year=today.year)
        home = functional_commodity()
        advance.journal_entry = post_entry(
            branch=branch, business_date=today, kind=EntryKind.AUTO,
            source_type=ADVANCE_DOC, source_id=advance.pk,
            memo=_("Advance %(number)s to %(name)s") % {"number": advance.number,
                                                        "name": employee.name},
            lines=[LedgerLine(account=account_for("employee_advances"), commodity=home,
                              quantity=value),
                   LedgerLine(account=holder.account, commodity=home, quantity=-value)])
        advance.status = DocStatus.POSTED
        advance.posted_at = timezone.now()
        advance.posted_by = getattr(actor, "user", None)
        advance.save()
    return advance


def _post_movement(employee_id: int, kind: str, amount, *, branch_id: int, method: str = "",
                   cash_box_id=None, bank_account_id=None, note: str = "",
                   actor=None) -> EmployeeAdvance:
    """A repayment (cash or bank in) or a settlement (no money moves)."""
    repaid = kind == AdvanceKind.REPAYMENT
    with transaction.atomic():
        branch = Branch.objects.filter(pk=branch_id, is_active=True).first()
        if branch is None:
            raise _field("branch", _("Unknown branch."))
        if actor is not None:
            actor.require("hr.advance.create", branch=branch)
        employee = Employee.objects.select_for_update().filter(pk=employee_id).first()
        if employee is None:
            raise _field("employee", _("Choose an employee."))
        require_for_employee(actor, "hr.advance.create", employee.branch_id)
        value = _amount(amount, "amount", positive=True)
        if ADVANCE_SIGN[kind] < 0:
            owed = outstanding_advance(employee)
            if value > owed:
                raise _field("amount", _("%(name)s owes %(owed)s in advances.")
                             % {"name": employee.name, "owed": f"{owed:,.2f}"})
        if not repaid and not note.strip():
            raise _field("note", _("Write down why."))
        holder = _holder(branch, method, cash_box_id, bank_account_id) if repaid else None
        today = timezone.localdate()
        movement = EmployeeAdvance.objects.create(
            branch=branch, business_date=today, employee=employee, kind=kind, amount=value,
            method=method if repaid else "", note=note.strip(),
            created_by=getattr(actor, "user", None), **(_paid_from(holder) if holder else {}))
        movement.number = allocate_number("ER" if repaid else "ES", branch=branch,
                                          fiscal_year=today.year)
        home = functional_commodity()
        other = holder.account if repaid else account_for("balance_adjustments")
        sign = ADVANCE_SIGN[kind]
        movement.journal_entry = post_entry(
            branch=branch, business_date=today, kind=EntryKind.AUTO,
            source_type=ADVANCE_DOC, source_id=movement.pk,
            memo=f"{movement.get_kind_display()} {movement.number} · {employee.name}",
            lines=[LedgerLine(account=account_for("employee_advances"), commodity=home,
                              quantity=sign * value),
                   LedgerLine(account=other, commodity=home, quantity=-sign * value)])
        movement.status = DocStatus.POSTED
        movement.posted_at = timezone.now()
        movement.posted_by = getattr(actor, "user", None)
        movement.save()
    return movement


def repay_advance(employee_id: int, amount, *, branch_id: int, method: str = TenderKind.CASH,
                  cash_box_id=None, bank_account_id=None, note: str = "",
                  actor=None) -> EmployeeAdvance:
    """The employee pays back some of their advances in cash or by transfer."""
    return _post_movement(employee_id, AdvanceKind.REPAYMENT, amount, branch_id=branch_id,
                          method=method, cash_box_id=cash_box_id,
                          bank_account_id=bank_account_id, note=note, actor=actor)


def settle_advance(employee_id: int, amount, *, favour: bool, branch_id: int, note: str,
                   actor=None) -> EmployeeAdvance:
    """Lower (in the employee's favour) or raise what they owe, with a reason."""
    return _post_movement(employee_id,
                          AdvanceKind.SETTLE_DOWN if favour else AdvanceKind.SETTLE_UP,
                          amount, branch_id=branch_id, note=note, actor=actor)


def void_advance(advance_id: int, *, reason: str = "", actor=None) -> EmployeeAdvance:
    with transaction.atomic():
        advance = (EmployeeAdvance.objects.select_for_update(of=("self",))
                   .select_related("branch", "employee").filter(pk=advance_id).first())
        if advance is None:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("hr.advance.void", branch=advance.branch)
        require_for_employee(actor, "hr.advance.void", advance.employee.branch_id)
        if advance.status != DocStatus.POSTED:
            raise DomainError(_("Only posted documents can be cancelled."), code="DOC_NOT_POSTED")
        if advance.sign > 0 and outstanding_advance(advance.employee) < advance.amount:
            raise DomainError(_("Part of this advance was already paid back or recovered in a "
                                "payroll. Cancel that first."), code="HR_ADVANCE_RECOVERED")
        today = timezone.localdate()
        reverse_entry(advance.journal_entry_id, business_date=today,
                      memo=_("Cancelled %(number)s") % {"number": advance.number})
        advance.status = DocStatus.VOIDED
        advance.voided_at = timezone.now()
        advance.voided_by = getattr(actor, "user", None)
        advance.void_reason = reason.strip()[:300]
        advance.save()
    return advance


def add_adjustment(employee_id: int, kind: str, amount, year: int, month: int, *,
                   note: str = "", actor=None) -> PayAdjustment:
    if kind not in AdjustmentKind.values:
        raise _field("kind", _("Choose bonus or deduction."))
    period_bounds(year, month)
    employee = Employee.objects.filter(pk=employee_id).first()
    if employee is None:
        raise NotFound(_("Not found."))
    require_for_employee(actor, "hr.advance.create", employee.branch_id)
    if PayrollRun.objects.filter(year=year, month=month, status=DocStatus.POSTED).exists():
        raise _field("period", _("This month's payroll is already paid. Cancel it first, or "
                                 "choose the next month."))
    return PayAdjustment.objects.create(
        employee=employee, kind=kind, amount=_amount(amount, "amount", positive=True),
        year=year, month=month, note=note.strip()[:200],
        created_by=getattr(actor, "user", None))


def delete_adjustment(adjustment_id: int, *, actor=None) -> None:
    with transaction.atomic():
        # Locked: a payroll being posted links it first, and then this sees it was paid.
        adjustment = (PayAdjustment.objects.select_for_update(of=("self",))
                      .select_related("employee").filter(pk=adjustment_id).first())
        if adjustment is None:
            raise NotFound(_("Not found."))
        require_for_employee(actor, "hr.advance.create", adjustment.employee.branch_id)
        if adjustment.payroll_line_id is not None:
            raise DomainError(_("This was paid in a payroll. Cancel that payroll first."),
                              code="HR_ADJUSTMENT_PAID")
        adjustment.delete()


# --- commissions ---------------------------------------------------------------------------------

@dataclass
class Commission:
    sales: int = 0
    making: Decimal = ZERO  # making charge sold, less returns
    grams: Decimal = ZERO  # gross weight sold, less returns
    base: Decimal = ZERO  # what the rate applies to
    amount: Decimal = ZERO


def commission_for(employee: Employee, year: int, month: int) -> Commission:
    from apps.sales.models import SalesInvoiceLine, SalesReturnLine

    result = Commission()
    if employee.user_id is None:
        return result
    first, last = period_bounds(year, month)
    sold = SalesInvoiceLine.objects.filter(
        invoice__status=DocStatus.POSTED, invoice__sold_by_id=employee.user_id,
        invoice__business_date__range=(first, last))
    totals = sold.aggregate(m=Sum("making_amount"), g=Sum("gross_weight_g"))
    returned = SalesReturnLine.objects.filter(
        sales_return__status=DocStatus.POSTED, sales_return__business_date__range=(first, last),
        original_line__invoice__sold_by_id=employee.user_id).aggregate(
        m=Sum("original_line__making_amount"), g=Sum("original_line__gross_weight_g"))
    result.sales = sold.values("invoice").distinct().count()
    result.making = (totals["m"] or ZERO) - (returned["m"] or ZERO)
    result.grams = (totals["g"] or ZERO) - (returned["g"] or ZERO)
    if employee.commission_basis == CommissionBasis.MAKING_PCT:
        result.base = result.making
        result.amount = max(ZERO, round_money(result.making * employee.commission_rate / 100))
    elif employee.commission_basis == CommissionBasis.PER_GRAM:
        result.base = result.grams
        result.amount = max(ZERO, round_money(result.grams * employee.commission_rate))
    return result


# --- payroll -------------------------------------------------------------------------------------

@dataclass
class PayslipPlan:
    employee: Employee
    salary: Decimal
    commission: Commission
    bonus: Decimal
    deduction: Decimal
    outstanding: Decimal  # advances still to recover
    advance: Decimal = ZERO
    adjustments: list[PayAdjustment] = field(default_factory=list)

    @property
    def gross(self) -> Decimal:
        return self.salary + self.commission.amount + self.bonus - self.deduction

    @property
    def net(self) -> Decimal:
        return self.gross - self.advance


@dataclass
class PayrollPlan:
    year: int
    month: int
    lines: list[PayslipPlan]
    paid: PayrollRun | None = None  # the posted payroll for the month, if any

    def total(self, name: str) -> Decimal:
        return sum((getattr(line, name) for line in self.lines), ZERO)

    @property
    def total_commission(self) -> Decimal:
        return sum((line.commission.amount for line in self.lines), ZERO)


def plan_payroll(year: int, month: int, advances: dict[int, object] | None = None) -> PayrollPlan:
    """Each employee's payslip for the month. `advances` overrides how much of an employee's
    outstanding advances is recovered (default: all of it, up to their pay)."""
    _first, last = period_bounds(year, month)
    pending = PayAdjustment.objects.filter(year=year, month=month, payroll_line__isnull=True)
    with_adjustments = set(pending.values_list("employee_id", flat=True))
    # Someone hired after the month is not on its payroll (unless they have a bonus for it).
    on_staff = Q(is_active=True) & (Q(hired_on__isnull=True) | Q(hired_on__lte=last))
    employees = Employee.objects.filter(on_staff | Q(pk__in=with_adjustments))
    plan = PayrollPlan(year=year, month=month, lines=[], paid=PayrollRun.objects.filter(
        year=year, month=month, status=DocStatus.POSTED).first())
    for employee in employees.order_by("code"):
        own = [a for a in pending if a.employee_id == employee.pk]
        line = PayslipPlan(
            employee=employee, salary=(employee.monthly_salary if employee.is_active and (
                employee.hired_on is None or employee.hired_on <= last) else ZERO),
            commission=commission_for(employee, year, month),
            bonus=sum((a.amount for a in own if a.kind == AdjustmentKind.BONUS), ZERO),
            deduction=sum((a.amount for a in own if a.kind == AdjustmentKind.DEDUCTION), ZERO),
            outstanding=outstanding_advance(employee), adjustments=own)
        ceiling = max(ZERO, min(line.outstanding, line.gross))
        wanted = (advances or {}).get(employee.pk)
        if wanted in (None, ""):
            line.advance = ceiling
        else:
            value = _amount(wanted, f"advances.{employee.pk}")
            if value > ceiling:
                raise _field(f"advances.{employee.pk}",
                             _("At most %(amount)s for %(name)s.")
                             % {"amount": ceiling, "name": employee.name})
            line.advance = value
        if line.gross or line.outstanding:
            plan.lines.append(line)
    return plan


def post_payroll(year: int, month: int, *, branch_id: int, method: str = TenderKind.CASH,
                 cash_box_id=None, bank_account_id=None, advances=None, note: str = "",
                 actor=None) -> PayrollRun:
    with transaction.atomic():
        branch = Branch.objects.filter(pk=branch_id, is_active=True).first()
        if branch is None:
            raise _field("branch", _("Unknown branch."))
        if actor is not None:
            actor.require("hr.payroll.post", branch=branch)
        require_all_branches(actor, "hr.payroll.post")  # it pays every branch's employees
        plan = plan_payroll(year, month, advances)
        if plan.paid is not None:
            raise DomainError(_("The payroll for this month is already paid (%(number)s).")
                              % {"number": plan.paid.number}, code="HR_PAYROLL_PAID")
        if not plan.lines:
            raise DomainError(_("Nobody is due anything this month."), code="HR_PAYROLL_EMPTY")
        for line in plan.lines:
            if line.gross < 0:
                raise DomainError(_("Deductions for %(name)s are more than their pay.")
                                  % {"name": line.employee.name}, code="HR_PAYROLL_NEGATIVE")
        holder = _holder(branch, method, cash_box_id, bank_account_id)
        net = plan.total("net")
        if holder.cash_box is not None and net > 0:
            ensure_cash_available(holder.cash_box, net, "method")

        today = timezone.localdate()
        run = PayrollRun.objects.create(
            branch=branch, business_date=today, year=year, month=month, method=method,
            note=note.strip(), created_by=getattr(actor, "user", None), **_paid_from(holder),
            total_salary=plan.total("salary"), total_commission=plan.total_commission,
            total_bonus=plan.total("bonus"), total_deduction=plan.total("deduction"),
            total_advance=plan.total("advance"), total_net=net)
        for line in plan.lines:
            saved = PayrollLine.objects.create(
                run=run, employee=line.employee, salary=line.salary,
                commission_basis=line.employee.commission_basis,
                commission_rate=line.employee.commission_rate,
                commission_base=quantize(line.commission.base, Decimal("0.001")),
                commission=line.commission.amount, bonus=line.bonus, deduction=line.deduction,
                advance=line.advance, net=line.net)
            linked = PayAdjustment.objects.filter(
                pk__in=[a.pk for a in line.adjustments], payroll_line__isnull=True,
            ).update(payroll_line=saved)
            if linked != len(line.adjustments):  # one was deleted (or paid) meanwhile
                raise Conflict(_("A bonus or deduction changed while the payroll was being "
                                 "posted. Prepare it again."), code="HR_ADJUSTMENTS_CHANGED")

        home = functional_commodity()
        wages = run.total_salary + run.total_bonus - run.total_deduction
        entry = [(account_for("salaries"), wages), (account_for("commissions"),
                                                     run.total_commission),
                 (account_for("employee_advances"), -run.total_advance),
                 (holder.account, -net)]
        lines = [LedgerLine(account=account, commodity=home, quantity=quantity)
                 for account, quantity in entry if quantity]
        run.number = allocate_number("PRL", branch=branch, fiscal_year=today.year)
        if lines:
            run.journal_entry = post_entry(
                branch=branch, business_date=today, kind=EntryKind.AUTO, lines=lines,
                source_type=PAYROLL_DOC, source_id=run.pk,
                memo=_("Payroll %(period)s") % {"period": run.period})
        run.status = DocStatus.POSTED
        run.posted_at = timezone.now()
        run.posted_by = getattr(actor, "user", None)
        run.save()
    return run


def void_payroll(run_id: int, *, reason: str = "", actor=None) -> PayrollRun:
    with transaction.atomic():
        run = (PayrollRun.objects.select_for_update(of=("self",)).select_related("branch")
               .filter(pk=run_id).first())
        if run is None:
            raise NotFound(_("Not found."))
        if actor is not None:
            actor.require("hr.payroll.void", branch=run.branch)
        require_all_branches(actor, "hr.payroll.void")
        if run.status != DocStatus.POSTED:
            raise DomainError(_("Only posted documents can be cancelled."), code="DOC_NOT_POSTED")
        if run.journal_entry_id:
            reverse_entry(run.journal_entry_id, business_date=timezone.localdate(),
                          memo=_("Cancelled %(number)s") % {"number": run.number})
        PayAdjustment.objects.filter(payroll_line__run=run).update(payroll_line=None)
        run.status = DocStatus.VOIDED
        run.voided_at = timezone.now()
        run.voided_by = getattr(actor, "user", None)
        run.void_reason = reason.strip()[:300]
        run.save()
    return run
