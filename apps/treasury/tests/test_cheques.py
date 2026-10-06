from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.errors import DomainError, ValidationError
from apps.core.tenancy import tenant_context
from apps.ledger.models import JournalLine
from apps.ledger.selectors import party_balance, trial_balance
from apps.parties.services import PartyData, create_customer, create_supplier
from apps.sales.tests.test_sales import Shop
from apps.treasury import reconciliation as recon
from apps.treasury.cheques import (
    ChequeInput,
    bounce_cheque,
    cancel_cheque,
    clear_cheque,
    deposit_cheque,
    endorse_cheque,
    record_cheque,
    return_cheque,
)
from apps.treasury.holders import balance
from apps.treasury.models import ChequeStatus
from conftest import login

pytestmark = pytest.mark.django_db
DUE = timezone.localdate() + timedelta(days=30)


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        shop = Shop()
        shop.mona = create_customer(PartyData(name="Mona"))
        shop.nile = create_supplier(PartyData(name="Nile Gold"))
        yield shop


def received(shop, amount="5000", number="100200"):
    return record_cheque(ChequeInput(
        direction="received", side="customer", party_id=shop.mona.pk, branch_id=shop.branch.pk,
        cheque_number=number, due_date=DUE, amount=amount, drawn_on="Banque Misr"))


def issued(shop, amount="3000", number="900001"):
    return record_cheque(ChequeInput(
        direction="issued", side="supplier", party_id=shop.nile.pk, branch_id=shop.branch.pk,
        cheque_number=number, due_date=DUE, amount=amount, bank_account_id=shop.bank.pk))


def held(account_role):
    rows = {row.account.role: row for row in trial_balance().rows}
    row = rows.get(account_role)
    return (row.debit - row.credit) if row else 0


def bank(shop):
    return balance(shop.bank.account_id)[0]


def balanced():
    tb = trial_balance()
    return tb.total_debit == tb.total_credit


def test_received_cheque_life(shop):
    cheque = received(shop)
    assert cheque.state == ChequeStatus.IN_HAND and "-CHR-" in cheque.number
    assert party_balance(shop.mona) == {"EGP": Decimal("-5000")}  # paid by cheque
    assert held("cheques_received") == 5000

    cheque = deposit_cheque(cheque.pk, shop.bank.pk)
    assert cheque.state == ChequeStatus.DEPOSITED and bank(shop) == 0  # not paid yet
    cheque = clear_cheque(cheque.pk)
    assert cheque.state == ChequeStatus.CLEARED and bank(shop) == 5000
    assert held("cheques_received") == 0

    # The bank takes it back later: Mona owes the money again.
    cheque = bounce_cheque(cheque.pk, reason="Insufficient funds")
    assert cheque.state == ChequeStatus.BOUNCED and cheque.void_reason == "Insufficient funds"
    assert bank(shop) == 0 and party_balance(shop.mona) == {}
    assert balanced()


def test_bounce_return_endorse_cancel(shop):
    first = bounce_cheque(received(shop, number="1").pk)
    assert held("cheques_received") == 0 and party_balance(shop.mona) == {}
    second = return_cheque(received(shop, number="2").pk, reason="Replaced")
    assert second.state == ChequeStatus.RETURNED and party_balance(shop.mona) == {}

    # Endorsed to the supplier: their balance (we owe them) goes down by the cheque.
    third = endorse_cheque(received(shop, number="3").pk, side="supplier", party_id=shop.nile.pk)
    assert third.state == ChequeStatus.ENDORSED and third.endorsed_to == shop.nile
    assert party_balance(shop.nile) == {"EGP": Decimal("5000")}
    assert held("cheques_received") == 0

    fourth = cancel_cheque(received(shop, number="4").pk, reason="Typo")
    assert fourth.status == "voided" and party_balance(shop.mona) == {"EGP": Decimal("-5000")}
    with pytest.raises(DomainError):  # nothing more happens to a cancelled cheque
        clear_cheque(fourth.pk, bank_account_id=shop.bank.pk)
    with pytest.raises(DomainError):  # a bounced cheque cannot be cleared or cancelled
        clear_cheque(first.pk, bank_account_id=shop.bank.pk)
    with pytest.raises(DomainError):
        cancel_cheque(first.pk)
    with pytest.raises(ValidationError):  # a received cheque clears into a bank account
        clear_cheque(received(shop, number="5").pk)
    assert balanced()


