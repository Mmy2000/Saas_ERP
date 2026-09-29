from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.routers import SimpleRouter

from apps.core.api.idempotency import idempotent

from .models import ConversionDirection, MoneyMethod, PartySide, Settlement, SettlementKind
from .services import SettlementInput, post_settlement, void_settlement


class SettlementWriteSerializer(serializers.Serializer):
    """Shape only; which fields a kind needs is checked by the service."""

    kind = serializers.ChoiceField(choices=SettlementKind.choices)
    side = serializers.ChoiceField(choices=PartySide.choices)
    party = serializers.IntegerField()
    branch = serializers.IntegerField()
    method = serializers.ChoiceField(choices=MoneyMethod.choices, required=False, default="")
    currency = serializers.CharField(max_length=3, required=False, allow_blank=True, default="")
    amount = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")
    karat = serializers.IntegerField(required=False, allow_null=True, default=None)
    gross_weight_g = serializers.CharField(max_length=30, required=False, allow_blank=True,
                                           default="")
    fine_weight_g = serializers.CharField(max_length=30, required=False, allow_blank=True,
                                          default="")
    direction = serializers.ChoiceField(choices=ConversionDirection.choices, required=False,
                                        default="")
    price_per_fine_g = serializers.CharField(max_length=30, required=False, allow_blank=True,
                                             default="")
    reference = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")
    note = serializers.CharField(required=False, allow_blank=True, default="")
    cash_box = serializers.IntegerField(required=False, allow_null=True, default=None)
    bank_account = serializers.IntegerField(required=False, allow_null=True, default=None)
    terminal = serializers.IntegerField(required=False, allow_null=True, default=None)


class SettlementSerializer(serializers.ModelSerializer):
    party_name = serializers.CharField(source="party.name")
    currency = serializers.CharField(source="currency.code", default=None)

    class Meta:
        model = Settlement
        fields = ["id", "number", "status", "kind", "side", "party", "party_name", "branch",
                  "business_date", "method", "currency", "amount", "fx_rate",
                  "functional_amount", "karat", "gross_weight_g", "fine_weight_g", "direction",
                  "price_per_fine_g", "cash_box", "bank_account", "terminal", "reference",
                  "note"]


class VoidSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class SettlementViewSet(viewsets.GenericViewSet):
    queryset = Settlement.objects.select_related("party", "currency")
    serializer_class = SettlementSerializer
    filterset_fields = {"kind": ["exact"], "side": ["exact"], "party": ["exact"],
                        "status": ["exact"]}
    required_permissions = {
        "list": "settlements.view",
        "retrieve": "settlements.view",
        "create": "settlements.view",  # the service checks the permission for the kind
        "void": "settlements.void",
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        branches = self.request.actor.branch_ids("settlements.view")
        return queryset if branches is None else queryset.filter(branch_id__in=branches)

    def _respond(self, settlement, code=status.HTTP_200_OK):
        return Response(self.get_serializer(self.get_queryset().get(pk=settlement.pk)).data,
                        status=code)

    def list(self, request):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    @idempotent
    def create(self, request):
        payload = SettlementWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        settlement = post_settlement(SettlementInput(
            kind=d["kind"], side=d["side"], party_id=d["party"], branch_id=d["branch"],
            method=d["method"], currency_code=d["currency"], amount=d["amount"] or None,
            karat_id=d["karat"], gross_weight_g=d["gross_weight_g"] or None,
            fine_weight_g=d["fine_weight_g"] or None, direction=d["direction"],
            price_per_fine_g=d["price_per_fine_g"] or None, reference=d["reference"],
            note=d["note"], cash_box_id=d["cash_box"], bank_account_id=d["bank_account"],
            terminal_id=d["terminal"],
        ), actor=request.actor)
        return self._respond(settlement, status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    @idempotent
    def void(self, request, pk=None):
        payload = VoidSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return self._respond(void_settlement(int(pk), reason=payload.validated_data["reason"],
                                             actor=request.actor))


router = SimpleRouter()
router.register("", SettlementViewSet, basename="settlement")
urlpatterns = router.urls
