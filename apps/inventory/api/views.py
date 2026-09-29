from rest_framework import serializers, viewsets

from apps.inventory.models import Item, LotBalance


class ItemSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name")
    karat_label = serializers.CharField(source="karat.label", default=None)
    branch_name = serializers.CharField(source="branch.name")
    status_label = serializers.CharField(source="get_status_display")

    class Meta:
        model = Item
        fields = ["id", "barcode", "rfid_epc", "external_code", "item_type", "category",
                  "category_name", "karat", "karat_label", "gross_weight_g", "metal_weight_g",
                  "fine_weight_g", "stone_weight_ct", "status", "status_label", "branch",
                  "branch_name", "list_making_rate", "label_price", "cost_making_rate",
                  "cost_amount"]

    def to_representation(self, item):
        data = super().to_representation(item)
        actor = getattr(self.context.get("request"), "actor", None)
        if actor is None or not actor.can("inventory.item.view_cost"):
            # Omitted, not hidden (§14.2): the cost never leaves the server.
            data.pop("cost_making_rate")
            data.pop("cost_amount")
        return data


class ItemViewSet(viewsets.ReadOnlyModelViewSet):
    """Pieces. `?barcode=` finds one exactly (scanner input); other filters narrow the list."""

    queryset = Item.objects.select_related("category", "karat__metal", "branch").order_by("-id")
    serializer_class = ItemSerializer
    filterset_fields = {"barcode": ["exact"], "status": ["exact"], "branch": ["exact"],
                        "category": ["exact"], "karat": ["exact"], "supplier": ["exact"]}
    required_permissions = {"list": "inventory.stock.view", "retrieve": "inventory.stock.view"}

    def get_queryset(self):
        queryset = super().get_queryset()
        branches = self.request.actor.branch_ids("inventory.stock.view")
        return queryset if branches is None else queryset.filter(branch_id__in=branches)


class LotSerializer(serializers.ModelSerializer):
    category = serializers.IntegerField(source="lot.category_id")
    category_name = serializers.CharField(source="lot.category.name")
    karat = serializers.IntegerField(source="lot.karat_id", allow_null=True)
    branch = serializers.IntegerField(source="lot.branch_id")

    class Meta:
        model = LotBalance
        fields = ["id", "category", "category_name", "karat", "branch", "qty", "gross_weight_g",
                  "fine_weight_g"]


class LotViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = LotBalance.objects.select_related("lot__category", "lot__karat").order_by("id")
    serializer_class = LotSerializer
    required_permissions = {"list": "inventory.stock.view", "retrieve": "inventory.stock.view"}

    def get_queryset(self):
        queryset = super().get_queryset().filter(gross_weight_g__gt=0)
        branches = self.request.actor.branch_ids("inventory.stock.view")
        return queryset if branches is None else queryset.filter(lot__branch_id__in=branches)
