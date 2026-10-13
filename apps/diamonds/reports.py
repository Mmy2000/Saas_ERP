"""Diamond reports (part of the Diamonds feature): stock by category and stone kind, and sales
with the margin on the stones."""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.core.models import DocStatus
from apps.inventory.models import ON_HAND, Item
from apps.reports.registry import (
    BRANCH_FILTER,
    DATE_FILTERS,
    Column,
    Filter,
    Report,
    Table,
    register,
)
from apps.sales.models import SalesInvoiceLine

from .models import ItemStone, StoneKind
from .services import FAMILIES

ZERO = Decimal(0)


def _in_scope(queryset, scope, field="branch_id"):
    return queryset if scope is None else queryset.filter(**{f"{field}__in": scope})


class _DiamondReport(Report):
    """Its permission belongs to the Diamonds feature (diamonds.report_<code>.view)."""

    @classmethod
    def permission(cls) -> str:
        return f"diamonds.report_{cls.code.removeprefix('diamond_')}.view"


@register
class DiamondStock(_DiamondReport):
    code = "diamond_stock"
    section = "gold"
    title = gettext_lazy("Diamond stock")
    description = gettext_lazy("Diamond pieces and loose stones on hand: carats, what the "
                               "stones cost and the label value, by category, by branch and by "
                               "stone.")
    filters = (BRANCH_FILTER,)

    def run(self, params, scope):
        items = _in_scope(Item.objects.filter(status__in=ON_HAND,
                                              category__product_family__in=FAMILIES), scope)
        by_category = Table(_("By category"), [
            Column("category", _("Category")), Column("pieces", _("Pieces"), "int"),
            Column("gold", _("Gold (g)"), "weight"), Column("carats", _("Carats"), "weight"),
            Column("cost", _("Stones' cost"), "money"),
            Column("label", _("Label value"), "money")])
        for row in (items.values("category__name").annotate(
                pieces=Count("id"), gold=Sum("metal_weight_g"), carats=Sum("stone_weight_ct"),
                cost=Sum("stone_cost_amount"), label=Sum("label_price"))
                .order_by("category__name")):
            by_category.rows.append({**row, "category": row.pop("category__name")})
        by_category.add_totals("category", _("Total"))

        # Branches side by side (legacy GHD33 / GHZ25): diamond pieces and loose stones apart.
        by_branch = Table(_("By branch"), [
            Column("branch", _("Branch")), Column("pieces", _("Diamond pieces"), "int"),
            Column("loose", _("Loose stones"), "int"), Column("carats", _("Carats"), "weight"),
            Column("cost", _("Stones' cost"), "money"),
            Column("label", _("Label value"), "money")])
        for row in (items.values("branch__name").annotate(
                pieces=Count("id", filter=Q(category__product_family="diamond")),
                loose=Count("id", filter=Q(category__product_family="stone")),
                carats=Sum("stone_weight_ct"), cost=Sum("stone_cost_amount"),
                label=Sum("label_price")).order_by("branch__name")):
            by_branch.rows.append({**row, "branch": row.pop("branch__name")})
        by_branch.add_totals("branch", _("Total"))

        by_stone = Table(_("By stone"), [
            Column("kind", _("Stone")), Column("stones", _("Stones"), "int"),
            Column("carats", _("Carats"), "weight"),
            Column("certified", _("With a certificate"), "int")])
        kinds = dict(StoneKind.choices)
        stones = ItemStone.objects.filter(item__in=items)
        certified = Count("id", filter=~Q(certificate_no=""))
        for row in (stones.values("kind").annotate(stones=Sum("count"), carats=Sum("carat"),
                                                   certified=certified).order_by("kind")):
            by_stone.rows.append({**row, "kind": str(kinds.get(row["kind"], row["kind"]))})
        by_stone.add_totals("kind", _("Total"))
        return [by_category, by_branch, by_stone]


