from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.catalog.models import Currency, ItemCategory, Karat, ProductFamily, Tracking
from apps.core.models import DocStatus
from apps.iam.authz import permission_required
from apps.inventory.models import Item, ItemStatus
from apps.org.models import Branch, TenantProfile
from apps.pricing.selectors import functional_currency

from .models import JobLineState, SettingJob, StoneKind, StoneRemoval, StoneSetting, StoneShape
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
    item = (StoneSetting.objects.select_related("piece__category", "piece__karat", "setter", "job",
                                                "branch", "journal_entry", "posted_by")
            .filter(pk=pk).first())
    scope = request.actor.branch_ids("diamonds.view")
    if item is None or (scope is not None and item.branch_id not in scope):
        raise Http404
    rows = list(item.stones.select_related("stone__category", "removal")
                .prefetch_related("stone__stones"))
    return render(request, "diamonds/setting.html", {
        "setting": item, "rows": rows,
        "still_in": [row for row in rows if row.removal_id is None],
        "removals": item.removals.filter(status=DocStatus.POSTED).select_related("setter"),
        "remove_url": reverse("diamonds-setting-remove", args=[item.pk]),
        "profile": TenantProfile.objects.first(), "home_currency": functional_currency(),
        "void_url": reverse("diamonds-setting-void", args=[item.pk]),
        "can_cancel": item.status == DocStatus.POSTED,
    })


# --- setter jobs ---------------------------------------------------------------------------------

JOB_TABS = ("open", "closed", "all")


@permission_required("diamonds.view")
def jobs(request):
    tab = request.GET.get("tab") if request.GET.get("tab") in JOB_TABS else "open"
    queryset = (SettingJob.objects.filter(status=DocStatus.POSTED)
                .select_related("setter", "branch")
                .annotate(total=Count("lines"),
                          out=Count("lines", filter=Q(lines__state=JobLineState.OUT)),
                          set_count=Count("lines", filter=Q(lines__state=JobLineState.SET))))
    scope = request.actor.branch_ids("diamonds.view")
    if scope is not None:
        queryset = queryset.filter(branch_id__in=scope)
    if tab == "open":
        queryset = queryset.filter(closed_at__isnull=True)
    elif tab == "closed":
        queryset = queryset.filter(closed_at__isnull=False)
    page = Paginator(queryset.order_by("-business_date", "-id"), 25).get_page(
        request.GET.get("page"))
    return render(request, "diamonds/jobs.html", {"page": page, "tab": tab})


@permission_required("diamonds.setting")
def job_new(request):
    return render(request, "diamonds/job_form.html", {
        "branches": _branches(request, "diamonds.setting"),
        "default_branch": request.membership.default_branch_id,
    })


@permission_required("diamonds.view")
def job(request, pk):
    item = (SettingJob.objects.select_related("setter", "branch", "journal_entry", "posted_by")
            .filter(pk=pk).first())
    scope = request.actor.branch_ids("diamonds.view")
    if item is None or (scope is not None and item.branch_id not in scope):
        raise Http404
    lines = list(item.lines.select_related("item__category", "item__karat", "setting")
                 .prefetch_related("item__stones").order_by("state", "item__barcode"))
    for line in lines:
        line.summary = stones_summary(line.item.stones.all())
        line.family = line.item.category.product_family
    out = [line for line in lines if line.state == JobLineState.OUT]
    return render(request, "diamonds/job.html", {
        "job": item, "lines": lines,
        "mountings": [line for line in out if line.family in ("gold", "diamond")],
        "stones": [line for line in out if line.family == "stone"],
        "out": out,
        "diamond_categories": ItemCategory.objects.filter(
            product_family=ProductFamily.DIAMOND, tracking=Tracking.SERIALIZED,
            is_active=True).order_by("code"),
        "settings": item.settings.filter(status=DocStatus.POSTED).select_related("piece"),
        "profile": TenantProfile.objects.first(), "home_currency": functional_currency(),
    })


@permission_required("diamonds.view")
def stone_removal(request, pk):
    removal = StoneRemoval.objects.filter(pk=pk).values_list("setting_id", flat=True).first()
    if removal is None:
        raise Http404
    return redirect(f"{reverse('stone-setting', args=[removal])}#removals")
