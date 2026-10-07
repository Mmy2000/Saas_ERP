"""Following the market gold price: margins, rounding, minimum change, approval of big jumps,
pause and hours, silver, the feature switch and the page."""

from datetime import time, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone, translation

from apps.catalog.models import Karat
from apps.core.tenancy import tenant_context
from apps.platform.market.models import MarketQuote, MarketSource
from apps.pricing import feed as feeds
from apps.pricing.models import PriceFeed
from apps.pricing.selectors import current_price_board
from apps.sales.tests.test_sales import Shop
from conftest import login

pytestmark = pytest.mark.django_db
D = Decimal


def market(sell, buy, silver=None, key="isagha"):
    source, _created = MarketSource.objects.get_or_create(key=key)
    source.last_ok_at = timezone.now()
    source.save()
    prices = {"gold": {"21": {"sell": str(sell), "buy": str(buy)}}, "silver": {}}
    if silver:
        prices["silver"]["999"] = {"sell": str(silver[0]), "buy": str(silver[1])}
    return MarketQuote.objects.create(source=source, fetched_at=timezone.now(), prices=prices,
                                      gold21_sell=sell, gold21_buy=buy, digest=f"{sell}-{buy}")


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        yield Shop()  # 21K board at 4000 / 3950 / 3900


def _reference(board):
    return next(ln for ln in board.lines.all() if ln.karat_id == board.reference_karat_id)


def _settings(**values):
    feed = feeds.feed_settings()
    for name, value in values.items():
        setattr(feed, name, value)
    feed.save()
    return feed


def test_following_the_market_with_the_shops_rules(tenant_a, shop):
    market(D("4100"), D("4060"), silver=(D("103"), D("99.75")))
    with tenant_context(tenant_a.id):
        assert feeds.follow_market() == feeds.Outcome.OFF
        _settings(is_enabled=True, sell_adjust=D("12"), buy_adjust=D("-4"),
                  scrap_adjust=D("-52"), rounding=D("5"), max_jump_pct=D("5"))
        assert feeds.follow_market() == feeds.Outcome.PUBLISHED
        board = current_price_board()
        line = _reference(board)
        assert (line.sell_price_per_g, line.buy_price_per_g, line.scrap_buy_price_per_g) == (
            D("4110.0000"), D("4055.0000"), D("4010.0000"))  # 4112→4110, 4056→4055, 4008→4010
        assert board.source == "feed:isagha" and "4100" in board.note
        k18 = next(ln for ln in board.lines.all() if ln.karat.code == 18)
        assert k18.sell_price_per_g == D("3522.8600") and k18.is_derived  # 4110 × 750/875
        silver = Karat.objects.filter(metal__code="silver").first()
        if silver is not None:
            line = next(ln for ln in board.lines.all() if ln.karat_id == silver.pk)
            assert line.sell_price_per_g == (D(103) * silver.fineness / 999).quantize(D("0.01"))

        assert feeds.follow_market() == feeds.Outcome.UNCHANGED
    market(D("4099"), D("4061"))  # after margins and rounding: the same 4110 / 4055 / 4010
    with tenant_context(tenant_a.id):
        assert feeds.follow_market() == feeds.Outcome.UNCHANGED


def test_a_big_jump_waits_for_approval(tenant_a, shop):
    market(D("4600"), D("4550"))  # 15% above the board
    with tenant_context(tenant_a.id):
        _settings(is_enabled=True, max_jump_pct=D("3"))
        assert feeds.follow_market() == feeds.Outcome.HELD
        feed = PriceFeed.objects.get()
        assert feed.held["sell"] == "4600.00" and D(feed.held["jump_pct"]) == D("15.00")
        assert _reference(current_price_board()).sell_price_per_g == D("4000")

        from apps.iam.authz import build_actor
        from apps.iam.models import Membership
        from apps.org.dashboard import needs_attention

        actor = build_actor(Membership.objects.get(username="owner"))
        with translation.override("en"):
            assert any("approval" in item.label for item in needs_attention(actor))

        assert feeds.follow_market(force=True, approve=True) == feeds.Outcome.PUBLISHED
        assert PriceFeed.objects.get().held is None
        assert _reference(current_price_board()).sell_price_per_g == D("4600")


