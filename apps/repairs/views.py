from django.db.models import Q, Sum
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.models import Currency, Karat
from apps.core.listing import Choice, ListQuery, Total
from apps.core.models import DocStatus
from apps.iam.authz import permission_required
from apps.org.models import Branch, TenantProfile
from apps.parties.models import Party, PartyRoleType
from apps.pricing.selectors import functional_currency
from apps.treasury.selectors import holder_choices

from .models import RepairKind, RepairOrder


def _visible(request):
    orders = RepairOrder.objects.select_related("customer", "workshop", "branch")
    scope = request.actor.branch_ids("repairs.order.view")
    return orders if scope is None else orders.filter(branch_id__in=scope)


@permission_required("repairs.order.view")
def repairs(request):
    """Repairs and custom orders: the state tabs, plus search, dates, branch and kind (Gweb's
    GHFX21 / GHFX22 reports)."""
    today = timezone.localdate()
    open_orders = Q(status=DocStatus.POSTED, delivered_at__isnull=True)
    states = {"open": open_orders, "ready": open_orders & Q(ready_on__isnull=False),
              "overdue": open_orders & Q(promised_on__lt=today),
              "delivered": Q(delivered_at__isnull=False), "cancelled": Q(status=DocStatus.VOIDED),
              "all": Q()}
    filters = ListQuery(
        request, permission="repairs.order.view",
        search=("number", "customer__name", "customer_name", "customer_phone",
                "customer__phone", "lines__description"),
        search_label=_("Number, bag, customer, phone or piece"), party="customer_id",
        party_param="customer",
        choices=[Choice("state", _("State"), [
            ("open", _("In progress")), ("ready", _("Ready for pickup")), ("overdue", _("Late")),
            ("delivered", _("Delivered")), ("cancelled", _("Cancelled")), ("all", _("All"))],
            apply=lambda qs, v: qs.filter(states[v]), value="open", tabs=True),
                 Choice("kind", _("Kind"), [("", _("All"))] + list(RepairKind.choices), "kind")])
    orders = filters.apply(_visible(request))
    if filters.q.isdigit():  # the bag number as well
        bags = filters.apply(_visible(request).filter(bag_number=int(filters.q)), search=False)
        orders = _visible(request).filter(Q(pk__in=orders.values("pk"))
                                          | Q(pk__in=bags.values("pk")))
    sums = RepairOrder.objects.filter(pk__in=orders.exclude(
        status=DocStatus.VOIDED).values("pk")).aggregate(
        deposits=Sum("deposit_amount"), charges=Sum("charge_amount"))
    home = functional_currency()
    return render(request, "repairs/repairs.html", {
        "page": filters.page(orders.order_by("-business_date", "-id")),
        "filters": filters, "home_currency": home,
        "totals": [Total(_("Charges"), sums["charges"] or 0, unit=home),
                   Total(_("Deposits"), sums["deposits"] or 0, unit=home)],
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
