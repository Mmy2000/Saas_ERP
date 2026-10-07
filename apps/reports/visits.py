"""Customer visits (legacy GHC34 "سجل الزيارات"), worked out from what customers did: buying,
paying, returning, reserving, repairs and selling us scrap. No one types visits in.

A visit is one person on one day, however many documents they had that day. A person is the
customer account, or for walk-ins the phone number (matched to an account with that number),
else the name. New = their first visit ever is in the period. "Not back lately": came in the
year before but not in the last 90 days, the people worth a call.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.core.documents import document_url
from apps.core.models import DocStatus
from apps.org.models import TenantProfile
from apps.parties.domain import InvalidPhone, normalize_phone
from apps.parties.models import Party
from apps.purchasing.models import ScrapPurchase
from apps.repairs.models import RepairOrder
from apps.sales.models import Reservation, SalesInvoice, SalesReturn
from apps.settlements.models import PartySide, Settlement, SettlementKind

from .registry import BRANCH_FILTER, DATE_FILTERS, Column, Filter, Report, Table, register

ZERO = Decimal(0)
LAPSED_AFTER = 90
KINDS = (("all", gettext_lazy("Everyone")), ("new", gettext_lazy("New customers")),
         ("returning", gettext_lazy("Returning customers")))


@dataclass
class Visit:
    day: date
    party_id: int | None
    name: str
    phone: str
    what: str
    number: str
    source: tuple[str, int]
    value: Decimal
    branch_id: int
    key: str = ""


class _People:
    """The key that tells people apart: their account, else their phone, else their name."""

    def __init__(self):
        self.region = TenantProfile.objects.values_list("country", flat=True).first() or "EG"
        self.by_phone = {}
        for row in Party.objects.exclude(phone_e164="").values("id", "phone_e164", "name",
                                                              "phone"):
            self.by_phone.setdefault(row["phone_e164"], row)

    def phone_key(self, raw: str) -> str:
        try:
            return normalize_phone(raw, self.region)
        except InvalidPhone:
            return "".join(ch for ch in raw if ch.isdigit())

    def key(self, visit: Visit) -> str:
        if visit.party_id:
            return f"p{visit.party_id}"
        phone = self.phone_key(visit.phone) if visit.phone else ""
        if phone and phone in self.by_phone:
            party = self.by_phone[phone]
            visit.party_id, visit.name, visit.phone = party["id"], party["name"], party["phone"]
            return f"p{visit.party_id}"
        if phone:
            return f"t{phone}"
        name = " ".join(visit.name.split()).casefold()
        return f"n{name}" if name else ""


def _between(queryset, start, end, field="business_date"):
    if start:
        queryset = queryset.filter(**{f"{field}__gte": start})
    return queryset.filter(**{f"{field}__lte": end})


def collect(start: date | None, end: date, scope=None) -> list[Visit]:
    """Every customer visit between the two days (`start` None: from the beginning)."""
    def branches(queryset):
        return queryset if scope is None else queryset.filter(branch_id__in=scope)

    visits: list[Visit] = []
    sales = branches(_between(SalesInvoice.objects.filter(status=DocStatus.POSTED), start, end))
    for row in sales.values("id", "business_date", "customer_id", "customer__name",
                            "customer__phone", "customer_name", "customer_phone", "number",
                            "total_amount", "branch_id"):
        visits.append(Visit(row["business_date"], row["customer_id"],
                            row["customer__name"] or row["customer_name"],
                            row["customer__phone"] or row["customer_phone"], _("Purchase"),
                            row["number"], ("sales.SalesInvoice", row["id"]),
                            row["total_amount"], row["branch_id"]))
    returns = branches(_between(SalesReturn.objects.filter(status=DocStatus.POSTED), start, end))
    for row in returns.values("id", "business_date", "original_invoice__customer_id",
                              "original_invoice__customer__name",
                              "original_invoice__customer__phone",
                              "original_invoice__customer_name",
                              "original_invoice__customer_phone", "number", "returned_amount",
                              "branch_id"):
        visits.append(Visit(row["business_date"], row["original_invoice__customer_id"],
                            row["original_invoice__customer__name"]
                            or row["original_invoice__customer_name"],
                            row["original_invoice__customer__phone"]
                            or row["original_invoice__customer_phone"], _("Return"),
                            row["number"], ("sales.SalesReturn", row["id"]),
                            row["returned_amount"], row["branch_id"]))
    payments = branches(_between(Settlement.objects.filter(
        status=DocStatus.POSTED, side=PartySide.CUSTOMER,
        kind__in=(SettlementKind.RECEIPT, SettlementKind.PAYMENT, SettlementKind.METAL_IN,
                  SettlementKind.METAL_OUT)), start, end))
    labels = {SettlementKind.RECEIPT: _("Payment"), SettlementKind.PAYMENT: _("Money paid back"),
              SettlementKind.METAL_IN: _("Gold received"),
              SettlementKind.METAL_OUT: _("Gold given")}
    for row in payments.values("id", "business_date", "party_id", "party__name", "party__phone",
                               "kind", "number", "functional_amount", "branch_id"):
        visits.append(Visit(row["business_date"], row["party_id"], row["party__name"],
                            row["party__phone"], labels[row["kind"]], row["number"],
                            ("settlements.Settlement", row["id"]), row["functional_amount"],
                            row["branch_id"]))
    reservations = branches(_between(Reservation.objects.exclude(status=DocStatus.DRAFT),
                                     start, end))
    for row in reservations.values("id", "business_date", "customer_id", "customer__name",
                                   "customer__phone", "number", "quoted_total", "branch_id"):
        visits.append(Visit(row["business_date"], row["customer_id"], row["customer__name"],
                            row["customer__phone"], _("Reservation"), row["number"],
                            ("sales.Reservation", row["id"]), row["quoted_total"],
                            row["branch_id"]))
    repairs = branches(RepairOrder.objects.exclude(status=DocStatus.DRAFT))
    fields = ("id", "business_date", "delivered_at", "customer_id", "customer__name",
              "customer__phone", "customer_name", "customer_phone", "number", "charge_amount",
              "branch_id")
    for row in _between(repairs, start, end).values(*fields):
        visits.append(Visit(row["business_date"], row["customer_id"],
                            row["customer__name"] or row["customer_name"],
                            row["customer__phone"] or row["customer_phone"], _("Repair left"),
                            row["number"], ("repairs.RepairOrder", row["id"]), ZERO,
                            row["branch_id"]))
    for row in _between(repairs.filter(delivered_at__isnull=False), start, end,
                        "delivered_at__date").values(*fields):
        visits.append(Visit(row["delivered_at"].date(), row["customer_id"],
                            row["customer__name"] or row["customer_name"],
                            row["customer__phone"] or row["customer_phone"],
                            _("Repair collected"), row["number"],
                            ("repairs.RepairOrder", row["id"]), row["charge_amount"],
                            row["branch_id"]))
    scrap = branches(_between(ScrapPurchase.objects.filter(status=DocStatus.POSTED), start, end))
    for row in scrap.values("id", "business_date", "seller_id", "seller__name", "seller__phone",
                            "seller_name", "seller_phone", "number", "total_amount",
                            "branch_id"):
        visits.append(Visit(row["business_date"], row["seller_id"],
                            row["seller__name"] or row["seller_name"],
                            row["seller__phone"] or row["seller_phone"], _("Sold us gold"),
                            row["number"], ("purchasing.ScrapPurchase", row["id"]),
                            row["total_amount"], row["branch_id"]))
    people = _People()
    kept = []
    for visit in visits:
        visit.key = people.key(visit)
        if visit.key:  # a walk-in with no name and no phone is nobody we can follow
            kept.append(visit)
    return kept


@register
class CustomerVisits(Report):
    code = "customer_visits"
    section = "sales"
    title = gettext_lazy("Customer visits")
    description = gettext_lazy("Who came in and why, new and returning customers, and who has "
                               "not been back lately: from sales, payments, returns, "
                               "reservations, repairs and scrap bought.")
    filters = (*DATE_FILTERS, BRANCH_FILTER, Filter("kind", "choice", gettext_lazy("Customers"),
                                                    KINDS, "all"))

    def run(self, params, scope):
        start, end = params.date("date_from"), params.date("date_to")
        if params.get("branch"):
            scope = [params.get("branch").pk]
        visits = collect(start, end, scope)
        before = {v.key for v in collect(None, start - timedelta(days=1), scope)}
        by_person = defaultdict(list)
        for visit in visits:
            by_person[visit.key].append(visit)
        wanted = params.get("kind") or "all"

        people = Table(_("Customers"), [
            Column("name", _("Name")), Column("phone", _("Phone")),
            Column("visits", _("Visits"), "int"), Column("value", _("Value"), "money"),
            Column("last", _("Last visit"), "date"), Column("kind", _("Customer"))])
        rows = Table(_("Visits"), [
            Column("date", _("Date"), "date"), Column("name", _("Name")),
            Column("phone", _("Phone")), Column("what", _("What")),
            Column("document", _("Document")), Column("value", _("Value"), "money")])
        spent = {_("Purchase"), _("Repair collected")}
        shown = set()
        for key, own in by_person.items():
            new = key not in before
            if (wanted == "new" and not new) or (wanted == "returning" and new):
                continue
            shown.add(key)
            latest = max(own, key=lambda v: v.day)
            people.rows.append({
                "name": latest.name, "phone": latest.phone,
                "visits": len({v.day for v in own}),
                "value": sum((v.value for v in own if v.what in spent), ZERO),
                "last": latest.day, "kind": _("New") if new else _("Returning")})
        people.rows.sort(key=lambda row: (-row["visits"], -row["value"]))
        for visit in sorted((v for v in visits if v.key in shown),
                            key=lambda v: (v.day, v.name), reverse=True):
            url = document_url(*visit.source)
            rows.rows.append({"date": visit.day, "name": visit.name, "phone": visit.phone,
                              "what": visit.what, "document": visit.number,
                              "value": visit.value, "_urls": {"document": url} if url else {}})
        people.add_totals("name", _("Total"))
        people.note = _("A visit is one person on one day. Value: what they bought and the "
                        "repairs they collected.")
        return [people, self._lapsed(end, scope), rows]

    def _lapsed(self, end, scope):
        """Came in the year before but not in the last LAPSED_AFTER days."""
        recent_start = end - timedelta(days=LAPSED_AFTER)
        recent = {v.key for v in collect(recent_start, end, scope)}
        earlier = collect(end - timedelta(days=365), recent_start - timedelta(days=1), scope)
        last = {}
        for visit in earlier:
            if visit.key not in recent and (visit.key not in last
                                            or visit.day > last[visit.key].day):
                last[visit.key] = visit
        table = Table(_("Not back lately"), [
            Column("name", _("Name")), Column("phone", _("Phone")),
            Column("last", _("Last visit"), "date"), Column("days", _("Days ago"), "int"),
            Column("what", _("Last time"))])
        for visit in sorted(last.values(), key=lambda v: v.day, reverse=True):
            table.rows.append({"name": visit.name, "phone": visit.phone, "last": visit.day,
                               "days": (end - visit.day).days, "what": visit.what})
        table.note = _("Came in during the year before but not in the last %(n)s days.") % {
            "n": LAPSED_AFTER}
        return table
