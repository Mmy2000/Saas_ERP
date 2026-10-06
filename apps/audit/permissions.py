from django.utils.translation import gettext_lazy as _

from apps.iam.catalog import register_permissions

register_permissions(_("Administration"), [
    ("admin.audit.history", _("See the history of changes (who changed what, and when)")),
])
