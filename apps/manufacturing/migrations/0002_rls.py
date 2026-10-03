from django.db import migrations

from apps.core.tenancy.rls import EnableTenantRLS
from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [
        ("manufacturing", "0001_initial"),
        ("iam", "0006_seed_roles_for_existing_tenants"),
    ]

    operations = [
        EnableTenantRLS("WorkOrder"),
        EnableTenantRLS("WorkOrderIssueLine"),
        EnableTenantRLS("WorkOrderReceiptLine"),
        EnableTenantRLS("WorkOrderPiece"),
        migrations.RunPython(grant_to_system_roles("manufacturing."), migrations.RunPython.noop),
    ]
