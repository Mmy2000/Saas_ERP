from datetime import date

from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse

from apps.catalog.models import Currency
from apps.iam.authz import permission_required
from apps.ledger.statements import account_statement
from apps.org.models import Branch, TenantProfile
from apps.pricing.selectors import functional_currency

from .holders import HOLDER_MODELS, MANAGE
from .models import TreasuryDocument, TreasuryKind
from .selectors import (
    cash_totals,
    holder_rows,
    in_transit,
    visible_banks,
    visible_boxes,
    visible_terminals,
)
from .services import PERMISSIONS

HOLDER_API = {"box": "cash-box", "bank": "bank-account", "terminal": "terminal"}


def _scope(request):
    return request.actor.branch_ids("treasury.view")


def _branches(request, permission="treasury.view"):
    allowed = request.actor.branch_ids(permission)
    branches = Branch.objects.filter(is_active=True).order_by("code")
    return branches if allowed is None else branches.filter(pk__in=allowed)


@permission_required("treasury.view")
def overview(request):
    scope = _scope(request)
    rows = holder_rows(scope)
    incoming, outgoing = in_transit(scope)
    by_branch: dict = {}
    for row in rows["box"]:
        by_branch.setdefault(row.holder.branch, []).append(row)
    return render(request, "treasury/overview.html", {
        "boxes_by_branch": sorted(by_branch.items(), key=lambda pair: pair[0].code),
        "banks": rows["bank"], "terminals": rows["terminal"],
        "cash_totals": cash_totals(rows["box"]),
        "incoming": incoming, "outgoing": outgoing,
        "home_currency": functional_currency(),
    })


def _visible_documents(request):
    documents = TreasuryDocument.objects.select_related(
        "branch", "to_branch", "currency", "dest_currency", "source_box", "source_bank",
        "source_terminal", "dest_box", "dest_bank")
    scope = _scope(request)
    if scope is None:
        return documents
    return documents.filter(branch_id__in=scope) | documents.filter(to_branch_id__in=scope)


@permission_required("treasury.view")
def documents(request):
    kind = request.GET.get("kind", "")
    queryset = _visible_documents(request)
    if kind in TreasuryKind.values:
        queryset = queryset.filter(kind=kind)
    page = Paginator(queryset.order_by("-business_date", "-id"), 25).get_page(
        request.GET.get("page"))
    return render(request, "treasury/documents.html", {
        "page": page, "kind": kind, "kinds": TreasuryKind.choices,
    })


@permission_required("treasury.view")
def document_new(request):
    actor = request.actor
    kinds = [(value, label) for value, label in TreasuryKind.choices
             if actor.can(PERMISSIONS[value])]
    if not kinds:
        raise Http404
    kind = request.GET.get("kind") if request.GET.get("kind") in dict(kinds) else kinds[0][0]
    boxes = visible_boxes(None).filter(is_active=True).order_by("branch__code", "currency__code")
    terminals = holder_rows(_scope(request))["terminal"]
    return render(request, "treasury/document_form.html", {
        "kinds": kinds, "kind": kind,
        "source_boxes": boxes.filter(branch__in=_branches(request, PERMISSIONS[kind])),
        "dest_boxes": boxes,
        "banks": visible_banks(None).filter(is_active=True),
        "terminals": terminals,
        "branches": _branches(request, PERMISSIONS[kind]),
        "default_branch": request.membership.default_branch_id,
        "source": request.GET.get("source", ""),
    })


@permission_required("treasury.view")
def document_detail(request, pk):
    doc = _visible_documents(request).select_related("journal_entry", "receive_entry",
                                                     "posted_by", "received_by").filter(
        pk=pk).first()
    if doc is None:
        raise Http404
    return render(request, "treasury/document.html", {
        "doc": doc, "profile": TenantProfile.objects.first(),
        "home_currency": functional_currency(),
        "endpoint": reverse("treasury-doc-detail", args=[doc.pk]),
    })


def _holder(request, kind, pk):
    if kind not in HOLDER_MODELS:
        raise Http404
    scope = _scope(request)
    queryset = {"box": visible_boxes, "bank": visible_banks, "terminal": visible_terminals}[kind](
        scope)
    holder = queryset.filter(pk=pk).first()
    if holder is None:
        raise Http404
    return holder


def _holder_form(request, kind, holder=None):
    if kind not in HOLDER_MODELS:
        raise Http404
    api = HOLDER_API[kind]
    linked = set()
    if kind == "bank" and holder is not None and not holder.all_branches:
        linked = {link.branch_id for link in holder.branch_links.all()}
    return render(request, "treasury/holder_form.html", {
        "kind": kind, "holder": holder,
        "endpoint": (reverse(f"{api}-detail", args=[holder.pk]) if holder
                     else reverse(f"{api}-list")),
        "method": "PUT" if holder else "POST",
        "branches": _branches(request, MANAGE),
        "all_branches": Branch.objects.filter(is_active=True).order_by("code"),
        "linked": linked,
        "currencies": Currency.objects.filter(is_active=True),
        "home_currency": functional_currency(),
        "banks": visible_banks(None).filter(is_active=True),
        "default_branch": request.membership.default_branch_id,
    })


@permission_required(MANAGE)
def holder_new(request, kind):
    return _holder_form(request, kind)


@permission_required(MANAGE)
def holder_edit(request, kind, pk):
    return _holder_form(request, kind, _holder(request, kind, pk))


def _date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


@permission_required("treasury.view")
def holder_statement(request, kind, pk):
    holder = _holder(request, kind, pk)
    date_from, date_to = _date(request.GET.get("from")), _date(request.GET.get("to"))
    return render(request, "treasury/statement.html", {
        "kind": kind, "holder": holder,
        "sections": account_statement(holder.account, date_from, date_to),
        "date_from": date_from, "date_to": date_to,
        "profile": TenantProfile.objects.first(),
    })
