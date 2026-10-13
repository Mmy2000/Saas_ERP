from django.db import migrations

from apps.audit.triggers import GuardTenantFKs


class Migration(migrations.Migration):
    dependencies = [
        ("settlements", "0010_cross_currency"),
        ("audit", "0003_tenant_fk_guards"),
    ]

    operations = [GuardTenantFKs("Settlement")]
