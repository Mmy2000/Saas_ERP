from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Karat
from apps.iam.authz import permission_required
from apps.ledger.models import Account, Commodity
from apps.ledger.selectors import party_balance
from apps.ledger.statements import party_statement
from apps.org.models import Branch, TenantProfile
from apps.parties.models import BarcodeWeightRule, Gender, PartyKind, PartyRoleType
from apps.parties.selectors import parties_with_role, search

ROLES = {
    PartyRoleType.CUSTOMER: {
        "title": _("Customers"),
        "subtitle": _("People and companies you sell to."),
        "empty": _("No customers yet"),
        "new_title": _("New customer"),
        "edit_title": _("Edit customer"),
        "list_url": "customers", "new_url": "customer-new", "edit_url": "customer-edit",
        "api": "customer", "account_role": "customers",
        "label": _("Customer"), "statement_url": "customer-statement",
        "sign_hint": _("Positive: the customer owes you."),
        "settle_kind": "receipt", "settle_label": _("Receive payment"),
        "actions": [("repair-new", "customer", _("Take in a repair"), "wrench",
                     "repairs.order.create")],
        # Other accounts that hold money for this party, shown beside the main balance.
        "extra": [{
            "account_role": "customer_deposits", "label": _("Deposits held"),
            "title": _("Deposits held for reservations"),
            "hint": _("Negative: money you hold for the customer until the reservation is "
                      "completed or cancelled."),
            "url": "reservations",
        }],
    },
    PartyRoleType.SUPPLIER: {
        "title": _("Suppliers"),
        "subtitle": _("Manufacturers and traders you buy from."),
        "empty": _("No suppliers yet"),
        "new_title": _("New supplier"),
        "edit_title": _("Edit supplier"),
        "list_url": "suppliers", "new_url": "supplier-new", "edit_url": "supplier-edit",
        "api": "supplier", "account_role": "suppliers",
        "label": _("Supplier"), "statement_url": "supplier-statement",
        "sign_hint": _("Negative: you owe the supplier."),
        "settle_kind": "payment", "settle_label": _("Pay supplier"),
    },
    PartyRoleType.TRADE_ACCOUNT: {
        "title": _("Trade accounts"),
        "subtitle": _("Shops and traders you sell to wholesale, by weight."),
        "empty": _("No trade accounts yet"),
        "new_title": _("New trade account"),
        "edit_title": _("Edit trade account"),
        "list_url": "trade-accounts", "new_url": "trade-account-new",
        "edit_url": "trade-account-edit",
        "api": "trade-account", "account_role": "trade_accounts",
        "label": _("Trade account"), "statement_url": "trade-account-statement",
        "actions": [("trade-sale-new", "account", _("Sell wholesale"), "scale",
                     "sales.trade.create"),
                    ("invoice-new", "from=trade_account&party", _("Buy from them"), "receipt",
                     "purchasing.invoice.create")],
        "sign_hint": _("Positive: the trader owes you."),
        "settle_kind": "receipt", "settle_label": _("Receive payment"),
    },
    PartyRoleType.WORKSHOP: {
        "title": _("Workshops"),
        "subtitle": _("Workshops that make or repair goods from your gold."),
        "empty": _("No workshops yet"),
        "new_title": _("New workshop"),
        "edit_title": _("Edit workshop"),
        "list_url": "workshops", "new_url": "workshop-new", "edit_url": "workshop-edit",
        "api": "workshop", "account_role": "workshops",
        "label": _("Workshop"), "statement_url": "workshop-statement",
        "sign_hint": _("Gold: positive is your gold held by the workshop. "
                       "Money: negative is labour you owe them."),
        "settle_kind": "payment", "settle_label": _("Pay workshop"),
        "actions": [("work-order-new", "workshop", _("Send gold"), "send",
                     "manufacturing.order.issue")],
    },
}
PROFILES = {PartyRoleType.CUSTOMER: "customer_profile", PartyRoleType.SUPPLIER: "supplier_profile"}


def _list(request, role):
    config = ROLES[role]
    term = request.GET.get("q", "").strip()
    status = request.GET.get("status", "active")
    queryset = search(parties_with_role(role), term).order_by("-code")
    if status in ("active", "inactive"):
        queryset = queryset.filter(is_active=status == "active")
    page = Paginator(queryset, 25).get_page(request.GET.get("page"))
    return render(request, "parties/list.html", {
        "role": role, "config": config, "page": page, "term": term, "status": status,
    })


