"""Advances paid back in cash (legacy GHE20) and settled without money (legacy GHE23)."""

# ruff: noqa: F811  (pytest fixtures imported from other test modules)
from decimal import Decimal

import pytest

from apps.core.errors import DomainError, ValidationError
from apps.core.tenancy import tenant_context
from apps.hr.models import AdvanceKind
from apps.hr.services import (
    give_advance,
    outstanding_advance,
    plan_payroll,
    repay_advance,
    settle_advance,
    void_advance,
)
from apps.hr.tests.test_payroll import (
    M,
    Y,
    balanced,
    cash,
    roles,
    shop,  # noqa: F401  (fixture)
)
from conftest import login

pytestmark = pytest.mark.django_db


def test_repay_and_settle(shop):
    give_advance(shop.omar.pk, "1000", branch_id=shop.branch.pk)
    before = cash(shop)
    with pytest.raises(ValidationError):  # more than he owes
        repay_advance(shop.omar.pk, "1500", branch_id=shop.branch.pk)
    paid = repay_advance(shop.omar.pk, "300", branch_id=shop.branch.pk)
    assert paid.kind == AdvanceKind.REPAYMENT and paid.number.startswith("01-ER-")
    assert cash(shop) == before + 300 and outstanding_advance(shop.omar) == Decimal("700")

    with pytest.raises(ValidationError):  # a settlement needs a reason
        settle_advance(shop.omar.pk, "100", favour=True, branch_id=shop.branch.pk, note="")
    forgiven = settle_advance(shop.omar.pk, "200", favour=True, branch_id=shop.branch.pk,
                              note="Eid gift")
    assert forgiven.number.startswith("01-ES-") and outstanding_advance(shop.omar) == 500
    settle_advance(shop.omar.pk, "50", favour=False, branch_id=shop.branch.pk,
                   note="Broken scale")
    assert outstanding_advance(shop.omar) == Decimal("550")
    assert cash(shop) == before + 300  # settlements move no money
    assert roles()["balance_adjustments"].debit == Decimal("150")  # 200 forgiven − 50 charged
    assert balanced()

    plan = plan_payroll(Y, M)
    omar = next(p for p in plan.lines if p.employee == shop.omar)
    assert omar.outstanding == Decimal("550")  # the payroll takes back what is still owed

    void_advance(paid.pk)  # cancelling a repayment makes it owed again
    assert outstanding_advance(shop.omar) == Decimal("850") and cash(shop) == before


def test_cancelling_an_advance_after_it_was_repaid(shop):
    advance = give_advance(shop.omar.pk, "400", branch_id=shop.branch.pk)
    repay_advance(shop.omar.pk, "400", branch_id=shop.branch.pk)
    with pytest.raises(DomainError):  # nothing left of it to cancel
        void_advance(advance.pk)


def test_page_and_api(tenant_a, shop):
    with tenant_context(tenant_a.id):
        give_advance(shop.omar.pk, "600", branch_id=shop.branch.pk)
    client = login(tenant_a, language="en")
    page = client.get(f"/hr/{shop.omar.pk}/").content.decode()
    assert "Advance repaid or settled" in page
    made = client.post(f"/api/v1/hr/employees/{shop.omar.pk}/repayment/", {
        "kind": "repayment", "amount": "100", "branch": shop.branch.pk, "method": "cash"},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="rp1")
    assert made.status_code == 201, made.content
    settled = client.post(f"/api/v1/hr/employees/{shop.omar.pk}/repayment/", {
        "kind": "favour", "amount": "50", "branch": shop.branch.pk, "note": "Agreed"},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="rp2")
    assert settled.status_code == 201 and settled.json()["kind"] == "settle_down"
    detail = client.get(f"/hr/advances/{settled.json()['id']}/").content.decode()
    assert "Settlement in the employee&#x27;s favour" in detail
