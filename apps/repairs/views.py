from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Currency, Karat
from apps.core.models import DocStatus
from apps.iam.authz import permission_required
from apps.org.models import Branch, TenantProfile
from apps.parties.models import Party, PartyRoleType
from apps.pricing.selectors import functional_currency
from apps.treasury.selectors import holder_choices

from .models import RepairKind, RepairOrder

STATES = ("open", "ready", "overdue", "delivered", "cancelled", "all")


def _visible(request):
    orders = RepairOrder.objects.select_related("customer", "workshop", "branch")
    scope = request.actor.branch_ids("repairs.order.view")
    return orders if scope is None else orders.filter(branch_id__in=scope)


@permission_required("repairs.order.view")
def repairs(request):
    state = request.GET.get("state") if request.GET.get("state") in STATES else "open"
    orders = _visible(request)
    open_orders = Q(status=DocStatus.POSTED, delivered_at__isnull=True)
    if state == "open":
        orders = orders.filter(open_orders)
    elif state == "ready":
        orders = orders.filter(open_orders, ready_on__isnull=False)
    elif state == "overdue":
        orders = orders.filter(open_orders, promised_on__lt=timezone.localdate())
    elif state == "delivered":
        orders = orders.filter(delivered_at__isnull=False)
    elif state == "cancelled":
        orders = orders.filter(status=DocStatus.VOIDED)
    customer = request.GET.get("customer", "")
    if customer.isdigit():
        orders = orders.filter(customer_id=int(customer))
    term = request.GET.get("q", "").strip()
    if term:
        match = (Q(number__icontains=term) | Q(customer__name__icontains=term)
                 | Q(customer_name__icontains=term) | Q(customer_phone__icontains=term)
                 | Q(customer__phone__icontains=term) | Q(lines__description__icontains=term))
        if term.isdigit():
            match |= Q(bag_number=int(term))
        orders = orders.filter(match).distinct()
    page = Paginator(orders.order_by("-business_date", "-id"), 25).get_page(
        request.GET.get("page"))
    return render(request, "repairs/repairs.html", {
        "page": page, "state": state, "term": term,
        "customer": customer if customer.isdigit() else "",
        "home_currency": functional_currency(),
    })


def _payment_context(request, permission):
    allowed = request.actor.branch_ids(permission)
    return {"currencies": Currency.objects.filter(is_active=True),
            "home_currency": functional_currency(), **holder_choices(allowed)}


@permission_required("repairs.order.create")
def repair_new(request):
    allowed = request.actor.branch_ids("repairs.order.create")
    branches = Branch.objects.filter(is_active=True).order_by("code")
    if allowed is not None:
        branches = branches.filter(pk__in=allowed)
    customer_id = request.GET.get("customer", "")
    customer = (Party.objects.filter(pk=int(customer_id), roles__role=PartyRoleType.CUSTOMER,
                                     is_active=True).first() if customer_id.isdigit() else None)
    return render(request, "repairs/repair_form.html", {
        "branches": branches, "default_branch": request.membership.default_branch_id,
        "kinds": RepairKind.choices, "karats": Karat.objects.filter(is_active=True),
        "customer": customer, "today": timezone.localdate(),
        **_payment_context(request, "repairs.order.create"),
    })


@permission_required("repairs.order.view")
def repair(request, pk):
    order = _visible(request).select_related("delivery_entry", "labour_entry").filter(
        pk=pk).first()
    if order is None:
        raise Http404
    return render(request, "repairs/repair.html", {
        "order": order, "profile": TenantProfile.objects.first(),
        "lines": order.lines.select_related("karat"),
        "payments": order.payments.select_related("currency").order_by("id"),
        "endpoint": reverse("repair-api-detail", args=[order.pk]),
        "customer_url": (reverse("customer-edit", args=[order.customer_id])
                         if order.customer_id else None),
        **_payment_context(request, "repairs.order.view"),
    })
