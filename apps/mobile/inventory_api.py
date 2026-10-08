"""/Sup/api/inventory/*: the pieces in stock (Gweb's Sup/api/inventory.py, for the app)."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.core.paginator import EmptyPage, Paginator
from django.db.models import Count, Q, Sum
from django.utils.translation import gettext as _
from rest_framework import status
from rest_framework.response import Response

from apps.catalog.models import MetalCode
from apps.inventory.models import ON_HAND, Item
from apps.org.models import Branch
from apps.pricing.selectors import functional_currency

from . import shapes
from .legacy import LegacyView, detail

VIEW = "inventory.stock.view"


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def scoped_branches(actor, permission: str):
    branches = Branch.objects.filter(is_active=True).order_by("code")
    allowed = actor.branch_ids(permission)
    return branches if allowed is None else branches.filter(pk__in=allowed)


def _pieces(request):
    """In stock here, in the branches this member may see (one, with ?branch=<code>)."""
    branches = scoped_branches(request.actor, VIEW)
    code = _int(request.query_params.get("branch"))
    if code is not None:
        branches = branches.filter(code=code)
    return (Item.objects.filter(status__in=ON_HAND, branch__in=branches)
            .select_related("category__parent__parent", "karat__metal", "branch"))


class InventoryView(LegacyView):
    required_permissions = {"GET": VIEW}

    def get(self, request):
        items = _pieces(request)
        term = (request.query_params.get("search") or "").strip()
        if term:
            match = Q(external_code__icontains=term) | Q(category__name__icontains=term) \
                | Q(note__icontains=term)
            if term.isdigit():
                match |= Q(barcode=term)
            items = items.filter(match)
        karat = _int(request.query_params.get("karat"))
        if karat is not None:
            items = items.filter(karat__in=shapes.karats_for(karat))
        size = min(max(_int(request.query_params.get("page_size"), 25), 1), 100)
        pages = Paginator(items.order_by("barcode"), size)
        number = max(_int(request.query_params.get("page"), 1), 1)
        try:
            page = pages.page(number)
        except EmptyPage:
            page = None
        show_cost = request.actor.can("inventory.item.view_cost")
        rows = [shapes.piece(item, show_cost=show_cost) for item in (page or [])]
        return Response({"results": rows, "count": pages.count, "page": number,
                         "num_pages": pages.num_pages, "page_size": size,
                         "has_next": bool(page and page.has_next()),
                         "has_previous": bool(page and page.has_previous())})


class SummaryView(LegacyView):
    required_permissions = {"GET": VIEW}

    def get(self, request):
        show_cost = request.actor.can("inventory.item.view_cost")
        totals = defaultdict(lambda: [Decimal(0), 0, Decimal(0)])
        grouped = (_pieces(request).order_by().values("karat__metal__code", "karat__code")
                   .annotate(weight=Sum("gross_weight_g"), count=Count("id"),
                             cost=Sum("cost_amount")))
        for group in grouped:
            metal, code = group["karat__metal__code"], group["karat__code"]
            key = 0 if code is None else 1 if metal == MetalCode.SILVER else int(code)
            row = totals[key]
            row[0] += group["weight"] or 0
            row[1] += group["count"]
            row[2] += group["cost"] or 0
        weight = sum((row[0] for row in totals.values()), Decimal(0))
        value = sum((row[2] for row in totals.values()), Decimal(0))
        return Response({
            "total_weight": shapes.grams(weight),
            "total_pieces": sum(row[1] for row in totals.values()),
            "by_karat": [{"karat": code, "weight": shapes.grams(totals[code][0]),
                          "pieces": totals[code][1],
                          "value": shapes.money(totals[code][2]) if show_cost else None}
                         for code in shapes.APP_KARATS],
            "currency": functional_currency(),
            "total_value": shapes.money(value) if show_cost else None,
        })


class BranchesView(LegacyView):
    required_permissions = {"GET": VIEW}

    def get(self, request):
        return Response({"branches": [{"br": b.code, "brna": b.name}
                                      for b in scoped_branches(request.actor, VIEW)]})


class PieceView(LegacyView):
    required_permissions = {"GET": VIEW}

    def get(self, request, code):
        item = _pieces(request).filter(barcode=str(code)).first()
        if item is None:
            return detail(_("No piece with this code."), status.HTTP_404_NOT_FOUND)
        return Response(shapes.piece(item, show_cost=request.actor.can(
            "inventory.item.view_cost")))
