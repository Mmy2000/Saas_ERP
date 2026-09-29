from decimal import Decimal

import pytest

from apps.catalog.models import Currency, ItemCategory, Karat, ProductFamily, Tracking
from apps.catalog.services import (
    CreateItemCategoryCommand,
    MakingChargeInput,
    create_item_category,
    seed_reference_data,
)
from apps.core.errors import ValidationError
from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db


def _category(code, parent=None, **kw):
    return create_item_category(CreateItemCategoryCommand(
        code=code, name=f"Category {code}", product_family=ProductFamily.GOLD,
        tracking=Tracking.SERIALIZED, parent_id=parent.pk if parent else None, **kw,
    ))


class TestSeeding:
    def test_new_tenant_gets_default_karats_and_currencies(self, tenant_a):
        with tenant_context(tenant_a.id):
            gold = {k.code: k.fineness for k in Karat.objects.filter(metal__code="gold")}
            assert gold == {24: Decimal("999.900"), 22: Decimal("916.667"),
                            21: Decimal("875.000"), 18: Decimal("750.000"),
                            14: Decimal("583.333")}
            assert Karat.objects.get(is_reference=True, metal__code="gold").code == 21
            silver = Karat.objects.get(metal__code="silver")
            assert (silver.code, silver.legacy_code) == (925, 1)
            assert set(Currency.objects.values_list("code", flat=True)) == {"EGP", "USD"}

    def test_24k_fineness_is_a_tenant_choice(self, make_tenant):
        tenant = make_tenant("purist", fineness_24k="1000")
        with tenant_context(tenant.id):
            assert Karat.objects.get(code=24).fineness == Decimal("1000.000")

    def test_uae_tenant_gets_aed(self, make_tenant):
        tenant = make_tenant("dubai", functional_currency="AED", timezone="Asia/Dubai")
        with tenant_context(tenant.id):
            assert set(Currency.objects.values_list("code", flat=True)) == {"AED", "USD"}

    def test_seeding_is_idempotent(self, tenant_a):
        with tenant_context(tenant_a.id):
            before = Karat.objects.count()
            seed_reference_data()
            assert Karat.objects.count() == before


class TestItemCategories:
    def test_tree_depth_is_tracked_and_capped(self, tenant_a):
        with tenant_context(tenant_a.id):
            root = _category("1")
            child = _category("11", parent=root)
            grandchild = _category("111", parent=child)
            assert (root.depth, child.depth, grandchild.depth) == (1, 2, 3)
            with pytest.raises(ValidationError) as exc:
                _category("1111", parent=grandchild)
            assert exc.value.code == "CATALOG_CATEGORY_TOO_DEEP"

    def test_making_charges_per_currency(self, tenant_a):
        with tenant_context(tenant_a.id):
            category = _category("20", barcode_prefix=20, making_charges=(
                MakingChargeInput("EGP", cost_rate_per_g="150", list_rate_per_g="250"),
                MakingChargeInput("USD", cost_rate_per_g="3.5", list_rate_per_g="6"),
            ))
            charges = {c.currency.code: c.list_rate_per_g
                       for c in category.making_charges.select_related("currency")}
            assert charges == {"EGP": Decimal("250"), "USD": Decimal("6")}

    def test_codes_are_unique_per_tenant_only(self, tenant_a, tenant_b):
        with tenant_context(tenant_a.id):
            _category("30")
            with pytest.raises(ValidationError):
                _category("30")
        with tenant_context(tenant_b.id):
            _category("30")
            assert ItemCategory.objects.count() == 1

    def test_commission_rate_is_a_fraction(self, tenant_a):
        with tenant_context(tenant_a.id), pytest.raises(ValidationError):
            _category("40", commission_rate="12.5")

    def test_unknown_currency_rejected(self, tenant_a):
        with tenant_context(tenant_a.id), pytest.raises(ValidationError):
            _category("50", making_charges=(MakingChargeInput("GBP", "1", "2"),))
