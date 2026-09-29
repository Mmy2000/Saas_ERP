from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.routers import SimpleRouter

from apps.core.api.idempotency import idempotent

from . import holders
from .models import BankAccount, CardTerminal, CashBox, TreasuryDocument, TreasuryKind
from .selectors import visible_banks, visible_boxes, visible_terminals
from .services import (
    TreasuryInput,
    post_treasury_document,
    receive_transfer,
    void_treasury_document,
)

# --- holders ----------------------------------------------------------------------------------


class CashBoxSerializer(serializers.ModelSerializer):
    label = serializers.CharField(read_only=True)
    currency = serializers.CharField(source="currency.code", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)

    class Meta:
        model = CashBox
        fields = ["id", "label", "name", "branch", "branch_name", "currency", "is_default",
                  "account", "is_active"]


class CashBoxWriteSerializer(serializers.Serializer):
    branch = serializers.IntegerField(required=False)
    currency = serializers.CharField(max_length=3, required=False, allow_blank=True,
                                     default="")  # fixed once created
    name = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    is_default = serializers.BooleanField(required=False, default=False)


class BankAccountSerializer(serializers.ModelSerializer):
    label = serializers.CharField(read_only=True)
    currency = serializers.CharField(source="currency.code", read_only=True)
    branches = serializers.SerializerMethodField()

    class Meta:
        model = BankAccount
        fields = ["id", "label", "name", "bank_name", "account_number", "iban", "currency",
                  "all_branches", "branches", "account", "is_active"]

    def get_branches(self, bank):
        return None if bank.all_branches else [link.branch_id for link in bank.branch_links.all()]


class BankAccountWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, allow_blank=True)
    currency = serializers.CharField(max_length=3, required=False, allow_blank=True,
                                     default="")  # fixed once created
    bank_name = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    account_number = serializers.CharField(max_length=60, required=False, allow_blank=True,
                                           default="")
    iban = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")
    branches = serializers.ListField(child=serializers.IntegerField(), required=False,
                                     allow_null=True, default=None)


class TerminalSerializer(serializers.ModelSerializer):
    label = serializers.CharField(read_only=True)
    currency = serializers.CharField(source="bank_account.currency.code", read_only=True)

    class Meta:
        model = CardTerminal
        fields = ["id", "label", "name", "bank_account", "branch", "fee_rate", "currency",
                  "account", "is_active"]


class TerminalWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, allow_blank=True)
    bank_account = serializers.IntegerField(allow_null=True)
    branch = serializers.IntegerField(required=False, allow_null=True, default=None)
    fee_rate = serializers.CharField(max_length=20, required=False, allow_blank=True, default="0")


class _HolderViewSet(viewsets.GenericViewSet):
    kind = ""
    required_permissions = {
        "list": "treasury.view",
        "retrieve": "treasury.view",
        "create": holders.MANAGE,
        "update": holders.MANAGE,
        "deactivate": holders.MANAGE,
        "activate": holders.MANAGE,
    }

    def list(self, request):
        queryset = self.filter_queryset(self.get_queryset())
        if request.query_params.get("active") == "1":
            queryset = queryset.filter(is_active=True)
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    def _respond(self, holder, code=status.HTTP_200_OK):
        return Response(self.get_serializer(self.get_queryset().get(pk=holder.pk)).data,
                        status=code)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        return self._respond(holders.set_holder_active(self.kind, int(pk), False,
                                                       actor=request.actor))

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        return self._respond(holders.set_holder_active(self.kind, int(pk), True,
                                                       actor=request.actor))


class CashBoxViewSet(_HolderViewSet):
    kind = "box"
    queryset = CashBox.objects.select_related("branch", "currency")
    serializer_class = CashBoxSerializer
    filterset_fields = {"branch": ["exact"], "is_active": ["exact"]}

    def get_queryset(self):
        return visible_boxes(self.request.actor.branch_ids("treasury.view"))

    def create(self, request):
        payload = CashBoxWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        box = holders.create_cash_box(holders.CashBoxInput(
            branch_id=d.get("branch") or 0, currency_code=d["currency"], name=d["name"],
            is_default=d["is_default"]), actor=request.actor)
        return self._respond(box, status.HTTP_201_CREATED)

    def update(self, request, pk=None):
        payload = CashBoxWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        return self._respond(holders.update_cash_box(int(pk), name=d["name"],
                                                     is_default=d["is_default"],
                                                     actor=request.actor))


