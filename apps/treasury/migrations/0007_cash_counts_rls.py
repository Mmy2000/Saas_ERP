from django.db import migrations

from apps.core.tenancy.rls import EnableTenantRLS
from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [
        ("treasury", "0006_cash_counts"),
        ("iam", "0006_seed_roles_for_existing_tenants"),
    ]

    operations = [
        EnableTenantRLS("CashCount"),
        migrations.RunPython(grant_to_system_roles("treasury.count."), migrations.RunPython.noop),
    ]
