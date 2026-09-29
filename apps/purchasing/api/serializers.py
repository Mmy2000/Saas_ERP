from decimal import Decimal

from rest_framework import serializers

from apps.purchasing.models import SellerRole, SupplierInvoice, SupplierInvoiceLine


def _decimal(digits, places, **kw):
    return serializers.DecimalField(max_digits=digits, decimal_places=places,
                                    min_value=Decimal(0), **kw)


class LineWriteSerializer(serializers.Serializer):
    category = serializers.IntegerField()
    karat = serializers.IntegerField(required=False, allow_null=True, default=None)
    qty = serializers.IntegerField(min_value=0, required=False, default=0)
    gross_weight_g = _decimal(14, 3, required=False, allow_null=True, default=None)
    piece_weights = serializers.ListField(child=_decimal(14, 3), required=False, default=list)
    making_cost_rate = _decimal(18, 4, required=False, default=Decimal(0))
    list_making_rate = _decimal(18, 4, required=False, default=Decimal(0))
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class InvoiceWriteSerializer(serializers.Serializer):
    """Shape and types only; existence and business rules are checked by the service."""

    supplier = serializers.IntegerField()
    seller_role = serializers.ChoiceField(choices=SellerRole.choices, required=False,
                                          default=SellerRole.SUPPLIER)
    branch = serializers.IntegerField()
    business_date = serializers.DateField()
    currency = serializers.CharField(max_length=3)
    fx_rate = serializers.DecimalField(max_digits=18, decimal_places=8, required=False,
                                       allow_null=True, default=None)
    supplier_reference = serializers.CharField(max_length=60, required=False, allow_blank=True,
                                               default="")
    note = serializers.CharField(required=False, allow_blank=True, default="")
    lines = LineWriteSerializer(many=True)


class VoidSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class LineReadSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name")
    karat_label = serializers.CharField(source="karat.label", default=None)
    tracking = serializers.CharField(source="category.tracking")
    pieces = serializers.SerializerMethodField()

    class Meta:
        model = SupplierInvoiceLine
        fields = ["id", "position", "category", "category_name", "tracking", "karat",
                  "karat_label", "qty", "gross_weight_g", "fine_weight_g", "making_cost_rate",
                  "making_cost_amount", "list_making_rate", "note", "pieces"]

    def get_pieces(self, line):
        return [{"gross_weight_g": str(p.gross_weight_g), "item": p.item_id,
                 "barcode": p.item.barcode if p.item_id else None} for p in line.pieces.all()]


class InvoiceReadSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name")
    currency = serializers.CharField(source="currency.code")
    lines = LineReadSerializer(many=True, read_only=True)
    journal_entry_number = serializers.CharField(source="journal_entry.number", default=None)

    class Meta:
        model = SupplierInvoice
        fields = ["id", "number", "status", "branch", "business_date", "supplier",
                  "seller_role", "supplier_name", "supplier_reference", "currency", "fx_rate",
                  "note", "posted_at", "voided_at", "void_reason", "journal_entry",
                  "journal_entry_number", "lines"]
