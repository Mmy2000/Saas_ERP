from django.db import migrations

from apps.audit.triggers import AuditModel, GuardTenantFKs
from apps.core.tenancy.rls import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [
        ("diamonds", "0025_stone_codes_origin"),
        ("audit", "0003_tenant_fk_guards"),
    ]

    operations = [
        EnableTenantRLS("StoneCode"),
        GuardTenantFKs("StoneCode"),
        GuardTenantFKs("ItemStone"),
        AuditModel("StoneCode"),
    ]
