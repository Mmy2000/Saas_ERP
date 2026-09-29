from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from apps.catalog.models import Karat
from apps.core.errors import DomainError, ValidationError
from apps.core.tenancy import tenant_context
from apps.pricing.models import PriceSide
from apps.pricing.selectors import current_price_board, fx_rate, metal_price_per_gram
from apps.pricing.services import (
    KaratPriceInput,
    PublishPriceBoardCommand,
    publish_price_board,
    record_fx_rate,
)
from conftest import PASSWORD

pytestmark = pytest.mark.django_db


def _karat(code):
    return Karat.objects.get(code=code)


def _publish(sell="4000", buy="3950", scrap="3900", overrides=(), **kw):
    return publish_price_board(PublishPriceBoardCommand(
        reference=KaratPriceInput(_karat(21).pk, sell, buy, scrap), overrides=overrides, **kw,
    ))


class TestPublishBoard:
    def test_other_gold_karats_are_derived_and_stored(self, tenant_a):
        with tenant_context(tenant_a.id):
            board = _publish()
            sell = {ln.karat.code: ln.sell_price_per_g
                    for ln in board.lines.select_related("karat")}
            assert sell == {21: Decimal("4000"), 24: Decimal("4570.97"),
                            22: Decimal("4190.48"), 18: Decimal("3428.57"),
                            14: Decimal("2666.67")}
            line_18 = board.lines.get(karat__code=18)
            assert line_18.is_derived
            assert line_18.buy_price_per_g == Decimal("3385.71")  # 3950 × 750/875
            assert line_18.scrap_buy_price_per_g == Decimal("3342.86")  # 3900 × 750/875

    def test_override_wins_over_derivation(self, tenant_a):
        with tenant_context(tenant_a.id):
            board = _publish(overrides=(KaratPriceInput(_karat(24).pk, "4600"),))
            line = board.lines.get(karat__code=24)
            assert (line.sell_price_per_g, line.is_derived) == (Decimal("4600"), False)

    def test_silver_only_when_entered(self, tenant_a):
        with tenant_context(tenant_a.id):
            assert not _publish().lines.filter(karat__code=925).exists()
            board = _publish(overrides=(KaratPriceInput(_karat(925).pk, "55"),))
            assert board.lines.get(karat__code=925).sell_price_per_g == Decimal("55")

    def test_missing_sides_stay_empty(self, tenant_a):
        with tenant_context(tenant_a.id):
            board = _publish(buy=None, scrap=None)
            assert board.lines.get(karat__code=18).buy_price_per_g is None
            with pytest.raises(DomainError) as exc:
                current_price_board().price(_karat(18), PriceSide.BUY)
            assert exc.value.code == "PRICING_SIDE_NOT_ON_BOARD"

    @pytest.mark.parametrize("sell", ["0", "-5"])
    def test_rejects_non_positive_prices(self, tenant_a, sell):
        with tenant_context(tenant_a.id), pytest.raises(ValidationError):
            _publish(sell=sell)

    def test_rejects_karat_of_another_tenant(self, tenant_a, tenant_b):
        with tenant_context(tenant_b.id):
            foreign = _karat(24).pk
        with tenant_context(tenant_a.id), pytest.raises(ValidationError):
            _publish(overrides=(KaratPriceInput(foreign, "4600"),))


class TestCurrentBoard:
    def test_current_is_latest_effective_not_latest_id(self, tenant_a):
        now = timezone.now()
        with tenant_context(tenant_a.id):
            newer = _publish(sell="4100", effective_at=now - timedelta(minutes=5))
            _publish(sell="3900", effective_at=now - timedelta(hours=2))  # back-dated, higher id
            _publish(sell="4500", effective_at=now + timedelta(hours=1))  # scheduled
            assert current_price_board().pk == newer.pk
            price, board = metal_price_per_gram(_karat(21))
            assert (price, board.pk) == (Decimal("4100"), newer.pk)

    def test_as_of_lookup(self, tenant_a):
        now = timezone.now()
        with tenant_context(tenant_a.id):
            old = _publish(sell="3000", effective_at=now - timedelta(days=30))
            _publish(sell="4000", effective_at=now - timedelta(days=1))
            assert current_price_board(at=now - timedelta(days=10)).pk == old.pk

    def test_no_board_yet(self, tenant_a):
        with tenant_context(tenant_a.id), pytest.raises(DomainError) as exc:
            metal_price_per_gram(_karat(21))
        assert exc.value.code == "PRICING_NO_BOARD"

    def test_boards_are_per_tenant(self, tenant_a, tenant_b):
        with tenant_context(tenant_a.id):
            _publish()
        with tenant_context(tenant_b.id):
            assert current_price_board() is None


class TestFxRates:
    def test_functional_currency_is_one(self, tenant_a):
        with tenant_context(tenant_a.id):
            assert fx_rate("EGP") == Decimal(1)

    def test_latest_rate_applies(self, tenant_a):
        now = timezone.now()
        with tenant_context(tenant_a.id):
            record_fx_rate("USD", "48.5", effective_at=now - timedelta(days=2))
            record_fx_rate("USD", "49.12345678", effective_at=now - timedelta(hours=1))
            assert fx_rate("USD") == Decimal("49.12345678")
            assert fx_rate("USD", at=now - timedelta(days=1)) == Decimal("48.5")

    def test_missing_and_invalid_rates(self, tenant_a):
        with tenant_context(tenant_a.id):
            with pytest.raises(DomainError):
                fx_rate("USD")
            with pytest.raises(ValidationError):
                record_fx_rate("EGP", "1")
            with pytest.raises(ValidationError):
                record_fx_rate("USD", "0")
            with pytest.raises(ValidationError):
                record_fx_rate("GBP", "60")


def test_current_board_api(tenant_a):
    with tenant_context(tenant_a.id):
        board = _publish()
    client = Client()
    client.post("/login/", {"username": "owner", "password": PASSWORD},
                HTTP_HOST="alpha.localhost")
    body = client.get("/api/v1/pricing/boards/current/", HTTP_HOST="alpha.localhost").json()
    assert body["id"] == board.pk
    assert len(body["lines"]) == 5
    assert all(isinstance(ln["sell_price_per_g"], str) for ln in body["lines"])  # no floats
