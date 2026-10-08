"""Stone parcels kept by weight (legacy برتيتات: GHZ11, GHZ16, GHZ71, and parcels set by the
Install screens)."""

from decimal import Decimal

import pytest
from django.core.cache import cache

from apps.core.errors import ValidationError
from apps.core.tenancy import tenant_context
from apps.diamonds import jobs, parcels
from apps.diamonds.models import ItemStone, StoneParcel
from apps.diamonds.services import SettingInput, cancel_setting, set_stones
from apps.diamonds.tests.test_diamonds import (  # noqa: F401
    balanced,
    net,
    receive_ring,
    shop,
    switch_on,
)
from apps.ledger.selectors import party_balance
from apps.parties.services import PartyData, create_workshop
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def melee(shop):  # noqa: F811
    shop.parcel = parcels.save_parcel(parcels.ParcelInput(
        code="M-01", branch_id=shop.branch.pk, kind="diamond", shape="round",
        size="0.01–0.02 ct", color="G", clarity="VS", sale_price_per_ct="20000"))
    parcels.deal(parcels.DealInput(parcel_id=shop.parcel.pk, kind="buy", carats="2", count=100,
                                   price_per_ct="10000", payment="account",
                                   party_id=shop.dealer.pk))
    parcels.deal(parcels.DealInput(parcel_id=shop.parcel.pk, kind="buy", carats="1", count=50,
                                   price_per_ct="13000", payment="account",
                                   party_id=shop.dealer.pk))
    shop.parcel.refresh_from_db()
    return shop


def test_bought_by_the_carat_at_an_average_cost(melee):
    parcel = melee.parcel
    assert (parcel.carats, parcel.count, parcel.cost_amount) == (Decimal("3.000"), 150,
                                                                 Decimal("33000.00"))
    assert parcel.cost_per_ct == Decimal("11000.00")
    assert party_balance(melee.dealer) == {"EGP": Decimal("-33000")}
    assert net("inventory_diamonds") == 33000 and balanced()

    sold = parcels.deal(parcels.DealInput(parcel_id=parcel.pk, kind="sell", carats="0.5",
                                          count=25, price_per_ct="20000", payment="cash",
                                          party_name="Walk-in"))
    assert sold.amount == Decimal("10000.00") and sold.cost_amount == Decimal("5500.00")
    assert net("sales_diamonds") == -10000 and net("cogs_diamonds") == 5500
    parcel.refresh_from_db()
    assert (parcel.carats, parcel.count, parcel.cost_amount) == (Decimal("2.500"), 125,
                                                                 Decimal("27500.00"))
    with pytest.raises(ValidationError):  # more than there is
        parcels.deal(parcels.DealInput(parcel_id=parcel.pk, kind="sell", carats="9",
                                       price_per_ct="1", payment="cash", party_name="X"))
    parcels.void_deal(sold.pk)
    parcel.refresh_from_db()
    assert parcel.carats == Decimal("3.000") and parcel.cost_amount == Decimal("33000.00")
    assert net("sales_diamonds") == 0 and balanced()


def test_set_into_a_piece_and_back(melee):
    ring = receive_ring(melee, stones=(), stone_cost="0")
    setting = set_stones(SettingInput(
        piece_id=ring.pk, stone_ids=(), gross_after_g=str(ring.gross_weight_g + Decimal("0.06")),
        parcel_uses=(parcels.ParcelUse(parcel_id=melee.parcel.pk, carats="0.30", count=15),)))
    ring.refresh_from_db()
    assert ring.stone_weight_ct == Decimal("0.300") and ring.stone_cost_amount == Decimal("3300")
    stone = ItemStone.objects.get(item=ring, setting=setting)
    assert (stone.count, stone.color, stone.clarity) == (15, "G", "VS")
    parcel = StoneParcel.objects.get(pk=melee.parcel.pk)
    assert (parcel.carats, parcel.count, parcel.cost_amount) == (Decimal("2.700"), 135,
                                                                 Decimal("29700.00"))
    assert net("inventory_diamonds") == 33000 and balanced()  # the cost only moved

    cancel_setting(setting.pk)
    parcel.refresh_from_db()
    ring.refresh_from_db()
    assert (parcel.carats, parcel.count, parcel.cost_amount) == (Decimal("3.000"), 150,
                                                                 Decimal("33000.00"))
    assert ring.stone_cost_amount == 0 and not ItemStone.objects.filter(item=ring).exists()
    with pytest.raises(ValidationError):  # a parcel can only give what it holds
        set_stones(SettingInput(piece_id=ring.pk, stone_ids=(), gross_after_g="6",
                                parcel_uses=(parcels.ParcelUse(melee.parcel.pk, "5"),)))


def test_a_setter_brings_a_gold_ring_back_with_parcel_stones(melee):
    setter = create_workshop(PartyData(name="Hany the setter"))
    job = jobs.hand_over(jobs.JobInput(branch_id=melee.branch.pk, setter_id=setter.pk,
                                       item_ids=(melee.ring_a.pk,)))
    setting = jobs.set_from_job(job.pk, jobs.SetInput(
        mounting_line_id=job.lines.get().pk, stone_line_ids=(), gross_after_g="5.040",
        labour_amount="200", category_id=melee.mountings.pk,
        parcel_uses=(parcels.ParcelUse(melee.parcel.pk, "0.20", 10),)))
    melee.ring_a.refresh_from_db()
    assert melee.ring_a.stone_cost_amount == Decimal("2200") and setting.stones_carat == Decimal(
        "0.200")
    assert net("inventory_at_workshop") == 0 and balanced()
    cancel_setting(setting.pk)  # back with the setter; the parcel stones back in the parcel
    assert StoneParcel.objects.get(pk=melee.parcel.pk).carats == Decimal("3.000")
    assert balanced()


def test_api_and_pages(tenant_a):
    cache.clear()
    switch_on(tenant_a)
    client = login(tenant_a, language="en")
    with tenant_context(tenant_a.id):
        from apps.parties.services import create_supplier
        from apps.sales.tests.test_sales import Shop

        shop_ = Shop()
        dealer = create_supplier(PartyData(name="Antwerp Diamonds"))
    made = client.post("/api/v1/diamonds/parcels/", {
        "code": "M-01", "branch": shop_.branch.pk, "kind": "diamond", "size": "0.01 ct"},
        content_type="application/json")
    assert made.status_code == 201, made.content
    pk = made.json()["id"]
    bought = client.post("/api/v1/diamonds/parcels/deals/", {
        "parcel": pk, "kind": "buy", "carats": "1.5", "count": 80, "price_per_ct": "9000",
        "payment": "account", "party": dealer.pk}, content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="p1")
    assert bought.status_code == 201, bought.content
    listed = client.get(f"/api/v1/diamonds/parcels/?branch={shop_.branch.pk}").json()
    assert listed["results"][0]["carats"] in ("1.500", 1.5)
    for path in ("/diamonds/parcels/", f"/diamonds/parcels/{pk}/", "/diamonds/settings/new/"):
        assert client.get(path).status_code == 200, path
    page = client.get(f"/diamonds/parcels/{pk}/").content.decode()
    assert "Bought" in page and "Sell by the carat" in page
    assert client.get(f"/diamonds/parcels/deals/{bought.json()['id']}/").status_code == 302
