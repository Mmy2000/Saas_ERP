from decimal import Decimal

from rest_framework import serializers

from apps.sales.models import PaymentKind, PaymentTerms, SalesInvoice


def _amount(places=3, **kw):
    return serializers.DecimalField(max_digits=18, decimal_places=places, min_value=Decimal(0),
                                    **kw)


class SaleLineSerializer(serializers.Serializer):
    item = serializers.IntegerField(required=False, allow_null=True, default=None)
    barcode = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    discount_rate = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=Decimal(0),
                                            max_value=Decimal(1), required=False,
                                            default=Decimal(0))
    # Bulk gold by weight, instead of a piece.
    category = serializers.IntegerField(required=False, allow_null=True, default=None)
    karat = serializers.IntegerField(required=False, allow_null=True, default=None)
    gross_weight_g = _amount(required=False, allow_null=True, default=None)
    qty = serializers.IntegerField(required=False, min_value=0, default=0)


class TradeInSerializer(serializers.Serializer):
    karat = serializers.IntegerField()
    gross_weight_g = _amount()
    loss_weight_g = _amount(required=False, default=Decimal(0))
    price_override = _amount(4, required=False, allow_null=True, default=None)


class PaymentSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=PaymentKind.choices)
    currency = serializers.CharField(max_length=3)
    amount = _amount(2)
    reference = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")
    cash_box = serializers.IntegerField(required=False, allow_null=True, default=None)
    bank_account = serializers.IntegerField(required=False, allow_null=True, default=None)
    terminal = serializers.IntegerField(required=False, allow_null=True, default=None)


class SaleSerializer(serializers.Serializer):
    branch = serializers.IntegerField()
    lines = SaleLineSerializer(many=True, required=False, default=list)
    trade_ins = TradeInSerializer(many=True, required=False, default=list)
    payments = PaymentSerializer(many=True, required=False, default=list)
    payment_terms = serializers.ChoiceField(choices=PaymentTerms.choices, required=False,
                                            default=PaymentTerms.CASH)
    customer = serializers.IntegerField(required=False, allow_null=True, default=None)
    customer_name = serializers.CharField(max_length=200, required=False, allow_blank=True,
                                          default="")
    customer_phone = serializers.CharField(max_length=30, required=False, allow_blank=True,
                                           default="")
    note = serializers.CharField(required=False, allow_blank=True, default="")


class VoidSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class InvoiceSerializer(serializers.ModelSerializer):
    buyer = serializers.CharField(read_only=True)
    lines = serializers.SerializerMethodField()
    trade_ins = serializers.SerializerMethodField()
    payments = serializers.SerializerMethodField()

    class Meta:
        model = SalesInvoice
        fields = ["id", "number", "status", "branch", "business_date", "customer", "buyer",
                  "customer_phone", "payment_terms", "subtotal_amount", "discount_amount",
                  "total_amount", "trade_in_amount", "paid_amount", "change_amount",
                  "balance_amount", "posted_at", "voided_at", "void_reason", "lines",
                  "trade_ins", "payments"]

    def get_lines(self, invoice):
        return [{"item": ln.item_id, "lot": ln.lot_id, "barcode": ln.label,
                 "category": ln.category_id, "qty": ln.qty,
                 "karat": ln.karat.label if ln.karat_id else "",
                 "gross_weight_g": str(ln.gross_weight_g),
                 "metal_price_per_g": str(ln.metal_price_per_g),
                 "making_rate_net": str(ln.making_rate_net),
                 "discount_rate": str(ln.discount_rate), "line_total": str(ln.line_total)}
                for ln in invoice.lines.all()]

    def get_trade_ins(self, invoice):
        return [{"karat": t.karat.label, "net_weight_g": str(t.net_weight_g),
                 "price_per_g": str(t.price_per_g), "amount": str(t.amount)}
                for t in invoice.trade_ins.all()]

    def get_payments(self, invoice):
        return [{"kind": p.kind, "currency": p.currency.code, "amount": str(p.amount),
                 "functional_amount": str(p.functional_amount), "cash_box": p.cash_box_id,
                 "bank_account": p.bank_account_id, "terminal": p.terminal_id}
                for p in invoice.payments.all()]
