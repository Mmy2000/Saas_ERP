from rest_framework import viewsets

from apps.catalog.models import Currency, ItemCategory, Karat

from .serializers import CurrencySerializer, ItemCategorySerializer, KaratSerializer


class CurrencyViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Currency.objects.all()
    serializer_class = CurrencySerializer
    required_permissions = {"list": "catalog.view", "retrieve": "catalog.view"}
    filterset_fields = ["is_active"]


class KaratViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Karat.objects.select_related("metal")
    serializer_class = KaratSerializer
    required_permissions = {"list": "catalog.view", "retrieve": "catalog.view"}
    filterset_fields = {"metal__code": ["exact"], "is_active": ["exact"]}


class ItemCategoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ItemCategory.objects.prefetch_related("making_charges__currency")
    serializer_class = ItemCategorySerializer
    required_permissions = {"list": "catalog.view", "retrieve": "catalog.view"}
    filterset_fields = ["parent", "depth", "product_family", "tracking", "is_active"]
