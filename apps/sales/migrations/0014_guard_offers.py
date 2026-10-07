from django.db import migrations

from apps.audit.triggers import GuardTenantFKs


class Migration(migrations.Migration):
    """The sale line now points at offers: its same-client guard covers them too."""

    dependencies = [
        ("sales", "0013_salesinvoiceline_cashback_amount_and_more"),
        ("merchandising", "0002_rls"),
    ]

    operations = [GuardTenantFKs("SalesInvoiceLine")]
