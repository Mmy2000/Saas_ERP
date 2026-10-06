"""Diamond report permissions are "diamonds.report_*" (owned by the Diamonds feature only);
drop grants of their first names, "reports.diamond_*"."""

from django.db import migrations


def drop(apps, schema_editor):
    db = schema_editor.connection.alias
    Tenant = apps.get_model("tenants", "Tenant")
    RolePermission = apps.get_model("iam", "RolePermission")
    for tenant_id in Tenant.objects.using(db).values_list("id", flat=True):
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
        RolePermission.objects.using(db).filter(permission__startswith="reports.diamond_").delete()
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.tenant_id', '', true)")


class Migration(migrations.Migration):
    dependencies = [("diamonds", "0005_stone_kind_label")]

    operations = [migrations.RunPython(drop, migrations.RunPython.noop)]
