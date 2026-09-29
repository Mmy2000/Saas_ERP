from django.utils.translation import gettext_lazy as _

from apps.iam.catalog import register_permissions

register_permissions(_("Treasury"), [
    ("treasury.view", _("View cash boxes, bank accounts and their movements")),
    ("treasury.setup.manage", _("Set up cash boxes, bank accounts and card terminals")),
    ("treasury.transfer.create", _("Move money between cash boxes and bank accounts")),
    ("treasury.transfer.receive", _("Receive cash sent from another branch")),
    ("treasury.exchange.create", _("Exchange currencies")),
    ("treasury.card_settlement.create", _("Record card settlements from the bank")),
    ("treasury.void", _("Cancel treasury documents")),
])
