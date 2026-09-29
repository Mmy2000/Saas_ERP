from django.db import migrations

from apps.core.tenancy.rls import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("pricing", "0001_initial")]

    operations = [
        EnableTenantRLS("FxRate"),
        EnableTenantRLS("MetalPriceBoard"),
        EnableTenantRLS("MetalPriceBoardLine"),
    ]
