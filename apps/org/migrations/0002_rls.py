from django.db import migrations

from apps.core.tenancy.rls import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("org", "0001_initial")]

    operations = [
        EnableTenantRLS("TenantProfile"),
        EnableTenantRLS("Branch"),
    ]
