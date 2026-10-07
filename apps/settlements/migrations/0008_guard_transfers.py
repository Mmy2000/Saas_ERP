from django.db import migrations

from apps.audit.triggers import GuardTenantFKs
from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    """Transfers name a second party (the guard covers it); roles get the new permissions."""

    dependencies = [
        ("settlements", "0007_balance_transfers"),
        ("iam", "0006_seed_roles_for_existing_tenants"),
    ]

    operations = [
        GuardTenantFKs("Settlement"),
        migrations.RunPython(grant_to_system_roles("settlements.transfer.", "settlements.adjust."),
                             migrations.RunPython.noop),
    ]
