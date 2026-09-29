from django.utils.translation import gettext_lazy as _

from apps.iam.catalog import register_permissions

register_permissions(_("Purchasing"), [
    ("purchasing.invoice.view", _("View supplier invoices")),
    ("purchasing.invoice.create", _("Create and edit draft supplier invoices")),
    ("purchasing.invoice.post", _("Post supplier invoices (brings goods into stock)")),
    ("purchasing.invoice.void", _("Cancel posted supplier invoices")),
    ("purchasing.return.create", _("Return goods to suppliers")),
    ("purchasing.return.void", _("Cancel returns to suppliers")),
    ("purchasing.scrap.view", _("View scrap gold bought and sold")),
    ("purchasing.scrap.buy", _("Buy scrap gold")),
    ("purchasing.scrap.sell", _("Sell scrap gold")),
    ("purchasing.scrap.override_price", _("Change the scrap gold price when buying")),
    ("purchasing.scrap.void", _("Cancel scrap purchases and sales")),
])
