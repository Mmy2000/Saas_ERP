from django.http import Http404
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.api.idempotency import idempotent
from apps.purchasing import scrap
from apps.purchasing.models import ScrapPayment, ScrapPurchase, ScrapSale


class LineSerializer(serializers.Serializer):
    karat = serializers.IntegerField()
    gross_weight_g = serializers.CharField(max_length=30)
    loss_weight_g = serializers.CharField(max_length=30, required=False, allow_blank=True,
                                          default="0")
    price = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")


class ScrapWriteSerializer(serializers.Serializer):
    branch = serializers.IntegerField()
    lines = LineSerializer(many=True)
    payment = serializers.ChoiceField(choices=ScrapPayment.choices, required=False,
                                      default=ScrapPayment.CASH)
    party = serializers.IntegerField(required=False, allow_null=True, default=None)
    seller_name = serializers.CharField(max_length=200, required=False, allow_blank=True,
                                        default="")
    seller_phone = serializers.CharField(max_length=30, required=False, allow_blank=True,
                                         default="")
    seller_id_number = serializers.CharField(max_length=40, required=False, allow_blank=True,
                                             default="")
    cash_box = serializers.IntegerField(required=False, allow_null=True, default=None)
    bank_account = serializers.IntegerField(required=False, allow_null=True, default=None)
    note = serializers.CharField(required=False, allow_blank=True, default="")


def _input(request) -> scrap.ScrapInput:
    payload = ScrapWriteSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    d = payload.validated_data
    return scrap.ScrapInput(
        branch_id=d["branch"], payment=d["payment"], party_id=d["party"],
        seller_name=d["seller_name"], seller_phone=d["seller_phone"],
        seller_id_number=d["seller_id_number"], cash_box_id=d["cash_box"],
        bank_account_id=d["bank_account"], note=d["note"],
        lines=tuple(scrap.ScrapLineInput(karat_id=ln["karat"],
                                         gross_weight_g=ln["gross_weight_g"],
                                         loss_weight_g=ln["loss_weight_g"] or "0",
                                         price=ln["price"] or None) for ln in d["lines"]))


class QuoteView(APIView):
    """Live pricing for the buy-scrap screen. Never writes."""

    required_permissions = {"POST": "purchasing.scrap.buy"}

    def post(self, request):
        _board, quotes = scrap.quote_scrap_purchase(_input(request), actor=request.actor)
        return Response({
            "lines": [{"karat": q.karat_label, "gross_weight_g": q.gross_weight_g,
                       "loss_weight_g": q.loss_weight_g, "net_weight_g": q.net_weight_g,
                       "fine_weight_g": q.fine_weight_g, "price_per_g": q.price_per_g,
                       "amount": q.amount} for _k, q in quotes],
            "total": sum((q.amount for _k, q in quotes), 0),
        })


class VoidSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class _ScrapViewSet(viewsets.GenericViewSet):
    model = None
    run = None
    required_permissions = {
        "list": "purchasing.scrap.view",
        "retrieve": "purchasing.scrap.view",
        "void": "purchasing.scrap.void",
    }

    def get_queryset(self):
        queryset = self.model.objects.all()
        branches = self.request.actor.branch_ids("purchasing.scrap.view")
        return queryset if branches is None else queryset.filter(branch_id__in=branches)

    def _data(self, doc):
        return {"id": doc.pk, "number": doc.number, "status": doc.status,
                "branch": doc.branch_id, "business_date": doc.business_date,
                "payment": doc.payment, "total_gross_weight_g": doc.total_gross_weight_g,
                "total_fine_weight_g": doc.total_fine_weight_g,
                "total_amount": doc.total_amount}

    def list(self, request):
        page = self.paginate_queryset(self.get_queryset().order_by("-business_date", "-id"))
        return self.get_paginated_response([self._data(doc) for doc in page])

    def retrieve(self, request, pk=None):
        doc = self.get_queryset().filter(pk=pk).first()
        if doc is None:
            raise Http404
        return Response(self._data(doc))

    @idempotent
    def create(self, request):
        doc = type(self).run(_input(request), actor=request.actor)
        return Response(self._data(doc), status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    @idempotent
    def void(self, request, pk=None):
        payload = VoidSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        doc = scrap.void_scrap(self.model, int(pk), reason=payload.validated_data["reason"],
                               actor=request.actor)
        return Response(self._data(doc))


class ScrapPurchaseViewSet(_ScrapViewSet):
    model = ScrapPurchase
    run = staticmethod(scrap.buy_scrap)
    required_permissions = {**_ScrapViewSet.required_permissions,
                            "create": "purchasing.scrap.buy"}


class ScrapSaleViewSet(_ScrapViewSet):
    model = ScrapSale
    run = staticmethod(scrap.sell_scrap)
    required_permissions = {**_ScrapViewSet.required_permissions,
                            "create": "purchasing.scrap.sell"}
