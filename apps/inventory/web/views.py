from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.catalog.models import ItemCategory, Karat
from apps.core.documents import document_titles, document_url
from apps.core.models import DocStatus
from apps.iam.authz import permission_required
from apps.iam.catalog import AUTHENTICATED
from apps.inventory.models import (
    DiscountClass,
    Item,
    ItemStatus,
    LotBalance,
    Stocktake,
    StocktakeResult,
    StockTransfer,
)
from apps.inventory.selectors import filter_pieces, stock_by_karat
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

    items, filters = filter_pieces(items, request.GET)
    for key in ("branch", "category", "karat"):
        if filters[key].isdigit():
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
    from apps.diamonds.models import StoneKind, StoneOrigin, StoneShape

    context = {"item": item, "movements": movements, "price": _selling_price(item)}
    if diamonds.is_diamond(item.category) and diamonds.enabled():
        from apps.diamonds.grades import choices as grade_choices
        from apps.diamonds.stone_codes import codes_for_entry

        stones = list(item.stones.select_related("code"))
        grades = grade_choices()
        context.update({"diamond": True, "stones": stones, "grades": grades,
                        "stone_codes": codes_for_entry(),
                        "reference_value": diamonds.reference_value(stones),
                        "model_class_choices": [*grades["model"], *(
                            c for c in item.model_classes if c not in grades["model"])],
                        "stone_origins": StoneOrigin.choices,
                        "stone_kinds": StoneKind.choices, "stone_shapes": StoneShape.choices,
                        "discount_classes": DiscountClass.objects.filter(
                            Q(is_active=True) | Q(pk=item.discount_class_id))})
    return render(request, "inventory/item.html", context)


def _selling_price(item) -> dict:
    """The piece's making charge (its own or its category's), any offer on it today, and its
    earlier price changes (apps.merchandising)."""
    from django.utils import timezone

    from apps.inventory.models import ON_HAND
    from apps.merchandising.models import PriceChangeLine
    from apps.merchandising.offers import deal_for, running
    from apps.pricing.engine import category_making_rate
    from apps.pricing.selectors import functional_currency

    category_rate = category_making_rate(item.category_id, functional_currency())
    rate = item.list_making_rate or category_rate
    deal = None
    if item.status in ON_HAND and item.karat_id:
        deal = deal_for(running(timezone.localdate(), item.branch_id), making=rate,
                        category_id=item.category_id, karat_id=item.karat_id,
                        supplier_id=item.supplier_id, barcode=item.barcode)
    history = (PriceChangeLine.objects.filter(item=item).select_related("change__created_by")
               .order_by("-change__created_at")[:10])
    return {"rate": rate, "own": bool(item.list_making_rate), "category_rate": category_rate,
            "deal": deal if deal and (deal.making_rate is not None or deal.cashbacks) else None,
            "history": list(history), "on_hand": item.status in ON_HAND,
            "is_label": item.label_price is not None and not item.karat_id
            or item.category.product_family in ("diamond", "stone")}


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
    """Transfers between branches by date, branch (sent or received) and state, with totals
    (Gweb's transfer reports GHBR22–48)."""
    from django.db.models import Sum

    from apps.core.listing import Choice, ListQuery, Total

    states = {"transit": Q(status=DocStatus.POSTED, received_at__isnull=True),
              "received": Q(received_at__isnull=False), "cancelled": Q(status=DocStatus.VOIDED)}
    filters = ListQuery(
        request, permission="inventory.stock.view",
        search=("number", "lines__item__barcode", "note"),
        search_label=_("Number, barcode or note"),
        branch_field=lambda qs, ids: qs.filter(Q(branch_id__in=ids) | Q(to_branch_id__in=ids)),
        choices=[Choice("show", _("State"), [("", _("All")), ("transit", _("In transit")),
                                             ("received", _("Received")),
                                             ("cancelled", _("Cancelled"))],
                        apply=lambda qs, v: qs.filter(states[v]), tabs=True)])
    found = filters.apply(_visible_transfers(request))
    sums = StockTransfer.objects.filter(pk__in=found.exclude(
        status=DocStatus.VOIDED).values("pk")).aggregate(
        qty=Sum("total_qty"), gross=Sum("total_gross_weight_g"),
        fine=Sum("total_fine_weight_g"))
    return render(request, "inventory/transfers.html", {
        "page": filters.page(found.order_by("-business_date", "-id")), "filters": filters,
        "totals": [Total(_("Items"), sums["qty"] or 0, 0),
                   Total(_("Weight"), sums["gross"] or 0, 3, _("g")),
                   Total(_("Fine"), sums["fine"] or 0, 3, _("g"))]})


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


