from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.api.idempotency import idempotent
from apps.purchasing import returns
from apps.purchasing.models import SellerRole, SupplierReturn


class LineSerializer(serializers.Serializer):
    barcode = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    item = serializers.IntegerField(required=False, allow_null=True, default=None)
    category = serializers.IntegerField(required=False, allow_null=True, default=None)
    karat = serializers.IntegerField(required=False, allow_null=True, default=None)
    gross_weight_g = serializers.CharField(max_length=30, required=False, allow_blank=True,
                                           default="")
    qty = serializers.IntegerField(required=False, min_value=0, default=0)


class WriteSerializer(serializers.Serializer):
    branch = serializers.IntegerField()
    supplier = serializers.IntegerField(allow_null=True)
    seller_role = serializers.ChoiceField(choices=SellerRole.choices, required=False,
                                          default=SellerRole.SUPPLIER)
    lines = LineSerializer(many=True)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class ReturnSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name")

    class Meta:
        model = SupplierReturn
        fields = ["id", "number", "status", "branch", "supplier", "seller_role", "supplier_name",
                  "business_date", "total_qty", "total_gross_weight_g", "total_fine_weight_g",
                  "total_cost", "note"]


class VoidSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class SupplierReturnViewSet(viewsets.GenericViewSet):
    queryset = SupplierReturn.objects.select_related("supplier")
    serializer_class = ReturnSerializer
    filterset_fields = {"supplier": ["exact"], "status": ["exact"]}
    required_permissions = {
        "list": "purchasing.invoice.view",
        "retrieve": "purchasing.invoice.view",
        "create": "purchasing.return.create",
        "void": "purchasing.return.void",
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        branches = self.request.actor.branch_ids("purchasing.invoice.view")
        return queryset if branches is None else queryset.filter(branch_id__in=branches)

    def _respond(self, doc, code=status.HTTP_200_OK):
        return Response(self.get_serializer(self.get_queryset().get(pk=doc.pk)).data,
                        status=code)

    def list(self, request):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    @idempotent
    def create(self, request):
        payload = WriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        doc = returns.post_supplier_return(returns.SupplierReturnInput(
            branch_id=d["branch"], supplier_id=d["supplier"], note=d["note"],
            seller_role=d["seller_role"],
            lines=tuple(returns.ReturnLineInput(
                barcode=ln["barcode"], item_id=ln["item"], category_id=ln["category"],
                karat_id=ln["karat"], gross_weight_g=ln["gross_weight_g"] or None, qty=ln["qty"])
                for ln in d["lines"])), actor=request.actor)
        return self._respond(doc, status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    @idempotent
    def void(self, request, pk=None):
        payload = VoidSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return self._respond(returns.void_supplier_return(
            int(pk), reason=payload.validated_data["reason"], actor=request.actor))
