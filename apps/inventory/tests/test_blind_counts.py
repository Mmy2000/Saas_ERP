"""Blind counts (legacy GHS45a / GHS45b): typed without the books, differences marked, the real
figures for owners only."""

from decimal import Decimal

import pytest

from apps.core.errors import ValidationError
from apps.core.tenancy import tenant_context
from apps.inventory.blind_counts import BlindCountInput, books, record, sheet
from apps.inventory.models import BlindCount
from apps.sales.tests.test_sales import Shop
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop(tenant_a):
    with tenant_context(tenant_a.id):
        yield Shop()


def _typed(held, kind):
    return {key: str(value) for key, value in held[kind].items() if value}


def test_matching_and_differing_counts(shop):
    layout = sheet()
    held = books(shop.branch, layout)
    assert sum(held["pieces"].values()) >= 2  # the two rings

    right = record(BlindCountInput(branch_id=shop.branch.pk, pieces=_typed(held, "pieces"),
                                   weights=_typed(held, "weights"), cash=str(held["cash"])))
    assert right.differences == 0 and right.number and right.status == "posted"
    assert all(cell["ok"] for cell in right.pieces)

    key = next(iter(held["pieces"]))
    wrong = dict(_typed(held, "pieces"))
    wrong[key] = str(held["pieces"][key] + 1)
    off = record(BlindCountInput(branch_id=shop.branch.pk, parts=("pieces",), pieces=wrong))
    assert off.differences == 1 and off.weights == [] and off.cash_currency == ""
    [bad] = [cell for cell in off.pieces if not cell["ok"]]
    assert Decimal(bad["counted"]) == held["pieces"][key] + 1

    nothing = record(BlindCountInput(branch_id=shop.branch.pk, parts=("pieces",)))
    assert nothing.differences == len([v for v in held["pieces"].values() if v])

    by_group = record(BlindCountInput(branch_id=shop.branch.pk, by_group=True,
                                      parts=("pieces",)))
    assert by_group.by_group and by_group.differences >= 1

    for bad_value in ("-1", "1.5", "x"):
        with pytest.raises(ValidationError):
            record(BlindCountInput(branch_id=shop.branch.pk, parts=("pieces",),
                                   pieces={key: bad_value}))
    with pytest.raises(ValidationError):
        record(BlindCountInput(branch_id=shop.branch.pk, parts=()))


def test_pages_hide_the_books_from_counters(tenant_a, shop, make_member):
    with tenant_context(tenant_a.id):
        held = books(shop.branch, sheet())
        key = next(iter(held["pieces"]))
    make_member(tenant_a, "clerk", "manager")  # counts, but does not see the books
    owner = login(tenant_a, language="en")
    page = owner.get(f"/stock/blind-counts/new/?branch={shop.branch.pk}").content.decode()
    assert f'name="p-{key[0]}-{key[1] or 0}"' in page and "Check and save" in page
    made = owner.post("/stock/blind-counts/new/", {
        "branch": shop.branch.pk, "parts": ["pieces"], "save": "1",
        f"p-{key[0]}-{key[1] or 0}": str(held["pieces"][key] + 5)})
    assert made.status_code == 302
    with tenant_context(tenant_a.id):
        count = BlindCount.objects.get()
    detail = owner.get(f"/stock/blind-counts/{count.pk}/").content.decode()
    assert "data-differs" in detail and "data-books" in detail  # owners see the real figures
    assert count.number in owner.get("/stock/blind-counts/").content.decode()
    again = owner.get(f"/stock/blind-counts/new/?again={count.pk}").content.decode()
    assert f'value="{held["pieces"][key] + 5}"' in again
    clerk = login(tenant_a, "clerk", language="en")
    page = clerk.get(f"/stock/blind-counts/{count.pk}/").content.decode()
    assert "data-differs" in page and "data-books" not in page
