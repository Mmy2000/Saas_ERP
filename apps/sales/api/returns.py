from decimal import Decimal

from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.api.idempotency import idempotent
from apps.sales.models import RefundMethod, SalesReturn
from apps.sales.returns import create_return, void_return


class ReturnWriteSerializer(serializers.Serializer):
    invoice = serializers.IntegerField()
    lines = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)
    refund_method = serializers.ChoiceField(choices=RefundMethod.choices)
    deduction_amount = serializers.DecimalField(max_digits=18, decimal_places=2,
                                                min_value=Decimal(0), required=False,
                                                default=Decimal(0))
    cash_box = serializers.IntegerField(required=False, allow_null=True, default=None)
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class ReturnSerializer(serializers.ModelSerializer):
    original_number = serializers.CharField(source="original_invoice.number")
    lines = serializers.SerializerMethodField()

    class Meta:
        model = SalesReturn
        fields = ["id", "number", "status", "branch", "business_date", "original_invoice",
                  "original_number", "customer", "refund_method", "returned_amount",
                  "deduction_amount", "refund_amount", "cash_box", "reason", "lines"]

    def get_lines(self, sales_return):
        return [{"line": ln.original_line_id, "barcode": ln.original_line.label,
                 "line_total": str(ln.original_line.line_total)}
                for ln in sales_return.lines.all()]


class VoidSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class SalesReturnViewSet(viewsets.GenericViewSet):
    queryset = (SalesReturn.objects.select_related("original_invoice")
                .prefetch_related("lines__original_line__item"))
    serializer_class = ReturnSerializer
    required_permissions = {
        "list": "sales.invoice.view",
        "retrieve": "sales.invoice.view",
        "create": "sales.return.create",
        "void": "sales.return.void",
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        branches = self.request.actor.branch_ids("sales.invoice.view")
        return queryset if branches is None else queryset.filter(branch_id__in=branches)

    def _respond(self, sales_return, code=status.HTTP_200_OK):
        return Response(self.get_serializer(self.get_queryset().get(pk=sales_return.pk)).data,
                        status=code)

    def list(self, request):
        page = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    @idempotent
    def create(self, request):
        payload = ReturnWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        sales_return = create_return(data["invoice"], data["lines"],
                                     refund_method=data["refund_method"],
                                     deduction_amount=data["deduction_amount"],
                                     cash_box_id=data["cash_box"], reason=data["reason"],
                                     actor=request.actor)
        return self._respond(sales_return, status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    @idempotent
    def void(self, request, pk=None):
        payload = VoidSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return self._respond(void_return(int(pk), reason=payload.validated_data["reason"],
                                         actor=request.actor))
