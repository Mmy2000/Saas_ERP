from django.db import migrations

from apps.audit.triggers import GuardTenantFKs
from apps.core.tenancy.rls import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [
        ("printing", "0006_print_fonts"),
        ("audit", "0003_tenant_fk_guards"),
    ]

    operations = [
        EnableTenantRLS("PrintFont"),
        GuardTenantFKs("LabelTemplate"),
        GuardTenantFKs("DocumentDesign"),
    ]
