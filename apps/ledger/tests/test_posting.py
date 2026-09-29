from datetime import date
from decimal import Decimal

import pytest
from django.db import DatabaseError, connection, transaction

from apps.core.errors import DomainError, ValidationError
from apps.core.tenancy import tenant_context
from apps.ledger.chart import CHART
from apps.ledger.models import (
    Account,
    BalanceProjection,
    Commodity,
    FiscalPeriod,
    JournalEntry,
    JournalLine,
    PeriodStatus,
)
from apps.ledger.selectors import party_balance, trial_balance
from apps.ledger.services import LineInput, post_entry, rebuild_balances, reverse_entry
from apps.ledger.tests import money_account
from apps.org.models import Branch
from apps.parties.services import PartyData, create_customer, create_supplier

pytestmark = pytest.mark.django_db
TODAY = date(2026, 9, 24)


class Books:
    """Shortcuts over the seeded chart inside one tenant."""

    def __init__(self):
        self.branch = Branch.objects.get(code=1)
        self.egp = Commodity.objects.get(code="EGP")
        self.usd = Commodity.objects.get(code="USD")
        self.gold = Commodity.objects.get(code="XAU")

    @staticmethod
    def acc(role):
        # "cash" is a group of cash boxes (apps.treasury); the engine tests use a plain,
        # multi-currency money account instead.
        return money_account() if role == "cash" else Account.objects.get(role=role)

    def line(self, role, commodity, quantity, functional=None, party=None):
        return LineInput(account=self.acc(role), commodity=commodity, quantity=quantity,
                         functional_amount=functional, party=party)

    def post(self, *lines, **kw):
        return post_entry(branch=self.branch, business_date=kw.pop("on", TODAY),
                          lines=list(lines), **kw)


@pytest.fixture
def books(tenant_a):
    with tenant_context(tenant_a.id):
        yield Books()


def test_seeded_chart_and_commodities(books):
    assert Account.objects.count() == len(CHART)
    assert Commodity.objects.get(is_functional=True).code == "EGP"
    assert set(Commodity.objects.values_list("code", flat=True)) == {"EGP", "USD", "XAU", "XAG"}
    assert Account.objects.get(role="customers").subledger == "party"


def test_balanced_money_entry_updates_balances(books):
    customer = create_customer(PartyData(name="Mona"))
    entry = books.post(books.line("customers", books.egp, "1500", party=customer),
                       books.line("sales_gold", books.egp, "-1500"))
    assert entry.number == "01-JV-2026-000001"
    assert entry.lines.count() == 2
    assert party_balance(customer) == {"EGP": Decimal("1500")}


def test_unbalanced_entry_is_rejected_and_nothing_written(books):
    with pytest.raises(ValidationError) as exc:
        books.post(books.line("cash", books.egp, "100"), books.line("sales_gold", books.egp, "-90"))
    assert exc.value.code == "LEDGER_UNBALANCED"
    assert JournalEntry.objects.count() == 0 and BalanceProjection.objects.count() == 0


def test_metal_must_balance_per_metal(books):
    supplier = create_supplier(PartyData(name="Factory"))
    with pytest.raises(ValidationError) as exc:
        books.post(books.line("inventory_gold", books.gold, "10", "40000"),
                   books.line("suppliers", books.egp, "-40000", party=supplier))
    assert exc.value.code == "LEDGER_METAL_UNBALANCED"
    # Gold received on the supplier's metal account balances: fine grams in = grams owed.
    books.post(books.line("inventory_gold", books.gold, "10", "40000"),
               books.line("suppliers", books.gold, "-10", "-40000", party=supplier))
    assert party_balance(supplier) == {"XAU": Decimal("-10")}


def test_foreign_currency_needs_functional_value(books):
    with pytest.raises(ValidationError) as exc:
        books.post(books.line("cash", books.usd, "100"),
                   books.line("sales_gold", books.egp, "-4895"))
    assert exc.value.code == "LEDGER_VALUE_REQUIRED"
    books.post(books.line("cash", books.usd, "100", "4895"),
               books.line("sales_gold", books.egp, "-4895"))


@pytest.mark.parametrize(("role", "commodity", "party", "code"), [
    ("customers", "egp", False, "LEDGER_PARTY_MISMATCH"),
    ("cash", "egp", True, "LEDGER_PARTY_MISMATCH"),
    ("cash", "gold", False, "LEDGER_COMMODITY_NOT_ALLOWED"),
])
def test_account_rules(books, role, commodity, party, code):
    customer = create_customer(PartyData(name="Mona")) if party else None
    commodity = getattr(books, commodity)
    with pytest.raises(ValidationError) as exc:
        books.post(books.line(role, commodity, "5", "5", party=customer),
                   books.line("sales_gold", commodity, "-5", "-5"))
    assert exc.value.code == code


