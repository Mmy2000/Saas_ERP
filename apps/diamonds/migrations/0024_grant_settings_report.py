"""Existing tenants' built-in roles get the stones set into pieces report."""

from django.db import migrations

from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [("diamonds", "0023_grant_parcel_report"), ("reports", "0007_grant_pack4")]

    operations = [migrations.RunPython(grant_to_system_roles("diamonds.report_settings."),
                                       migrations.RunPython.noop)]
