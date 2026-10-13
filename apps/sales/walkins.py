"""A walk-in buyer becomes a customer: their name and phone, kept on the sale only, open a
customer record (or find the one that already has the phone), and every walk-in sale with that
phone is put on it, so their history, visits and follow-up start from what they already bought.

Walk-in sales were settled at the till, so no balance moves: only the invoices are linked.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import CharField, F, Func, Value
from django.utils.translation import gettext as _

from apps.core.errors import DomainError, NotFound
from apps.core.models import DocStatus
from apps.parties.domain import InvalidPhone, normalize_phone
from apps.parties.models import Party, PartyRoleType
from apps.parties.services import PartyData, create_customer

from .models import SalesInvoice


def _region() -> str:
    from apps.org.models import TenantProfile

    return TenantProfile.objects.values_list("country", flat=True).first() or "EG"


def _e164(phone: str, region: str) -> str:
    try:
        return normalize_phone(phone, region) if phone else ""
    except InvalidPhone:
        return ""


def same_buyer(invoice: SalesInvoice):
    """Walk-in sales with the same phone as `invoice` (and `invoice` itself)."""
    region = _region()
    target = _e164(invoice.customer_phone, region)
    if not target:
        return SalesInvoice.objects.filter(pk=invoice.pk)
    # Narrow on the last digits, ignoring spaces and dashes, then compare in full.
    tail = "".join(ch for ch in invoice.customer_phone if ch.isdigit())[-7:]
    digits = Func(F("customer_phone"), Value(r"\D"), Value(""), Value("g"),
                  function="regexp_replace", output_field=CharField())
    candidates = (SalesInvoice.objects.filter(customer__isnull=True)
                  .exclude(customer_phone="").annotate(digits=digits)
                  .filter(digits__endswith=tail))
    ids = [c.pk for c in candidates.only("pk", "customer_phone")
           if _e164(c.customer_phone, region) == target]
    return SalesInvoice.objects.filter(pk__in=set(ids) | {invoice.pk})


def make_customer(invoice_id: int, *, name: str = "", actor=None) -> tuple[Party, int]:
    """(the customer, how many sales were put on them)."""
    if actor is not None:
        actor.require("parties.customer.create")
    with transaction.atomic():
        invoice = SalesInvoice.objects.select_for_update().filter(pk=invoice_id).first()
        if invoice is None:
            raise NotFound(_("Not found."))
        if invoice.status == DocStatus.DRAFT:
            raise DomainError(_("Only a posted sale can be used."), code="DOC_NOT_POSTED")
        if invoice.customer_id:
            raise DomainError(_("This sale already has a customer."),
                              code="SALES_HAS_CUSTOMER")
        name = (name or invoice.customer_name).strip()
        if not name and not invoice.customer_phone:
            raise DomainError(_("The sale has no name or phone to make a customer from."),
                              code="SALES_NO_BUYER")
        e164 = _e164(invoice.customer_phone, _region())
        party = (Party.objects.filter(phone_e164=e164, roles__role=PartyRoleType.CUSTOMER)
                 .first() if e164 else None)
        if party is None:
            party = create_customer(PartyData(name=name or invoice.customer_phone,
                                              phone=invoice.customer_phone if e164 else "",
                                              home_branch_id=invoice.branch_id), actor=actor)
        linked = same_buyer(invoice).filter(customer__isnull=True).update(customer=party)
    return party, linked
