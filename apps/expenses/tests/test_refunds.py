"""Money back on an expense, in full or in part (legacy GHM13 مرتد المصروفات)."""

from decimal import Decimal

import pytest
from django.utils import translation

from apps.core.errors import DomainError, ValidationError
from apps.core.models import DocStatus
from apps.core.tenancy import tenant_context
from apps.expenses.services import (
    create_category,
    refund_expense,
    refunded,
    void_expense,
    void_refund,
)
from apps.expenses.tests.test_expenses import expense
from apps.ledger.selectors import trial_balance
from apps.org.models import Branch
from apps.reports.registry import REPORTS
from apps.sales.tests.test_sales import Shop
from apps.treasury.holders import balance
from apps.treasury.models import CashBox
from apps.treasury.tests.test_treasury import cash_sale
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        shop = Shop()
        cash_sale(shop)
        shop.rent = create_category("Rent")
        shop.voucher = expense(shop, shop.rent, "1000")
        shop.box = CashBox.objects.get()
        yield shop


def costs():
    row = next((r for r in trial_balance().rows if r.account.role == "expenses"), None)
    return row.debit if row else 0  # a zero balance drops off the trial balance


def test_partial_refunds(shop):
    start = balance(shop.box.account_id)[0]
    first = refund_expense(shop.voucher.pk, "300", method="cash")
    assert first.number.startswith("01-XR-") and first.functional_amount == Decimal("300.00")
    assert balance(shop.box.account_id)[0] == start + 300 and costs() == Decimal("700")
    with pytest.raises(ValidationError):  # only 700 left
        refund_expense(shop.voucher.pk, "800", method="cash")
    refund_expense(shop.voucher.pk, "700", method="bank_transfer")
    assert refunded(shop.voucher) == (Decimal("1000"), Decimal("1000"))
    assert not costs()
    with pytest.raises(DomainError):  # refunded: cancel the refunds first
        void_expense(shop.voucher.pk)
    void_refund(first.pk)
    first.refresh_from_db()
    assert first.status == DocStatus.VOIDED and costs() == Decimal("300")


def test_reports_and_pages(tenant_a, shop):
    with tenant_context(tenant_a.id):
        refund_expense(shop.voucher.pk, "250", method="cash")
        with translation.override("en"):
            report = REPORTS["expenses"]()
            [table] = report.run(report.parse({}, Branch.objects.all()), None)
        [rent] = table.rows
        assert (rent["total"], rent["refunded"], rent["net"]) == (
            Decimal("1000"), Decimal("250"), Decimal("750"))
    client = login(tenant_a, language="en")
    page = client.get(f"/expenses/{shop.voucher.pk}/").content.decode()
    assert "Money back" in page and "Refunded" in page and "750.00" in page
    listing = client.get("/expenses/").content.decode()
    assert "Refunded" in listing and "Net" in listing
    made = client.post(f"/api/v1/expenses/vouchers/{shop.voucher.pk}/refund/", {
        "amount": "50", "method": "cash"}, content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="xr1")
    assert made.status_code == 201, made.content
    assert "Expense refunds" in client.get("/reports/daily_summary/").content.decode()
    gone = client.post(f"/api/v1/expenses/refunds/{made.json()['id']}/void/", {},
                       content_type="application/json", HTTP_IDEMPOTENCY_KEY="xr2")
    assert gone.status_code == 200 and gone.json()["status"] == "voided"
