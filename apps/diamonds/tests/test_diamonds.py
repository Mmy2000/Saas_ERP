from decimal import Decimal

import pytest
from django.core.cache import cache

from apps.catalog.models import ProductFamily, Tracking
from apps.catalog.services import CreateItemCategoryCommand, create_item_category
from apps.core.errors import PermissionDenied, ValidationError
from apps.core.tenancy import tenant_context
from apps.diamonds.models import ItemStone
from apps.diamonds.services import (
    PieceInput,
    ReceiveInput,
    SettingInput,
    cancel_setting,
    receive,
    set_stones,
)
from apps.inventory.models import Item, ItemStatus
from apps.ledger.selectors import party_balance, trial_balance
from apps.parties.services import PartyData, create_supplier
from apps.platform.tenants import features
from apps.platform.tenants.models import TenantFeature
from apps.sales.returns import create_return
from apps.sales.services import post_sale, quote_sale
from apps.sales.tests.test_sales import Shop

pytestmark = pytest.mark.django_db

SOLITAIRE = ({"kind": "diamond", "shape": "round", "count": 1, "carat": "0.50", "color": "D",
              "clarity": "VS1", "cut": "Excellent", "lab": "GIA", "certificate_no": "2141438171"},
             {"kind": "diamond", "count": 12, "carat": "0.24"})


def switch_on(tenant):
    TenantFeature.objects.update_or_create(tenant=tenant, key="diamonds",
                                           defaults={"enabled": True})
    features.forget(tenant.pk)


@pytest.fixture
def shop(tenant_a):
    cache.clear()
    switch_on(tenant_a)
    with tenant_context(tenant_a.id):
        shop = Shop()
        shop.mountings = create_item_category(CreateItemCategoryCommand(
            code="3010", name="Diamond rings", product_family=ProductFamily.DIAMOND,
            tracking=Tracking.SERIALIZED, barcode_prefix=3010))
        shop.loose = create_item_category(CreateItemCategoryCommand(
            code="3900", name="Loose diamonds", product_family=ProductFamily.STONE,
            tracking=Tracking.SERIALIZED, barcode_prefix=3900))
        shop.dealer = create_supplier(PartyData(name="Antwerp Diamonds"))
        yield shop


def receive_ring(shop, label="60000", stones=SOLITAIRE, gross="5.200", stone_cost="30000"):
    invoice = receive(ReceiveInput(
        supplier_id=shop.dealer.pk, branch_id=shop.branch.pk, currency_code="EGP",
        pieces=(PieceInput(category_id=shop.mountings.pk, karat_id=shop.k18.pk,
                           gross_weight_g=gross, making_cost_rate="100", stone_cost=stone_cost,
                           label_price=label, stones=stones),)))
    return Item.objects.get(pk=invoice.lines.get().pieces.get().item_id)


def roles():
    return {row.account.role: row for row in trial_balance().rows}


def net(role):
    row = roles().get(role)
    return (row.debit - row.credit) if row else 0


def balanced():
    tb = trial_balance()
    return tb.total_debit == tb.total_credit


def test_off_unless_switched_on_for_the_client(tenant_a):
    cache.clear()
    assert not features.BY_KEY["diamonds"].default
    with tenant_context(tenant_a.id), pytest.raises(PermissionDenied):
        receive(ReceiveInput(supplier_id=1, branch_id=1, currency_code="EGP", pieces=()))
    switch_on(tenant_a)
    assert features.is_enabled(tenant_a.pk, "diamonds")


def test_receive_and_sell_a_diamond_ring(shop):
    ring = receive_ring(shop)
    # 0.74 ct of stones = 0.148 g: the gold is 5.052 g of 18K, 3.789 fine.
    assert (ring.stone_weight_ct, ring.metal_weight_g, ring.fine_weight_g) == (
        Decimal("0.740"), Decimal("5.052"), Decimal("3.7890"))
    assert (ring.stone_cost_amount, ring.cost_amount, ring.label_price) == (
        Decimal("30000"), Decimal("505.20"), Decimal("60000"))
    assert ItemStone.objects.filter(item=ring).count() == 2
    # We owe the dealer the gold (fine grams) and the making and stones (money).
    assert party_balance(shop.dealer) == {"XAU": Decimal("-3.789"), "EGP": Decimal("-30505.2")}
    assert net("inventory_diamonds") == 30000
    assert balanced()

    # 10 % off the label price; the gold part is today's 18K price × 5.052 g.
    quote = quote_sale(shop.sale(shop.line(ring, "0.10")))
    line = quote.lines[0]
    assert line.line_total == Decimal("54000.00")
    assert line.metal_amount == Decimal("17321.14")  # 3428.57 × 5.052
    assert line.stones_amount == Decimal("36678.86")
    # A huge discount stops at what the ring cost: gold today + making + stones.
    floor = quote_sale(shop.sale(shop.line(ring, "0.90"))).lines[0]
    assert floor.line_total == Decimal("17321.14") + Decimal("505.20") + Decimal("30000")
    assert floor.at_cost_floor

    invoice = post_sale(shop.sale(shop.line(ring), payments=[shop.cash("60000")]))
    assert net("sales_diamonds") == -(Decimal("60000") - Decimal("17321.14"))
    assert net("cogs_diamonds") == 30000 and net("inventory_diamonds") == 0
    assert balanced()

    create_return(invoice.pk, [invoice.lines.get().pk], refund_method="cash")
    ring.refresh_from_db()
    assert ring.status == ItemStatus.IN_STOCK
    assert net("inventory_diamonds") == 30000 and net("sales_diamonds") == 0
    assert balanced()


