"""Bank money sent to another branch (legacy GHBR45-48 تحويل نقدية بنك لفرع / استلام / الخزينة
الوسيطة): it waits in transit until the receiving branch confirms, into a cash box there or as
the same bank account's balance at that branch."""

# ruff: noqa: F811  (pytest fixtures imported from other test modules)
from decimal import Decimal

import pytest

from apps.core.errors import ValidationError
from apps.ledger.selectors import trial_balance
from apps.org.services import BranchInput, create_branch
from apps.treasury.holders import default_cash_box
from apps.treasury.models import CashBox
from apps.treasury.services import receive_transfer, void_treasury_document
from apps.treasury.tests.test_treasury import (
    cash_sale,
    held,
    move,
    shop,  # noqa: F401  (fixture)
)
from conftest import login

pytestmark = pytest.mark.django_db


def _bank_at(account_id, branch) -> Decimal:
    return sum((row.debit - row.credit for row in trial_balance(branch_ids=[branch.pk]).rows
                if row.account.pk == account_id), Decimal(0))


@pytest.fixture
def funded(shop):
    cash_sale(shop)
    box = CashBox.objects.get()
    move(kind="transfer", source_box_id=box.pk, dest_bank_id=shop.bank.pk, amount="10000")
    shop.other = create_branch(BranchInput(code=2, name="Maadi"))
    return shop


def test_bank_to_a_box_at_another_branch(funded):
    there = default_cash_box(funded.other, funded.bank.currency)
    sent = move(kind="transfer", source_bank_id=funded.bank.pk, dest_box_id=there.pk,
                branch_id=funded.branch.pk, amount="4000")
    assert sent.in_transit and (sent.branch, sent.to_branch) == (funded.branch, funded.other)
    assert held(funded.bank) == 6000 and held(there) == 0
    receive_transfer(sent.pk)
    assert held(there) == 4000

    # Without a sending branch it is a withdrawal booked where the box is, as before.
    direct = move(kind="transfer", source_bank_id=funded.bank.pk, dest_box_id=there.pk,
                  amount="1000")
    assert not direct.needs_receipt and held(there) == 5000


def test_a_bank_balance_moved_to_another_branch(funded):
    bank = funded.bank
    with pytest.raises(ValidationError):  # the same account at the same branch
        move(kind="transfer", source_bank_id=bank.pk, dest_bank_id=bank.pk,
             branch_id=funded.branch.pk, amount="100")
    sent = move(kind="transfer", source_bank_id=bank.pk, dest_bank_id=bank.pk,
                branch_id=funded.branch.pk, to_branch_id=funded.other.pk, amount="3000")
    assert sent.in_transit and held(bank) == 7000
    assert _bank_at(bank.account_id, funded.branch) == 7000
    received = receive_transfer(sent.pk)
    assert not received.in_transit and held(bank) == 10000
    assert _bank_at(bank.account_id, funded.other) == 3000
    tb = trial_balance()
    assert tb.total_debit == tb.total_credit
    assert not any(r.account.role == "branch_clearing" and r.debit != r.credit for r in tb.rows)
    void_treasury_document(sent.pk)
    assert _bank_at(bank.account_id, funded.other) == 0 and held(bank) == 10000


def test_form_api_and_print(tenant_a, funded):
    client = login(tenant_a, language="en")
    page = client.get("/treasury/movements/new/").content.decode()
    assert "To branch" in page and "From branch" in page
    made = client.post("/api/v1/treasury/documents/", {
        "kind": "transfer", "source_bank": funded.bank.pk, "dest_bank": funded.bank.pk,
        "branch": funded.branch.pk, "to_branch": funded.other.pk, "amount": "2500"},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="bt1")
    assert made.status_code == 201, made.content
    body = made.json()
    assert body["in_transit"] and body["to_branch"] == funded.other.pk
    detail = client.get(f"/treasury/movements/{body['id']}/").content.decode()
    assert "Maadi" in detail and "Confirm the money arrived" in detail
    printed = client.get(f"/print/treasury_document/{body['id']}/").content.decode()
    assert "Maadi" in printed
    assert "Maadi" in client.get("/stock/in-transit/").content.decode()
