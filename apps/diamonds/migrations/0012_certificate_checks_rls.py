from django.db import migrations

from apps.audit.triggers import GuardTenantFKs
from apps.core.tenancy.rls import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("diamonds", "0011_certificate_checks")]

    operations = [EnableTenantRLS("CertificateCheck"), GuardTenantFKs("CertificateCheck")]