def test_header_accounts_are_not_postable(books):
    header = Account.objects.get(code="11")
    with pytest.raises(ValidationError) as exc:
        post_entry(branch=books.branch, business_date=TODAY, lines=[
            LineInput(account=header, commodity=books.egp, quantity="1"),
            books.line("sales_gold", books.egp, "-1")])
    assert exc.value.code == "LEDGER_ACCOUNT_NOT_POSTABLE"


def test_rounding_is_absorbed_within_tolerance(books):
    entry = books.post(books.line("cash", books.usd, "33.33", "1631.51"),
                       books.line("sales_gold", books.egp, "-1631.50"), absorb_rounding=True)
    rounding = entry.lines.get(account__role="rounding")
    assert rounding.quantity == Decimal("-0.01")
    with pytest.raises(ValidationError):
        books.post(books.line("cash", books.usd, "33.33", "1632.00"),
                   books.line("sales_gold", books.egp, "-1631.50"), absorb_rounding=True)


def test_closed_period_rejects_postings(books):
    FiscalPeriod.objects.create(name="Aug 2026", start_date=date(2026, 8, 1),
                                end_date=date(2026, 8, 31), status=PeriodStatus.CLOSED)
    with pytest.raises(DomainError) as exc:
        books.post(books.line("cash", books.egp, "1"), books.line("sales_gold", books.egp, "-1"),
                   on=date(2026, 8, 15))
    assert exc.value.code == "LEDGER_PERIOD_CLOSED"


def test_reversal_mirrors_and_only_once(books):
    customer = create_customer(PartyData(name="Mona"))
    entry = books.post(books.line("customers", books.egp, "700", party=customer),
                       books.line("sales_gold", books.egp, "-700"))
    reversal = reverse_entry(entry.pk, business_date=TODAY)
    assert reversal.reverses_id == entry.pk
    assert party_balance(customer) == {}
    with pytest.raises(DomainError):
        reverse_entry(entry.pk)
    with pytest.raises(DomainError):
        reverse_entry(reversal.pk)


def test_posted_lines_are_append_only(books):
    entry = books.post(books.line("cash", books.egp, "5"),
                       books.line("sales_gold", books.egp, "-5"))
    line_id = entry.lines.first().pk
    for sql in ("UPDATE ledger_journalline SET memo = 'x' WHERE id = %s",
                "DELETE FROM ledger_journalline WHERE id = %s"):
        with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(sql, [line_id])
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("DELETE FROM ledger_journalentry WHERE id = %s", [entry.pk])


def test_database_rejects_unbalanced_lines_written_directly(books):
    """Bypassing PostingService entirely still cannot commit an unbalanced entry."""
    with pytest.raises(DatabaseError), transaction.atomic():
        entry = JournalEntry.objects.create(number="X-1", branch=books.branch,
                                            business_date=TODAY, kind="manual")
        JournalLine.objects.create(entry=entry, account=books.acc("cash"), commodity=books.egp,
                                   quantity=Decimal("10"), functional_amount=Decimal("10"),
                                   branch=books.branch, business_date=TODAY)
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")  # what COMMIT would do


def test_rebuild_matches_incremental_balances(books):
    customer = create_customer(PartyData(name="Mona"))
    books.post(books.line("customers", books.egp, "100", party=customer),
               books.line("sales_gold", books.egp, "-100"))
    books.post(books.line("cash", books.egp, "60"),
               books.line("customers", books.egp, "-60", party=customer))
    before = sorted(BalanceProjection.objects.values_list(
        "account_id", "commodity_id", "party_id", "quantity"))
    rebuild_balances()
    after = sorted(BalanceProjection.objects.values_list(
        "account_id", "commodity_id", "party_id", "quantity"))
    assert before == after
    assert party_balance(customer) == {"EGP": Decimal("40")}


def test_trial_balance_balances_and_supports_as_of(books):
    supplier = create_supplier(PartyData(name="Factory"))
    books.post(books.line("inventory_gold", books.gold, "10", "40000"),
               books.line("suppliers", books.gold, "-10", "-40000", party=supplier),
               on=date(2026, 9, 1))
    books.post(books.line("cash", books.egp, "5000"), books.line("sales_gold", books.egp, "-5000"))
    tb = trial_balance()
    assert tb.total_debit == tb.total_credit == Decimal("45000")
    assert tb.metal_codes == ["XAU"]
    early = trial_balance(as_of=date(2026, 9, 10))
    assert early.total_debit == Decimal("40000")


def test_ledgers_are_isolated_between_tenants(tenant_a, tenant_b):
    with tenant_context(tenant_a.id):
        books = Books()
        books.post(books.line("cash", books.egp, "5"), books.line("sales_gold", books.egp, "-5"))
    with tenant_context(tenant_b.id):
        assert JournalEntry.objects.count() == 0
        assert trial_balance().rows == []
