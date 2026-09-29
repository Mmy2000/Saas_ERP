from django.db import migrations

from apps.core.tenancy.rls import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("purchasing", "0001_initial")]

    operations = [
        EnableTenantRLS("SupplierInvoice"),
        EnableTenantRLS("SupplierInvoiceLine"),
        EnableTenantRLS("SupplierInvoicePiece"),
    ]