class BankAccountViewSet(_HolderViewSet):
    kind = "bank"
    queryset = BankAccount.objects.select_related("currency")
    serializer_class = BankAccountSerializer
    filterset_fields = {"is_active": ["exact"]}

    def get_queryset(self):
        return visible_banks(self.request.actor.branch_ids("treasury.view")).prefetch_related(
            "branch_links")

    def _input(self):
        payload = BankAccountWriteSerializer(data=self.request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        return holders.BankAccountInput(
            name=d["name"], currency_code=d["currency"], bank_name=d["bank_name"],
            account_number=d["account_number"], iban=d["iban"],
            branch_ids=None if d["branches"] is None else tuple(d["branches"]))

    def create(self, request):
        return self._respond(holders.create_bank_account(self._input(), actor=request.actor),
                             status.HTTP_201_CREATED)

    def update(self, request, pk=None):
        return self._respond(holders.update_bank_account(int(pk), self._input(),
                                                         actor=request.actor))


class TerminalViewSet(_HolderViewSet):
    kind = "terminal"
    queryset = CardTerminal.objects.select_related("bank_account__currency")
    serializer_class = TerminalSerializer
    filterset_fields = {"branch": ["exact"], "is_active": ["exact"]}

    def get_queryset(self):
        return visible_terminals(self.request.actor.branch_ids("treasury.view"))

    def _input(self):
        payload = TerminalWriteSerializer(data=self.request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        return holders.TerminalInput(name=d["name"], bank_account_id=d["bank_account"] or 0,
                                     branch_id=d["branch"], fee_rate=d["fee_rate"])

    def create(self, request):
        return self._respond(holders.create_terminal(self._input(), actor=request.actor),
                             status.HTTP_201_CREATED)

    def update(self, request, pk=None):
        return self._respond(holders.update_terminal(int(pk), self._input(), actor=request.actor))


# --- documents --------------------------------------------------------------------------------


class DocumentWriteSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=TreasuryKind.choices)
    source_box = serializers.IntegerField(required=False, allow_null=True, default=None)
    source_bank = serializers.IntegerField(required=False, allow_null=True, default=None)
    source_terminal = serializers.IntegerField(required=False, allow_null=True, default=None)
    dest_box = serializers.IntegerField(required=False, allow_null=True, default=None)
    dest_bank = serializers.IntegerField(required=False, allow_null=True, default=None)
    branch = serializers.IntegerField(required=False, allow_null=True, default=None)
    amount = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")
    dest_amount = serializers.CharField(max_length=30, required=False, allow_blank=True,
                                        default="")
    fee_amount = serializers.CharField(max_length=30, required=False, allow_blank=True,
                                       default="")
    reference = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")
    note = serializers.CharField(required=False, allow_blank=True, default="")


class DocumentSerializer(serializers.ModelSerializer):
    title = serializers.CharField(read_only=True)
    currency = serializers.CharField(source="currency.code")
    dest_currency = serializers.CharField(source="dest_currency.code")
    in_transit = serializers.BooleanField(read_only=True)

    class Meta:
        model = TreasuryDocument
        fields = ["id", "number", "status", "kind", "title", "branch", "to_branch",
                  "business_date", "source_box", "source_bank", "source_terminal", "dest_box",
                  "dest_bank", "currency", "amount", "dest_currency", "dest_amount",
                  "fee_amount", "rate", "functional_amount", "reference", "note",
                  "needs_receipt", "received_at", "in_transit"]


class VoidSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class TreasuryDocumentViewSet(viewsets.GenericViewSet):
    queryset = TreasuryDocument.objects.select_related("currency", "dest_currency")
    serializer_class = DocumentSerializer
    filterset_fields = {"kind": ["exact"], "status": ["exact"], "branch": ["exact"]}
    required_permissions = {
        "list": "treasury.view",
        "retrieve": "treasury.view",
        "create": "treasury.view",  # the service checks the permission for the kind
        "receive": "treasury.transfer.receive",
        "void": "treasury.void",
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        branches = self.request.actor.branch_ids("treasury.view")
        if branches is None:
            return queryset
        return queryset.filter(branch_id__in=branches) | queryset.filter(
            to_branch_id__in=branches)

    def _respond(self, doc, code=status.HTTP_200_OK):
        return Response(self.get_serializer(self.get_queryset().get(pk=doc.pk)).data,
                        status=code)

    def list(self, request):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    @idempotent
    def create(self, request):
        payload = DocumentWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        doc = post_treasury_document(TreasuryInput(
            kind=d["kind"], source_box_id=d["source_box"], source_bank_id=d["source_bank"],
            source_terminal_id=d["source_terminal"], dest_box_id=d["dest_box"],
            dest_bank_id=d["dest_bank"], branch_id=d["branch"], amount=d["amount"] or None,
            dest_amount=d["dest_amount"] or None, fee_amount=d["fee_amount"] or None,
            reference=d["reference"], note=d["note"]), actor=request.actor)
        return self._respond(doc, status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    @idempotent
    def receive(self, request, pk=None):
        return self._respond(receive_transfer(int(pk), actor=request.actor))

    @action(detail=True, methods=["post"])
    @idempotent
    def void(self, request, pk=None):
        payload = VoidSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return self._respond(void_treasury_document(
            int(pk), reason=payload.validated_data["reason"], actor=request.actor))


router = SimpleRouter()
router.register("cash-boxes", CashBoxViewSet, basename="cash-box")
router.register("bank-accounts", BankAccountViewSet, basename="bank-account")
router.register("terminals", TerminalViewSet, basename="terminal")
router.register("documents", TreasuryDocumentViewSet, basename="treasury-doc")

urlpatterns = router.urls
