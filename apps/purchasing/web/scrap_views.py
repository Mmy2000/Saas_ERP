from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse

from apps.catalog.models import Karat
from apps.iam.authz import permission_required
from apps.inventory.models import LotBalance
from apps.inventory.services import SCRAP_CATEGORY_CODE
from apps.org.models import Branch, TenantProfile
from apps.parties.models import PartyRoleType
from apps.pricing.selectors import current_price_board, functional_currency
from apps.purchasing.models import ScrapPurchase, ScrapSale
from apps.treasury.selectors import holder_choices


def _scope(request):
    return request.actor.branch_ids("purchasing.scrap.view")


def _visible(request, model):
    documents = model.objects.select_related("branch")
    scope = _scope(request)
    return documents if scope is None else documents.filter(branch_id__in=scope)


@permission_required("purchasing.scrap.view")
def scrap(request):
    tab = "sales" if request.GET.get("tab") == "sales" else "purchases"
    model = ScrapSale if tab == "sales" else ScrapPurchase
    related = "buyer" if tab == "sales" else "seller"
    page = Paginator(_visible(request, model).select_related(related).order_by(
        "-business_date", "-id"), 25).get_page(request.GET.get("page"))
    stock = (LotBalance.objects.filter(lot__category__code=SCRAP_CATEGORY_CODE,
                                       gross_weight_g__gt=0)
             .select_related("lot__karat", "lot__branch").order_by("lot__branch__code",
                                                                  "-lot__karat__code"))
    scope = _scope(request)
    if scope is not None:
        stock = stock.filter(lot__branch_id__in=scope)
    return render(request, "purchasing/scrap.html", {
        "tab": tab, "page": page, "stock": stock, "home_currency": functional_currency(),
    })


def _branches(request, permission):
    allowed = request.actor.branch_ids(permission)
    branches = Branch.objects.filter(is_active=True).order_by("code")
    return (branches if allowed is None else branches.filter(pk__in=allowed)), allowed


@permission_required("purchasing.scrap.buy")
def scrap_buy(request):
    branches, allowed = _branches(request, "purchasing.scrap.buy")
    return render(request, "purchasing/scrap_form.html", {
        "mode": "buy", "branches": branches,
        "default_branch": request.membership.default_branch_id,
        "karats": Karat.objects.filter(is_active=True).select_related("metal").order_by("-code"),
        "board": current_price_board(), "home_currency": functional_currency(),
        "can_override": request.actor.can("purchasing.scrap.override_price"),
        "quote_url": reverse("scrap-quote"), "post_url": reverse("scrap-purchase-api-list"),
        "detail_url": reverse("scrap") + "bought/{id}/", **holder_choices(allowed),
    })


@permission_required("purchasing.scrap.sell")
def scrap_sell(request):
    branches, allowed = _branches(request, "purchasing.scrap.sell")
    stock = (LotBalance.objects.filter(lot__category__code=SCRAP_CATEGORY_CODE,
                                       gross_weight_g__gt=0, lot__branch__in=branches)
             .select_related("lot__karat"))
    return render(request, "purchasing/scrap_form.html", {
        "mode": "sell", "branches": branches, "stock": stock,
        "default_branch": request.membership.default_branch_id,
        "home_currency": functional_currency(),
        "post_url": reverse("scrap-sale-api-list"),
        "detail_url": reverse("scrap") + "sold/{id}/", **holder_choices(allowed),
    })


def _detail(request, model, pk, template, endpoint_name):
    doc = _visible(request, model).select_related("journal_entry", "posted_by").filter(
        pk=pk).first()
    if doc is None:
        raise Http404
    party = doc.seller if model is ScrapPurchase else doc.buyer
    party_url = None
    if party is not None:
        is_customer = party.roles.filter(role=PartyRoleType.CUSTOMER).exists()
        party_url = reverse("customer-edit" if is_customer else "supplier-edit", args=[party.pk])
    return render(request, template, {
        "doc": doc, "lines": doc.lines.select_related("karat__metal"),
        "profile": TenantProfile.objects.first(), "home_currency": functional_currency(),
        "endpoint": reverse(endpoint_name, args=[doc.pk]), "party_url": party_url,
        "can_see_id": request.actor.can("parties.customer.view_pii"),
    })


@permission_required("purchasing.scrap.view")
def scrap_purchase(request, pk):
    return _detail(request, ScrapPurchase, pk, "purchasing/scrap_purchase.html",
                   "scrap-purchase-api-detail")


@permission_required("purchasing.scrap.view")
def scrap_sale(request, pk):
    return _detail(request, ScrapSale, pk, "purchasing/scrap_sale.html", "scrap-sale-api-detail")
