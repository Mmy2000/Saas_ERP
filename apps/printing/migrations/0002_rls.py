from django.db import migrations

from apps.core.tenancy.rls import EnableTenantRLS
from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [
        ("printing", "0001_initial"),
        ("iam", "0006_seed_roles_for_existing_tenants"),
    ]

    operations = [
        EnableTenantRLS("LabelTemplate"),
        migrations.RunPython(grant_to_system_roles("printing.", "inventory.item.print_label"),
                             migrations.RunPython.noop),
    ]
