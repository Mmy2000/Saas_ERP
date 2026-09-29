from django.utils.translation import gettext_lazy as _

from apps.iam.catalog import register_permissions

register_permissions(_("Organisation"), [
    ("org.branch.view", _("View branches")),
    ("org.branch.manage", _("Manage branches")),
    ("org.settings.manage", _("Manage company settings")),
])
