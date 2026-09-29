from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.api.idempotency import idempotent
from apps.sales import reservations as service
from apps.sales.models import PaymentKind, PaymentTerms, Reservation
from apps.sales.services import PaymentInput, SaleLineInput

MONEY_KINDS = [(k, k) for k in (PaymentKind.CASH, PaymentKind.CARD, PaymentKind.BANK_TRANSFER)]


class PaymentWriteSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=MONEY_KINDS)
    currency = serializers.CharField(max_length=3)
    amount = serializers.CharField(max_length=30, allow_blank=True)
    cash_box = serializers.IntegerField(required=False, allow_null=True, default=None)
    bank_account = serializers.IntegerField(required=False, allow_null=True, default=None)
    terminal = serializers.IntegerField(required=False, allow_null=True, default=None)

    def to_input(self) -> PaymentInput:
        return _payment(self.validated_data)


def _payment(d) -> PaymentInput:
    return PaymentInput(kind=d["kind"], currency_code=d["currency"], amount=d["amount"],
                        cash_box_id=d["cash_box"], bank_account_id=d["bank_account"],
                        terminal_id=d["terminal"])


class LineWriteSerializer(serializers.Serializer):
    item = serializers.IntegerField(required=False, allow_null=True, default=None)
    barcode = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    discount_rate = serializers.CharField(max_length=20, required=False, default="0")


class ReservationWriteSerializer(serializers.Serializer):
    branch = serializers.IntegerField()
    customer = serializers.IntegerField(allow_null=True)
    lines = LineWriteSerializer(many=True)
    price_locked = serializers.BooleanField(required=False, default=False)
    expires_on = serializers.DateField(required=False, allow_null=True, default=None)
    deposit = PaymentWriteSerializer(required=False, allow_null=True, default=None)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class CompleteSerializer(serializers.Serializer):
    payments = PaymentWriteSerializer(many=True, required=False, default=list)
    payment_terms = serializers.ChoiceField(choices=PaymentTerms.choices, required=False,
                                            default=PaymentTerms.CASH)


class CancelSerializer(serializers.Serializer):
    refund_method = serializers.ChoiceField(choices=[(c, c) for c in service.RefundMethod.CHOICES])
    cash_box = serializers.IntegerField(required=False, allow_null=True, default=None)
    bank_account = serializers.IntegerField(required=False, allow_null=True, default=None)
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class ReservationSerializer(serializers.ModelSerializer):
    state = serializers.CharField(read_only=True)
    customer_name = serializers.CharField(source="customer.name")
    lines = serializers.SerializerMethodField()

    class Meta:
        model = Reservation
        fields = ["id", "number", "state", "branch", "customer", "customer_name",
                  "business_date", "price_locked", "expires_on", "quoted_total",
                  "deposit_amount", "sale", "note", "lines"]

    def get_lines(self, reservation):
        return [{"item": ln.item_id, "barcode": ln.item.barcode,
                 "quoted_total": str(ln.quoted_total)} for ln in reservation.lines.all()]


class ReservationViewSet(viewsets.GenericViewSet):
    queryset = Reservation.objects.select_related("customer").prefetch_related("lines__item")
    serializer_class = ReservationSerializer
    filterset_fields = {"customer": ["exact"], "status": ["exact"]}
    required_permissions = {
        "list": "sales.reservation.view",
        "retrieve": "sales.reservation.view",
        "create": "sales.reservation.create",
        "deposit": "sales.reservation.create",
        "complete": "sales.reservation.complete",
        "cancel": "sales.reservation.cancel",
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        branches = self.request.actor.branch_ids("sales.reservation.view")
        return queryset if branches is None else queryset.filter(branch_id__in=branches)

    def _respond(self, reservation, code=status.HTTP_200_OK, **extra):
        data = self.get_serializer(self.get_queryset().get(pk=reservation.pk)).data
        return Response({**data, **extra}, status=code)

    def list(self, request):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    @idempotent
    def create(self, request):
        payload = ReservationWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        reservation = service.create_reservation(service.ReservationInput(
            branch_id=d["branch"], customer_id=d["customer"], price_locked=d["price_locked"],
            expires_on=d["expires_on"], note=d["note"],
            deposit=_payment(d["deposit"]) if d["deposit"] and d["deposit"]["amount"] else None,
            lines=tuple(SaleLineInput(item_id=ln["item"], barcode=ln["barcode"],
                                      discount_rate=ln["discount_rate"]) for ln in d["lines"]),
        ), actor=request.actor)
        return self._respond(reservation, status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    @idempotent
    def deposit(self, request, pk=None):
        payload = PaymentWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        service.add_deposit(int(pk), payload.to_input(), actor=request.actor)
        return self._respond(self.get_object())

    @action(detail=True, methods=["post"])
    @idempotent
    def complete(self, request, pk=None):
        payload = CompleteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        invoice = service.complete_reservation(
            int(pk), payments=tuple(_payment(p) for p in d["payments"]),
            payment_terms=d["payment_terms"], actor=request.actor)
        return self._respond(self.get_object(), sale_id=invoice.pk)

    @action(detail=True, methods=["post"])
    @idempotent
    def cancel(self, request, pk=None):
        payload = CancelSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        return self._respond(service.cancel_reservation(
            int(pk), refund_method=d["refund_method"], cash_box_id=d["cash_box"],
            bank_account_id=d["bank_account"], reason=d["reason"], actor=request.actor))
