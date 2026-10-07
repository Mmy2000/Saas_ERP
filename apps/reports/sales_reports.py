"""Sales reports from the legacy pack: profit per piece (GHS29, GHS33 losses, GHS87), net
sales after returns (GHS38, GHS62), salespeople ranked by net sales (GHS26, GHS27, GHS43,
GHS81) and customer analysis (GHC18, GHC26).

Profit here is the making profit, as Gweb counted it: what the piece sold for, less the gold at
the day's selling price, less what the making (and any stones) cost. Gold price movements are
not in it; the sales analysis margin and the profit and loss include them. Weights are also
shown as 21K equivalent (fine gold ÷ 0.875). Returns count on the day they came back, against
the figures of the original sale.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, Max, Q, Sum
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.core.documents import document_url
from apps.core.models import DocStatus
from apps.ledger.models import BalanceProjection
from apps.parties.models import Party
from apps.sales.models import SalesInvoiceLine, SalesReturnLine

from .registry import BRANCH_FILTER, DATE_FILTERS, Column, Filter, Report, Table, register

ZERO = Decimal(0)
REFERENCE_FINENESS = Decimal(875)


def eq21(fine) -> Decimal:
    """Fine grams as grams of 21K."""
    return ((fine or ZERO) * 1000 / REFERENCE_FINENESS).quantize(Decimal("0.001"))


def _profit(line) -> Decimal:
    return line.line_total - line.metal_amount - line.cost_amount - line.stone_cost_amount


def _sold(start, end, scope):
    lines = (SalesInvoiceLine.objects.filter(invoice__status=DocStatus.POSTED,
                                             invoice__business_date__range=(start, end))
             .select_related("invoice__sold_by", "invoice__branch", "item", "category",
                             "karat__metal"))
    return lines if scope is None else lines.filter(invoice__branch_id__in=scope)


def _returned(start, end, scope):
    lines = (SalesReturnLine.objects.filter(sales_return__status=DocStatus.POSTED,
                                            sales_return__business_date__range=(start, end))
             .select_related("sales_return__branch", "original_line__invoice__sold_by",
                             "original_line__item", "original_line__category",
                             "original_line__karat__metal"))
    return lines if scope is None else lines.filter(sales_return__branch_id__in=scope)


def _piece(line) -> str:
    return line.item.barcode if line.item_id else line.category.name


def _is_gold(line) -> bool:
    return line.karat_id is not None and line.karat.metal.code == "gold"


# --- profit per piece -------------------------------------------------------------------------

@register
class SalesProfit(Report):
    code = "sales_profit"
    section = "sales"
    title = gettext_lazy("Profit per piece")
    description = gettext_lazy("Every piece sold with its price per gram, the gold at the day's "
                               "price, the making charged and what it cost, and the profit. "
                               "Show only the pieces sold at a loss.")
    filters = (*DATE_FILTERS, BRANCH_FILTER,
               Filter("show", "choice", gettext_lazy("Show"),
                      (("all", gettext_lazy("All pieces")),
                       ("losses", gettext_lazy("Sold at a loss"))), "all"))

    COLUMNS = (("date", gettext_lazy("Date"), "date"),
               ("document", gettext_lazy("Invoice"), "text"),
               ("piece", gettext_lazy("Piece"), "text"), ("karat", gettext_lazy("Karat"), "text"),
               ("weight", gettext_lazy("Weight (g)"), "weight"),
               ("price_g", gettext_lazy("Price / g"), "money"),
               ("gold", gettext_lazy("Gold at the day's price"), "money"),
               ("making", gettext_lazy("Making charged"), "money"),
               ("cost", gettext_lazy("Making cost"), "money"),
               ("profit", gettext_lazy("Profit"), "money"),
               ("profit_g", gettext_lazy("Profit / g"), "money"),
               ("seller", gettext_lazy("Salesperson"), "text"))

    def _row(self, line, *, date, number, url, sign=1):
        weight = line.gross_weight_g
        profit = _profit(line)
        return {"date": date, "document": number, "piece": _piece(line),
                "karat": line.karat.label if line.karat_id else "—",
                "weight": sign * weight, "price_g": line.line_total / weight if weight else None,
                "gold": sign * line.metal_amount,
                "making": sign * (line.line_total - line.metal_amount),
                "cost": sign * (line.cost_amount + line.stone_cost_amount),
                "profit": sign * profit, "profit_g": profit / weight if weight else None,
                "seller": (line.invoice.sold_by.display_name if line.invoice.sold_by_id
                           else "—"),
                "_urls": {"document": url} if url else {}}

    def run(self, params, scope):
        start, end = params.date("date_from"), params.date("date_to")
        losses = params.get("show") == "losses"
        columns = [Column(key, str(label), kind) for key, label, kind in self.COLUMNS]
        sold = Table(_("Pieces sold"), columns)
        by_karat = defaultdict(lambda: {"pieces": 0, "weight": ZERO, "fine": ZERO,
                                        "sales": ZERO, "profit": ZERO})
        for line in _sold(start, end, scope).order_by("invoice__business_date", "invoice_id",
                                                       "position"):
            if losses and _profit(line) >= 0:
                continue
            invoice = line.invoice
            sold.rows.append(self._row(line, date=invoice.business_date, number=invoice.number,
                                       url=document_url("sales.SalesInvoice", invoice.pk)))
            cell = by_karat[line.karat.label if line.karat_id else "—"]
            cell["pieces"] += line.qty
            cell["weight"] += line.gross_weight_g
            cell["fine"] += line.fine_weight_g if _is_gold(line) else ZERO
            cell["sales"] += line.line_total
            cell["profit"] += _profit(line)
        sold.add_totals("date", _("Total"))
        for key in ("price_g", "profit_g"):
            sold.totals.pop(key, None)

        back = Table(_("Returned in the period"), columns,
                     note=_("Taken off at the figures of the original sale."))
        for return_line in _returned(start, end, scope).order_by("sales_return__business_date"):
            line = return_line.original_line
            if losses and _profit(line) >= 0:
                continue
            sales_return = return_line.sales_return
            back.rows.append(self._row(line, date=sales_return.business_date,
                                       number=sales_return.number, sign=-1,
                                       url=document_url("sales.SalesReturn", sales_return.pk)))
            cell = by_karat[line.karat.label if line.karat_id else "—"]
            cell["pieces"] -= line.qty
            cell["weight"] -= line.gross_weight_g
            cell["fine"] -= line.fine_weight_g if _is_gold(line) else ZERO
            cell["sales"] -= line.line_total
            cell["profit"] -= _profit(line)
        back.add_totals("date", _("Total"))
        for key in ("price_g", "profit_g"):
            back.totals.pop(key, None)

        summary = Table(_("Net by karat"), [
            Column("karat", _("Karat")), Column("pieces", _("Pieces"), "int"),
            Column("weight", _("Weight (g)"), "weight"), Column("eq21", _("21K equivalent (g)"),
                                                               "weight"),
            Column("sales", _("Sales"), "money"), Column("profit", _("Profit"), "money"),
            Column("per21", _("Sales per 21K gram"), "money")],
            note=_("Profit = price − gold at the day's selling price − making and stones cost."))
        for karat, cell in sorted(by_karat.items()):
            equivalent = eq21(cell["fine"])
            summary.rows.append({"karat": karat, **cell, "eq21": equivalent,
                                 "per21": cell["sales"] / equivalent if equivalent else None})
        summary.add_totals("karat", _("Total"))
        if summary.totals.get("eq21"):
            summary.totals["per21"] = summary.totals["sales"] / summary.totals["eq21"]
        return [summary, sold, back]


# --- net sales and salespeople ----------------------------------------------------------------

NET_GROUPS = (("karat", gettext_lazy("Karat")), ("category", gettext_lazy("Category")),
              ("branch", gettext_lazy("Branch")), ("seller", gettext_lazy("Salesperson")),
              ("day", gettext_lazy("Day")))


def _net_rows(start, end, scope, group: str) -> dict:
    """{group label: sold and returned figures}. Returns count on their own day and branch."""
    def key(line, day, branch):
        if group == "karat":
            return line.karat.label if line.karat_id else "—"
        if group == "category":
            return line.category.name if line.category_id else "—"
        if group == "branch":
            return branch.name
        if group == "seller":
            user = line.invoice.sold_by
            return user.display_name if user else "—"
        return day

    rows = defaultdict(lambda: defaultdict(lambda: ZERO))
    for line in _sold(start, end, scope):
        cell = rows[key(line, line.invoice.business_date, line.invoice.branch)]
        cell["invoices_set"] = cell.get("invoices_set") or set()
        cell["invoices_set"].add(line.invoice_id)
        cell["pieces"] += line.qty
        cell["sold_g"] += line.gross_weight_g
        cell["sold21"] += eq21(line.fine_weight_g) if _is_gold(line) else ZERO
        cell["sold"] += line.line_total
        cell["profit"] += _profit(line)
    for return_line in _returned(start, end, scope):
        line, sales_return = return_line.original_line, return_line.sales_return
        cell = rows[key(line, sales_return.business_date, sales_return.branch)]
        cell["returned_pieces"] += line.qty
        cell["returned_g"] += line.gross_weight_g
        cell["returned21"] += eq21(line.fine_weight_g) if _is_gold(line) else ZERO
        cell["returned"] += line.line_total
        cell["profit"] -= _profit(line)
    for cell in rows.values():
        cell["invoices"] = len(cell.pop("invoices_set", None) or ())
        cell["net_g"] = cell["sold_g"] - cell["returned_g"]
        cell["net21"] = cell["sold21"] - cell["returned21"]
        cell["net"] = cell["sold"] - cell["returned"]
    return rows


@register
class NetSales(Report):
    code = "net_sales"
    section = "sales"
    title = gettext_lazy("Net sales")
    description = gettext_lazy("Sold, returned and net in a period by karat, category, branch, "
                               "salesperson or day, in grams, 21K equivalent and money.")
    filters = (*DATE_FILTERS, BRANCH_FILTER,
               Filter("group", "choice", gettext_lazy("Group by"), NET_GROUPS, "karat"))

    def run(self, params, scope):
        group = params.get("group") or "karat"
        label = str(dict(NET_GROUPS)[group])
        rows = _net_rows(params.date("date_from"), params.date("date_to"), scope, group)
        table = Table(_("Net sales"), [
            Column("group", label, "date" if group == "day" else "text"),
            Column("pieces", _("Pieces sold"), "int"), Column("sold_g", _("Sold (g)"), "weight"),
            Column("sold", _("Sold"), "money"),
            Column("returned_pieces", _("Pieces returned"), "int"),
            Column("returned_g", _("Returned (g)"), "weight"),
            Column("returned", _("Returned"), "money"),
            Column("net_g", _("Net (g)"), "weight"), Column("net21", _("Net 21K (g)"), "weight"),
            Column("net", _("Net sales"), "money"), Column("profit", _("Net profit"), "money")])
        for name, cell in sorted(rows.items(), key=lambda pair: str(pair[0])):
            table.rows.append({"group": name, **cell})
        return [table.add_totals("group", _("Total"))]


@register
class Salespeople(Report):
    code = "salespeople"
    section = "sales"
    title = gettext_lazy("Salespeople")
    description = gettext_lazy("Salespeople ranked by net gold sold (21K equivalent): sales, "
                               "returns, net sales, profit and profit per gram.")
    filters = (*DATE_FILTERS, BRANCH_FILTER)

    def run(self, params, scope):
        rows = _net_rows(params.date("date_from"), params.date("date_to"), scope, "seller")
        table = Table(_("Salespeople"), [
            Column("rank", _("Rank"), "int"), Column("seller", _("Salesperson")),
            Column("invoices", _("Sales"), "int"), Column("pieces", _("Pieces"), "int"),
            Column("sold21", _("Sold 21K (g)"), "weight"), Column("sold", _("Sold"), "money"),
            Column("returned21", _("Returned 21K (g)"), "weight"),
            Column("returned", _("Returned"), "money"),
            Column("net21", _("Net 21K (g)"), "weight"), Column("net", _("Net sales"), "money"),
            Column("profit", _("Net profit"), "money"), Column("margin", _("Profit %"), "pct"),
            Column("per_g", _("Profit per 21K gram"), "money")],
            note=_("Returns count against whoever made the sale, on the day they came back."))
        ranked = sorted(rows.items(), key=lambda pair: (-pair[1]["net21"], -pair[1]["net"]))
        for rank, (name, cell) in enumerate(ranked, start=1):
            table.rows.append({"rank": rank, "seller": name, **cell,
                               "margin": cell["profit"] * 100 / cell["net"] if cell["net"]
                               else None,
                               "per_g": cell["profit"] / cell["net21"] if cell["net21"]
                               else None})
        table.add_totals("seller", _("Total"))
        for key in ("rank", "margin", "per_g"):
            table.totals.pop(key, None)
        return [table]


# --- customers ----------------------------------------------------------------------------------

@register
class CustomerAnalysis(Report):
    code = "customer_analysis"
    section = "sales"
    title = gettext_lazy("Customer analysis")
    description = gettext_lazy("What each customer bought and returned in a period, their last "
                               "purchase, and what they owe now in money and gold.")
    filters = (*DATE_FILTERS, BRANCH_FILTER)
    LIMIT = 500

    def run(self, params, scope):
        start, end = params.date("date_from"), params.date("date_to")
        sold = _sold(start, end, scope).filter(invoice__customer__isnull=False)
        figures = defaultdict(lambda: defaultdict(lambda: ZERO))
        for row in sold.values("invoice__customer").annotate(
                invoices=Count("invoice", distinct=True), pieces=Sum("qty"),
                total=Sum("line_total"), last=Max("invoice__business_date"),
                fine=Sum("fine_weight_g", filter=Q(karat__metal__code="gold"))):
            cell = figures[row["invoice__customer"]]
            cell.update({"invoices": row["invoices"], "pieces": row["pieces"],
                         "bought": row["total"], "eq21": eq21(row["fine"]),
                         "last": row["last"]})
        returned = _returned(start, end, scope).filter(
            original_line__invoice__customer__isnull=False)
        for row in returned.values("original_line__invoice__customer").annotate(
                total=Sum("original_line__line_total")):
            figures[row["original_line__invoice__customer"]]["returned"] += row["total"]
        parties = Party.objects.in_bulk(list(figures))
        balances = defaultdict(dict)
        for row in (BalanceProjection.objects.filter(party_id__in=list(figures),
                                                     account__role="customers")
                    .values("party_id", "commodity__kind", "commodity__is_functional")
                    .annotate(q=Sum("quantity"))):
            if row["commodity__kind"] == "metal":
                balances[row["party_id"]]["gold"] = row["q"]
            elif row["commodity__is_functional"]:
                balances[row["party_id"]]["money"] = row["q"]

        table = Table(_("Customers"), [
            Column("name", _("Name")), Column("phone", _("Phone")),
            Column("invoices", _("Sales"), "int"), Column("pieces", _("Pieces"), "int"),
            Column("eq21", _("21K equivalent (g)"), "weight"),
            Column("bought", _("Bought"), "money"), Column("returned", _("Returned"), "money"),
            Column("net", _("Net"), "money"), Column("last", _("Last purchase"), "date"),
            Column("owes", _("Owes now"), "money"), Column("gold", _("Gold owed (fine g)"),
                                                           "fine")],
            note=_("Owes now: their balance today in the company currency; negative is credit "
                   "they have with you."))
        for party_id, cell in figures.items():
            party = parties[party_id]
            net = cell["bought"] - cell["returned"]
            url = reverse("customer-edit", args=[party_id])
            table.rows.append({"name": party.name, "phone": party.phone, **cell, "net": net,
                               "owes": balances[party_id].get("money"),
                               "gold": balances[party_id].get("gold"),
                               "_urls": {"name": url}})
        table.rows.sort(key=lambda row: -row["net"])
        del table.rows[self.LIMIT:]
        table.add_totals("name", _("Total"))
        table.totals.pop("gold", None)
        return [table]
