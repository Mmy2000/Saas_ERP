from django.utils.translation import gettext_lazy as _

from apps.iam.catalog import register_permissions

register_permissions(_("Printing"), [
    ("printing.templates.manage", _("Design label templates")),
])
