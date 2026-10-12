from django.db import migrations

from apps.audit.triggers import AuditModel, GuardTenantFKs
from apps.core.tenancy.rls import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("diamonds", "0021_stone_grades"), ("audit", "0005_owner_only")]

    operations = [
        EnableTenantRLS("StoneGrade"),
        GuardTenantFKs("StoneGrade"),
        AuditModel("StoneGrade"),
    ]
