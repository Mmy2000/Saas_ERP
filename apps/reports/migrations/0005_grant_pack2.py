"""Existing tenants' built-in roles get reports pack 2."""

from django.db import migrations

from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [("reports", "0004_grant_pack")]

    operations = [migrations.RunPython(
        grant_to_system_roles("reports.purchases.", "reports.wholesale.",
                              "reports.customer_returns.", "reports.balances.",
                              "reports.branch_transfers.", "reports.production_loss."),
        migrations.RunPython.noop)]
