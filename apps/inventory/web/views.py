from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse

from apps.catalog.models import ItemCategory, Karat
from apps.core.documents import document_titles, document_url
from apps.core.models import DocStatus
from apps.iam.authz import permission_required
from apps.inventory.models import (
    Item,
    ItemStatus,
    LotBalance,
    Stocktake,
    StocktakeResult,
    StockTransfer,
)
from apps.inventory.selectors import stock_by_karat
from apps.inventory.stocktakes import summary
from apps.org.models import Branch, TenantProfile


@permission_required("inventory.stock.view")
def stock(request):
    branches = request.actor.branch_ids("inventory.stock.view")
    items = Item.objects.select_related("category", "karat__metal", "branch")
    lots = (LotBalance.objects.filter(gross_weight_g__gt=0)
            .select_related("lot__category", "lot__karat__metal", "lot__branch"))
    if branches is not None:
        items = items.filter(branch_id__in=branches)
        lots = lots.filter(lot__branch_id__in=branches)

    filters = {key: request.GET.get(key, "") for key in ("q", "status", "branch", "category",
                                                         "karat")}
    filters["status"] = filters["status"] or ItemStatus.IN_STOCK
    if filters["q"]:
        term = filters["q"].strip()
        items = items.filter(Q(barcode=term) | Q(barcode__startswith=term)
                             | Q(external_code=term) | Q(rfid_epc=term))
    if filters["status"] != "all":
        items = items.filter(status=filters["status"])
    for key in ("branch", "category", "karat"):
        if filters[key].isdigit():
            items = items.filter(**{f"{key}_id": int(filters[key])})
            lots = lots.filter(**{f"lot__{key}_id": int(filters[key])})

    page = Paginator(items.order_by("-id"), 30).get_page(request.GET.get("page"))
    query = urlencode({k: v for k, v in filters.items() if v})
    return render(request, "inventory/stock.html", {
        "summary": stock_by_karat(branches),
        "page": page, "lots": lots.order_by("lot__category__code"), "filters": filters,
        "query": query, "statuses": ItemStatus.choices,
        "branches": Branch.objects.filter(is_active=True).order_by("code"),
        "categories": ItemCategory.objects.filter(is_active=True).order_by("code"),
        "karats": Karat.objects.filter(is_active=True).select_related("metal"),
    })


@permission_required("inventory.stock.view")
def item_detail(request, pk):
    branches = request.actor.branch_ids("inventory.stock.view")
    items = Item.objects.select_related("category", "karat__metal", "branch", "supplier",
                                        "cost_currency")
    if branches is not None:
        items = items.filter(branch_id__in=branches)
    item = items.filter(pk=pk).first()
    if item is None:
        raise Http404
    movements = list(item.movements.select_related("branch").order_by("business_date", "id"))
    titles = document_titles((m.document_type, m.document_id) for m in movements)
    for movement in movements:
        movement.doc_title, movement.doc_number = titles.get(
            (movement.document_type, movement.document_id), ("", ""))
        movement.doc_url = document_url(movement.document_type, movement.document_id)
    from apps.diamonds import services as diamonds
    from apps.diamonds.models import StoneKind, StoneShape

    context = {"item": item, "movements": movements}
    if diamonds.is_diamond(item.category) and diamonds.enabled():
        context.update({"diamond": True, "stones": list(item.stones.all()),
                        "stone_kinds": StoneKind.choices, "stone_shapes": StoneShape.choices})
    return render(request, "inventory/item.html", context)


# --- transfers --------------------------------------------------------------------------------

def _branches_for(request, permission):
    allowed = request.actor.branch_ids(permission)
    branches = Branch.objects.filter(is_active=True).order_by("code")
    return branches if allowed is None else branches.filter(pk__in=allowed)


