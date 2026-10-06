from datetime import date
from decimal import Decimal

import pytest
from django.db.models import Sum
from django.utils import timezone

from apps.core.errors import DomainError, PermissionDenied, ValidationError
from apps.core.tenancy import tenant_context
from apps.hr.services import EmployeeInput, create_employee
from apps.ledger import closing
from apps.ledger.models import EntryKind, JournalLine, PeriodEvent, YearEnd
from apps.ledger.services import reverse_entry
from apps.ledger.tests.test_posting import Books
from conftest import login

pytestmark = pytest.mark.django_db
Y = timezone.localdate().year - 1  # a whole year in the past


@pytest.fixture
def books(tenant_a):
    with tenant_context(tenant_a.id):
        yield Books()


def _sale(books, day, amount="1000"):
    return books.post(books.line("cash", books.egp, amount),
                      books.line("repair_income", books.egp, f"-{amount}"), on=day)


def _expense(books, day, amount="300"):
    return books.post(books.line("expenses", books.egp, amount),
                      books.line("cash", books.egp, f"-{amount}"), on=day)


def _net(role, commodity=None):
    lines = JournalLine.objects.filter(account__role=role)
    if commodity is not None:
        lines = lines.filter(commodity=commodity)
    sums = lines.aggregate(q=Sum("quantity"), f=Sum("functional_amount"))
    return sums["q"] or 0, sums["f"] or 0


def test_months_close_in_order_and_lock_their_entries(books):
    sale = _sale(books, date(Y, 3, 10))
    _expense(books, date(Y, 5, 2))
    assert closing.first_month() == (Y, 3)
    assert closing.next_to_close() == (Y, 3)
    with pytest.raises(DomainError) as error:  # months close in order
        closing.close_month(Y, 4)
    assert error.value.code == "PERIOD_EARLIER_OPEN"
    today = timezone.localdate()
    with pytest.raises(DomainError):  # still running
        closing.close_month(today.year, today.month)

    closing.close_month(Y, 3, note="Checked")
    with pytest.raises(DomainError) as error:  # nothing can be dated into it
        _sale(books, date(Y, 3, 20))
    assert error.value.code == "LEDGER_PERIOD_CLOSED"
    with pytest.raises(DomainError) as error:  # nor can its entries (documents) be cancelled
        reverse_entry(sale.pk)
    assert error.value.code == "LEDGER_PERIOD_CLOSED"
    _sale(books, date(Y, 4, 1))  # the next month is still open

    march = closing.months_of(Y)[2]
    assert (march.state, march.figures.income, march.figures.profit) == ("closed", 1000, 1000)
    assert closing.months_of(Y)[3].can_close

    closing.close_month(Y, 4)
    with pytest.raises(ValidationError):
        closing.reopen_month(Y, 4, reason=" ")
    with pytest.raises(DomainError) as error:  # reopened newest first
        closing.reopen_month(Y, 3, reason="Late invoice")
    assert error.value.code == "PERIOD_LATER_CLOSED"
    closing.reopen_month(Y, 4, reason="Late invoice")
    closing.reopen_month(Y, 3, reason="Late invoice")
    reverse_entry(sale.pk)  # open again
    assert list(PeriodEvent.objects.order_by("id").values_list("action", "month", "reason")) == [
        ("closed", 3, "Checked"), ("closed", 4, ""), ("reopened", 4, "Late invoice"),
        ("reopened", 3, "Late invoice")]


def test_unfinished_work_needs_confirmation(books):
    _sale(books, date(Y, 3, 10))
    create_employee(EmployeeInput(name="Hany", monthly_salary="3000"))
    checks = closing.checklist(Y, 3)
    assert [c.count for c in checks if "payroll" in c.url] == [1]
    with pytest.raises(DomainError) as error:
        closing.close_month(Y, 3)
    assert error.value.code == "PERIOD_HAS_OPEN_ITEMS"
    closing.close_month(Y, 3, confirm=True)


