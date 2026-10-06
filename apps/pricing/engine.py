"""The pricing engine (§8.4, ADR-010): the only place a sale or trade-in price is computed.

The browser asks for quotes; posting recomputes everything here and ignores client totals.

Gold piece (retail):
    metal price/g   = board sell price for the karat                         (captured)
    making/g        = item list rate, else the category's rate in company currency
    discount        ≤ the seller's limit, on the making charge only
    making/g net    = max(making × (1 − discount), making cost/g)             (never below cost)
    line total      = round2((metal price/g + making/g net) × weight)
    metal amount    = round2(metal price/g × weight); making amount = total − metal amount

Bulk gold by weight: the same, with the category's making rate and the lot's average making
cost per gram as the floor.

Diamond piece or loose stone (apps.diamonds), sold at its label price:
    discount        ≤ the seller's diamond limit, on the whole label price
    gold part       = round2(board sell price × metal weight)          (none for loose stones)
    line total      = max(label × (1 − discount), what it cost)   (gold part + making + stones)
    stones part     = line total − gold part: the stones and the workmanship

Scrap trade-in:
    net weight      = gross − loss
    price/g         = board scrap-buy price for the karat (buy price if none), or an override
    value           = round2(net weight × price/g)
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.utils.translation import gettext as _

from apps.catalog.domain.metal import fine_weight
from apps.catalog.models import CategoryMakingCharge
from apps.core.errors import DomainError, ValidationError
from apps.core.numeric import RATE, UNIT_PRICE, WEIGHT, quantize, round_money

from .models import PriceSide

ZERO = Decimal(0)


@dataclass(frozen=True)
class ItemQuote:
    """A priced piece (item_id) or weight of bulk gold (lot_id, qty pieces if counted)."""

    item_id: int | None
    barcode: str
    description: str
    karat_label: str
    gross_weight_g: Decimal
    fine_weight_g: Decimal
    metal_price_per_g: Decimal
    making_rate: Decimal
    discount_rate: Decimal
    making_rate_net: Decimal
    metal_amount: Decimal
    making_amount: Decimal
    line_total: Decimal
    discount_amount: Decimal
    cost_amount: Decimal
    at_cost_floor: bool
    lot_id: int | None = None
    qty: int = 1
    stones_amount: Decimal = ZERO  # label-priced pieces: the price beyond the gold
    stone_cost: Decimal = ZERO
    label_price: Decimal | None = None


@dataclass(frozen=True)
class TradeInQuote:
    karat_id: int
    karat_label: str
    gross_weight_g: Decimal
    loss_weight_g: Decimal
    net_weight_g: Decimal
    fine_weight_g: Decimal
    price_per_g: Decimal
    amount: Decimal
    price_overridden: bool


def category_making_rate(category_id: int, functional_currency: str) -> Decimal:
    charge = (CategoryMakingCharge.objects.filter(category_id=category_id,
                                                  currency__code=functional_currency)
              .values_list("list_rate_per_g", flat=True).first())
    return charge or ZERO


def list_making_rate(item, functional_currency: str) -> Decimal:
    return item.list_making_rate or category_making_rate(item.category_id, functional_currency)


def _discount(discount_rate, max_discount: Decimal) -> Decimal:
    discount = quantize(discount_rate or "0", RATE)
    if discount < 0 or discount > 1:
        raise ValidationError(_("The discount must be between 0 and 100%."),
                              code="SALES_DISCOUNT_RANGE")
    if discount > max_discount:
        raise DomainError(
            _("The largest discount you may give is %(max)s%%.")
            % {"max": (max_discount * 100).normalize()}, code="SALES_DISCOUNT_LIMIT")
    return discount


def _priced(*, karat, board, weight: Decimal, making: Decimal, cost_amount: Decimal,
            discount: Decimal, **identity) -> ItemQuote:
    """The one gold formula, for a piece or a weight of bulk gold."""
    metal_price = board.price(karat, PriceSide.SELL)
    cost_per_g = quantize(cost_amount / weight, UNIT_PRICE) if weight else ZERO
    discounted = quantize(making * (1 - discount), UNIT_PRICE)
    making_net = max(discounted, min(cost_per_g, making))
    total = round_money((metal_price + making_net) * weight)
    metal_amount = round_money(metal_price * weight)
    return ItemQuote(
        karat_label=karat.label, gross_weight_g=weight, metal_price_per_g=metal_price,
        making_rate=making, discount_rate=discount, making_rate_net=making_net,
        metal_amount=metal_amount, making_amount=total - metal_amount, line_total=total,
        discount_amount=round_money((metal_price + making) * weight) - total,
        cost_amount=cost_amount, at_cost_floor=making_net > discounted, **identity)


def quote_item(item, board, *, discount_rate, max_discount: Decimal,
               functional_currency: str) -> ItemQuote:
    if item.karat is None:
        raise DomainError(_("Piece %(barcode)s has no karat and cannot be priced by weight.")
                          % {"barcode": item.barcode}, code="PRICING_NO_KARAT")
    return _priced(
        karat=item.karat, board=board, weight=item.gross_weight_g,
        making=list_making_rate(item, functional_currency), cost_amount=item.cost_amount,
        discount=_discount(discount_rate, max_discount), item_id=item.pk,
        barcode=item.barcode, description=item.category.name, fine_weight_g=item.fine_weight_g)


def quote_label_priced(item, board, *, discount_rate, max_discount: Decimal) -> ItemQuote:
    """A diamond piece or loose stone, at its label price (see the module docstring)."""
    if not item.label_price:
        raise DomainError(_("Piece %(barcode)s has no label price.") % {"barcode": item.barcode},
                          code="PRICING_NO_LABEL_PRICE")
    discount = _discount(discount_rate, max_discount)
    label = item.label_price
    metal_price = board.price(item.karat, PriceSide.SELL) if item.karat else ZERO
    metal_amount = round_money(metal_price * item.metal_weight_g) if item.karat else ZERO
    asked = round_money(label * (1 - discount))
    floor = min(metal_amount + item.cost_amount + item.stone_cost_amount, label)
    total = max(asked, floor)
    gold = min(metal_amount, total)
    return ItemQuote(
        item_id=item.pk, barcode=item.barcode, description=item.category.name,
        karat_label=item.karat.label if item.karat else "", gross_weight_g=item.gross_weight_g,
        fine_weight_g=item.fine_weight_g, metal_price_per_g=metal_price, making_rate=ZERO,
        discount_rate=discount, making_rate_net=ZERO, metal_amount=gold, making_amount=ZERO,
        line_total=total, discount_amount=label - total, cost_amount=item.cost_amount,
        at_cost_floor=total > asked, stones_amount=total - gold,
        stone_cost=item.stone_cost_amount, label_price=label)


def quote_bulk(lot, balance, board, *, gross_weight_g, qty: int = 0, discount_rate,
               max_discount: Decimal, functional_currency: str) -> ItemQuote:
    """A weight of bulk gold (chain by the gram, bullion…) from a lot. Its making cost is the
    lot's average per gram."""
    weight = quantize(gross_weight_g, WEIGHT)
    if weight <= 0:
        raise ValidationError(_("Enter the weight."), code="SALES_BULK_WEIGHT")
    cost = (round_money(balance.cost_amount * weight / balance.gross_weight_g)
            if balance.gross_weight_g else ZERO)
    return _priced(
        karat=lot.karat, board=board, weight=weight,
        making=category_making_rate(lot.category_id, functional_currency), cost_amount=cost,
        discount=_discount(discount_rate, max_discount), item_id=None, lot_id=lot.pk, qty=qty,
        barcode="", description=lot.category.name,
        fine_weight_g=fine_weight(weight, lot.karat.fineness))


