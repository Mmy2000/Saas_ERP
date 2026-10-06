from django.utils.translation import gettext as _
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.routers import SimpleRouter

from apps.core.api.idempotency import idempotent
from apps.core.errors import ValidationError

from . import services
from .models import AdjustmentKind, CommissionBasis, Employee, EmployeeAdvance, PayrollRun


def parse_period(value: str | None) -> tuple[int, int]:
    """'2026-10' → (2026, 10)."""
    try:
        year, month = (int(part) for part in (value or "").split("-", 1))
        services.period_bounds(year, month)
    except (TypeError, ValueError):
        message = _("Choose a month.")
        raise ValidationError(message, fields={"period": [message]}) from None
    return year, month


class EmployeeSerializer(serializers.ModelSerializer):
    outstanding_advance = serializers.SerializerMethodField()
    branch_name = serializers.CharField(source="branch.name", default=None, read_only=True)

    class Meta:
        model = Employee
        fields = ["id", "code", "name", "job_title", "phone", "address", "branch", "branch_name",
                  "user", "hired_on", "monthly_salary", "commission_basis", "commission_rate",
                  "is_active", "notes", "outstanding_advance"]

    def get_outstanding_advance(self, employee):
        return str(services.outstanding_advance(employee))


class EmployeeWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    job_title = serializers.CharField(max_length=100, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    national_id = serializers.CharField(max_length=40, required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)
    branch = serializers.IntegerField(required=False, allow_null=True)
    user = serializers.IntegerField(required=False, allow_null=True)
    hired_on = serializers.DateField(required=False, allow_null=True)
    monthly_salary = serializers.CharField(max_length=30, required=False, allow_blank=True)
    commission_basis = serializers.ChoiceField(choices=CommissionBasis.choices, required=False)
    commission_rate = serializers.CharField(max_length=30, required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True)


class AdvanceWriteSerializer(serializers.Serializer):
    amount = serializers.CharField(max_length=30)
    branch = serializers.IntegerField()
    method = serializers.ChoiceField(choices=services.METHODS)
    cash_box = serializers.IntegerField(required=False, allow_null=True, default=None)
    bank_account = serializers.IntegerField(required=False, allow_null=True, default=None)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class AdjustmentWriteSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=AdjustmentKind.choices)
    amount = serializers.CharField(max_length=30)
    period = serializers.CharField(max_length=7)
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class AdvanceSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.name")

    class Meta:
        model = EmployeeAdvance
        fields = ["id", "number", "status", "branch", "employee", "employee_name",
                  "business_date", "amount", "method", "note"]


class PayrollSerializer(serializers.ModelSerializer):
    period = serializers.CharField(read_only=True)

    class Meta:
        model = PayrollRun
        fields = ["id", "number", "status", "period", "year", "month", "branch", "method",
                  "total_salary", "total_commission", "total_bonus", "total_deduction",
                  "total_advance", "total_net", "note"]


class PayrollWriteSerializer(serializers.Serializer):
    period = serializers.CharField(max_length=7)
    branch = serializers.IntegerField()
    method = serializers.ChoiceField(choices=services.METHODS)
    cash_box = serializers.IntegerField(required=False, allow_null=True, default=None)
    bank_account = serializers.IntegerField(required=False, allow_null=True, default=None)
    advances = serializers.DictField(child=serializers.CharField(allow_blank=True),
                                     required=False, default=dict)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class VoidSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


def _employee_input(data: dict, instance: Employee | None = None) -> services.EmployeeInput:
    def pick(name, current, default=""):
        if name in data:
            return data[name]
        return current if instance is not None else default

    return services.EmployeeInput(
        name=pick("name", getattr(instance, "name", "")),
        job_title=pick("job_title", getattr(instance, "job_title", "")),
        phone=pick("phone", getattr(instance, "phone", "")),
        national_id=data.get("national_id"),
        address=pick("address", getattr(instance, "address", "")),
        branch_id=pick("branch", getattr(instance, "branch_id", None), None),
        user_id=pick("user", getattr(instance, "user_id", None), None),
        hired_on=pick("hired_on", getattr(instance, "hired_on", None), None),
        monthly_salary=pick("monthly_salary", getattr(instance, "monthly_salary", "0"), "0")
        or "0",
        commission_basis=pick("commission_basis",
                              getattr(instance, "commission_basis", CommissionBasis.NONE),
                              CommissionBasis.NONE),
        commission_rate=pick("commission_rate", getattr(instance, "commission_rate", "0"), "0")
        or "0",
        is_active=pick("is_active", getattr(instance, "is_active", True), True),
        notes=pick("notes", getattr(instance, "notes", "")),
    )


