"""Stones from parcels handed to workshops and setters (legacy Prod GHPR14 / 15 / 16, GHSHH23)."""

from decimal import Decimal

import pytest
from django.core.cache import cache

from apps.core.errors import DomainError, ValidationError
from apps.core.tenancy import tenant_context
from apps.diamonds import handouts, jobs, parcels
from apps.diamonds.models import StoneParcel, WorkshopStones
from apps.diamonds.services import cancel_setting
from apps.diamonds.tests.test_diamonds import balanced, net, shop, switch_on  # noqa: F401
from apps.diamonds.tests.test_parcels import melee  # noqa: F401
from apps.ledger.selectors import party_balance
from apps.parties.services import PartyData, create_workshop
from conftest import login

pytestmark = pytest.mark.django_db


def give(stock, kind, carats, count=0):
    return handouts.handout(handouts.HandoutInput(
        workshop_id=stock.setter.pk, parcel_id=stock.parcel.pk, kind=kind, carats=carats,
        count=count))


def held(stock):
    return WorkshopStones.objects.get(workshop=stock.setter, parcel=stock.parcel)


@pytest.fixture
def out(melee):  # noqa: F811
    melee.setter = create_workshop(PartyData(name="Hany the setter"))
    give(melee, "out", "1", 50)  # the parcel's average is 11,000 a carat
    return melee


def test_handed_out_taken_back_charged_and_lost(out):
    parcel = StoneParcel.objects.get(pk=out.parcel.pk)
    assert (parcel.carats, parcel.count, parcel.cost_amount) == (Decimal("2.000"), 100,
                                                                 Decimal("22000.00"))
    assert (held(out).carats, held(out).count, held(out).cost_amount) == (
        Decimal("1.000"), 50, Decimal("11000.00"))
    assert net("inventory_at_workshop") == 11000 and net("inventory_diamonds") == 22000
    assert balanced()

    give(out, "back", "0.4", 20)
    assert StoneParcel.objects.get(pk=out.parcel.pk).cost_amount == Decimal("26400.00")
    assert held(out).cost_amount == Decimal("6600.00")
    give(out, "charged", "0.1", 5)
    assert party_balance(out.setter) == {"EGP": Decimal("1100")}  # the setter owes it
    lost = give(out, "lost", "0.1", 5)
    assert net("stone_loss") == 1100 and net("inventory_at_workshop") == 4400 and balanced()

    handouts.void_handout(lost.pk)
    assert held(out).carats == Decimal("0.500") and net("stone_loss") == 0
    with pytest.raises(ValidationError):  # more than the setter holds
        give(out, "back", "3")
    with pytest.raises(ValidationError):  # more than the parcel has
        give(out, "out", "9")


def test_cancelling_a_hand_out_needs_the_stones_still_there(out):
    first = out.parcel.handouts.get(kind="out")
    give(out, "lost", "1", 50)
    with pytest.raises(DomainError):  # the setter no longer holds them
        handouts.void_handout(first.pk)


def test_a_setter_sets_the_stones_they_hold(out):
    job = jobs.hand_over(jobs.JobInput(branch_id=out.branch.pk, setter_id=out.setter.pk,
                                       item_ids=(out.ring_a.pk,)))
    with_setter = net("inventory_at_workshop")  # the stones and now the ring
    setting = jobs.set_from_job(job.pk, jobs.SetInput(
        mounting_line_id=job.lines.get().pk, stone_line_ids=(), gross_after_g="5.040",
        category_id=out.mountings.pk,
        parcel_uses=(parcels.ParcelUse(out.parcel.pk, "0.20", 10, held=True),)))
    out.ring_a.refresh_from_db()
    assert out.ring_a.stone_cost_amount == Decimal("2200")  # at the cost it was handed out at
    assert held(out).carats == Decimal("0.800")
    assert StoneParcel.objects.get(pk=out.parcel.pk).carats == Decimal("2.000")  # untouched
    assert net("inventory_at_workshop") == 8800 and balanced()

    cancel_setting(setting.pk)  # back with the setter, and so are the stones
    assert held(out).carats == Decimal("1.000")
    assert net("inventory_at_workshop") == with_setter
    assert balanced()
    with pytest.raises(ValidationError):  # a setter can only set what they hold
        jobs.set_from_job(job.pk, jobs.SetInput(
            mounting_line_id=job.lines.get().pk, stone_line_ids=(), gross_after_g="5.1",
            category_id=out.mountings.pk,
            parcel_uses=(parcels.ParcelUse(out.parcel.pk, "2", held=True),)))


def test_page_api_and_parcel_page(tenant_a):
    cache.clear()
    switch_on(tenant_a)
    client = login(tenant_a, language="en")
    with tenant_context(tenant_a.id):
        from apps.sales.tests.test_sales import Shop

        shop_ = Shop()
        setter = create_workshop(PartyData(name="Atef"))
        parcel = parcels.save_parcel(parcels.ParcelInput(code="M-02", branch_id=shop_.branch.pk))
        from apps.parties.services import create_supplier

        dealer = create_supplier(PartyData(name="Dealer"))
        parcels.deal(parcels.DealInput(parcel_id=parcel.pk, kind="buy", carats="1",
                                       count=10, price_per_ct="9000", payment="account",
                                       party_id=dealer.pk))
    made = client.post("/api/v1/diamonds/handouts/", {
        "workshop": setter.pk, "parcel": parcel.pk, "kind": "out", "carats": "0.5", "count": 4},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="h1")
    assert made.status_code == 201, made.content
    page = client.get("/diamonds/workshop-stones/").content.decode()
    assert "Atef" in page and "M-02" in page and "4,500.00" in page
    assert "With workshops" in client.get(f"/diamonds/parcels/{parcel.pk}/").content.decode()
    assert client.get(f"/api/v1/diamonds/held/?workshop={setter.pk}").json()["results"][0][
        "carats"] in ("0.500", 0.5)
    assert client.get(f"/diamonds/workshop-stones/{made.json()['id']}/").status_code == 302
    bad = client.post("/api/v1/diamonds/handouts/", {
        "workshop": setter.pk, "parcel": parcel.pk, "kind": "back", "carats": "5"},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="h2")
    assert bad.status_code == 400
    void = client.post(f"/api/v1/diamonds/handouts/{made.json()['id']}/void/", {},
                       content_type="application/json", HTTP_IDEMPOTENCY_KEY="h3")
    assert void.json()["status"] == "voided"
