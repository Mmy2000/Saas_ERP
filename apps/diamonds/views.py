from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Currency, ItemCategory, Karat, ProductFamily, Tracking
from apps.core.models import DocStatus
from apps.iam.authz import permission_required
from apps.inventory.models import DiscountClass, Item, ItemStatus
from apps.org.models import Branch, TenantProfile
from apps.pricing.selectors import functional_currency

from . import certificates
from .models import (
    CertificateCheck,
    ItemStone,
    JobLineState,
    SettingJob,
    StoneKind,
    StoneRemoval,
    StoneSetting,
    StoneShape,
)
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
    current = request.GET.get("cls", "")
    items = (Item.objects.filter(category__product_family__in=FAMILIES)
             .select_related("category", "karat", "branch", "discount_class")
             .prefetch_related("stones"))
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
    if current == "none":
        items = items.filter(discount_class__isnull=True)
    elif current.isdigit():
        items = items.filter(discount_class_id=int(current))
    totals = items.aggregate(n=Count("id", distinct=True), ct=Sum("stone_weight_ct"),
                             cost=Sum("stone_cost_amount"), label=Sum("label_price"))
    page = Paginator(items.order_by("-created_at", "-id"), 30).get_page(request.GET.get("page"))
    for item in page.object_list:
        item.summary = stones_summary(item.stones.all())
    return render(request, "diamonds/stock.html", {
        "page": page, "state": state, "family": family, "term": term, "totals": totals,
        "current": current, "classes": DiscountClass.objects.all(),
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
        "classes": DiscountClass.objects.filter(is_active=True),
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


@permission_required("diamonds.view")
def discount_classes(request):
    classes = DiscountClass.objects.annotate(
        pieces=Count("items", filter=Q(items__status__in=(ItemStatus.IN_STOCK,
                                                          ItemStatus.RESERVED))))
    classes = list(classes)
    for found in classes:  # shown and edited as percentages (0.125 → "12.5")
        found.usual_pct = f"{(found.usual_rate * 100).normalize():f}"
        found.max_pct = f"{(found.max_rate * 100).normalize():f}"
    return render(request, "diamonds/discount_classes.html", {"classes": classes})


# --- certificates --------------------------------------------------------------------------------

@permission_required("diamonds.view")
def certificates_page(request):
    term = request.GET.get("q", "").strip()
    stones = (ItemStone.objects.exclude(certificate_no="")
              .select_related("item__category", "item__branch"))
    scope = request.actor.branch_ids("diamonds.view")
    if scope is not None:
        stones = stones.filter(item__branch_id__in=scope)
    if term:
        stones = stones.filter(Q(certificate_no__icontains=term) | Q(item__barcode=term))
    else:
        stones = stones.filter(item__status__in=(ItemStatus.IN_STOCK, ItemStatus.RESERVED))
    stones = list(stones.order_by("-item__created_at", "-id")[:60])
    checks = certificates.latest(stone.certificate_no for stone in stones
                                 if stone.lab.upper() == "IGI")
    for stone in stones:
        stone.igi = stone.lab.upper() == "IGI"
        stone.check = checks.get("".join(ch for ch in stone.certificate_no if ch.isalnum()))
    return render(request, "diamonds/certificates.html", {"stones": stones, "term": term})


@permission_required("diamonds.view")
def certificate_pdf(request, pk):
    found = CertificateCheck.objects.filter(pk=pk).first()
    if found is None or not found.pdf:
        raise Http404
    response = FileResponse(found.pdf.open("rb"), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{found.lab}-{found.report_no}.pdf"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


@permission_required("diamonds.view")
def certificate_cards(request):
    """Credit-card certificates for chosen pieces (?items=1,2), or for the diamond pieces of a
    sale with the customer's name (?sale=ID). ?format=pdf downloads them."""
    items = (Item.objects.filter(category__product_family__in=FAMILIES)
             .select_related("category", "karat").prefetch_related("stones"))
    scope = request.actor.branch_ids("diamonds.view")
    if scope is not None:
        items = items.filter(branch_id__in=scope)
    customer = ""
    sale_id = request.GET.get("sale", "")
    if sale_id.isdigit():
        from apps.sales.models import SalesInvoice

        sale = SalesInvoice.objects.filter(pk=int(sale_id)).first()
        if sale is None:
            raise Http404
        customer = sale.buyer
        items = items.filter(pk__in=sale.lines.values("item_id"))
    else:
        ids = [int(x) for x in request.GET.get("items", "").split(",") if x.strip().isdigit()]
        items = items.filter(pk__in=ids)
    made = certificates.cards(items.order_by("barcode"), customer=customer)
    if not made:
        raise Http404
    context = {"cards": made, "profile": TenantProfile.objects.first(),
               "today": timezone.localdate()}
    if request.GET.get("format") == "pdf":
        from django.template.loader import render_to_string

        from apps.printing.pdf import Paper, PdfUnavailable, html_to_pdf, logo_src, pdf_response

        try:
            content = html_to_pdf(render_to_string("diamonds/cards.html", {
                **context, "logo": logo_src(context["profile"]), "for_pdf": True}, request),
                Paper(width="85.6mm", height="54mm"))
        except PdfUnavailable:
            return render(request, "printing/pdf_unavailable.html", status=503)
        return pdf_response(content, "certificates.pdf")
    logo = context["profile"].logo.url if context["profile"] and context["profile"].logo else ""
    return render(request, "diamonds/cards.html", {**context, "logo": logo})