class EmployeeViewSet(viewsets.GenericViewSet):
    queryset = Employee.objects.select_related("branch").order_by("code")
    serializer_class = EmployeeSerializer
    filterset_fields = {"is_active": ["exact"], "branch": ["exact"]}
    required_permissions = {
        "list": "hr.employee.view", "retrieve": "hr.employee.view",
        "create": "hr.employee.manage", "partial_update": "hr.employee.manage",
        "advance": "hr.advance.create", "adjustments": "hr.advance.create",
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        term = self.request.query_params.get("q", "").strip()
        return queryset.filter(name__icontains=term) if term else queryset

    def list(self, request):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    def create(self, request):
        payload = EmployeeWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        employee = services.create_employee(_employee_input(payload.validated_data),
                                            actor=request.actor)
        return Response(self.get_serializer(employee).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        instance = self.get_object()
        payload = EmployeeWriteSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        employee = services.update_employee(
            instance.pk, _employee_input(payload.validated_data, instance), actor=request.actor)
        return Response(self.get_serializer(employee).data)

    @action(detail=True, methods=["post"])
    @idempotent
    def advance(self, request, pk=None):
        payload = AdvanceWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        advance = services.give_advance(
            int(pk), d["amount"], branch_id=d["branch"], method=d["method"],
            cash_box_id=d["cash_box"], bank_account_id=d["bank_account"], note=d["note"],
            actor=request.actor)
        return Response(AdvanceSerializer(advance).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def adjustments(self, request, pk=None):
        payload = AdjustmentWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        year, month = parse_period(d["period"])
        adjustment = services.add_adjustment(int(pk), d["kind"], d["amount"], year, month,
                                             note=d["note"], actor=request.actor)
        return Response({"id": adjustment.pk}, status=status.HTTP_201_CREATED)


class AdvanceViewSet(viewsets.GenericViewSet):
    queryset = EmployeeAdvance.objects.select_related("employee")
    serializer_class = AdvanceSerializer
    filterset_fields = {"employee": ["exact"], "status": ["exact"]}
    required_permissions = {"list": "hr.employee.view", "retrieve": "hr.employee.view",
                            "void": "hr.advance.void"}

    def list(self, request):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post"])
    @idempotent
    def void(self, request, pk=None):
        payload = VoidSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        advance = services.void_advance(int(pk), reason=payload.validated_data["reason"],
                                        actor=request.actor)
        return Response(self.get_serializer(advance).data)


class AdjustmentViewSet(viewsets.GenericViewSet):
    required_permissions = {"destroy": "hr.advance.create"}

    def destroy(self, request, pk=None):
        services.delete_adjustment(int(pk), actor=request.actor)
        return Response(status=status.HTTP_204_NO_CONTENT)


def plan_json(plan: services.PayrollPlan) -> dict:
    return {
        "period": f"{plan.year}-{plan.month:02d}",
        "paid": plan.paid.number if plan.paid else None,
        "lines": [{
            "employee": line.employee.pk, "name": line.employee.name,
            "salary": str(line.salary), "commission": str(line.commission.amount),
            "bonus": str(line.bonus), "deduction": str(line.deduction),
            "gross": str(line.gross), "outstanding": str(line.outstanding),
            "advance": str(line.advance), "net": str(line.net),
        } for line in plan.lines],
        "totals": {name: str(plan.total(name)) for name in
                   ("salary", "bonus", "deduction", "gross", "advance", "net")}
        | {"commission": str(plan.total_commission)},
    }


class PayrollViewSet(viewsets.GenericViewSet):
    queryset = PayrollRun.objects.all()
    serializer_class = PayrollSerializer
    filterset_fields = {"status": ["exact"], "year": ["exact"]}
    required_permissions = {"list": "hr.payroll.view", "retrieve": "hr.payroll.view",
                            "preview": "hr.payroll.view", "create": "hr.payroll.post",
                            "void": "hr.payroll.void"}

    def list(self, request):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=False, methods=["get", "post"])
    def preview(self, request):
        """The payslips the month would pay. POST to try other advance recoveries."""
        source = request.data if request.method == "POST" else request.query_params
        year, month = parse_period(source.get("period"))
        advances = source.get("advances") if request.method == "POST" else None
        advances = {int(k): v for k, v in (advances or {}).items() if str(k).isdigit()}
        return Response(plan_json(services.plan_payroll(year, month, advances or None)))

    @idempotent
    def create(self, request):
        payload = PayrollWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        year, month = parse_period(d["period"])
        run = services.post_payroll(
            year, month, branch_id=d["branch"], method=d["method"], cash_box_id=d["cash_box"],
            bank_account_id=d["bank_account"],
            advances={int(k): v for k, v in d["advances"].items()} or None, note=d["note"],
            actor=request.actor)
        return Response(self.get_serializer(run).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    @idempotent
    def void(self, request, pk=None):
        payload = VoidSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        run = services.void_payroll(int(pk), reason=payload.validated_data["reason"],
                                    actor=request.actor)
        return Response(self.get_serializer(run).data)


router = SimpleRouter()
router.register("employees", EmployeeViewSet, basename="hr-employee")
router.register("advances", AdvanceViewSet, basename="hr-advance")
router.register("adjustments", AdjustmentViewSet, basename="hr-adjustment")
router.register("payroll", PayrollViewSet, basename="hr-payroll")
urlpatterns = router.urls
