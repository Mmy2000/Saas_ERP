"""The P0 reports (§3.1): daily summary, gold balances, sales analysis, expenses."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.catalog.domain.metal import DEFAULT_REFERENCE_FINENESS
from apps.catalog.models import Karat
from apps.core.models import DocStatus
from apps.expenses.models import ExpenseVoucher
from apps.inventory.models import ON_HAND, Item, LotBalance, StockTransferLine
from apps.inventory.services import SCRAP_CATEGORY_CODE
from apps.ledger.models import BalanceProjection, JournalLine
from apps.org.models import Branch
from apps.pricing.selectors import fine_gram_value
from apps.sales.models import SalesInvoiceLine, SalesReturn, SalesTradeIn
from apps.settlements.models import Settlement, SettlementKind
from apps.treasury.models import BankAccount, CardTerminal, CashBox

from .registry import BRANCH_FILTER, DATE_FILTERS, Column, Filter, Report, Table, register

ZERO = Decimal(0)


def _in_scope(queryset, scope, field="branch_id"):
    return queryset if scope is None else queryset.filter(**{f"{field}__in": scope})


def _karat_labels() -> dict:
    return {k.pk: k for k in Karat.objects.select_related("metal")}


def _equivalent_21k(fine: Decimal) -> Decimal:
    return (fine * 1000 / DEFAULT_REFERENCE_FINENESS).quantize(Decimal("0.001"))


# --- daily summary ------------------------------------------------------------------------------

@register
class DailySummary(Report):
    code = "daily_summary"
    title = gettext_lazy("Daily summary")
    description = gettext_lazy("One day at a branch: sales, scrap received, money in and out of "
                               "every cash box and bank account.")
    filters = (Filter("date", "date", gettext_lazy("Day")), BRANCH_FILTER)

    def run(self, params, scope):
        day = params.date("date")
        karats = _karat_labels()
        return [self._sales(day, scope, karats), self._scrap(day, scope, karats),
                self._boxes(day, scope), self._banks(day, scope), self._documents(day, scope)]

    def _sales(self, day, scope, karats):
        lines = _in_scope(SalesInvoiceLine.objects.filter(
            invoice__status=DocStatus.POSTED, invoice__business_date=day), scope,
            "invoice__branch_id")
        table = Table(_("Sales"), [
            Column("karat", _("Karat")), Column("invoices", _("Sales"), "int"),
            Column("pieces", _("Pieces"), "int"), Column("gross", _("Weight (g)"), "weight"),
            Column("fine", _("Fine gold (g)"), "fine"), Column("making", _("Making"), "money"),
            Column("discount", _("Discount"), "money"), Column("total", _("Total"), "money")])
        for row in (lines.values("karat").annotate(
                invoices=Count("invoice", distinct=True), pieces=Sum("qty"),
                gross=Sum("gross_weight_g"), fine=Sum("fine_weight_g"),
                making=Sum("making_amount"), discount=Sum("discount_amount"),
                total=Sum("line_total")).order_by("-karat__code")):
            table.rows.append({**row, "karat": karats[row["karat"]].label})
        return table.add_totals("karat", _("Total"))

    def _scrap(self, day, scope, karats):
        trades = _in_scope(SalesTradeIn.objects.filter(
            invoice__status=DocStatus.POSTED, invoice__business_date=day), scope,
            "invoice__branch_id")
        table = Table(_("Scrap gold received"), [
            Column("karat", _("Karat")), Column("gross", _("Weight (g)"), "weight"),
            Column("net", _("After loss (g)"), "weight"),
            Column("fine", _("Fine gold (g)"), "fine"), Column("amount", _("Paid"), "money")])
        for row in trades.values("karat").annotate(
                gross=Sum("gross_weight_g"), net=Sum("net_weight_g"), fine=Sum("fine_weight_g"),
                amount=Sum("amount")).order_by("-karat__code"):
            table.rows.append({**row, "karat": karats[row["karat"]].label})
        return table.add_totals("karat", _("Total"))

    def _boxes(self, day, scope):
        boxes = _in_scope(CashBox.objects.select_related("branch", "currency"), scope)
        table = Table(_("Cash boxes"), [
            Column("box", _("Cash box")), Column("currency", _("Currency")),
            Column("opening", _("Opening"), "money"), Column("in", _("In"), "money"),
            Column("out", _("Out"), "money"), Column("closing", _("Closing"), "money")])
        for box in boxes.order_by("branch__code", "currency__code"):
            lines = JournalLine.objects.filter(account_id=box.account_id)
            opening = lines.filter(business_date__lt=day).aggregate(q=Sum("quantity"))["q"] or ZERO
            moves = lines.filter(business_date=day).aggregate(
                i=Sum("quantity", filter=Q(quantity__gt=0)),
                o=Sum("quantity", filter=Q(quantity__lt=0)))
            money_in, money_out = moves["i"] or ZERO, -(moves["o"] or ZERO)
            if not (opening or money_in or money_out):
                continue
            table.rows.append({"box": f"{box.branch.name} · {box.label}",
                               "currency": box.currency.code, "opening": opening,
                               "in": money_in, "out": money_out,
                               "closing": opening + money_in - money_out})
        return table

    def _banks(self, day, scope):
        table = Table(_("Bank accounts and card terminals"), [
            Column("holder", _("Account")), Column("currency", _("Currency")),
            Column("in", _("In"), "money"), Column("out", _("Out"), "money")],
            note=_("Movements booked at the branch that day."))
        holders = ([(bank, bank.currency.code) for bank in BankAccount.objects.select_related(
            "currency")] + [(t, t.currency.code) for t in CardTerminal.objects.select_related(
                "bank_account__currency")])
        for holder, currency in holders:
            lines = _in_scope(JournalLine.objects.filter(account_id=holder.account_id,
                                                         business_date=day), scope)
            moves = lines.aggregate(i=Sum("quantity", filter=Q(quantity__gt=0)),
                                    o=Sum("quantity", filter=Q(quantity__lt=0)))
            if moves["i"] or moves["o"]:
                table.rows.append({"holder": holder.label, "currency": currency,
                                   "in": moves["i"] or ZERO, "out": -(moves["o"] or ZERO)})
        return table

    def _documents(self, day, scope):
        table = Table(_("Other movements"), [
            Column("what", _("What")), Column("count", _("Number"), "int"),
            Column("amount", _("Amount"), "money")])

        def add(label, queryset, field):
            totals = _in_scope(queryset, scope).aggregate(n=Count("id"), a=Sum(field))
            if totals["n"]:
                table.rows.append({"what": label, "count": totals["n"], "amount": totals["a"]})

        returns = SalesReturn.objects.filter(status=DocStatus.POSTED, business_date=day)
        add(_("Customer returns (refunded)"), returns, "refund_amount")
        settlements = Settlement.objects.filter(status=DocStatus.POSTED, business_date=day)
        add(_("Money received"), settlements.filter(kind=SettlementKind.RECEIPT),
            "functional_amount")
        add(_("Money paid"), settlements.filter(kind=SettlementKind.PAYMENT), "functional_amount")
        add(_("Expenses"), ExpenseVoucher.objects.filter(status=DocStatus.POSTED,
                                                        business_date=day), "functional_amount")
        return table


# --- gold balances ------------------------------------------------------------------------------

@register
class GoldBalances(Report):
    code = "gold_balances"
    title = gettext_lazy("Gold balances")
    description = gettext_lazy("Gold on hand per branch and karat, gold on its way between "
                               "branches, and gold owed with customers, suppliers and traders.")
    filters = (BRANCH_FILTER,)

    def run(self, params, scope):
        return [self._stock(scope), self._transit(scope), self._owed()]

    def _stock(self, scope):
        karats = _karat_labels()
        branches = {b.pk: b for b in Branch.objects.all()}
        cells: dict[tuple, dict] = defaultdict(lambda: defaultdict(lambda: ZERO))
        items = _in_scope(Item.objects.filter(status__in=ON_HAND, karat__isnull=False), scope)
        for row in items.values("branch", "karat").annotate(
                n=Count("id"), g=Sum("gross_weight_g"), f=Sum("fine_weight_g")):
            cell = cells[(row["branch"], row["karat"])]
            cell["pieces"] += row["n"]
            cell["piece_g"] += row["g"]
            cell["fine"] += row["f"]
        lots = _in_scope(LotBalance.objects.filter(gross_weight_g__gt=0, lot__karat__isnull=False),
                         scope, "lot__branch_id")
        for row in lots.values("lot__branch", "lot__karat", "lot__category__code").annotate(
                g=Sum("gross_weight_g"), f=Sum("fine_weight_g")):
            cell = cells[(row["lot__branch"], row["lot__karat"])]
            cell["scrap_g" if row["lot__category__code"] == SCRAP_CATEGORY_CODE else "bulk_g"] += \
                row["g"]
            cell["fine"] += row["f"]

        values = {code: fine_gram_value(code) for code in {k.metal.code for k in karats.values()}}
        table = Table(_("Gold on hand"), [
            Column("branch", _("Branch")), Column("karat", _("Karat")),
            Column("pieces", _("Pieces"), "int"), Column("piece_g", _("Pieces (g)"), "weight"),
            Column("bulk_g", _("By weight (g)"), "weight"),
            Column("scrap_g", _("Scrap (g)"), "weight"), Column("fine", _("Fine gold (g)"), "fine"),
            Column("eq21", _("21K equivalent (g)"), "weight"),
            Column("value", _("Value today"), "money")],
            note=_("Pieces include reserved ones. Value at today's buy price for pure metal."))
        for (branch_id, karat_id), cell in sorted(
                cells.items(), key=lambda pair: (branches[pair[0][0]].code,
                                                 -karats[pair[0][1]].code)):
            karat = karats[karat_id]
            table.rows.append({
                "branch": branches[branch_id].name, "karat": karat.label, **cell,
                "eq21": _equivalent_21k(cell["fine"]) if karat.metal.code == "gold" else None,
                "value": (cell["fine"] * values[karat.metal.code]).quantize(Decimal("0.01"))})
        return table.add_totals("branch", _("Total"))

    def _transit(self, scope):
        karats = _karat_labels()
        table = Table(_("On the way between branches"), [
            Column("karat", _("Karat")), Column("pieces", _("Pieces"), "int"),
            Column("gross", _("Weight (g)"), "weight"), Column("fine", _("Fine gold (g)"), "fine")])
        lines = StockTransferLine.objects.filter(transfer__status=DocStatus.POSTED,
                                                 transfer__received_at__isnull=True,
                                                 karat__isnull=False)
        if scope is not None:
            lines = lines.filter(Q(transfer__branch_id__in=scope)
                                 | Q(transfer__to_branch_id__in=scope))
        for row in lines.values("karat").annotate(n=Sum("qty"), g=Sum("gross_weight_g"),
                                                  f=Sum("fine_weight_g")).order_by("-karat__code"):
            table.rows.append({"karat": karats[row["karat"]].label, "pieces": row["n"],
                               "gross": row["g"], "fine": row["f"]})
        return table.add_totals("karat", _("Total"))

    def _owed(self):
        table = Table(_("Gold owed with customers, suppliers and traders"), [
            Column("party", _("Name")), Column("role", _("Account")),
            Column("fine", _("Fine gold (g)"), "fine")],
            note=_("Positive: they owe you gold. Negative: you owe them."))
        rows = (BalanceProjection.objects.filter(party__isnull=False, commodity__kind="metal")
                .values("party__name", "account__role").annotate(q=Sum("quantity"))
                .order_by("q"))
        names = {"customers": _("Customer"), "suppliers": _("Supplier"),
                 "trade_accounts": _("Trade account")}
        for row in rows:
            if row["q"]:
                table.rows.append({"party": row["party__name"],
                                   "role": names.get(row["account__role"], row["account__role"]),
                                   "fine": row["q"]})
        return table.add_totals("party", _("Total"))


# --- sales analysis -----------------------------------------------------------------------------

GROUPS = (
    ("day", gettext_lazy("Day"), "invoice__business_date"),
    ("seller", gettext_lazy("Salesperson"), "invoice__sold_by__display_name"),
    ("category", gettext_lazy("Category"), "category__name"),
    ("karat", gettext_lazy("Karat"), "karat"),
    ("branch", gettext_lazy("Branch"), "invoice__branch__name"),
)


@register
class SalesAnalysis(Report):
    code = "sales"
    title = gettext_lazy("Sales analysis")
    description = gettext_lazy("Sales over a period by day, salesperson, category, karat or "
                               "branch, with the making-charge margin.")
    filters = (*DATE_FILTERS, BRANCH_FILTER,
               Filter("group", "choice", gettext_lazy("Group by"),
                      tuple((key, label) for key, label, _field in GROUPS), "day"))

    def run(self, params, scope):
        key, label, field = next(g for g in GROUPS if g[0] == params.get("group"))
        start, end = params.date("date_from"), params.date("date_to")
        lines = _in_scope(SalesInvoiceLine.objects.filter(
            invoice__status=DocStatus.POSTED, invoice__business_date__range=(start, end)),
            scope, "invoice__branch_id")
        columns = [Column("group", str(label), "date" if key == "day" else "text"),
                   Column("invoices", _("Sales"), "int"), Column("pieces", _("Pieces"), "int"),
                   Column("gross", _("Weight (g)"), "weight"),
                   Column("fine", _("Fine gold (g)"), "fine"),
                   Column("total", _("Sales total"), "money"),
                   Column("discount", _("Discount"), "money"),
                   Column("gold_value", _("Gold value"), "money"),
                   Column("making_cost", _("Making cost"), "money"),
                   Column("margin", _("Margin"), "money"),
                   Column("margin_pct", _("Margin %"), "pct")]
        table = Table(_("Sales"), columns, note=_(
            "Margin = sales total − gold value (at the board of the sale) − making cost paid to "
            "the supplier. Cancelled sales are left out; returns are shown separately."))
        karats = _karat_labels() if key == "karat" else {}
        for row in (lines.values(field).annotate(
                invoices=Count("invoice", distinct=True), pieces=Sum("qty"),
                gross=Sum("gross_weight_g"), fine=Sum("fine_weight_g"),
                total=Sum("line_total"), discount=Sum("discount_amount"),
                gold_value=Sum("metal_value"), making_cost=Sum("cost_amount"))
                .order_by(field)):
            group = row.pop(field)
            if key == "karat":
                group = karats[group].label
            margin = row["total"] - row["gold_value"] - row["making_cost"]
            share = (margin * 100 / row["total"]) if row["total"] else None
            table.rows.append({**row, "group": group if group is not None else "—",
                               "margin": margin, "margin_pct": share})
        table.add_totals("group", _("Total"))
        if table.totals and table.totals.get("total"):
            table.totals["margin_pct"] = table.totals["margin"] * 100 / table.totals["total"]

        returns = _in_scope(SalesReturn.objects.filter(
            status=DocStatus.POSTED, business_date__range=(start, end)), scope)
        summary = returns.aggregate(n=Count("id"), value=Sum("returned_amount"),
                                    kept=Sum("deduction_amount"), refunded=Sum("refund_amount"))
        back = Table(_("Customer returns in the period"), [
            Column("count", _("Returns"), "int"), Column("value", _("Returned value"), "money"),
            Column("kept", _("Deductions kept"), "money"),
            Column("refunded", _("Refunded"), "money")])
        if summary["n"]:
            back.rows.append({"count": summary["n"], "value": summary["value"],
                              "kept": summary["kept"], "refunded": summary["refunded"]})
        return [table, back]


# --- expenses -----------------------------------------------------------------------------------

@register
class Expenses(Report):
    code = "expenses"
    title = gettext_lazy("Expenses by category")
    description = gettext_lazy("What was spent in a period, per expense category.")
    filters = (*DATE_FILTERS, BRANCH_FILTER)

    def run(self, params, scope):
        vouchers = _in_scope(ExpenseVoucher.objects.filter(
            status=DocStatus.POSTED,
            business_date__range=(params.date("date_from"), params.date("date_to"))), scope)
        table = Table(_("Expenses"), [
            Column("category", _("Expense category")), Column("count", _("Number"), "int"),
            Column("total", _("Total"), "money")])
        for row in vouchers.values("category__name").annotate(
                count=Count("id"), total=Sum("functional_amount")).order_by("-total"):
            table.rows.append({"category": row["category__name"], "count": row["count"],
                               "total": row["total"]})
        return [table.add_totals("category", _("Total"))]
