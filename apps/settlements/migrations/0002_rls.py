from django.db import migrations

from apps.core.tenancy.rls import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("settlements", "0001_initial")]

    operations = [EnableTenantRLS("Settlement")]