def test_pause_hours_and_no_market(tenant_a, shop):
    with tenant_context(tenant_a.id):
        _settings(is_enabled=True)
        assert feeds.follow_market() == feeds.Outcome.NO_MARKET
    market(D("4010"), D("3960"))
    with tenant_context(tenant_a.id):
        feeds.pause_until_tomorrow()
        assert feeds.follow_market() == feeds.Outcome.PAUSED
        assert feeds.follow_market(force=True) == feeds.Outcome.PUBLISHED  # "Update now"
        feeds.resume()
        now = timezone.localtime().replace(hour=23, minute=30)
        _settings(active_from=time(10, 0), active_to=time(22, 0))
        assert feeds.follow_market(now=now) == feeds.Outcome.CLOSED
        _settings(active_from=time(22, 0), active_to=time(2, 0))  # past midnight
        assert feeds._in_hours(PriceFeed.objects.get(), now)
        assert not feeds._in_hours(PriceFeed.objects.get(), now - timedelta(hours=12))


def test_the_job_respects_the_feature_switch(tenant_a, shop):
    from apps.platform.tenants import features
    from apps.platform.tenants.models import TenantFeature
    from apps.pricing.jobs import follow_market as job

    market(D("4050"), D("4000"))
    with tenant_context(tenant_a.id):
        _settings(is_enabled=True)
    TenantFeature.objects.create(tenant=tenant_a, key="price_feed", enabled=False)
    features.forget(tenant_a.id)
    with tenant_context(tenant_a.id):
        job()
        assert _reference(current_price_board()).sell_price_per_g == D("4000")
    TenantFeature.objects.filter(tenant=tenant_a).update(enabled=True)
    features.forget(tenant_a.id)
    with tenant_context(tenant_a.id):
        job()
        assert _reference(current_price_board()).sell_price_per_g == D("4050")


def test_page_settings_and_buttons(tenant_a, shop, make_member):
    market(D("4020"), D("3980"))
    client = login(tenant_a, language="en")
    page = client.get("/pricing/gold/").content.decode()
    assert "Market prices" in page and "4,020.00" in page and "Not following the market" in page
    body = {"is_enabled": True, "source": "", "sell_adjust": "10", "buy_adjust": "0",
            "scrap_adjust": "-40", "rounding": "5", "min_change": "1", "max_jump_pct": "3",
            "follow_silver": True, "active_from": "10:00", "active_to": None}
    bad = client.put("/api/v1/pricing/feed/", body, content_type="application/json")
    assert bad.status_code == 400  # one time without the other
    body["active_from"] = None
    saved = client.put("/api/v1/pricing/feed/", body, content_type="application/json")
    assert saved.status_code == 200 and saved.json()["outcome"] == "published"
    with tenant_context(tenant_a.id):
        assert _reference(current_price_board()).sell_price_per_g == D("4030")
    action = client.post("/api/v1/pricing/feed/action/", {"action": "pause"},
                         content_type="application/json", HTTP_IDEMPOTENCY_KEY="p1")
    assert action.json()["outcome"] == "paused"
    assert "Paused until" in client.get("/pricing/gold/").content.decode()
    assert "Automatic" in client.get("/pricing/gold/").content.decode()  # in the history

    make_member(tenant_a, "viewer", "viewer")
    viewer = login(tenant_a, username="viewer", language="en")
    page = viewer.get("/pricing/gold/").content.decode()
    assert "Market prices" in page and "Update now" not in page
    assert viewer.put("/api/v1/pricing/feed/", body,
                      content_type="application/json").status_code == 403
