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
def invoice_print(request, pk):
    """The invoice in the client's document design (Settings → Documents)."""
    from apps.printing.design_views import print_sales_invoice

    invoice = _visible(request).select_related("branch", "sold_by").filter(pk=pk).first()
    if invoice is None:
        raise Http404
    return print_sales_invoice(request, invoice)


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
        "orders_url": reverse("sales-reservation-orders-quote"),
        "order_categories": _order_categories(),
        "karats": Karat.objects.filter(is_active=True).select_related("metal").order_by(
            "metal", "-code"),
        **holder_choices(allowed),
    })


def _order_categories():
    from apps.sales.reservations import order_categories

    return order_categories().order_by("code")


def _with_stock(reservation):
    """The reservation's lines; an order (gold by weight, not a piece) says whether the branch
    now has enough of it to deliver."""
    from apps.inventory.models import LotBalance

    lines = list(reservation.lines.select_related("item__category", "item__karat__metal",
                                                  "category", "karat"))
    for line in lines:
        if line.item_id is None:
            balance = LotBalance.objects.filter(
                lot__category_id=line.category_id, lot__karat_id=line.karat_id,
                lot__branch_id=reservation.branch_id).first()
            line.in_stock = bool(balance and balance.gross_weight_g >= line.gross_weight_g
                                 and balance.qty >= line.qty)
    return lines

def _completion_code(request, reservation, total):
    """Whether completing needs the customer's code: paid now, or with the rest left on their
    account (the shop's rules may differ)."""
    from apps.sales import codes
    from apps.sales.models import PaymentTerms

    paid, on_account = (codes.need(total=total, payment_terms=terms,
                                   customer=reservation.customer, reservation=reservation)
                        for terms in (PaymentTerms.CASH, PaymentTerms.CREDIT))
    if not (paid.needed or on_account.needed):
        return None
    return {"paid": paid.needed, "on_account": on_account.needed,
            "reason": paid.reason, "reason_on_account": on_account.reason,
            "can_skip": request.actor.can("sales.code.skip", branch=reservation.branch)}


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
                          "leftover": reservation.deposit_amount - applied,
                          "code": _completion_code(request, reservation, probe.total)}
        except (DomainError, ValidationError) as exc:
            completion = {"problem": exc.message}
    allowed = request.actor.branch_ids("sales.reservation.view")
    return render(request, "sales/reservation.html", {
        "reservation": reservation, "completion": completion,
        "lines": _with_stock(reservation),
        "deposits": reservation.deposits.select_related("currency", "cash_box", "bank_account",
                                                        "terminal"),
        "profile": TenantProfile.objects.first(), "home_currency": functional_currency(),
        "currencies": Currency.objects.filter(is_active=True),
        "endpoint": reverse("sales-reservation-detail", args=[reservation.pk]),
        **holder_choices(allowed),
    })


@permission_required("sales.code.manage")
def sale_code_rules(request):
    from apps.messaging.codes import configured
    from apps.sales import codes
    from apps.sales.models import SaleCode

    recent = SaleCode.objects.select_related("customer", "branch", "invoice",
                                             "created_by").order_by("-created_at")[:30]
    now = timezone.now()
    rows = []
    for code in recent:
        if code.invoice_id:
            state = "used"
        elif code.verified_at:
            state = "verified"
        elif code.expires_at < now:
            state = "expired"
        else:
            state = "waiting"
        rows.append({"code": code, "phone": codes.masked(code.phone), "state": state})
    return render(request, "sales/code_rules.html", {
        "rules": codes.rules(), "can_send": configured(), "rows": rows,
        "home_currency": functional_currency(),
        "skipped": SalesInvoice.objects.filter(code_check="skipped").select_related(
            "customer", "branch", "posted_by").order_by("-posted_at")[:10],
    })
