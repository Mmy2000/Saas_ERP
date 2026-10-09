from django.db.models import Sum
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.catalog.models import Currency
from apps.core.listing import Choice, ListQuery, Total
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
    """Expense vouchers by date, branch, category and words (Gweb's GHM21 report)."""
    filters = ListQuery(
        request, permission="expenses.view",
        search=("number", "note", "payee", "reference", "category__name"),
        search_label=_("Number, note, payee or category"),
        choices=[Choice("category", _("Category"), [("", _("All"))] + [
            (str(c.pk), c.name) for c in ExpenseCategory.objects.order_by("name")],
            "category_id"),
                 Choice("status", _("Status"), [("", _("All")), ("posted", _("Posted")),
                                                ("voided", _("Cancelled"))], "status")])
    found = filters.apply(_visible(request))
    total = found.filter(status="posted").aggregate(total=Sum("functional_amount"))["total"]
    home = functional_currency()
    return render(request, "expenses/list.html", {
        "page": filters.page(found.order_by("-business_date", "-id")), "filters": filters,
        "total": total or 0, "home_currency": home,
        "totals": [Total(_("Total"), total or 0, unit=home)],
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
