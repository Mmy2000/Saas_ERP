"""Reports pack 4 (the third pass over Gweb): payroll over any months (legacy GHE21), cash and
bank balances per branch on any day (GHV23/GHV24, GHB23/GHB24), supplier invoices against the
totals printed on them (GHS47) and changes to pieces' label prices and discount classes
(GHD45)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.core.models import DocStatus

from .registry import BRANCH_FILTER, DATE_FILTERS, Column, Filter, Report, Table, register

ZERO = Decimal(0)


def _scope(queryset, scope, field="branch_id"):
    return queryset if scope is None else queryset.filter(**{f"{field}__in": scope})


def _employees():
    from apps.hr.models import Employee

    return [("", _("Everyone"))] + [(str(e.pk), f"{e.code} · {e.name}")
                                    for e in Employee.objects.order_by("code")]


# --- payroll ----------------------------------------------------------------------------------

@register
class Payroll(Report):
    code = "payroll"
    section = "money"
    title = gettext_lazy("Payroll")
    description = gettext_lazy("Salaries paid over any months: per employee and per month, the "
                               "salary, commission, bonuses, deductions, advances taken back and "
                               "net pay.")
    filters = (*DATE_FILTERS, BRANCH_FILTER,
               Filter("employee", "choice", gettext_lazy("Employee"), _employees))

    def run(self, params, scope):
        from apps.hr.models import PayrollLine

        start, end = params.date("date_from"), params.date("date_to")
        first, last = date(start.year, start.month, 1), date(end.year, end.month, 1)
        months = Q()
        year, month = first.year, first.month
        while date(year, month, 1) <= last:
            months |= Q(run__year=year, run__month=month)
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        lines = _scope(PayrollLine.objects.filter(run__status=DocStatus.POSTED).filter(months),
                       scope, "run__branch_id").select_related("employee", "run")
        chosen = params.get("employee") or ""
        if chosen.isdigit():
            lines = lines.filter(employee_id=int(chosen))
        money = ("salary", "commission", "bonus", "deduction", "advance", "net")
        columns = [Column("salary", _("Salary"), "money"),
                   Column("commission", _("Commission"), "money"),
                   Column("bonus", _("Bonuses"), "money"),
                   Column("deduction", _("Deductions"), "money"),
                   Column("advance", _("Advances taken back"), "money"),
                   Column("net", _("Net pay"), "money")]
        per_employee = Table(_("Per employee"), [
            Column("employee", _("Employee")), Column("months", _("Months"), "int"), *columns])
        per_month = Table(_("Per month"), [
            Column("month", _("Month")), Column("people", _("Employees"), "int"), *columns])
        employees = defaultdict(lambda: {"months": 0, **{key: ZERO for key in money}})
        by_month = defaultdict(lambda: {"people": 0, **{key: ZERO for key in money}})
        for line in lines:
            person = employees[(line.employee.code, line.employee.name, line.employee_id)]
            period = by_month[(line.run.year, line.run.month)]
            person["months"] += 1
            period["people"] += 1
            for key in money:
                person[key] += getattr(line, key)
                period[key] += getattr(line, key)
        for (code, name, pk), cell in sorted(employees.items()):
            per_employee.rows.append({"employee": f"{code} · {name}", **cell,
                                      "_urls": {"employee": f"/hr/{pk}/"}})
        for (year, month), cell in sorted(by_month.items()):
            per_month.rows.append({"month": f"{year}-{month:02d}", **cell})
        per_employee.add_totals("employee", _("Total"))
        per_employee.totals.pop("months", None)
        per_month.add_totals("month", _("Total"))
        per_month.totals.pop("people", None)
        return [per_employee, per_month]


# --- cash and bank balances on a day ----------------------------------------------------------

@register
class CashBalances(Report):
    code = "cash_balances"
    section = "money"
    title = gettext_lazy("Cash and bank balances")
    description = gettext_lazy("What every cash box and bank account held at the end of any "
                               "day, per branch, in its own currency and in the company "
                               "currency.")
    filters = (Filter("date", "date", gettext_lazy("On")), BRANCH_FILTER)

    def run(self, params, scope):
        from apps.ledger.models import JournalLine
        from apps.treasury.models import BankAccount, CashBox

        day = params.date("date")
        lines = JournalLine.objects.filter(business_date__lte=day)
        boxes = Table(_("Cash boxes"), [
            Column("branch", _("Branch")), Column("box", _("Cash box")),
            Column("currency", _("Currency")), Column("balance", _("Balance"), "money"),
            Column("value", _("Value"), "money")],
            note=_("Value: in the company currency, at what the money was booked at."))
        for box in _scope(CashBox.objects.select_related("branch", "currency"), scope).order_by(
                "branch__code", "currency__code", "name"):
            totals = lines.filter(account_id=box.account_id).aggregate(
                q=Sum("quantity"), v=Sum("functional_amount"))
            if totals["q"] or totals["v"]:
                boxes.rows.append({"branch": box.branch.name, "box": box.label,
                                   "currency": box.currency.code, "balance": totals["q"],
                                   "value": totals["v"]})
        boxes.add_totals("branch", _("Total"))
        boxes.totals.pop("balance", None)  # different currencies: only the value adds up

        banks = Table(_("Bank accounts per branch"), [
            Column("account", _("Account")), Column("branch", _("Branch")),
            Column("currency", _("Currency")), Column("balance", _("Balance"), "money"),
            Column("value", _("Value"), "money")],
            note=_("A bank account serves several branches: each line is what was booked "
                   "through it at that branch."))
        names = {}
        for bank in BankAccount.objects.select_related("currency"):
            names[bank.account_id] = (bank.label, bank.currency.code)
        for row in (_scope(lines.filter(account_id__in=list(names)), scope)
                    .values("account_id", "branch__name").annotate(
                        q=Sum("quantity"), v=Sum("functional_amount"))
                    .order_by("account_id", "branch__name")):
            if row["q"] or row["v"]:
                label, currency = names[row["account_id"]]
                banks.rows.append({"account": label, "branch": row["branch__name"],
                                   "currency": currency, "balance": row["q"],
                                   "value": row["v"]})
        banks.add_totals("account", _("Total"))
        banks.totals.pop("balance", None)
        return [boxes, banks]


# --- supplier invoices against their own totals -----------------------------------------------

SHOW = (("differs", gettext_lazy("Only those that differ")),
        ("all", gettext_lazy("Every invoice with their totals")))


@register
class CodingDifferences(Report):
    code = "coding_differences"
    section = "gold"
    title = gettext_lazy("Purchases against their invoices")
    description = gettext_lazy("Purchases whose supplier's own totals were typed in, compared "
                               "with what was entered line by line: pieces, weight, fine gold "
                               "and making charges.")
    filters = (*DATE_FILTERS, BRANCH_FILTER,
               Filter("show", "choice", gettext_lazy("Show"), SHOW, "differs"))

    def run(self, params, scope):
        from apps.purchasing.models import SupplierInvoice
        from apps.purchasing.services import stated_check, totals

        start, end = params.date("date_from"), params.date("date_to")
        invoices = _scope(SupplierInvoice.objects.filter(
            business_date__range=(start, end)).exclude(status=DocStatus.VOIDED).filter(
            Q(stated_pieces__isnull=False) | Q(stated_weight_g__isnull=False)
            | Q(stated_fine_g__isnull=False) | Q(stated_making_amount__isnull=False)),
            scope).select_related("supplier")
        table = Table(_("Invoices"), [
            Column("number", _("Number"), "code"), Column("date", _("Date"), "date"),
            Column("supplier", _("Supplier")), Column("reference", _("Their number")),
            Column("pieces", _("Pieces difference"), "int"),
            Column("weight", _("Weight difference (g)"), "weight"),
            Column("fine", _("Fine gold difference (g)"), "fine"),
            Column("making", _("Making difference"), "money")],
            note=_("Difference = entered − their total; empty where they gave no total."))
        keys = {"stated_pieces": "pieces", "stated_weight_g": "weight", "stated_fine_g": "fine",
                "stated_making_amount": "making"}
        only = params.get("show") != "all"
        for invoice in invoices.order_by("business_date", "id"):
            found = {keys[row["name"]]: row["difference"]
                     for row in stated_check(invoice, totals(invoice))}
            if only and not any(found.values()):
                continue
            table.rows.append({"number": invoice.number or _("Draft"),
                               "date": invoice.business_date, "supplier": invoice.supplier.name,
                               "reference": invoice.supplier_reference, **found,
                               "_urls": {"number": f"/purchasing/{invoice.pk}/"}})
        return [table.add_totals("number", _("Total"))]


# --- label price and discount class changes ---------------------------------------------------

@register
class LabelChanges(Report):
    code = "label_changes"
    section = "gold"
    title = gettext_lazy("Label price and discount changes")
    description = gettext_lazy("Every change to a piece's label price or discount class in a "
                               "period: the piece, before and after, who and when.")
    filters = DATE_FILTERS

    def run(self, params, scope):
        from apps.audit.models import AuditAction, AuditEvent
        from apps.inventory.models import DiscountClass, Item

        start, end = params.date("date_from"), params.date("date_to")
        zone = timezone.get_current_timezone()
        events = (AuditEvent.objects.filter(
            table_name="inventory_item", action=AuditAction.UPDATE,
            at__date__range=(start, end))
            .filter(Q(changes__has_key="label_price") | Q(changes__has_key="discount_class_id"))
            .select_related("user").order_by("at", "id"))
        events = list(events[:2000])
        pieces = Item.objects.in_bulk({event.row_id for event in events})
        if scope is not None:
            pieces = {pk: item for pk, item in pieces.items() if item.branch_id in scope}
        classes = {c.pk: str(c) for c in DiscountClass.objects.all()}
        table = Table(_("Changes"), [
            Column("when", _("When")), Column("piece", _("Piece"), "code"),
            Column("what", _("What")), Column("before", _("Before")),
            Column("after", _("After")), Column("who", _("By"))],
            note=_("The newest 2,000 changes of the period at most."))

        def shown(key, value):
            if value in (None, ""):
                return "—"
            if key == "discount_class_id":
                return classes.get(int(value), str(value))
            return f"{Decimal(str(value)):,.2f}"

        labels = {"label_price": _("Label price"), "discount_class_id": _("Discount class")}
        for event in events:
            item = pieces.get(event.row_id)
            if item is None:
                continue
            for key, label in labels.items():
                if key not in event.changes:
                    continue
                before, after = event.changes[key]
                table.rows.append({
                    "when": timezone.localtime(event.at, zone).strftime("%Y-%m-%d %H:%M"),
                    "piece": item.barcode, "what": str(label), "before": shown(key, before),
                    "after": shown(key, after),
                    "who": event.user.display_name if event.user_id and event.user else "—",
                    "_urls": {"piece": f"/stock/items/{item.pk}/"}})
        return [table]

