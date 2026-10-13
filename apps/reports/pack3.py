"""Reports pack 3: employees' advances (legacy GHE22 كشف حساب الموظفين) and gold ordered by
weight: bullion, coins and other gold reserved for a customer before it is in stock (legacy
GHC30 and its open / delivered lists)."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.core.models import DocStatus

from .registry import BRANCH_FILTER, DATE_FILTERS, Column, Filter, Report, Table, register
from .sales_reports import eq21

ZERO = Decimal(0)


def _scope(queryset, scope, field="branch_id"):
    return queryset if scope is None else queryset.filter(**{f"{field}__in": scope})


# --- employees' advances ----------------------------------------------------------------------

def _employees():
    from apps.hr.models import Employee

    return [("", _("Everyone"))] + [(str(e.pk), f"{e.code} · {e.name}")
                                    for e in Employee.objects.order_by("code")]


@register
class EmployeeAdvances(Report):
    code = "employee_advances"
    section = "money"
    title = gettext_lazy("Employees' advances")
    description = gettext_lazy("What each employee owes in advances: given, paid back, settled "
                               "and taken back in payroll in a period, with the balance before "
                               "and after; and one employee's statement.")
    filters = (*DATE_FILTERS, BRANCH_FILTER,
               Filter("employee", "choice", gettext_lazy("Employee"), _employees))

    def _movements(self, scope):
        """(date, employee id, kind label, document, url, signed amount) of every advance,
        repayment, settlement and payroll recovery."""
        from apps.hr.models import EmployeeAdvance, PayrollLine

        rows = []
        for advance in _scope(EmployeeAdvance.objects.filter(status=DocStatus.POSTED),
                              scope):
            rows.append((advance.business_date, advance.employee_id,
                         str(advance.get_kind_display()), advance.number,
                         f"/hr/advances/{advance.pk}/", advance.sign * advance.amount,
                         advance.kind))
        for line in _scope(PayrollLine.objects.filter(run__status=DocStatus.POSTED,
                                                      advance__gt=0).select_related("run"),
                           scope, "run__branch_id"):
            rows.append((line.run.business_date, line.employee_id, str(_("Taken back in payroll")),
                         line.run.number, "", -line.advance, "payroll"))
        rows.sort(key=lambda row: (row[0], row[3] or ""))
        return rows

    def run(self, params, scope):
        from apps.hr.models import Employee

        start, end = params.date("date_from"), params.date("date_to")
        names = {e.pk: f"{e.code} · {e.name}" for e in Employee.objects.all()}
        movements = [m for m in self._movements(scope) if m[0] <= end]
        cells = defaultdict(lambda: defaultdict(lambda: ZERO))
        for day, employee, _label, _doc, _url, amount, kind in movements:
            cell = cells[employee]
            if day < start:
                cell["opening"] += amount
                continue
            key = {"advance": "given", "repayment": "repaid", "payroll": "recovered"}.get(
                kind, "settled")
            cell[key] += amount
        summary = Table(_("Balances"), [
            Column("employee", _("Employee")), Column("opening", _("Before"), "money"),
            Column("given", _("Given"), "money"), Column("repaid", _("Paid back"), "money"),
            Column("settled", _("Settled"), "money"),
            Column("recovered", _("Taken back in payroll"), "money"),
            Column("closing", _("Owed now"), "money")],
            note=_("Paid back, settlements in their favour and payroll take-backs are negative."))
        for employee, cell in sorted(cells.items(), key=lambda pair: names.get(pair[0], "")):
            closing = sum(cell.values(), ZERO)
            if not any(cell.values()):
                continue
            summary.rows.append({"employee": names.get(employee, "—"), **cell,
                                 "closing": closing, "_urls": {"employee": f"/hr/{employee}/"}})
        summary.add_totals("employee", _("Total"))

        statement = Table(_("Statement"), [
            Column("date", _("Date"), "date"), Column("what", _("Movement")),
            Column("document", _("Document"), "code"), Column("amount", _("Amount"), "money"),
            Column("balance", _("Balance"), "money")],
            note=_("Choose an employee to see their statement."))
        chosen = params.get("employee") or ""
        if chosen.isdigit():
            balance = sum((m[5] for m in movements if m[1] == int(chosen) and m[0] < start),
                          ZERO)
            statement.rows.append({"date": start - timedelta(days=1), "what": _("Before"),
                                   "balance": balance})
            for day, employee, label, number, url, amount, _kind in movements:
                if employee != int(chosen) or day < start:
                    continue
                balance += amount
                statement.rows.append({"date": day, "what": label, "document": number,
                                       "amount": amount, "balance": balance,
                                       "_urls": {"document": url} if url else {}})
        return [summary, statement]


# --- gold ordered by weight -------------------------------------------------------------------

STATES = (("open", gettext_lazy("Open")), ("completed", gettext_lazy("Delivered")),
          ("cancelled", gettext_lazy("Cancelled")), ("", gettext_lazy("All")))


@register
class WeightOrders(Report):
    code = "weight_orders"
    section = "sales"
    title = gettext_lazy("Orders by weight")
    description = gettext_lazy("Bullion, coins and other gold reserved for customers by weight: "
                               "what is still to deliver, by category and karat, and every "
                               "order with its deposit and due date.")
    filters = (*DATE_FILTERS, BRANCH_FILTER,
               Filter("state", "choice", gettext_lazy("Orders"), STATES, "open"))

    def run(self, params, scope):
        from apps.sales.models import ReservationLine

        start, end = params.date("date_from"), params.date("date_to")
        state = params.get("state") or ""
        lines = _scope(ReservationLine.objects.filter(
            item__isnull=True, reservation__business_date__range=(start, end))
            .exclude(reservation__status=DocStatus.DRAFT)
            .select_related("reservation__customer", "category", "karat__metal"),
            scope, "reservation__branch_id")
        if state == "open":
            lines = lines.filter(reservation__status=DocStatus.POSTED,
                                 reservation__sale__isnull=True)
        elif state == "completed":
            lines = lines.filter(reservation__status=DocStatus.POSTED,
                                 reservation__sale__isnull=False)
        elif state == "cancelled":
            lines = lines.filter(reservation__status=DocStatus.VOIDED)

        ready = Table(_("To deliver"), [
            Column("category", _("Category")), Column("karat", _("Karat")),
            Column("orders", _("Orders"), "int"), Column("qty", _("Pieces"), "int"),
            Column("weight", _("Weight (g)"), "weight"),
            Column("eq21", _("21K equivalent (g)"), "weight")],
            note=_("Open orders only: what has to be in stock to deliver them."))
        cells = defaultdict(lambda: {"orders": set(), "qty": 0, "weight": ZERO, "fine": ZERO})
        orders = Table(_("Orders"), [
            Column("number", _("Number"), "code"), Column("date", _("Date"), "date"),
            Column("customer", _("Customer")), Column("due", _("Due"), "date"),
            Column("goods", _("Goods")), Column("weight", _("Weight (g)"), "weight"),
            Column("quoted", _("Quoted"), "money"), Column("deposit", _("Deposit"), "money"),
            Column("state", _("State"))])
        labels = {"open": _("Open"), "completed": _("Delivered"), "cancelled": _("Cancelled")}
        seen = set()
        for line in lines.order_by("reservation__business_date", "reservation_id", "id"):
            order = line.reservation
            karat = line.karat
            goods = " ".join(part for part in (line.category.name if line.category_id else "",
                                               karat.label if karat else "") if part)
            first = order.pk not in seen
            seen.add(order.pk)
            orders.rows.append({
                "number": order.number, "date": order.business_date,
                "customer": order.customer.name, "due": order.expires_on, "goods": goods,
                "weight": line.gross_weight_g, "quoted": line.quoted_total,
                "deposit": order.deposit_amount if first else None,
                "state": str(labels[order.state]) + (
                    f" · {_('overdue')}" if order.is_overdue else ""),
                "_urls": {"number": f"/sales/reservations/{order.pk}/"}})
            if order.state == "open":
                cell = cells[(line.category.name if line.category_id else "—",
                              karat.label if karat else "—")]
                cell["orders"].add(order.pk)
                cell["qty"] += line.qty
                cell["weight"] += line.gross_weight_g
                if karat is not None and karat.metal.code == "gold":
                    cell["fine"] += line.gross_weight_g * karat.fineness / 1000
        for (category, karat), cell in sorted(cells.items()):
            ready.rows.append({"category": category, "karat": karat,
                               "orders": len(cell["orders"]), "qty": cell["qty"],
                               "weight": cell["weight"], "eq21": eq21(cell["fine"])})
        ready.add_totals("category", _("Total"))
        orders.add_totals("number", _("Total"))
        return [ready, orders]
