from django.utils.translation import gettext_lazy as _

from apps.iam.catalog import register_permissions

register_permissions(_("Workshops and work orders"), [
    ("manufacturing.order.view", _("View work orders")),
    ("manufacturing.order.issue", _("Send gold to workshops")),
    ("manufacturing.order.receive", _("Receive goods back from workshops")),
    ("manufacturing.order.cancel", _("Cancel work orders and their receipts")),
])
