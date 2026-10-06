"""The history permission is "admin.audit.history" (owners by default): an earlier name ended in
".view", which the read-only Viewer role receives automatically."""

from django.db import migrations


def drop_old_grant(apps, schema_editor):
    db = schema_editor.connection.alias
    Tenant = apps.get_model("tenants", "Tenant")
    RolePermission = apps.get_model("iam", "RolePermission")
    for tenant_id in Tenant.objects.using(db).values_list("id", flat=True):
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
        RolePermission.objects.using(db).filter(permission="admin.audit.view").delete()
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.tenant_id', '', true)")


class Migration(migrations.Migration):
    dependencies = [("audit", "0004_action_labels")]

    operations = [migrations.RunPython(drop_old_grant, migrations.RunPython.noop)]
