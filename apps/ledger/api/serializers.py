from decimal import Decimal

from django.utils.translation import gettext as _
from rest_framework import serializers

from apps.ledger.models import Account, Commodity, JournalEntry, JournalLine
from apps.org.models import Branch
from apps.parties.models import Party


class AccountSerializer(serializers.ModelSerializer):
    label = serializers.CharField(read_only=True)
    name = serializers.SerializerMethodField()

    class Meta:
        model = Account
        fields = ["id", "code", "name", "label", "parent", "type", "nature", "subledger",
                  "commodity_scope", "is_postable", "role", "is_active"]

    def get_name(self, account):
        return f"{account.code} · {account.label}"  # what searchable selects display


class CommoditySerializer(serializers.ModelSerializer):
    label = serializers.CharField(read_only=True)

    class Meta:
        model = Commodity
        fields = ["id", "code", "kind", "label", "decimal_places", "is_functional"]


class LineSerializer(serializers.ModelSerializer):
    account_label = serializers.CharField(source="account.__str__", read_only=True)
    commodity_code = serializers.CharField(source="commodity.code", read_only=True)
    party_name = serializers.CharField(source="party.name", read_only=True, default=None)

    class Meta:
        model = JournalLine
        fields = ["id", "account", "account_label", "commodity", "commodity_code", "quantity",
                  "functional_amount", "party", "party_name", "branch", "memo"]


class EntrySerializer(serializers.ModelSerializer):
    lines = LineSerializer(many=True, read_only=True)
    reversed_by = serializers.SerializerMethodField()

    class Meta:
        model = JournalEntry
        fields = ["id", "number", "branch", "business_date", "kind", "memo", "source_type",
                  "source_id", "reverses", "reversed_by", "posted_at", "lines"]

    def get_reversed_by(self, entry):
        reversal = getattr(entry, "reversed_by", None)
        return reversal.pk if reversal else None


def _amount(**kw):
    return serializers.DecimalField(max_digits=20, decimal_places=6, min_value=Decimal(0),
                                    required=False, allow_null=True, **kw)


class ManualLineSerializer(serializers.Serializer):
    account = serializers.PrimaryKeyRelatedField(queryset=Account.objects.filter(is_active=True))
    commodity = serializers.PrimaryKeyRelatedField(queryset=Commodity.objects.all())
    debit = _amount()
    credit = _amount()
    functional_amount = _amount()  # value in company currency, for foreign currency / metal
    party = serializers.PrimaryKeyRelatedField(queryset=Party.objects.all(), required=False,
                                               allow_null=True)
    memo = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")

    def validate(self, data):
        debit, credit = data.get("debit") or 0, data.get("credit") or 0
        if (debit > 0) == (credit > 0):
            raise serializers.ValidationError(_("Enter either a debit or a credit."))
        return data


class ManualEntrySerializer(serializers.Serializer):
    branch = serializers.PrimaryKeyRelatedField(queryset=Branch.objects.filter(is_active=True))
    business_date = serializers.DateField()
    memo = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")
    lines = ManualLineSerializer(many=True)


class ReverseSerializer(serializers.Serializer):
    business_date = serializers.DateField(required=False, allow_null=True)
    memo = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")
