from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ledger import services
from apps.ledger.models import Account, Commodity, EntryKind, JournalEntry
from apps.ledger.selectors import trial_balance
from apps.parties.models import Party
from apps.parties.selectors import search

from .serializers import (
    AccountSerializer,
    CommoditySerializer,
    EntrySerializer,
    ManualEntrySerializer,
    ReverseSerializer,
)


class AccountViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Account.objects.all().order_by("code")
    serializer_class = AccountSerializer
    filterset_fields = ["is_postable", "is_active", "type", "role"]
    required_permissions = {"list": "ledger.view", "retrieve": "ledger.view"}


class CommodityViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Commodity.objects.select_related("metal", "currency")
    serializer_class = CommoditySerializer
    required_permissions = {"list": "ledger.view", "retrieve": "ledger.view"}


class PartyLookupSerializer(serializers.ModelSerializer):
    class Meta:
        model = Party
        fields = ["id", "name", "phone"]


class PartyLookupView(APIView):
    """Any party (customer, supplier, partner…) by name/phone, for journal lines."""

    required_permissions = {"GET": "ledger.journal.post"}

    def get(self, request):
        parties = search(Party.objects.filter(is_active=True), request.query_params.get("q", ""))
        return Response({"results": PartyLookupSerializer(parties.order_by("name")[:20],
                                                          many=True).data})


class EntryViewSet(viewsets.GenericViewSet):
    queryset = (JournalEntry.objects.select_related("reversed_by")
                .prefetch_related("lines__account", "lines__commodity", "lines__party"))
    serializer_class = EntrySerializer
    filterset_fields = {"business_date": ["gte", "lte"], "kind": ["exact"],
                        "branch": ["exact"], "lines__account": ["exact"]}
    required_permissions = {
        "list": "ledger.view",
        "retrieve": "ledger.view",
        "create": "ledger.journal.post",
        "reverse": "ledger.journal.post",
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        branches = self.request.actor.branch_ids("ledger.view")
        return queryset if branches is None else queryset.filter(branch_id__in=branches)

    def list(self, request):
        queryset = self.filter_queryset(self.get_queryset()).distinct()
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    def create(self, request):
        payload = ManualEntrySerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        lines = []
        for line in data["lines"]:
            sign = 1 if (line.get("debit") or 0) > 0 else -1
            quantity = line.get("debit") or line.get("credit")
            value = line.get("functional_amount")
            lines.append(services.LineInput(
                account=line["account"], commodity=line["commodity"], quantity=sign * quantity,
                functional_amount=None if value is None else sign * value,
                party=line.get("party"), memo=line["memo"],
            ))
        entry = services.post_entry(branch=data["branch"], business_date=data["business_date"],
                                    lines=lines, kind=EntryKind.MANUAL, memo=data["memo"],
                                    actor=request.actor)
        return Response(self.get_serializer(self.get_queryset().get(pk=entry.pk)).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def reverse(self, request, pk=None):
        payload = ReverseSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        reversal = services.reverse_entry(int(pk), actor=request.actor,
                                          business_date=payload.validated_data.get("business_date"),
                                          memo=payload.validated_data["memo"])
        return Response(self.get_serializer(self.get_queryset().get(pk=reversal.pk)).data,
                        status=status.HTTP_201_CREATED)


class TrialBalanceView(APIView):
    required_permissions = {"GET": "ledger.view"}

    def get(self, request):
        as_of = serializers.DateField(required=False, allow_null=True).to_internal_value(
            request.query_params["as_of"]) if request.query_params.get("as_of") else None
        tb = trial_balance(as_of=as_of, branch_ids=request.actor.branch_ids("ledger.view"))
        return Response({
            "as_of": as_of,
            "metal_codes": tb.metal_codes,
            "total_debit": tb.total_debit,
            "total_credit": tb.total_credit,
            "rows": [{"account": row.account.pk, "code": row.account.code,
                      "label": row.account.label, "debit": row.debit, "credit": row.credit,
                      "metals": row.metals} for row in tb.rows],
        })
