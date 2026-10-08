from django.db import migrations

from apps.audit.triggers import GuardTenantFKs
from apps.core.tenancy.rls import EnableTenantRLS
from apps.iam.migration_helpers import grant_to_system_roles


def links_so_far(apps, schema_editor):
    """WhatsApp messages before the gateways all opened the shop's WhatsApp with a link."""
    db = schema_editor.connection.alias
    Tenant = apps.get_model("tenants", "Tenant")
    OutboundMessage = apps.get_model("messaging", "OutboundMessage")
    for tenant_id in Tenant.objects.using(db).values_list("id", flat=True):
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
        OutboundMessage.objects.using(db).filter(tenant_id=tenant_id, channel="whatsapp",
                                                 via="").update(via="link")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.tenant_id', '', true)")


class Migration(migrations.Migration):
    dependencies = [("messaging", "0004_whatsapp_gateways"), ("audit", "0005_owner_only"),
                    ("iam", "0008_sign_ins_and_shortcuts_rls")]

    operations = [
        EnableTenantRLS("WhatsAppLink"),
        GuardTenantFKs("WhatsAppLink"),
        migrations.RunPython(links_so_far, migrations.RunPython.noop),
        migrations.RunPython(grant_to_system_roles("messaging.whatsapp."),
                             migrations.RunPython.noop),
    ]
