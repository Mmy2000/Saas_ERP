from django.utils.translation import gettext_lazy as _

from apps.iam.catalog import register_permissions

register_permissions(_("Settlements"), [
    ("settlements.view", _("View receipts, payments and gold settlements")),
    ("settlements.money.post", _("Record money received from or paid to customers and suppliers")),
    ("settlements.metal.post", _("Record gold received from or given to customers and suppliers")),
    ("settlements.conversion.post", _("Settle gold balances in money")),
    ("settlements.void", _("Cancel receipts, payments and settlements")),
])
