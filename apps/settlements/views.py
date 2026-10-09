from django.db.models import Q, Sum
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Currency, Karat
from apps.core.listing import Choice, ListQuery, Total
from apps.core.models import DocStatus
from apps.iam.authz import permission_required
from apps.org.models import Branch, TenantProfile
from apps.parties.models import Party
from apps.pricing.selectors import fine_gram_value, functional_currency
from apps.treasury.selectors import holder_choices

from .models import (
    AdjustmentDirection,
    ConversionDirection,
    MoneyMethod,
    PartySide,
    Settlement,
    SettlementKind,
)
from .services import PERMISSIONS


def _visible(request):
    settlements = Settlement.objects.select_related("party", "currency", "karat__metal", "branch",
                                                    "counter_party")
    branches = request.actor.branch_ids("settlements.view")
    return settlements if branches is None else settlements.filter(branch_id__in=branches)


@permission_required("settlements.view")
def settlements(request):
    """Every receipt, payment, gold movement and settlement, searchable (Gweb's GHC28 / GHC31-33,
    GHSS39-40, the fixing reports GHSS24 / 32 / 38 / 39)."""
    filters = ListQuery(
        request, permission="settlements.view",
        search=("number", "party__name", "party__phone", "counter_party__name", "reference"),
        search_label=_("Number, name, phone or reference"),
        party=lambda qs, pk: qs.filter(Q(party_id=pk) | Q(counter_party_id=pk)),
        choices=[Choice("kind", _("Type"), [("", _("All"))] + list(SettlementKind.choices),
                        "kind"),
                 Choice("side", _("With"), [("", _("Everyone"))] + list(PartySide.choices),
                        apply=lambda qs, v: qs.filter(Q(side=v) | Q(counter_side=v))),
                 Choice("status", _("Status"), [("", _("All")), (DocStatus.POSTED, _("Posted")),
                                                (DocStatus.VOIDED, _("Cancelled"))], "status")])
    found = filters.apply(_visible(request))
    posted = found.filter(status=DocStatus.POSTED)
    home = functional_currency()
    money = dict(posted.filter(kind__in=(SettlementKind.RECEIPT, SettlementKind.PAYMENT))
                 .values_list("kind").annotate(total=Sum("functional_amount")).order_by())
    gold = dict(posted.filter(kind__in=(SettlementKind.METAL_IN, SettlementKind.METAL_OUT))
                .values_list("kind").annotate(total=Sum("fine_weight_g")).order_by())
    totals = [Total(_("Received"), money.get(SettlementKind.RECEIPT, 0), unit=home),
              Total(_("Paid"), money.get(SettlementKind.PAYMENT, 0), unit=home)]
    if gold:
        totals += [Total(_("Gold in (fine)"), gold.get(SettlementKind.METAL_IN, 0), 3, _("g")),
                   Total(_("Gold out (fine)"), gold.get(SettlementKind.METAL_OUT, 0), 3,
                         _("g"))]
    return render(request, "settlements/list.html", {
        "page": filters.page(found.order_by("-business_date", "-id")), "filters": filters,
        "home_currency": home, "totals": totals,
    })


PARTY_PAGES = {PartySide.PARTNER: "partner-edit",
               PartySide.CUSTOMER: "customer-edit", PartySide.SUPPLIER: "supplier-edit",
               PartySide.TRADE_ACCOUNT: "trade-account-edit", PartySide.WORKSHOP: "workshop-edit"}


def _sides(actor):
    """The kinds of account the form offers, with the list each one searches."""
    sides = [("customer", _("Customer"), "customer-list"),
             ("supplier", _("Supplier"), "supplier-list"),
             ("trade_account", _("Trade account"), "trade-account-list"),
             ("workshop", _("Workshop"), "workshop-list")]
    if actor.can("parties.partner.view"):
        sides.append(("partner", _("Partner"), "partner-list"))
    return [(value, label, reverse(url)) for value, label, url in sides]


@permission_required("settlements.view")
def new_settlement(request):
    actor = request.actor
    kinds = [(value, label) for value, label in SettlementKind.choices
             if actor.can(PERMISSIONS[value])]
    side = request.GET.get("side") if request.GET.get("side") in PartySide.values else "customer"
    party = None
    if (request.GET.get("party") or "").isdigit():
        party = Party.objects.filter(pk=int(request.GET["party"]), roles__role=side).first()
    allowed = actor.branch_ids("settlements.view")
    branches = Branch.objects.filter(is_active=True).order_by("code")
    if allowed is not None:
        branches = branches.filter(pk__in=allowed)
    return render(request, "settlements/form.html", {
        "kinds": kinds,
        "kind": request.GET.get("kind") or (kinds[0][0] if kinds else ""),
        "side": side, "party": party,
        "branches": branches, "default_branch": request.membership.default_branch_id,
        "methods": MoneyMethod.choices, "directions": ConversionDirection.choices,
        "adjustments": AdjustmentDirection.choices,
        "sides": _sides(actor),
        "currencies": Currency.objects.filter(is_active=True),
        "home_currency": functional_currency(),
        "karats": Karat.objects.filter(is_active=True).select_related("metal"),
        "gold_value": fine_gram_value("gold"),
        "can_partners": actor.can("parties.partner.view"),
        **holder_choices(allowed),
    })


@permission_required("settlements.view")
def settlement_detail(request, pk):
    settlement = _visible(request).select_related("journal_entry", "posted_by").filter(
        pk=pk).first()
    if settlement is None:
        raise Http404
    return render(request, "settlements/detail.html", {
        "settlement": settlement, "profile": TenantProfile.objects.first(),
        "home_currency": functional_currency(),
        "endpoint": reverse("settlement-detail", args=[settlement.pk]),
        "party_url": reverse(PARTY_PAGES[settlement.side], args=[settlement.party_id]),
        "counter_url": (reverse(PARTY_PAGES[settlement.counter_side],
                                args=[settlement.counter_party_id])
                        if settlement.counter_party_id else ""),
    })
