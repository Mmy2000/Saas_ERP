"""Opening balances between branches (legacy Bran GHBR34 "رصيد افتتاحي لفرع"): booked on both
branches at once, shown by the branch statement, entered once only."""

from decimal import Decimal

import pytest
from django.db.models import Sum
from django.utils import translation

from apps.core.errors import ValidationError
from apps.core.tenancy import tenant_context
from apps.ledger.models import JournalLine
from apps.opening.services import BranchBalance, OpeningInput, post_opening, void_opening
from apps.org.models import Branch
from apps.org.services import BranchInput, create_branch
from apps.reports.registry import REPORTS
from apps.sales.tests.test_sales import Shop
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        shop = Shop()
        shop.maadi = create_branch(BranchInput(code=2, name="Maadi"))
        yield shop


def clearing(branch, commodity):
    return JournalLine.objects.filter(account__role="branch_clearing", branch=branch,
                                      commodity__code=commodity).aggregate(
        q=Sum("quantity"))["q"] or 0


def test_both_branches_are_booked(shop):
    doc = post_opening(OpeningInput(branch_id=shop.branch.pk, branches=[
        BranchBalance(shop.maadi.pk, "3000", True, currency_code="EGP"),
        BranchBalance(shop.maadi.pk, "40", False, karat_id=shop.k21.pk)]))
    assert clearing(shop.branch, "EGP") == Decimal("3000")
    assert clearing(shop.maadi, "EGP") == Decimal("-3000")
    assert clearing(shop.branch, "XAU") == Decimal("-35")  # 40 g × 875‰ owed to Maadi
    assert clearing(shop.maadi, "XAU") == Decimal("35")
    assert doc.journal_entry.lines.filter(branch=shop.maadi).count() == 4

    with translation.override("en"):
        report = REPORTS["branch_statement"]()
        tables = report.run(report.parse({}, Branch.objects.all()), None)
    rows = {row["branch"]: row for row in tables[0].rows}
    assert rows["Maadi"]["money"] == Decimal("-3000") and rows["Maadi"]["gold"] == Decimal("35")

    # Entered once: neither branch can enter it again while this one stands.
    for branch, other in ((shop.branch, shop.maadi), (shop.maadi, shop.branch)):
        with pytest.raises(ValidationError) as caught:
            post_opening(OpeningInput(branch_id=branch.pk, branches=[
                BranchBalance(other.pk, "1", True, currency_code="EGP")]))
        assert doc.number in str(caught.value.fields)
    with pytest.raises(ValidationError):  # not with itself
        post_opening(OpeningInput(branch_id=shop.branch.pk, branches=[
            BranchBalance(shop.branch.pk, "1", True, currency_code="EGP")]))

    void_opening(doc.pk)
    assert clearing(shop.maadi, "EGP") == 0 and clearing(shop.branch, "XAU") == 0
    post_opening(OpeningInput(branch_id=shop.maadi.pk, branches=[
        BranchBalance(shop.branch.pk, "500", True, currency_code="EGP")]))
    assert clearing(shop.maadi, "EGP") == Decimal("500")


def test_form_api_and_detail(tenant_a, shop):
    client = login(tenant_a, language="en")
    assert "Other branches" in client.get("/accounting/opening/new/").content.decode()
    made = client.post("/api/v1/opening/", {
        "branch": shop.branch.pk, "branches": [
            {"branch": shop.maadi.pk, "amount": "1200", "owes_us": True, "currency": "EGP"}]},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="ob-branch")
    assert made.status_code == 201, made.content
    page = client.get(made.json()["url"]).content.decode()
    assert "Other branches" in page and "Maadi" in page and "1,200.00" in page
