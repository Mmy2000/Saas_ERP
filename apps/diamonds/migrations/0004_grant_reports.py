from django.db import migrations

from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [("diamonds", "0003_stone_from_setting"), ("reports", "0002_grant_statements")]

    operations = [migrations.RunPython(grant_to_system_roles("diamonds.report_"),
                                       migrations.RunPython.noop)]
