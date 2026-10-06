"""Row-level security on the audit table, the trigger functions, and the audit trail on the
master data listed in apps/audit/registry.py."""

from django.db import migrations

from apps.audit.registry import AUDITED, UPDATES_ONLY
from apps.audit.triggers import AUDIT_TRIGGER, FUNCTIONS_SQL, audit_sql
from apps.core.tenancy.rls import EnableTenantRLS
from apps.iam.migration_helpers import grant_to_system_roles

DEPENDENCIES = [
    ("audit", "0001_initial"),
    ("catalog", "0005_clear_seeded_names"),
    ("pricing", "0002_rls"),
    ("parties", "0004_workshop_permissions"),
    ("org", "0005_branding"),
    ("iam", "0006_seed_roles_for_existing_tenants"),
    ("treasury", "0007_cash_counts_rls"),
    ("ledger", "0013_cash_over_short"),
    ("hr", "0003_alter_payadjustment_kind"),
    ("expenses", "0003_rls"),
    ("printing", "0005_follows"),
    ("inventory", "0005_trade"),
]


def install(apps, schema_editor):
    schema_editor.execute(FUNCTIONS_SQL, params=None)  # no params: "%" stays literal
    schema_editor.execute('CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE ON '
                          '"audit_auditevent" FOR EACH ROW EXECUTE FUNCTION audit_append_only()')
    for label, ignore in AUDITED.items():
        try:
            model = apps.get_model(label)
        except LookupError:  # a later app: its own migration uses AuditModel
            continue
        for sql in audit_sql(model._meta.db_table, ignore=ignore,
                             updates_only=label in UPDATES_ONLY):
            schema_editor.execute(sql)


def remove(apps, schema_editor):
    for label in AUDITED:
        try:
            model = apps.get_model(label)
        except LookupError:
            continue
        schema_editor.execute(f'DROP TRIGGER IF EXISTS {AUDIT_TRIGGER} ON "{model._meta.db_table}"')
    schema_editor.execute('DROP TRIGGER IF EXISTS audit_append_only ON "audit_auditevent"')


class Migration(migrations.Migration):
    dependencies = DEPENDENCIES

    operations = [
        EnableTenantRLS("AuditEvent"),
        migrations.RunPython(install, remove),
        migrations.RunPython(grant_to_system_roles("admin.audit."), migrations.RunPython.noop),
    ]
