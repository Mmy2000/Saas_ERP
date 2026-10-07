from django.core.paginator import Paginator
from django.db.models import Count, Sum
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone

from apps.iam.authz import permission_required

from .models import Offer, OfferKind, PriceChange, PriceMethod, PriceTarget


def _choices(request, permission):
    from apps.catalog.models import ItemCategory, Karat
    from apps.org.models import Branch
    from apps.parties.models import PartyRoleType
    from apps.parties.selectors import parties_with_role

    scope = request.actor.branch_ids(permission)
    branches = Branch.objects.filter(is_active=True).order_by("code")
    return {
        "branches": branches if scope is None else branches.filter(pk__in=scope),
        "categories": ItemCategory.objects.filter(is_active=True).order_by("code"),
        "karats": Karat.objects.filter(is_active=True).select_related("metal").order_by(
            "metal", "code"),
        "suppliers": parties_with_role(PartyRoleType.SUPPLIER).filter(is_active=True)
        .order_by("name"),
    }


# ---- price changes ---------------------------------------------------------------------------

@permission_required("inventory.price.change")
def price_changes(request):
    page = Paginator(PriceChange.objects.select_related("created_by", "undone_by")
                     .order_by("-created_at", "-id"), 30).get_page(request.GET.get("page"))
    return render(request, "merchandising/price_changes.html", {
        "page": page, **_choices(request, "inventory.price.change"),
        "targets": PriceTarget.choices, "methods": PriceMethod.choices, "selected": {},
    })


@permission_required("inventory.price.change")
def price_change(request, pk):
    change = PriceChange.objects.select_related("created_by", "undone_by").filter(pk=pk).first()
    if change is None:
        raise Http404
    page = Paginator(change.lines.select_related("item__category", "item__karat")
                     .order_by("item__barcode"), 100).get_page(request.GET.get("page"))
    return render(request, "merchandising/price_change.html", {"change": change, "page": page})


# ---- offers -----------------------------------------------------------------------------------

VIEWS = ("running", "upcoming", "ended")


def _state(offer: Offer, today) -> str:
    if not offer.is_active or offer.ends_on < today:
        return "ended"
    return "upcoming" if offer.starts_on > today else "running"


@permission_required("sales.offer.view")
def offers(request):
    today = timezone.localdate()
    view = request.GET.get("show", "running")
    view = view if view in VIEWS else "running"
    rows = [o for o in Offer.objects.order_by("-starts_on", "-id") if _state(o, today) == view]
    return render(request, "merchandising/offers.html", {
        "rows": rows, "show": view, "views": VIEWS, "today": today})


def _offer_form(request, offer=None):
    from apps.sales.models import SalesInvoiceLine

    from .offers import pieces_in_offer

    stats = None
    if offer is not None:
        from apps.core.models import DocStatus

        sold = SalesInvoiceLine.objects.filter(invoice__status=DocStatus.POSTED)
        stats = {
            "price": sold.filter(offer=offer).aggregate(lines=Count("id"),
                                                        weight=Sum("gross_weight_g"),
                                                        saving=Sum("offer_saving"),
                                                        total=Sum("line_total")),
            "cashback": sold.filter(cashback_offer=offer).aggregate(
                lines=Count("id"), amount=Sum("cashback_amount")),
            "pieces": pieces_in_offer(offer).count(),
        }
    selected = {}
    if offer is not None:
        selected = {"branch_ids": offer.branch_ids, "category_ids": offer.category_ids,
                    "karat_ids": offer.karat_ids, "supplier_ids": offer.supplier_ids,
                    "barcodes_text": "\n".join(offer.barcodes)}
    return render(request, "merchandising/offer_form.html", {
        "offer": offer, "kinds": OfferKind.choices, "stats": stats, "selected": selected,
        "state": _state(offer, timezone.localdate()) if offer else None,
        "today": timezone.localdate(), **_choices(request, "sales.offer.view"),
    })


@permission_required("sales.offer.manage")
def offer_new(request):
    return _offer_form(request)


@permission_required("sales.offer.view")
def offer(request, pk):
    item = Offer.objects.filter(pk=pk).first()
    if item is None:
        raise Http404
    return _offer_form(request, item)
