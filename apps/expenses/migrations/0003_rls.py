from django.db import migrations

from apps.core.tenancy.rls import EnableTenantRLS
from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [
        ("expenses", "0002_initial"),
        ("iam", "0006_seed_roles_for_existing_tenants"),
    ]

    operations = [
        EnableTenantRLS("ExpenseCategory"),
        EnableTenantRLS("ExpenseVoucher"),
        migrations.RunPython(grant_to_system_roles("expenses."), migrations.RunPython.noop),
    ]
