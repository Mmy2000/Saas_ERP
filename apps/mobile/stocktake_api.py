"""/Sup1/api/ghu11/*: counting a branch's pieces with the phone (Gweb's GHU11 stocktake API).

A stocktake is this platform's (Goods → Stocktake): started here or on the web, counted from
either, posted on the web. `xo1` is its id and `brna` its branch's code. A barcode the count does
not expect is turned down with Gweb's reasons (sold, another branch, in transit…) and nothing is
recorded, as Gweb did; the web screen still records such pieces when scanned there.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from urllib.parse import quote

from django.core.paginator import EmptyPage, Paginator
from django.http import HttpResponse
from django.utils.translation import gettext as _
from rest_framework import status
from rest_framework.response import Response

from apps.catalog.models import ItemCategory, Karat, Tracking
from apps.core.errors import DomainError
from apps.core.models import DocStatus
from apps.inventory import stocktakes
from apps.inventory.models import Item, ItemStatus, Stocktake, StocktakeLine, StocktakeResult
from apps.org.models import Branch

from . import shapes
from .inventory_api import _int, scoped_branches
from .legacy import LegacyView, detail

COUNT = "inventory.stocktake.count"
START = "inventory.stocktake.start"


def _show_cost(request) -> bool:
    return request.actor.can("inventory.item.view_cost")


def _branch(request, code, permission=COUNT):
    """(the branch, an error response) from Gweb's `brna`."""
    number = _int(code)
    if number is None:
        return None, detail(_("Enter the branch and the stocktake number."),
                            status.HTTP_400_BAD_REQUEST)
    branch = Branch.objects.filter(code=number).first()
    if branch is None:
        return None, detail(_("Choose the branch."), status.HTTP_404_NOT_FOUND)
    if not request.actor.can(permission, branch=branch):
        return None, detail(_("You may not count stock at this branch."),
                            status.HTTP_403_FORBIDDEN)
    return branch, None


def _stocktake(request, data):
    branch, error = _branch(request, data.get("brna"))
    if error is not None:
        return None, error
    number = _int(data.get("xo1"))
    if number is None:
        return None, detail(_("Enter the branch and the stocktake number."),
                            status.HTTP_400_BAD_REQUEST)
    stocktake = (Stocktake.objects.select_related("branch", "category", "karat__metal")
                 .filter(pk=number, branch=branch).first())
    if stocktake is None:
        return None, detail(_("No stocktake with this number at this branch."),
                            status.HTTP_404_NOT_FOUND)
    return stocktake, None


def _lines(stocktake):
    return (StocktakeLine.objects.filter(stocktake=stocktake, expected=True)
            .select_related("item__category", "item__karat__metal"))


def summary(stocktake) -> dict:
    """Gweb's InventorySummarySerializer: the count's header and its totals per karat."""
    totals = defaultdict(lambda: [Decimal(0), 0, Decimal(0), 0])
    for line in _lines(stocktake):
        code = shapes.karat_code(line.item.karat) if line.item else 0
        weight = line.item.gross_weight_g if line.item else Decimal(0)
        totals[code][0] += weight
        totals[code][1] += 1
        if line.result == StocktakeResult.COUNTED:
            totals[code][2] += weight
            totals[code][3] += 1
    category = stocktake.category
    group = shapes.top_group(category)
    return {
        "xo1": stocktake.pk, "date": stocktake.business_date.isoformat(),
        "branch": stocktake.branch.code, "branch_name": stocktake.branch.name,
        "group": category.pk if category else None, "group_name": category.name if category else "",
        "main_group": group.pk if group and group != category else None,
        "main_group_name": group.name if group and group != category else "",
        "karat": shapes.karat_code(stocktake.karat) if stocktake.karat_id else None,
        "karats": [{"karat": code, "start_weight": shapes.grams(totals[code][0]),
                    "end_weight": shapes.grams(totals[code][2]),
                    "diff_weight": shapes.grams(totals[code][0] - totals[code][2]),
                    "start_count": totals[code][1], "end_count": totals[code][3],
                    "diff_count": totals[code][1] - totals[code][3]}
                   for code in shapes.APP_KARATS],
    }


def state(request, stocktake) -> dict:
    lines = _lines(stocktake)
    return {
        "summary": summary(stocktake),
        "remaining_count": lines.filter(result=StocktakeResult.PENDING).count(),
        "counted_count": lines.filter(result=StocktakeResult.COUNTED).count(),
        "user_count": StocktakeLine.objects.filter(
            stocktake=stocktake, result=StocktakeResult.COUNTED,
            counted_by=request.user).count(),
        "show_cost": _show_cost(request),
    }


