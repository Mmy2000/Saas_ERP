"""Existing tenants' built-in roles get the financial statements."""

from django.db import migrations

from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [("reports", "0001_grant_reports")]

    operations = [migrations.RunPython(
        grant_to_system_roles("reports.profit_loss.", "reports.balance_sheet."),
        migrations.RunPython.noop)]
