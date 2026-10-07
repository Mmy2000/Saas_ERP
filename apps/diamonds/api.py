from django.db.models import Q
from django.urls import path
from django.utils.translation import gettext as _
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.api.idempotency import idempotent
from apps.core.errors import NotFound
from apps.inventory.models import Item, ItemStatus

from . import jobs, services
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
    discount_class = serializers.IntegerField(required=False, allow_null=True, default=None)


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
                stones=tuple(p["stones"]), discount_class_id=p["discount_class"])
                for p in d["pieces"])), actor=request.actor)
        return Response({"id": invoice.pk, "number": invoice.number},
                        status=status.HTTP_201_CREATED)


class ItemStonesSerializer(serializers.Serializer):
    stones = StoneSerializer(many=True)
    label_price = serializers.CharField(max_length=20, required=False, allow_blank=True,
                                        allow_null=True, default=None)
    discount_class = serializers.IntegerField(required=False, allow_null=True)


class ItemStonesView(APIView):
    required_permissions = {"PUT": "diamonds.manage"}

    def put(self, request, pk):
        payload = ItemStonesSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        item = services.update_item(pk, stones=d["stones"], label_price=d["label_price"],
                                    discount_class_id=d.get("discount_class",
                                                            services.UNCHANGED),
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
        # "job": anything a setter can be handed (gold or diamond mountings, loose stones).
        families = (("gold", "diamond", "stone") if family == "job" else (family,))
        items = (Item.objects.select_related("category", "karat", "branch")
                 .filter(status=ItemStatus.IN_STOCK, category__product_family__in=families,
                         category__tracking="serialized")
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
            "id": item.pk, "barcode": item.barcode, "family": item.category.product_family,
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


class JobSerializer(serializers.Serializer):
    branch = serializers.IntegerField()
    setter = serializers.IntegerField(required=False, allow_null=True, default=None)
    items = serializers.ListField(child=serializers.IntegerField(), allow_empty=True)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class JobView(APIView):
    """Hand pieces and loose stones to a setter."""

    required_permissions = {"POST": "diamonds.setting"}

    @idempotent
    def post(self, request):
        payload = JobSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        job = jobs.hand_over(jobs.JobInput(branch_id=d["branch"], setter_id=d["setter"] or 0,
                                           item_ids=tuple(d["items"]), note=d["note"]),
                             actor=request.actor)
        return Response({"id": job.pk, "number": job.number}, status=status.HTTP_201_CREATED)


class JobReturnView(APIView):
    required_permissions = {"POST": "diamonds.setting"}

    @idempotent
    def post(self, request, pk):
        lines = [int(x) for x in request.data.get("lines") or [] if str(x).isdigit()]
        job = jobs.return_items(pk, lines, actor=request.actor)
        return Response({"id": job.pk, "closed": job.closed_at is not None})


class JobSetSerializer(serializers.Serializer):
    mounting = serializers.IntegerField()
    stones = serializers.ListField(child=serializers.IntegerField(), allow_empty=True)
    gross_after_g = serializers.CharField(max_length=20, allow_blank=True)
    labour_amount = serializers.CharField(max_length=20, required=False, allow_blank=True,
                                          default="0")
    category = serializers.IntegerField(required=False, allow_null=True, default=None)
    label_price = serializers.CharField(max_length=20, required=False, allow_blank=True,
                                        default="")
    note = serializers.CharField(required=False, allow_blank=True, default="")


class JobSetView(APIView):
    """A mounting and stones of a job come back set as one piece."""

    required_permissions = {"POST": "diamonds.setting"}

    @idempotent
    def post(self, request, pk):
        payload = JobSetSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        setting = jobs.set_from_job(pk, jobs.SetInput(
            mounting_line_id=d["mounting"], stone_line_ids=tuple(d["stones"]),
            gross_after_g=d["gross_after_g"], labour_amount=d["labour_amount"] or "0",
            category_id=d["category"], label_price=d["label_price"] or None, note=d["note"]),
            actor=request.actor)
        return Response({"id": setting.pk, "number": setting.number},
                        status=status.HTTP_201_CREATED)


class RemovalSerializer(serializers.Serializer):
    stones = serializers.ListField(child=serializers.IntegerField(), allow_empty=True)
    gross_after_g = serializers.CharField(max_length=20, allow_blank=True)
    fee_amount = serializers.CharField(max_length=20, required=False, allow_blank=True,
                                       default="0")
    setter = serializers.IntegerField(required=False, allow_null=True, default=None)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class RemovalView(APIView):
    """Take stones set here out of the piece again."""

    required_permissions = {"POST": "diamonds.setting"}

    @idempotent
    def post(self, request, pk):
        payload = RemovalSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        removal = jobs.remove_stones(jobs.RemovalInput(
            setting_id=pk, stone_ids=tuple(d["stones"]), gross_after_g=d["gross_after_g"],
            fee_amount=d["fee_amount"] or "0", setter_id=d["setter"], note=d["note"]),
            actor=request.actor)
        return Response({"id": removal.pk, "number": removal.number},
                        status=status.HTTP_201_CREATED)


class DiscountClassSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=4)
    name = serializers.CharField(max_length=100, allow_blank=True)
    usual_percent = serializers.CharField(max_length=10, required=False, allow_blank=True,
                                          default="0")
    max_percent = serializers.CharField(max_length=10, required=False, allow_blank=True,
                                        default="0")
    is_active = serializers.BooleanField(required=False, default=True)


def _class_input(request):
    payload = DiscountClassSerializer(data=request.data)
    payload.is_valid(raise_exception=True)
    d = payload.validated_data
    return services.DiscountClassInput(code=d["code"], name=d["name"],
                                       usual_percent=d["usual_percent"],
                                       max_percent=d["max_percent"], is_active=d["is_active"])


class DiscountClassesView(APIView):
    required_permissions = {"POST": "diamonds.manage"}

    def post(self, request):
        found = services.save_discount_class(_class_input(request), actor=request.actor)
        return Response({"id": found.pk}, status=status.HTTP_201_CREATED)


class DiscountClassView(APIView):
    required_permissions = {"PUT": "diamonds.manage"}

    def put(self, request, pk):
        found = services.save_discount_class(_class_input(request), class_id=pk,
                                             actor=request.actor)
        return Response({"id": found.pk})


class AssignClassSerializer(serializers.Serializer):
    discount_class = serializers.IntegerField(allow_null=True)
    family = serializers.CharField(max_length=16, required=False, allow_blank=True, default="")
    q = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")
    current = serializers.CharField(max_length=10, required=False, allow_blank=True, default="")


class AssignClassView(APIView):
    """Put every matching diamond piece in stock into a class (legacy GHD42)."""

    required_permissions = {"POST": "diamonds.manage"}

    @idempotent
    def post(self, request):
        payload = AssignClassSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        count = services.assign_discount_class(
            d["discount_class"], family=d["family"], term=d["q"].strip(), current=d["current"],
            scope=request.actor.branch_ids("diamonds.manage"), actor=request.actor)
        return Response({"count": count})


urlpatterns = [
    path("receive/", ReceiveView.as_view(), name="diamonds-receive"),
    path("items/<int:pk>/stones/", ItemStonesView.as_view(), name="diamonds-item-stones"),
    path("lookup/", LookupView.as_view(), name="diamonds-lookup"),
    path("settings/", SettingView.as_view(), name="diamonds-settings"),
    path("settings/<int:pk>/void/", SettingVoidView.as_view(), name="diamonds-setting-void"),
    path("settings/<int:pk>/remove/", RemovalView.as_view(), name="diamonds-setting-remove"),
    path("jobs/", JobView.as_view(), name="diamonds-jobs"),
    path("discount-classes/", DiscountClassesView.as_view(), name="diamonds-discount-classes"),
    path("discount-classes/assign/", AssignClassView.as_view(), name="diamonds-class-assign"),
    path("discount-classes/<int:pk>/", DiscountClassView.as_view(),
         name="diamonds-discount-class"),
    path("jobs/<int:pk>/return/", JobReturnView.as_view(), name="diamonds-job-return"),
    path("jobs/<int:pk>/set/", JobSetView.as_view(), name="diamonds-job-set"),
]
