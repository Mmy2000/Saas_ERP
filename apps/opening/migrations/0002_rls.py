from django.db import migrations

from apps.audit.triggers import GuardTenantFKs
from apps.core.tenancy.rls import EnableTenantRLS
from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [
        ("opening", "0001_initial"),
        ("audit", "0005_owner_only"),
        ("iam", "0006_seed_roles_for_existing_tenants"),
    ]

    operations = [
        EnableTenantRLS("OpeningBalance"),
        EnableTenantRLS("OpeningLine"),
        GuardTenantFKs("OpeningBalance"),
        GuardTenantFKs("OpeningLine"),
        migrations.RunPython(grant_to_system_roles("ledger.opening."), migrations.RunPython.noop),
    ]
