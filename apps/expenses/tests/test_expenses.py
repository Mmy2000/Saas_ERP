from decimal import Decimal

import pytest

from apps.core.errors import ValidationError
from apps.core.models import DocStatus
from apps.core.tenancy import tenant_context
from apps.expenses.services import (
    ExpenseInput,
    create_category,
    post_expense,
    void_expense,
)
from apps.ledger.selectors import trial_balance
from apps.sales.tests.test_sales import RING_A_TOTAL, Shop
from apps.treasury.holders import balance
from apps.treasury.models import CashBox
from apps.treasury.tests.test_treasury import cash_sale
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        yield Shop()


def expense(shop, category, amount, method="cash", **kw):
    return post_expense(ExpenseInput(branch_id=shop.branch.pk, category_id=category.pk,
                                     method=method, currency_code="EGP", amount=amount, **kw))


def test_categories(shop):
    rent = create_category("Rent")
    assert rent.account.role == "expenses"
    with pytest.raises(ValidationError):
        create_category("Rent")


def test_cash_expense_posts_and_cancels(shop):
    rent = create_category("Rent")
    with pytest.raises(ValidationError):  # the drawer is empty
        expense(shop, rent, "500")
    cash_sale(shop)
    box = CashBox.objects.get()
    voucher = expense(shop, rent, "500", payee="Landlord")
    assert voucher.number == "01-EX-2026-000001" and voucher.cash_box == box
    assert balance(box.account_id)[0] == RING_A_TOTAL - 500
    costs = next(r for r in trial_balance().rows if r.account.role == "expenses")
    assert costs.debit == Decimal("500")
    void_expense(voucher.pk)
    voucher.refresh_from_db()
    assert voucher.status == DocStatus.VOIDED
    assert balance(box.account_id)[0] == RING_A_TOTAL


def test_bank_expense(shop):
    fees = create_category("Bank charges")
    voucher = expense(shop, fees, "25", method="bank_transfer")
    assert voucher.bank_account == shop.bank
    assert balance(shop.bank.account_id)[0] == Decimal("-25")  # banks may be overdrawn


def test_api_and_pages(tenant_a, shop):
    client = login(tenant_a)
    created = client.post("/api/v1/expenses/categories/", {"name": "Electricity"},
                          content_type="application/json")
    assert created.status_code == 201, created.content
    voucher = client.post("/api/v1/expenses/vouchers/", {
        "branch": shop.branch.pk, "category": created.json()["id"], "method": "bank_transfer",
        "currency": "EGP", "amount": "120"}, content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="e1")
    assert voucher.status_code == 201, voucher.content
    category = created.json()["id"]
    for path in ("/expenses/", "/expenses/new/", "/expenses/categories/",
                 f"/expenses/{voucher.json()['id']}/", f"/expenses/?category={category}"):
        assert client.get(path).status_code == 200, path
