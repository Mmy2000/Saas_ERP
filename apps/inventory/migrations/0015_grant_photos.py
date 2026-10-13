"""Existing tenants' built-in roles get the photo permission."""

from django.db import migrations

from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [("inventory", "0014_photos")]

    operations = [migrations.RunPython(grant_to_system_roles("inventory.item.photo"),
                                       migrations.RunPython.noop)]
