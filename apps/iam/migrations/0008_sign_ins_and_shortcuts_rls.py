from django.db import migrations

from apps.audit.triggers import GuardTenantFKs
from apps.core.tenancy.rls import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("iam", "0007_sign_ins_and_shortcuts"), ("audit", "0005_owner_only")]

    operations = [
        EnableTenantRLS("SignInEvent"),
        EnableTenantRLS("Shortcut"),
        GuardTenantFKs("SignInEvent"),
        GuardTenantFKs("Shortcut"),
    ]
