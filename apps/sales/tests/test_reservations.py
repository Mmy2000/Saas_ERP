from decimal import Decimal

import pytest

from apps.core.errors import DomainError, ValidationError
from apps.core.models import DocStatus
from apps.core.tenancy import tenant_context
from apps.inventory.models import Item, ItemStatus
from apps.inventory.selectors import stock_by_karat
from apps.ledger.models import Account
from apps.ledger.selectors import party_balance, trial_balance
from apps.parties.services import PartyData, create_customer
from apps.pricing.services import KaratPriceInput, PublishPriceBoardCommand, publish_price_board
from apps.sales.models import PaymentKind
from apps.sales.reservations import (
    ReservationInput,
    add_deposit,
    cancel_reservation,
    complete_reservation,
    create_reservation,
)
from apps.sales.services import PaymentInput, post_sale, quote_sale
from apps.sales.tests.test_sales import RING_A_TOTAL, Shop
from apps.treasury.holders import balance, default_cash_box
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        shop = Shop()
        shop.customer = create_customer(PartyData(name="Mona"))
        yield shop


def reserve(shop, *items, deposit="5000", locked=False):
    return create_reservation(ReservationInput(
        branch_id=shop.branch.pk, customer_id=shop.customer.pk, price_locked=locked,
        lines=tuple(shop.line(item) for item in items),
        deposit=shop.cash(deposit) if deposit else None))


def deposits_held(shop) -> Decimal:
    account = Account.objects.get(role="customer_deposits")
    return -party_balance(shop.customer, account).get("EGP", Decimal(0))


def cash(shop) -> Decimal:
    return balance(default_cash_box(shop.branch, shop.bank.currency).account_id)[0]


class TestReserve:
    def test_pieces_are_held_and_the_deposit_is_a_liability(self, shop):
        reservation = reserve(shop, shop.ring_a)
        assert reservation.number == "01-RS-2026-000001" and reservation.state == "open"
        assert reservation.quoted_total == RING_A_TOTAL
        shop.ring_a.refresh_from_db()
        assert shop.ring_a.status == ItemStatus.RESERVED
        assert deposits_held(shop) == 5000 and cash(shop) == 5000
        assert reservation.deposits.get().number == "01-RD-2026-000001"
        # Still owned stock, but nobody can sell it at the counter.
        assert sum(line.pieces for line in stock_by_karat()) == 2
        with pytest.raises(ValidationError):
            post_sale(shop.sale(shop.line(shop.ring_a), payments=[shop.cash(RING_A_TOTAL)]))

    def test_rules(self, shop):
        with pytest.raises(ValidationError):
            create_reservation(ReservationInput(branch_id=shop.branch.pk, customer_id=None,
                                                lines=(shop.line(shop.ring_a),)))
        with pytest.raises(ValidationError):  # a deposit cannot be spent outside its reservation
            quote_sale(shop.sale(shop.line(shop.ring_a), customer_id=shop.customer.pk,
                                 payments=[PaymentInput(kind=PaymentKind.DEPOSIT,
                                                        currency_code="EGP", amount="10")]))


class TestComplete:
    def test_price_follows_the_board_unless_locked(self, shop):
        open_price = reserve(shop, shop.ring_a)
        locked = reserve(shop, shop.ring_b, locked=True)
        publish_price_board(PublishPriceBoardCommand(
            reference=KaratPriceInput(shop.k21.pk, "4200", "4150", "4100")))
        rest = RING_A_TOTAL  # generous; the change comes back in cash
        sale = complete_reservation(open_price.pk, payments=[shop.cash(rest)])
        assert sale.total_amount > RING_A_TOTAL  # repriced on the new board
        kinds = {p.kind: p.amount for p in sale.payments.all()}
        assert kinds[PaymentKind.DEPOSIT] == 5000
        locked_sale = complete_reservation(locked.pk, payments=[shop.cash("30000")])
        locked.refresh_from_db()
        assert locked_sale.total_amount == locked.quoted_total
        assert locked.state == "completed" and deposits_held(shop) == 0
        assert Item.objects.get(pk=shop.ring_b.pk).status == ItemStatus.SOLD
        with pytest.raises(DomainError):
            complete_reservation(locked.pk)
        tb = trial_balance()
        assert tb.total_debit == tb.total_credit

    def test_deposit_larger_than_the_price_becomes_credit(self, shop):
        reservation = reserve(shop, shop.ring_a, deposit="20000")
        complete_reservation(reservation.pk)
        assert deposits_held(shop) == 0
        assert party_balance(shop.customer) == {"EGP": RING_A_TOTAL - 20000}

    def test_on_account(self, shop):
        reservation = reserve(shop, shop.ring_a, deposit="1000")
        add_deposit(reservation.pk, PaymentInput(kind="card", currency_code="EGP", amount="2000"))
        reservation.refresh_from_db()
        assert reservation.deposit_amount == 3000 and balance(shop.terminal.account_id)[0] == 2000
        complete_reservation(reservation.pk, payment_terms="credit")
        assert party_balance(shop.customer) == {"EGP": RING_A_TOTAL - 3000}


