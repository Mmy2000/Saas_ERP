"""quote_sale as it behaves today, recorded in snapshots/quote_sale.json, so its refactoring
(TD-10) can be proven to change nothing: every price, total, problem and error, for pieces,
bulk gold, diamonds, offers, trade-ins, currencies, credit terms and reservations.

To re-record after an intended change: RECORD_SNAPSHOTS=1 (tests/snapshot.py).
"""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.utils import timezone, translation

from apps.catalog.models import ProductFamily, Tracking
from apps.catalog.services import CreateItemCategoryCommand, create_item_category
from apps.core.errors import AppError
from apps.core.tenancy import tenant_context
from apps.diamonds.tests.test_diamonds import receive_ring, switch_on
from apps.iam.authz import build_actor
from apps.merchandising.offers import save_offer
from apps.parties.services import PartyData, create_customer, create_supplier
from apps.pricing.services import (
    KaratPriceInput,
    PublishPriceBoardCommand,
    publish_price_board,
    record_fx_rate,
)
from apps.sales.reservations import ReservationInput, create_reservation
from apps.sales.services import (
    PaymentInput,
    SaleInput,
    SaleLineInput,
    TradeInInput,
    quote_sale,
)
from apps.sales.tests.test_bulk import Chain
from tests.snapshot import assert_snapshots

pytestmark = pytest.mark.django_db
SNAPSHOT = Path(__file__).parent / "snapshots" / "quote_sale.json"
D = Decimal


def pay(kind, amount, currency="EGP"):
    return PaymentInput(kind=kind, currency_code=currency, amount=str(amount))


def snapshot(quote):
    """The quote, with the board identified by its 21K price (rows have no stable id)."""
    if isinstance(quote, AppError):
        return quote
    board_price = quote.board.price(quote.board.lines.get(karat__code=21).karat)
    return {"quote": quote, "board_21k": board_price,
            "reservation": quote.reservation is not None}


@pytest.fixture
def world(tenant_a, make_member):
    """A shop with two 18K rings, 20 g of 21K chain, a diamond ring, an offer on the chain
    (40 % off making), cashback on rings, USD at 50, a customer, a seller with the manager
    role, and ring B reserved for the customer at a locked price before the price went up."""
    switch_on(tenant_a)
    seller = make_member(tenant_a, "seller", "manager")
    with tenant_context(tenant_a.id):
        shop = Chain()
        shop.mountings = create_item_category(CreateItemCategoryCommand(
            code="3010", name="Diamond rings", product_family=ProductFamily.DIAMOND,
            tracking=Tracking.SERIALIZED, barcode_prefix=3010))
        shop.dealer = create_supplier(PartyData(name="Antwerp Diamonds"))
        shop.diamond = receive_ring(shop)
        shop.customer = create_customer(PartyData(name="Mona"))
        shop.other = create_customer(PartyData(name="Hoda"))
        record_fx_rate("USD", "50")
        today = timezone.localdate()
        for offer in ({"name": "Chain week", "kind": "making_percent", "value": D("40"),
                       "category_ids": [shop.chain.pk]},
                      {"name": "Ring gift", "kind": "cashback_amount", "value": D("200"),
                       "category_ids": [shop.ring_a.category_id]}):
            save_offer({"starts_on": today, "ends_on": today + timedelta(days=3), **offer})
        shop.reservation = create_reservation(ReservationInput(
            branch_id=shop.branch.pk, customer_id=shop.customer.pk, price_locked=True,
            lines=(shop.line(shop.ring_b),), deposit=shop.cash("5000")))
        publish_price_board(PublishPriceBoardCommand(
            reference=KaratPriceInput(shop.k21.pk, "4100", "4050", "4000")))
        shop.seller = build_actor(seller)
        yield shop


