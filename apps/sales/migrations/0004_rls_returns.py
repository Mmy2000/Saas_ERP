from django.db import migrations

from apps.core.tenancy.rls import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("sales", "0003_salesreturn_salesreturnline_and_more")]

    operations = [
        EnableTenantRLS("SalesReturn"),
        EnableTenantRLS("SalesReturnLine"),
    ]
