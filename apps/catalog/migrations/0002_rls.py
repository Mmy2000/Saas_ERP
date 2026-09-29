from django.db import migrations

from apps.core.tenancy.rls import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("catalog", "0001_initial")]

    operations = [
        EnableTenantRLS("Currency"),
        EnableTenantRLS("Metal"),
        EnableTenantRLS("Karat"),
        EnableTenantRLS("ItemCategory"),
        EnableTenantRLS("CategoryMakingCharge"),
    ]
