"""Parcels returned to the supplier, moved between branches and counted (none of which Gweb did
for parcels: it returned, moved and counted single stones only)."""

# ruff: noqa: F811  (pytest fixtures imported from other test modules)
from decimal import Decimal

import pytest

from apps.core.errors import DomainError, ValidationError
from apps.core.tenancy import tenant_context
from apps.diamonds import parcels
from apps.diamonds.models import ParcelMovement, StoneParcel
from apps.diamonds.tests.test_diamonds import balanced, net, shop  # noqa: F401
from apps.diamonds.tests.test_parcels import melee  # noqa: F401
from apps.ledger.models import JournalLine
from apps.ledger.selectors import party_balance
from apps.org.services import BranchInput, create_branch
from conftest import login

pytestmark = pytest.mark.django_db


def parcel_of(melee):
    melee.parcel.refresh_from_db()
    return melee.parcel


def test_return_to_the_supplier(melee):
    returned = parcels.deal(parcels.DealInput(
        parcel_id=melee.parcel.pk, kind="return", carats="1", count=50, price_per_ct="",
        payment="account", party_id=melee.dealer.pk))
    assert returned.number.startswith("01-PX-")
    assert returned.amount == returned.cost_amount == Decimal("11000.00")  # the average
    parcel = parcel_of(melee)
    assert (parcel.carats, parcel.count, parcel.cost_amount) == (
        Decimal("2.000"), 100, Decimal("22000.00"))
    assert party_balance(melee.dealer) == {"EGP": Decimal("-22000")}  # 33,000 − 11,000
    assert net("inventory_diamonds") == 22000 and balanced()
    agreed = parcels.deal(parcels.DealInput(
        parcel_id=melee.parcel.pk, kind="return", carats="0.5", count=0, price_per_ct="10000",
        payment="account", party_id=melee.dealer.pk))
    assert agreed.amount == Decimal("5000.00") and agreed.cost_amount == Decimal("5500.00")
    assert net("cogs_diamonds") == 500 and balanced()  # 500 less than it cost
    with pytest.raises(ValidationError):  # a supplier is needed
        parcels.deal(parcels.DealInput(parcel_id=melee.parcel.pk, kind="return", carats="0.1",
                                       price_per_ct="", payment="account"))
    parcels.void_deal(returned.pk)
    assert parcel_of(melee).carats == Decimal("2.500")


def test_moved_to_another_branch(melee):
    maadi = create_branch(BranchInput(code=2, name="Maadi"))
    moved = parcels.transfer_parcel(melee.parcel.pk, to_branch_id=maadi.pk, carats="1.5",
                                    count=75)
    assert moved.number.startswith("01-PT-") and moved.cost_amount == Decimal("16500.00")
    target = moved.to_parcel
    assert target.branch == maadi and target.code == "M-01-2" and target.color == "G"
    assert (target.carats, target.count, target.cost_amount) == (
        Decimal("1.500"), 75, Decimal("16500.00"))
    clearing = JournalLine.objects.filter(account__role="branch_clearing",
                                          entry=moved.journal_entry)
    assert {(line.branch_id, line.quantity) for line in clearing} == {
        (melee.branch.pk, Decimal("16500")), (maadi.pk, Decimal("-16500"))}
    again = parcels.transfer_parcel(melee.parcel.pk, to_branch_id=maadi.pk, carats="0.5",
                                    to_parcel_id=target.pk)
    assert again.to_parcel == target
    with pytest.raises(ValidationError):  # not to its own branch
        parcels.transfer_parcel(melee.parcel.pk, to_branch_id=melee.branch.pk, carats="0.1")
    with pytest.raises(ValidationError):  # more than there is
        parcels.transfer_parcel(melee.parcel.pk, to_branch_id=maadi.pk, carats="5")
    parcels.void_transfer(again.pk)
    target.refresh_from_db()
    assert target.carats == Decimal("1.500") and parcel_of(melee).carats == Decimal("1.500")
    assert balanced()


def test_counted(melee):
    other = parcels.save_parcel(parcels.ParcelInput(code="E-01", branch_id=melee.branch.pk,
                                                    kind="emerald"))
    count = parcels.count_parcels(melee.branch.pk, [
        parcels.CountedParcel(melee.parcel.pk, "2.9", 148),
        parcels.CountedParcel(other.pk, "0.2", 3)])
    assert count.number.startswith("01-PC-")
    lines = {line.parcel_id: line for line in count.lines.all()}
    assert lines[melee.parcel.pk].cost_amount == Decimal("-1100.00")  # 0.1 ct at 11,000
    assert lines[other.pk].cost_amount == 0  # found in an empty parcel: no cost known
    parcel = parcel_of(melee)
    assert (parcel.carats, parcel.count) == (Decimal("2.900"), 148)
    assert net("stone_loss") == 1100 and net("inventory_diamonds") == 31900 and balanced()
    assert ParcelMovement.objects.filter(kind="count").count() == 2
    with pytest.raises(ValidationError):  # another branch's parcel
        maadi = create_branch(BranchInput(code=2, name="Maadi"))
        parcels.count_parcels(maadi.pk, [parcels.CountedParcel(melee.parcel.pk, "1", 1)])
    parcels.void_count(count.pk)
    assert parcel_of(melee).carats == Decimal("3.000") and not net("stone_loss")
    with pytest.raises(DomainError):
        parcels.void_count(count.pk)


def test_pages(tenant_a, melee):
    with tenant_context(tenant_a.id):
        maadi = create_branch(BranchInput(code=2, name="Maadi"))
    client = login(tenant_a, language="en")
    page = client.get(f"/diamonds/parcels/{melee.parcel.pk}/").content.decode()
    assert "Return to the supplier" in page and "Move to another branch" in page
    assert "A new parcel at Maadi" in page
    moved = client.post("/api/v1/diamonds/parcels/transfers/", {
        "parcel": melee.parcel.pk, "to_branch": maadi.pk, "carats": "0.5", "count": 10},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="pt1")
    assert moved.status_code == 201, moved.content
    assert "Moved between branches" in client.get(
        f"/diamonds/parcels/{melee.parcel.pk}/").content.decode()
    form = client.get(f"/diamonds/parcels/count/?branch={melee.branch.pk}").content.decode()
    assert "M-01" in form and "Post the count" in form
    counted = client.post("/api/v1/diamonds/parcels/counts/", {
        "branch": melee.branch.pk, "rows": [{"parcel": melee.parcel.pk, "carats": "2.5",
                                             "count": 140}]},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="pc1")
    assert counted.status_code == 201, counted.content
    detail = client.get(f"/diamonds/parcels/counts/{counted.json()['id']}/").content.decode()
    assert "Parcels counted" in detail and "M-01" in detail
    with tenant_context(tenant_a.id):
        assert StoneParcel.objects.filter(branch=maadi).count() == 1
