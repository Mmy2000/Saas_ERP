from django.utils.translation import gettext_lazy as _

from apps.iam.catalog import register_permissions

# "ledger." so they belong to the Accounting feature with the journal they post to.
register_permissions(_("Opening balances"), [
    ("ledger.opening.view", _("See opening balances")),
    ("ledger.opening.post", _("Enter opening balances and import opening stock from Excel")),
    ("ledger.opening.void", _("Cancel opening balances")),
])
