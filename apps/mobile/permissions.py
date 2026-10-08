from django.utils.translation import gettext_lazy as _

from apps.iam.catalog import register_permissions

register_permissions(_("Mobile app"), [
    ("mobile.signin", _("Sign in to the mobile app (stock and stocktake)")),
    ("mobile.devices.manage", _("See everyone's signed-in phones and sign them out")),
])
