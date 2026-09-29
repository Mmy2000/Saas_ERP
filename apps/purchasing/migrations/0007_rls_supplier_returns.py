from django.db import migrations

from apps.core.tenancy.rls import EnableTenantRLS
from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [
        ("purchasing", "0006_supplier_returns"),
        ("iam", "0006_seed_roles_for_existing_tenants"),
    ]

    operations = [
        EnableTenantRLS("SupplierReturn"),
        EnableTenantRLS("SupplierReturnLine"),
        migrations.RunPython(grant_to_system_roles("purchasing.return."),
                             migrations.RunPython.noop),
    ]
