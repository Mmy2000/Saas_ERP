"""Adding and editing item categories and karats from the workspace."""

from decimal import Decimal

import pytest

from apps.catalog.models import ItemCategory, Karat
from apps.core.tenancy import tenant_context
from conftest import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner(tenant_a):
    client = login(tenant_a)
    client.cookies["django_language"] = "en"
    return client


def _category(tenant, **filters):
    with tenant_context(tenant.id):
        category = ItemCategory.objects.get(**filters)
        category.charges = {c.currency.code: (c.list_rate_per_g, c.cost_rate_per_g)
                            for c in category.making_charges.select_related("currency")}
        return category


def test_owner_adds_and_edits_categories(owner, tenant_a):
    page = owner.get("/catalog/categories/").content.decode()
    assert "New item category" in page
    assert owner.get("/catalog/categories/new/").status_code == 200

    response = owner.post("/catalog/categories/new/", {
        "code": "10", "name": "Rings", "product_family": "gold", "tracking": "serialized",
        "barcode_prefix": "10", "commission_percent": "1.5",
        "list_EGP": "120", "cost_EGP": "90", "list_USD": "", "cost_USD": ""})
    assert response.status_code == 302, response.content.decode()[:2000]
    rings = _category(tenant_a, code="10")
    assert (rings.name, rings.depth, rings.commission_rate) == ("Rings", 1, Decimal("0.015"))
    assert rings.charges["EGP"] == (Decimal("120"), Decimal("90"))

    # A sub-category, started from the row's "+".
    assert owner.get(f"/catalog/categories/new/?parent={rings.pk}").status_code == 200
    owner.post("/catalog/categories/new/", {
        "code": "11", "name": "Wedding rings", "parent": str(rings.pk),
        "product_family": "gold", "tracking": "serialized"})
    child = _category(tenant_a, code="11")
    assert (child.parent_id, child.depth) == (rings.pk, 2)

    # Edit: rename, new rate, deactivate.
    edit = owner.post(f"/catalog/categories/{rings.pk}/", {
        "code": "10", "name": "Gold rings", "product_family": "gold", "tracking": "serialized",
        "barcode_prefix": "10", "list_EGP": "150", "cost_EGP": "90"})
    assert edit.status_code == 302
    rings = _category(tenant_a, pk=rings.pk)
    assert (rings.name, rings.is_active) == ("Gold rings", False)
    assert rings.charges["EGP"][0] == Decimal("150")

    # Moving the parent shifts the child's depth along with it.
    owner.post("/catalog/categories/new/", {"code": "20", "name": "Sets",
                                            "product_family": "gold", "tracking": "serialized"})
    sets = _category(tenant_a, code="20")
    owner.post(f"/catalog/categories/{rings.pk}/", {
        "code": "10", "name": "Gold rings", "parent": str(sets.pk), "product_family": "gold",
        "tracking": "serialized", "is_active": "on"})
    assert _category(tenant_a, pk=rings.pk).depth == 2
    assert _category(tenant_a, pk=child.pk).depth == 3


def test_category_rules_show_on_the_form(owner, tenant_a):
    owner.post("/catalog/categories/new/", {"code": "10", "name": "Rings",
                                            "product_family": "gold", "tracking": "serialized"})
    rings = _category(tenant_a, code="10")
    taken = owner.post("/catalog/categories/new/", {
        "code": "10", "name": "Again", "product_family": "gold", "tracking": "serialized"})
    assert taken.status_code == 200 and "already exists" in taken.content.decode()
    # A category cannot go under itself (it is not even offered).
    page = owner.get(f"/catalog/categories/{rings.pk}/").content.decode()
    assert f'value="{rings.pk}"' not in page.split('name="parent"')[1].split("</select>")[0]
    bad = owner.post(f"/catalog/categories/{rings.pk}/", {
        "code": "10", "name": "Rings", "parent": str(rings.pk), "product_family": "gold",
        "tracking": "serialized", "is_active": "on"})
    assert bad.status_code == 200


def test_owner_adds_and_edits_karats(owner, tenant_a):
    assert "New karat" in owner.get("/catalog/karats/").content.decode()
    response = owner.post("/catalog/karats/new/", {
        "metal": "gold", "code": "9", "fineness": "375", "display_name": "", "is_active": "on"})
    assert response.status_code == 302
    with tenant_context(tenant_a.id):
        nine = Karat.objects.get(metal__code="gold", code=9)
        reference = Karat.objects.get(metal__code="gold", is_reference=True)
    assert nine.fineness == Decimal("375.000")

    duplicate = owner.post("/catalog/karats/new/", {"metal": "gold", "code": "9",
                                                    "fineness": "375", "is_active": "on"})
    assert "Already exists." in duplicate.content.decode()
    too_fine = owner.post("/catalog/karats/new/", {"metal": "gold", "code": "8",
                                                   "fineness": "1200", "is_active": "on"})
    assert too_fine.status_code == 200

    owner.post(f"/catalog/karats/{nine.pk}/", {"fineness": "376", "display_name": "Nine"})
    with tenant_context(tenant_a.id):
        nine.refresh_from_db()
    assert (nine.fineness, nine.display_name, nine.is_active) == (Decimal("376.000"), "Nine", False)

    # The reference karat stays active whatever is posted.
    owner.post(f"/catalog/karats/{reference.pk}/", {"fineness": str(reference.fineness)})
    with tenant_context(tenant_a.id):
        reference.refresh_from_db()
    assert reference.is_active


def test_managing_needs_the_permissions(tenant_a, make_member):
    make_member(tenant_a, "viewer", "viewer")
    viewer = login(tenant_a, username="viewer")
    viewer.cookies["django_language"] = "en"
    assert "New item category" not in viewer.get("/catalog/categories/").content.decode()
    assert "New karat" not in viewer.get("/catalog/karats/").content.decode()
    for path in ("/catalog/categories/new/", "/catalog/karats/new/"):
        assert viewer.get(path).status_code == 403


def test_family_and_tracking_are_fixed_once_a_category_has_stock(tenant_a):
    from apps.catalog.services import UpdateItemCategoryCommand, update_item_category
    from apps.core.errors import ValidationError
    from apps.sales.tests.test_sales import Shop

    with tenant_context(tenant_a.id):
        category = Shop().ring_a.category
        same = dict(code=category.code, name="Renamed", parent_id=category.parent_id,
                    product_family=category.product_family, default_karat_id=None)
        update_item_category(category.pk, UpdateItemCategoryCommand(
            tracking=category.tracking, **same))  # a rename is fine
        with pytest.raises(ValidationError) as exc:
            update_item_category(category.pk, UpdateItemCategoryCommand(tracking="bulk", **same))
        assert exc.value.code == "CATALOG_CATEGORY_IN_USE"
