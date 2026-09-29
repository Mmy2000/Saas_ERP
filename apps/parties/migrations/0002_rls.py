from django.db import migrations

from apps.core.tenancy.rls import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("parties", "0001_initial")]

    operations = [
        EnableTenantRLS("Party"),
        EnableTenantRLS("PartyRole"),
        EnableTenantRLS("CustomerProfile"),
        EnableTenantRLS("SupplierProfile"),
    ]
