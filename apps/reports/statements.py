"""Financial statements (§7.10, §7.12): profit and loss, and the balance sheet, from the ledger.

Amounts are in the company currency. Gold and other metals also show their net fine grams,
since a jewellery business keeps its books in both. The profit and loss leaves the year-end
closing entries out (they only move profit to retained earnings); the balance sheet shows profit
not yet closed as its own equity line, so it always balances.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db.models import Sum
from django.utils.formats import date_format
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.ledger.models import (
    Account,
    AccountType,
    Commodity,
    CommodityKind,
    EntryKind,
    JournalLine,
)

from .registry import BRANCH_FILTER, DATE_FILTERS, Column, Filter, Report, Table, register

ZERO = Decimal(0)

# Where each posting role sits in the profit and loss. Accounts a business adds itself fall back
# on their type: income under other income, expenses under operating expenses.
SALES_ROLES = {"sales_gold", "sales_making", "sales_diamonds", "repair_income"}
COST_ROLES = {"cogs_gold", "cogs_diamonds", "repair_costs"}
OTHER_ROLES = {"metal_gain", "fx_gain", "metal_loss", "fx_loss", "rounding"}


@dataclass
class Balances:
    """Net functional amount and net quantity per metal code, per account."""

    money: dict[int, Decimal] = field(default_factory=lambda: defaultdict(lambda: ZERO))
    metal: dict[int, dict[str, Decimal]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(lambda: ZERO)))


def _balances(*, start: date | None, end: date, scope, types, closing: bool) -> Balances:
    lines = JournalLine.objects.filter(business_date__lte=end, account__type__in=types)
    if start is not None:
        lines = lines.filter(business_date__gte=start)
    if not closing:
        lines = lines.exclude(entry__kind=EntryKind.CLOSING)
    if scope is not None:
        lines = lines.filter(branch_id__in=scope)
    metals = {c.pk: c.metal.code for c in Commodity.objects.filter(
        kind=CommodityKind.METAL).select_related("metal")}
    result = Balances()
    for row in lines.values("account", "commodity").annotate(q=Sum("quantity"),
                                                             f=Sum("functional_amount")):
        result.money[row["account"]] += row["f"] or ZERO
        if row["commodity"] in metals and row["q"]:
            result.metal[row["account"]][metals[row["commodity"]]] += row["q"]
    return result


def _metal_label(code: str) -> str:
    names = {"gold": _("Gold"), "silver": _("Silver"), "platinum": _("Platinum")}
    return _("%(metal)s (fine g)") % {"metal": names.get(code, code)}


def _pct(part: Decimal, whole: Decimal):
    return (part * 100 / whole) if whole else None


# --- profit and loss -----------------------------------------------------------------------------

def _pnl_section(account: Account) -> str:
    if account.role in SALES_ROLES:
        return "sales"
    if account.role in COST_ROLES:
        return "cost"
    if account.role in OTHER_ROLES or account.type == AccountType.INCOME:
        return "other"
    return "expenses"


def _months(start: date, end: date) -> list[tuple[date, date]]:
    """The calendar months a period touches, each cut to the period."""
    months = []
    first = start.replace(day=1)
    while first <= end:
        months.append((max(first, start), min(_month_end(first), end)))
        first = date.fromordinal(_month_end(first).toordinal() + 1)
    return months


def _month_end(first: date) -> date:
    following = (first.replace(year=first.year + 1, month=1) if first.month == 12
                 else first.replace(month=first.month + 1))
    return date.fromordinal(following.toordinal() - 1)


def _shift_year(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 February
        return day.replace(year=day.year + years, day=28)


@register
class ProfitAndLoss(Report):
    code = "profit_loss"
    title = gettext_lazy("Profit and loss")
    description = gettext_lazy("Sales, cost of sales, expenses and the net profit for a period, "
                               "against the period before or month by month.")
    filters = (*DATE_FILTERS, BRANCH_FILTER,
               Filter("columns", "choice", gettext_lazy("Columns"), (
                   ("period", gettext_lazy("This period")),
                   ("previous", gettext_lazy("Against the period before")),
                   ("last_year", gettext_lazy("Against the same period last year")),
                   ("months", gettext_lazy("Month by month"))), "previous"))

    def run(self, params, scope):
        start, end = params.date("date_from"), params.date("date_to")
        mode = params.get("columns")
        accounts = {a.pk: a for a in Account.objects.filter(
            type__in=(AccountType.INCOME, AccountType.EXPENSE), is_postable=True)}

        def period(first, last):
            return _balances(start=first, end=last, scope=scope, closing=False,
                             types=(AccountType.INCOME, AccountType.EXPENSE))

        if mode == "months":
            spans = _months(start, end)
            columns = [(f"m{i}", date_format(first, "M Y"), period(first, last))
                       for i, (first, last) in enumerate(spans)]
            columns.append(("total", _("Total"), period(start, end)))
        else:
            columns = [("amount", _("This period"), period(start, end))]
            if mode == "previous":
                length = end - start
                last = date.fromordinal(start.toordinal() - 1)
                columns.append(("before", _("Period before"), period(last - length, last)))
            elif mode == "last_year":
                columns.append(("before", _("Same period last year"),
                                period(_shift_year(start, -1), _shift_year(end, -1))))
        return [self._statement(accounts, columns, mode)]

    def _statement(self, accounts, columns, mode):
        keys = [key for key, _label, _data in columns]
        main = columns[0][2] if mode != "months" else columns[-1][2]
        metals = sorted({code for per in main.metal.values() for code, q in per.items() if q})
        table_columns = [Column("label", _("Account"))]
        table_columns += [Column(key, label, "money") for key, label, _data in columns]
        if mode in ("previous", "last_year"):
            table_columns += [Column("change", _("Change"), "money"),
                              Column("change_pct", _("Change %"), "pct")]
        if mode == "period":
            table_columns.append(Column("share", _("% of sales"), "pct"))
            table_columns += [Column(f"metal_{code}", _metal_label(code), "fine")
                              for code in metals]
        table = Table(_("Profit and loss"), table_columns, note=_(
            "Cost of sales is the cost of the goods sold. Gold columns show the net fine grams "
            "booked (for example the gold sold at cost, or lost in workshops)."))

        def effect(data, account_id):
            """Positive = adds to profit."""
            return -data.money.get(account_id, ZERO)

        def row_for(values: dict, label: str, style: str = "", indent: int = 0) -> dict:
            row = {"label": label, "_style": style, "_indent": indent, **values}
            if "before" in values:
                row["change"] = values["amount"] - values["before"]
                row["change_pct"] = _pct(row["change"], abs(values["before"]))
            return row

        sections = {"sales": [], "cost": [], "expenses": [], "other": []}
        for account in sorted(accounts.values(), key=lambda a: a.code):
            if all(not data.money.get(account.pk) and not data.metal.get(account.pk)
                   for _k, _l, data in columns):
                continue
            sections[_pnl_section(account)].append(account)

        sales_total = {key: sum((effect(data, a.pk) for a in sections["sales"]), ZERO)
                       for key, _l, data in columns}
        totals: dict[str, dict[str, Decimal]] = {}

        def section(name: str, heading: str, total_label: str, sign: int):
            """sign: +1 shows the effect on profit (income), -1 shows costs as positive."""
            table.rows.append(row_for({}, heading, "heading"))
            sums = {key: ZERO for key in keys}
            for account in sections[name]:
                values = {}
                for key, _l, data in columns:
                    values[key] = sign * effect(data, account.pk)
                    sums[key] += effect(data, account.pk)
                row = row_for(values, f"{account.code} {account.label}", indent=1)
                if mode == "period":
                    row["share"] = _pct(values["amount"], sales_total["amount"])
                    for code, grams in main.metal.get(account.pk, {}).items():
                        row[f"metal_{code}"] = grams if sign < 0 else -grams
                table.rows.append(row)
            totals[name] = sums
            shown = {key: sign * value for key, value in sums.items()}
            row = row_for(shown, total_label, "subtotal")
            if mode == "period":
                row["share"] = _pct(shown["amount"], sales_total["amount"])
            table.rows.append(row)

        def result(label: str, names: tuple[str, ...]):
            values = {key: sum((totals[n][key] for n in names), ZERO) for key in keys}
            row = row_for(values, label, "total")
            if mode == "period":
                row["share"] = _pct(values["amount"], sales_total["amount"])
            table.rows.append(row)

        section("sales", _("Sales"), _("Total sales"), +1)
        section("cost", _("Cost of sales"), _("Total cost of sales"), -1)
        result(_("Gross profit"), ("sales", "cost"))
        section("expenses", _("Operating expenses"), _("Total operating expenses"), -1)
        result(_("Operating profit"), ("sales", "cost", "expenses"))
        section("other", _("Other income and losses"), _("Net other income"), +1)
        result(_("Net profit"), ("sales", "cost", "expenses", "other"))
        return table


# --- balance sheet -------------------------------------------------------------------------------

@register
class BalanceSheet(Report):
    code = "balance_sheet"
    title = gettext_lazy("Balance sheet")
    description = gettext_lazy("What the business owns and owes on a day, in money and in fine "
                               "gold: assets, liabilities and equity.")
    filters = (Filter("date", "date", gettext_lazy("As of")), BRANCH_FILTER)

    def run(self, params, scope):
        day = params.date("date")
        everything = _balances(start=None, end=day, scope=scope, closing=True,
                               types=tuple(AccountType.values))
        accounts = {a.pk: a for a in Account.objects.all()}
        metals = sorted({code for per in everything.metal.values() for code, q in per.items()
                         if q})
        table = Table(_("Balance sheet"), [
            Column("label", _("Account")), Column("amount", _("Amount"), "money"),
            *[Column(f"metal_{code}", _metal_label(code), "fine") for code in metals]],
            note=_("Gold is carried at the value it was booked at; the fine gold columns show "
                   "the grams. Profit not yet closed is the profit of the years not closed yet."))

        def shown_under(account):
            """The account a balance is shown on (cash boxes roll up into cash on hand) and
            the group heading above it."""
            chain = [account]
            while chain[-1].parent_id is not None:
                chain.append(accounts[chain[-1].parent_id])
            chain.reverse()  # root, group, account, holder
            if len(chain) >= 3:
                return chain[2], chain[1]
            return chain[-1], None

        lines: dict[int, dict] = {}
        for account_id in set(everything.money) | set(everything.metal):
            account = accounts[account_id]
            if account.type not in (AccountType.ASSET, AccountType.LIABILITY, AccountType.EQUITY):
                continue
            target, group = shown_under(account)
            entry = lines.setdefault(target.pk, {"account": target, "group": group,
                                                 "money": ZERO, "metal": defaultdict(lambda: ZERO)})
            entry["money"] += everything.money.get(account_id, ZERO)
            for code, grams in everything.metal.get(account_id, {}).items():
                entry["metal"][code] += grams

        def values(money, metal, sign):
            row = {"amount": sign * money}
            for code in metals:
                if metal.get(code):
                    row[f"metal_{code}"] = sign * metal[code]
            return row

        totals: dict[str, dict] = {}

        def section(kind, heading, total_label, sign, extra=None):
            table.rows.append({"label": heading, "_style": "heading"})
            money, metal = ZERO, defaultdict(lambda: ZERO)
            entries = sorted((e for e in lines.values() if e["account"].type == kind),
                             key=lambda e: e["account"].code)
            current_group = None
            for entry in entries:
                if not entry["money"] and not any(entry["metal"].values()):
                    continue
                if entry["group"] is not None and entry["group"] != current_group:
                    current_group = entry["group"]
                    table.rows.append({"label": current_group.label, "_indent": 1,
                                       "_style": "subtotal"})
                account = entry["account"]
                table.rows.append({"label": f"{account.code} {account.label}",
                                   "_indent": 2 if entry["group"] is not None else 1,
                                   **values(entry["money"], entry["metal"], sign)})
                money += entry["money"]
                for code, grams in entry["metal"].items():
                    metal[code] += grams
            if extra is not None:
                label, extra_money, extra_metal = extra
                table.rows.append({"label": label, "_indent": 1,
                                   **values(extra_money, extra_metal, sign)})
                money += extra_money
                for code, grams in extra_metal.items():
                    metal[code] += grams
            totals[kind] = {"money": money, "metal": metal}
            table.rows.append({"label": total_label, "_style": "total",
                               **values(money, metal, sign)})

        # Income and expenses not yet moved to retained earnings by a year-end closing.
        profit_money, profit_metal = ZERO, defaultdict(lambda: ZERO)
        for account_id, money in everything.money.items():
            if accounts[account_id].type in (AccountType.INCOME, AccountType.EXPENSE):
                profit_money += money
        for account_id, per in everything.metal.items():
            if accounts[account_id].type in (AccountType.INCOME, AccountType.EXPENSE):
                for code, grams in per.items():
                    profit_metal[code] += grams

        section(AccountType.ASSET, _("Assets"), _("Total assets"), +1)
        section(AccountType.LIABILITY, _("Liabilities"), _("Total liabilities"), -1)
        section(AccountType.EQUITY, _("Equity"), _("Total equity"), -1,
                extra=(_("Profit not yet closed"), profit_money, profit_metal))
        both_money = totals[AccountType.LIABILITY]["money"] + totals[AccountType.EQUITY]["money"]
        both_metal = defaultdict(lambda: ZERO)
        for kind in (AccountType.LIABILITY, AccountType.EQUITY):
            for code, grams in totals[kind]["metal"].items():
                both_metal[code] += grams
        table.rows.append({"label": _("Total liabilities and equity"), "_style": "total",
                           **values(both_money, both_metal, -1)})
        difference = totals[AccountType.ASSET]["money"] + both_money
        if difference:  # only possible when one branch is shown on its own
            table.rows.append({"label": _("Difference with other branches"),
                               "amount": difference})
        return [table]
