from django.db import migrations

from apps.audit.triggers import AuditModel, GuardTenantFKs
from apps.core.tenancy.rls import EnableTenantRLS
from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0017_sale_codes"),
        ("audit", "0005_owner_only"),
        ("iam", "0006_seed_roles_for_existing_tenants"),
    ]

    operations = [
        EnableTenantRLS("SaleCode"),
        EnableTenantRLS("SaleCodeRules"),
        GuardTenantFKs("SaleCode"),
        GuardTenantFKs("SaleCodeRules"),
        AuditModel("SaleCodeRules"),
        migrations.RunPython(grant_to_system_roles("sales.code."), migrations.RunPython.noop),
    ]
