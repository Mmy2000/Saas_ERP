from django.shortcuts import render
from django.utils import timezone

from apps.catalog.models import Currency, Karat
from apps.iam.authz import permission_required
from apps.pricing.models import FxRate, MetalPriceBoard
from apps.pricing.selectors import (
    board_lines_for_display,
    current_price_board,
    functional_currency,
)


@permission_required("pricing.board.view")
def gold_prices(request):
    board = current_price_board()
    history = list(
        MetalPriceBoard.objects.select_related("reference_karat__metal", "created_by")
        .prefetch_related("lines").order_by("-effective_at", "-id")[:15]
    )
    for past in history:
        past.reference_line = next(
            (ln for ln in past.lines.all() if ln.karat_id == past.reference_karat_id), None)
    return render(request, "pricing/gold.html", {
        "board": board,
        "board_rows": board_lines_for_display(board),
        "history": history,
        "reference_karat": Karat.objects.select_related("metal")
        .filter(is_reference=True, metal__code="gold").first(),
        "silver_karats": Karat.objects.select_related("metal")
        .filter(metal__code="silver", is_active=True),
        "home_currency": functional_currency(),
    })


@permission_required("pricing.fx.view")
def fx_rates(request):
    home = functional_currency()
    currencies = list(Currency.objects.filter(is_active=True).exclude(code=home))
    now = timezone.now()
    current = []
    for currency in currencies:
        latest = (FxRate.objects.filter(currency=currency, effective_at__lte=now)
                  .order_by("-effective_at", "-id").first())
        current.append({"currency": currency, "rate": latest})
    history = (FxRate.objects.select_related("currency", "created_by")
               .order_by("-effective_at", "-id")[:20])
    return render(request, "pricing/fx.html", {
        "home_currency": home, "currencies": currencies, "current": current, "history": history,
    })
