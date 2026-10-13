"""Existing tenants' built-in roles get reports pack 3."""

from django.db import migrations

from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [("reports", "0005_grant_pack2")]

    operations = [migrations.RunPython(
        grant_to_system_roles("reports.employee_advances.", "reports.weight_orders."),
        migrations.RunPython.noop)]
