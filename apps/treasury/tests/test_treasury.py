from decimal import Decimal

import pytest
from django.utils import translation

from apps.core.errors import DomainError, ValidationError
from apps.core.models import DocStatus
from apps.core.tenancy import tenant_context
from apps.ledger.models import Account
from apps.ledger.selectors import trial_balance
from apps.org.services import BranchInput, create_branch
from apps.pricing.services import record_fx_rate
from apps.sales.services import PaymentInput, post_sale, quote_sale
from apps.sales.tests.test_sales import RING_A_TOTAL, Shop
from apps.treasury.holders import (
    BankAccountInput,
    CashBoxInput,
    balance,
    create_bank_account,
    create_cash_box,
    default_cash_box,
    set_holder_active,
)
from apps.treasury.models import CashBox
from apps.treasury.services import (
    TreasuryInput,
    post_treasury_document,
    receive_transfer,
    void_treasury_document,
)
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        yield Shop()


def cash_sale(shop, item=None, **payment):
    kind = payment.pop("kind", "cash")
    return post_sale(shop.sale(shop.line(item or shop.ring_a), payments=[PaymentInput(
        kind=kind, currency_code=payment.pop("currency", "EGP"),
        amount=payment.pop("amount", str(RING_A_TOTAL)), **payment)]))


def held(holder) -> Decimal:
    return balance(holder.account_id)[0]


def move(**kw):
    return post_treasury_document(TreasuryInput(**kw))


class TestHolders:
    def test_first_cash_sale_opens_the_branch_box(self, shop):
        assert not CashBox.objects.exists()
        invoice = cash_sale(shop)
        box = CashBox.objects.get()
        assert box.is_default and box.branch == shop.branch and box.account.parent.role == "cash"
        assert invoice.payments.get().cash_box == box
        assert held(box) == RING_A_TOTAL

    def test_groups_cannot_be_posted_to(self, shop):
        for role in ("cash", "bank", "card_receivable"):
            assert not Account.objects.get(role=role).is_postable

    def test_card_goes_to_the_terminal_and_bank_needs_a_choice(self, shop):
        cash_sale(shop, kind="card")
        assert held(shop.terminal) == RING_A_TOTAL
        second = create_bank_account(BankAccountInput(name="NBE", currency_code="EGP"))
        total = str(quote_sale(shop.sale(shop.line(shop.ring_b))).total)
        with pytest.raises(ValidationError) as exc:
            cash_sale(shop, shop.ring_b, kind="bank_transfer", amount=total)
        assert "payments.0.bank_account" in exc.value.fields
        cash_sale(shop, shop.ring_b, kind="bank_transfer", amount=total,
                  bank_account_id=second.pk)
        assert held(second) > 0

    def test_one_default_box_per_branch_and_currency(self, shop):
        main = default_cash_box(shop.branch, shop.bank.currency)
        drawer = create_cash_box(CashBoxInput(branch_id=shop.branch.pk, currency_code="EGP",
                                              name="Drawer 2", is_default=True))
        main.refresh_from_db()
        assert drawer.is_default and not main.is_default
        assert drawer.account.code == "1101002"

    def test_deactivating_needs_an_empty_holder(self, shop):
        cash_sale(shop)
        box = CashBox.objects.get()
        with pytest.raises(DomainError) as exc:
            set_holder_active("box", box.pk, False)
        assert exc.value.code == "TREASURY_HOLDER_NOT_EMPTY"


