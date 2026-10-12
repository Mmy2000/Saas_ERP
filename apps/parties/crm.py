"""Customer lists for follow-up (legacy GHC11 / GHC24 / GHC26): who to call this week.

Filters on the customer's class and city, home branch, gender, a birthday or anniversary coming
up, when they last bought, how much they bought, and what they owe; each customer row carries
its last purchase, total bought and balance. The same list exports to CSV for Excel.
"""

from __future__ import annotations

import csv
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from io import StringIO

from django.db.models import DecimalField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.models import DocStatus

from .models import CustomerProfile, CustomerTier, Gender

FILTERS = ("tier", "city", "branch", "gender", "birthday", "anniversary", "bought",
           "not_bought", "min_total", "balance")
WINDOWS = {"week": 7, "month": 30}  # birthday / anniversary in the next … days
MONEY = DecimalField(max_digits=18, decimal_places=2)


def cities() -> list[str]:
    return sorted({c.strip() for c in CustomerProfile.objects.exclude(city="")
                  .values_list("city", flat=True) if c.strip()}, key=str.casefold)


def _coming(field: str, days: int) -> Q:
    """A date whose day and month fall in the next `days` days (any year)."""
    today = timezone.localdate()
    found = Q()
    for offset in range(days + 1):
        day = today + timedelta(days=offset)
        found |= Q(**{f"customer_profile__{field}__month": day.month,
                      f"customer_profile__{field}__day": day.day})
    return found


def annotate(queryset):
    """Each customer's last purchase date, total bought and balance (company currency)."""
    from apps.ledger.models import Account, BalanceProjection
    from apps.sales.models import SalesInvoice

    sales = SalesInvoice.objects.filter(customer=OuterRef("pk"), status=DocStatus.POSTED)
    account = Account.objects.filter(role="customers").first()
    balance = (BalanceProjection.objects.filter(account=account, party=OuterRef("pk"))
               .values("party").annotate(total=Sum("functional_amount")).values("total"))
    return queryset.annotate(
        last_sale=Subquery(sales.order_by("-business_date").values("business_date")[:1]),
        bought=Coalesce(Subquery(sales.values("customer").annotate(
            total=Sum("total_amount")).values("total")), Value(0), output_field=MONEY),
        balance=Coalesce(Subquery(balance), Value(0), output_field=MONEY),
    )


def _number(value: str) -> Decimal | None:
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


def apply(queryset, params) -> tuple[object, dict[str, str]]:
    filters = {key: str(params.get(key, "") or "").strip() for key in FILTERS}
    queryset = annotate(queryset.select_related("customer_profile", "home_branch"))
    if filters["tier"] in CustomerTier.values:
        queryset = queryset.filter(customer_profile__tier=filters["tier"])
    else:
        filters["tier"] = ""
    if filters["city"]:
        queryset = queryset.filter(customer_profile__city__iexact=filters["city"])
    if filters["branch"].isdigit():
        queryset = queryset.filter(home_branch_id=int(filters["branch"]))
    if filters["gender"] in Gender.values:
        queryset = queryset.filter(customer_profile__gender=filters["gender"])
    for field, key in (("birth_date", "birthday"), ("anniversary", "anniversary")):
        if filters[key] in WINDOWS:
            queryset = queryset.filter(_coming(field, WINDOWS[filters[key]]))
        else:
            filters[key] = ""
    today = timezone.localdate()
    if filters["bought"].isdigit():
        queryset = queryset.filter(last_sale__gte=today - timedelta(days=int(filters["bought"])))
    if filters["not_bought"].isdigit():
        since = today - timedelta(days=int(filters["not_bought"]))
        queryset = queryset.filter(Q(last_sale__lt=since) | Q(last_sale__isnull=True))
    minimum = _number(filters["min_total"])
    if minimum is not None:
        queryset = queryset.filter(bought__gte=minimum)
    else:
        filters["min_total"] = ""
    if filters["balance"] == "owes":
        queryset = queryset.filter(balance__gt=0)
    elif filters["balance"] == "owed":
        queryset = queryset.filter(balance__lt=0)
    else:
        filters["balance"] = ""
    return queryset, filters


def export_csv(queryset) -> str:
    """The list for Excel: UTF-8 with a byte-order mark so Arabic names open correctly."""
    out = StringIO()
    out.write(chr(0xFEFF))  # byte-order mark
    writer = csv.writer(out)
    writer.writerow([_("Code"), _("Name"), _("Phone"), _("Other phone"), _("Email"), _("City"),
                     _("Customer class"), _("Branch"), _("Gender"), _("Birth date"),
                     _("Anniversary"), _("Last purchase"), _("Bought"), _("Balance")])
    for party in queryset.iterator(chunk_size=500):
        profile = getattr(party, "customer_profile", None)
        writer.writerow([
            party.code, party.name, party.phone, party.alt_phone, party.email,
            getattr(profile, "city", ""),
            profile.get_tier_display() if profile and profile.tier else "",
            party.home_branch.name if party.home_branch_id else "",
            profile.get_gender_display() if profile and profile.gender else "",
            profile.birth_date.isoformat() if profile and profile.birth_date else "",
            profile.anniversary.isoformat() if profile and profile.anniversary else "",
            party.last_sale.isoformat() if party.last_sale else "",
            f"{party.bought:.2f}", f"{party.balance:.2f}"])
    return out.getvalue()
