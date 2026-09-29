from rest_framework import serializers

from apps.catalog.models import Karat
from apps.org.models import Branch
from apps.parties.models import BarcodeWeightRule, Gender, Party, PartyKind


class PartyReadSerializer(serializers.ModelSerializer):
    code = serializers.IntegerField(read_only=True)
    home_branch_name = serializers.CharField(source="home_branch.name", read_only=True,
                                             default=None)

    class Meta:
        model = Party
        fields = ["id", "code", "name", "kind", "phone", "phone_e164", "alt_phone", "email",
                  "address", "notes", "home_branch", "home_branch_name", "is_active"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        pii_permission = self.context.get("pii_permission")
        actor = getattr(self.context.get("request"), "actor", None)
        # PII is omitted, not just hidden, without the permission (§14.2).
        if pii_permission and actor is not None and actor.can(pii_permission):
            data["national_id"] = instance.national_id
        return data


class CustomerReadSerializer(PartyReadSerializer):
    profile = serializers.SerializerMethodField()

    class Meta(PartyReadSerializer.Meta):
        fields = [*PartyReadSerializer.Meta.fields, "profile"]

    def get_profile(self, party):
        p = getattr(party, "customer_profile", None)
        if p is None:
            return None
        return {"gender": p.gender, "birth_date": p.birth_date, "anniversary": p.anniversary,
                "ring_size": p.ring_size, "bracelet_size": p.bracelet_size,
                "preferences": p.preferences}


class SupplierReadSerializer(PartyReadSerializer):
    profile = serializers.SerializerMethodField()

    class Meta(PartyReadSerializer.Meta):
        fields = [*PartyReadSerializer.Meta.fields, "profile"]

    def get_profile(self, party):
        p = getattr(party, "supplier_profile", None)
        if p is None:
            return None
        return {"account_karat": p.account_karat_id,
                "barcode_weight_rule": p.barcode_weight_rule, "lookup_url": p.lookup_url}


class PartyWriteSerializer(serializers.Serializer):
    """Input contract (ADR-018). Business validation (phones, duplicates) is in the service."""

    name = serializers.CharField(max_length=200)
    kind = serializers.ChoiceField(choices=PartyKind.choices, required=False)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    alt_phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    national_id = serializers.CharField(max_length=40, required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    home_branch = serializers.PrimaryKeyRelatedField(queryset=Branch.objects.all(),
                                                     required=False, allow_null=True)


class CustomerWriteSerializer(PartyWriteSerializer):
    gender = serializers.ChoiceField(choices=Gender.choices, required=False, allow_blank=True)
    birth_date = serializers.DateField(required=False, allow_null=True)
    anniversary = serializers.DateField(required=False, allow_null=True)
    ring_size = serializers.CharField(max_length=10, required=False, allow_blank=True)
    bracelet_size = serializers.CharField(max_length=10, required=False, allow_blank=True)
    preferences = serializers.CharField(required=False, allow_blank=True)


class SupplierWriteSerializer(PartyWriteSerializer):
    account_karat = serializers.PrimaryKeyRelatedField(
        queryset=Karat.objects.filter(is_active=True), required=False, allow_null=True)
    barcode_weight_rule = serializers.ChoiceField(choices=BarcodeWeightRule.choices,
                                                  required=False)
    lookup_url = serializers.URLField(required=False, allow_blank=True)
