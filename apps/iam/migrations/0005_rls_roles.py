from django.db import migrations

from apps.core.tenancy.rls import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("iam", "0004_role_membershiprole_rolepermission_membershiplimit_and_more")]

    operations = [
        EnableTenantRLS("Role"),
        EnableTenantRLS("RolePermission"),
        EnableTenantRLS("MembershipRole"),
        EnableTenantRLS("MembershipRoleBranch"),
        EnableTenantRLS("MembershipLimit"),
    ]
