from django.db.models import Max
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

from apps.catalog.models import Karat
from apps.iam.authz import permission_required
from apps.iam.catalog import AUTHENTICATED
from apps.org.dashboard import figures
from apps.org.models import Branch
from apps.pricing.models import FxRate
from apps.pricing.selectors import (
    board_lines_for_display,
    current_price_board,
    functional_currency,
)


@permission_required(AUTHENTICATED)
def home(request):
    actor = request.actor
    now = timezone.localtime()
    context = {"today_label": f"{date_format(now, 'l')} · {date_format(now, 'DATE_FORMAT')}",
               "home_currency": functional_currency()}

    if actor.can("pricing.board.view"):
        board = current_price_board()
        rows = board_lines_for_display(board)
        reference_id = getattr(board, "reference_karat_id", None)
        reference = next((ln for ln in rows if ln.karat_id == reference_id), None)
        context.update(board=board, board_rows=rows, reference_line=reference)

    if actor.can("pricing.fx.view"):
        context["usd_rate"] = (
            None if context["home_currency"] == "USD" else
            FxRate.objects.filter(currency__code="USD", effective_at__lte=timezone.now())
            .order_by("-effective_at", "-id").first()
        )

    context.update(figures(actor))
    context["karat_count"] = Karat.objects.filter(is_active=True).count()
    return render(request, "org/dashboard.html", context)


@permission_required("org.branch.view")
def branches(request):
    return render(request, "org/branches.html", {"branches": Branch.objects.order_by("code")})


def _branch_form(request, branch=None):
    next_code = (Branch.objects.aggregate(top=Max("code"))["top"] or 0) + 1
    return render(request, "org/branch_form.html", {
        "branch": branch,
        "next_code": next_code,
        "endpoint": (reverse("branch-detail", args=[branch.pk]) if branch
                     else reverse("branch-list")),
        "method": "PUT" if branch else "POST",
    })


@permission_required("org.branch.manage")
def branch_new(request):
    return _branch_form(request)


@permission_required("org.branch.manage")
def branch_edit(request, pk):
    branch = Branch.objects.filter(pk=pk).first()
    if branch is None:
        raise Http404
    return _branch_form(request, branch)
