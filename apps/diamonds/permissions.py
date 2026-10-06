from django.utils.translation import gettext_lazy as _

from apps.iam.catalog import register_limit, register_permissions

# All "diamonds." permissions belong to the Diamonds feature, which is off unless switched on
# for the client in the platform console.
register_permissions(_("Diamonds and gemstones"), [
    ("diamonds.view", _("See diamond pieces, loose stones and their certificates")),
    ("diamonds.manage", _("Edit stone details, certificates and label prices")),
    ("diamonds.receive", _("Receive diamond pieces and loose stones from suppliers")),
    ("diamonds.setting", _("Record stones set into pieces")),
    ("diamonds.sell", _("Sell diamond pieces and loose stones")),
])
register_limit("diamonds.discount.max_rate", _("Maximum discount on diamond pieces"), "0")
