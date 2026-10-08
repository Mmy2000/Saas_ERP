from django.utils.translation import gettext_lazy as _

from apps.iam.catalog import register_permissions

register_permissions(_("Messages to customers"), [
    ("messaging.send", _("E-mail documents and statements, and share them on WhatsApp")),
    ("messaging.view", _("See the messages sent to customers")),
    ("messaging.whatsapp.api", _("Send documents on WhatsApp through the WhatsApp service")),
    ("messaging.whatsapp.own", _("Send documents on WhatsApp from the shop's own number")),
    ("messaging.whatsapp.link", _("Link the shop's WhatsApp number (scan the QR)")),
    ("messaging.campaign.manage", _("Send WhatsApp campaigns to customers, and keep the list of "
                                    "numbers that do not want them")),
])
