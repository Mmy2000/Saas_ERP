from datetime import date, timedelta
from decimal import Decimal

from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.models import Currency
from apps.core.documents import document_url
from apps.core.models import DocStatus
from apps.iam.authz import permission_required
from apps.ledger.statements import account_statement
from apps.org.models import Branch, TenantProfile
from apps.pricing.selectors import functional_currency
from apps.settlements.models import PartySide

from . import reconciliation
from .counts import DENOMINATIONS
from .holders import HOLDER_MODELS, MANAGE, balance
from .models import (
    OPEN_CHEQUE,
    CashCount,
    Cheque,
    ChequeDirection,
    ChequeStatus,
    TreasuryDocument,
    TreasuryKind,
)
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
    sections = account_statement(holder.account, date_from, date_to)
    if request.GET.get("format") == "pdf":
        from apps.printing.pdf import pdf_page

        kinds = {"box": _("Cash box"), "bank": _("Bank account"), "terminal": _("Card terminal")}
        hint = (_("Debit: card payments taken. Credit: settled by the bank.") if kind == "terminal"
                else _("Debit: money in. Credit: money out."))
        day = (date_to or timezone.localdate()).isoformat()
        return pdf_page(request, "printing/statement_sheet.html", {
            "who_name": holder.label, "who_line": f"{kinds.get(kind, '')} · {holder.account.code}",
            "sections": sections, "hint": hint, "date_from": date_from, "date_to": date_to,
        }, title=_("Statement of account"), filename=f"{_('Statement')} {holder.label} {day}.pdf")
    return render(request, "treasury/statement.html", {
        "kind": kind, "holder": holder,
        "sections": sections,
        "date_from": date_from, "date_to": date_to,
        "profile": TenantProfile.objects.first(),
    })


# --- cheques -------------------------------------------------------------------------------------

CHEQUE_FILTERS = ("open", "due", "cleared", "bounced", "other", "cancelled", "all")


def _visible_cheques(request):
    cheques = Cheque.objects.select_related("party", "bank_account", "branch", "endorsed_to")
    scope = request.actor.branch_ids("treasury.cheque.view")
    return cheques if scope is None else cheques.filter(branch_id__in=scope)


@permission_required("treasury.cheque.view")
def cheques(request):
    direction = (request.GET.get("direction") if request.GET.get("direction")
                 in ChequeDirection.values else ChequeDirection.RECEIVED)
    state = request.GET.get("state") if request.GET.get("state") in CHEQUE_FILTERS else "open"
    today = timezone.localdate()
    visible = _visible_cheques(request)
    queryset = visible.filter(direction=direction)
    posted = queryset.filter(status=DocStatus.POSTED)
    if state == "open":
        queryset = posted.filter(state__in=OPEN_CHEQUE)
    elif state == "due":
        queryset = posted.filter(state__in=OPEN_CHEQUE, due_date__lte=today)
    elif state == "cleared":
        queryset = posted.filter(state=ChequeStatus.CLEARED)
    elif state == "bounced":
        queryset = posted.filter(state=ChequeStatus.BOUNCED)
    elif state == "other":
        queryset = posted.filter(state__in=(ChequeStatus.RETURNED, ChequeStatus.ENDORSED))
    elif state == "cancelled":
        queryset = queryset.filter(status=DocStatus.VOIDED)
    term = request.GET.get("q", "").strip()
    if term:
        queryset = queryset.filter(Q(cheque_number__icontains=term) | Q(number__icontains=term)
                                   | Q(party__name__icontains=term))
    ordering = ("due_date", "id") if state in ("open", "due") else ("-business_date", "-id")
    page = Paginator(queryset.order_by(*ordering), 25).get_page(request.GET.get("page"))

    open_cheques = visible.filter(status=DocStatus.POSTED, state__in=OPEN_CHEQUE)
    week = today + timedelta(days=7)

    def total(queryset):
        return queryset.aggregate(n=Count("id"), a=Sum("amount"))

    return render(request, "treasury/cheques.html", {
        "page": page, "direction": direction, "state": state, "term": term, "today": today,
        "cards": {
            "in_hand": total(open_cheques.filter(state=ChequeStatus.IN_HAND)),
            "deposited": total(open_cheques.filter(state=ChequeStatus.DEPOSITED)),
            "outstanding": total(open_cheques.filter(state=ChequeStatus.OUTSTANDING)),
            "due_week": total(open_cheques.filter(due_date__lte=week)),
        },
        "home_currency": functional_currency(),
    })


@permission_required("treasury.cheque.manage")
def cheque_new(request):
    direction = (request.GET.get("direction") if request.GET.get("direction")
                 in ChequeDirection.values else ChequeDirection.RECEIVED)
    side = request.GET.get("side") if request.GET.get("side") in PartySide.values else (
        PartySide.CUSTOMER if direction == ChequeDirection.RECEIVED else PartySide.SUPPLIER)
    return render(request, "treasury/cheque_form.html", {
        "direction": direction, "side": side, "party_sides": PartySide.choices,
        "branches": _branches(request, "treasury.cheque.manage"),
        "default_branch": request.membership.default_branch_id,
        "banks": visible_banks(None).filter(is_active=True, currency__code=functional_currency()),
        "home_currency": functional_currency(), "today": timezone.localdate(),
    })


