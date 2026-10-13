"""Existing tenants' built-in roles get the parcel stones report."""

from django.db import migrations

from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [("diamonds", "0022_stone_grades_rls"), ("reports", "0006_grant_pack3")]

    operations = [migrations.RunPython(grant_to_system_roles("diamonds.report_parcels."),
                                       migrations.RunPython.noop)]
