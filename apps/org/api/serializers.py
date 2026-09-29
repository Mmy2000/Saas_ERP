from rest_framework import serializers

from apps.org.models import Branch


class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = ["id", "code", "name", "is_head_office", "handles_diamonds", "phone",
                  "address", "is_active"]
