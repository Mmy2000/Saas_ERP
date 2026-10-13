from django.db import migrations

from apps.audit.triggers import AuditModel, GuardTenantFKs
from apps.core.tenancy.rls import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [
        ("parties", "0007_statement_agreements"),
        ("audit", "0003_tenant_fk_guards"),
    ]

    operations = [
        EnableTenantRLS("StatementAgreement"),
        GuardTenantFKs("StatementAgreement"),
        AuditModel("StatementAgreement"),
    ]