class LookupsView(LegacyView):
    required_permissions = {"GET": COUNT}

    def get(self, request):
        categories = ItemCategory.objects.filter(is_active=True).select_related(
            "parent__parent").order_by("code")
        tops = [c for c in categories if c.parent_id is None]
        groups = [c for c in categories if c.tracking == Tracking.SERIALIZED]
        codes = sorted({shapes.karat_code(k) for k in Karat.objects.select_related("metal")
                        .filter(is_active=True)} & set(shapes.APP_KARATS))
        return Response({
            "page_title": _("Stocktake"),
            "branches": [{"br": b.code, "brna": b.name}
                         for b in scoped_branches(request.actor, COUNT)],
            "main_groups": [{"ssco": c.pk, "ssdis": c.name} for c in tops],
            "groups": [{"gco": c.pk, "gdis": c.name, "ssco": shapes.top_group(c).pk}
                       for c in groups],
            "karats": [{"co": code} for code in codes],
            "show_cost": _show_cost(request),
        })


class StateView(LegacyView):
    required_permissions = {"GET": COUNT}

    def get(self, request):
        stocktake, error = _stocktake(request, request.query_params)
        if error is not None:
            return error
        show_cost = _show_cost(request)
        size = min(max(_int(request.query_params.get("page_size"), 10), 1), 100)
        number = max(_int(request.query_params.get("page"), 1), 1)
        lines = _lines(stocktake)
        pages = Paginator(lines.filter(result=StocktakeResult.PENDING).order_by("barcode"), size)
        try:
            page = pages.page(number)
        except EmptyPage:
            page = None
        counted = lines.filter(result=StocktakeResult.COUNTED).order_by("-counted_at", "-id")
        return Response({
            **state(request, stocktake),
            "remaining_items": [shapes.stocktake_piece(line, show_cost=show_cost)
                                for line in (page or [])],
            "counted_items": [shapes.stocktake_piece(line, show_cost=show_cost)
                              for line in counted],
            "page": {"number": number, "num_pages": pages.num_pages, "count": pages.count,
                     "page_size": size, "has_next": bool(page and page.has_next()),
                     "has_previous": bool(page and page.has_previous())},
        })


def _turned_down(item, stocktake) -> tuple[str, str]:
    """Why a piece the count does not expect is turned down (Gweb's reasons)."""
    if item is None:
        return "not_found", _("No piece with this code.")
    if item.status == ItemStatus.SOLD:
        return "sold", _("This piece was sold.")
    if item.status == ItemStatus.RETURNED_TO_SUPPLIER:
        return "returned_to_factory", _("This piece was returned to the supplier.")
    if item.status == ItemStatus.IN_TRANSIT:
        return "not_received", _("This piece is on its way and was not received yet.")
    if item.branch_id != stocktake.branch_id:
        return "other_branch", _("This piece belongs to another branch.")
    return "not_in_stocktake", _("This piece is not part of this stocktake.")


class ScanView(LegacyView):
    required_permissions = {"POST": COUNT}

    def post(self, request):
        stocktake, error = _stocktake(request, request.data)
        if error is not None:
            return error
        code = str(request.data.get("mco") or "").strip()
        if not code:
            return Response({"mco": [_("This field is required.")]},
                            status=status.HTTP_400_BAD_REQUEST)
        if stocktake.status != DocStatus.DRAFT:
            return detail(_("This stocktake is closed."), status.HTTP_400_BAD_REQUEST)
        item = Item.objects.select_related("branch").filter(barcode=code).first() \
            or Item.objects.select_related("branch").filter(rfid_epc=code).first()
        line = (StocktakeLine.objects.filter(stocktake=stocktake, expected=True,
                                             barcode=item.barcode).first()
                if item is not None else None)
        show_cost = _show_cost(request)
        if line is not None and line.result == StocktakeResult.COUNTED:
            return Response({"success": False, "code": "already_counted",
                             "detail": _("This piece was already counted."), "branch_name": "",
                             **state(request, stocktake)})
        if line is None:
            reason, words = _turned_down(item, stocktake)
            return Response({"success": False, "code": reason, "detail": words,
                             "branch_name": item.branch.name if reason == "other_branch" else "",
                             **state(request, stocktake)})
        result = stocktakes.scan(stocktake.pk, item.barcode, actor=request.actor)
        line = _lines(stocktake).get(pk=result.line.pk)
        return Response({"success": True, "code": "counted", "detail": _("Counted."),
                         "item": shapes.stocktake_piece(line, show_cost=show_cost),
                         **state(request, stocktake)})


