from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse

from apps.iam.authz import permission_required
from apps.inventory.models import LotBalance
from apps.inventory.services import SCRAP_CATEGORY_CODE
from apps.org.models import Branch, TenantProfile
from apps.parties.models import Party, PartyRoleType
from apps.pricing.selectors import functional_currency
from apps.sales.models import SettlementBasis, TradeReturn, TradeSale
from apps.sales.trade import returned_line_ids


def _scope(request, queryset):
    branches = request.actor.branch_ids("sales.trade.view")
    return queryset if branches is None else queryset.filter(branch_id__in=branches)


@permission_required("sales.trade.view")
def trade_sales(request):
    term = request.GET.get("q", "").strip()
    sales = _scope(request, TradeSale.objects.select_related("trade_account", "branch"))
    if term:
        sales = sales.filter(Q(number__icontains=term) | Q(trade_account__name__icontains=term))
    page = Paginator(sales.order_by("-business_date", "-id"), 25).get_page(
        request.GET.get("page"))
    return render(request, "sales/trade_sales.html", {
        "page": page, "term": term, "home_currency": functional_currency(),
    })


@permission_required("sales.trade.create")
def trade_sale_new(request):
    allowed = request.actor.branch_ids("sales.trade.create")
    branches = Branch.objects.filter(is_active=True).order_by("code")
    if allowed is not None:
        branches = branches.filter(pk__in=allowed)
    lots = (LotBalance.objects.filter(gross_weight_g__gt=0, lot__branch__in=branches,
                                      lot__karat__isnull=False)
            .exclude(lot__category__code=SCRAP_CATEGORY_CODE)
            .select_related("lot__category", "lot__karat").order_by("lot__category__code"))
    account_id = request.GET.get("account", "")
    account = (Party.objects.filter(pk=int(account_id), roles__role=PartyRoleType.TRADE_ACCOUNT,
                                    is_active=True).first() if account_id.isdigit() else None)
    return render(request, "sales/trade_sale_form.html", {
        "account": account, "branches": branches,
        "default_branch": request.membership.default_branch_id,
        "lots": lots, "bases": SettlementBasis.choices, "home_currency": functional_currency(),
    })


@permission_required("sales.trade.view")
def trade_sale(request, pk):
    doc = (_scope(request, TradeSale.objects.select_related(
        "trade_account", "branch", "journal_entry", "posted_by")).filter(pk=pk).first())
    if doc is None:
        raise Http404
    return render(request, "sales/trade_sale.html", {
        "doc": doc, "profile": TenantProfile.objects.first(),
        "lines": doc.lines.select_related("item", "category", "karat__metal"),
        "returned": returned_line_ids(doc),
        "returns": doc.returns.order_by("-id"),
        "home_currency": functional_currency(),
        "endpoint": reverse("trade-sale-api-detail", args=[doc.pk]),
    })


@permission_required("sales.trade.view")
def trade_return(request, pk):
    doc = (_scope(request, TradeReturn.objects.select_related(
        "trade_account", "branch", "original_sale", "journal_entry")).filter(pk=pk).first())
    if doc is None:
        raise Http404
    return render(request, "sales/trade_return.html", {
        "doc": doc, "sale": doc.original_sale, "profile": TenantProfile.objects.first(),
        "lines": [line.original_line for line in doc.lines.select_related(
            "original_line__item", "original_line__category", "original_line__karat")],
        "home_currency": functional_currency(),
        "endpoint": reverse("trade-return-api-detail", args=[doc.pk]),
    })
