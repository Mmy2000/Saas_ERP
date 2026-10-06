from django.db import migrations

from apps.core.tenancy.rls import EnableTenantRLS
from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0009_period_closing"),
        ("iam", "0006_seed_roles_for_existing_tenants"),
    ]

    operations = [
        EnableTenantRLS("PeriodEvent"),
        EnableTenantRLS("YearEnd"),
        migrations.RunPython(grant_to_system_roles("ledger.period.reopen"),
                             migrations.RunPython.noop),
    ]
