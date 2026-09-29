from django.utils.translation import gettext_lazy as _

from .catalog import register_permissions

register_permissions(_("Administration"), [
    ("admin.users.view", _("View users")),
    ("admin.users.manage", _("Manage users and their roles")),
    ("admin.roles.manage", _("Manage roles")),
])
