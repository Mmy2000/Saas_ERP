from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.errors import NotFound, ValidationError
from apps.pricing.models import FxRate, MetalPriceBoard
from apps.pricing.selectors import current_price_board
from apps.pricing.services import (
    KaratPriceInput,
    PublishPriceBoardCommand,
    publish_price_board,
    record_fx_rate,
)

from .serializers import (
    FxRateSerializer,
    PriceBoardSerializer,
    PublishBoardSerializer,
    RecordFxRateSerializer,
)


def _karat_input(data) -> KaratPriceInput:
    return KaratPriceInput(
        karat_id=data["karat"].pk,
        sell_price_per_g=data["sell_price_per_g"],
        buy_price_per_g=data.get("buy_price_per_g"),
        scrap_buy_price_per_g=data.get("scrap_buy_price_per_g"),
    )


class PriceBoardViewSet(viewsets.GenericViewSet):
    queryset = MetalPriceBoard.objects.prefetch_related("lines__karat__metal").order_by(
        "-effective_at", "-id")
    serializer_class = PriceBoardSerializer
    required_permissions = {
        "list": "pricing.board.view",
        "retrieve": "pricing.board.view",
        "current": "pricing.board.view",
        "create": "pricing.board.publish",
    }

    def list(self, request):
        page = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=False)
    def current(self, request):
        board = current_price_board()
        if board is None:
            raise NotFound(_("No gold price has been published yet."), code="PRICING_NO_BOARD")
        return Response(self.get_serializer(board).data)

    def create(self, request):
        payload = PublishBoardSerializer(data=request.data, context={"request": request})
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        board = publish_price_board(
            PublishPriceBoardCommand(
                reference=_karat_input(data["reference"]),
                overrides=tuple(_karat_input(o) for o in data["overrides"]),
                effective_at=data.get("effective_at"),
                note=data["note"],
            ),
            actor=request.actor,
        )
        board = self.get_queryset().get(pk=board.pk)
        return Response(self.get_serializer(board).data, status=status.HTTP_201_CREATED)


class FxRateViewSet(viewsets.GenericViewSet):
    queryset = FxRate.objects.select_related("currency").order_by("-effective_at", "-id")
    serializer_class = FxRateSerializer
    filterset_fields = {"currency__code": ["exact"]}
    required_permissions = {
        "list": "pricing.fx.view",
        "current": "pricing.fx.view",
        "create": "pricing.fx.publish",
    }

    def list(self, request):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    @action(detail=False)
    def current(self, request):
        code = request.query_params.get("currency")
        if not code:
            raise ValidationError(_("The currency parameter is required."),
                                  fields={"currency": [_("required")]})
        rate = (self.get_queryset().filter(currency__code=code, effective_at__lte=timezone.now())
                .first())
        if rate is None:
            raise NotFound(_("No exchange rate for %(code)s.") % {"code": code},
                           code="PRICING_NO_FX_RATE")
        return Response(self.get_serializer(rate).data)

    def create(self, request):
        payload = RecordFxRateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        rate = record_fx_rate(data["currency"].code, data["rate"],
                              effective_at=data.get("effective_at"), actor=request.actor)
        return Response(self.get_serializer(rate).data, status=status.HTTP_201_CREATED)
