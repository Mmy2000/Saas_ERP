from django.utils.translation import gettext_lazy as _

from apps.iam.catalog import register_permissions

register_permissions(_("Pricing"), [
    ("pricing.board.view", _("View gold prices")),
    ("pricing.board.publish", _("Publish gold prices")),
    ("pricing.fx.view", _("View exchange rates")),
    ("pricing.fx.publish", _("Publish exchange rates")),
])
