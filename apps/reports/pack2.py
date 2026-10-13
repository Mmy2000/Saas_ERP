"""Reports pack 2 (the rest of Gweb's report screens): purchases (GHS21, GHS35, GHD21, GHD46),
wholesale sales, returns and net (GHSH23, GHSH29, GHSH25), customer returns (GHS32, GHD28, GHD38,
GHZ26), everyone's balances in money and gold valued today (GHSS22, GHSS23, GHP23, GHP24,
GHSHH22, GHSH22, GHC19), transfers between branches (GHBR31-33) and workshop and production loss
(GHSHH25, GHPR21)."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.catalog.models import Karat
from apps.core.models import DocStatus

from .registry import BRANCH_FILTER, DATE_FILTERS, Column, Filter, Report, Table, register
from .sales_reports import DIAMOND_FAMILIES, FAMILIES, _family, eq21  # noqa: F401

ZERO = Decimal(0)


def _scope(queryset, scope, field="branch_id"):
    return queryset if scope is None else queryset.filter(**{f"{field}__in": scope})


def _labels():
    return {k.pk: k.label for k in Karat.objects.select_related("metal")}


def _parties(role):
    def choices():
        from apps.parties.selectors import parties_with_role

        return [("", _("Everyone"))] + [(str(p.pk), p.name) for p in
                                        parties_with_role(role).filter(is_active=True)
                                        .order_by("name")]
    return choices




# --- purchases --------------------------------------------------------------------------------

@register
class Purchases(Report):
    code = "purchases"
    section = "gold"
    title = gettext_lazy("Purchases")
    description = gettext_lazy("What was bought from suppliers and traders in a period: by "
                               "supplier, and by category and karat, with weight, 21K "
                               "equivalent and making cost; goods returned to them are taken "
                               "off.")
    filters = (*DATE_FILTERS, BRANCH_FILTER,
               Filter("supplier", "choice", gettext_lazy("Supplier"), _parties("supplier")),
               Filter("family", "choice", gettext_lazy("Goods"), FAMILIES))

    def run(self, params, scope):
        from apps.purchasing.models import SupplierInvoiceLine, SupplierReturnLine

        start, end = params.date("date_from"), params.date("date_to")
        bought = _scope(SupplierInvoiceLine.objects.filter(
            invoice__status=DocStatus.POSTED, invoice__business_date__range=(start, end)),
            scope, "invoice__branch_id")
        returned = _scope(SupplierReturnLine.objects.filter(
            supplier_return__status=DocStatus.POSTED,
            supplier_return__business_date__range=(start, end)),
            scope, "supplier_return__branch_id")
        supplier = params.get("supplier")
        if supplier:
            bought = bought.filter(invoice__supplier_id=int(supplier))
            returned = returned.filter(supplier_return__supplier_id=int(supplier))
        family = params.get("family")
        bought = _family(bought, family, "category__product_family")
        returned = _family(returned, family, "category__product_family")
        return [self._by_supplier(bought, returned), self._by_category(bought, returned)]

    def _by_supplier(self, bought, returned):
        table = Table(_("By supplier"), [
            Column("supplier", _("Supplier")), Column("invoices", _("Invoices"), "int"),
            Column("qty", _("Pieces"), "int"), Column("gross", _("Weight (g)"), "weight"),
            Column("eq21", _("21K equivalent (g)"), "weight"),
            Column("making", _("Making cost"), "money"),
            Column("returned", _("Returned (g)"), "weight"),
            Column("net", _("Net weight (g)"), "weight")])
        back = {row["supplier_return__supplier__name"]: row["gross"] for row in returned.values(
            "supplier_return__supplier__name").annotate(gross=Sum("gross_weight_g")).order_by()}
        for row in bought.values("invoice__supplier__name").annotate(
                invoices=Count("invoice", distinct=True), qty=Sum("qty"),
                gross=Sum("gross_weight_g"), fine=Sum("fine_weight_g"),
                making=Sum("making_cost_amount")).order_by("-gross"):
            name = row["invoice__supplier__name"]
            gone = back.pop(name, ZERO)
            table.rows.append({"supplier": name, "invoices": row["invoices"], "qty": row["qty"],
                               "gross": row["gross"], "eq21": eq21(row["fine"]),
                               "making": row["making"], "returned": gone,
                               "net": row["gross"] - gone})
        for name, gone in back.items():  # returned to someone not bought from in the period
            table.rows.append({"supplier": name, "returned": gone, "net": -gone})
        return table.add_totals("supplier", _("Total"))

    def _by_category(self, bought, returned):
        table = Table(_("By category and karat"), [
            Column("category", _("Category")), Column("karat", _("Karat")),
            Column("qty", _("Pieces"), "int"), Column("gross", _("Weight (g)"), "weight"),
            Column("eq21", _("21K equivalent (g)"), "weight"),
            Column("making", _("Making cost"), "money"),
            Column("per_g", _("Making per gram"), "money"),
            Column("returned", _("Returned (g)"), "weight")])
        labels = _labels()
        back = {(row["category__name"], row["karat"]): row["gross"] for row in returned.values(
            "category__name", "karat").annotate(gross=Sum("gross_weight_g")).order_by()}
        for row in bought.values("category__code", "category__name", "karat").annotate(
                qty=Sum("qty"), gross=Sum("gross_weight_g"), fine=Sum("fine_weight_g"),
                making=Sum("making_cost_amount")).order_by("category__code", "karat"):
            table.rows.append({
                "category": row["category__name"], "karat": labels.get(row["karat"], "—"),
                "qty": row["qty"], "gross": row["gross"], "eq21": eq21(row["fine"]),
                "making": row["making"],
                "per_g": (row["making"] / row["gross"]) if row["gross"] else None,
                "returned": back.get((row["category__name"], row["karat"]), ZERO)})
        table.add_totals("category", _("Total"))
        totals = table.totals
        totals["per_g"] = (totals["making"] / totals["gross"]) if totals.get("gross") else None
        return table


# --- wholesale --------------------------------------------------------------------------------

@register
class Wholesale(Report):
    code = "wholesale"
    section = "sales"
    title = gettext_lazy("Wholesale sales")
    description = gettext_lazy("Sales to traders by weight in a period, per trade account: "
                               "weight, fine gold, making and money charged, what they returned, "
                               "and the net.")
    filters = (*DATE_FILTERS, BRANCH_FILTER,
               Filter("account", "choice", gettext_lazy("Trade account"),
                      _parties("trade_account")))

    def run(self, params, scope):
        from apps.sales.models import TradeReturnLine, TradeSaleLine

        start, end = params.date("date_from"), params.date("date_to")
        sold = _scope(TradeSaleLine.objects.filter(
            trade_sale__status=DocStatus.POSTED, trade_sale__business_date__range=(start, end)),
            scope, "trade_sale__branch_id")
        back = _scope(TradeReturnLine.objects.filter(
            trade_return__status=DocStatus.POSTED,
            trade_return__business_date__range=(start, end)), scope, "trade_return__branch_id")
        account = params.get("account")
        if account:
            sold = sold.filter(trade_sale__trade_account_id=int(account))
            back = back.filter(trade_return__trade_account_id=int(account))
        table = Table(_("Per trade account"), [
            Column("account", _("Trade account")), Column("sales", _("Sales"), "int"),
            Column("gross", _("Weight (g)"), "weight"), Column("fine", _("Fine (g)"), "fine"),
            Column("making", _("Making"), "money"), Column("metal", _("Gold value"), "money"),
            Column("ret_gross", _("Returned (g)"), "weight"),
            Column("ret_fine", _("Returned fine (g)"), "fine"),
            Column("net_fine", _("Net fine (g)"), "fine"),
            Column("net_making", _("Net making"), "money")])
        returns = {row["trade_return__trade_account__name"]: row for row in back.values(
            "trade_return__trade_account__name").annotate(
            gross=Sum("original_line__gross_weight_g"), fine=Sum("original_line__fine_weight_g"),
            making=Sum("original_line__making_amount")).order_by()}
        for row in sold.values("trade_sale__trade_account__name").annotate(
                sales=Count("trade_sale", distinct=True), gross=Sum("gross_weight_g"),
                fine=Sum("fine_weight_g"), making=Sum("making_amount"),
                metal=Sum("metal_amount")).order_by("-fine"):
            name = row["trade_sale__trade_account__name"]
            gone = returns.pop(name, {"gross": ZERO, "fine": ZERO, "making": ZERO})
            table.rows.append({
                "account": name, "sales": row["sales"], "gross": row["gross"],
                "fine": row["fine"], "making": row["making"], "metal": row["metal"],
                "ret_gross": gone["gross"], "ret_fine": gone["fine"],
                "net_fine": row["fine"] - gone["fine"],
                "net_making": row["making"] - gone["making"]})
        for name, gone in returns.items():
            table.rows.append({"account": name, "ret_gross": gone["gross"],
                               "ret_fine": gone["fine"], "net_fine": -gone["fine"],
                               "net_making": -gone["making"]})
        return [table.add_totals("account", _("Total"))]


# --- customer returns -------------------------------------------------------------------------

@register
class CustomerReturns(Report):
    code = "customer_returns"
    section = "sales"
    title = gettext_lazy("Customer returns")
    description = gettext_lazy("Pieces customers brought back in a period, by category and "
                               "karat, with what they had paid, what was kept and what was "
                               "refunded.")
    filters = (*DATE_FILTERS, BRANCH_FILTER,
               Filter("family", "choice", gettext_lazy("Goods"), FAMILIES))

    def run(self, params, scope):
        from apps.sales.models import SalesReturn, SalesReturnLine

        start, end = params.date("date_from"), params.date("date_to")
        lines = _family(_scope(SalesReturnLine.objects.filter(
            sales_return__status=DocStatus.POSTED,
            sales_return__business_date__range=(start, end)), scope, "sales_return__branch_id"),
            params.get("family"), "original_line__category__product_family")
        labels = _labels()
        goods = Table(_("By category and karat"), [
            Column("category", _("Category")), Column("karat", _("Karat")),
            Column("qty", _("Pieces"), "int"), Column("gross", _("Weight (g)"), "weight"),
            Column("eq21", _("21K equivalent (g)"), "weight"),
            Column("value", _("Sold for"), "money")])
        for row in lines.values("original_line__category__name", "original_line__karat").annotate(
                qty=Sum("original_line__qty"), gross=Sum("original_line__gross_weight_g"),
                fine=Sum("original_line__fine_weight_g"),
                value=Sum("original_line__line_total")).order_by(
                "original_line__category__name"):
            goods.rows.append({"category": row["original_line__category__name"],
                               "karat": labels.get(row["original_line__karat"], "—"),
                               "qty": row["qty"], "gross": row["gross"],
                               "eq21": eq21(row["fine"]), "value": row["value"]})
        goods.add_totals("category", _("Total"))
        money = Table(_("Refunds"), [
            Column("method", _("Refund")), Column("returns", _("Returns"), "int"),
            Column("returned", _("Returned"), "money"), Column("kept", _("Deducted"), "money"),
            Column("refunded", _("Refunded"), "money")])
        headers = SalesReturn.objects.filter(pk__in=lines.values("sales_return_id"))
        methods = dict(SalesReturn._meta.get_field("refund_method").choices)
        for row in headers.values("refund_method").annotate(
                returns=Count("id"), returned=Sum("returned_amount"),
                kept=Sum("deduction_amount"), refunded=Sum("refund_amount")).order_by():
            money.rows.append({"method": str(methods.get(row["refund_method"],
                                                         row["refund_method"])),
                               "returns": row["returns"], "returned": row["returned"],
                               "kept": row["kept"], "refunded": row["refunded"]})
        return [goods, money.add_totals("method", _("Total"))]


# --- balances ---------------------------------------------------------------------------------

ROLES = (("supplier", gettext_lazy("Suppliers")), ("customer", gettext_lazy("Customers")),
         ("trade_account", gettext_lazy("Trade accounts")),
         ("workshop", gettext_lazy("Workshops")), ("partner", gettext_lazy("Partners")))
PAGES = {"supplier": "suppliers", "customer": "customers", "trade_account": "trade-accounts",
         "workshop": "workshops", "partner": "partners"}
ACCOUNTS = {"supplier": "suppliers", "customer": "customers", "trade_account": "trade_accounts",
            "workshop": "workshops", "partner": "partners_capital"}


@register
class Balances(Report):
    code = "balances"
    section = "money"
    title = gettext_lazy("Balances")
    description = gettext_lazy("What each supplier, customer, trader, workshop or partner owes "
                               "or is owed today: money per currency and gold in fine grams, the "
                               "gold valued at today's price, and the net in the company "
                               "currency.")
    filters = (Filter("role", "choice", gettext_lazy("Who"), ROLES, "supplier"),
               Filter("side", "choice", gettext_lazy("Show"), (
                   ("", gettext_lazy("Everyone with a balance")),
                   ("owes", gettext_lazy("They owe us")), ("owed", gettext_lazy("We owe them"))),
                      ""))

    def run(self, params, scope):
        from apps.ledger.models import BalanceProjection
        from apps.pricing.selectors import fine_gram_value, functional_currency

        role = params.get("role") or "supplier"
        rows = _scope(BalanceProjection.objects.filter(
            account__role=ACCOUNTS[role], party__isnull=False), scope).values(
            "party_id", "party__name", "commodity__code", "commodity__kind",
            "commodity__metal__code").annotate(qty=Sum("quantity"),
                                               value=Sum("functional_amount")).order_by()
        home = functional_currency()
        prices = {}
        people = defaultdict(lambda: {"money": defaultdict(lambda: ZERO), "gold": ZERO,
                                      "silver": ZERO, "value": ZERO})
        names = {}
        for row in rows:
            if not row["qty"]:
                continue
            person = people[row["party_id"]]
            names[row["party_id"]] = row["party__name"]
            if row["commodity__kind"] == "metal":
                metal = row["commodity__metal__code"]
                if metal not in prices:
                    prices[metal] = fine_gram_value(metal)
                person["gold" if metal == "gold" else "silver"] += row["qty"]
                person["value"] += (row["qty"] * prices[metal]).quantize(Decimal("0.01"))
            else:
                person["money"][row["commodity__code"]] += row["qty"]
                person["value"] += row["value"] if row["commodity__code"] != home else row["qty"]
        currencies = sorted({code for p in people.values() for code in p["money"]},
                            key=lambda code: (code != home, code))
        columns = [Column("name", _("Name"))] + [
            Column(f"cur_{code}", code, "money") for code in currencies] + [
            Column("gold", _("Gold, fine (g)"), "fine")]
        if any(p["silver"] for p in people.values()):
            columns.append(Column("silver", _("Silver, fine (g)"), "fine"))
        columns += [Column("gold_value", _("Gold at today's price"), "money"),
                    Column("net", _("Net (%(currency)s)") % {"currency": home}, "money")]
        table = Table(dict(ROLES)[role], columns, note=_(
            "Positive: they owe us. Negative: we owe them. Gold is valued at today's buying "
            "price per fine gram; other currencies at the rates they were booked at."))
        side = params.get("side")
        for pk, person in sorted(people.items(), key=lambda pair: -abs(pair[1]["value"])):
            if side == "owes" and person["value"] <= 0 or side == "owed" and person["value"] >= 0:
                continue
            row = {"name": names[pk], "gold": person["gold"], "silver": person["silver"],
                   "net": person["value"],
                   "gold_value": (person["gold"] * prices.get("gold", ZERO)).quantize(
                       Decimal("0.01")),
                   "_urls": {"name": f"/{PAGES[role]}/{pk}/statement/"}}
            for code in currencies:
                row[f"cur_{code}"] = person["money"].get(code, ZERO)
            table.rows.append(row)
        return [table.add_totals("name", _("Total"))]


# --- transfers between branches ---------------------------------------------------------------

@register
class BranchTransfers(Report):
    code = "branch_transfers"
    section = "gold"
    title = gettext_lazy("Transfers between branches")
    description = gettext_lazy("Goods sent from branch to branch in a period, per route and "
                               "karat: pieces, weight, fine gold and 21K equivalent, and how "
                               "much of it is still on the way.")
    filters = (*DATE_FILTERS, BRANCH_FILTER,
               Filter("family", "choice", gettext_lazy("Goods"), FAMILIES))

    def run(self, params, scope):
        from apps.inventory.models import StockTransferLine

        start, end = params.date("date_from"), params.date("date_to")
        lines = StockTransferLine.objects.filter(
            transfer__status=DocStatus.POSTED, transfer__business_date__range=(start, end))
        if scope is not None:
            lines = lines.filter(Q(transfer__branch_id__in=scope)
                                 | Q(transfer__to_branch_id__in=scope))
        lines = _family(lines, params.get("family"), "category__product_family")
        labels = _labels()
        table = Table(_("Per route and karat"), [
            Column("route", _("From → to")), Column("karat", _("Karat")),
            Column("transfers", _("Transfers"), "int"), Column("qty", _("Items"), "int"),
            Column("gross", _("Weight (g)"), "weight"), Column("fine", _("Fine (g)"), "fine"),
            Column("eq21", _("21K equivalent (g)"), "weight"),
            Column("transit", _("Still on the way (g)"), "weight")])
        for row in lines.values("transfer__branch__name", "transfer__to_branch__name",
                                "karat").annotate(
                transfers=Count("transfer", distinct=True), qty=Sum("qty"),
                gross=Sum("gross_weight_g"), fine=Sum("fine_weight_g"),
                transit=Sum("gross_weight_g", filter=Q(transfer__received_at__isnull=True)),
        ).order_by("transfer__branch__name", "transfer__to_branch__name", "karat"):
            table.rows.append({
                "route": f"{row['transfer__branch__name']} → {row['transfer__to_branch__name']}",
                "karat": labels.get(row["karat"], "—"), "transfers": row["transfers"],
                "qty": row["qty"], "gross": row["gross"], "fine": row["fine"],
                "eq21": eq21(row["fine"]), "transit": row["transit"] or ZERO})
        return [table.add_totals("route", _("Total"))]


# --- workshop and production loss -------------------------------------------------------------

@register
class ProductionLoss(Report):
    code = "production_loss"
    section = "gold"
    title = gettext_lazy("Workshop and production loss")
    description = gettext_lazy("Work orders and production received in a period: fine gold "
                               "sent and returned, the loss and gain, loss per cent and "
                               "labour, per workshop or craftsman and per order.")
    filters = (*DATE_FILTERS, BRANCH_FILTER,
               Filter("kind", "choice", gettext_lazy("Work"), (
                   ("", gettext_lazy("All work")), ("manufacture", gettext_lazy("Making pieces")),
                   ("casting", gettext_lazy("Casting")),
                   ("repair", gettext_lazy("Repair and polishing")),
                   ("in_house", gettext_lazy("In-house production")))))

    def run(self, params, scope):
        from apps.manufacturing.models import WorkOrder

        start, end = params.date("date_from"), params.date("date_to")
        orders = _scope(WorkOrder.objects.filter(
            status=DocStatus.POSTED, received_at__isnull=False,
            received_on__range=(start, end)), scope).select_related("workshop", "craftsman")
        kind = params.get("kind")
        if kind == "in_house":
            orders = orders.filter(workshop__isnull=True)
        elif kind:
            orders = orders.filter(kind=kind, workshop__isnull=False)

        def who(order):
            if order.workshop_id:
                return order.workshop.name
            return order.craftsman.name if order.craftsman_id else _("In-house")

        columns = [Column("issued", _("Sent, fine (g)"), "fine"),
                   Column("received", _("Back, fine (g)"), "fine"),
                   Column("loss", _("Loss, fine (g)"), "fine"),
                   Column("gain", _("Gain, fine (g)"), "fine"),
                   Column("pct", _("Loss %"), "pct"), Column("labour", _("Labour"), "money")]
        per = defaultdict(lambda: defaultdict(lambda: ZERO))
        detail = Table(_("Per order"), [Column("number", _("Order"), "code"),
                                        Column("date", _("Received"), "date"),
                                        Column("who", _("Workshop or craftsman")),
                                        Column("kind", _("Work"))] + columns)
        for order in orders.order_by("received_on", "id"):
            name = who(order)
            row = {"issued": order.issued_fine_weight_g, "received": order.received_fine_weight_g,
                   "loss": order.loss_fine_weight_g, "gain": order.gain_fine_weight_g,
                   "labour": order.labour_amount}
            for key, value in row.items():
                per[name][key] += value
            detail.rows.append({"number": order.number, "date": order.received_on, "who": name,
                                "kind": order.get_kind_display(), **row,
                                "pct": _pct(row["loss"], row["issued"]),
                                "_urls": {"number": f"/manufacturing/{order.pk}/"}})
        detail.add_totals("number", _("Total"))
        detail.totals["pct"] = _pct(detail.totals.get("loss", ZERO),
                                    detail.totals.get("issued", ZERO))
        summary = Table(_("Per workshop or craftsman"), [Column("who", _("Workshop or craftsman"))]
                        + columns)
        for name, values in sorted(per.items(), key=lambda pair: -pair[1]["loss"]):
            summary.rows.append({"who": name, **values,
                                 "pct": _pct(values["loss"], values["issued"])})
        summary.add_totals("who", _("Total"))
        summary.totals["pct"] = _pct(summary.totals.get("loss", ZERO),
                                     summary.totals.get("issued", ZERO))
        return [summary, detail]


def _pct(part, whole):
    return (part * 100 / whole) if whole else None
