"""Helpers for data migrations of apps that add permissions.

Tenants provisioned earlier have Manager/Viewer roles seeded from an older catalog. An app
that adds permissions grants its own codes to those built-in roles, the way a new tenant would
get them. Runs per tenant with app.tenant_id set (RLS applies to the migrating role too).
"""

from __future__ import annotations


def grant_to_system_roles(*prefixes: str):
    def run(apps, schema_editor):
        from apps.iam.services import _system_role_templates

        Tenant = apps.get_model("tenants", "Tenant")
        Role = apps.get_model("iam", "Role")
        RolePermission = apps.get_model("iam", "RolePermission")
        connection = schema_editor.connection
        db = connection.alias
        templates = _system_role_templates()
        for tenant_id in Tenant.objects.using(db).values_list("id", flat=True):
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
            for role in Role.objects.using(db).filter(tenant_id=tenant_id, is_system=True):
                _name, _description, grants = templates.get(role.code, ("", "", []))
                RolePermission.objects.using(db).bulk_create(
                    [RolePermission(tenant_id=tenant_id, role=role, permission=code)
                     for code in grants if code.startswith(prefixes)],
                    ignore_conflicts=True)
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.tenant_id', '', true)")

    return run
