"""Diamond reports (part of the Diamonds feature): stock by category and stone kind, and sales
with the margin on the stones."""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.core.models import DocStatus
from apps.inventory.models import ON_HAND, Item
from apps.reports.registry import BRANCH_FILTER, DATE_FILTERS, Column, Report, Table, register
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
    title = gettext_lazy("Diamond stock")
    description = gettext_lazy("Diamond pieces and loose stones on hand: carats, what the "
                               "stones cost and the label value, by category and by stone.")
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
        return [by_category, by_stone]


@register
class DiamondSales(_DiamondReport):
    code = "diamond_sales"
    title = gettext_lazy("Diamond sales")
    description = gettext_lazy("Diamond pieces and loose stones sold in a period: the gold "
                               "part, the stones part and the margin on the stones.")
    filters = (*DATE_FILTERS, BRANCH_FILTER)

    def run(self, params, scope):
        start, end = params.date("date_from"), params.date("date_to")
        lines = _in_scope(SalesInvoiceLine.objects.filter(
            invoice__status=DocStatus.POSTED, invoice__business_date__range=(start, end),
            category__product_family__in=FAMILIES), scope, "invoice__branch_id")
        table = Table(_("Diamond sales"), [
            Column("category", _("Category")), Column("pieces", _("Pieces"), "int"),
            Column("carats", _("Carats"), "weight"), Column("total", _("Sales total"), "money"),
            Column("discount", _("Discount"), "money"), Column("gold", _("Gold part"), "money"),
            Column("stones", _("Stones part"), "money"),
            Column("stone_cost", _("Stones' cost"), "money"),
            Column("margin", _("Margin on stones"), "money"),
            Column("margin_pct", _("Margin %"), "pct")],
            note=_("Margin on stones = the stones part of the price − what the stones and the "
                   "making cost. Returns are not deducted."))
        for row in (lines.values("category__name").annotate(
                pieces=Count("id"), carats=Sum("item__stone_weight_ct"),
                total=Sum("line_total"), discount=Sum("discount_amount"),
                gold=Sum("metal_amount"), stones=Sum("stones_amount"),
                stone_cost=Sum("stone_cost_amount"), making_cost=Sum("cost_amount"))
                .order_by("category__name")):
            margin = row["stones"] - row["stone_cost"] - row.pop("making_cost")
            table.rows.append({**row, "category": row.pop("category__name"), "margin": margin,
                               "margin_pct": (margin * 100 / row["stones"]) if row["stones"]
                               else None})
        table.add_totals("category", _("Total"))
        if table.totals and table.totals.get("stones"):
            table.totals["margin_pct"] = table.totals["margin"] * 100 / table.totals["stones"]
        return [table]
