"""Existing tenants' built-in roles may add currencies."""

from django.db import migrations

from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0005_clear_seeded_names"),
        ("iam", "0006_seed_roles_for_existing_tenants"),
    ]

    operations = [migrations.RunPython(grant_to_system_roles("catalog.currency."),
                                       migrations.RunPython.noop)]
