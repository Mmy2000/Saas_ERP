from django.db.models import Q
from django.urls import path
from django.utils.translation import gettext as _
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.api.idempotency import idempotent
from apps.core.errors import NotFound
from apps.inventory.models import Item, ItemStatus

from . import services
from .models import StoneKind, StoneShape


class StoneSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=StoneKind.choices, required=False,
                                   default=StoneKind.DIAMOND)
    shape = serializers.ChoiceField(choices=StoneShape.choices, required=False, allow_blank=True,
                                    default="")
    count = serializers.IntegerField(required=False, min_value=1, default=1)
    carat = serializers.CharField(max_length=20, allow_blank=True)
    color = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    clarity = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    cut = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    lab = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    certificate_no = serializers.CharField(max_length=60, required=False, allow_blank=True,
                                           default="")
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class PieceSerializer(serializers.Serializer):
    category = serializers.IntegerField()
    karat = serializers.IntegerField(required=False, allow_null=True, default=None)
    gross_weight_g = serializers.CharField(max_length=20, required=False, allow_blank=True,
                                           allow_null=True, default=None)
    making_cost_rate = serializers.CharField(max_length=20, required=False, allow_blank=True,
                                             default="0")
    stone_cost = serializers.CharField(max_length=20, required=False, allow_blank=True,
                                       default="0")
    label_price = serializers.CharField(max_length=20, required=False, allow_blank=True,
                                        allow_null=True, default=None)
    stones = StoneSerializer(many=True, required=False, default=list)


class ReceiveSerializer(serializers.Serializer):
    supplier = serializers.IntegerField(allow_null=True)
    branch = serializers.IntegerField()
    currency = serializers.CharField(max_length=3)
    supplier_reference = serializers.CharField(max_length=60, required=False, allow_blank=True,
                                               default="")
    note = serializers.CharField(required=False, allow_blank=True, default="")
    pieces = PieceSerializer(many=True)


class ReceiveView(APIView):
    required_permissions = {"POST": "diamonds.receive"}

    @idempotent
    def post(self, request):
        payload = ReceiveSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        invoice = services.receive(services.ReceiveInput(
            supplier_id=d["supplier"] or 0, branch_id=d["branch"], currency_code=d["currency"],
            supplier_reference=d["supplier_reference"], note=d["note"],
            pieces=tuple(services.PieceInput(
                category_id=p["category"], karat_id=p["karat"],
                gross_weight_g=p["gross_weight_g"], making_cost_rate=p["making_cost_rate"] or "0",
                stone_cost=p["stone_cost"] or "0", label_price=p["label_price"] or None,
                stones=tuple(p["stones"])) for p in d["pieces"])), actor=request.actor)
        return Response({"id": invoice.pk, "number": invoice.number},
                        status=status.HTTP_201_CREATED)


class ItemStonesSerializer(serializers.Serializer):
    stones = StoneSerializer(many=True)
    label_price = serializers.CharField(max_length=20, required=False, allow_blank=True,
                                        allow_null=True, default=None)


class ItemStonesView(APIView):
    required_permissions = {"PUT": "diamonds.manage"}

    def put(self, request, pk):
        payload = ItemStonesSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        item = services.update_item(pk, stones=payload.validated_data["stones"],
                                    label_price=payload.validated_data["label_price"],
                                    actor=request.actor)
        return Response({"id": item.pk, "label_price": item.label_price,
                         "stone_weight_ct": item.stone_weight_ct})


class LookupView(APIView):
    """Diamond pieces or loose stones in stock, by barcode or certificate (setting screen)."""

    required_permissions = {"GET": "diamonds.view"}

    def get(self, request):
        services.require_enabled()
        family = request.query_params.get("family", "stone")
        term = request.query_params.get("q", "").strip()
        items = (Item.objects.select_related("category", "karat", "branch")
                 .filter(status=ItemStatus.IN_STOCK, category__product_family=family)
                 .prefetch_related("stones"))
        branch = request.query_params.get("branch", "")
        if branch.isdigit():
            items = items.filter(branch_id=int(branch))
        if term:
            items = items.filter(Q(barcode=term) | Q(stones__certificate_no=term)).distinct()
        found = list(items.order_by("barcode")[:20])
        if term and not found:
            raise NotFound(_("No piece with barcode %(barcode)s.") % {"barcode": term})
        return Response({"results": [{
            "id": item.pk, "barcode": item.barcode,
            "name": f"{item.barcode} · {item.category.name}",
            "category": item.category.name, "karat": item.karat.label if item.karat else "",
            "gross_weight_g": item.gross_weight_g, "stone_weight_ct": item.stone_weight_ct,
            "stones": services.stones_summary(item.stones.all()),
            "label_price": item.label_price} for item in found]})


class SettingSerializer(serializers.Serializer):
    piece = serializers.IntegerField()
    stones = serializers.ListField(child=serializers.IntegerField(), allow_empty=True)
    gross_after_g = serializers.CharField(max_length=20, allow_blank=True)
    setter = serializers.IntegerField(required=False, allow_null=True, default=None)
    labour_amount = serializers.CharField(max_length=20, required=False, allow_blank=True,
                                          default="0")
    note = serializers.CharField(required=False, allow_blank=True, default="")


class SettingView(APIView):
    required_permissions = {"POST": "diamonds.setting"}

    @idempotent
    def post(self, request):
        payload = SettingSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        setting = services.set_stones(services.SettingInput(
            piece_id=d["piece"], stone_ids=tuple(d["stones"]), gross_after_g=d["gross_after_g"],
            setter_id=d["setter"], labour_amount=d["labour_amount"] or "0", note=d["note"]),
            actor=request.actor)
        return Response({"id": setting.pk, "number": setting.number},
                        status=status.HTTP_201_CREATED)


class SettingVoidView(APIView):
    required_permissions = {"POST": "diamonds.setting"}

    @idempotent
    def post(self, request, pk):
        reason = str(request.data.get("reason") or "")
        setting = services.cancel_setting(pk, reason=reason, actor=request.actor)
        return Response({"id": setting.pk, "status": setting.status})


urlpatterns = [
    path("receive/", ReceiveView.as_view(), name="diamonds-receive"),
    path("items/<int:pk>/stones/", ItemStonesView.as_view(), name="diamonds-item-stones"),
    path("lookup/", LookupView.as_view(), name="diamonds-lookup"),
    path("settings/", SettingView.as_view(), name="diamonds-settings"),
    path("settings/<int:pk>/void/", SettingVoidView.as_view(), name="diamonds-setting-void"),
]
