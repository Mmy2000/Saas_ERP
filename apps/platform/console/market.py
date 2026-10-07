"""Console → Market prices: are the gold price sources answering, do they agree, which one
is preferred; switch one off, change the order, read them now."""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.platform.market.models import MarketQuote, MarketSource
from apps.platform.market.service import (
    FRESH_FOR,
    OUTLIER_PCT,
    deviation_pct,
    ensure_sources,
    market_price,
    poll,
)
from apps.platform.market.sources import SOURCES

from .views import record, staff_required


@staff_required
def market(request):
    now = timezone.now()
    sources = ensure_sources()
    best = market_price(now=now)
    rows = []
    for source in sources:
        quote = source.quotes.order_by("-fetched_at").first()
        fresh = bool(source.last_ok_at and now - source.last_ok_at <= FRESH_FOR)
        module = SOURCES.get(source.key)
        rows.append({
            "source": source, "label": module.LABEL if module else source.key,
            "url": module.URL if module else "", "quote": quote, "fresh": fresh,
            "deviation": deviation_pct(quote.gold21_sell, best.median_sell)
            if quote is not None and best is not None else None,
            "chosen": best is not None and best.source.pk == source.pk,
            "karats": sorted(((int(k), v) for k, v in (quote.prices.get("gold") or {}).items()),
                             reverse=True) if quote else [],
            "silver": (quote.prices.get("silver") or {}).get("999") if quote else None,
        })
    labels = {key: module.LABEL for key, module in SOURCES.items()}
    recent = list(MarketQuote.objects.select_related("source").order_by("-fetched_at")[:30])
    for quote in recent:
        quote.label = labels.get(quote.source.key, quote.source.key)
    return render(request, "console/market.html", {
        "section": "market", "rows": rows, "best": best, "recent": recent, "now": now,
        "best_label": labels.get(best.source.key, best.source.key) if best else "",
        "fresh_minutes": int(FRESH_FOR.total_seconds() // 60), "outlier_pct": OUTLIER_PCT,
    })


def _source(pk) -> MarketSource:
    source = MarketSource.objects.filter(pk=pk).first()
    if source is None:
        raise Http404
    return source


@staff_required
@require_POST
def market_source(request, pk):
    """POST action = enable | disable | up | down."""
    source = _source(pk)
    action = request.POST.get("action", "")
    if action in ("enable", "disable"):
        source.is_enabled = action == "enable"
        source.save(update_fields=["is_enabled"])
        record(request, "market.source", None, source=source.key, change=action)
    elif action in ("up", "down"):
        ordered = list(ensure_sources())
        index = ordered.index(next(s for s in ordered if s.pk == source.pk))
        swap = index - 1 if action == "up" else index + 1
        if 0 <= swap < len(ordered):
            ordered[index], ordered[swap] = ordered[swap], ordered[index]
            for position, item in enumerate(ordered):
                MarketSource.objects.filter(pk=item.pk).update(priority=10 * (position + 1))
            record(request, "market.source", None, source=source.key, change=action)
    return redirect("console-market")


@staff_required
@require_POST
def market_poll(request):
    changed = poll()
    messages.success(request, _("Sources read: %(n)s price change(s).") % {"n": len(changed)})
    return redirect("console-market")