def test_issued_cheque(shop):
    with pytest.raises(ValidationError):  # drawn on one of our bank accounts
        record_cheque(ChequeInput(direction="issued", side="supplier", party_id=shop.nile.pk,
                                  branch_id=shop.branch.pk, cheque_number="1", due_date=DUE,
                                  amount="10"))
    cheque = issued(shop)
    assert cheque.state == ChequeStatus.OUTSTANDING
    assert party_balance(shop.nile) == {"EGP": Decimal("3000")}  # paid them
    assert held("cheques_payable") == -3000 and bank(shop) == 0
    with pytest.raises(DomainError):  # issued cheques are not deposited
        deposit_cheque(cheque.pk, shop.bank.pk)
    clear_cheque(cheque.pk)
    assert bank(shop) == -3000 and held("cheques_payable") == 0

    other = bounce_cheque(issued(shop, number="900002").pk)
    assert other.state == ChequeStatus.BOUNCED and party_balance(shop.nile) == {
        "EGP": Decimal("3000")}
    assert balanced()


def test_bank_reconciliation(shop):
    clear_cheque(deposit_cheque(received(shop).pk, shop.bank.pk).pk)  # +5000
    clear_cheque(issued(shop).pk)  # -3000
    lines = list(JournalLine.objects.filter(account_id=shop.bank.account_id).order_by("id"))
    today = timezone.localdate()

    # The statement shows both, less a 10 charge we had not booked: 1,990.
    statement = recon.start(shop.bank.pk, today, "1990")
    assert [ln.pk for ln in recon.candidates(statement)] == [ln.pk for ln in lines]
    recon.tick(statement.pk, [ln.pk for ln in lines])
    figures = recon.summary(statement)
    assert (figures.ticked, figures.difference, figures.book) == (2000, -10, 2000)
    with pytest.raises(DomainError):
        recon.complete(statement.pk)
    recon.add_bank_item(statement.pk, kind="charge", amount="10", memo="Monthly fee")
    assert recon.summary(statement).difference == 0
    assert held("bank_charges") == 10
    statement = recon.complete(statement.pk)
    assert statement.is_completed

    # The next statement starts from 1,990 with nothing left to tick.
    following = recon.start(shop.bank.pk, today, "1990")
    assert list(recon.candidates(following)) == []
    figures = recon.summary(following)
    assert (figures.opening, figures.cleared, figures.difference) == (1990, 1990, 0)
    with pytest.raises(DomainError):  # one at a time
        recon.reopen(statement.pk)
    recon.discard(following.pk)
    recon.reopen(statement.pk)
    assert len(recon.candidates(statement)) == 3  # its lines are unticked-able again
    with pytest.raises(ValidationError):  # a line from another account
        other = JournalLine.objects.exclude(account_id=shop.bank.account_id).first()
        recon.tick(statement.pk, [other.pk])


def test_api_and_pages(tenant_a, shop):
    client = login(tenant_a, language="en")
    made = client.post("/api/v1/treasury/cheques/", {
        "direction": "received", "side": "customer", "party": shop.mona.pk,
        "branch": shop.branch.pk, "cheque_number": "777", "due_date": DUE.isoformat(),
        "amount": "1500", "drawn_on": "QNB"}, content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="chq1")
    assert made.status_code == 201, made.content
    cheque = made.json()["id"]
    deposited = client.post(f"/api/v1/treasury/cheques/{cheque}/deposit/",
                            {"bank_account": shop.bank.pk}, content_type="application/json")
    assert deposited.status_code == 200 and deposited.json()["state"] == "deposited"
    cleared = client.post(f"/api/v1/treasury/cheques/{cheque}/clear/", {},
                          content_type="application/json", HTTP_IDEMPOTENCY_KEY="chq1c")
    assert cleared.status_code == 200, cleared.content

    started = client.post("/api/v1/treasury/reconciliations/", {
        "bank_account": shop.bank.pk, "statement_date": timezone.localdate().isoformat(),
        "statement_balance": "1500"}, content_type="application/json")
    assert started.status_code == 201, started.content
    rec = started.json()["id"]
    line = JournalLine.objects.get(account_id=shop.bank.account_id)
    ticked = client.post(f"/api/v1/treasury/reconciliations/{rec}/tick/", {"lines": [line.pk]},
                         content_type="application/json")
    assert ticked.status_code == 200 and ticked.json()["difference"] == "0.00"
    done = client.post(f"/api/v1/treasury/reconciliations/{rec}/complete/", {},
                       content_type="application/json")
    assert done.status_code == 200, done.content

    for path in ("/treasury/cheques/", "/treasury/cheques/?direction=issued",
                 "/treasury/cheques/?state=all", "/treasury/cheques/new/",
                 "/treasury/cheques/new/?direction=issued", f"/treasury/cheques/{cheque}/",
                 f"/treasury/reconcile/{shop.bank.pk}/",
                 f"/treasury/reconcile/{shop.bank.pk}/?id={rec}"):
        assert client.get(path).status_code == 200, path
    page = client.get(f"/treasury/cheques/{cheque}/").content.decode()
    assert "777" in page and "Cleared" in page
