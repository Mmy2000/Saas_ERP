from django.db import migrations

from apps.core.tenancy.rls import EnableTenantRLS
from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [
        ("treasury", "0004_cheques_and_reconciliation"),
        ("iam", "0006_seed_roles_for_existing_tenants"),
    ]

    operations = [
        EnableTenantRLS("Cheque"),
        EnableTenantRLS("BankReconciliation"),
        EnableTenantRLS("ReconciledLine"),
        migrations.RunPython(grant_to_system_roles("treasury.cheque.", "treasury.reconcile"),
                             migrations.RunPython.noop),
    ]
