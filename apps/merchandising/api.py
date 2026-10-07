from django.urls import path, reverse
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.api.idempotency import idempotent
from apps.core.errors import NotFound

from . import offers as offer_service
from . import prices
from .models import Offer, OfferKind, PriceMethod, PriceTarget


class FilterSerializer(serializers.Serializer):
    branch_ids = serializers.ListField(child=serializers.IntegerField(), required=False,
                                       default=list)
    category_ids = serializers.ListField(child=serializers.IntegerField(), required=False,
                                         default=list)
    karat_ids = serializers.ListField(child=serializers.IntegerField(), required=False,
                                      default=list)
    supplier_ids = serializers.ListField(child=serializers.IntegerField(), required=False,
                                         default=list)
    min_weight = serializers.DecimalField(max_digits=12, decimal_places=3, required=False,
                                          allow_null=True, default=None)
    max_weight = serializers.DecimalField(max_digits=12, decimal_places=3, required=False,
                                          allow_null=True, default=None)
    barcodes = serializers.CharField(required=False, allow_blank=True, default="")


class ChangeSerializer(serializers.Serializer):
    target = serializers.ChoiceField(choices=PriceTarget.choices)
    method = serializers.ChoiceField(choices=PriceMethod.choices)
    value = serializers.CharField(max_length=30)
    filters = FilterSerializer()
    summary = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


def _rules(data: dict) -> prices.PieceFilter:
    barcodes = [b.strip() for b in data["barcodes"].replace(",", "\n").splitlines() if b.strip()]
    return prices.PieceFilter(
        branch_ids=data["branch_ids"], category_ids=data["category_ids"],
        karat_ids=data["karat_ids"], supplier_ids=data["supplier_ids"],
        min_weight=data["min_weight"], max_weight=data["max_weight"], barcodes=barcodes[:20000])


class PreviewView(APIView):
    required_permissions = {"POST": "inventory.price.change"}

    def post(self, request):
        serializer = ChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        return Response(prices.preview(
            data["target"], data["method"], data["value"], _rules(data["filters"]),
            scope=request.actor.branch_ids("inventory.price.change")))


class ChangeView(APIView):
    required_permissions = {"POST": "inventory.price.change"}

    @idempotent
    def post(self, request):
        serializer = ChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        change = prices.apply_change(
            data["target"], data["method"], data["value"], _rules(data["filters"]),
            summary=data["summary"], note=data["note"],
            scope=request.actor.branch_ids("inventory.price.change"), actor=request.actor)
        return Response({"id": change.pk, "count": change.item_count,
                         "url": reverse("price-change", args=[change.pk])}, status=201)


class UndoView(APIView):
    required_permissions = {"POST": "inventory.price.change"}

    def post(self, request, pk):
        change = prices.undo_change(pk, actor=request.actor)
        return Response({"id": change.pk, "restored": change.undone_count,
                         "skipped": change.item_count - change.undone_count})


class PieceSerializer(serializers.Serializer):
    target = serializers.ChoiceField(choices=PriceTarget.choices)
    value = serializers.CharField(max_length=30)
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class PieceView(APIView):
    """One piece's making charge per gram (gold) or label price (diamonds)."""

    required_permissions = {"PUT": "inventory.price.change"}

    def put(self, request, pk):
        serializer = PieceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        change = prices.set_piece_price(pk, data["target"], data["value"], note=data["note"],
                                        actor=request.actor)
        return Response({"id": change.pk})


class OfferSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    kind = serializers.ChoiceField(choices=OfferKind.choices)
    value = serializers.DecimalField(max_digits=18, decimal_places=4)
    starts_on = serializers.DateField()
    ends_on = serializers.DateField()
    is_active = serializers.BooleanField(required=False, default=True)
    category_ids = serializers.ListField(child=serializers.IntegerField(), required=False,
                                         default=list)
    karat_ids = serializers.ListField(child=serializers.IntegerField(), required=False,
                                      default=list)
    supplier_ids = serializers.ListField(child=serializers.IntegerField(), required=False,
                                         default=list)
    branch_ids = serializers.ListField(child=serializers.IntegerField(), required=False,
                                       default=list)
    barcodes = serializers.CharField(required=False, allow_blank=True, default="")
    note = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")

    def validate_value(self, value):
        if value <= 0:
            raise serializers.ValidationError("Must be greater than zero.")
        return value


def _offer_data(data: dict) -> dict:
    return {**data, "barcodes": [b.strip() for b in data["barcodes"].replace(",", "\n")
                                 .splitlines() if b.strip()]}


class OfferListView(APIView):
    required_permissions = {"POST": "sales.offer.manage"}

    @idempotent
    def post(self, request):
        serializer = OfferSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        offer = offer_service.save_offer(_offer_data(serializer.validated_data),
                                         actor=request.actor)
        return Response({"id": offer.pk, "url": reverse("offer", args=[offer.pk])}, status=201)


class OfferView(APIView):
    required_permissions = {"PUT": "sales.offer.manage"}

    def put(self, request, pk):
        offer = Offer.objects.filter(pk=pk).first()
        if offer is None:
            raise NotFound
        serializer = OfferSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        offer = offer_service.save_offer(_offer_data(serializer.validated_data), offer=offer,
                                         actor=request.actor)
        return Response({"id": offer.pk})


urlpatterns = [
    path("prices/preview/", PreviewView.as_view(), name="price-change-preview"),
    path("prices/", ChangeView.as_view(), name="price-change-create"),
    path("prices/<int:pk>/undo/", UndoView.as_view(), name="price-change-undo"),
    path("items/<int:pk>/price/", PieceView.as_view(), name="piece-price"),
    path("offers/", OfferListView.as_view(), name="offer-create"),
    path("offers/<int:pk>/", OfferView.as_view(), name="offer-update"),
]
