from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.routers import SimpleRouter

from apps.core.api.idempotency import idempotent
from apps.sales.services import PaymentInput

from . import services
from .models import RepairKind, RepairOrder


class PaymentSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["cash", "card", "bank_transfer"])
    currency = serializers.CharField(max_length=3)
    amount = serializers.CharField(max_length=30)
    reference = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")
    cash_box = serializers.IntegerField(required=False, allow_null=True, default=None)
    bank_account = serializers.IntegerField(required=False, allow_null=True, default=None)
    terminal = serializers.IntegerField(required=False, allow_null=True, default=None)


def _payment(d) -> PaymentInput:
    return PaymentInput(kind=d["kind"], currency_code=d["currency"], amount=d["amount"],
                        reference=d["reference"], cash_box_id=d["cash_box"],
                        bank_account_id=d["bank_account"], terminal_id=d["terminal"])


class LineSerializer(serializers.Serializer):
    description = serializers.CharField(max_length=300, allow_blank=True)
    karat = serializers.IntegerField(required=False, allow_null=True, default=None)
    weight_in_g = serializers.CharField(max_length=30, required=False, allow_blank=True,
                                        default="0")
    charge = serializers.CharField(max_length=30, required=False, allow_blank=True, default="0")


class CreateSerializer(serializers.Serializer):
    branch = serializers.IntegerField()
    kind = serializers.ChoiceField(choices=RepairKind.choices, required=False,
                                   default=RepairKind.REPAIR)
    customer = serializers.IntegerField(required=False, allow_null=True, default=None)
    customer_name = serializers.CharField(max_length=200, required=False, allow_blank=True,
                                          default="")
    customer_phone = serializers.CharField(max_length=30, required=False, allow_blank=True,
                                           default="")
    promised_on = serializers.DateField(required=False, allow_null=True, default=None)
    lines = LineSerializer(many=True)
    deposit = PaymentSerializer(required=False, allow_null=True, default=None)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class SendSerializer(serializers.Serializer):
    workshop = serializers.IntegerField()


class ReadyLineSerializer(serializers.Serializer):
    line = serializers.IntegerField()
    weight_out_g = serializers.CharField(max_length=30, allow_blank=True)
    charge = serializers.CharField(max_length=30, required=False, allow_blank=True,
                                   allow_null=True, default=None)


class ReadySerializer(serializers.Serializer):
    lines = ReadyLineSerializer(many=True)
    labour_amount = serializers.CharField(max_length=30, required=False, allow_blank=True,
                                          default="0")


class DeliverSerializer(serializers.Serializer):
    payments = PaymentSerializer(many=True, required=False, default=list)
    on_account = serializers.BooleanField(required=False, default=False)


class CancelSerializer(serializers.Serializer):
    refund_method = serializers.ChoiceField(choices=services.RefundMethod.CHOICES,
                                            required=False, default=services.RefundMethod.CASH)
    cash_box = serializers.IntegerField(required=False, allow_null=True, default=None)
    bank_account = serializers.IntegerField(required=False, allow_null=True, default=None)
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class RepairSerializer(serializers.ModelSerializer):
    state = serializers.CharField(read_only=True)
    who = serializers.CharField(read_only=True)

    class Meta:
        model = RepairOrder
        fields = ["id", "number", "status", "state", "kind", "branch", "bag_number", "customer",
                  "who", "customer_phone", "business_date", "promised_on", "workshop",
                  "sent_on", "ready_on", "charge_amount", "deposit_amount", "labour_amount",
                  "paid_amount", "change_amount", "balance_amount", "note"]


class RepairViewSet(viewsets.GenericViewSet):
    queryset = RepairOrder.objects.select_related("customer", "workshop")
    serializer_class = RepairSerializer
    filterset_fields = {"customer": ["exact"], "workshop": ["exact"], "status": ["exact"],
                        "bag_number": ["exact"]}
    required_permissions = {
        "list": "repairs.order.view",
        "retrieve": "repairs.order.view",
        "create": "repairs.order.create",
        "deposit": "repairs.order.create",
        "send": "repairs.order.update",
        "ready": "repairs.order.update",
        "deliver": "repairs.order.deliver",
        "void": "repairs.order.cancel",
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        branches = self.request.actor.branch_ids("repairs.order.view")
        return queryset if branches is None else queryset.filter(branch_id__in=branches)

    def _respond(self, order, code=status.HTTP_200_OK):
        return Response(self.get_serializer(self.get_queryset().get(pk=order.pk)).data,
                        status=code)

    def _data(self, serializer_class):
        payload = serializer_class(data=self.request.data)
        payload.is_valid(raise_exception=True)
        return payload.validated_data

    def list(self, request):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    @idempotent
    def create(self, request):
        d = self._data(CreateSerializer)
        order = services.create_repair(services.RepairInput(
            branch_id=d["branch"], kind=d["kind"], customer_id=d["customer"],
            customer_name=d["customer_name"], customer_phone=d["customer_phone"],
            promised_on=d["promised_on"], note=d["note"],
            deposit=_payment(d["deposit"]) if d["deposit"] else None,
            lines=tuple(services.RepairLineInput(
                description=ln["description"], karat_id=ln["karat"],
                weight_in_g=ln["weight_in_g"] or "0", charge=ln["charge"] or "0")
                for ln in d["lines"])), actor=request.actor)
        return self._respond(order, status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    @idempotent
    def deposit(self, request, pk=None):
        services.add_deposit(int(pk), _payment(self._data(PaymentSerializer)), actor=request.actor)
        return self._respond(self.get_object())

    @action(detail=True, methods=["post"])
    @idempotent
    def send(self, request, pk=None):
        d = self._data(SendSerializer)
        return self._respond(services.send_to_workshop(int(pk), d["workshop"],
                                                       actor=request.actor))

    @action(detail=True, methods=["post"])
    @idempotent
    def ready(self, request, pk=None):
        d = self._data(ReadySerializer)
        return self._respond(services.mark_ready(
            int(pk), tuple(services.ReadyLineInput(ln["line"], ln["weight_out_g"], ln["charge"])
                           for ln in d["lines"]),
            labour_amount=d["labour_amount"] or "0", actor=request.actor))

    @action(detail=True, methods=["post"])
    @idempotent
    def deliver(self, request, pk=None):
        d = self._data(DeliverSerializer)
        return self._respond(services.deliver(
            int(pk), payments=tuple(_payment(p) for p in d["payments"]),
            on_account=d["on_account"], actor=request.actor))

    @action(detail=True, methods=["post"])
    @idempotent
    def void(self, request, pk=None):
        d = self._data(CancelSerializer)
        return self._respond(services.cancel_repair(
            int(pk), refund_method=d["refund_method"], cash_box_id=d["cash_box"],
            bank_account_id=d["bank_account"], reason=d["reason"], actor=request.actor))


router = SimpleRouter()
router.register("", RepairViewSet, basename="repair-api")
urlpatterns = router.urls