def _visible_transfers(request):
    transfers = StockTransfer.objects.select_related("branch", "to_branch")
    scope = request.actor.branch_ids("inventory.stock.view")
    if scope is None:
        return transfers
    return transfers.filter(branch_id__in=scope) | transfers.filter(to_branch_id__in=scope)


@permission_required("inventory.stock.view")
def transfers(request):
    show = request.GET.get("show", "")
    queryset = _visible_transfers(request)
    if show == "transit":
        queryset = queryset.filter(status=DocStatus.POSTED, received_at__isnull=True)
    page = Paginator(queryset.order_by("-business_date", "-id"), 25).get_page(
        request.GET.get("page"))
    return render(request, "inventory/transfers.html", {"page": page, "show": show})


@permission_required("inventory.transfer.send")
def transfer_new(request):
    sources = _branches_for(request, "inventory.transfer.send")
    lots = (LotBalance.objects.filter(lot__branch__in=sources, gross_weight_g__gt=0)
            .select_related("lot__category", "lot__karat", "lot__branch")
            .order_by("lot__category__code", "lot__karat__code"))
    return render(request, "inventory/transfer_form.html", {
        "sources": sources, "destinations": Branch.objects.filter(is_active=True).order_by("code"),
        "default_branch": request.membership.default_branch_id, "lots": lots,
    })


@permission_required("inventory.stock.view")
def transfer_detail(request, pk):
    transfer = _visible_transfers(request).select_related(
        "journal_entry", "receive_entry", "posted_by", "received_by").filter(pk=pk).first()
    if transfer is None:
        raise Http404
    return render(request, "inventory/transfer.html", {
        "transfer": transfer, "profile": TenantProfile.objects.first(),
        "lines": transfer.lines.select_related("item", "category", "karat__metal"),
        "endpoint": reverse("stock-transfer-api-detail", args=[transfer.pk]),
    })


# --- stocktakes -------------------------------------------------------------------------------

def _visible_stocktakes(request):
    stocktakes_ = Stocktake.objects.select_related("branch", "category", "karat__metal")
    scope = request.actor.branch_ids("inventory.stock.view")
    return stocktakes_ if scope is None else stocktakes_.filter(branch_id__in=scope)


@permission_required("inventory.stock.view")
def stocktake_list(request):
    page = Paginator(_visible_stocktakes(request).order_by("-business_date", "-id"), 25).get_page(
        request.GET.get("page"))
    for take in page.object_list:
        take.summary = summary(take)
    return render(request, "inventory/stocktakes.html", {"page": page})


@permission_required("inventory.stocktake.start")
def stocktake_new(request):
    return render(request, "inventory/stocktake_form.html", {
        "branches": _branches_for(request, "inventory.stocktake.start"),
        "default_branch": request.membership.default_branch_id,
        "categories": ItemCategory.objects.filter(is_active=True).order_by("code"),
        "karats": Karat.objects.filter(is_active=True).select_related("metal"),
    })


@permission_required("inventory.stock.view")
def stocktake_detail(request, pk):
    take = _visible_stocktakes(request).select_related("journal_entry").filter(pk=pk).first()
    if take is None:
        raise Http404
    lines = take.lines.select_related("item__category", "item__karat__metal", "counted_by")
    return render(request, "inventory/stocktake.html", {
        "take": take, "summary": summary(take), "profile": TenantProfile.objects.first(),
        "pending": lines.filter(result=StocktakeResult.PENDING).order_by("barcode"),
        "missing": lines.filter(result=StocktakeResult.MISSING).order_by("barcode"),
        "unexpected": lines.filter(result=StocktakeResult.UNEXPECTED).order_by("-counted_at"),
        "left": lines.filter(result=StocktakeResult.LEFT).order_by("barcode"),
        "recent": lines.filter(result=StocktakeResult.COUNTED).order_by("-counted_at")[:15],
        "lot_lines": take.lot_lines.select_related("lot__category", "lot__karat__metal"),
        "endpoint": reverse("stocktake-api-detail", args=[take.pk]),
    })
