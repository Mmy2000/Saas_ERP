"""Market gold prices: the three readers on real pages, polling, health, cross-checking, the
interval schedule and the console page."""

import gzip
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.test import Client
from django.utils import timezone, translation

from apps.platform.jobs.models import Job
from apps.platform.market import service
from apps.platform.market.models import MarketQuote, MarketSource
from apps.platform.market.sources import FeedError, Quote, edahab, goldbullion, isagha

FIXTURES = Path(__file__).parent / "fixtures"
D = Decimal


def page(name: str) -> str:
    return gzip.open(FIXTURES / f"{name}.html.gz", "rt", encoding="utf-8").read()


def test_the_readers_on_real_pages():
    quote = isagha.parse(page("isagha"), page("isagha_silver"))
    assert quote.gold[21] == (D("6125"), D("6075")) and quote.gold[24][0] == D("7000")
    assert quote.silver[999] == (D("103"), D("99.75"))
    quote = goldbullion.parse(page("goldbullion"))
    assert quote.gold[21] == (D("6125"), D("6105")) and 14 in quote.gold and not quote.silver
    quote = edahab.parse(page("edahab"))
    assert quote.gold[21] == (D("6125"), D("6104")) and 22 not in quote.gold
    with pytest.raises(FeedError):
        edahab.parse("<html>maintenance</html>")


def test_implausible_prices_are_refused():
    with pytest.raises(FeedError):
        Quote(gold={24: (D(7000), D(6900))}).check("x")  # no 21K
    with pytest.raises(FeedError):
        Quote(gold={21: (D(6000), D(6100))}).check("x")  # buy above sell
    with pytest.raises(FeedError):
        Quote(gold={21: (D(6000), D(5900)), 18: (D(6100), D(6000))}).check("x")  # out of order
    with pytest.raises(FeedError):
        Quote(gold={21: (D(61), D(60))}).check("x")  # a hundredth of the price
    assert Quote(gold={21: (D(6000), D(5950))}).check("x")


def _fake(monkeypatch, prices: dict):
    """Sources reading `prices[key]` (a (sell, buy) pair, or an exception)."""
    def reader(key):
        def read():
            value = prices[key]
            if isinstance(value, Exception):
                raise value
            return Quote(gold={21: (D(value[0]), D(value[1]))}).check(key)
        return read

    fakes = {key: SimpleNamespace(LABEL=key.title(), URL="", read=reader(key)) for key in prices}
    monkeypatch.setattr(service, "SOURCES", fakes)
    import apps.platform.market.sources as sources

    monkeypatch.setattr(sources, "SOURCES", fakes)
    return fakes


@pytest.mark.django_db
def test_polling_keeps_health_and_only_real_moves(monkeypatch):
    prices = {"isagha": ("6125", "6075"), "goldbullion": ("6125", "6105"),
              "edahab": FeedError("edahab: timed out")}
    _fake(monkeypatch, prices)
    changed = service.poll()
    assert {q.source.key for q in changed} == {"isagha", "goldbullion"}
    failing = MarketSource.objects.get(key="edahab")
    assert failing.failures == 1 and "timed out" in failing.last_error
    assert service.poll() == []  # nothing moved: no new rows
    assert MarketQuote.objects.count() == 2

    prices["isagha"] = ("6140", "6090")
    prices["edahab"] = ("6125", "6104")
    assert {q.source.key for q in service.poll()} == {"isagha", "edahab"}
    assert MarketSource.objects.get(key="edahab").failures == 0


@pytest.mark.django_db
def test_the_market_price_is_cross_checked(monkeypatch):
    prices = {"isagha": ("6500", "6450"), "goldbullion": ("6125", "6105"),
              "edahab": ("6130", "6104")}
    _fake(monkeypatch, prices)
    service.poll()
    MarketSource.objects.filter(key="isagha").update(priority=1)  # preferred, but out of line
    with translation.override("en"):
        best = service.market_price()
        assert best.source.key == "goldbullion" and set(best.agreeing) == {"goldbullion", "edahab"}
        assert any("Isagha" in note for note in best.notes)
        pinned = service.market_price("edahab")
        assert pinned.source.key == "edahab" and pinned.sell21 == D("6130")
        fallback = service.market_price("isagha")
        assert fallback.source.key == "goldbullion"
        assert any("not available" in note for note in fallback.notes)

        later = timezone.now() + timedelta(hours=1)  # nobody answered for an hour
        assert service.market_price(now=later) is None
        MarketSource.objects.update(is_enabled=False)
        assert service.market_price() is None


@pytest.mark.django_db
def test_the_poll_task_tells_the_shops(monkeypatch, tenant_a, tenant_b):
    from apps.platform.jobs.worker import run_pending
    from apps.platform.market.jobs import poll_market

    prices = {"isagha": ("6125", "6075")}
    _fake(monkeypatch, prices)
    poll_market()
    jobs = Job.objects.filter(name="pricing.follow_market")
    assert set(jobs.values_list("tenant_id", flat=True)) == {tenant_a.id, tenant_b.id}
    poll_market()  # no move: no new jobs
    assert jobs.count() == 2
    run_pending()  # the shops do not follow the market yet: nothing published
    assert set(Job.objects.values_list("status", flat=True)) == {"done"}


def test_interval_schedules(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from apps.platform.jobs import registry
    from apps.platform.jobs.worker import Scheduler

    schedule = next(s for s in registry.SCHEDULES if s.task == "market.poll")
    assert schedule.every_minutes == 3
    monkeypatch.setattr(registry, "SCHEDULES", [schedule])
    queued = []
    monkeypatch.setattr("apps.platform.jobs.worker.enqueue",
                        lambda name, **kw: queued.append(kw["dedupe_key"]) or object())
    scheduler = Scheduler()
    cairo = ZoneInfo("Africa/Cairo")
    for minute, second in ((0, 5), (1, 0), (2, 59), (3, 0), (4, 30), (6, 1)):
        scheduler.tick(datetime(2026, 10, 7, 10, minute, second, tzinfo=cairo))
    assert len(queued) == 3 and len(set(queued)) == 3  # :00, :03 and :06 buckets


@pytest.mark.django_db
def test_console_market_page(monkeypatch):
    from apps.iam.models import User

    _fake(monkeypatch, {"isagha": ("6125", "6075"), "goldbullion": ("6125", "6105")})
    staff = User.objects.create_user(email="ops@gweb.test", password="pw-123456789",
                                     is_platform_staff=True)
    client = Client(HTTP_HOST="admin.localhost")
    client.cookies["django_language"] = "en"
    client.force_login(staff)
    assert "No source has answered" in client.get("/market/").content.decode()
    assert client.post("/market/read/").status_code == 302
    html = client.get("/market/").content.decode()
    assert "6,125.00" in html and "Answering" in html
    source = MarketSource.objects.get(key="goldbullion")
    client.post(f"/market/{source.pk}/", {"action": "up"})
    assert list(MarketSource.objects.values_list("key", flat=True))[0] == "goldbullion"
    client.post(f"/market/{source.pk}/", {"action": "disable"})
    source.refresh_from_db()
    assert not source.is_enabled
    assert Client(HTTP_HOST="admin.localhost").get("/market/").status_code == 302
