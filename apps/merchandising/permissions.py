from django.utils.translation import gettext_lazy as _

from apps.iam.catalog import register_permissions

register_permissions(_("Selling prices"), [
    ("inventory.price.change", _("Change the selling prices of pieces, one or many at once, and "
                                 "undo a change")),
])
register_permissions(_("Offers"), [
    ("sales.offer.view", _("See offers and what they gave")),
    ("sales.offer.manage", _("Create, change and stop offers")),
])
