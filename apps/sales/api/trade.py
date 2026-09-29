from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.api.idempotency import idempotent
from apps.sales import trade
from apps.sales.models import SettlementBasis, TradeReturn, TradeSale


class LineSerializer(serializers.Serializer):
    barcode = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    item = serializers.IntegerField(required=False, allow_null=True, default=None)
    category = serializers.IntegerField(required=False, allow_null=True, default=None)
    karat = serializers.IntegerField(required=False, allow_null=True, default=None)
    gross_weight_g = serializers.CharField(max_length=30, required=False, allow_blank=True,
                                           default="")
    qty = serializers.IntegerField(required=False, min_value=0, default=0)
    making_rate = serializers.CharField(max_length=30, required=False, allow_blank=True,
                                        allow_null=True, default=None)


class WriteSerializer(serializers.Serializer):
    branch = serializers.IntegerField()
    trade_account = serializers.IntegerField(required=False, allow_null=True, default=None)
    settlement_basis = serializers.ChoiceField(choices=SettlementBasis.choices)
    lines = LineSerializer(many=True)
    note = serializers.CharField(required=False, allow_blank=True, default="")


def _input(request) -> trade.TradeSaleInput:
    payload = WriteSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    d = payload.validated_data
    return trade.TradeSaleInput(
        branch_id=d["branch"], trade_account_id=d["trade_account"],
        settlement_basis=d["settlement_basis"], note=d["note"],
        lines=tuple(trade.TradeLineInput(
            barcode=ln["barcode"], item_id=ln["item"], category_id=ln["category"],
            karat_id=ln["karat"], gross_weight_g=ln["gross_weight_g"] or None, qty=ln["qty"],
            making_rate=ln["making_rate"]) for ln in d["lines"]))


class TradeQuoteView(APIView):
    """Live pricing for the wholesale screen. Never writes; posting recomputes it."""

    required_permissions = {"POST": "sales.trade.create"}

    def post(self, request):
        quote = trade.quote_trade_sale(_input(request))
        return Response({
            "lines": [{
                "item": ln.item.pk if ln.item else None, "lot": ln.lot.pk if ln.lot else None,
                "barcode": ln.label, "category": ln.category.pk,
                "category_name": ln.category.name, "karat": ln.karat.label, "qty": ln.qty,
                "gross_weight_g": ln.gross_weight_g, "fine_weight_g": ln.fine_weight_g,
                "metal_price_per_g": ln.metal_price_per_g, "making_rate": ln.making_rate,
                "metal_amount": ln.metal_amount, "making_amount": ln.making_amount,
            } for ln in quote.lines],
            "categories": [{"id": pk, **values} for pk, values in quote.categories.items()],
            "totals": {
                "qty": quote.total("qty"), "gross_weight_g": quote.total("gross_weight_g"),
                "fine_weight_g": quote.total("fine_weight_g"),
                "metal_amount": quote.total("metal_amount"),
                "making_amount": quote.total("making_amount"),
                "money_amount": quote.money_amount,
            },
        })


class TradeSaleSerializer(serializers.ModelSerializer):
    trade_account_name = serializers.CharField(source="trade_account.name")

    class Meta:
        model = TradeSale
        fields = ["id", "number", "status", "branch", "trade_account", "trade_account_name",
                  "settlement_basis", "business_date", "total_qty", "total_gross_weight_g",
                  "total_fine_weight_g", "metal_amount", "making_amount", "money_amount",
                  "note"]


class TradeReturnSerializer(serializers.ModelSerializer):
    trade_account_name = serializers.CharField(source="trade_account.name")

    class Meta:
        model = TradeReturn
        fields = ["id", "number", "status", "branch", "original_sale", "trade_account",
                  "trade_account_name", "business_date", "total_gross_weight_g",
                  "total_fine_weight_g", "money_amount", "reason"]


class VoidSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class ReturnWriteSerializer(serializers.Serializer):
    sale = serializers.IntegerField()
    lines = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class _Documents(viewsets.GenericViewSet):
    def get_queryset(self):
        queryset = super().get_queryset()
        branches = self.request.actor.branch_ids("sales.trade.view")
        return queryset if branches is None else queryset.filter(branch_id__in=branches)

    def _respond(self, doc, code=status.HTTP_200_OK):
        return Response(self.get_serializer(self.get_queryset().get(pk=doc.pk)).data,
                        status=code)

    def list(self, request):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)


class TradeSaleViewSet(_Documents):
    queryset = TradeSale.objects.select_related("trade_account")
    serializer_class = TradeSaleSerializer
    filterset_fields = {"trade_account": ["exact"], "status": ["exact"],
                        "settlement_basis": ["exact"]}
    required_permissions = {
        "list": "sales.trade.view",
        "retrieve": "sales.trade.view",
        "create": "sales.trade.create",
        "void": "sales.trade.void",
    }

    @idempotent
    def create(self, request):
        doc = trade.post_trade_sale(_input(request), actor=request.actor)
        return self._respond(doc, status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    @idempotent
    def void(self, request, pk=None):
        payload = VoidSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return self._respond(trade.void_trade_sale(
            int(pk), reason=payload.validated_data["reason"], actor=request.actor))


class TradeReturnViewSet(_Documents):
    queryset = TradeReturn.objects.select_related("trade_account")
    serializer_class = TradeReturnSerializer
    filterset_fields = {"trade_account": ["exact"], "original_sale": ["exact"],
                        "status": ["exact"]}
    required_permissions = {
        "list": "sales.trade.view",
        "retrieve": "sales.trade.view",
        "create": "sales.trade.return",
        "void": "sales.trade.return",
    }

    @idempotent
    def create(self, request):
        payload = ReturnWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        doc = trade.post_trade_return(d["sale"], d["lines"], reason=d["reason"],
                                      actor=request.actor)
        return self._respond(doc, status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    @idempotent
    def void(self, request, pk=None):
        payload = VoidSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return self._respond(trade.void_trade_return(
            int(pk), reason=payload.validated_data["reason"], actor=request.actor))