@permission_required(AUTHENTICATED)
def in_transit(request):
    """Everything sent between branches and not received yet (legacy الخزن الوسيطة, one screen
    per kind in Gweb): goods by transfer with totals per karat, and cash."""
    from collections import defaultdict
    from decimal import Decimal

    from django.utils import timezone

    from apps.inventory.models import StockTransferLine
    from apps.inventory.services import SCRAP_CATEGORY_CODE

    see_goods, see_cash = (request.actor.can("inventory.stock.view"),
                           request.actor.can("treasury.view"))
    if not (see_goods or see_cash):
        raise PermissionDenied
    branch = request.GET.get("branch", "")
    direction = request.GET.get("direction", "")
    if direction and not branch.isdigit() and request.membership.default_branch_id:
        branch = str(request.membership.default_branch_id)  # "coming to my branch"
    today = timezone.localdate()

    def narrow(rows):
        if not branch.isdigit():
            return rows
        if direction == "in":
            return rows.filter(to_branch_id=int(branch))
        if direction == "out":
            return rows.filter(branch_id=int(branch))
        return rows.filter(Q(branch_id=int(branch)) | Q(to_branch_id=int(branch)))

    transfers = []
    if see_goods:
        transfers = list(narrow(_visible_transfers(request).filter(
            status=DocStatus.POSTED, received_at__isnull=True)).order_by("business_date", "id"))
    by_karat = defaultdict(lambda: {"pieces": 0, "qty": 0, "gross": Decimal(0),
                                    "fine": Decimal(0), "cost": Decimal(0)})
    totals = {"pieces": 0, "gross": Decimal(0), "fine": Decimal(0), "cost": Decimal(0)}
    lines = StockTransferLine.objects.filter(transfer__in=transfers).select_related(
        "karat__metal", "category")
    for line in lines:
        scrap = line.category.code == SCRAP_CATEGORY_CODE
        key = (line.karat.metal.code if line.karat else "", line.karat.code if line.karat else 0,
               scrap)
        row = by_karat[key]
        row["label"] = str(line.karat) if line.karat else "—"
        row["scrap"] = scrap
        row["pieces"] += 1 if line.item_id else 0
        row["qty"] += 0 if line.item_id else line.qty
        row["gross"] += line.gross_weight_g
        row["fine"] += line.fine_weight_g
        row["cost"] += line.cost_amount
        totals["pieces"] += 1 if line.item_id else 0
        totals["gross"] += line.gross_weight_g
        totals["fine"] += line.fine_weight_g
        totals["cost"] += line.cost_amount
    for transfer in transfers:
        transfer.days = (today - transfer.business_date).days

    cash, cash_totals = None, {}
    if see_cash:
        from apps.treasury.models import TreasuryDocument

        documents = TreasuryDocument.objects.select_related("branch", "to_branch", "currency")
        scope = request.actor.branch_ids("treasury.view")
        if scope is not None:
            documents = (documents.filter(branch_id__in=scope)
                         | documents.filter(to_branch_id__in=scope))
        cash = list(narrow(documents.filter(status=DocStatus.POSTED, needs_receipt=True,
                                            received_at__isnull=True))
                    .order_by("business_date", "id"))
        for doc in cash:
            doc.days = (today - doc.business_date).days
            cash_totals[doc.currency.code] = cash_totals.get(doc.currency.code, 0) + doc.amount

    branches = Branch.objects.filter(is_active=True).order_by("code")
    scope = request.actor.branch_ids("inventory.stock.view" if see_goods else "treasury.view")
    if scope is not None:
        branches = branches.filter(pk__in=scope)
    return render(request, "inventory/in_transit.html", {
        "see_goods": see_goods,
        "transfers": transfers, "karats": [by_karat[k] for k in sorted(by_karat)],
        "totals": totals, "cash": cash, "cash_totals": sorted(cash_totals.items()),
        "branches": branches, "branch": branch, "direction": direction,
    })