class TestDocuments:
    def test_deposit_and_cash_limits(self, shop):
        cash_sale(shop)
        box = CashBox.objects.get()
        with pytest.raises(ValidationError):  # more than the drawer holds
            move(kind="transfer", source_box_id=box.pk, dest_bank_id=shop.bank.pk,
                 amount="20000")
        deposit = move(kind="transfer", source_box_id=box.pk, dest_bank_id=shop.bank.pk,
                       amount="10000")
        assert deposit.number == "01-TR-2026-000001"
        with translation.override("en"):
            assert deposit.title == "Bank deposit"
        assert held(box) == RING_A_TOTAL - 10000 and held(shop.bank) == 10000
        void_treasury_document(deposit.pk)
        assert held(box) == RING_A_TOTAL and held(shop.bank) == 0

    def test_cash_to_another_branch_waits_in_transit(self, shop):
        cash_sale(shop)
        here = CashBox.objects.get()
        other = create_branch(BranchInput(code=2, name="Maadi"))
        there = default_cash_box(other, here.currency)
        sent = move(kind="transfer", source_box_id=here.pk, dest_box_id=there.pk, amount="5000")
        assert sent.in_transit and sent.to_branch == other
        assert held(here) == RING_A_TOTAL - 5000 and held(there) == 0
        clearing = trial_balance(branch_ids=[shop.branch.pk])
        assert any(r.account.role == "branch_clearing" and r.debit == 5000 for r in clearing.rows)
        received = receive_transfer(sent.pk)
        assert not received.in_transit and held(there) == 5000
        with pytest.raises(DomainError):
            receive_transfer(sent.pk)
        void_treasury_document(sent.pk)
        assert held(here) == RING_A_TOTAL and held(there) == 0

    def test_exchange_books_the_difference(self, shop):
        record_fx_rate("USD", "48.5")
        cash_sale(shop, currency="USD", amount="400")  # 400 USD carried at 19,400
        usd = CashBox.objects.get(currency__code="USD")
        egp = default_cash_box(shop.branch, shop.bank.currency)
        change_given = RING_A_TOTAL - Decimal("19400")  # handed back from the EGP box
        assert held(egp) == change_given
        exchange = move(kind="exchange", source_box_id=usd.pk, dest_box_id=egp.pk,
                        amount="100", dest_amount="4900")
        assert exchange.rate == Decimal("49") and exchange.functional_amount == Decimal("4850")
        gain = next(r for r in trial_balance().rows if r.account.role == "fx_gain")
        assert gain.credit == Decimal("50")
        assert held(usd) == 300 and held(egp) == change_given + 4900
        with pytest.raises(ValidationError):  # same currency: that is a transfer
            move(kind="exchange", source_box_id=egp.pk, dest_box_id=egp.pk, amount="1",
                 dest_amount="1")

    def test_card_settlement_takes_the_fee(self, shop):
        cash_sale(shop, kind="card")
        with pytest.raises(ValidationError):
            move(kind="card_settlement", source_terminal_id=shop.terminal.pk,
                 branch_id=shop.branch.pk, amount=str(RING_A_TOTAL + 1))
        settled = move(kind="card_settlement", source_terminal_id=shop.terminal.pk,
                       branch_id=shop.branch.pk, amount=str(RING_A_TOTAL))
        assert settled.fee_amount == Decimal("367.86")  # 2 %
        assert held(shop.terminal) == 0
        assert held(shop.bank) == RING_A_TOTAL - Decimal("367.86")
        fees = next(r for r in trial_balance().rows if r.account.role == "card_fees")
        assert fees.debit == Decimal("367.86")
        tb = trial_balance()
        assert tb.total_debit == tb.total_credit
        void_treasury_document(settled.pk)
        assert held(shop.terminal) == RING_A_TOTAL
        settled.refresh_from_db()
        assert settled.status == DocStatus.VOIDED


def test_api_and_pages(tenant_a, shop):
    client = login(tenant_a)
    with tenant_context(tenant_a.id):
        cash_sale(shop)
        box = CashBox.objects.get()
    created = client.post("/api/v1/treasury/cash-boxes/", {
        "branch": shop.branch.pk, "currency": "EGP", "name": "Safe"},
        content_type="application/json")
    assert created.status_code == 201, created.content
    body = {"kind": "transfer", "source_box": box.pk, "dest_box": created.json()["id"],
            "amount": "1000"}
    first = client.post("/api/v1/treasury/documents/", body, content_type="application/json",
                        HTTP_IDEMPOTENCY_KEY="t1")
    again = client.post("/api/v1/treasury/documents/", body, content_type="application/json",
                        HTTP_IDEMPOTENCY_KEY="t1")
    assert first.status_code == 201 and first.json()["id"] == again.json()["id"]
    renamed = client.put(f"/api/v1/treasury/bank-accounts/{shop.bank.pk}/", {
        "name": "CIB current", "currency": "", "branches": [shop.branch.pk]},
        content_type="application/json")
    assert renamed.status_code == 200, renamed.content
    assert renamed.json()["branches"] == [shop.branch.pk] and renamed.json()["currency"] == "EGP"
    terminal = client.put(f"/api/v1/treasury/terminals/{shop.terminal.pk}/", {
        "name": "Front desk", "bank_account": shop.bank.pk, "fee_rate": "0.015"},
        content_type="application/json")
    assert terminal.json()["fee_rate"] == "0.015000"
    for path in ("/treasury/", "/treasury/movements/", "/treasury/movements/new/",
                 "/treasury/movements/new/?kind=exchange",
                 "/treasury/movements/new/?kind=card_settlement",
                 f"/treasury/movements/{first.json()['id']}/", "/treasury/box/new/",
                 f"/print/treasury_document/{first.json()['id']}/",
                 f"/treasury/box/{box.pk}/", f"/treasury/box/{box.pk}/statement/",
                 f"/treasury/bank/{shop.bank.pk}/", f"/treasury/bank/{shop.bank.pk}/statement/",
                 f"/treasury/terminal/{shop.terminal.pk}/", "/treasury/terminal/new/",
                 "/sales/new/", "/settlements/new/"):
        assert client.get(path).status_code == 200, path
    assert client.get("/treasury/nope/new/").status_code == 404
