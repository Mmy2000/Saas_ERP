from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.errors import DomainError
from apps.core.numeric import UNIT_PRICE, quantize
from apps.org.models import TenantProfile

from .models import FxRate, MetalPriceBoard, PriceSide


def current_price_board(at: datetime | None = None) -> MetalPriceBoard | None:
    """The board in force at `at` (default now): latest effective_at, ties by latest id."""
    return (
        MetalPriceBoard.objects.filter(effective_at__lte=at or timezone.now())
        .order_by("-effective_at", "-id")
        .prefetch_related("lines")
        .first()
    )


def metal_price_per_gram(karat, side: str = PriceSide.SELL,
                         at: datetime | None = None) -> tuple[Decimal, MetalPriceBoard]:
    """Price and the board it came from; documents store both (§8.3)."""
    board = current_price_board(at)
    if board is None:
        raise DomainError(_("No gold price has been published yet."), code="PRICING_NO_BOARD")
    return board.price(karat, side), board


def functional_currency() -> str:
    return TenantProfile.objects.values_list("functional_currency", flat=True).get()


def fx_rate(currency_code: str, at: datetime | None = None) -> Decimal:
    """Functional-currency units per one unit of `currency_code` at `at` (default now)."""
    if currency_code == functional_currency():
        return Decimal(1)
    rate = (
        FxRate.objects.filter(currency__code=currency_code,
                              effective_at__lte=at or timezone.now())
        .order_by("-effective_at", "-id")
        .values_list("rate", flat=True)
        .first()
    )
    if rate is None:
        raise DomainError(_("No exchange rate for %(code)s.") % {"code": currency_code},
                          code="PRICING_NO_FX_RATE")
    return rate


def fine_gram_value(metal_code: str, at: datetime | None = None) -> Decimal:
    """Value of one fine gram of `metal_code` in the company currency, from the board in force:
    the buy price of the most refined karat priced on it (sell price if no buy price), scaled
    to pure metal. 0 when nothing is published. Used to value metal lines in the ledger."""
    board = current_price_board(at)
    if board is None:
        return Decimal(0)
    lines = [ln for ln in board.lines.select_related("karat__metal")
             if ln.karat.metal.code == metal_code]
    if not lines:
        return Decimal(0)
    best = max(lines, key=lambda ln: ln.karat.fineness)
    price = best.buy_price_per_g or best.sell_price_per_g
    return quantize(price * 1000 / best.karat.fineness, UNIT_PRICE)


def board_lines_for_display(board: MetalPriceBoard | None) -> list:
    """Board lines ordered gold first by descending karat, then other metals."""
    if board is None:
        return []
    lines = list(board.lines.select_related("karat__metal"))
    return sorted(lines, key=lambda ln: (ln.karat.metal.code != "gold", -ln.karat.code))
