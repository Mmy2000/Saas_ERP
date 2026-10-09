from django.db.models import Sum
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.core.listing import Choice, ListQuery, Total
from apps.core.models import DocStatus
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
    filters = ListQuery(
        request, permission="sales.trade.view",
        search=("number", "trade_account__name", "trade_account__phone"),
        search_label=_("Number or trade account"), party="trade_account_id",
        choices=[Choice("status", _("Status"), [("", _("All")), (DocStatus.POSTED, _("Posted")),
                                                (DocStatus.VOIDED, _("Cancelled"))], "status"),
                 Choice("basis", _("Settled in"), [("", _("All"))] + list(SettlementBasis.choices),
                        "settlement_basis")])
    sales = filters.apply(_scope(request, TradeSale.objects.select_related("trade_account",
                                                                           "branch")))
    sums = TradeSale.objects.filter(
        pk__in=sales.filter(status=DocStatus.POSTED).values("pk")).aggregate(
        weight=Sum("total_gross_weight_g"), fine=Sum("total_fine_weight_g"),
        money=Sum("money_amount"), making=Sum("making_amount"))
    home = functional_currency()
    return render(request, "sales/trade_sales.html", {
        "page": filters.page(sales.order_by("-business_date", "-id")), "filters": filters,
        "home_currency": home,
        "totals": [Total(_("Weight"), sums["weight"] or 0, 3, _("g")),
                   Total(_("Fine"), sums["fine"] or 0, 3, _("g")),
                   Total(_("Making"), sums["making"] or 0, unit=home),
                   Total(_("Money"), sums["money"] or 0, unit=home)],
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
