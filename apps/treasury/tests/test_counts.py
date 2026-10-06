from decimal import Decimal

import pytest

from apps.core.errors import DomainError, ValidationError
from apps.ledger.selectors import trial_balance
from apps.sales.tests.test_sales import RING_A_TOTAL
from apps.treasury.counts import CountInput, record_count, void_count
from apps.treasury.holders import balance
from apps.treasury.models import CashBox
from apps.treasury.tests.test_treasury import cash_sale, shop  # noqa: F401
from conftest import login

pytestmark = pytest.mark.django_db


def held(box):
    return balance(box.account_id)[0]


def over_short():
    rows = {row.account.role: row for row in trial_balance().rows}
    row = rows.get("cash_over_short")
    return (row.debit - row.credit) if row else 0


def test_counts(shop):  # noqa: F811
    cash_sale(shop)
    box = CashBox.objects.get()  # opened by the first cash sale
    assert held(box) == RING_A_TOTAL

    # Counted as a total, matching the books: nothing is booked.
    notes = {"200": int(RING_A_TOTAL // 200)}
    rest = RING_A_TOTAL - 200 * notes["200"]
    exact = record_count(CountInput(cash_box_id=box.pk, counted=str(RING_A_TOTAL)))
    assert exact.difference == 0 and exact.journal_entry is None and exact.number

    with pytest.raises(ValidationError):  # a difference needs an explanation
        record_count(CountInput(cash_box_id=box.pk, counted=str(RING_A_TOTAL - 50)))
    with pytest.raises(ValidationError):  # not a note or coin of the pound
        record_count(CountInput(cash_box_id=box.pk, denominations={"3": 1}))

    short = record_count(CountInput(cash_box_id=box.pk, denominations=notes,
                                    note="Change given twice"))
    assert short.difference == -rest and short.denominations == notes
    assert held(box) == 200 * notes["200"] and over_short() == rest
    with pytest.raises(DomainError):  # only the latest count is cancelled
        void_count(exact.pk)
    void_count(short.pk, reason="Recounted")
    assert held(box) == RING_A_TOTAL and over_short() == 0

    surplus = record_count(CountInput(cash_box_id=box.pk, counted=str(RING_A_TOTAL + 20),
                                      note="Found in the drawer"))
    assert surplus.difference == 20 and held(box) == RING_A_TOTAL + 20
    assert over_short() == -20
    tb = trial_balance()
    assert tb.total_debit == tb.total_credit


def test_api_and_pages(tenant_a, shop):  # noqa: F811
    cash_sale(shop)
    box = CashBox.objects.get()
    client = login(tenant_a, language="en")
    made = client.post("/api/v1/treasury/cash-counts/", {
        "cash_box": box.pk, "denominations": {"200": 10, "50": 1}, "note": "Short"},
        content_type="application/json", HTTP_IDEMPOTENCY_KEY="cc1")
    assert made.status_code == 201, made.content
    count = made.json()
    assert Decimal(count["counted"]) == 2050
    for path in ("/treasury/counts/", f"/treasury/counts/?box={box.pk}",
                 "/treasury/counts/new/", f"/treasury/counts/new/?box={box.pk}",
                 f"/treasury/counts/{count['id']}/", "/reports/daily_summary/", "/treasury/"):
        assert client.get(path).status_code == 200, path
    page = client.get(f"/treasury/counts/{count['id']}/").content.decode()
    assert "Short" in page and "Cancel the count" in page
    voided = client.post(f"/api/v1/treasury/cash-counts/{count['id']}/void/", {},
                         content_type="application/json", HTTP_IDEMPOTENCY_KEY="cc1v")
    assert voided.status_code == 200 and voided.json()["status"] == "voided"