# --- blind counts (legacy GHS45a / GHS45b) ---------------------------------------------------

def _visible_blind_counts(request):
    from apps.inventory.models import BlindCount

    counts = BlindCount.objects.select_related("branch", "created_by")
    allowed = request.actor.branch_ids("inventory.blind_count.view")
    return counts if allowed is None else counts.filter(branch_id__in=allowed)


@permission_required("inventory.blind_count.view")
def blind_counts(request):
    page = Paginator(_visible_blind_counts(request).order_by("-posted_at", "-id"), 25).get_page(
        request.GET.get("page"))
    return render(request, "inventory/blind_counts.html", {"page": page})


@permission_required("inventory.blind_count.create")
def blind_count_new(request):
    from django.contrib import messages
    from django.shortcuts import redirect

    from apps.core.errors import ValidationError
    from apps.inventory import blind_counts as service

    branches = _branches_for(request, "inventory.blind_count.create")
    source = request.POST if request.method == "POST" else request.GET
    again = _visible_blind_counts(request).filter(pk=request.GET.get("again") or 0).first()
    branch_id = source.get("branch") or (again.branch_id if again else None) \
        or request.membership.default_branch_id
    by_group = source.get("by", "group" if again and again.by_group else "") == "group"
    parts = [p for p in service.PARTS if p in source.getlist("parts")] or (
        [p for p in service.PARTS if p != "cash" or not again or again.cash_currency]
        if again else list(service.PARTS))
    layout = service.sheet(by_group=by_group)
    errors = {}
    if request.method == "POST" and request.POST.get("save"):
        def typed(prefix):
            found = {}
            for key, value in request.POST.items():
                if key.startswith(prefix + "-") and value.strip():
                    _p, row, karat = key.split("-")
                    found[(int(row), int(karat) or None)] = value
            return found

        try:
            made = service.record(service.BlindCountInput(
                branch_id=int(branch_id or 0), by_group=by_group, parts=tuple(parts),
                pieces=typed("p"), weights=typed("w"), cash=request.POST.get("cash"),
                note=request.POST.get("note", "")), actor=request.actor)
        except ValidationError as exc:
            errors = {key: " ".join(str(m) for m in problems)
                      for key, problems in exc.fields.items()}
            messages.error(request, exc.message)
        else:
            return redirect("blind-count", pk=made.pk)
    values = {}
    if request.method == "POST":
        values = {k: v for k, v in request.POST.items() if k[:2] in ("p-", "w-")}
        values["cash"] = request.POST.get("cash", "")
    elif again:
        for kind, cells in (("p", again.pieces), ("w", again.weights)):
            for cell in cells:
                values[f"{kind}-{cell['category']}-{cell['karat'] or 0}"] = cell["counted"]
        values["cash"] = again.cash_counted if again.cash_counted is not None else ""

    def grid(rows, kind):
        return [{"row": row, "cells": [
            {"name": f"{kind}-{row.pk}-{karat.pk if karat else 0}",
             "value": values.get(f"{kind}-{row.pk}-{karat.pk if karat else 0}", "")}
            for karat in layout.columns]} for row in rows]

    return render(request, "inventory/blind_count_form.html", {
        "branches": branches, "branch_id": str(branch_id or ""), "by_group": by_group,
        "parts": parts, "layout": layout, "errors": errors, "again": again,
        "piece_grid": grid(layout.piece_rows, "p"), "weight_grid": grid(layout.weight_rows, "w"),
        "cash": values.get("cash", ""), "note": source.get("note", ""),
    })


