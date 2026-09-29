"""Transfers and stocktakes over the API."""

from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.api.idempotency import idempotent
from apps.inventory import stocktakes, transfers
from apps.inventory.models import Stocktake, StockTransfer

# --- transfers --------------------------------------------------------------------------------


class TransferLineWriteSerializer(serializers.Serializer):
    barcode = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    item = serializers.IntegerField(required=False, allow_null=True, default=None)
    category = serializers.IntegerField(required=False, allow_null=True, default=None)
    karat = serializers.IntegerField(required=False, allow_null=True, default=None)
    gross_weight_g = serializers.CharField(max_length=30, required=False, allow_blank=True,
                                           default="")
    qty = serializers.IntegerField(required=False, min_value=0, default=0)


class TransferWriteSerializer(serializers.Serializer):
    from_branch = serializers.IntegerField()
    to_branch = serializers.IntegerField(allow_null=True)
    lines = TransferLineWriteSerializer(many=True)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class TransferSerializer(serializers.ModelSerializer):
    in_transit = serializers.BooleanField(read_only=True)
    lines = serializers.SerializerMethodField()

    class Meta:
        model = StockTransfer
        fields = ["id", "number", "status", "branch", "to_branch", "business_date", "total_qty",
                  "total_gross_weight_g", "total_fine_weight_g", "in_transit", "received_at",
                  "note", "lines"]

    def get_lines(self, transfer):
        return [{"item": ln.item_id, "barcode": ln.item.barcode if ln.item_id else None,
                 "category": ln.category_id, "karat": ln.karat_id, "qty": ln.qty,
                 "gross_weight_g": str(ln.gross_weight_g),
                 "fine_weight_g": str(ln.fine_weight_g)} for ln in transfer.lines.all()]


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class StockTransferViewSet(viewsets.GenericViewSet):
    queryset = StockTransfer.objects.prefetch_related("lines__item")
    serializer_class = TransferSerializer
    filterset_fields = {"status": ["exact"], "branch": ["exact"], "to_branch": ["exact"]}
    required_permissions = {
        "list": "inventory.stock.view",
        "retrieve": "inventory.stock.view",
        "create": "inventory.transfer.send",
        "receive": "inventory.transfer.receive",
        "void": "inventory.transfer.void",
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        branches = self.request.actor.branch_ids("inventory.stock.view")
        if branches is None:
            return queryset
        return queryset.filter(branch_id__in=branches) | queryset.filter(
            to_branch_id__in=branches)

    def _respond(self, transfer, code=status.HTTP_200_OK):
        return Response(self.get_serializer(self.get_queryset().get(pk=transfer.pk)).data,
                        status=code)

    def list(self, request):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    @idempotent
    def create(self, request):
        payload = TransferWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        transfer = transfers.send_transfer(transfers.TransferInput(
            from_branch_id=d["from_branch"], to_branch_id=d["to_branch"] or 0, note=d["note"],
            lines=tuple(transfers.TransferLineInput(
                barcode=ln["barcode"], item_id=ln["item"], category_id=ln["category"],
                karat_id=ln["karat"], gross_weight_g=ln["gross_weight_g"] or None, qty=ln["qty"])
                for ln in d["lines"])), actor=request.actor)
        return self._respond(transfer, status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    @idempotent
    def receive(self, request, pk=None):
        return self._respond(transfers.receive_transfer(int(pk), actor=request.actor))

    @action(detail=True, methods=["post"])
    @idempotent
    def void(self, request, pk=None):
        payload = ReasonSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return self._respond(transfers.void_transfer(
            int(pk), reason=payload.validated_data["reason"], actor=request.actor))


# --- stocktakes -------------------------------------------------------------------------------


class StocktakeWriteSerializer(serializers.Serializer):
    branch = serializers.IntegerField()
    category = serializers.IntegerField(required=False, allow_null=True, default=None)
    karat = serializers.IntegerField(required=False, allow_null=True, default=None)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class StocktakeSerializer(serializers.ModelSerializer):
    summary = serializers.SerializerMethodField()

    class Meta:
        model = Stocktake
        fields = ["id", "number", "status", "branch", "category", "karat", "business_date",
                  "note", "summary"]

    def get_summary(self, stocktake):
        s = stocktakes.summary(stocktake)
        return {"expected": s.expected, "counted": s.counted, "pending": s.pending,
                "missing": s.missing, "unexpected": s.unexpected, "left": s.left,
                "lots": s.lots, "lots_weighed": s.lots_weighed, "progress": s.progress}


class ScanSerializer(serializers.Serializer):
    barcode = serializers.CharField(max_length=64, allow_blank=True)


class LineSerializer(serializers.Serializer):
    line = serializers.IntegerField()


class WeighSerializer(serializers.Serializer):
    line = serializers.IntegerField()
    gross_weight_g = serializers.CharField(max_length=30, allow_blank=True)
    qty = serializers.IntegerField(required=False, allow_null=True, min_value=0, default=None)


class StocktakeViewSet(viewsets.GenericViewSet):
    queryset = Stocktake.objects.all()
    serializer_class = StocktakeSerializer
    filterset_fields = {"status": ["exact"], "branch": ["exact"]}
    required_permissions = {
        "list": "inventory.stock.view",
        "retrieve": "inventory.stock.view",
        "create": "inventory.stocktake.start",
        "scan": "inventory.stocktake.count",
        "undo": "inventory.stocktake.count",
        "weigh": "inventory.stocktake.count",
        "post_document": "inventory.stocktake.post",
        "cancel": "inventory.stocktake.start",
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        branches = self.request.actor.branch_ids("inventory.stock.view")
        return queryset if branches is None else queryset.filter(branch_id__in=branches)

    def _respond(self, stocktake, code=status.HTTP_200_OK, **extra):
        data = self.get_serializer(self.get_queryset().get(pk=stocktake.pk)).data
        return Response({**data, **extra}, status=code)

    def list(self, request):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    def create(self, request):
        payload = StocktakeWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        stocktake = stocktakes.start_stocktake(d["branch"], category_id=d["category"],
                                               karat_id=d["karat"], note=d["note"],
                                               actor=request.actor)
        return self._respond(stocktake, status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def scan(self, request, pk=None):
        payload = ScanSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        result = stocktakes.scan(int(pk), payload.validated_data["barcode"], actor=request.actor)
        line, item = result.line, result.line.item
        return self._respond(self.get_object(), outcome=result.outcome, line={
            "id": line.pk, "barcode": line.barcode, "result": line.result,
            "result_label": line.get_result_display(), "expected": line.expected,
            "category": item.category.name if item else None,
            "karat": item.karat.label if item and item.karat else None,
            "gross_weight_g": str(item.gross_weight_g) if item else None,
            "status_label": item.get_status_display() if item else None,
        })

    @action(detail=True, methods=["post"])
    def undo(self, request, pk=None):
        payload = LineSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        stocktakes.undo_scan(int(pk), payload.validated_data["line"], actor=request.actor)
        return self._respond(self.get_object())

    @action(detail=True, methods=["post"])
    def weigh(self, request, pk=None):
        payload = WeighSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        stocktakes.weigh_lot(int(pk), d["line"], d["gross_weight_g"], d["qty"],
                             actor=request.actor)
        return self._respond(self.get_object())

    @action(detail=True, methods=["post"], url_path="post")
    @idempotent
    def post_document(self, request, pk=None):
        return self._respond(stocktakes.post_stocktake(int(pk), actor=request.actor))

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        payload = ReasonSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return self._respond(stocktakes.cancel_stocktake(
            int(pk), reason=payload.validated_data["reason"], actor=request.actor))
