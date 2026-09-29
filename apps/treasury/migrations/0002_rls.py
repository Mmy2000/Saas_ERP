from django.db import migrations

from apps.core.tenancy.rls import EnableTenantRLS
from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [
        ("treasury", "0001_initial"),
        ("iam", "0006_seed_roles_for_existing_tenants"),
    ]

    operations = [
        EnableTenantRLS("CashBox"),
        EnableTenantRLS("BankAccount"),
        EnableTenantRLS("BankAccountBranch"),
        EnableTenantRLS("CardTerminal"),
        EnableTenantRLS("TreasuryDocument"),
        # Settlements shipped without this; its permissions reach existing roles here too.
        migrations.RunPython(grant_to_system_roles("treasury.", "settlements."),
                             migrations.RunPython.noop),
    ]