@register
class DiamondSales(_DiamondReport):
    code = "diamond_sales"
    section = "gold"
    title = gettext_lazy("Diamond sales")
    description = gettext_lazy("Diamond pieces and loose stones sold in a period: the gold "
                               "part, the stones part and the margin on the stones.")
    filters = (*DATE_FILTERS, BRANCH_FILTER)

    def run(self, params, scope):
        from apps.sales.models import SalesReturnLine

        start, end = params.date("date_from"), params.date("date_to")
        lines = _in_scope(SalesInvoiceLine.objects.filter(
            invoice__status=DocStatus.POSTED, invoice__business_date__range=(start, end),
            category__product_family__in=FAMILIES), scope, "invoice__branch_id")
        back = _in_scope(SalesReturnLine.objects.filter(
            sales_return__status=DocStatus.POSTED,
            sales_return__business_date__range=(start, end),
            original_line__category__product_family__in=FAMILIES), scope,
            "sales_return__branch_id")
        table = Table(_("Diamond sales"), [
            Column("category", _("Category")), Column("pieces", _("Pieces"), "int"),
            Column("carats", _("Carats"), "weight"), Column("total", _("Net sales"), "money"),
            Column("returned", _("Returned"), "money"),
            Column("discount", _("Discount"), "money"), Column("gold", _("Gold part"), "money"),
            Column("stones", _("Stones part"), "money"),
            Column("stone_cost", _("Stones' cost"), "money"),
            Column("margin", _("Margin on stones"), "money"),
            Column("margin_pct", _("Margin %"), "pct")],
            note=_("Net of returns: what came back in the period is taken off at the figures "
                   "of the original sale (Returned shows its price). Margin on stones = the "
                   "stones part of the price − what the stones and the making cost."))
        keys = ("pieces", "carats", "total", "discount", "gold", "stones", "stone_cost",
                "making_cost")
        cells = {}

        def add(rows, sign, prefix=""):
            for row in rows:
                name = row.pop(f"{prefix}category__name")
                cell = cells.setdefault(name, {key: ZERO for key in (*keys, "returned")})
                for key in keys:
                    cell[key] += sign * (row[key] or 0)
                if sign < 0:
                    cell["returned"] += row["total"] or 0

        add(lines.values("category__name").annotate(
            pieces=Count("id"), carats=Sum("item__stone_weight_ct"), total=Sum("line_total"),
            discount=Sum("discount_amount"), gold=Sum("metal_amount"),
            stones=Sum("stones_amount"), stone_cost=Sum("stone_cost_amount"),
            making_cost=Sum("cost_amount")), 1)
        add(back.values("original_line__category__name").annotate(
            pieces=Count("id"), carats=Sum("original_line__item__stone_weight_ct"),
            total=Sum("original_line__line_total"),
            discount=Sum("original_line__discount_amount"),
            gold=Sum("original_line__metal_amount"), stones=Sum("original_line__stones_amount"),
            stone_cost=Sum("original_line__stone_cost_amount"),
            making_cost=Sum("original_line__cost_amount")), -1, "original_line__")
        for name, cell in sorted(cells.items()):
            margin = cell["stones"] - cell["stone_cost"] - cell.pop("making_cost")
            table.rows.append({**cell, "category": name, "margin": margin,
                               "margin_pct": (margin * 100 / cell["stones"]) if cell["stones"]
                               else None})
        table.add_totals("category", _("Total"))
        if table.totals and table.totals.get("stones"):
            table.totals["margin_pct"] = table.totals["margin"] * 100 / table.totals["stones"]
        return [table]


DEAL_KINDS = (("", gettext_lazy("Everything")), ("buy", gettext_lazy("Bought")),
              ("sell", gettext_lazy("Sold")), ("return", gettext_lazy("Returned to the supplier")))


