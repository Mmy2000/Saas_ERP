from django.db import migrations

from apps.core.tenancy.rls import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("sales", "0001_initial")]

    operations = [
        EnableTenantRLS("SalesInvoice"),
        EnableTenantRLS("SalesInvoiceLine"),
        EnableTenantRLS("SalesTradeIn"),
        EnableTenantRLS("SalesPayment"),
    ]
