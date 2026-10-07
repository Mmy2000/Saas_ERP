"""Polling the sources and agreeing on one market price.

`market_price()` is what a shop follows: among the sources that answered recently, the ones whose
21K price is far from the others (more than OUTLIER_PCT from the median, when there are three or
more) are set aside, and the preferred source among the rest is used. A shop may pin one source;
if that source is down or out of line, the best other source is used and the reason is given.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from statistics import median

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import MarketQuote, MarketSource
from .sources import SOURCES, FeedError
from .sources.base import timed

logger = logging.getLogger(__name__)

FRESH_FOR = timedelta(minutes=20)  # a source silent for longer is not used
OUTLIER_PCT = Decimal("1.5")


def ensure_sources() -> list[MarketSource]:
    """One row per known source (new sources appear switched on, after the others)."""
    existing = {s.key: s for s in MarketSource.objects.all()}
    for index, key in enumerate(SOURCES):
        if key not in existing:
            existing[key] = MarketSource.objects.create(key=key, priority=10 * (index + 1))
    return sorted(existing.values(), key=lambda s: (s.priority, s.key))


def digest(prices: dict) -> str:
    return hashlib.sha256(json.dumps(prices, sort_keys=True).encode()).hexdigest()


def poll_source(source: MarketSource, now=None) -> MarketQuote | None:
    """Read one source and record how it went. Returns the new quote when prices changed."""
    now = now or timezone.now()
    module = SOURCES.get(source.key)
    try:
        if module is None:
            raise FeedError(f"Unknown source {source.key!r}.")
        quote, elapsed = timed(module.read)
    except FeedError as exc:
        MarketSource.objects.filter(pk=source.pk).update(
            last_checked_at=now, last_error_at=now, last_error=str(exc)[:1000],
            failures=source.failures + 1)
        logger.warning("market source %s failed: %s", source.key, exc)
        return None
    prices = quote.as_json()
    MarketSource.objects.filter(pk=source.pk).update(
        last_checked_at=now, last_ok_at=now, failures=0, last_latency_ms=elapsed)
    fingerprint = digest(prices)
    latest = source.quotes.order_by("-fetched_at").values_list("digest", flat=True).first()
    if latest == fingerprint:
        return None
    sell21, buy21 = quote.gold[21]
    return MarketQuote.objects.create(source=source, fetched_at=now, prices=prices,
                                      gold21_sell=sell21, gold21_buy=buy21, digest=fingerprint)


def poll(now=None) -> list[MarketQuote]:
    """Every enabled source once. Returns the quotes that changed."""
    changed = []
    for source in ensure_sources():
        if source.is_enabled:
            with transaction.atomic():
                quote = poll_source(source, now)
            if quote is not None:
                changed.append(quote)
    return changed


@dataclass
class MarketPrice:
    quote: MarketQuote
    source: MarketSource
    median_sell: Decimal
    agreeing: list[str]  # keys of the sources in line with each other
    notes: list[str] = field(default_factory=list)

    @property
    def sell21(self) -> Decimal:
        return self.quote.gold21_sell

    @property
    def buy21(self) -> Decimal:
        return self.quote.gold21_buy

    def silver999(self) -> tuple[Decimal, Decimal] | None:
        row = (self.quote.prices.get("silver") or {}).get("999")
        return (Decimal(row["sell"]), Decimal(row["buy"])) if row else None


def latest_quotes(now=None) -> list[tuple[MarketSource, MarketQuote, bool]]:
    """(source, its latest quote, fresh?) for every enabled source that has a quote."""
    now = now or timezone.now()
    rows = []
    for source in MarketSource.objects.filter(is_enabled=True).order_by("priority", "key"):
        quote = source.quotes.order_by("-fetched_at").first()
        if quote is not None:
            fresh = bool(source.last_ok_at and now - source.last_ok_at <= FRESH_FOR)
            rows.append((source, quote, fresh))
    return rows


def label(key: str) -> str:
    module = SOURCES.get(key)
    return module.LABEL if module is not None else key


def deviation_pct(value: Decimal, middle: Decimal) -> Decimal:
    return abs(value - middle) * 100 / middle if middle else Decimal(0)


def market_price(preferred: str = "", now=None) -> MarketPrice | None:
    """The market price to follow (None when no source is fresh)."""
    fresh = [(s, q) for s, q, ok in latest_quotes(now) if ok]
    if not fresh:
        return None
    middle = Decimal(median([q.gold21_sell for _s, q in fresh]))
    agreeing = fresh
    if len(fresh) >= 3:
        agreeing = [(s, q) for s, q in fresh if deviation_pct(q.gold21_sell, middle) <= OUTLIER_PCT]
    notes = [_("%(source)s is out of line with the other sources: not used.")
             % {"source": label(s.key)} for s, q in fresh if (s, q) not in agreeing]
    chosen = next(((s, q) for s, q in agreeing if s.key == preferred), None)
    if preferred and chosen is None:
        notes.append(_("%(source)s is not available right now: another source is used.")
                     % {"source": label(preferred)})
    chosen = chosen or agreeing[0]
    return MarketPrice(quote=chosen[1], source=chosen[0], median_sell=middle,
                       agreeing=[s.key for s, _q in agreeing], notes=notes)


def prune(now=None) -> int:
    """Quotes older than 90 days, except each source's last quote of each day."""
    from django.db.models import Max
    from django.db.models.functions import TruncDate

    now = now or timezone.now()
    old = MarketQuote.objects.filter(fetched_at__lt=now - timedelta(days=90))
    keep = (old.annotate(day=TruncDate("fetched_at")).values("source_id", "day")
            .annotate(last=Max("id")).values_list("last", flat=True))
    return old.exclude(pk__in=list(keep)).delete()[0]
