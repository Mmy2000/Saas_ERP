"""Existing tenants' built-in roles get the reports pack."""

from django.db import migrations

from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [("reports", "0003_grant_receivables_visits")]

    operations = [migrations.RunPython(
        grant_to_system_roles("reports.sales_profit.", "reports.net_sales.",
                              "reports.salespeople.", "reports.customer_analysis.",
                              "reports.scrap.", "reports.coverage.", "reports.gold_position.",
                              "reports.stock_card.", "reports.branch_statement."),
        migrations.RunPython.noop)]
