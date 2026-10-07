"""Buying old diamonds (legacy GHD40): each thing bought is a barcoded piece in stock."""

from decimal import Decimal

import pytest
from django.core.cache import cache

from apps.core.errors import DomainError, ValidationError
from apps.core.tenancy import tenant_context
from apps.diamonds import buyback
from apps.diamonds.models import ItemStone
from apps.diamonds.tests.test_diamonds import balanced, net, shop, switch_on  # noqa: F401
from apps.inventory.models import ItemStatus
from apps.ledger.selectors import party_balance
from apps.parties.services import PartyData, create_customer
from apps.sales.services import post_sale
from conftest import login

pytestmark = pytest.mark.django_db


def ring(shop, price="30000", **kw):  # noqa: F811
    return buyback.PieceInput(category_id=shop.mountings.pk, karat_id=shop.k18.pk,
                              gross_weight_g="5.000", stones_carat="0.50", stones_count=1,
                              stones_note="D VS1, GIA", price=price, **kw)


def test_an_old_ring_becomes_a_piece_in_stock(shop):  # noqa: F811
    gold = buyback.gold_value(shop.k18, Decimal("5.000"), Decimal("0.50"))
    assert gold > 0
    bought = buyback.buy_back(buyback.BuyBackInput(
        branch_id=shop.branch.pk, pieces=(ring(shop),), payment="bank_transfer",
        seller_name="Hassan", seller_phone="01011112222", seller_id_number="29001011234567",
        bank_account_id=shop.bank.pk))
    assert bought.number == "01-DB-2026-000001" and bought.total_amount == Decimal("30000")
    item = bought.lines.get().item
    # 5 g with 0.5 ct of stones (0.1 g): 4.9 g of 18K = 3.675 fine g.
    assert item.status == ItemStatus.IN_STOCK and item.fine_weight_g == Decimal("3.6750")
    assert item.stone_weight_ct == Decimal("0.500") and item.label_price is None
    assert item.stone_cost_amount == Decimal("30000") - gold and item.cost_amount == 0
    stone = ItemStone.objects.get(item=item)
    assert (stone.carat, stone.note) == (Decimal("0.500"), "D VS1, GIA")
    assert bought.seller_id_encrypted and "2900" not in bought.seller_id_encrypted
    assert net("inventory_diamonds") == Decimal("30000") - gold
    assert balanced()


def test_loose_stones_cheap_gold_and_on_account(shop):  # noqa: F811
    mona = create_customer(PartyData(name="Mona"))
    gold = buyback.gold_value(shop.k18, Decimal("5.000"), Decimal("0.50"))
    cheap = str(gold - 1000)
    bought = buyback.buy_back(buyback.BuyBackInput(
        branch_id=shop.branch.pk, payment="account", seller_id=mona.pk, pieces=(
            buyback.PieceInput(category_id=shop.loose.pk, stones_carat="0.30", price="5000"),
            ring(shop, price=cheap))))
    stone, piece = (line.item for line in bought.lines.order_by("pk"))
    assert stone.karat is None and stone.gross_weight_g == Decimal("0.060")
    assert stone.stone_cost_amount == Decimal("5000")
    # Paid less than its gold is worth: all of the price is gold, nothing on the stones.
    assert piece.stone_cost_amount == 0 and bought.gold_value == Decimal(cheap)
    assert party_balance(mona) == {"EGP": -(Decimal("5000") + Decimal(cheap))}  # we owe her
    assert balanced()
    with pytest.raises(ValidationError):  # a walk-in cannot be paid on account
        buyback.buy_back(buyback.BuyBackInput(branch_id=shop.branch.pk, payment="account",
                                              seller_name="X", pieces=(ring(shop),)))
    with pytest.raises(ValidationError):
        buyback.buy_back(buyback.BuyBackInput(branch_id=shop.branch.pk, payment="cash",
                                              seller_name="X", pieces=(ring(shop, price=""),)))


def test_cancelling_while_untouched(shop):  # noqa: F811
    first = buyback.buy_back(buyback.BuyBackInput(
        branch_id=shop.branch.pk, pieces=(ring(shop, label_price="45000"),),
        payment="bank_transfer", seller_name="Hassan", bank_account_id=shop.bank.pk))
    buyback.void_buy_back(first.pk, reason="wrong weight")
    first.refresh_from_db()
    assert first.status == "voided" and first.lines.get().item.status == ItemStatus.VOIDED
    assert net("inventory_diamonds") == 0 and balanced()

    second = buyback.buy_back(buyback.BuyBackInput(
        branch_id=shop.branch.pk, pieces=(ring(shop, label_price="45000"),),
        payment="bank_transfer", seller_name="Hassan", bank_account_id=shop.bank.pk))
    item = second.lines.get().item
    post_sale(shop.sale(shop.line(item), payment_terms="credit",
                        customer_id=create_customer(PartyData(name="Sara")).pk))
    with pytest.raises(DomainError):  # sold since
        buyback.void_buy_back(second.pk)


def test_api_and_pages(tenant_a):
    cache.clear()
    switch_on(tenant_a)
    client = login(tenant_a, language="en")
    with tenant_context(tenant_a.id):
        from apps.catalog.models import ProductFamily, Tracking
        from apps.catalog.services import CreateItemCategoryCommand, create_item_category
        from apps.sales.tests.test_sales import Shop

        shop_ = Shop()
        rings = create_item_category(CreateItemCategoryCommand(
            code="3010", name="Diamond rings", product_family=ProductFamily.DIAMOND,
            tracking=Tracking.SERIALIZED, barcode_prefix=3010))
    piece = {"category": rings.pk, "karat": shop_.k18.pk, "gross_weight_g": "5",
             "stones_carat": "0.5", "price": "30000"}
    quote = client.post("/api/v1/diamonds/buy-back/quote/", {"pieces": [piece]},
                        content_type="application/json").json()
    assert Decimal(str(quote["pieces"][0]["gold_value"])) > 0
    made = client.post("/api/v1/diamonds/buy-back/", {
        "branch": shop_.branch.pk, "pieces": [piece], "payment": "bank_transfer",
        "bank_account": shop_.bank.pk, "seller_name": "Hassan"},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="b1")
    assert made.status_code == 201, made.content
    pk = made.json()["id"]
    for path in ("/diamonds/buy-back/", "/diamonds/buy-back/new/", f"/diamonds/buy-back/{pk}/"):
        assert client.get(path).status_code == 200, path
    assert "Hassan" in client.get(f"/diamonds/buy-back/{pk}/").content.decode()
