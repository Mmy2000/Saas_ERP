from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Currency, Karat
from apps.core.errors import DomainError, ValidationError
from apps.core.models import DocStatus
from apps.iam.authz import permission_required
from apps.inventory.models import LotBalance
from apps.inventory.services import SCRAP_CATEGORY_CODE
from apps.org.models import Branch, TenantProfile
from apps.pricing.selectors import current_price_board, functional_currency
from apps.sales.models import Reservation, SalesInvoice, SalesReturn
from apps.sales.reservations import quote_completion
from apps.sales.returns import returned_line_ids
from apps.sales.services import DISCOUNT_LIMIT
from apps.treasury.selectors import holder_choices


def _visible(request):
    invoices = SalesInvoice.objects.select_related("customer", "branch", "sold_by")
    branches = request.actor.branch_ids("sales.invoice.view")
    return invoices if branches is None else invoices.filter(branch_id__in=branches)


@permission_required("sales.invoice.view")
def invoices(request):
    status = request.GET.get("status", "")
    queryset = _visible(request)
    if status in DocStatus.values:
        queryset = queryset.filter(status=status)
    page = Paginator(queryset.order_by("-business_date", "-id"), 25).get_page(
        request.GET.get("page"))
    return render(request, "sales/invoices.html", {
        "page": page, "status": status, "statuses": DocStatus.choices,
        "home_currency": functional_currency(),
    })


@permission_required("sales.invoice.create")
def new_sale(request):
    allowed = request.actor.branch_ids("sales.invoice.create")
    branches = Branch.objects.filter(is_active=True).order_by("code")
    if allowed is not None:
        branches = branches.filter(pk__in=allowed)
    home = functional_currency()
    return render(request, "sales/pos.html", {
        "branches": branches,
        "default_branch": request.membership.default_branch_id,
        "board": current_price_board(),
        "home_currency": home,
        "foreign_currencies": Currency.objects.filter(is_active=True).exclude(code=home),
        "karats": Karat.objects.filter(is_active=True, metal__code="gold")
        .select_related("metal").order_by("-code"),
        "max_discount_pct": request.actor.limit(DISCOUNT_LIMIT) * 100,
        "can_override_scrap": request.actor.can("sales.trade_in.override_price"),
        "quote_url": reverse("sales-quote"),
        "post_url": reverse("sales-invoice-list"),
        # Bulk gold that can be sold by weight (scrap is sold from its own screen).
        "lots": LotBalance.objects.filter(gross_weight_g__gt=0, lot__branch__in=branches,
                                          lot__karat__isnull=False)
        .exclude(lot__category__code=SCRAP_CATEGORY_CODE)
        .select_related("lot__category", "lot__karat").order_by("lot__category__code"),
        **holder_choices(allowed),
    })


@permission_required("sales.invoice.view")
def invoice_detail(request, pk):
    invoice = _visible(request).select_related("journal_entry", "price_board").filter(pk=pk).first()
    if invoice is None:
        raise Http404
    returned = returned_line_ids(invoice)
    return render(request, "sales/receipt.html", {
        "invoice": invoice,
        "returned": returned,
        "returns": invoice.returns.order_by("id"),
        "returnable": invoice.status == DocStatus.POSTED
        and invoice.lines.exclude(pk__in=returned).exists(),
        "lines": invoice.lines.select_related("item", "category", "karat__metal"),
        "trade_ins": invoice.trade_ins.select_related("karat__metal"),
        "payments": invoice.payments.select_related("currency"),
        "profile": TenantProfile.objects.first(),
        "home_currency": functional_currency(),
        "endpoint": reverse("sales-invoice-detail", args=[invoice.pk]),
    })


@permission_required("sales.invoice.view")
def return_detail(request, pk):
    branches = request.actor.branch_ids("sales.invoice.view")
    returns = SalesReturn.objects.select_related("original_invoice", "branch", "customer",
                                                 "journal_entry", "posted_by")
    if branches is not None:
        returns = returns.filter(branch_id__in=branches)
    sales_return = returns.filter(pk=pk).first()
    if sales_return is None:
        raise Http404
    return render(request, "sales/return.html", {
        "sales_return": sales_return,
        "lines": sales_return.lines.select_related("original_line__item",
                                                   "original_line__category",
                                                   "original_line__karat__metal"),
        "profile": TenantProfile.objects.first(),
        "home_currency": functional_currency(),
        "endpoint": reverse("sales-return-detail", args=[sales_return.pk]),
    })


# --- reservations -----------------------------------------------------------------------------

def _visible_reservations(request):
    reservations = Reservation.objects.select_related("customer", "branch", "sale")
    branches = request.actor.branch_ids("sales.reservation.view")
    return reservations if branches is None else reservations.filter(branch_id__in=branches)


@permission_required("sales.reservation.view")
def reservation_list(request):
    state = request.GET.get("state", "open")
    queryset = _visible_reservations(request)
    today = timezone.localdate()
    if state in ("open", "overdue"):
        queryset = queryset.filter(status=DocStatus.POSTED, sale__isnull=True)
        if state == "overdue":
            queryset = queryset.filter(expires_on__lt=today)
    elif state == "completed":
        queryset = queryset.filter(sale__isnull=False)
    elif state == "cancelled":
        queryset = queryset.filter(status=DocStatus.VOIDED)
    customer = request.GET.get("customer", "")
    if customer.isdigit():
        queryset = queryset.filter(customer_id=int(customer))
    page = Paginator(queryset.order_by("-business_date", "-id"), 25).get_page(
        request.GET.get("page"))
    return render(request, "sales/reservations.html", {
        "page": page, "state": state, "home_currency": functional_currency(),
        "customer": customer if customer.isdigit() else "",
    })


@permission_required("sales.reservation.create")
def reservation_new(request):
    allowed = request.actor.branch_ids("sales.reservation.create")
    branches = Branch.objects.filter(is_active=True).order_by("code")
    if allowed is not None:
        branches = branches.filter(pk__in=allowed)
    return render(request, "sales/reservation_form.html", {
        "branches": branches, "default_branch": request.membership.default_branch_id,
        "currencies": Currency.objects.filter(is_active=True),
        "home_currency": functional_currency(), "board": current_price_board(),
        "quote_url": reverse("sales-quote"), "post_url": reverse("sales-reservation-list"),
        **holder_choices(allowed),
    })


@permission_required("sales.reservation.view")
def reservation_detail(request, pk):
    reservation = _visible_reservations(request).select_related("price_board").filter(
        pk=pk).first()
    if reservation is None:
        raise Http404
    completion = None
    if reservation.state == "open":
        try:
            _lines, _payments, applied, probe = quote_completion(reservation, actor=request.actor)
            completion = {"total": probe.total, "applied": applied,
                          "rest": probe.due - applied,
                          "leftover": reservation.deposit_amount - applied}
        except (DomainError, ValidationError) as exc:
            completion = {"problem": exc.message}
    allowed = request.actor.branch_ids("sales.reservation.view")
    return render(request, "sales/reservation.html", {
        "reservation": reservation, "completion": completion,
        "lines": reservation.lines.select_related("item__category", "item__karat__metal"),
        "deposits": reservation.deposits.select_related("currency", "cash_box", "bank_account",
                                                        "terminal"),
        "profile": TenantProfile.objects.first(), "home_currency": functional_currency(),
        "currencies": Currency.objects.filter(is_active=True),
        "endpoint": reverse("sales-reservation-detail", args=[reservation.pk]),
        **holder_choices(allowed),
    })
