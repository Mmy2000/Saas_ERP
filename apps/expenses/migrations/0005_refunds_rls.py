from django.db import migrations

from apps.audit.triggers import GuardTenantFKs
from apps.core.tenancy.rls import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [
        ("expenses", "0004_refunds"),
        ("audit", "0003_tenant_fk_guards"),
    ]

    operations = [
        EnableTenantRLS("ExpenseRefund"),
        GuardTenantFKs("ExpenseRefund"),
    ]
