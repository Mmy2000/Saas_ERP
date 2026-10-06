from django.utils.translation import gettext_lazy as _

from apps.iam.catalog import register_permissions

register_permissions(_("Messages to customers"), [
    ("messaging.send", _("E-mail documents and statements, and share them on WhatsApp")),
    ("messaging.view", _("See the messages sent to customers")),
])
