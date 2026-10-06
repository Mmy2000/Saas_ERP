from datetime import date

from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from apps.core.models import DocStatus
from apps.iam.authz import permission_required
from apps.iam.models import Membership, MembershipStatus
from apps.org.models import Branch, TenantProfile
from apps.pricing.selectors import functional_currency
from apps.treasury.selectors import holder_choices

from . import services
from .models import AdjustmentKind, CommissionBasis, Employee, EmployeeAdvance, PayrollRun


def _period(request) -> tuple[int, int]:
    """?period=YYYY-MM, else the current month."""
    value = request.GET.get("period", "")
    try:
        year, month = (int(part) for part in value.split("-", 1))
        services.period_bounds(year, month)
        return year, month
    except (TypeError, ValueError, Exception):  # noqa: BLE001 - any bad value: this month
        today = timezone.localdate()
        return today.year, today.month


def _months(around: tuple[int, int], count: int = 12) -> list[tuple[str, date]]:
    """The last `count` months up to this one, newest first, for the month pickers."""
    today = timezone.localdate()
    year, month = today.year, today.month
    months = []
    for _ in range(count):
        months.append((f"{year}-{month:02d}", date(year, month, 1)))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return months


@permission_required("hr.employee.view")
def employees(request):
    status = request.GET.get("status", "active")
    term = request.GET.get("q", "").strip()
    queryset = Employee.objects.select_related("branch", "user").order_by("code")
    if status in ("active", "inactive"):
        queryset = queryset.filter(is_active=status == "active")
    if term:
        queryset = queryset.filter(Q(name__icontains=term) | Q(phone__icontains=term)
                                   | Q(job_title__icontains=term))
    page = Paginator(queryset, 25).get_page(request.GET.get("page"))
    for employee in page.object_list:
        employee.outstanding = services.outstanding_advance(employee)
    return render(request, "hr/employees.html", {
        "page": page, "status": status, "term": term, "home_currency": functional_currency(),
    })


def _form_context(request, employee=None) -> dict:
    members = (Membership.objects.select_related("user")
               .filter(status=MembershipStatus.ACTIVE).order_by("username"))
    return {
        "employee": employee, "branches": Branch.objects.filter(is_active=True),
        "members": members, "bases": CommissionBasis.choices,
        "endpoint": (reverse("hr-employee-detail", args=[employee.pk]) if employee
                     else reverse("hr-employee-list")),
        "method": "PATCH" if employee else "POST",
        "home_currency": functional_currency(),
    }


@permission_required("hr.employee.manage")
def employee_new(request):
    return render(request, "hr/employee_form.html", _form_context(request))


@permission_required("hr.employee.view")
def employee(request, pk):
    employee = Employee.objects.select_related("branch", "user").filter(pk=pk).first()
    if employee is None:
        raise Http404
    year, month = _period(request)
    context = _form_context(request, employee)
    allowed = request.actor.branch_ids("hr.advance.create")
    context.update({
        "outstanding": services.outstanding_advance(employee),
        "commission": services.commission_for(employee, year, month),
        "period": f"{year}-{month:02d}", "months": _months((year, month)),
        "advances": employee.advances.order_by("-business_date", "-id")[:10],
        "adjustments": employee.adjustments.select_related("payroll_line__run")[:12],
        "payslips": employee.payslips.filter(run__status=DocStatus.POSTED)
        .select_related("run").order_by("-run__year", "-run__month")[:12],
        "kinds": AdjustmentKind.choices, "default_branch": request.membership.default_branch_id,
        **holder_choices(allowed),
    })
    return render(request, "hr/employee.html", context)


@permission_required("hr.employee.view")
def advance(request, pk):
    advance = (EmployeeAdvance.objects.select_related("employee", "branch", "cash_box",
                                                      "bank_account", "journal_entry")
               .filter(pk=pk).first())
    if advance is None:
        raise Http404
    return render(request, "hr/advance.html", {
        "advance": advance, "profile": TenantProfile.objects.first(),
        "endpoint": reverse("hr-advance-detail", args=[advance.pk]),
        "home_currency": functional_currency(),
    })


@permission_required("hr.payroll.view")
def payrolls(request):
    page = Paginator(PayrollRun.objects.select_related("posted_by").order_by(
        "-year", "-month", "-id"), 24).get_page(request.GET.get("page"))
    year, month = _period(request)
    return render(request, "hr/payrolls.html", {
        "page": page, "months": _months((year, month)), "home_currency": functional_currency(),
    })


@permission_required("hr.payroll.post")
def payroll_new(request):
    year, month = _period(request)
    allowed = request.actor.branch_ids("hr.payroll.post")
    branches = Branch.objects.filter(is_active=True).order_by("code")
    if allowed is not None:
        branches = branches.filter(pk__in=allowed)
    plan = services.plan_payroll(year, month)
    totals = {name: plan.total(name) for name in
              ("salary", "bonus", "deduction", "gross", "advance", "net")}
    totals["commission"] = plan.total_commission
    return render(request, "hr/payroll_new.html", {
        "plan": plan, "totals": totals, "period": f"{year}-{month:02d}",
        "period_date": date(year, month, 1), "months": _months((year, month)),
        "branches": branches, "default_branch": request.membership.default_branch_id,
        "home_currency": functional_currency(), **holder_choices(allowed),
    })


@permission_required("hr.payroll.view")
def payroll(request, pk):
    run = (PayrollRun.objects.select_related("branch", "cash_box", "bank_account",
                                             "journal_entry", "posted_by")
           .filter(pk=pk).first())
    if run is None:
        raise Http404
    return render(request, "hr/payroll.html", {
        "run": run, "lines": run.lines.select_related("employee"),
        "period_date": date(run.year, run.month, 1), "profile": TenantProfile.objects.first(),
        "endpoint": reverse("hr-payroll-detail", args=[run.pk]),
        "home_currency": functional_currency(),
    })


@permission_required("hr.payroll.view")
def commissions(request):
    year, month = _period(request)
    rows = []
    for employee in Employee.objects.filter(
            Q(is_active=True) | Q(payslips__run__year=year, payslips__run__month=month)
    ).exclude(commission_basis=CommissionBasis.NONE, user__isnull=True).distinct().order_by(
            "code"):
        result = services.commission_for(employee, year, month)
        if employee.commission_basis == CommissionBasis.NONE and not result.sales:
            continue
        rows.append({"employee": employee, "result": result})
    return render(request, "hr/commissions.html", {
        "rows": rows, "period": f"{year}-{month:02d}", "period_date": date(year, month, 1),
        "months": _months((year, month)), "home_currency": functional_currency(),
        "total": sum((row["result"].amount for row in rows), services.ZERO),
        "making": sum((row["result"].making for row in rows), services.ZERO),
    })
