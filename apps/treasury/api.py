from django.utils.translation import gettext as _
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.routers import SimpleRouter

from apps.core.api.idempotency import idempotent
from apps.core.errors import NotFound

from . import cheques, counts, holders, reconciliation
from .models import (
    BankAccount,
    BankReconciliation,
    CardTerminal,
    CashBox,
    CashCount,
    Cheque,
    ChequeDirection,
    TreasuryDocument,
    TreasuryKind,
)
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


# --- cheques -----------------------------------------------------------------------------------


class ChequeSerializer(serializers.ModelSerializer):
    party_name = serializers.CharField(source="party.name", read_only=True)
    currency = serializers.CharField(source="currency.code", read_only=True)
    bank_account_label = serializers.CharField(source="bank_account.label", default=None,
                                               read_only=True)
    endorsed_to_name = serializers.CharField(source="endorsed_to.name", default=None,
                                             read_only=True)

    class Meta:
        model = Cheque
        fields = ["id", "number", "status", "direction", "side", "party", "party_name",
                  "cheque_number", "drawn_on", "due_date", "currency", "amount", "bank_account",
                  "bank_account_label", "state", "state_on", "endorsed_to", "endorsed_to_name",
                  "branch", "business_date", "note", "void_reason"]


class ChequeWriteSerializer(serializers.Serializer):
    direction = serializers.ChoiceField(choices=ChequeDirection.choices)
    side = serializers.CharField(max_length=16)
    party = serializers.IntegerField(allow_null=True)
    branch = serializers.IntegerField()
    cheque_number = serializers.CharField(max_length=40, allow_blank=True)
    due_date = serializers.CharField(max_length=10, allow_blank=True)
    amount = serializers.CharField(max_length=30, allow_blank=True)
    drawn_on = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    bank_account = serializers.IntegerField(required=False, allow_null=True, default=None)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class BankChoiceSerializer(serializers.Serializer):
    bank_account = serializers.IntegerField(required=False, allow_null=True, default=None)


class EndorseSerializer(serializers.Serializer):
    side = serializers.CharField(max_length=16)
    party = serializers.IntegerField(allow_null=True)


