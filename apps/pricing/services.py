"""Publishing price boards and exchange rates (§7.6, §8.3).

`actor` is an apps.iam.authz.Actor. When given, the service checks the permission itself
(defence in depth, §14.3); None is for trusted system code such as provisioning and imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.domain.metal import derive_price_per_gram
from apps.catalog.models import Currency, Karat
from apps.core.errors import ValidationError
from apps.core.numeric import FX_RATE, UNIT_PRICE, quantize

from .models import FxRate, MetalPriceBoard, MetalPriceBoardLine
from .selectors import functional_currency


def _price(value, field: str) -> Decimal | None:
    if value is None:
        return None
    price = quantize(value, UNIT_PRICE)
    if price <= 0:
        raise ValidationError(_("Prices must be positive."),
                              fields={field: [_("Must be greater than zero.")]})
    return price


@dataclass(frozen=True)
class KaratPriceInput:
    karat_id: int
    sell_price_per_g: Decimal | str
    buy_price_per_g: Decimal | str | None = None
    scrap_buy_price_per_g: Decimal | str | None = None


@dataclass(frozen=True)
class PublishPriceBoardCommand:
    reference: KaratPriceInput
    # Karats priced explicitly instead of derived. Other metals (silver) appear on the board
    # only when listed here: their prices are not derivable from gold.
    overrides: tuple[KaratPriceInput, ...] = ()
    effective_at: datetime | None = None
    source: str = "manual"
    note: str = ""


def publish_price_board(cmd: PublishPriceBoardCommand, *, actor=None) -> MetalPriceBoard:
    if actor is not None:
        actor.require("pricing.board.publish")
    user = getattr(actor, "user", None)
    entered = [cmd.reference, *cmd.overrides]
    karat_ids = [p.karat_id for p in entered]
    if len(set(karat_ids)) != len(karat_ids):
        raise ValidationError(_("A karat appears twice."),
                              fields={"overrides": [_("Duplicate karat.")]})

    karats = {k.pk: k for k in Karat.objects.select_related("metal").filter(is_active=True)}
    missing = [kid for kid in karat_ids if kid not in karats]
    if missing:
        raise ValidationError(_("Unknown or inactive karat."), fields={"karat_id": missing})

    reference = karats[cmd.reference.karat_id]
    ref_sell = _price(cmd.reference.sell_price_per_g, "sell_price_per_g")
    ref_buy = _price(cmd.reference.buy_price_per_g, "buy_price_per_g")
    ref_scrap = _price(cmd.reference.scrap_buy_price_per_g, "scrap_buy_price_per_g")

    def derive(price, karat):
        if price is None:
            return None
        return derive_price_per_gram(price, reference.fineness, karat.fineness)

    lines = [
        MetalPriceBoardLine(
            karat=karats[p.karat_id],
            sell_price_per_g=_price(p.sell_price_per_g, "sell_price_per_g"),
            buy_price_per_g=_price(p.buy_price_per_g, "buy_price_per_g"),
            scrap_buy_price_per_g=_price(p.scrap_buy_price_per_g, "scrap_buy_price_per_g"),
            is_derived=False,
        )
        for p in entered
    ]
    for karat in karats.values():
        if karat.metal_id == reference.metal_id and karat.pk not in karat_ids:
            lines.append(MetalPriceBoardLine(
                karat=karat,
                sell_price_per_g=derive(ref_sell, karat),
                buy_price_per_g=derive(ref_buy, karat),
                scrap_buy_price_per_g=derive(ref_scrap, karat),
                is_derived=True,
            ))

    with transaction.atomic():
        board = MetalPriceBoard.objects.create(
            effective_at=cmd.effective_at or timezone.now(),
            source=cmd.source,
            reference_karat=reference,
            note=cmd.note,
            created_by=user,
        )
        for line in lines:
            line.board = board
            line.created_by = user
        MetalPriceBoardLine.objects.bulk_create(lines)
        # TODO(outbox): publish PriceBoardPublished for the e-commerce connector (§9.6).
    return board


def record_fx_rate(currency_code: str, rate: Decimal | str, *,
                   effective_at: datetime | None = None, source: str = "manual",
                   actor=None) -> FxRate:
    if actor is not None:
        actor.require("pricing.fx.publish")
    currency = Currency.objects.filter(code=currency_code, is_active=True).first()
    if currency is None:
        raise ValidationError(_("Unknown currency %(code)s.") % {"code": currency_code},
                              fields={"currency": [_("Unknown currency.")]})
    if currency_code == functional_currency():
        raise ValidationError(_("The company currency always has a rate of 1."),
                              fields={"currency": [_("This is the company currency.")]})
    value = quantize(rate, FX_RATE)
    if value <= 0:
        raise ValidationError(_("Rates must be positive."),
                              fields={"rate": [_("Must be greater than zero.")]})
    return FxRate.objects.create(
        currency=currency, rate=value, effective_at=effective_at or timezone.now(),
        source=source, created_by=getattr(actor, "user", None),
    )
