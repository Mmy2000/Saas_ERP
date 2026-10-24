"""Setters (legacy Install GHN1–GHN4): hand over, set from the job, return unused, undo, and
take stones out again."""

from decimal import Decimal

import pytest
from django.core.cache import cache

from apps.core.errors import DomainError, ValidationError
from apps.core.tenancy import tenant_context
from apps.diamonds import jobs
from apps.diamonds.models import ItemStone, JobLineState
from apps.diamonds.services import PieceInput, ReceiveInput, cancel_setting, receive
from apps.diamonds.tests.test_diamonds import balanced, net, shop, switch_on  # noqa: F401
from apps.inventory.models import Item, ItemStatus
from apps.ledger.selectors import party_balance
from apps.parties.services import PartyData, create_workshop
from conftest import login, this_year

pytestmark = pytest.mark.django_db


def loose_stone(shop):  # noqa: F811
    invoice = receive(ReceiveInput(
        supplier_id=shop.dealer.pk, branch_id=shop.branch.pk, currency_code="EGP", pieces=(
            PieceInput(category_id=shop.loose.pk, stone_cost="8000", label_price="15000",
                       stones=({"kind": "diamond", "carat": "0.30", "color": "F",
                                "clarity": "VVS2"},)),)))
    return Item.objects.get(pk=invoice.lines.get().pieces.get().item_id)


@pytest.fixture
def job(shop):  # noqa: F811
    shop.setter = create_workshop(PartyData(name="Hany the setter"))
    shop.stone = loose_stone(shop)
    shop.job = jobs.hand_over(jobs.JobInput(branch_id=shop.branch.pk, setter_id=shop.setter.pk,
                                            item_ids=(shop.ring_a.pk, shop.stone.pk)))
    return shop


def line(shop, item):  # noqa: F811
    return shop.job.lines.get(item=item)


def test_handing_over_moves_the_goods_to_the_workshop(job):
    assert job.job.number == f"01-SJ-{this_year()}-000001"
    for item in (job.ring_a, job.stone):
        item.refresh_from_db()
        assert item.status == ItemStatus.AT_WORKSHOP
    # Ring A: 3.75 fine g of gold, 750 making; the stone: 8000. All now at the workshop.
    held = net("inventory_at_workshop")
    assert held == line(job, job.ring_a).metal_value + Decimal("750") + Decimal("8000")
    assert net("inventory_diamonds") == 0
    assert balanced()
    with pytest.raises(ValidationError):  # already out
        jobs.hand_over(jobs.JobInput(branch_id=job.branch.pk, setter_id=job.setter.pk,
                                     item_ids=(job.ring_a.pk,)))

    jobs.return_items(job.job.pk, [line(job, job.stone).pk])
    job.stone.refresh_from_db()
    assert job.stone.status == ItemStatus.IN_STOCK and net("inventory_diamonds") == 8000
    job.job.refresh_from_db()
    assert job.job.closed_at is None
    jobs.return_items(job.job.pk, [line(job, job.ring_a).pk])
    job.job.refresh_from_db()
    assert job.job.closed_at is not None and net("inventory_at_workshop") == 0
    assert balanced()


def test_a_gold_ring_comes_back_as_a_diamond_ring(job):
    ring_line, stone_line = line(job, job.ring_a), line(job, job.stone)
    with pytest.raises(ValidationError) as exc:  # a gold mounting needs its new category
        jobs.set_from_job(job.job.pk, jobs.SetInput(
            mounting_line_id=ring_line.pk, stone_line_ids=(stone_line.pk,), gross_after_g="5.06"))
    assert "category" in exc.value.fields
    setting = jobs.set_from_job(job.job.pk, jobs.SetInput(
        mounting_line_id=ring_line.pk, stone_line_ids=(stone_line.pk,), gross_after_g="5.060",
        labour_amount="400", category_id=job.mountings.pk, label_price="35000"))
    ring = Item.objects.get(pk=job.ring_a.pk)
    assert ring.status == ItemStatus.IN_STOCK and ring.category_id == job.mountings.pk
    assert (ring.gross_weight_g, ring.stone_weight_ct, ring.stone_cost_amount) == (
        Decimal("5.060"), Decimal("0.300"), Decimal("8000"))
    assert ring.cost_amount == Decimal("1150") and ring.label_price == Decimal("35000")
    assert Item.objects.get(pk=job.stone.pk).status == ItemStatus.CONSUMED
    assert party_balance(job.setter) == {"EGP": Decimal("-400")}  # we owe the setter
    assert net("inventory_at_workshop") == 0 and net("inventory_diamonds") == 8000
    assert balanced()
    job.job.refresh_from_db()
    assert job.job.closed_at is not None

    # Cancelled while untouched: both are with the setter again, the ring a gold ring.
    cancel_setting(setting.pk, reason="wrong stone")
    ring.refresh_from_db()
    assert ring.status == ItemStatus.AT_WORKSHOP and ring.category_id != job.mountings.pk
    assert ring.cost_amount == Decimal("750") and ring.gross_weight_g == Decimal("5.000")
    assert Item.objects.get(pk=job.stone.pk).status == ItemStatus.AT_WORKSHOP
    assert set(job.job.lines.values_list("state", flat=True)) == {JobLineState.OUT}
    assert party_balance(job.setter) == {}
    assert net("inventory_at_workshop") == ring_line.metal_value + Decimal("750") + 8000
    assert balanced()