class ChequeViewSet(viewsets.GenericViewSet):
    queryset = Cheque.objects.select_related("party", "currency", "bank_account", "endorsed_to")
    serializer_class = ChequeSerializer
    filterset_fields = {"direction": ["exact"], "state": ["exact"], "party": ["exact"],
                        "status": ["exact"]}
    required_permissions = {
        "list": "treasury.cheque.view", "retrieve": "treasury.cheque.view",
        "create": "treasury.cheque.manage", "deposit": "treasury.cheque.manage",
        "clear": "treasury.cheque.manage", "bounce": "treasury.cheque.manage",
        "hand_back": "treasury.cheque.manage", "endorse": "treasury.cheque.manage",
        "void": "treasury.cheque.manage",
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        branches = self.request.actor.branch_ids("treasury.cheque.view")
        return queryset if branches is None else queryset.filter(branch_id__in=branches)

    def _respond(self, cheque, code=status.HTTP_200_OK):
        return Response(self.get_serializer(self.get_queryset().get(pk=cheque.pk)).data,
                        status=code)

    def list(self, request):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    @idempotent
    def create(self, request):
        payload = ChequeWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        cheque = cheques.record_cheque(cheques.ChequeInput(
            direction=d["direction"], side=d["side"], party_id=d["party"], branch_id=d["branch"],
            cheque_number=d["cheque_number"], due_date=d["due_date"] or None,
            amount=d["amount"] or None, drawn_on=d["drawn_on"],
            bank_account_id=d["bank_account"], note=d["note"]), actor=request.actor)
        return self._respond(cheque, status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def deposit(self, request, pk=None):
        payload = BankChoiceSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return self._respond(cheques.deposit_cheque(
            int(pk), payload.validated_data["bank_account"], actor=request.actor))

    @action(detail=True, methods=["post"])
    @idempotent
    def clear(self, request, pk=None):
        payload = BankChoiceSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return self._respond(cheques.clear_cheque(
            int(pk), bank_account_id=payload.validated_data["bank_account"], actor=request.actor))

    @action(detail=True, methods=["post"])
    @idempotent
    def bounce(self, request, pk=None):
        payload = VoidSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return self._respond(cheques.bounce_cheque(
            int(pk), reason=payload.validated_data["reason"], actor=request.actor))

    @action(detail=True, methods=["post"], url_path="hand-back")
    @idempotent
    def hand_back(self, request, pk=None):
        payload = VoidSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return self._respond(cheques.return_cheque(
            int(pk), reason=payload.validated_data["reason"], actor=request.actor))

    @action(detail=True, methods=["post"])
    @idempotent
    def endorse(self, request, pk=None):
        payload = EndorseSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        return self._respond(cheques.endorse_cheque(int(pk), side=d["side"], party_id=d["party"],
                                                    actor=request.actor))

    @action(detail=True, methods=["post"])
    @idempotent
    def void(self, request, pk=None):
        payload = VoidSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return self._respond(cheques.cancel_cheque(
            int(pk), reason=payload.validated_data["reason"], actor=request.actor))


# --- bank reconciliation -------------------------------------------------------------------------


class StartSerializer(serializers.Serializer):
    bank_account = serializers.IntegerField(required=False)
    statement_date = serializers.CharField(max_length=10, allow_blank=True)
    statement_balance = serializers.CharField(max_length=30, allow_blank=True)
    note = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class TickSerializer(serializers.Serializer):
    lines = serializers.ListField(child=serializers.IntegerField(), allow_empty=True)


class BankItemSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=[("charge", "charge"), ("interest", "interest")])
    amount = serializers.CharField(max_length=30, allow_blank=True)
    memo = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


def reconciliation_json(item: BankReconciliation) -> dict:
    figures = reconciliation.summary(item)
    money = "{:.2f}".format
    return {"id": item.pk, "bank_account": item.bank_account_id,
            "statement_date": item.statement_date, "completed": item.is_completed,
            "statement_balance": money(item.statement_balance), "opening": money(figures.opening),
            "cleared": money(figures.cleared), "ticked": money(figures.ticked),
            "book": money(figures.book), "difference": money(figures.difference)}


class ReconciliationViewSet(viewsets.GenericViewSet):
    queryset = BankReconciliation.objects.select_related("bank_account")
    required_permissions = {
        "retrieve": "treasury.reconcile", "create": "treasury.reconcile",
        "partial_update": "treasury.reconcile", "destroy": "treasury.reconcile",
        "tick": "treasury.reconcile", "bank_item": "treasury.reconcile",
        "complete": "treasury.reconcile", "reopen": "treasury.reconcile",
    }

    def _get(self, pk):
        item = BankReconciliation.objects.select_related("bank_account").filter(pk=pk).first()
        if item is None:
            raise NotFound(_("Not found."))
        return item

    def retrieve(self, request, pk=None):
        return Response(reconciliation_json(self._get(pk)))

    def create(self, request):
        payload = StartSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        item = reconciliation.start(d.get("bank_account") or 0, d["statement_date"],
                                    d["statement_balance"] or None, note=d["note"],
                                    actor=request.actor)
        return Response(reconciliation_json(item), status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        payload = StartSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        item = reconciliation.start(self._get(pk).bank_account_id, d["statement_date"],
                                    d["statement_balance"] or None, note=d["note"],
                                    actor=request.actor)
        return Response(reconciliation_json(item))

    def destroy(self, request, pk=None):
        reconciliation.discard(int(pk), actor=request.actor)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def tick(self, request, pk=None):
        payload = TickSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        item = reconciliation.tick(int(pk), payload.validated_data["lines"], actor=request.actor)
        return Response(reconciliation_json(item))

    @action(detail=True, methods=["post"], url_path="bank-item")
    @idempotent
    def bank_item(self, request, pk=None):
        payload = BankItemSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        item = reconciliation.add_bank_item(int(pk), kind=d["kind"], amount=d["amount"] or None,
                                            memo=d["memo"], actor=request.actor)
        return Response(reconciliation_json(item))

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        return Response(reconciliation_json(reconciliation.complete(int(pk),
                                                                    actor=request.actor)))

    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        return Response(reconciliation_json(reconciliation.reopen(int(pk), actor=request.actor)))


# --- cash counts ---------------------------------------------------------------------------------


class CashCountSerializer(serializers.ModelSerializer):
    cash_box_label = serializers.CharField(source="cash_box.label", read_only=True)
    currency = serializers.CharField(source="cash_box.currency.code", read_only=True)

    class Meta:
        model = CashCount
        fields = ["id", "number", "status", "branch", "cash_box", "cash_box_label", "currency",
                  "business_date", "expected", "counted", "difference", "denominations", "note"]


class CashCountWriteSerializer(serializers.Serializer):
    cash_box = serializers.IntegerField()
    counted = serializers.CharField(max_length=30, required=False, allow_blank=True,
                                    allow_null=True, default=None)
    denominations = serializers.DictField(child=serializers.IntegerField(min_value=0),
                                          required=False, default=dict)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class CashCountViewSet(viewsets.GenericViewSet):
    queryset = CashCount.objects.select_related("cash_box__currency")
    serializer_class = CashCountSerializer
    filterset_fields = {"cash_box": ["exact"], "status": ["exact"]}
    required_permissions = {"list": "treasury.view", "retrieve": "treasury.view",
                            "create": "treasury.count.create", "void": "treasury.count.void"}

    def get_queryset(self):
        queryset = super().get_queryset()
        branches = self.request.actor.branch_ids("treasury.view")
        return queryset if branches is None else queryset.filter(branch_id__in=branches)

    def _respond(self, count, code=status.HTTP_200_OK):
        return Response(self.get_serializer(self.get_queryset().get(pk=count.pk)).data,
                        status=code)

    def list(self, request):
        page = self.paginate_queryset(self.filter_queryset(self.get_queryset()))
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    @idempotent
    def create(self, request):
        payload = CashCountWriteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        d = payload.validated_data
        count = counts.record_count(counts.CountInput(
            cash_box_id=d["cash_box"], counted=d["counted"] or None,
            denominations=d["denominations"], note=d["note"]), actor=request.actor)
        return self._respond(count, status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    @idempotent
    def void(self, request, pk=None):
        payload = VoidSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return self._respond(counts.void_count(int(pk), reason=payload.validated_data["reason"],
                                               actor=request.actor))


router = SimpleRouter()
router.register("cash-counts", CashCountViewSet, basename="cash-count")
router.register("cheques", ChequeViewSet, basename="cheque-api")
router.register("reconciliations", ReconciliationViewSet, basename="reconciliation")
router.register("cash-boxes", CashBoxViewSet, basename="cash-box")
router.register("bank-accounts", BankAccountViewSet, basename="bank-account")
router.register("terminals", TerminalViewSet, basename="terminal")
router.register("documents", TreasuryDocumentViewSet, basename="treasury-doc")

urlpatterns = router.urls