def scenarios(w):
    k18, k21 = w.k18.pk, w.k21.pk
    ring, diamond, reserved = w.line(w.ring_a), w.line(w.diamond), w.line(w.ring_b)
    return {
        "one piece, no payment": lambda: quote_sale(w.sale(ring)),
        "one piece, paid in cash with change": lambda: quote_sale(
            w.sale(ring, payments=[pay("cash", 30000)])),
        "one piece, discount, limited seller": lambda: quote_sale(
            w.sale(w.line(w.ring_a, "0.10")), actor=w.seller),
        "one piece, discount to the floor, owner": lambda: quote_sale(
            w.sale(w.line(w.ring_a, "0.90"))),
        "bulk with an offer and a discount": lambda: quote_sale(w.sale(SaleLineInput(
            category_id=w.chain.pk, karat_id=k21, gross_weight_g="5", qty=1,
            discount_rate="0.05"))),
        "bulk, more than the lot in two lines": lambda: quote_sale(w.sale(
            w.grams("15", qty=2), w.grams("6", qty=1))),
        "bulk, more pieces than the lot": lambda: quote_sale(w.sale(w.grams("5", qty=5))),
        "bulk of a kind not in stock": lambda: quote_sale(w.sale(SaleLineInput(
            category_id=w.chain.pk, karat_id=k18, gross_weight_g="1"))),
        "diamond piece with a discount, customer": lambda: quote_sale(w.sale(
            w.line(w.diamond, "0.05"), customer_id=w.customer.pk)),
        "diamond piece, seller": lambda: quote_sale(w.sale(diamond), actor=w.seller),
        "pieces, trade-in and card, customer": lambda: quote_sale(w.sale(
            ring, diamond, customer_id=w.customer.pk,
            trade_ins=[TradeInInput(karat_id=k18, gross_weight_g="10", loss_weight_g="0.5")],
            payments=[pay("card", 10000)])),
        "trade-in with a price override, owner": lambda: quote_sale(w.sale(
            ring, trade_ins=[TradeInInput(karat_id=k18, gross_weight_g="4",
                                          price_override="3000")])),
        "trade-in with a price override, seller": lambda: quote_sale(w.sale(
            ring, trade_ins=[TradeInInput(karat_id=k18, gross_weight_g="4",
                                          price_override="3000")]), actor=w.seller),
        "trade-in worth more than the goods": lambda: quote_sale(w.sale(
            ring, trade_ins=[TradeInInput(karat_id=k21, gross_weight_g="20")])),
        "trade-in worth more, and a card": lambda: quote_sale(w.sale(
            ring, trade_ins=[TradeInInput(karat_id=k21, gross_weight_g="20")],
            payments=[pay("card", 1000)])),
        "dollars and pounds": lambda: quote_sale(w.sale(
            ring, payments=[pay("cash", 200, "USD"), pay("cash", 5000)])),
        "card pays too much": lambda: quote_sale(w.sale(ring, payments=[pay("card", 99999)])),
        "bank transfer and cash, cash short": lambda: quote_sale(w.sale(
            ring, payments=[pay("bank_transfer", 10000), pay("cash", 100)])),
        "credit, customer, part paid": lambda: quote_sale(w.sale(
            ring, payment_terms="credit", customer_id=w.customer.pk,
            payments=[pay("cash", 5000)])),
        "credit without a customer": lambda: quote_sale(w.sale(ring, payment_terms="credit")),
        "credit paid too much": lambda: quote_sale(w.sale(
            ring, payment_terms="credit", customer_id=w.customer.pk,
            payments=[pay("cash", 99999)])),
        "nothing on the sale": lambda: quote_sale(w.sale()),
        "nothing on the sale, a payment": lambda: quote_sale(w.sale(
            payments=[pay("cash", 100)])),
        "the same piece twice": lambda: quote_sale(w.sale(ring, ring)),
        "unknown barcode": lambda: quote_sale(w.sale(SaleLineInput(barcode="999999"))),
        "a reserved piece outside its reservation": lambda: quote_sale(w.sale(reserved)),
        "unknown branch": lambda: quote_sale(SaleInput(branch_id=999999, lines=(ring,))),
        "unknown customer": lambda: quote_sale(w.sale(ring, customer_id=999999)),
        "unknown trade-in karat": lambda: quote_sale(w.sale(
            ring, trade_ins=[TradeInInput(karat_id=999999, gross_weight_g="1")])),
        "unknown payment method": lambda: quote_sale(w.sale(ring, payments=[
            pay("cash", 0), pay("cheque", 100)])),
        "unknown payment currency": lambda: quote_sale(w.sale(ring, payments=[
            pay("cash", 100, "XYZ")])),
        "a zero payment is skipped": lambda: quote_sale(w.sale(ring, payments=[
            pay("cash", 0), pay("card", "0.00")])),
        "a deposit without a reservation": lambda: quote_sale(w.sale(
            ring, payments=[pay("deposit", 100)])),
        "completing the reservation": lambda: quote_sale(w.sale(
            reserved, customer_id=w.customer.pk, reservation_id=w.reservation.pk,
            payments=[pay("deposit", 5000), pay("cash", 20000)])),
        "the reservation, plus another piece": lambda: quote_sale(w.sale(
            reserved, ring, customer_id=w.customer.pk, reservation_id=w.reservation.pk,
            payments=[pay("deposit", 5000)])),
        "the reservation for another customer": lambda: quote_sale(w.sale(
            reserved, customer_id=w.other.pk, reservation_id=w.reservation.pk)),
        "the reservation without a customer": lambda: quote_sale(w.sale(
            reserved, reservation_id=w.reservation.pk)),
        "a deposit larger than the reservation's": lambda: quote_sale(w.sale(
            reserved, customer_id=w.customer.pk, reservation_id=w.reservation.pk,
            payments=[pay("deposit", 6000)])),
        "a deposit in dollars": lambda: quote_sale(w.sale(
            reserved, customer_id=w.customer.pk, reservation_id=w.reservation.pk,
            payments=[pay("deposit", 10, "USD")])),
        "a reservation that is not open": lambda: quote_sale(w.sale(
            reserved, customer_id=w.customer.pk, reservation_id=999999)),
    }


def test_quote_sale_is_unchanged(world):
    results = {}
    # Messages in one language: an earlier test can leave another one active.
    with translation.override("en"):
        for name, run in scenarios(world).items():
            try:
                results[name] = snapshot(run())
            except AppError as exc:
                results[name] = exc
        assert_snapshots(SNAPSHOT, results)
