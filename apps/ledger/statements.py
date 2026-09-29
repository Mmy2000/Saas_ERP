"""Statements straight from the ledger (ADR-005): one section per currency / metal, with the
balance brought forward, each movement and a running balance. For a customer or supplier
(party_statement) and for a single account such as a cash box (account_statement)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db.models import Sum
from django.utils.translation import gettext

from apps.core.documents import document_titles, document_url

from .models import Commodity, JournalLine

ZERO = Decimal(0)


@dataclass
class StatementRow:
    business_date: date
    number: str  # journal entry
    memo: str  # document label, or the entry memo when it has no registered document
    document_number: str
    amount: Decimal  # debit positive: increases what the party owes us
    balance: Decimal
    url: str | None


@dataclass
class CommodityStatement:
    commodity: Commodity
    opening: Decimal = ZERO
    rows: list[StatementRow] = field(default_factory=list)
    debit: Decimal = ZERO
    credit: Decimal = ZERO

    @property
    def closing(self) -> Decimal:
        return self.opening + self.debit - self.credit


def _untitled(line) -> tuple[str, str]:
    """Label and number for an entry without a registered document (manual journals)."""
    if line.entry.reverses_id:
        return gettext("Reversal of"), line.entry.reverses.number
    return line.entry.memo or line.memo, ""


def party_statement(party, account, date_from: date | None = None,
                    date_to: date | None = None) -> list[CommodityStatement]:
    return statement(JournalLine.objects.filter(party=party, account=account), date_from,
                     date_to)


def account_statement(account, date_from: date | None = None,
                      date_to: date | None = None) -> list[CommodityStatement]:
    return statement(JournalLine.objects.filter(account=account), date_from, date_to)


def statement(lines, date_from: date | None = None,
              date_to: date | None = None) -> list[CommodityStatement]:
    if date_to is not None:
        lines = lines.filter(business_date__lte=date_to)
    commodities = {c.pk: c for c in Commodity.objects.select_related("metal", "currency")}

    sections: dict[int, CommodityStatement] = {}
    if date_from is not None:
        for row in (lines.filter(business_date__lt=date_from).values("commodity")
                    .annotate(q=Sum("quantity"))):
            sections[row["commodity"]] = CommodityStatement(commodity=commodities[row["commodity"]],
                                                            opening=row["q"] or ZERO)
        lines = lines.filter(business_date__gte=date_from)

    running: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for commodity_id, section in sections.items():
        running[commodity_id] = section.opening
    lines = list(lines.select_related("entry__reverses").order_by("business_date", "entry_id",
                                                                   "id"))
    titles = document_titles((line.entry.source_type, line.entry.source_id) for line in lines)
    for line in lines:
        section = sections.setdefault(
            line.commodity_id, CommodityStatement(commodity=commodities[line.commodity_id]))
        running[line.commodity_id] += line.quantity
        if line.quantity > 0:
            section.debit += line.quantity
        else:
            section.credit -= line.quantity
        label, document_number = titles.get((line.entry.source_type, line.entry.source_id),
                                            _untitled(line))
        section.rows.append(StatementRow(
            business_date=line.business_date, number=line.entry.number, memo=label,
            document_number=document_number, amount=line.quantity,
            balance=running[line.commodity_id],
            url=document_url(line.entry.source_type, line.entry.source_id),
        ))
    return sorted(sections.values(),
                  key=lambda s: (not s.commodity.is_functional, s.commodity.kind, s.commodity.code))
