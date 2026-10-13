from django.db import migrations

from apps.audit.triggers import AuditModel, GuardTenantFKs
from apps.core.tenancy.rls import EnableTenantRLS
from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0012_blind_counts"),
        ("audit", "0003_tenant_fk_guards"),
    ]

    operations = [
        EnableTenantRLS("BlindCount"),
        GuardTenantFKs("BlindCount"),
        AuditModel("BlindCount"),
        migrations.RunPython(grant_to_system_roles("inventory.blind_count."),
                             migrations.RunPython.noop),
    ]