def quote_trade_in(karat, board, *, gross_weight_g, loss_weight_g="0", price_override=None,
                   may_override: bool = False) -> TradeInQuote:
    gross = quantize(gross_weight_g, WEIGHT)
    loss = quantize(loss_weight_g or "0", WEIGHT)
    if gross <= 0 or loss < 0 or loss >= gross:
        raise ValidationError(_("Loss must be less than the weight."), code="SALES_TRADE_IN_WEIGHT")
    net = gross - loss
    if price_override not in (None, ""):
        if not may_override:
            raise DomainError(_("You may not change the scrap gold price."),
                              code="SALES_TRADE_IN_PRICE_LOCKED")
        price = quantize(price_override, UNIT_PRICE)
        overridden = True
    else:
        try:
            price = board.price(karat, PriceSide.SCRAP_BUY)
        except DomainError:
            price = board.price(karat, PriceSide.BUY)
        overridden = False
    if price <= 0:
        raise ValidationError(_("Prices must be positive."), code="SALES_TRADE_IN_PRICE")
    return TradeInQuote(
        karat_id=karat.pk, karat_label=karat.label, gross_weight_g=gross, loss_weight_g=loss,
        net_weight_g=net, fine_weight_g=fine_weight(net, karat.fineness), price_per_g=price,
        amount=round_money(net * price), price_overridden=overridden,
    )