def test_year_end_moves_profit_to_retained_earnings(books):
    _sale(books, date(Y, 3, 10))
    _expense(books, date(Y, 6, 1))
    books.post(books.line("metal_loss", books.gold, "1.5", "4500"),
               books.line("inventory_gold", books.gold, "-1.5", "-4500"), on=date(Y, 6, 1))
    view = closing.year_view(Y)
    assert not view.can_close and view.blocker
    with pytest.raises(DomainError):
        closing.close_year(Y)
    for month in range(3, 13):
        closing.close_month(Y, month)
    view = closing.year_view(Y)
    assert view.can_close and view.figures.profit == Decimal("-3800")

    year_end = closing.close_year(Y)
    entry = year_end.entry
    assert (entry.kind, entry.business_date) == (EntryKind.CLOSING, date(Y, 12, 31))
    assert _net("repair_income") == (0, 0)
    assert _net("expenses") == (0, 0)
    assert _net("metal_loss", books.gold) == (0, 0)
    # The loss sits in retained earnings: 700 credit in money, 1.5 g (4,500) debit in gold.
    assert _net("retained_earnings", books.egp) == (-700, -700)
    assert _net("retained_earnings", books.gold) == (Decimal("1.5"), 4500)
    assert closing.year_view(Y).figures.profit == Decimal("-3800")  # closing left out
    with pytest.raises(DomainError):
        closing.close_year(Y)
    with pytest.raises(DomainError) as error:  # its months stay closed while the year is
        closing.reopen_month(Y, 12, reason="Fix")
    assert error.value.code == "PERIOD_YEAR_CLOSED"

    with pytest.raises(ValidationError):
        closing.reopen_year(Y, reason="")
    closing.reopen_year(Y, reason="Auditor's adjustment")
    assert _net("repair_income") == (-1000, -1000)
    assert YearEnd.objects.get(pk=year_end.pk).reopened_at is not None
    closing.reopen_month(Y, 12, reason="Auditor's adjustment")
    closing.close_month(Y, 12)
    again = closing.close_year(Y)  # a reopened year closes again with a new entry
    assert again.pk != year_end.pk and _net("expenses") == (0, 0)


def test_closing_needs_every_branch():
    class BranchManager:
        def require(self, code):
            pass

        def branch_ids(self, code):
            return frozenset({1})

    with pytest.raises(PermissionDenied):
        closing._require(BranchManager(), "ledger.period.close")


def test_api_and_pages(tenant_a, books):
    _sale(books, date(Y, 11, 5))
    client = login(tenant_a, language="en")
    assert client.get("/accounting/periods/").status_code == 200
    page = client.get(f"/accounting/periods/{Y}/11/")
    assert page.status_code == 200 and b"Close November" in page.content
    assert client.get(f"/accounting/periods/{Y}/1/").status_code == 404  # before the first posting
    closed = client.post("/api/v1/ledger/periods/close/", {"period": f"{Y}-11"},
                         content_type="application/json")
    assert closed.status_code == 200, closed.content
    early = client.post("/api/v1/ledger/years/close/", {"year": Y},
                        content_type="application/json")
    assert early.status_code == 422 and early.json()["error"]["code"] == "YEAR_NOT_READY"
    client.post("/api/v1/ledger/periods/close/", {"period": f"{Y}-12"},
                content_type="application/json")
    year = client.post("/api/v1/ledger/years/close/", {"year": Y},
                       content_type="application/json")
    assert year.status_code == 201, year.content
    reopen = client.post("/api/v1/ledger/years/reopen/", {"year": Y, "reason": "Audit"},
                         content_type="application/json")
    assert reopen.status_code == 200, reopen.content
    month = client.post("/api/v1/ledger/periods/reopen/", {"period": f"{Y}-12", "reason": "Audit"},
                        content_type="application/json")
    assert month.status_code == 200, month.content
    for path in (f"/accounting/periods/?year={Y}", f"/accounting/periods/{Y}/11/",
                 f"/accounting/periods/{Y}/12/"):
        assert client.get(path).status_code == 200, path
