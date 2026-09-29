import pytest

from apps.core.tenancy import tenant_context
from apps.ledger.models import Account, Commodity
from apps.ledger.tests import money_account
from apps.org.models import Branch
from apps.parties.services import PartyData, create_customer
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def ids(tenant_a):
    with tenant_context(tenant_a.id):
        return {
            "branch": Branch.objects.get(code=1).pk,
            "cash": money_account().pk,
            "customers": Account.objects.get(role="customers").pk,
            "equity": Account.objects.get(role="opening_equity").pk,
            "gold_stock": Account.objects.get(role="inventory_gold").pk,
            "EGP": Commodity.objects.get(code="EGP").pk,
            "XAU": Commodity.objects.get(code="XAU").pk,
            "customer": create_customer(PartyData(name="Mona")).pk,
        }


def _entry(ids, lines, **extra):
    return {"branch": ids["branch"], "business_date": "2026-09-24", "memo": "Opening",
            "lines": lines,
            **extra}


def test_post_opening_balances_and_reverse(tenant_a, ids):
    client = login(tenant_a)
    payload = _entry(ids, [
        {"account": ids["cash"], "commodity": ids["EGP"], "debit": "25000"},
        {"account": ids["gold_stock"], "commodity": ids["XAU"], "debit": "100",
         "functional_amount": "400000"},
        {"account": ids["equity"], "commodity": ids["XAU"], "credit": "100",
         "functional_amount": "400000"},
        {"account": ids["equity"], "commodity": ids["EGP"], "credit": "25000"},
    ])
    created = client.post("/api/v1/ledger/entries/", payload, content_type="application/json")
    assert created.status_code == 201, created.content
    entry = created.json()
    assert entry["kind"] == "manual" and len(entry["lines"]) == 4

    tb = client.get("/api/v1/ledger/trial-balance/").json()
    assert tb["total_debit"] == tb["total_credit"] == "425000.000000"
    assert tb["metal_codes"] == ["XAU"]

    reversed_ = client.post(f"/api/v1/ledger/entries/{entry['id']}/reverse/", {},
                            content_type="application/json")
    assert reversed_.status_code == 201
    assert client.get("/api/v1/ledger/trial-balance/").json()["rows"] == []


def test_line_level_errors(tenant_a, ids):
    client = login(tenant_a)
    both = _entry(ids, [
        {"account": ids["cash"], "commodity": ids["EGP"], "debit": "5", "credit": "5"},
        {"account": ids["equity"], "commodity": ids["EGP"], "credit": "5"},
    ])
    fields = client.post("/api/v1/ledger/entries/", both,
                         content_type="application/json").json()["error"]["fields"]
    assert "0" in fields["lines"] and "1" not in fields["lines"]  # keyed by line index

    no_party = _entry(ids, [
        {"account": ids["customers"], "commodity": ids["EGP"], "debit": "5"},
        {"account": ids["equity"], "commodity": ids["EGP"], "credit": "5"},
    ])
    error = client.post("/api/v1/ledger/entries/", no_party,
                        content_type="application/json").json()["error"]
    assert error["code"] == "LEDGER_PARTY_MISMATCH"
    assert "0" in error["fields"]["lines"]


def test_unbalanced_entry_is_a_business_error(tenant_a, ids):
    response = login(tenant_a).post("/api/v1/ledger/entries/", _entry(ids, [
        {"account": ids["cash"], "commodity": ids["EGP"], "debit": "10"},
        {"account": ids["equity"], "commodity": ids["EGP"], "credit": "9"},
    ]), content_type="application/json")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "LEDGER_UNBALANCED"


def test_viewer_reads_but_cannot_post(tenant_a, ids, make_member):
    make_member(tenant_a, "val", "viewer")
    client = login(tenant_a, "val")
    assert client.get("/api/v1/ledger/entries/").status_code == 200
    response = client.post("/api/v1/ledger/entries/", _entry(ids, [
        {"account": ids["cash"], "commodity": ids["EGP"], "debit": "1"},
        {"account": ids["equity"], "commodity": ids["EGP"], "credit": "1"},
    ]), content_type="application/json")
    assert response.status_code == 403


def test_party_lookup_finds_any_party(tenant_a, ids):
    results = login(tenant_a).get("/api/v1/ledger/parties/?q=mon").json()["results"]
    assert [r["id"] for r in results] == [ids["customer"]]
