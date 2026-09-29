"""Reports have no tables; existing tenants' built-in roles get the report permissions."""

from django.db import migrations

from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [("iam", "0006_seed_roles_for_existing_tenants")]

    operations = [migrations.RunPython(grant_to_system_roles("reports."),
                                       migrations.RunPython.noop)]
