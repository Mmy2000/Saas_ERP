from django.db import migrations

from apps.core.tenancy.rls import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("iam", "0002_initial")]

    operations = [
        EnableTenantRLS("Membership"),
    ]
