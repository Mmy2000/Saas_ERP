from django.utils.translation import gettext_lazy as _

from apps.iam.catalog import register_permissions

register_permissions(_("Workshops and production"), [
    ("manufacturing.order.view", _("View work orders and production")),
    ("manufacturing.order.issue", _("Send gold to workshops or into production")),
    ("manufacturing.order.receive", _("Receive goods from workshops and production")),
    ("manufacturing.order.cancel", _("Cancel work and production orders and their receipts")),
])
