from rest_framework import serializers

from apps.iam.catalog import WILDCARD
from apps.iam.models import Membership, Role


class RoleAssignmentSerializer(serializers.Serializer):
    role = serializers.IntegerField()
    all_branches = serializers.BooleanField(default=True)
    branches = serializers.ListField(child=serializers.IntegerField(), required=False,
                                     default=list)


class MemberWriteSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    display_name = serializers.CharField(max_length=150)
    email = serializers.CharField(max_length=254, required=False, allow_blank=True, default="")
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    default_branch = serializers.IntegerField(required=False, allow_null=True, default=None)
    roles = RoleAssignmentSerializer(many=True)
    password = serializers.CharField(required=False, allow_blank=True, write_only=True,
                                     trim_whitespace=False)


class PasswordSerializer(serializers.Serializer):
    password = serializers.CharField(trim_whitespace=False)


class MemberReadSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(source="user.display_name")
    email = serializers.CharField(source="user.email", allow_null=True)
    phone = serializers.CharField(source="user.phone", allow_null=True)
    last_login = serializers.DateTimeField(source="user.last_login", allow_null=True)
    roles = serializers.SerializerMethodField()

    class Meta:
        model = Membership
        fields = ["id", "username", "display_name", "email", "phone", "status",
                  "default_branch", "last_login", "roles"]

    def get_roles(self, membership):
        return [
            {"role": a.role_id, "name": a.role.label, "all_branches": a.all_branches,
             "branches": [b.branch_id for b in a.branches.all()]}
            for a in membership.roles.all()
        ]


class RoleReadSerializer(serializers.ModelSerializer):
    permissions = serializers.SerializerMethodField()
    is_owner = serializers.SerializerMethodField()
    member_count = serializers.IntegerField(read_only=True, default=None)
    label = serializers.CharField(read_only=True)

    class Meta:
        model = Role
        fields = ["id", "code", "name", "label", "description", "is_system", "is_owner",
                  "permissions", "member_count"]

    def get_permissions(self, role):
        return sorted(g.permission for g in role.grants.all())

    def get_is_owner(self, role):
        return any(g.permission == WILDCARD for g in role.grants.all())


class RoleWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    description = serializers.CharField(max_length=300, required=False, allow_blank=True,
                                        default="")
    permissions = serializers.ListField(child=serializers.CharField(), default=list)
