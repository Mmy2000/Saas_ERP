from django.db import migrations

from apps.audit.triggers import GuardTenantFKs
from apps.core.tenancy.rls import EnableTenantRLS
from apps.iam.migration_helpers import grant_to_system_roles


class Migration(migrations.Migration):
    dependencies = [
        ("treasury", "0008_money_vouchers"),
        ("iam", "0006_seed_roles_for_existing_tenants"),
        ("audit", "0003_tenant_fk_guards"),
    ]

    operations = [
        EnableTenantRLS("MoneyVoucher"),
        GuardTenantFKs("MoneyVoucher"),
        migrations.RunPython(grant_to_system_roles("treasury.voucher."),
                             migrations.RunPython.noop),
    ]
