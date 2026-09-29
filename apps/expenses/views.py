from django.core.paginator import Paginator
from django.db.models import Sum
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse

from apps.catalog.models import Currency
from apps.iam.authz import permission_required
from apps.org.models import Branch, TenantProfile
from apps.pricing.selectors import functional_currency
from apps.treasury.selectors import holder_choices

from .models import ExpenseCategory, ExpenseVoucher
from .services import expense_accounts


def _visible(request):
    vouchers = ExpenseVoucher.objects.select_related("category", "currency", "branch", "cash_box",
                                                     "bank_account")
    branches = request.actor.branch_ids("expenses.view")
    return vouchers if branches is None else vouchers.filter(branch_id__in=branches)


@permission_required("expenses.view")
def expenses(request):
    category = request.GET.get("category", "")
    queryset = _visible(request)
    if category.isdigit():
        queryset = queryset.filter(category_id=int(category))
    total = queryset.filter(status="posted").aggregate(total=Sum("functional_amount"))["total"]
    page = Paginator(queryset.order_by("-business_date", "-id"), 25).get_page(
        request.GET.get("page"))
    return render(request, "expenses/list.html", {
        "page": page, "category": category, "total": total or 0,
        "categories": ExpenseCategory.objects.all(),
        "home_currency": functional_currency(),
    })


@permission_required("expenses.voucher.create")
def expense_new(request):
    allowed = request.actor.branch_ids("expenses.voucher.create")
    branches = Branch.objects.filter(is_active=True).order_by("code")
    if allowed is not None:
        branches = branches.filter(pk__in=allowed)
    return render(request, "expenses/form.html", {
        "branches": branches, "default_branch": request.membership.default_branch_id,
        "categories": ExpenseCategory.objects.filter(is_active=True),
        "currencies": Currency.objects.filter(is_active=True),
        "home_currency": functional_currency(),
        **holder_choices(allowed),
    })


@permission_required("expenses.view")
def expense_detail(request, pk):
    voucher = _visible(request).select_related("journal_entry", "posted_by").filter(pk=pk).first()
    if voucher is None:
        raise Http404
    return render(request, "expenses/detail.html", {
        "voucher": voucher, "profile": TenantProfile.objects.first(),
        "home_currency": functional_currency(),
        "endpoint": reverse("expense-voucher-detail", args=[voucher.pk]),
    })


@permission_required("expenses.view")
def categories(request):
    return render(request, "expenses/categories.html", {
        "categories": ExpenseCategory.objects.select_related("account"),
        "accounts": expense_accounts(),
    })
