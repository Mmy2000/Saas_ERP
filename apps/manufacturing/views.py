from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse

from apps.catalog.models import ItemCategory, Karat
from apps.core.models import DocStatus
from apps.hr.models import Employee
from apps.iam.authz import permission_required
from apps.inventory.models import LotBalance
from apps.inventory.services import scrap_category
from apps.org.models import Branch, TenantProfile
from apps.parties.models import Party, PartyRoleType
from apps.pricing.selectors import functional_currency

from .models import WorkOrder, WorkOrderKind


def _visible(request):
    orders = WorkOrder.objects.select_related("workshop", "craftsman", "branch")
    scope = request.actor.branch_ids("manufacturing.order.view")
    return orders if scope is None else orders.filter(branch_id__in=scope)


def _orders(request, in_house: bool):
    state = request.GET.get("state", "at_workshop")
    orders = _visible(request).filter(workshop__isnull=in_house)
    if state == "at_workshop":
        orders = orders.filter(status=DocStatus.POSTED, received_at__isnull=True)
    elif state == "received":
        orders = orders.filter(status=DocStatus.POSTED, received_at__isnull=False)
    elif state == "cancelled":
        orders = orders.filter(status=DocStatus.VOIDED)
    workshop = request.GET.get("workshop", "")
    if workshop.isdigit():
        orders = orders.filter(workshop_id=int(workshop))
    craftsman = request.GET.get("craftsman", "")
    if craftsman.isdigit():
        orders = orders.filter(craftsman_id=int(craftsman))
    term = request.GET.get("q", "").strip()
    if term:
        orders = orders.filter(Q(number__icontains=term) | Q(workshop__name__icontains=term)
                               | Q(craftsman__name__icontains=term))
    page = Paginator(orders.order_by("-business_date", "-id"), 25).get_page(
        request.GET.get("page"))
    return render(request, "manufacturing/work_orders.html", {
        "page": page, "state": state, "term": term, "in_house": in_house,
        "workshop": workshop if workshop.isdigit() else "",
        "craftsman": craftsman if craftsman.isdigit() else "",
        "home_currency": functional_currency(),
    })


@permission_required("manufacturing.order.view")
def work_orders(request):
    return _orders(request, in_house=False)


@permission_required("manufacturing.order.view")
def production(request):
    """In-house production orders: gold and scrap made into new goods in our own shop."""
    return _orders(request, in_house=True)


def _new(request, in_house: bool):
    allowed = request.actor.branch_ids("manufacturing.order.issue")
    branches = Branch.objects.filter(is_active=True).order_by("code")
    if allowed is not None:
        branches = branches.filter(pk__in=allowed)
    lots = (LotBalance.objects.filter(gross_weight_g__gt=0, lot__branch__in=branches,
                                      lot__karat__isnull=False)
            .select_related("lot__category", "lot__karat").order_by("lot__category__code"))
    workshop_id = request.GET.get("workshop", "")
    workshop = (Party.objects.filter(pk=int(workshop_id), roles__role=PartyRoleType.WORKSHOP,
                                     is_active=True).first() if workshop_id.isdigit() else None)
    return render(request, "manufacturing/work_order_form.html", {
        "branches": branches, "default_branch": request.membership.default_branch_id,
        "lots": lots, "kinds": WorkOrderKind.choices, "workshop": workshop, "bulk_only": True,
        "in_house": in_house,
        "craftsmen": Employee.objects.filter(is_active=True).order_by("name") if in_house else (),
    })


@permission_required("manufacturing.order.issue")
def work_order_new(request):
    return _new(request, in_house=False)


@permission_required("manufacturing.order.issue")
def production_new(request):
    return _new(request, in_house=True)


@permission_required("manufacturing.order.view")
def work_order(request, pk):
    order = _visible(request).select_related(
        "journal_entry", "receive_entry", "posted_by", "received_by").filter(pk=pk).first()
    if order is None:
        raise Http404
    endpoint = reverse("work-order-api-detail", args=[order.pk])
    context = {
        "order": order, "profile": TenantProfile.objects.first(),
        "issue_lines": order.issue_lines.select_related("category", "karat__metal"),
        "receipt_lines": order.receipt_lines.select_related("category", "karat__metal")
        .prefetch_related("pieces__item"),
        "home_currency": functional_currency(), "endpoint": endpoint,
    }
    if order.at_workshop:
        # What can come back: gold pieces and bulk gold, or scrap.
        context["scrap_id"] = scrap_category().pk
        context["categories"] = ItemCategory.objects.filter(is_active=True).order_by("code")
        context["karats"] = Karat.objects.filter(is_active=True).select_related("metal")
    return render(request, "manufacturing/work_order.html", context)
