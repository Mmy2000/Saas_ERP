from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse

from apps.catalog.models import Currency, ItemCategory, Karat, ProductFamily, Tracking
from apps.core.models import DocStatus
from apps.iam.authz import permission_required
from apps.inventory.models import Item, ItemStatus
from apps.org.models import Branch, TenantProfile
from apps.pricing.selectors import functional_currency

from .models import StoneKind, StoneSetting, StoneShape
from .services import FAMILIES, stones_summary

STATES = ("in_stock", "sold", "all")


def _branches(request, permission):
    allowed = request.actor.branch_ids(permission)
    branches = Branch.objects.filter(is_active=True).order_by("code")
    return branches if allowed is None else branches.filter(pk__in=allowed)


@permission_required("diamonds.view")
def stock(request):
    state = request.GET.get("state") if request.GET.get("state") in STATES else "in_stock"
    family = request.GET.get("family", "")
    term = request.GET.get("q", "").strip()
    items = (Item.objects.filter(category__product_family__in=FAMILIES)
             .select_related("category", "karat", "branch").prefetch_related("stones"))
    scope = request.actor.branch_ids("diamonds.view")
    if scope is not None:
        items = items.filter(branch_id__in=scope)
    if state == "in_stock":
        items = items.filter(status__in=(ItemStatus.IN_STOCK, ItemStatus.RESERVED))
    elif state == "sold":
        items = items.filter(status=ItemStatus.SOLD)
    if family in FAMILIES:
        items = items.filter(category__product_family=family)
    if term:
        items = items.filter(Q(barcode=term) | Q(stones__certificate_no__icontains=term)
                             | Q(category__name__icontains=term)).distinct()
    totals = items.aggregate(n=Count("id", distinct=True), ct=Sum("stone_weight_ct"),
                             cost=Sum("stone_cost_amount"), label=Sum("label_price"))
    page = Paginator(items.order_by("-created_at", "-id"), 30).get_page(request.GET.get("page"))
    for item in page.object_list:
        item.summary = stones_summary(item.stones.all())
    return render(request, "diamonds/stock.html", {
        "page": page, "state": state, "family": family, "term": term, "totals": totals,
        "families": [(value, label) for value, label in ProductFamily.choices
                     if value in FAMILIES],
        "home_currency": functional_currency(),
    })


@permission_required("diamonds.receive")
def receive(request):
    categories = ItemCategory.objects.filter(product_family__in=FAMILIES, is_active=True,
                                             tracking=Tracking.SERIALIZED).order_by("code")
    return render(request, "diamonds/receive.html", {
        "categories": categories,
        "karats": Karat.objects.filter(is_active=True, metal__code="gold").order_by("-code"),
        "branches": _branches(request, "diamonds.receive"),
        "default_branch": request.membership.default_branch_id,
        "currencies": Currency.objects.filter(is_active=True),
        "home_currency": functional_currency(),
        "kinds": StoneKind.choices, "shapes": StoneShape.choices,
    })


@permission_required("diamonds.view")
def settings_list(request):
    settings = StoneSetting.objects.select_related("piece", "setter", "branch")
    scope = request.actor.branch_ids("diamonds.view")
    if scope is not None:
        settings = settings.filter(branch_id__in=scope)
    page = Paginator(settings.order_by("-business_date", "-id"), 25).get_page(
        request.GET.get("page"))
    return render(request, "diamonds/settings.html", {
        "page": page, "home_currency": functional_currency()})


@permission_required("diamonds.setting")
def setting_new(request):
    return render(request, "diamonds/setting_form.html", {
        "branches": _branches(request, "diamonds.setting"),
        "default_branch": request.membership.default_branch_id,
        "home_currency": functional_currency(),
    })


@permission_required("diamonds.view")
def setting(request, pk):
    item = (StoneSetting.objects.select_related("piece__category", "piece__karat", "setter",
                                                "branch", "journal_entry", "posted_by")
            .filter(pk=pk).first())
    scope = request.actor.branch_ids("diamonds.view")
    if item is None or (scope is not None and item.branch_id not in scope):
        raise Http404
    return render(request, "diamonds/setting.html", {
        "setting": item, "rows": item.stones.select_related("stone__category")
        .prefetch_related("stone__stones"),
        "profile": TenantProfile.objects.first(), "home_currency": functional_currency(),
        "void_url": reverse("diamonds-setting-void", args=[item.pk]),
        "can_cancel": item.status == DocStatus.POSTED,
    })
