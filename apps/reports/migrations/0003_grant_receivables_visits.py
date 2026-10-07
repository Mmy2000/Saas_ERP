"""Existing tenants' built-in roles get the unpaid invoices and customer visits reports."""

from django.db import migrations

from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [("reports", "0002_grant_statements")]

    operations = [migrations.RunPython(
        grant_to_system_roles("reports.unpaid_invoices.", "reports.customer_visits."),
        migrations.RunPython.noop)]
