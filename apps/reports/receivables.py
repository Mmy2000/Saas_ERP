"""Unpaid invoices and ageing (legacy GHC27, done properly).

Gweb judged each cash invoice on its own payments and ignored later receipts. Here what a
customer or trader owes is their balance in the books, and payments, returns and credits clear
the oldest charges first (first in, first out). So the charges still open are the newest ones
that add up to the balance: an invoice, a repair, an opening balance or an adjustment. Each is
shown with its age, then the balance of every account is split by age.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.core.documents import document_titles, document_url
from apps.ledger.models import Account, Commodity, JournalEntry, JournalLine
from apps.parties.models import Party

from .registry import BRANCH_FILTER, Column, Filter, Report, Table, register

ZERO = Decimal(0)
BUCKETS = ((30, "d30"), (60, "d60"), (90, "d90"), (None, "older"))
WHO = (("customers", gettext_lazy("Customers")), ("trade_accounts", gettext_lazy("Traders")))


def open_charges(role: str, day, scope=None) -> dict[int, list[dict]]:
    """For each account with a balance owed to us on `day` (in the company currency): the
    charges still open, newest first, each {date, amount, open, source_type, source_id, memo}.
    Cancelled documents and their cancellations are left out on both sides."""
    account = Account.objects.filter(role=role).first()
    home = Commodity.objects.filter(is_functional=True).first()
    if account is None or home is None:
        return {}
    cancelled = set()
    for original, reversal in (JournalEntry.objects.filter(reverses__isnull=False,
                                                           business_date__lte=day)
                               .values_list("reverses_id", "id")):
        cancelled.update((original, reversal))
    lines = (JournalLine.objects.filter(account=account, commodity=home, party__isnull=False,
                                        business_date__lte=day)
             .exclude(entry_id__in=cancelled))
    owed = {row["party"]: row["q"] for row in lines.values("party").annotate(q=Sum("quantity"))
            if row["q"] > 0}
    charges = defaultdict(list)
    debits = (lines.filter(party_id__in=owed, quantity__gt=0)
              .select_related("entry").order_by("party_id", "-business_date", "-id"))
    remaining = dict(owed)
    for line in debits:
        left = remaining[line.party_id]
        if left <= 0:
            continue
        if scope is not None and line.branch_id not in scope:
            remaining[line.party_id] = left - min(left, line.quantity)
            continue
        still_open = min(left, line.quantity)
        remaining[line.party_id] = left - still_open
        charges[line.party_id].append({
            "date": line.business_date, "amount": line.quantity, "open": still_open,
            "source_type": line.entry.source_type, "source_id": line.entry.source_id,
            "memo": line.entry.memo})
    return dict(charges)


def _bucket(days: int) -> str:
    for limit, key in BUCKETS:
        if limit is None or days <= limit:
            return key
    return "older"


@register
class UnpaidInvoices(Report):
    code = "unpaid_invoices"
    title = gettext_lazy("Unpaid invoices")
    description = gettext_lazy("What customers or traders still owe, invoice by invoice, and "
                               "how old it is. Payments clear the oldest invoices first.")
    filters = (Filter("date", "date", gettext_lazy("On")), BRANCH_FILTER,
               Filter("who", "choice", gettext_lazy("Who"), WHO, "customers"))

    def run(self, params, scope):
        day = params.date("date")
        if params.get("branch"):
            scope = [params.get("branch").pk]
        charges = open_charges(params.get("who") or "customers", day, scope)
        parties = Party.objects.in_bulk(list(charges))
        titles = document_titles((c["source_type"], c["source_id"])
                                 for items in charges.values() for c in items)

        items = Table(_("Open invoices"), [
            Column("party", _("Name")), Column("phone", _("Phone")),
            Column("document", _("Document")), Column("date", _("Date"), "date"),
            Column("days", _("Days"), "int"), Column("amount", _("Amount"), "money"),
            Column("open", _("Still owed"), "money")])
        ageing = Table(_("By age"), [
            Column("party", _("Name")), Column("phone", _("Phone")),
            Column("d30", _("Up to 30 days"), "money"), Column("d60", _("31–60 days"), "money"),
            Column("d90", _("61–90 days"), "money"), Column("older", _("Over 90 days"), "money"),
            Column("total", _("Total"), "money"), Column("oldest", _("Oldest"), "date")])
        ordered = sorted(charges.items(), key=lambda pair: parties[pair[0]].name)
        for party_id, open_items in ordered:
            party = parties[party_id]
            buckets = dict.fromkeys(("d30", "d60", "d90", "older"), ZERO)
            for charge in reversed(open_items):  # oldest first
                days = (day - charge["date"]).days
                buckets[_bucket(days)] += charge["open"]
                label, number = titles.get((charge["source_type"], charge["source_id"]),
                                           (charge["memo"], ""))
                url = document_url(charge["source_type"], charge["source_id"])
                items.rows.append({
                    "party": party.name, "phone": party.phone,
                    "document": f"{label} {number}".strip(), "date": charge["date"],
                    "days": days, "amount": charge["amount"], "open": charge["open"],
                    "_urls": {"document": url} if url else {}})
            ageing.rows.append({"party": party.name, "phone": party.phone, **buckets,
                                "total": sum(buckets.values(), ZERO),
                                "oldest": open_items[-1]["date"]})
        ageing.add_totals("party", _("Total"))
        ageing.totals.pop("oldest", None)
        items.add_totals("party", _("Total"))
        items.totals.pop("days", None)
        items.totals.pop("amount", None)
        items.note = _("In the company currency. Amounts owed in another currency or in gold "
                       "are on each account's statement.")
        return [ageing, items]
