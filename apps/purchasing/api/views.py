from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.purchasing import services
from apps.purchasing.models import SupplierInvoice

from .serializers import InvoiceReadSerializer, InvoiceWriteSerializer, VoidSerializer


def invoice_input(data) -> services.InvoiceInput:
    return services.InvoiceInput(
        supplier_id=data["supplier"], branch_id=data["branch"],
        business_date=data["business_date"], currency_code=data["currency"],
        fx_rate=data["fx_rate"], supplier_reference=data["supplier_reference"],
        note=data["note"], seller_role=data["seller_role"],
        lines=tuple(services.LineInput(
            category_id=line["category"], karat_id=line["karat"], qty=line["qty"],
            gross_weight_g=line["gross_weight_g"], piece_weights=tuple(line["piece_weights"]),
            making_cost_rate=line["making_cost_rate"], list_making_rate=line["list_making_rate"],
            note=line["note"],
        ) for line in data["lines"]),
    )


class SupplierInvoiceViewSet(viewsets.GenericViewSet):
    queryset = (SupplierInvoice.objects.select_related("supplier", "currency", "journal_entry")
                .prefetch_related("lines__category", "lines__karat__metal", "lines__pieces__item"))
    serializer_class = InvoiceReadSerializer
    filterset_fields = {"status": ["exact"], "supplier": ["exact"],
                        "business_date": ["gte", "lte"]}
    required_permissions = {
        "list": "purchasing.invoice.view",
        "retrieve": "purchasing.invoice.view",
        "create": "purchasing.invoice.create",
        "update": "purchasing.invoice.create",
        "destroy": "purchasing.invoice.create",
        "post_document": "purchasing.invoice.post",
        "void": "purchasing.invoice.void",
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        branches = self.request.actor.branch_ids("purchasing.invoice.view")
        return queryset if branches is None else queryset.filter(branch_id__in=branches)

    def _respond(self, invoice, code=status.HTTP_200_OK):
        return Response(self.get_serializer(self.get_queryset().get(pk=invoice.pk)).data,
                        status=code)

    def _payload(self):
        payload = InvoiceWriteSerializer(data=self.request.data)
        payload.is_valid(raise_exception=True)
        return invoice_input(payload.validated_data)

    def list(self, request):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    def create(self, request):
        invoice = services.save_draft(self._payload(), actor=request.actor)
        return self._respond(invoice, status.HTTP_201_CREATED)

    def update(self, request, pk=None):
        invoice = services.save_draft(self._payload(), invoice_id=int(pk), actor=request.actor)
        return self._respond(invoice)

    def destroy(self, request, pk=None):
        services.delete_draft(int(pk), actor=request.actor)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="post")
    def post_document(self, request, pk=None):
        return self._respond(services.post_invoice(int(pk), actor=request.actor))

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        payload = VoidSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        invoice = services.void_invoice(int(pk), reason=payload.validated_data["reason"],
                                        actor=request.actor)
        return self._respond(invoice)