def _choice(model_rows, value, field, message):
    if value in (None, "", 0, "0"):
        return None, None
    number = _int(value)
    found = model_rows.filter(pk=number).first() if number is not None else None
    if found is None:
        return None, Response({field: message}, status=status.HTTP_400_BAD_REQUEST)
    return found, None


class StartView(LegacyView):
    required_permissions = {"POST": START}

    def post(self, request):
        data = request.data
        branch, error = _branch(request, data.get("brna"), START)
        if error is not None:
            return error
        if data.get("confirm") is not True:
            return Response({"confirm": [_("Confirm to start the stocktake.")]},
                            status=status.HTTP_400_BAD_REQUEST)
        karat = None
        if data.get("karat") not in (None, "", 0):
            code = _int(data.get("karat"))
            karat = shapes.karats_for(code).filter(is_active=True).first() if code else None
            if karat is None:
                return Response({"karat": _("Unknown karat.")}, status=status.HTTP_400_BAD_REQUEST)
        categories = ItemCategory.objects.filter(is_active=True)
        group, error = _choice(categories, data.get("group"), "group", _("Unknown group."))
        if error is not None:
            return error
        main, error = _choice(categories.filter(parent__isnull=True), data.get("main_group"),
                              "main_group", _("Unknown main group."))
        if error is not None:
            return error
        try:
            stocktake = stocktakes.start_stocktake(
                branch.pk, category_id=(group or main).pk if (group or main) else None,
                karat_id=karat.pk if karat else None, actor=request.actor)
        except DomainError as exc:
            if exc.code != "STOCKTAKE_ALREADY_OPEN":
                raise
            open_ = Stocktake.objects.filter(branch=branch, status=DocStatus.DRAFT).first()
            return Response({
                "detail": _("A stocktake is already open at this branch."),
                "code": "stocktake_in_progress", "xo1": open_.pk if open_ else None,
                "remaining": open_.lines.filter(result=StocktakeResult.PENDING).count()
                if open_ else 0}, status=status.HTTP_409_CONFLICT)
        stocktake = Stocktake.objects.select_related("branch", "category", "karat__metal").get(
            pk=stocktake.pk)
        return Response(state(request, stocktake), status=status.HTTP_201_CREATED)


class ReportView(LegacyView):
    """The count at its start (what was expected) or now (counted and not), as PDF or Excel."""

    required_permissions = {"GET": COUNT}

    def get(self, request):
        stocktake, error = _stocktake(request, request.query_params)
        if error is not None:
            return error
        stage = request.query_params.get("stage", "start")
        fmt = request.query_params.get("fmt", "pdf")
        if stage not in ("start", "end") or fmt not in ("pdf", "xlsx"):
            return detail(_("Choose the stage (start or end) and the format (pdf or xlsx)."),
                          status.HTTP_400_BAD_REQUEST)
        lines = list(_lines(stocktake).order_by("barcode"))
        if stage == "end":
            lines.sort(key=lambda line: (line.result == StocktakeResult.COUNTED, line.barcode))
        rows = [{
            "code": line.barcode, "group": line.item.category.name if line.item else "",
            "karat": str(line.item.karat) if line.item and line.item.karat else "",
            "weight": shapes.grams(line.item.gross_weight_g if line.item else 0),
            "state": line.get_result_display()} for line in lines]
        title = (_("Stocktake %(number)s at the start") if stage == "start"
                 else _("Stocktake %(number)s, counted and not counted")) % {
            "number": stocktake.number}
        name = f"{title}.{fmt}"
        if fmt == "xlsx":
            content, kind = _xlsx(rows, stage, title), (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        else:
            from apps.printing.pdf import PdfUnavailable, sheet_pdf

            try:
                content = sheet_pdf("mobile/stocktake_report.html", {
                    "rows": rows, "stage": stage, "stocktake": stocktake,
                    "summary": summary(stocktake)}, title=title)
            except PdfUnavailable:
                return detail(_("PDF files cannot be made on this server right now."),
                              status.HTTP_503_SERVICE_UNAVAILABLE)
            kind = "application/pdf"
        response = HttpResponse(content, content_type=kind)
        response["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(name)}"
        return response


def _xlsx(rows, stage, title) -> bytes:
    from io import BytesIO

    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "Stocktake"
    sheet.sheet_view.rightToLeft = True
    sheet.append([title])
    headings = [_("Code"), _("Group"), _("Karat"), _("Weight (g)")]
    sheet.append(headings + ([_("Counted")] if stage == "end" else []))
    for row in rows:
        values = [row["code"], row["group"], row["karat"], float(row["weight"])]
        sheet.append(values + ([row["state"]] if stage == "end" else []))
    out = BytesIO()
    book.save(out)
    return out.getvalue()
