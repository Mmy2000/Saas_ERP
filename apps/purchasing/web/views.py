from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Currency, ItemCategory, Karat
from apps.core.models import DocStatus
from apps.iam.authz import permission_required
from apps.org.models import Branch
from apps.pricing.selectors import functional_currency
from apps.purchasing.models import SupplierInvoice, seller_from_query
from apps.purchasing.services import totals


def _visible(request):
    invoices = SupplierInvoice.objects.select_related("supplier", "currency", "branch")
    branches = request.actor.branch_ids("purchasing.invoice.view")
    return invoices if branches is None else invoices.filter(branch_id__in=branches)


@permission_required("purchasing.invoice.view")
def invoices(request):
    status = request.GET.get("status", "")
    queryset = _visible(request).prefetch_related("lines")
    if status in DocStatus.values:
        queryset = queryset.filter(status=status)
    page = Paginator(queryset.order_by("-business_date", "-id"), 25).get_page(
        request.GET.get("page"))
    for invoice in page.object_list:
        lines = invoice.lines.all()
        invoice.weight = sum(line.gross_weight_g for line in lines)
        invoice.making = sum(line.making_cost_amount for line in lines)
    return render(request, "purchasing/invoices.html", {
        "page": page, "status": status, "statuses": DocStatus.choices,
    })


def _form(request, invoice=None):
    allowed = request.actor.branch_ids("purchasing.invoice.create")
    branches = Branch.objects.filter(is_active=True).order_by("code")
    if allowed is not None:
        branches = branches.filter(pk__in=allowed)
    initial = None
    if invoice is not None:
        initial = [{
            "category": line.category_id, "karat": line.karat_id, "qty": line.qty,
            "gross_weight_g": str(line.gross_weight_g),
            "piece_weights": " ".join(str(p.gross_weight_g) for p in line.pieces.all()),
            "making_cost_rate": str(line.making_cost_rate),
            "list_making_rate": str(line.list_making_rate), "note": line.note,
        } for line in invoice.lines.prefetch_related("pieces")]
    seller_role, seller = ((invoice.seller_role, invoice.supplier) if invoice
                           else seller_from_query(request.GET))
    return render(request, "purchasing/invoice_form.html", {
        "invoice": invoice, "seller_role": seller_role, "seller": seller,
        "branches": branches,
        "default_branch": invoice.branch_id if invoice else request.membership.default_branch_id,
        "today": timezone.localdate(),
        "currencies": Currency.objects.filter(is_active=True),
        "home_currency": functional_currency(),
        "categories": ItemCategory.objects.filter(is_active=True).order_by("code"),
        "karats": Karat.objects.filter(is_active=True).select_related("metal"),
        "initial_lines": initial or [],
        "endpoint": (reverse("supplier-invoice-detail", args=[invoice.pk]) if invoice
                     else reverse("supplier-invoice-list")),
        "method": "PUT" if invoice else "POST",
    })


@permission_required("purchasing.invoice.create")
def invoice_new(request):
    return _form(request)


@permission_required("purchasing.invoice.create")
def invoice_edit(request, pk):
    invoice = _visible(request).filter(pk=pk).first()
    if invoice is None:
        raise Http404
    if not invoice.is_draft:
        return redirect("invoice-detail", pk=pk)
    return _form(request, invoice)


@permission_required("purchasing.invoice.view")
def invoice_detail(request, pk):
    invoice = (_visible(request).select_related("journal_entry", "posted_by", "voided_by")
               .filter(pk=pk).first())
    if invoice is None:
        raise Http404
    lines = invoice.lines.select_related("category", "karat__metal").prefetch_related(
        "pieces__item")
    return render(request, "purchasing/invoice_detail.html", {
        "invoice": invoice, "lines": lines, "totals": totals(invoice),
        "endpoint": reverse("supplier-invoice-detail", args=[invoice.pk]),
        "home_currency": functional_currency(),
    })
