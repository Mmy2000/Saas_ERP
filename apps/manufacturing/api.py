from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.routers import SimpleRouter

from apps.core.api.idempotency import idempotent

from . import services
from .models import WorkOrder, WorkOrderKind


class IssueLineSerializer(serializers.Serializer):
    category = serializers.IntegerField()
    karat = serializers.IntegerField()
    gross_weight_g = serializers.CharField(max_length=30, allow_blank=True)
    qty = serializers.IntegerField(required=False, min_value=0, default=0)


class IssueSerializer(serializers.Serializer):
    branch = serializers.IntegerField()
    workshop = serializers.IntegerField(allow_null=True)
    kind = serializers.ChoiceField(choices=WorkOrderKind.choices, required=False,
                                   default=WorkOrderKind.MANUFACTURE)
    lines = IssueLineSerializer(many=True)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class ReceiptLineSerializer(serializers.Serializer):
    category = serializers.IntegerField()
    karat = serializers.IntegerField(allow_null=True)
    piece_weights = serializers.ListField(child=serializers.CharField(max_length=30),
                                          required=False, default=list)
    gross_weight_g = serializers.CharField(max_length=30, required=False, allow_blank=True,
                                           allow_null=True, default=None)
    qty = serializers.IntegerField(required=False, min_value=0, default=0)
    labour_rate = serializers.CharField(max_length=30, required=False, allow_blank=True,
                                        default="0")
    list_making_rate = serializers.CharField(max_length=30, required=False, allow_blank=True,
                                             default="0")


class ReceiptSerializer(serializers.Serializer):
    lines = ReceiptLineSerializer(many=True)
    accept_gain = serializers.BooleanField(required=False, default=False)
    note = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class VoidSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class WorkOrderSerializer(serializers.ModelSerializer):
    workshop_name = serializers.CharField(source="workshop.name")
    state = serializers.CharField(read_only=True)

    class Meta:
        model = WorkOrder
        fields = ["id", "number", "status", "state", "branch", "workshop", "workshop_name", "kind",
                  "business_date", "issued_gross_weight_g", "issued_fine_weight_g",
                  "received_on", "received_fine_weight_g", "loss_fine_weight_g",
                  "gain_fine_weight_g", "labour_amount", "note"]


def _total(per_metal: dict):
    return sum(per_metal.values(), services.ZERO)


def _receipt(request) -> services.ReceiptInput:
    payload = ReceiptSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    d = payload.validated_data
    return services.ReceiptInput(
        accept_gain=d["accept_gain"], note=d["note"],
        lines=tuple(services.ReceiptLineInput(
            category_id=ln["category"], karat_id=ln["karat"],
            piece_weights=tuple(ln["piece_weights"]), gross_weight_g=ln["gross_weight_g"] or None,
            qty=ln["qty"], labour_rate=ln["labour_rate"] or "0",
            list_making_rate=ln["list_making_rate"] or "0") for ln in d["lines"]))


class WorkOrderViewSet(viewsets.GenericViewSet):
    queryset = WorkOrder.objects.select_related("workshop")
    serializer_class = WorkOrderSerializer
    filterset_fields = {"workshop": ["exact"], "status": ["exact"], "kind": ["exact"]}
    required_permissions = {
        "list": "manufacturing.order.view",
        "retrieve": "manufacturing.order.view",
        "create": "manufacturing.order.issue",
        "preview": "manufacturing.order.receive",
        "receive": "manufacturing.order.receive",
        "void": "manufacturing.order.cancel",
        "cancel_receipt": "manufacturing.order.cancel",
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        branches = self.request.actor.branch_ids("manufacturing.order.view")
        return queryset if branches is None else queryset.filter(branch_id__in=branches)

    def _respond(self, order, code=status.HTTP_200_OK):
        return Response(self.get_serializer(self.get_queryset().get(pk=order.pk)).data,
                        status=code)

    def list(self, request):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    @idempotent
    def create(self, request):
        payload = IssueSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        order = services.issue_work_order(services.IssueInput(
            branch_id=d["branch"], workshop_id=d["workshop"], kind=d["kind"], note=d["note"],
            lines=tuple(services.IssueLineInput(
                category_id=ln["category"], karat_id=ln["karat"],
                gross_weight_g=ln["gross_weight_g"], qty=ln["qty"]) for ln in d["lines"])),
            actor=request.actor)
        return self._respond(order, status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def preview(self, request, pk=None):
        """What a receipt would book: fine gold back, loss or gain, labour. Never writes."""
        data = _receipt(request)
        plan = services.plan_receipt(self.get_object(), services.ReceiptInput(
            lines=data.lines, accept_gain=True))
        return Response({
            "issued_fine_g": _total(plan.issued), "received_fine_g": _total(plan.received),
            "loss_fine_g": _total(plan.loss), "gain_fine_g": _total(plan.gain),
            "labour": plan.labour,
            "lines": [{"type": ln.line_type, "qty": ln.qty, "gross_weight_g": ln.gross_weight_g,
                       "fine_weight_g": ln.fine_weight_g, "labour": ln.labour_amount}
                      for ln in plan.lines],
        })

    @action(detail=True, methods=["post"])
    @idempotent
    def receive(self, request, pk=None):
        return self._respond(services.receive_work_order(int(pk), _receipt(request),
                                                         actor=request.actor))

    @action(detail=True, methods=["post"])
    @idempotent
    def void(self, request, pk=None):
        payload = VoidSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return self._respond(services.cancel_work_order(
            int(pk), reason=payload.validated_data["reason"], actor=request.actor))

    @action(detail=True, methods=["post"], url_path="cancel-receipt")
    @idempotent
    def cancel_receipt(self, request, pk=None):
        return self._respond(services.cancel_receipt(int(pk), actor=request.actor))


router = SimpleRouter()
router.register("work-orders", WorkOrderViewSet, basename="work-order-api")
urlpatterns = router.urls
