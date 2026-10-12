from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.urls import reverse

from apps.iam.authz import permission_required
from apps.org.models import Branch, TenantProfile

from .models import LineKind, OpeningBalance


def _branches(request, permission):
    scope = request.actor.branch_ids(permission)
    branches = Branch.objects.filter(is_active=True).order_by("code")
    return branches if scope is None else branches.filter(pk__in=scope)


def _visible(request):
    scope = request.actor.branch_ids("ledger.opening.view")
    docs = OpeningBalance.objects.select_related("branch", "posted_by")
    return docs if scope is None else docs.filter(branch_id__in=scope)


@permission_required("ledger.opening.view")
def openings(request):
    page = Paginator(_visible(request).order_by("-business_date", "-id"), 30).get_page(
        request.GET.get("page"))
    return render(request, "opening/list.html", {"page": page})


def _form_context(request):
    from apps.catalog.models import Currency, ItemCategory, Karat, Tracking
    from apps.parties.models import PartyRoleType
    from apps.pricing.selectors import functional_currency
    from apps.treasury.models import BankAccount, CashBox

    from .services import ROLE_ACCOUNTS

    branches = _branches(request, "ledger.opening.post")
    labels = dict(PartyRoleType.choices)
    return {
        "branches": branches,
        "roles": [(role, labels[role]) for role in ROLE_ACCOUNTS],
        "currencies": Currency.objects.filter(is_active=True).order_by("code"),
        "home_currency": functional_currency(),
        "karats": Karat.objects.filter(is_active=True).select_related("metal").order_by(
            "metal", "code"),
        "boxes": CashBox.objects.filter(is_active=True, branch__in=branches).select_related(
            "branch", "currency"),
        "banks": BankAccount.objects.filter(is_active=True).select_related("currency"),
        "all_branches": Branch.objects.filter(is_active=True).order_by("code"),
        "bulk_categories": ItemCategory.objects.filter(is_active=True, tracking=Tracking.BULK)
        .order_by("code"),
    }


@permission_required("ledger.opening.post")
def opening_new(request):
    return render(request, "opening/form.html", _form_context(request))


@permission_required("ledger.opening.post")
def opening_import(request):
    return render(request, "opening/import.html", {
        "branches": _branches(request, "ledger.opening.post")})


@permission_required("ledger.opening.post")
def import_template(request):
    from .imports import template_workbook

    response = HttpResponse(
        template_workbook(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = 'attachment; filename="opening-stock.xlsx"'
    return response


@permission_required("ledger.opening.view")
def opening(request, pk):
    from apps.pricing.selectors import functional_currency

    doc = _visible(request).select_related("journal_entry").filter(pk=pk).first()
    if doc is None:
        raise Http404
    lines = list(doc.lines.select_related("party", "cash_box__branch", "cash_box__currency",
                                          "bank_account", "currency", "karat__metal",
                                          "other_branch", "category", "item")
                 .order_by("kind", "id"))
    groups = {kind: [line for line in lines if line.kind == kind] for kind in LineKind.values}
    pieces = groups[LineKind.PIECE]
    return render(request, "opening/detail.html", {
        "doc": doc, "groups": groups, "kinds": dict(LineKind.choices),
        "pieces": pieces[:200], "piece_count": len(pieces),
        "piece_weight": sum(line.weight_g for line in pieces),
        "home_currency": functional_currency(), "profile": TenantProfile.objects.first(),
        "endpoint": reverse("opening-void", args=[doc.pk]),
    })
