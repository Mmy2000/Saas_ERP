from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.parties import services
from apps.parties.models import PartyKind, PartyRoleType
from apps.parties.selectors import parties_with_role, search

from .serializers import (
    CustomerReadSerializer,
    CustomerWriteSerializer,
    PartyReadSerializer,
    PartyWriteSerializer,
    SupplierReadSerializer,
    SupplierWriteSerializer,
)

PARTY_FIELDS = ("name", "kind", "phone", "alt_phone", "email", "address", "notes")


def _party_data(validated: dict, instance=None) -> services.PartyData:
    """Full PartyData from a (possibly partial) payload over the current values."""
    def pick(name, default=""):
        if name in validated:
            return validated[name]
        return getattr(instance, name) if instance is not None else default

    values = {name: pick(name) for name in PARTY_FIELDS}
    values["kind"] = values["kind"] or PartyKind.PERSON
    if "home_branch" in validated:
        branch = validated["home_branch"]
        values["home_branch_id"] = branch.pk if branch else None
    else:
        values["home_branch_id"] = instance.home_branch_id if instance is not None else None
    values["national_id"] = validated.get("national_id")  # absent → unchanged
    return services.PartyData(**values)


class _PartyViewSet(viewsets.GenericViewSet):
    role: str
    read_serializer: type
    write_serializer: type
    pii_permission: str | None = None

    def get_queryset(self):
        queryset = parties_with_role(self.role).order_by("-code")
        queryset = search(queryset, self.request.query_params.get("q", ""))
        active = self.request.query_params.get("is_active")
        if active in ("true", "false"):
            queryset = queryset.filter(is_active=active == "true")
        return queryset

    def get_serializer_class(self):
        return self.read_serializer

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "pii_permission": self.pii_permission}

    def _respond(self, party, code=status.HTTP_200_OK):
        party = self.get_queryset().get(pk=party.pk)
        return Response(self.get_serializer(party).data, status=code)

    def list(self, request):
        page = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    def _validated(self, partial=False):
        payload = self.write_serializer(data=self.request.data, partial=partial)
        payload.is_valid(raise_exception=True)
        return payload.validated_data

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        party = services.set_party_active(int(pk), self.role, False, actor=request.actor)
        return self._respond(party)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        party = services.set_party_active(int(pk), self.role, True, actor=request.actor)
        return self._respond(party)


class CustomerViewSet(_PartyViewSet):
    role = PartyRoleType.CUSTOMER
    read_serializer = CustomerReadSerializer
    write_serializer = CustomerWriteSerializer
    pii_permission = "parties.customer.view_pii"
    required_permissions = {
        "list": "parties.customer.view",
        "retrieve": "parties.customer.view",
        "create": "parties.customer.create",
        "partial_update": "parties.customer.edit",
        "deactivate": "parties.customer.deactivate",
        "activate": "parties.customer.deactivate",
    }

    def get_queryset(self):
        return super().get_queryset().select_related("customer_profile")

    @staticmethod
    def _profile(validated, instance=None) -> services.CustomerData:
        current = getattr(instance, "customer_profile", None)
        names = ("gender", "birth_date", "anniversary", "ring_size", "bracelet_size",
                 "preferences")
        blank = services.CustomerData()
        return services.CustomerData(**{
            n: validated[n] if n in validated
            else (getattr(current, n) if current is not None else getattr(blank, n))
            for n in names
        })

    def create(self, request):
        data = self._validated()
        party = services.create_customer(_party_data(data), self._profile(data),
                                         actor=request.actor)
        return self._respond(party, status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        instance = self.get_object()
        data = self._validated(partial=True)
        party = services.update_customer(instance.pk, _party_data(data, instance),
                                         self._profile(data, instance), actor=request.actor)
        return self._respond(party)


class SupplierViewSet(_PartyViewSet):
    role = PartyRoleType.SUPPLIER
    read_serializer = SupplierReadSerializer
    write_serializer = SupplierWriteSerializer
    required_permissions = {
        "list": "parties.supplier.view",
        "retrieve": "parties.supplier.view",
        "create": "parties.supplier.create",
        "partial_update": "parties.supplier.edit",
        "deactivate": "parties.supplier.deactivate",
        "activate": "parties.supplier.deactivate",
    }

    def get_queryset(self):
        return super().get_queryset().select_related("supplier_profile")

    @staticmethod
    def _profile(validated, instance=None) -> services.SupplierData:
        current = getattr(instance, "supplier_profile", None)
        karat = validated.get("account_karat", getattr(current, "account_karat", None))
        return services.SupplierData(
            account_karat_id=karat.pk if karat else None,
            barcode_weight_rule=validated.get(
                "barcode_weight_rule", getattr(current, "barcode_weight_rule", "none")),
            lookup_url=validated.get("lookup_url", getattr(current, "lookup_url", "")),
        )

    def create(self, request):
        data = self._validated()
        base = _party_data(data)
        if "kind" not in data:
            base = services.PartyData(**{**base.__dict__, "kind": PartyKind.ORGANIZATION})
        party = services.create_supplier(base, self._profile(data), actor=request.actor)
        return self._respond(party, status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        instance = self.get_object()
        data = self._validated(partial=True)
        party = services.update_supplier(instance.pk, _party_data(data, instance),
                                         self._profile(data, instance), actor=request.actor)
        return self._respond(party)


class TradeAccountViewSet(_PartyViewSet):
    role = PartyRoleType.TRADE_ACCOUNT
    read_serializer = PartyReadSerializer
    write_serializer = PartyWriteSerializer
    required_permissions = {
        "list": "parties.trade_account.view",
        "retrieve": "parties.trade_account.view",
        "create": "parties.trade_account.create",
        "partial_update": "parties.trade_account.edit",
        "deactivate": "parties.trade_account.deactivate",
        "activate": "parties.trade_account.deactivate",
    }

    def create(self, request):
        data = self._validated()
        base = _party_data(data)
        if "kind" not in data:
            base = services.PartyData(**{**base.__dict__, "kind": PartyKind.ORGANIZATION})
        party = services.create_trade_account(base, actor=request.actor)
        return self._respond(party, status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        instance = self.get_object()
        data = self._validated(partial=True)
        party = services.update_trade_account(instance.pk, _party_data(data, instance),
                                              actor=request.actor)
        return self._respond(party)
