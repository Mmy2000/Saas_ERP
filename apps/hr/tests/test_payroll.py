from datetime import date
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.errors import DomainError, ValidationError
from apps.core.tenancy import tenant_context
from apps.hr.models import AdjustmentKind, CommissionBasis, PayAdjustment
from apps.hr.services import (
    EmployeeInput,
    add_adjustment,
    commission_for,
    create_employee,
    delete_adjustment,
    give_advance,
    outstanding_advance,
    plan_payroll,
    post_payroll,
    update_employee,
    void_advance,
    void_payroll,
)
from apps.iam.authz import build_actor
from apps.iam.models import Membership
from apps.ledger.selectors import trial_balance
from apps.sales.returns import create_return
from apps.sales.services import post_sale
from apps.sales.tests.test_sales import RING_A_TOTAL, Shop
from apps.treasury.holders import balance, default_cash_box
from conftest import login

pytestmark = pytest.mark.django_db
TODAY = timezone.localdate()
Y, M = TODAY.year, TODAY.month


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        shop = Shop()
        shop.owner = Membership.objects.get(username="owner")
        shop.actor = build_actor(shop.owner)
        # Sara sells with the owner's login: 10% of the making charge she sells.
        shop.sara = create_employee(EmployeeInput(
            name="Sara", job_title="Sales", user_id=shop.owner.user_id, monthly_salary="5000",
            commission_basis=CommissionBasis.MAKING_PCT, commission_rate="10",
            branch_id=shop.branch.pk))
        shop.omar = create_employee(EmployeeInput(name="Omar", monthly_salary="3000"))
        # Ring A: making 1,250 → Sara's commission 125. Paid in cash, so the box has money.
        shop.sale = post_sale(shop.sale(shop.line(shop.ring_a),
                                        payments=[shop.cash(RING_A_TOTAL)]), actor=shop.actor)
        yield shop


def cash(shop) -> Decimal:
    return balance(default_cash_box(shop.branch, shop.bank.currency).account_id)[0]


def roles():
    return {r.account.role: r for r in trial_balance().rows}


def balanced():
    tb = trial_balance()
    return tb.total_debit == tb.total_credit


def test_monthly_payroll(shop):
    assert (shop.sara.code, shop.omar.code) == (1, 2)
    commission = commission_for(shop.sara, Y, M)
    assert (commission.sales, commission.making, commission.amount) == (
        1, Decimal("1250.00"), Decimal("125.00"))
    assert commission_for(shop.omar, Y, M).amount == 0  # no login, no commission

    advance = give_advance(shop.sara.pk, "1000", branch_id=shop.branch.pk)
    assert advance.number == "01-EA-2026-000001" and outstanding_advance(shop.sara) == 1000
    add_adjustment(shop.sara.pk, AdjustmentKind.BONUS, "300", Y, M, note="Eid")
    add_adjustment(shop.sara.pk, AdjustmentKind.DEDUCTION, "100", Y, M)

    plan = plan_payroll(Y, M)
    sara = next(line for line in plan.lines if line.employee == shop.sara)
    # 5,000 + 125 + 300 − 100 = 5,325; the 1,000 advance comes back out of it.
    assert (sara.gross, sara.advance, sara.net) == (Decimal("5325"), Decimal("1000"),
                                                    Decimal("4325"))
    assert plan.total("net") == Decimal("7325")

    run = post_payroll(Y, M, branch_id=shop.branch.pk, actor=shop.actor)
    assert run.number == "01-PRL-2026-000001"
    assert (run.total_salary, run.total_commission, run.total_net) == (
        Decimal("8000"), Decimal("125"), Decimal("7325"))
    ledger = roles()
    assert ledger["salaries"].debit == Decimal("8200")  # salaries + bonus − deduction
    assert ledger["commissions"].debit == Decimal("125")
    assert "employee_advances" not in ledger  # given and recovered
    assert cash(shop) == RING_A_TOTAL - 1000 - 7325
    assert outstanding_advance(shop.sara) == 0
    assert PayAdjustment.objects.filter(payroll_line__isnull=False).count() == 2
    assert balanced()

    with pytest.raises(DomainError):  # one payroll a month
        post_payroll(Y, M, branch_id=shop.branch.pk)
    with pytest.raises(ValidationError):  # the month is closed for bonuses
        add_adjustment(shop.sara.pk, AdjustmentKind.BONUS, "50", Y, M)
    with pytest.raises(DomainError):  # recovered: cancel the payroll first
        void_advance(advance.pk)
    with pytest.raises(DomainError):  # paid adjustments stay
        delete_adjustment(PayAdjustment.objects.first().pk)

    void_payroll(run.pk, reason="Wrong month")
    assert outstanding_advance(shop.sara) == 1000
    assert PayAdjustment.objects.filter(payroll_line__isnull=False).count() == 0
    assert cash(shop) == RING_A_TOTAL - 1000
    # Pay again, recovering only 400 of the advance this time.
    run = post_payroll(Y, M, branch_id=shop.branch.pk, advances={shop.sara.pk: "400"})
    assert run.total_advance == 400 and outstanding_advance(shop.sara) == 600
    assert balanced()


