from django.utils.translation import gettext_lazy as _

from apps.iam.catalog import register_permissions

register_permissions(_("Repairs and custom orders"), [
    ("repairs.order.view", _("View repairs and custom orders")),
    ("repairs.order.create", _("Take in repairs and custom orders, and their deposits")),
    ("repairs.order.update", _("Send repairs to workshops and mark them ready")),
    ("repairs.order.deliver", _("Deliver repairs and take payment")),
    ("repairs.order.cancel", _("Cancel repairs and refund deposits")),
])
