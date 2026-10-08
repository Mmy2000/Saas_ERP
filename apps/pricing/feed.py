"""Following the market gold price automatically (the shop's side of apps.platform.market).

On every market move (and on "Update now"), the market's 21K prices are turned into this shop's
board:

    sell  = market sell + sell_adjust        scrap buy = market buy + scrap_adjust
    buy   = market buy  + buy_adjust         all rounded to the shop's step (1, 5, 10…)

The other karats are derived by fineness as with a board typed in by hand; silver follows the
market's 999 price when the shop sells silver. Nothing is published when the change is smaller
than `min_change`, outside the shop's hours, or while paused; a jump bigger than
`max_jump_pct` waits on the gold prices page for someone to approve it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.domain.metal import derive_price_per_gram
from apps.catalog.models import Karat
from apps.core.numeric import MONEY, quantize

from .models import PriceFeed
from .selectors import current_price_board

ZERO = Decimal(0)


class Outcome:
    OFF = "off"
    PAUSED = "paused"
    CLOSED = "closed"  # outside the shop's hours
    NO_MARKET = "no_market"
    UNCHANGED = "unchanged"
    HELD = "held"
    PUBLISHED = "published"


@dataclass
class Proposal:
    sell: Decimal  # reference karat, per gram
    buy: Decimal
    scrap: Decimal
    silver: dict[int, tuple[Decimal, Decimal]] = field(default_factory=dict)  # karat id → prices
    source: str = ""
    market_sell21: Decimal = ZERO
    market_buy21: Decimal = ZERO
    jump_pct: Decimal = ZERO
    notes: list[str] = field(default_factory=list)

    def as_json(self) -> dict:
        return {"sell": str(self.sell), "buy": str(self.buy), "scrap": str(self.scrap),
                "silver": {str(k): [str(s), str(b)] for k, (s, b) in self.silver.items()},
                "source": self.source, "market_sell21": str(self.market_sell21),
                "market_buy21": str(self.market_buy21), "jump_pct": str(self.jump_pct)}


def feed_settings() -> PriceFeed:
    feed = PriceFeed.objects.first()
    return feed if feed is not None else PriceFeed.objects.create()


def round_to(value: Decimal, step: Decimal) -> Decimal:
    if not step:
        return quantize(value, MONEY)
    return quantize((value / step).quantize(Decimal(1), rounding=ROUND_HALF_UP) * step, MONEY)


def _reference_karats() -> tuple[Karat, Karat | None]:
    gold = Karat.objects.select_related("metal").filter(metal__code="gold", is_active=True)
    reference = gold.filter(is_reference=True).first() or gold.filter(code=21).first()
    return reference, gold.filter(code=21).first()


def propose(feed: PriceFeed, market) -> Proposal:
    """The board this shop would publish for a market price (apps.platform.market)."""
    reference, k21 = _reference_karats()
    fineness21 = k21.fineness if k21 is not None else Decimal("875")

    def at_reference(price_21: Decimal) -> Decimal:
        return derive_price_per_gram(price_21, fineness21, reference.fineness)

    proposal = Proposal(
        sell=round_to(at_reference(market.sell21 + feed.sell_adjust), feed.rounding),
        buy=round_to(at_reference(market.buy21 + feed.buy_adjust), feed.rounding),
        scrap=round_to(at_reference(market.buy21 + feed.scrap_adjust), feed.rounding),
        source=market.source.key, market_sell21=market.sell21, market_buy21=market.buy21,
        notes=list(market.notes))
    silver = market.silver999() if feed.follow_silver else None
    if silver:
        for karat in Karat.objects.filter(metal__code="silver", is_active=True):
            proposal.silver[karat.pk] = (derive_price_per_gram(silver[0], 999, karat.fineness),
                                         derive_price_per_gram(silver[1], 999, karat.fineness))
    board = current_price_board()
    if board is not None:
        current = next((ln for ln in board.lines.all()
                        if ln.karat_id == board.reference_karat_id), None)
        if current is not None and current.sell_price_per_g:
            proposal.jump_pct = quantize(
                abs(proposal.sell - current.sell_price_per_g) * 100 / current.sell_price_per_g,
                MONEY)
    return proposal


def _differs(proposal: Proposal, threshold: Decimal) -> bool:
    board = current_price_board()
    if board is None:
        return True
    lines = {ln.karat_id: ln for ln in board.lines.all()}
    current = lines.get(board.reference_karat_id)
    if current is None:
        return True
    pairs = [(proposal.sell, current.sell_price_per_g), (proposal.buy, current.buy_price_per_g),
             (proposal.scrap, current.scrap_buy_price_per_g)]
    for karat_id, (sell, buy) in proposal.silver.items():
        line = lines.get(karat_id)
        pairs += [(sell, line.sell_price_per_g if line else None),
                  (buy, line.buy_price_per_g if line else None)]
    return any(old is None or abs(new - old) >= max(threshold, Decimal("0.01"))
               for new, old in pairs)


def _in_hours(feed: PriceFeed, now: datetime) -> bool:
    if feed.active_from is None or feed.active_to is None:
        return True
    clock = timezone.localtime(now).time()
    if feed.active_from <= feed.active_to:
        return feed.active_from <= clock <= feed.active_to
    return clock >= feed.active_from or clock <= feed.active_to  # past midnight


def publish(proposal: Proposal, *, actor=None):
    from .services import KaratPriceInput, PublishPriceBoardCommand, publish_price_board

    reference, _k21 = _reference_karats()
    overrides = tuple(KaratPriceInput(karat_id, sell, buy, buy)
                      for karat_id, (sell, buy) in proposal.silver.items())
    note = _("Market 21K %(sell)s / %(buy)s (%(source)s)") % {
        "sell": proposal.market_sell21, "buy": proposal.market_buy21,
        "source": source_label(proposal.source)}
    return publish_price_board(PublishPriceBoardCommand(
        reference=KaratPriceInput(reference.pk, proposal.sell, proposal.buy, proposal.scrap),
        overrides=overrides, source=f"feed:{proposal.source}"[:40], note=note[:200]),
        actor=actor)


def source_label(key: str) -> str:
    from apps.platform.market.sources import SOURCES

    module = SOURCES.get(key)
    return module.LABEL if module is not None else key


def follow_market(*, force: bool = False, approve: bool = False, now=None, actor=None) -> str:
    """Publish the market price under this shop's rules. `force` ("Update now") skips the
    on/off switch, pause, hours and minimum change; `approve` also accepts a big jump."""
    from apps.platform.market.service import market_price

    now = now or timezone.now()
    with transaction.atomic():
        feed = PriceFeed.objects.select_for_update().filter(pk=feed_settings().pk).first()
        outcome = _decide(feed, now, force=force, approve=approve, actor=actor,
                          market=market_price(feed.source, now))
        feed.last_checked_at, feed.last_outcome = now, outcome
        feed.save(update_fields=["last_checked_at", "last_outcome", "held", "held_at",
                                 "updated_at"])
    return outcome


def _decide(feed, now, *, force, approve, actor, market) -> str:
    if not force:
        if not feed.is_enabled:
            return Outcome.OFF
        if feed.paused_until and feed.paused_until > now:
            return Outcome.PAUSED
        if not _in_hours(feed, now):
            return Outcome.CLOSED
    if market is None:
        return Outcome.NO_MARKET
    proposal = propose(feed, market)
    if not force and not _differs(proposal, feed.min_change):
        return Outcome.UNCHANGED
    if proposal.jump_pct > feed.max_jump_pct and not approve:
        feed.held, feed.held_at = proposal.as_json(), now
        from apps.notifications import events

        events.price_held(proposal.jump_pct)
        return Outcome.HELD
    publish(proposal, actor=actor)
    feed.held = feed.held_at = None
    return Outcome.PUBLISHED


def pause_until_tomorrow(now=None) -> PriceFeed:
    now = now or timezone.now()
    tomorrow = (timezone.localtime(now) + timedelta(days=1)).replace(hour=0, minute=0, second=0,
                                                                   microsecond=0)
    feed = feed_settings()
    feed.paused_until = tomorrow
    feed.save(update_fields=["paused_until", "updated_at"])
    return feed


def resume() -> PriceFeed:
    feed = feed_settings()
    feed.paused_until = None
    feed.save(update_fields=["paused_until", "updated_at"])
    return feed


def dismiss_held() -> PriceFeed:
    feed = feed_settings()
    feed.held = feed.held_at = None
    feed.save(update_fields=["held", "held_at", "updated_at"])
    from apps.notifications.events import resolve_prefix

    resolve_prefix("price_held:")
    return feed