def test_loose_stones_set_into_a_mounting(shop):
    invoice = receive(ReceiveInput(
        supplier_id=shop.dealer.pk, branch_id=shop.branch.pk, currency_code="EGP", pieces=(
            PieceInput(category_id=shop.loose.pk, stone_cost="8000", label_price="15000",
                       stones=({"kind": "diamond", "carat": "0.30", "color": "F",
                                "clarity": "VVS2"},)),
            PieceInput(category_id=shop.mountings.pk, karat_id=shop.k18.pk,
                       gross_weight_g="3.000", label_price="9000"))))
    stone, mounting = (Item.objects.get(pk=line.pieces.get().item_id)
                       for line in invoice.lines.order_by("position"))
    assert stone.karat is None and stone.gross_weight_g == Decimal("0.060")
    assert net("inventory_diamonds") == 8000

    setting = set_stones(SettingInput(piece_id=mounting.pk, stone_ids=(stone.pk,),
                                      gross_after_g="3.060", labour_amount="500"))
    stone.refresh_from_db()
    mounting.refresh_from_db()
    assert stone.status == ItemStatus.CONSUMED
    assert (mounting.stone_weight_ct, mounting.stone_cost_amount, mounting.gross_weight_g) == (
        Decimal("0.300"), Decimal("8000"), Decimal("3.060"))
    assert mounting.cost_amount == Decimal("500")  # the setting labour
    assert ItemStone.objects.filter(item=mounting, setting=setting).count() == 1
    assert net("labour_absorbed") == -500 and net("inventory_diamonds") == 8000
    assert balanced()
    with pytest.raises(ValidationError):  # the stone is used
        set_stones(SettingInput(piece_id=mounting.pk, stone_ids=(stone.pk,),
                                gross_after_g="3.1"))

    cancel_setting(setting.pk)
    stone.refresh_from_db()
    mounting.refresh_from_db()
    assert stone.status == ItemStatus.IN_STOCK
    assert (mounting.stone_weight_ct, mounting.stone_cost_amount, mounting.cost_amount) == (
        0, 0, 0)
    assert not ItemStone.objects.filter(item=mounting).exists()
    assert net("labour_absorbed") == 0
    assert balanced()


def test_api_and_pages(tenant_a, shop):
    from conftest import login

    client = login(tenant_a, language="en")
    made = client.post("/api/v1/diamonds/receive/", {
        "supplier": shop.dealer.pk, "branch": shop.branch.pk, "currency": "EGP",
        "pieces": [{"category": shop.mountings.pk, "karat": shop.k18.pk,
                    "gross_weight_g": "4.100", "making_cost_rate": "90", "stone_cost": "21000",
                    "label_price": "42000",
                    "stones": [{"kind": "diamond", "carat": "0.40", "color": "G",
                                "clarity": "VS2", "lab": "IGI", "certificate_no": "LG55"}]},
                   {"category": shop.loose.pk, "stone_cost": "5000", "label_price": "9000",
                    "stones": [{"kind": "ruby", "carat": "1.10"}]}]},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="dia1")
    assert made.status_code == 201, made.content
    with tenant_context(tenant_a.id):
        ring = Item.objects.get(category=shop.mountings)
        ruby = Item.objects.get(category=shop.loose)
    found = client.get("/api/v1/diamonds/lookup/?family=stone&q=LG55")
    assert found.status_code == 404  # LG55 is the ring's stone, not a loose one
    assert client.get(f"/api/v1/diamonds/lookup/?family=stone&q={ruby.barcode}").json()[
        "results"][0]["id"] == ruby.pk
    edited = client.put(f"/api/v1/diamonds/items/{ring.pk}/stones/", {
        "label_price": "45000", "stones": [{"kind": "diamond", "carat": "0.40", "color": "F"}]},
        content_type="application/json")
    assert edited.status_code == 200 and edited.json()["label_price"] == "45000.00"
    set_ = client.post("/api/v1/diamonds/settings/", {
        "piece": ring.pk, "stones": [ruby.pk], "gross_after_g": "4.320", "labour_amount": "300"},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="dia2")
    assert set_.status_code == 201, set_.content
    setting = set_.json()["id"]
    for path in ("/diamonds/", "/diamonds/?state=all&family=stone", "/diamonds/receive/",
                 "/diamonds/settings/", "/diamonds/settings/new/", f"/diamonds/settings/{setting}/",
                 f"/stock/items/{ring.pk}/", "/reports/diamond_stock/", "/reports/diamond_sales/"):
        response = client.get(path)
        assert response.status_code == 200, path
    page = client.get(f"/stock/items/{ring.pk}/").content.decode()
    assert "Label price" in page and "45,000.00" in page
    assert "Diamonds and stones" in client.get("/").content.decode()  # in the menu
    voided = client.post(f"/api/v1/diamonds/settings/{setting}/void/", {"reason": "Wrong stone"},
                         content_type="application/json", HTTP_IDEMPOTENCY_KEY="dia3")
    assert voided.status_code == 200 and voided.json()["status"] == "voided"


def test_switched_off_hides_everything(tenant_a):
    from conftest import login

    cache.clear()
    client = login(tenant_a, language="en")
    home = client.get("/").content.decode()
    assert "Receive diamonds" not in home
    for path in ("/diamonds/", "/diamonds/receive/", "/reports/diamond_stock/"):
        assert client.get(path).status_code == 403, path
    refused = client.post("/api/v1/diamonds/receive/", {}, content_type="application/json",
                          HTTP_IDEMPOTENCY_KEY="dia9")
    assert refused.status_code == 403
    form = client.get("/catalog/categories/new/").content.decode()
    assert 'value="gold"' in form and 'value="diamond"' not in form  # no diamond families