def test_limits_and_rules(shop):
    give_advance(shop.omar.pk, "500", branch_id=shop.branch.pk)
    with pytest.raises(ValidationError):  # more than is outstanding
        plan_payroll(Y, M, advances={shop.omar.pk: "600"})
    add_adjustment(shop.omar.pk, AdjustmentKind.DEDUCTION, "4000", Y, M)
    with pytest.raises(DomainError):  # deductions above pay
        post_payroll(Y, M, branch_id=shop.branch.pk)
    delete_adjustment(PayAdjustment.objects.get().pk)
    with pytest.raises(ValidationError):  # more cash than the box holds
        give_advance(shop.omar.pk, "999999", branch_id=shop.branch.pk)
    with pytest.raises(ValidationError):
        create_employee(EmployeeInput(name="Hany", user_id=shop.owner.user_id))  # login in use
    with pytest.raises(ValidationError):
        create_employee(EmployeeInput(name="Hany", commission_basis=CommissionBasis.MAKING_PCT,
                                      commission_rate="150"))
    advance = give_advance(shop.omar.pk, "100", branch_id=shop.branch.pk)
    void_advance(advance.pk)
    assert outstanding_advance(shop.omar) == 500


def test_hired_after_the_month_is_not_paid(shop):
    later = date(Y + 1, 1, 15)
    hany = create_employee(EmployeeInput(name="Hany", monthly_salary="3000", hired_on=later))
    assert hany.pk not in {line.employee.pk for line in plan_payroll(Y, M).lines}
    assert hany.pk in {line.employee.pk for line in plan_payroll(later.year, later.month).lines}


def test_commission_per_gram_and_returns(shop):
    update_employee(shop.sara.pk, EmployeeInput(
        name="Sara", user_id=shop.owner.user_id, monthly_salary="5000",
        commission_basis=CommissionBasis.PER_GRAM, commission_rate="20"))
    shop.sara.refresh_from_db()
    assert commission_for(shop.sara, Y, M).amount == Decimal("100.00")  # 5 g × 20
    create_return(shop.sale.pk, [shop.sale.lines.get().pk], refund_method="cash")
    after = commission_for(shop.sara, Y, M)
    assert (after.grams, after.amount) == (0, 0)  # returned this month


def test_api_and_pages(tenant_a, shop):
    client = login(tenant_a, language="en")
    made = client.post("/api/v1/hr/employees/", {
        "name": "Mona", "monthly_salary": "4000", "commission_basis": "making_pct",
        "commission_rate": "5", "national_id": "29001011234567"},
        content_type="application/json")
    assert made.status_code == 201, made.content
    mona = made.json()["id"]
    advance = client.post(f"/api/v1/hr/employees/{mona}/advance/", {
        "amount": "250", "branch": shop.branch.pk, "method": "cash"},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="hr1")
    assert advance.status_code == 201, advance.content
    bonus = client.post(f"/api/v1/hr/employees/{mona}/adjustments/", {
        "kind": "bonus", "amount": "150", "period": f"{Y}-{M:02d}", "note": "Target"},
        content_type="application/json")
    assert bonus.status_code == 201, bonus.content
    preview = client.get(f"/api/v1/hr/payroll/preview/?period={Y}-{M:02d}")
    assert preview.status_code == 200
    names = {row["name"] for row in preview.json()["lines"]}
    assert {"Sara", "Omar", "Mona"} <= names
    paid = client.post("/api/v1/hr/payroll/", {
        "period": f"{Y}-{M:02d}", "branch": shop.branch.pk, "method": "cash"},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="hr2")
    assert paid.status_code == 201, paid.content
    run = paid.json()["id"]
    for path in ("/hr/", "/hr/new/", f"/hr/{mona}/", "/hr/payroll/",
                 f"/hr/payroll/new/?period={Y}-{M:02d}", f"/hr/payroll/{run}/",
                 f"/hr/commissions/?period={Y}-{M:02d}", "/hr/commissions/",
                 f"/hr/advances/{advance.json()['id']}/"):
        assert client.get(path).status_code == 200, path
    page = client.get(f"/hr/{mona}/").content.decode()
    assert "Mona" in page and "250.00" in page
    voided = client.post(f"/api/v1/hr/payroll/{run}/void/", {}, content_type="application/json",
                         HTTP_IDEMPOTENCY_KEY="hr3")
    assert voided.json()["status"] == "voided"
