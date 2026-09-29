from rest_framework import serializers

from apps.catalog.models import CategoryMakingCharge, Currency, ItemCategory, Karat


class CurrencySerializer(serializers.ModelSerializer):
    label = serializers.CharField(read_only=True)

    class Meta:
        model = Currency
        fields = ["id", "code", "name", "label", "symbol", "minor_units", "is_active"]


class KaratSerializer(serializers.ModelSerializer):
    metal = serializers.CharField(source="metal.code")
    label = serializers.CharField(read_only=True)

    class Meta:
        model = Karat
        fields = ["id", "metal", "code", "fineness", "is_reference", "display_name", "label",
                  "is_active"]


class MakingChargeSerializer(serializers.ModelSerializer):
    currency = serializers.CharField(source="currency.code")

    class Meta:
        model = CategoryMakingCharge
        fields = ["currency", "cost_rate_per_g", "list_rate_per_g"]


class ItemCategorySerializer(serializers.ModelSerializer):
    # TODO(rbac): omit cost_rate_per_g without `inventory.item.view_cost` (§14.2).
    making_charges = MakingChargeSerializer(many=True, read_only=True)

    class Meta:
        model = ItemCategory
        fields = ["id", "code", "name", "short_name", "parent", "depth", "product_family",
                  "tracking", "default_karat", "barcode_prefix", "commission_rate",
                  "reorder_min_qty", "reorder_max_qty", "is_active", "making_charges"]
