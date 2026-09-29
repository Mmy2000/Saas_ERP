from django.db import migrations

from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [
        ("parties", "0002_rls"),
        ("iam", "0006_seed_roles_for_existing_tenants"),
    ]

    operations = [
        migrations.RunPython(grant_to_system_roles("parties.trade_account."),
                             migrations.RunPython.noop),
    ]
