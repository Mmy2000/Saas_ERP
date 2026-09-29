from django.db import migrations

from apps.core.tenancy.rls import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("core", "0002_initial")]

    operations = [
        EnableTenantRLS("DocumentSequence"),
    ]
