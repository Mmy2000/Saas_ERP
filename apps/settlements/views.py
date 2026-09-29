from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse

from apps.catalog.models import Currency, Karat
from apps.iam.authz import permission_required
from apps.org.models import Branch, TenantProfile
from apps.parties.models import Party
from apps.pricing.selectors import fine_gram_value, functional_currency
from apps.treasury.selectors import holder_choices

from .models import ConversionDirection, MoneyMethod, PartySide, Settlement, SettlementKind
from .services import PERMISSIONS


def _visible(request):
    settlements = Settlement.objects.select_related("party", "currency", "karat__metal", "branch")
    branches = request.actor.branch_ids("settlements.view")
    return settlements if branches is None else settlements.filter(branch_id__in=branches)


@permission_required("settlements.view")
def settlements(request):
    kind = request.GET.get("kind", "")
    queryset = _visible(request)
    if kind in SettlementKind.values:
        queryset = queryset.filter(kind=kind)
    page = Paginator(queryset.order_by("-business_date", "-id"), 25).get_page(
        request.GET.get("page"))
    return render(request, "settlements/list.html", {
        "page": page, "kind": kind, "kinds": SettlementKind.choices,
        "home_currency": functional_currency(),
    })


PARTY_PAGES = {PartySide.CUSTOMER: "customer-edit", PartySide.SUPPLIER: "supplier-edit",
               PartySide.TRADE_ACCOUNT: "trade-account-edit"}


@permission_required("settlements.view")
def new_settlement(request):
    actor = request.actor
    kinds = [(value, label) for value, label in SettlementKind.choices
             if actor.can(PERMISSIONS[value])]
    side = request.GET.get("side") if request.GET.get("side") in PartySide.values else "customer"
    party = None
    if (request.GET.get("party") or "").isdigit():
        party = Party.objects.filter(pk=int(request.GET["party"]), roles__role=side).first()
    allowed = actor.branch_ids("settlements.view")
    branches = Branch.objects.filter(is_active=True).order_by("code")
    if allowed is not None:
        branches = branches.filter(pk__in=allowed)
    return render(request, "settlements/form.html", {
        "kinds": kinds,
        "kind": request.GET.get("kind") or (kinds[0][0] if kinds else ""),
        "side": side, "party": party,
        "branches": branches, "default_branch": request.membership.default_branch_id,
        "methods": MoneyMethod.choices, "directions": ConversionDirection.choices,
        "currencies": Currency.objects.filter(is_active=True),
        "home_currency": functional_currency(),
        "karats": Karat.objects.filter(is_active=True).select_related("metal"),
        "gold_value": fine_gram_value("gold"),
        **holder_choices(allowed),
    })


@permission_required("settlements.view")
def settlement_detail(request, pk):
    settlement = _visible(request).select_related("journal_entry", "posted_by").filter(
        pk=pk).first()
    if settlement is None:
        raise Http404
    return render(request, "settlements/detail.html", {
        "settlement": settlement, "profile": TenantProfile.objects.first(),
        "home_currency": functional_currency(),
        "endpoint": reverse("settlement-detail", args=[settlement.pk]),
        "party_url": reverse(PARTY_PAGES[settlement.side], args=[settlement.party_id]),
    })
