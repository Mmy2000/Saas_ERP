from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.routers import SimpleRouter

from apps.core.api.idempotency import idempotent

from . import services
from .models import ExpenseCategory, ExpenseVoucher


class CategorySerializer(serializers.ModelSerializer):
    account_label = serializers.CharField(source="account.__str__", read_only=True)

    class Meta:
        model = ExpenseCategory
        fields = ["id", "name", "account", "account_label", "is_active"]


class CategoryWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, allow_blank=True)
    account = serializers.IntegerField(required=False, allow_null=True, default=None)


class CategoryViewSet(viewsets.GenericViewSet):
    queryset = ExpenseCategory.objects.select_related("account")
    serializer_class = CategorySerializer
    filterset_fields = {"is_active": ["exact"]}
    required_permissions = {
        "list": "expenses.view",
        "retrieve": "expenses.view",
        "create": services.MANAGE,
        "update": services.MANAGE,
        "deactivate": services.MANAGE,
        "activate": services.MANAGE,
    }

    def list(self, request):
        queryset = self.filter_queryset(self.get_queryset())
        if request.query_params.get("q"):
            queryset = queryset.filter(name__icontains=request.query_params["q"])
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    def _payload(self):
        payload = CategoryWriteSerializer(data=self.request.data)
        payload.is_valid(raise_exception=True)
        return payload.validated_data

    def create(self, request):
        d = self._payload()
        category = services.create_category(d["name"], d["account"], actor=request.actor)
        return Response(self.get_serializer(category).data, status=status.HTTP_201_CREATED)

    def update(self, request, pk=None):
        d = self._payload()
        category = services.update_category(int(pk), d["name"], d["account"],
                                            actor=request.actor)
        return Response(self.get_serializer(category).data)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        category = services.set_category_active(int(pk), False, actor=request.actor)
        return Response(self.get_serializer(category).data)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        category = services.set_category_active(int(pk), True, actor=request.actor)
        return Response(self.get_serializer(category).data)


class VoucherWriteSerializer(serializers.Serializer):
    branch = serializers.IntegerField()
    category = serializers.IntegerField(allow_null=True)
    method = serializers.ChoiceField(choices=[("cash", "cash"), ("bank_transfer", "bank")])
    currency = serializers.CharField(max_length=3)
    amount = serializers.CharField(max_length=30, allow_blank=True)
    cash_box = serializers.IntegerField(required=False, allow_null=True, default=None)
    bank_account = serializers.IntegerField(required=False, allow_null=True, default=None)
    payee = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    reference = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")
    note = serializers.CharField(required=False, allow_blank=True, default="")


class VoucherSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name")
    currency = serializers.CharField(source="currency.code")

    class Meta:
        model = ExpenseVoucher
        fields = ["id", "number", "status", "branch", "business_date", "category",
                  "category_name", "cash_box", "bank_account", "currency", "amount",
                  "functional_amount", "payee", "reference", "note"]


class VoidSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class VoucherViewSet(viewsets.GenericViewSet):
    queryset = ExpenseVoucher.objects.select_related("category", "currency")
    serializer_class = VoucherSerializer
    filterset_fields = {"category": ["exact"], "status": ["exact"],
                        "business_date": ["gte", "lte"]}
    required_permissions = {
        "list": "expenses.view",
        "retrieve": "expenses.view",
        "create": "expenses.voucher.create",
        "void": "expenses.void",
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        branches = self.request.actor.branch_ids("expenses.view")
        return queryset if branches is None else queryset.filter(branch_id__in=branches)

    def _respond(self, voucher, code=status.HTTP_200_OK):
        return Response(self.get_serializer(self.get_queryset().get(pk=voucher.pk)).data,
                        status=code)

    def list(self, request):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    @idempotent
    def create(self, request):
        payload = VoucherWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        voucher = services.post_expense(services.ExpenseInput(
            branch_id=d["branch"], category_id=d["category"] or 0, method=d["method"],
            currency_code=d["currency"], amount=d["amount"], cash_box_id=d["cash_box"],
            bank_account_id=d["bank_account"], payee=d["payee"], reference=d["reference"],
            note=d["note"]), actor=request.actor)
        return self._respond(voucher, status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    @idempotent
    def void(self, request, pk=None):
        payload = VoidSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return self._respond(services.void_expense(
            int(pk), reason=payload.validated_data["reason"], actor=request.actor))


router = SimpleRouter()
router.register("categories", CategoryViewSet, basename="expense-category")
router.register("vouchers", VoucherViewSet, basename="expense-voucher")

urlpatterns = router.urls