@register
class DiamondParcels(_DiamondReport):
    code = "diamond_parcels"
    section = "gold"
    title = gettext_lazy("Parcel stones bought and sold")
    description = gettext_lazy("Small stones bought into parcels, sold from them and returned to "
                               "suppliers in a period: carats, amounts, the cost of what was sold "
                               "and the margin, per parcel and deal by deal.")
    filters = (*DATE_FILTERS, BRANCH_FILTER,
               Filter("kind", "choice", gettext_lazy("Deals"), DEAL_KINDS))

    def run(self, params, scope):
        from .models import ParcelDeal

        start, end = params.date("date_from"), params.date("date_to")
        deals = _in_scope(ParcelDeal.objects.filter(
            status=DocStatus.POSTED, business_date__range=(start, end))
            .select_related("parcel", "party"), scope)
        if params.get("kind") in ("buy", "sell", "return"):
            deals = deals.filter(kind=params.get("kind"))
        per_parcel = Table(_("Per parcel"), [
            Column("parcel", _("Parcel")), Column("bought_ct", _("Bought (ct)"), "weight"),
            Column("bought", _("Bought"), "money"), Column("sold_ct", _("Sold (ct)"), "weight"),
            Column("sold", _("Sold"), "money"), Column("cost", _("Cost of what was sold"),
                                                       "money"),
            Column("margin", _("Margin"), "money"),
            Column("returned_ct", _("Returned (ct)"), "weight"),
            Column("returned", _("Returned"), "money")])
        listed = Table(_("Deals"), [
            Column("number", _("Number"), "code"), Column("date", _("Date"), "date"),
            Column("kind", _("Deal")), Column("parcel", _("Parcel")), Column("party", _("With")),
            Column("carats", _("Carats"), "weight"), Column("price", _("Price / ct"), "money"),
            Column("amount", _("Amount"), "money")])
        cells: dict[int, dict] = {}
        labels = dict(DEAL_KINDS)
        for deal in deals.order_by("business_date", "id"):
            cell = cells.setdefault(deal.parcel_id, {"parcel": deal.parcel.code, **{
                key: ZERO for key in ("bought_ct", "bought", "sold_ct", "sold", "cost",
                                      "returned_ct", "returned")},
                "_urls": {"parcel": f"/diamonds/parcels/{deal.parcel_id}/"}})
            prefix = {"buy": "bought", "sell": "sold", "return": "returned"}[deal.kind]
            cell[f"{prefix}_ct"] += deal.carats
            cell[prefix] += deal.amount
            if deal.kind == "sell":
                cell["cost"] += deal.cost_amount
            listed.rows.append({
                "number": deal.number, "date": deal.business_date,
                "kind": str(labels.get(deal.kind, deal.kind)), "parcel": deal.parcel.code,
                "party": deal.party.name if deal.party_id else deal.party_name,
                "carats": deal.carats, "price": deal.price_per_ct, "amount": deal.amount,
                "_urls": {"number": f"/diamonds/parcels/{deal.parcel_id}/#deals"}})
        for cell in sorted(cells.values(), key=lambda c: c["parcel"]):
            cell["margin"] = cell["sold"] - cell["cost"]
            per_parcel.rows.append(cell)
        per_parcel.add_totals("parcel", _("Total"))
        return [per_parcel, listed]


