"""The bell (legacy Notifications app): who is told about what, reading, muting, the morning
round, and the endpoints."""

from datetime import timedelta

import pytest
from django.utils import timezone, translation

from apps.core.tenancy import tenant_context
from apps.iam.authz import build_actor, members_who_can
from apps.iam.models import Membership
from apps.inventory.tests.test_transfers_stocktakes import Stock
from apps.inventory.transfers import (
    TransferInput,
    TransferLineInput,
    receive_transfer,
    send_transfer,
    void_transfer,
)
from apps.notifications import events, services
from apps.notifications.models import Notification
from apps.pricing.feed import dismiss_held
from apps.pricing.services import KaratPriceInput, PublishPriceBoardCommand, publish_price_board
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def stock(tenant_a, make_member):
    with tenant_context(tenant_a.id):
        stock = Stock()
    # A manager of the receiving branch only, a manager of the sending branch only, a viewer.
    stock.maadi = make_member(tenant_a, "maadi", "manager", branches=[stock.other])
    stock.zamalek = make_member(tenant_a, "zamalek", "manager", branches=[stock.branch])
    stock.viewer = make_member(tenant_a, "viewer")
    with tenant_context(tenant_a.id):
        stock.owner = Membership.objects.get(username="owner")
        yield stock


def actor(membership):
    return build_actor(Membership.objects.get(pk=membership.pk))


def told(kind, membership=None):
    rows = Notification.objects.filter(kind=kind)
    if membership is not None:
        rows = rows.filter(membership=membership)
    return rows


def ship(stock):
    return send_transfer(TransferInput(
        from_branch_id=stock.branch.pk, to_branch_id=stock.other.pk,
        lines=(TransferLineInput(barcode=stock.ring_a.barcode),)),
        actor=actor(stock.owner))


def test_only_people_who_can_receive_in_that_branch_are_told(stock):
    assert set(members_who_can("inventory.transfer.receive", stock.other)) == {stock.owner,
                                                                              stock.maadi}
    transfer = ship(stock)
    # The owner sent it, so is not told; the Zamalek manager cannot receive at Maadi; the viewer
    # cannot receive at all.
    assert set(told("stock_transfer").values_list("membership__username", flat=True)) == {
        "maadi"}
    note = told("stock_transfer").get()
    with translation.override("en"):
        title, body = note.text
    assert title == f"Goods on their way from {stock.branch.name}"
    assert transfer.number in body and "1 item" in body and "g." in body
    assert note.url == f"/stock/transfers/{transfer.pk}/"
    assert services.unread_count(actor(stock.maadi)) == 1

    receive_transfer(transfer.pk)  # dealt with: read for everyone
    assert services.unread_count(actor(stock.maadi)) == 0
    assert told("stock_transfer").get().read_at is not None


def test_a_cancelled_transfer_leaves_nothing_to_do(stock):
    transfer = ship(stock)
    void_transfer(transfer.pk)
    assert not told("stock_transfer").filter(read_at__isnull=True).exists()


def test_mute_read_and_lost_permission(stock):
    maadi = actor(stock.maadi)
    services.set_muted(maadi, {"stock_transfer", "no-such-kind"})
    assert {p["kind"]: p["on"] for p in services.preferences(maadi)}["stock_transfer"] is False
    ship(stock)
    assert not told("stock_transfer", stock.maadi).exists()

    services.set_muted(maadi, set())
    transfer2 = send_transfer(TransferInput(
        from_branch_id=stock.branch.pk, to_branch_id=stock.other.pk,
        lines=(TransferLineInput(barcode=stock.ring_b.barcode),)))
    note = told("stock_transfer", stock.maadi).get()
    assert note.key == f"stock_transfer:{transfer2.pk}"
    services.mark_read(actor(stock.viewer), note.pk)  # not theirs: nothing happens
    assert told("stock_transfer", stock.maadi).get().read_at is None
    services.mark_read(maadi, note.pk)
    assert services.unread_count(maadi) == 0
    # The viewer never sees a kind their role cannot act on.
    assert "stock_transfer" not in {p["kind"] for p in services.preferences(actor(stock.viewer))}


def test_only_the_latest_gold_price_stays_unread(stock):
    for price in ("4100", "4200"):
        publish_price_board(PublishPriceBoardCommand(
            reference=KaratPriceInput(stock.k21.pk, price, "4000", "3900")))
    viewer = told("gold_price", stock.viewer).filter(read_at__isnull=True)
    assert viewer.count() == 1
    with translation.override("en"):
        assert viewer.get().text[1].startswith("21")

    events.price_held("6.5")
    assert told("price_held", stock.owner).filter(read_at__isnull=True).exists()
    assert not told("price_held", stock.viewer).exists()  # cannot approve prices
    dismiss_held()
    assert not told("price_held").filter(read_at__isnull=True).exists()


def test_the_morning_round(stock):
    from apps.repairs.models import RepairOrder
    from apps.repairs.tests.test_repairs import take_in  # noqa: PLC0415

    today = timezone.localdate()
    repair = take_in(stock, customer=False)
    RepairOrder.objects.filter(pk=repair.pk).update(promised_on=today)
    assert events.due_today(today) >= 1
    assert told("repairs_due", stock.zamalek).exists()
    assert not told("repairs_due", stock.maadi).exists()  # another branch's repairs
    with translation.override("en"):
        assert "1 repair at" in told("repairs_due", stock.zamalek).get().text[1]
    events.due_today(today)  # the job ran twice: still once each
    assert told("repairs_due", stock.zamalek).count() == 1

    old = told("repairs_due", stock.zamalek).get()
    Notification.objects.filter(pk=old.pk).update(read_at=timezone.now() - timedelta(days=91))
    assert services.clean_up() >= 1
    assert not Notification.objects.filter(pk=old.pk).exists()


def test_bell_endpoints_and_page(tenant_a, stock):
    ship(stock)
    client = login(tenant_a, "maadi", language="en")
    assert client.get("/api/v1/notifications/?count=1").json() == {"unread": 1}
    data = client.get("/api/v1/notifications/").json()
    assert data["unread"] == 1 and data["results"][0]["title"].startswith("Goods on their way")
    assert any(item["label"] == "Transfers waiting to be received" for item in data["attention"])
    pk = data["results"][0]["id"]

    page = client.get("/").content.decode()
    assert "data-bell" in page and 'data-bell-count>1<' in page.replace(" hidden", "")
    page = client.get("/notifications/").content.decode()
    assert "Goods on their way" in page and "Tell me about" in page

    assert client.post(f"/api/v1/notifications/{pk}/read/").json() == {"unread": 0}
    muted = client.put("/api/v1/notifications/preferences/", {"muted": ["gold_price"]},
                       content_type="application/json")
    assert muted.status_code == 200
    assert {p["kind"]: p["on"] for p in muted.json()["results"]}["gold_price"] is False
    bad = client.put("/api/v1/notifications/preferences/", {"muted": ["nope"]},
                     content_type="application/json")
    assert bad.status_code == 400
    assert client.post("/api/v1/notifications/read-all/").json() == {"unread": 0}