class TestCancel:
    def test_refund_in_cash(self, shop):
        reservation = reserve(shop, shop.ring_a)
        cancel_reservation(reservation.pk, refund_method="cash")
        reservation.refresh_from_db()
        assert reservation.status == DocStatus.VOIDED and reservation.state == "cancelled"
        assert Item.objects.get(pk=shop.ring_a.pk).status == ItemStatus.IN_STOCK
        assert deposits_held(shop) == 0 and cash(shop) == 0
        assert reservation.deposits.order_by("id").last().amount == -5000

    def test_credit_to_the_account(self, shop):
        reservation = reserve(shop, shop.ring_a)
        cancel_reservation(reservation.pk, refund_method="customer_credit")
        assert party_balance(shop.customer) == {"EGP": Decimal("-5000")}
        assert cash(shop) == 5000


def test_api_and_pages(tenant_a, shop):
    client = login(tenant_a)
    made = client.post("/api/v1/sales/reservations/", {
        "branch": shop.branch.pk, "customer": shop.customer.pk, "price_locked": True,
        "lines": [{"barcode": shop.ring_a.barcode}],
        "deposit": {"kind": "cash", "currency": "EGP", "amount": "2500"}},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="rs1")
    assert made.status_code == 201, made.content
    rid = made.json()["id"]
    assert made.json()["deposit_amount"] == "2500.00"
    for path in ("/sales/reservations/", "/sales/reservations/new/", f"/sales/reservations/{rid}/",
                 f"/customers/{shop.customer.pk}/"):
        assert client.get(path).status_code == 200, path
    done = client.post(f"/api/v1/sales/reservations/{rid}/complete/", {
        "payments": [{"kind": "cash", "currency": "EGP", "amount": str(RING_A_TOTAL)}]},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="rs1c")
    assert done.status_code == 200, done.content
    assert done.json()["state"] == "completed" and done.json()["sale_id"]
    assert client.get(f"/sales/reservations/{rid}/").status_code == 200
    assert client.get(f"/sales/{done.json()['sale_id']}/").status_code == 200


class TestDepositsOnTheCustomer:
    def test_customer_page_statement_and_dashboard_show_deposits(self, tenant_a, shop):
        from apps.iam.authz import build_actor
        from apps.iam.models import Membership
        from apps.org.dashboard import figures

        reservation = reserve(shop, shop.ring_a, deposit="5000")
        other = create_customer(PartyData(name="Hoda"))
        client = login(tenant_a, language="en")

        page = client.get(f"/customers/{shop.customer.pk}/").content.decode()
        assert "Deposits held" in page and "5,000.00" in page
        assert f"/sales/reservations/?customer={shop.customer.pk}" in page
        assert "Deposits held" not in client.get(f"/customers/{other.pk}/").content.decode()

        statement = client.get(f"/customers/{shop.customer.pk}/statement/").content.decode()
        deposits = statement.split("Deposits held for reservations", 1)[1]
        assert reservation.number in deposits and "5,000.00" in deposits

        listed = client.get(f"/sales/reservations/?customer={other.pk}").content.decode()
        assert reservation.number not in listed and "One customer" in listed
        assert reservation.number in client.get(
            f"/sales/reservations/?customer={shop.customer.pk}").content.decode()

        with tenant_context(tenant_a.id):
            data = figures(build_actor(Membership.objects.get(username="owner")))
        customers = next(g for g in data["balances"] if g.url == "/customers/")
        assert {a.commodity.code: a.quantity for a in customers.deposits} == {
            "EGP": Decimal("-5000")}