@register
class DiamondSettings(_DiamondReport):
    code = "diamond_settings"
    section = "gold"
    title = gettext_lazy("Stones set into pieces")
    # Legacy GHPR22 (الماس المصنع من ذهب).
    description = gettext_lazy("Diamond pieces made by setting stones into gold in a period: "
                               "the piece, the setter, the gold before and after, the carats "
                               "and what the stones and the labour cost.")
    filters = (*DATE_FILTERS, BRANCH_FILTER)

    def run(self, params, scope):
        from .models import StoneSetting

        start, end = params.date("date_from"), params.date("date_to")
        settings = _in_scope(StoneSetting.objects.filter(
            status=DocStatus.POSTED, business_date__range=(start, end))
            .select_related("piece__category", "piece__karat__metal", "setter"), scope)
        table = Table(_("Settings"), [
            Column("number", _("Number"), "code"), Column("date", _("Date"), "date"),
            Column("piece", _("Piece"), "code"), Column("category", _("Category")),
            Column("karat", _("Karat")), Column("setter", _("Set by")),
            Column("before", _("Gold before (g)"), "weight"),
            Column("after", _("Weight after (g)"), "weight"),
            Column("carats", _("Carats"), "weight"),
            Column("stones", _("Stones' cost"), "money"), Column("labour", _("Labour"), "money"),
            Column("cost", _("Added to the piece"), "money")])
        for setting in settings.order_by("business_date", "id"):
            piece = setting.piece
            table.rows.append({
                "number": setting.number, "date": setting.business_date,
                "piece": piece.barcode, "category": piece.category.name,
                "karat": piece.karat.label if piece.karat_id else "—",
                "setter": setting.setter.name if setting.setter_id else _("In-house"),
                "before": setting.gross_before_g, "after": setting.gross_after_g,
                "carats": setting.stones_carat, "stones": setting.stones_cost,
                "labour": setting.labour_amount,
                "cost": setting.stones_cost + setting.labour_amount,
                "_urls": {"number": f"/diamonds/settings/{setting.pk}/",
                          "piece": f"/stock/items/{piece.pk}/"}})
        return [table.add_totals("number", _("Total"))]


def _diamond_categories():
    from apps.catalog.models import ItemCategory

    return [("", gettext_lazy("All categories"))] + [
        (str(c.pk), f"{c.code} · {c.name}") for c in ItemCategory.objects.filter(
            is_active=True, product_family__in=FAMILIES).order_by("code")]


