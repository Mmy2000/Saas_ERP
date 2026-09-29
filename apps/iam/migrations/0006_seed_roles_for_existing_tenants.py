"""Tenants provisioned before RBAC existed get the system roles, and every member who has no
role yet becomes an Owner (they were, in effect, all-powerful before). Runs per tenant with
`app.tenant_id` set, because RLS applies to the migrating role too."""

from django.db import migrations
from django.utils import translation


def seed(apps, schema_editor):
    from apps.iam.services import OWNER, _system_role_templates

    Tenant = apps.get_model("tenants", "Tenant")
    TenantProfile = apps.get_model("org", "TenantProfile")
    Membership = apps.get_model("iam", "Membership")
    Role = apps.get_model("iam", "Role")
    RolePermission = apps.get_model("iam", "RolePermission")
    MembershipRole = apps.get_model("iam", "MembershipRole")
    connection = schema_editor.connection
    db = connection.alias  # every query must use the connection that has app.tenant_id set

    for tenant_id in Tenant.objects.using(db).values_list("id", flat=True):
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
        locale = (TenantProfile.objects.using(db).filter(tenant_id=tenant_id)
                  .values_list("locale", flat=True).first() or "ar")
        with translation.override(locale):
            templates = _system_role_templates()
        roles = {}
        for code, (name, description, grants) in templates.items():
            role, created = Role.objects.using(db).get_or_create(
                tenant_id=tenant_id, code=code,
                defaults={"name": name, "description": description, "is_system": True},
            )
            if created:
                RolePermission.objects.using(db).bulk_create([
                    RolePermission(tenant_id=tenant_id, role=role, permission=p) for p in grants
                ])
            roles[code] = role
        for membership in Membership.objects.using(db).filter(tenant_id=tenant_id):
            if not MembershipRole.objects.using(db).filter(membership=membership).exists():
                MembershipRole.objects.using(db).create(tenant_id=tenant_id, membership=membership,
                                              role=roles[OWNER], all_branches=True)

    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.tenant_id', '', true)")


class Migration(migrations.Migration):
    dependencies = [
        ("iam", "0005_rls_roles"),
        ("org", "0003_tenantprofile_country_alter_tenantprofile_locale"),
        ("tenants", "0001_initial"),
    ]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
