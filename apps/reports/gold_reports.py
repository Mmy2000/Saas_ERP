"""Gold reports from the legacy pack: scrap bought and sold (GHS48, GHS82), buy/sell coverage
(GHS83), the shop's own gold on a day (GHS86), the category card with opening and closing
(GHS49, GHED12) and the statement between branches (GHBR35, GHBR36).

Gweb rebuilt balances from daily snapshots and its own running sums. Here they come from
what is already kept: stock movements (signed, per piece or lot) and the ledger, where every
gold balance is held in fine grams. Weights are also shown as 21K equivalent.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, F, Q, Sum
from django.db.models.functions import Coalesce
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.catalog.models import ItemCategory, Karat
from apps.core.documents import document_titles, document_url
from apps.core.models import DocStatus
from apps.inventory.models import LotBalance, MovementType, StockMovement
from apps.inventory.services import SCRAP_CATEGORY_CODE
from apps.ledger.models import Account, AccountType, Commodity, JournalEntry, JournalLine
from apps.org.models import Branch
from apps.pricing.models import PriceSide
from apps.pricing.selectors import fine_gram_value
from apps.purchasing.models import ScrapPurchaseLine, ScrapSaleLine
from apps.sales.models import (
    SalesReturnLine,
    SalesTradeIn,
    SettlementBasis,
    TradeSaleLine,
)
from apps.settlements.models import ConversionDirection, Settlement, SettlementKind

from .registry import BRANCH_FILTER, DATE_FILTERS, Column, Filter, Report, Table, register
from .sales_reports import _sold, eq21

ZERO = Decimal(0)


def _scope(queryset, scope, field="branch_id"):
    return queryset if scope is None else queryset.filter(**{f"{field}__in": scope})


def _per(value, weight):
    return (value / weight) if weight else None


# --- scrap ------------------------------------------------------------------------------------

@register
class ScrapGold(Report):
    code = "scrap"
    section = "gold"
    title = gettext_lazy("Scrap gold")
    description = gettext_lazy("Scrap and bullion bought from customers (and taken in on sales), "
                               "what it cost per 21K gram against the day's price, scrap sold "
                               "with its gain, and scrap on hand per branch.")
    filters = (*DATE_FILTERS, BRANCH_FILTER)

    def run(self, params, scope):
        start, end = params.date("date_from"), params.date("date_to")
        return [self._bought(start, end, scope), self._sold(start, end, scope),
                self._on_hand(scope)]

    def _bought(self, start, end, scope):
        cells = defaultdict(lambda: defaultdict(lambda: ZERO))
        purchases = _scope(ScrapPurchaseLine.objects.filter(
            purchase__status=DocStatus.POSTED, purchase__business_date__range=(start, end)),
            scope, "purchase__branch_id").select_related("karat", "purchase__price_board")
        trade_ins = _scope(SalesTradeIn.objects.filter(
            invoice__status=DocStatus.POSTED, invoice__business_date__range=(start, end)),
            scope, "invoice__branch_id").select_related("karat", "invoice__price_board")
        boards = {}

        def add(source, line, board):
            cell = cells[(source, line.karat.code, line.karat.label)]
            cell["gross"] += line.gross_weight_g
            cell["loss"] += line.loss_weight_g
            cell["net"] += line.net_weight_g
            cell["fine"] += line.fine_weight_g
            cell["paid"] += line.amount
            if board.pk not in boards:
                boards[board.pk] = board
            try:
                price = boards[board.pk].price(line.karat, PriceSide.SELL)
            except Exception:  # noqa: BLE001 (a karat missing on that board: no comparison)
                return
            cell["value"] += (line.net_weight_g * price).quantize(Decimal("0.01"))

        for line in purchases:
            add(_("Bullion and coins") if line.category_id else _("Scrap bought"), line,
                line.purchase.price_board)
        for line in trade_ins:
            add(_("Taken in on sales"), line, line.invoice.price_board)
        table = Table(_("Bought"), [
            Column("source", _("From")), Column("karat", _("Karat")),
            Column("gross", _("Weight (g)"), "weight"), Column("loss", _("Loss (g)"), "weight"),
            Column("net", _("Net (g)"), "weight"), Column("eq21", _("21K equivalent (g)"),
                                                         "weight"),
            Column("paid", _("Paid"), "money"), Column("per21", _("Paid per 21K gram"), "money"),
            Column("value", _("At the day's selling price"), "money"),
            Column("margin", _("Difference"), "money")],
            note=_("Difference: what the gold was worth at the day's selling price for its karat "
                   "less what was paid."))
        for (source, _code, label), cell in sorted(cells.items(),
                                                   key=lambda pair: (pair[0][0], -pair[0][1])):
            equivalent = eq21(cell["fine"])
            table.rows.append({"source": source, "karat": label, **cell, "eq21": equivalent,
                               "per21": _per(cell["paid"], equivalent),
                               "margin": cell["value"] - cell["paid"]})
        table.add_totals("source", _("Total"))
        table.totals["per21"] = _per(table.totals.get("paid", ZERO),
                                     table.totals.get("eq21", ZERO))
        return table

    def _sold(self, start, end, scope):
        lines = _scope(ScrapSaleLine.objects.filter(
            sale__status=DocStatus.POSTED, sale__business_date__range=(start, end)),
            scope, "sale__branch_id")
        table = Table(_("Sold"), [
            Column("karat", _("Karat")), Column("gross", _("Weight (g)"), "weight"),
            Column("eq21", _("21K equivalent (g)"), "weight"),
            Column("received", _("Received"), "money"),
            Column("per21", _("Price per 21K gram"), "money"), Column("cost", _("Cost"), "money"),
            Column("gain", _("Gain"), "money")])
        labels = {k.pk: k for k in Karat.objects.all()}
        for row in lines.values("karat").annotate(
                gross=Sum("gross_weight_g"), fine=Sum("fine_weight_g"), received=Sum("amount"),
                cost=Sum("cost_amount")).order_by("-karat__code"):
            equivalent = eq21(row["fine"])
            table.rows.append({"karat": labels[row["karat"]].label, "gross": row["gross"],
                               "eq21": equivalent, "received": row["received"],
                               "per21": _per(row["received"], equivalent), "cost": row["cost"],
                               "gain": row["received"] - row["cost"]})
        table.add_totals("karat", _("Total"))
        table.totals["per21"] = _per(table.totals.get("received", ZERO),
                                     table.totals.get("eq21", ZERO))
        return table

    def _on_hand(self, scope):
        balances = _scope(LotBalance.objects.filter(
            lot__category__code=SCRAP_CATEGORY_CODE, gross_weight_g__gt=0), scope,
            "lot__branch_id").select_related("lot__branch", "lot__karat")
        table = Table(_("Scrap on hand now"), [
            Column("branch", _("Branch")), Column("karat", _("Karat")),
            Column("gross", _("Weight (g)"), "weight"), Column("fine", _("Fine gold (g)"), "fine"),
            Column("eq21", _("21K equivalent (g)"), "weight"), Column("cost", _("Cost"), "money"),
            Column("per21", _("Cost per 21K gram"), "money")])
        for balance in balances.order_by("lot__branch__code", "-lot__karat__code"):
            equivalent = eq21(balance.fine_weight_g)
            table.rows.append({"branch": balance.lot.branch.name,
                               "karat": balance.lot.karat.label if balance.lot.karat_id else "—",
                               "gross": balance.gross_weight_g, "fine": balance.fine_weight_g,
                               "eq21": equivalent, "cost": balance.cost_amount,
                               "per21": _per(balance.cost_amount, equivalent)})
        table.add_totals("branch", _("Total"))
        table.totals["per21"] = _per(table.totals.get("cost", ZERO),
                                     table.totals.get("eq21", ZERO))
        return table


# --- coverage ---------------------------------------------------------------------------------

@register
class Coverage(Report):
    code = "coverage"
    section = "gold"
    title = gettext_lazy("Buy and sell coverage")
    description = gettext_lazy("Gold sold against gold bought back in a period (21K "
                               "equivalent, gold value only): average prices, the spread, what "
                               "is covered and the open position.")
    filters = (*DATE_FILTERS, BRANCH_FILTER)

    def _flows(self, start, end, scope):
        """(side, source, day, fine grams, value) for every sale or purchase of gold for money."""
        flows = []
        for line in _sold(start, end, scope).filter(karat__metal__code="gold"):
            flows.append(("sell", _("Retail sales"), line.invoice.business_date,
                          line.fine_weight_g, line.metal_amount))
        returns = _scope(SalesReturnLine.objects.filter(
            sales_return__status=DocStatus.POSTED,
            sales_return__business_date__range=(start, end),
            original_line__karat__metal__code="gold"), scope, "sales_return__branch_id")
        for row in returns.values("sales_return__business_date").annotate(
                fine=Sum("original_line__fine_weight_g"),
                value=Sum("original_line__metal_amount")):
            flows.append(("sell", _("Customer returns"), row["sales_return__business_date"],
                          -row["fine"], -row["value"]))
        wholesale = _scope(TradeSaleLine.objects.filter(
            trade_sale__status=DocStatus.POSTED, trade_sale__business_date__range=(start, end),
            trade_sale__settlement_basis=SettlementBasis.MONEY, karat__metal__code="gold"),
            scope, "trade_sale__branch_id")
        for row in wholesale.values("trade_sale__business_date").annotate(
                fine=Sum("fine_weight_g"), value=Sum("metal_amount")):
            flows.append(("sell", _("Wholesale for money"), row["trade_sale__business_date"],
                          row["fine"], row["value"]))
        scrap_sales = _scope(ScrapSaleLine.objects.filter(
            sale__status=DocStatus.POSTED, sale__business_date__range=(start, end)),
            scope, "sale__branch_id")
        for row in scrap_sales.values("sale__business_date").annotate(
                fine=Sum("fine_weight_g"), value=Sum("amount")):
            flows.append(("sell", _("Scrap sold"), row["sale__business_date"], row["fine"],
                          row["value"]))
        purchases = _scope(ScrapPurchaseLine.objects.filter(
            purchase__status=DocStatus.POSTED, purchase__business_date__range=(start, end)),
            scope, "purchase__branch_id")
        for row in purchases.values("purchase__business_date").annotate(
                fine=Sum("fine_weight_g"), value=Sum("amount")):
            flows.append(("buy", _("Scrap and bullion bought"), row["purchase__business_date"],
                          row["fine"], row["value"]))
        trade_ins = _scope(SalesTradeIn.objects.filter(
            invoice__status=DocStatus.POSTED, invoice__business_date__range=(start, end)),
            scope, "invoice__branch_id")
        for row in trade_ins.values("invoice__business_date").annotate(
                fine=Sum("fine_weight_g"), value=Sum("amount")):
            flows.append(("buy", _("Taken in on sales"), row["invoice__business_date"],
                          row["fine"], row["value"]))
        fixings = _scope(Settlement.objects.filter(
            status=DocStatus.POSTED, kind=SettlementKind.CONVERSION,
            business_date__range=(start, end)), scope)
        for settlement in fixings:
            side = "buy" if settlement.direction == ConversionDirection.WE_OWE_GOLD else "sell"
            flows.append((side, _("Gold balances settled in money"), settlement.business_date,
                          settlement.fine_weight_g, settlement.functional_amount))
        return flows

    def run(self, params, scope):
        start, end = params.date("date_from"), params.date("date_to")
        flows = self._flows(start, end, scope)
        sources = defaultdict(lambda: [ZERO, ZERO])
        days = defaultdict(lambda: {"sell": [ZERO, ZERO], "buy": [ZERO, ZERO]})
        totals = {"sell": [ZERO, ZERO], "buy": [ZERO, ZERO]}
        for side, source, day, fine, value in flows:
            grams = eq21(fine)
            for bucket in (sources[(side, source)], days[day][side], totals[side]):
                bucket[0] += grams
                bucket[1] += value

        by_source = Table(_("Where the gold came from and went"), [
            Column("side", _("Side")), Column("source", _("From")),
            Column("grams", _("21K equivalent (g)"), "weight"),
            Column("value", _("Value"), "money"),
            Column("average", _("Per 21K gram"), "money")])
        names = {"sell": _("Sold"), "buy": _("Bought")}
        for (side, source), (grams, value) in sorted(sources.items()):
            by_source.rows.append({"side": names[side], "source": source, "grams": grams,
                                   "value": value, "average": _per(value, grams)})

        sold, bought = totals["sell"], totals["buy"]
        average_sell, average_buy = _per(sold[1], sold[0]), _per(bought[1], bought[0])
        covered = min(sold[0], bought[0])
        spread = (average_sell - average_buy) if average_sell and average_buy else None
        open_grams = bought[0] - sold[0]
        reference = average_buy if open_grams > 0 else average_sell
        result = Table(_("Coverage"), [
            Column("what", _("What")), Column("grams", _("21K equivalent (g)"), "weight"),
            Column("price", _("Per 21K gram"), "money"), Column("amount", _("Amount"), "money")],
            note=_("Covered: the grams both sold and bought back; its profit is the spread "
                   "between the average prices. Open position: bought − sold (negative: sold "
                   "more than bought back), valued at the average price of the larger side."))
        if flows:
            larger = max(sold[0], bought[0])
            result.rows += [
                {"what": _("Sold"), "grams": sold[0], "price": average_sell, "amount": sold[1]},
                {"what": _("Bought"), "grams": bought[0], "price": average_buy,
                 "amount": bought[1]},
                {"what": _("Covered (%(pct)s%%)") % {
                    "pct": f"{covered * 100 / larger:.0f}" if larger else "0"},
                 "grams": covered, "price": spread,
                 "amount": spread * covered if spread is not None else None},
                {"what": _("Open position"), "grams": open_grams, "price": reference,
                 "amount": open_grams * reference if reference else None}]

        per_day = Table(_("Day by day"), [
            Column("day", _("Day"), "date"), Column("sold", _("Sold 21K (g)"), "weight"),
            Column("average_sell", _("Average sell"), "money"),
            Column("bought", _("Bought 21K (g)"), "weight"),
            Column("average_buy", _("Average buy"), "money"),
            Column("open", _("Open (g)"), "weight")])
        for day, cell in sorted(days.items()):
            per_day.rows.append({
                "day": day, "sold": cell["sell"][0],
                "average_sell": _per(cell["sell"][1], cell["sell"][0]),
                "bought": cell["buy"][0], "average_buy": _per(cell["buy"][1], cell["buy"][0]),
                "open": cell["buy"][0] - cell["sell"][0]})
        return [result, by_source, per_day]


# --- the shop's own gold ----------------------------------------------------------------------

POSITION_EXCLUDED = {"metal_position", "opening_equity", "retained_earnings"}


@register
class GoldPosition(Report):
    code = "gold_position"
    section = "gold"
    title = gettext_lazy("Gold position")
    description = gettext_lazy("The shop's own gold on any day: what it holds (stock, scrap, at "
                               "workshops, in production, between branches) and what others "
                               "owe it, less what it owes, in fine grams and 21K.")
    filters = (Filter("date", "date", gettext_lazy("On")),)

    def run(self, params, scope):
        day = params.date("date")
        gold = Commodity.objects.filter(kind="metal", metal__code="gold").first()
        table = Table(_("Gold position"), [
            Column("group", _("Group")), Column("account", _("Account")),
            Column("fine", _("Fine gold (g)"), "fine"),
            Column("eq21", _("21K equivalent (g)"), "weight"),
            Column("value", _("Value today"), "money")],
            note=_("From the books on that day. Owed: positive is owed to you, negative is what "
                   "you owe. The total is the gold that is the shop's own."))
        if gold is None:
            return [table]
        price = fine_gram_value("gold")
        rows = (JournalLine.objects.filter(commodity=gold, business_date__lte=day,
                                           account__type__in=(AccountType.ASSET,
                                                              AccountType.LIABILITY,
                                                              AccountType.EQUITY))
                .exclude(account__role__in=POSITION_EXCLUDED)
                .values("account").annotate(q=Sum("quantity")))
        accounts = Account.objects.in_bulk([row["account"] for row in rows])
        held, owed = [], []
        for row in rows:
            if not row["q"]:
                continue
            account = accounts[row["account"]]
            target = owed if account.subledger == "party" else held
            target.append({"group": _("Owed") if target is owed else _("Held"),
                           "account": str(account), "fine": row["q"],
                           "eq21": eq21(row["q"]),
                           "value": (row["q"] * price).quantize(Decimal("0.01"))})
        table.rows = sorted(held, key=lambda r: r["account"]) + sorted(
            owed, key=lambda r: r["account"])
        return [table.add_totals("group", _("Own gold"))]


# --- category card ----------------------------------------------------------------------------

def _categories():
    return [("", gettext_lazy("All categories"))] + [
        (str(c.pk), f"{c.code} · {c.name}")
        for c in ItemCategory.objects.filter(is_active=True).order_by("code")]


def _karats():
    return [("", gettext_lazy("All karats"))] + [
        (str(k.pk), k.label) for k in Karat.objects.filter(is_active=True)
        .select_related("metal").order_by("metal__code", "-code")]


@register
class StockCard(Report):
    code = "stock_card"
    section = "gold"
    title = gettext_lazy("Category card")
    description = gettext_lazy("Opening, what came in and went out by kind, and closing stock "
                               "per category and karat (pieces, grams, 21K), with every "
                               "movement of the period.")
    filters = (*DATE_FILTERS, BRANCH_FILTER,
               Filter("category", "choice", gettext_lazy("Category"), _categories, ""),
               Filter("karat", "choice", gettext_lazy("Karat"), _karats, ""))
    LIMIT = 2000

    def _movements(self, params, scope):
        moves = _scope(StockMovement.objects.annotate(
            category_id=Coalesce(F("item__category_id"), F("lot__category_id")),
            karat_id=Coalesce(F("item__karat_id"), F("lot__karat_id"))), scope)
        if params.get("category"):
            moves = moves.filter(category_id=int(params.get("category")))
        if params.get("karat"):
            moves = moves.filter(karat_id=int(params.get("karat")))
        return moves

    def run(self, params, scope):
        start, end = params.date("date_from"), params.date("date_to")
        moves = self._movements(params, scope)
        categories = {c.pk: c for c in ItemCategory.objects.all()}
        karats = {k.pk: k for k in Karat.objects.select_related("metal")}
        cells = defaultdict(lambda: defaultdict(lambda: ZERO))
        sums = {"qty": Sum("qty"), "g": Sum("gross_weight_g"), "f": Sum("fine_weight_g")}
        for row in moves.filter(business_date__lt=start).values(
                "category_id", "karat_id").annotate(**sums):
            cell = cells[(row["category_id"], row["karat_id"])]
            cell["open_qty"], cell["open_g"], cell["open_f"] = row["qty"], row["g"], row["f"]
        period = moves.filter(business_date__range=(start, end))
        for row in period.values("category_id", "karat_id").annotate(
                in_qty=Sum("qty", filter=Q(gross_weight_g__gt=0) | Q(qty__gt=0)),
                in_g=Sum("gross_weight_g", filter=Q(gross_weight_g__gt=0)),
                out_qty=Sum("qty", filter=Q(gross_weight_g__lt=0) | Q(qty__lt=0)),
                out_g=Sum("gross_weight_g", filter=Q(gross_weight_g__lt=0)),
                net_f=Sum("fine_weight_g")):
            cell = cells[(row["category_id"], row["karat_id"])]
            cell["in_qty"] = row["in_qty"] or 0
            cell["in_g"] = row["in_g"] or ZERO
            cell["out_qty"] = -(row["out_qty"] or 0)
            cell["out_g"] = -(row["out_g"] or ZERO)
            cell["net_f"] = row["net_f"] or ZERO

        summary = Table(_("Opening and closing"), [
            Column("category", _("Category")), Column("karat", _("Karat")),
            Column("open_qty", _("Opening pieces"), "int"),
            Column("open_g", _("Opening (g)"), "weight"),
            Column("in_qty", _("Pieces in"), "int"), Column("in_g", _("In (g)"), "weight"),
            Column("out_qty", _("Pieces out"), "int"), Column("out_g", _("Out (g)"), "weight"),
            Column("close_qty", _("Closing pieces"), "int"),
            Column("close_g", _("Closing (g)"), "weight"),
            Column("close21", _("Closing 21K (g)"), "weight")])
        for (category_id, karat_id), cell in sorted(
                cells.items(), key=lambda pair: (categories[pair[0][0]].code
                                                 if pair[0][0] in categories else "",
                                                 -(karats[pair[0][1]].code
                                                   if pair[0][1] in karats else 0))):
            karat = karats.get(karat_id)
            close_qty = (cell["open_qty"] or 0) + cell["in_qty"] - cell["out_qty"]
            close_g = cell["open_g"] + cell["in_g"] - cell["out_g"]
            close_f = cell["open_f"] + cell["net_f"]
            if not any((cell["open_g"], cell["in_g"], cell["out_g"], cell["open_qty"],
                        cell["in_qty"], cell["out_qty"])):
                continue
            summary.rows.append({
                "category": categories[category_id].name if category_id in categories else "—",
                "karat": karat.label if karat else "—",
                "open_qty": cell["open_qty"] or 0, "open_g": cell["open_g"],
                "in_qty": cell["in_qty"], "in_g": cell["in_g"], "out_qty": cell["out_qty"],
                "out_g": cell["out_g"], "close_qty": close_qty, "close_g": close_g,
                "close21": eq21(close_f) if karat and karat.metal.code == "gold" else None})
        summary.add_totals("category", _("Total"))

        kinds = Table(_("By kind of movement"), [
            Column("kind", _("Movement")), Column("count", _("Movements"), "int"),
            Column("qty", _("Pieces"), "int"), Column("g", _("Weight (g)"), "weight"),
            Column("eq21", _("21K equivalent (g)"), "weight")],
            note=_("Positive: in. Negative: out."))
        labels = dict(MovementType.choices)
        for row in period.values("movement_type").annotate(
                count=Count("id"), qty=Sum("qty"), g=Sum("gross_weight_g"),
                f=Sum("fine_weight_g")).order_by("movement_type"):
            kinds.rows.append({"kind": str(labels.get(row["movement_type"],
                                                      row["movement_type"])),
                               "count": row["count"], "qty": row["qty"], "g": row["g"],
                               "eq21": eq21(row["f"])})
        kinds.add_totals("kind", _("Total"))
        kinds.totals.pop("count", None)

        lines = Table(_("Movements"), [
            Column("date", _("Date"), "date"), Column("kind", _("Movement")),
            Column("document", _("Document")), Column("piece", _("Piece")),
            Column("karat", _("Karat")), Column("qty", _("Pieces"), "int"),
            Column("g", _("Weight (g)"), "weight"), Column("balance", _("Balance (g)"), "weight")])
        opening = sum((cell["open_g"] for cell in cells.values()), ZERO)
        balance = opening
        listed = list(period.select_related("item", "lot__category").order_by(
            "business_date", "id")[:self.LIMIT])
        titles = document_titles((m.document_type, m.document_id) for m in listed)
        for move in listed:
            balance += move.gross_weight_g
            label, number = titles.get((move.document_type, move.document_id), ("", ""))
            url = document_url(move.document_type, move.document_id)
            karat = karats.get(move.karat_id)
            lines.rows.append({
                "date": move.business_date, "kind": str(labels.get(move.movement_type, "")),
                "document": f"{label} {number}".strip() or "—",
                "piece": move.item.barcode if move.item_id else move.lot.category.name,
                "karat": karat.label if karat else "—", "qty": move.qty,
                "g": move.gross_weight_g, "balance": balance,
                "_urls": {"document": url} if url else {}})
        lines.note = (_("Opening balance %(g)s g.") % {"g": f"{opening:,.3f}"}
                      + (" " + _("Only the first %(n)s movements are listed.")
                         % {"n": self.LIMIT} if len(listed) >= self.LIMIT else ""))
        return [summary, kinds, lines]


# --- between branches -------------------------------------------------------------------------

@register
class BranchStatement(Report):
    code = "branch_statement"
    title = gettext_lazy("Branch statement")
    description = gettext_lazy("Money and gold between branches: what each branch is owed by "
                               "the others, and a branch's statement with running balances.")
    filters = (*DATE_FILTERS, BRANCH_FILTER)

    def run(self, params, scope):
        start, end = params.date("date_from"), params.date("date_to")
        account = Account.objects.filter(role="branch_clearing").first()
        gold = Commodity.objects.filter(kind="metal", metal__code="gold").first()
        balances = Table(_("Balances between branches"), [
            Column("branch", _("Branch")), Column("money", _("Money (value)"), "money"),
            Column("gold", _("Gold (fine g)"), "fine"),
            Column("eq21", _("21K equivalent (g)"), "weight")],
            note=_("On the last day of the period. Positive: the other branches owe this branch "
                   "(goods or cash it sent and not yet settled)."))
        statement = Table(_("Statement"), [
            Column("date", _("Date"), "date"), Column("document", _("Document")),
            Column("money_in", _("Money in"), "money"), Column("money_out", _("Money out"),
                                                              "money"),
            Column("money_balance", _("Money balance"), "money"),
            Column("gold_in", _("Gold in (fine g)"), "fine"),
            Column("gold_out", _("Gold out (fine g)"), "fine"),
            Column("gold_balance", _("Gold balance (fine g)"), "fine")],
            note=_("Choose a branch to see its statement. Money is valued in the company "
                   "currency."))
        if account is None:
            return [balances, statement]
        lines = _scope(JournalLine.objects.filter(account=account), scope)
        names = {b.pk: b.name for b in Branch.objects.all()}
        per_branch = defaultdict(lambda: {"money": ZERO, "gold": ZERO})
        for row in lines.filter(business_date__lte=end).values(
                "branch_id", "commodity__kind", "commodity_id").annotate(
                q=Sum("quantity"), v=Sum("functional_amount")):
            cell = per_branch[row["branch_id"]]
            if gold is not None and row["commodity_id"] == gold.pk:
                cell["gold"] += row["q"]
            elif row["commodity__kind"] == "money":
                cell["money"] += row["v"]
        for branch_id, cell in sorted(per_branch.items(), key=lambda p: names.get(p[0], "")):
            if cell["money"] or cell["gold"]:
                balances.rows.append({"branch": names.get(branch_id, "—"), **cell,
                                      "eq21": eq21(cell["gold"])})
        balances.add_totals("branch", _("Total"))

        chosen = params.get("branch")
        if chosen is None:
            return [balances, statement]
        own = lines.filter(branch_id=chosen.pk)
        money, metal = ZERO, ZERO
        for row in own.filter(business_date__lt=start).values("commodity__kind",
                                                             "commodity_id").annotate(
                q=Sum("quantity"), v=Sum("functional_amount")):
            if gold is not None and row["commodity_id"] == gold.pk:
                metal += row["q"]
            elif row["commodity__kind"] == "money":
                money += row["v"]
        statement.rows.append({"date": start - timedelta(days=1), "document": _("Opening"),
                               "money_balance": money, "gold_balance": metal})
        entries = defaultdict(lambda: {"money": ZERO, "gold": ZERO})
        order = []
        for line in own.filter(business_date__range=(start, end)).select_related(
                "commodity").order_by("business_date", "entry_id"):
            key = line.entry_id
            if key not in entries:
                order.append((line.business_date, key))
            if gold is not None and line.commodity_id == gold.pk:
                entries[key]["gold"] += line.quantity
            elif line.commodity.kind == "money":
                entries[key]["money"] += line.functional_amount
        heads = JournalEntry.objects.in_bulk([key for _day, key in order])
        titles = document_titles((heads[k].source_type, heads[k].source_id) for _d, k in order)
        for day, key in order:
            head, cell = heads[key], entries[key]
            money += cell["money"]
            metal += cell["gold"]
            label, number = titles.get((head.source_type, head.source_id), (head.memo, ""))
            url = document_url(head.source_type, head.source_id)
            statement.rows.append({
                "date": day, "document": f"{label} {number}".strip(),
                "money_in": cell["money"] if cell["money"] > 0 else None,
                "money_out": -cell["money"] if cell["money"] < 0 else None,
                "money_balance": money,
                "gold_in": cell["gold"] if cell["gold"] > 0 else None,
                "gold_out": -cell["gold"] if cell["gold"] < 0 else None,
                "gold_balance": metal, "_urls": {"document": url} if url else {}})
        statement.title = _("Statement of %(branch)s") % {"branch": chosen.name}
        return [balances, statement]
