"""Balance queries: current balances come from projections, as-of balances from the lines."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db.models import Sum

from .models import Account, BalanceProjection, Commodity, CommodityKind, JournalLine

ZERO = Decimal(0)


def _rows(as_of: date | None, branch_ids):
    # Current balances read the projections; historical ones sum the lines (date index).
    queryset = (BalanceProjection.objects.all() if as_of is None
                else JournalLine.objects.filter(business_date__lte=as_of))
    if branch_ids is not None:
        queryset = queryset.filter(branch_id__in=branch_ids)
    return (queryset.values("account", "commodity")
            .annotate(q=Sum("quantity"), f=Sum("functional_amount")))


@dataclass
class TrialBalanceRow:
    account: Account
    debit: Decimal = ZERO  # functional currency
    credit: Decimal = ZERO
    metals: dict[str, Decimal] = field(default_factory=dict)  # net fine grams per metal code


@dataclass
class TrialBalance:
    rows: list[TrialBalanceRow]
    metal_codes: list[str]
    total_debit: Decimal
    total_credit: Decimal


def trial_balance(as_of: date | None = None, branch_ids=None) -> TrialBalance:
    """Per postable account: net functional balance split into debit/credit, plus net metal
    quantities. Accounts without movement are left out."""
    commodities = {c.pk: c for c in Commodity.objects.select_related("metal")}
    net_functional: dict[int, Decimal] = defaultdict(lambda: ZERO)
    net_metal: dict[int, dict[str, Decimal]] = defaultdict(dict)
    for row in _rows(as_of, branch_ids):
        net_functional[row["account"]] += row["f"] or ZERO
        commodity = commodities[row["commodity"]]
        if commodity.kind == CommodityKind.METAL:
            net_metal[row["account"]][commodity.code] = (
                net_metal[row["account"]].get(commodity.code, ZERO) + (row["q"] or ZERO))

    accounts = Account.objects.filter(pk__in=set(net_functional) | set(net_metal)).order_by("code")
    rows = []
    for account in accounts:
        net = net_functional.get(account.pk, ZERO)
        metals = {code: q for code, q in net_metal.get(account.pk, {}).items() if q != 0}
        if net == 0 and not metals:
            continue
        rows.append(TrialBalanceRow(account=account, debit=net if net > 0 else ZERO,
                                    credit=-net if net < 0 else ZERO, metals=metals))
    metal_codes = sorted({code for row in rows for code in row.metals})
    return TrialBalance(rows=rows, metal_codes=metal_codes,
                        total_debit=sum((r.debit for r in rows), ZERO),
                        total_credit=sum((r.credit for r in rows), ZERO))


def account_balances(branch_ids=None) -> dict[int, dict[str, Decimal]]:
    """Current balance per account id: {"functional": …, "<metal code>": fine grams}."""
    commodities = {c.pk: c for c in Commodity.objects.all()}
    result: dict[int, dict[str, Decimal]] = defaultdict(lambda: {"functional": ZERO})
    for row in _rows(None, branch_ids):
        balance = result[row["account"]]
        balance["functional"] += row["f"] or ZERO
        commodity = commodities[row["commodity"]]
        if commodity.kind == CommodityKind.METAL:
            balance[commodity.code] = balance.get(commodity.code, ZERO) + (row["q"] or ZERO)
    return result


def party_balance(party, account: Account | None = None) -> dict[str, Decimal]:
    """A party's balance per commodity code (money in its currency, metal in fine grams)."""
    queryset = BalanceProjection.objects.filter(party=party)
    if account is not None:
        queryset = queryset.filter(account=account)
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for row in queryset.values("commodity__code").annotate(q=Sum("quantity")):
        totals[row["commodity__code"]] += row["q"]
    return {code: q for code, q in totals.items() if q != 0}