def _form(request, role, pk=None):
    config = ROLES[role]
    party = None
    if pk is not None:
        queryset = parties_with_role(role)
        if role in PROFILES:
            queryset = queryset.select_related(PROFILES[role])
        party = queryset.filter(pk=pk).first()
        if party is None:
            raise Http404
    api_base = reverse(f"{config['api']}-list")
    balances, extras = [], []
    if party is not None:
        balances = _balances(party, config["account_role"])
        for extra in config.get("extra", []):
            rows = _balances(party, extra["account_role"])
            if rows:
                extras.append({**extra, "rows": rows,
                               "link": f"{reverse(extra['url'])}?customer={party.pk}"})
    return render(request, "parties/form.html", {
        "balances": balances, "extras": extras,
        "role": role, "config": config, "party": party,
        "profile": getattr(party, PROFILES[role], None) if party and role in PROFILES else None,
        "endpoint": reverse(f"{config['api']}-detail", args=[pk]) if party else api_base,
        "method": "PATCH" if party else "POST",
        "branches": Branch.objects.filter(is_active=True),
        "kinds": PartyKind.choices, "genders": Gender.choices,
        "karats": Karat.objects.filter(is_active=True).select_related("metal"),
        "barcode_rules": BarcodeWeightRule.choices,
        "list_url": reverse(config["list_url"]),
    })


@permission_required("parties.customer.view")
def customers(request):
    return _list(request, PartyRoleType.CUSTOMER)


@permission_required("parties.customer.create")
def customer_new(request):
    return _form(request, PartyRoleType.CUSTOMER)


@permission_required("parties.customer.edit")
def customer_edit(request, pk):
    return _form(request, PartyRoleType.CUSTOMER, pk)


@permission_required("parties.supplier.view")
def suppliers(request):
    return _list(request, PartyRoleType.SUPPLIER)


@permission_required("parties.supplier.create")
def supplier_new(request):
    return _form(request, PartyRoleType.SUPPLIER)


@permission_required("parties.supplier.edit")
def supplier_edit(request, pk):
    return _form(request, PartyRoleType.SUPPLIER, pk)


def _statement(request, role, pk):
    config = ROLES[role]
    party = parties_with_role(role).filter(pk=pk).first()
    if party is None:
        raise Http404
    date_from = _parse_date(request.GET.get("from"))
    date_to = _parse_date(request.GET.get("to"))
    account = Account.objects.filter(role=config["account_role"]).first()
    sections = party_statement(party, account, date_from, date_to) if account else []
    extras = []
    for extra in config.get("extra", []):
        extra_account = Account.objects.filter(role=extra["account_role"]).first()
        extra_sections = (party_statement(party, extra_account, date_from, date_to)
                          if extra_account else [])
        if extra_sections:
            extras.append({**extra, "sections": extra_sections})
    if request.GET.get("format") == "pdf":
        from apps.printing.pdf import pdf_page

        return pdf_page(request, "printing/statement_sheet.html", {
            "who_name": party.name, "who_line": f"{config['label']} · {party.code}",
            "sections": sections, "extras": extras, "hint": config["sign_hint"],
            "date_from": date_from, "date_to": date_to,
        }, title=_("Statement of account"), filename=_statement_filename(party.name, date_to))
    return render(request, "parties/statement.html", {
        "role": role, "config": config, "party": party, "sections": sections,
        "extras": extras,
        "date_from": date_from, "date_to": date_to, "profile": TenantProfile.objects.first(),
        "edit_url": reverse(config["edit_url"], args=[party.pk]),
    })


def _statement_filename(name: str, date_to) -> str:
    from django.utils import timezone

    return f"{_('Statement')} {name} {(date_to or timezone.localdate()).isoformat()}.pdf"


def _balances(party, account_role) -> list[dict]:
    """The party's non-zero balances on the account with this role, per commodity."""
    account = Account.objects.filter(role=account_role).first()
    amounts = party_balance(party, account=account) if account else {}
    return [{"commodity": commodity, "amount": amounts[commodity.code]}
            for commodity in Commodity.objects.filter(code__in=amounts).select_related("metal")]


def _parse_date(value):
    from datetime import date

    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


@permission_required("parties.customer.view")
def customer_statement(request, pk):
    return _statement(request, PartyRoleType.CUSTOMER, pk)


@permission_required("parties.supplier.view")
def supplier_statement(request, pk):
    return _statement(request, PartyRoleType.SUPPLIER, pk)


@permission_required("parties.trade_account.view")
def trade_accounts(request):
    return _list(request, PartyRoleType.TRADE_ACCOUNT)


@permission_required("parties.trade_account.create")
def trade_account_new(request):
    return _form(request, PartyRoleType.TRADE_ACCOUNT)


@permission_required("parties.trade_account.edit")
def trade_account_edit(request, pk):
    return _form(request, PartyRoleType.TRADE_ACCOUNT, pk)


@permission_required("parties.trade_account.view")
def trade_account_statement(request, pk):
    return _statement(request, PartyRoleType.TRADE_ACCOUNT, pk)


@permission_required("parties.workshop.view")
def workshops(request):
    return _list(request, PartyRoleType.WORKSHOP)


@permission_required("parties.workshop.create")
def workshop_new(request):
    return _form(request, PartyRoleType.WORKSHOP)


@permission_required("parties.workshop.edit")
def workshop_edit(request, pk):
    return _form(request, PartyRoleType.WORKSHOP, pk)


@permission_required("parties.workshop.view")
def workshop_statement(request, pk):
    return _statement(request, PartyRoleType.WORKSHOP, pk)