@permission_required("treasury.cheque.view")
def cheque_detail(request, pk):
    cheque = _visible_cheques(request).select_related(
        "journal_entry", "settle_entry", "posted_by", "currency").filter(pk=pk).first()
    if cheque is None:
        raise Http404
    return render(request, "treasury/cheque.html", {
        "cheque": cheque, "profile": TenantProfile.objects.first(),
        "endpoint": reverse("cheque-api-detail", args=[cheque.pk]),
        "banks": visible_banks(None).filter(is_active=True, currency=cheque.currency),
        "today": timezone.localdate(), "party_sides": PartySide.choices,
    })


# --- bank reconciliation -------------------------------------------------------------------------

@permission_required("treasury.reconcile")
def reconcile(request, pk):
    bank = visible_banks(None).filter(pk=pk).first()
    if bank is None:
        raise Http404
    chosen = request.GET.get("id", "")
    item = (bank.reconciliations.filter(pk=int(chosen)).first() if chosen.isdigit()
            else reconciliation.open_reconciliation(bank))
    context = {
        "bank": bank, "item": item,
        "history": bank.reconciliations.filter(completed_at__isnull=False)
        .select_related("completed_by")[:12],
        "latest": reconciliation.last_completed(bank),
        "today": timezone.localdate(),
    }
    if item is not None:
        ticked = set(item.lines.values_list("journal_line_id", flat=True))
        lines = (item.lines.select_related("journal_line__entry").order_by(
            "journal_line__business_date", "journal_line_id") if item.is_completed
            else reconciliation.candidates(item))
        rows = []
        for line in lines:
            journal_line = line.journal_line if item.is_completed else line
            rows.append({"line": journal_line, "ticked": journal_line.pk in ticked,
                         "url": document_url(journal_line.entry.source_type,
                                             journal_line.entry.source_id)})
        context.update({"rows": rows, "figures": reconciliation.summary(item),
                        "endpoint": reverse("reconciliation-detail", args=[item.pk])})
    return render(request, "treasury/reconcile.html", context)


# --- cash counts ---------------------------------------------------------------------------------

@permission_required("treasury.view")
def cash_counts(request):
    scope = _scope(request)
    boxes = list(visible_boxes(scope).filter(is_active=True).select_related("branch", "currency")
                 .order_by("branch__code", "currency__code", "-is_default", "name"))
    last = {}
    for count in (CashCount.objects.filter(cash_box__in=boxes, status=DocStatus.POSTED)
                  .order_by("cash_box_id", "-posted_at", "-id")):
        last.setdefault(count.cash_box_id, count)
    rows = [{"box": box, "held": balance(box.account_id)[0], "last": last.get(box.pk)}
            for box in boxes]
    history = CashCount.objects.select_related("cash_box__currency", "branch", "posted_by")
    if scope is not None:
        history = history.filter(branch_id__in=scope)
    chosen = request.GET.get("box", "")
    if chosen.isdigit():
        history = history.filter(cash_box_id=int(chosen))
    page = Paginator(history.order_by("-posted_at", "-id"), 25).get_page(request.GET.get("page"))
    return render(request, "treasury/counts.html", {
        "rows": rows, "page": page, "box": chosen if chosen.isdigit() else "",
        "today": timezone.localdate(),
    })


@permission_required("treasury.count.create")
def cash_count_new(request):
    allowed = request.actor.branch_ids("treasury.count.create")
    boxes = visible_boxes(allowed).filter(is_active=True).select_related("branch", "currency")
    chosen = request.GET.get("box", "")
    box = boxes.filter(pk=int(chosen)).first() if chosen.isdigit() else None
    if box is None:
        box = boxes.filter(branch_id=request.membership.default_branch_id,
                           is_default=True).first() or boxes.first()
    return render(request, "treasury/count_form.html", {
        "box": box, "boxes": boxes.order_by("branch__code", "currency__code"),
        "expected": balance(box.account_id)[0] if box else 0,
        "denominations": DENOMINATIONS.get(box.currency.code, ()) if box else (),
    })


@permission_required("treasury.view")
def cash_count(request, pk):
    count = (CashCount.objects.select_related("cash_box__currency", "branch", "posted_by",
                                              "journal_entry")
             .filter(pk=pk).first())
    scope = _scope(request)
    if count is None or (scope is not None and count.branch_id not in scope):
        raise Http404
    order = DENOMINATIONS.get(count.cash_box.currency.code, ())
    lines = [{"value": key, "pieces": count.denominations[key],
              "total": Decimal(key) * count.denominations[key]}
             for key in order if key in count.denominations]
    latest = (CashCount.objects.filter(cash_box=count.cash_box, status=DocStatus.POSTED)
              .order_by("-posted_at", "-id").first())
    return render(request, "treasury/count.html", {
        "count": count, "lines": lines, "is_latest": latest == count,
        "profile": TenantProfile.objects.first(),
        "endpoint": reverse("cash-count-detail", args=[count.pk]),
    })