@permission_required("inventory.blind_count.view")
def blind_count_detail(request, pk):
    count = _visible_blind_counts(request).filter(pk=pk).first()
    if count is None:
        raise Http404

    def table(cells):
        columns, rows = [], {}
        for cell in cells:
            if cell["label"] not in columns:
                columns.append(cell["label"])
            rows.setdefault((cell["category"], cell["name"]), {})[cell["label"]] = cell
        return {"columns": columns,
                "rows": [{"name": name, "cells": [found.get(label) for label in columns]}
                         for (_id, name), found in rows.items()]}

    return render(request, "inventory/blind_count.html", {
        "count": count, "pieces": table(count.pieces), "weights": table(count.weights),
        "reveal": request.actor.can("admin.blind_count.reveal", branch=count.branch),
        "cash_ok": count.cash_counted == count.cash_actual,
    })


# --- photo catalogue (legacy GHMRK2 / GHMRK4) ------------------------------------------------

@permission_required("inventory.stock.view")
def catalogue(request):
    from django.utils import timezone

    from apps.inventory import catalogue as service

    items, filters, price_range = service.pieces(
        request.GET, branch_ids=request.actor.branch_ids("inventory.stock.view"))
    query = urlencode({k: v for k, v in filters.items() if v})
    if request.GET.get("format") == "pdf":
        from django.template.loader import render_to_string

        from apps.printing.pdf import Paper, PdfUnavailable, html_to_pdf, logo_src, pdf_response

        profile = TenantProfile.objects.first()
        entries = service.priced(items, price_range, limit=300)
        html = render_to_string("inventory/catalogue_sheet.html", {
            "entries": entries, "profile": profile, "logo": logo_src(profile),
            "made_at": timezone.localtime(), "home_currency": _home_currency()}, request=request)
        try:
            content = html_to_pdf(html, Paper(page_numbers=True))
        except PdfUnavailable:
            return render(request, "printing/pdf_unavailable.html", status=503)
        return pdf_response(content, f"{_('Catalogue')} {timezone.localdate().isoformat()}.pdf")
    if any(v is not None for v in price_range):
        entries = service.priced(items, price_range)
        page = Paginator(entries, 24).get_page(request.GET.get("page"))
    else:
        page = Paginator(items, 24).get_page(request.GET.get("page"))
        page.object_list = service.priced(page.object_list, (None, None))
    return render(request, "inventory/catalogue.html", {
        "page": page, "filters": filters, "query": query,
        "branches": _branches_for(request, "inventory.stock.view"),
        "categories": ItemCategory.objects.filter(is_active=True, tracking="serialized")
        .order_by("code"),
        "karats": Karat.objects.filter(is_active=True).select_related("metal"),
        "home_currency": _home_currency(),
    })


def _home_currency() -> str:
    from apps.pricing.selectors import functional_currency

    return functional_currency()


@permission_required("inventory.item.photo")
def item_photo(request, pk):
    from django.contrib import messages
    from django.shortcuts import redirect

    from apps.core.errors import ValidationError
    from apps.inventory import catalogue as service

    if request.method != "POST":
        raise Http404
    item = service.item_for_photo(pk)
    try:
        service.set_photo(item, None if request.POST.get("remove") else request.FILES.get("photo"),
                          actor=request.actor, branch=item.branch)
    except ValidationError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, _("Photo saved.") if item.photo else _("Photo removed."))
    return redirect("stock-item", pk=item.pk)


@permission_required("inventory.item.photo")
def category_photo(request, pk):
    from django.contrib import messages
    from django.shortcuts import redirect

    from apps.core.errors import ValidationError
    from apps.inventory import catalogue as service

    category = ItemCategory.objects.filter(pk=pk).first()
    if request.method != "POST" or category is None:
        raise Http404
    try:
        service.set_photo(category, None if request.POST.get("remove")
                          else request.FILES.get("photo"), actor=request.actor)
    except ValidationError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, _("Photo saved.") if category.photo else _("Photo removed."))
    return redirect("category-edit", pk=category.pk)
