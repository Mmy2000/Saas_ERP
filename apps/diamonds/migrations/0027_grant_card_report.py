"""Existing tenants' built-in roles get the diamond category card."""

from django.db import migrations

from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [("diamonds", "0026_stone_codes_rls"), ("reports", "0007_grant_pack4")]

    operations = [migrations.RunPython(grant_to_system_roles("diamonds.report_card."),
                                       migrations.RunPython.noop)]
