"""How many workspaces each login belongs to (ADR-022). A workspace can't see the others'
memberships (row-level security), so a trigger on iam_membership keeps the number on the global
user row, and the existing memberships are counted one tenant at a time."""

from django.db import migrations, models

TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION iam_membership_count() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP IN ('INSERT', 'UPDATE') THEN
    UPDATE iam_user SET membership_count = membership_count + 1 WHERE id = NEW.user_id;
  END IF;
  IF TG_OP IN ('DELETE', 'UPDATE') THEN
    UPDATE iam_user SET membership_count = membership_count - 1 WHERE id = OLD.user_id;
  END IF;
  RETURN NULL;
END $$;
CREATE TRIGGER iam_membership_count AFTER INSERT OR DELETE OR UPDATE OF user_id
  ON iam_membership FOR EACH ROW EXECUTE FUNCTION iam_membership_count();
"""
DROP_SQL = """
DROP TRIGGER IF EXISTS iam_membership_count ON iam_membership;
DROP FUNCTION IF EXISTS iam_membership_count();
"""


def count_existing(apps, schema_editor):
    Tenant = apps.get_model("tenants", "Tenant")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("UPDATE iam_user SET membership_count = 0")
        for tenant_id in Tenant.objects.values_list("pk", flat=True):
            # Memberships are visible one tenant at a time; a login is in a tenant at most once.
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
            cursor.execute("UPDATE iam_user AS u SET membership_count = u.membership_count + 1 "
                           "FROM iam_membership AS m WHERE m.user_id = u.id")
        cursor.execute("SELECT set_config('app.tenant_id', '', true)")


class Migration(migrations.Migration):

    dependencies = [
        ('iam', '0008_sign_ins_and_shortcuts_rls'),
        ('tenants', '0006_plans'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='membership_count',
            field=models.PositiveIntegerField(default=0, editable=False),
        ),
        migrations.RunSQL(TRIGGER_SQL, DROP_SQL),
        migrations.RunPython(count_existing, migrations.RunPython.noop),
    ]
