"""What the treasury screens show: holders with their balances, and money in transit."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Q, Sum

from apps.core.models import DocStatus
from apps.ledger.models import BalanceProjection

from .models import BankAccount, CardTerminal, CashBox, TreasuryDocument

ZERO = Decimal(0)


@dataclass
class HolderRow:
    kind: str  # box | bank | terminal
    holder: object
    quantity: Decimal = ZERO
    value: Decimal = ZERO  # in the company currency

    @property
    def currency(self):
        return self.holder.currency


def _balances(account_ids) -> dict[int, tuple[Decimal, Decimal]]:
    rows = (BalanceProjection.objects.filter(account_id__in=account_ids).values("account")
            .annotate(q=Sum("quantity"), f=Sum("functional_amount")))
    return {r["account"]: (r["q"] or ZERO, r["f"] or ZERO) for r in rows}


def visible_boxes(branch_ids):
    boxes = CashBox.objects.select_related("branch", "currency", "account")
    return boxes if branch_ids is None else boxes.filter(branch_id__in=branch_ids)


def visible_banks(branch_ids):
    banks = BankAccount.objects.select_related("currency", "account")
    if branch_ids is None:
        return banks
    return banks.filter(Q(all_branches=True) | Q(branch_links__branch_id__in=branch_ids)).distinct()


def visible_terminals(branch_ids):
    terminals = CardTerminal.objects.select_related("bank_account__currency", "branch", "account")
    if branch_ids is None:
        return terminals
    return terminals.filter(Q(branch__isnull=True) | Q(branch_id__in=branch_ids))


def holder_choices(branch_ids) -> dict:
    """Active boxes, bank accounts and terminals for forms that take a payment."""
    return {
        "boxes": visible_boxes(branch_ids).filter(is_active=True),
        "banks": visible_banks(branch_ids).filter(is_active=True).prefetch_related(
            "branch_links"),
        "terminals": visible_terminals(branch_ids).filter(is_active=True),
    }


def holder_rows(branch_ids, *, include_inactive: bool = False) -> dict[str, list[HolderRow]]:
    groups = {"box": visible_boxes(branch_ids), "bank": visible_banks(branch_ids),
              "terminal": visible_terminals(branch_ids)}
    if not include_inactive:
        groups = {kind: qs.filter(is_active=True) for kind, qs in groups.items()}
    holders = {kind: list(qs) for kind, qs in groups.items()}
    balances = _balances([h.account_id for hs in holders.values() for h in hs])
    return {kind: [HolderRow(kind, h, *balances.get(h.account_id, (ZERO, ZERO))) for h in hs]
            for kind, hs in holders.items()}


def cash_totals(rows: list[HolderRow]) -> list[tuple[str, Decimal]]:
    """Cash on hand per currency, over the given boxes."""
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for row in rows:
        totals[row.currency.code] += row.quantity
    return sorted(totals.items())


def in_transit(branch_ids):
    """Cash sent between branches and not received yet: (incoming, outgoing) for the scope."""
    pending = (TreasuryDocument.objects.filter(status=DocStatus.POSTED, needs_receipt=True,
                                               received_at__isnull=True)
               .select_related("branch", "to_branch", "currency", "source_box", "dest_box"))
    if branch_ids is None:
        return list(pending), []
    return (list(pending.filter(to_branch_id__in=branch_ids)),
            list(pending.filter(branch_id__in=branch_ids).exclude(to_branch_id__in=branch_ids)))