@register
class DiamondCard(_DiamondReport):
    """Legacy GHD51 كرت الصنف ماس: the stock card of diamond pieces and loose stones, counted in
    pieces and carats with their cost, from the stock movements."""

    code = "diamond_card"
    section = "gold"
    title = gettext_lazy("Diamond category card")
    description = gettext_lazy("Opening, what came in and went out by kind, and closing per "
                               "category in pieces, carats and cost, with every movement and "
                               "a running balance of carats.")
    filters = (*DATE_FILTERS, BRANCH_FILTER,
               Filter("category", "choice", gettext_lazy("Category"), _diamond_categories, ""))
    LIMIT = 2000

    def run(self, params, scope):
        from collections import defaultdict

        from apps.core.documents import document_titles, document_url
        from apps.inventory.models import MovementType, StockMovement

        start, end = params.date("date_from"), params.date("date_to")
        moves = _in_scope(StockMovement.objects.filter(
            item__category__product_family__in=FAMILIES), scope)
        if params.get("category"):
            moves = moves.filter(item__category_id=int(params.get("category")))
        moves = moves.select_related("item__category")

        def sign(move):
            return 1 if move.qty > 0 else -1 if move.qty < 0 else 0

        def value(move):
            return move.item.cost_amount + move.item.stone_cost_amount

        cells = defaultdict(lambda: defaultdict(lambda: ZERO))
        for move in moves.filter(business_date__lt=start):
            cell, s = cells[move.item.category], sign(move)
            cell["open_n"] += s
            cell["open_ct"] += s * move.item.stone_weight_ct
            cell["open_cost"] += s * value(move)
        period = list(moves.filter(business_date__range=(start, end)).order_by(
            "business_date", "id"))
        for move in period:
            cell, s = cells[move.item.category], sign(move)
            side = "in" if s > 0 else "out"
            cell[f"{side}_n"] += abs(s)
            cell[f"{side}_ct"] += move.item.stone_weight_ct if s else ZERO
            cell[f"{side}_cost"] += value(move) if s else ZERO

        summary = Table(_("Opening and closing"), [
            Column("category", _("Category")),
            Column("open_n", _("Opening pieces"), "int"),
            Column("open_ct", _("Opening carats"), "weight"),
            Column("in_n", _("Pieces in"), "int"), Column("in_ct", _("Carats in"), "weight"),
            Column("out_n", _("Pieces out"), "int"), Column("out_ct", _("Carats out"), "weight"),
            Column("close_n", _("Closing pieces"), "int"),
            Column("close_ct", _("Closing carats"), "weight"),
            Column("close_cost", _("Closing cost"), "money")])
        for category, cell in sorted(cells.items(), key=lambda pair: pair[0].code):
            close_n = cell["open_n"] + cell["in_n"] - cell["out_n"]
            if not any((cell["open_n"], cell["in_n"], cell["out_n"])):
                continue
            summary.rows.append({
                "category": category.name, "open_n": int(cell["open_n"]),
                "open_ct": cell["open_ct"], "in_n": int(cell["in_n"]), "in_ct": cell["in_ct"],
                "out_n": int(cell["out_n"]), "out_ct": cell["out_ct"], "close_n": int(close_n),
                "close_ct": cell["open_ct"] + cell["in_ct"] - cell["out_ct"],
                "close_cost": cell["open_cost"] + cell["in_cost"] - cell["out_cost"]})
        summary.add_totals("category", _("Total"))

        labels = dict(MovementType.choices)
        by_kind = defaultdict(lambda: {"count": 0, "n": 0, "ct": ZERO, "cost": ZERO})
        for move in period:
            row, s = by_kind[move.movement_type], sign(move)
            row["count"] += 1
            row["n"] += s
            row["ct"] += s * move.item.stone_weight_ct
            row["cost"] += s * value(move)
        kinds = Table(_("By kind of movement"), [
            Column("kind", _("Movement")), Column("count", _("Movements"), "int"),
            Column("n", _("Pieces"), "int"), Column("ct", _("Carats"), "weight"),
            Column("cost", _("Cost"), "money")], note=_("Positive: in. Negative: out."))
        for kind, row in sorted(by_kind.items()):
            kinds.rows.append({"kind": str(labels.get(kind, kind)), **row})
        kinds.add_totals("kind", _("Total"))
        kinds.totals.pop("count", None)

        lines = Table(_("Movements"), [
            Column("date", _("Date"), "date"), Column("kind", _("Movement")),
            Column("document", _("Document"), "code"), Column("piece", _("Piece"), "code"),
            Column("category", _("Category")), Column("n", _("Pieces"), "int"),
            Column("ct", _("Carats"), "weight"), Column("cost", _("Cost"), "money"),
            Column("balance", _("Balance (ct)"), "weight")])
        opening = sum((cell["open_ct"] for cell in cells.values()), ZERO)
        balance = opening
        listed = period[:self.LIMIT]
        titles = document_titles((m.document_type, m.document_id) for m in listed)
        for move in listed:
            s = sign(move)
            balance += s * move.item.stone_weight_ct
            label, number = titles.get((move.document_type, move.document_id), ("", ""))
            url = document_url(move.document_type, move.document_id)
            lines.rows.append({
                "date": move.business_date, "kind": str(labels.get(move.movement_type, "")),
                "document": f"{label} {number}".strip() or "—", "piece": move.item.barcode,
                "category": move.item.category.name, "n": s,
                "ct": s * move.item.stone_weight_ct, "cost": s * value(move),
                "balance": balance,
                "_urls": {"piece": f"/stock/items/{move.item_id}/",
                          **({"document": url} if url else {})}})
        lines.note = (_("Opening balance %(ct)s ct.") % {"ct": f"{opening:,.3f}"}
                      + (" " + _("Only the first %(n)s movements are listed.")
                         % {"n": self.LIMIT} if len(period) > self.LIMIT else ""))
        return [summary, kinds, lines]
