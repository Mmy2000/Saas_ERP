"""Existing tenants' built-in roles get reports pack 4."""

from django.db import migrations

from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [("reports", "0006_grant_pack3")]

    operations = [migrations.RunPython(
        grant_to_system_roles("reports.payroll.", "reports.cash_balances.",
                              "reports.coding_differences.", "reports.label_changes."),
        migrations.RunPython.noop)]
