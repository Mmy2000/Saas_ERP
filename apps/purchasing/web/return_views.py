from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse

from apps.iam.authz import permission_required
from apps.inventory.models import LotBalance
from apps.inventory.services import SCRAP_CATEGORY_CODE
from apps.org.models import Branch, TenantProfile
from apps.purchasing.models import SupplierReturn, seller_from_query


def _visible(request):
    returns = SupplierReturn.objects.select_related("supplier", "branch")
    scope = request.actor.branch_ids("purchasing.invoice.view")
    return returns if scope is None else returns.filter(branch_id__in=scope)


@permission_required("purchasing.invoice.view")
def supplier_returns(request):
    page = Paginator(_visible(request).order_by("-business_date", "-id"), 25).get_page(
        request.GET.get("page"))
    return render(request, "purchasing/supplier_returns.html", {"page": page})


@permission_required("purchasing.return.create")
def supplier_return_new(request):
    allowed = request.actor.branch_ids("purchasing.return.create")
    branches = Branch.objects.filter(is_active=True).order_by("code")
    if allowed is not None:
        branches = branches.filter(pk__in=allowed)
    lots = (LotBalance.objects.filter(gross_weight_g__gt=0, lot__branch__in=branches)
            .exclude(lot__category__code=SCRAP_CATEGORY_CODE)
            .select_related("lot__category", "lot__karat").order_by("lot__category__code"))
    seller_role, seller = seller_from_query(request.GET)
    return render(request, "purchasing/supplier_return_form.html", {
        "branches": branches, "default_branch": request.membership.default_branch_id,
        "lots": lots, "seller_role": seller_role, "seller": seller,
    })


@permission_required("purchasing.invoice.view")
def supplier_return(request, pk):
    doc = _visible(request).select_related("journal_entry", "posted_by").filter(pk=pk).first()
    if doc is None:
        raise Http404
    return render(request, "purchasing/supplier_return.html", {
        "doc": doc, "profile": TenantProfile.objects.first(),
        "lines": doc.lines.select_related("item", "category", "karat__metal"),
        "endpoint": reverse("supplier-return-api-detail", args=[doc.pk]),
    })