def test_stones_taken_out_come_back_with_their_cost(job):
    setting = jobs.set_from_job(job.job.pk, jobs.SetInput(
        mounting_line_id=line(job, job.ring_a).pk, stone_line_ids=(line(job, job.stone).pk,),
        gross_after_g="5.060", labour_amount="400", category_id=job.mountings.pk))
    removal = jobs.remove_stones(jobs.RemovalInput(
        setting_id=setting.pk, stone_ids=(job.stone.pk,), gross_after_g="5.000",
        fee_amount="100", setter_id=job.setter.pk))
    assert removal.number == f"01-SU-{this_year()}-000001"
    stone = Item.objects.get(pk=job.stone.pk)
    ring = Item.objects.get(pk=job.ring_a.pk)
    assert stone.status == ItemStatus.IN_STOCK and stone.stone_cost_amount == Decimal("8000")
    assert (ring.stone_weight_ct, ring.stone_cost_amount, ring.gross_weight_g) == (0, 0,
                                                                                   Decimal("5.000"))
    assert ring.cost_amount == Decimal("1250")  # making 750 + setting 400 + taking out 100
    assert not ItemStone.objects.filter(item=ring).exists()
    assert party_balance(job.setter) == {"EGP": Decimal("-500")}
    assert net("inventory_diamonds") == 8000 and balanced()
    with pytest.raises(DomainError):  # stones came out since
        cancel_setting(setting.pk)
    with pytest.raises(ValidationError):  # already out
        jobs.remove_stones(jobs.RemovalInput(setting_id=setting.pk, stone_ids=(job.stone.pk,),
                                             gross_after_g="5"))


def test_api_and_pages(tenant_a):
    cache.clear()
    switch_on(tenant_a)
    client = login(tenant_a, language="en")
    with tenant_context(tenant_a.id):
        from apps.catalog.models import ProductFamily, Tracking
        from apps.catalog.services import CreateItemCategoryCommand, create_item_category
        from apps.parties.services import create_supplier
        from apps.sales.tests.test_sales import Shop

        shop_ = Shop()
        shop_.loose = create_item_category(CreateItemCategoryCommand(
            code="3900", name="Loose diamonds", product_family=ProductFamily.STONE,
            tracking=Tracking.SERIALIZED, barcode_prefix=3900))
        shop_.dealer = create_supplier(PartyData(name="Antwerp Diamonds"))
        stone = loose_stone(shop_)
        setter = create_workshop(PartyData(name="Hany the setter"))
    found = client.get(f"/api/v1/diamonds/lookup/?family=job&q={shop_.ring_a.barcode}").json()
    assert found["results"][0]["family"] == "gold"
    made = client.post("/api/v1/diamonds/jobs/", {
        "branch": shop_.branch.pk, "setter": setter.pk, "items": [shop_.ring_a.pk, stone.pk]},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="j1")
    assert made.status_code == 201, made.content
    pk = made.json()["id"]
    for path in ("/diamonds/setters/", "/diamonds/setters/new/", f"/diamonds/setters/{pk}/",
                 "/diamonds/setters/?tab=closed"):
        assert client.get(path).status_code == 200, path
    page = client.get(f"/diamonds/setters/{pk}/").content.decode()
    assert "Came back set" in page and "Hany the setter" in page
    with tenant_context(tenant_a.id):
        from apps.diamonds.models import SettingJob

        lines = {ln.item_id: ln.pk for ln in SettingJob.objects.get(pk=pk).lines.all()}
    back = client.post(f"/api/v1/diamonds/jobs/{pk}/return/", {"lines": [lines[stone.pk]]},
                       content_type="application/json", HTTP_IDEMPOTENCY_KEY="j2")
    assert back.status_code == 200 and back.json()["closed"] is False
