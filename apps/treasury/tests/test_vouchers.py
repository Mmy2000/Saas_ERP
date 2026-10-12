"""Money in and out of a cash box or bank account against any account (legacy Cash GHV11a)."""

from decimal import Decimal

import pytest

from apps.core.errors import ValidationError
from apps.core.models import DocStatus
from apps.core.tenancy import tenant_context
from apps.ledger.accounts import AccountInput, create_account
from apps.ledger.models import Account, JournalLine
from apps.ledger.services import account_for
from apps.pricing.services import record_fx_rate
from apps.sales.tests.test_sales import Shop
from apps.treasury.holders import CashBoxInput, balance, create_cash_box, default_cash_box
from apps.treasury.vouchers import VoucherInput, counter_accounts, post_voucher, void_voucher
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        shop = Shop()
        shop.capital = create_account(AccountInput(
            parent_id=Account.objects.get(code="3").pk, code="", name="Owner's current account"))
        yield shop


def voucher(shop, direction, amount, account=None, method="cash", currency="EGP", **kw):
    return post_voucher(VoucherInput(
        branch_id=shop.branch.pk, direction=direction, account_id=(account or shop.capital).pk,
        method=method, currency_code=currency, amount=amount, **kw))


def test_money_in_and_out(shop):
    with pytest.raises(ValidationError):  # nothing in the box yet
        voucher(shop, "out", "100")
    put = voucher(shop, "in", "5000", counterparty="Mahmoud")
    assert put.number == "01-MV-2026-000001" and put.status == DocStatus.POSTED
    box = put.cash_box
    assert balance(box.account_id)[0] == Decimal("5000")
    owner = JournalLine.objects.filter(account=shop.capital).get()
    assert owner.quantity == Decimal("-5000")  # credited: the business owes the owner
    taken = voucher(shop, "out", "1200", account=account_for("bank_interest"))
    assert balance(box.account_id)[0] == Decimal("3800")
    assert taken.functional_amount == Decimal("1200")
    with pytest.raises(ValidationError):  # taking the 5000 back out would leave it short
        void_voucher(put.pk)
    void_voucher(taken.pk)
    taken.refresh_from_db()
    assert taken.status == DocStatus.VOIDED and balance(box.account_id)[0] == Decimal("5000")


def test_accounts_and_currencies(shop):
    allowed = set(counter_accounts().values_list("role", flat=True))
    assert "inventory_gold" not in allowed and "customers" not in allowed
    assert "cash" not in allowed and "employee_advances" not in allowed
    assert "bank_interest" in allowed
    assert not counter_accounts().filter(pk=default_cash_box(
        shop.branch, shop.bank.currency).account_id).exists()
    record_fx_rate("USD", "50")
    create_cash_box(CashBoxInput(branch_id=shop.branch.pk, currency_code="USD",
                                 is_default=True))
    loan = create_account(AccountInput(parent_id=Account.objects.get(code="21").pk, code="",
                                       name="Dollar loan", currency_code="USD"))
    with pytest.raises(ValidationError):  # the loan is kept in dollars
        voucher(shop, "in", "100", account=loan)
    usd = voucher(shop, "in", "100", account=loan, currency="USD")
    assert usd.functional_amount == Decimal("5000.00")
    line = JournalLine.objects.get(account=loan)
    assert line.commodity.code == "USD" and line.quantity == Decimal("-100")
    income = voucher(shop, "in", "10", account=account_for("bank_interest"), currency="USD")
    line = JournalLine.objects.get(account=account_for("bank_interest"),
                                   entry__source_id=income.pk)
    assert line.commodity.code == "EGP" and line.quantity == Decimal("-500.00")
    bank = voucher(shop, "out", "40", account=shop.capital, method="bank_transfer")
    assert bank.bank_account == shop.bank and balance(shop.bank.account_id)[0] == Decimal("-40")


def test_api_pages_and_print(tenant_a, shop):
    client = login(tenant_a, language="en")
    made = client.post("/api/v1/treasury/money-vouchers/", {
        "branch": shop.branch.pk, "direction": "in", "account": shop.capital.pk,
        "method": "cash", "currency": "EGP", "amount": "750", "counterparty": "Owner"},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="mv1")
    assert made.status_code == 201, made.content
    pk = made.json()["id"]
    for path in ("/treasury/money/", "/treasury/money/new/", "/treasury/money/new/?direction=out",
                 f"/treasury/money/{pk}/", "/treasury/money/?direction=in",
                 f"/print/money_voucher/{pk}/"):
        assert client.get(path).status_code == 200, path
    listing = client.get("/treasury/money/").content.decode()
    assert made.json()["number"] in listing and "Money in" in listing
    report = client.get("/reports/daily_summary/").content.decode()
    assert "Money in (other)" in report
    cancelled = client.post(f"/api/v1/treasury/money-vouchers/{pk}/void/", {"reason": "typo"},
                            content_type="application/json", HTTP_IDEMPOTENCY_KEY="mv2")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "voided"
