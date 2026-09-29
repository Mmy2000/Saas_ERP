from rest_framework import serializers

from apps.catalog.models import Currency, Karat
from apps.pricing.models import FxRate, MetalPriceBoard, MetalPriceBoardLine


class BoardLineSerializer(serializers.ModelSerializer):
    karat_name = serializers.CharField(source="karat.label", read_only=True)

    class Meta:
        model = MetalPriceBoardLine
        fields = ["karat", "karat_name", "sell_price_per_g", "buy_price_per_g",
                  "scrap_buy_price_per_g", "is_derived"]


class PriceBoardSerializer(serializers.ModelSerializer):
    lines = BoardLineSerializer(many=True, read_only=True)

    class Meta:
        model = MetalPriceBoard
        fields = ["id", "effective_at", "source", "reference_karat", "note", "lines"]


def _price_field(**kw):
    return serializers.DecimalField(max_digits=18, decimal_places=4, min_value=0, **kw)


class KaratPriceSerializer(serializers.Serializer):
    # Resolved through the tenant-scoped queryset: another tenant's karat id is a 400.
    karat = serializers.PrimaryKeyRelatedField(queryset=Karat.objects.filter(is_active=True))
    sell_price_per_g = _price_field()
    buy_price_per_g = _price_field(required=False, allow_null=True)
    scrap_buy_price_per_g = _price_field(required=False, allow_null=True)


class PublishBoardSerializer(serializers.Serializer):
    reference = KaratPriceSerializer()
    overrides = KaratPriceSerializer(many=True, required=False, default=list)
    effective_at = serializers.DateTimeField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True, max_length=200, default="")


class FxRateSerializer(serializers.ModelSerializer):
    currency = serializers.CharField(source="currency.code")

    class Meta:
        model = FxRate
        fields = ["id", "currency", "rate", "effective_at", "source"]


class RecordFxRateSerializer(serializers.Serializer):
    currency = serializers.SlugRelatedField(slug_field="code",
                                            queryset=Currency.objects.filter(is_active=True))
    rate = serializers.DecimalField(max_digits=18, decimal_places=8, min_value=0)
    effective_at = serializers.DateTimeField(required=False, allow_null=True)
